"""CLI: ``python -m backend [options]``."""

from __future__ import annotations

import argparse
import atexit
import errno
import fcntl
import os
import sys
import tempfile
import threading
from typing import List, Optional

from . import handlers  # noqa: F401  -- importing registers the handlers
from . import hub, limits, metrics
from . import service as service_module
from .service import Service, ServiceError, Transcript
from .store import Store, StoreError


def _ports(text: str) -> List[int]:
    ports = []
    for piece in text.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            port = int(piece)
        except ValueError:
            raise argparse.ArgumentTypeError("%r is not a port number" % piece)
        if not 1 <= port <= 65535:
            raise argparse.ArgumentTypeError("port %d out of range 1-65535" % port)
        ports.append(port)
    if not ports:
        raise argparse.ArgumentTypeError("no ports given")
    return ports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backend",
        description="Reconstructed EA game backend (Madden NFL 2004, PS2).")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=_ports, default=[10000, 10001],
                        help="comma-separated; must include the advertised port")
    parser.add_argument("--advertise-host", required=True,
                        help="dotted quad the client is redirected to; it parses "
                             "this by splitting on '.', so a hostname will not do")
    parser.add_argument("--advertise-port", type=int, default=10001)
    parser.add_argument("--db", default="backend.db")
    parser.add_argument("--transcript", help="JSONL of every exchange")
    parser.add_argument("--buddy-port", type=int,
                        help="also run the buddy/presence stub on this port")
    parser.add_argument("--roster-date",
                        help="roster version handed to the client in `news`; "
                             "omitted by default, which leaves it at zero")
    parser.add_argument("--roster-csum",
                        help="roster checksum, as above")
    parser.add_argument("--roster-db",
                        help="path to DB_TEAMS.DAT; the announced checksum is "
                             "computed from it, so the console agrees its "
                             "roster is current. Overridden by --roster-csum.")
    parser.add_argument("--roster-csum-sweep",
                        help="comma-separated candidate checksums; a different "
                             "one is announced on each login, so several can be "
                             "tested in one session instead of one per boot")
    parser.add_argument("--pair-any", action="store_true",
                        help="pair any two waiting quickmatch clients instead "
                             "of requiring equal KIND. TESTING ONLY: a console "
                             "computes KIND from its own build and settings, so "
                             "a stand-in client cannot otherwise meet it in the "
                             "queue.")
    parser.add_argument("--roster-payload",
                        help="league database to SERVE as an update. Published "
                             "over HTTP and named in the `new2` manifest; the "
                             "console downloads it and installs it as LEAG. "
                             "Its length must equal the console's own league "
                             "file or the client refuses it.")
    parser.add_argument("--roster-crc", type=lambda v: int(v, 0),
                        help="override the CRC advertised for the payload. "
                             "Diagnostic: a deliberately wrong value lets the "
                             "transfer run to completion and fail at the "
                             "checksum instead, which tests whether the size "
                             "was acceptable without installing anything.")
    parser.add_argument("--http-port", type=int, default=10080,
                        help="port for the roster download (default 10080)")
    parser.add_argument("--quiet", action="store_true")

    group = parser.add_argument_group(
        "limits",
        "Bounds on what one host can make this process do. The defaults are "
        "far above anything a real console produces; 0 disables a limit, which "
        "is reasonable on a LAN and is not on a public address.")
    group.add_argument("--max-connections", type=int,
                       default=limits.DEFAULT_MAX_CONNECTIONS,
                       help="concurrent connections across all game ports "
                            "(default %(default)s, 0 for unlimited)")
    group.add_argument("--max-connections-per-ip", type=int,
                       default=limits.DEFAULT_MAX_PER_IP,
                       help="concurrent connections from one address. A "
                            "console holds two during the @dir redirect "
                            "(default %(default)s, 0 for unlimited)")
    group.add_argument("--send-timeout", type=float, default=hub.SEND_TIMEOUT,
                       help="seconds one write may take before the peer is "
                            "judged wedged and dropped. Writes are serialised "
                            "per connection, so without this a peer that stops "
                            "reading freezes its whole room "
                            "(default %(default)s)")
    group.add_argument("--idle-timeout", type=float,
                       default=service_module.IDLE_TIMEOUT,
                       help="seconds an established connection may go quiet. "
                            "We ping every %ds and the client echoes, so this "
                            "allows several missed echoes "
                            "(default %%(default)s)" % int(hub.PING_AFTER))
    group.add_argument("--first-byte-timeout", type=float,
                       default=service_module.FIRST_BYTE_DEADLINE,
                       help="seconds a connection may stay silent having sent "
                            "nothing at all (default %(default)s)")
    group.add_argument("--pre-auth-timeout", type=float,
                       default=service_module.PRE_AUTH_DEADLINE,
                       help="seconds a connection may talk without ever "
                            "logging in. Catches the socket that sends one "
                            "byte a minute and so never looks idle "
                            "(default %(default)s)")

    rate = parser.add_argument_group(
        "rate limiting",
        "Defaults to OBSERVING, not enforcing. The thresholds below are not "
        "measured -- the transcripts that would have set them did not survive "
        "-- and this client's response to a refused message is to wait "
        "forever rather than report anything. Run a session, read "
        "nfl_rate_peak_messages_per_second off the metrics page, set the "
        "limits near ten times it, then pass --rate-limit enforce.")
    rate.add_argument("--rate-limit", choices=("off", "observe", "enforce"),
                      default="observe",
                      help="off disables the buckets entirely; observe counts "
                           "and logs violations without acting on them; "
                           "enforce closes the connection (default %(default)s)")
    rate.add_argument("--rate", type=float, default=limits.DEFAULT_RATE,
                      help="sustained messages per second per connection "
                           "(default %(default)s)")
    rate.add_argument("--rate-burst", type=float, default=limits.DEFAULT_BURST,
                      help="bucket capacity per connection (default %(default)s)")
    rate.add_argument("--rate-per-ip", type=float,
                      default=limits.DEFAULT_IP_RATE,
                      help="sustained messages per second per address "
                           "(default %(default)s)")
    rate.add_argument("--rate-per-ip-burst", type=float,
                      default=limits.DEFAULT_IP_BURST,
                      help="bucket capacity per address (default %(default)s)")

    ban = parser.add_argument_group(
        "bans",
        "Strikes come from hard signals only -- malformed framing, a "
        "connection that never speaks, one that never logs in. A rate "
        "violation counts only when --rate-limit enforce is set, because "
        "banning on an unmeasured threshold takes a real player off the "
        "service with no way to tell them why.")
    ban.add_argument("--ban-threshold", type=int,
                     default=limits.DEFAULT_BAN_THRESHOLD,
                     help="strikes within the window before an address is "
                          "refused; 0 disables banning (default %(default)s)")
    ban.add_argument("--ban-window", type=float,
                     default=limits.DEFAULT_BAN_WINDOW,
                     help="seconds over which strikes accumulate "
                          "(default %(default)s)")
    ban.add_argument("--ban-ttl", type=float, default=limits.DEFAULT_BAN_TTL,
                     help="seconds a ban lasts (default %(default)s)")

    obs = parser.add_argument_group(
        "metrics",
        "An operator's view of the process, on loopback. Without it, 'nobody "
        "is playing' and 'everybody is being refused' look identical from "
        "outside. Counters are aggregate and carry no addresses.")
    obs.add_argument("--metrics-port", type=int, default=metrics.DEFAULT_PORT,
                     help="port for the counters; 0 disables "
                          "(default %(default)s)")
    obs.add_argument("--metrics-bind", default=metrics.DEFAULT_BIND,
                     help="address for the counters (default %(default)s)")
    obs.add_argument("--metrics-allow-public", action="store_true",
                     help="permit a non-loopback bind. There is no "
                          "authentication on this endpoint.")
    return parser


#: Held for the lifetime of the process, so a second server cannot start on the
#: same ports. The file is only a handle for the lock -- the kernel releases it
#: automatically if we are killed, so a crash cannot leave a stale one behind.
_LOCK_HANDLE = None


def _claim_ports(ports: List[int]) -> Optional[str]:
    """Take an exclusive lock on this port set, or say who already has it.

    Returns None on success, or a description of the holder.

    This is enforced here rather than in serve-madden.sh because the script only
    protects the people who use it. Two servers on the same ports is not a
    harmless mistake: the second fails to bind while the first keeps answering,
    so a console goes on talking to whatever configuration the *old* one had.
    That produced an empty roster manifest during a hardware test and cost an
    evening, because everything looked like it had restarted.
    """
    global _LOCK_HANDLE
    name = "nfl-backend-%s.lock" % "-".join(str(p) for p in sorted(ports))
    path = os.path.join(tempfile.gettempdir(), name)
    handle = open(path, "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno not in (errno.EACCES, errno.EAGAIN):
            raise
        handle.seek(0)
        holder = handle.read().strip() or "an unknown process"
        handle.close()
        return holder
    handle.seek(0)
    handle.truncate()
    handle.write("PID %d: %s\n" % (os.getpid(), " ".join(sys.argv)))
    handle.flush()
    _LOCK_HANDLE = handle          # keep it open; closing releases the lock
    atexit.register(handle.close)
    return None


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.advertise_port not in args.port:
        print("error: --advertise-port %d is not in --port %s. The client "
              "reconnects to the advertised port, so it must be listening."
              % (args.advertise_port, ",".join(str(p) for p in args.port)),
              file=sys.stderr)
        return 2
    if not all(part.isdigit() for part in args.advertise_host.split(".")) \
            or args.advertise_host.count(".") != 3:
        print("error: --advertise-host must be a dotted quad like 192.168.1.10; "
              "the client parses it octet by octet.", file=sys.stderr)
        return 2

    holder = _claim_ports(args.port)
    if holder is not None:
        print("error: a backend already owns ports %s.\n  %s\n"
              "Stop it first, or run serve-madden.sh with REPLACE=1."
              % (", ".join(str(p) for p in args.port), holder), file=sys.stderr)
        return 2

    try:
        store = Store(args.db)
    except StoreError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1

    store.seed_defaults()

    config = {
        "advertise_host": args.advertise_host,
        "advertise_port": str(args.advertise_port),
        "mask": "GS",
        # Told to the client in `news`; it hands both to its buddy manager.
        "buddy_host": args.advertise_host,
        "buddy_port": args.buddy_port or 0,
    }
    if args.roster_date:
        config["roster_date"] = args.roster_date
    # An explicit --roster-csum wins, so a value observed on hardware can always
    # override whatever the tool derives.
    if args.roster_db and not args.roster_csum:
        try:
            # Imported here, not at module scope: the server runs perfectly well
            # without a roster, and should not fail to start over a tool it is
            # not being asked to use.
            from tools import roster_checksum
            value, rows = roster_checksum.from_file(args.roster_db)
        except Exception as exc:                      # any read or parse failure
            print("error: cannot compute a checksum from %s: %s"
                  % (args.roster_db, exc), file=sys.stderr)
            return 2
        if not rows:
            print("error: %s has no players on teams 1-32; wrong file?"
                  % args.roster_db, file=sys.stderr)
            return 2
        config["roster_csum"] = str(value)
        if not args.quiet:
            print("roster: %d players from %s -> CSUM %d (0x%08x)"
                  % (len(rows), args.roster_db, value, value))
    if args.roster_csum:
        config["roster_csum"] = args.roster_csum
    if args.roster_csum_sweep:
        candidates = [c.strip() for c in args.roster_csum_sweep.split(",")
                      if c.strip()]
        if not candidates:
            print("error: --roster-csum-sweep listed no candidates",
                  file=sys.stderr)
            return 2
        config["roster_csum_sweep"] = candidates
        config["_sweep_index"] = 0
        if not args.quiet:
            print("roster: sweeping %d checksum candidates, one per login:"
                  % len(candidates))
            for i, c in enumerate(candidates, 1):
                print("   %d. %s" % (i, c))

    if args.pair_any:
        config["pair_any"] = True
        if not args.quiet:
            print("matchmaking: PAIR-ANY is on -- any two waiting clients are "
                  "matched regardless of KIND. Testing only.")

    roster_server = None
    if args.roster_payload:
        from .rosterfile import RosterFileError, RosterServer, load, manifest_url
        try:
            payload, crc = load(args.roster_payload)
            roster_server = RosterServer(
                payload, on_event=None if args.quiet else print)
            bound = roster_server.start("0.0.0.0", args.http_port)
        except RosterFileError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
        config["roster_url"] = manifest_url(args.advertise_host, bound)
        config["roster_file_crc"] = crc

        # Announce the checksum of the roster we are SERVING, not of some other
        # file. After installing, the console recomputes over what it received;
        # if we go on announcing a different roster's value it concludes the
        # fresh install is already stale and offers the same update forever.
        # Deriving it from the payload makes the two agree by construction.
        if not args.roster_csum:
            try:
                from tools import madden_tdb, roster_checksum
                table = madden_tdb.Database(payload, 0).table("PLAY")
                team = roster_checksum.FIELDS.index("TGID")
                rows = sorted(
                    (r for r in table.rows(roster_checksum.FIELDS)
                     if 1 <= r[team] <= 32), key=lambda r: r[0])
                if rows:
                    served = roster_checksum.checksum(rows)
                    if str(served) != str(config.get("roster_csum", "")):
                        if not args.quiet:
                            print("roster: announcing the SERVED roster's "
                                  "checksum %d (%d players)"
                                  % (served, len(rows)))
                        config["roster_csum"] = str(served)
            except Exception as exc:            # not a LEAG database
                if not args.quiet:
                    print("roster: cannot read a checksum out of the payload "
                          "(%s); announcing --roster-db's instead" % exc) if args.roster_crc is None else args.roster_crc
        if args.roster_crc is not None and not args.quiet:
            print("roster CRC OVERRIDDEN to %d -- the console will reject the "
                  "download at the checksum. Diagnostic only."
                  % args.roster_crc)
        if not args.quiet:
            print("roster payload: %d bytes, CRC %d (0x%08x)"
                  % (len(payload), crc, crc))
            print("roster URL: %s" % config["roster_url"])
            # The client checks the download against its OWN league file's
            # length, so a mismatch here fails the same way a corrupt transfer
            # does. Worth saying out loud rather than debugging on hardware.
            print("            (the console will reject this unless its own "
                  "league database is exactly %d bytes)" % len(payload))
    transcript = Transcript(args.transcript)
    buddy_service = None
    if args.buddy_port:
        # A separate endpoint. The client only learns its address after login,
        # so it cannot gate reaching a lobby -- the stub exists to keep the
        # buddy layer quiet rather than because anything depends on it.
        from . import buddy as buddy_module
        from .buddy import BuddyService
        buddy_service = BuddyService(
            verbose=not args.quiet, transcript=transcript,
            # Its own counters, not the game service's: a console holds one
            # buddy connection and two game ones, so sharing a per-address cap
            # would make the two endpoints compete for the same budget.
            limiter=limits.ConnectionLimiter(
                total=min(args.max_connections,
                          buddy_module.DEFAULT_MAX_CONNECTIONS)
                if args.max_connections else 0,
                per_ip=min(args.max_connections_per_ip,
                           buddy_module.DEFAULT_MAX_PER_IP)
                if args.max_connections_per_ip else 0),
            send_timeout=args.send_timeout,
            idle_timeout=args.idle_timeout,
            first_byte_deadline=args.first_byte_timeout)
        threading.Thread(
            target=buddy_service.serve_forever,
            args=(args.bind, args.buddy_port), daemon=True).start()

    if args.rate_limit == "off":
        # Zero rate and zero burst is the limiter's "unlimited"; keep the
        # object so every call site stays uniform.
        rates = limits.RateLimiter(0, 0, 0, 0, enforce=False)
    else:
        rates = limits.RateLimiter(
            rate=args.rate, burst=args.rate_burst,
            ip_rate=args.rate_per_ip, ip_burst=args.rate_per_ip_burst,
            enforce=args.rate_limit == "enforce")

    counters = metrics.Metrics()
    service = Service(
        store, config, transcript, verbose=not args.quiet,
        limiter=limits.ConnectionLimiter(total=args.max_connections,
                                         per_ip=args.max_connections_per_ip),
        send_timeout=args.send_timeout,
        idle_timeout=args.idle_timeout,
        first_byte_deadline=args.first_byte_timeout,
        pre_auth_deadline=args.pre_auth_timeout,
        rates=rates,
        bans=limits.BanList(threshold=args.ban_threshold,
                            window=args.ban_window, ttl=args.ban_ttl),
        metrics=counters)

    metrics_server = None
    if args.metrics_port:
        metrics_server = metrics.MetricsServer(counters)
        try:
            bound = metrics_server.start(args.metrics_bind, args.metrics_port,
                                         allow_public=args.metrics_allow_public)
            if not args.quiet:
                print("[ea] metrics on http://%s:%d/"
                      % (args.metrics_bind, bound), flush=True)
        except metrics.MetricsError as exc:
            # Never fatal. Losing the counters is worth a warning; refusing to
            # run a game server because a diagnostic port is taken is not.
            print("warning: %s" % exc, file=sys.stderr)
            metrics_server = None
        else:
            atexit.register(metrics_server.stop)
    try:
        service.serve_forever(args.bind, args.port)
    except ServiceError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        if buddy_service is not None:
            buddy_service.stop()
        if roster_server is not None:
            roster_server.stop()
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

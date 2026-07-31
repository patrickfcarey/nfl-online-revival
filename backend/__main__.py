"""CLI: ``python -m backend [options]``."""

from __future__ import annotations

import argparse
import sys
import threading
from typing import List, Optional

from . import handlers  # noqa: F401  -- importing registers the handlers
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
    parser.add_argument("--roster-payload",
                        help="league database to SERVE as an update. Published "
                             "over HTTP and named in the `new2` manifest; the "
                             "console downloads it and installs it as LEAG. "
                             "Its length must equal the console's own league "
                             "file or the client refuses it.")
    parser.add_argument("--http-port", type=int, default=10080,
                        help="port for the roster download (default 10080)")
    parser.add_argument("--quiet", action="store_true")
    return parser


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
        from .buddy import BuddyService
        buddy_service = BuddyService(verbose=not args.quiet,
                                     transcript=transcript)
        threading.Thread(
            target=buddy_service.serve_forever,
            args=(args.bind, args.buddy_port), daemon=True).start()

    service = Service(store, config, transcript, verbose=not args.quiet)
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

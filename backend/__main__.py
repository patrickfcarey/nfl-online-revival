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
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

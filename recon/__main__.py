"""CLI dispatch: ``python -m recon <dns|sink|classify|pcap> ...``.

Argument problems are reported as messages, not tracebacks -- these commands are
run at a console next to a booting console, and a stack trace there costs a
capture session.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional


def _port_list(text: str) -> List[int]:
    """Parse "80,443" into ports, rejecting anything unusable."""
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
            raise argparse.ArgumentTypeError("port %d is out of range 1-65535" % port)
        ports.append(port)
    if not ports:
        raise argparse.ArgumentTypeError("no ports given")
    return ports


def _port(text: str) -> int:
    return _port_list(text)[0]


def _hex_bytes(text: str) -> bytes:
    cleaned = text.replace(" ", "").replace(":", "")
    try:
        return bytes.fromhex(cleaned)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "--respond-hex must be hex bytes, e.g. 5c6f6b5c (got %r)" % text)


def _host_ip(text: str) -> tuple:
    if "=" not in text:
        raise argparse.ArgumentTypeError("--map expects HOST=IP, got %r" % text)
    host, ip = text.split("=", 1)
    host, ip = host.strip(), ip.strip()
    if not host or not ip:
        raise argparse.ArgumentTypeError("--map expects HOST=IP, got %r" % text)
    return host, ip


def _cmd_dns(args: argparse.Namespace) -> int:
    from . import dnsd

    hostmap = dict(args.map or [])
    if args.ip is None and not hostmap:
        print("error: give --ip (answer everything) and/or --map HOST=IP.\n"
              "       With neither, every lookup returns NXDOMAIN and the game "
              "is redirected nowhere.", file=sys.stderr)
        return 2
    try:
        if args.ip is not None:
            dnsd.validate_ip(args.ip, "--ip")
        for host, ip in hostmap.items():
            dnsd.validate_ip(ip, "--map %s" % host)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    try:
        dnsd.serve(bind=args.bind, port=args.port, default_ip=args.ip,
                   hostmap=hostmap)
    except OSError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    return 0


def _cmd_sink(args: argparse.Namespace) -> int:
    from . import sinkd

    if not args.tcp and not args.udp:
        print("error: give at least one --tcp and/or --udp port.", file=sys.stderr)
        return 2
    try:
        sinkd.serve(bind=args.bind, tcp_ports=args.tcp or [],
                    udp_ports=args.udp or [], transcript_path=args.out,
                    respond=args.respond_hex)
    except sinkd.SinkError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    except OSError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    return 0


def _cmd_classify(args: argparse.Namespace) -> int:
    from . import classify

    try:
        if args.transcript:
            classify.classify_transcript(args.path)
        else:
            classify.classify_pcap(args.path)
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    return 0


def _cmd_pcap(args: argparse.Namespace) -> int:
    from . import pcapreader

    shown = 0
    try:
        for flow in pcapreader.read_flows_path(args.path):
            head = flow.payload[:48]
            ascii_head = "".join(chr(b) if 32 <= b < 127 else "." for b in head)
            print("%.3f %s %s:%d -> %s:%d  %dB  %s"
                  % (flow.ts, flow.proto, flow.src, flow.sport,
                     flow.dst, flow.dport, len(flow.payload), ascii_head))
            shown += 1
            if args.max and shown >= args.max:
                break
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    print("(%d flow record(s))" % shown)
    if shown == 0:
        print("note: no TCP/UDP packets decoded. Check the capture filter, and "
              "that the file is classic pcap from tcpdump.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recon", description="NFL online-revival Phase 1 recon harness.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_dns = sub.add_parser("dns", help="answer the game's DNS lookups")
    p_dns.add_argument("--bind", default="0.0.0.0")
    p_dns.add_argument("--port", type=_port, default=53)
    p_dns.add_argument("--ip", help="default A answer for unmapped names")
    p_dns.add_argument("--map", action="append", type=_host_ip, metavar="HOST=IP",
                       help="host (or parent domain) -> ip override; repeatable")
    p_dns.set_defaults(func=_cmd_dns)

    p_sink = sub.add_parser("sink", help="sinkhole and log client connections")
    p_sink.add_argument("--bind", default="0.0.0.0")
    p_sink.add_argument("--tcp", type=_port_list, help="comma-separated TCP ports")
    p_sink.add_argument("--udp", type=_port_list, help="comma-separated UDP ports")
    p_sink.add_argument("--out", help="JSONL transcript path")
    p_sink.add_argument("--respond-hex", type=_hex_bytes,
                        help="canned reply, hex (e.g. 5c6f6b5c)")
    p_sink.set_defaults(func=_cmd_sink)

    p_cls = sub.add_parser("classify", help="fingerprint a capture")
    p_cls.add_argument("path", help="a .pcap, or a sink transcript with --transcript")
    p_cls.add_argument("--transcript", action="store_true",
                       help="input is a sinkd JSONL transcript, not a pcap")
    p_cls.set_defaults(func=_cmd_classify)

    p_pcap = sub.add_parser("pcap", help="dump TCP/UDP flows from a .pcap")
    p_pcap.add_argument("path")
    p_pcap.add_argument("--max", type=int, default=0, help="stop after N records")
    p_pcap.set_defaults(func=_cmd_pcap)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())

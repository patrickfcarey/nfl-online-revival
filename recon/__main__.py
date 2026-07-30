"""CLI dispatch: ``python -m recon <dns|sink|classify|pcap> ...``."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional


def _ports(text: str) -> List[int]:
    return [int(p) for p in text.split(",") if p.strip()]


def _hostmap(pairs: Optional[List[str]]) -> dict:
    out = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit("--map expects host=ip, got %r" % pair)
        host, ip = pair.split("=", 1)
        out[host.strip()] = ip.strip()
    return out


def _cmd_dns(args: argparse.Namespace) -> int:
    from . import dnsd
    dnsd.serve(bind=args.bind, port=args.port, default_ip=args.ip,
               hostmap=_hostmap(args.map))
    return 0


def _cmd_sink(args: argparse.Namespace) -> int:
    from . import sinkd
    respond = bytes.fromhex(args.respond_hex) if args.respond_hex else None
    sinkd.serve(bind=args.bind,
                tcp_ports=_ports(args.tcp) if args.tcp else [],
                udp_ports=_ports(args.udp) if args.udp else [],
                transcript_path=args.out, respond=respond)
    return 0


def _cmd_classify(args: argparse.Namespace) -> int:
    from . import classify
    if args.transcript:
        classify.classify_transcript(args.path)
    else:
        classify.classify_pcap(args.path)
    return 0


def _cmd_pcap(args: argparse.Namespace) -> int:
    from . import pcapreader
    shown = 0
    for flow in pcapreader.read_flows_path(args.path):
        head = flow.payload[:48]
        ascii_head = "".join(chr(b) if 32 <= b < 127 else "." for b in head)
        print("%.3f %s %s:%d -> %s:%d  %dB  %s"
              % (flow.ts, flow.proto, flow.src, flow.sport,
                 flow.dst, flow.dport, len(flow.payload), ascii_head))
        shown += 1
        if args.max and shown >= args.max:
            break
    print("(%d flow record(s))" % shown)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recon", description="NFL online-revival Phase 1 recon harness.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_dns = sub.add_parser("dns", help="answer the game's DNS lookups")
    p_dns.add_argument("--bind", default="0.0.0.0")
    p_dns.add_argument("--port", type=int, default=53)
    p_dns.add_argument("--ip", help="default A answer for unmapped names")
    p_dns.add_argument("--map", action="append", metavar="HOST=IP",
                       help="exact host->ip override (repeatable)")
    p_dns.set_defaults(func=_cmd_dns)

    p_sink = sub.add_parser("sink", help="sinkhole and log client connections")
    p_sink.add_argument("--bind", default="0.0.0.0")
    p_sink.add_argument("--tcp", help="comma-separated TCP ports")
    p_sink.add_argument("--udp", help="comma-separated UDP ports")
    p_sink.add_argument("--out", help="JSONL transcript path")
    p_sink.add_argument("--respond-hex", help="canned reply, hex (e.g. 5c6f6b5c)")
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

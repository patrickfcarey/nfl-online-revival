"""Fingerprint captured payloads to a likely network stack.

The point of Phase 1 is deciding which title sits on reusable infrastructure
(GameSpy -> OpenSpy) versus a proprietary stack you reconstruct from scratch.
This reads a sink transcript or a pcap, classifies the first meaningful payload
per *server* endpoint, and prints a verdict per ``host:port`` plus a summary.

Direction matters: only the client's own bytes identify the protocol it speaks,
so each flow's server side is resolved first (by TCP SYN where available, else
by which side was seen first, with a well-known-port tiebreak) and replies are
counted but never fingerprinted. Without that, every reply would invent a
second "endpoint" on the client's ephemeral port.

Signatures are deliberately conservative: a confident hit names the token it
matched; everything else falls back to an entropy read (plaintext vs
encrypted/compressed) so an unknown is never silently called known.
"""

from __future__ import annotations

import json
import math
from typing import Dict, Optional, Tuple

# Ports that strongly imply a stack, independent of payload bytes.
_PORT_HINTS = {
    27900: "gamespy (heartbeat/master, udp)",
    27901: "gamespy (natneg)",
    28900: "gamespy (server browser / master list)",
    29900: "gamespy (presence & messaging, GP)",
    29901: "gamespy (presence search)",
    29910: "gamespy (CD-key auth)",
    6500: "gamespy (query report)",
    13139: "gamespy (available-search, udp)",
}

_GAMESPY_TOKENS = (b"\\gamename\\", b"\\challenge\\", b"\\secure\\",
                   b"\\login\\", b"\\getpid\\", b"\\gpsp\\", b"\\basic\\",
                   b"\\status\\", b"\\heartbeat\\")
#: Strong enough to stand alone anywhere in the header.
_EA_TOKENS = (b"TXN=", b"theater", b"Blaze", b"easfc")
#: FESL component names -- short and generic, so only trusted inside text.
_EA_WEAK_TOKENS = (b"fsys", b"acct", b"pnow", b"subs")
_DNAS_TOKENS = (b"DNAS", b"dnas")
_HTTP_TOKENS = (b"GET ", b"POST ", b"HEAD ", b"PUT ", b"HTTP/")


def shannon_entropy(data: bytes) -> float:
    """Bits per byte, 0..8. High means encrypted or already compressed."""
    if not data:
        return 0.0
    counts = [0] * 256
    for byte in data:
        counts[byte] += 1
    n = len(data)
    entropy = 0.0
    for count in counts:
        if count:
            p = count / n
            entropy -= p * math.log2(p)
    return entropy


def _mostly_text(data: bytes, threshold: float = 0.85) -> bool:
    """True when the bytes look like text rather than a binary struct."""
    if not data:
        return False
    printable = sum(1 for b in data if 32 <= b < 127 or b in (9, 10, 13))
    return printable / len(data) >= threshold


def _show(token: bytes) -> str:
    """Render a signature token readably (GameSpy keys are backslash-heavy)."""
    return token.decode("ascii", "replace")


def classify_payload(data: bytes) -> Tuple[str, str]:
    """Return (label, evidence) for one payload."""
    if not data:
        return "empty", "no bytes"
    if data[0] == 0x16 and len(data) > 2 and data[1] == 0x03:
        return "tls", "TLS/SSL handshake record (0x16 0x03..)"
    for token in _HTTP_TOKENS:
        if data.startswith(token):
            return "http", "starts with %s" % _show(token)
    for token in _GAMESPY_TOKENS:
        if token in data:
            return "gamespy", "token %s" % _show(token)
    if data.startswith(b"\\") and data.count(b"\\") >= 4:
        return "gamespy?", "backslash key/value framing"
    for token in _EA_TOKENS:
        if token in data[:64]:
            return "ea", "token %s" % _show(token)
    # Four-letter component names would otherwise match inside binary or
    # encrypted payloads and send the whole investigation the wrong way.
    if _mostly_text(data[:64]):
        for token in _EA_WEAK_TOKENS:
            if token in data[:64]:
                return "ea", "component name %s in text payload" % _show(token)
    for token in _DNAS_TOKENS:
        if token in data:
            return "ps2-dnas", "token %s" % _show(token)
    entropy = shannon_entropy(data)
    if len(data) >= 32:
        if entropy > 7.2:
            return "encrypted/compressed?", "entropy %.2f bits/byte" % entropy
        return "plaintext-unknown", "entropy %.2f bits/byte" % entropy
    return "short-unknown", "%d bytes: %s" % (len(data), data[:16].hex())


class _Endpoint:
    """First meaningful client->server payload seen for one host:port."""

    def __init__(self, proto: str, host: str, port: int) -> None:
        self.proto = proto
        self.host = host
        self.port = port
        self.first_payload: Optional[bytes] = None
        self.to_server = 0
        self.from_server = 0

    def observe(self, payload: bytes, to_server: bool) -> None:
        if to_server:
            self.to_server += 1
            if self.first_payload is None and payload:
                self.first_payload = payload
        else:
            self.from_server += 1

    def verdict(self) -> Dict[str, str]:
        if self.first_payload:
            label, evidence = classify_payload(self.first_payload)
        elif self.from_server:
            label, evidence = "no-payload", "connected; no client payload captured"
        else:
            # The common shape against a dead service: dialled, nothing answered.
            label, evidence = "no-reply", "client dialled; server never answered"
        return {
            "endpoint": "%s %s:%d" % (self.proto, self.host, self.port),
            "pkts": "%d/%d" % (self.to_server, self.from_server),
            "stack": label,
            "evidence": evidence,
            "port_hint": _PORT_HINTS.get(self.port, ""),
        }


def _pick_server(flow) -> Tuple[str, int]:
    """Which end of this packet's flow is the server?

    A bare SYN settles it outright. Otherwise prefer a side whose port carries a
    known stack hint, then a privileged port, and finally the lower port number
    -- ephemeral client ports are high by convention.
    """
    if getattr(flow, "is_syn_open", False):
        return flow.dst, flow.dport
    src_known = flow.sport in _PORT_HINTS
    dst_known = flow.dport in _PORT_HINTS
    if src_known != dst_known:
        return (flow.src, flow.sport) if src_known else (flow.dst, flow.dport)
    src_priv = flow.sport < 1024
    dst_priv = flow.dport < 1024
    if src_priv != dst_priv:
        return (flow.src, flow.sport) if src_priv else (flow.dst, flow.dport)
    return ((flow.src, flow.sport) if flow.sport < flow.dport
            else (flow.dst, flow.dport))


def _report(endpoints: Dict[Tuple[str, str, int], _Endpoint]) -> None:
    if not endpoints:
        print("(no TCP/UDP flows found -- if the capture is not empty, check the "
              "pcap link type and the capture filter)")
        return
    rows = [ep.verdict() for ep in endpoints.values()]
    rows.sort(key=lambda r: r["endpoint"])
    print("%-34s %9s  %-22s %s" % ("SERVER ENDPOINT", "TO/FROM", "STACK", "EVIDENCE"))
    print("-" * 96)
    for row in rows:
        print("%-34s %9s  %-22s %s"
              % (row["endpoint"], row["pkts"], row["stack"], row["evidence"]))
        if row["port_hint"]:
            print("%-34s %9s  %-22s port hint: %s" % ("", "", "", row["port_hint"]))
    stacks = sorted({r["stack"] for r in rows})
    print("\nsummary: %d server endpoint(s); stacks seen: %s"
          % (len(rows), ", ".join(stacks)))
    print("(TO/FROM = packets client->server / server->client)")


def classify_pcap(path: str) -> None:
    """Classify by server endpoint, resolving each flow's direction first."""
    from . import pcapreader

    endpoints: Dict[Tuple[str, str, int], _Endpoint] = {}
    servers: Dict[Tuple[str, Tuple], Tuple[str, int]] = {}
    for flow in pcapreader.read_flows_path(path):
        ends = ((flow.src, flow.sport), (flow.dst, flow.dport))
        pair = (flow.proto, tuple(sorted(ends)))
        # A later bare SYN is authoritative and upgrades an earlier guess.
        if pair not in servers or getattr(flow, "is_syn_open", False):
            servers[pair] = _pick_server(flow)
        server = servers[pair]
        key = (flow.proto, server[0], server[1])
        ep = endpoints.get(key)
        if ep is None:
            ep = endpoints[key] = _Endpoint(flow.proto, server[0], server[1])
        ep.observe(flow.payload, to_server=((flow.dst, flow.dport) == server))
    _report(endpoints)


def classify_transcript(path: str) -> None:
    """Classify a sinkd JSONL transcript (recv rows carry the client's bytes)."""
    endpoints: Dict[Tuple[str, str, int], _Endpoint] = {}
    with open(path, "r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                proto, port = row["proto"], int(row["port"])
                payload = bytes.fromhex(row.get("hex", ""))
            except (ValueError, KeyError) as exc:
                print("(skipping malformed transcript line %d: %s)" % (lineno, exc))
                continue
            key = (proto, "sink", port)
            ep = endpoints.get(key)
            if ep is None:
                ep = endpoints[key] = _Endpoint(proto, "sink", port)
            ep.observe(payload, to_server=(row.get("dir") == "recv"))
    _report(endpoints)

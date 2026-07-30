"""Fingerprint captured payloads to a likely network stack.

The point of Phase 1 is deciding which title sits on reusable infrastructure
(GameSpy -> OpenSpy) versus a proprietary stack you reconstruct from scratch.
This reads a sink transcript or a pcap, classifies the first meaningful payload
per server endpoint, and prints a verdict per ``host:port`` plus a summary.

Signatures are deliberately conservative: a confident hit names the token it
matched; everything else falls back to an entropy read (plaintext vs
encrypted/compressed) so an unknown is never silently called known.
"""

from __future__ import annotations

import json
import math
from typing import Dict, List, Optional, Tuple

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
_EA_TOKENS = (b"TXN=", b"\nTXN=", b"fsys", b"acct", b"theater", b"pnow",
              b"subs", b"Blaze", b"easfc")
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


def classify_payload(data: bytes) -> Tuple[str, str]:
    """Return (label, evidence) for one payload."""
    if not data:
        return "empty", "no bytes"
    if data[0] == 0x16 and len(data) > 2 and data[1] == 0x03:
        return "tls", "TLS/SSL handshake record (0x16 0x03..)"
    for token in _HTTP_TOKENS:
        if data.startswith(token):
            return "http", "starts with %r" % token.decode("ascii", "replace")
    for token in _GAMESPY_TOKENS:
        if token in data:
            return "gamespy", "token %r" % token.decode("ascii", "replace")
    if data.startswith(b"\\") and data.count(b"\\") >= 4:
        return "gamespy?", "backslash key/value framing"
    for token in _EA_TOKENS:
        if token in data[:64]:
            return "ea", "token %r" % token.decode("ascii", "replace")
    for token in _DNAS_TOKENS:
        if token in data:
            return "ps2-dnas", "token %r" % token.decode("ascii", "replace")
    if len(data) >= 32:
        entropy = shannon_entropy(data)
        if entropy > 7.2:
            return "encrypted/compressed?", "entropy %.2f bits/byte" % entropy
        return "plaintext-unknown", "entropy %.2f bits/byte" % shannon_entropy(data)
    return "short-unknown", "%d bytes: %s" % (len(data), data[:16].hex())


class _Endpoint:
    """First meaningful client->server payload seen for one host:port."""

    def __init__(self, proto: str, host: str, port: int) -> None:
        self.proto = proto
        self.host = host
        self.port = port
        self.first_payload: Optional[bytes] = None
        self.packets = 0

    def observe(self, payload: bytes) -> None:
        self.packets += 1
        if self.first_payload is None and payload:
            self.first_payload = payload

    def verdict(self) -> Dict[str, str]:
        label, evidence = (classify_payload(self.first_payload)
                           if self.first_payload else ("no-payload", "client sent nothing"))
        hint = _PORT_HINTS.get(self.port, "")
        return {
            "endpoint": "%s %s:%d" % (self.proto, self.host, self.port),
            "packets": str(self.packets),
            "stack": label,
            "evidence": evidence,
            "port_hint": hint,
        }


def _report(endpoints: Dict[Tuple[str, str, int], _Endpoint]) -> None:
    if not endpoints:
        print("(no TCP/UDP client->server payloads found)")
        return
    rows = [ep.verdict() for ep in endpoints.values()]
    rows.sort(key=lambda r: r["endpoint"])
    print("%-34s %5s  %-22s %s" % ("ENDPOINT", "PKTS", "STACK", "EVIDENCE"))
    print("-" * 92)
    for row in rows:
        print("%-34s %5s  %-22s %s"
              % (row["endpoint"], row["packets"], row["stack"], row["evidence"]))
        if row["port_hint"]:
            print("%-34s %5s  %-22s port hint: %s"
                  % ("", "", "", row["port_hint"]))
    stacks = sorted({r["stack"] for r in rows})
    print("\nsummary: %d endpoint(s); stacks seen: %s"
          % (len(rows), ", ".join(stacks)))


def classify_pcap(path: str) -> None:
    """Classify by server endpoint. A server endpoint is the (dst, dport) of a
    client->server packet; we treat the side that received the first data as the
    server, which is right for these client-initiated protocols."""
    from . import pcapreader

    endpoints: Dict[Tuple[str, str, int], _Endpoint] = {}
    for flow in pcapreader.read_flows_path(path):
        key = (flow.proto, flow.dst, flow.dport)
        ep = endpoints.get(key)
        if ep is None:
            ep = endpoints[key] = _Endpoint(flow.proto, flow.dst, flow.dport)
        ep.observe(flow.payload)
    _report(endpoints)


def classify_transcript(path: str) -> None:
    """Classify a sinkd JSONL transcript (only recv rows carry client bytes)."""
    endpoints: Dict[Tuple[str, str, int], _Endpoint] = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("dir") != "recv":
                continue
            key = (row["proto"], "sink", int(row["port"]))
            ep = endpoints.get(key)
            if ep is None:
                ep = endpoints[key] = _Endpoint(row["proto"], "sink", int(row["port"]))
            ep.observe(bytes.fromhex(row.get("hex", "")))
    _report(endpoints)

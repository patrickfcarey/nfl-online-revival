"""A small, controllable DNS responder.

Point the emulated console's DNS at the box running this, and every hostname
the game resolves is logged here -- which by itself enumerates the whole set of
servers a title contacts. Answers are steerable: a default IP for "send
everything to my sinkhole", or an exact host->IP map to redirect some names and
NXDOMAIN the rest.

UDP only, which is all these games use. Binding port 53 needs privilege on the
rig (``sudo``, or ``setcap cap_net_bind_service``); a high port plus a firewall
redirect works too -- see docs/emulator-capture.md.
"""

from __future__ import annotations

import socket
import struct
import time
from typing import Callable, Dict, Optional, Tuple

# DNS TYPE / CLASS / RCODE constants we care about.
_TYPE_A = 1
_CLASS_IN = 1
_RCODE_NOERROR = 0
_RCODE_NXDOMAIN = 3

_QTYPE_NAMES = {1: "A", 2: "NS", 5: "CNAME", 12: "PTR", 15: "MX", 16: "TXT",
                28: "AAAA", 33: "SRV", 255: "ANY"}


def _parse_question(msg: bytes) -> Tuple[str, int, int, int]:
    """Return (qname, qtype, qclass, offset_past_question).

    Only the first question is read (QDCOUNT is 1 in every real query these
    games send). Query names are not compressed, so labels are walked directly.
    """
    if len(msg) < 12:
        raise ValueError("short DNS message")
    if struct.unpack_from(">H", msg, 4)[0] < 1:
        raise ValueError("query carries no question (QDCOUNT=0)")
    offset = 12  # skip the 12-byte header
    labels = []
    while True:
        if offset >= len(msg):
            raise ValueError("truncated QNAME")
        length = msg[offset]
        offset += 1
        if length == 0:
            break
        if length & 0xC0:  # a compression pointer has no place in a question
            raise ValueError("unexpected pointer in question")
        labels.append(msg[offset:offset + length].decode("ascii", "replace"))
        offset += length
    if offset + 4 > len(msg):
        raise ValueError("truncated question")
    qtype, qclass = struct.unpack_from(">HH", msg, offset)
    return ".".join(labels), qtype, qclass, offset + 4


def build_response(query: bytes, answer_ip: Optional[str]) -> bytes:
    """Build a reply to *query*.

    ``answer_ip`` None -> NXDOMAIN. An A query for a resolvable name gets a
    single A record pointing at ``answer_ip``; any other qtype for a resolvable
    name gets NOERROR with no answer, so the client falls back to an A lookup
    rather than treating the name as dead.
    """
    if len(query) < 12:
        raise ValueError("short DNS message")
    if answer_ip is not None:
        validate_ip(answer_ip, "answer address")
    (msg_id,) = struct.unpack_from(">H", query, 0)
    (flags,) = struct.unpack_from(">H", query, 2)
    rd = flags & 0x0100  # preserve the client's recursion-desired bit
    qname, qtype, qclass, qend = _parse_question(query)
    question = query[12:qend]

    if answer_ip is None:
        header = struct.pack(">HHHHHH", msg_id, 0x8400 | rd | _RCODE_NXDOMAIN,
                             1, 0, 0, 0)
        return header + question

    if qtype == _TYPE_A and qclass == _CLASS_IN:
        header = struct.pack(">HHHHHH", msg_id, 0x8400 | rd | _RCODE_NOERROR,
                             1, 1, 0, 0)
        answer = (
            b"\xc0\x0c"                                   # name -> question at 0x0c
            + struct.pack(">HHIH", _TYPE_A, _CLASS_IN, 60, 4)
            + socket.inet_aton(answer_ip)
        )
        return header + question + answer

    # Resolvable name, but not an A query: NOERROR, zero answers.
    header = struct.pack(">HHHHHH", msg_id, 0x8400 | rd | _RCODE_NOERROR,
                         1, 0, 0, 0)
    return header + question


def validate_ip(text: str, label: str = "address") -> str:
    """Return *text* if it is a dotted-quad IPv4 address, else raise.

    Called at startup: an unusable answer address must not become an exception
    on the first query, which would take the responder down mid-capture.
    """
    try:
        socket.inet_aton(text)
    except OSError:
        raise ValueError("%s is not a valid IPv4 address: %r" % (label, text))
    if text.count(".") != 3:  # inet_aton also accepts "10" and "10.1"
        raise ValueError("%s must be a dotted quad, got %r" % (label, text))
    return text


def _resolve(qname: str, default_ip: Optional[str],
             hostmap: Dict[str, str]) -> Optional[str]:
    """Exact host wins, then any parent domain, then the default IP.

    Parent matching is deliberate: a title resolves several names under one
    domain, and mapping ``ea.com`` should catch ``easo.ea.com`` rather than
    silently NXDOMAIN it.
    """
    name = qname.lower().rstrip(".")
    mapped = hostmap.get(name)
    if mapped:
        return mapped
    labels = name.split(".")
    for index in range(1, len(labels)):
        mapped = hostmap.get(".".join(labels[index:]))
        if mapped:
            return mapped
    return default_ip


def serve(bind: str = "0.0.0.0", port: int = 53,
          default_ip: Optional[str] = None,
          hostmap: Optional[Dict[str, str]] = None,
          on_query: Optional[Callable[[str, str, Optional[str]], None]] = None
          ) -> None:
    """Run until interrupted, logging and answering queries."""
    hostmap = {k.lower().rstrip("."): v for k, v in (hostmap or {}).items()}
    if default_ip is not None:
        validate_ip(default_ip, "--ip")
    for host, ip in hostmap.items():
        validate_ip(ip, "--map %s" % host)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((bind, port))
    except OSError as exc:
        hint = (" Port %d needs root: rerun with sudo, or use a high port plus a "
                "firewall redirect." % port) if port < 1024 else ""
        raise OSError("cannot bind %s:%d: %s.%s" % (bind, port, exc, hint))
    where = "everything -> %s" % default_ip if default_ip else "map-only"
    print("[dns] listening on %s:%d (%s), %d mapped host(s)"
          % (bind, port, where, len(hostmap)), flush=True)

    while True:
        data, peer = sock.recvfrom(4096)
        try:
            qname, qtype, _qclass, _end = _parse_question(data)
        except ValueError as exc:
            print("[dns] %s malformed query: %s" % (peer[0], exc), flush=True)
            continue
        answer_ip = _resolve(qname, default_ip, hostmap)
        tname = _QTYPE_NAMES.get(qtype, str(qtype))
        stamp = time.strftime("%H:%M:%S")
        print("[dns] %s  %s  %-5s -> %s"
              % (stamp, peer[0], tname, answer_ip or "NXDOMAIN"), flush=True)
        if on_query is not None:
            on_query(qname, tname, answer_ip)
        try:
            sock.sendto(build_response(data, answer_ip), peer)
        except OSError as exc:
            print("[dns] send failed: %s" % exc, flush=True)

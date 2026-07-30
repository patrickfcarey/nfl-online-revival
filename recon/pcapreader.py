"""A dependency-free reader for classic ``.pcap`` captures.

Enough of libpcap and the link/IP/TCP/UDP headers to turn a capture taken at an
emulator's NIC (``tcpdump -w cap.pcap``) into a stream of flow records. No
scapy, no Wireshark. Classic pcap only -- pcapng (Wireshark's default) is
detected and rejected with a clear message; capture with tcpdump, which writes
classic pcap by default.

Link types are the practical trap here. ``tcpdump -i any`` on libpcap 1.10+
(Ubuntu 24.04 and newer) writes **LINUX_SLL2**, not the older LINUX_SLL, so both
are decoded; an unrecognised link type raises rather than silently yielding
nothing, because "0 flows" is indistinguishable from a failed capture.

Remember the layer: a NIC capture of original-Xbox *Xbox Live* traffic is
post-IPsec ciphertext. It still reveals hosts, ports and timing, but for
plaintext payloads you hook the emulator's socket boundary instead (see
docs/emulator-capture.md). PS2 and system-link traffic is usually readable here
directly.
"""

from __future__ import annotations

import socket
import struct
from typing import BinaryIO, Iterator, NamedTuple, Optional

# libpcap link-layer types we decode.
_LINKTYPE_NULL = 0            # BSD loopback: 4-byte host-order AF
_LINKTYPE_ETHERNET = 1
_LINKTYPE_RAW_BSD = 12        # raw IP on some platforms
_LINKTYPE_RAW_ALT = 14
_LINKTYPE_RAW = 101           # libpcap's canonical raw IPv4/IPv6
_LINKTYPE_LOOP = 108          # OpenBSD loopback: 4-byte network-order AF
_LINKTYPE_LINUX_SLL = 113     # "cooked" capture, 16-byte header
_LINKTYPE_LINUX_SLL2 = 276    # "cooked" v2, 20-byte header (libpcap >= 1.10)

_RAW_LINKTYPES = (_LINKTYPE_RAW, _LINKTYPE_RAW_BSD, _LINKTYPE_RAW_ALT)

#: Human-readable names for the error message on an unsupported link type.
LINKTYPE_NAMES = {
    _LINKTYPE_NULL: "NULL/loopback", _LINKTYPE_ETHERNET: "Ethernet",
    _LINKTYPE_RAW: "raw IP", _LINKTYPE_LOOP: "OpenBSD loopback",
    _LINKTYPE_LINUX_SLL: "LINUX_SLL", _LINKTYPE_LINUX_SLL2: "LINUX_SLL2",
}

_ETHERTYPE_IPV4 = 0x0800
_ETHERTYPE_VLAN = 0x8100

# TCP flag bits we expose, so a consumer can tell which side opened a flow.
TCP_FIN = 0x01
TCP_SYN = 0x02
TCP_RST = 0x04
TCP_ACK = 0x10


class UnsupportedLinkType(ValueError):
    """Raised for a link type this reader cannot decode."""


class Flow(NamedTuple):
    ts: float
    proto: str          # "tcp" | "udp"
    src: str
    sport: int
    dst: str
    dport: int
    payload: bytes
    tcp_flags: int = 0  # 0 for UDP; SYN-without-ACK marks the connecting side

    @property
    def is_syn_open(self) -> bool:
        """True for a bare SYN -- i.e. this packet's *destination* is the server."""
        return bool(self.tcp_flags & TCP_SYN) and not (self.tcp_flags & TCP_ACK)


def _decode_ip(packet: bytes, ts: float) -> Optional[Flow]:
    if len(packet) < 20 or (packet[0] >> 4) != 4:
        return None  # IPv4 only; these titles predate any IPv6 need
    ihl = (packet[0] & 0x0F) * 4
    if ihl < 20 or len(packet) < ihl:
        return None
    total_len = struct.unpack_from(">H", packet, 2)[0]
    # Bits 0-2 are flags, 3-15 the fragment offset. A non-zero offset means this
    # is a continuation fragment with no transport header -- decoding it would
    # invent ports and payload out of arbitrary bytes.
    frag_field = struct.unpack_from(">H", packet, 6)[0]
    if frag_field & 0x1FFF:
        return None
    proto = packet[9]
    src = socket.inet_ntoa(packet[12:16])
    dst = socket.inet_ntoa(packet[16:20])
    # Trust total_len only when it is sane; a sliced/padded frame may disagree.
    end = total_len if ihl <= total_len <= len(packet) else len(packet)
    body = packet[ihl:end]

    if proto == 6 and len(body) >= 20:          # TCP
        sport, dport = struct.unpack_from(">HH", body, 0)
        data_off = (body[12] >> 4) * 4
        flags = body[13]
        if data_off < 20 or data_off > len(body):
            return Flow(ts, "tcp", src, sport, dst, dport, b"", flags)
        return Flow(ts, "tcp", src, sport, dst, dport, body[data_off:], flags)
    if proto == 17 and len(body) >= 8:          # UDP
        sport, dport, ulen = struct.unpack_from(">HHH", body, 0)
        stop = ulen if 8 <= ulen <= len(body) else len(body)
        return Flow(ts, "udp", src, sport, dst, dport, body[8:stop], 0)
    return None


def _strip_link(linktype: int, frame: bytes) -> Optional[bytes]:
    """Return the IPv4 packet inside a link-layer frame, or None to skip it."""
    if linktype in _RAW_LINKTYPES:
        return frame
    if linktype == _LINKTYPE_ETHERNET:
        if len(frame) < 14:
            return None
        ethertype = struct.unpack_from(">H", frame, 12)[0]
        offset = 14
        if ethertype == _ETHERTYPE_VLAN:        # 802.1Q tag
            if len(frame) < 18:
                return None
            ethertype = struct.unpack_from(">H", frame, 16)[0]
            offset = 18
        return frame[offset:] if ethertype == _ETHERTYPE_IPV4 else None
    if linktype == _LINKTYPE_LINUX_SLL:
        # packet type, ARPHRD, addr len, addr[8], then protocol at offset 14.
        if len(frame) < 16:
            return None
        return frame[16:] if struct.unpack_from(">H", frame, 14)[0] == _ETHERTYPE_IPV4 else None
    if linktype == _LINKTYPE_LINUX_SLL2:
        # protocol at offset 0, then reserved, ifindex, ARPHRD, pkt type,
        # addr len, addr[8] -- 20 bytes total. This is what `-i any` writes on
        # libpcap >= 1.10, which is why it must be handled.
        if len(frame) < 20:
            return None
        return frame[20:] if struct.unpack_from(">H", frame, 0)[0] == _ETHERTYPE_IPV4 else None
    if linktype in (_LINKTYPE_NULL, _LINKTYPE_LOOP):
        if len(frame) < 4:
            return None
        order = "<" if linktype == _LINKTYPE_NULL else ">"
        family = struct.unpack_from(order + "I", frame, 0)[0]
        if family not in (2, 0x02000000):       # AF_INET, either byte order
            return None
        return frame[4:]
    raise UnsupportedLinkType(
        "pcap link type %d is not supported by this reader (known: %s). "
        "Recapture with `tcpdump -i <iface>` or `-i any`."
        % (linktype, ", ".join("%d=%s" % kv for kv in sorted(LINKTYPE_NAMES.items()))))


def read_flows(handle: BinaryIO) -> Iterator[Flow]:
    """Yield one Flow per TCP/UDP packet in a classic pcap stream."""
    header = handle.read(24)
    if len(header) < 24:
        raise ValueError("not a pcap file (truncated global header)")
    magic = header[:4]
    if magic == b"\x0a\x0d\x0d\x0a":
        raise ValueError(
            "this is a pcapng file, not classic pcap. Recapture with tcpdump "
            "(it writes classic pcap), or convert: "
            "`tcpdump -r in.pcapng -w out.pcap`")
    if magic == b"\xd4\xc3\xb2\xa1":
        endian, nano = "<", False
    elif magic == b"\xa1\xb2\xc3\xd4":
        endian, nano = ">", False
    elif magic == b"\x4d\x3c\xb2\xa1":
        endian, nano = "<", True
    elif magic == b"\xa1\xb2\x3c\x4d":
        endian, nano = ">", True
    else:
        raise ValueError("not a pcap file (bad magic %s)" % magic.hex())

    linktype = struct.unpack(endian + "I", header[20:24])[0]
    rec_hdr = endian + "IIII"
    while True:
        raw = handle.read(16)
        if len(raw) < 16:
            break
        ts_sec, ts_frac, incl_len, _orig_len = struct.unpack(rec_hdr, raw)
        packet = handle.read(incl_len)
        if len(packet) < incl_len:
            break  # truncated final record
        ts = ts_sec + (ts_frac / 1e9 if nano else ts_frac / 1e6)
        ip_packet = _strip_link(linktype, packet)
        if ip_packet is None:
            continue
        flow = _decode_ip(ip_packet, ts)
        if flow is not None:
            yield flow


def read_flows_path(path: str) -> Iterator[Flow]:
    with open(path, "rb") as handle:
        yield from read_flows(handle)

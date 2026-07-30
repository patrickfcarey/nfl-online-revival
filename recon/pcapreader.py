"""A dependency-free reader for classic ``.pcap`` captures.

Enough of libpcap and the link/IP/TCP/UDP headers to turn a capture taken at an
emulator's NIC (``tcpdump -w cap.pcap``) into a stream of flow records. No
scapy, no Wireshark. Classic pcap only -- pcapng (Wireshark's default) is
detected and rejected with a clear message; capture with tcpdump, which writes
classic pcap by default.

Remember the layer: a NIC capture of original-Xbox *Xbox Live* traffic is
post-IPsec ciphertext. It still reveals hosts, ports and timing, but for
plaintext payloads you hook the emulator's socket boundary instead (see
docs/emulator-capture.md). PS2 and system-link traffic is usually readable here
directly.
"""

from __future__ import annotations

import socket
import struct
from typing import BinaryIO, Iterator, NamedTuple

# libpcap link-layer types we decode.
_LINKTYPE_ETHERNET = 1
_LINKTYPE_RAW = 101      # raw IPv4/IPv6, no link header
_LINKTYPE_LINUX_SLL = 113


class Flow(NamedTuple):
    ts: float
    proto: str          # "tcp" | "udp"
    src: str
    sport: int
    dst: str
    dport: int
    payload: bytes


def _decode_ip(packet: bytes, ts: float) -> "Flow | None":
    if len(packet) < 20 or (packet[0] >> 4) != 4:
        return None  # IPv4 only; these titles predate any IPv6 need
    ihl = (packet[0] & 0x0F) * 4
    total_len = struct.unpack_from(">H", packet, 2)[0]
    proto = packet[9]
    src = socket.inet_ntoa(packet[12:16])
    dst = socket.inet_ntoa(packet[16:20])
    body = packet[ihl:total_len] if total_len else packet[ihl:]

    if proto == 6 and len(body) >= 20:          # TCP
        sport, dport = struct.unpack_from(">HH", body, 0)
        data_off = (body[12] >> 4) * 4
        return Flow(ts, "tcp", src, sport, dst, dport, body[data_off:])
    if proto == 17 and len(body) >= 8:          # UDP
        sport, dport, ulen = struct.unpack_from(">HHH", body, 0)
        end = ulen if 8 <= ulen <= len(body) else len(body)
        return Flow(ts, "udp", src, sport, dst, dport, body[8:end])
    return None


def _strip_link(linktype: int, frame: bytes) -> "bytes | None":
    """Return the IP packet inside a link-layer frame, or None to skip it."""
    if linktype == _LINKTYPE_RAW:
        return frame
    if linktype == _LINKTYPE_ETHERNET:
        if len(frame) < 14:
            return None
        ethertype = struct.unpack_from(">H", frame, 12)[0]
        offset = 14
        if ethertype == 0x8100:                 # 802.1Q VLAN tag
            if len(frame) < 18:
                return None
            ethertype = struct.unpack_from(">H", frame, 16)[0]
            offset = 18
        return frame[offset:] if ethertype == 0x0800 else None
    if linktype == _LINKTYPE_LINUX_SLL:
        if len(frame) < 16:
            return None
        return frame[16:] if struct.unpack_from(">H", frame, 14)[0] == 0x0800 else None
    return None


def read_flows(handle: BinaryIO) -> Iterator[Flow]:
    """Yield one Flow per TCP/UDP packet in a classic pcap stream."""
    header = handle.read(24)
    if len(header) < 24:
        raise ValueError("not a pcap file (truncated global header)")
    magic = header[:4]
    if magic == b"\x0a\x0d\x0d\x0a":
        raise ValueError("this is pcapng; recapture with tcpdump (classic pcap)")
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
            break
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

"""A connection sinkhole that logs everything a redirected client sends.

Once DNS points the game here, it opens TCP/UDP connections to ports it expects
a real server on. This accepts them all, hexdumps every byte to the console,
and writes a machine-readable JSONL transcript that ``classify`` can read back.
Optionally it replies with a canned byte string, to probe how the client reacts
to a server that speaks -- often enough to advance the handshake one more step.

Ports are given explicitly (learn them from a DNS run plus one NIC capture).
For a true catch-all, a firewall REDIRECT to one sink port also works; that is
Linux/root territory and lives in docs/emulator-capture.md rather than here.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import List, Optional, TextIO


def hexdump(data: bytes, width: int = 16) -> str:
    """Classic offset / hex / ascii dump."""
    lines = []
    for base in range(0, len(data), width):
        chunk = data[base:base + width]
        hexpart = " ".join("%02x" % b for b in chunk)
        asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append("  %04x  %-*s  %s" % (base, width * 3 - 1, hexpart, asciipart))
    return "\n".join(lines)


class _Transcript:
    """Thread-safe JSONL writer shared by every listener."""

    def __init__(self, handle: Optional[TextIO]) -> None:
        self._handle = handle
        self._lock = threading.Lock()

    def record(self, proto: str, port: int, peer: str, direction: str,
               payload: bytes) -> None:
        if self._handle is None:
            return
        row = {
            "ts": time.time(),
            "proto": proto,
            "port": port,
            "peer": peer,
            "dir": direction,           # "recv" (client->us) or "send" (us->client)
            "len": len(payload),
            "hex": payload.hex(),
        }
        line = json.dumps(row)
        with self._lock:
            self._handle.write(line + "\n")
            self._handle.flush()


def _log(proto: str, port: int, peer: str, direction: str, payload: bytes) -> None:
    arrow = "->" if direction == "recv" else "<-"
    print("\n[sink] %s %s/%d  %s %s  %d bytes"
          % (time.strftime("%H:%M:%S"), proto, port, peer, arrow, len(payload)),
          flush=True)
    if payload:
        print(hexdump(payload), flush=True)


def _serve_tcp_conn(conn: socket.socket, addr, port: int,
                    transcript: _Transcript, respond: Optional[bytes]) -> None:
    peer = "%s:%d" % addr
    replied = False
    try:
        while True:
            data = conn.recv(65535)
            if not data:
                break
            _log("tcp", port, peer, "recv", data)
            transcript.record("tcp", port, peer, "recv", data)
            if respond and not replied:
                conn.sendall(respond)
                _log("tcp", port, peer, "send", respond)
                transcript.record("tcp", port, peer, "send", respond)
                replied = True
    except OSError:
        pass
    finally:
        conn.close()


def _serve_tcp(bind: str, port: int, transcript: _Transcript,
               respond: Optional[bytes]) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((bind, port))
    except OSError as exc:
        print("[sink] tcp/%d bind failed: %s" % (port, exc), flush=True)
        return
    srv.listen(8)
    print("[sink] tcp/%d listening" % port, flush=True)
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=_serve_tcp_conn,
                         args=(conn, addr, port, transcript, respond),
                         daemon=True).start()


def _serve_udp(bind: str, port: int, transcript: _Transcript,
               respond: Optional[bytes]) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((bind, port))
    except OSError as exc:
        print("[sink] udp/%d bind failed: %s" % (port, exc), flush=True)
        return
    print("[sink] udp/%d listening" % port, flush=True)
    while True:
        data, addr = sock.recvfrom(65535)
        peer = "%s:%d" % addr
        _log("udp", port, peer, "recv", data)
        transcript.record("udp", port, peer, "recv", data)
        if respond:
            sock.sendto(respond, addr)
            _log("udp", port, peer, "send", respond)
            transcript.record("udp", port, peer, "send", respond)


def serve(bind: str = "0.0.0.0", tcp_ports: Optional[List[int]] = None,
          udp_ports: Optional[List[int]] = None,
          transcript_path: Optional[str] = None,
          respond: Optional[bytes] = None) -> None:
    """Start every listener and block until interrupted."""
    tcp_ports = tcp_ports or []
    udp_ports = udp_ports or []
    if not tcp_ports and not udp_ports:
        raise ValueError("give at least one --tcp or --udp port")

    handle = open(transcript_path, "a", encoding="utf-8") if transcript_path else None
    transcript = _Transcript(handle)
    if transcript_path:
        print("[sink] transcript -> %s" % transcript_path, flush=True)

    threads = []
    for port in tcp_ports:
        threads.append(threading.Thread(
            target=_serve_tcp, args=(bind, port, transcript, respond), daemon=True))
    for port in udp_ports:
        threads.append(threading.Thread(
            target=_serve_udp, args=(bind, port, transcript, respond), daemon=True))
    for thread in threads:
        thread.start()

    print("[sink] up. Ctrl-C to stop.", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n[sink] stopping", flush=True)
    finally:
        if handle is not None:
            handle.close()

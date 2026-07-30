"""A connection sinkhole that logs everything a redirected client sends.

Once DNS points the game here, it opens TCP/UDP connections to ports it expects
a real server on. This accepts them all, hexdumps every byte to the console,
and writes a machine-readable JSONL transcript that ``classify`` can read back.
Optionally it replies with a canned byte string, to probe how the client reacts
to a server that speaks -- often enough to advance the handshake one more step.

Every socket is bound **before** the sinkhole reports itself up, and a run with
no usable listener fails loudly instead of sitting there looking healthy: a
silent sinkhole and a successful one are otherwise indistinguishable until the
capture is over and empty. Ports below 1024 need root, which is the usual cause.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import List, Optional, TextIO, Tuple


class SinkError(RuntimeError):
    """No usable listener could be established."""


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
    """Thread-safe JSONL writer shared by every listener.

    ``close`` latches rather than closing under a live writer: the listeners are
    daemon threads that may be mid-``record`` when Ctrl-C lands, and closing the
    handle beneath them turns a clean exit into a traceback.
    """

    def __init__(self, handle: Optional[TextIO]) -> None:
        self._handle = handle
        self._lock = threading.Lock()
        self._closed = False

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
            if self._closed:
                return
            self._handle.write(line + "\n")
            self._handle.flush()

    def close(self) -> None:
        with self._lock:
            if self._closed or self._handle is None:
                return
            self._closed = True
            self._handle.close()


def _log(proto: str, port: int, peer: str, direction: str, payload: bytes) -> None:
    arrow = "->" if direction == "recv" else "<-"
    print("\n[sink] %s %s/%d  %s %s  %d bytes"
          % (time.strftime("%H:%M:%S"), proto, port, peer, arrow, len(payload)),
          flush=True)
    if payload:
        print(hexdump(payload), flush=True)


def _bind_tcp(bind: str, port: int) -> Optional[socket.socket]:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((bind, port))
        srv.listen(8)
    except OSError as exc:
        print("[sink] tcp/%d bind FAILED: %s" % (port, exc), flush=True)
        srv.close()
        return None
    return srv


def _bind_udp(bind: str, port: int) -> Optional[socket.socket]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((bind, port))
    except OSError as exc:
        print("[sink] udp/%d bind FAILED: %s" % (port, exc), flush=True)
        sock.close()
        return None
    return sock


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


def _accept_loop(srv: socket.socket, port: int, transcript: _Transcript,
                 respond: Optional[bytes]) -> None:
    while True:
        try:
            conn, addr = srv.accept()
        except OSError:
            return
        threading.Thread(target=_serve_tcp_conn,
                         args=(conn, addr, port, transcript, respond),
                         daemon=True).start()


def _udp_loop(sock: socket.socket, port: int, transcript: _Transcript,
              respond: Optional[bytes]) -> None:
    while True:
        try:
            data, addr = sock.recvfrom(65535)
        except OSError:
            return
        peer = "%s:%d" % addr
        _log("udp", port, peer, "recv", data)
        transcript.record("udp", port, peer, "recv", data)
        if respond:
            try:
                sock.sendto(respond, addr)
            except OSError as exc:
                print("[sink] udp/%d reply failed: %s" % (port, exc), flush=True)
                continue
            _log("udp", port, peer, "send", respond)
            transcript.record("udp", port, peer, "send", respond)


def serve(bind: str = "0.0.0.0", tcp_ports: Optional[List[int]] = None,
          udp_ports: Optional[List[int]] = None,
          transcript_path: Optional[str] = None,
          respond: Optional[bytes] = None) -> None:
    """Bind every port, then serve until interrupted.

    Raises :class:`SinkError` if nothing could be bound, so a privilege problem
    surfaces now rather than as an empty capture later.
    """
    tcp_ports = sorted(set(tcp_ports or []))
    udp_ports = sorted(set(udp_ports or []))
    if not tcp_ports and not udp_ports:
        raise ValueError("give at least one --tcp or --udp port")

    # Bind first, in this thread, so success is known before anything is claimed.
    listeners: List[Tuple[str, int, socket.socket]] = []
    for port in tcp_ports:
        sock = _bind_tcp(bind, port)
        if sock is not None:
            listeners.append(("tcp", port, sock))
    for port in udp_ports:
        sock = _bind_udp(bind, port)
        if sock is not None:
            listeners.append(("udp", port, sock))

    requested = len(tcp_ports) + len(udp_ports)
    if not listeners:
        privileged = [p for p in tcp_ports + udp_ports if p < 1024]
        hint = ""
        if privileged:
            hint = (" Ports below 1024 need root: rerun with sudo, or pick high "
                    "ports and redirect to them.")
        raise SinkError("could not bind any of the %d requested port(s).%s"
                        % (requested, hint))
    if len(listeners) < requested:
        print("[sink] WARNING: only %d of %d port(s) bound -- the rest are NOT "
              "being captured." % (len(listeners), requested), flush=True)

    handle = open(transcript_path, "a", encoding="utf-8") if transcript_path else None
    transcript = _Transcript(handle)
    if transcript_path:
        print("[sink] transcript -> %s" % transcript_path, flush=True)

    for proto, port, sock in listeners:
        target = _accept_loop if proto == "tcp" else _udp_loop
        threading.Thread(target=target, args=(sock, port, transcript, respond),
                         daemon=True).start()
        print("[sink] %s/%d listening" % (proto, port), flush=True)

    print("[sink] up on %d listener(s). Ctrl-C to stop." % len(listeners), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n[sink] stopping", flush=True)
    finally:
        transcript.close()
        for _proto, _port, sock in listeners:
            try:
                sock.close()
            except OSError:
                pass

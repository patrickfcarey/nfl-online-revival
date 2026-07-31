"""A minimal, experiment-driven EA game server.

Speaks the framing in :mod:`recon.eaproto` and answers what the client asks.
Its purpose is iteration: the framing is known, the *content* of a valid reply
is not, so replies are loaded from an editable JSON file rather than compiled
in. Change a field, restart, watch what the client does -- that loop is the
whole method for reconstructing a protocol whose server no longer exists.

Every exchange is logged in both directions and appended to a JSONL transcript
as it happens, so a session that ends badly still leaves its evidence.

Reply file format -- a JSON object keyed by the four-character message type::

    {
      "@dir": {"TYPE": "1", "ADDR": "192.168.68.85", "PORT": "10001"},
      "@tic": {"RESULT": "0"}
    }

A type with no entry gets no reply, which is itself informative: it separates
"the client needs an answer here" from "the client moves on regardless".
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Callable, Dict, Optional

from . import eaproto


class EaServerError(RuntimeError):
    """The server could not be started."""


def load_replies(path: Optional[str]) -> Dict[str, Dict[str, str]]:
    """Read the reply table, or return the built-in starting point."""
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise EaServerError("cannot read reply file %s: %s" % (path, exc))
    except ValueError as exc:
        raise EaServerError("reply file %s is not valid JSON: %s" % (path, exc))
    if not isinstance(data, dict):
        raise EaServerError("reply file must be a JSON object keyed by message type")
    table: Dict[str, Dict[str, str]] = {}
    for key, value in data.items():
        if len(key) != 4:
            raise EaServerError(
                "reply key %r is not a 4-character message type" % key)
        if not isinstance(value, dict):
            raise EaServerError("reply for %s must be an object of KEY=VALUE" % key)
        table[key] = {str(k): str(v) for k, v in value.items()}
    return table


class _Transcript:
    """Append-as-it-happens JSONL, shared across connections."""

    def __init__(self, path: Optional[str]) -> None:
        self.path = path
        self._lock = threading.Lock()

    def record(self, direction: str, peer: str, message: eaproto.EaMessage,
               raw: bytes) -> None:
        if not self.path:
            return
        row = {
            "ts": time.time(),
            "peer": peer,
            "dir": direction,               # "recv" or "send"
            "type": message.type,
            "txn": message.txn,
            "fields": message.fields,
            "hex": raw.hex(),
        }
        with self._lock:
            try:
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row) + "\n")
                    handle.flush()
            except OSError as exc:
                print("[ea] transcript write failed: %s" % exc, flush=True)


def _reply_for(message: eaproto.EaMessage, replies: Dict[str, Dict[str, str]],
               host: str, port: int) -> Optional[bytes]:
    """Build the configured reply, or the built-in @dir guess."""
    fields = replies.get(message.type)
    if fields is not None:
        # A reply echoes the transaction it answers; the client matches on it.
        return eaproto.encode(message.type, message.txn, fields)
    if message.type == "@dir":
        return eaproto.directory_reply(message, host, port)
    return None


def _serve_connection(conn: socket.socket, addr, replies, transcript,
                      host: str, redirect_port: int,
                      on_message: Optional[Callable] = None) -> None:
    peer = "%s:%d" % addr
    buffer = b""
    print("\n[ea] %s %s connected" % (time.strftime("%H:%M:%S"), peer), flush=True)
    try:
        while True:
            chunk = conn.recv(65535)
            if not chunk:
                break
            buffer += chunk
            try:
                messages, buffer = eaproto.split_stream(buffer)
            except eaproto.EaProtocolError as exc:
                # Desync is worth seeing in full: it usually means the framing
                # assumption is wrong, not that the client misbehaved.
                print("[ea] framing error from %s: %s" % (peer, exc), flush=True)
                print("     buffer head: %s" % buffer[:48].hex(), flush=True)
                break
            for message in messages:
                raw = message.type.encode("latin-1") + b"" + message.raw_payload
                print("[ea] <- %s  %s (txn %d)"
                      % (peer, message.type, message.txn), flush=True)
                print(message.describe(), flush=True)
                transcript.record("recv", peer, message, raw)
                if on_message is not None:
                    on_message(message)

                reply = _reply_for(message, replies, host, redirect_port)
                if reply is None:
                    print("[ea] -> (no reply configured for %s; the client's next "
                          "move tells us whether one was needed)" % message.type,
                          flush=True)
                    continue
                conn.sendall(reply)
                decoded = eaproto.decode(reply)
                print("[ea] -> %s  %s (txn %d)  %s"
                      % (peer, decoded.type, decoded.txn,
                         ", ".join("%s=%s" % kv for kv in decoded.fields.items())),
                      flush=True)
                transcript.record("send", peer, decoded, reply)
    except OSError as exc:
        print("[ea] %s socket error: %s" % (peer, exc), flush=True)
    finally:
        if buffer:
            print("[ea] %s left %d unconsumed byte(s): %s"
                  % (peer, len(buffer), buffer[:48].hex()), flush=True)
        print("[ea] %s disconnected" % peer, flush=True)
        try:
            conn.close()
        except OSError:
            pass


def serve(bind: str = "0.0.0.0", port: int = 10000,
          reply_file: Optional[str] = None,
          transcript_path: Optional[str] = None,
          redirect_host: Optional[str] = None,
          redirect_port: int = 10001) -> None:
    """Answer EA protocol messages until interrupted."""
    replies = load_replies(reply_file)
    transcript = _Transcript(transcript_path)
    host = redirect_host or bind
    if host in ("0.0.0.0", ""):
        host = "127.0.0.1"

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((bind, port))
        srv.listen(8)
    except OSError as exc:
        raise EaServerError("cannot bind %s:%d: %s" % (bind, port, exc))

    import signal

    def _terminate(_signum, _frame):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, _terminate)
    except (ValueError, OSError):  # pragma: no cover - not the main thread
        pass

    print("[ea] serving the EA protocol on %s:%d" % (bind, port), flush=True)
    print("[ea] replies: %s"
          % (reply_file if reply_file else "built-in @dir guess only"), flush=True)
    if transcript_path:
        print("[ea] transcript -> %s" % transcript_path, flush=True)
    print("[ea] waiting for the console. Ctrl-C when done.", flush=True)
    try:
        while True:
            conn, addr = srv.accept()
            threading.Thread(
                target=_serve_connection,
                args=(conn, addr, replies, transcript, host, redirect_port),
                daemon=True).start()
    except KeyboardInterrupt:
        print("\n[ea] stopping", flush=True)
    finally:
        srv.close()

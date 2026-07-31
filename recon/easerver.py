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


def _normalise(key: str, value) -> list:
    """Accept either a single reply or a sequence of them.

    A dict is the common case -- answer with the same type. A list allows a
    follow-up push, which this protocol needs: the server both answers and
    volunteers messages (+ses, +msg, sele config), and a client can be waiting
    on the second one.
    """
    if isinstance(value, dict):
        return [(key, {str(k): str(v) for k, v in value.items()})]
    if not isinstance(value, list):
        raise EaServerError(
            "reply for %s must be an object, or a list of {type, fields}" % key)
    out = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise EaServerError("reply %s[%d] must be an object" % (key, index))
        msg_type = item.get("type", key)
        if len(msg_type) != 4:
            raise EaServerError(
                "reply %s[%d] has type %r, which is not 4 characters"
                % (key, index, msg_type))
        fields = item.get("fields", {})
        if not isinstance(fields, dict):
            raise EaServerError("reply %s[%d] fields must be an object" % (key, index))
        out.append((msg_type, {str(k): str(v) for k, v in fields.items()}))
    return out


def load_replies(path: Optional[str]) -> Dict[str, list]:
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
    table: Dict[str, list] = {}
    for key, value in data.items():
        if len(key) != 4:
            raise EaServerError(
                "reply key %r is not a 4-character message type" % key)
        table[key] = _normalise(key, value)
    return table


class _Transcript:
    """Append-as-it-happens JSONL, shared across connections."""

    def __init__(self, path: Optional[str]) -> None:
        self.path = path
        self._lock = threading.Lock()

    def record_raw(self, peer: str, kind: str, raw: bytes,
                   note: str = "") -> None:
        """Persist bytes we could not decode. They are the most valuable kind."""
        if not self.path:
            return
        row = {"ts": time.time(), "peer": peer, "dir": kind,
               "note": note, "len": len(raw), "hex": raw.hex()}
        with self._lock:
            try:
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row) + "\n")
                    handle.flush()
            except OSError as exc:
                print("[ea] transcript write failed: %s" % exc, flush=True)

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


def _replies_for(message: eaproto.EaMessage, replies: Dict[str, list],
                 host: str, port: int) -> list:
    """Every message to send in answer, in order. Empty means stay silent."""
    configured = replies.get(message.type)
    if configured is not None:
        # Each reply echoes the transaction it answers; the client matches on
        # it, and requires zero in the status position on some arms.
        return [eaproto.encode(t, message.txn, f) for t, f in configured]
    if message.type == "@dir":
        return [eaproto.directory_reply(message, host, port)]
    return []


def _serve_connection(conn: socket.socket, addr, replies, transcript,
                      host: str, redirect_port: int,
                      on_message: Optional[Callable] = None,
                      listen_port: Optional[int] = None) -> None:
    peer = "%s:%d" % addr
    if listen_port is not None:
        peer = "%s->:%d" % (peer, listen_port)
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
                # Desync usually means our framing assumption is wrong, not that
                # the client misbehaved -- so keep the bytes. Console-only
                # reporting made a rejected message look identical to no message
                # at all, which is the most expensive ambiguity available here.
                print("[ea] FRAMING ERROR from %s: %s" % (peer, exc), flush=True)
                print("     %d unparsed byte(s): %s"
                      % (len(buffer), buffer[:64].hex()), flush=True)
                transcript.record_raw(peer, "framing-error", buffer, str(exc))
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
            transcript.record_raw(peer, "unconsumed", buffer,
                                  "still buffered when the peer disconnected")
        print("[ea] %s disconnected" % peer, flush=True)
        try:
            conn.close()
        except OSError:
            pass


def serve(bind: str = "0.0.0.0", port=10000,
          reply_file: Optional[str] = None,
          transcript_path: Optional[str] = None,
          redirect_host: Optional[str] = None,
          redirect_port: int = 10001) -> None:
    """Answer EA protocol messages until interrupted.

    ``port`` may be a list. ``@dir`` is a *redirector*: it answers with an
    address the client then reconnects to, so the port named in that answer
    must also be listening or the redirect dead-ends in a refused connection
    that looks like a rejected reply.
    """
    ports = [port] if isinstance(port, int) else list(port)
    replies = load_replies(reply_file)
    transcript = _Transcript(transcript_path)
    host = redirect_host or bind
    if host in ("0.0.0.0", ""):
        host = "127.0.0.1"

    listeners = []
    for one in ports:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((bind, one))
            srv.listen(8)
        except OSError as exc:
            for _p, done in listeners:
                done.close()
            raise EaServerError("cannot bind %s:%d: %s" % (bind, one, exc))
        listeners.append((one, srv))

    import signal

    def _terminate(_signum, _frame):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, _terminate)
    except (ValueError, OSError):  # pragma: no cover - not the main thread
        pass

    print("[ea] serving the EA protocol on %s ports %s"
          % (bind, ", ".join(str(p) for p, _ in listeners)), flush=True)
    print("[ea] replies: %s"
          % (reply_file if reply_file else "built-in @dir guess only"), flush=True)
    if transcript_path:
        print("[ea] transcript -> %s" % transcript_path, flush=True)
    print("[ea] waiting for the console. Ctrl-C when done.", flush=True)
    def accept_loop(listen_port, sock):
        while True:
            try:
                conn, addr = sock.accept()
            except OSError:
                return
            threading.Thread(
                target=_serve_connection,
                args=(conn, addr, replies, transcript, host, redirect_port,
                      None, listen_port),
                daemon=True).start()

    for one, sock in listeners:
        threading.Thread(target=accept_loop, args=(one, sock), daemon=True).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n[ea] stopping", flush=True)
    finally:
        for _one, sock in listeners:
            try:
                sock.close()
            except OSError:
                pass

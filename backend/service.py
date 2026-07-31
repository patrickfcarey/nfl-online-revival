"""The TCP service: accept connections, frame messages, dispatch, reply.

Listens on several ports at once because ``@dir`` is a redirector -- it answers
with an address the client then reconnects to, so the advertised port must also
be accepting or the redirect dead-ends in a refused connection that looks
exactly like a rejected reply.

Everything is recorded to a JSONL transcript **as it happens**, including bytes
that fail to decode. A message the framing rejects would otherwise be
indistinguishable from a message never sent, which has cost this project a
session more than once.
"""

from __future__ import annotations

import json
import signal
import socket
import threading
import time
from typing import Dict, List, Optional

from . import handlers, protocol
from .handlers import Context, Session
from .store import Store


class ServiceError(RuntimeError):
    """The service could not be started."""


class Transcript:
    """Append-as-it-happens JSONL, shared across connections."""

    def __init__(self, path: Optional[str]) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _write(self, row: dict) -> None:
        if not self.path:
            return
        with self._lock:
            try:
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row) + "\n")
                    handle.flush()
            except OSError as exc:
                print("[ea] transcript write failed: %s" % exc, flush=True)

    def message(self, direction: str, peer: str, msg: protocol.Message,
                raw: bytes) -> None:
        self._write({"ts": time.time(), "peer": peer, "dir": direction,
                     "type": msg.type, "status": msg.status_tag or "ok",
                     "fields": msg.fields, "hex": raw.hex()})

    def raw(self, peer: str, kind: str, data: bytes, note: str = "") -> None:
        """Bytes we could not decode -- the most valuable kind to keep."""
        self._write({"ts": time.time(), "peer": peer, "dir": kind,
                     "note": note, "len": len(data), "hex": data.hex()})


class Service:
    def __init__(self, store: Store, config: Dict[str, str],
                 transcript: Optional[Transcript] = None,
                 verbose: bool = True) -> None:
        self.store = store
        self.config = config
        self.transcript = transcript or Transcript(None)
        self.verbose = verbose
        self.sessions: Dict[str, Session] = {}
        self._sessions_lock = threading.Lock()
        self._listeners: List[socket.socket] = []
        self._stopping = threading.Event()

    # -- logging -------------------------------------------------------

    def _say(self, text: str) -> None:
        if self.verbose:
            print(text, flush=True)

    # -- connection ----------------------------------------------------

    def _serve(self, conn: socket.socket, addr, listen_port: int) -> None:
        peer = "%s:%d" % addr
        label = "%s->:%d" % (peer, listen_port)
        session = Session(peer, listen_port)
        with self._sessions_lock:
            self.sessions[label] = session
        buffer = b""
        self._say("\n[ea] %s %s connected" % (time.strftime("%H:%M:%S"), label))
        try:
            while not self._stopping.is_set():
                chunk = conn.recv(65535)
                if not chunk:
                    break
                buffer += chunk
                try:
                    messages, buffer = protocol.split_stream(buffer)
                except protocol.ProtocolError as exc:
                    # Our framing assumption is the likelier suspect, not the
                    # client, so keep every byte and say so loudly.
                    self._say("[ea] FRAMING ERROR from %s: %s" % (label, exc))
                    self._say("     %d unparsed byte(s): %s"
                              % (len(buffer), buffer[:64].hex()))
                    self.transcript.raw(label, "framing-error", buffer, str(exc))
                    break
                for message in messages:
                    self._handle(conn, label, session, message)
        except OSError as exc:
            self._say("[ea] %s socket error: %s" % (label, exc))
        finally:
            if buffer:
                self.transcript.raw(label, "unconsumed", buffer,
                                    "still buffered at disconnect")
            with self._sessions_lock:
                self.sessions.pop(label, None)
            self._say("[ea] %s disconnected (%s)" % (label, session.describe()))
            try:
                conn.close()
            except OSError:
                pass

    def _handle(self, conn: socket.socket, label: str, session: Session,
                message: protocol.Message) -> None:
        # The bytes as they arrived, not a re-encode: the two differ exactly
        # when our parsing is wrong, which is the case worth being able to see.
        raw = message.raw or protocol.encode(message.type, message.status,
                                             message.fields)
        self._say("[ea] <- %s  %s" % (label, message.type))
        self._say(message.describe())
        self.transcript.message("recv", label, message, raw)

        context = Context(message, session, self.store, self.config)
        try:
            outgoing = handlers.dispatch(context)
        except Exception as exc:  # a handler bug must not drop the connection
            self._say("[ea] handler for %s raised: %s" % (message.type, exc))
            self.transcript.raw(label, "handler-error", b"", "%s: %s"
                                % (message.type, exc))
            return

        if not outgoing:
            known = handlers.handler_for(message.type) is not None
            self._say("[ea] -> (no reply%s)"
                      % ("" if known else "; no handler registered for %s"
                         % message.type))
            return
        for blob in outgoing:
            try:
                conn.sendall(blob)
            except OSError as exc:
                self._say("[ea] send to %s failed: %s" % (label, exc))
                return
            decoded = protocol.decode(blob)
            detail = ", ".join("%s=%s" % kv for kv in decoded.fields.items())
            self._say("[ea] -> %s  %s %s %s"
                      % (label, decoded.type,
                         "" if decoded.ok else "[%s]" % decoded.status_tag,
                         detail))
            self.transcript.message("send", label, decoded, blob)

    # -- lifecycle -----------------------------------------------------

    def serve_forever(self, bind: str, ports: List[int]) -> None:
        for port in ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((bind, port))
                sock.listen(16)
            except OSError as exc:
                for done in self._listeners:
                    done.close()
                raise ServiceError("cannot bind %s:%d: %s" % (bind, port, exc))
            self._listeners.append(sock)

        # A shell starts background jobs with SIGINT ignored, so a parent
        # script's `kill -INT` never arrives; SIGTERM is what actually does.
        def terminate(_signum, _frame):
            raise KeyboardInterrupt

        try:
            signal.signal(signal.SIGTERM, terminate)
        except (ValueError, OSError):  # pragma: no cover - not the main thread
            pass

        counts = self.store.counts()
        self._say("[ea] store %s: %s" % (self.store.path,
                  ", ".join("%s=%d" % kv for kv in sorted(counts.items()))))
        self._say("[ea] handlers: %s" % " ".join(handlers.known_types()))
        self._say("[ea] listening on %s ports %s"
                  % (bind, ", ".join(str(p) for p in ports)))
        self._say("[ea] advertising %s:%s to clients"
                  % (self.config["advertise_host"], self.config["advertise_port"]))

        for sock, port in zip(self._listeners, ports):
            threading.Thread(target=self._accept_loop, args=(sock, port),
                             daemon=True).start()
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            self._say("\n[ea] stopping")
        finally:
            self.stop()

    def _accept_loop(self, sock: socket.socket, port: int) -> None:
        while not self._stopping.is_set():
            try:
                conn, addr = sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn, addr, port),
                             daemon=True).start()

    def stop(self) -> None:
        self._stopping.set()
        for sock in self._listeners:
            try:
                sock.close()
            except OSError:
                pass
        self._listeners = []

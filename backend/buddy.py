"""The buddy / presence service -- a deliberate stub.

It is a *separate endpoint*: the client learns its address from `BUDDY_URL` and
`BUDDY_PORT`, which the main service delivers in-band **after** login. That
ordering is what makes stubbing safe -- the client is already past DNAS and into
the main service before it hears about this one, so nothing here can block
reaching a lobby or starting a match.

It uses the *same framing* as the main service, despite XMPP-shaped verbs: the
buddy send wrapper calls the same send function, so the 12-byte
type/status/length header is unchanged.

What this stub does, and why that is enough:

* ``AUTH`` -- answered with status 0, so the client considers itself signed in.
* ``PING`` -- echoed, matching the main service's keepalive contract.
* ``ROST`` / ``RGET`` -- answered with an empty roster, so the buddy list shows
  as empty rather than failing.
* everything else -- answered with status 0 and no fields, which is the least
  surprising thing to tell a client whose request we have not implemented.

Presence is accepted and discarded. The client has a table at 0x005742b8 --
0 DISC, 1 CHAT, 2 AWAY, 3 XA, 4 DND, 5 PASS -- but on the wire a console sends
the **name**, not the index: an observed ``PSET`` carried ``SHOW=CHAT``. The
mapping below is the client's table, not the wire encoding.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Dict, List, Optional

from . import protocol
from .hub import Connection, Hub

#: SHOW values, as the client maps them.
SHOW_STATES = {0: "DISC", 1: "CHAT", 2: "AWAY", 3: "XA", 4: "DND", 5: "PASS"}

#: Verbs answered with something more specific than a bare acknowledgement.
ROSTER_REQUESTS = ("ROST", "RGET")


class BuddyService:
    """Accepts connections and keeps the client's buddy layer quiet."""

    def __init__(self, verbose: bool = True,
                 transcript=None) -> None:
        self.verbose = verbose
        self.transcript = transcript
        self.hub = Hub(on_event=self._say)
        self._listener: Optional[socket.socket] = None
        self._stopping = threading.Event()

    def _say(self, text: str) -> None:
        if self.verbose:
            print(text, flush=True)

    # -- protocol ------------------------------------------------------

    def respond(self, message: protocol.Message) -> List[bytes]:
        """What to send back for one request. Pure, so it is testable."""
        verb = message.type
        if verb == "PING":
            # Echo, exactly as the main service's keepalive expects.
            return [protocol.encode("PING")]
        if verb == "AUTH":
            # Status 0 is the whole contract; the client then reads an
            # optional refreshed key and proceeds.
            return [protocol.encode("AUTH", protocol.OK, {
                "USER": message.get("USER"),
                "DOMN": message.get("DOMN"),
                "RSRC": message.get("RSRC"),
            })]
        if verb in ROSTER_REQUESTS:
            return [protocol.encode(verb, protocol.OK, {"LIST": ""})]
        if verb == "DISC":
            return []
        return [protocol.encode(verb, protocol.OK, {})]

    # -- transport -----------------------------------------------------

    def _serve(self, conn: socket.socket, addr) -> None:
        label = "buddy %s:%d" % addr
        connection = Connection(conn, label, _BuddySession(label))
        self.hub.register(connection)
        buffer = b""
        self._say("\n[buddy] %s connected" % label)
        try:
            while not self._stopping.is_set():
                chunk = conn.recv(65535)
                if not chunk:
                    break
                buffer += chunk
                try:
                    messages, buffer = protocol.split_stream(buffer)
                except protocol.ProtocolError as exc:
                    self._say("[buddy] framing error from %s: %s" % (label, exc))
                    if self.transcript:
                        self.transcript.raw(label, "framing-error", buffer,
                                            str(exc))
                    break
                for message in messages:
                    self._say("[buddy] <- %s  %s %s" % (
                        label, message.type,
                        ", ".join("%s=%s" % kv
                                  for kv in list(message.fields.items())[:4])))
                    if self.transcript:
                        self.transcript.message("recv", label, message,
                                                message.raw)
                    for blob in self.respond(message):
                        if not connection.send(blob):
                            return
                        if self.transcript:
                            self.transcript.message(
                                "send", label, protocol.decode(blob), blob)
        except OSError:
            pass
        finally:
            self.hub.unregister(connection)
            self._say("[buddy] %s disconnected" % label)
            connection.close()

    def serve_forever(self, bind: str, port: int) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((bind, port))
        sock.listen(8)
        self._listener = sock
        self._say("[buddy] stub listening on %s:%d" % (bind, port))
        threading.Thread(target=self.hub.run_keepalive, daemon=True).start()
        while not self._stopping.is_set():
            try:
                conn, addr = sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn, addr),
                             daemon=True).start()

    def stop(self) -> None:
        self._stopping.set()
        self.hub.stop()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None


class _BuddySession:
    """Minimal session object, so Connection and Hub can treat it uniformly."""

    def __init__(self, peer: str) -> None:
        self.peer = peer
        self.persona: Optional[str] = None
        self.room: Optional[str] = None
        self.opened = time.time()

    def describe(self) -> str:
        return "buddy %s" % self.peer

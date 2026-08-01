"""Live connections, and the rules for writing to them.

A handler answering one client often has to reach others -- someone joining a
room changes what everyone else in it should see. That needs a registry of live
connections and a safe way to write to a socket the owning thread is not
currently using, which is what this provides.

Three rules are enforced here rather than left to callers, because each was
recovered from the client and getting any of them wrong is expensive:

**Every write is serialised per connection.** Two threads calling ``sendall``
on one socket interleave, and the client does no reassembly -- a torn message
desynchronises the stream permanently.

**The server must speak within 60 seconds.** The client resets its receive
deadline to ``now + 60s`` on every message that arrives and tears the session
down when it lapses. It never originates a keepalive itself: it intercepts any
``~png`` and echoes it, so the ping has to come from here.

**Only push-only types may be sent unsolicited.** The client matches replies to
its outstanding requests by *type*, against the head of its pending queue only.
An unsolicited message whose type happens to sit at that head is mistaken for
the reply and fires the wrong callback, so pushes are restricted to types the
client never sends.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Callable, Dict, Iterable, List, Optional

from . import matchmaking, protocol

#: Types the client never sends, so they can never collide with something in
#: its pending queue. Anything outside this set must only ever be a reply.
PUSH_ONLY = frozenset(("+ses", "+msg", "+who", "+rom", "+pop", "+usr",
                       "+rnk", "+snp"))

#: ``~png`` is safe to send unsolicited for a different reason: the client
#: intercepts it at 0x00448C58 *before* the reply matching runs, echoes it, and
#: returns. It never reaches the pending queue, so it cannot be mistaken for a
#: reply the way an ordinary type would be.
SAFE_UNSOLICITED = PUSH_ONLY | frozenset(("~png",))

#: The client tears the session down 60 s after the last message it received.
CLIENT_DEADLINE = 60.0
#: Send a keepalive well inside that, so one lost message is not fatal.
PING_AFTER = 25.0
#: How often the keepalive thread looks for idle connections.
PING_TICK = 5.0

#: How long one write may take before the peer is judged wedged.
#:
#: Without this, ``sendall`` blocks forever on a peer that has stopped reading:
#: its receive window fills, our kernel buffer fills, and the call never
#: returns. Because writes are serialised per connection and ``broadcast``
#: walks its targets one at a time, that single dead console froze chat and
#: presence for everyone else in the room -- and nothing logged, because from
#: the server's point of view a send was simply still in progress.
#:
#: Ten seconds is far above any real network stall for messages that average 27
#: bytes, and far below "never".
SEND_TIMEOUT = 10.0


class PushError(RuntimeError):
    """An unsolicited send used a type that could be mistaken for a reply."""


class Connection:
    """One client socket, with its session and a lock over writes."""

    def __init__(self, sock: socket.socket, label: str, session,
                 listen_port: int = 0,
                 send_timeout: float = SEND_TIMEOUT) -> None:
        self.sock = sock
        self.label = label
        self.session = session
        self.listen_port = listen_port
        self.send_timeout = send_timeout
        self._lock = threading.Lock()
        self.opened = time.time()
        self.last_sent = self.opened
        self.closed = False
        #: Set when a write timed out rather than failed, so the disconnect
        #: log can tell a wedged peer from one that simply hung up.
        self.stalled = False

    def send(self, blob: bytes) -> bool:
        """Write one whole message. False if the peer has gone or has stalled.

        The lock covers the entire message: the client cannot reassemble a torn
        one, so a half-written message is not a delay, it is a dead session.

        The timeout comes from the socket, set once when the connection is
        accepted, and ``sendall`` applies it to the whole operation. On expiry
        there is no safe recovery -- an unknown number of bytes has already
        gone, so the stream is torn whatever we do next -- which is why this
        abandons the connection rather than retrying.
        """
        with self._lock:
            if self.closed:
                return False
            try:
                self.sock.sendall(blob)
            except socket.timeout:
                # Distinct from OSError below: the peer is still there, it has
                # simply stopped reading. Left alone it would hold this lock,
                # and therefore the room's whole fanout, indefinitely.
                self.stalled = True
                self._abort()
                return False
            except OSError:
                self.closed = True
                return False
            self.last_sent = time.time()
            return True

    def _abort(self) -> None:
        """Wake the reader and mark this connection dead, without closing it.

        ``shutdown`` rather than ``close`` on purpose. The thread that owns
        this connection is blocked in ``recv``; shutting the socket down makes
        that return, and the owning thread then closes the descriptor in its
        own ``finally``. Closing it from here would free the descriptor while
        another thread is about to read from it, and the number can be reused
        by the next ``accept`` -- so the reader would resume against an
        unrelated client's socket.
        """
        self.closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def close(self) -> None:
        with self._lock:
            self.closed = True
            try:
                self.sock.close()
            except OSError:
                pass

    @property
    def idle_for(self) -> float:
        return time.time() - self.last_sent


class Hub:
    """The set of live connections, and how to reach them."""

    def __init__(self, on_event: Optional[Callable[[str], None]] = None,
                 pair_any: bool = False, transcript=None) -> None:
        self._connections: Dict[str, Connection] = {}
        self._lock = threading.RLock()
        self._stopping = threading.Event()
        self._on_event = on_event or (lambda text: None)
        #: Pushes must be transcribed too. Without this they were invisible:
        #: only the request/reply path recorded anything, so every +ses, +usr,
        #: +rom and +msg the server ever sent was missing from the captures.
        #: That made "it is not in the transcript" unsound reasoning for exactly
        #: the messages hardest to observe from the client side.
        self._transcript = transcript
        #: The quickmatch/challenge waiting room. Lives here because it spans
        #: connections, and because unregister() must evict a departing client
        #: from it -- a dead socket left in the queue gets paired with the next
        #: arrival, which the survivor experiences as a match that never starts.
        self.matchmaker = matchmaking.Matchmaker(pair_any=pair_any)

    # -- membership ----------------------------------------------------

    def register(self, conn: Connection) -> None:
        with self._lock:
            self._connections[conn.label] = conn

    def unregister(self, conn: Connection) -> None:
        with self._lock:
            self._connections.pop(conn.label, None)
        # Evict from the waiting room too. A closed socket left queued is
        # matched against the next arrival, and that player then waits for a
        # console that is no longer there.
        self.matchmaker.forget(conn)

    def all(self) -> List[Connection]:
        with self._lock:
            return list(self._connections.values())

    def count(self) -> int:
        with self._lock:
            return len(self._connections)

    def in_room(self, room: Optional[str]) -> List[Connection]:
        """Everyone currently in *room*. A None room matches nobody."""
        if room is None:
            return []
        return [c for c in self.all()
                if getattr(c.session, "room", None) == room and not c.closed]

    def by_persona(self, persona: str) -> Optional[Connection]:
        for conn in self.all():
            if getattr(conn.session, "persona", None) == persona:
                return conn
        return None

    # -- sending -------------------------------------------------------

    def push(self, conn: Connection, blob: bytes) -> bool:
        """Send an unsolicited message, refusing types that could be misread."""
        msg_type = blob[:4].decode("latin-1", "replace")
        if msg_type not in SAFE_UNSOLICITED:
            raise PushError(
                "%r is not a push-only type. The client matches replies to its "
                "pending requests by type against the head of its queue, so an "
                "unsolicited %r can be taken for the reply to an outstanding "
                "request and fire the wrong callback." % (msg_type, msg_type))
        sent = conn.send(blob)
        if sent and self._transcript is not None:
            try:
                self._transcript.message("push", conn.label,
                                         protocol.decode(blob), blob)
            except Exception:
                # Never let bookkeeping break delivery.
                pass
        return sent

    def broadcast(self, blobs: Iterable[bytes], room: Optional[str] = None,
                  exclude: Optional[Connection] = None) -> int:
        """Push to a room, or to everyone when *room* is None. Returns the
        number of connections written to."""
        targets = self.in_room(room) if room is not None else self.all()
        blobs = list(blobs)
        sent = 0
        for conn in targets:
            if conn is exclude or conn.closed:
                continue
            if all(self.push(conn, blob) for blob in blobs):
                sent += 1
        return sent

    # -- keepalive -----------------------------------------------------

    def keepalive_once(self, now: Optional[float] = None) -> int:
        """Ping every connection that has heard nothing from us recently.

        Returns how many were pinged. Split out from the loop so a test can
        drive it without waiting on wall-clock time.
        """
        now = time.time() if now is None else now
        ping = protocol.encode("~png")
        pinged = 0
        for conn in self.all():
            if conn.closed:
                continue
            if now - conn.last_sent >= PING_AFTER:
                if self.push(conn, ping):
                    pinged += 1
        return pinged

    def run_keepalive(self) -> None:
        """Loop until stopped. Intended to run on its own daemon thread."""
        while not self._stopping.wait(PING_TICK):
            try:
                count = self.keepalive_once()
                if count:
                    self._on_event("[ea] keepalive: pinged %d connection(s)"
                                   % count)
            except Exception as exc:  # never let the keepalive thread die
                self._on_event("[ea] keepalive error: %s" % exc)

    def stop(self) -> None:
        self._stopping.set()
        for conn in self.all():
            conn.close()
        with self._lock:
            self._connections.clear()

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

from . import limits, protocol
from . import metrics as metrics_module
from .hub import SEND_TIMEOUT, Connection, Hub

#: This endpoint is as reachable as the main one and had the same unbounded
#: accept loop, so it gets the same limits. They are separate numbers because
#: a console holds exactly one buddy connection -- there is no redirect here --
#: so a much tighter per-address cap is still generous.
DEFAULT_MAX_CONNECTIONS = 256
DEFAULT_MAX_PER_IP = 4

#: A client that connects and says nothing is not waiting for anything: the
#: buddy layer opens this socket in order to send AUTH.
FIRST_BYTE_DEADLINE = 30.0
IDLE_TIMEOUT = 120.0

#: SHOW values, as the client maps them.
SHOW_STATES = {0: "DISC", 1: "CHAT", 2: "AWAY", 3: "XA", 4: "DND", 5: "PASS"}

#: Verbs answered with something more specific than a bare acknowledgement.
ROSTER_REQUESTS = ("ROST", "RGET")


class BuddyService:
    """Accepts connections and keeps the client's buddy layer quiet."""

    def __init__(self, verbose: bool = True,
                 transcript=None,
                 limiter: Optional[limits.ConnectionLimiter] = None,
                 send_timeout: float = SEND_TIMEOUT,
                 idle_timeout: float = IDLE_TIMEOUT,
                 first_byte_deadline: float = FIRST_BYTE_DEADLINE,
                 rates: Optional[limits.RateLimiter] = None,
                 bans: Optional[limits.BanList] = None,
                 metrics: Optional[metrics_module.Metrics] = None) -> None:
        if send_timeout <= 0:
            raise ValueError(
                "send_timeout must be positive; 0 puts the socket in "
                "non-blocking mode and drops every connection.")
        self.verbose = verbose
        self.transcript = transcript
        self.hub = Hub(on_event=self._say)
        self.limiter = limiter or limits.ConnectionLimiter(
            total=DEFAULT_MAX_CONNECTIONS, per_ip=DEFAULT_MAX_PER_IP)
        #: Shared with the game service by default, and that sharing is the
        #: point. A ban earned on :10000 has to keep the same address off
        #: :10002 as well, or the endpoint with fewer controls becomes the one
        #: worth attacking. The *connection* limiter stays separate, because a
        #: console holds one buddy socket and two game ones and they should not
        #: compete for a single per-address budget.
        self.rates = rates or limits.RateLimiter()
        self.bans = bans or limits.BanList()
        self.metrics = metrics or metrics_module.Metrics()
        self._declare_metrics()
        self.send_timeout = send_timeout
        self.idle_timeout = idle_timeout
        self.first_byte_deadline = first_byte_deadline
        self._listener: Optional[socket.socket] = None
        self._stopping = threading.Event()

    def _say(self, text: str) -> None:
        if self.verbose:
            print(text, flush=True)

    def _declare_metrics(self) -> None:
        """Buddy's own counters, declared at zero.

        Namespaced separately from the game service's so a shared Metrics can
        hold both without either endpoint's traffic hiding in the other's
        totals. `rate_*` and `bans_*` are deliberately *not* re-declared -- the
        limiter and ban list are shared, so those counters are shared too.
        """
        for name, help_text in (
                ("buddy_connections_total", "Buddy connections accepted."),
                ("buddy_connections_refused_total",
                 "Buddy connections closed immediately by a limit or a ban."),
                ("buddy_messages_total", "Buddy messages decoded."),
                ("buddy_framing_errors_total",
                 "Buddy streams abandoned as unparseable."),
                ("buddy_timeouts_first_byte_total",
                 "Buddy connections closed having never sent anything."),
                ("buddy_timeouts_idle_total",
                 "Buddy connections closed for going quiet."),
                ("buddy_accept_failures_total",
                 "Accepted buddy connections that could not be given a worker.")):
            self.metrics.declare(name, help_text)

    def _strike(self, address: str, reason: str) -> None:
        until = self.bans.record(address, reason)
        if until is not None:
            self.metrics.bump("bans_total")
            self._say("[buddy] BAN %s for %.0fs (%s)"
                      % (address, self.bans.ttl, reason))

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
        connection = Connection(conn, label, _BuddySession(label),
                                send_timeout=self.send_timeout)
        self.hub.register(connection)
        buffer = b""
        self._say("\n[buddy] %s connected" % label)
        last_heard = time.time()
        heard_anything = False
        rate_state = self.rates.new_bucket()
        self.rates.attach(addr[0])
        over_limit = False
        try:
            while not self._stopping.is_set() and not over_limit:
                try:
                    chunk = conn.recv(65535)
                except socket.timeout:
                    # Before the outer `except OSError`, which it subclasses.
                    quiet = time.time() - last_heard
                    deadline = (self.idle_timeout if heard_anything
                                else self.first_byte_deadline)
                    if deadline and quiet >= deadline:
                        self._say("[buddy] %s closed: silent for %.0fs"
                                  % (label, quiet))
                        if heard_anything:
                            self.metrics.bump("buddy_timeouts_idle_total")
                        else:
                            self.metrics.bump("buddy_timeouts_first_byte_total")
                            self._strike(addr[0], "connected and sent nothing")
                        break
                    continue
                if not chunk:
                    break
                last_heard = time.time()
                heard_anything = True
                buffer += chunk
                try:
                    messages, buffer = protocol.split_stream(buffer)
                except protocol.ProtocolError as exc:
                    self._say("[buddy] framing error from %s: %s" % (label, exc))
                    if self.transcript:
                        self.transcript.raw(label, "framing-error", buffer,
                                            str(exc))
                    self.metrics.bump("buddy_framing_errors_total")
                    self._strike(addr[0], "malformed framing")
                    break
                for message in messages:
                    self.metrics.bump("buddy_messages_total")
                    reason = self.rates.check(rate_state, addr[0])
                    if reason is not None:
                        self.metrics.bump("rate_violations_total")
                        if self.rates.enforce:
                            self._say("[buddy] %s dropped: %s" % (label, reason))
                            self.metrics.bump("rate_enforced_total")
                            self._strike(addr[0], reason)
                            over_limit = True
                            break
                        self._say("[buddy] %s over the rate limit (%s) -- "
                                  "observing only" % (label, reason))
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
            self._say("[buddy] %s disconnected%s"
                      % (label, " -- stopped reading" if connection.stalled
                         else ""))
            connection.close()
            self.rates.detach(addr[0])
            self.limiter.release(addr[0])

    def serve_forever(self, bind: str, port: int) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((bind, port))
        sock.listen(8)
        self._listener = sock
        self._say("[buddy] stub listening on %s:%d (%s connections, %s per "
                  "address)" % (bind, port,
                                self.limiter.total or "unlimited",
                                self.limiter.per_ip or "unlimited"))
        threading.Thread(target=self.hub.run_keepalive, daemon=True).start()
        while not self._stopping.is_set():
            try:
                conn, addr = sock.accept()
            except OSError:
                return
            # Bans first, so a banned address does not also spend a slot to be
            # told so. The list is shared with the game service: an address
            # banned there must not simply move here.
            remaining = self.bans.banned_for(addr[0])
            if remaining:
                refusal = "banned, %.0fs remaining" % remaining
                self.metrics.bump("bans_refused_total")
            else:
                refusal = self.limiter.acquire(addr[0])
            if refusal is not None:
                self._say("[buddy] refused %s:%d -- %s"
                          % (addr[0], addr[1], refusal))
                self.metrics.bump("buddy_connections_refused_total")
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            self.metrics.bump("buddy_connections_total")
            try:
                conn.settimeout(self.send_timeout)
                threading.Thread(target=self._serve, args=(conn, addr),
                                 daemon=True).start()
            except (OSError, RuntimeError) as exc:
                # See the game service's accept loop: an unguarded start()
                # leaks the slot and the descriptor exactly when threads are
                # already exhausted.
                self._say("[buddy] could not serve %s:%d -- %s"
                          % (addr[0], addr[1], exc))
                self.metrics.bump("buddy_accept_failures_total")
                self.limiter.release(addr[0])
                try:
                    conn.close()
                except OSError:
                    pass
                continue

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

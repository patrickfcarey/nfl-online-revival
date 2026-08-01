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

from . import handlers, limits, protocol
from . import metrics as metrics_module
from .handlers import Context, Session
from .hub import SEND_TIMEOUT, Connection, Hub, PushError
from .store import Store

#: How long a connection may stay silent having never said anything at all.
#:
#: A console connects in order to speak -- it sends `@dir` or `auth` at once --
#: so silence here is not a slow player, it is a socket held open for the sake
#: of holding it. Before this existed, `recv` had no timeout and one SYN
#: consumed a thread permanently, which is the cheapest denial of service there
#: is.
FIRST_BYTE_DEADLINE = 30.0

#: How long an established connection may go quiet.
#:
#: Grounded in the protocol rather than guessed: we ping every PING_AFTER (25 s)
#: and the client echoes every `~png` it receives, so a healthy connection sends
#: us something roughly every 25 seconds. This allows nearly five consecutive
#: missed echoes before giving up, which keeps it firmly on the fail-open side
#: for a real player while still bounding what an idle socket can hold.
IDLE_TIMEOUT = 120.0

#: How long a connection may talk without ever logging in.
#:
#: Distinct from FIRST_BYTE_DEADLINE, and it closes a different hole: a socket
#: that sends one byte every minute passes the first-byte check and the idle
#: check forever while holding a thread, a slot, and a user id. Login is a
#: short fixed exchange, so a minute is many times what it needs.
#:
#: Connections to the redirector are exempt in practice rather than by rule --
#: a client that asks `@dir` and leaves is gone long before this expires.
PRE_AUTH_DEADLINE = 60.0


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
                 verbose: bool = True,
                 limiter: Optional[limits.ConnectionLimiter] = None,
                 send_timeout: float = SEND_TIMEOUT,
                 idle_timeout: float = IDLE_TIMEOUT,
                 first_byte_deadline: float = FIRST_BYTE_DEADLINE,
                 pre_auth_deadline: float = PRE_AUTH_DEADLINE,
                 rates: Optional[limits.RateLimiter] = None,
                 bans: Optional[limits.BanList] = None,
                 metrics: Optional["metrics_module.Metrics"] = None) -> None:
        self.store = store
        self.config = config
        self.transcript = transcript or Transcript(None)
        self.verbose = verbose
        self.hub = Hub(on_event=self._say,
                       pair_any=bool(config.get("pair_any")),
                       transcript=transcript)
        #: Shared across every listening port on purpose: one console holds
        #: :10000 and the advertised port at the same time during the redirect,
        #: so a per-port count would be counting the wrong thing.
        if send_timeout <= 0:
            # settimeout(0) means non-blocking, not "no timeout": recv would
            # raise BlockingIOError -- an OSError but not socket.timeout -- and
            # every connection would be dropped on its first quiet moment. An
            # unlimited send timeout is also the wedge this exists to prevent,
            # so there is no sensible value here at or below zero.
            raise ValueError(
                "send_timeout must be positive; 0 puts the socket in "
                "non-blocking mode and drops every connection. Unlike the "
                "connection caps, this limit cannot be disabled.")
        self.limiter = limiter or limits.ConnectionLimiter()
        self.rates = rates or limits.RateLimiter()
        self.bans = bans or limits.BanList()
        self.metrics = metrics or metrics_module.Metrics()
        self._declare_metrics()
        self.send_timeout = send_timeout
        self.idle_timeout = idle_timeout
        self.first_byte_deadline = first_byte_deadline
        self.pre_auth_deadline = pre_auth_deadline
        self._next_user_id = 0
        self._id_lock = threading.Lock()
        self._listeners: List[socket.socket] = []
        self._stopping = threading.Event()

    # -- logging -------------------------------------------------------

    def _say(self, text: str) -> None:
        if self.verbose:
            print(text, flush=True)

    def _declare_metrics(self) -> None:
        """Every counter exists from the start, at zero.

        A counter that only appears once it is non-zero cannot be told apart
        from one that was never wired up, so an absent line would read as
        "nothing was refused" when it might mean the check does not run.
        """
        declare = self.metrics.declare
        declare("connections_total", "Connections accepted.")
        declare("connections_refused_total",
                "Connections closed immediately by a limit or a ban.")
        declare("framing_errors_total", "Streams abandoned as unparseable.")
        declare("timeouts_first_byte_total",
                "Connections closed having never sent anything.")
        declare("timeouts_idle_total",
                "Established connections closed for going quiet.")
        declare("timeouts_pre_auth_total",
                "Connections closed for never logging in.")
        declare("sends_stalled_total",
                "Connections abandoned because a write timed out.")
        declare("messages_total", "Messages decoded from clients.")
        declare("rate_violations_total",
                "Messages over a rate limit, whether or not it was enforced.")
        declare("rate_enforced_total",
                "Connections actually closed for exceeding a rate limit.")
        declare("bans_total", "Addresses that crossed the strike threshold.")
        declare("bans_refused_total",
                "Connections turned away because their address was banned.")
        declare("accept_failures_total",
                "Accepted connections that could not be given a worker.")

        self.metrics.gauge("connections_active", "Connections open now.",
                           lambda: self.limiter.active)
        self.metrics.gauge("bans_active", "Addresses currently refused.",
                           lambda: len(self.bans.active()))
        # The number the log-only mode exists to produce: set the limit from
        # this, not from the guess in limits.py. It is a maximum over
        # connections, not a total across them, because `rate` is spent by one
        # connection -- measuring the sum would inflate it by however many
        # clients happened to be online.
        self.metrics.gauge("rate_peak_messages_per_second",
                           "Messages per second on the busiest single "
                           "connection.",
                           lambda: self.rates.peak)
        self.metrics.gauge("rate_limit_enforced",
                           "1 when rate limits close connections, 0 when only "
                           "observed.",
                           lambda: 1 if self.rates.enforce else 0)

    def _strike(self, address: str, label: str, reason: str) -> None:
        """Record a hard signal against an address, and say so if it bans."""
        until = self.bans.record(address, reason)
        if until is not None:
            self.metrics.bump("bans_total")
            self._say("[ea] BAN %s for %.0fs after %d strikes (%s)"
                      % (address, self.bans.ttl, self.bans.threshold, reason))
            self.transcript.raw(label, "ban", b"",
                                "%s: %s" % (address, reason))

    # -- connection ----------------------------------------------------

    def _serve(self, conn: socket.socket, addr, listen_port: int) -> None:
        peer = "%s:%d" % addr
        label = "%s->:%d" % (peer, listen_port)
        session = Session(peer, listen_port)
        with self._id_lock:
            self._next_user_id += 1
            # Positive only: the client silently discards a record with a
            # negative id, so an occupant with id 0 would simply not appear.
            session.user_id = self._next_user_id
        connection = Connection(conn, label, session, listen_port,
                                send_timeout=self.send_timeout)
        self.hub.register(connection)
        buffer = b""
        self._say("\n[ea] %s %s connected" % (time.strftime("%H:%M:%S"), label))
        # The socket timeout does double duty: it bounds `sendall` for every
        # writer (see hub.Connection.send) and it paces this read loop so the
        # deadlines below can be checked at all.
        last_heard = time.time()
        heard_anything = False
        bucket = self.rates.new_bucket()
        self.rates.attach(addr[0])
        try:
            while not self._stopping.is_set():
                if self._past_pre_auth(connection, addr[0]):
                    break
                try:
                    chunk = conn.recv(65535)
                except socket.timeout:
                    # Nested, and before the outer `except OSError`, because
                    # socket.timeout is a subclass of it -- caught out there,
                    # every poll would look like a socket error and drop a
                    # perfectly healthy connection.
                    quiet = time.time() - last_heard
                    deadline = (self.idle_timeout if heard_anything
                                else self.first_byte_deadline)
                    if deadline and quiet >= deadline:
                        self._say("[ea] %s closed: silent for %.0fs (%s)"
                                  % (label, quiet, "idle" if heard_anything
                                     else "never sent anything"))
                        self.transcript.raw(label, "timeout", b"",
                                            "silent for %.1fs" % quiet)
                        if heard_anything:
                            self.metrics.bump("timeouts_idle_total")
                        else:
                            self.metrics.bump("timeouts_first_byte_total")
                            # A socket opened and never used is not a slow
                            # player, it is someone holding a thread.
                            self._strike(addr[0], label,
                                         "connected and sent nothing")
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
                    # Our framing assumption is the likelier suspect, not the
                    # client, so keep every byte and say so loudly.
                    self._say("[ea] FRAMING ERROR from %s: %s" % (label, exc))
                    self._say("     %d unparsed byte(s): %s"
                              % (len(buffer), buffer[:64].hex()))
                    self.transcript.raw(label, "framing-error", buffer, str(exc))
                    self.metrics.bump("framing_errors_total")
                    self._strike(addr[0], label, "malformed framing")
                    break
                over_limit = False
                for message in messages:
                    self.metrics.bump("messages_total")
                    reason = self.rates.check(bucket, addr[0])
                    if reason is not None:
                        self.metrics.bump("rate_violations_total")
                        if self.rates.enforce:
                            self._say("[ea] %s dropped: %s" % (label, reason))
                            self.transcript.raw(label, "rate-limited", b"",
                                                reason)
                            self.metrics.bump("rate_enforced_total")
                            # Only a strike when enforcing. In log-only mode
                            # the threshold is still a guess, and banning on a
                            # guess takes a real player off the service for ten
                            # minutes with no way to tell them why.
                            self._strike(addr[0], label, reason)
                            over_limit = True
                            break
                        # Observing: say it once and carry on serving.
                        self._say("[ea] %s over the rate limit (%s) -- "
                                  "observing only, not enforced" % (label, reason))
                    self._handle(connection, message)
                if over_limit:
                    break
        except OSError as exc:
            self._say("[ea] %s socket error: %s" % (label, exc))
        finally:
            if buffer:
                self.transcript.raw(label, "unconsumed", buffer,
                                    "still buffered at disconnect")
            self.hub.unregister(connection)
            self._say("[ea] %s disconnected (%s)%s"
                      % (label, session.describe(),
                         " -- stopped reading; writes timed out"
                         if connection.stalled else ""))
            if connection.stalled:
                self.metrics.bump("sends_stalled_total")
            connection.close()
            # Last, and outside every other failure path: a slot that is not
            # given back is a slot lost for the lifetime of the process, and
            # the symptom is a server that refuses everyone after a while for
            # no visible reason.
            self.rates.detach(addr[0])
            self.limiter.release(addr[0])

    def _past_pre_auth(self, connection: Connection, address: str) -> bool:
        """True when this connection has been talking too long without logging in.

        Separate from the idle check because it catches the opposite shape: a
        socket that sends one byte a minute stays inside every silence deadline
        indefinitely while holding a thread, a slot and a user id.
        """
        if not self.pre_auth_deadline:
            return False
        session = connection.session
        if getattr(session, "authenticated", True):
            return False
        age = time.time() - connection.opened
        if age < self.pre_auth_deadline:
            return False
        self._say("[ea] %s closed: %.0fs without logging in"
                  % (connection.label, age))
        self.transcript.raw(connection.label, "pre-auth-timeout", b"",
                            "%.1fs without auth" % age)
        self.metrics.bump("timeouts_pre_auth_total")
        # Deliberately no strike. Connections to the redirector port send
        # `@dir` and legitimately never authenticate, and nothing establishes
        # that the console closes that socket before this deadline -- the
        # protocol notes say only that the session moves to a second
        # connection. If it does hold the first one open, striking here would
        # cost a strike per login and ban a real player after five, with no
        # feedback they could act on. Closing the connection is the whole
        # remedy; the connection cap bounds the rest.
        return True

    def _handle(self, connection: Connection,
                message: protocol.Message) -> None:
        label, session = connection.label, connection.session
        # The bytes as they arrived, not a re-encode: the two differ exactly
        # when our parsing is wrong, which is the case worth being able to see.
        raw = message.raw or protocol.encode(message.type, message.status,
                                             message.fields)
        self._say("[ea] <- %s  %s" % (label, message.type))
        self._say(message.describe())
        self.transcript.message("recv", label, message, raw)

        context = Context(message, session, self.store, self.config,
                          hub=self.hub, connection=connection)
        try:
            outgoing = handlers.dispatch(context)
        except Exception as exc:  # a handler bug must not drop the connection
            # Silence would leave the client waiting out its two-minute
            # timeout for a reply that is never coming. A failure status at
            # least moves it along and shows an error rather than a hang.
            self._say("[ea] handler for %s raised: %s" % (message.type, exc))
            self.transcript.raw(label, "handler-error", b"", "%s: %s"
                                % (message.type, exc))
            try:
                connection.send(protocol.encode(message.type,
                                                handlers.ERR_INTERNAL, {}))
            except protocol.ProtocolError:
                pass
            return

        if not outgoing:
            known = handlers.handler_for(message.type) is not None
            self._say("[ea] -> (no reply%s)"
                      % ("" if known else "; no handler registered for %s"
                         % message.type))
            return
        for blob in outgoing:
            if not connection.send(blob):
                self._say("[ea] send to %s failed; peer gone" % label)
                return
            decoded = protocol.decode(blob)
            detail = ", ".join("%s=%s" % kv for kv in decoded.fields.items())
            self._say("[ea] -> %s  %s %s %s"
                      % (label, decoded.type,
                         "" if decoded.ok else "[%s]" % decoded.status_tag,
                         detail))
            self.transcript.message("send", label, decoded, blob)

        # Some handlers have work that must run *after* their reply lands. A
        # room change is the case that matters: the reply carries the new room
        # id, and the user records that follow are only meaningful once the
        # client has it. Sending them first would have them discarded.
        follow_up = handlers.AFTER_REPLY.get(message.type)
        if follow_up is not None:
            try:
                follow_up(context)
            except Exception as exc:
                self._say("[ea] follow-up for %s raised: %s"
                          % (message.type, exc))
                self.transcript.raw(label, "follow-up-error", b"",
                                    "%s: %s" % (message.type, exc))

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
        self._say("[ea] limits: %s connections, %s per address; "
                  "timeouts send %.0fs, idle %.0fs, first byte %.0fs, "
                  "pre-auth %.0fs"
                  % (self.limiter.total or "unlimited",
                     self.limiter.per_ip or "unlimited",
                     self.send_timeout, self.idle_timeout,
                     self.first_byte_deadline, self.pre_auth_deadline))
        self._say("[ea] rate: %s" % self.rates.describe())
        if not self.rates.enforce:
            self._say("[ea]   thresholds are NOT measured -- run a real "
                      "session, read nfl_rate_peak_messages_per_second, then "
                      "set --rate and --rate-burst from it and pass "
                      "--rate-limit enforce.")
        self._say("[ea] bans: %s"
                  % ("%d strikes in %.0fs -> %.0fs"
                     % (self.bans.threshold, self.bans.window, self.bans.ttl)
                     if self.bans.threshold else "disabled"))
        self._say("[ea] advertising %s:%s to clients"
                  % (self.config["advertise_host"], self.config["advertise_port"]))

        for sock, port in zip(self._listeners, ports):
            threading.Thread(target=self._accept_loop, args=(sock, port),
                             daemon=True).start()
        # Without this the client hears nothing for 60 s and tears the session
        # down. It never pings us -- it only echoes -- so this is the only
        # thing keeping an idle lobby alive.
        threading.Thread(target=self.hub.run_keepalive, daemon=True).start()
        self._say("[ea] keepalive every %.0fs (client deadline is %.0fs)"
                  % (__import__("backend.hub", fromlist=["PING_AFTER"]).PING_AFTER,
                     __import__("backend.hub", fromlist=["CLIENT_DEADLINE"]).CLIENT_DEADLINE))
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
            # Gate before the thread exists. Spawning one and then discovering
            # it is over the limit would make the limit describe how fast the
            # box dies rather than whether it does.
            #
            # Bans are checked first: an address that already earned one should
            # not also consume a connection slot to be told so.
            remaining = self.bans.banned_for(addr[0])
            refusal = None
            if remaining:
                refusal = "banned, %.0fs remaining" % remaining
                self.metrics.bump("bans_refused_total")
            else:
                refusal = self.limiter.acquire(addr[0])
            if refusal is not None:
                self._say("[ea] refused %s:%d on :%d -- %s"
                          % (addr[0], addr[1], port, refusal))
                self.metrics.bump("connections_refused_total")
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            self.metrics.bump("connections_total")
            try:
                conn.settimeout(self.send_timeout)
                threading.Thread(target=self._serve, args=(conn, addr, port),
                                 daemon=True).start()
            except (OSError, RuntimeError) as exc:
                # RuntimeError is "can't start new thread", which happens
                # exactly under the exhaustion the cap exists for -- and under
                # the --pids-limit the deployment notes recommend. Without this
                # the slot and the descriptor both leak, and the symptom is a
                # server that gradually refuses everyone for no visible reason.
                self._say("[ea] could not serve %s:%d on :%d -- %s"
                          % (addr[0], addr[1], port, exc))
                self.metrics.bump("accept_failures_total")
                self.limiter.release(addr[0])
                try:
                    conn.close()
                except OSError:
                    pass
                continue

    def stop(self) -> None:
        self._stopping.set()
        self.hub.stop()
        for sock in self._listeners:
            try:
                sock.close()
            except OSError:
                pass
        self._listeners = []

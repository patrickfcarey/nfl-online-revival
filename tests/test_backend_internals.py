"""Error paths and lifecycle in the service, the hub and the buddy stub.

These are the branches a working session never takes, which is exactly why they
rot: a handler that raises, a transcript that cannot be written, a peer that
vanishes mid-broadcast. Each is a place where the wrong behaviour is silence,
and silence on this protocol is the most expensive failure available -- the
client waits out a two-minute timeout for a reply that is never coming, and an
unanswered request wedges its pending queue permanently (0x00446ce0).

`serve_forever` blocks until Ctrl-C, so the tests swap the module's `time` for
a proxy whose `sleep` is a rendezvous, the same technique as the recon servers.
"""

from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import handlers, hub as hub_module, limits, protocol  # noqa: E402
from backend import service as service_module  # noqa: E402
from backend.buddy import BuddyService, _BuddySession  # noqa: E402
from backend.hub import Connection, Hub  # noqa: E402
from backend.service import Service, ServiceError, Session, Transcript  # noqa: E402
from backend.store import Store  # noqa: E402

CONFIG = {"advertise_host": "127.0.0.1", "advertise_port": "10001",
          "mask": "GS", "buddy_host": "127.0.0.1", "buddy_port": 10002}


def make_store(case):
    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    os.unlink(path)
    store = Store(path)
    store.seed_defaults()
    case.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
    case.addCleanup(store.close)
    return store


def pair(case, timeout=1.0):
    left, right = socket.socketpair()
    case.addCleanup(left.close)
    case.addCleanup(right.close)
    left.settimeout(timeout)
    return left, right


class _Clock:
    def __init__(self, real, on_sleep):
        self._real = real
        self._on_sleep = on_sleep

    def __getattr__(self, name):
        return getattr(self._real, name)

    def sleep(self, _seconds):
        self._on_sleep()


class TranscriptWriting(unittest.TestCase):
    def test_a_message_is_recorded_as_jsonl(self):
        handle, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(handle)
        self.addCleanup(os.unlink, path)
        transcript = Transcript(path)
        message = protocol.decode(protocol.encode("auth", protocol.OK,
                                                  {"NAME": "alice"}))
        transcript.message("recv", "peer", message, b"\x01")
        with open(path, encoding="utf-8") as handle:
            self.assertIn("alice", handle.read())

    def test_no_path_writes_nothing(self):
        Transcript(None).raw("peer", "kind", b"x")      # must not raise

    def test_an_unwritable_path_is_reported_not_fatal(self):
        # Losing the transcript is worth a line on stdout; it is not worth
        # dropping the connection that produced it.
        Transcript("/nonexistent/dir/t.jsonl").raw("peer", "kind", b"x")


class ConnectionLifecycle(unittest.TestCase):
    def test_close_on_an_already_closed_socket_is_harmless(self):
        left, _right = pair(self)
        conn = Connection(left, "x", object())
        conn.close()
        conn.close()
        self.assertTrue(conn.closed)

    def test_abort_on_a_dead_socket_is_harmless(self):
        left, _right = pair(self)
        conn = Connection(left, "x", object())
        left.close()
        conn._abort()
        self.assertTrue(conn.closed)

    def test_sending_on_a_closed_connection_returns_false(self):
        left, _right = pair(self)
        conn = Connection(left, "x", object())
        conn.close()
        self.assertFalse(conn.send(protocol.encode("~png")))

    def test_a_vanished_peer_reports_failure_rather_than_stalling(self):
        left, right = pair(self)
        conn = Connection(left, "x", object())
        right.close()
        left.close()
        self.assertFalse(conn.send(protocol.encode("~png")))
        self.assertFalse(conn.stalled, "a gone peer is not a stalled one")

    def test_idle_for_grows(self):
        left, _right = pair(self)
        conn = Connection(left, "x", object())
        self.assertGreaterEqual(conn.idle_for, 0.0)
        conn.last_sent = time.time() - 5
        self.assertGreater(conn.idle_for, 4.0)


class HubMembership(unittest.TestCase):
    def setUp(self):
        self.hub = Hub()
        self.addCleanup(self.hub.stop)

    def _conn(self, label, room=None, persona=None):
        left, _right = pair(self)

        class _S:
            pass

        session = _S()
        session.room = room
        session.persona = persona
        conn = Connection(left, label, session)
        self.hub.register(conn)
        return conn

    def test_count_tracks_registration(self):
        self.assertEqual(self.hub.count(), 0)
        self._conn("a")
        self._conn("b")
        self.assertEqual(self.hub.count(), 2)

    def test_unregister_removes_it(self):
        conn = self._conn("a")
        self.hub.unregister(conn)
        self.assertEqual(self.hub.count(), 0)

    def test_a_none_room_matches_nobody(self):
        # Otherwise a session with no room would broadcast to every other one.
        self._conn("a", room=None)
        self.assertEqual(self.hub.in_room(None), [])

    def test_in_room_selects_and_skips_closed(self):
        first = self._conn("a", room="lobby")
        second = self._conn("b", room="lobby")
        self._conn("c", room="other")
        self.assertEqual(len(self.hub.in_room("lobby")), 2)
        second.closed = True
        self.assertEqual(self.hub.in_room("lobby"), [first])

    def test_by_persona_finds_and_misses(self):
        conn = self._conn("a", persona="AliceP")
        self.assertIs(self.hub.by_persona("AliceP"), conn)
        self.assertIsNone(self.hub.by_persona("Nobody"))

    def test_broadcast_skips_the_excluded_and_the_closed(self):
        speaker = self._conn("a", room="lobby")
        listener = self._conn("b", room="lobby")
        dead = self._conn("c", room="lobby")
        dead.closed = True
        blob = protocol.encode("+msg", protocol.OK, {"N": "a", "T": "hi"})
        self.assertEqual(self.hub.broadcast([blob], room="lobby",
                                            exclude=speaker), 1)
        self.assertIs(listener, self.hub.in_room("lobby")[1])

    def test_broadcast_to_everyone_when_no_room_is_given(self):
        self._conn("a", room="lobby")
        self._conn("b", room="other")
        blob = protocol.encode("~png")
        self.assertEqual(self.hub.broadcast([blob]), 2)

    def test_a_non_push_type_is_refused(self):
        """The client matches replies by type against its queue head.

        An unsolicited `auth` could be taken for the reply to an outstanding
        one and fire the wrong callback.
        """
        conn = self._conn("a")
        with self.assertRaises(hub_module.PushError) as caught:
            self.hub.push(conn, protocol.encode("auth", protocol.OK, {}))
        self.assertIn("push-only", str(caught.exception))

    def test_ping_is_push_safe_for_its_own_reason(self):
        # Intercepted at 0x00448C58 before reply matching runs, so it never
        # reaches the pending queue.
        conn = self._conn("a")
        self.assertTrue(self.hub.push(conn, protocol.encode("~png")))

    def test_a_broken_transcript_never_breaks_delivery(self):
        class _Explodes:
            def message(self, *_a, **_k):
                raise RuntimeError("bookkeeping is down")

        hub = Hub(transcript=_Explodes())
        self.addCleanup(hub.stop)
        left, right = pair(self)
        conn = Connection(left, "a", object())
        hub.register(conn)
        self.assertTrue(hub.push(conn, protocol.encode("~png")))
        right.settimeout(2)
        self.assertTrue(right.recv(64))


class Keepalive(unittest.TestCase):
    """The only thing keeping an idle lobby alive; the client never pings."""

    def setUp(self):
        self.hub = Hub()
        self.addCleanup(self.hub.stop)

    def test_only_connections_past_the_interval_are_pinged(self):
        left, _right = pair(self)
        conn = Connection(left, "a", object())
        self.hub.register(conn)
        self.assertEqual(self.hub.keepalive_once(), 0, "pinged too early")
        conn.last_sent = time.time() - hub_module.PING_AFTER - 1
        self.assertEqual(self.hub.keepalive_once(), 1)

    def test_closed_connections_are_skipped(self):
        left, _right = pair(self)
        conn = Connection(left, "a", object())
        conn.last_sent = time.time() - 999
        conn.closed = True
        self.hub.register(conn)
        self.assertEqual(self.hub.keepalive_once(), 0)

    def test_the_interval_sits_inside_the_clients_deadline(self):
        # One lost keepalive must not be fatal.
        self.assertLess(hub_module.PING_AFTER * 2, hub_module.CLIENT_DEADLINE)

    def test_the_loop_survives_an_exception(self):
        """It must never die: it is the only thing keeping the lobby alive."""
        events = []
        hub = Hub(on_event=events.append)
        self.addCleanup(hub.stop)
        calls = []

        def explode(now=None):
            calls.append(1)
            raise RuntimeError("boom")

        hub.keepalive_once = explode
        saved = hub_module.PING_TICK
        hub_module.PING_TICK = 0.05
        try:
            thread = threading.Thread(target=hub.run_keepalive, daemon=True)
            thread.start()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and len(calls) < 2:
                time.sleep(0.05)
            self.assertGreaterEqual(len(calls), 2, "the loop stopped on error")
            self.assertTrue(any("keepalive error" in e for e in events))
        finally:
            hub_module.PING_TICK = saved
            hub.stop()

    def test_the_loop_reports_how_many_it_pinged(self):
        events = []
        hub = Hub(on_event=events.append)
        self.addCleanup(hub.stop)
        left, _right = pair(self)
        conn = Connection(left, "a", object())
        conn.last_sent = time.time() - 999
        hub.register(conn)
        saved = hub_module.PING_TICK
        hub_module.PING_TICK = 0.05
        try:
            threading.Thread(target=hub.run_keepalive, daemon=True).start()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if any("keepalive: pinged" in e for e in events):
                    break
                time.sleep(0.05)
            self.assertTrue(any("keepalive: pinged" in e for e in events),
                            events)
        finally:
            hub_module.PING_TICK = saved


class HandlerFailures(unittest.TestCase):
    """A handler bug must not drop the connection or produce silence."""

    def setUp(self):
        self.store = make_store(self)
        self.service = Service(self.store, dict(CONFIG), verbose=False,
                               limiter=limits.ConnectionLimiter(0, 0))

    def _connection(self):
        left, right = pair(self, timeout=2)
        session = Session("test:1", 10001)
        return Connection(left, "test", session), right

    def test_a_raising_handler_yields_a_failure_status(self):
        conn, peer = self._connection()
        saved = handlers.dispatch

        def explode(_ctx):
            raise RuntimeError("handler bug")

        handlers.dispatch = explode
        self.addCleanup(lambda: setattr(handlers, "dispatch", saved))
        message = protocol.decode(protocol.encode("auth", protocol.OK, {}))
        self.service._handle(conn, message)
        peer.settimeout(2)
        reply = protocol.decode(peer.recv(4096))
        self.assertEqual(reply.type, "auth")
        self.assertEqual(reply.status_tag, handlers.ERR_INTERNAL)

    def test_a_raising_handler_on_an_unencodable_type_stays_quiet(self):
        """The fallback reply cannot be built; the connection must survive.

        `raw` is supplied because _handle logs the bytes as they arrived and
        only re-encodes when it has none -- a decoded message always has them,
        so a type this malformed can only reach here synthetically.
        """
        conn, _peer = self._connection()
        saved = handlers.dispatch
        handlers.dispatch = lambda _ctx: (_ for _ in ()).throw(RuntimeError("x"))
        self.addCleanup(lambda: setattr(handlers, "dispatch", saved))
        message = protocol.Message("toolong", 0, {}, b"", b"rawbytes")
        self.service._handle(conn, message)          # must not raise

    def test_a_raising_follow_up_is_logged_and_survived(self):
        conn, peer = self._connection()
        saved = dict(handlers.AFTER_REPLY)

        def explode(_ctx):
            raise RuntimeError("follow-up bug")

        handlers.AFTER_REPLY["@dir"] = explode
        self.addCleanup(lambda: (handlers.AFTER_REPLY.clear(),
                                 handlers.AFTER_REPLY.update(saved)))
        message = protocol.decode(protocol.encode("@dir", protocol.OK, {}))
        self.service._handle(conn, message)
        peer.settimeout(2)
        self.assertTrue(peer.recv(4096), "the reply itself should still land")

    def test_an_unhandled_type_produces_no_reply(self):
        conn, peer = self._connection()
        message = protocol.decode(protocol.encode("zzzz", protocol.OK, {}))
        self.service._handle(conn, message)
        peer.settimeout(0.5)
        with self.assertRaises((socket.timeout, OSError)):
            peer.recv(64)

    def test_a_send_failure_mid_reply_stops_the_exchange(self):
        conn, peer = self._connection()
        peer.close()
        conn.sock.close()
        message = protocol.decode(protocol.encode("@dir", protocol.OK, {}))
        self.service._handle(conn, message)          # must not raise


class ServiceLifecycle(unittest.TestCase):
    def setUp(self):
        self.store = make_store(self)

    def _service(self, **kwargs):
        kwargs.setdefault("limiter", limits.ConnectionLimiter(0, 0))
        return Service(self.store, dict(CONFIG), verbose=False, **kwargs)

    def test_binding_a_taken_port_raises_and_releases_the_rest(self):
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        self.addCleanup(held.close)
        taken = held.getsockname()[1]
        free = socket.socket()
        free.bind(("127.0.0.1", 0))
        first = free.getsockname()[1]
        free.close()

        service = self._service()
        with self.assertRaises(ServiceError) as caught:
            service.serve_forever("127.0.0.1", [first, taken])
        self.assertIn("cannot bind", str(caught.exception))
        # The port it did get must be free again, or a retry after a typo
        # fails on the listener the first attempt is still holding.
        probe = socket.socket()
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", first))
        probe.close()

    def test_it_serves_a_connection_and_shuts_down(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        service = self._service()

        ready, release = threading.Event(), threading.Event()

        def on_sleep():
            ready.set()
            release.wait(20)
            raise KeyboardInterrupt

        saved = service_module.time
        service_module.time = _Clock(time, on_sleep)
        try:
            thread = threading.Thread(
                target=service.serve_forever,
                args=("127.0.0.1", [port]), daemon=True)
            thread.start()
            self.assertTrue(ready.wait(20), "serve_forever never settled")
            client = socket.create_connection(("127.0.0.1", port), timeout=5)
            self.addCleanup(client.close)
            client.sendall(protocol.encode("@dir", protocol.OK, {}))
            client.settimeout(10)
            reply = protocol.decode(client.recv(4096))
            self.assertEqual(reply.type, "@dir")
            release.set()
            thread.join(20)
        finally:
            service_module.time = saved
            service.stop()

    def test_stop_is_idempotent(self):
        service = self._service()
        service.stop()
        service.stop()

    def test_a_quiet_service_prints_nothing(self):
        # The transcript is the record; stdout belongs to whoever launched it.
        service = self._service()
        service.verbose = False
        service._say("this should not appear")


class BuddyStub(unittest.TestCase):
    """Answered with the least surprising thing, so nothing blocks on it."""

    def setUp(self):
        self.service = BuddyService(verbose=False)

    def _respond(self, verb, **fields):
        message = protocol.decode(protocol.encode(verb, protocol.OK, fields))
        return [protocol.decode(blob) for blob in self.service.respond(message)]

    def test_ping_is_echoed(self):
        self.assertEqual(self._respond("PING")[0].type, "PING")

    def test_auth_succeeds_and_echoes_its_identifiers(self):
        reply = self._respond("AUTH", USER="alice", DOMN="ea.com",
                              RSRC="ps2")[0]
        self.assertTrue(reply.ok)
        self.assertEqual(reply.fields["USER"], "alice")

    def test_roster_requests_return_an_empty_list(self):
        # An empty buddy list shows as empty rather than failing.
        for verb in ("ROST", "RGET"):
            reply = self._respond(verb)[0]
            self.assertTrue(reply.ok)
            self.assertEqual(reply.fields["LIST"], "")

    def test_disc_is_answered_with_silence(self):
        self.assertEqual(self._respond("DISC"), [])

    def test_an_unknown_verb_is_acknowledged(self):
        reply = self._respond("PSET", SHOW="CHAT")[0]
        self.assertEqual(reply.type, "PSET")
        self.assertTrue(reply.ok)

    def test_the_session_describes_itself(self):
        session = _BuddySession("10.0.0.1:1")
        self.assertIn("10.0.0.1", session.describe())
        self.assertIsNone(session.persona)

    def test_stop_before_start_is_harmless(self):
        BuddyService(verbose=False).stop()

    def test_a_ban_is_recorded_through_the_shared_list(self):
        bans = limits.BanList(threshold=1, window=60, ttl=60)
        service = BuddyService(verbose=False, bans=bans)
        service._strike("10.0.0.1", "framing")
        self.assertGreater(bans.banned_for("10.0.0.1"), 0)


if __name__ == "__main__":
    unittest.main()

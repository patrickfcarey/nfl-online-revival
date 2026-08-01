"""Connection caps, socket timeouts, and the slow-consumer wedge.

Everything here was written against a console on a LAN, where none of it
mattered. These tests exist because the failures they cover are invisible from
the server's own logs: a wedged write looks like a send still in progress, and
an exhausted thread pool looks like nobody is playing.

The important one is `SlowConsumer.test_a_stalled_peer_does_not_block_the_room`.
It fails against the code as it stood before 2026-08-01, and it fails in the way
the real bug did -- not by erroring, but by never returning.
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import limits, protocol  # noqa: E402
from backend.hub import Connection, Hub  # noqa: E402
from backend.service import Service  # noqa: E402
from backend.store import Store  # noqa: E402

CONFIG = {
    "advertise_host": "127.0.0.1",
    "advertise_port": "10001",
    "mask": "GS",
    "buddy_host": "127.0.0.1",
    "buddy_port": 10002,
}


def make_store():
    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    os.unlink(path)
    store = Store(path)
    store.seed_defaults()
    return store, path


class Limiter(unittest.TestCase):
    """The counter itself, away from any sockets."""

    def test_total_cap_refuses_past_the_limit(self):
        limiter = limits.ConnectionLimiter(total=2, per_ip=0)
        self.assertIsNone(limiter.acquire("10.0.0.1"))
        self.assertIsNone(limiter.acquire("10.0.0.2"))
        self.assertIsNotNone(limiter.acquire("10.0.0.3"))
        self.assertEqual(limiter.refused_total, 1)

    def test_per_ip_cap_is_independent_of_the_total(self):
        limiter = limits.ConnectionLimiter(total=100, per_ip=2)
        self.assertIsNone(limiter.acquire("10.0.0.1"))
        self.assertIsNone(limiter.acquire("10.0.0.1"))
        self.assertIsNotNone(limiter.acquire("10.0.0.1"))
        # A different host is unaffected -- the whole point of the second cap.
        self.assertIsNone(limiter.acquire("10.0.0.2"))
        self.assertEqual(limiter.refused_per_ip, 1)

    def test_release_returns_the_slot(self):
        limiter = limits.ConnectionLimiter(total=1, per_ip=1)
        self.assertIsNone(limiter.acquire("10.0.0.1"))
        self.assertIsNotNone(limiter.acquire("10.0.0.1"))
        limiter.release("10.0.0.1")
        self.assertIsNone(limiter.acquire("10.0.0.1"))

    def test_release_forgets_an_address_entirely(self):
        # Otherwise the map grows once per address ever seen, which on a public
        # box is a slow leak keyed on attacker input.
        limiter = limits.ConnectionLimiter()
        limiter.acquire("10.0.0.1")
        limiter.release("10.0.0.1")
        self.assertEqual(limiter.held_by("10.0.0.1"), 0)
        self.assertNotIn("10.0.0.1", limiter.describe())

    def test_release_of_an_unknown_address_is_harmless(self):
        limiter = limits.ConnectionLimiter()
        limiter.release("10.0.0.9")          # must not raise
        self.assertEqual(limiter.active, 0)

    def test_zero_means_unlimited(self):
        limiter = limits.ConnectionLimiter(total=0, per_ip=0)
        for i in range(50):
            self.assertIsNone(limiter.acquire("10.0.0.1"), "refused at %d" % i)

    def test_counts_survive_concurrent_use(self):
        limiter = limits.ConnectionLimiter(total=0, per_ip=0)

        def churn():
            for _ in range(200):
                limiter.acquire("10.0.0.1")
                limiter.release("10.0.0.1")

        threads = [threading.Thread(target=churn) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(limiter.active, 0)
        self.assertEqual(limiter.held_by("10.0.0.1"), 0)


def _stalled_pair(send_timeout: float):
    """A connected pair whose reader never reads, with tiny buffers.

    Shrinking both buffers is what makes this finish quickly: with default
    sizes it takes megabytes to fill the path, and the test would be measuring
    the kernel rather than our timeout.
    """
    left, right = socket.socketpair()
    for sock in (left, right):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2048)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
        except OSError:      # pragma: no cover - platform dependent
            pass
    left.settimeout(send_timeout)
    return left, right


class SlowConsumer(unittest.TestCase):
    """A peer that stops reading must not take anyone else with it."""

    def test_send_gives_up_instead_of_blocking_forever(self):
        sender, receiver = _stalled_pair(send_timeout=0.5)
        self.addCleanup(receiver.close)
        self.addCleanup(sender.close)
        conn = Connection(sender, "stalled", object(), send_timeout=0.5)

        started = time.monotonic()
        # Push until the unread buffer fills. Without a timeout this never
        # returns; with one it returns False after roughly send_timeout.
        for _ in range(200):
            if not conn.send(b"~png" + b"\x00" * 4096):
                break
        else:
            self.fail("send never reported failure; it is still blocking")
        elapsed = time.monotonic() - started

        self.assertTrue(conn.stalled, "a timed-out write must be marked stalled")
        self.assertTrue(conn.closed)
        self.assertLess(elapsed, 10.0,
                        "took %.1fs to give up on a 0.5s timeout" % elapsed)

    def test_a_stalled_peer_does_not_block_the_room(self):
        """The regression that matters.

        The stalled connection is registered *first*, so a hub that writes to
        its targets one at a time and blocks on the first will never reach the
        healthy one. Before the send timeout existed this test did not fail --
        it hung.
        """
        hub = Hub()
        self.addCleanup(hub.stop)

        stalled_sock, stalled_peer = _stalled_pair(send_timeout=0.5)
        self.addCleanup(stalled_peer.close)
        healthy_sock, healthy_peer = socket.socketpair()
        self.addCleanup(healthy_peer.close)
        healthy_sock.settimeout(0.5)

        class _Session:
            room = "lobby"
            persona = None

        stalled = Connection(stalled_sock, "stalled", _Session(),
                             send_timeout=0.5)
        healthy = Connection(healthy_sock, "healthy", _Session(),
                             send_timeout=0.5)
        hub.register(stalled)
        hub.register(healthy)
        self.assertIs(hub.all()[0], stalled, "ordering makes this test valid")

        # Wedge the first peer.
        for _ in range(200):
            if not stalled.send(b"~png" + b"\x00" * 4096):
                break
        self.assertTrue(stalled.stalled)

        blob = protocol.encode("+msg", protocol.OK, {"N": "someone",
                                                     "T": "hello", "F": "B"})
        done = threading.Event()
        delivered = []

        def go():
            delivered.append(hub.broadcast([blob], room="lobby"))
            done.set()

        threading.Thread(target=go, daemon=True).start()
        self.assertTrue(done.wait(timeout=15),
                        "broadcast never returned: one dead peer has frozen "
                        "the room, which is the bug this test exists for")

        self.assertEqual(delivered, [1], "the healthy peer should still get it")
        healthy_peer.settimeout(2)
        got = healthy_peer.recv(len(blob))
        self.assertEqual(got, blob)


class AcceptGate(unittest.TestCase):
    """Caps and deadlines against a real listening socket."""

    def setUp(self):
        self.store, self.path = make_store()
        self.sockets = []

    def tearDown(self):
        for sock in self.sockets:
            try:
                sock.close()
            except OSError:
                pass
        if getattr(self, "service", None) is not None:
            self.service.stop()
        self.store.close()
        os.unlink(self.path)

    def _start(self, **kwargs):
        """Run one accept loop on an ephemeral port. Returns the port."""
        kwargs.setdefault("send_timeout", 0.2)
        kwargs.setdefault("idle_timeout", 0.0)
        kwargs.setdefault("first_byte_deadline", 0.0)
        self.service = Service(self.store, dict(CONFIG), verbose=False, **kwargs)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        self.sockets.append(listener)
        port = listener.getsockname()[1]
        threading.Thread(target=self.service._accept_loop,
                         args=(listener, port), daemon=True).start()
        return port

    def _connect(self, port):
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.sockets.append(sock)
        return sock

    @staticmethod
    def _is_closed(sock, timeout=5.0):
        """True once the server hangs up on us.

        Drains rather than reading one byte. A connection that has spoken has a
        reply waiting, and reading a single byte of it reports "not closed" for
        the wrong reason -- which is exactly how the first version of the idle
        test passed against a server that was not timing anything out.
        """
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            sock.settimeout(remaining)
            try:
                if sock.recv(65535) == b"":
                    return True
            except socket.timeout:
                return False
            except OSError:
                return True

    def test_refuses_past_the_total_cap(self):
        port = self._start(limiter=limits.ConnectionLimiter(total=2, per_ip=0))
        first, second = self._connect(port), self._connect(port)
        self.assertFalse(self._is_closed(first, timeout=0.5))
        self.assertFalse(self._is_closed(second, timeout=0.5))
        # The third is accepted and immediately hung up on, which is what the
        # client can actually recover from -- see backend/limits.py.
        third = self._connect(port)
        self.assertTrue(self._is_closed(third),
                        "the connection past the cap should have been closed")

    def test_a_freed_slot_is_reusable(self):
        port = self._start(limiter=limits.ConnectionLimiter(total=1, per_ip=0))
        first = self._connect(port)
        self.assertFalse(self._is_closed(first, timeout=0.5))
        first.close()
        # The serving thread has to notice and release; give it a moment.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.service.limiter.active == 0:
                break
            time.sleep(0.05)
        self.assertEqual(self.service.limiter.active, 0)
        self.assertFalse(self._is_closed(self._connect(port), timeout=0.5))

    def test_refuses_past_the_per_ip_cap(self):
        port = self._start(limiter=limits.ConnectionLimiter(total=100, per_ip=1))
        self.assertFalse(self._is_closed(self._connect(port), timeout=0.5))
        self.assertTrue(self._is_closed(self._connect(port)))
        self.assertEqual(self.service.limiter.refused_per_ip, 1)

    def test_a_silent_connection_is_dropped(self):
        # The slowloris case: connect, send nothing, hold a thread forever.
        port = self._start(first_byte_deadline=0.6,
                           limiter=limits.ConnectionLimiter(0, 0))
        self.assertTrue(self._is_closed(self._connect(port)),
                        "a connection that never sent anything was kept")

    def test_an_idle_connection_is_dropped_after_speaking(self):
        port = self._start(idle_timeout=0.6, first_byte_deadline=0.0,
                           limiter=limits.ConnectionLimiter(0, 0))
        sock = self._connect(port)
        sock.sendall(protocol.encode("@dir", protocol.OK, {}))
        self.assertTrue(self._is_closed(sock),
                        "an established connection went quiet and was kept")

    def test_a_talkative_connection_survives(self):
        """The fail-open half. A limit that drops real players is worse than
        no limit, because the console's failure mode is to wait rather than to
        report anything."""
        port = self._start(idle_timeout=1.5, first_byte_deadline=1.5,
                           limiter=limits.ConnectionLimiter(0, 0))
        sock = self._connect(port)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            sock.sendall(protocol.encode("@dir", protocol.OK, {}))
            time.sleep(0.3)
        self.assertFalse(self._is_closed(sock, timeout=0.5),
                         "a connection sending every 0.3s was dropped by a "
                         "1.5s idle timeout")


if __name__ == "__main__":
    unittest.main()

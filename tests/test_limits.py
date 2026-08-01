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
import struct
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


class _ServiceHarness:
    """One accept loop on an ephemeral port, for the socket-level tests.

    Deliberately not a TestCase. Subclassing one to share a harness makes
    unittest collect the parent's tests again through every child, so the
    socket suites would run twice for no added coverage.
    """

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


class AcceptGate(_ServiceHarness, unittest.TestCase):
    """Caps and deadlines against a real listening socket."""

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


class Bucket(unittest.TestCase):
    def test_burst_is_spendable_at_once(self):
        bucket = limits.TokenBucket(rate=1, burst=5)
        for i in range(5):
            self.assertTrue(bucket.take(), "refused token %d of the burst" % i)
        self.assertFalse(bucket.take())

    def test_refills_over_time(self):
        bucket = limits.TokenBucket(rate=100, burst=2)
        self.assertTrue(bucket.take())
        self.assertTrue(bucket.take())
        self.assertFalse(bucket.take())
        time.sleep(0.15)             # 100/s -> ~15 tokens, capped at burst
        self.assertTrue(bucket.take())

    def test_zero_rate_and_burst_is_unlimited(self):
        bucket = limits.TokenBucket(rate=0, burst=0)
        for _ in range(1000):
            self.assertTrue(bucket.take())

    def test_zero_rate_with_a_burst_is_a_hard_quota(self):
        # Used by the tests below to make enforcement deterministic.
        bucket = limits.TokenBucket(rate=0, burst=3)
        self.assertEqual([bucket.take() for _ in range(5)],
                         [True, True, True, False, False])

    def test_a_rate_with_no_burst_is_refused_at_construction(self):
        """It would otherwise refuse every message while reading as lenient.

        Zero capacity with a positive refill rate can never hold a token long
        enough to spend one, so `--rate 20 --rate-burst 0` was a total outage
        dressed up as "no burst allowance".
        """
        with self.assertRaises(ValueError) as caught:
            limits.TokenBucket(rate=20, burst=0)
        self.assertIn("refuses every message", str(caught.exception))
        # Both zero remains the way to say unlimited.
        self.assertTrue(limits.TokenBucket(rate=0, burst=0).take())


class Rates(unittest.TestCase):
    def test_per_connection_and_per_address_are_independent(self):
        rates = limits.RateLimiter(rate=0, burst=2, ip_rate=0, ip_burst=99)
        rates.attach("10.0.0.1")
        first = rates.new_bucket()
        second = rates.new_bucket()
        self.assertIsNone(rates.check(first, "10.0.0.1"))
        self.assertIsNone(rates.check(first, "10.0.0.1"))
        self.assertIsNotNone(rates.check(first, "10.0.0.1"))
        # A second connection from the same host has its own allowance.
        self.assertIsNone(rates.check(second, "10.0.0.1"))

    def test_the_address_bucket_catches_what_per_connection_misses(self):
        # Many connections, each individually well behaved.
        rates = limits.RateLimiter(rate=0, burst=99, ip_rate=0, ip_burst=3)
        rates.attach("10.0.0.1")
        buckets = [rates.new_bucket() for _ in range(10)]
        verdicts = [rates.check(b, "10.0.0.1") for b in buckets]
        self.assertEqual(verdicts[:3], [None, None, None])
        self.assertIsNotNone(verdicts[3])
        self.assertGreater(rates.violations_per_ip, 0)

    def test_detach_drops_the_bucket_when_nothing_holds_it(self):
        rates = limits.RateLimiter()
        rates.attach("10.0.0.1")
        rates.attach("10.0.0.1")
        rates.detach("10.0.0.1")
        self.assertIn("10.0.0.1", rates._ip_buckets)   # one holder left
        rates.detach("10.0.0.1")
        self.assertNotIn("10.0.0.1", rates._ip_buckets)

    def test_peak_is_observed_even_when_nothing_is_refused(self):
        # The measurement log-only mode exists to produce.
        rates = limits.RateLimiter(rate=0, burst=0, ip_rate=0, ip_burst=0)
        state = rates.new_bucket()
        for _ in range(25):
            self.assertIsNone(rates.check(state, "10.0.0.1"))
        self.assertGreaterEqual(rates.peak, 25)

    def test_peak_is_per_connection_not_a_total_across_them(self):
        """The number the whole log-only design produces.

        `rate` is spent by one connection, so the measurement calibrating it
        has to be of one connection. Summing across them inflated the figure by
        however many clients were online -- already about double with a single
        console, which holds :10000 and the advertised port at once -- so the
        limit derived from it would be that many times too loose.
        """
        rates = limits.RateLimiter(rate=0, burst=0, ip_rate=0, ip_burst=0)
        first, second = rates.new_bucket(), rates.new_bucket()
        for _ in range(5):
            rates.check(first, "10.0.0.1")
            rates.check(second, "10.0.0.2")
        self.assertEqual(rates.peak, 5,
                         "peak reads %d for two connections sending 5 each; "
                         "it is summing them" % rates.peak)

    def test_peak_tracks_the_busiest_connection(self):
        rates = limits.RateLimiter(rate=0, burst=0, ip_rate=0, ip_burst=0)
        quiet, busy = rates.new_bucket(), rates.new_bucket()
        for _ in range(3):
            rates.check(quiet, "10.0.0.1")
        for _ in range(11):
            rates.check(busy, "10.0.0.2")
        self.assertEqual(rates.peak, 11)

    def test_describe_says_which_mode_it_is_in(self):
        self.assertIn("observing only", limits.RateLimiter().describe())
        self.assertIn("ENFORCING",
                      limits.RateLimiter(enforce=True).describe())


class Bans(unittest.TestCase):
    def test_strikes_accumulate_to_a_ban(self):
        bans = limits.BanList(threshold=3, window=60, ttl=30)
        self.assertIsNone(bans.record("10.0.0.1", "framing"))
        self.assertIsNone(bans.record("10.0.0.1", "framing"))
        self.assertIsNotNone(bans.record("10.0.0.1", "framing"))
        self.assertGreater(bans.banned_for("10.0.0.1"), 0)

    def test_strikes_outside_the_window_do_not_count(self):
        bans = limits.BanList(threshold=3, window=10, ttl=30)
        now = 1000.0
        bans.record("10.0.0.1", "x", now=now)
        bans.record("10.0.0.1", "x", now=now + 20)     # first has aged out
        self.assertIsNone(bans.record("10.0.0.1", "x", now=now + 21))
        self.assertEqual(bans.banned_for("10.0.0.1", now=now + 21), 0)

    def test_a_ban_expires(self):
        bans = limits.BanList(threshold=1, window=60, ttl=10)
        now = 1000.0
        bans.record("10.0.0.1", "x", now=now)
        self.assertGreater(bans.banned_for("10.0.0.1", now=now + 5), 0)
        self.assertEqual(bans.banned_for("10.0.0.1", now=now + 11), 0)

    def test_addresses_are_independent(self):
        bans = limits.BanList(threshold=2, window=60, ttl=30)
        bans.record("10.0.0.1", "x")
        bans.record("10.0.0.1", "x")
        self.assertGreater(bans.banned_for("10.0.0.1"), 0)
        self.assertEqual(bans.banned_for("10.0.0.2"), 0)

    def test_threshold_zero_disables_everything(self):
        bans = limits.BanList(threshold=0)
        for _ in range(100):
            self.assertIsNone(bans.record("10.0.0.1", "x"))
        self.assertEqual(bans.banned_for("10.0.0.1"), 0)

    def test_forget_lifts_a_ban(self):
        bans = limits.BanList(threshold=1, window=60, ttl=600)
        bans.record("10.0.0.1", "x")
        bans.forget("10.0.0.1")
        self.assertEqual(bans.banned_for("10.0.0.1"), 0)

    def test_tracking_is_bounded(self):
        # Both maps are keyed on attacker input and need a ceiling that does
        # not depend on expiry running.
        bans = limits.BanList(threshold=1000, window=600, ttl=600)
        for i in range(limits._MAX_TRACKED + 500):
            bans.record("10.%d.%d.%d" % (i >> 16 & 255, i >> 8 & 255, i & 255),
                        "x", now=1000.0)
        self.assertLessEqual(len(bans._strikes), limits._MAX_TRACKED)


class RateEnforcement(_ServiceHarness, unittest.TestCase):
    """Rate limiting and the pre-auth deadline, over a real socket."""

    @staticmethod
    def _quota(burst, enforce):
        """A limiter that allows exactly `burst` messages, then nothing."""
        return limits.RateLimiter(rate=0, burst=burst, ip_rate=0, ip_burst=0,
                                  enforce=enforce)

    def test_observing_never_drops_a_connection(self):
        """The safety property that makes log-only mode worth having.

        The thresholds are unmeasured guesses. If observing could disconnect,
        shipping them would risk cutting off a real player, and this client's
        response to that is to wait rather than report anything.
        """
        port = self._start(rates=self._quota(2, enforce=False),
                           limiter=limits.ConnectionLimiter(0, 0))
        sock = self._connect(port)
        for _ in range(20):
            sock.sendall(protocol.encode("@dir", protocol.OK, {}))
        self.assertFalse(self._is_closed(sock, timeout=1.5),
                         "an over-limit connection was dropped while only "
                         "observing")
        self.assertGreater(self.service.rates.violations, 0,
                           "the violations should still have been counted")

    def test_observing_never_bans(self):
        port = self._start(rates=self._quota(1, enforce=False),
                           bans=limits.BanList(threshold=2, window=60, ttl=60),
                           limiter=limits.ConnectionLimiter(0, 0))
        sock = self._connect(port)
        for _ in range(30):
            sock.sendall(protocol.encode("@dir", protocol.OK, {}))
        time.sleep(0.5)
        self.assertEqual(self.service.bans.active(), [],
                         "a guessed threshold produced a ban")

    def test_enforcing_drops_the_connection(self):
        port = self._start(rates=self._quota(2, enforce=True),
                           limiter=limits.ConnectionLimiter(0, 0))
        sock = self._connect(port)
        for _ in range(20):
            sock.sendall(protocol.encode("@dir", protocol.OK, {}))
        self.assertTrue(self._is_closed(sock),
                        "an over-limit connection survived enforcement")

    def test_a_connection_that_never_logs_in_is_dropped(self):
        # @dir does not authenticate, so this stays chatty and unauthenticated
        # -- inside every silence deadline, forever.
        port = self._start(pre_auth_deadline=1.0, idle_timeout=0.0,
                           first_byte_deadline=0.0,
                           limiter=limits.ConnectionLimiter(0, 0))
        sock = self._connect(port)
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            try:
                sock.sendall(protocol.encode("@dir", protocol.OK, {}))
            except OSError:
                break
            time.sleep(0.2)
        self.assertTrue(self._is_closed(sock),
                        "a connection talked indefinitely without logging in")

    def test_a_zero_send_timeout_is_refused(self):
        """0 means "unlimited" for every other limit and non-blocking here.

        settimeout(0) makes recv raise BlockingIOError -- an OSError but not
        socket.timeout -- so it falls past the poll handler and every
        connection is dropped on its first quiet moment. There is no sensible
        value at or below zero, so it is refused rather than accepted and
        silently catastrophic.
        """
        with self.assertRaises(ValueError) as caught:
            Service(self.store, dict(CONFIG), verbose=False, send_timeout=0)
        self.assertIn("non-blocking", str(caught.exception))

    def test_a_connection_that_never_logs_in_is_not_banned(self):
        """Closing it is the whole remedy.

        Connections to the redirector port send `@dir` and legitimately never
        authenticate, and nothing establishes that the console closes that
        socket promptly. Striking here would cost a strike per login and ban a
        real player after five, with no feedback they could act on.
        """
        port = self._start(pre_auth_deadline=0.5, idle_timeout=0.0,
                           first_byte_deadline=0.0,
                           bans=limits.BanList(threshold=2, window=60, ttl=60),
                           limiter=limits.ConnectionLimiter(0, 0))
        for _ in range(4):
            sock = self._connect(port)
            sock.sendall(protocol.encode("@dir", protocol.OK, {}))
            self._is_closed(sock, timeout=3)
            sock.close()
        self.assertEqual(self.service.bans.active(), [],
                         "a client that only ever asked @dir was banned")

    def test_repeated_framing_errors_earn_a_ban(self):
        port = self._start(bans=limits.BanList(threshold=3, window=60, ttl=60),
                           limiter=limits.ConnectionLimiter(0, 0))
        # 12 bytes whose declared length is 0 -- shorter than the header.
        garbage = b"junk" + struct.pack(">II", 0, 0)
        for _ in range(3):
            sock = self._connect(port)
            sock.sendall(garbage)
            self._is_closed(sock, timeout=2)
            sock.close()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not self.service.bans.active():
            time.sleep(0.05)
        self.assertTrue(self.service.bans.active(), "three framing errors "
                        "should have crossed a threshold of three")
        self.assertTrue(self._is_closed(self._connect(port)),
                        "a banned address was still accepted")


if __name__ == "__main__":
    unittest.main()

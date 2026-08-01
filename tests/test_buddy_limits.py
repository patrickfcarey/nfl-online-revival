"""The buddy endpoint carries the same controls as the game service.

It had none of them. Phase A gave it connection caps and timeouts; Phase C
went to `service.py` only, so `:10002` was a publicly reachable port with no
rate limiting, no bans and no counters -- and an address banned on the game
ports could simply move here.

Sharing matters more than symmetry: the ban list and the rate limiter are the
*same objects* across both endpoints, because the point of a ban is that it
holds everywhere. The connection limiter stays separate, since a console holds
one buddy socket and two game ones and they should not compete for a single
per-address budget.
"""

from __future__ import annotations

import socket
import struct
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import limits, metrics, protocol  # noqa: E402
from backend.buddy import BuddyService  # noqa: E402


class BuddyControls(unittest.TestCase):
    def setUp(self):
        self.sockets = []
        self.service = None

    def tearDown(self):
        for sock in self.sockets:
            try:
                sock.close()
            except OSError:
                pass
        if self.service is not None:
            self.service.stop()

    def _start(self, **kwargs):
        kwargs.setdefault("send_timeout", 0.2)
        kwargs.setdefault("idle_timeout", 0.0)
        kwargs.setdefault("first_byte_deadline", 0.0)
        kwargs.setdefault("limiter", limits.ConnectionLimiter(0, 0))
        self.service = BuddyService(verbose=False, **kwargs)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Ask the kernel for a free port, then hand it to the service, which
        # binds its own listener. Read the port before closing -- getsockname
        # on a closed socket is EBADF, not the address it used to have.
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.close()
        threading.Thread(target=self.service.serve_forever,
                         args=("127.0.0.1", port), daemon=True).start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port),
                                         timeout=0.2).close()
                return port
            except OSError:
                time.sleep(0.05)
        self.fail("buddy service never came up")

    def _connect(self, port):
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.sockets.append(sock)
        return sock

    @staticmethod
    def _is_closed(sock, timeout=5.0):
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

    def test_a_ban_from_the_game_service_applies_here(self):
        """The whole reason the list is shared.

        Otherwise the endpoint with fewer controls is the one worth attacking.
        """
        bans = limits.BanList(threshold=1, window=60, ttl=60)
        port = self._start(bans=bans)
        bans.record("127.0.0.1", "banned elsewhere")
        self.assertTrue(self._is_closed(self._connect(port)),
                        "an address banned on the game ports still got in")

    def test_rate_limiting_reaches_this_endpoint(self):
        rates = limits.RateLimiter(rate=0, burst=2, ip_rate=0, ip_burst=0,
                                   enforce=True)
        port = self._start(rates=rates)
        sock = self._connect(port)
        for _ in range(20):
            try:
                sock.sendall(protocol.encode("PING", protocol.OK, {}))
            except OSError:
                break
        self.assertTrue(self._is_closed(sock),
                        "an over-limit buddy connection survived enforcement")

    def test_observing_does_not_drop_here_either(self):
        rates = limits.RateLimiter(rate=0, burst=2, ip_rate=0, ip_burst=0,
                                   enforce=False)
        port = self._start(rates=rates)
        sock = self._connect(port)
        for _ in range(20):
            sock.sendall(protocol.encode("PING", protocol.OK, {}))
        self.assertFalse(self._is_closed(sock, timeout=1.5),
                         "log-only mode dropped a buddy connection")

    def test_framing_errors_earn_strikes_here_too(self):
        bans = limits.BanList(threshold=2, window=60, ttl=60)
        port = self._start(bans=bans)
        garbage = b"junk" + struct.pack(">II", 0, 0)
        for _ in range(2):
            sock = self._connect(port)
            sock.sendall(garbage)
            self._is_closed(sock, timeout=2)
            sock.close()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not bans.active():
            time.sleep(0.05)
        self.assertTrue(bans.active(), "buddy framing errors earned no strikes")

    def test_its_traffic_appears_in_the_counters(self):
        counters = metrics.Metrics()
        port = self._start(metrics=counters)
        sock = self._connect(port)
        sock.sendall(protocol.encode("PING", protocol.OK, {}))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if counters.snapshot().get("buddy_messages_total", 0):
                break
            time.sleep(0.05)
        snap = counters.snapshot()
        self.assertGreater(snap.get("buddy_connections_total", 0), 0)
        self.assertGreater(snap.get("buddy_messages_total", 0), 0)

    def test_counters_are_declared_at_zero_before_any_traffic(self):
        counters = metrics.Metrics()
        BuddyService(verbose=False, metrics=counters)
        page = counters.render()
        for name in ("buddy_connections_total", "buddy_framing_errors_total",
                     "buddy_accept_failures_total"):
            self.assertIn("nfl_%s 0" % name, page)

    def test_a_zero_send_timeout_is_refused(self):
        with self.assertRaises(ValueError):
            BuddyService(verbose=False, send_timeout=0)


if __name__ == "__main__":
    unittest.main()

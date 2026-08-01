"""The counters, and the two rules that keep them from leaking players.

Both rules are the kind that look like paranoia until they are violated once:
a metrics page bound to a public interface is an unauthenticated view of the
process, and a counter labelled with a source address is a log of who played
and when.
"""

from __future__ import annotations

import sys
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import metrics  # noqa: E402


class Counters(unittest.TestCase):
    def test_a_declared_counter_reads_zero_rather_than_being_absent(self):
        """The distinction this exists for.

        A counter that only appears once non-zero cannot be told apart from one
        that was never wired up, so an absent line reads as "nothing was
        refused" when it might mean the check does not run.
        """
        m = metrics.Metrics()
        m.declare("connections_refused_total", "Refused.")
        self.assertIn("nfl_connections_refused_total 0", m.render())

    def test_bump_accumulates(self):
        m = metrics.Metrics()
        m.declare("messages_total", "Messages.")
        m.bump("messages_total")
        m.bump("messages_total", 4)
        self.assertEqual(m.snapshot()["messages_total"], 5)

    def test_gauges_are_read_at_scrape_time(self):
        m = metrics.Metrics()
        box = {"value": 1}
        m.gauge("connections_active", "Open now.", lambda: box["value"])
        self.assertIn("nfl_connections_active 1", m.render())
        box["value"] = 9
        self.assertIn("nfl_connections_active 9", m.render())

    def test_a_broken_gauge_does_not_take_the_page_down(self):
        # The counters are the part you need when something is already wrong.
        m = metrics.Metrics()
        m.declare("messages_total", "Messages.")
        m.bump("messages_total", 3)

        def explode():
            raise RuntimeError("nope")

        m.gauge("broken", "Broken.", explode)
        page = m.render()
        self.assertIn("nfl_messages_total 3", page)
        self.assertNotIn("nfl_broken", page)
        self.assertEqual(m.snapshot()["messages_total"], 3)

    def test_help_and_type_lines_are_present(self):
        m = metrics.Metrics()
        m.declare("bans_total", "Addresses that crossed the threshold.")
        page = m.render()
        self.assertIn("# HELP nfl_bans_total Addresses that crossed the "
                      "threshold.", page)
        self.assertIn("# TYPE nfl_bans_total counter", page)


class Exposure(unittest.TestCase):
    def setUp(self):
        self.metrics = metrics.Metrics()
        self.metrics.declare("connections_total", "Accepted.")
        self.metrics.bump("connections_total", 7)
        self.server = metrics.MetricsServer(self.metrics)

    def tearDown(self):
        self.server.stop()

    def test_refuses_a_public_bind_by_default(self):
        with self.assertRaises(metrics.MetricsError) as caught:
            self.server.start("0.0.0.0", 0)
        self.assertIn("no authentication", str(caught.exception))

    def test_a_public_bind_can_be_forced(self):
        # The operator may have a private network in front of it.
        port = self.server.start("0.0.0.0", 0, allow_public=True)
        self.assertGreater(port, 0)

    def test_loopback_is_allowed(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            self.assertTrue(metrics.is_loopback(host), host)
        for host in ("0.0.0.0", "192.168.1.10", "example.com"):
            self.assertFalse(metrics.is_loopback(host), host)

    def test_serves_the_counters(self):
        port = self.server.start("127.0.0.1", 0)
        with urllib.request.urlopen("http://127.0.0.1:%d/" % port,
                                    timeout=5) as response:
            body = response.read().decode()
        self.assertIn("nfl_connections_total 7", body)

    def test_no_counter_carries_a_source_address(self):
        """Aggregate totals answer every operational question here.

        How many were refused, not who. This asserts the shape rather than any
        particular name: a Prometheus label is the only way an address could
        get onto the page, so no line may carry one.
        """
        from backend import limits
        from backend.service import Service
        from backend.store import Store
        import os
        import tempfile

        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(path)
        store = Store(path)
        store.seed_defaults()
        try:
            counters = metrics.Metrics()
            Service(store, {"advertise_host": "127.0.0.1",
                            "advertise_port": "10001", "mask": "GS"},
                    verbose=False, metrics=counters,
                    limiter=limits.ConnectionLimiter(1, 1))
            page = counters.render()
            for line in page.splitlines():
                if line.startswith("#") or not line.strip():
                    continue
                self.assertNotIn("{", line,
                                 "a labelled metric could carry an address: %r"
                                 % line)
        finally:
            store.close()
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()

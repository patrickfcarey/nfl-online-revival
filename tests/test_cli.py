"""The command line: the entry point everyone actually invokes.

`backend/__main__.py` was at 0% coverage while holding every guard that stops a
misconfigured server from looking like a working one. The failures it prevents
are all of the same shape -- the process starts, prints something reassuring,
and the console then fails for a reason nothing on the server side explains:

* an advertised port nobody is listening on, so the `@dir` redirect dead-ends
  in a refused connection that looks exactly like a rejected reply
* an advertised host that is not a dotted quad, which the client parses octet
  by octet into garbage rather than failing
* a second server on the same ports, so a console goes on talking to whatever
  configuration the *old* one had. That cost an evening once: everything looked
  like it had restarted.

Only the paths that return before `serve_forever` are exercised here. Each test
uses its own port numbers, because claiming a port set takes a real lock.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import __main__ as cli  # noqa: E402

#: Ports no test actually binds -- main() returns before it would.
_NEXT = [41000]


def ports(count=2):
    base = _NEXT[0]
    _NEXT[0] += 10
    return ",".join(str(base + i) for i in range(count)), base


def base_args(port_spec, advertised, **extra):
    argv = ["--port", port_spec, "--advertise-port", str(advertised),
            "--advertise-host", "127.0.0.1", "--quiet"]
    for key, value in extra.items():
        argv += ["--%s" % key.replace("_", "-"), str(value)]
    return argv


class PortList(unittest.TestCase):
    def test_a_single_port(self):
        self.assertEqual(cli._ports("10000"), [10000])

    def test_a_comma_separated_list(self):
        self.assertEqual(cli._ports("10000,10001,10002"),
                         [10000, 10001, 10002])

    def test_whitespace_and_empty_pieces_are_tolerated(self):
        self.assertEqual(cli._ports(" 10000 , 10001 ,"), [10000, 10001])

    def test_a_non_number_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._ports("http")

    def test_an_out_of_range_port_is_rejected(self):
        for text in ("0", "65536", "-1"):
            with self.assertRaises(argparse.ArgumentTypeError):
                cli._ports(text)

    def test_an_empty_list_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._ports(" , ")


class Defaults(unittest.TestCase):
    def test_both_lobby_ports_are_listened_on_by_default(self):
        """10000 is where the console first connects; the advertised port is
        where it reconnects. Dropping either dead-ends the redirect."""
        args = cli.build_parser().parse_args(["--advertise-host", "1.2.3.4"])
        self.assertEqual(args.port, [10000, 10001])
        self.assertIn(args.advertise_port, args.port)

    def test_rate_limiting_defaults_to_observing(self):
        args = cli.build_parser().parse_args(["--advertise-host", "1.2.3.4"])
        self.assertEqual(args.rate_limit, "observe")

    def test_the_limits_have_the_documented_defaults(self):
        args = cli.build_parser().parse_args(["--advertise-host", "1.2.3.4"])
        self.assertEqual(args.max_connections, 512)
        self.assertEqual(args.max_connections_per_ip, 8)
        self.assertEqual(args.send_timeout, 10.0)
        self.assertEqual(args.idle_timeout, 120.0)
        self.assertEqual(args.first_byte_timeout, 30.0)
        self.assertEqual(args.pre_auth_timeout, 60.0)

    def test_metrics_default_to_loopback(self):
        args = cli.build_parser().parse_args(["--advertise-host", "1.2.3.4"])
        self.assertEqual(args.metrics_bind, "127.0.0.1")
        self.assertFalse(args.metrics_allow_public)


class Guards(unittest.TestCase):
    """Every path that refuses to start, and why it matters."""

    def test_an_unlistened_advertised_port_is_refused(self):
        spec, base = ports()
        self.assertEqual(cli.main(base_args(spec, base + 99)), 2)

    def test_a_non_dotted_advertise_host_is_refused(self):
        spec, base = ports()
        for host in ("localhost", "example.com", "10.0.0", "10:0:0:1"):
            argv = ["--port", spec, "--advertise-port", str(base),
                    "--advertise-host", host, "--quiet"]
            self.assertEqual(cli.main(argv), 2, host)

    def test_a_zero_send_timeout_is_refused_with_an_explanation(self):
        """0 disables six other limits and would disable nothing here.

        settimeout(0) means non-blocking, and every connection dies on its
        first quiet moment.
        """
        spec, base = ports()
        db = tempfile.mkstemp(suffix=".db")[1]
        self.addCleanup(os.unlink, db)
        argv = base_args(spec, base, db=db, send_timeout=0)
        self.assertEqual(cli.main(argv), 2)

    def test_a_rate_with_no_burst_is_refused(self):
        spec, base = ports()
        db = tempfile.mkstemp(suffix=".db")[1]
        self.addCleanup(os.unlink, db)
        argv = base_args(spec, base, db=db, rate=20, rate_burst=0)
        self.assertEqual(cli.main(argv), 2)

    def test_rate_limiting_can_be_turned_off_entirely(self):
        # --rate-limit off is the supported way to disable it, so it must not
        # trip the burst guard.
        args = cli.build_parser().parse_args(
            ["--advertise-host", "1.2.3.4", "--rate-limit", "off",
             "--rate-burst", "0"])
        self.assertEqual(args.rate_limit, "off")

    def test_an_empty_checksum_sweep_is_refused(self):
        spec, base = ports()
        db = tempfile.mkstemp(suffix=".db")[1]
        self.addCleanup(os.unlink, db)
        argv = base_args(spec, base, db=db) + ["--roster-csum-sweep", " , "]
        self.assertEqual(cli.main(argv), 2)

    def test_an_unreadable_roster_db_is_refused(self):
        spec, base = ports()
        db = tempfile.mkstemp(suffix=".db")[1]
        self.addCleanup(os.unlink, db)
        argv = base_args(spec, base, db=db,
                         roster_db="/nonexistent/DB_TEAMS.DAT")
        self.assertEqual(cli.main(argv), 2)

    def test_a_roster_db_with_no_team_players_is_refused(self):
        # A plausible file that is the wrong one. Serving it would announce a
        # checksum computed over nothing.
        spec, base = ports()
        db = tempfile.mkstemp(suffix=".db")[1]
        self.addCleanup(os.unlink, db)
        handle, empty = tempfile.mkstemp(suffix=".DAT")
        os.write(handle, b"\x00" * 512)
        os.close(handle)
        self.addCleanup(os.unlink, empty)
        self.assertEqual(cli.main(base_args(spec, base, db=db,
                                            roster_db=empty)), 2)

    def test_an_unreadable_roster_payload_is_refused(self):
        spec, base = ports()
        db = tempfile.mkstemp(suffix=".db")[1]
        self.addCleanup(os.unlink, db)
        argv = base_args(spec, base, db=db,
                         roster_payload="/nonexistent/roster.dat")
        self.assertEqual(cli.main(argv), 2)


class PortLock(unittest.TestCase):
    """Two servers on one port set is not a harmless mistake.

    The second fails to bind while the first keeps answering, so a console goes
    on talking to the old configuration. That produced an empty roster manifest
    during a hardware test and looked exactly like a restart that had worked.
    """

    def setUp(self):
        self._saved = cli._LOCK_HANDLE

    def tearDown(self):
        if cli._LOCK_HANDLE is not None and cli._LOCK_HANDLE is not self._saved:
            try:
                cli._LOCK_HANDLE.close()
            except OSError:
                pass
        cli._LOCK_HANDLE = self._saved

    def test_a_free_port_set_is_claimed(self):
        _spec, base = ports()
        self.assertIsNone(cli._claim_ports([base, base + 1]))

    def test_a_second_claim_reports_who_holds_it(self):
        _spec, base = ports()
        self.assertIsNone(cli._claim_ports([base, base + 1]))
        holder = cli._claim_ports([base, base + 1])
        self.assertIsNotNone(holder)
        self.assertIn("PID", holder)

    def test_the_lock_is_keyed_on_the_port_set(self):
        # A different port set is a different server and must not collide.
        _spec, base = ports()
        self.assertIsNone(cli._claim_ports([base]))
        self.assertIsNone(cli._claim_ports([base + 1]))

    def test_the_order_of_the_ports_does_not_matter(self):
        _spec, base = ports()
        self.assertIsNone(cli._claim_ports([base, base + 1]))
        self.assertIsNotNone(cli._claim_ports([base + 1, base]))

    def test_a_running_server_is_refused_by_main(self):
        spec, base = ports()
        db = tempfile.mkstemp(suffix=".db")[1]
        self.addCleanup(os.unlink, db)
        self.assertIsNone(cli._claim_ports([base, base + 1]))
        self.assertEqual(cli.main(base_args(spec, base, db=db)), 2)


class RosterConfiguration(unittest.TestCase):
    """Reading a payload, and announcing the checksum of what is served."""

    @staticmethod
    def _payload(size=253044):
        blob = bytearray(size)
        blob[0:4] = struct.pack("<I", 0x08004244)
        struct.pack_into("<I", blob, 16, 0)          # no tables
        handle, path = tempfile.mkstemp(suffix=".dat")
        os.write(handle, bytes(blob))
        os.close(handle)
        return path, zlib.crc32(bytes(blob)) & 0xFFFFFFFF

    def test_an_empty_payload_file_is_refused(self):
        spec, base = ports()
        db = tempfile.mkstemp(suffix=".db")[1]
        self.addCleanup(os.unlink, db)
        handle, empty = tempfile.mkstemp(suffix=".dat")
        os.close(handle)
        self.addCleanup(os.unlink, empty)
        self.assertEqual(
            cli.main(base_args(spec, base, db=db, roster_payload=empty)), 2)

    def test_the_served_crc_comes_from_the_bytes_on_disk(self):
        # Not from a flag: the two must agree by construction, since the client
        # checks the download against the CRC we advertised.
        path, expected = self._payload()
        self.addCleanup(os.unlink, path)
        from backend.rosterfile import load
        payload, crc = load(path)
        self.assertEqual(crc, expected)
        self.assertEqual(len(payload), 253044)


class Startup(unittest.TestCase):
    """main() all the way through, with the blocking serve replaced.

    Everything the server is actually configured with is decided in this
    stretch -- the rate limiter's mode, the shared ban list, the buddy
    endpoint, the roster manifest -- and none of it was reachable while the
    last call blocked forever.
    """

    def setUp(self):
        self.built = {}
        test = self

        class _StubService:
            def __init__(self, store, config, transcript=None, **kwargs):
                test.built["config"] = config
                test.built["kwargs"] = kwargs

            def serve_forever(self, bind, ports):
                test.built["bind"] = bind
                test.built["ports"] = ports

            def stop(self):
                pass

        self._saved_service = cli.Service
        cli.Service = _StubService
        self.addCleanup(lambda: setattr(cli, "Service", self._saved_service))

        self.db = tempfile.mkstemp(suffix=".db")[1]
        os.unlink(self.db)
        self.addCleanup(lambda: os.path.exists(self.db) and os.unlink(self.db))

    def _run(self, *extra):
        spec, base = ports()
        argv = base_args(spec, base, db=self.db) + ["--metrics-port", "0",
                                                   "--buddy-port", "0"]
        return cli.main(argv + list(extra)), base

    def test_a_plain_start_succeeds(self):
        code, base = self._run()
        self.assertEqual(code, 0)
        self.assertEqual(self.built["ports"], [base, base + 1])
        self.assertEqual(self.built["bind"], "0.0.0.0")

    def test_the_advertised_address_reaches_the_config(self):
        self._run()
        self.assertEqual(self.built["config"]["advertise_host"], "127.0.0.1")

    def test_observing_is_the_default_mode(self):
        self._run()
        self.assertFalse(self.built["kwargs"]["rates"].enforce)

    def test_enforcing_can_be_selected(self):
        self._run("--rate-limit", "enforce")
        self.assertTrue(self.built["kwargs"]["rates"].enforce)

    def test_rate_limiting_can_be_switched_off(self):
        self._run("--rate-limit", "off")
        rates = self.built["kwargs"]["rates"]
        self.assertEqual((rates.rate, rates.burst), (0, 0))
        self.assertFalse(rates.enforce)

    def test_the_limits_reach_the_service(self):
        self._run("--max-connections", "7", "--max-connections-per-ip", "3",
                  "--idle-timeout", "42")
        kwargs = self.built["kwargs"]
        self.assertEqual(kwargs["limiter"].total, 7)
        self.assertEqual(kwargs["limiter"].per_ip, 3)
        self.assertEqual(kwargs["idle_timeout"], 42.0)

    def test_the_ban_list_is_configured_from_the_flags(self):
        self._run("--ban-threshold", "9", "--ban-window", "30",
                  "--ban-ttl", "60")
        bans = self.built["kwargs"]["bans"]
        self.assertEqual((bans.threshold, bans.window, bans.ttl), (9, 30, 60))

    def test_banning_can_be_disabled(self):
        self._run("--ban-threshold", "0")
        self.assertEqual(self.built["kwargs"]["bans"].threshold, 0)

    def test_a_roster_payload_is_served_and_announced(self):
        payload, _crc = self._payload()
        code, _base = self._run("--roster-payload", payload)
        self.assertEqual(code, 0)
        self.assertIn("roster_url", self.built["config"])
        self.assertIn("roster_file_crc", self.built["config"])

    def test_an_explicit_checksum_wins(self):
        # So a value observed on hardware can always override the derived one.
        code, _base = self._run("--roster-csum", "4242")
        self.assertEqual(code, 0)
        self.assertEqual(self.built["config"]["roster_csum"], "4242")

    def test_a_checksum_sweep_is_recorded(self):
        code, _base = self._run("--roster-csum-sweep", "1,2,3")
        self.assertEqual(code, 0)
        self.assertEqual(self.built["config"]["roster_csum_sweep"],
                         ["1", "2", "3"])

    def test_the_buddy_port_is_advertised_when_asked_for(self):
        spec, base = ports()
        argv = base_args(spec, base, db=self.db) + [
            "--metrics-port", "0", "--buddy-port", str(base + 5)]
        self.assertEqual(cli.main(argv), 0)
        self.assertEqual(self.built["config"]["buddy_port"], base + 5)

    @staticmethod
    def _payload(size=253044):
        blob = bytearray(size)
        blob[0:4] = struct.pack("<I", 0x08004244)
        struct.pack_into("<I", blob, 16, 0)
        handle, path = tempfile.mkstemp(suffix=".dat")
        os.write(handle, bytes(blob))
        os.close(handle)
        return path, zlib.crc32(bytes(blob)) & 0xFFFFFFFF


if __name__ == "__main__":
    unittest.main()

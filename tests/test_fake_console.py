"""The stand-in console -- untested test tooling, until now.

This is the thing that decides whether the server is right, so a bug in it is
worse than a bug in what it checks: a broken validator does not report a
failure, it reports a pass. Two of its checks encode rules the real client
enforces silently, where the console's own response to a violation is to
discard the message and go quiet:

* a ``news`` reply carries its category in the STATUS word, not the type
* a ``+ses`` pair must cross ADDR and FROM, so each console is told the
  *other's* address

The integration tests below drive the real backend through the whole login
chain and through a quickmatch pairing. That pairing has never completed on
hardware -- two PCSX2 guests are both 192.0.2.100 and cannot dial each other --
so this is the only place it runs end to end at all.
"""

from __future__ import annotations

import contextlib
import io
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
from backend.service import Service  # noqa: E402
from backend.store import Store  # noqa: E402
from tools import fake_console as fc  # noqa: E402


class _Who:
    """Just enough of a console for check_session, which reads only persona."""

    def __init__(self, persona):
        self.persona = persona


def invite(**overrides):
    """A +ses body that passes every check, so a test can break one thing."""
    fields = {"SELF": "AliceP", "HOST": "AliceP", "OPPO": "BobP",
              "ADDR": "10.0.0.2", "FROM": "10.0.0.1", "SEED": "12345",
              "WHEN": "1690000000", "NAME": "match"}
    fields.update(overrides)
    return protocol.decode(protocol.encode("+ses", protocol.OK, fields))


class CheckSession(unittest.TestCase):
    """The +ses rules, which the real client enforces by going quiet."""

    def _check(self, a_fields=None, b_fields=None):
        a, b = _Who("AliceP"), _Who("BobP")
        return fc.check_session(a, invite(**(a_fields or {})),
                                b, invite(SELF="BobP", **(b_fields or {})))

    def test_a_correct_pair_has_no_problems(self):
        self.assertEqual(self._check(), [])

    def test_a_missing_self_is_caught(self):
        self.assertTrue(any("no SELF" in p for p in self._check({"SELF": ""})))

    def test_a_missing_host_is_caught(self):
        """An empty HOST compares equal to an empty SELF.

        Both consoles would then decide they are the host, and neither would
        listen.
        """
        problems = self._check({"HOST": ""}, {"HOST": ""})
        self.assertTrue(any("no HOST" in p for p in problems), problems)

    def test_a_zero_when_is_caught(self):
        for value in ("0", ""):
            problems = self._check({"WHEN": value}, {"WHEN": value})
            self.assertTrue(any("WHEN is zero" in p for p in problems), value)

    def test_addresses_must_be_dotted_quads(self):
        # The client parses ADDR octet by octet against '.'; anything else
        # accumulates into garbage rather than failing.
        for bad in ("localhost", "16777343", "0x0A000002", "10.0.0", "999.1.1.1"):
            problems = self._check({"ADDR": bad}, {"ADDR": bad})
            self.assertTrue(any("not a dotted quad" in p for p in problems), bad)

    def test_both_invites_sharing_a_self_is_caught(self):
        a, b = _Who("AliceP"), _Who("BobP")
        problems = fc.check_session(a, invite(), b, invite())   # both SELF=AliceP
        self.assertTrue(any("same SELF" in p for p in problems), problems)

    def test_the_two_invites_must_agree_on_the_host(self):
        problems = self._check({"HOST": "AliceP"}, {"HOST": "BobP"})
        self.assertTrue(any("disagree about HOST" in p for p in problems),
                        problems)

    def test_the_seed_must_match_or_the_simulations_diverge(self):
        problems = self._check({"SEED": "1"}, {"SEED": "2"})
        self.assertTrue(any("SEED differs" in p for p in problems), problems)

    def test_the_reported_address_is_rejected(self):
        """The trap this check exists for.

        Every PCSX2 guest reports 192.0.2.100 in its `addr` message. A server
        that echoes it back sends both consoles to the same host, and the
        symptom is a match that simply never starts.
        """
        problems = self._check({"ADDR": "192.0.2.100"},
                               {"ADDR": "192.0.2.100"})
        self.assertTrue(any("reported" in p for p in problems), problems)
        self.assertTrue(any("192.0.2.100" in p for p in problems))

    def test_identical_addresses_are_not_a_failure(self):
        """Correct when both clients share a host, which is always true here.

        Failing on it would cry wolf on every local run, so it is a note rather
        than a problem -- and the note says the crossing is untested.
        """
        problems = self._check({"ADDR": "10.0.0.1", "FROM": "10.0.0.1"},
                               {"ADDR": "10.0.0.1", "FROM": "10.0.0.1"})
        self.assertEqual(problems, [])


class Batching(unittest.TestCase):
    """A reply sharing a TCP segment with a push must not be lost."""

    def _console(self):
        console = fc.FakeConsole("127.0.0.1", verbose=False)
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        console.sock = left
        left.settimeout(2)
        return console, right

    def test_a_reply_behind_a_push_in_one_segment_is_found(self):
        """The regression that looked random.

        An earlier version returned the first message of a batch and filed the
        rest straight into `pushes`, where the caller waiting for a reply never
        looked. Whether a run worked depended on how the kernel chunked the
        stream.
        """
        console, peer = self._console()
        peer.sendall(protocol.encode("+usr", protocol.OK, {"I": "1"})
                     + protocol.encode("move", protocol.OK, {"IDENT": "7"}))
        reply = console.expect("move", timeout=2)
        self.assertEqual(reply.get("IDENT"), "7")
        self.assertEqual([p.type for p in console.pushes], ["+usr"])

    def test_messages_split_across_segments_are_reassembled(self):
        console, peer = self._console()
        blob = protocol.encode("move", protocol.OK, {"IDENT": "9"})
        peer.sendall(blob[:5])
        threading.Timer(0.1, lambda: peer.sendall(blob[5:])).start()
        self.assertEqual(console.expect("move", timeout=3).get("IDENT"), "9")

    def test_arrival_order_is_preserved(self):
        console, peer = self._console()
        peer.sendall(b"".join(
            protocol.encode("+usr", protocol.OK, {"I": str(i)})
            for i in range(4)))
        seen = console.collect(0.5)
        self.assertEqual([m.get("I") for m in seen], ["0", "1", "2", "3"])

    def test_a_ping_is_echoed_not_answered_with_a_new_one(self):
        # The client never originates a keepalive; answering an echo would have
        # both sides pinging forever.
        console, peer = self._console()
        peer.sendall(protocol.encode("~png")
                     + protocol.encode("move", protocol.OK, {"IDENT": "1"}))
        console.expect("move", timeout=2)
        peer.settimeout(2)
        echoed = protocol.decode(peer.recv(4096))
        self.assertEqual(echoed.type, "~png")

    def test_a_missing_reply_times_out_rather_than_hanging(self):
        console, _peer = self._console()
        with self.assertRaises(fc.ConsoleError) as caught:
            console.expect("move", timeout=0.3)
        self.assertIn("no move reply", str(caught.exception))

    def test_a_closed_connection_is_reported(self):
        console, peer = self._console()
        peer.close()
        with self.assertRaises(fc.ConsoleError) as caught:
            console.expect("move", timeout=2)
        self.assertIn("closed the connection", str(caught.exception))

    def test_faults_are_recorded_without_aborting(self):
        # One run should surface every problem, not just the first.
        console = fc.FakeConsole("127.0.0.1", verbose=False)
        console._fault("first")
        console._fault("second")
        self.assertEqual(console.problems, ["first", "second"])


class _Backend:
    """A real backend on two ephemeral ports, for the integration tests."""

    def __init__(self, pair_any=False):
        handle, self.db = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.db)
        self.store = Store(self.db)
        self.store.seed_defaults()
        self.listeners = []
        ports = []
        for _ in range(2):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", 0))
            sock.listen(16)
            self.listeners.append(sock)
            ports.append(sock.getsockname()[1])
        self.port, self.advertised = ports
        config = {"advertise_host": "127.0.0.1",
                  "advertise_port": str(self.advertised),
                  "mask": "GS", "buddy_host": "127.0.0.1",
                  "buddy_port": self.advertised, "pair_any": pair_any}
        self.service = Service(self.store, config, verbose=False,
                               limiter=limits.ConnectionLimiter(0, 0),
                               bans=limits.BanList(threshold=0))
        for sock, port in zip(self.listeners, ports):
            threading.Thread(target=self.service._accept_loop,
                             args=(sock, port), daemon=True).start()

    def stop(self):
        self.service.stop()
        for sock in self.listeners:
            try:
                sock.close()
            except OSError:
                pass
        self.store.close()
        try:
            os.unlink(self.db)
        except OSError:
            pass


class Login(unittest.TestCase):
    """The whole login chain, driven by the client's own expectations."""

    def setUp(self):
        self.backend = _Backend()
        self.addCleanup(self.backend.stop)

    def _console(self):
        console = fc.FakeConsole("127.0.0.1", self.backend.port, verbose=False)
        self.addCleanup(console.close)
        return console

    def test_a_fresh_server_takes_a_console_through_login(self):
        # @dir, the redirect, addr, skey, auth, acct, pers, cper, sele, news --
        # with the client asserting the news tag rule as it goes.
        console = self._console()
        console.login("alice", "AliceP")
        self.assertEqual(console.problems, [],
                         "the client found protocol faults: %s"
                         % console.problems)
        self.assertEqual(console.persona, "AliceP")

    def test_logging_in_twice_reuses_the_account(self):
        # The second run takes the auth-succeeds path rather than creating.
        self._console().login("alice", "AliceP")
        second = self._console()
        second.login("alice", "AliceP")
        self.assertEqual(second.problems, [])

    def test_a_room_can_be_joined(self):
        console = self._console()
        console.login("alice", "AliceP")
        reply = console.join("Open Lobby")
        self.assertTrue(reply.ok)
        self.assertIsNotNone(reply.get("IDENT"))

    def test_chat_is_accepted(self):
        # No console has ever sent `mesg`, so this is the only exercise it gets.
        console = self._console()
        console.login("alice", "AliceP")
        console.join("Open Lobby")
        console.say("hello")
        self.assertEqual(console.problems, [])

    def test_the_news_tag_rule_is_actually_checked(self):
        """Proof the validator would notice.

        The client gates on STATUS being 'new0' + NAME; a plain success status
        there means the console discards the reply. Answering `news` with
        status 0 has to be reported as a fault, or the check is decoration.
        """
        console = self._console()
        console.login("alice", "AliceP")
        console.problems = []
        reply = protocol.decode(protocol.encode("news", protocol.OK,
                                                {"CSUM": "1"}))
        original = console.expect
        console.expect = lambda *_a, **_k: reply
        console.send = lambda *_a, **_k: None
        console._check_news()
        console.expect = original
        self.assertTrue(any("not 'new0'" in p for p in console.problems),
                        console.problems)


class Quickmatch(unittest.TestCase):
    """Two clients, one pairing -- the matchmaking path, end to end.

    This has never completed on hardware: under PCSX2 in Sockets mode both
    guests are 192.0.2.100, so they cannot dial each other. Here they can.
    """

    def setUp(self):
        self.backend = _Backend(pair_any=True)
        self.addCleanup(self.backend.stop)

    def test_two_consoles_are_introduced_to_each_other(self):
        a = fc.FakeConsole("127.0.0.1", self.backend.port, verbose=False)
        b = fc.FakeConsole("127.0.0.1", self.backend.port, verbose=False)
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        a.login("alice", "AliceP")
        a.join("Open Lobby")
        b.login("bob", "BobP")
        b.join("Open Lobby")

        a.quickmatch("-1")
        time.sleep(0.3)          # so the pairing is deterministic, not a race
        b.quickmatch("-1")

        invite_a = a.await_session(timeout=10)
        invite_b = b.await_session(timeout=10)
        self.assertIsNotNone(invite_a, "alice was never introduced")
        self.assertIsNotNone(invite_b, "bob was never introduced")

        problems = (a.problems + b.problems
                    + fc.check_session(a, invite_a, b, invite_b))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_the_pair_carries_one_host_and_two_selves(self):
        a = fc.FakeConsole("127.0.0.1", self.backend.port, verbose=False)
        b = fc.FakeConsole("127.0.0.1", self.backend.port, verbose=False)
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        a.login("alice", "AliceP")
        b.login("bob", "BobP")
        a.quickmatch("-1")
        time.sleep(0.3)
        b.quickmatch("-1")
        invite_a = a.await_session(timeout=10)
        invite_b = b.await_session(timeout=10)
        self.assertIsNotNone(invite_a)
        self.assertIsNotNone(invite_b)
        # SELF is the only field that should differ, and exactly one of them
        # names the host.
        self.assertNotEqual(invite_a.get("SELF"), invite_b.get("SELF"))
        self.assertEqual(invite_a.get("HOST"), invite_b.get("HOST"))
        selves = {invite_a.get("SELF"), invite_b.get("SELF")}
        self.assertIn(invite_a.get("HOST"), selves)

    def test_a_cancelled_queue_produces_no_introduction(self):
        a = fc.FakeConsole("127.0.0.1", self.backend.port, verbose=False)
        self.addCleanup(a.close)
        a.login("alice", "AliceP")
        a.quickmatch("-1")
        a.cancel_quickmatch()
        b = fc.FakeConsole("127.0.0.1", self.backend.port, verbose=False)
        self.addCleanup(b.close)
        b.login("bob", "BobP")
        b.quickmatch("-1")
        self.assertIsNone(b.await_session(timeout=2),
                          "a cancelled client was still paired")


class Cli(unittest.TestCase):
    """The entry points, whose exit codes are the whole result of a run."""

    def setUp(self):
        self.backend = _Backend(pair_any=True)
        self.addCleanup(self.backend.stop)

    def _run(self, function, *args):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), \
                contextlib.redirect_stderr(buffer):
            code = function(*args)
        return code, buffer.getvalue()

    def test_a_single_client_run_succeeds(self):
        code, output = self._run(fc.run_single, "127.0.0.1", self.backend.port,
                                 "tester", "TesterP", "Open Lobby", "", "", 0.0)
        self.assertEqual(code, 0, output)
        self.assertIn("OK", output)

    def test_a_single_run_can_say_something(self):
        code, output = self._run(fc.run_single, "127.0.0.1", self.backend.port,
                                 "tester", "TesterP", "Open Lobby",
                                 "hello there", "", 0.0)
        self.assertEqual(code, 0, output)
        self.assertIn("hello there", output)

    def test_a_run_against_nothing_fails_rather_than_hanging(self):
        # A closed port must not look like a pass.
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), \
                contextlib.redirect_stderr(buffer):
            with self.assertRaises((OSError, fc.ConsoleError)):
                fc.run_single("127.0.0.1", port, "t", "TP", "Open Lobby",
                              "", "", 0.0)

    def test_the_pair_run_reports_success(self):
        """The matchmaking test, through its own entry point.

        This is the check that has never been runnable on hardware: two PCSX2
        guests are both 192.0.2.100 and cannot be told apart.
        """
        code, output = self._run(fc.run_pair, "127.0.0.1",
                                 self.backend.port, "-1")
        self.assertEqual(code, 0, output)
        self.assertIn("both consoles were introduced", output)

    def test_main_dispatches_to_the_pair_runner(self):
        code, output = self._run(
            fc.main, ["--host", "127.0.0.1", "--port", str(self.backend.port),
                      "--pair"])
        self.assertEqual(code, 0, output)

    def test_main_defaults_to_a_single_client(self):
        code, output = self._run(
            fc.main, ["--host", "127.0.0.1", "--port", str(self.backend.port),
                      "--account", "solo", "--persona", "SoloP"])
        self.assertEqual(code, 0, output)

    def test_spar_gives_up_when_nobody_arrives(self):
        # Nothing else queues, so the wait must end rather than block.
        code, output = self._run(fc.run_spar, "127.0.0.1", self.backend.port,
                                 "sparrer", "SparP", "Open Lobby", "-1", 1.0)
        self.assertEqual(code, 1)
        self.assertIn("no +ses arrived", output)


if __name__ == "__main__":
    unittest.main()

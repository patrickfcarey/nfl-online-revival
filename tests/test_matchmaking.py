"""Pairing two consoles, and the `+ses` that introduces them.

The address crossing is the part worth pinning hardest. Each console must be
told the *other's* address, and the failure mode when it is not is silent: both
dial the same host and the match simply never starts. It also cannot be checked
on loopback, where the two addresses are legitimately identical -- so it is
checked here, with distinct ones, rather than by the fake-console script.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import handlers, lobby, matchmaking, protocol  # noqa: E402
from backend.handlers import Context, Session  # noqa: E402
from backend.store import Store  # noqa: E402

CONFIG = {"advertise_host": "10.0.0.1", "advertise_port": "10001", "mask": "GS"}


class FakeConnection:
    """Enough of a Connection for the matchmaker and the push path."""

    def __init__(self, persona: str, addr: str = "10.0.0.9"):
        self.session = Session("%s:40000" % addr)
        self.session.account = persona.lower()
        self.session.persona = persona
        self.session.room = "Open Lobby"
        self.session.user_id = 1
        self.closed = False
        self.sent = []

    def send(self, blob):
        self.sent.append(protocol.decode(blob))
        return True


class FakeHub:
    def __init__(self):
        self.matchmaker = matchmaking.Matchmaker()

    def push(self, conn, blob):
        return conn.send(blob)


def make_store():
    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    os.unlink(path)
    store = Store(path)
    store.seed_defaults()
    return store, path


class Queueing(unittest.TestCase):
    def setUp(self):
        self.maker = matchmaking.Matchmaker()

    def test_equal_kinds_pair(self):
        a, b = FakeConnection("A"), FakeConnection("B")
        self.assertIsNone(self.maker.enqueue(a, "-17"))
        pairing = self.maker.enqueue(b, "-17")
        self.assertEqual(pairing, (a, b))

    def test_the_waiting_client_hosts(self):
        a, b = FakeConnection("A"), FakeConnection("B")
        self.maker.enqueue(a, "-17")
        host, guest = self.maker.enqueue(b, "-17")
        self.assertIs(host, a)
        self.assertIs(guest, b)

    def test_different_kinds_do_not_pair(self):
        a, b = FakeConnection("A"), FakeConnection("B")
        self.assertIsNone(self.maker.enqueue(a, "-17"))
        self.assertIsNone(self.maker.enqueue(b, "-18"))
        self.assertEqual(self.maker.waiting_count(), 2)

    def test_withdrawing_leaves_the_queue_empty(self):
        a = FakeConnection("A")
        self.maker.enqueue(a, "-17")
        self.assertTrue(self.maker.withdraw(a))
        self.assertEqual(self.maker.waiting_count(), 0)

    def test_withdrawing_twice_is_harmless(self):
        a = FakeConnection("A")
        self.maker.enqueue(a, "-17")
        self.maker.withdraw(a)
        self.assertFalse(self.maker.withdraw(a))

    def test_requeueing_does_not_duplicate(self):
        a = FakeConnection("A")
        self.maker.enqueue(a, "-17")
        self.maker.enqueue(a, "-17")
        self.assertEqual(self.maker.waiting_count("-17"), 1)

    def test_a_closed_connection_is_skipped_not_paired(self):
        # A dead socket left in the queue would be matched with the next
        # arrival, who then waits for a console that is gone.
        dead, live = FakeConnection("Dead"), FakeConnection("Live")
        self.maker.enqueue(dead, "-17")
        dead.closed = True
        self.assertIsNone(self.maker.enqueue(live, "-17"))

    def test_pair_any_matches_across_kinds(self):
        # Testing affordance: a console's KIND is a CRC over its own build and
        # settings, so a stand-in client cannot reproduce it.
        maker = matchmaking.Matchmaker(pair_any=True)
        a, b = FakeConnection("A"), FakeConnection("B")
        self.assertIsNone(maker.enqueue(a, "-17"))
        self.assertEqual(maker.enqueue(b, "totally-different"), (a, b))

    def test_pair_any_is_off_by_default(self):
        # Real pairing must stay exact: KIND equality is the only thing the
        # client itself can verify about a match.
        a, b = FakeConnection("A"), FakeConnection("B")
        self.assertIsNone(self.maker.enqueue(a, "-17"))
        self.assertIsNone(self.maker.enqueue(b, "-18"))
        self.assertEqual(self.maker.waiting_count(), 2)

    def test_pair_any_still_skips_dead_connections(self):
        maker = matchmaking.Matchmaker(pair_any=True)
        dead, live = FakeConnection("Dead"), FakeConnection("Live")
        maker.enqueue(dead, "-17")
        dead.closed = True
        self.assertIsNone(maker.enqueue(live, "-99"))

    def test_forget_evicts_on_disconnect(self):
        a = FakeConnection("A")
        self.maker.enqueue(a, "-17")
        self.maker.forget(a)
        self.assertEqual(self.maker.waiting_count(), 0)


class Challenges(unittest.TestCase):
    def setUp(self):
        self.maker = matchmaking.Matchmaker()

    def test_the_side_claiming_host_hosts(self):
        a, b = FakeConnection("A"), FakeConnection("B")
        self.maker.offer("A", a, hosts=True)
        self.maker.offer("B", b, hosts=False)
        host, guest = self.maker.resolve("B", "A")
        self.assertIs(host, a)
        self.assertIs(guest, b)

    def test_one_side_alone_does_not_pair(self):
        a = FakeConnection("A")
        self.maker.offer("A", a, hosts=True)
        self.assertIsNone(self.maker.resolve("A", "B"))

    def test_cancelling_removes_the_offer(self):
        a = FakeConnection("A")
        self.maker.offer("A", a, hosts=True)
        self.assertTrue(self.maker.cancel_challenge("A"))
        self.assertIsNone(self.maker.resolve("A", "B"))


class SessionInvite(unittest.TestCase):
    """`+ses` -- what the client requires of the introduction."""

    def _invite(self, **kw):
        args = dict(recipient="Alice", host_persona="Alice",
                    guest_persona="Bob", host_addr="10.0.0.2",
                    guest_addr="10.0.0.3", seed=99, when=1234)
        args.update(kw)
        return protocol.decode(handlers.session_invite(**args))

    def test_addresses_are_crossed(self):
        # ADDR is dialled by the host, so it must be the GUEST's address;
        # FROM is dialled by the guest, so it must be the HOST's.
        invite = self._invite()
        self.assertEqual(invite.fields["ADDR"], "10.0.0.3")   # guest
        self.assertEqual(invite.fields["FROM"], "10.0.0.2")   # host

    def test_empty_self_or_host_is_refused(self):
        # Two empty strings compare equal at 0x004e2cbc, so both consoles would
        # take the host branch and neither would dial.
        with self.assertRaises(ValueError):
            self._invite(recipient="")
        with self.assertRaises(ValueError):
            self._invite(host_persona="")

    def test_when_must_be_non_zero(self):
        with self.assertRaises(ValueError):
            self._invite(when=0)

    def test_player_slots_are_clipped_to_fifteen(self):
        # P1-P4 are 16-byte buffers, unlike the 32 given to the name fields.
        invite = self._invite(host_persona="A" * 40, recipient="A" * 40)
        self.assertEqual(len(invite.fields["P1"]), lobby.MAX_SLOT)
        self.assertEqual(len(invite.fields["SELF"]), lobby.MAX_NAME)


class Introduction(unittest.TestCase):
    """Both invites together, which is where the crossing is provable."""

    def test_both_consoles_get_the_others_address(self):
        hub = FakeHub()
        host = FakeConnection("Alice", addr="10.0.0.2")
        guest = FakeConnection("Bob", addr="10.0.0.3")
        self.assertTrue(handlers.start_match(hub, host, guest))

        sent_host = host.sent[-1]
        sent_guest = guest.sent[-1]
        for invite in (sent_host, sent_guest):
            self.assertEqual(invite.type, "+ses")
            # Alice hosts, so ADDR is Bob's and FROM is Alice's, in BOTH bodies.
            self.assertEqual(invite.fields["ADDR"], "10.0.0.3")
            self.assertEqual(invite.fields["FROM"], "10.0.0.2")

    def test_only_self_differs_between_the_two(self):
        hub = FakeHub()
        host = FakeConnection("Alice", addr="10.0.0.2")
        guest = FakeConnection("Bob", addr="10.0.0.3")
        handlers.start_match(hub, host, guest)
        a = host.sent[-1].fields
        b = guest.sent[-1].fields
        self.assertNotEqual(a["SELF"], b["SELF"])
        self.assertEqual(a["HOST"], b["HOST"])
        self.assertEqual(a["SEED"], b["SEED"])
        differing = {k for k in a if a[k] != b.get(k)}
        self.assertEqual(differing, {"SELF"})

    def test_the_observed_address_is_used_not_the_reported_one(self):
        # A console reports its own local address, which is 192.0.2.100 for
        # every PCSX2 guest. Trusting it makes both consoles dial the same host.
        hub = FakeHub()
        host = FakeConnection("Alice", addr="10.0.0.2")
        guest = FakeConnection("Bob", addr="10.0.0.3")
        host.session.client_addr = "192.0.2.100"
        guest.session.client_addr = "192.0.2.100"
        handlers.start_match(hub, host, guest)
        for conn in (host, guest):
            fields = conn.sent[-1].fields
            self.assertNotIn("192.0.2.100", (fields["ADDR"], fields["FROM"]))


class Handlers(unittest.TestCase):
    """`quik` and `chal` must always answer."""

    def setUp(self):
        self.store, self.path = make_store()
        self.hub = FakeHub()

    def tearDown(self):
        self.store.close()
        os.unlink(self.path)

    def _dispatch(self, conn, msg_type, fields):
        msg = protocol.decode(protocol.encode(msg_type, 0, fields))
        ctx = Context(msg, conn.session, self.store, dict(CONFIG),
                      hub=self.hub, connection=conn)
        return [protocol.decode(b) for b in handlers.dispatch(ctx)], ctx

    def test_quik_always_replies(self):
        conn = FakeConnection("Alice")
        for fields in ({"KIND": "-17"}, {"KIND": "*"}, {}):
            replies, _ = self._dispatch(conn, "quik", fields)
            self.assertEqual(len(replies), 1)
            self.assertEqual(replies[0].type, "quik")

    def test_quik_queues_then_pairs(self):
        a, b = FakeConnection("Alice"), FakeConnection("Bob")
        self._dispatch(a, "quik", {"KIND": "-17"})
        self.assertEqual(self.hub.matchmaker.waiting_count(), 1)
        _replies, ctx = self._dispatch(b, "quik", {"KIND": "-17"})
        self.assertIsNotNone(ctx.session.pending_pair)

    def test_the_invite_follows_the_reply(self):
        # +ses must not precede the reply the client is still inside.
        a, b = FakeConnection("Alice"), FakeConnection("Bob")
        self._dispatch(a, "quik", {"KIND": "-17"})
        _replies, ctx = self._dispatch(b, "quik", {"KIND": "-17"})
        self.assertEqual(a.sent, [])          # nothing pushed yet
        handlers.after_quickmatch(ctx)
        self.assertTrue(a.sent and a.sent[-1].type == "+ses")

    def test_cancel_empties_the_queue(self):
        conn = FakeConnection("Alice")
        self._dispatch(conn, "quik", {"KIND": "-17"})
        self._dispatch(conn, "quik", {"KIND": "*"})
        self.assertEqual(self.hub.matchmaker.waiting_count(), 0)

    def test_chal_replies_and_pairs_on_the_host_bit(self):
        a, b = FakeConnection("Alice"), FakeConnection("Bob")
        replies, _ = self._dispatch(a, "chal", {"PERS": "Bob", "HOST": "1"})
        self.assertEqual(replies[0].type, "chal")
        self.assertTrue(replies[0].ok)
        _replies, ctx = self._dispatch(b, "chal", {"PERS": "Alice", "HOST": "0"})
        self.assertIsNotNone(ctx.session.pending_pair)
        host, _guest = ctx.session.pending_pair
        self.assertIs(host, a)

    def test_unauthenticated_is_refused_not_ignored(self):
        conn = FakeConnection("Alice")
        conn.session.account = None
        replies, _ = self._dispatch(conn, "quik", {"KIND": "-17"})
        self.assertEqual(len(replies), 1)
        self.assertFalse(replies[0].ok)


class UnimplementedVerbs(unittest.TestCase):
    """Every verb the client can send must get an answer."""

    def setUp(self):
        self.store, self.path = make_store()

    def tearDown(self):
        self.store.close()
        os.unlink(self.path)

    def test_each_is_acknowledged(self):
        # An unanswered request sits at the head of the client's pending queue
        # and blocks every later reply on that connection.
        for verb in handlers.UNIMPLEMENTED_VERBS:
            session = Session("10.0.0.9:40000")
            session.state = "idle"
            session.account = "alice"
            msg = protocol.decode(protocol.encode(verb, 0, {}))
            replies = handlers.dispatch(
                Context(msg, session, self.store, dict(CONFIG)))
            self.assertEqual(len(replies), 1, "%s went unanswered" % verb)
            self.assertEqual(protocol.decode(replies[0]).type, verb)

    def test_onln_is_covered(self):
        # It goes through the client's modal path, so the client blocks on it.
        self.assertIn("onln", handlers.UNIMPLEMENTED_VERBS)


if __name__ == "__main__":
    unittest.main()


class PushesAreTranscribed(unittest.TestCase):
    """A push must appear in the transcript.

    It did not. `hub.push()` wrote to the socket and returned; only the
    request/reply path in service.py recorded anything. So every +ses, +usr,
    +rom and +msg the server ever sent was missing from the captures, and a
    reviewer reading them correctly concluded that no +ses had ever been sent --
    while the console had demonstrably acted on one.

    The lesson is narrower than "log more": absence of evidence was being read
    as evidence of absence for exactly the messages that are hardest to observe
    from the client side.
    """

    class Recorder:
        def __init__(self):
            self.entries = []

        def message(self, direction, label, message, blob):
            self.entries.append((direction, message.type))

    def test_a_push_is_recorded(self):
        from backend.hub import Connection, Hub
        recorder = self.Recorder()
        hub = Hub(transcript=recorder)
        conn = FakeConnection("Alice")
        conn.label = "test:1"
        hub.push(conn, protocol.encode("+ses", 0, {"SELF": "Alice"}))
        self.assertEqual(recorder.entries, [("push", "+ses")])

    def test_an_undelivered_push_is_not_recorded(self):
        from backend.hub import Hub
        recorder = self.Recorder()
        hub = Hub(transcript=recorder)
        conn = FakeConnection("Alice")
        conn.label = "test:1"
        conn.send = lambda blob: False        # peer has gone
        hub.push(conn, protocol.encode("+ses", 0, {"SELF": "Alice"}))
        self.assertEqual(recorder.entries, [],
                         "a push that failed must not look delivered")

    def test_start_match_leaves_both_invites_in_the_transcript(self):
        from backend.hub import Hub
        recorder = self.Recorder()
        hub = Hub(transcript=recorder)
        hub.matchmaker = matchmaking.Matchmaker()
        host = FakeConnection("Alice", addr="10.0.0.2")
        guest = FakeConnection("Bob", addr="10.0.0.3")
        host.label, guest.label = "h:1", "g:1"
        handlers.start_match(hub, host, guest)
        self.assertEqual(recorder.entries,
                         [("push", "+ses"), ("push", "+ses")])

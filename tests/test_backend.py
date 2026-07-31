"""Tests for the reconstructed EA backend.

Offline: no sockets except one full round-trip through a real listener, no game
data, no emulator. The captured opening message from Madden NFL 2004 is used
verbatim as a fixture so the framing is checked against the wire, not against
our own encoder.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import buddy, handlers, lobby, protocol  # noqa: E402
from backend.handlers import Context, Session  # noqa: E402
from backend.hub import (CLIENT_DEADLINE, Connection, Hub,  # noqa: E402
                         PING_AFTER, PUSH_ONLY, PushError)
from backend.service import Service, Transcript  # noqa: E402
from backend.store import MAX_PERSONAS, Store  # noqa: E402

#: The exact bytes Madden NFL 2004 (PS2) sent to ps2madden04.ea.com on
#: TCP/10000, captured 2026-07-30. Real wire bytes.
MADDEN_DIR = bytes.fromhex(
    "40646972000000000000005750524f443d4d414444454e2d5053322d323030340a"
    "564552533d225053322f4d53352d4a756e2031372032303033220a4c414e473d65"
    "6e0a534c55533d4241534c55532d32303735320a00")

CONFIG = {"advertise_host": "192.168.68.85", "advertise_port": "10001",
          "mask": "GS"}


def make_store():
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    return Store(handle.name), handle.name


def open_session():
    """A session as it exists after `skey` -- the state the client is in before
    it sends anything that matters. Starting from a fresh Session would skip
    the handshake the real client always performs."""
    session = Session("test:1")
    session.state = "idle"
    return session


def run(store, msg_type, fields=None, session=None, status=protocol.OK,
        raw_payload=None):
    """Dispatch one message and return (session, decoded replies)."""
    session = session if session is not None else open_session()
    blob = protocol.encode(msg_type, status, fields or {})
    message = protocol.decode(blob)
    if raw_payload is not None:
        message = message._replace(raw_payload=raw_payload)
    replies = handlers.dispatch(Context(message, session, store, CONFIG))
    return session, [protocol.decode(r) for r in replies]


# --------------------------------------------------------------------------
# protocol
# --------------------------------------------------------------------------

class FramingTests(unittest.TestCase):
    def test_the_captured_message_decodes(self):
        msg = protocol.decode(MADDEN_DIR)
        self.assertEqual(msg.type, "@dir")
        self.assertTrue(msg.ok)
        self.assertEqual(msg.fields["PROD"], "MADDEN-PS2-2004")
        self.assertEqual(msg.fields["SLUS"], "BASLUS-20752")
        self.assertEqual(msg.fields["VERS"], "PS2/MS5-Jun 17 2003")

    def test_length_counts_the_header(self):
        self.assertEqual(len(MADDEN_DIR), 87)
        self.assertEqual(struct.unpack_from(">I", MADDEN_DIR, 8)[0], 87)

    def test_round_trip_reproduces_the_exact_length(self):
        msg = protocol.decode(MADDEN_DIR)
        again = protocol.encode(msg.type, msg.status, msg.fields)
        self.assertEqual(len(again), len(MADDEN_DIR))

    def test_encode_computes_the_length(self):
        blob = protocol.encode("acct", protocol.OK, {})
        self.assertEqual(struct.unpack_from(">I", blob, 8)[0], len(blob))

    def test_the_documented_minimum_acct_reply(self):
        # 12 bytes of header plus a single NUL: what the client accepts.
        self.assertEqual(protocol.encode("acct").hex(), "61636374" "00000000"
                                                        "0000000d" "00")


class StatusTests(unittest.TestCase):
    """The status field is four ASCII characters, not a number. Treating it as
    an integer works only by accident, because success happens to be zero."""

    def test_success_is_zero_and_renders_empty(self):
        self.assertEqual(protocol.encode_status(0), 0)
        self.assertEqual(protocol.decode_status(0), "")

    def test_a_tag_round_trips(self):
        for tag in ("dupl", "inam", "mail", "pass", "tooy", "full"):
            wire = protocol.encode_status(tag)
            self.assertEqual(protocol.decode_status(wire), tag)

    def test_a_tag_lands_in_the_status_field_not_the_payload(self):
        blob = protocol.encode("acct", "dupl", {})
        self.assertEqual(blob[4:8], b"dupl")
        decoded = protocol.decode(blob)
        self.assertFalse(decoded.ok)
        self.assertEqual(decoded.status_tag, "dupl")

    def test_a_wrong_length_tag_is_refused_not_truncated(self):
        for bad in ("dup", "duplicate", "d"):
            with self.assertRaises(protocol.ProtocolError):
                protocol.encode_status(bad)

    def test_stream_splitting_keeps_a_partial_trailer(self):
        two = MADDEN_DIR + MADDEN_DIR
        msgs, rest = protocol.split_stream(two + MADDEN_DIR[:20])
        self.assertEqual(len(msgs), 2)
        self.assertEqual(len(rest), 20)

    def test_lying_lengths_are_refused(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode(b"@dir" + struct.pack(">II", 0, 9999) + b"X")
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode(b"@dir" + struct.pack(">II", 0, 4))


# --------------------------------------------------------------------------
# store
# --------------------------------------------------------------------------

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store, self.path = make_store()

    def tearDown(self):
        self.store.close()
        os.unlink(self.path)

    def test_account_round_trip(self):
        self.store.create_account("alice", PASS="x", MAIL="a@b.com")
        row = self.store.account("alice")
        self.assertEqual(row["MAIL"], "a@b.com")
        self.assertEqual(row["ALTS"], MAX_PERSONAS)

    def test_duplicate_account_is_the_database_s_job(self):
        import sqlite3
        self.store.create_account("alice")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_account("alice")

    def test_personas_cascade_with_the_account(self):
        self.store.create_account("alice")
        self.store.create_persona("alice", "AlphaQB")
        self.assertEqual(self.store.personas("alice"), ["AlphaQB"])
        with self.store._lock:
            self.store._db.execute("DELETE FROM account WHERE NAME='alice'")
            self.store._db.commit()
        self.assertEqual(self.store.personas("alice"), [])

    def test_survives_reopen(self):
        self.store.create_account("bob", MAIL="b@c.com")
        self.store.close()
        again = Store(self.path)
        try:
            self.assertEqual(again.account("bob")["MAIL"], "b@c.com")
        finally:
            again.close()

    def test_counts_reports_every_table(self):
        counts = self.store.counts()
        self.assertIn("account", counts)
        self.assertIn("tourney", counts)


# --------------------------------------------------------------------------
# handlers
# --------------------------------------------------------------------------

class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.store, self.path = make_store()

    def tearDown(self):
        self.store.close(); os.unlink(self.path)

    def test_dir_answers_with_a_dotted_quad(self):
        msg = protocol.decode(MADDEN_DIR)
        session = Session("t:1")
        replies = [protocol.decode(b) for b in handlers.dispatch(
            Context(msg, session, self.store, CONFIG))]
        self.assertEqual(len(replies), 1)
        reply = replies[0]
        self.assertEqual(reply.type, "@dir")
        self.assertTrue(reply.ok)
        # The client parses ADDR by splitting on '.', so this must be a quad.
        self.assertEqual(reply.fields["ADDR"].count("."), 3)
        self.assertEqual(reply.fields["PORT"], "10001")

    def test_addr_is_recorded_and_gets_no_reply(self):
        session, replies = run(self.store, "addr",
                               {"ADDR": "192.0.2.100", "PORT": "5000"})
        self.assertEqual(replies, [])
        self.assertEqual(session.client_addr, "192.0.2.100")
        self.assertEqual(session.client_port, 5000)

    def test_skey_opens_the_session(self):
        session, replies = run(self.store, "skey", {"SKEY": "$5075626c"})
        self.assertEqual(session.state, "idle")
        self.assertEqual(replies[0].type, "skey")
        self.assertTrue(replies[0].fields["SKEY"].startswith("$"))


class RegistrationTests(unittest.TestCase):
    GOOD = {"NAME": "testguy", "TOS": "1", "PASS": "~abc", "MAIL": "a@b.com",
            "ALTS": "4", "BORN": "19850312", "GEND": "M", "SPAM": "NN",
            "MINAGE": "18"}

    def setUp(self):
        self.store, self.path = make_store()

    def tearDown(self):
        self.store.close(); os.unlink(self.path)

    def test_registration_creates_a_durable_account(self):
        session, replies = run(self.store, "acct", self.GOOD)
        self.assertTrue(replies[0].ok)
        self.assertEqual(session.account, "testguy")
        self.assertEqual(session.state, "acct")
        self.assertIsNotNone(self.store.account("testguy"))

    def test_duplicate_name_returns_the_clients_own_tag(self):
        run(self.store, "acct", self.GOOD)
        _s, replies = run(self.store, "acct", self.GOOD)
        self.assertFalse(replies[0].ok)
        self.assertEqual(replies[0].status_tag, handlers.ERR_DUPLICATE)

    def test_each_rejection_uses_the_tag_for_its_own_screen(self):
        cases = [
            ({"NAME": ""}, handlers.ERR_BAD_NAME),
            (dict(self.GOOD, NAME="bad name!"), handlers.ERR_BAD_NAME),
            (dict(self.GOOD, MAIL="notanemail"), handlers.ERR_BAD_MAIL),
            (dict(self.GOOD, TOS="0"), handlers.ERR_TOS),
            (dict(self.GOOD, BORN="nonsense"), handlers.ERR_BORN),
            (dict(self.GOOD, GEND="X"), handlers.ERR_GEND),
        ]
        for fields, expected in cases:
            _s, replies = run(self.store, "acct", fields)
            self.assertEqual(replies[0].status_tag, expected,
                             "fields %s" % fields)

    def test_a_child_gets_the_tooy_screen(self):
        recent = str(time.gmtime().tm_year - 5) + "0101"
        _s, replies = run(self.store, "acct",
                          dict(self.GOOD, BORN=recent, MINAGE="13"))
        self.assertEqual(replies[0].status_tag, handlers.ERR_TOO_YOUNG)

    def test_a_rejected_registration_writes_nothing(self):
        run(self.store, "acct", dict(self.GOOD, TOS="0"))
        self.assertEqual(self.store.counts()["account"], 0)


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.store, self.path = make_store()
        self.store.create_account("alice", PASS="~secret")
        self.store.create_persona("alice", "AlphaQB")
        self.store.create_persona("alice", "BravoRB")

    def tearDown(self):
        self.store.close(); os.unlink(self.path)

    def test_login_returns_the_persona_list(self):
        session, replies = run(self.store, "auth",
                               {"NAME": "alice", "PASS": "~secret"})
        self.assertTrue(replies[0].ok)
        self.assertEqual(session.state, "auth")
        self.assertEqual(replies[0].fields["PERSONAS"], "AlphaQB,BravoRB")

    def test_wrong_password_is_refused(self):
        _s, replies = run(self.store, "auth",
                          {"NAME": "alice", "PASS": "~wrong"})
        self.assertEqual(replies[0].status_tag, handlers.ERR_BAD_PASS)

    def test_unknown_account_is_refused(self):
        _s, replies = run(self.store, "auth", {"NAME": "nobody", "PASS": "x"})
        self.assertEqual(replies[0].status_tag, handlers.ERR_MISSING)

    def test_persona_selection_requires_login(self):
        _s, replies = run(self.store, "pers", {"PERS": "AlphaQB"})
        self.assertEqual(replies[0].status_tag, handlers.ERR_MISSING)

    def test_persona_selection_after_login(self):
        session, _ = run(self.store, "auth", {"NAME": "alice", "PASS": "~secret"})
        session, replies = run(self.store, "pers", {"PERS": "AlphaQB"},
                               session=session)
        self.assertTrue(replies[0].ok)
        self.assertEqual(session.persona, "AlphaQB")

    def test_selecting_someone_elses_persona_is_refused(self):
        self.store.create_account("mallory")
        session, _ = run(self.store, "auth", {"NAME": "mallory", "PASS": ""})
        _s, replies = run(self.store, "pers", {"PERS": "AlphaQB"}, session=session)
        self.assertEqual(replies[0].status_tag, handlers.ERR_MISSING)


class PersonaTests(unittest.TestCase):
    def setUp(self):
        self.store, self.path = make_store()
        self.store.create_account("alice", PASS="")
        self.session, _ = run(self.store, "auth", {"NAME": "alice", "PASS": ""},
                              session=open_session())

    def tearDown(self):
        self.store.close(); os.unlink(self.path)

    def test_create_and_list(self):
        _s, replies = run(self.store, "cper", {"PERS": "AlphaQB"},
                          session=self.session)
        self.assertTrue(replies[0].ok)
        self.assertEqual(replies[0].fields["PERSONAS"], "AlphaQB")

    def test_the_four_slot_limit_is_enforced(self):
        for name in ("one", "two", "three", "four"):
            run(self.store, "cper", {"PERS": name}, session=self.session)
        _s, replies = run(self.store, "cper", {"PERS": "five"},
                          session=self.session)
        self.assertEqual(replies[0].status_tag, handlers.ERR_TOO_MANY)

    def test_a_too_long_persona_is_refused(self):
        # The client reads persona names through a 13-byte buffer.
        _s, replies = run(self.store, "cper", {"PERS": "A" * 13},
                          session=self.session)
        self.assertEqual(replies[0].status_tag, handlers.ERR_BAD_NAME)

    def test_duplicate_persona_is_refused(self):
        run(self.store, "cper", {"PERS": "AlphaQB"}, session=self.session)
        _s, replies = run(self.store, "cper", {"PERS": "AlphaQB"},
                          session=self.session)
        self.assertEqual(replies[0].status_tag, handlers.ERR_DUPLICATE)

    def test_delete(self):
        run(self.store, "cper", {"PERS": "AlphaQB"}, session=self.session)
        _s, replies = run(self.store, "dper", {"PERS": "AlphaQB"},
                          session=self.session)
        self.assertTrue(replies[0].ok)
        self.assertEqual(self.store.personas("alice"), [])


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.store, self.path = make_store()

    def tearDown(self):
        self.store.close(); os.unlink(self.path)

    def test_space_separated_body_is_parsed(self):
        """The client sends `ROOMS=1 USERS=1 RANKS=1 MESGS=1` on one line, not
        one key per line, so the ordinary field split sees a single key."""
        session, replies = run(
            self.store, "sele", {},
            raw_payload=b"ROOMS=1 USERS=1 RANKS=1 MESGS=1\x00")
        self.assertTrue(session.subscriptions.get("ROOMS"))
        self.assertTrue(session.subscriptions.get("MESGS"))
        self.assertEqual(replies[0].fields["SLOTS"], str(MAX_PERSONAS))

    def test_ping_is_not_echoed_back(self):
        """The client never originates a ping -- it echoes any it receives. So
        an incoming ~png is the echo of ours, and answering it would make the
        two of us ping each other forever."""
        session, replies = run(self.store, "~png", {})
        self.assertEqual(replies, [])
        self.assertGreater(session.last_seen, 0)

    def test_an_unregistered_type_is_silent(self):
        _s, replies = run(self.store, "zzzz", {})
        self.assertEqual(replies, [])


class HandlerRegistryTests(unittest.TestCase):
    def test_every_type_seen_on_the_wire_has_a_handler(self):
        for msg_type in ("@dir", "addr", "skey", "acct", "auth", "pers"):
            self.assertIsNotNone(handlers.handler_for(msg_type), msg_type)

    def test_registration_rejects_a_wrong_length_type(self):
        with self.assertRaises(ValueError):
            handlers.handles("toolong")(lambda ctx: [])


class ReviewRegressionTests(unittest.TestCase):
    """Each of these is a defect found by reviewing the first cut."""

    def setUp(self):
        self.store, self.path = make_store()

    def tearDown(self):
        self.store.close(); os.unlink(self.path)

    def test_a_message_keeps_the_bytes_it_was_decoded_from(self):
        """The transcript must record the wire, not our re-encode of it: the
        two differ exactly when the parsing is wrong."""
        msg = protocol.decode(MADDEN_DIR)
        self.assertEqual(msg.raw, MADDEN_DIR)

    def test_raw_survives_stream_splitting(self):
        msgs, _rest = protocol.split_stream(MADDEN_DIR + MADDEN_DIR)
        self.assertTrue(all(m.raw == MADDEN_DIR for m in msgs))

    def test_an_absurd_declared_length_is_refused_not_buffered(self):
        """A desynced stream can claim 4 GB; without a cap the reader waits for
        bytes that never arrive while the buffer grows."""
        huge = b"@dir" + struct.pack(">II", 0, 0xFFFFFFFF)
        with self.assertRaises(protocol.ProtocolError):
            protocol.split_stream(huge)

    def test_an_out_of_range_int_status_is_a_protocol_error(self):
        for bad in (-1, 2 ** 32):
            with self.assertRaises(protocol.ProtocolError):
                protocol.encode("acct", bad, {})

    def test_only_known_account_columns_may_be_written(self):
        """Column names cannot be parameterised, so they are interpolated --
        safe only while the set stays fixed and checked."""
        from backend.store import StoreError
        self.store.create_account("alice")
        with self.assertRaises(StoreError):
            self.store.update_account("alice", **{"MAIL = 'x' WHERE 1=1 --": "v"})
        with self.assertRaises(StoreError):
            self.store.create_account("bob", NOTACOLUMN="v")

    def test_an_account_with_no_password_does_not_accept_any_password(self):
        """Skipping the comparison when the stored value is empty is the
        opposite of what an empty password means."""
        self.store.create_account("nopass")
        _s, replies = run(self.store, "auth",
                          {"NAME": "nopass", "PASS": "anything"})
        self.assertEqual(replies[0].status_tag, handlers.ERR_BAD_PASS)
        _s, replies = run(self.store, "auth", {"NAME": "nopass", "PASS": ""})
        self.assertTrue(replies[0].ok)


class InjectionTests(unittest.TestCase):
    """A field value must not be able to forge the payload's own framing."""

    def test_a_newline_in_a_value_is_refused(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.encode("auth", 0, {"NAME": "eve\nADMIN=1"})

    def test_a_nul_in_a_value_is_refused(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.encode("auth", 0, {"NAME": "eve\x00tail"})

    def test_a_key_that_would_break_framing_is_refused(self):
        for bad in ("A=B", "A\nB", "A\x00B"):
            with self.assertRaises(protocol.ProtocolError):
                protocol.encode("auth", 0, {bad: "v"})

    def test_an_equals_sign_inside_a_value_is_fine(self):
        # Only the first '=' separates, so this is data, not injection.
        blob = protocol.encode("auth", 0, {"A": "x=y"})
        self.assertEqual(protocol.decode(blob).fields["A"], "x=y")

    def test_a_persona_read_back_from_the_store_cannot_inject(self):
        """The encoder is the last line of defence for values that did not
        come straight off the wire."""
        store, path = make_store()
        try:
            store.create_account("alice")
            # Bypass the handler's own validation, as a future bug might.
            with store._lock:
                store._db.execute(
                    "INSERT INTO persona(PERS, NAME, created) VALUES(?,?,0)",
                    ("bad\nOPTS=x", "alice"))
                store._db.commit()
            session = open_session()
            _s, replies = run(store, "auth", {"NAME": "alice", "PASS": ""},
                              session=session)
            self.fail("expected the encoder to refuse, got %r" % replies)
        except protocol.ProtocolError:
            pass
        finally:
            store.close(); os.unlink(path)


class SessionGateTests(unittest.TestCase):
    """The client's own wrapper only sends in {idle, auth, acct, skey}, so a
    message arriving before the session is open is a desync, not a request."""

    def setUp(self):
        self.store, self.path = make_store()

    def tearDown(self):
        self.store.close(); os.unlink(self.path)

    def test_acct_before_skey_is_refused(self):
        fresh = Session("t:1")            # state 'conn'
        _s, replies = run(self.store, "acct", {"NAME": "x", "TOS": "1"},
                          session=fresh)
        self.assertEqual(replies[0].status_tag, handlers.ERR_INTERNAL)
        self.assertEqual(self.store.counts()["account"], 0)

    def test_auth_before_skey_is_refused(self):
        self.store.create_account("alice", PASS="")
        fresh = Session("t:1")
        _s, replies = run(self.store, "auth", {"NAME": "alice", "PASS": ""},
                          session=fresh)
        self.assertEqual(replies[0].status_tag, handlers.ERR_INTERNAL)

    def test_skey_opens_the_gate(self):
        session, _ = run(self.store, "skey", {"SKEY": "$00"},
                         session=Session("t:1"))
        self.assertIn(session.state, handlers.OPEN_STATES)


class DefaultRoomTests(unittest.TestCase):
    def test_seed_creates_lobbies_and_is_idempotent(self):
        store, path = make_store()
        try:
            self.assertEqual(len(store.rooms()), 0)
            store.seed_defaults()
            first = len(store.rooms())
            self.assertGreater(first, 0)
            store.seed_defaults()
            self.assertEqual(len(store.rooms()), first)
        finally:
            store.close(); os.unlink(path)


class _FakeSocket:
    """Records what was written, and can pretend the peer has gone."""

    def __init__(self, fail=False):
        self.written = []
        self.fail = fail
        self.closed = False

    def sendall(self, blob):
        if self.fail:
            raise OSError("peer gone")
        self.written.append(blob)

    def close(self):
        self.closed = True


class HubTests(unittest.TestCase):
    def setUp(self):
        self.hub = Hub()

    def tearDown(self):
        self.hub.stop()

    def _conn(self, label="c:1", fail=False):
        session = open_session()
        conn = Connection(_FakeSocket(fail), label, session)
        self.hub.register(conn)
        return conn

    def test_only_push_only_types_may_be_sent_unsolicited(self):
        """The client matches replies by type against the head of its pending
        queue, so an unsolicited reply-type can fire the wrong callback."""
        conn = self._conn()
        for good in ("+rom", "+usr", "+msg", "+ses"):
            self.assertTrue(self.hub.push(conn, protocol.encode(good)))
        for bad in ("auth", "acct", "@dir", "pers", "sele"):
            with self.assertRaises(PushError, msg=bad):
                self.hub.push(conn, protocol.encode(bad))

    def test_the_push_only_set_matches_the_documented_types(self):
        self.assertEqual(PUSH_ONLY, frozenset(
            ("+ses", "+msg", "+who", "+rom", "+pop", "+usr", "+rnk", "+snp")))

    def test_ping_is_unsolicited_safe_for_a_different_reason(self):
        """~png is not push-only, but the client intercepts it before the
        reply matching runs, so it can never be taken for a reply."""
        from backend.hub import SAFE_UNSOLICITED
        self.assertNotIn("~png", PUSH_ONLY)
        self.assertIn("~png", SAFE_UNSOLICITED)
        conn = self._conn("p")
        self.assertTrue(self.hub.push(conn, protocol.encode("~png")))

    def test_keepalive_pings_only_connections_that_have_gone_quiet(self):
        fresh = self._conn("fresh")
        stale = self._conn("stale")
        stale.last_sent = time.time() - (PING_AFTER + 1)
        self.assertEqual(self.hub.keepalive_once(), 1)
        self.assertEqual(len(stale.sock.written), 1)
        self.assertEqual(protocol.decode(stale.sock.written[0]).type, "~png")
        self.assertEqual(fresh.sock.written, [])

    def test_the_keepalive_interval_leaves_room_under_the_client_deadline(self):
        # One lost keepalive must not be fatal.
        self.assertLess(PING_AFTER * 2, CLIENT_DEADLINE)

    def test_sending_marks_a_dead_peer_closed_rather_than_raising(self):
        conn = self._conn("dead", fail=True)
        self.assertFalse(conn.send(protocol.encode("+rom")))
        self.assertTrue(conn.closed)

    def test_broadcast_reaches_a_room_and_can_exclude_the_sender(self):
        a, b, c = self._conn("a"), self._conn("b"), self._conn("c")
        a.session.room = b.session.room = "Lobby"
        c.session.room = "Other"
        sent = self.hub.broadcast([protocol.encode("+usr")], room="Lobby",
                                  exclude=a)
        self.assertEqual(sent, 1)
        self.assertEqual(len(b.sock.written), 1)
        self.assertEqual(c.sock.written, [])
        self.assertEqual(a.sock.written, [])

    def test_writes_to_one_connection_are_serialised(self):
        """A torn message is not a delay -- the client cannot reassemble, so
        the stream desynchronises permanently."""
        conn = self._conn("busy")
        blob = protocol.encode("+usr", 0, {"N": "x" * 200})
        errors = []

        def hammer():
            try:
                for _ in range(50):
                    conn.send(blob)
            except Exception as exc:      # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(conn.sock.written), 200)
        # Every recorded write is exactly one whole message.
        self.assertTrue(all(w == blob for w in conn.sock.written))

    def test_by_persona_and_in_room_lookups(self):
        a = self._conn("a")
        a.session.persona = "AlphaQB"
        a.session.room = "Lobby"
        self.assertIs(self.hub.by_persona("AlphaQB"), a)
        self.assertIsNone(self.hub.by_persona("nobody"))
        self.assertEqual(self.hub.in_room("Lobby"), [a])
        self.assertEqual(self.hub.in_room(None), [])


class MessageCeilingTests(unittest.TestCase):
    """The client clamps its socket buffer to 8192 and writes a NUL at
    base+declared_length-1 without bounds checking."""

    def test_a_message_over_the_ceiling_is_refused(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.encode("+msg", 0, {"BODY": "x" * 9000})

    def test_the_ceiling_matches_the_clients_buffer(self):
        self.assertEqual(protocol.MAX_MESSAGE_SIZE, 8192)

    def test_a_message_just_under_the_ceiling_is_fine(self):
        blob = protocol.encode("+msg", 0, {"BODY": "x" * 8000})
        self.assertLessEqual(len(blob), protocol.MAX_MESSAGE_SIZE)


# --------------------------------------------------------------------------
# end to end, over a real socket
# --------------------------------------------------------------------------

def _free_port_pair():
    """Two ports the OS has just confirmed free.

    Each test needs its own pair: reusing fixed ports across test methods
    leaves the previous listener lingering and the next bind fails inside the
    service thread, which surfaces as a connection reset in the client and
    looks like a server bug.
    """
    socks = []
    ports = []
    for _ in range(2):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        ports.append(sock.getsockname()[1])
        socks.append(sock)
    for sock in socks:
        sock.close()
    return ports


class LiveSessionTests(unittest.TestCase):
    """Drive a real listener with the captured bytes, all the way to a login."""

    def setUp(self):
        self.store, self.path = make_store()
        handle = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        handle.close()
        self.transcript_path = handle.name
        self.first, self.second = _free_port_pair()
        self.service = Service(self.store,
                               dict(CONFIG, advertise_port=str(self.second)),
                               Transcript(self.transcript_path), verbose=False)
        self.thread = threading.Thread(
            target=self.service.serve_forever,
            args=("127.0.0.1", [self.first, self.second]), daemon=True)
        self.thread.start()
        deadline = time.time() + 5
        while time.time() < deadline:          # wait for the listener, don't guess
            try:
                probe = socket.create_connection(("127.0.0.1", self.first), 1)
                probe.close()
                break
            except OSError:
                time.sleep(0.05)

    def tearDown(self):
        self.service.stop()
        self.store.close()
        os.unlink(self.path)
        os.unlink(self.transcript_path)

    def _exchange(self, port, blobs, expect):
        sock = socket.create_connection(("127.0.0.1", port), 5)
        sock.settimeout(5)
        received = []
        try:
            for blob in blobs:
                sock.sendall(blob)
            buffer = b""
            while len(received) < expect:
                chunk = sock.recv(65535)
                if not chunk:
                    break
                buffer += chunk
                msgs, buffer = protocol.split_stream(buffer)
                received.extend(msgs)
        finally:
            sock.close()
        return received

    def test_the_captured_opening_message_is_answered(self):
        got = self._exchange(self.first, [MADDEN_DIR], 1)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].type, "@dir")
        self.assertEqual(got[0].fields["PORT"], str(self.second))

    def test_full_session_registration_then_login(self):
        # Stage one: directory lookup on the first port.
        self._exchange(self.first, [MADDEN_DIR], 1)
        # Stage two: everything else on the advertised port.
        got = self._exchange(self.second, [
            protocol.encode("addr", 0, {"ADDR": "192.0.2.100", "PORT": "5000"}),
            protocol.encode("skey", 0, {"SKEY": "$5075626c"}),
            protocol.encode("acct", 0, {"NAME": "wirely", "TOS": "1",
                                        "PASS": "~pw", "MAIL": "w@x.com",
                                        "BORN": "19800101", "GEND": "M"}),
            protocol.encode("auth", 0, {"NAME": "wirely", "PASS": "~pw"}),
        ], 3)   # addr is deliberately unanswered
        self.assertEqual([m.type for m in got], ["skey", "acct", "auth"])
        self.assertTrue(all(m.ok for m in got))
        self.assertIn("PERSONAS", got[2].fields)
        # And it is durable.
        self.assertIsNotNone(self.store.account("wirely"))

    def test_garbage_is_recorded_not_silently_dropped(self):
        """A rejected message must never look like a message never sent."""
        sock = socket.create_connection(("127.0.0.1", self.second), 5)
        try:
            sock.sendall(b"@dir" + struct.pack(">II", 0, 4) + b"junkjunk")
            time.sleep(0.4)
        finally:
            sock.close()
        time.sleep(0.3)
        with open(self.transcript_path) as reader:
            kinds = [json.loads(line)["dir"] for line in reader if line.strip()]
        self.assertIn("framing-error", kinds)




# --------------------------------------------------------------------------
# lobby, chat, matchmaking, buddy
# --------------------------------------------------------------------------



class RoomRecordTests(unittest.TestCase):
    """Record shapes the client will actually accept."""

    def test_a_room_record_always_carries_F(self):
        """An absent F defaults to -1, which sets the private bit and makes the
        client show every room as password-protected."""
        msg = protocol.decode(lobby.room_record(1, "Open Lobby"))
        self.assertIn("F", msg.fields)
        self.assertEqual(msg.fields["F"], "")
        self.assertEqual(msg.type, "+rom")

    def test_private_rooms_set_the_P_letter(self):
        msg = protocol.decode(lobby.room_record(1, "VIP", private=True))
        self.assertEqual(msg.fields["F"], lobby.ROOM_PRIVATE)

    def test_removal_omits_the_name(self):
        """A record with no N is how the client is told to delete an entry."""
        msg = protocol.decode(lobby.room_removal(3))
        self.assertNotIn("N", msg.fields)
        self.assertEqual(msg.fields["I"], "3")
        msg = protocol.decode(lobby.user_removal(7))
        self.assertNotIn("N", msg.fields)

    def test_ids_must_be_positive(self):
        for bad in (0, -1):
            with self.assertRaises(ValueError):
                lobby.room_record(bad, "x")
            with self.assertRaises(ValueError):
                lobby.user_record(bad, "x")

    def test_ping_has_three_distinct_states(self):
        blank = protocol.decode(lobby.room_record(1, "a"))
        dashes = protocol.decode(lobby.room_record(1, "a", ping=0))
        value = protocol.decode(lobby.room_record(1, "a", ping=42))
        self.assertNotIn("P", blank.fields)      # absent -> blank
        self.assertEqual(dashes.fields["P"], "0")   # zero  -> "---"
        self.assertEqual(value.fields["P"], "42")   # >0    -> "~42ms"

    def test_names_are_clipped_to_the_clients_buffer(self):
        msg = protocol.decode(lobby.user_record(1, "N" * 100))
        self.assertEqual(len(msg.fields["N"]), lobby.MAX_NAME)

    def test_self_flag_marks_the_clients_own_row(self):
        msg = protocol.decode(lobby.user_record(1, "me", is_self=True))
        self.assertEqual(msg.fields["F"], lobby.USER_SELF)

    def test_population_pairs_and_the_zero_terminator(self):
        msg = protocol.decode(lobby.population([(1, 4), (2, 0)]))
        self.assertEqual(msg.type, "+pop")
        self.assertIn("1,4", msg.fields["Z"])
        with self.assertRaises(ValueError):
            lobby.population([(0, 1)])       # id 0 terminates the list
        with self.assertRaises(ValueError):
            lobby.population([(i, 9) for i in range(1, 200)])   # over 512 bytes

    def test_whereabouts_carries_the_room_id(self):
        msg = protocol.decode(lobby.whereabouts("AlphaQB", 2, "Ranked", 3))
        self.assertEqual(msg.type, "+who")
        self.assertEqual(msg.fields["RI"], "2")
        self.assertEqual(msg.fields["R"], "Ranked")

    def test_every_lobby_push_is_a_safe_unsolicited_type(self):
        from backend.hub import SAFE_UNSOLICITED
        for blob in (lobby.room_record(1, "a"), lobby.user_record(1, "b"),
                     lobby.population([(1, 1)]), lobby.whereabouts("p", 1, "a"),
                     lobby.room_removal(1), lobby.user_removal(1)):
            self.assertIn(blob[:4].decode(), SAFE_UNSOLICITED)


class MoveTests(unittest.TestCase):
    def setUp(self):
        self.store, self.path = make_store()
        self.store.seed_defaults()
        self.store.create_account("alice", PASS="")
        self.session, _ = run(self.store, "auth", {"NAME": "alice", "PASS": ""},
                              session=open_session())
        self.session.user_id = 1

    def tearDown(self):
        self.store.close(); os.unlink(self.path)

    def test_joining_by_name_returns_the_room_id(self):
        session, replies = run(self.store, "move", {"NAME": "Open Lobby"},
                               session=self.session)
        self.assertTrue(replies[0].ok)
        self.assertEqual(session.room, "Open Lobby")
        self.assertEqual(replies[0].fields["IDENT"], str(session.room_id))
        self.assertEqual(replies[0].fields["NAME"], "Open Lobby")

    def test_an_empty_name_leaves(self):
        run(self.store, "move", {"NAME": "Open Lobby"}, session=self.session)
        session, replies = run(self.store, "move", {"NAME": ""},
                               session=self.session)
        self.assertIsNone(session.room)
        self.assertEqual(replies[0].fields["IDENT"], "-1")

    def test_the_reply_reports_the_room_being_left(self):
        run(self.store, "move", {"NAME": "Open Lobby"}, session=self.session)
        first_id = self.session.room_id
        _s, replies = run(self.store, "move", {"NAME": "Ranked"},
                          session=self.session)
        self.assertEqual(replies[0].fields["LIDENT"], str(first_id))

    def test_an_unknown_room_is_refused(self):
        _s, replies = run(self.store, "move", {"NAME": "Nowhere"},
                          session=self.session)
        self.assertEqual(replies[0].status_tag, handlers.ERR_MISSING)

    def test_a_wrong_password_uses_the_clients_own_tag(self):
        self.store.ensure_room("Private", "", 8, password="secret")
        _s, replies = run(self.store, "move", {"NAME": "Private", "PASS": "no"},
                          session=self.session)
        self.assertEqual(replies[0].status_tag, handlers.ERR_BAD_PASSWORD)
        _s, replies = run(self.store, "move",
                          {"NAME": "Private", "PASS": "secret"},
                          session=self.session)
        self.assertTrue(replies[0].ok)

    def test_joining_requires_a_login(self):
        _s, replies = run(self.store, "move", {"NAME": "Open Lobby"},
                          session=open_session())
        self.assertEqual(replies[0].status_tag, handlers.ERR_MISSING)


class MatchmakingTests(unittest.TestCase):
    def test_the_invite_tells_each_side_a_different_story(self):
        """Each client works out its role by comparing SELF against HOST."""
        host = protocol.decode(handlers.session_invite(
            "HostGuy", "HostGuy", "GuestGuy", "10.0.0.1", "10.0.0.2", 7, 99))
        guest = protocol.decode(handlers.session_invite(
            "GuestGuy", "HostGuy", "GuestGuy", "10.0.0.1", "10.0.0.2", 7, 99))
        self.assertEqual(host.fields["SELF"], host.fields["HOST"])
        self.assertNotEqual(guest.fields["SELF"], guest.fields["HOST"])
        # Host dials OPPO at ADDR; guest dials HOST at FROM.
        self.assertEqual(host.fields["ADDR"], "10.0.0.2")
        self.assertEqual(guest.fields["FROM"], "10.0.0.1")

    def test_when_must_be_non_zero(self):
        """The client only delivers the record while WHEN is set."""
        with self.assertRaises(ValueError):
            handlers.session_invite("a", "a", "b", "1.1.1.1", "2.2.2.2", 1, 0)

    def test_the_invite_is_a_push_only_type(self):
        blob = handlers.session_invite("a", "a", "b", "1.1.1.1", "2.2.2.2", 1, 9)
        self.assertEqual(blob[:4], b"+ses")

    def test_the_peer_port_is_the_clients_hardcoded_one(self):
        self.assertEqual(handlers.PEER_PORT, 3658)


class BuddyStubTests(unittest.TestCase):
    def setUp(self):
        self.svc = buddy.BuddyService(verbose=False)

    def test_auth_is_answered_with_success(self):
        msg = protocol.decode(protocol.encode("AUTH", 0, {"USER": "alice"}))
        replies = [protocol.decode(b) for b in self.svc.respond(msg)]
        self.assertEqual(replies[0].type, "AUTH")
        self.assertTrue(replies[0].ok)

    def test_ping_is_echoed(self):
        msg = protocol.decode(protocol.encode("PING"))
        self.assertEqual(protocol.decode(self.svc.respond(msg)[0]).type, "PING")

    def test_roster_comes_back_empty_rather_than_failing(self):
        for verb in buddy.ROSTER_REQUESTS:
            msg = protocol.decode(protocol.encode(verb))
            reply = protocol.decode(self.svc.respond(msg)[0])
            self.assertTrue(reply.ok)
            self.assertEqual(reply.fields.get("LIST"), "")

    def test_an_unimplemented_verb_is_acknowledged_not_ignored(self):
        msg = protocol.decode(protocol.encode("PSET", 0, {"SHOW": "1"}))
        replies = self.svc.respond(msg)
        self.assertEqual(protocol.decode(replies[0]).type, "PSET")
        self.assertTrue(protocol.decode(replies[0]).ok)

    def test_disc_gets_no_reply(self):
        msg = protocol.decode(protocol.encode("DISC"))
        self.assertEqual(self.svc.respond(msg), [])

    def test_show_states_match_the_clients_table(self):
        self.assertEqual(buddy.SHOW_STATES[0], "DISC")
        self.assertEqual(buddy.SHOW_STATES[5], "PASS")


class TwoClientLobbyTests(unittest.TestCase):
    """Two real sockets: both join a room and each should learn of the other."""

    def setUp(self):
        self.store, self.path = make_store()
        self.store.seed_defaults()
        self.store.create_account("alice", PASS="")
        self.store.create_account("bob", PASS="")
        self.first, self.second = _free_port_pair()
        self.service = Service(self.store,
                               dict(CONFIG, advertise_port=str(self.second)),
                               Transcript(None), verbose=False)
        threading.Thread(target=self.service.serve_forever,
                         args=("127.0.0.1", [self.first, self.second]),
                         daemon=True).start()
        # Wait for EVERY port, not just the first. The service binds them in
        # order, so checking only `first` can succeed while `second` -- the one
        # the tests actually connect to -- is still closed. That raced about one
        # run in three.
        deadline = time.time() + 5
        for port in (self.first, self.second):
            while True:
                try:
                    socket.create_connection(("127.0.0.1", port), 1).close()
                    break
                except OSError:
                    if time.time() > deadline:
                        raise AssertionError(
                            "service never began listening on port %d" % port)
                    time.sleep(0.05)

    def tearDown(self):
        self.service.stop()
        self.store.close(); os.unlink(self.path)

    def _login(self, name):
        sock = socket.create_connection(("127.0.0.1", self.second), 5)
        sock.settimeout(3)
        for blob in (protocol.encode("skey", 0, {"SKEY": "$00"}),
                     protocol.encode("auth", 0, {"NAME": name, "PASS": ""})):
            sock.sendall(blob)
        self._drain(sock, 0.6)
        return sock

    def _drain(self, sock, seconds):
        got, buffer, deadline = [], b"", time.time() + seconds
        sock.settimeout(0.3)
        while time.time() < deadline:
            try:
                chunk = sock.recv(65535)
            except socket.timeout:
                continue
            if not chunk:
                break
            buffer += chunk
            msgs, buffer = protocol.split_stream(buffer)
            got.extend(msgs)
        return got

    def test_both_clients_end_up_in_the_room_and_see_each_other(self):
        a = self._login("alice")
        b = self._login("bob")
        try:
            a.sendall(protocol.encode("move", 0, {"NAME": "Open Lobby"}))
            self._drain(a, 0.6)
            b.sendall(protocol.encode("move", 0, {"NAME": "Open Lobby"}))
            b_got = self._drain(b, 0.8)
            a_got = self._drain(a, 0.8)

            self.assertTrue(any(m.type == "move" and m.ok for m in b_got))
            # bob's own move brings him the occupant list, alice included.
            users = [m for m in b_got if m.type == "+usr"]
            self.assertTrue(users, "bob received no user records")
            self.assertTrue(any(m.fields.get("N") == "alice" for m in users),
                            "bob was not told about alice")
            # alice is told, unsolicited, that someone arrived.
            self.assertTrue(any(m.type == "+usr" and m.fields.get("N") == "bob"
                                for m in a_got),
                            "alice was not told bob joined")
        finally:
            a.close(); b.close()

    def test_chat_reaches_the_other_occupant(self):
        a = self._login("alice")
        b = self._login("bob")
        try:
            for sock in (a, b):
                sock.sendall(protocol.encode("move", 0, {"NAME": "Ranked"}))
            self._drain(a, 0.5); self._drain(b, 0.5)
            a.sendall(protocol.encode("mesg", 0, {"BODY": "hello lobby"}))
            b_got = self._drain(b, 0.8)
            said = [m for m in b_got if m.type == "+msg"]
            self.assertTrue(said, "bob received no chat")
            self.assertEqual(said[0].fields["BODY"], "hello lobby")
        finally:
            a.close(); b.close()


class NewsTests(unittest.TestCase):
    """The news reply also carries the service configuration -- leaving it
    unanswered is what made a real client sit on "logging into server"."""

    def setUp(self):
        self.store, self.path = make_store()
        self.config = dict(CONFIG, buddy_host="192.168.68.85", buddy_port=10002)

    def tearDown(self):
        self.store.close(); os.unlink(self.path)

    def _news(self, name="0", **extra):
        cfg = dict(self.config, **extra)
        msg = protocol.decode(protocol.encode("news", 0, {"NAME": name}))
        blobs = handlers.dispatch(
            Context(msg, open_session(), self.store, cfg))
        return [protocol.decode(b) for b in blobs]

    def test_news_is_answered_at_all(self):
        replies = self._news()
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].type, "news")
        self.assertTrue(replies[0].ok)

    def test_it_carries_the_buddy_address(self):
        fields = self._news()[0].fields
        self.assertEqual(fields["BUDDY_URL"], "192.168.68.85")
        self.assertEqual(fields["BUDDY_PORT"], "10002")

    def test_the_requested_index_is_echoed(self):
        self.assertEqual(self._news("2")[0].fields["NAME"], "2")

    def test_the_roster_version_is_always_populated(self):
        """Sent even though the checksum is a placeholder, so the field exists
        and the path is exercised. The pnach zeroes the comparison, so a
        mismatch is currently inert."""
        fields = self._news()[0].fields
        self.assertEqual(fields["DATE"], handlers.PLACEHOLDER_ROSTER_DATE)
        self.assertEqual(fields["CSUM"], handlers.PLACEHOLDER_ROSTER_CSUM)

    def test_the_placeholder_is_labelled_as_one(self):
        """A future reader must not mistake it for a real checksum."""
        import inspect
        source = inspect.getsource(handlers)
        self.assertIn("PLACEHOLDER_ROSTER_CSUM", source)
        self.assertIn("not derived from any roster", source)

    def test_all_eight_fields_the_parser_reads_are_present(self):
        """The parser reads these unconditionally; leaving one out means
        falling back to a default nobody has reasoned about."""
        fields = self._news()[0].fields
        for key in ("BUDDY_URL", "BUDDY_PORT", "FPLY", "BGNR", "ELIT", "TWRP"):
            self.assertIn(key, fields, key)

    def test_twrp_matches_the_clients_own_default(self):
        # 240 at 0x0034f078 -- so sending it changes nothing unless we mean it.
        self.assertEqual(self._news()[0].fields["TWRP"], "240")
        self.assertEqual(handlers.TWRP_DEFAULT, 240)

    def test_the_roster_version_is_sent_when_configured(self):
        fields = self._news(roster_date="20040817", roster_csum="12345")[0].fields
        self.assertEqual(fields["DATE"], "20040817")
        self.assertEqual(fields["CSUM"], "12345")


class ChangeUserTests(unittest.TestCase):
    def setUp(self):
        self.store, self.path = make_store()

    def tearDown(self):
        self.store.close(); os.unlink(self.path)

    def test_cusr_is_acknowledged_and_remembered(self):
        session, replies = run(self.store, "cusr",
                               {"PERS": "Raythatruth", "SETFAV": "32"})
        self.assertTrue(replies[0].ok)
        self.assertEqual(replies[0].type, "cusr")
        self.assertEqual(session.favourite, "32")


class ObservedSessionTests(unittest.TestCase):
    """Replay the exact message sequence a real console produced, and assert
    every one of them now gets an answer. The two that did not -- cusr and
    news -- are why the client stalled."""

    SEQUENCE = [
        ("skey", {"SKEY": "$5075626c6963204b6579"}),
        ("acct", {"NAME": "Itruckedray", "TOS": "1", "PASS": "$2d41",
                  "MAIL": "Pa@sa.com", "ALTS": "4", "BORN": "19800319",
                  "GEND": "M"}),
        ("auth", {"NAME": "Itruckedray", "TOS": "1", "PASS": "$2d41",
                  "MID": "$00041f82cf72", "HWFLAG": "4", "HWMASK": "67876",
                  "PROD": "MADDEN-PS2-2004"}),
        ("cper", {"PERS": "Raythatruth", "ALTS": "4"}),
        ("pers", {"PERS": "Raythatruth"}),
        ("sele", {"ROOMS": "1"}),
        ("sele", {"RANKS": "50"}),
        ("cusr", {"PERS": "Raythatruth", "SETFAV": "32"}),
        ("news", {"NAME": "0"}),
        ("news", {"NAME": "2"}),
    ]

    def test_every_message_the_console_sent_now_gets_a_reply(self):
        store, path = make_store()
        session = Session("console:1")
        cfg = dict(CONFIG, buddy_host="192.168.68.85", buddy_port=10002)
        try:
            unanswered = []
            for msg_type, fields in self.SEQUENCE:
                msg = protocol.decode(protocol.encode(msg_type, 0, fields))
                replies = handlers.dispatch(Context(msg, session, store, cfg))
                if not replies:
                    unanswered.append(msg_type)
            self.assertEqual(unanswered, [], "still unanswered: %s" % unanswered)
        finally:
            store.close(); os.unlink(path)

    def test_the_ping_echo_is_still_answered_with_silence(self):
        """The console's ~png echoes carry a non-zero status (a latency
        figure, not an error). They must not draw a reply either way."""
        store, path = make_store()
        try:
            msg = protocol.decode(protocol.encode("~png", 0x11, {}))
            self.assertFalse(msg.ok)      # non-zero status
            replies = handlers.dispatch(
                Context(msg, open_session(), store, CONFIG))
            self.assertEqual(replies, [])
        finally:
            store.close(); os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)

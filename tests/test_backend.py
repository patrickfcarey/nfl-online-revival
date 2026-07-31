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

from backend import handlers, protocol  # noqa: E402
from backend.handlers import Context, Session  # noqa: E402
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

    def test_ping_is_echoed(self):
        _s, replies = run(self.store, "~png", {})
        self.assertEqual(replies[0].type, "~png")
        self.assertTrue(replies[0].ok)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)

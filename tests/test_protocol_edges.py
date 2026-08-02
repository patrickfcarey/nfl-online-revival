"""Framing and escaping edge cases the happy path never reaches.

`backend/protocol.py` is the one module every message passes through twice, and
its failure mode is not an exception -- it is a message the console accepts and
reads differently from what we meant. Three of the things pinned here have
already cost this project a session:

* the status word is a **4-character tag**, not a number, and 0 means success.
  Reading it as an integer made every failure look like a success.
* every value the client copies goes through a percent-decoder (0x0044c9b0),
  so a raw ``%`` in a persona or a chat line either eats the next two
  characters or ends the copy early.
* the encoder is the last place a newline or NUL can be caught before it
  *injects* a field rather than merely corrupting one.
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import protocol  # noqa: E402


class StatusWord(unittest.TestCase):
    """A 4-character ASCII tag. 0 is success; `auth` ends the session."""

    def test_zero_is_success(self):
        self.assertEqual(protocol.encode_status(0), protocol.OK)
        self.assertEqual(protocol.decode_status(protocol.OK), "")

    def test_an_empty_tag_means_success(self):
        self.assertEqual(protocol.encode_status(""), protocol.OK)

    def test_a_tag_round_trips(self):
        for tag in ("miss", "dupl", "new0", "auth", "tosa"):
            self.assertEqual(protocol.decode_status(
                protocol.encode_status(tag)), tag)

    def test_a_tag_of_the_wrong_length_is_refused(self):
        for bad in ("no", "toolong", "abcde"):
            with self.assertRaises(protocol.ProtocolError):
                protocol.encode_status(bad)

    def test_an_integer_status_is_passed_through(self):
        self.assertEqual(protocol.encode_status(3), 3)

    def test_an_integer_too_wide_for_the_field_is_refused(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.encode_status(1 << 33)

    def test_an_unprintable_status_renders_as_hex(self):
        # Better than a mojibake tag: it is visibly not a 4-character code.
        self.assertEqual(protocol.decode_status(0x00010203), "0x00010203")

    def test_a_printable_status_renders_as_its_tag(self):
        value = struct.unpack(">I", b"miss")[0]
        self.assertEqual(protocol.decode_status(value), "miss")


class PercentEscaping(unittest.TestCase):
    """The client percent-decodes every value it copies."""

    def test_a_literal_percent_is_doubled(self):
        self.assertEqual(protocol.percent_escape("100%"), "100%%")

    def test_escape_then_unescape_is_the_identity(self):
        for text in ("100%", "%%", "%41", "a%b%c", "%", "no percent here"):
            self.assertEqual(
                protocol.percent_unescape(protocol.percent_escape(text)), text)

    def test_a_doubled_percent_decodes_to_one(self):
        self.assertEqual(protocol.percent_unescape("50%%"), "50%")

    def test_a_hex_escape_decodes(self):
        self.assertEqual(protocol.percent_unescape("%41%42"), "AB")

    def test_a_malformed_escape_keeps_the_percent_rather_than_dropping_data(self):
        # Dropping it would silently shorten a name the client sent.
        self.assertEqual(protocol.percent_unescape("%zz"), "%zz")
        self.assertEqual(protocol.percent_unescape("100%"), "100%")
        self.assertEqual(protocol.percent_unescape("%"), "%")

    def test_a_percent_survives_a_round_trip_through_a_message(self):
        blob = protocol.encode("pers", protocol.OK, {"PERS": "5%off"})
        self.assertEqual(protocol.decode(blob).fields["PERS"], "5%off")


class Injection(unittest.TestCase):
    """The encoder is the last place framing can be forged."""

    def test_a_newline_in_a_value_is_refused(self):
        # `eve\nADMIN=1` would arrive at the client as two fields.
        with self.assertRaises(protocol.ProtocolError):
            protocol.encode("pers", protocol.OK, {"PERS": "eve\nADMIN=1"})

    def test_a_nul_in_a_value_is_refused(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.encode("pers", protocol.OK, {"PERS": "eve\x00"})

    def test_a_newline_in_a_key_is_refused(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.encode("pers", protocol.OK, {"BAD\nKEY": "x"})

    def test_an_equals_in_a_key_is_refused(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.encode("pers", protocol.OK, {"A=B": "x"})

    def test_a_message_type_must_be_four_characters(self):
        for bad in ("abc", "abcde", ""):
            with self.assertRaises(protocol.ProtocolError):
                protocol.encode(bad, protocol.OK, {})


class Decoding(unittest.TestCase):
    def test_a_short_message_is_refused(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode(b"abc")

    def test_a_length_below_the_header_is_refused(self):
        blob = b"test" + struct.pack(">II", 0, 4)
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode(blob)

    def test_a_length_beyond_the_bytes_present_is_refused(self):
        blob = b"test" + struct.pack(">II", 0, 999)
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode(blob)

    def test_a_length_over_the_client_buffer_is_refused(self):
        blob = b"test" + struct.pack(">II", 0, protocol.MAX_MESSAGE_SIZE + 1)
        blob += b"\x00" * (protocol.MAX_MESSAGE_SIZE + 1)
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode(blob)

    def test_a_quoted_value_has_its_quotes_stripped(self):
        raw = protocol.encode_raw("test", protocol.OK, b'NAME="Open Lobby"\n\x00')
        self.assertEqual(protocol.decode(raw).fields["NAME"], "Open Lobby")

    def test_a_line_without_an_equals_is_skipped(self):
        raw = protocol.encode_raw("test", protocol.OK, b"junk\nA=1\n\x00")
        self.assertEqual(protocol.decode(raw).fields, {"A": "1"})


class RawBodies(unittest.TestCase):
    """`encode_raw` exists for list replies, whose body is not key=value."""

    def test_the_body_is_passed_through_verbatim(self):
        body = b"URL=http://x\tCRC=1\n\x00"
        blob = protocol.encode_raw("news", "new2", body)
        self.assertTrue(blob.endswith(body))
        self.assertEqual(protocol.decode(blob).status_tag, "new2")

    def test_the_length_is_computed_not_supplied(self):
        blob = protocol.encode_raw("news", protocol.OK, b"abc")
        self.assertEqual(struct.unpack_from(">I", blob, 8)[0], len(blob))

    def test_a_type_of_the_wrong_length_is_refused(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.encode_raw("nope!", protocol.OK, b"")

    def test_a_body_over_the_client_buffer_is_refused(self):
        with self.assertRaises(protocol.ProtocolError) as caught:
            protocol.encode_raw("news", protocol.OK,
                                b"x" * protocol.MAX_MESSAGE_SIZE)
        self.assertIn("client's buffer", str(caught.exception))


class Describing(unittest.TestCase):
    """The log line a human reads when something has gone wrong."""

    def test_a_failure_shows_its_status_tag(self):
        text = protocol.decode(
            protocol.encode("auth", "miss", {})).describe()
        self.assertIn("miss", text)

    def test_a_success_does_not_mention_a_status(self):
        text = protocol.decode(protocol.encode("auth", protocol.OK, {})).describe()
        self.assertNotIn("status", text)

    def test_fields_are_aligned(self):
        text = protocol.decode(
            protocol.encode("news", protocol.OK,
                            {"A": "1", "LONGER": "2"})).describe()
        self.assertIn("A", text)
        self.assertIn("LONGER", text)

    def test_a_non_key_value_payload_is_shown_as_hex(self):
        # A list reply, or a body we failed to parse -- the bytes are the only
        # useful thing to print.
        blob = protocol.encode_raw("news", protocol.OK, b"\x01\x02\x03")
        self.assertIn("010203", protocol.decode(blob).describe())


class Tokens(unittest.TestCase):
    def test_a_packed_list_splits(self):
        self.assertEqual(list(protocol.iter_tokens("a,b,c")), ["a", "b", "c"])

    def test_whitespace_and_empties_are_dropped(self):
        self.assertEqual(list(protocol.iter_tokens(" a , , b ")), ["a", "b"])

    def test_an_empty_value_yields_nothing(self):
        self.assertEqual(list(protocol.iter_tokens("")), [])

    def test_the_separator_can_be_changed(self):
        self.assertEqual(list(protocol.iter_tokens("a b", separator=" ")),
                         ["a", "b"])


if __name__ == "__main__":
    unittest.main()

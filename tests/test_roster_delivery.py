"""The `news` reply tag, and serving a roster over HTTP.

The tag is the whole reason these tests exist. We answered `news` with type
`news` for a long time and every reply was silently discarded, because the
client builds the tag it will accept as ``'new0' + NAME`` and keeps the body
only via a ``movz`` -- a mismatch yields NULL, not an error. Nothing on the
server side looks wrong when that happens, so only a test can hold the line.
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import handlers, protocol, rosterfile  # noqa: E402
from backend.handlers import Context  # noqa: E402
from backend.service import Session  # noqa: E402
from backend.store import Store  # noqa: E402


def open_session():
    session = Session("test:1")
    session.state = "idle"
    return session


def make_store():
    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    os.unlink(path)
    store = Store(path)
    store.seed_defaults()
    return store, path


CONFIG = {
    "advertise_host": "192.168.1.10",
    "advertise_port": "10001",
    "mask": "GS",
    "buddy_host": "192.168.1.10",
    "buddy_port": 10002,
}


class NewsReplyTag(unittest.TestCase):
    """`news NAME=n` is answered with type `news` and STATUS `new<n>`.

    The category rides in the status word, not the type -- msg+8 is the status
    field, and the client keeps the body only when it equals 'new0' + n
    (0x0034f500) and non-zero (0x0034eb20). An earlier version of this docstring
    said "type new<n>", which is what the code was doing when nothing worked.
    """

    def setUp(self):
        self.store, self.path = make_store()
        self.config = dict(CONFIG)

    def tearDown(self):
        self.store.close()
        os.unlink(self.path)

    def _dispatch(self, fields):
        msg = protocol.decode(protocol.encode("news", 0, fields))
        replies = handlers.dispatch(
            Context(msg, open_session(), self.store, self.config))
        return [protocol.decode(b) for b in replies]

    def _news(self, name):
        return self._dispatch({"NAME": str(name)})

    def test_config_request_is_answered_new0(self):
        replies = self._news(0)
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].type, "news")
        self.assertEqual(replies[0].status_tag, "new0")

    def test_headlines_request_is_answered_new1(self):
        self.assertEqual(self._news(1)[0].status_tag, "new1")

    def test_roster_request_is_answered_new2(self):
        self.assertEqual(self._news(2)[0].status_tag, "new2")

    def test_the_type_is_news_and_the_category_is_the_status(self):
        # The exact bug this file exists for: tagging the message new<n> fails
        # the client's type match and the reply is never delivered at all.
        for name in (0, 1, 2):
            self.assertEqual(self._news(name)[0].type, "news")
            self.assertEqual(self._news(name)[0].status_tag,
                             "new%d" % name)

    def test_missing_name_is_treated_as_the_config_request(self):
        self.assertEqual(self._dispatch({})[0].status_tag, "new0")

    def test_a_non_numeric_name_does_not_crash(self):
        self.assertEqual(self._dispatch({"NAME": "x"})[0].status_tag, "new0")

    def test_config_reply_carries_the_buddy_address_and_roster_version(self):
        reply = self._news(0)[0]
        self.assertEqual(reply.fields["BUDDY_URL"], "192.168.1.10")
        self.assertEqual(reply.fields["BUDDY_PORT"], "10002")
        self.assertIn("DATE", reply.fields)
        self.assertIn("CSUM", reply.fields)

    def test_reply_type_helper_rejects_an_unnameable_category(self):
        ctx = Context(protocol.decode(protocol.encode("news", 0, {})),
                      open_session(), self.store, self.config)
        with self.assertRaises(protocol.ProtocolError):
            handlers.news_status(10)

    def test_the_legacy_type_can_be_selected_for_diagnosis(self):
        # The console is not storing the CSUM we announce, and whether the
        # reply must be tagged new<n> or echo `news` is the open question.
        for name in (0, 1, 2):
            self.assertEqual(self._news(name)[0].type, "news")


class RosterManifest(unittest.TestCase):
    """`new2` carries URL and CRC, or nothing at all."""

    def setUp(self):
        self.store, self.path = make_store()

    def tearDown(self):
        self.store.close()
        os.unlink(self.path)

    @staticmethod
    def _records(reply):
        """Split a list reply into records: one per LINE, fields by TAB.

        Deliberately not protocol.parse_fields, which is for ordinary replies
        and would read a whole record line as a single field. The two layouts
        are genuinely different, and conflating them is what produced three
        roster records out of one entry.
        """
        text = reply.raw_payload.split(b"\x00", 1)[0].decode("latin-1")
        out = []
        for line in text.split("\n"):
            if not line.strip():
                continue
            fields = {}
            for part in line.split("\t"):
                if "=" in part:
                    key, value = part.split("=", 1)
                    fields[key] = value
            out.append(fields)
        return out

    def _manifest(self, **extra):
        config = dict(CONFIG); config.update(extra)
        msg = protocol.decode(protocol.encode("news", 0, {"NAME": "2"}))
        replies = handlers.dispatch(
            Context(msg, open_session(), self.store, config))
        return protocol.decode(replies[0])

    def test_empty_when_no_roster_is_configured(self):
        reply = self._manifest()
        self.assertEqual(reply.status_tag, "new2")
        self.assertEqual(self._records(reply), [])

    def test_carries_url_and_crc_when_configured(self):
        reply = self._manifest(roster_url="http://10.0.0.1:10080/roster.dat",
                               roster_file_crc=12345)
        records = self._records(reply)
        self.assertEqual(len(records), 1, "one entry must be ONE record")
        self.assertEqual(records[0]["URL"], "http://10.0.0.1:10080/roster.dat")
        self.assertEqual(records[0]["CRC"], "12345")

    def test_one_entry_is_one_line(self):
        """The bug this format exists for.

        Encoding the three fields one-per-line made the console see three
        records -- measured live at gp+17660 -- the first of which had no URL.
        It selected that one and reported a failed download.
        """
        reply = self._manifest(roster_url="http://10.0.0.1:10080/roster.dat",
                               roster_file_crc=12345)
        body = reply.raw_payload.split(b"\x00", 1)[0]
        self.assertEqual(body.count(b"\n"), 1)

    def test_fields_are_tab_separated_not_space(self):
        """Proven on hardware: with a space the console requested
        `GET /roster.dat CRC=... NAME=... HTTP/1.0` -- the URL had swallowed the
        rest of the line, because its value copy only stops below byte 32."""
        reply = self._manifest(roster_url="http://h/r.dat", roster_file_crc=1)
        body = reply.raw_payload.split(b"\x00", 1)[0]
        self.assertIn(b"\t", body)
        self.assertNotIn(b"dat ", body)      # no space after the URL

    def test_a_control_character_in_a_value_is_refused(self):
        with self.assertRaises(protocol.ProtocolError):
            handlers._news_list(None, 2, [{"NAME": "two\tparts"}])

    def test_a_url_without_a_crc_is_not_advertised(self):
        # A record with a URL the client cannot verify is worse than no record:
        # it downloads, fails the check, and reports a corrupt transfer.
        reply = self._manifest(roster_url="http://10.0.0.1:10080/roster.dat")
        self.assertEqual(self._records(reply), [])

    def test_crc_is_decimal_not_hex(self):
        reply = self._manifest(roster_url="http://x/y", roster_file_crc=0xDEADBEEF)
        self.assertEqual(self._records(reply)[0]["CRC"], str(0xDEADBEEF))


class PayloadChecksum(unittest.TestCase):
    """The download CRC seeds from 0 -- unlike the roster CSUM."""

    def test_crc_is_plain_zlib_over_the_bytes(self):
        handle, path = tempfile.mkstemp()
        os.write(handle, b"roster bytes")
        os.close(handle)
        try:
            payload, crc = rosterfile.load(path)
            self.assertEqual(payload, b"roster bytes")
            self.assertEqual(crc, zlib.crc32(b"roster bytes") & 0xFFFFFFFF)
        finally:
            os.unlink(path)

    def test_seed_is_zero_not_the_length(self):
        # Guards against reusing the CSUM algorithm here, which seeds from the
        # row count. Same primitive, different seed, silently wrong.
        data = b"x" * 64
        self.assertNotEqual(zlib.crc32(data, 0), zlib.crc32(data, len(data)))
        handle, path = tempfile.mkstemp()
        os.write(handle, data)
        os.close(handle)
        try:
            _payload, crc = rosterfile.load(path)
            self.assertEqual(crc, zlib.crc32(data, 0) & 0xFFFFFFFF)
        finally:
            os.unlink(path)

    def test_an_empty_payload_is_refused(self):
        handle, path = tempfile.mkstemp()
        os.close(handle)
        try:
            with self.assertRaises(rosterfile.RosterFileError):
                rosterfile.load(path)
        finally:
            os.unlink(path)

    def test_a_missing_payload_is_refused(self):
        with self.assertRaises(rosterfile.RosterFileError):
            rosterfile.load("/nonexistent/roster.dat")


class HttpDelivery(unittest.TestCase):
    """A real socket: the console fetches this with an ordinary GET."""

    def setUp(self):
        self.payload = bytes(range(256)) * 8
        self.server = rosterfile.RosterServer(self.payload)
        self.port = self.server.start("127.0.0.1", 0)

    def tearDown(self):
        self.server.stop()

    def test_serves_the_payload_byte_for_byte(self):
        url = "http://127.0.0.1:%d%s" % (self.port, rosterfile.ROSTER_PATH)
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read()
        self.assertEqual(body, self.payload)

    def test_sends_a_content_length(self):
        # The client reads the body length from the header rather than to EOF.
        url = "http://127.0.0.1:%d%s" % (self.port, rosterfile.ROSTER_PATH)
        with urllib.request.urlopen(url, timeout=5) as response:
            self.assertEqual(int(response.headers["Content-Length"]),
                             len(self.payload))

    def test_served_bytes_match_the_advertised_crc(self):
        url = "http://127.0.0.1:%d%s" % (self.port, rosterfile.ROSTER_PATH)
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read()
        self.assertEqual(zlib.crc32(body) & 0xFFFFFFFF, self.server.crc)

    def test_any_path_serves_the_roster(self):
        # A manifest typo should not look like a network failure on hardware.
        url = "http://127.0.0.1:%d/anything" % self.port
        with urllib.request.urlopen(url, timeout=5) as response:
            self.assertEqual(response.read(), self.payload)


class ConcurrentDelivery(unittest.TestCase):
    """Two consoles joining together must not serialise.

    This server was a plain ``HTTPServer`` until 2026-08-01, which handles one
    request at a time. Both tests here failed then, and both mattered on
    hardware rather than only under load: the install path wipes the league
    database before it validates (0x004c9ee8), so a console left waiting behind
    someone else's transfer sits on an empty database until it is rebooted.
    """

    def setUp(self):
        self.payload = bytes(range(256)) * 8
        self.server = rosterfile.RosterServer(self.payload)
        self.port = self.server.start("127.0.0.1", 0)
        self.url = "http://127.0.0.1:%d%s" % (self.port, rosterfile.ROSTER_PATH)

    def tearDown(self):
        self.server.stop()

    def test_a_silent_peer_does_not_block_a_download(self):
        # Connect and never send a request line -- the cheapest possible
        # denial of service against a single-threaded server.
        stalled = socket.create_connection(("127.0.0.1", self.port))
        self.addCleanup(stalled.close)
        with urllib.request.urlopen(self.url, timeout=5) as response:
            self.assertEqual(response.read(), self.payload)

    def test_downloads_actually_overlap(self):
        # Assert overlap in time, not merely that all of them finished: a
        # serialising server also returns every byte correctly, just one at a
        # time, so correctness alone cannot tell the two apart.
        spans, lock = [], threading.Lock()

        def fetch():
            start = time.time()
            with urllib.request.urlopen(self.url, timeout=10) as response:
                body = response.read()
            with lock:
                spans.append((start, time.time(), len(body)))

        threads = [threading.Thread(target=fetch) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertEqual(len(spans), 4, "a download did not finish")
        for _start, _end, length in spans:
            self.assertEqual(length, len(self.payload))
        overlapping = sum(1 for a in spans for b in spans
                          if a is not b and a[0] < b[1] and b[0] < a[1])
        self.assertGreater(overlapping, 0,
                           "no two downloads overlapped; the server is "
                           "handling requests one at a time")


class ManifestUrl(unittest.TestCase):
    def test_is_absolute_and_numeric(self):
        url = rosterfile.manifest_url("192.168.68.85", 10080)
        self.assertTrue(url.startswith("http://192.168.68.85:10080/"))

    def test_uses_the_declared_path(self):
        self.assertTrue(
            rosterfile.manifest_url("10.0.0.1", 80).endswith(
                rosterfile.ROSTER_PATH))


if __name__ == "__main__":
    unittest.main()

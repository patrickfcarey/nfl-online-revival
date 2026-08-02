"""The reconnaissance tooling: the sinkhole, the TLS sink, and the replay server.

These are the instruments that produced the findings the rest of the project is
built on, which gives their bugs an unusual shape. A sinkhole that mis-parses a
ClientHello does not fail -- it reports a different client, and that report goes
into a document as a fact. The SSLv2 path is the sharp example: a 2004 console
opens with the *old* record framing even when it means to speak TLS 1.0, modern
OpenSSL will not parse it at all, and reading it wrong makes the console look
like it offered ciphers it never mentioned.

So the parsers get the attention here, and the servers get enough to show they
bind, log, and hang up cleanly.
"""

from __future__ import annotations

import io
import json
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

from recon import easerver, eaproto, sinkd, tlssink  # noqa: E402


# ---------------------------------------------------------------------------
# building handshake bytes
# ---------------------------------------------------------------------------

def client_hello(version=0x0301, ciphers=(0x0004, 0x0005), sni=None,
                 record_version=0x0301, handshake_type=0x01):
    body = struct.pack(">H", version) + b"\x00" * 32     # version + random
    body += b"\x00"                                      # no session id
    body += struct.pack(">H", len(ciphers) * 2)
    body += b"".join(struct.pack(">H", c) for c in ciphers)
    body += b"\x01\x00"                                  # one compression: null
    if sni is not None:
        name = sni.encode()
        entry = b"\x00" + struct.pack(">H", len(name)) + name
        ext = struct.pack(">H", len(entry)) + entry
        extension = struct.pack(">HH", 0x0000, len(ext)) + ext
        body += struct.pack(">H", len(extension)) + extension
    handshake = bytes([handshake_type]) + struct.pack(">I", len(body))[1:] + body
    return (b"\x16" + struct.pack(">H", record_version)
            + struct.pack(">H", len(handshake)) + handshake)


def sslv2_hello(version=0x0300, specs=((0x00, 0x00, 0x04),), msg_type=0x01):
    payload = bytes([msg_type]) + struct.pack(">H", version)
    payload += struct.pack(">HHH", len(specs) * 3, 0, 16)
    for spec in specs:
        payload += bytes(spec)
    payload += b"\x00" * 16                              # challenge
    length = len(payload)
    return struct.pack(">H", 0x8000 | length) + payload


class ClientHelloParsing(unittest.TestCase):
    def test_a_modern_hello_yields_version_and_ciphers(self):
        info = tlssink.parse_client_hello(client_hello())
        self.assertTrue(info["is_tls"])
        self.assertIsNone(info["error"])
        self.assertEqual(info["ciphers"], ["0x0004", "0x0005"])

    def test_sni_is_extracted(self):
        info = tlssink.parse_client_hello(client_hello(sni="ps2madden04.ea.com"))
        self.assertEqual(info["sni"], "ps2madden04.ea.com")

    def test_no_extensions_is_normal_for_an_old_client(self):
        # A 2004 console sends none; treating that as malformed would discard
        # the cipher list, which is the part worth having.
        info = tlssink.parse_client_hello(client_hello(sni=None))
        self.assertIsNone(info["error"])
        self.assertIsNone(info["sni"])
        self.assertTrue(info["ciphers"])

    def test_something_that_is_not_a_handshake_is_reported(self):
        info = tlssink.parse_client_hello(b"GET / HTTP/1.0\r\n\r\n")
        self.assertFalse(info["is_tls"])
        self.assertIn("not a TLS handshake", info["error"])

    def test_a_handshake_that_is_not_a_client_hello_is_reported(self):
        info = tlssink.parse_client_hello(client_hello(handshake_type=0x02))
        self.assertIn("not ClientHello", info["error"])

    def test_truncated_bytes_do_not_raise(self):
        # The sink must survive whatever arrives; an exception here would take
        # down the listener and lose the capture.
        full = client_hello(sni="x.example")
        for cut in range(1, len(full)):
            info = tlssink.parse_client_hello(full[:cut])
            self.assertIsInstance(info, dict)

    def test_empty_input_is_handled(self):
        info = tlssink.parse_client_hello(b"")
        self.assertIn("not a TLS handshake", info["error"])


class SslV2Parsing(unittest.TestCase):
    """The legacy framing a 2004 console actually opens with."""

    def test_it_is_recognised_from_the_high_bit(self):
        info = tlssink.parse_client_hello(sslv2_hello())
        self.assertTrue(info["is_sslv2_hello"])
        self.assertIsNone(info["error"])

    def test_the_version_inside_says_what_it_means_to_speak(self):
        # The old framing does not mean SSL 2.0; the version field decides.
        info = tlssink.parse_sslv2_hello(sslv2_hello(version=0x0301))
        self.assertIn("1.0", str(info["version"]))

    def test_cipher_specs_are_three_bytes_not_two(self):
        """Reading them as TLS-sized pairs shifts every subsequent spec.

        The console then appears to have offered ciphers it never mentioned.
        """
        info = tlssink.parse_sslv2_hello(
            sslv2_hello(specs=((0x00, 0x00, 0x04), (0x00, 0x00, 0x05))))
        self.assertEqual(info["ciphers"], ["0x0004", "0x0005"])

    def test_a_leading_zero_marks_an_ordinary_tls_id(self):
        info = tlssink.parse_sslv2_hello(sslv2_hello(specs=((0x01, 0x00, 0x80),)))
        self.assertEqual(info["ciphers"], ["SSL2_0x010080"])

    def test_a_non_hello_message_type_is_reported(self):
        info = tlssink.parse_sslv2_hello(sslv2_hello(msg_type=0x04))
        self.assertIn("not CLIENT-HELLO", info["error"])

    def test_a_record_without_the_high_bit_is_refused(self):
        info = tlssink.parse_sslv2_hello(b"\x00" * 16)
        self.assertFalse(info["is_sslv2_hello"])
        self.assertIn("not an SSLv2 record", info["error"])

    def test_a_short_record_does_not_raise(self):
        for cut in range(1, 12):
            self.assertIsInstance(
                tlssink.parse_sslv2_hello(sslv2_hello()[:cut]), dict)


class Describing(unittest.TestCase):
    def test_a_modern_hello_is_summarised(self):
        text = tlssink.describe_client_hello(
            tlssink.parse_client_hello(client_hello(sni="a.example")))
        self.assertIn("a.example", text)

    def test_an_sslv2_hello_says_so_loudly(self):
        text = tlssink.describe_client_hello(
            tlssink.parse_client_hello(sslv2_hello()))
        self.assertIn("SSLv2", text)

    def test_a_failure_is_summarised_rather_than_hidden(self):
        text = tlssink.describe_client_hello(
            tlssink.parse_client_hello(b"nonsense"))
        self.assertTrue(text.strip())

    def test_a_summary_of_several_results(self):
        results = [{"port": 443, "sni": "a.example", "peer": "10.0.0.1",
                    "ciphers": ["0x0004"], "version": "TLS 1.0"}]
        self.assertIsInstance(tlssink.format_summary(results), str)

    def test_an_empty_summary_does_not_raise(self):
        self.assertIsInstance(tlssink.format_summary([]), str)


class Hexdump(unittest.TestCase):
    def test_offsets_hex_and_ascii(self):
        text = sinkd.hexdump(b"AB\x00\xff")
        self.assertIn("0000", text)
        self.assertIn("41 42 00 ff", text)
        self.assertIn("AB..", text)

    def test_it_wraps_at_the_requested_width(self):
        self.assertEqual(len(sinkd.hexdump(b"x" * 33, width=16).splitlines()), 3)

    def test_empty_input_yields_nothing(self):
        self.assertEqual(sinkd.hexdump(b""), "")

    def test_unprintable_bytes_become_dots(self):
        self.assertTrue(sinkd.hexdump(bytes(range(32))).rstrip().endswith("."))


class Binding(unittest.TestCase):
    def test_a_free_tcp_port_binds(self):
        sock = sinkd._bind_tcp("127.0.0.1", 0)
        self.assertIsNotNone(sock)
        sock.close()

    def test_a_privileged_port_fails_softly(self):
        """Returning None rather than raising is deliberate.

        The sink binds a long list of ports and must keep the ones it got, or
        a single permission error would lose the whole capture.
        """
        buffer = io.StringIO()
        original = sys.stdout
        sys.stdout = buffer
        try:
            result = sinkd._bind_tcp("127.0.0.1", 1)
        finally:
            sys.stdout = original
        if result is None:
            self.assertIn("bind FAILED", buffer.getvalue())
        else:
            result.close()          # running as root; nothing to assert

    def test_a_free_udp_port_binds(self):
        sock = sinkd._bind_udp("127.0.0.1", 0)
        self.assertIsNotNone(sock)
        sock.close()

    def test_a_taken_tcp_port_fails_softly(self):
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        buffer, original = io.StringIO(), sys.stdout
        sys.stdout = buffer
        try:
            # SO_REUSEADDR does not permit a second listener on the same port.
            second = sinkd._bind_tcp("127.0.0.1", port)
        finally:
            sys.stdout = original
            held.close()
        if second is not None:
            second.close()


class ReplyTable(unittest.TestCase):
    """The captured-reply table the replay server answers from."""

    def _file(self, payload):
        handle, path = tempfile.mkstemp(suffix=".json")
        os.write(handle, json.dumps(payload).encode())
        os.close(handle)
        self.addCleanup(os.unlink, path)
        return path

    def test_no_path_yields_an_empty_table(self):
        self.assertEqual(easerver.load_replies(None), {})

    def test_a_single_object_becomes_one_reply(self):
        path = self._file({"auth": {"NAME": "x"}})
        table = easerver.load_replies(path)
        self.assertEqual(table["auth"], [("auth", {"NAME": "x"})])

    def test_a_list_allows_a_follow_up_push(self):
        # The server both answers and volunteers; a client can be waiting on
        # the second message rather than the first.
        path = self._file({"quik": [{"type": "quik", "fields": {}},
                                    {"type": "+ses", "fields": {"SELF": "a"}}]})
        table = easerver.load_replies(path)
        self.assertEqual([t for t, _f in table["quik"]], ["quik", "+ses"])

    def test_field_values_are_coerced_to_strings(self):
        path = self._file({"news": {"CSUM": 12345}})
        self.assertEqual(easerver.load_replies(path)["news"][0][1]["CSUM"],
                         "12345")

    def test_a_key_that_is_not_four_characters_is_refused(self):
        path = self._file({"toolong": {}})
        with self.assertRaises(easerver.EaServerError):
            easerver.load_replies(path)

    def test_a_reply_type_that_is_not_four_characters_is_refused(self):
        path = self._file({"auth": [{"type": "nope!", "fields": {}}]})
        with self.assertRaises(easerver.EaServerError):
            easerver.load_replies(path)

    def test_a_non_object_reply_is_refused(self):
        path = self._file({"auth": ["not an object"]})
        with self.assertRaises(easerver.EaServerError):
            easerver.load_replies(path)

    def test_non_object_fields_are_refused(self):
        path = self._file({"auth": [{"type": "auth", "fields": "no"}]})
        with self.assertRaises(easerver.EaServerError):
            easerver.load_replies(path)

    def test_a_scalar_value_is_refused(self):
        path = self._file({"auth": 5})
        with self.assertRaises(easerver.EaServerError):
            easerver.load_replies(path)

    def test_a_top_level_array_is_refused(self):
        path = self._file([1, 2, 3])
        with self.assertRaises(easerver.EaServerError):
            easerver.load_replies(path)

    def test_invalid_json_is_reported_as_such(self):
        handle, path = tempfile.mkstemp(suffix=".json")
        os.write(handle, b"{not json")
        os.close(handle)
        self.addCleanup(os.unlink, path)
        with self.assertRaises(easerver.EaServerError) as caught:
            easerver.load_replies(path)
        self.assertIn("not valid JSON", str(caught.exception))

    def test_a_missing_file_is_reported(self):
        with self.assertRaises(easerver.EaServerError) as caught:
            easerver.load_replies("/nonexistent/replies.json")
        self.assertIn("cannot read", str(caught.exception))


class RepliesFor(unittest.TestCase):
    @staticmethod
    def _message(msg_type, **fields):
        return eaproto.decode(eaproto.encode(msg_type, 0, fields))

    def test_a_configured_type_is_answered(self):
        table = {"auth": [("auth", {"OK": "1"})]}
        out = easerver._replies_for(self._message("auth"), table,
                                    "10.0.0.1", 10001)
        self.assertEqual(len(out), 1)
        self.assertEqual(eaproto.decode(out[0]).type, "auth")

    def test_dir_is_answered_even_with_no_table(self):
        # The redirect is the one thing the replay server always has to do.
        out = easerver._replies_for(self._message("@dir"), {}, "10.0.0.1", 10001)
        self.assertEqual(len(out), 1)
        self.assertEqual(eaproto.decode(out[0]).type, "@dir")

    def test_an_unknown_type_produces_silence(self):
        self.assertEqual(
            easerver._replies_for(self._message("zzzz"), {}, "10.0.0.1", 10001),
            [])

    def test_a_configured_dir_overrides_the_built_in(self):
        table = {"@dir": [("@dir", {"ADDR": "1.2.3.4", "PORT": "999"})]}
        out = easerver._replies_for(self._message("@dir"), table,
                                    "10.0.0.1", 10001)
        self.assertEqual(eaproto.decode(out[0]).fields["PORT"], "999")

    def test_every_reply_echoes_the_transaction(self):
        # The client matches on it, so a fresh value would be discarded.
        message = self._message("auth")
        table = {"auth": [("auth", {})]}
        out = easerver._replies_for(message, table, "10.0.0.1", 10001)
        self.assertEqual(eaproto.decode(out[0]).txn, message.txn)


class SinkholeConnection(unittest.TestCase):
    """One connection through the TCP sinkhole, end to end."""

    def test_it_logs_and_hangs_up(self):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        self.addCleanup(os.unlink, path)
        # _Transcript takes an open handle, not a path -- it is handed the
        # already-open file so every listener shares one descriptor.
        handle = open(path, "w", encoding="utf-8")
        self.addCleanup(handle.close)
        transcript = sinkd._Transcript(handle)

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]

        def accept():
            conn, addr = listener.accept()
            sinkd._serve_tcp_conn(conn, addr, port, transcript, None)

        threading.Thread(target=accept, daemon=True).start()
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.addCleanup(client.close)
        client.sendall(b"hello sinkhole")
        client.settimeout(5)
        deadline = time.time() + 5
        while time.time() < deadline:
            if os.path.getsize(path):
                break
            time.sleep(0.05)
        with open(path, encoding="utf-8") as readback:
            rows = [json.loads(line) for line in readback if line.strip()]
        self.assertTrue(rows)
        self.assertTrue(any("hello sinkhole" in json.dumps(r) or
                            "68656c6c6f" in json.dumps(r) for r in rows))

    def test_a_greeting_is_sent_before_anything_is_read(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]
        transcript = sinkd._Transcript(None)

        def accept():
            conn, addr = listener.accept()
            sinkd._serve_tcp_conn(conn, addr, port, transcript, None,
                                  greet=b"220 hello\r\n")

        threading.Thread(target=accept, daemon=True).start()
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.addCleanup(client.close)
        client.settimeout(5)
        self.assertEqual(client.recv(32), b"220 hello\r\n")


if __name__ == "__main__":
    unittest.main()

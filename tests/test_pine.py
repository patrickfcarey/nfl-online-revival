"""The PINE client: reading and writing a running emulator's memory.

Two things make this worth covering despite never touching a real PCSX2 here.

The socket path rule was wrong once in a way that connects you to *nothing* or,
worse, to the wrong emulator: the fallback when ``XDG_RUNTIME_DIR`` is unset is
``/tmp``, not ``/run/user/<uid>`` -- which is the ordinary case over SSH -- and
any ``PINESlot`` but the default appends ``.<slot>`` to the filename.

And a write here lands in EE RAM immediately, with no validation, no bounds
check and no undo. A framing bug that shifts an address by a word would be
indistinguishable from a game that misbehaved on its own.

Everything below runs against a fake PINE server on a Unix socket, so the tests
neither need nor touch the rig.
"""

from __future__ import annotations

import os
import socket
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import pine  # noqa: E402


class FakePine:
    """A PINE server that records requests and replies from a script."""

    def __init__(self, replies=None, result=pine.OK):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "pcsx2.sock")
        self.requests = []
        self.replies = list(replies or [])
        self.result = result
        self.drop_after = None          # hang up mid-reply after N bytes
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(self.path)
        self._sock.listen(1)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        with conn:
            while True:
                header = self._read(conn, 4)
                if not header:
                    return
                total = struct.unpack("<I", header)[0]
                payload = self._read(conn, total - 4)
                if payload is None:
                    return
                self.requests.append(payload)
                data = self.replies.pop(0) if self.replies else b""
                body = bytes([self.result]) + data
                blob = struct.pack("<I", 4 + len(body)) + body
                if self.drop_after is not None:
                    conn.sendall(blob[:self.drop_after])
                    return
                try:
                    conn.sendall(blob)
                except OSError:
                    return

    @staticmethod
    def _read(conn, count):
        out = b""
        while len(out) < count:
            chunk = conn.recv(count - len(out))
            if not chunk:
                return None if out else b""
            out += chunk
        return out

    def stop(self):
        try:
            self._sock.close()
        except OSError:
            pass


class SocketPath(unittest.TestCase):
    """PCSX2's own rule, which an earlier version of this got wrong twice."""

    def setUp(self):
        self._saved = os.environ.get("XDG_RUNTIME_DIR")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("XDG_RUNTIME_DIR", None)
        else:
            os.environ["XDG_RUNTIME_DIR"] = self._saved

    def test_falls_back_to_tmp_not_run_user(self):
        # The ordinary case over SSH. /run/user/<uid> was the earlier guess and
        # simply does not exist there.
        os.environ.pop("XDG_RUNTIME_DIR", None)
        self.assertEqual(pine.default_socket_path(), "/tmp/pcsx2.sock")

    def test_uses_the_runtime_dir_when_it_is_set(self):
        os.environ["XDG_RUNTIME_DIR"] = "/run/user/1000"
        self.assertEqual(pine.default_socket_path(),
                         "/run/user/1000/pcsx2.sock")

    def test_the_default_slot_adds_no_suffix(self):
        os.environ["XDG_RUNTIME_DIR"] = "/tmp"
        self.assertEqual(pine.default_socket_path(pine.DEFAULT_SLOT),
                         "/tmp/pcsx2.sock")

    def test_any_other_slot_appends_it(self):
        # Not TCP-only, which is what the earlier note claimed.
        os.environ["XDG_RUNTIME_DIR"] = "/tmp"
        self.assertEqual(pine.default_socket_path(28012),
                         "/tmp/pcsx2.sock.28012")


class Framing(unittest.TestCase):
    def setUp(self):
        self.server = FakePine()
        self.addCleanup(self.server.stop)

    def _client(self):
        client = pine.Pine(self.server.path, timeout=5)
        self.addCleanup(client.close)
        client.connect()
        return client

    def test_a_read_sends_opcode_and_address(self):
        self.server.replies = [struct.pack("<I", 0xDEADBEEF)]
        value = self._client().read(0x00600B2C, 4)
        self.assertEqual(value, 0xDEADBEEF)
        self.assertEqual(self.server.requests[0],
                         struct.pack("<BI", pine.READ32, 0x00600B2C))

    def test_each_width_uses_its_own_opcode(self):
        for size, opcode, raw in ((1, pine.READ8, b"\x7f"),
                                  (2, pine.READ16, struct.pack("<H", 0x1234)),
                                  (4, pine.READ32, struct.pack("<I", 0x1234)),
                                  (8, pine.READ64, struct.pack("<Q", 0x1234))):
            server = FakePine(replies=[raw])
            self.addCleanup(server.stop)
            client = pine.Pine(server.path, timeout=5)
            self.addCleanup(client.close)
            client.read(0x100, size)
            self.assertEqual(server.requests[0][0], opcode, "size %d" % size)

    def test_a_write_sends_opcode_address_and_value(self):
        client = self._client()
        client.write(0x00600B2C, 0x1234, 4)
        self.assertEqual(self.server.requests[0],
                         struct.pack("<BI", pine.WRITE32, 0x00600B2C)
                         + struct.pack("<I", 0x1234))

    def test_a_write_masks_to_the_requested_width(self):
        # Otherwise struct.pack raises and the caller sees a crash rather than
        # the truncation they asked for.
        client = self._client()
        client.write(0x100, 0x1FF, 1)
        self.assertEqual(self.server.requests[0][5:], b"\xff")

    def test_an_invalid_width_is_refused_before_anything_is_sent(self):
        client = self._client()
        with self.assertRaises(ValueError):
            client.read(0x100, 3)
        with self.assertRaises(ValueError):
            client.write(0x100, 1, 5)
        self.assertEqual(self.server.requests, [])

    def test_read_bytes_assembles_words_in_order(self):
        self.server.replies = [struct.pack("<I", 0x44434241),
                               struct.pack("<I", 0x48474645)]
        self.assertEqual(self._client().read_bytes(0x100, 8), b"ABCDEFGH")

    def test_read_bytes_trims_to_the_requested_length(self):
        self.server.replies = [struct.pack("<I", 0x44434241),
                               struct.pack("<I", 0x48474645)]
        self.assertEqual(self._client().read_bytes(0x100, 6), b"ABCDEF")

    def test_read_string_stops_at_the_nul(self):
        self.server.replies = [b"ZZTE", b"ST\x00\x00"]
        self.assertEqual(self._client().read_string(0x100, limit=8), "ZZTEST")

    def test_identity_replies_skip_their_length_prefix(self):
        self.server.replies = [struct.pack("<I", 12) + b"SLUS-20752\x00"]
        self.assertEqual(self._client().game_id(), "SLUS-20752")

    def test_status_is_a_little_endian_word(self):
        self.server.replies = [struct.pack("<I", 2)]
        self.assertEqual(self._client().status(), 2)


class Failures(unittest.TestCase):
    def test_a_missing_socket_says_what_to_check(self):
        client = pine.Pine("/nonexistent/pcsx2.sock")
        with self.assertRaises(pine.PineError) as caught:
            client.connect()
        self.assertIn("EnablePINE", str(caught.exception))

    def test_a_rejected_request_raises(self):
        server = FakePine(result=0xFF)
        self.addCleanup(server.stop)
        client = pine.Pine(server.path, timeout=5)
        self.addCleanup(client.close)
        with self.assertRaises(pine.PineError) as caught:
            client.read(0x100)
        self.assertIn("0xff", str(caught.exception))

    def test_a_reply_shorter_than_the_minimum_raises(self):
        # A declared total of 4 leaves no room for the result byte. Trusting it
        # would index into an empty body.
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        path = os.path.join(tempfile.mkdtemp(), "short.sock")
        listener.bind(path)
        listener.listen(1)
        self.addCleanup(listener.close)

        def answer():
            conn, _ = listener.accept()
            conn.recv(64)
            conn.sendall(struct.pack("<I", 4))
            conn.close()

        threading.Thread(target=answer, daemon=True).start()
        client = pine.Pine(path, timeout=5)
        self.addCleanup(client.close)
        with self.assertRaises(pine.PineError) as caught:
            client.read(0x100)
        self.assertIn("minimum is 5", str(caught.exception))

    def test_a_connection_closed_mid_reply_raises(self):
        server = FakePine(replies=[struct.pack("<I", 1)])
        server.drop_after = 6          # header plus part of the body
        self.addCleanup(server.stop)
        client = pine.Pine(server.path, timeout=5)
        self.addCleanup(client.close)
        with self.assertRaises(pine.PineError) as caught:
            client.read(0x100)
        self.assertIn("closed the connection", str(caught.exception))

    def test_the_context_manager_closes_the_socket(self):
        server = FakePine(replies=[struct.pack("<I", 1)])
        self.addCleanup(server.stop)
        with pine.Pine(server.path, timeout=5) as client:
            client.read(0x100)
            self.assertIsNotNone(client.sock)
        self.assertIsNone(client.sock)

    def test_close_is_idempotent(self):
        client = pine.Pine("/nonexistent/x.sock")
        client.close()
        client.close()


if __name__ == "__main__":
    unittest.main()

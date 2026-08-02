"""The capture servers' listening loops.

The parsers were covered in `test_recon_tools.py`; what was left is the part
that binds sockets and runs until interrupted. That is the half where a bug
costs a capture session rather than a wrong reading -- a listener that dies on
the first malformed packet, or a `serve()` that reports success having bound
nothing, produces an empty transcript and no explanation.

`serve()` blocks on `time.sleep` until Ctrl-C, so the tests swap the module's
`time` for a proxy whose `sleep` is a rendezvous: the test waits until the
listeners are up, drives traffic, then releases the shim, which raises
KeyboardInterrupt and lets the real shutdown path run. Only the module under
test sees the substitution.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recon import easerver, eaproto, sinkd, tlssink  # noqa: E402


class _Clock:
    """The real time module, with `sleep` turned into a controllable stop."""

    def __init__(self, real, on_sleep):
        self._real = real
        self._on_sleep = on_sleep

    def __getattr__(self, name):        # strftime, time, and the rest
        return getattr(self._real, name)

    def sleep(self, _seconds):
        self._on_sleep()


class _Harness:
    """Run a blocking serve() long enough to drive traffic through it."""

    def __init__(self, module):
        self.module = module
        self.ready = threading.Event()
        self.release = threading.Event()
        self._saved = module.time

    def __enter__(self):
        def on_sleep():
            self.ready.set()
            self.release.wait(20)
            raise KeyboardInterrupt
        self.module.time = _Clock(time, on_sleep)
        return self

    def start(self, target, **kwargs):
        self.thread = threading.Thread(target=target, kwargs=kwargs,
                                       daemon=True)
        self.thread.start()
        if not self.ready.wait(20):
            raise AssertionError("serve() never reached its wait loop")

    def __exit__(self, *_exc):
        self.release.set()
        thread = getattr(self, "thread", None)
        if thread is not None:
            thread.join(20)
        self.module.time = self._saved
        return False


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def transcript_path(case):
    handle, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(handle)
    case.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
    return path


def rows(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class SinkholeLoops(unittest.TestCase):
    """`_accept_loop` and `_udp_loop`, driven directly."""

    def test_the_accept_loop_serves_each_connection(self):
        listener = sinkd._bind_tcp("127.0.0.1", 0)
        self.assertIsNotNone(listener)
        port = listener.getsockname()[1]
        path = transcript_path(self)
        handle = open(path, "w", encoding="utf-8")
        self.addCleanup(handle.close)
        transcript = sinkd._Transcript(handle)
        threading.Thread(target=sinkd._accept_loop,
                         args=(listener, port, transcript, None),
                         daemon=True).start()
        for index in range(2):
            client = socket.create_connection(("127.0.0.1", port), timeout=5)
            client.sendall(b"probe %d" % index)
            client.close()
        self.assertTrue(wait_for(lambda: len(rows(path)) >= 2), rows(path))
        listener.close()

    def test_the_accept_loop_returns_on_a_dead_listener(self):
        """Rather than spinning on a descriptor that will never accept again.

        Closed *before* the call: on Linux, closing a socket another thread is
        already blocked in `accept()` on does not wake it, so asserting that
        would be testing the kernel rather than this loop.
        """
        listener = sinkd._bind_tcp("127.0.0.1", 0)
        listener.close()
        stopped = threading.Event()

        def run():
            sinkd._accept_loop(listener, 0, sinkd._Transcript(None), None)
            stopped.set()

        threading.Thread(target=run, daemon=True).start()
        self.assertTrue(stopped.wait(5), "the accept loop did not return")

    def test_a_configured_reply_is_sent_once(self):
        listener = sinkd._bind_tcp("127.0.0.1", 0)
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]
        threading.Thread(
            target=sinkd._accept_loop,
            args=(listener, port, sinkd._Transcript(None), b"HELLO"),
            daemon=True).start()
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.addCleanup(client.close)
        client.sendall(b"first")
        client.settimeout(5)
        self.assertEqual(client.recv(16), b"HELLO")
        client.sendall(b"second")
        client.settimeout(1)
        with self.assertRaises((socket.timeout, OSError)):
            self.assertEqual(client.recv(16), b"")

    def test_a_greeting_is_recorded_as_a_send(self):
        listener = sinkd._bind_tcp("127.0.0.1", 0)
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]
        path = transcript_path(self)
        handle = open(path, "w", encoding="utf-8")
        self.addCleanup(handle.close)
        threading.Thread(
            target=sinkd._accept_loop,
            args=(listener, port, sinkd._Transcript(handle), None, b"220 hi\r\n"),
            daemon=True).start()
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.addCleanup(client.close)
        client.settimeout(5)
        client.recv(32)
        self.assertTrue(wait_for(
            lambda: any(r["dir"] == "send" for r in rows(path))), rows(path))

    def test_the_udp_loop_records_and_can_reply(self):
        sock = sinkd._bind_udp("127.0.0.1", 0)
        self.assertIsNotNone(sock)
        self.addCleanup(sock.close)
        port = sock.getsockname()[1]
        path = transcript_path(self)
        handle = open(path, "w", encoding="utf-8")
        self.addCleanup(handle.close)
        threading.Thread(target=sinkd._udp_loop,
                         args=(sock, port, sinkd._Transcript(handle), b"PONG"),
                         daemon=True).start()
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(client.close)
        client.settimeout(5)
        client.sendto(b"PING", ("127.0.0.1", port))
        self.assertEqual(client.recv(16), b"PONG")
        self.assertTrue(wait_for(lambda: len(rows(path)) >= 2), rows(path))

    def test_the_udp_loop_returns_on_a_dead_socket(self):
        sock = sinkd._bind_udp("127.0.0.1", 0)
        sock.close()
        stopped = threading.Event()

        def run():
            sinkd._udp_loop(sock, 0, sinkd._Transcript(None), None)
            stopped.set()

        threading.Thread(target=run, daemon=True).start()
        self.assertTrue(stopped.wait(5))

    def test_an_empty_payload_logs_a_header_and_no_dump(self):
        sinkd._log("udp", 53, "10.0.0.1:1", "recv", b"")     # must not raise


class SinkholeServe(unittest.TestCase):
    def test_no_ports_is_a_usage_error(self):
        with self.assertRaises(ValueError):
            sinkd.serve("127.0.0.1", [], [])

    def test_binding_nothing_raises_rather_than_capturing_silently(self):
        """A privilege problem must surface now, not as an empty transcript.

        Port 1 is privileged; if the suite happens to run as root the bind
        succeeds and there is nothing to assert, so the test says so.
        """
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        taken = held.getsockname()[1]
        try:
            with self.assertRaises(sinkd.SinkError) as caught:
                sinkd.serve("127.0.0.1", [taken], [])
            self.assertIn("could not bind", str(caught.exception))
        finally:
            held.close()

    def test_it_binds_serves_and_shuts_down_cleanly(self):
        port = free_port()
        path = transcript_path(self)
        with _Harness(sinkd) as harness:
            harness.start(sinkd.serve, bind="127.0.0.1", tcp_ports=[port],
                          udp_ports=[], transcript_path=path)
            client = socket.create_connection(("127.0.0.1", port), timeout=5)
            client.sendall(b"through the real serve loop")
            client.close()
            self.assertTrue(wait_for(lambda: rows(path)), "nothing recorded")
        # The transcript is closed by serve()'s finally; a second close latches.
        self.assertTrue(rows(path))

    def test_a_partial_bind_still_serves_what_it_got(self):
        # Losing one port must not lose the capture on the others.
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        taken = held.getsockname()[1]
        self.addCleanup(held.close)
        good = free_port()
        with _Harness(sinkd) as harness:
            harness.start(sinkd.serve, bind="127.0.0.1",
                          tcp_ports=[good, taken], udp_ports=[])
            client = socket.create_connection(("127.0.0.1", good), timeout=5)
            client.sendall(b"still captured")
            client.close()


class EaTranscript(unittest.TestCase):
    def test_a_decoded_message_is_recorded(self):
        path = transcript_path(self)
        transcript = easerver._Transcript(path)
        message = eaproto.decode(eaproto.encode("auth", 7, {"NAME": "alice"}))
        transcript.record("recv", "10.0.0.1:1", message, b"\x01\x02")
        self.assertEqual(rows(path)[0]["type"], "auth")
        self.assertEqual(rows(path)[0]["txn"], 7)

    def test_undecodable_bytes_are_recorded_too(self):
        # The most valuable kind: a rejected message would otherwise look
        # identical to one never sent.
        path = transcript_path(self)
        easerver._Transcript(path).record_raw("10.0.0.1:1", "framing-error",
                                              b"\xde\xad", "bad length")
        self.assertEqual(rows(path)[0]["hex"], "dead")

    def test_no_path_records_nothing_and_does_not_raise(self):
        transcript = easerver._Transcript(None)
        transcript.record_raw("p", "k", b"x")
        message = eaproto.decode(eaproto.encode("auth", 0, {}))
        transcript.record("recv", "p", message, b"x")

    def test_an_unwritable_path_is_reported_not_fatal(self):
        transcript = easerver._Transcript("/nonexistent/dir/t.jsonl")
        transcript.record_raw("p", "k", b"x")       # must not raise


class EaServe(unittest.TestCase):
    def test_a_connection_gets_its_dir_redirect(self):
        port = free_port()
        path = transcript_path(self)
        with _Harness(easerver) as harness:
            harness.start(easerver.serve, bind="127.0.0.1", port=port,
                          transcript_path=path, redirect_host="127.0.0.1",
                          redirect_port=port + 1)
            client = socket.create_connection(("127.0.0.1", port), timeout=5)
            self.addCleanup(client.close)
            client.sendall(eaproto.encode("@dir", 1, {"PROD": "MADDEN"}))
            client.settimeout(10)
            reply = eaproto.decode(client.recv(4096))
            self.assertEqual(reply.type, "@dir")
            self.assertEqual(reply.fields["ADDR"], "127.0.0.1")

    def test_a_configured_reply_is_answered(self):
        handle, replies = tempfile.mkstemp(suffix=".json")
        os.write(handle, json.dumps({"auth": {"OK": "1"}}).encode())
        os.close(handle)
        self.addCleanup(os.unlink, replies)
        port = free_port()
        with _Harness(easerver) as harness:
            harness.start(easerver.serve, bind="127.0.0.1", port=port,
                          reply_file=replies)
            client = socket.create_connection(("127.0.0.1", port), timeout=5)
            self.addCleanup(client.close)
            client.sendall(eaproto.encode("auth", 3, {}))
            client.settimeout(10)
            reply = eaproto.decode(client.recv(4096))
            self.assertEqual(reply.fields["OK"], "1")
            self.assertEqual(reply.txn, 3, "the transaction must be echoed")

    def test_a_framing_error_is_kept_rather_than_discarded(self):
        port = free_port()
        path = transcript_path(self)
        with _Harness(easerver) as harness:
            harness.start(easerver.serve, bind="127.0.0.1", port=port,
                          transcript_path=path)
            client = socket.create_connection(("127.0.0.1", port), timeout=5)
            self.addCleanup(client.close)
            # A declared length below the header: unparseable by construction.
            client.sendall(b"junk" + b"\x00" * 8)
            self.assertTrue(
                wait_for(lambda: any(r.get("dir") == "framing-error"
                                     for r in rows(path))), rows(path))

    def test_an_unbindable_port_is_reported(self):
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        self.addCleanup(held.close)
        with self.assertRaises(easerver.EaServerError) as caught:
            easerver.serve("127.0.0.1", held.getsockname()[1])
        self.assertIn("cannot bind", str(caught.exception))

    def test_a_bind_failure_releases_the_ports_already_taken(self):
        # Otherwise a retry after a typo fails on the ports the first attempt
        # is still holding.
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        self.addCleanup(held.close)
        first = free_port()
        with self.assertRaises(easerver.EaServerError):
            easerver.serve("127.0.0.1", [first, held.getsockname()[1]])
        probe = socket.socket()
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", first))       # must be free again
        probe.close()


class Certificates(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cert = os.path.join(self.dir, "c.pem")
        self.key = os.path.join(self.dir, "k.pem")

    def test_an_existing_pair_is_returned_untouched(self):
        Path(self.cert).write_text("cert")
        Path(self.key).write_text("key")
        self.assertEqual(tlssink.ensure_certificate(self.cert, self.key),
                         (self.cert, self.key))
        self.assertEqual(Path(self.cert).read_text(), "cert")

    def test_an_unreadable_pair_says_what_to_delete(self):
        """Left root-owned by an earlier sudo run.

        The bare PermissionError from load_cert_chain named nothing useful.
        """
        Path(self.cert).write_text("cert")
        Path(self.key).write_text("key")
        os.chmod(self.key, 0)
        self.addCleanup(os.chmod, self.key, 0o600)
        if os.access(self.key, os.R_OK):        # running as root
            self.skipTest("running as root; the key stays readable")
        with self.assertRaises(tlssink.TlsSinkError) as caught:
            tlssink.ensure_certificate(self.cert, self.key)
        self.assertIn("rm -f", str(caught.exception))

    def test_a_missing_openssl_is_reported_clearly(self):
        import subprocess
        original = subprocess.run

        def explode(*_a, **_k):
            raise FileNotFoundError("openssl")

        subprocess.run = explode
        self.addCleanup(lambda: setattr(subprocess, "run", original))
        with self.assertRaises(tlssink.TlsSinkError) as caught:
            tlssink.ensure_certificate(self.cert, self.key)
        self.assertIn("openssl is not installed", str(caught.exception))

    def test_a_failing_openssl_is_reported_with_its_output(self):
        import subprocess
        original = subprocess.run

        class _Result:
            returncode = 1
            stdout = b"some openssl complaint"

        subprocess.run = lambda *a, **k: _Result()
        self.addCleanup(lambda: setattr(subprocess, "run", original))
        with self.assertRaises(tlssink.TlsSinkError) as caught:
            tlssink.ensure_certificate(self.cert, self.key)
        self.assertIn("some openssl complaint", str(caught.exception))

    def test_a_pair_is_generated_when_openssl_is_available(self):
        import shutil
        if shutil.which("openssl") is None:
            self.skipTest("openssl is not installed here")
        tlssink.ensure_certificate(self.cert, self.key, common_name="x")
        self.assertTrue(os.path.getsize(self.cert))
        self.assertTrue(os.path.getsize(self.key))

    def test_a_missing_directory_is_created(self):
        import shutil
        if shutil.which("openssl") is None:
            self.skipTest("openssl is not installed here")
        nested = os.path.join(self.dir, "deep", "c.pem")
        key = os.path.join(self.dir, "deep", "k.pem")
        tlssink.ensure_certificate(nested, key)
        self.assertTrue(os.path.exists(nested))


class TlsServe(unittest.TestCase):
    def setUp(self):
        import shutil
        if shutil.which("openssl") is None:
            self.skipTest("openssl is not installed here")
        self.dir = tempfile.mkdtemp()
        self.cert = os.path.join(self.dir, "c.pem")
        self.key = os.path.join(self.dir, "k.pem")

    def _context(self):
        import ssl
        tlssink.ensure_certificate(self.cert, self.key)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, self.key)
        return context

    def _drive(self, payload):
        """Push `payload` through _handle and return what it recorded.

        _handle rather than serve(): tlssink's wait loop blocks in `accept()`
        rather than on a sleep, so there is no rendezvous to interrupt it with
        from another thread. _handle is where the parsing and recording live.
        """
        path = transcript_path(self)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]
        results, lock = [], threading.Lock()

        def accept():
            conn, addr = listener.accept()
            tlssink._handle(conn, addr, port, self._context(), results, lock,
                            path)

        thread = threading.Thread(target=accept, daemon=True)
        thread.start()
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.addCleanup(client.close)
        client.sendall(payload)
        wait_for(lambda: rows(path))
        thread.join(10)
        return rows(path), results

    def test_a_plaintext_client_is_reported_rather_than_dropped(self):
        """Something other than TLS on this port is itself a finding.

        The handshake cannot proceed, and what matters is that the bytes were
        parsed and recorded rather than the handler dying on them.
        """
        recorded, _results = self._drive(b"GET / HTTP/1.0\r\n\r\n")
        self.assertTrue(recorded, "nothing recorded")
        # The parsed hello is nested under "hello"; the outer record carries
        # the peer, the port and the handshake outcome.
        self.assertFalse(recorded[0]["hello"]["is_tls"])
        self.assertIn("not a TLS handshake", recorded[0]["hello"]["error"])

    def test_a_client_hello_is_parsed_and_recorded(self):
        hello = (b"\x16\x03\x01\x00\x2f\x01\x00\x00\x2b\x03\x01"
                 + b"\x00" * 32 + b"\x00\x00\x02\x00\x2f\x01\x00")
        recorded, _results = self._drive(hello)
        self.assertTrue(recorded, "nothing recorded")
        self.assertTrue(recorded[0]["hello"]["is_tls"])
        self.assertEqual(recorded[0]["hello"]["ciphers"], ["0x002f"])
        self.assertIn("peer", recorded[0])

    def test_an_sslv2_framed_hello_is_recognised(self):
        # What a 2004 console actually opens with.
        body = (b"\x01" + b"\x03\x00"          # CLIENT-HELLO, SSL 3.0
                + b"\x00\x03\x00\x00\x00\x10"  # cipher/session/challenge
                + b"\x00\x00\x04" + b"\x00" * 16)
        recorded, _results = self._drive(
            bytes([0x80 | (len(body) >> 8), len(body) & 0xFF]) + body)
        self.assertTrue(recorded, "nothing recorded")
        self.assertTrue(recorded[0]["hello"]["is_sslv2_hello"])

    def test_an_unbindable_port_is_reported(self):
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        self.addCleanup(held.close)
        with self.assertRaises(tlssink.TlsSinkError):
            tlssink.serve("127.0.0.1", held.getsockname()[1],
                          cert=self.cert, key=self.key)


if __name__ == "__main__":
    unittest.main()

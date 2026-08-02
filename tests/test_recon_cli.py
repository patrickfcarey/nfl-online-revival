"""`python -m recon`: argument parsing and subcommand dispatch.

The argument types are where a capture session is won or lost before it starts.
A port list that silently accepts garbage, or a `--respond-hex` that quietly
yields the wrong bytes, produces a capture that looks like the client did
something it did not -- and these tools exist precisely because the client's
behaviour is the thing in question.

Every command here ends in a blocking `serve()`, so the tests drive the error
paths and stop at the boundary: the last thing checked is the exit code the
wrapper turns each failure into.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recon import __main__ as cli  # noqa: E402


@contextlib.contextmanager
def captured():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


class PortArguments(unittest.TestCase):
    def test_a_list_parses(self):
        self.assertEqual(cli._port_list("80,443,10000"), [80, 443, 10000])

    def test_whitespace_and_empty_pieces_are_tolerated(self):
        self.assertEqual(cli._port_list(" 80 , , 443 "), [80, 443])

    def test_a_non_number_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._port_list("https")

    def test_out_of_range_is_rejected(self):
        for text in ("0", "65536", "-1"):
            with self.assertRaises(argparse.ArgumentTypeError):
                cli._port_list(text)

    def test_an_empty_list_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._port_list(" , ")

    def test_a_single_port_takes_the_first(self):
        self.assertEqual(cli._port("443,80"), 443)


class NumericArguments(unittest.TestCase):
    def test_zero_is_allowed(self):
        self.assertEqual(cli._non_negative("0"), 0)

    def test_a_negative_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._non_negative("-1")

    def test_a_non_number_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._non_negative("many")


class HexArguments(unittest.TestCase):
    def test_plain_hex_decodes(self):
        self.assertEqual(cli._hex_bytes("5c6f6b5c"), b"\\ok\\")

    def test_separators_are_ignored(self):
        # Pasting from a hexdump should just work.
        self.assertEqual(cli._hex_bytes("5c 6f:6b 5c"), b"\\ok\\")

    def test_an_odd_length_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._hex_bytes("5c6")

    def test_non_hex_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError) as caught:
            cli._hex_bytes("hello")
        self.assertIn("--respond-hex", str(caught.exception))


class MapArguments(unittest.TestCase):
    def test_a_host_ip_pair_splits(self):
        self.assertEqual(cli._host_ip("ps2madden04.ea.com=10.0.0.1"),
                         ("ps2madden04.ea.com", "10.0.0.1"))

    def test_surrounding_space_is_trimmed(self):
        self.assertEqual(cli._host_ip(" a.example = 10.0.0.1 "),
                         ("a.example", "10.0.0.1"))

    def test_a_missing_equals_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._host_ip("a.example")

    def test_an_empty_half_is_rejected(self):
        for text in ("=10.0.0.1", "a.example=", "="):
            with self.assertRaises(argparse.ArgumentTypeError):
                cli._host_ip(text)


class Dispatch(unittest.TestCase):
    """Each subcommand's guard, stopping before the blocking serve()."""

    def test_sink_needs_at_least_one_port(self):
        with captured() as (_o, err):
            self.assertEqual(cli.main(["sink"]), 2)
        self.assertIn("at least one --tcp", err.getvalue())

    def test_sink_reports_a_bind_failure(self):
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        self.addCleanup(held.close)
        port = held.getsockname()[1]
        with captured() as (_o, err):
            code = cli.main(["sink", "--bind", "127.0.0.1", "--tcp", str(port)])
        self.assertEqual(code, 1)
        self.assertIn("could not bind", err.getvalue())

    def test_dns_validates_the_default_ip(self):
        with captured() as (_o, err):
            self.assertEqual(cli.main(["dns", "--ip", "not-an-ip"]), 2)
        self.assertIn("error", err.getvalue())

    def test_dns_validates_a_mapped_ip(self):
        with captured() as (_o, err):
            self.assertEqual(
                cli.main(["dns", "--map", "a.example=999.1.1.1"]), 2)
        self.assertIn("error", err.getvalue())

    def test_ea_reports_a_bind_failure(self):
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        self.addCleanup(held.close)
        with captured() as (_o, err):
            code = cli.main(["ea", "--bind", "127.0.0.1",
                             "--port", str(held.getsockname()[1])])
        self.assertEqual(code, 1)
        self.assertIn("cannot bind", err.getvalue())

    def test_ea_reports_a_bad_reply_file(self):
        handle, path = tempfile.mkstemp(suffix=".json")
        os.write(handle, b"{not json")
        os.close(handle)
        self.addCleanup(os.unlink, path)
        with captured() as (_o, err):
            code = cli.main(["ea", "--bind", "127.0.0.1", "--port", "1",
                             "--replies", path])
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", err.getvalue())

    def test_tls_reports_a_bind_failure(self):
        import shutil
        if shutil.which("openssl") is None:
            self.skipTest("openssl is not installed here")
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        self.addCleanup(held.close)
        directory = tempfile.mkdtemp()
        with captured() as (_o, err):
            code = cli.main(["tls", "--bind", "127.0.0.1",
                             "--port", str(held.getsockname()[1]),
                             "--cert", os.path.join(directory, "c.pem"),
                             "--key", os.path.join(directory, "k.pem")])
        self.assertEqual(code, 1)
        self.assertIn("cannot bind", err.getvalue())


class Classify(unittest.TestCase):
    def test_a_pcap_passed_as_a_transcript_is_caught(self):
        """The two file kinds look alike from the command line.

        Reading a capture as JSONL yields nothing and no explanation, which is
        indistinguishable from a capture with nothing in it.
        """
        handle, path = tempfile.mkstemp(suffix=".pcap")
        os.write(handle, b"\xd4\xc3\xb2\xa1" + b"\x00" * 64)
        os.close(handle)
        self.addCleanup(os.unlink, path)
        with captured() as (_o, err):
            self.assertEqual(cli.main(["classify", path, "--transcript"]), 2)
        self.assertIn("packet capture, not a sink transcript", err.getvalue())

    def test_a_missing_file_exits_one(self):
        with captured():
            self.assertEqual(cli.main(["classify", "/nonexistent.pcap"]), 1)

    def test_an_empty_transcript_is_read_without_error(self):
        handle, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(handle)
        self.addCleanup(os.unlink, path)
        with captured():
            self.assertEqual(cli.main(["classify", path, "--transcript"]), 0)


class Pcap(unittest.TestCase):
    def test_a_missing_file_exits_one(self):
        with captured():
            self.assertEqual(cli.main(["pcap", "/nonexistent.pcap"]), 1)

    def test_an_empty_capture_says_what_to_check(self):
        handle, path = tempfile.mkstemp(suffix=".pcap")
        # A classic pcap header with no packets after it.
        os.write(handle, b"\xd4\xc3\xb2\xa1\x02\x00\x04\x00" + b"\x00" * 8
                 + b"\xff\xff\x00\x00\x01\x00\x00\x00")
        os.close(handle)
        self.addCleanup(os.unlink, path)
        with captured() as (out, _err):
            code = cli.main(["pcap", path])
        self.assertEqual(code, 0)
        self.assertIn("no TCP/UDP packets decoded", out.getvalue())


class Parser(unittest.TestCase):
    def test_every_subcommand_is_registered(self):
        parser = cli.build_parser()
        for command in ("dns", "sink", "tls", "ea", "classify", "pcap"):
            args = parser.parse_args([command, "x"] if command in
                                     ("classify", "pcap") else [command])
            self.assertTrue(callable(args.func), command)

    def test_an_unknown_subcommand_is_refused(self):
        with captured():
            with self.assertRaises(SystemExit):
                cli.build_parser().parse_args(["nonesuch"])


if __name__ == "__main__":
    unittest.main()

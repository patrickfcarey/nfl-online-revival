"""Tests for the recon harness. Offline: no sockets, no network, no capture.

Run: ``python3 tests/test_recon.py`` (or ``python3 -m unittest discover tests``).

Several cases here are regression tests for defects found by review before the
first real capture, and each is named for the failure it prevents. The link-type
and bind-failure ones matter most: both used to present as "capture came back
empty", which is indistinguishable from a game that simply never phoned home.
"""

from __future__ import annotations

import io
import json
import os
import socket
import struct
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recon import __main__ as recon_cli  # noqa: E402
from recon import classify, dnsd, pcapreader, sinkd  # noqa: E402


# --------------------------------------------------------------------------
# builders: synthetic packets and pcap files
# --------------------------------------------------------------------------

def pcap_file(frames, linktype=1, magic=b"\xa1\xb2\xc3\xd4"):
    """Wrap frames in a classic pcap. Big-endian magic keeps the writer simple."""
    endian = ">" if magic == b"\xa1\xb2\xc3\xd4" else "<"
    out = magic + struct.pack(endian + "HHiIII", 2, 4, 0, 0, 65535, linktype)
    for frame in frames:
        out += struct.pack(endian + "IIII", 0, 0, len(frame), len(frame)) + frame
    return out


def ip_packet(src, dst, proto, l4, frag_off=0):
    total = 20 + len(l4)
    header = struct.pack(">BBHHHBBH4s4s", 0x45, 0, total, 1, frag_off, 64,
                         proto, 0, socket.inet_aton(src), socket.inet_aton(dst))
    return header + l4


def tcp(sport, dport, payload=b"", flags=0x18, data_off=5):
    return struct.pack(">HHIIBBHHH", sport, dport, 0, 0,
                       data_off << 4, flags, 0xffff, 0, 0) + payload


def udp(sport, dport, payload=b""):
    return struct.pack(">HHHH", sport, dport, 8 + len(payload), 0) + payload


def eth(ip):
    return b"\x00" * 12 + b"\x08\x00" + ip


def sll(ip):
    return struct.pack(">HHH", 0, 1, 6) + b"\x00" * 8 + struct.pack(">H", 0x0800) + ip


def sll2(ip):
    return (struct.pack(">HHIHBB", 0x0800, 0, 2, 1, 0, 6) + b"\x00" * 8 + ip)


def dns_query(host, qtype=1, qdcount=1):
    msg = struct.pack(">HHHHHH", 0x1234, 0x0100, qdcount, 0, 0, 0)
    for label in host.split("."):
        msg += bytes([len(label)]) + label.encode()
    return msg + b"\x00" + struct.pack(">HH", qtype, 1)


# --------------------------------------------------------------------------
# pcapreader
# --------------------------------------------------------------------------

class LinkTypeTests(unittest.TestCase):
    """Regression: `tcpdump -i any` writes SLL2 on libpcap >= 1.10, and an
    unhandled link type silently yielded zero flows -- a capture that looks
    empty rather than one that failed to parse."""

    PAYLOAD = b"\\gamename\\madden2004\\"

    def _one_flow(self, frame, linktype):
        data = pcap_file([frame], linktype=linktype)
        return list(pcapreader.read_flows(io.BytesIO(data)))

    def test_ethernet(self):
        flows = self._one_flow(eth(ip_packet("10.0.0.9", "10.0.0.5", 17,
                                             udp(4000, 28900, self.PAYLOAD))), 1)
        self.assertEqual(len(flows), 1)
        self.assertEqual((flows[0].dport, flows[0].payload), (28900, self.PAYLOAD))

    def test_linux_sll_cooked(self):
        flows = self._one_flow(sll(ip_packet("10.0.0.9", "10.0.0.5", 17,
                                             udp(4000, 28900, self.PAYLOAD))), 113)
        self.assertEqual(len(flows), 1)
        self.assertEqual(flows[0].payload, self.PAYLOAD)

    def test_linux_sll2_is_decoded(self):
        # The one that matters: this is what `-i any` produces on Ubuntu 24.04.
        flows = self._one_flow(sll2(ip_packet("10.0.0.9", "10.0.0.5", 17,
                                              udp(4000, 28900, self.PAYLOAD))), 276)
        self.assertEqual(len(flows), 1, "SLL2 capture decoded as empty")
        self.assertEqual(flows[0].payload, self.PAYLOAD)

    def test_raw_ip(self):
        flows = self._one_flow(ip_packet("10.0.0.9", "10.0.0.5", 17,
                                         udp(4000, 28900, self.PAYLOAD)), 101)
        self.assertEqual(len(flows), 1)

    def test_null_loopback(self):
        frame = struct.pack("<I", 2) + ip_packet("127.0.0.1", "127.0.0.1", 17,
                                                 udp(4000, 28900, self.PAYLOAD))
        self.assertEqual(len(self._one_flow(frame, 0)), 1)

    def test_vlan_tagged_ethernet(self):
        ip = ip_packet("10.0.0.9", "10.0.0.5", 17, udp(4000, 28900, self.PAYLOAD))
        frame = b"\x00" * 12 + b"\x81\x00" + b"\x00\x64" + b"\x08\x00" + ip
        self.assertEqual(len(self._one_flow(frame, 1)), 1)

    def test_unknown_link_type_raises_instead_of_yielding_nothing(self):
        with self.assertRaises(pcapreader.UnsupportedLinkType) as caught:
            self._one_flow(b"\x00" * 40, 999)
        self.assertIn("999", str(caught.exception))


class PcapFormatTests(unittest.TestCase):
    def test_pcapng_is_rejected_with_guidance(self):
        with self.assertRaises(ValueError) as caught:
            list(pcapreader.read_flows(io.BytesIO(b"\x0a\x0d\x0d\x0a" + b"\x00" * 32)))
        self.assertIn("pcapng", str(caught.exception))

    def test_little_endian_magic(self):
        data = pcap_file([eth(ip_packet("10.0.0.9", "10.0.0.5", 17, udp(1, 2, b"x")))],
                         magic=b"\xd4\xc3\xb2\xa1")
        self.assertEqual(len(list(pcapreader.read_flows(io.BytesIO(data)))), 1)

    def test_bad_magic_is_rejected(self):
        with self.assertRaises(ValueError):
            list(pcapreader.read_flows(io.BytesIO(b"junkjunk" + b"\x00" * 32)))

    def test_truncated_final_record_is_ignored(self):
        data = pcap_file([eth(ip_packet("10.0.0.9", "10.0.0.5", 17, udp(1, 2, b"x")))])
        flows = list(pcapreader.read_flows(io.BytesIO(data + b"\x00" * 8)))
        self.assertEqual(len(flows), 1)


class IpDecodeTests(unittest.TestCase):
    def test_mid_fragment_is_skipped(self):
        """Regression: a continuation fragment has no transport header, so
        decoding it invented ports and payload from arbitrary bytes."""
        frame = eth(ip_packet("10.0.0.9", "10.0.0.5", 6,
                              tcp(4000, 28900, b"\xde\xad" * 10), frag_off=185))
        flows = list(pcapreader.read_flows(io.BytesIO(pcap_file([frame]))))
        self.assertEqual(flows, [])

    def test_first_fragment_with_more_flag_is_kept(self):
        # Offset 0 with MF set still carries the real transport header.
        frame = eth(ip_packet("10.0.0.9", "10.0.0.5", 6,
                              tcp(4000, 28900, b"hello"), frag_off=0x2000))
        flows = list(pcapreader.read_flows(io.BytesIO(pcap_file([frame]))))
        self.assertEqual(len(flows), 1)

    def test_tcp_flags_and_syn_detection(self):
        frame = eth(ip_packet("10.0.0.9", "10.0.0.5", 6, tcp(4000, 28900, flags=0x02)))
        flow = list(pcapreader.read_flows(io.BytesIO(pcap_file([frame]))))[0]
        self.assertTrue(flow.is_syn_open)
        syn_ack = eth(ip_packet("10.0.0.5", "10.0.0.9", 6, tcp(28900, 4000, flags=0x12)))
        flow2 = list(pcapreader.read_flows(io.BytesIO(pcap_file([syn_ack]))))[0]
        self.assertFalse(flow2.is_syn_open)

    def test_tcp_options_are_not_counted_as_payload(self):
        # data offset 8 words = 32 bytes: 20 header + 12 of options.
        segment = tcp(4000, 28900, b"\x00" * 12 + b"PAYLOAD", data_off=8)
        frame = eth(ip_packet("10.0.0.9", "10.0.0.5", 6, segment))
        flow = list(pcapreader.read_flows(io.BytesIO(pcap_file([frame]))))[0]
        self.assertEqual(flow.payload, b"PAYLOAD")

    def test_udp_length_trims_ethernet_padding(self):
        segment = udp(4000, 28900, b"AB") + b"\x00" * 20  # padded short frame
        frame = eth(ip_packet("10.0.0.9", "10.0.0.5", 17, segment))
        flow = list(pcapreader.read_flows(io.BytesIO(pcap_file([frame]))))[0]
        self.assertEqual(flow.payload, b"AB")

    def test_ipv6_and_icmp_are_ignored(self):
        v6 = eth(b"\x60" + b"\x00" * 39)
        icmp = eth(ip_packet("10.0.0.9", "10.0.0.5", 1, b"\x08\x00" + b"\x00" * 6))
        flows = list(pcapreader.read_flows(io.BytesIO(pcap_file([v6, icmp]))))
        self.assertEqual(flows, [])


# --------------------------------------------------------------------------
# classify
# --------------------------------------------------------------------------

class SignatureTests(unittest.TestCase):
    CASES = [
        (b"\\gamename\\madden\\final\\", "gamespy"),
        (b"\\heartbeat\\27900\\", "gamespy"),
        (b"\x16\x03\x01\x00\x50....", "tls"),
        (b"GET /x HTTP/1.1\r\n", "http"),
        (b"TXN=Hello\n", "ea"),
        (b"junk DNAS token here", "ps2-dnas"),
        (b"A" * 64, "plaintext-unknown"),
        (b"", "empty"),
        (b"\x01\x02\x03", "short-unknown"),
    ]

    def test_signatures(self):
        for payload, expected in self.CASES:
            self.assertEqual(classify.classify_payload(payload)[0], expected,
                             "payload %r" % payload[:20])

    def test_high_entropy_reads_as_encrypted(self):
        label, evidence = classify.classify_payload(bytes(range(256)))
        self.assertEqual(label, "encrypted/compressed?")
        self.assertIn("entropy", evidence)

    def test_entropy_bounds(self):
        self.assertEqual(classify.shannon_entropy(b""), 0.0)
        self.assertEqual(classify.shannon_entropy(b"\x00" * 100), 0.0)
        self.assertAlmostEqual(classify.shannon_entropy(bytes(range(256))), 8.0, places=6)

    def test_evidence_is_readable_not_repr_escaped(self):
        _label, evidence = classify.classify_payload(b"\\gamename\\x\\")
        self.assertIn("\\gamename\\", evidence)
        self.assertNotIn("\\\\", evidence)


class DirectionTests(unittest.TestCase):
    """Regression: every reply used to invent a second 'server' endpoint on the
    client's ephemeral port, doubling the report with fictitious servers."""

    def _classify(self, frames):
        """Run the real classify_pcap and return the endpoints it reported."""
        with tempfile.NamedTemporaryFile(suffix=".pcap", delete=False) as handle:
            handle.write(pcap_file(frames))
            path = handle.name
        captured = io.StringIO()
        try:
            stdout, sys.stdout = sys.stdout, captured
            try:
                classify.classify_pcap(path)
            finally:
                sys.stdout = stdout
        finally:
            os.unlink(path)
        found = []
        for line in captured.getvalue().splitlines():
            parts = line.split()
            if len(parts) > 2 and parts[0] in ("tcp", "udp") and ":" in parts[1]:
                host, port = parts[1].rsplit(":", 1)
                found.append((host, int(port)))
        return found

    def test_reply_does_not_create_a_second_endpoint(self):
        frames = [
            eth(ip_packet("10.0.0.9", "10.0.0.5", 6, tcp(40000, 28900, b"\\gamename\\x\\"))),
            eth(ip_packet("10.0.0.5", "10.0.0.9", 6, tcp(28900, 40000, b"\\ok\\"))),
        ]
        endpoints = self._classify(frames)
        self.assertEqual(endpoints, [("10.0.0.5", 28900)],
                         "a reply invented a second, fictitious server endpoint")

    def test_syn_settles_direction_even_when_capture_starts_late(self):
        # Server packet first, then the client's SYN: the SYN must win.
        frames = [
            eth(ip_packet("10.0.0.5", "10.0.0.9", 6, tcp(28900, 40000, b"x"))),
            eth(ip_packet("10.0.0.9", "10.0.0.5", 6, tcp(40000, 28900, flags=0x02))),
        ]
        self.assertEqual(self._classify(frames), [("10.0.0.5", 28900)])

    def test_known_port_breaks_the_tie_without_a_syn(self):
        flow = pcapreader.Flow(0.0, "udp", "10.0.0.5", 27900, "10.0.0.9", 50000, b"")
        self.assertEqual(classify._pick_server(flow), ("10.0.0.5", 27900))

    def test_privileged_port_breaks_the_tie(self):
        flow = pcapreader.Flow(0.0, "tcp", "10.0.0.9", 50000, "10.0.0.5", 80, b"")
        self.assertEqual(classify._pick_server(flow), ("10.0.0.5", 80))


class TranscriptTests(unittest.TestCase):
    def _write(self, rows):
        handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        for row in rows:
            handle.write(json.dumps(row) + "\n")
        handle.close()
        return handle.name

    def test_malformed_line_is_skipped_not_fatal(self):
        path = self._write([{"proto": "tcp", "port": 80, "dir": "recv",
                             "hex": b"TXN=x".hex()}])
        with open(path, "a") as handle:
            handle.write("this is not json\n")
        try:
            classify.classify_transcript(path)  # must not raise
        finally:
            os.unlink(path)

    def test_send_rows_are_not_fingerprinted(self):
        # Our own canned reply must never be mistaken for the client's protocol.
        path = self._write([{"proto": "tcp", "port": 80, "dir": "send",
                             "hex": b"\\gamename\\ours\\".hex()}])
        try:
            classify.classify_transcript(path)
        finally:
            os.unlink(path)


# --------------------------------------------------------------------------
# dnsd
# --------------------------------------------------------------------------

class DnsParseTests(unittest.TestCase):
    def test_question_is_parsed(self):
        name, qtype, qclass, _end = dnsd._parse_question(dns_query("easo.ea.com"))
        self.assertEqual((name, qtype, qclass), ("easo.ea.com", 1, 1))

    def test_zero_question_count_is_rejected(self):
        with self.assertRaises(ValueError):
            dnsd._parse_question(dns_query("x.com", qdcount=0))

    def test_compression_pointer_in_question_is_rejected(self):
        bad = struct.pack(">HHHHHH", 1, 0x0100, 1, 0, 0, 0) + b"\xc0\x0c"
        with self.assertRaises(ValueError):
            dnsd._parse_question(bad)

    def test_truncated_message_is_rejected(self):
        with self.assertRaises(ValueError):
            dnsd._parse_question(b"\x00" * 4)


class DnsResponseTests(unittest.TestCase):
    def test_a_record_answer(self):
        resp = dnsd.build_response(dns_query("easo.ea.com"), "10.0.0.5")
        msg_id, flags, qd, an, _ns, _ar = struct.unpack_from(">HHHHHH", resp, 0)
        self.assertEqual(msg_id, 0x1234)
        self.assertTrue(flags & 0x8000, "QR bit must be set on a response")
        self.assertTrue(flags & 0x0400, "AA bit expected")
        self.assertEqual((qd, an), (1, 1))
        self.assertTrue(resp.endswith(socket.inet_aton("10.0.0.5")))

    def test_recursion_desired_is_echoed(self):
        resp = dnsd.build_response(dns_query("x.com"), "10.0.0.5")
        self.assertTrue(struct.unpack_from(">H", resp, 2)[0] & 0x0100)

    def test_nxdomain_when_unresolved(self):
        resp = dnsd.build_response(dns_query("x.com"), None)
        flags, an = struct.unpack_from(">H", resp, 2)[0], struct.unpack_from(">H", resp, 6)[0]
        self.assertEqual(flags & 0x000F, 3)
        self.assertEqual(an, 0)

    def test_non_a_query_gets_noerror_no_answer(self):
        # AAAA must not be NXDOMAIN, or the client may stop trying an A lookup.
        resp = dnsd.build_response(dns_query("x.com", qtype=28), "10.0.0.5")
        flags, an = struct.unpack_from(">H", resp, 2)[0], struct.unpack_from(">H", resp, 6)[0]
        self.assertEqual(flags & 0x000F, 0)
        self.assertEqual(an, 0)

    def test_bad_answer_ip_raises_before_serving(self):
        """Regression: an unusable --ip raised OSError on the first query,
        taking the responder down mid-capture instead of failing at startup."""
        with self.assertRaises(ValueError):
            dnsd.build_response(dns_query("x.com"), "not.an.ip")


class DnsResolveTests(unittest.TestCase):
    def test_exact_match_wins(self):
        self.assertEqual(
            dnsd._resolve("a.ea.com", "9.9.9.9", {"a.ea.com": "1.1.1.1"}), "1.1.1.1")

    def test_parent_domain_matches_subdomain(self):
        """Mapping a domain should catch its hosts; exact-only silently
        NXDOMAINed every subdomain the title actually asked for."""
        self.assertEqual(
            dnsd._resolve("easo.ea.com", None, {"ea.com": "1.1.1.1"}), "1.1.1.1")

    def test_default_ip_is_the_fallback(self):
        self.assertEqual(dnsd._resolve("who.example", "9.9.9.9", {}), "9.9.9.9")

    def test_unmapped_without_default_is_unresolved(self):
        self.assertIsNone(dnsd._resolve("who.example", None, {"ea.com": "1.1.1.1"}))

    def test_case_and_trailing_dot_are_normalised(self):
        self.assertEqual(
            dnsd._resolve("EASO.EA.CoM.", None, {"ea.com": "1.1.1.1"}), "1.1.1.1")

    def test_partial_label_does_not_match(self):
        # "notea.com" must not match a map entry for "ea.com".
        self.assertIsNone(dnsd._resolve("notea.com", None, {"ea.com": "1.1.1.1"}))


class ValidateIpTests(unittest.TestCase):
    def test_dotted_quad_accepted(self):
        self.assertEqual(dnsd.validate_ip("192.168.68.85"), "192.168.68.85")

    def test_short_forms_rejected(self):
        for bad in ("10", "10.1", "10.0.1", "", "nope", "1.2.3.4.5"):
            with self.assertRaises(ValueError, msg="accepted %r" % bad):
                dnsd.validate_ip(bad)


# --------------------------------------------------------------------------
# sinkd
# --------------------------------------------------------------------------

class SinkBindTests(unittest.TestCase):
    def test_no_bindable_port_raises_instead_of_claiming_up(self):
        """Regression: with every bind failing, the sinkhole printed 'up' and
        waited forever -- the capture came back empty with no indication why."""
        with self.assertRaises(sinkd.SinkError) as caught:
            # Port 1 requires root; this test must not be run as root.
            sinkd.serve(tcp_ports=[1], transcript_path=None)
        self.assertIn("could not bind", str(caught.exception))
        self.assertIn("need root", str(caught.exception))

    def test_no_ports_at_all_is_a_value_error(self):
        with self.assertRaises(ValueError):
            sinkd.serve()


class HexdumpTests(unittest.TestCase):
    def test_layout_and_ascii_column(self):
        dump = sinkd.hexdump(b"AB\x00\xff")
        self.assertIn("0000", dump)
        self.assertIn("41 42 00 ff", dump)
        self.assertTrue(dump.rstrip().endswith("AB.."))

    def test_multiple_lines(self):
        self.assertEqual(len(sinkd.hexdump(b"x" * 33).splitlines()), 3)


class TranscriptWriterTests(unittest.TestCase):
    def test_close_latches_and_later_writes_are_dropped(self):
        """Regression: closing under a live daemon thread turned Ctrl-C into a
        traceback on a closed file."""
        handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        path = handle.name
        transcript = sinkd._Transcript(handle)
        transcript.record("tcp", 80, "c:1", "recv", b"one")
        transcript.close()
        transcript.record("tcp", 80, "c:1", "recv", b"two")  # must not raise
        transcript.close()                                    # idempotent
        try:
            with open(path) as reader:
                rows = [json.loads(line) for line in reader if line.strip()]
        finally:
            os.unlink(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(bytes.fromhex(rows[0]["hex"]), b"one")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

class CliTests(unittest.TestCase):
    def test_bad_port_is_a_message_not_a_traceback(self):
        parser = recon_cli.build_parser()
        for argv in (["dns", "--port", "99999"], ["sink", "--tcp", "abc"],
                     ["sink", "--tcp", "0"]):
            with self.assertRaises(SystemExit):
                parser.parse_args(argv)

    def test_map_requires_host_equals_ip(self):
        parser = recon_cli.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["dns", "--map", "noequals"])

    def test_dns_without_ip_or_map_exits_nonzero(self):
        main = recon_cli.main
        self.assertEqual(main(["dns", "--port", "15353"]), 2)

    def test_dns_with_invalid_ip_exits_nonzero(self):
        main = recon_cli.main
        self.assertEqual(main(["dns", "--ip", "1.2.3"]), 2)

    def test_respond_hex_is_validated(self):
        parser = recon_cli.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["sink", "--tcp", "80", "--respond-hex", "zz"])
        args = parser.parse_args(["sink", "--tcp", "80", "--respond-hex", "5c6f6b"])
        self.assertEqual(args.respond_hex, b"\x5c\x6f\x6b")

    def test_classify_missing_file_exits_nonzero(self):
        main = recon_cli.main
        self.assertEqual(main(["classify", "/nonexistent/nope.pcap"]), 1)


class DnsLoggingTests(unittest.TestCase):
    """Regression: the log line dropped the hostname entirely, so two different
    lookups printed identically -- the one fact Phase 1 exists to collect."""

    def test_summary_lists_hostnames_counts_and_answers(self):
        from collections import OrderedDict
        seen = OrderedDict()
        seen["easo.ea.com"] = [3, "10.0.0.5", "A"]
        seen["nfl2k5.2ksports.com"] = [1, "NXDOMAIN", "A"]
        summary = dnsd.format_summary(seen)
        self.assertIn("easo.ea.com", summary)
        self.assertIn("nfl2k5.2ksports.com", summary)
        self.assertIn("x3", summary)
        self.assertIn("NXDOMAIN", summary)
        self.assertIn("2 unique", summary)

    def test_empty_summary_says_the_console_never_asked(self):
        summary = dnsd.format_summary({})
        self.assertIn("no queries seen", summary)
        self.assertIn("DNS setting", summary)

    def test_live_responder_logs_the_hostname_and_persists_it(self):
        """Drive the real responder over a socket and read what it recorded."""
        import threading
        log = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False)
        log.close()
        port = 15399
        stop = {"sock": None}

        def run():
            try:
                dnsd.serve(bind="127.0.0.1", port=port, default_ip="10.0.0.5",
                           log_path=log.name)
            except OSError:
                pass

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            client.settimeout(3)
            deadline = time.time() + 5
            reply = None
            while time.time() < deadline and reply is None:
                try:
                    client.sendto(dns_query("easo.ea.com"), ("127.0.0.1", port))
                    reply, _ = client.recvfrom(4096)
                except socket.timeout:
                    continue
            self.assertIsNotNone(reply, "responder never answered")
            self.assertTrue(reply.endswith(socket.inet_aton("10.0.0.5")))
            # The log is flushed per query, so it is readable while still running.
            deadline = time.time() + 3
            body = ""
            while time.time() < deadline and "easo.ea.com" not in body:
                with open(log.name) as reader:
                    body = reader.read()
                time.sleep(0.05)
            self.assertIn("easo.ea.com", body,
                          "the hostname was not recorded -- the deliverable is lost")
        finally:
            os.unlink(log.name)
            del stop


class SinkConsoleTests(unittest.TestCase):
    def test_large_payload_is_capped_on_console_only(self):
        """A 64 KB read must not push 4096 lines past the operator."""
        capped = sinkd.hexdump(b"x" * (sinkd.CONSOLE_DUMP_LIMIT * 4))
        self.assertGreater(len(capped.splitlines()), 100)  # hexdump itself is uncapped
        buffer = io.StringIO()
        stdout, sys.stdout = sys.stdout, buffer
        try:
            sinkd._log("tcp", 80, "c:1", "recv", b"y" * (sinkd.CONSOLE_DUMP_LIMIT * 4))
        finally:
            sys.stdout = stdout
        printed = buffer.getvalue()
        self.assertIn("more byte(s)", printed)
        expected_lines = sinkd.CONSOLE_DUMP_LIMIT // 16
        self.assertLessEqual(len(printed.splitlines()), expected_lines + 6)


class WeakTokenTests(unittest.TestCase):
    """Regression: four-letter FESL component names matched inside binary and
    encrypted payloads, which would send the investigation the wrong way."""

    def test_component_name_in_text_is_accepted(self):
        label, _ev = classify.classify_payload(b"TYPE=fsys\nSTATE=ok\n" + b" " * 20)
        self.assertEqual(label, "ea")

    def test_component_name_inside_binary_is_not_ea(self):
        blob = bytes(range(60)) + b"acct" + bytes(range(60))
        self.assertNotEqual(classify.classify_payload(blob)[0], "ea")

    def test_strong_token_still_matches_anywhere(self):
        self.assertEqual(classify.classify_payload(b"\x00\x01TXN=Auth")[0], "ea")

    def test_mostly_text_helper(self):
        self.assertTrue(classify._mostly_text(b"hello world\n"))
        self.assertFalse(classify._mostly_text(bytes(range(64))))
        self.assertFalse(classify._mostly_text(b""))


class MisuseTests(unittest.TestCase):
    def test_pcap_passed_as_transcript_is_caught(self):
        """Wrong flag under time pressure must not print a thousand parse
        warnings; it must say what happened."""
        handle = tempfile.NamedTemporaryFile(suffix=".pcap", delete=False)
        handle.write(pcap_file([eth(ip_packet("10.0.0.9", "10.0.0.5", 17,
                                              udp(1, 2, b"x")))]))
        handle.close()
        try:
            self.assertEqual(recon_cli.main(["classify", "--transcript", handle.name]), 2)
        finally:
            os.unlink(handle.name)

    def test_negative_max_is_rejected(self):
        with self.assertRaises(SystemExit):
            recon_cli.build_parser().parse_args(["pcap", "x.pcap", "--max", "-1"])

    def test_greet_hex_is_parsed(self):
        args = recon_cli.build_parser().parse_args(
            ["sink", "--tcp", "80", "--greet-hex", "5c6c635c"])
        self.assertEqual(args.greet_hex, b"\\lc\\")


if __name__ == "__main__":
    unittest.main(verbosity=2)

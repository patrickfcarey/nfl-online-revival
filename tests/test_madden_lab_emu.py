"""Layer 1 of the harness: emulator control over PINE.

Framing is the whole risk here. The reply to a batch carries no tags -- just
results concatenated in request order -- so a client that miscounts one width
gets values that are wrong and still parse, for every read after the mistake.
The same goes for the savestate slot, which the server reads as a single byte:
a slot of 256 becomes 0 and loads some other experiment's world.

And two commands lie by omission. `load_state` answers OK the moment it is
queued, so its test is about what it *sends*, never about what happened; and
`pause` does not exist at all in this build, which must surface as a refusal
rather than as a call that appears to work.

Everything runs against the fake PINE server from `test_pine`, so none of this
needs the rig.
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_pine import FakePine  # noqa: E402
from tools import pine  # noqa: E402
from tools.madden_lab import emu  # noqa: E402


class Connected(unittest.TestCase):
    """A live-ish emulator on a socket, with replies scripted per test."""

    def setUp(self):
        self.server = FakePine()
        self.addCleanup(self.server.stop)

    def emu(self, **kwargs):
        machine = emu.Emu(self.server.path, timeout=5, **kwargs)
        self.addCleanup(machine.close)
        return machine


class Memory(Connected):
    def test_a_read_is_the_plain_pine_framing(self):
        self.server.replies = [struct.pack("<I", 0x24050032)]
        self.assertEqual(self.emu().read(0x0019BD7C), 0x24050032)
        self.assertEqual(self.server.requests[0],
                         struct.pack("<BI", pine.READ32, 0x0019BD7C))

    def test_writes_are_refused_unless_asked_for(self):
        # Read-only by default is a non-negotiable, not a convenience: an
        # undeclared poke makes every row in the run unreproducible.
        with self.assertRaises(emu.ReadOnly):
            self.emu().write(0x00600E48, 1)
        self.assertEqual(self.server.requests, [])

    def test_a_writable_connection_writes(self):
        self.emu(writable=True).write(0x00600E48, 0x1234)
        self.assertEqual(self.server.requests[0],
                         struct.pack("<BI", pine.WRITE32, 0x00600E48)
                         + struct.pack("<I", 0x1234))

    def test_a_batch_is_one_message_of_commands_end_to_end(self):
        self.server.replies = [struct.pack("<III", 1, 2, 3)]
        values = self.emu().read_many([(0x100, 4), (0x104, 4), (0x108, 4)])
        self.assertEqual(values, [1, 2, 3])
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.server.requests[0],
                         struct.pack("<BI", pine.READ32, 0x100)
                         + struct.pack("<BI", pine.READ32, 0x104)
                         + struct.pack("<BI", pine.READ32, 0x108))

    def test_a_batch_splits_the_reply_by_the_widths_it_asked_for(self):
        # Mixed widths are where an off-by-one turns into plausible nonsense.
        self.server.replies = [b"\x7f" + struct.pack("<H", 0x1234)
                               + struct.pack("<I", 0xDEADBEEF)
                               + struct.pack("<Q", 0x1122334455667788)]
        values = self.emu().read_many(
            [(0x100, 1), (0x101, 2), (0x104, 4), (0x108, 8)])
        self.assertEqual(values, [0x7F, 0x1234, 0xDEADBEEF,
                                  0x1122334455667788])
        self.assertEqual(self.server.requests[0][:1],
                         bytes([pine.READ8]))
        self.assertEqual(self.server.requests[0][5:6],
                         bytes([pine.READ16]))

    def test_a_bad_width_is_refused_before_anything_is_sent(self):
        with self.assertRaises(ValueError):
            self.emu().read_many([(0x100, 4), (0x104, 3)])
        self.assertEqual(self.server.requests, [])

    def test_read_bytes_takes_one_round_trip_not_one_per_word(self):
        # The point of the override. A 512-byte struct was 128 round trips.
        self.server.replies = [struct.pack("<II", 0x44434241, 0x48474645)]
        self.assertEqual(self.emu().read_bytes(0x100, 8), b"ABCDEFGH")
        self.assertEqual(len(self.server.requests), 1)

    def test_read_bytes_trims_to_the_requested_length(self):
        self.server.replies = [struct.pack("<II", 0x44434241, 0x48474645)]
        self.assertEqual(self.emu().read_bytes(0x100, 6), b"ABCDEF")

    def test_read_bytes_of_nothing_asks_for_nothing(self):
        self.assertEqual(self.emu().read_bytes(0x100, 0), b"")
        self.assertEqual(self.server.requests, [])


class Batching(unittest.TestCase):
    """The split has to happen client-side, because the server will not say."""

    def test_reads_are_split_before_the_reply_ceiling(self):
        specs = [(0x100 + 8 * i, 8) for i in range(200000)]
        batches = list(emu._batches(specs))
        self.assertGreater(len(batches), 1)
        self.assertEqual(sum(len(b) for b in batches), len(specs))
        self.assertEqual([s for b in batches for s in b], specs)

    def test_no_batch_can_trip_either_ceiling(self):
        specs = [(0x100 + 8 * i, 8) for i in range(200000)]
        for batch in emu._batches(specs):
            request = emu._REQUEST_HEADER + emu._READ_COST * len(batch)
            reply = emu._REPLY_HEADER + sum(size for _a, size in batch)
            self.assertLessEqual(request, emu.MAX_REQUEST_BYTES)
            # The server's own test is `>=`, so equality already fails.
            self.assertLess(reply, emu.MAX_REPLY_BYTES)

    def test_a_batch_that_fits_is_not_split(self):
        specs = [(0x100 + 4 * i, 4) for i in range(40)]
        self.assertEqual(list(emu._batches(specs)), [specs])


class State(Connected):
    def test_status_is_named_not_numbered(self):
        self.server.replies = [struct.pack("<I", 1)]
        self.assertIs(self.emu().status(), emu.Status.PAUSED)

    def test_shutdown_is_a_running_emulator_with_no_game(self):
        self.server.replies = [struct.pack("<I", 2)]
        self.assertIs(self.emu().status(), emu.Status.SHUTDOWN)

    def test_an_unknown_status_is_not_guessed_at(self):
        self.server.replies = [struct.pack("<I", 7)]
        with self.assertRaises(pine.PineError) as caught:
            self.emu().status()
        self.assertIn("status 7", str(caught.exception))

    def test_save_state_sends_the_opcode_and_a_slot_byte(self):
        self.emu().save_state(1)
        self.assertEqual(self.server.requests[0],
                         struct.pack("<BB", pine.SAVESTATE, 1))

    def test_load_state_sends_the_opcode_and_a_slot_byte(self):
        self.emu().load_state(3)
        self.assertEqual(self.server.requests[0],
                         struct.pack("<BB", pine.LOADSTATE, 3))

    def test_an_out_of_range_slot_never_reaches_the_wire(self):
        # The server truncates to a byte, so 256 would load slot 0 -- some
        # other experiment's world -- and report success.
        machine = self.emu()
        for slot in (-1, 256, 1000):
            with self.assertRaises(ValueError):
                machine.load_state(slot)
        self.assertEqual(self.server.requests, [])

    def test_pause_refuses_rather_than_pretending(self):
        with self.assertRaises(emu.UnsupportedCommand) as caught:
            self.emu().pause()
        self.assertIn("no pause command", str(caught.exception))
        self.assertEqual(self.server.requests, [])

    def test_resume_refuses_rather_than_pretending(self):
        with self.assertRaises(emu.UnsupportedCommand):
            self.emu().resume()

    def test_a_hook_is_the_only_way_to_pause(self):
        calls = []
        machine = self.emu(pause_hook=lambda: calls.append("pause"),
                           resume_hook=lambda: calls.append("resume"))
        machine.pause()
        machine.resume()
        self.assertEqual(calls, ["pause", "resume"])
        self.assertEqual(self.server.requests, [])

    def test_wait_until_returns_once_the_condition_holds(self):
        seen = []

        def settled(_machine):
            seen.append(1)
            return len(seen) >= 3

        self.emu().wait_until(settled, timeout=5, interval=0)
        self.assertEqual(len(seen), 3)

    def test_wait_until_gives_up_loudly(self):
        with self.assertRaises(emu.EmuTimeout):
            self.emu().wait_until(lambda _m: False, timeout=0.05, interval=0)


class Identity(Connected):
    def test_the_crc_comes_back_as_text_and_is_parsed(self):
        self.server.replies = [struct.pack("<I", 9) + b"14f8b841\x00"]
        self.assertEqual(self.emu().game_crc(), emu.EXPECTED_CRC)
        self.assertEqual(self.server.requests[0], bytes([pine.GAME_UUID]))

    def test_a_crc_that_is_not_hex_is_an_error_not_a_zero(self):
        self.server.replies = [struct.pack("<I", 5) + b"none\x00"]
        with self.assertRaises(pine.PineError) as caught:
            self.emu().game_crc()
        self.assertIn("not hex", str(caught.exception))

    def test_require_crc_passes_the_expected_build(self):
        self.server.replies = [struct.pack("<I", 9) + b"14f8b841\x00"]
        self.assertEqual(self.emu().require_crc(), emu.EXPECTED_CRC)

    def test_require_crc_names_what_it_found_instead(self):
        self.server.replies = [struct.pack("<I", 9) + b"deadbeef\x00",
                               struct.pack("<I", 11) + b"SLUS-20919\x00",
                               struct.pack("<I", 8) + b"Madden 05\x00"]
        with self.assertRaises(emu.WrongGame) as caught:
            self.emu().require_crc()
        message = str(caught.exception)
        self.assertIn("14F8B841", message)
        self.assertIn("DEADBEEF", message)
        self.assertIn("SLUS-20919", message)

    def test_title_passes_through(self):
        self.server.replies = [struct.pack("<I", 12) + b"Madden 2004\x00"]
        self.assertEqual(self.emu().title(), "Madden 2004")


class Failures(unittest.TestCase):
    def test_a_missing_socket_says_what_to_check(self):
        machine = emu.Emu("/nonexistent/pcsx2.sock")
        with self.assertRaises(emu.EmuNotRunning) as caught:
            machine.connect()
        self.assertIn("EnablePINE", str(caught.exception))

    def test_a_stale_socket_is_named_as_a_crash_not_a_mystery(self):
        # A file where the socket should be: what a crashed emulator leaves,
        # because PCSX2 unlinks it only on a clean shutdown.
        path = os.path.join(tempfile.mkdtemp(), "pcsx2.sock")
        with open(path, "wb"):
            pass
        machine = emu.Emu(path)
        self.addCleanup(machine.close)
        with self.assertRaises(emu.EmuNotRunning) as caught:
            machine.read(0x100)
        message = str(caught.exception)
        self.assertIn("crashed", message)
        self.assertIn(path, message)

    def test_the_failure_survives_the_lazy_connect(self):
        # Pine reconnects by itself on first use; without going through our
        # own connect() the caller would get the bare socket error back.
        machine = emu.Emu("/nonexistent/pcsx2.sock")
        with self.assertRaises(emu.EmuNotRunning):
            machine.status()

    def test_a_rejected_batch_raises(self):
        server = FakePine(result=0xFF)
        self.addCleanup(server.stop)
        machine = emu.Emu(server.path, timeout=5)
        self.addCleanup(machine.close)
        with self.assertRaises(pine.PineError) as caught:
            machine.read_many([(0x100, 4), (0x104, 4)])
        self.assertIn("0xff", str(caught.exception))

    def test_a_short_batch_reply_is_caught_not_silently_padded(self):
        # Two words asked for, one word answered. Trusting the length would
        # hand back one real value and one from beyond the buffer.
        server = FakePine(replies=[struct.pack("<I", 1)])
        self.addCleanup(server.stop)
        machine = emu.Emu(server.path, timeout=5)
        self.addCleanup(machine.close)
        with self.assertRaises(pine.PineError) as caught:
            machine.read_many([(0x100, 4), (0x104, 4)])
        self.assertIn("short of", str(caught.exception))

    def test_a_connection_closed_mid_reply_raises(self):
        server = FakePine(replies=[struct.pack("<I", 1)])
        server.drop_after = 6
        self.addCleanup(server.stop)
        machine = emu.Emu(server.path, timeout=5)
        self.addCleanup(machine.close)
        with self.assertRaises(pine.PineError) as caught:
            machine.read(0x100)
        self.assertIn("closed the connection", str(caught.exception))

    def test_the_context_manager_closes_the_socket(self):
        server = FakePine(replies=[struct.pack("<I", 0)])
        self.addCleanup(server.stop)
        with emu.Emu(server.path, timeout=5) as machine:
            machine.status()
            self.assertIsNotNone(machine.pine.sock)
        self.assertIsNone(machine.pine.sock)


if __name__ == "__main__":
    unittest.main()

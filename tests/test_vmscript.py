"""The situational-policy bytecode disassembler (disc asset #69).

Every rule tested here was read out of the PS2 interpreter at `0x0024bfc0` and
its helpers, so the fixtures are hand-assembled bytes rather than slices of the
asset: the game data is not in the repository, and a fixture built from the
asset would only prove the decoder agrees with itself.

The three encodings that are easy to get backwards, and cost the most if they
are, each get a test: branch offsets are signed big-endian and relative to the
*opcode* byte (the interpreter adds them to opcode+1 then subtracts one at use,
`0x0024c070` / `0x0024c2a8`); operands are either a one-byte variable reference
or a two-byte 14-bit signed immediate (`0x0024bea0`); and END payloads are
little-endian, unlike everything else in the file (`0x0024c2d4`).

The last test runs the real asset if it happens to be extracted, and asserts
what the investigation measured: full recursive-descent coverage from the ten
header entry points, with nothing undecodable.
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import vmscript                                 # noqa: E402

ASSET = Path(__file__).resolve().parent.parent / "extract" / "asset69_ps2.bin"


def header(*offsets: int) -> bytes:
    """Ten big-endian entry offsets, the layout 0x0024bf10 relocates."""
    entries = list(offsets) + [0] * (vmscript.HEADER_ENTRIES - len(offsets))
    return struct.pack(">%dI" % vmscript.HEADER_ENTRIES, *entries)


class Values(unittest.TestCase):
    def test_variable_reference_is_one_byte(self):
        value = vmscript.read_value(b"\x86", 0)
        self.assertEqual((value.kind, value.n, value.size), ("var", 6, 1))

    def test_top_variable_index_is_63(self):
        # 0x0024bea0 masks with 0x7f but only reaches here when the two high
        # bits are 10, so 0xbf is the last variable and 0xc0 is not one.
        self.assertEqual(vmscript.read_value(b"\xbf", 0).n, 0x3F)
        self.assertEqual(vmscript.read_value(b"\xc0\x00", 0).kind, "imm")

    def test_immediate_is_big_endian_and_14_bit_signed(self):
        self.assertEqual(vmscript.read_value(b"\x01\x2c", 0).n, 300)
        self.assertEqual(vmscript.read_value(b"\x00\x78", 0).n, 120)
        # bit 0x2000 set -> negative (`andi v0, a1, 0x2000` at 0x0024bee0)
        self.assertEqual(vmscript.read_value(b"\x3f\xff", 0).n, -1)
        self.assertEqual(vmscript.read_value(b"\x3f\xf4", 0).n, -12)
        self.assertEqual(vmscript.read_value(b"\x20\x00", 0).n, -8192)


class Statements(unittest.TestCase):
    def test_if_reads_n_triples_and_a_relative_else(self):
        # 12 00 0d  V4 < 3  V5 >= 15
        code = bytes.fromhex("12000d84020003850500 0f".replace(" ", ""))
        st = vmscript.decode(code, 0)
        self.assertEqual(st.op, 1)
        self.assertEqual(st.n, 2)
        self.assertEqual(st.size, 11)
        self.assertIn("<", st.text)
        self.assertIn(">=", st.text)
        # the else target is relative to the opcode byte, not to the operands
        self.assertIn("-> 000d", st.text)
        self.assertEqual(st.succ, [11, 13])

    def test_negative_branch_offsets_go_backwards(self):
        code = bytes(0x40) + bytes.fromhex("11ffc084010001")
        st = vmscript.decode(code, 0x40)
        self.assertEqual(st.succ, [0x47, 0x00])

    def test_end_payload_is_little_endian(self):
        # op4 carries one byte, op5 two, low byte first (0x0024c2d4).
        self.assertEqual(vmscript.decode(b"\x40\x22", 0).text, "END result=34 (exit 1)")
        self.assertEqual(vmscript.decode(b"\x50\xfe\x00", 0).text,
                         "END result=254 (exit 2)")
        self.assertEqual(vmscript.decode(b"\x50\x01\x01", 0).text,
                         "END result=257 (exit 2)")

    def test_switch_header_carries_join_and_default(self):
        code = bytes.fromhex("248600100020")
        st = vmscript.decode(code, 0)
        self.assertEqual((st.op, st.n, st.size), (2, 4, 6))
        self.assertEqual(st.join, 0x10)
        self.assertIn("default -> 0020", st.text)

    def test_case_label_with_a_literal_value(self):
        st = vmscript.decode(bytes.fromhex("6000040012"), 0)
        self.assertEqual(st.op, 6)
        self.assertEqual(st.size, 5)
        self.assertIn("CASE 4", st.text)
        self.assertEqual(st.succ, [5, 0x12])

    def test_case_label_that_asks_the_engine_for_a_play(self):
        # 0x40..0x7f and 0xc0..0xff are intercepted before the operand reader
        # (0x0024c200) and become handler cmd11 -> the selector 0x00249498.
        own = vmscript.decode(bytes.fromhex("60490004"), 0)
        self.assertEqual(own.size, 4)
        self.assertIn("SELECT_PLAY", own.text)
        self.assertIn("side=US", own.text)
        self.assertIn("flag=0x09", own.text)
        # (b & 0xc0) == 0xc0 flips the side: cmd11 does `xori v0, s0, 1`.
        other = vmscript.decode(bytes.fromhex("60c90004"), 0)
        self.assertIn("side=THEM", other.text)
        self.assertIn("flag=0x09", other.text)

    def test_opcodes_above_seven_are_rejected(self):
        # the interpreter bound-checks `sltiu v0, a0, 8` at 0x0024c018.
        with self.assertRaises(ValueError):
            vmscript.decode(b"\x80\x00", 0)


class Walk(unittest.TestCase):
    def test_recursive_descent_follows_both_arms_and_the_case_chain(self):
        body = (bytes.fromhex("11000984020003")     # 00: IF V4 < 3 else -> 09
                + bytes.fromhex("4022")             # 07: END 34
                + bytes.fromhex("4009"))            # 09: END 9
        blob = header(0x38) + bytes(0x38 - len(header())) + body
        stmts = vmscript.walk(blob)
        self.assertEqual(sorted(stmts), [0x38, 0x3F, 0x41])
        self.assertEqual(stmts[0x3F].text, "END result=34 (exit 1)")
        self.assertEqual(stmts[0x41].text, "END result=9 (exit 1)")

    def test_listing_marks_entry_points(self):
        blob = header(0x38) + bytes(0x38 - len(header())) + b"\x40\x09"
        self.assertIn("=== ENTRY 0 ===", vmscript.listing(blob))


@unittest.skipUnless(ASSET.exists(), "extract/asset69_ps2.bin not present")
class RealAsset(unittest.TestCase):
    """What the investigation measured, so a decoder change cannot quietly
    lose coverage of the shipped script."""

    @classmethod
    def setUpClass(cls):
        cls.blob = ASSET.read_bytes()
        cls.stmts = vmscript.walk(cls.blob)

    def test_size_and_header(self):
        self.assertEqual(len(self.blob), 28301)
        self.assertEqual(vmscript.entry_points(self.blob)[:3], [0x38, 0x5016, 0x5018])

    def test_every_statement_decodes(self):
        bad = [s for s in self.stmts.values() if s.op == -1]
        self.assertEqual(bad, [])

    def test_coverage_is_essentially_complete(self):
        covered = sum(s.size for s in self.stmts.values())
        # 176 bytes are unreachable one-byte padding between case chains.
        self.assertGreaterEqual(covered, len(self.blob) - 240)

    def test_the_fourth_down_block_is_where_the_investigation_found_it(self):
        # Q4 (entry 0, `SWITCH V6` case 4) -> `SWITCH V3` case 4 -> tied game,
        # field-goal fringe: go only on 4th-and-1.
        self.assertIn("SWITCH V3(down)", self.stmts[0x21BA].text)
        self.assertEqual(self.stmts[0x2352].text,
                         "IF V4(togo) < 2 ELSE -> 235b")
        self.assertEqual(self.stmts[0x2359].text, "END result=34 (exit 1)")
        self.assertEqual(self.stmts[0x235B].text, "END result=9 (exit 1)")


if __name__ == "__main__":
    unittest.main()

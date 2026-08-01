"""The disassembler that underwrites the documentation.

Every address, opcode and branch condition cited in `docs/ea-protocol.md`,
`docs/lobby-and-matchmaking.md` and the rest was read out of this module. A bug
here does not produce a crash or a wrong-looking listing -- it produces a
plausible one, and the wrong conclusion is then written down as fact and built
on. That has already happened once on this project: a disassembler written by
another tool had BEQL at 0x13 instead of 0x14, which silently inverts every
branch-likely condition it prints.

So these tests are less about the code being right today -- it is; each case
below was checked by hand against the MIPS IV manual before being written down
-- and more about it not drifting later without anyone noticing.

The R5900 traps, all of which have cost this project time:

* **BEQL is 0x14, not 0x13**, and its delay slot executes only when taken.
* **`movz`/`movn` are conditional.** Read as an unconditional move, the address
  override at 0x004deb58 looks like an assignment rather than a condition.
* **`mult` has a three-operand form** that writes rd as well as HI/LO. Printing
  only rs and rt hides the write and makes a multiply look like it discards its
  result.
* **The low half of a lui/addiu pair is sign-extended**, so the high half is
  adjusted when the low half has bit 15 set. Getting that wrong is the usual
  reason an address search finds nothing.
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recon import mipsdis  # noqa: E402


def i_type(op, rs, rt, imm):
    return (op << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)


def r_type(rs, rt, rd, funct, shamt=0):
    return (rs << 21) | (rt << 16) | (rd << 11) | (shamt << 6) | funct


def j_type(op, target):
    return (op << 26) | ((target >> 2) & 0x03FFFFFF)


#: Register numbers used below, by the names the listing prints.
ZERO, AT, V0, V1, A0, A1, A2, A3 = range(8)
S0 = 16


class BranchLikely(unittest.TestCase):
    """The off-by-one that inverts every condition it prints."""

    def test_the_likely_opcodes_are_the_canonical_ones(self):
        self.assertEqual(mipsdis._LIKELY,
                         {0x14: "beql", 0x15: "bnel",
                          0x16: "blezl", 0x17: "bgtzl"})

    def test_beql_is_0x14(self):
        text = mipsdis.disassemble(i_type(0x14, S0, V0, 1), 0x00400000)
        self.assertTrue(text.startswith("beql "), text)

    def test_0x13_is_not_a_branch_at_all(self):
        # The encoding the broken disassembler called beql. It is unassigned
        # here, and printing it as .word is the honest answer.
        text = mipsdis.disassemble(i_type(0x13, S0, V0, 1), 0x00400000)
        self.assertTrue(text.startswith(".word"), text)
        self.assertNotIn("beql", text)

    def test_every_likely_form_is_marked(self):
        # A reader who forgets that the delay slot is conditional will
        # mis-attribute whatever it does.
        for op, name in mipsdis._LIKELY.items():
            text = mipsdis.disassemble(i_type(op, S0, V0, 1), 0x00400000)
            self.assertIn(name, text)
            self.assertIn("; likely", text, name)

    def test_the_ordinary_forms_are_not_marked(self):
        for op in (0x04, 0x05, 0x06, 0x07):
            text = mipsdis.disassemble(i_type(op, S0, V0, 1), 0x00400000)
            self.assertNotIn("likely", text, text)


class BranchTargets(unittest.TestCase):
    """PC-relative from the delay slot, and signed."""

    def test_a_forward_branch(self):
        text = mipsdis.disassemble(i_type(0x04, S0, V0, 4), 0x00400000)
        self.assertIn("0x00400014", text)          # 0x400000 + 4 + 4*4

    def test_a_backward_branch_sign_extends(self):
        # -3 words back. An unsigned read lands ~256 KB forward instead, which
        # is a plausible-looking address in a 1 MB image.
        text = mipsdis.disassemble(i_type(0x04, S0, V0, 0xFFFD), 0x00400000)
        self.assertIn("0x003ffff8", text)          # 0x400000 + 4 - 12

    def test_likely_branches_use_the_same_arithmetic(self):
        ordinary = mipsdis.disassemble(i_type(0x04, S0, V0, 0xFFFD), 0x00400000)
        likely = mipsdis.disassemble(i_type(0x14, S0, V0, 0xFFFD), 0x00400000)
        self.assertIn("0x003ffff8", ordinary)
        self.assertIn("0x003ffff8", likely)

    def test_one_operand_branches_print_only_rs(self):
        text = mipsdis.disassemble(i_type(0x06, S0, 0, 4), 0x00400000)
        self.assertEqual(text, "blez s0, 0x00400014")

    def test_jump_targets_take_the_region_from_the_delay_slot(self):
        text = mipsdis.disassemble(j_type(0x03, 0x00446ce0), 0x00400000)
        self.assertEqual(text, "jal 0x00446ce0")
        self.assertEqual(mipsdis.disassemble(j_type(0x02, 0x004df098), 0),
                         "j 0x004df098")


class ConditionalMoves(unittest.TestCase):
    """`movz`/`movn` write rd only on a condition."""

    def test_both_are_decoded(self):
        self.assertEqual(mipsdis._SPECIAL[0x0A], "movz")
        self.assertEqual(mipsdis._SPECIAL[0x0B], "movn")

    def test_all_three_registers_are_shown(self):
        # The address override at 0x004deb58 is `movn s0, v0, v0` -- read as a
        # plain move, it looks unconditional and the +ses address bug becomes
        # invisible.
        self.assertEqual(mipsdis.disassemble(r_type(V0, V0, S0, 0x0B)),
                         "movn s0, v0, v0")
        self.assertEqual(mipsdis.disassemble(r_type(V0, V1, S0, 0x0A)),
                         "movz s0, v0, v1")


class Multiply(unittest.TestCase):
    """The R5900 three-operand form writes rd as well as HI/LO."""

    def test_rd_is_shown_when_present(self):
        self.assertEqual(mipsdis.disassemble(r_type(A0, A1, A2, 0x18)),
                         "mult a2, a0, a1")

    def test_the_two_operand_form_stays_two_operand(self):
        self.assertEqual(mipsdis.disassemble(r_type(A0, A1, ZERO, 0x18)),
                         "mult a0, a1")

    def test_the_same_holds_for_the_other_hi_lo_writers(self):
        for funct, name in ((0x19, "multu"), (0x1A, "div"), (0x1B, "divu")):
            self.assertEqual(mipsdis.disassemble(r_type(A0, A1, A2, funct)),
                             "%s a2, a0, a1" % name)


class Immediates(unittest.TestCase):
    def test_lui_prints_its_immediate_unsigned(self):
        self.assertEqual(mipsdis.disassemble(i_type(0x0F, 0, V0, 0x8004)),
                         "lui v0, 0x8004")

    def test_addiu_prints_its_immediate_signed(self):
        self.assertEqual(mipsdis.disassemble(i_type(0x09, ZERO, A3, 10000)),
                         "addiu a3, zero, 10000")
        self.assertEqual(mipsdis.disassemble(i_type(0x09, S0, S0, 0xFFF8)),
                         "addiu s0, s0, -8")

    def test_logical_immediates_are_unsigned(self):
        # andi/ori/xori zero-extend; printing them signed would misread a mask.
        self.assertEqual(mipsdis.disassemble(i_type(0x0C, V0, V0, 0xFFFF)),
                         "andi v0, v0, 0xffff")
        self.assertEqual(mipsdis.disassemble(i_type(0x0D, V0, V0, 0x0101)),
                         "ori v0, v0, 0x0101")

    def test_load_and_store_offsets_are_signed(self):
        self.assertEqual(mipsdis.disassemble(i_type(0x25, S0, V0, 0)),
                         "lhu v0, 0(s0)")
        self.assertEqual(mipsdis.disassemble(i_type(0x23, mipsdis._REGS.index("sp"),
                                                    mipsdis._REGS.index("ra"), 0xFFFC)),
                         "lw ra, -4(sp)")

    def test_unaligned_loads_are_decoded_rather_than_left_as_word(self):
        for op, name in mipsdis._UNALIGNED.items():
            text = mipsdis.disassemble(i_type(op, S0, V0, 4))
            self.assertTrue(text.startswith(name + " "), text)


class Rendering(unittest.TestCase):
    def test_zero_is_nop(self):
        self.assertEqual(mipsdis.disassemble(0), "nop")

    def test_an_unknown_encoding_is_word_not_a_guess(self):
        # Inventing a mnemonic for an encoding we do not know is how a wrong
        # reading enters the documentation. 0x1E is unassigned in this table;
        # 0x3F is not -- it is `sd`, which is why picking a "clearly invalid"
        # word by eye is a poor way to test this.
        self.assertTrue(mipsdis.disassemble(0x1E << 26).startswith(".word"))
        self.assertTrue(mipsdis.disassemble(r_type(0, 0, 0, 0x3D)).startswith(".word"))

    def test_every_table_entry_actually_decodes(self):
        # Guards the inverse: an entry that exists but falls through to .word
        # because its name never matched a rendering branch.
        for op in mipsdis._OPCODES:
            self.assertFalse(
                mipsdis.disassemble(i_type(op, S0, V0, 4)).startswith(".word"),
                "opcode 0x%02x is in the table but renders as .word" % op)
        for funct in mipsdis._SPECIAL:
            word = r_type(A0, A1, A2, funct, shamt=1)
            self.assertFalse(mipsdis.disassemble(word).startswith(".word"),
                             "SPECIAL 0x%02x renders as .word" % funct)

    def test_register_names_are_the_standard_o32_set(self):
        self.assertEqual(mipsdis._REGS[0], "zero")
        self.assertEqual(mipsdis._REGS[2], "v0")
        self.assertEqual(mipsdis._REGS[4], "a0")
        self.assertEqual(mipsdis._REGS[16], "s0")
        self.assertEqual(mipsdis._REGS[29], "sp")
        self.assertEqual(mipsdis._REGS[31], "ra")
        self.assertEqual(len(mipsdis._REGS), 32)

    def test_shifts_print_rt_then_the_amount(self):
        self.assertEqual(mipsdis.disassemble(r_type(0, V0, V1, 0x00, shamt=2)),
                         "sll v1, v0, 2")


def _elf(words, vaddr=0x00100000):
    """A minimal ELF32 with one PT_LOAD, matching the game's own shape."""
    body = b"".join(struct.pack("<I", w) for w in words)
    ph_off, ph_size = 52, 32
    header = bytearray(52)
    header[0:4] = b"\x7fELF"
    header[4] = 1                                   # ELF32
    header[5] = 1                                   # little endian
    struct.pack_into("<I", header, 24, vaddr)       # e_entry
    struct.pack_into("<I", header, 28, ph_off)
    struct.pack_into("<H", header, 42, ph_size)
    struct.pack_into("<H", header, 44, 1)
    body_off = ph_off + ph_size
    ph = bytearray(ph_size)
    struct.pack_into("<I", ph, 0, 1)                # PT_LOAD
    struct.pack_into("<I", ph, 4, body_off)
    struct.pack_into("<I", ph, 8, vaddr)
    struct.pack_into("<I", ph, 16, len(body))       # p_filesz
    handle, path = tempfile.mkstemp(suffix=".elf")
    os.write(handle, bytes(header) + bytes(ph) + body)
    os.close(handle)
    return path


class ElfMapping(unittest.TestCase):
    def setUp(self):
        self.path = _elf([0, 1, 2, 3])
        self.elf = mipsdis.Elf32(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_addresses_map_both_ways(self):
        offset = self.elf.offset_of(0x00100008)
        self.assertIsNotNone(offset)
        self.assertEqual(self.elf.vaddr_of(offset), 0x00100008)

    def test_addresses_outside_the_segment_are_none(self):
        self.assertIsNone(self.elf.offset_of(0x00000000))
        self.assertIsNone(self.elf.word(0x0FFFFFFF))

    def test_words_are_read_little_endian(self):
        self.assertEqual(self.elf.word(0x00100004), 1)
        self.assertEqual([w for _v, w in self.elf.words()], [0, 1, 2, 3])

    def test_a_non_elf_file_is_refused(self):
        handle, path = tempfile.mkstemp()
        os.write(handle, b"not an elf at all")
        os.close(handle)
        try:
            with self.assertRaises(ValueError):
                mipsdis.Elf32(path)
        finally:
            os.unlink(path)


class AddressSearch(unittest.TestCase):
    """lui/addiu pairs, including the sign-extension adjustment."""

    def _find(self, words, target):
        path = _elf(words)
        try:
            return mipsdis.find_address_refs(mipsdis.Elf32(path), target)
        finally:
            os.unlink(path)

    def test_a_pair_with_a_positive_low_half(self):
        # 0x00446ce0 -> lo 0x6ce0 (bit 15 clear), so hi is unadjusted.
        hits = self._find([i_type(0x0F, 0, V0, 0x0044),
                           i_type(0x09, V0, V0, 0x6ce0)], 0x00446ce0)
        self.assertEqual(len(hits), 1, hits)

    def test_a_pair_with_a_negative_low_half_needs_the_adjustment(self):
        """The usual reason an address search finds nothing.

        0x004deb58 has lo 0xeb58, bit 15 set, so addiu sign-extends and the
        assembler emits hi+1 -- 0x004e, not 0x004d. A search for the unadjusted
        half matches nothing while looking perfectly reasonable.
        """
        hits = self._find([i_type(0x0F, 0, V0, 0x004e),
                           i_type(0x09, V0, V0, 0xeb58)], 0x004deb58)
        self.assertEqual(len(hits), 1, hits)

    def test_the_unadjusted_half_with_addiu_is_not_a_hit(self):
        # The other side of the same rule: 0x004d/addiu would actually
        # materialise 0x004ceb58, and matching it would be a false positive.
        hits = self._find([i_type(0x0F, 0, V0, 0x004d),
                           i_type(0x09, V0, V0, 0xeb58)], 0x004deb58)
        self.assertEqual(hits, [])

    def test_ori_does_not_get_the_adjustment(self):
        # ori zero-extends, so the high half is the plain one.
        hits = self._find([i_type(0x0F, 0, V0, 0x004d),
                           i_type(0x0D, V0, V0, 0xeb58)], 0x004deb58)
        self.assertEqual(len(hits), 1, hits)

    def test_a_pair_in_different_registers_is_not_a_hit(self):
        hits = self._find([i_type(0x0F, 0, V0, 0x0044),
                           i_type(0x09, V1, V1, 0x6ce0)], 0x00446ce0)
        self.assertEqual(hits, [])

    def test_a_wrong_low_half_is_not_a_hit(self):
        hits = self._find([i_type(0x0F, 0, V0, 0x0044),
                           i_type(0x09, V0, V0, 0x6ce4)], 0x00446ce0)
        self.assertEqual(hits, [])


class CallSearch(unittest.TestCase):
    def test_finds_every_jal_to_a_target(self):
        path = _elf([j_type(0x03, 0x00100010), 0,
                     j_type(0x03, 0x00100010), j_type(0x03, 0x00100020)])
        try:
            elf = mipsdis.Elf32(path)
            self.assertEqual(mipsdis.find_jal_targets(elf, 0x00100010),
                             [0x00100000, 0x00100008])
        finally:
            os.unlink(path)

    def test_a_j_is_not_counted_as_a_call(self):
        path = _elf([j_type(0x02, 0x00100010)])
        try:
            self.assertEqual(
                mipsdis.find_jal_targets(mipsdis.Elf32(path), 0x00100010), [])
        finally:
            os.unlink(path)


class Listing(unittest.TestCase):
    def test_dump_stops_at_the_end_of_the_segment(self):
        path = _elf([0, 0, 0])
        try:
            text = mipsdis.dump(mipsdis.Elf32(path), 0x00100000, count=32)
            self.assertEqual(len(text.splitlines()), 3)
        finally:
            os.unlink(path)

    def test_dump_shows_address_word_and_mnemonic(self):
        path = _elf([i_type(0x09, ZERO, A3, 10000)])
        try:
            line = mipsdis.dump(mipsdis.Elf32(path), 0x00100000, count=1)
            self.assertIn("00100000", line)
            self.assertIn("addiu a3, zero, 10000", line)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()

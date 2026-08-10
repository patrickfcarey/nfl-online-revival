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
* **REGIMM (opcode 0x01) keeps its condition in the rt field.** Left undecoded
  it prints as `.word`, and a gate that prints as nothing reads as no gate.
* **MMI (opcode 0x1C) is a whole second integer pipeline.** A `div1` printed as
  `.word` took a `/115` term out of a formula without leaving a mark.
* **Variable shifts are `rd, rt, rs`**, the reverse of every other
  three-register SPECIAL form. Printed the usual way round, a shift of the
  score by a counter reads as a shift of the counter by the score.

The instruction words used below with no explanatory arithmetic were copied out
of `extract/SLUS_207.52` at the addresses named in their comments. The file is
not in the repository, so they are inlined rather than read back.
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recon import fpudis, mipsdis  # noqa: E402


def i_type(op, rs, rt, imm):
    return (op << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)


def r_type(rs, rt, rd, funct, shamt=0):
    return (rs << 21) | (rt << 16) | (rd << 11) | (shamt << 6) | funct


def mmi_type(rs, rt, rd, funct):
    """SPECIAL's layout under opcode 0x1C, the second integer pipeline."""
    return (0x1C << 26) | r_type(rs, rt, rd, funct)


def j_type(op, target):
    return (op << 26) | ((target >> 2) & 0x03FFFFFF)


#: Register numbers used below, by the names the listing prints.
ZERO, AT, V0, V1, A0, A1, A2, A3 = range(8)
S0 = 16
GP = 28


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


class Regimm(unittest.TestCase):
    """Opcode 0x01, where the condition hides in the rt field."""

    def test_the_four_conditions(self):
        for rt, name in ((0, "bltz"), (1, "bgez"), (2, "bltzl"), (3, "bgezl")):
            text = mipsdis.disassemble(i_type(0x01, S0, rt, 4), 0x00400000)
            self.assertTrue(text.startswith("%s s0, 0x00400014" % name), text)

    def test_the_and_link_forms(self):
        for rt, name in ((0x10, "bltzal"), (0x11, "bgezal")):
            text = mipsdis.disassemble(i_type(0x01, S0, rt, 4), 0x00400000)
            self.assertTrue(text.startswith("%s s0, " % name), text)

    def test_the_target_is_pc_relative_from_the_delay_slot(self):
        # 0x06000004 at 0x001448b8, the shape these gates usually take.
        self.assertEqual(mipsdis.disassemble(0x06000004, 0x001448b8),
                         "bltz s0, 0x001448cc")
        self.assertEqual(mipsdis.disassemble(i_type(0x01, S0, 0, 0xFFFD),
                                             0x00400000),
                         "bltz s0, 0x003ffff8")

    def test_the_likely_members_are_marked(self):
        self.assertIn("; likely", mipsdis.disassemble(i_type(0x01, S0, 2, 4)))
        self.assertNotIn("; likely", mipsdis.disassemble(i_type(0x01, S0, 0, 4)))

    def test_an_unassigned_rt_is_word_not_a_guess(self):
        text = mipsdis.disassemble(i_type(0x01, S0, 8, 4))
        self.assertTrue(text.startswith(".word"), text)


class SecondPipeline(unittest.TestCase):
    """MMI (opcode 0x1C): the EE's second divider, and its own HI1/LO1."""

    def test_a_divide_and_the_read_of_its_result(self):
        # 0x0014ecbc/0x0014ecc0. As two `.word`s this pair is invisible, and a
        # division that is invisible drops a term out of the formula around it.
        self.assertEqual(mipsdis.disassemble(0x7068001A), "div1 v1, t0")
        self.assertEqual(mipsdis.disassemble(0x70001812), "mflo1 v1")

    def test_mult1_keeps_the_three_operand_rule(self):
        self.assertEqual(mipsdis.disassemble(mmi_type(A0, A1, A2, 0x18)),
                         "mult1 a2, a0, a1")
        self.assertEqual(mipsdis.disassemble(mmi_type(A0, A1, ZERO, 0x18)),
                         "mult1 a0, a1")

    def test_the_moves_show_the_register_they_touch(self):
        self.assertEqual(mipsdis.disassemble(mmi_type(0, 0, A0, 0x10)),
                         "mfhi1 a0")
        self.assertEqual(mipsdis.disassemble(mmi_type(A0, 0, 0, 0x13)),
                         "mtlo1 a0")

    def test_the_simd_subtables_stay_word(self):
        # MMI0 (funct 0x08) needs the sa field as a second index. Printing the
        # encoding is honest; guessing a mnemonic for it would not be.
        text = mipsdis.disassemble(mmi_type(A0, A1, A2, 0x08))
        self.assertTrue(text.startswith(".word"), text)


class VariableShifts(unittest.TestCase):
    """`sllv`/`srlv`/`srav` are `rd, rt, rs` -- backwards from the rest."""

    def test_the_value_comes_before_the_count(self):
        # 0x00901007 at 0x00186f68: the value in s0 shifted by the count in a0.
        # Printed the generic way round it reads as the count shifted by the
        # value, which is how a live function came to look like dead code.
        self.assertEqual(mipsdis.disassemble(0x00901007), "srav v0, s0, a0")

    def test_all_six_variable_shifts_use_that_order(self):
        for funct, name in ((0x04, "sllv"), (0x06, "srlv"), (0x07, "srav"),
                            (0x14, "dsllv"), (0x16, "dsrlv"), (0x17, "dsrav")):
            self.assertEqual(mipsdis.disassemble(r_type(A0, A1, A2, funct)),
                             "%s a2, a1, a0" % name)

    def test_the_ordinary_three_register_forms_are_untouched(self):
        # add/or/slt really are `rd, rs, rt`; the exception must stay one.
        for funct, name in ((0x21, "addu"), (0x25, "or"), (0x2A, "slt")):
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


class GpRelative(unittest.TestCase):
    """gp is fixed for this executable, so the slot has a real address."""

    def test_gp_base_is_this_executables_value(self):
        self.assertEqual(mipsdis.GP_BASE, 0x006056F0)

    def test_a_load_through_gp_is_resolved(self):
        # 0x8f83b524 at 0x0014483c. The offset alone names nothing; the
        # resolved address is something find_address_refs can chase.
        self.assertEqual(mipsdis.disassemble(0x8F83B524),
                         "lw v1, -19164(gp)   ; gp-relative 0x00600c14")

    def test_the_address_forming_add_is_resolved_too(self):
        self.assertEqual(mipsdis.disassemble(i_type(0x09, GP, V0, 0x0010)),
                         "addiu v0, gp, 16   ; gp-relative 0x00605700")

    def test_any_other_base_is_left_alone(self):
        self.assertNotIn(";", mipsdis.disassemble(i_type(0x23, S0, V0, 4)))

    def test_the_annotation_can_be_turned_off(self):
        # Another executable has another gp, and a stale annotation would be
        # worse than none at all.
        self.assertEqual(mipsdis.disassemble(0x8F83B524, 0, None),
                         "lw v1, -19164(gp)")


class FloatingPoint(unittest.TestCase):
    """COP1 belongs to fpudis; the default path has to reach it."""

    def test_float_arithmetic_is_not_a_word(self):
        # 0x001448a0 and 0x0014487c, in the middle of ordinary integer code.
        self.assertEqual(mipsdis.disassemble(0x46020842), "mul.s f1, f1, f2")
        self.assertEqual(mipsdis.disassemble(0x46800860), "cvt.s.w f1, f1")

    def test_moves_between_the_two_register_files(self):
        self.assertEqual(mipsdis.disassemble(0x44900800), "mtc1 s0, f1")
        self.assertEqual(mipsdis.disassemble(0x44020000), "mfc1 v0, f0")

    def test_a_float_load_is_annotated_like_any_other(self):
        # 0xc78483ec at 0x00144890 -- float constants live gp-relative too.
        self.assertEqual(mipsdis.disassemble(0xC78483EC),
                         "lwc1 f4, -31764(gp)   ; gp-relative 0x005fdadc")

    def test_the_delegation_stays_one_way(self):
        """fpudis calls back for everything that is not FPU.

        Only these three opcodes may cross, in one direction. Widen the set and
        the two modules call each other until the stack runs out.
        """
        self.assertEqual(mipsdis._COP1, frozenset((0x11, 0x31, 0x39)))
        self.assertEqual(fpudis.dis(i_type(0x09, ZERO, A3, 1)),
                         "addiu a3, zero, 1")


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


class ImmediateSearch(unittest.TestCase):
    """Two sweeps, and the difference that makes one of them unsound."""

    #: A constant reached four ways: as an operand, as a struct offset, as the
    #: high half of an address, and as a branch displacement that only looks
    #: like the others.
    WORDS = [i_type(0x09, ZERO, V0, 100),       # addiu v0, zero, 100
             i_type(0x23, S0, V1, 100),         # lw v1, 100(s0)
             i_type(0x0F, 0, A0, 100),          # lui a0, 0x0064
             i_type(0x04, V0, V1, 100)]         # beq -- 100 is a displacement

    def _sweep(self, finder, words=None, value=100):
        path = _elf(self.WORDS if words is None else words)
        try:
            return [vaddr for vaddr, _ in finder(mipsdis.Elf32(path), value)]
        finally:
            os.unlink(path)

    def test_the_narrow_sweep_sees_only_the_arithmetic_form(self):
        self.assertEqual(self._sweep(mipsdis.find_immediate), [0x00100000])

    def test_the_exhaustive_sweep_sees_the_load_and_the_lui(self):
        # The difference that matters: a struct offset lives in a load, and a
        # sweep that cannot see loads is not exhaustive whatever it is called.
        # Searches described here as exhaustive were run through the narrow one.
        self.assertEqual(self._sweep(mipsdis.find_immediate_all),
                         [0x00100000, 0x00100004, 0x00100008])

    def test_a_branch_displacement_is_not_a_constant(self):
        # Included, it would bury the real hits under every branch of that span.
        self.assertNotIn(0x0010000C, self._sweep(mipsdis.find_immediate_all))

    def test_a_negative_value_matches_the_encoding_it_has(self):
        self.assertEqual(self._sweep(mipsdis.find_immediate_all,
                                     [i_type(0x09, S0, S0, 0xFFF8)], -8),
                         [0x00100000])


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

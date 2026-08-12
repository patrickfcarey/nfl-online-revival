"""Offline tests for the five-axis cave census.

The ELF-backed cases are the regression that matters: they pin the exact two
regions this project cleared as dead and then patched over. If the census ever
calls cave #1 or cave #3 dead again, these fail.
"""
import os
import unittest
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recon.cave_census import (ADDR_LO, CaveVerdict, ImageIndex, Reference,
                               census)
from recon.mipsdis import Elf32

ELF = os.path.join(os.path.dirname(__file__), "..", "extract", "SLUS_207.52")


class FakeElf:
    """A word stream with the same surface `ImageIndex` consumes."""

    def __init__(self, base, words):
        self._base = base
        self._words = list(words)

    def words(self):
        for i, w in enumerate(self._words):
            yield self._base + 4 * i, w


def _index(base, words):
    return ImageIndex(FakeElf(base, words))


NOP = 0x00000000
JR_RA = 0x03E00008


class AxisTest(unittest.TestCase):
    """Each axis, on a handcrafted word stream -- no ELF required."""

    #: 0x00200000 is the notional cave; the stream starts at 0x00100000.
    CAVE = 0x00100040

    #: `jr ra` + delay slot immediately above the cave, so entry is guarded.
    GUARD = 14

    def _stream(self, *, at_index, word):
        words = [NOP] * 32
        words[self.GUARD] = JR_RA
        words[self.GUARD + 1] = NOP
        words[at_index] = word
        return _index(0x00100000, words)

    def test_jal_into_range_is_found(self):
        # jal 0x00100040 -> target encoded as (addr >> 2) in the low 26 bits
        jal = (0x03 << 26) | (self.CAVE >> 2)
        idx = self._stream(at_index=0, word=jal)
        refs = idx.refs_into(self.CAVE, 8)
        self.assertEqual([r.axis for r in refs], ["jal"])
        self.assertEqual(refs[0].target, self.CAVE)

    def test_tail_call_j_is_found(self):
        # The axis a jal-only caller search misses -- two of the four
        # "zero-caller" functions were reached exactly this way.
        j = (0x02 << 26) | (self.CAVE >> 2)
        idx = self._stream(at_index=0, word=j)
        self.assertEqual([r.axis for r in idx.refs_into(self.CAVE, 8)], ["j"])

    def test_branch_into_range_is_found(self):
        # beq zero, zero, +0x38 from 0x00100004 -> 0x00100040
        offset = (self.CAVE - (0x00100004 + 4)) >> 2
        beq = (0x04 << 26) | offset
        idx = self._stream(at_index=1, word=beq)
        self.assertEqual([r.axis for r in idx.refs_into(self.CAVE, 8)],
                         ["branch"])

    def test_regimm_and_cop1_branches_count(self):
        for op_word, label in (((0x01 << 26) | (0 << 16), "REGIMM bltz"),
                               ((0x11 << 26) | (0x08 << 21), "COP1 bc1f")):
            offset = (self.CAVE - (0x00100004 + 4)) >> 2
            idx = self._stream(at_index=1, word=op_word | offset)
            self.assertEqual([r.axis for r in idx.refs_into(self.CAVE, 8)],
                             ["branch"], label)

    def test_data_word_pointer_is_found(self):
        # The vtable / jump-table test -- "the one everybody forgets".
        idx = self._stream(at_index=0, word=self.CAVE)
        self.assertEqual([r.axis for r in idx.refs_into(self.CAVE, 8)],
                         ["word"])

    def test_lui_addiu_pair_at_long_distance(self):
        # 124 bytes apart: inside a 4 KB window, outside the 64-byte window
        # that originally cleared cave #1.
        words = [NOP] * 64
        words[8], words[9] = JR_RA, NOP
        words[0] = (0x0F << 26) | (8 << 16) | 0x0010          # lui t0, 0x0010
        words[31] = (0x09 << 26) | (8 << 21) | (8 << 16) | 0x0040  # addiu
        idx = _index(0x00100000, words)
        refs = idx.refs_into(self.CAVE, 8)
        self.assertEqual([r.axis for r in refs], ["formed"])

    def test_addiu_off_a_reused_base_is_found(self):
        """The axis that missed caves #1 and #3.

        One `lui` establishes a base; a later `addiu rD, rBase, simm` with
        rD != rBase forms an address *without* the pair ever completing to
        it, and leaves the base live for the next one. A pair search for the
        target finds nothing at all.
        """
        words = [NOP] * 64
        words[8], words[9] = JR_RA, NOP
        # lui s0, 0x0011  -> s0 = 0x00110000
        words[0] = (0x0F << 26) | (16 << 16) | 0x0011
        # addiu t0, s0, -0xFFC0 -> 0x00100040, and s0 survives
        words[20] = (0x09 << 26) | (16 << 21) | (8 << 16) | (0x10000 - 0xFFC0)
        idx = _index(0x00100000, words)
        refs = idx.refs_into(self.CAVE, 8)
        self.assertEqual([r.axis for r in refs], ["formed"])
        self.assertIn("off r16", refs[0].detail)

    def test_clobbering_the_base_drops_it(self):
        # What makes an unbounded pairing window safe: a write to the base
        # register invalidates it, so no stale pairing is reported.
        words = [NOP] * 64
        words[8], words[9] = JR_RA, NOP
        words[0] = (0x0F << 26) | (16 << 16) | 0x0011        # lui s0
        words[1] = (0x0F << 26) | (16 << 16) | 0x0055        # lui s0 again
        words[20] = (0x09 << 26) | (16 << 21) | (8 << 16) | (0x10000 - 0xFFC0)
        idx = _index(0x00100000, words)
        self.assertEqual(idx.refs_into(self.CAVE, 8), [])

    def test_internal_branches_are_not_liveness(self):
        # A branch from inside the range to inside the range is the cave's
        # own control flow and must not count against it.
        words = [NOP] * 32
        words[8], words[9] = JR_RA, NOP
        offset = (self.CAVE + 4 - (self.CAVE + 4)) >> 2
        words[16] = (0x04 << 26) | (offset & 0xFFFF)   # at 0x00100040
        idx = _index(0x00100000, words)
        self.assertEqual(idx.refs_into(self.CAVE, 16), [])

    def test_entry_classification(self):
        words = [NOP] * 32
        words[8], words[9] = JR_RA, NOP
        self.assertEqual(_index(0x00100000, words).entry(self.CAVE), "guarded")
        words[8] = NOP
        self.assertEqual(_index(0x00100000, words).entry(self.CAVE),
                         "fallsthru")

    def test_verdict_requires_both_clean_refs_and_guarded_entry(self):
        clean = CaveVerdict(0x100, 16, [], "guarded")
        self.assertTrue(clean.dead)
        self.assertTrue(CaveVerdict(0x100, 16, [], "data").dead)
        self.assertFalse(CaveVerdict(0x100, 16, [], "fallsthru").dead)
        ref = Reference(0x200, 0x100, "formed", "x")
        self.assertFalse(CaveVerdict(0x100, 16, [ref], "guarded").dead)

    def test_small_integers_are_not_treated_as_pointers(self):
        idx = self._stream(at_index=0, word=ADDR_LO - 4)
        self.assertEqual(idx.refs_into(ADDR_LO - 4, 4), [])


@unittest.skipUnless(os.path.exists(ELF), "game executable missing")
class ShippedCaveTest(unittest.TestCase):
    """The regression: the two caves this project burned itself on."""

    @classmethod
    def setUpClass(cls):
        cls.index = ImageIndex(Elf32(ELF))

    def _verdict(self, base, size):
        return census(None, [(base, size)], index=self.index)[0]

    def test_cave_1_is_live_code(self):
        # fact-check-2026-08.md section 1: four interior addresses are
        # materialised at 0x00139E2C..44 and registered as callbacks. Ten of
        # the eleven lines of code-caves.md's worked example sat on top of it.
        v = self._verdict(0x00139A68, 456)
        self.assertFalse(v.dead)
        formed = v.by_axis()["formed"]
        self.assertEqual(sorted(r.vaddr for r in formed),
                         [0x00139E2C, 0x00139E38, 0x00139E3C, 0x00139E44])
        # the region START itself is materialised, not merely an interior word
        self.assertIn(0x00139A68, [r.target for r in formed])

    def test_cave_3_is_live_code(self):
        # code-caves.md's struck row: anim-lanes/2-mass-law.md found the pair
        # at 0x00460178/80. "the original survey's lui-pairing window missed a
        # cross-function pair, the same failure class as the #1 burn."
        v = self._verdict(0x0045F598, 624)
        self.assertFalse(v.dead)
        self.assertEqual(sorted(r.vaddr for r in v.by_axis()["formed"]),
                         [0x00460178, 0x00460180])

    def test_cave_7_is_dead(self):
        # The motion cave's workhorse, re-censused clean in its own doc.
        v = self._verdict(0x00443270, 480)
        self.assertTrue(v.dead, v.report())
        self.assertEqual(v.entry, "guarded")

    def test_cave_11_is_dead_linker_padding(self):
        # Owned by no object at all -- the lowest-risk region in the image.
        v = self._verdict(0x00514920, 96)
        self.assertTrue(v.dead, v.report())
        self.assertEqual(v.entry, "data")

    def test_canary_word_is_unwritten(self):
        # motion-block-cave.md's execution canary: ELF-zero and referenced by
        # nothing, which is what makes a non-zero read proof of execution.
        self.assertEqual(self.index.word(0x00514974), 0)
        self.assertEqual(self.index.refs_into(0x00514974, 4), [])


if __name__ == "__main__":
    unittest.main()

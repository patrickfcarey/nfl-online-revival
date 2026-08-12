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
        words[self.GUARD], words[self.GUARD + 1] = JR_RA, NOP
        words[0] = (0x0F << 26) | (8 << 16) | 0x0010          # lui t0, 0x0010
        words[31] = (0x09 << 26) | (8 << 21) | (8 << 16) | 0x0040  # addiu
        idx = _index(0x00100000, words)
        refs = idx.refs_into(self.CAVE, 8)
        self.assertEqual([r.axis for r in refs], ["formed"])

    def test_one_base_serving_several_addresses(self):
        """Cave #1's exact shape: one `lui`, four callbacks off it.

        Each `addiu rD, rBase, simm` has rD != rBase, so the base survives
        and serves the next one. A census must report *every* address formed
        this way, not just the first -- cave #1 had four and the region start
        was among them.
        """
        words = [NOP] * 64
        words[self.GUARD], words[self.GUARD + 1] = JR_RA, NOP
        words[0] = (0x0F << 26) | (16 << 16) | 0x0010     # lui s0, 0x0010
        # addiu t0, s0, 0x40 / addiu t1, s0, 0x44 -- base reused, never dies
        words[40] = (0x09 << 26) | (16 << 21) | (8 << 16) | 0x0040
        words[41] = (0x09 << 26) | (16 << 21) | (9 << 16) | 0x0044
        idx = _index(0x00100000, words)
        refs = idx.refs_into(self.CAVE, 8)
        self.assertEqual([r.axis for r in refs], ["formed", "formed"])
        self.assertEqual(sorted(r.target for r in refs),
                         [self.CAVE, self.CAVE + 4])
        self.assertIn("off r16", refs[0].detail)

    def test_clobbering_the_base_drops_it(self):
        # What makes an unbounded pairing window safe: a write to the base
        # register invalidates it, so no stale pairing is reported.
        words = [NOP] * 64
        words[self.GUARD], words[self.GUARD + 1] = JR_RA, NOP
        words[0] = (0x0F << 26) | (16 << 16) | 0x0010        # lui s0, 0x0010
        words[1] = (0x0F << 26) | (16 << 16) | 0x0055        # lui s0 again
        words[40] = (0x09 << 26) | (16 << 21) | (8 << 16) | 0x0040
        idx = _index(0x00100000, words)
        self.assertEqual(idx.refs_into(self.CAVE, 8), [])

    def test_internal_branches_are_not_liveness(self):
        # A branch from inside the range to inside the range is the cave's
        # own control flow and must not count against it.
        words = [NOP] * 32
        words[self.GUARD], words[self.GUARD + 1] = JR_RA, NOP
        words[16] = (0x04 << 26) | 0x0000            # at 0x00100040 -> +0x44
        idx = _index(0x00100000, words)
        self.assertEqual(idx.refs_into(self.CAVE, 16), [])

    def test_entry_classification(self):
        words = [NOP] * 32
        words[self.GUARD], words[self.GUARD + 1] = JR_RA, NOP
        self.assertEqual(_index(0x00100000, words).entry(self.CAVE), "guarded")
        words[self.GUARD] = NOP
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

    def test_cave_2_is_dead_as_a_unit_but_is_one_function(self):
        """The fourth false-safe region, and the subtlest.

        Nothing outside reaches cave #2 on any axis -- the external census is
        right that it is dead. But its base is a *function prologue*
        (`addiu sp, sp, -224`) and its tail at 0x0044C404 branches back to
        0x0044C228, inside the first 55 words anyone would write there. A
        partial overwrite corrupts a function rather than replacing it, so
        the region is unusable even though it is unreferenced.
        """
        v = self._verdict(0x0044C1C0, 640)
        self.assertTrue(v.dead)                      # no external reference
        self.assertEqual(v.entry, "guarded")
        cross = self.index.internal_crossings(0x0044C1C0, 55, 640)
        self.assertEqual([(r.vaddr, r.target) for r in cross],
                         [(0x0044C404, 0x0044C228)])

    def test_alignment_padding_does_not_fake_a_fall_through(self):
        # A `nop` pad sits between the previous function's delay slot and
        # cave #2's prologue. A two-word lookback lands on the pad and
        # wrongly reports live code running in.
        self.assertEqual(self.index.entry(0x0044C1C0), "guarded")
        self.assertEqual(self.index.word(0x0044C1BC), 0)          # the pad
        self.assertEqual(self.index.word(0x0044C1B4), 0x03E00008)  # jr ra

    def test_the_verified_alternative_caves_are_clean(self):
        # Caves #4/#5/#6, re-verified on every axis after #2 was rejected.
        for base, size in ((0x004F4AA0, 608), (0x00447888, 600),
                           (0x0044BEB0, 584)):
            v = self._verdict(base, size)
            self.assertTrue(v.dead, v.report())
            self.assertEqual(self.index.internal_crossings(base, 55, size), [])

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

    def test_caller_query_finds_a_jal_host(self):
        # Site B's only caller, the per-frame engagement driver.
        refs = self.index.callers_of(0x001F20F8)
        self.assertEqual([(r.vaddr, r.axis) for r in refs],
                         [(0x001F733C, "jal")])

    def test_caller_query_finds_a_dispatch_TABLE_entry(self):
        """State 32's ai_think has no `jal` caller at all.

        It is reached only by a function-pointer word in the 93-state AI
        dispatch table. A jal-only caller scan reports zero callers here --
        which is precisely how live functions came to be recorded as dead,
        and why the per-frame host that P10 eventually used was hard to find.
        """
        refs = self.index.callers_of(0x001E8088)
        self.assertEqual([(r.vaddr, r.axis) for r in refs],
                         [(0x00527540, "word")])

    def test_caller_query_walks_the_frame_spine(self):
        # The gameplay tick's single caller, per drive-lanes/1-per-frame-host.
        refs = self.index.callers_of(0x00164EC0)
        self.assertEqual([(r.vaddr, r.axis) for r in refs],
                         [(0x0015418C, "jal")])

    def test_canary_word_is_unwritten(self):
        # motion-block-cave.md's execution canary: ELF-zero and referenced by
        # nothing, which is what makes a non-zero read proof of execution.
        self.assertEqual(self.index.word(0x00514974), 0)
        self.assertEqual(self.index.refs_into(0x00514974, 4), [])


if __name__ == "__main__":
    unittest.main()

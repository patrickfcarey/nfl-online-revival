"""Baking a pnach into the executable.

This tool writes the words that make the game behave differently, and it writes
them once, permanently, into the file the console runs. The failure modes worth
paying for are all silent ones:

* **a word at the wrong file offset.** The mapping is one subtraction, which is
  exactly the kind of arithmetic that looks right in review and lands four
  bytes into the next instruction.
* **a patch that quietly did not ship.** A `.bss` address has no byte in the
  file; a dropped or skipped line has none either. Both would produce a
  cheerful "102 words written" over an executable missing the keystone.
* **a width guessed.** `byte` and `short` writes cover different bytes than
  `word`, and this project has already lost time to a silent width bug.

The fixture is a 4,352-byte ELF with one PT_LOAD laid out like the real one --
file offset 0x1000 to vaddr 0x00100000, so `vaddr = file_offset + 0xFF000`
holds here too -- plus a `.bss` tail that exists in memory and not in the file.
Small enough to assert byte-for-byte, shaped enough to prove the arithmetic.

Run: ``python3 tests/test_bake_pnach.py``
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import bake_pnach  # noqa: E402

SEG_OFFSET = 0x1000
SEG_VADDR = 0x00100000
SEG_FILESZ = 0x100
SEG_MEMSZ = 0x180

#: The first word of the segment, and the first word past its file image.
FIRST = SEG_VADDR
LAST_WORD = SEG_VADDR + SEG_FILESZ - 4
FIRST_BSS = SEG_VADDR + SEG_FILESZ
OUTSIDE_ADDR = 0x00200000


def elf_bytes(offset=SEG_OFFSET, vaddr=SEG_VADDR, filesz=SEG_FILESZ,
              memsz=SEG_MEMSZ, machine=8, phnum=1):
    """A minimal ELF32 MIPS executable with one PT_LOAD.

    The segment's words are 0xAA0000NN, NN being the word's index, so a
    misplaced write is visible in the old value the manifest records.
    """
    header = bytearray(52)
    header[0:4] = b"\x7fELF"
    header[4] = 1                                   # ELF32
    header[5] = 1                                   # little-endian
    header[6] = 1
    struct.pack_into("<HHI", header, 16, 2, machine, 1)   # EXEC, MIPS, v1
    struct.pack_into("<III", header, 24, vaddr, 52, 0)    # entry, phoff, shoff
    struct.pack_into("<HHHHHH", header, 40, 52, 32, phnum, 40, 0, 0)
    program = bytearray(32)
    struct.pack_into("<IIIIIIII", program, 0, 1, offset, vaddr, vaddr,
                     filesz, memsz, 5, 0x1000)
    body = bytearray(filesz)
    for index in range(filesz // 4):
        struct.pack_into("<I", body, index * 4, 0xAA000000 | index)
    blob = bytearray(offset + filesz)
    blob[0:52] = header
    blob[52:52 + 32] = program
    blob[offset:offset + filesz] = body
    return bytes(blob)


def pnach_text(*lines, header=True):
    prefix = ["gametitle=Synthetic (SLUS-20752) [DEADBEEF]",
              "comment=a fixture", "// a comment line", ""] if header else []
    return "\n".join(prefix + list(lines)) + "\n"


def patch_line(vaddr, value, mode=1, width="word", cpu="EE"):
    return "patch=%d,%s,%08X,%s,%08X" % (mode, cpu, vaddr, width, value)


def word_at(data, vaddr):
    return struct.unpack_from("<I", data, SEG_OFFSET + (vaddr - SEG_VADDR))[0]


def _temp(suffix, content=b""):
    handle, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(handle, "wb") as out:
        out.write(content)
    return Path(path)


# --------------------------------------------------------------------------
# the pnach
# --------------------------------------------------------------------------

class Parsing(unittest.TestCase):
    def test_reads_address_and_value_as_hex(self):
        patches = bake_pnach.parse_pnach(pnach_text(
            "patch=1,EE,001F2D60,word,1000000F"))
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0].vaddr, 0x001F2D60)
        self.assertEqual(patches[0].value, 0x1000000F)
        self.assertTrue(patches[0].enabled)
        self.assertEqual(patches[0].line, 5)

    def test_a_trailing_comment_is_not_part_of_the_value(self):
        """The deployed set has one, on the T3 detour line."""
        patches = bake_pnach.parse_pnach(pnach_text(
            "patch=1,EE,004F4B60,word,0813D2EA    // T3: detour to k-scale"))
        self.assertEqual(patches[0].value, 0x0813D2EA)

    def test_blanks_comments_and_headers_carry_no_patches(self):
        patches = bake_pnach.parse_pnach(pnach_text(
            "// commentary", "", "   ", "# another style", ";and another",
            "[a section header]", "patch=1,EE,00100000,word,00000001"))
        self.assertEqual(len(patches), 1)

    def test_patch_zero_is_kept_but_marked_not_applied(self):
        patches = bake_pnach.parse_pnach(pnach_text(
            patch_line(FIRST, 1, mode=0)))
        self.assertFalse(patches[0].enabled)

    def test_a_non_word_width_is_refused_by_name(self):
        for width in ("byte", "short", "extended"):
            with self.assertRaises(bake_pnach.BakeError) as caught:
                bake_pnach.parse_pnach(pnach_text(
                    patch_line(FIRST, 1, width=width)))
            self.assertIn(width, str(caught.exception))
            self.assertIn("Only 'word'", str(caught.exception))

    def test_an_iop_patch_is_refused(self):
        with self.assertRaises(bake_pnach.BakeError) as caught:
            bake_pnach.parse_pnach(pnach_text(patch_line(FIRST, 1, cpu="IOP")))
        self.assertIn("IOP", str(caught.exception))

    def test_a_bad_mode_is_refused(self):
        with self.assertRaises(bake_pnach.BakeError):
            bake_pnach.parse_pnach(pnach_text("patch=2,EE,00100000,word,1"))

    def test_a_non_hex_field_is_refused(self):
        for line in ("patch=1,EE,00G00000,word,00000001",
                     "patch=1,EE,00100000,word,zzzz"):
            with self.assertRaises(bake_pnach.BakeError) as caught:
                bake_pnach.parse_pnach(pnach_text(line))
            self.assertIn("not hexadecimal", str(caught.exception))

    def test_a_short_patch_line_is_refused(self):
        with self.assertRaises(bake_pnach.BakeError) as caught:
            bake_pnach.parse_pnach(pnach_text("patch=1,EE,00100000,word"))
        self.assertIn("five", str(caught.exception))

    def test_an_unknown_directive_is_refused_rather_than_skipped(self):
        """A skipped line is a patch that silently did not ship."""
        with self.assertRaises(bake_pnach.BakeError) as caught:
            bake_pnach.parse_pnach(pnach_text("patchh=1,EE,00100000,word,1"))
        self.assertIn("unknown directive", str(caught.exception))

    def test_a_bare_word_is_not_silently_ignored(self):
        with self.assertRaises(bake_pnach.BakeError):
            bake_pnach.parse_pnach(pnach_text("nonsense"))

    def test_the_0x_prefix_is_accepted(self):
        patches = bake_pnach.parse_pnach(pnach_text(
            "patch=1,EE,0x00100000,word,0xDEADBEEF"))
        self.assertEqual(patches[0].vaddr, 0x00100000)
        self.assertEqual(patches[0].value, 0xDEADBEEF)

    def test_a_value_wider_than_a_word_is_refused(self):
        with self.assertRaises(bake_pnach.BakeError) as caught:
            bake_pnach.parse_pnach(pnach_text(
                "patch=1,EE,00100000,word,1DEADBEEF"))
        self.assertIn("32 bits", str(caught.exception))

    def test_several_files_keep_their_own_names_and_line_numbers(self):
        first = bake_pnach.parse_pnach(pnach_text(patch_line(FIRST, 1)), "a")
        second = bake_pnach.parse_pnach(pnach_text(patch_line(FIRST, 2)), "b")
        self.assertEqual(first[0].source, "a")
        self.assertEqual(second[0].source, "b")


# --------------------------------------------------------------------------
# the ELF
# --------------------------------------------------------------------------

class ElfHeaders(unittest.TestCase):
    def test_the_load_segment_is_derived_not_assumed(self):
        elf = bake_pnach.Elf32(elf_bytes())
        self.assertEqual(len(elf.segments), 1)
        seg = elf.segments[0]
        self.assertEqual((seg.offset, seg.vaddr, seg.filesz, seg.memsz),
                         (SEG_OFFSET, SEG_VADDR, SEG_FILESZ, SEG_MEMSZ))
        self.assertEqual(seg.delta, bake_pnach.CONVENTION_DELTA)
        self.assertEqual(bake_pnach.convention_notes(elf), [])

    def test_a_different_mapping_is_used_but_announced(self):
        """The delta comes from the headers; the constant only cross-checks."""
        elf = bake_pnach.Elf32(elf_bytes(offset=0x2000, vaddr=0x00800000))
        self.assertEqual(elf.segments[0].delta, 0x7FE000)
        notes = bake_pnach.convention_notes(elf)
        self.assertEqual(len(notes), 1)
        self.assertIn("0xFF000", notes[0])
        placement = bake_pnach.classify(
            bake_pnach.Patch("p", 1, True, 0x00800040, 0), elf)
        self.assertEqual(placement.file_offset, 0x2040)

    def test_not_an_elf_is_refused(self):
        for blob in (b"", b"not an elf at all" * 8,
                     b"\x7fELF" + b"\x00" * 200):
            with self.assertRaises(bake_pnach.BakeError):
                bake_pnach.Elf32(blob)

    def test_a_truncated_segment_is_refused(self):
        blob = elf_bytes()[:-16]
        with self.assertRaises(bake_pnach.BakeError) as caught:
            bake_pnach.Elf32(blob)
        self.assertIn("truncated", str(caught.exception))

    def test_a_non_mips_elf_is_refused(self):
        with self.assertRaises(bake_pnach.BakeError) as caught:
            bake_pnach.Elf32(elf_bytes(machine=3))
        self.assertIn("MIPS", str(caught.exception))

    def test_an_elf_with_no_load_segment_is_refused(self):
        with self.assertRaises(bake_pnach.BakeError) as caught:
            bake_pnach.Elf32(elf_bytes(phnum=0))
        self.assertIn("program headers", str(caught.exception))


# --------------------------------------------------------------------------
# classification -- the offset arithmetic and the two refusals
# --------------------------------------------------------------------------

class Classify(unittest.TestCase):
    def setUp(self):
        self.elf = bake_pnach.Elf32(elf_bytes())

    def place(self, vaddr):
        return bake_pnach.classify(
            bake_pnach.Patch("p", 1, True, vaddr, 0), self.elf)

    def test_a_file_backed_word_maps_by_subtraction(self):
        for vaddr, offset in ((FIRST, SEG_OFFSET),
                              (SEG_VADDR + 0x40, SEG_OFFSET + 0x40),
                              (LAST_WORD, SEG_OFFSET + SEG_FILESZ - 4)):
            place = self.place(vaddr)
            self.assertEqual(place.kind, bake_pnach.FILE_BACKED)
            self.assertEqual(place.file_offset, offset)
            self.assertEqual(place.file_offset,
                             vaddr - bake_pnach.CONVENTION_DELTA)

    def test_a_bss_address_is_refused_with_the_reason(self):
        for vaddr in (FIRST_BSS, SEG_VADDR + SEG_MEMSZ - 4):
            place = self.place(vaddr)
            self.assertEqual(place.kind, bake_pnach.BSS)
            self.assertIsNone(place.file_offset)
            self.assertIn("zeroes", place.reason)

    def test_a_word_straddling_the_end_of_the_file_image_is_refused(self):
        """Three bytes in the file and one in .bss is not a bakeable word."""
        place = self.place(FIRST_BSS - 2)
        self.assertEqual(place.kind, bake_pnach.BSS)
        self.assertIn("straddles", place.reason)

    def test_an_address_no_segment_maps_is_outside(self):
        for vaddr in (OUTSIDE_ADDR, SEG_VADDR - 4, 0x00000000):
            place = self.place(vaddr)
            self.assertEqual(place.kind, bake_pnach.OUTSIDE)
            self.assertIsNone(place.file_offset)


class Collisions(unittest.TestCase):
    def places(self, *lines):
        elf = bake_pnach.Elf32(elf_bytes())
        return [bake_pnach.classify(p, elf)
                for p in bake_pnach.parse_pnach(pnach_text(*lines))]

    def test_the_same_word_twice_is_a_duplicate_not_a_conflict(self):
        duplicates, conflicts = bake_pnach.collisions(self.places(
            patch_line(FIRST, 0x11111111), patch_line(FIRST, 0x11111111)))
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(conflicts, [])
        self.assertIn("0x00100000", duplicates[0])

    def test_two_values_at_one_address_is_a_conflict(self):
        duplicates, conflicts = bake_pnach.collisions(self.places(
            patch_line(FIRST, 0x11111111), patch_line(FIRST, 0x22222222)))
        self.assertEqual(duplicates, [])
        self.assertEqual(len(conflicts), 1)
        self.assertIn("different values", conflicts[0])

    def test_overlapping_unaligned_words_are_a_conflict(self):
        _duplicates, conflicts = bake_pnach.collisions(self.places(
            patch_line(FIRST, 0x11111111), patch_line(FIRST + 2, 0x22222222)))
        self.assertTrue(conflicts)
        self.assertIn("overlaps", conflicts[0])

    def test_a_parked_patch_does_not_collide_with_the_live_one(self):
        duplicates, conflicts = bake_pnach.collisions(self.places(
            patch_line(FIRST, 0x11111111),
            patch_line(FIRST, 0x22222222, mode=0)))
        self.assertEqual((duplicates, conflicts), ([], []))


# --------------------------------------------------------------------------
# the write
# --------------------------------------------------------------------------

class Bake(unittest.TestCase):
    def setUp(self):
        self.before = elf_bytes()
        self.elf = bake_pnach.Elf32(self.before)

    def run_bake(self, *lines):
        placements = [bake_pnach.classify(p, self.elf)
                      for p in bake_pnach.parse_pnach(pnach_text(*lines))]
        return bake_pnach.bake(self.elf, placements)

    def test_the_words_land_where_the_mapping_says(self):
        after, records = self.run_bake(patch_line(FIRST, 0x1000000F),
                                       patch_line(LAST_WORD, 0x0C13D2A8))
        self.assertEqual(word_at(after, FIRST), 0x1000000F)
        self.assertEqual(word_at(after, LAST_WORD), 0x0C13D2A8)
        self.assertEqual(records[0]["file_offset"], "0x00001000")
        self.assertEqual(records[0]["old"], "0xAA000000")
        self.assertEqual(records[0]["new"], "0x1000000F")
        self.assertTrue(records[0]["changed"])

    def test_nothing_else_in_the_file_moves(self):
        after, _records = self.run_bake(patch_line(SEG_VADDR + 0x40, 0xFFFFFFFF))
        offset = SEG_OFFSET + 0x40
        self.assertEqual(len(after), len(self.before))
        self.assertEqual(after[:offset], self.before[:offset])
        self.assertEqual(after[offset + 4:], self.before[offset + 4:])

    def test_a_word_already_holding_its_value_is_written_but_flagged(self):
        after, records = self.run_bake(patch_line(FIRST, 0xAA000000))
        self.assertEqual(after, self.before)
        self.assertTrue(records[0]["written"])
        self.assertFalse(records[0]["changed"])

    def test_a_parked_patch_is_recorded_and_not_written(self):
        after, records = self.run_bake(patch_line(FIRST, 0xDEADBEEF, mode=0))
        self.assertEqual(after, self.before)
        self.assertFalse(records[0]["written"])
        self.assertIn("patch=0", records[0]["note"])

    def test_a_refused_placement_reaching_the_write_raises(self):
        """The classify step is the gate; bake asserts it rather than skipping."""
        placements = [bake_pnach.classify(p, self.elf) for p in
                      bake_pnach.parse_pnach(pnach_text(
                          patch_line(FIRST_BSS, 1)))]
        with self.assertRaises(bake_pnach.BakeError):
            bake_pnach.bake(self.elf, placements)


class Verify(unittest.TestCase):
    def setUp(self):
        self.before = elf_bytes()
        elf = bake_pnach.Elf32(self.before)
        placements = [bake_pnach.classify(p, elf) for p in
                      bake_pnach.parse_pnach(pnach_text(
                          patch_line(FIRST, 0x1000000F),
                          patch_line(SEG_VADDR + 0x20, 0x3F4CCCCD)))]
        self.after, self.records = bake_pnach.bake(elf, placements)

    def test_a_good_bake_has_no_problems(self):
        self.assertEqual(bake_pnach.verify(self.before, self.after,
                                           self.records), [])

    def test_a_word_that_did_not_land_is_reported(self):
        broken = bytearray(self.after)
        struct.pack_into("<I", broken, SEG_OFFSET, 0xDEADBEEF)
        problems = bake_pnach.verify(self.before, bytes(broken), self.records)
        self.assertTrue(any("0x00100000" in p and "read 0xDEADBEEF" in p
                            for p in problems), problems)

    def test_the_last_of_two_lines_at_one_address_is_the_one_verified(self):
        """--allow-conflicts takes the last line, as the cheat engine does."""
        elf = bake_pnach.Elf32(self.before)
        placements = [bake_pnach.classify(p, elf) for p in
                      bake_pnach.parse_pnach(pnach_text(
                          patch_line(FIRST, 0x11111111),
                          patch_line(FIRST, 0x22222222)))]
        after, records = bake_pnach.bake(elf, placements)
        self.assertEqual(word_at(after, FIRST), 0x22222222)
        self.assertEqual(bake_pnach.verify(self.before, after, records), [])

    def test_a_byte_no_patch_covers_is_reported(self):
        """The check that catches a bug in this tool, not in the pnach."""
        stray = bytearray(self.after)
        stray[SEG_OFFSET + 0x80] ^= 0xFF
        problems = bake_pnach.verify(self.before, bytes(stray), self.records)
        self.assertTrue(any("no patch covers it" in p for p in problems))

    def test_a_changed_size_is_reported_first(self):
        problems = bake_pnach.verify(self.before, self.after + b"\x00",
                                     self.records)
        self.assertEqual(len(problems), 1)
        self.assertIn("size", problems[0])


class Manifest(unittest.TestCase):
    def test_it_records_every_line_and_counts_the_classes(self):
        elf = bake_pnach.Elf32(elf_bytes())
        pnach = _temp(".pnach", pnach_text(
            patch_line(FIRST, 0x11111111),
            patch_line(SEG_VADDR + 4, 0x22222222),
            patch_line(SEG_VADDR + 8, 0x33333333, mode=0)).encode())
        self.addCleanup(os.unlink, pnach)
        placements = [bake_pnach.classify(p, elf)
                      for p in bake_pnach.load_pnach(pnach)]
        duplicates, conflicts = bake_pnach.collisions(placements)
        after, records = bake_pnach.bake(elf, placements)
        summary = bake_pnach.summarise(placements, records, duplicates,
                                       conflicts)
        self.assertEqual(summary["patch_lines"], 3)
        self.assertEqual(summary["applied"], 2)
        self.assertEqual(summary["parked"], 1)
        self.assertEqual(summary["file_backed"], 2)
        self.assertEqual(summary["bss"], 0)
        self.assertEqual(summary["outside"], 0)
        self.assertEqual(summary["words_written"], 2)

        manifest = bake_pnach.build_manifest(elf, Path("out.elf"), after,
                                             records, summary, duplicates,
                                             conflicts, [], [pnach])
        text = json.dumps(manifest)                  # it has to serialise
        self.assertIn("sha256_before", text)
        self.assertEqual(len(manifest["patches"]), 3)
        self.assertEqual(manifest["elf"]["segments"][0]["delta"], "0x000FF000")
        self.assertTrue(manifest["elf"]["segments"][0]["matches_convention"])
        self.assertNotEqual(manifest["elf"]["sha256_before"],
                            manifest["elf"]["sha256_after"])
        record = manifest["patches"][0]
        self.assertEqual(record["vaddr"], "0x00100000")
        self.assertEqual(record["old"], "0xAA000000")
        self.assertEqual(record["new"], "0x11111111")


# --------------------------------------------------------------------------
# the command line
# --------------------------------------------------------------------------

class Cli(unittest.TestCase):
    def setUp(self):
        self.stock = elf_bytes()
        self.elf_path = _temp(".elf", self.stock)
        self.out = Path(tempfile.mkdtemp()) / "baked.elf"
        self.addCleanup(os.unlink, self.elf_path)

    def write_pnach(self, *lines):
        path = _temp(".pnach", pnach_text(*lines).encode())
        self.addCleanup(os.unlink, path)
        return path

    def run_cli(self, *argv):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = bake_pnach.main([str(a) for a in argv])
        return code, out.getvalue(), err.getvalue()

    def test_a_clean_bake_writes_the_elf_the_manifest_and_verifies(self):
        pnach = self.write_pnach(patch_line(FIRST, 0x1000000F),
                                 patch_line(LAST_WORD, 0x0C13D2A8))
        code, out, _err = self.run_cli(self.elf_path, pnach, "-o", self.out,
                                       "--verify")
        self.assertEqual(code, 0)
        baked = self.out.read_bytes()
        self.assertEqual(len(baked), len(self.stock))
        self.assertEqual(word_at(baked, FIRST), 0x1000000F)
        self.assertEqual(word_at(baked, LAST_WORD), 0x0C13D2A8)
        self.assertIn("2/2 words read back", out)
        manifest = json.loads(
            (Path(str(self.out) + ".manifest.json")).read_text())
        self.assertEqual(manifest["summary"]["file_backed"], 2)
        self.assertEqual(self.elf_path.read_bytes(), self.stock)

    def test_the_input_is_never_the_output(self):
        pnach = self.write_pnach(patch_line(FIRST, 1))
        code, _out, err = self.run_cli(self.elf_path, pnach, "-o",
                                       self.elf_path)
        self.assertEqual(code, 2)
        self.assertIn("names the input", err)
        self.assertEqual(self.elf_path.read_bytes(), self.stock)

    def test_no_output_and_no_audit_is_refused(self):
        pnach = self.write_pnach(patch_line(FIRST, 1))
        code, _out, err = self.run_cli(self.elf_path, pnach)
        self.assertEqual(code, 2)
        self.assertIn("-o OUTPUT", err)

    def test_a_bss_patch_refuses_the_whole_bake(self):
        """One unbakeable line stops the set: a partial bake is a silent one."""
        pnach = self.write_pnach(patch_line(FIRST, 0x1000000F),
                                 patch_line(FIRST_BSS, 0x00000001))
        code, _out, err = self.run_cli(self.elf_path, pnach, "-o", self.out)
        self.assertEqual(code, 2)
        self.assertIn(".bss", err)
        self.assertIn("init hook", err)
        self.assertIn("nothing was written", err)
        self.assertFalse(self.out.exists())

    def test_an_address_outside_every_segment_refuses(self):
        pnach = self.write_pnach(patch_line(OUTSIDE_ADDR, 1))
        code, _out, err = self.run_cli(self.elf_path, pnach, "-o", self.out)
        self.assertEqual(code, 2)
        self.assertIn("outside", err)
        self.assertFalse(self.out.exists())

    def test_a_conflict_refuses_until_it_is_allowed(self):
        pnach = self.write_pnach(patch_line(FIRST, 0x11111111),
                                 patch_line(FIRST, 0x22222222))
        code, _out, err = self.run_cli(self.elf_path, pnach, "-o", self.out)
        self.assertEqual(code, 2)
        self.assertIn("conflict", err)
        self.assertFalse(self.out.exists())

        code, out, _err = self.run_cli(self.elf_path, pnach, "-o", self.out,
                                       "--allow-conflicts", "--verify")
        self.assertEqual(code, 0)
        self.assertEqual(word_at(self.out.read_bytes(), FIRST), 0x22222222)

    def test_audit_classifies_without_writing_anything(self):
        pnach = self.write_pnach(patch_line(FIRST, 1))
        code, out, _err = self.run_cli(self.elf_path, pnach, "--audit")
        self.assertEqual(code, 0)
        self.assertFalse(self.out.exists())
        manifest = json.loads(out[out.index("{"):])
        self.assertIsNone(manifest["elf"]["output"])
        self.assertEqual(manifest["summary"]["file_backed"], 1)

    def test_several_pnach_files_are_applied_in_order(self):
        first = self.write_pnach(patch_line(FIRST, 0x11111111))
        second = self.write_pnach(patch_line(SEG_VADDR + 4, 0x22222222))
        code, _out, _err = self.run_cli(self.elf_path, first, second, "-o",
                                        self.out, "--verify")
        self.assertEqual(code, 0)
        baked = self.out.read_bytes()
        self.assertEqual(word_at(baked, FIRST), 0x11111111)
        self.assertEqual(word_at(baked, SEG_VADDR + 4), 0x22222222)

    def test_a_missing_elf_exits_two(self):
        pnach = self.write_pnach(patch_line(FIRST, 1))
        code, _out, err = self.run_cli("/nonexistent.elf", pnach, "-o",
                                       self.out)
        self.assertEqual(code, 2)
        self.assertIn("no ELF", err)

    def test_a_bad_pnach_exits_two_without_writing(self):
        pnach = self.write_pnach(patch_line(FIRST, 1, width="byte"))
        code, _out, err = self.run_cli(self.elf_path, pnach, "-o", self.out)
        self.assertEqual(code, 2)
        self.assertIn("byte", err)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()

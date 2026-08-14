"""Writing byte patches into an Xbox executable.

This tool writes the bytes that make the game behave differently, into the file
the console runs, permanently. Every failure mode worth paying for is silent:

* **bytes at the wrong file offset.** The XBE mapping is one subtraction per
  section, which is exactly the arithmetic that looks right in review and lands
  two bytes into the next instruction.
* **a patch that quietly did not ship.** Five sections of the retail image
  declare virtual size beyond their raw size -- 689 KB of zero fill with no
  bytes in the file. An address there looks ordinary and has nowhere to be
  written; so does an address in a file gap, or in the header block. All four
  refusals are here, by name.
* **a file that changed size.** A byte patch that grows the executable moves
  every byte after it and breaks the disc image's extent; this tool is same-size
  only and the tests pin that from both directions.
* **a digest quietly left describing the old bytes -- or quietly invented.**
  Section headers carry a SHA-1 at `+0x24`. Ten of the retail image's eleven
  reproduce as `SHA-1(le32(raw_size) || raw_bytes)`; `.text` does not. The
  fixture models both cases on purpose, because the *first* real patch lands in
  `.text` and the operator has to know what his file carries.

The fixture is a 1,664-byte XBE with three sections laid out like the real one:
a `.text` whose stored digest deliberately does not follow the rule, a `NEXT`
adjacent to it in virtual space (so a span can straddle the boundary), and a
`.data` with a zero-fill tail that exists in memory and not in the file. Small
enough to assert byte-for-byte, shaped enough to prove the arithmetic.

Run: ``python3 tests/test_patch_xbe.py``
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recon.xbe import ENTRY_XOR, SECTION_HEADER_SIZE, THUNK_XOR, Xbe  # noqa: E402
from tools import patch_xbe  # noqa: E402

BASE = 0x00010000
HEADERS_SIZE = 0x400

#: (name, vaddr, virtual size, raw size). `.data`'s vsize exceeds its raw size
#: by 0x80: that tail is the Xbox's .bss.
SECTIONS = (
    (".text", 0x00011000, 0x100, 0x100),
    ("NEXT", 0x00011100, 0x080, 0x080),
    (".data", 0x00012000, 0x180, 0x100),
)

TEXT_VA = 0x00011000
TEXT_END = 0x00011100                    # first byte past .text
NEXT_VA = 0x00011100
DATA_VA = 0x00012000
DATA_FILE_END = 0x00012100               # first zero-fill byte of .data
DATA_MAPPED_END = 0x00012180
GAP_VA = 0x00011180                      # between NEXT and .data: mapped nowhere
HEADER_VA = BASE + 0x300                 # the section table itself
OUTSIDE_VA = 0x00020000

TEXT_RAW = HEADERS_SIZE                  # 0x400
NEXT_RAW = TEXT_RAW + 0x100              # 0x500
DATA_RAW = NEXT_RAW + 0x080              # 0x580


def xbe_bytes(sections=SECTIONS, base=BASE, broken_digests=(".text",),
              build="retail"):
    """A small but structurally real XBE, with honest section digests.

    `broken_digests` names sections whose *stored* digest deliberately does not
    follow the rule -- the retail image's `.text` is like this, and it is the
    case the tool must refuse to paper over.
    """
    cert_off, names_off, sect_off = 0x180, 0x280, 0x300
    # The library table follows the section table, wherever that ends.
    libs_off = sect_off + len(sections) * SECTION_HEADER_SIZE
    libs_off += -libs_off % 0x10
    assert libs_off + 0x20 <= HEADERS_SIZE, "the fixture's headers overflowed"
    blob = bytearray(HEADERS_SIZE)

    title = "Fixture"
    struct.pack_into("<3I", blob, cert_off, 0x1D0, 0x3F000000, 0x45410036)
    blob[cert_off + 0x0C:cert_off + 0x0C + len(title) * 2] = \
        title.encode("utf-16-le")
    struct.pack_into("<5I", blob, cert_off + 0x9C, 1, 2, 3, 4, 7)

    bodies, raw_off, name_ptr = [], HEADERS_SIZE, names_off
    for index, (name, vaddr, vsize, raw_size) in enumerate(sections):
        body = bytearray(raw_size)
        for word in range(raw_size // 4):
            struct.pack_into("<I", body, word * 4,
                             0xA0000000 | (index << 16) | word)
        blob[name_ptr:name_ptr + len(name)] = name.encode("latin-1")
        header = sect_off + index * SECTION_HEADER_SIZE
        struct.pack_into("<6I", blob, header, 0x04, vaddr, vsize, raw_off,
                         raw_size, base + name_ptr)
        digest = (hashlib.sha1(b"not the rule at all").digest()
                  if name in broken_digests
                  else hashlib.sha1(struct.pack("<I", raw_size)
                                    + bytes(body)).digest())
        blob[header + 0x24:header + 0x24 + 20] = digest
        bodies.append(body)
        raw_off += raw_size
        name_ptr += len(name) + 1

    for index, lib in enumerate((b"XAPILIB", b"LIBCMT")):
        off = libs_off + index * 0x10
        blob[off:off + len(lib)] = lib
        struct.pack_into("<4H", blob, off + 8, 1, 0, 5455, 0)

    entry, thunk = sections[0][1] + 0x10, sections[0][1] + 0x20
    size_of_image = max(v + s for _n, v, s, _r in sections) - base
    blob[0:4] = b"XBEH"
    struct.pack_into("<I", blob, 0x104, base)
    struct.pack_into("<I", blob, 0x108, HEADERS_SIZE)
    struct.pack_into("<I", blob, 0x10C, size_of_image)
    struct.pack_into("<I", blob, 0x118, base + cert_off)
    struct.pack_into("<I", blob, 0x11C, len(sections))
    struct.pack_into("<I", blob, 0x120, base + sect_off)
    struct.pack_into("<I", blob, 0x128, entry ^ ENTRY_XOR[build])
    struct.pack_into("<I", blob, 0x158, thunk ^ THUNK_XOR[build])
    struct.pack_into("<I", blob, 0x160, 2)
    struct.pack_into("<I", blob, 0x164, base + libs_off)

    for body in bodies:
        blob += body
    return bytes(blob)


def load():
    return Xbe(xbe_bytes(), path="<fixture>")


def place(vaddr, new=b"\xEB\x0F", expect=None, enabled=True, xbe=None):
    patch = patch_xbe.Patch("t", 1, enabled, vaddr, new, expect, "")
    return patch_xbe.classify(patch, xbe or load())


def _temp(suffix, content=b""):
    handle, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(handle, "wb") as out:
        out.write(content)
    return Path(path)


# --------------------------------------------------------------------------
# the patch spec
# --------------------------------------------------------------------------

class ParseText(unittest.TestCase):
    def test_a_line_is_an_address_and_the_bytes_to_put_there(self):
        patches = patch_xbe.parse_text("0x0011E2A0 = EB 0F\n")
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0].vaddr, 0x0011E2A0)
        self.assertEqual(patches[0].new, b"\xEB\x0F")
        self.assertIsNone(patches[0].expect)
        self.assertTrue(patches[0].enabled)
        self.assertEqual(patches[0].line, 1)

    def test_the_expected_stock_bytes_are_optional_and_kept(self):
        patches = patch_xbe.parse_text("0011E2A0 = EB0F : 740F\n")
        self.assertEqual(patches[0].expect, b"\x74\x0F")

    def test_comments_and_blanks_carry_no_patches(self):
        patches = patch_xbe.parse_text(
            "# a comment\n// another\n; and another\n\n   \n"
            "0x11000 = 90\n")
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0].line, 6)

    def test_hex_may_be_spaced_or_run_together_or_0x_prefixed(self):
        for text in ("0x11000 = DEADBEEF", "0x11000 = DE AD BE EF",
                     "0x11000 = 0xDE,0xAD,0xBE,0xEF"):
            self.assertEqual(patch_xbe.parse_text(text)[0].new,
                             b"\xDE\xAD\xBE\xEF")

    def test_an_odd_number_of_hex_digits_is_refused(self):
        with self.assertRaises(patch_xbe.PatchError) as caught:
            patch_xbe.parse_text("0x11000 = EB0")
        self.assertIn("pairs", str(caught.exception))

    def test_an_empty_replacement_is_refused(self):
        with self.assertRaises(patch_xbe.PatchError) as caught:
            patch_xbe.parse_text("0x11000 = ")
        self.assertIn("did not ship", str(caught.exception))

    def test_a_non_hex_address_is_refused(self):
        with self.assertRaises(patch_xbe.PatchError) as caught:
            patch_xbe.parse_text("0xZZZZ = 90")
        self.assertIn("not hexadecimal", str(caught.exception))

    def test_a_bare_word_is_not_silently_ignored(self):
        with self.assertRaises(patch_xbe.PatchError) as caught:
            patch_xbe.parse_text("nonsense")
        self.assertIn("not a 'VA = BYTES' line", str(caught.exception))

    def test_a_replacement_of_a_different_length_than_expected_is_refused(self):
        """Same-size only: the check that keeps the file from growing."""
        with self.assertRaises(patch_xbe.PatchError) as caught:
            patch_xbe.parse_text("0x11000 = EB0F : 74")
        self.assertIn("same-size", str(caught.exception))


class ParseJson(unittest.TestCase):
    def test_a_list_and_a_patches_object_mean_the_same(self):
        for text in ('[{"va": "0x11000", "new": "90"}]',
                     '{"patches": [{"va": "0x11000", "new": "90"}]}'):
            patches = patch_xbe.parse_json(text)
            self.assertEqual(len(patches), 1)
            self.assertEqual(patches[0].vaddr, 0x11000)
            self.assertEqual(patches[0].new, b"\x90")

    def test_it_carries_the_expected_bytes_a_note_and_the_parked_flag(self):
        patches = patch_xbe.parse_json(json.dumps({"patches": [
            {"va": "0x11000", "new": "EB0F", "expect": "740F",
             "note": "C1", "enabled": False}]}))
        self.assertEqual(patches[0].expect, b"\x74\x0F")
        self.assertEqual(patches[0].note, "C1")
        self.assertFalse(patches[0].enabled)

    def test_an_unknown_key_is_refused_rather_than_ignored(self):
        with self.assertRaises(patch_xbe.PatchError) as caught:
            patch_xbe.parse_json('[{"va": "0x11000", "new": "90", "wdith": 2}]')
        self.assertIn("unknown key", str(caught.exception))

    def test_a_declared_length_that_disagrees_is_refused(self):
        with self.assertRaises(patch_xbe.PatchError) as caught:
            patch_xbe.parse_json(
                '[{"va": "0x11000", "new": "90", "length": 5}]')
        self.assertIn("Same-size only", str(caught.exception))

    def test_a_missing_field_is_refused(self):
        for text in ('[{"va": "0x11000"}]', '[{"new": "90"}]'):
            with self.assertRaises(patch_xbe.PatchError):
                patch_xbe.parse_json(text)

    def test_malformed_json_says_so(self):
        with self.assertRaises(patch_xbe.PatchError) as caught:
            patch_xbe.parse_json("{not json")
        self.assertIn("not valid JSON", str(caught.exception))


class ParseCliAndFiles(unittest.TestCase):
    def test_the_command_line_form(self):
        patch = patch_xbe.parse_cli_patch("0x0011E2A0=EB0F:740F")
        self.assertEqual((patch.vaddr, patch.new, patch.expect),
                         (0x0011E2A0, b"\xEB\x0F", b"\x74\x0F"))

    def test_a_command_line_patch_without_an_equals_is_refused(self):
        with self.assertRaises(patch_xbe.PatchError) as caught:
            patch_xbe.parse_cli_patch("0x0011E2A0 EB0F")
        self.assertIn("VA=HEXBYTES", str(caught.exception))

    def test_the_format_is_sniffed_from_the_content_not_the_suffix(self):
        text = _temp(".patch", b"0x11000 = 90\n")
        js = _temp(".patch", b'[{"va": "0x11000", "new": "90"}]')
        self.addCleanup(os.unlink, text)
        self.addCleanup(os.unlink, js)
        self.assertEqual(patch_xbe.load_patch_file(text)[0].vaddr, 0x11000)
        self.assertEqual(patch_xbe.load_patch_file(js)[0].vaddr, 0x11000)


# --------------------------------------------------------------------------
# classification -- the offset arithmetic and the four refusals
# --------------------------------------------------------------------------

class Classify(unittest.TestCase):
    def setUp(self):
        self.xbe = load()

    def test_a_file_backed_span_maps_by_subtraction(self):
        for vaddr, offset in ((TEXT_VA, TEXT_RAW),
                              (TEXT_VA + 0x40, TEXT_RAW + 0x40),
                              (TEXT_END - 2, TEXT_RAW + 0x100 - 2),
                              (NEXT_VA, NEXT_RAW),
                              (DATA_VA, DATA_RAW)):
            found = place(vaddr, xbe=self.xbe)
            self.assertEqual(found.kind, patch_xbe.FILE_BACKED)
            self.assertEqual(found.file_offset, offset)
            section = self.xbe.sections[found.section]
            self.assertEqual(found.file_offset,
                             section.raw_off + (vaddr - section.vaddr))

    def test_a_zero_fill_address_is_refused_by_name(self):
        for vaddr in (DATA_FILE_END, DATA_MAPPED_END - 2):
            found = place(vaddr, xbe=self.xbe)
            self.assertEqual(found.kind, patch_xbe.ZERO_FILL)
            self.assertIsNone(found.file_offset)
            self.assertIn("zero fill", found.reason)
            self.assertIn(".bss", found.reason)

    def test_a_span_crossing_into_the_zero_fill_tail_is_refused(self):
        """Two bytes in the file and two in .bss is not a writable span."""
        found = place(DATA_FILE_END - 2, new=b"\x01\x02\x03\x04", xbe=self.xbe)
        self.assertEqual(found.kind, patch_xbe.ZERO_FILL)
        self.assertIn("crosses the end", found.reason)
        self.assertIn("first 2 of 4", found.reason)

    def test_a_span_crossing_a_section_boundary_is_refused(self):
        found = place(TEXT_END - 2, new=b"\x01\x02\x03\x04", xbe=self.xbe)
        self.assertEqual(found.kind, patch_xbe.STRADDLE)
        self.assertIsNone(found.file_offset)
        self.assertIn("straddles", found.reason.replace("straddle", "straddles"))

    def test_a_span_ending_exactly_at_a_section_end_is_fine(self):
        found = place(TEXT_END - 4, new=b"\x01\x02\x03\x04", xbe=self.xbe)
        self.assertEqual(found.kind, patch_xbe.FILE_BACKED)

    def test_an_address_in_the_header_block_is_refused(self):
        found = place(HEADER_VA, xbe=self.xbe)
        self.assertEqual(found.kind, patch_xbe.HEADERS)
        self.assertIn("header block", found.reason)

    def test_an_address_no_section_maps_is_outside(self):
        for vaddr in (OUTSIDE_VA, GAP_VA, 0x00000000, BASE - 4):
            found = place(vaddr, xbe=self.xbe)
            self.assertEqual(found.kind, patch_xbe.OUTSIDE)
            self.assertIsNone(found.file_offset)

    def test_a_truncated_file_is_refused_before_anything_is_classified(self):
        blob = xbe_bytes()[:-8]
        with self.assertRaises(patch_xbe.PatchError) as caught:
            patch_xbe.check_sections(Xbe(blob))
        self.assertIn("truncated", str(caught.exception))


class Collisions(unittest.TestCase):
    def placements(self, *specs):
        xbe = load()
        return [patch_xbe.classify(
            patch_xbe.Patch("t", index, True, vaddr, new, None, ""), xbe)
            for index, (vaddr, new) in enumerate(specs, 1)]

    def test_the_same_span_twice_with_the_same_bytes_is_a_duplicate(self):
        duplicates, conflicts = patch_xbe.collisions(
            self.placements((TEXT_VA, b"\x90\x90"), (TEXT_VA, b"\x90\x90")))
        self.assertEqual(conflicts, [])
        self.assertEqual(len(duplicates), 1)     # one report per pair

    def test_overlapping_spans_that_disagree_are_a_conflict(self):
        _duplicates, conflicts = patch_xbe.collisions(
            self.placements((TEXT_VA, b"\x90\x90"), (TEXT_VA + 1, b"\xEB")))
        self.assertTrue(conflicts)
        self.assertIn("overlap", conflicts[0])

    def test_neighbouring_spans_do_not_collide(self):
        duplicates, conflicts = patch_xbe.collisions(
            self.placements((TEXT_VA, b"\x90\x90"), (TEXT_VA + 2, b"\xEB")))
        self.assertEqual((duplicates, conflicts), ([], []))


class StockBytes(unittest.TestCase):
    def test_a_declared_stock_value_that_is_wrong_is_reported(self):
        xbe = load()
        good = place(TEXT_VA, new=b"\x90\x90",
                     expect=xbe.data[TEXT_RAW:TEXT_RAW + 2], xbe=xbe)
        bad = place(TEXT_VA + 4, new=b"\x90\x90", expect=b"\xDE\xAD", xbe=xbe)
        problems = patch_xbe.stock_mismatches([good, bad], xbe.data)
        self.assertEqual(len(problems), 1)
        self.assertIn("0x00011004", problems[0])
        self.assertIn("DEAD", problems[0])


# --------------------------------------------------------------------------
# the write
# --------------------------------------------------------------------------

class Apply(unittest.TestCase):
    def setUp(self):
        self.before = xbe_bytes()
        self.xbe = Xbe(self.before, path="<fixture>")

    def apply(self, *specs):
        placements = [patch_xbe.classify(
            patch_xbe.Patch("t", index, enabled, vaddr, new, None, ""), self.xbe)
            for index, (vaddr, new, enabled) in enumerate(specs, 1)]
        return patch_xbe.apply_patches(self.xbe, placements)

    def test_the_bytes_land_where_the_mapping_says(self):
        after, records, touched = self.apply((TEXT_VA, b"\xEB\x0F", True),
                                             (DATA_VA + 8, b"\x01\x02", True))
        self.assertEqual(bytes(after[TEXT_RAW:TEXT_RAW + 2]), b"\xEB\x0F")
        self.assertEqual(bytes(after[DATA_RAW + 8:DATA_RAW + 10]), b"\x01\x02")
        self.assertEqual(records[0]["file_offset"], "0x00000400")
        self.assertEqual(records[0]["old"], "0000")
        self.assertEqual(records[0]["new"], "EB0F")
        self.assertEqual(records[0]["section"], ".text")
        # The fixture's words are 0xA0<section><word>, so a misplaced write is
        # visible in the old bytes: word 2 of section 2, and nothing else.
        self.assertEqual(records[1]["old"], "0200")
        self.assertEqual(records[1]["section"], ".data")
        self.assertTrue(records[0]["changed"])
        self.assertEqual(sorted(touched), [0, 2])

    def test_nothing_else_in_the_file_moves(self):
        after, _records, _touched = self.apply((DATA_VA + 4, b"\xFF\xFF", True))
        offset = DATA_RAW + 4
        self.assertEqual(len(after), len(self.before))
        self.assertEqual(bytes(after[:offset]), self.before[:offset])
        self.assertEqual(bytes(after[offset + 2:]), self.before[offset + 2:])

    def test_bytes_already_in_place_are_written_but_flagged(self):
        stock = self.before[TEXT_RAW:TEXT_RAW + 2]
        after, records, _touched = self.apply((TEXT_VA, stock, True))
        self.assertEqual(bytes(after), self.before)
        self.assertTrue(records[0]["written"])
        self.assertFalse(records[0]["changed"])

    def test_a_parked_patch_is_recorded_and_not_written(self):
        after, records, touched = self.apply((TEXT_VA, b"\xDE\xAD", False))
        self.assertEqual(bytes(after), self.before)
        self.assertFalse(records[0]["written"])
        self.assertEqual(touched, [])

    def test_a_refused_placement_reaching_the_writer_raises(self):
        """Classification is the gate; the writer asserts it, never skips."""
        with self.assertRaises(patch_xbe.PatchError):
            self.apply((DATA_FILE_END, b"\x01\x02", True))


# --------------------------------------------------------------------------
# the section digests
# --------------------------------------------------------------------------

class Digests(unittest.TestCase):
    def setUp(self):
        self.before = xbe_bytes()
        self.xbe = Xbe(self.before, path="<fixture>")
        self.survey = patch_xbe.digest_survey(self.xbe)

    def test_the_survey_separates_the_sections_that_follow_the_rule(self):
        by_name = {entry["section"]: entry for entry in self.survey}
        self.assertFalse(by_name[".text"]["verified_before"])
        self.assertTrue(by_name["NEXT"]["verified_before"])
        self.assertTrue(by_name[".data"]["verified_before"])
        self.assertEqual(int(by_name[".text"]["digest_offset"], 16),
                         0x300 + patch_xbe.DIGEST_OFFSET)

    def test_a_touched_section_that_follows_the_rule_is_recomputed(self):
        data = bytearray(self.before)
        data[DATA_RAW] = 0xFF
        records, warnings = patch_xbe.apply_digests(data, self.xbe, [2],
                                                    self.survey, fix=True)
        self.assertEqual(records[0]["action"], "recomputed")
        self.assertEqual(warnings, [])
        section = self.xbe.sections[2]
        self.assertEqual(
            bytes.fromhex(records[0]["after"]),
            patch_xbe.computed_digest(data, section.raw_off, section.raw_size))
        header = patch_xbe.section_header_offset(self.xbe, 2)
        self.assertEqual(
            bytes(data[header + 0x24:header + 0x38]),
            patch_xbe.computed_digest(data, section.raw_off, section.raw_size))

    def test_a_section_whose_stored_digest_breaks_the_rule_is_left_alone(self):
        """The `.text` case: never write a digest under a rule it does not follow."""
        data = bytearray(self.before)
        data[TEXT_RAW] = 0xEB
        records, warnings = patch_xbe.apply_digests(data, self.xbe, [0],
                                                    self.survey, fix=True)
        self.assertIn("left untouched", records[0]["action"])
        self.assertEqual(records[0]["before"], records[0]["after"])
        self.assertEqual(bytes(data[0x300 + 0x24:0x300 + 0x38]),
                         self.before[0x300 + 0x24:0x300 + 0x38])
        self.assertEqual(len(warnings), 1)
        self.assertIn("may reject", warnings[0])
        self.assertIn("softmodded", warnings[0])

    def test_no_fix_digests_leaves_them_stale_and_says_so(self):
        data = bytearray(self.before)
        data[DATA_RAW] = 0xFF
        records, warnings = patch_xbe.apply_digests(data, self.xbe, [2],
                                                    self.survey, fix=False)
        self.assertIn("stale", records[0]["action"])
        self.assertEqual(bytes(data[0x300 + 2 * SECTION_HEADER_SIZE + 0x24:
                                    0x300 + 2 * SECTION_HEADER_SIZE + 0x38]),
                         self.before[0x300 + 2 * SECTION_HEADER_SIZE + 0x24:
                                     0x300 + 2 * SECTION_HEADER_SIZE + 0x38])
        self.assertEqual(len(warnings), 1)
        self.assertIn("--no-fix-digests", warnings[0])

    def test_an_untouched_sections_digest_is_never_rewritten(self):
        data = bytearray(self.before)
        data[DATA_RAW] = 0xFF
        patch_xbe.apply_digests(data, self.xbe, [2], self.survey, fix=True)
        for index in (0, 1):
            header = patch_xbe.section_header_offset(self.xbe, index)
            self.assertEqual(bytes(data[header + 0x24:header + 0x38]),
                             self.before[header + 0x24:header + 0x38])

    def test_the_rule_is_the_one_measured_on_the_retail_image(self):
        section = self.xbe.sections[1]
        self.assertEqual(
            patch_xbe.computed_digest(self.before, section.raw_off,
                                      section.raw_size),
            hashlib.sha1(struct.pack("<I", section.raw_size)
                         + self.before[section.raw_off:
                                       section.raw_off + section.raw_size]
                         ).digest())


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

class Verify(unittest.TestCase):
    def setUp(self):
        self.before = xbe_bytes()
        xbe = Xbe(self.before, path="<fixture>")
        self.xbe = xbe
        placements = [patch_xbe.classify(
            patch_xbe.Patch("t", 1, True, TEXT_VA, b"\xEB\x0F", None, ""), xbe),
            patch_xbe.classify(
                patch_xbe.Patch("t", 2, True, DATA_VA, b"\x01\x02", None, ""),
                xbe)]
        data, self.records, touched = patch_xbe.apply_patches(xbe, placements)
        self.digests, _warnings = patch_xbe.apply_digests(
            data, xbe, touched, patch_xbe.digest_survey(xbe), fix=True)
        self.after = bytes(data)

    def test_a_good_write_has_no_problems(self):
        self.assertEqual(
            patch_xbe.verify(self.before, self.after, self.records,
                             self.digests), [])
        self.assertEqual(patch_xbe.reparse(self.after, self.xbe), [])

    def test_a_byte_that_did_not_land_is_reported(self):
        broken = bytearray(self.after)
        broken[TEXT_RAW] = 0x00
        problems = patch_xbe.verify(self.before, bytes(broken), self.records,
                                    self.digests)
        self.assertTrue(any("0x00011000" in text and "read 000F" in text
                            for text in problems), problems)

    def test_a_stray_byte_no_patch_covers_is_reported(self):
        """The check that catches a bug in this tool, not in the patch spec."""
        stray = bytearray(self.after)
        stray[TEXT_RAW + 0x40] ^= 0xFF
        problems = patch_xbe.verify(self.before, bytes(stray), self.records,
                                    self.digests)
        self.assertTrue(any("no patch covers it" in text for text in problems),
                        problems)

    def test_a_digest_that_was_not_written_as_recorded_is_reported(self):
        tampered = bytearray(self.after)
        header = patch_xbe.section_header_offset(self.xbe, 2)
        tampered[header + 0x24] ^= 0xFF
        problems = patch_xbe.verify(self.before, bytes(tampered), self.records,
                                    self.digests)
        self.assertTrue(any("digest of .data" in text for text in problems),
                        problems)

    def test_a_changed_size_is_reported_first_and_alone(self):
        problems = patch_xbe.verify(self.before, self.after + b"\x00",
                                    self.records, self.digests)
        self.assertEqual(len(problems), 1)
        self.assertIn("must not change the size", problems[0])

    def test_an_output_that_no_longer_parses_is_reported(self):
        broken = bytearray(self.after)
        broken[0x11C] = 0x40                     # absurd section count
        self.assertTrue(patch_xbe.reparse(bytes(broken), self.xbe))


# --------------------------------------------------------------------------
# the command line
# --------------------------------------------------------------------------

class Cli(unittest.TestCase):
    def setUp(self):
        self.stock = xbe_bytes()
        self.xbe_path = _temp(".xbe", self.stock)
        self.out = Path(tempfile.mkdtemp()) / "patched.xbe"
        self.addCleanup(os.unlink, self.xbe_path)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = patch_xbe.main([str(a) for a in argv])
        return code, out.getvalue(), err.getvalue()

    def test_a_clean_run_writes_the_xbe_the_manifest_and_verifies(self):
        code, out, _err = self.run_cli(
            self.xbe_path, "--patch", "0x%X=EB0F" % TEXT_VA,
            "--patch", "0x%X=41424344" % DATA_VA, "-o", self.out, "--verify")
        self.assertEqual(code, 0)
        patched = self.out.read_bytes()
        self.assertEqual(len(patched), len(self.stock))
        self.assertEqual(patched[TEXT_RAW:TEXT_RAW + 2], b"\xEB\x0F")
        self.assertEqual(patched[DATA_RAW:DATA_RAW + 4], b"ABCD")
        self.assertIn("2/2 spans read back", out)
        self.assertEqual(self.xbe_path.read_bytes(), self.stock)

        manifest = json.loads(
            Path(str(self.out) + ".manifest.json").read_text())
        self.assertEqual(manifest["summary"]["file_backed"], 2)
        self.assertEqual(manifest["summary"]["bytes_written"], 6)
        self.assertEqual(manifest["summary"]["digests_recomputed"], 1)
        self.assertEqual(manifest["summary"]["digests_left_alone"], 1)
        self.assertNotEqual(manifest["xbe"]["sha256_before"],
                            manifest["xbe"]["sha256_after"])
        self.assertEqual(manifest["patches"][0]["old"], "0000")
        self.assertEqual(manifest["patches"][0]["new"], "EB0F")
        self.assertEqual(manifest["patches"][0]["file_offset"], "0x00000400")

    def test_the_input_is_never_the_output(self):
        code, _out, err = self.run_cli(self.xbe_path, "--patch",
                                       "0x%X=90" % TEXT_VA, "-o", self.xbe_path)
        self.assertEqual(code, 2)
        self.assertIn("names the input", err)
        self.assertEqual(self.xbe_path.read_bytes(), self.stock)

    def test_no_output_and_no_audit_is_refused(self):
        code, _out, err = self.run_cli(self.xbe_path, "--patch",
                                       "0x%X=90" % TEXT_VA)
        self.assertEqual(code, 2)
        self.assertIn("-o OUTPUT", err)

    def test_no_patches_at_all_is_refused(self):
        code, _out, err = self.run_cli(self.xbe_path, "-o", self.out)
        self.assertEqual(code, 2)
        self.assertIn("no patches given", err)
        self.assertFalse(self.out.exists())

    def test_a_zero_fill_patch_refuses_the_whole_run(self):
        """One unwritable line stops the set: a partial write is a silent one."""
        code, _out, err = self.run_cli(
            self.xbe_path, "--patch", "0x%X=EB0F" % TEXT_VA,
            "--patch", "0x%X=01" % DATA_FILE_END, "-o", self.out)
        self.assertEqual(code, 2)
        self.assertIn("zero fill", err)
        self.assertIn("nothing was written", err)
        self.assertFalse(self.out.exists())

    def test_a_straddling_patch_refuses(self):
        code, _out, err = self.run_cli(
            self.xbe_path, "--patch", "0x%X=01020304" % (TEXT_END - 2),
            "-o", self.out)
        self.assertEqual(code, 2)
        self.assertIn("straddling a section boundary", err)
        self.assertFalse(self.out.exists())

    def test_an_address_outside_every_section_refuses(self):
        code, _out, err = self.run_cli(self.xbe_path, "--patch",
                                       "0x%X=90" % OUTSIDE_VA, "-o", self.out)
        self.assertEqual(code, 2)
        self.assertIn("outside every section", err)
        self.assertFalse(self.out.exists())

    def test_a_header_patch_refuses(self):
        code, _out, err = self.run_cli(self.xbe_path, "--patch",
                                       "0x%X=90" % HEADER_VA, "-o", self.out)
        self.assertEqual(code, 2)
        self.assertIn("header block", err)
        self.assertFalse(self.out.exists())

    def test_a_wrong_stock_assertion_refuses_before_writing(self):
        code, _out, err = self.run_cli(
            self.xbe_path, "--patch", "0x%X=EB0F:DEAD" % TEXT_VA,
            "-o", self.out)
        self.assertEqual(code, 2)
        self.assertIn("does not match the bytes it declared", err)
        self.assertFalse(self.out.exists())

    def test_overlapping_patches_refuse_until_allowed(self):
        code, _out, err = self.run_cli(
            self.xbe_path, "--patch", "0x%X=9090" % TEXT_VA,
            "--patch", "0x%X=EB" % (TEXT_VA + 1), "-o", self.out)
        self.assertEqual(code, 2)
        self.assertIn("overlap", err)
        self.assertFalse(self.out.exists())

        code, _out, _err = self.run_cli(
            self.xbe_path, "--patch", "0x%X=9090" % TEXT_VA,
            "--patch", "0x%X=EB" % (TEXT_VA + 1), "-o", self.out,
            "--allow-conflicts", "--verify")
        self.assertEqual(code, 0)
        self.assertEqual(self.out.read_bytes()[TEXT_RAW:TEXT_RAW + 2],
                         b"\x90\xEB")

    def test_audit_classifies_without_writing_anything(self):
        code, out, _err = self.run_cli(self.xbe_path, "--patch",
                                       "0x%X=90" % TEXT_VA, "--audit")
        self.assertEqual(code, 0)
        self.assertFalse(self.out.exists())
        manifest = json.loads(out[out.index("{"):])
        self.assertIsNone(manifest["xbe"]["output"])
        self.assertIsNone(manifest["xbe"]["sha256_after"])
        self.assertEqual(manifest["summary"]["file_backed"], 1)

    def test_a_patch_file_and_a_command_line_patch_combine(self):
        spec = _temp(".patch", ("0x%X = EB 0F\n" % TEXT_VA).encode())
        self.addCleanup(os.unlink, spec)
        code, _out, _err = self.run_cli(self.xbe_path, spec, "--patch",
                                        "0x%X=41" % DATA_VA, "-o", self.out,
                                        "--verify")
        self.assertEqual(code, 0)
        patched = self.out.read_bytes()
        self.assertEqual(patched[TEXT_RAW:TEXT_RAW + 2], b"\xEB\x0F")
        self.assertEqual(patched[DATA_RAW], 0x41)
        manifest = json.loads(
            Path(str(self.out) + ".manifest.json").read_text())
        self.assertEqual(len(manifest["spec"]), 1)
        self.assertIn("sha256", manifest["spec"][0])

    def test_the_touched_sections_digest_is_updated_by_default(self):
        code, _out, _err = self.run_cli(self.xbe_path, "--patch",
                                        "0x%X=41" % DATA_VA, "-o", self.out,
                                        "--verify")
        self.assertEqual(code, 0)
        patched = self.out.read_bytes()
        xbe = Xbe(patched)
        section = xbe.sections[2]
        header = patch_xbe.section_header_offset(xbe, 2)
        self.assertEqual(
            patch_xbe.stored_digest(patched, header),
            patch_xbe.computed_digest(patched, section.raw_off,
                                      section.raw_size))

    def test_no_fix_digests_leaves_it_stale_and_warns(self):
        code, _out, err = self.run_cli(self.xbe_path, "--patch",
                                       "0x%X=41" % DATA_VA, "-o", self.out,
                                       "--no-fix-digests", "--verify")
        self.assertEqual(code, 0)
        patched = self.out.read_bytes()
        header = patch_xbe.section_header_offset(Xbe(patched), 2)
        self.assertEqual(patch_xbe.stored_digest(patched, header),
                         patch_xbe.stored_digest(self.stock, header))
        self.assertIn("--no-fix-digests", err)

    def test_a_patch_in_a_section_whose_digest_breaks_the_rule_warns_loudly(self):
        code, _out, err = self.run_cli(self.xbe_path, "--patch",
                                       "0x%X=EB0F" % TEXT_VA, "-o", self.out,
                                       "--verify")
        self.assertEqual(code, 0)
        self.assertIn("does not reproduce under", err)
        self.assertIn("may reject", err)
        header = patch_xbe.section_header_offset(Xbe(self.out.read_bytes()), 0)
        self.assertEqual(patch_xbe.stored_digest(self.out.read_bytes(), header),
                         patch_xbe.stored_digest(self.stock, header))

    def test_a_missing_xbe_exits_two(self):
        code, _out, err = self.run_cli("/nonexistent.xbe", "--patch",
                                       "0x11000=90", "-o", self.out)
        self.assertEqual(code, 2)
        self.assertIn("no XBE", err)

    def test_a_bad_patch_file_exits_two_without_writing(self):
        spec = _temp(".patch", b"0x11000 = ZZ\n")
        self.addCleanup(os.unlink, spec)
        code, _out, err = self.run_cli(self.xbe_path, spec, "-o", self.out)
        self.assertEqual(code, 2)
        self.assertIn("not hexadecimal", err)
        self.assertFalse(self.out.exists())

    def test_a_failed_run_leaves_no_partial_output(self):
        code, _out, _err = self.run_cli(self.xbe_path, "--patch",
                                        "0x%X=90" % OUTSIDE_VA, "-o", self.out)
        self.assertEqual(code, 2)
        self.assertFalse(self.out.exists())
        self.assertFalse(self.out.with_name(self.out.name + ".partial").exists())


if __name__ == "__main__":
    unittest.main()

"""Tests for the XBE free-space surveyor.

Two layers, and they answer two different questions.

The **synthetic layer** builds complete little XBEs with known answers planted
in them. Its most important tests are the negative ones: a region that *is*
referenced must not be reported free. This project has shipped four regions
documented as safe and later found live -- reached by a tail call, by a
mid-function address, by a pointer word in a data section, and by an address a
compiler built in a register. The x86 analogues of the first three are each
planted here as a fixture (`call` into the region, `jcc` into the region, an
absolute VA word in `.data` aiming at the region's middle) and each one must
suppress the region it points into. There is also a fixture for the axis with
no MIPS analogue at all: the entry point, which an XBE stores XOR'd, so its
plain address appears nowhere in the file and only a hand-added axis can see
it.

The **image-backed layer** pins the numbers measured against the operator's
dump so a future change to the tool cannot quietly move them: 1,624 bytes of
header headroom above SizeOfHeaders, a section table that cannot grow in place
because the shared-page refcount array sits at its last byte, 30,104 bytes of
in-file slack of which **zero** have a virtual address, 92 bytes of
inter-section VA gap, 688,964 bytes of unbakeable virtual zero-fill, and 10 of
11 section digests verifying under `SHA-1(le32(raw_size) || raw_bytes)`.
"""
import hashlib
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recon.xbe import ENTRY_XOR, THUNK_XOR, Xbe  # noqa: E402
from tools import xbe_space  # noqa: E402

IMAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "extract", "xbox", "default.xbe")

BASE = 0x00010000

try:
    import capstone  # noqa: F401
    HAVE_CAPSTONE = True
except ImportError:
    HAVE_CAPSTONE = False


# ---------------------------------------------------------------------------
# fixture builder
# ---------------------------------------------------------------------------

class Sec:
    """One section of a synthetic XBE, with its raw placement under test control.

    `raw_off` is explicit so a fixture can leave file-alignment slack between
    sections, which is the whole point of the class-2 tests.
    """

    def __init__(self, name, vaddr, raw_off, body, vsize=None, flags=0x06):
        self.name = name
        self.vaddr = vaddr
        self.raw_off = raw_off
        self.body = bytes(body)
        self.raw_size = len(self.body)
        self.vsize = self.raw_size if vsize is None else vsize
        self.flags = flags


def build_xbe(sections, entry=None, size_of_headers=None, pack_after_table=True,
              corrupt_digest=None, tail_slack=0, size_of_image=None,
              build="retail"):
    """A structurally complete XBE with everything the surveyor reads.

    `pack_after_table=False` moves the refcount array, the name pool and the
    library table well above the section table, leaving free bytes immediately
    behind it -- the "the table CAN grow in place" fixture, which exists so the
    retail answer ("it cannot") is a measurement and not a constant.
    """
    cert_off, cert_size = 0x184, 0x100
    table_off = cert_off + cert_size
    n = len(sections)
    table_size = n * xbe_space.SECTION_HEADER_SIZE

    if pack_after_table:
        refs_off = table_off + table_size
    else:
        refs_off = 0x800
    names_off = refs_off + (n + 1) * 2
    libs_off = names_off + sum(len(s.name) + 1 for s in sections)
    header_end = libs_off + 0x10

    first_raw = min(s.raw_off for s in sections)
    blob = bytearray(first_raw)

    # --- certificate
    struct.pack_into("<3I", blob, cert_off, cert_size, 0x3F000000, 0x45410036)
    title = "Synthetic".encode("utf-16-le")
    blob[cert_off + 0x0C:cert_off + 0x0C + len(title)] = title
    struct.pack_into("<5I", blob, cert_off + 0x9C, 1, 2, 3, 4, 7)

    # --- names
    cursor = names_off
    name_va = {}
    for s in sections:
        name_va[s.name] = BASE + cursor
        blob[cursor:cursor + len(s.name)] = s.name.encode("latin-1")
        cursor += len(s.name) + 1

    # --- section headers (digest computed under the verified rule)
    for i, s in enumerate(sections):
        off = table_off + i * xbe_space.SECTION_HEADER_SIZE
        struct.pack_into("<9I", blob, off, s.flags, s.vaddr, s.vsize, s.raw_off,
                         s.raw_size, name_va[s.name], 0,
                         BASE + refs_off + i * 2, BASE + refs_off + (i + 1) * 2)
        digest = hashlib.sha1(struct.pack("<I", s.raw_size) + s.body).digest()
        if corrupt_digest == s.name:
            digest = bytes((digest[0] ^ 0xFF,)) + digest[1:]
        blob[off + xbe_space.S_DIGEST:off + xbe_space.S_DIGEST + 20] = digest

    # --- library table
    blob[libs_off:libs_off + 6] = b"LIBCMT"
    struct.pack_into("<4H", blob, libs_off + 8, 1, 0, 5455, 0)

    # --- header
    top = max(s.vaddr + s.vsize for s in sections)
    if size_of_image is None:
        size_of_image = top - BASE
    if entry is None:
        entry = sections[0].vaddr
    blob[0:4] = b"XBEH"
    struct.pack_into("<I", blob, 0x104, BASE)
    struct.pack_into("<I", blob, 0x108,
                     header_end if size_of_headers is None else size_of_headers)
    struct.pack_into("<I", blob, 0x10C, size_of_image)
    struct.pack_into("<I", blob, 0x110, 0x184)
    struct.pack_into("<I", blob, 0x118, BASE + cert_off)
    struct.pack_into("<I", blob, 0x11C, n)
    struct.pack_into("<I", blob, 0x120, BASE + table_off)
    struct.pack_into("<I", blob, 0x128, entry ^ ENTRY_XOR[build])
    struct.pack_into("<I", blob, 0x158, sections[0].vaddr ^ THUNK_XOR[build])
    struct.pack_into("<I", blob, 0x160, 1)
    struct.pack_into("<I", blob, 0x164, BASE + libs_off)

    # --- section bodies at their declared raw offsets, gaps left as zeros
    end = max(s.raw_off + s.raw_size for s in sections) + tail_slack
    blob.extend(bytes(end - len(blob)))
    for s in sections:
        blob[s.raw_off:s.raw_off + s.raw_size] = s.body
    return bytes(blob)


def write_xbe(tmpdir, data, name="synthetic.xbe"):
    path = os.path.join(tmpdir, name)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


# --- a tiny x86 assembler, only what the census fixtures need ---------------

NOP, RET, INT3 = 0x90, 0xC3, 0xCC


def text_body(size, pieces):
    """`pieces` is {offset_in_section: bytes}; everything else is int3 padding.

    int3 is the filler on purpose: it is what MSVC emits between functions and
    it is a terminator, so padding never makes a fragment look reachable by
    fall-through.
    """
    body = bytearray([INT3]) * size
    for at, data in pieces.items():
        body[at:at + len(data)] = data
    return bytes(body)


def call_rel32(site_va, target_va):
    return b"\xE8" + struct.pack("<i", target_va - (site_va + 5))


def jcc_rel8(site_va, target_va):
    return b"\x74" + struct.pack("<b", target_va - (site_va + 2))


# The census fixture's map. LIVE1 is called by nothing but is the entry point;
# DEAD is guarded by LIVE1's `ret` and reaches nothing; LIVE2 is called from
# LIVE1 and therefore bounds DEAD from above.
TEXT_VA, TEXT_RAW, TEXT_SIZE = 0x00011000, 0x1000, 0x200
LIVE1, DEAD, LIVE2 = 0x00011000, 0x00011010, 0x00011100


def census_sections(extra_data=b"", dead_start=DEAD, live1_terminates=True):
    text = text_body(TEXT_SIZE, {
        LIVE1 - TEXT_VA: (b"\x55\x89\xE5"                       # push ebp; mov ebp,esp
                          + call_rel32(LIVE1 + 3, LIVE2)
                          + (bytes([RET]) if live1_terminates else bytes([NOP]))),
        dead_start - TEXT_VA: bytes([NOP]) * 63 + bytes([RET]),
        LIVE2 - TEXT_VA: bytes([NOP, NOP, RET]),
    })
    secs = [Sec(".text", TEXT_VA, TEXT_RAW, text, flags=0x06)]
    if extra_data:
        secs.append(Sec(".data", 0x00012000, 0x2000, extra_data, flags=0x03))
    return secs


# ---------------------------------------------------------------------------
# class 1 -- header headroom and the section-append recipe
# ---------------------------------------------------------------------------

class HeaderHeadroomTest(unittest.TestCase):

    def _survey(self, **kwargs):
        data = build_xbe([Sec(".text", 0x11000, 0x1000, bytes(0x100))], **kwargs)
        xbe = Xbe(data)
        layout = xbe_space.header_layout(xbe)
        return xbe, layout, xbe_space.section_append(xbe, layout)

    def test_free_run_between_headers_and_first_section(self):
        _xbe, layout, append = self._survey()
        run = max(layout["free_runs"], key=lambda r: r["size"])
        self.assertEqual(run["end"], 0x1000)
        self.assertTrue(run["zero_filled"])
        self.assertEqual(append["header_headroom_bytes"], run["size"])
        self.assertEqual(append["header_ceiling_off"], 0x1000)

    def test_table_cannot_grow_when_refcounts_sit_behind_it(self):
        """The retail shape: section i's tail slot is section i+1's head slot,
        and the array starts at the table's last byte."""
        _xbe, _layout, append = self._survey(pack_after_table=True)
        table = append["section_table"]
        self.assertFalse(table["can_grow_in_place"])
        self.assertIn("refcount", table["blocked_by"])

    def test_table_can_grow_when_nothing_follows_it(self):
        """The measurement must be a measurement -- prove the other answer is
        reachable, so 'cannot grow' is not a constant wearing a function."""
        _xbe, _layout, append = self._survey(pack_after_table=False)
        self.assertTrue(append["section_table"]["can_grow_in_place"])

    def test_append_plan_names_the_four_header_fields(self):
        _xbe, _layout, append = self._survey()
        names = {f["name"] for f in append["fields_to_update"]}
        self.assertEqual(names, {"SizeOfHeaders", "SizeOfImage",
                                 "NumberOfSections", "SectionHeadersAddress"})

    def test_new_section_lands_above_the_image_and_at_the_file_end(self):
        xbe, _layout, append = self._survey()
        self.assertEqual(append["raw_placement_off"], len(xbe.data))
        self.assertGreaterEqual(append["va_placement"], xbe.base + xbe.size_of_image)
        self.assertEqual(append["va_placement"] % xbe_space.PAGE, 0)


# ---------------------------------------------------------------------------
# class 2 -- slack, and the proof that it is not a cave
# ---------------------------------------------------------------------------

class FileSlackTest(unittest.TestCase):

    def test_slack_between_sections_has_no_virtual_address(self):
        data = build_xbe([Sec(".text", 0x11000, 0x1000, bytes(0x100)),
                          Sec(".data", 0x12000, 0x2000, bytes(0x80))],
                         tail_slack=0x40)
        slack = xbe_space.file_slack(Xbe(data))
        sizes = {(r["off"], r["size"]) for r in slack["runs"]}
        self.assertIn((0x1100, 0xF00), sizes)          # .text tail -> .data
        self.assertIn((0x2080, 0x40), sizes)           # tail after the last section
        self.assertEqual(slack["mapped_bytes"], 0)
        self.assertEqual(slack["usable_for_code_bytes"], 0)
        self.assertIn("NOT USABLE FOR CODE", slack["verdict"])

    def test_every_slack_byte_is_asked_individually(self):
        """Not a spot check: the run's `mapped_bytes` is a per-byte count, so a
        single loadable byte anywhere in 29 KB would show up."""
        data = build_xbe([Sec(".text", 0x11000, 0x1000, bytes(0x100)),
                          Sec(".data", 0x12000, 0x2000, bytes(0x80))])
        xbe = Xbe(data)
        slack = xbe_space.file_slack(xbe)
        for run in slack["runs"]:
            for off in range(run["off"], run["end"]):
                self.assertIsNone(xbe.off_to_va(off))

    def test_mapped_slack_is_reported_as_a_finding_not_swallowed(self):
        """The guard must be wired to the real address map, not decorative.

        With a well-formed section table the runs are the complement of the
        section spans, so `off_to_va` provably answers None for all of them --
        which means the only way to exercise the other branch is to make the
        map disagree. This stubs `off_to_va` to claim one run byte is mapped
        and requires the report to change its mind, rather than printing
        "NOT USABLE FOR CODE" regardless of what it just measured.
        """
        data = build_xbe([Sec(".text", 0x11000, 0x1000, bytes(0x100)),
                          Sec(".data", 0x12000, 0x2000, bytes(0x80))])

        class Disagreeing(Xbe):
            def off_to_va(self, off):
                if off == 0x1500:
                    return 0x11500
                return super().off_to_va(off)

        slack = xbe_space.file_slack(Disagreeing(data))
        self.assertEqual(slack["mapped_bytes"], 1)
        self.assertEqual(slack["usable_for_code_bytes"], 1)
        self.assertTrue(slack["verdict"].startswith("FINDING"))
        self.assertIn("0x1500", slack["verdict"])
        run = next(r for r in slack["runs"] if r["mapped_bytes"])
        self.assertEqual(run["first_mapped_off"], 0x1500)
        self.assertEqual(run["first_mapped_va"], "0x00011500")


# ---------------------------------------------------------------------------
# classes 4 and 5
# ---------------------------------------------------------------------------

class VaRoomAndZeroFillTest(unittest.TestCase):

    def test_inter_section_gap_is_measured(self):
        data = build_xbe([Sec(".text", 0x11000, 0x1000, bytes(0x100)),
                          Sec(".data", 0x11140, 0x2000, bytes(0x80))])
        room = xbe_space.va_room(Xbe(data))
        self.assertEqual(room["total_gap_bytes"], 0x40)
        self.assertEqual(room["largest_gap_bytes"], 0x40)
        self.assertEqual(room["gaps"][0]["va"], 0x11100)

    def test_virtual_zero_fill_is_a_refusal_class(self):
        data = build_xbe([Sec(".text", 0x11000, 0x1000, bytes(0x100)),
                          Sec(".data", 0x12000, 0x2000, bytes(0x80), vsize=0x480)])
        zf = xbe_space.virtual_zero_fill(Xbe(data))
        self.assertEqual(zf["total_bytes"], 0x400)
        self.assertEqual(zf["regions"][0]["va"], 0x12080)
        self.assertIn("UNBAKEABLE", zf["verdict"])


# ---------------------------------------------------------------------------
# constraints
# ---------------------------------------------------------------------------

class ConstraintTest(unittest.TestCase):

    def test_digests_verify_under_the_rule(self):
        data = build_xbe([Sec(".text", 0x11000, 0x1000, bytes(0x100)),
                          Sec(".data", 0x12000, 0x2000, b"\x01\x02\x03\x04" * 8)])
        digests = xbe_space.section_digests(Xbe(data))
        self.assertEqual(digests["verify_count"], 2)
        self.assertEqual(digests["exceptions"], [])

    def test_a_corrupt_digest_is_named_not_averaged_away(self):
        data = build_xbe([Sec(".text", 0x11000, 0x1000, bytes(0x100)),
                          Sec(".data", 0x12000, 0x2000, bytes(0x80))],
                         corrupt_digest=".data")
        digests = xbe_space.section_digests(Xbe(data))
        self.assertEqual(digests["verify_count"], 1)
        self.assertEqual(digests["exceptions"], [".data"])

    def test_flags_are_decoded_and_non_executable_is_visible(self):
        self.assertEqual(xbe_space.flag_names(0x16),
                         ["PRELOAD", "EXECUTABLE", "HEAD_PAGE_READ_ONLY"])
        self.assertEqual(xbe_space.flag_names(0x38),
                         ["INSERTED_FILE", "HEAD_PAGE_READ_ONLY", "TAIL_PAGE_READ_ONLY"])
        self.assertNotIn("EXECUTABLE", xbe_space.flag_names(0x38))

    def test_trailing_section_disagreement_is_flagged(self):
        """Last-by-VA and last-by-raw are not the same question, and an emitter
        that assumes they are appends into the middle of something."""
        data = build_xbe([Sec(".text", 0x11000, 0x1000, bytes(0x100)),
                          Sec("HIGH", 0x20000, 0x2000, bytes(0x80)),
                          Sec("LOW", 0x13000, 0x3000, bytes(0x80))])
        trailing = xbe_space.trailing_section(Xbe(data))
        self.assertEqual(trailing["last_by_va"], "HIGH")
        self.assertEqual(trailing["last_by_raw"], "LOW")
        self.assertFalse(trailing["same"])
        self.assertTrue(trailing["verdict"].startswith("CAUTION"))


# ---------------------------------------------------------------------------
# class 3 -- the census, and above all its negatives
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAVE_CAPSTONE, "capstone not installed")
class CensusTest(unittest.TestCase):

    def _regions(self, sections, entry=LIVE1, min_size=32):
        xbe = Xbe(build_xbe(sections, entry=entry))
        result, indexes = xbe_space.census(xbe, [".text"], min_size)
        check = xbe_space.verify_no_reported_region_is_referenced(result, indexes)
        self.assertTrue(check["ok"], check["failures"])
        return result["regions"]

    def _covers(self, regions, va):
        return [r for r in regions if r["va"] <= va < r["va"] + r["size"]]

    def test_dead_region_is_reported(self):
        regions = self._regions(census_sections())
        self.assertTrue(self._covers(regions, DEAD),
                        "the planted dead region should be found")
        region = self._covers(regions, DEAD)[0]
        # The region opens at LIVE1's `ret`, not at DEAD: the int3 padding
        # between them is free too, and a surveyor that started at the next
        # recognisable function head would under-report every cave in the image
        # by its leading padding.
        self.assertEqual(region["va"], LIVE1 + 9)
        self.assertTrue(region["executable"])
        self.assertGreaterEqual(region["size"], 64)
        # It stops below LIVE2, which is called and therefore not free.
        self.assertLessEqual(region["va"] + region["size"], LIVE2)
        self.assertGreater(region["int3_padding_bytes"], 0)

    def test_a_call_into_the_region_suppresses_it(self):
        """The `jal` axis, x86 spelling. The call is planted mid-region, where
        a caller scan keyed on function heads would never look."""
        sections = census_sections()
        text = bytearray(sections[0].body)
        site = LIVE2 + 3
        text[site - TEXT_VA:site - TEXT_VA + 5] = call_rel32(site, DEAD + 0x10)
        sections[0] = Sec(".text", TEXT_VA, TEXT_RAW, bytes(text), flags=0x06)
        regions = self._regions(sections)
        self.assertFalse(self._covers(regions, DEAD + 0x10))

    def test_a_short_conditional_jump_into_the_region_suppresses_it(self):
        """The rel8 axis. It only exists because the census reads *decoded*
        instructions -- an unaligned byte scan for 0x7x would reject the whole
        image, and skipping rel8 entirely would miss this."""
        sections = census_sections()
        text = bytearray(sections[0].body)
        site = DEAD - 4
        text[site - TEXT_VA:site - TEXT_VA + 2] = jcc_rel8(site, DEAD + 0x20)
        sections[0] = Sec(".text", TEXT_VA, TEXT_RAW, bytes(text), flags=0x06)
        regions = self._regions(sections)
        self.assertFalse(self._covers(regions, DEAD + 0x20))

    def test_an_absolute_word_in_data_suppresses_the_region(self):
        """The axis that caught the PS2 survey's fourth bad region: a function
        pointer sitting in a data section. Aimed at the region's middle to
        prove the test is byte-granular, not head-granular."""
        pointer = struct.pack("<I", DEAD + 0x18)
        regions = self._regions(census_sections(extra_data=bytes(16) + pointer))
        self.assertFalse(self._covers(regions, DEAD + 0x18))

    def test_a_region_reachable_by_fall_through_is_not_reported(self):
        """Structural axis: with LIVE1's `ret` replaced by a `nop`, execution
        walks straight into DEAD, so DEAD is not a region no matter how clean
        its reference count is."""
        regions = self._regions(census_sections(live1_terminates=False))
        self.assertFalse([r for r in regions if r["va"] == DEAD])

    def test_the_xor_stored_entry_point_suppresses_its_region(self):
        """An XBE stores the entry point XOR'd, so the plain VA is nowhere in
        the file and the word axis cannot see it. Only the hand-added entry
        axis can -- this proves it is wired up."""
        sections = census_sections()
        xbe = Xbe(build_xbe(sections, entry=DEAD))
        self.assertEqual(xbe.entry, DEAD)
        self.assertEqual(xbe.find_le32(DEAD), [],
                         "fixture invalid: the entry VA must not appear as a "
                         "literal, or this test proves nothing")
        result, indexes = xbe_space.census(xbe, [".text"], 32)
        self.assertTrue(
            xbe_space.verify_no_reported_region_is_referenced(result, indexes)["ok"])
        self.assertFalse([r for r in result["regions"] if r["va"] == DEAD])

    def test_reported_regions_never_overlap(self):
        regions = sorted(self._regions(census_sections()), key=lambda r: r["va"])
        for first, second in zip(regions, regions[1:]):
            self.assertLessEqual(first["va"] + first["size"], second["va"])

    def test_min_size_is_respected(self):
        for size in (32, 128):
            for region in self._regions(census_sections(), min_size=size):
                self.assertGreaterEqual(region["size"], size)


# ---------------------------------------------------------------------------
# survey assembly / CLI surface
# ---------------------------------------------------------------------------

class SurveyShapeTest(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        data = build_xbe([Sec(".text", TEXT_VA, TEXT_RAW,
                              text_body(TEXT_SIZE, {0: bytes([RET])})),
                          Sec(".data", 0x12000, 0x2000, bytes(0x80), vsize=0x200)],
                         tail_slack=0x100)
        self.path = write_xbe(self.tmp, data)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_survey_is_json_serialisable_and_has_the_allocator_keys(self):
        import json
        result = xbe_space.survey(self.path)
        blob = json.loads(json.dumps(result))
        self.assertEqual(blob["tool"], "xbe_space")
        self.assertIn("headline", blob)
        self.assertIn("inventory", blob)
        for item in blob["inventory"]:
            self.assertEqual(
                set(item) >= {"class", "va", "file_off", "size", "executable",
                              "usable_for_code", "proven", "evidence", "risk"},
                True, item)

    def test_every_class_appears_in_the_inventory(self):
        result = xbe_space.survey(self.path)
        classes = {item["class"] for item in result["inventory"]}
        for expected in ("1-section-append", "2-file-slack", "4-va-room",
                         "5-virtual-zero-fill"):
            self.assertIn(expected, classes)

    def test_class_2_and_5_are_marked_unusable(self):
        result = xbe_space.survey(self.path)
        for item in result["inventory"]:
            if item["class"] in ("2-file-slack", "5-virtual-zero-fill"):
                self.assertFalse(item["usable_for_code"], item)

    def test_text_report_renders(self):
        text = xbe_space.format_text(xbe_space.survey(self.path))
        self.assertIn("XBE FREE-SPACE SURVEY", text)
        self.assertIn("NOT USABLE FOR CODE", text)
        self.assertIn("UNPROVEN", text)

    def test_no_census_still_produces_a_survey(self):
        result = xbe_space.survey(self.path, run_census=False)
        self.assertFalse(result["classes"]["dead_code"]["run"])
        self.assertTrue(result["self_check"]["ok"])

    def test_cli_writes_json(self):
        out = os.path.join(self.tmp, "survey.json")
        rc = xbe_space.main([self.path, "--json", out, "--quiet", "--no-census"])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(out))


# ---------------------------------------------------------------------------
# the operator's dump -- the numbers this survey is written from
# ---------------------------------------------------------------------------

@unittest.skipUnless(os.path.exists(IMAGE), "extract/xbox/default.xbe not present")
class RetailImageTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.xbe = Xbe.load(IMAGE)
        cls.layout = xbe_space.header_layout(cls.xbe)
        cls.append = xbe_space.section_append(cls.xbe, cls.layout)

    def test_header_headroom(self):
        self.assertEqual(self.append["header_headroom_off"], 0x9A6)
        self.assertEqual(self.append["header_headroom_end"], 0x1000)
        self.assertEqual(self.append["header_headroom_bytes"], 1626)
        # The hand survey quoted 1,624: the run starts 2 bytes below
        # SizeOfHeaders (0x9A8), where the logo bitmap's tail padding sits.
        self.assertEqual(self.append["header_headroom_above_size_of_headers"], 1624)
        self.assertTrue(self.append["header_headroom_zero_filled"])
        self.assertEqual(self.append["header_ceiling_off"], 0x1000)

    def test_section_table_cannot_grow_in_place(self):
        table = self.append["section_table"]
        self.assertEqual((table["off"], table["end_off"]), (0x370, 0x5D8))
        self.assertEqual(table["entries"], 11)
        self.assertFalse(table["can_grow_in_place"])
        self.assertIn("refcount", table["blocked_by"])
        self.assertIn("0x5D8", table["blocked_by"])

    def test_refcount_array_and_name_pool_positions(self):
        spans = {s["what"].split(" (")[0]: (s["off"], s["size"])
                 for s in self.layout["occupied"]}
        self.assertEqual(spans["head/tail shared-page refcount array"], (0x5D8, 24))
        self.assertEqual(spans["section-name string pool"], (0x5F0, 68))

    def test_relocating_the_table_fits_and_leaves_room(self):
        self.assertEqual(self.append["relocate_table_cost_bytes"], 11 * 0x38)
        self.assertLess(self.append["relocate_table_cost_bytes"],
                        self.append["header_headroom_bytes"])
        self.assertGreaterEqual(self.append["max_new_sections"], 1)

    def test_append_placement(self):
        self.assertEqual(self.append["raw_placement_off"], 0x4AA000)
        self.assertTrue(self.append["raw_placement_aligned"])
        self.assertEqual(self.append["image_top_va"], 0x0055B460)
        self.assertEqual(self.append["va_placement"], 0x0055C000)

    def test_file_slack_is_29_4_kb_and_none_of_it_is_loadable(self):
        slack = xbe_space.file_slack(self.xbe)
        self.assertEqual(slack["total_bytes"], 30104)
        self.assertEqual(slack["mapped_bytes"], 0)
        self.assertEqual(slack["usable_for_code_bytes"], 0)
        self.assertEqual(len(slack["runs"]), 12)
        self.assertTrue(all(r["zero_filled"] for r in slack["runs"]))
        # The two the earlier draft named: DSOUND->WMADEC and the tail.
        by_off = {r["off"]: r["size"] for r in slack["runs"]}
        self.assertEqual(by_off[0x3AB004], 4092)
        self.assertEqual(by_off[0x4A9800], 2048)

    def test_va_gaps_total_92_bytes(self):
        room = xbe_space.va_room(self.xbe)
        self.assertEqual(room["total_gap_bytes"], 92)
        self.assertEqual(room["largest_gap_bytes"], 20)
        self.assertEqual(room["gaps"][0]["after"], ".text")
        self.assertEqual(room["image_top_va"], 0x0055B460)
        self.assertEqual(room["last_section_end_va"], 0x0055B460)

    def test_virtual_zero_fill_is_672_kb_and_unbakeable(self):
        zf = xbe_space.virtual_zero_fill(self.xbe)
        self.assertEqual(zf["total_bytes"], 688964)
        by_section = {r["section"]: r for r in zf["regions"]}
        self.assertEqual(by_section[".data"]["size"], 674796)
        self.assertEqual(by_section[".data"]["va"], 0x004ACEF0)
        self.assertEqual(by_section[".data"]["va_end"], 0x00551ADC)
        self.assertIsNone(self.xbe.va_to_off(0x004ACEF0))

    def test_ten_of_eleven_digests_verify_and_text_is_the_exception(self):
        digests = xbe_space.section_digests(self.xbe)
        self.assertEqual(digests["rule"], "SHA-1( le32(raw_size) || raw_bytes )")
        self.assertEqual(digests["verify_count"], 10)
        self.assertEqual(digests["total"], 11)
        self.assertEqual(digests["exceptions"], [".text"])
        text = next(d for d in digests["sections"] if d["section"] == ".text")
        self.assertTrue(text["populated"])
        self.assertTrue(text["stored"].startswith("824de671"))

    def test_ten_of_eleven_sections_are_executable(self):
        execs = [s.name for s in self.xbe.sections if s.executable]
        self.assertEqual(len(execs), 10)
        self.assertNotIn("$$XTIMAGE", execs)
        self.assertEqual(xbe_space.flag_names(
            self.xbe.section_by_name("$$XTIMAGE").flags),
            ["INSERTED_FILE", "HEAD_PAGE_READ_ONLY", "TAIL_PAGE_READ_ONLY"])

    def test_trailing_section_is_last_in_both_orders(self):
        trailing = xbe_space.trailing_section(self.xbe)
        self.assertEqual(trailing["last_by_va"], "$$XTIMAGE")
        self.assertEqual(trailing["last_by_raw"], "$$XTIMAGE")
        self.assertTrue(trailing["same"])
        self.assertFalse(trailing["executable"])

    def test_tls_has_no_callbacks_and_its_index_is_in_zero_fill(self):
        tls = xbe_space.tls_and_thunks(self.xbe)
        self.assertEqual(tls["tls_va"], 0x003E4804)
        self.assertEqual(tls["tls_callbacks_va"], 0)
        self.assertFalse(tls["tls_callbacks_present"])
        self.assertEqual(tls["tls_index_va"], 0x004ACF60)
        self.assertTrue(tls["tls_index_is_in_zero_fill"])

    def test_kernel_thunk_table_opens_rdata(self):
        tls = xbe_space.tls_and_thunks(self.xbe)
        self.assertEqual(tls["kernel_thunk_va"], 0x003E4240)
        self.assertEqual(tls["kernel_thunk_section"], ".rdata")
        self.assertEqual(tls["kernel_thunk_entries"], 161)

    def test_alignment_rule_measured(self):
        align = xbe_space.alignment_rules(self.xbe)
        self.assertTrue(align["raw_offsets_page_aligned"])
        self.assertEqual(align["vaddrs_page_aligned"], [".text"])
        self.assertTrue(align["file_size_page_aligned"])

    def test_signature_and_xor_fields_are_reported(self):
        integrity = xbe_space.integrity(self.xbe)
        self.assertTrue(integrity["signature_populated"])
        self.assertEqual(integrity["signature_nonzero_bytes"], 256)
        self.assertEqual(integrity["entry_point"], 0x0025A6C6)
        self.assertEqual(integrity["entry_point_raw"], 0xA8D9F16D)
        self.assertEqual(integrity["build_type"], "retail")
        self.assertEqual(self.xbe.find_le32(0x0025A6C6), [],
                         "the entry VA must appear nowhere as a literal -- that "
                         "is why the census needs its own entry axis")


@unittest.skipUnless(os.path.exists(IMAGE) and HAVE_CAPSTONE,
                     "needs the dump and capstone")
class RetailCensusTest(unittest.TestCase):
    """The class-3 census on the real image. Slow (~5 s), run once."""

    @classmethod
    def setUpClass(cls):
        cls.xbe = Xbe.load(IMAGE)
        cls.result, cls.indexes = xbe_space.census(cls.xbe, [".text"], 32)
        cls.text = cls.xbe.section_by_name(".text")

    def test_self_check_passes(self):
        check = xbe_space.verify_no_reported_region_is_referenced(
            self.result, self.indexes)
        self.assertTrue(check["ok"], check["failures"])
        self.assertEqual(check["checked"], len(self.result["regions"]))

    def test_the_sweep_is_worth_something(self):
        """Report the sweep's own quality rather than assuming it. If decode
        coverage or call-target corroboration ever collapses, the census's
        rel8 axis has quietly stopped working and this catches it."""
        section = self.result["sections"][0]
        coverage = section["bytes_covered"] / section["size"]
        self.assertGreater(coverage, 0.999)
        corroboration = (section["call_targets_on_a_sweep_boundary"]
                         / section["call_targets_harvested"])
        self.assertGreater(corroboration, 0.95)

    def test_regions_are_inside_text_and_do_not_overlap(self):
        lo, hi = self.text.vaddr, self.text.vaddr + self.text.raw_size
        regions = sorted(self.result["regions"], key=lambda r: r["va"])
        for region in regions:
            self.assertGreaterEqual(region["va"], lo)
            self.assertLessEqual(region["va"] + region["size"], hi)
            self.assertEqual(self.xbe.va_to_off(region["va"]), region["file_off"])
        for first, second in zip(regions, regions[1:]):
            self.assertLessEqual(first["va"] + first["size"], second["va"])

    def test_no_region_contains_the_entry_point(self):
        entry = self.xbe.entry
        self.assertFalse([r for r in self.result["regions"]
                          if r["va"] <= entry < r["va"] + r["size"]])

    def test_no_region_contains_the_recency_table_reader(self):
        """0x0012CA24 is live code the hook map is built on (the fadd that
        reads the ptrk recency weights). If the census ever calls it free, the
        census is broken."""
        live = 0x0012CA24
        self.assertFalse([r for r in self.result["regions"]
                          if r["va"] <= live < r["va"] + r["size"]])

    def test_the_measured_budget(self):
        """Pins the survey's headline number. capstone-version sensitive by
        construction -- if a decoder change moves it, that is worth knowing."""
        self.assertEqual(self.result["total_bytes"], 78093)
        self.assertEqual(len(self.result["regions"]), 1213)
        largest = self.result["regions"][0]
        self.assertEqual(largest["va"], 0x00264474)
        self.assertEqual(largest["size"], 466)
        self.assertEqual(self.result["buckets"]["256"]["count"], 11)

    def test_the_import_thunk_band_is_labelled(self):
        """A run of `jmp dword ptr [__imp_*]` is dead but is not a dead leaf:
        overwriting it removes an import. The shape field must say so."""
        band = [r for r in self.result["regions"] if r["va"] == 0x003302A2]
        self.assertTrue(band)
        self.assertTrue(band[0]["shape"].startswith("import-thunk"))


if __name__ == "__main__":
    unittest.main()

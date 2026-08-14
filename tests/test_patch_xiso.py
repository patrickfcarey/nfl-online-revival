"""Writing a patched executable back into the Xbox disc image.

Same argument as `test_patch_iso_elf.py` on the PS2 side, and the same two ways
to destroy a multi-gigabyte file that still mounts afterwards: write at the wrong
offset, or write the wrong number of bytes. There is a third way that is specific
to this disc, and it is the reason this suite exists:

**the two sectors after `default.xbe` are the `/DATA/` directory table, not
padding.** Measured on the operator's image: the XBE ends at sector 2653 and
`/DATA/`'s table begins at sector 2653. A census that counts only files sees a
gap before the next *file* and reports 4 KB of growth headroom; taking it would
overwrite the records for 66 of the disc's 67 entries and leave an image that
mounts, boots, and cannot find a single asset. So the fixture reproduces that
adjacency exactly, `headroom()` is asked what is there, and
`assert_span_exclusive` is asked to refuse a write that would reach it.

The other pinned properties:

* **the directory record's own address**, because the future growth path is an
  eight-byte edit to it -- `start_sector` at record+4, `size` at record+8. The
  test reads those two u32s out of the fixture and checks they are the file's
  extent, which is the same check that would catch the field offsets being wrong.
* **a size change is refused**, in both directions, with the append+repoint
  design named rather than half-attempted.
* **verification re-extracts the file from the produced image** and compares it
  byte for byte, which is the only arm that proves the write landed where the
  filesystem says the file lives.

Run: ``python3 tests/test_patch_xiso.py``
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recon import xdvdfs  # noqa: E402
from tools import patch_xiso  # noqa: E402

SECTOR = xdvdfs.SECTOR_SIZE

ROOT_SECTOR = 33
XBE_SECTOR = 34
XBE_SIZE = 2 * SECTOR                    # whole-sector, like the real 2,388
DATA_FILE_SIZE = 100


def entry(left, right, sector, size, attributes, name):
    """One directory record, padded to the 4-byte alignment the format uses."""
    raw = struct.pack("<HHIIBB", left, right, sector, size, attributes,
                      len(name)) + name.encode("latin-1")
    return raw + b"\x00" * (-len(raw) % 4)


def volume_descriptor(root_sector, root_size):
    body = xdvdfs.MAGIC + struct.pack("<IIQ", root_sector, root_size, 0)
    body += b"\x00" * (SECTOR - len(body) - len(xdvdfs.MAGIC))
    return body + xdvdfs.MAGIC


def xbe_bytes(size=XBE_SIZE, magic=b"XBEH"):
    body = bytearray(size)
    body[0:4] = magic
    for offset in range(4, size):
        body[offset] = (offset * 7) & 0xFF
    return bytes(body)


def build_image(xbe=None, gap=0):
    """A small XISO shaped like the real one, `/DATA/`'s table right behind.

    `gap` inserts free sectors between the executable and that table, which is
    how the tests prove `headroom()` measures the image rather than reciting 0.
    """
    xbe = xbe_bytes() if xbe is None else xbe
    xbe_sectors = (len(xbe) + SECTOR - 1) // SECTOR
    data_sector = XBE_SECTOR + xbe_sectors + gap
    file_sector = data_sector + 1

    # The root table, laid out like the retail disc's: DATA first, its right
    # child at word 5 (byte 20) being default.xbe.
    root = bytearray()
    root += entry(0, 5, data_sector, SECTOR, xdvdfs.ATTR_DIRECTORY, "DATA")
    root += entry(0, 0, XBE_SECTOR, len(xbe), xdvdfs.ATTR_ARCHIVE,
                  "default.xbe")
    root_size = len(root)

    data_table = entry(0, 0, file_sector, DATA_FILE_SIZE, xdvdfs.ATTR_ARCHIVE,
                       "FILE.DAT")
    data_table = data_table.ljust(SECTOR, b"\xFF")

    total = file_sector + 1
    image = bytearray(total * SECTOR)
    image[xdvdfs.VOLUME_SECTOR * SECTOR:
          xdvdfs.VOLUME_SECTOR * SECTOR + SECTOR] = \
        volume_descriptor(ROOT_SECTOR, root_size)
    image[ROOT_SECTOR * SECTOR:ROOT_SECTOR * SECTOR + root_size] = root
    image[XBE_SECTOR * SECTOR:XBE_SECTOR * SECTOR + len(xbe)] = xbe
    image[data_sector * SECTOR:data_sector * SECTOR + SECTOR] = data_table
    image[file_sector * SECTOR:file_sector * SECTOR + DATA_FILE_SIZE] = \
        bytes(range(DATA_FILE_SIZE))
    return bytes(image)


def write_image(blob):
    handle, path = tempfile.mkstemp(suffix=".iso")
    with os.fdopen(handle, "wb") as out:
        out.write(blob)
    return Path(path)


class IsoTest(unittest.TestCase):
    """Every case needs an image on disc and a record located in it."""

    gap = 0

    def setUp(self):
        self.xbe = xbe_bytes()
        self.blob = build_image(self.xbe, gap=self.gap)
        self.iso = write_image(self.blob)
        self.addCleanup(os.unlink, self.iso)

    @contextlib.contextmanager
    def opened(self):
        with xdvdfs.Image.open(str(self.iso)) as image:
            with open(self.iso, "rb") as handle:
                yield image, handle

    def record(self, name=patch_xiso.DEFAULT_NAME):
        with self.opened() as (image, handle):
            return patch_xiso.find_record(image, handle, name)


# --------------------------------------------------------------------------
# the directory record, and the two fields a repoint would rewrite
# --------------------------------------------------------------------------

class RecordLocation(IsoTest):
    def test_the_record_carries_the_extent_and_its_own_address(self):
        record = self.record()
        self.assertEqual(record.path, "/default.xbe")
        self.assertEqual(record.start_sector, XBE_SECTOR)
        self.assertEqual(record.size, XBE_SIZE)
        self.assertEqual(record.sectors, 2)
        self.assertEqual(record.end_sector, XBE_SECTOR + 2)
        # DATA is the first record; default.xbe is its right child at byte 20.
        self.assertEqual(record.table_sector, ROOT_SECTOR)
        self.assertEqual(record.table_offset, 20)
        self.assertEqual(record.offset, ROOT_SECTOR * SECTOR + 20)

    def test_start_sector_is_at_plus_four_and_size_at_plus_eight(self):
        """The eight bytes the growth path would rewrite. Read, not assumed."""
        record = self.record()
        raw = self.blob[record.offset:record.offset + 14]
        self.assertEqual(struct.unpack_from("<I", raw, 4)[0], XBE_SECTOR)
        self.assertEqual(struct.unpack_from("<I", raw, 8)[0], XBE_SIZE)
        self.assertEqual(record.start_field, record.offset + 4)
        self.assertEqual(record.size_field, record.offset + 8)

    def test_a_record_that_does_not_read_back_is_refused(self):
        record = self.record()
        with self.opened() as (image, handle):
            with self.assertRaises(patch_xiso.PatchError) as caught:
                patch_xiso.check_record_fields(handle, image,
                                               record._replace(size=999))
        self.assertIn("does not read back", str(caught.exception))

    def test_a_file_in_a_subdirectory_is_found_through_its_table(self):
        record = self.record("/DATA/FILE.DAT")
        self.assertEqual(record.path, "/DATA/FILE.DAT")
        self.assertEqual(record.size, DATA_FILE_SIZE)

    def test_a_missing_file_says_so_rather_than_guessing(self):
        with self.assertRaises(patch_xiso.PatchError) as caught:
            self.record("nosuch.xbe")
        self.assertIn("not in the image", str(caught.exception))


# --------------------------------------------------------------------------
# the trap: what is really behind the executable
# --------------------------------------------------------------------------

class Occupancy(IsoTest):
    def test_the_directory_table_counts_as_occupied_space(self):
        with self.opened() as (image, _handle):
            extents = patch_xiso.occupancy(image)
        labels = {extent.label: extent for extent in extents}
        self.assertIn("/DATA (directory table)", labels)
        self.assertEqual(labels["/DATA (directory table)"].start_sector,
                         XBE_SECTOR + 2)
        self.assertIn("the root directory table", labels)
        self.assertIn("the volume descriptor", labels)

    def test_there_is_no_headroom_and_the_blocker_is_named(self):
        record = self.record()
        with self.opened() as (image, _handle):
            extents = patch_xiso.occupancy(image)
        free, blocker = patch_xiso.headroom(extents, record)
        self.assertEqual(free, 0)
        self.assertEqual(blocker, "/DATA (directory table)")

    def test_a_write_that_would_reach_the_table_is_refused_by_assertion(self):
        """The guard a future growth path must not be able to walk past."""
        record = self.record()
        with self.opened() as (image, _handle):
            extents = patch_xiso.occupancy(image)
        patch_xiso.assert_span_exclusive(extents, record, XBE_SIZE)
        with self.assertRaises(patch_xiso.PatchError) as caught:
            patch_xiso.assert_span_exclusive(extents, record, XBE_SIZE + 1)
        self.assertIn("/DATA", str(caught.exception))
        self.assertIn("still mounts", str(caught.exception))

    def test_the_growth_plan_is_computed_and_not_executed(self):
        record = self.record()
        before = self.iso.read_bytes()
        with self.opened() as (image, _handle):
            extents = patch_xiso.occupancy(image)
            plan = patch_xiso.growth_plan(image, extents, record,
                                          XBE_SIZE + SECTOR)
        self.assertFalse(plan.same_size)
        self.assertEqual(plan.headroom_sectors, 0)
        self.assertEqual(plan.blocked_by, "/DATA (directory table)")
        self.assertEqual(plan.append_sector, len(before) // SECTOR)
        self.assertEqual(plan.padded_size, XBE_SIZE + SECTOR)
        self.assertEqual(plan.record_offset, record.offset)
        self.assertEqual(self.iso.read_bytes(), before)

    def test_a_grown_xbe_pads_up_to_a_whole_sector(self):
        record = self.record()
        with self.opened() as (image, _handle):
            plan = patch_xiso.growth_plan(image, patch_xiso.occupancy(image),
                                          record, XBE_SIZE + 1)
        self.assertEqual(plan.padded_size, XBE_SIZE + SECTOR)
        self.assertEqual(plan.new_size, XBE_SIZE + 1)


class HeadroomIsMeasured(IsoTest):
    """The same census on an image that really does have a gap."""

    gap = 2

    def test_free_sectors_are_counted_not_assumed(self):
        record = self.record()
        with self.opened() as (image, _handle):
            extents = patch_xiso.occupancy(image)
        free, blocker = patch_xiso.headroom(extents, record)
        self.assertEqual(free, 2)
        self.assertEqual(blocker, "/DATA (directory table)")
        patch_xiso.assert_span_exclusive(extents, record, XBE_SIZE + SECTOR)


# --------------------------------------------------------------------------
# the write
# --------------------------------------------------------------------------

class Patch(IsoTest):
    def replacement(self, size=XBE_SIZE):
        body = bytearray(xbe_bytes(size))
        body[0x40:0x44] = b"HERE"
        return bytes(body)

    def test_a_same_size_write_lands_and_moves_nothing_else(self):
        new = self.replacement()
        record = patch_xiso.patch(self.iso, new)
        after = self.iso.read_bytes()
        start = XBE_SECTOR * SECTOR
        self.assertEqual(len(after), len(self.blob))
        self.assertEqual(after[start:start + XBE_SIZE], new)
        self.assertEqual(after[:start], self.blob[:start])
        self.assertEqual(after[start + XBE_SIZE:], self.blob[start + XBE_SIZE:])
        self.assertEqual(record.start_sector, XBE_SECTOR)

    def test_the_write_verifies_by_re_extracting_the_file(self):
        new = self.replacement()
        record = patch_xiso.patch(self.iso, new)
        self.assertEqual(patch_xiso.verify(self.iso, new, record,
                                           image_size=len(self.blob)), [])
        self.assertEqual(patch_xiso.read_file(self.iso), new)

    def test_verification_catches_a_byte_that_did_not_land(self):
        new = self.replacement()
        record = patch_xiso.patch(self.iso, new)
        with open(self.iso, "r+b") as handle:
            handle.seek(XBE_SECTOR * SECTOR + 0x41)
            handle.write(b"\x00")
        problems = patch_xiso.verify(self.iso, new, record,
                                     image_size=len(self.blob))
        self.assertTrue(any("does not read back" in text for text in problems),
                        problems)

    def test_verification_catches_a_changed_image_size(self):
        new = self.replacement()
        record = patch_xiso.patch(self.iso, new)
        with open(self.iso, "ab") as handle:
            handle.write(b"\x00")
        problems = patch_xiso.verify(self.iso, new, record,
                                     image_size=len(self.blob))
        self.assertTrue(any("must not change its length" in text
                            for text in problems), problems)

    def test_verification_catches_a_moved_directory_record(self):
        new = self.replacement()
        record = patch_xiso.patch(self.iso, new)
        with open(self.iso, "r+b") as handle:      # repoint the record's sector
            handle.seek(record.start_field)
            handle.write(struct.pack("<I", XBE_SECTOR + 1))
        problems = patch_xiso.verify(self.iso, new, record,
                                     image_size=len(self.blob))
        self.assertTrue(any("record moved or changed" in text
                            for text in problems), problems)

    def test_a_bigger_xbe_is_refused_with_the_growth_design_named(self):
        before = self.iso.read_bytes()
        with self.assertRaises(patch_xiso.PatchError) as caught:
            patch_xiso.patch(self.iso, self.replacement(XBE_SIZE + SECTOR))
        message = str(caught.exception)
        self.assertIn("must match", message)
        self.assertIn("0 free sectors", message)
        self.assertIn("repointed", message)
        self.assertIn("%#x" % (ROOT_SECTOR * SECTOR + 20), message)
        self.assertEqual(self.iso.read_bytes(), before)

    def test_a_smaller_xbe_is_refused_too(self):
        before = self.iso.read_bytes()
        with self.assertRaises(patch_xiso.PatchError):
            patch_xiso.patch(self.iso, self.replacement(SECTOR))
        self.assertEqual(self.iso.read_bytes(), before)

    def test_a_replacement_that_is_not_an_xbe_is_refused(self):
        before = self.iso.read_bytes()
        with self.assertRaises(patch_xiso.PatchError) as caught:
            patch_xiso.patch(self.iso, b"\x7fELF" + bytes(XBE_SIZE - 4))
        self.assertIn("not an XBE", str(caught.exception))
        self.assertEqual(self.iso.read_bytes(), before)

    def test_a_target_that_is_not_an_xbe_is_refused(self):
        iso = write_image(build_image(xbe_bytes(magic=b"JUNK")))
        self.addCleanup(os.unlink, iso)
        before = iso.read_bytes()
        with self.assertRaises(patch_xiso.PatchError) as caught:
            patch_xiso.patch(iso, self.replacement())
        self.assertIn("cannot identify", str(caught.exception))
        self.assertEqual(iso.read_bytes(), before)


class Survey(IsoTest):
    def test_it_reports_every_number_the_write_depends_on(self):
        facts = patch_xiso.survey(self.iso)
        self.assertEqual(facts["start_sector"], XBE_SECTOR)
        self.assertEqual(facts["size"], XBE_SIZE)
        self.assertEqual(facts["sectors"], 2)
        self.assertTrue(facts["whole_sector_extent"])
        self.assertEqual(facts["magic"], "XBEH")
        self.assertEqual(facts["record_offset"], ROOT_SECTOR * SECTOR + 20)
        self.assertEqual(facts["start_sector_field"],
                         facts["record_offset"] + 4)
        self.assertEqual(facts["size_field"], facts["record_offset"] + 8)
        self.assertEqual(facts["headroom_sectors"], 0)
        self.assertEqual(facts["headroom_blocked_by"], "/DATA (directory table)")
        self.assertFalse(facts["partial_tail_sector"])


# --------------------------------------------------------------------------
# the command line
# --------------------------------------------------------------------------

class Cli(IsoTest):
    def setUp(self):
        super().setUp()
        self.workdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.new = bytearray(xbe_bytes())
        self.new[0x40:0x44] = b"HERE"
        self.new = bytes(self.new)
        self.xbe_path = self.workdir / "patched.xbe"
        self.xbe_path.write_bytes(self.new)
        self.out = self.workdir / "out.iso"

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = patch_xiso.main([str(a) for a in argv])
        return code, out.getvalue(), err.getvalue()

    def test_a_copy_is_patched_and_the_original_is_untouched(self):
        code, out, _err = self.run_cli(self.iso, self.xbe_path, "-o", self.out)
        self.assertEqual(code, 0)
        self.assertEqual(self.iso.read_bytes(), self.blob)
        patched = self.out.read_bytes()
        start = XBE_SECTOR * SECTOR
        self.assertEqual(patched[start:start + XBE_SIZE], self.new)
        self.assertEqual(len(patched), len(self.blob))
        self.assertIn("image size unchanged", out)
        self.assertIn("byte-identical", out)

    def test_in_place_writes_the_image_given(self):
        code, _out, _err = self.run_cli(self.iso, self.xbe_path, "--in-place")
        self.assertEqual(code, 0)
        self.assertEqual(patch_xiso.read_file(self.iso), self.new)

    def test_the_input_is_never_the_output(self):
        code, _out, err = self.run_cli(self.iso, self.xbe_path, "-o", self.iso)
        self.assertEqual(code, 2)
        self.assertIn("names the input image", err)
        self.assertEqual(self.iso.read_bytes(), self.blob)

    def test_no_output_and_no_in_place_is_refused(self):
        code, _out, err = self.run_cli(self.iso, self.xbe_path)
        self.assertEqual(code, 2)
        self.assertIn("-o OUTPUT", err)
        self.assertEqual(self.iso.read_bytes(), self.blob)

    def test_a_size_change_exits_two_and_leaves_the_copy_alone(self):
        grown = self.workdir / "grown.xbe"
        grown.write_bytes(xbe_bytes(XBE_SIZE + SECTOR))
        code, _out, err = self.run_cli(self.iso, grown, "-o", self.out)
        self.assertEqual(code, 2)
        self.assertIn("must match", err)
        self.assertIn("the copy is unmodified", err)
        self.assertEqual(self.out.read_bytes(), self.blob)
        self.assertEqual(self.iso.read_bytes(), self.blob)

    def test_extract_writes_the_stock_executable(self):
        stock = self.workdir / "stock.xbe"
        code, out, _err = self.run_cli(self.iso, "--extract", stock)
        self.assertEqual(code, 0)
        self.assertEqual(stock.read_bytes(), self.xbe)
        self.assertIn("%d bytes extracted" % XBE_SIZE, out)

    def test_audit_reports_the_layout_and_writes_nothing(self):
        code, out, _err = self.run_cli(self.iso, "--audit")
        self.assertEqual(code, 0)
        self.assertIn("/default.xbe", out)
        self.assertIn("2 sectors exactly", out)
        self.assertIn("0x%x" % (ROOT_SECTOR * SECTOR + 20), out)
        self.assertIn("/DATA (directory table) starts there", out)
        self.assertEqual(self.iso.read_bytes(), self.blob)

    def test_the_manifest_records_the_extent_and_the_hash(self):
        manifest = self.workdir / "m.json"
        code, _out, _err = self.run_cli(self.iso, self.xbe_path, "-o", self.out,
                                        "--manifest", manifest)
        self.assertEqual(code, 0)
        record = json.loads(manifest.read_text())
        self.assertEqual(record["start_sector"], XBE_SECTOR)
        self.assertEqual(record["size"], XBE_SIZE)
        self.assertEqual(record["record_offset"], ROOT_SECTOR * SECTOR + 20)
        self.assertEqual(record["xbe_sha256"], patch_xiso._sha256(self.new))

    def test_a_missing_image_exits_two(self):
        code, _out, err = self.run_cli(self.workdir / "nope.iso",
                                       self.xbe_path, "-o", self.out)
        self.assertEqual(code, 2)
        self.assertIn("no image", err)

    def test_a_missing_file_in_the_image_exits_two(self):
        code, _out, err = self.run_cli(self.iso, self.xbe_path, "-o", self.out,
                                       "--name", "nosuch.xbe")
        self.assertEqual(code, 2)
        self.assertIn("not in the image", err)


if __name__ == "__main__":
    unittest.main()

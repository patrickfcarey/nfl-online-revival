"""Baking a roster into the disc image.

This tool writes into a 3.2 GB file in place and there is no undo, so the tests
care most about the two ways it could destroy one: patching at the wrong offset,
and patching with the wrong number of bytes. A roster of the wrong length would
either move every file after it or leave the tail of the previous one behind,
and the image would still mount.

The other thing pinned here is the fallback. The first version of this tool used
`xorriso` and nothing else, and on a machine without it exited 0 after copying
3.2 GB and patching nothing at all -- a success message over a completely
unmodified image. Silence on a missing tool is worse than a loud failure.

The images below are synthetic: a few sectors of filler with a real TERF
container at a sector boundary, which is all `_locate_by_scanning` looks for.
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import madden_tdb  # noqa: E402
from tools import patch_iso_roster as patcher  # noqa: E402

#: The size the console demands, and the size the builder preserves.
LEAG_SIZE = 253044


def _tdb(fill=b"\x00"):
    """A raw TDB of exactly the size member 0 must be."""
    blob = bytearray(LEAG_SIZE)
    blob[0:4] = struct.pack("<I", 0x08004244)      # "DB" plus version 0x0800
    struct.pack_into("<I", blob, 16, 0)            # no tables; nothing reads them
    for index in range(64, LEAG_SIZE, 997):        # recognisable filler
        blob[index:index + 1] = fill
    return bytes(blob)


def _container(members):
    """A TERF archive with a 0x40 header, a DIR1 block, then the members."""
    dir_size = 8 + len(members) * 8
    header = bytearray(0x40)
    header[0:4] = b"TERF"
    struct.pack_into("<I", header, 0x04, 0x40)
    struct.pack_into("<H", header, 0x0E, len(members))
    directory = bytearray(dir_size)
    directory[0:4] = b"DIR1"
    struct.pack_into("<I", directory, 4, dir_size)
    body = b""
    for index, member in enumerate(members):
        struct.pack_into("<II", directory, 8 + index * 8, len(body),
                         len(member))
        body += member
    return bytes(header) + bytes(directory) + body


def _image(container, sector=3, trailing=2):
    """An ISO-shaped file with `container` starting on a sector boundary."""
    handle, path = tempfile.mkstemp(suffix=".iso")
    offset = sector * patcher.SECTOR
    with os.fdopen(handle, "wb") as out:
        out.write(b"\xA5" * offset)
        out.write(container)
        out.write(b"\x5A" * (trailing * patcher.SECTOR))
    return Path(path), offset


class MemberSpan(unittest.TestCase):
    def test_reports_where_member_zero_starts_and_how_big_it_is(self):
        blob = _tdb()
        container = _container([blob, b"other member"])
        offset, size = patcher.member_span(container)
        self.assertEqual(size, LEAG_SIZE)
        self.assertEqual(container[offset:offset + size], blob)

    def test_later_members_are_addressable_too(self):
        container = _container([_tdb(), b"SECOND"])
        offset, size = patcher.member_span(container, 1)
        self.assertEqual(container[offset:offset + size], b"SECOND")


class Scanning(unittest.TestCase):
    """The fallback that runs when xorriso is not installed."""

    def setUp(self):
        self.container = _container([_tdb(), b"x" * 32])
        self.path, self.offset = _image(self.container)
        self.addCleanup(os.unlink, self.path)

    def test_finds_the_container_at_its_sector(self):
        found, size = patcher._locate_by_scanning(self.path)
        self.assertEqual(found, self.offset)
        self.assertGreaterEqual(size, LEAG_SIZE)

    def test_the_reported_span_covers_every_member(self):
        _found, size = patcher._locate_by_scanning(self.path)
        container = madden_tdb.Container(self.container)
        expected = container._member_base + max(o + s for o, s
                                                in container._members)
        self.assertEqual(size, expected)

    def test_a_terf_that_is_not_sector_aligned_is_ignored(self):
        """Files inside an ISO9660 image start on a 2048-byte boundary.

        Four bytes spelling TERF anywhere else are a coincidence, and treating
        one as the container would patch the middle of some other file.
        """
        handle, path = tempfile.mkstemp(suffix=".iso")
        with os.fdopen(handle, "wb") as out:
            out.write(b"\xA5" * (patcher.SECTOR * 2 + 7))   # deliberately odd
            out.write(self.container)
            out.write(b"\x5A" * patcher.SECTOR)
        self.addCleanup(os.unlink, path)
        with self.assertRaises(patcher.PatchError):
            patcher._locate_by_scanning(Path(path))

    def test_a_bare_terf_signature_is_not_enough(self):
        # The magic alone occurs by chance; the directory offset and member
        # count have to agree as well.
        handle, path = tempfile.mkstemp(suffix=".iso")
        with os.fdopen(handle, "wb") as out:
            out.write(b"TERF" + b"\x00" * (patcher.SECTOR * 3 - 4))
        self.addCleanup(os.unlink, path)
        with self.assertRaises(patcher.PatchError):
            patcher._locate_by_scanning(Path(path))

    def test_an_image_with_no_container_says_so(self):
        handle, path = tempfile.mkstemp(suffix=".iso")
        with os.fdopen(handle, "wb") as out:
            out.write(b"\x00" * patcher.SECTOR * 4)
        self.addCleanup(os.unlink, path)
        with self.assertRaises(patcher.PatchError) as caught:
            patcher._locate_by_scanning(Path(path))
        self.assertIn("right disc image", str(caught.exception))

    def test_a_container_past_the_first_chunk_is_still_found(self):
        # The scan reads in 16 MB chunks with a sector of overlap; a container
        # beyond the first would be missed if the loop were wrong.
        container = _container([_tdb(), b"second member"])
        sector = (1 << 24) // patcher.SECTOR + 5
        path, offset = _image(container, sector=sector, trailing=1)
        self.addCleanup(os.unlink, path)
        found, _size = patcher._locate_by_scanning(path)
        self.assertEqual(found, offset)


class XorrisoParsing(unittest.TestCase):
    """The report_lba line, parsed without needing xorriso installed."""

    def _with_stdout(self, text, returncode=0):
        class _Result:
            stdout = text
        original = subprocess.run
        subprocess.run = lambda *a, **k: _Result()
        self.addCleanup(lambda: setattr(subprocess, "run", original))

    def test_reads_the_lba_and_size(self):
        self._with_stdout(
            "File data lba:  0 ,  1461725 ,  1330 ,  2723072 , "
            "'/DATA/TEMPLATE.DAT'\n")
        offset, size = patcher._locate_with_xorriso(Path("x.iso"),
                                                    patcher.TEMPLATE_PATH)
        self.assertEqual(offset, 1461725 * patcher.SECTOR)
        self.assertEqual(size, 2723072)

    def test_a_line_for_another_file_is_ignored(self):
        self._with_stdout("File data lba: 0 , 5 , 1 , 99 , '/DATA/OTHER.DAT'\n")
        with self.assertRaises(patcher.PatchError):
            patcher._locate_with_xorriso(Path("x.iso"), patcher.TEMPLATE_PATH)

    def test_a_missing_binary_raises_rather_than_returning_nothing(self):
        original = subprocess.run

        def explode(*_a, **_k):
            raise OSError(2, "No such file or directory: 'xorriso'")

        subprocess.run = explode
        self.addCleanup(lambda: setattr(subprocess, "run", original))
        with self.assertRaises(patcher.PatchError) as caught:
            patcher._locate_with_xorriso(Path("x.iso"), patcher.TEMPLATE_PATH)
        self.assertIn("unavailable", str(caught.exception))

    def test_locate_falls_back_to_scanning_when_xorriso_is_missing(self):
        """The regression this tool was rewritten for.

        With only the xorriso path, a machine without it produced a success
        message over an image that had not been touched.
        """
        original = subprocess.run

        def explode(*_a, **_k):
            raise OSError(2, "no xorriso")

        subprocess.run = explode
        self.addCleanup(lambda: setattr(subprocess, "run", original))
        path, offset = _image(_container([_tdb(), b"second member"]))
        self.addCleanup(os.unlink, path)
        found, _size = patcher.locate(path)
        self.assertEqual(found, offset)


class Patch(unittest.TestCase):
    def setUp(self):
        self.roster = _tdb(fill=b"\xEE")
        self.path, self.offset = _image(_container([_tdb(), b"tail" * 8]))
        self.addCleanup(os.unlink, self.path)
        self.before = self.path.read_bytes()

    def test_writes_the_roster_and_reads_it_back(self):
        absolute, written = patcher.patch(self.path, self.roster)
        self.assertEqual(written, LEAG_SIZE)
        self.assertTrue(patcher.verify(self.path, self.roster))
        data = self.path.read_bytes()
        self.assertEqual(data[absolute:absolute + LEAG_SIZE], self.roster)

    def test_the_image_is_unchanged_everywhere_else(self):
        """The whole argument for patching in place.

        No file moves, no directory entry changes, and every byte outside the
        member is identical -- which is also what makes the operation safe to
        run on the only copy.
        """
        absolute, _written = patcher.patch(self.path, self.roster)
        after = self.path.read_bytes()
        self.assertEqual(len(after), len(self.before))
        self.assertEqual(after[:absolute], self.before[:absolute])
        self.assertEqual(after[absolute + LEAG_SIZE:],
                         self.before[absolute + LEAG_SIZE:])

    def test_a_roster_of_the_wrong_size_is_refused(self):
        # The only way this could corrupt an image.
        for wrong in (LEAG_SIZE - 1, LEAG_SIZE + 1, 0):
            with self.assertRaises(patcher.PatchError) as caught:
                patcher.patch(self.path, b"\x00" * wrong)
            self.assertIn("must match", str(caught.exception))
        self.assertEqual(self.path.read_bytes(), self.before,
                         "a refused patch still modified the image")

    def test_a_roster_that_is_not_a_tdb_is_refused(self):
        not_a_tdb = b"XX" + b"\x00" * (LEAG_SIZE - 2)
        with self.assertRaises(patcher.PatchError) as caught:
            patcher.patch(self.path, not_a_tdb)
        self.assertIn("not a raw TDB", str(caught.exception))
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_verify_fails_when_the_bytes_differ(self):
        patcher.patch(self.path, self.roster)
        self.assertFalse(patcher.verify(self.path, _tdb(fill=b"\x11")))


class Cli(unittest.TestCase):
    def setUp(self):
        self.roster_path = Path(tempfile.mkstemp(suffix=".dat")[1])
        self.roster_path.write_bytes(_tdb(fill=b"\xEE"))
        self.addCleanup(os.unlink, self.roster_path)
        self.iso, _offset = _image(_container([_tdb(), b"second member"]))
        self.addCleanup(os.unlink, self.iso)

    def test_refuses_without_output_or_in_place(self):
        # Neither flag means the user has not said which image to modify.
        self.assertEqual(patcher.main([str(self.iso), str(self.roster_path)]), 2)

    def test_a_missing_image_exits_two(self):
        self.assertEqual(
            patcher.main(["/nonexistent.iso", str(self.roster_path),
                          "--in-place"]), 2)

    def test_a_missing_roster_exits_two(self):
        self.assertEqual(
            patcher.main([str(self.iso), "/nonexistent.dat", "--in-place"]), 2)

    def test_in_place_patches_the_image_given(self):
        self.assertEqual(
            patcher.main([str(self.iso), str(self.roster_path), "--in-place"]), 0)
        self.assertTrue(patcher.verify(self.iso, self.roster_path.read_bytes()))

    def test_output_leaves_the_original_untouched(self):
        before = self.iso.read_bytes()
        out = Path(tempfile.mkstemp(suffix=".iso")[1])
        self.addCleanup(os.unlink, out)
        self.assertEqual(
            patcher.main([str(self.iso), str(self.roster_path), "-o", str(out)]),
            0)
        self.assertEqual(self.iso.read_bytes(), before)
        self.assertTrue(patcher.verify(out, self.roster_path.read_bytes()))

    def test_a_wrong_sized_roster_exits_two_without_writing(self):
        bad = Path(tempfile.mkstemp(suffix=".dat")[1])
        bad.write_bytes(b"\x00" * 100)
        self.addCleanup(os.unlink, bad)
        before = self.iso.read_bytes()
        self.assertEqual(
            patcher.main([str(self.iso), str(bad), "--in-place"]), 2)
        self.assertEqual(self.iso.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

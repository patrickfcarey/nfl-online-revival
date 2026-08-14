"""Writing a baked executable back into the disc image.

Same argument as `test_patch_iso_roster.py`, and the same two ways to destroy a
3.2 GB file that still mounts afterwards: patch at the wrong offset, or patch
with the wrong number of bytes. An executable of the wrong length would spill
into whatever file follows it or leave the tail of the old one behind, and the
disc would look fine until the game ran.

The third pinned property is the fallback. `patch_iso_roster` once exited 0
having copied 3.2 GB and patched nothing, because `xorriso` was not installed
and nothing else knew where to look. Here the fallback reads ISO9660's own
directory records, so the tests build a real (tiny) filesystem: a primary
volume descriptor at sector 16, a root directory extent, SYSTEM.CNF, a
subdirectory, and a small ELF standing in for `SLUS_207.52`.

Run: ``python3 tests/test_patch_iso_elf.py``
"""

from __future__ import annotations

import contextlib
import io
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools import patch_iso_elf as patcher  # noqa: E402

try:                                        # discovery puts tests/ on the path
    from tests.test_bake_pnach import elf_bytes
except ImportError:                         # running the module directly
    from test_bake_pnach import elf_bytes   # noqa: F401

SECTOR = patcher.SECTOR
ROOT_LBA = 17
SUBDIR_LBA = 18
FILE_LBA = 20                       # first data sector; files follow in order


def record(name, lba, size, flags=0):
    """One ISO9660 directory record, both-endian fields and all."""
    ident = name.encode("latin-1")
    length = 33 + len(ident)
    length += length % 2                            # records are even-length
    blob = bytearray(length)
    blob[0] = length
    struct.pack_into("<I", blob, 2, lba)
    struct.pack_into(">I", blob, 6, lba)
    struct.pack_into("<I", blob, 10, size)
    struct.pack_into(">I", blob, 14, size)
    blob[25] = flags
    struct.pack_into("<H", blob, 28, 1)
    struct.pack_into(">H", blob, 30, 1)
    blob[32] = len(ident)
    blob[33:33 + len(ident)] = ident
    return bytes(blob)


def directory(records):
    """A one-sector directory extent: `.`, `..`, then the entries given."""
    blob = record("\x00", ROOT_LBA, SECTOR, flags=2)
    blob += record("\x01", ROOT_LBA, SECTOR, flags=2)
    for entry in records:
        blob += entry
    return blob.ljust(SECTOR, b"\x00")


def image(elf=None, boot="SLUS_207.52;1", system_cnf=None, deep=b"deep file",
          pvd=True):
    """A small but real ISO9660 image with the boot executable in it.

    Returns (path, offset of the executable, its bytes).
    """
    elf = elf_bytes() if elf is None else elf
    if system_cnf is None:
        system_cnf = ("BOOT2 = cdrom0:\\%s\nVER = 1.00\nVMODE = NTSC\n"
                      % boot).encode("latin-1")

    slus_lba = FILE_LBA
    cnf_lba = slus_lba + (len(elf) + SECTOR - 1) // SECTOR
    deep_lba = cnf_lba + 1

    root = directory([
        record("SUBDIR", SUBDIR_LBA, SECTOR, flags=2),
        record(boot, slus_lba, len(elf)),
        record("SYSTEM.CNF;1", cnf_lba, len(system_cnf)),
    ])
    subdir = directory([record("DEEP.DAT;1", deep_lba, len(deep))])

    volume = bytearray(SECTOR)
    if pvd:
        volume[0] = 1
        volume[1:6] = b"CD001"
        volume[6] = 1
        volume[patcher.ROOT_RECORD:patcher.ROOT_RECORD + 34] = record(
            "\x00", ROOT_LBA, SECTOR, flags=2)

    blob = bytearray(b"\x00" * (SECTOR * 16))       # the system area
    blob += volume
    blob += root
    blob += subdir
    blob += b"\xA5" * SECTOR                        # sector 19: filler
    assert len(blob) == FILE_LBA * SECTOR, len(blob)
    for lba, payload in ((slus_lba, elf), (cnf_lba, system_cnf),
                         (deep_lba, deep)):
        blob = blob.ljust(lba * SECTOR, b"\x00")
        blob += payload
    blob = blob.ljust((deep_lba + 2) * SECTOR, b"\x5A")

    handle, path = tempfile.mkstemp(suffix=".iso")
    with os.fdopen(handle, "wb") as out:
        out.write(blob)
    return Path(path), slus_lba * SECTOR, bytes(elf)


def without_xorriso(case):
    """xorriso is not installed on most machines here; make that certain."""
    original = subprocess.run

    def explode(*_a, **_k):
        raise OSError(2, "No such file or directory: 'xorriso'")

    subprocess.run = explode
    case.addCleanup(lambda: setattr(subprocess, "run", original))


class Records(unittest.TestCase):
    def test_self_and_parent_entries_are_skipped(self):
        names = [name for name, _lba, _size, _flags
                 in patcher._records(directory([record("A.DAT;1", 5, 9)]))]
        self.assertEqual(names, ["A.DAT;1"])

    def test_a_directory_is_flagged_as_one(self):
        entries = list(patcher._records(directory([
            record("SUBDIR", 6, SECTOR, flags=2), record("A.DAT;1", 5, 9)])))
        self.assertTrue(entries[0][3] & patcher.FLAG_DIRECTORY)
        self.assertFalse(entries[1][3] & patcher.FLAG_DIRECTORY)

    def test_the_walk_continues_into_the_next_sector(self):
        """A zero length byte ends the sector, not the directory."""
        extent = (directory([record("A.DAT;1", 5, 9)])
                  + directory([record("B.DAT;1", 6, 9)]))
        names = [name for name, _lba, _size, _flags in patcher._records(extent)]
        self.assertEqual(names, ["A.DAT;1", "B.DAT;1"])


class Locate(unittest.TestCase):
    def setUp(self):
        without_xorriso(self)
        self.iso, self.offset, self.elf = image()
        self.addCleanup(os.unlink, self.iso)

    def test_finds_the_executable_at_its_lba_with_its_exact_size(self):
        offset, size = patcher.locate(self.iso, "SLUS_207.52;1")
        self.assertEqual(offset, self.offset)
        self.assertEqual(size, len(self.elf))
        self.assertEqual(offset % SECTOR, 0)

    def test_the_version_suffix_and_case_do_not_matter(self):
        for name in ("SLUS_207.52", "slus_207.52;1", "/SLUS_207.52;1"):
            self.assertEqual(patcher.locate(self.iso, name)[0], self.offset)

    def test_a_file_in_a_subdirectory_is_found_too(self):
        offset, size = patcher.locate(self.iso, "DEEP.DAT;1")
        deep_lba = FILE_LBA + -(-len(self.elf) // SECTOR) + 1
        self.assertEqual(offset, deep_lba * SECTOR)
        self.assertEqual(size, len(b"deep file"))
        with open(self.iso, "rb") as handle:
            handle.seek(offset)
            self.assertEqual(handle.read(size), b"deep file")

    def test_a_file_that_is_not_there_says_so(self):
        with self.assertRaises(patcher.PatchError) as caught:
            patcher.locate(self.iso, "SLES_123.45;1")
        self.assertIn("not in", str(caught.exception))

    def test_an_image_with_no_volume_descriptor_says_so(self):
        iso, _offset, _elf = image(pvd=False)
        self.addCleanup(os.unlink, iso)
        with self.assertRaises(patcher.PatchError) as caught:
            patcher.locate(iso, "SLUS_207.52;1")
        self.assertIn("primary volume descriptor", str(caught.exception))

    def test_the_fallback_runs_when_xorriso_is_missing(self):
        """The regression the roster tool was rewritten for."""
        self.assertEqual(patcher.locate(self.iso, "SLUS_207.52;1")[0],
                         self.offset)

    def test_read_file_returns_exactly_the_file(self):
        self.assertEqual(patcher.read_file(self.iso, "SLUS_207.52;1"),
                         self.elf)


class BootName(unittest.TestCase):
    def setUp(self):
        without_xorriso(self)

    def test_it_comes_from_system_cnf(self):
        iso, _offset, _elf = image(boot="SLUS_999.99;1")
        self.addCleanup(os.unlink, iso)
        self.assertEqual(patcher.boot_name(iso), "SLUS_999.99;1")

    def test_a_missing_system_cnf_falls_back_to_this_game(self):
        iso, _offset, _elf = image(system_cnf=b"")
        self.addCleanup(os.unlink, iso)
        self.assertEqual(patcher.boot_name(iso), patcher.DEFAULT_BOOT)


class Patch(unittest.TestCase):
    def setUp(self):
        without_xorriso(self)
        self.iso, self.offset, self.elf = image()
        self.addCleanup(os.unlink, self.iso)
        self.before = self.iso.read_bytes()
        self.baked = bytearray(self.elf)
        struct.pack_into("<I", self.baked, 0x1000, 0x1000000F)
        self.baked = bytes(self.baked)

    def test_the_executable_is_replaced_where_it_lies(self):
        offset, written = patcher.patch(self.iso, self.baked)
        self.assertEqual(offset, self.offset)
        self.assertEqual(written, len(self.elf))
        self.assertTrue(patcher.verify(self.iso, self.baked))

    def test_the_image_is_unchanged_everywhere_else(self):
        offset, _written = patcher.patch(self.iso, self.baked)
        after = self.iso.read_bytes()
        self.assertEqual(len(after), len(self.before))
        self.assertEqual(after[:offset], self.before[:offset])
        self.assertEqual(after[offset + len(self.elf):],
                         self.before[offset + len(self.elf):])

    def test_an_executable_of_the_wrong_size_is_refused(self):
        """The only way this could corrupt an image."""
        for wrong in (self.baked + b"\x00", self.baked[:-1]):
            with self.assertRaises(patcher.PatchError) as caught:
                patcher.patch(self.iso, wrong)
            self.assertIn("match exactly", str(caught.exception))
        self.assertEqual(self.iso.read_bytes(), self.before,
                         "a refused patch still modified the image")

    def test_a_replacement_that_is_not_an_elf_is_refused(self):
        with self.assertRaises(patcher.PatchError) as caught:
            patcher.patch(self.iso, b"X" * len(self.elf))
        self.assertIn("not an ELF", str(caught.exception))
        self.assertEqual(self.iso.read_bytes(), self.before)

    def test_a_target_that_is_not_an_elf_is_refused(self):
        """If what is on the disc is not what we think, stop."""
        with self.assertRaises(patcher.PatchError) as caught:
            patcher.patch(self.iso, self.baked, name="SYSTEM.CNF;1")
        self.assertIn("not an ELF", str(caught.exception))
        self.assertEqual(self.iso.read_bytes(), self.before)

    def test_verify_fails_when_the_bytes_differ(self):
        patcher.patch(self.iso, self.baked)
        self.assertFalse(patcher.verify(self.iso, self.elf))


class Cli(unittest.TestCase):
    def setUp(self):
        without_xorriso(self)
        self.iso, self.offset, self.elf = image()
        self.addCleanup(os.unlink, self.iso)
        self.before = self.iso.read_bytes()
        baked = bytearray(self.elf)
        struct.pack_into("<I", baked, 0x1000, 0x1000000F)
        handle, path = tempfile.mkstemp(suffix=".elf")
        with os.fdopen(handle, "wb") as out:
            out.write(baked)
        self.elf_path = Path(path)
        self.addCleanup(os.unlink, self.elf_path)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = patcher.main([str(a) for a in argv])
        return code, out.getvalue(), err.getvalue()

    def test_refuses_without_output_or_in_place(self):
        code, _out, err = self.run_cli(self.iso, self.elf_path)
        self.assertEqual(code, 2)
        self.assertIn("-o OUTPUT", err)
        self.assertEqual(self.iso.read_bytes(), self.before)

    def test_a_missing_image_exits_two(self):
        self.assertEqual(
            self.run_cli("/nonexistent.iso", self.elf_path, "--in-place")[0], 2)

    def test_a_missing_elf_exits_two(self):
        self.assertEqual(
            self.run_cli(self.iso, "/nonexistent.elf", "--in-place")[0], 2)

    def test_in_place_patches_the_image_given(self):
        code, out, _err = self.run_cli(self.iso, self.elf_path, "--in-place")
        self.assertEqual(code, 0)
        self.assertIn("SLUS_207.52;1 replaced", out)
        self.assertTrue(patcher.verify(self.iso, self.elf_path.read_bytes()))

    def test_output_leaves_the_original_untouched(self):
        out_path = Path(tempfile.mkstemp(suffix=".iso")[1])
        self.addCleanup(os.unlink, out_path)
        code, _out, _err = self.run_cli(self.iso, self.elf_path, "-o", out_path)
        self.assertEqual(code, 0)
        self.assertEqual(self.iso.read_bytes(), self.before)
        self.assertTrue(patcher.verify(out_path, self.elf_path.read_bytes()))

    def test_a_wrong_sized_elf_exits_two_without_writing(self):
        bad = Path(tempfile.mkstemp(suffix=".elf")[1])
        bad.write_bytes(self.elf_path.read_bytes() + b"\x00" * 4)
        self.addCleanup(os.unlink, bad)
        code, _out, err = self.run_cli(self.iso, bad, "--in-place")
        self.assertEqual(code, 2)
        self.assertIn("match exactly", err)
        self.assertEqual(self.iso.read_bytes(), self.before)

    def test_extract_writes_the_stock_executable_and_stops(self):
        out_path = Path(tempfile.mkstemp(suffix=".elf")[1])
        self.addCleanup(os.unlink, out_path)
        code, _out, _err = self.run_cli(self.iso, "--extract", out_path)
        self.assertEqual(code, 0)
        self.assertEqual(out_path.read_bytes(), self.elf)
        self.assertEqual(self.iso.read_bytes(), self.before)


if __name__ == "__main__":
    unittest.main()

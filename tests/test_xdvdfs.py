"""Tests for the XDVDFS (Xbox XISO) reader. Offline: no disc image needed.

Run: ``python3 tests/test_xdvdfs.py`` (or ``python3 -m unittest discover tests``).

Everything here is built by :func:`build_image` out of a handful of synthetic
directory entries, so the suite carries no multi-gigabyte fixture and still
exercises the parts that actually broke or could break.

Two cases carry most of the weight, and both are about ``0xFF``:

``test_left_ffff_is_not_a_terminator`` -- descriptions of this format say a
left-child offset of ``0xFFFF`` ends the directory table, while other authoring
tools write ``0xFFFF`` to mean "no child". Believing the first reading throws
away every entry reachable past such a pointer, and "65 files instead of 66" is
the kind of wrong that never announces itself.

``test_leaf_with_both_children_ffff_is_not_padding`` -- the consequence of
accepting that spelling. A leaf written that way begins ``ff ff ff ff``, which
is also what the ``0xFF`` fill between and after records looks like. The
reader's first draft tested four bytes and silently dropped such a leaf; this
fixture is what caught it, so the padding test now reads the whole 14-byte
header.

(The retail Madden NFL 2004 disc itself uses 0 for "no child" throughout and
``0xFF`` only as padding -- verified across all 67 of its records. These two
cases defend against the layouts it does *not* use, which is exactly why they
need a synthetic image rather than the disc.)
"""

from __future__ import annotations

import io
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recon import xdvdfs  # noqa: E402

SECTOR = xdvdfs.SECTOR_SIZE


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------

def entry(left, right, sector, size, attributes, name):
    """One directory record, padded to the 4-byte alignment the format uses."""
    raw = struct.pack("<HHIIBB", left, right, sector, size, attributes,
                      len(name)) + name.encode("latin-1")
    return raw + b"\x00" * (-len(raw) % 4)


def directory(records, pad_to=None):
    """Concatenate records; ``pad_to`` fills with 0xFF the way real tables do."""
    table = b"".join(records)
    if pad_to is not None:
        table += b"\xff" * (pad_to - len(table))
    return table


def right_spine(records, no_child=0x0000, pad_to=None):
    """Chain *records* through their right-child pointers, in order.

    Child offsets are in 4-byte words and depend on every preceding record's
    padded length, so they are computed here rather than written by hand -- a
    hand-counted offset in a fixture tests the fixture, not the reader.
    """
    offsets, position = [], 0
    for record in records:
        offsets.append(position)
        position += len(record)
    table = bytearray(b"".join(records))
    for index, offset in enumerate(offsets):
        right = offsets[index + 1] // 4 if index + 1 < len(offsets) else no_child
        struct.pack_into("<HH", table, offset, no_child, right)
    if pad_to is not None:
        table += b"\xff" * (pad_to - len(table))
    return bytes(table)


def volume_descriptor(root_sector, root_size, filetime=0):
    body = xdvdfs.MAGIC + struct.pack("<IIQ", root_sector, root_size, filetime)
    body += b"\x00" * (SECTOR - len(body) - len(xdvdfs.MAGIC))
    return body + xdvdfs.MAGIC


def build_image(sectors, root_sector, root_size, base=0, descriptor=None):
    """An image with *sectors* ({index: bytes}) laid out at *base*."""
    highest = max(list(sectors) + [xdvdfs.VOLUME_SECTOR])
    image = bytearray(base + (highest + 1) * SECTOR)
    if descriptor is None:
        descriptor = volume_descriptor(root_sector, root_size)
    image[base + xdvdfs.VOLUME_SECTOR * SECTOR:
          base + xdvdfs.VOLUME_SECTOR * SECTOR + len(descriptor)] = descriptor
    for index, payload in sectors.items():
        start = base + index * SECTOR
        image[start:start + len(payload)] = payload
    return bytes(image)


#: The shape of the real disc: a DATA directory and a file whose left child is
#: 0xFFFF, plus two files one level down.
ONE = b"one-file-contents"
TWO = b"two" * 40


def madden_shaped_image(base=0):
    # The root links DATA -> default.xbe with 0 as "no child", as the retail
    # root does, except that default.xbe's left child is set to 0xFFFF: the
    # spelling this disc does not use, and the one a reader must not mistake
    # for a terminator.
    root = bytearray(right_spine([
        entry(0, 0, 40, SECTOR, xdvdfs.ATTR_DIRECTORY, "DATA"),
        entry(0, 0, 50, len(ONE), xdvdfs.ATTR_ARCHIVE, "default.xbe"),
    ]))
    struct.pack_into("<H", root, 20, 0xFFFF)
    root = bytes(root)
    # The DATA table uses 0xFFFF for "no child" throughout, so its last entry
    # begins with four 0xFF bytes -- indistinguishable from fill, unless the
    # whole header is checked.
    data = right_spine([
        entry(0, 0, 51, len(TWO), xdvdfs.ATTR_ARCHIVE, "DB_TEAMS.DAT"),
        entry(0, 0, 52, len(ONE), xdvdfs.ATTR_ARCHIVE, "GAMEDATA.DAT"),
    ], no_child=0xFFFF, pad_to=SECTOR)
    sectors = {39: root, 40: data, 50: ONE, 51: TWO, 52: ONE}
    return build_image(sectors, root_sector=39, root_size=len(root), base=base)


def open_image(blob, base=None):
    return xdvdfs.Image(io.BytesIO(blob), base=base)


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------

class WalkTest(unittest.TestCase):

    def setUp(self):
        self.image = open_image(madden_shaped_image())

    def test_volume_descriptor_is_read(self):
        self.assertEqual(self.image.base, 0)
        self.assertEqual(self.image.root_sector, 39)

    def test_left_ffff_is_not_a_terminator(self):
        """An entry with left=0xFFFF is a record, not the end of the table."""
        paths = [e.path for e in self.image.files()]
        self.assertIn("/default.xbe", paths)

    def test_leaf_with_both_children_ffff_is_not_padding(self):
        """ff ff ff ff opens a leaf as well as a run of fill. Check the header."""
        entries = {e.path: e for e in self.image.entries()}
        self.assertIn("/DATA/GAMEDATA.DAT", entries)
        self.assertEqual(entries["/DATA/GAMEDATA.DAT"].start_sector, 52)

    def test_walks_the_whole_tree(self):
        found = {e.path: (e.start_sector, e.size) for e in self.image.entries()}
        self.assertEqual(set(found), {
            "/DATA", "/default.xbe", "/DATA/DB_TEAMS.DAT", "/DATA/GAMEDATA.DAT"})
        self.assertEqual(found["/DATA/DB_TEAMS.DAT"], (51, len(TWO)))

    def test_directories_are_flagged_and_files_are_not(self):
        by_path = {e.path: e for e in self.image.entries()}
        self.assertTrue(by_path["/DATA"].is_directory)
        self.assertFalse(by_path["/DATA/GAMEDATA.DAT"].is_directory)
        self.assertEqual([e.path for e in self.image.files()].count("/DATA"), 0)

    def test_ff_padding_is_not_decoded_as_a_record(self):
        """The tail of a directory table is 0xFF fill, not a file called 'ÿÿÿ'."""
        names = [e.name for e in self.image.entries()]
        self.assertEqual(len(names), len(set(names)))
        for name in names:
            self.assertNotIn("\xff", name)

    def test_entry_offset_accounts_for_the_base(self):
        entry_ = self.image.find("/DATA/DB_TEAMS.DAT")
        self.assertEqual(entry_.offset(base=0x1000), 0x1000 + 51 * SECTOR)


class ReadTest(unittest.TestCase):

    def setUp(self):
        self.image = open_image(madden_shaped_image())

    def test_read_truncates_to_the_declared_size(self):
        """Sizes are not sector-rounded; the tail of the sector is not content."""
        data = self.image.read(self.image.find("/DATA/DB_TEAMS.DAT"))
        self.assertEqual(data, TWO)

    def test_read_honours_a_limit(self):
        data = self.image.read(self.image.find("/DATA/DB_TEAMS.DAT"), limit=9)
        self.assertEqual(data, TWO[:9])

    def test_read_rejects_a_directory(self):
        with self.assertRaises(xdvdfs.XdvdfsError):
            self.image.read(self.image.find("/DATA"))

    def test_extract_writes_the_exact_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = os.path.join(tmp, "out.bin")
            written = self.image.extract(self.image.find("/DATA/DB_TEAMS.DAT"),
                                         destination)
            self.assertEqual(written, len(TWO))
            with open(destination, "rb") as handle:
                self.assertEqual(handle.read(), TWO)

    def test_extract_honours_a_limit(self):
        """Sampling the head of a 262 MB file must not write the whole thing."""
        with tempfile.TemporaryDirectory() as tmp:
            destination = os.path.join(tmp, "head.bin")
            written = self.image.extract(self.image.find("/DATA/DB_TEAMS.DAT"),
                                         destination, limit=5)
            self.assertEqual(written, 5)
            with open(destination, "rb") as handle:
                self.assertEqual(handle.read(), TWO[:5])

    def test_find_is_case_insensitive_and_tolerates_a_missing_slash(self):
        self.assertEqual(self.image.find("data/db_teams.dat").start_sector, 51)
        self.assertEqual(self.image.find("/DATA/DB_TEAMS.DAT").start_sector, 51)

    def test_find_reports_a_missing_path(self):
        with self.assertRaises(xdvdfs.XdvdfsError):
            self.image.find("/DATA/NOPE.DAT")


class BaseDetectionTest(unittest.TestCase):

    def test_finds_a_non_zero_base(self):
        base = xdvdfs.KNOWN_BASES[1]
        image = open_image(madden_shaped_image(base=base))
        self.assertEqual(image.base, base)
        self.assertEqual(image.read(image.find("/DATA/GAMEDATA.DAT")), ONE)

    def test_no_magic_anywhere_is_a_clear_error(self):
        blob = b"\x00" * (SECTOR * 40)
        with self.assertRaises(xdvdfs.XdvdfsError) as caught:
            open_image(blob)
        self.assertIn("MICROSOFT*XBOX*MEDIA", str(caught.exception))

    def test_head_magic_without_tail_magic_is_refused(self):
        """A base that matched by luck must not be decoded as a filesystem."""
        descriptor = bytearray(volume_descriptor(39, 40))
        descriptor[-len(xdvdfs.MAGIC):] = b"\x00" * len(xdvdfs.MAGIC)
        blob = build_image({39: b"\x00" * 40}, 39, 40, descriptor=bytes(descriptor))
        with self.assertRaises(xdvdfs.XdvdfsError) as caught:
            open_image(blob)
        self.assertIn("tail magic", str(caught.exception))


class MalformedTest(unittest.TestCase):

    def test_a_cycle_is_reported_not_looped_on(self):
        table = bytearray(right_spine([
            entry(0, 0, 50, 4, 0, "AAAA"),
            entry(0, 0, 51, 4, 0, "BBBB"),
        ]))
        # B's right child points back at B: a loop, not a terminator.
        struct.pack_into("<H", table, 20 + 2, 20 // 4)
        blob = build_image({39: bytes(table)}, 39, len(table))
        with self.assertRaises(xdvdfs.XdvdfsError) as caught:
            list(open_image(blob).entries())
        self.assertIn("cycle", str(caught.exception))

    def test_a_name_running_past_the_table_is_reported(self):
        table = entry(0, 0, 50, 4, 0, "AAAA")
        table = bytearray(table)
        table[13] = 200  # name_length far past the end
        blob = build_image({39: bytes(table)}, 39, len(table))
        with self.assertRaises(xdvdfs.XdvdfsError) as caught:
            list(open_image(blob).entries())
        self.assertIn("name", str(caught.exception))

    def test_an_absurd_directory_size_is_refused(self):
        table = entry(0, 0, 50, 4, 0, "AAAA")
        blob = build_image({39: table}, 39, 64 * 1024 * 1024)
        with self.assertRaises(xdvdfs.XdvdfsError) as caught:
            list(open_image(blob).entries())
        self.assertIn("not a directory", str(caught.exception))

    def test_an_empty_directory_yields_nothing(self):
        root = directory([entry(0, 0, 40, 0, xdvdfs.ATTR_DIRECTORY, "EMPTY")])
        blob = build_image({39: root}, 39, len(root))
        paths = [e.path for e in open_image(blob).entries()]
        self.assertEqual(paths, ["/EMPTY"])


class CliTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "disc.iso")
        with open(self.path, "wb") as handle:
            handle.write(madden_shaped_image())

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        saved = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            code = xdvdfs.main(argv)
        finally:
            sys.stdout, sys.stderr = saved
        return code, out.getvalue(), err.getvalue()

    def test_list_prints_tab_separated_records(self):
        code, out, _ = self.run_cli(["list", self.path, "--quiet"])
        self.assertEqual(code, 0)
        rows = [line.split("\t") for line in out.splitlines()]
        self.assertEqual(len(rows), 3)          # files only, no /DATA
        self.assertTrue(all(len(row) == 3 for row in rows))
        self.assertIn(["/DATA/DB_TEAMS.DAT", "51", str(len(TWO))], rows)

    def test_list_all_includes_directories(self):
        _, out, _ = self.run_cli(["list", self.path, "--quiet", "--all"])
        self.assertIn("/DATA\t", out)

    def test_extract_writes_the_file(self):
        destination = os.path.join(self.tmp.name, "got.bin")
        code, out, _ = self.run_cli(
            ["extract", self.path, "/DATA/DB_TEAMS.DAT", "-o", destination])
        self.assertEqual(code, 0)
        with open(destination, "rb") as handle:
            self.assertEqual(handle.read(), TWO)
        self.assertIn("byte(s)", out)

    def test_extract_reports_a_missing_path_without_a_traceback(self):
        code, _, err = self.run_cli(["extract", self.path, "/DATA/NOPE.DAT"])
        self.assertEqual(code, 1)
        self.assertIn("error:", err)


if __name__ == "__main__":
    unittest.main()

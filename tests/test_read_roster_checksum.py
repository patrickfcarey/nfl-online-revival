"""Reading the console's own computed checksum out of a PCSX2 savestate.

This tool exists because guessing cost more than measuring: five candidate
checksum algorithms were tested against hardware and all five rejected, with
one bit of feedback per login and nothing about *how* wrong they were.

Which makes a silent misread here expensive in a specific way -- it does not
look like a bug, it looks like a sixth wrong algorithm. The value is a bare
32-bit number with no structure to sanity-check it against, so an address off
by a word, a wrong endianness, or a member read from the wrong offset all
produce a plausible answer.

The savestates here are synthetic ZIPs. Zstandard is only exercised when the
`zstd` binary is present, since PCSX2 now writes that format and `zipfile`
cannot read it.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import read_roster_checksum as rrc  # noqa: E402

#: Small enough to build in memory, and it only has to reach past the address
#: under test -- read_word never looks at the total length.
_RAM = 0x2000


def _savestate(words, method=zipfile.ZIP_DEFLATED, member=rrc.EE_MEMORY,
               size=_RAM):
    """A .p2s-shaped ZIP whose EE memory holds `words` at their addresses."""
    ram = bytearray(size)
    for address, value in words.items():
        struct.pack_into("<I", ram, address, value)
    handle, path = tempfile.mkstemp(suffix=".p2s")
    os.close(handle)
    with zipfile.ZipFile(path, "w", compression=method) as archive:
        archive.writestr(member, bytes(ram))
    return path


class ReadWord(unittest.TestCase):
    def test_reads_the_slot_the_measurement_patch_writes(self):
        path = _savestate({0x100: 0x8108963C}, size=0x1000)
        self.addCleanup(os.unlink, path)
        self.assertEqual(rrc.read_word(path, 0x100), 0x8108963C)

    def test_the_word_is_little_endian(self):
        # A big-endian read of 0x8108963c gives 0x3c960881 -- just as plausible
        # a checksum, and wrong.
        path = _savestate({0x40: 0x01020304}, size=0x1000)
        self.addCleanup(os.unlink, path)
        self.assertEqual(rrc.read_word(path, 0x40), 0x01020304)

    def test_the_default_address_is_the_patched_slot(self):
        self.assertEqual(rrc.SLOT_ADDRESS, 0x00600B2C)
        path = _savestate({rrc.SLOT_ADDRESS: 0xABCD1234},
                          size=rrc.SLOT_ADDRESS + 16)
        self.addCleanup(os.unlink, path)
        self.assertEqual(rrc.read_word(path), 0xABCD1234)

    def test_neighbouring_addresses_are_distinct(self):
        # Guards an off-by-one word in the seek, which would silently return
        # whatever the game stored next door.
        path = _savestate({0x100: 1, 0x104: 2}, size=0x1000)
        self.addCleanup(os.unlink, path)
        self.assertEqual(rrc.read_word(path, 0x100), 1)
        self.assertEqual(rrc.read_word(path, 0x104), 2)


class Bounds(unittest.TestCase):
    def test_a_negative_address_is_refused(self):
        path = _savestate({}, size=0x1000)
        self.addCleanup(os.unlink, path)
        with self.assertRaises(rrc.ReadError):
            rrc.read_word(path, -4)

    def test_an_address_past_ee_ram_is_refused(self):
        path = _savestate({}, size=0x1000)
        self.addCleanup(os.unlink, path)
        with self.assertRaises(rrc.ReadError) as caught:
            rrc.read_word(path, rrc.EE_SIZE)
        self.assertIn("outside EE RAM", str(caught.exception))

    def test_the_last_word_of_ram_is_addressable(self):
        """EE_SIZE - 4 is where the final word begins.

        The bound was `< EE_SIZE - 4`, which rejected it -- an off-by-one that
        put the last four bytes of RAM out of reach.
        """
        last = rrc.EE_SIZE - 4
        path = _savestate({}, size=0x1000)
        self.addCleanup(os.unlink, path)
        with self.assertRaises(rrc.ReadError) as caught:
            rrc.read_word(path, last)
        # It must fail for running off the end of *this* small file, not for
        # being an illegal address.
        self.assertNotIn("outside EE RAM", str(caught.exception))


class BadInput(unittest.TestCase):
    def test_a_file_that_is_not_a_zip(self):
        handle, path = tempfile.mkstemp()
        os.write(handle, b"this is not a savestate")
        os.close(handle)
        self.addCleanup(os.unlink, path)
        with self.assertRaises(rrc.ReadError) as caught:
            rrc.read_word(path)
        self.assertIn("cannot open", str(caught.exception))

    def test_a_missing_file(self):
        with self.assertRaises(rrc.ReadError):
            rrc.read_word("/nonexistent/state.p2s")

    def test_a_zip_without_ee_memory_lists_what_it_has(self):
        path = _savestate({}, member="something-else.bin", size=0x100)
        self.addCleanup(os.unlink, path)
        with self.assertRaises(rrc.ReadError) as caught:
            rrc.read_word(path, 0)
        self.assertIn("has no eeMemory.bin", str(caught.exception))
        self.assertIn("something-else.bin", str(caught.exception))

    def test_a_truncated_ee_memory(self):
        path = _savestate({}, size=0x100)
        self.addCleanup(os.unlink, path)
        with self.assertRaises(rrc.ReadError) as caught:
            rrc.read_word(path, 0x200)
        self.assertIn("truncated", str(caught.exception))

    def test_an_uncompressed_member_works_too(self):
        path = _savestate({0x10: 7}, method=zipfile.ZIP_STORED, size=0x100)
        self.addCleanup(os.unlink, path)
        self.assertEqual(rrc.read_word(path, 0x10), 7)


class RawMember(unittest.TestCase):
    """Lifting the stored bytes out without decompressing them."""

    def test_returns_the_compressed_bytes(self):
        path = _savestate({0x10: 7}, method=zipfile.ZIP_STORED, size=0x100)
        self.addCleanup(os.unlink, path)
        raw = rrc._raw_member(path, rrc.EE_MEMORY)
        # Stored, so the raw bytes are the payload.
        self.assertEqual(len(raw), 0x100)
        self.assertEqual(struct.unpack_from("<I", raw, 0x10)[0], 7)

    def test_it_trusts_the_local_header_lengths(self):
        """The central directory and the local header can disagree.

        Trusting the central directory's name and extra-field lengths puts the
        read at the wrong offset, and what comes back is the tail of the header
        rather than the member.
        """
        path = _savestate({0: 0x11223344}, method=zipfile.ZIP_STORED,
                          size=0x100)
        self.addCleanup(os.unlink, path)
        raw = rrc._raw_member(path, rrc.EE_MEMORY)
        self.assertEqual(struct.unpack_from("<I", raw, 0)[0], 0x11223344)


@unittest.skipIf(shutil.which("zstd") is None, "the zstd binary is not here")
class Zstandard(unittest.TestCase):
    """PCSX2 now writes method 93, which zipfile cannot decompress."""

    def _zstd_savestate(self, words, size=_RAM):
        ram = bytearray(size)
        for address, value in words.items():
            struct.pack_into("<I", ram, address, value)
        compressed = subprocess.run(["zstd", "-q", "--stdout", "-"],
                                    input=bytes(ram),
                                    stdout=subprocess.PIPE, check=True).stdout
        handle, path = tempfile.mkstemp(suffix=".p2s")
        os.close(handle)
        # zipfile will not write method 93, so assemble the archive by hand.
        name = rrc.EE_MEMORY.encode()
        local = struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, rrc.METHOD_ZSTD,
                            0, 0, 0, len(compressed), len(ram),
                            len(name), 0) + name
        central = struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0,
                              rrc.METHOD_ZSTD, 0, 0, 0, len(compressed),
                              len(ram), len(name), 0, 0, 0, 0, 0, 0) + name
        offset = len(local) + len(compressed)
        end = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(central),
                          offset, 0)
        Path(path).write_bytes(local + compressed + central + end)
        return path

    def test_reads_through_the_zstd_binary(self):
        path = self._zstd_savestate({0x100: 0x8108963C})
        self.addCleanup(os.unlink, path)
        self.assertEqual(rrc.read_word(path, 0x100), 0x8108963C)

    def test_running_off_the_end_is_reported(self):
        path = self._zstd_savestate({}, size=0x200)
        self.addCleanup(os.unlink, path)
        with self.assertRaises(rrc.ReadError) as caught:
            rrc.read_word(path, 0x1000)
        self.assertIn("ended before", str(caught.exception))


class Cli(unittest.TestCase):
    def test_a_zero_slot_is_reported_as_a_failure(self):
        """Zero means the check has not run, not that the checksum is zero.

        Returning 0 there would read as a successful measurement of a value
        that is simply the initialised state of the slot.
        """
        path = _savestate({0x100: 0}, size=0x1000)
        self.addCleanup(os.unlink, path)
        self.assertEqual(rrc.main([path, "--address", "0x100"]), 1)

    def test_a_non_zero_slot_succeeds(self):
        path = _savestate({0x100: 42}, size=0x1000)
        self.addCleanup(os.unlink, path)
        self.assertEqual(rrc.main([path, "--address", "0x100"]), 0)

    def test_an_unreadable_savestate_exits_two(self):
        self.assertEqual(rrc.main(["/nonexistent/x.p2s"]), 2)

    def test_candidates_can_be_compared(self):
        path = _savestate({0x100: 42}, size=0x1000)
        self.addCleanup(os.unlink, path)
        self.assertEqual(
            rrc.main([path, "--address", "0x100", "--compare", "42",
                      "--compare", "43"]), 0)


if __name__ == "__main__":
    unittest.main()

"""The TERF container reader, with the compressed path that was missing.

`docs/play-data.md` recorded "no populated playbook tables in GAMEDATA.DAT".
The tables were there all along: 67 of that container's 76 members are
LZH1-packed, and a reader that tested the *stored* bytes for `DB` magic found
none of them. These tests pin the two defects behind that false negative --
member offsets measured to the wrong chunk when a `COMP` block is present, and
the magic test applied before decompression -- and pin the rule that came out
of it: a member this reader cannot decode raises, because "I cannot open this"
and "this holds no database" are different answers and conflating them cost a
documented negative.

Game data is not in the repository, so the fixtures are built here, including
the LZH1 streams. `_pack` emits the simplest legal stream the format allows --
one block, every literal on a nine-bit code, no back-references -- and the
first test decodes it with the shipped decompressor so a wrong fixture fails
loudly instead of agreeing with a wrong reader.
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import lzh1                                     # noqa: E402
from tools import madden_tdb as tdb                        # noqa: E402


# --------------------------------------------------------------------------
# fixtures


class _BitWriter:
    """MSB-first, the order `lzh1.Bits.get` consumes."""

    def __init__(self) -> None:
        self.out = bytearray()
        self.acc = 0
        self.used = 0

    def put(self, value: int, width: int) -> None:
        for bit in range(width - 1, -1, -1):
            self.acc = (self.acc << 1) | ((value >> bit) & 1)
            self.used += 1
            if self.used == 8:
                self.out.append(self.acc)
                self.acc = 0
                self.used = 0

    def finish(self) -> bytes:
        if self.used:
            self.out.append(self.acc << (8 - self.used))
        return bytes(self.out)


def _pack(payload: bytes) -> bytes:
    """*payload* as an LZH1 stream of literals only.

    Code lengths are stored raw as 4-bit values, so handing every symbol
    0..256 a length of 9 makes the canonical assignment the identity: symbol
    *n* is the nine-bit code *n*. That is legal LZH1 and needs no compressor.
    """
    bits = _BitWriter()
    bits.put(0, 1)                              # 0 => a block follows
    for symbol in range(285):                   # literal/length code lengths
        bits.put(9 if symbol <= 256 else 0, 4)
    for _ in range(30):                         # distance alphabet: unused
        bits.put(0, 4)
    for byte in payload:
        bits.put(byte, 9)
    bits.put(256, 9)                            # end of block
    bits.put(1, 1)                              # end of stream
    bits.put(0, 32)                             # ...and its ignored trailer
    return bits.finish()


def _table(name, fields, rows):
    """One TDB table: (name, [(col, bits)], [{col: value}])."""
    offset = 0
    defs = []
    for col, bits in fields:
        defs.append((col, offset, bits))
        offset += bits
    record_bytes = (offset + 7) // 8

    header = bytearray(40)
    struct.pack_into("<I", header, 8, record_bytes)
    struct.pack_into("<I", header, 12, offset)
    struct.pack_into("<H", header, 20, max(len(rows), 1))
    struct.pack_into("<H", header, 22, len(rows))
    header[28] = len(defs)

    body = bytearray()
    for col, off, bits in defs:
        entry = bytearray(16)
        struct.pack_into("<I", entry, 4, off)
        entry[8:12] = col.encode("latin-1").ljust(4, b" ")[:4]
        struct.pack_into("<I", entry, 12, bits)
        body += entry
    for row in rows:
        record = bytearray(record_bytes)
        for col, off, bits in defs:
            value = row.get(col, 0)
            for bit in range(bits):
                if value >> bit & 1:
                    record[(off + bit) // 8] |= 1 << ((off + bit) % 8)
        body += record
    return name, bytes(header) + bytes(body)


def _database(tables):
    directory = bytearray()
    blob = bytearray()
    for name, data in tables:
        directory += name.encode("latin-1").ljust(4, b" ")[:4]
        directory += struct.pack("<I", len(blob))
        blob += data
    header = bytearray(24)
    header[0:2] = b"DB"
    struct.pack_into("<I", header, 8, 24 + len(directory) + len(blob))
    struct.pack_into("<I", header, 16, len(tables))
    return bytes(header) + bytes(directory) + bytes(blob)


def _book(plays):
    """A minimal playbook database: one AI group over *plays* plays."""
    return _database([
        _table("PLYL", [("SETL", 8), ("PLYL", 16)],
               [{"SETL": 1, "PLYL": 10 + i} for i in range(plays)]),
        _table("PBAI", [("PBPL", 16), ("AIGR", 8), ("prct", 8)],
               [{"PBPL": 10 + i, "AIGR": 6, "prct": 50} for i in range(plays)]),
    ])


def _terf(members, header_size=64):
    """A TERF container. *members* is [(codec, payload)]; codec None = no COMP.

    Stored bytes are produced from the payload according to the codec, so the
    ``COMP`` entry's uncompressed size is the payload's real length.
    """
    codecs = [c for c, _ in members]
    packed = []
    for codec, payload in members:
        if codec == tdb.CODEC_LZH1:
            packed.append(_pack(payload))
        else:
            packed.append(payload)

    count = len(members)
    head = bytearray(header_size)
    head[0:4] = b"TERF"
    struct.pack_into("<I", head, 4, header_size)
    head[8:12] = b"\x02\x02\x00\x05"
    struct.pack_into("<H", head, 12, header_size)
    struct.pack_into("<H", head, 14, count)     # 0x0E: the member count

    want_comp = any(c is not None for c in codecs)
    data = bytearray()
    directory = bytearray()
    for blob in packed:
        directory += struct.pack("<II", 8 + len(data), len(blob))
        data += blob                            # offsets measured to 'DATA'

    dir_chunk = bytearray(b"DIR1" + struct.pack("<I", 8 + len(directory)))
    dir_chunk += directory
    out = bytes(head) + bytes(dir_chunk)
    if want_comp:
        comp = bytearray(b"COMP" + struct.pack("<I", 8 + 8 * count))
        for (codec, payload) in members:
            comp += struct.pack("<II", codec or tdb.CODEC_STORED, len(payload))
        out += bytes(comp)
    out += b"DATA" + struct.pack("<I", 8 + len(data)) + bytes(data)
    return out


# --------------------------------------------------------------------------
# tests


class Fixture(unittest.TestCase):
    """The fixture builder must be right before it can prove anything."""

    def test_pack_round_trips_through_the_shipped_decoder(self):
        payload = bytes(range(256)) + b"the quick brown fox" * 8
        self.assertEqual(lzh1.lzh1_decompress(_pack(payload), len(payload)),
                         payload)

    def test_pack_handles_an_empty_payload(self):
        self.assertEqual(lzh1.lzh1_decompress(_pack(b""), 0), b"")


class UncompressedContainer(unittest.TestCase):
    """DB_TEAMS.DAT and TEMPLATE.DAT ship with no COMP chunk at all."""

    def setUp(self):
        self.blob = _terf([(None, _book(2)), (None, b"not a database")])

    def test_reads_as_before(self):
        container = tdb.Container(self.blob)
        self.assertEqual(len(container), 2)
        self.assertFalse(container.compressed)
        self.assertEqual(container.database(0).table("PLYL").record_count, 2)
        self.assertIsNone(container.database(1))

    def test_every_member_reports_as_stored(self):
        container = tdb.Container(self.blob)
        self.assertEqual(container.codec(0), (tdb.CODEC_STORED, len(_book(2))))

    def test_member_returns_the_bytes_unchanged(self):
        container = tdb.Container(self.blob)
        self.assertEqual(container.member(1), b"not a database")

    def test_a_non_terf_file_still_raises(self):
        with self.assertRaises(tdb.TdbError):
            tdb.Container(b"NOPE" + b"\x00" * 128)

    def test_a_missing_directory_still_raises(self):
        broken = bytearray(self.blob)
        broken[64:68] = b"XXXX"
        with self.assertRaises(tdb.TdbError):
            tdb.Container(bytes(broken))


class CompressedContainer(unittest.TestCase):
    """GAMEDATA.DAT: a COMP chunk, and 67 of 76 members packed."""

    def setUp(self):
        self.book = _book(3)
        self.blob = _terf([(tdb.CODEC_LZH1, self.book),
                           (tdb.CODEC_STORED, _book(1)),
                           (tdb.CODEC_LZH1, b"MMAP" + b"\x00" * 40)])

    def test_a_packed_member_parses_as_a_database(self):
        container = tdb.Container(self.blob)
        self.assertTrue(container.compressed)
        book = container.database(0)
        self.assertIsNotNone(book)
        self.assertEqual(book.table("PLYL").record_count, 3)
        self.assertEqual(book.table("PBAI").record_count, 3)

    def test_a_packed_member_unpacks_to_the_declared_bytes(self):
        container = tdb.Container(self.blob)
        self.assertEqual(container.member(0), self.book)
        self.assertEqual(container.codec(0), (tdb.CODEC_LZH1, len(self.book)))

    def test_member_offsets_are_measured_to_the_DATA_chunk(self):
        # The COMP chunk sits between DIR1 and DATA, so a base taken as "the
        # end of the directory" lands inside COMP and every member is garbage.
        container = tdb.Container(self.blob)
        self.assertEqual(container.stored(0), _pack(self.book))

    def test_a_stored_member_is_not_run_through_the_decompressor(self):
        container = tdb.Container(self.blob)
        self.assertEqual(container.database(1).table("PLYL").record_count, 1)

    def test_a_packed_member_that_is_not_a_database_is_None(self):
        container = tdb.Container(self.blob)
        self.assertIsNone(container.database(2))
        self.assertTrue(container.member(2).startswith(b"MMAP"))

    def test_databases_walks_the_whole_container(self):
        container = tdb.Container(self.blob)
        counts = [db.table("PLYL").record_count for db in container.databases()]
        self.assertEqual(counts, [3, 1])

    def test_a_member_is_unpacked_once(self):
        container = tdb.Container(self.blob)
        calls = []
        real = lzh1.lzh1_decompress

        def counted(src, size=None):
            calls.append(size)
            return real(src, size)

        lzh1.lzh1_decompress = counted
        try:
            container.database(0)
            container.database(0)
            container.member(0)
        finally:
            lzh1.lzh1_decompress = real
        self.assertEqual(len(calls), 1)

    def test_an_empty_member_is_empty_rather_than_an_error(self):
        container = tdb.Container(_terf([(tdb.CODEC_LZH1, b"")]))
        self.assertEqual(container.member(0), b"")
        self.assertIsNone(container.database(0))

    def test_a_128_byte_header_is_followed(self):
        # The Xbox disc's GAMEDATA.DAT states 0x80 where the PS2's states 0x40;
        # DIR1 is located from the chunk's own size field, not from a constant.
        container = tdb.Container(_terf([(tdb.CODEC_LZH1, self.book)],
                                        header_size=128))
        self.assertEqual(container.database(0).table("PLYL").record_count, 3)


class UnreadableMembers(unittest.TestCase):
    """The rule the false negative bought: cannot-decode is not not-a-database."""

    def test_an_unknown_codec_raises_rather_than_returning_None(self):
        blob = bytearray(_terf([(tdb.CODEC_LZH1, _book(1))]))
        index = blob.find(b"COMP")
        struct.pack_into("<I", blob, index + 8, 7)          # codec 7
        container = tdb.Container(bytes(blob))
        with self.assertRaises(tdb.TdbError) as caught:
            container.database(0)
        self.assertIn("codec 7", str(caught.exception))

    def test_a_short_unpack_raises(self):
        book = _book(1)
        blob = bytearray(_terf([(tdb.CODEC_LZH1, book)]))
        index = blob.find(b"COMP")
        struct.pack_into("<I", blob, index + 12, len(book) + 64)
        container = tdb.Container(bytes(blob))
        with self.assertRaises(tdb.TdbError):
            container.database(0)


GAMEDATA = Path(__file__).resolve().parent.parent / "extract" / "GAMEDATA.DAT"


@unittest.skipUnless(GAMEDATA.exists(), "disc data is not in the repository")
class ShippedContainer(unittest.TestCase):
    """One member of the real file, when a disc happens to be extracted."""

    def test_member_4_is_a_populated_playbook(self):
        container = tdb.load(str(GAMEDATA))
        self.assertTrue(container.compressed)
        book = container.database(4)
        self.assertIsNotNone(book)
        self.assertIn("PBAI", book)
        self.assertGreater(book.table("PBAI").record_count, 0)
        self.assertGreater(book.table("PLYL").record_count, 0)


if __name__ == "__main__":
    unittest.main()

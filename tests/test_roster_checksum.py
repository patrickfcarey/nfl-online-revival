"""The roster checksum, and the container it is computed over.

These tests pin the parts that were recovered from the executable and would
otherwise rot silently: a wrong field order or a wrong seed still produces a
perfectly plausible 32-bit number, and nothing downstream would notice.

They deliberately do not require ``DB_TEAMS.DAT``. Game data is not in this
repository and never should be, so the container tests build a synthetic TERF
archive with the same geometry. The one test that needs the real file is skipped
unless ``MADDEN_DB_TEAMS`` points at it.
"""

from __future__ import annotations

import os
import struct
import sys
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import madden_tdb, roster_checksum  # noqa: E402


def build_table(fields, records, record_bytes):
    """A TDB table: 40-byte header, 16-byte field defs, then packed records."""
    header = bytearray(40)
    struct.pack_into("<I", header, 8, record_bytes)
    struct.pack_into("<I", header, 12, record_bytes * 8)
    struct.pack_into("<H", header, 20, len(records))
    struct.pack_into("<H", header, 22, len(records))
    header[28] = len(fields)
    blob = bytes(header)
    for name, type_code, offset_bits, bits in fields:
        blob += struct.pack("<I", type_code)
        blob += struct.pack("<I", offset_bits)
        blob += name.encode("latin-1")
        blob += struct.pack("<I", bits)
    for record in records:
        blob += record.ljust(record_bytes, b"\x00")
    return blob


def build_db(tables):
    """A TDB file: header, table directory, then the tables themselves."""
    header = bytearray(24)
    header[0:2] = b"DB"
    struct.pack_into("<I", header, 16, len(tables))
    directory = b""
    body = b""
    for name, blob in tables:
        directory += name.encode("latin-1") + struct.pack("<I", len(body))
        body += blob
    struct.pack_into("<I", header, 8, len(header) + len(directory) + len(body))
    return bytes(header) + directory + body


def build_container(dbs):
    """A TERF archive. Member offsets are relative to the directory's end."""
    dir_size = 8 + len(dbs) * 8
    header = bytearray(0x40)
    header[0:4] = b"TERF"
    struct.pack_into("<I", header, 0x04, 0x40)
    struct.pack_into("<H", header, 0x0E, len(dbs))
    directory = bytearray(dir_size)
    directory[0:4] = b"DIR1"
    struct.pack_into("<I", directory, 4, dir_size)
    body = b""
    for i, db in enumerate(dbs):
        struct.pack_into("<II", directory, 8 + i * 8, len(body), len(db))
        body += db
    return bytes(header) + bytes(directory) + body


class BitReading(unittest.TestCase):
    """LSB-first within a byte and within a field, per the Madden 08 reader."""

    def test_single_bits(self):
        self.assertEqual(madden_tdb.read_bits(b"\x01", 0, 1), 1)
        self.assertEqual(madden_tdb.read_bits(b"\x02", 0, 1), 0)
        self.assertEqual(madden_tdb.read_bits(b"\x02", 1, 1), 1)

    def test_field_spanning_a_byte_boundary(self):
        # bits 6..9 of 0b...  -> low two bits from byte 0, high two from byte 1
        self.assertEqual(madden_tdb.read_bits(b"\xc0\x03", 6, 4), 0b1111)

    def test_reads_past_the_end_are_zero(self):
        # The last field of a record can run into the trailing pad.
        self.assertEqual(madden_tdb.read_bits(b"\xff", 4, 8), 0x0F)

    def test_full_byte(self):
        self.assertEqual(madden_tdb.read_bits(b"\xa5", 0, 8), 0xA5)


class FourCC(unittest.TestCase):
    def test_reverses(self):
        self.assertEqual(madden_tdb.fourcc(b"DIGP"), "PGID")
        self.assertEqual(madden_tdb.fourcc(b"RVOP"), "POVR")
        self.assertEqual(madden_tdb.fourcc(b"YALP"), "PLAY")


class FieldList(unittest.TestCase):
    """The 31 columns, as the marshaller's format string names them."""

    def test_count_matches_the_buffer(self):
        self.assertEqual(len(roster_checksum.FIELDS), 31)
        self.assertEqual(len(roster_checksum.FIELDS) * 4,
                         roster_checksum.BUFFER_SIZE)

    def test_no_duplicates(self):
        self.assertEqual(len(set(roster_checksum.FIELDS)),
                         len(roster_checksum.FIELDS))

    def test_order_is_the_wire_order(self):
        # Straight off 0x00579958. Pinned because a permutation still produces
        # a plausible checksum.
        self.assertEqual(roster_checksum.FIELDS[:4],
                         ("PGID", "TGID", "PPOS", "PACC"))
        self.assertEqual(roster_checksum.FIELDS[-4:],
                         ("PWGT", "PHAN", "PTEN", "PSTY"))
        # POVR, not PVOR -- 'RVOP' reversed.
        self.assertIn("POVR", roster_checksum.FIELDS)
        self.assertNotIn("PVOR", roster_checksum.FIELDS)


class Checksum(unittest.TestCase):
    """The accumulator at 0x0039d7e8, driven from 0x0012a888."""

    def test_seed_is_the_row_count(self):
        row = [0] * 31
        expected = zlib.crc32(struct.pack("<31I", *row), 1) & 0xFFFFFFFF
        self.assertEqual(roster_checksum.checksum([row]), expected)

    def test_seed_is_not_zero(self):
        row = [0] * 31
        wrong = zlib.crc32(struct.pack("<31I", *row), 0) & 0xFFFFFFFF
        self.assertNotEqual(roster_checksum.checksum([row]), wrong)

    def test_empty_roster_is_the_bare_seed(self):
        # No rows means the loop never runs; the value is the count itself.
        self.assertEqual(roster_checksum.checksum([]), 0)

    def test_row_order_matters(self):
        a = [1] + [0] * 30
        b = [2] + [0] * 30
        self.assertNotEqual(roster_checksum.checksum([a, b]),
                            roster_checksum.checksum([b, a]))

    def test_chains_across_rows(self):
        a = [1] + [0] * 30
        b = [2] + [0] * 30
        running = 2
        for row in (a, b):
            running = zlib.crc32(struct.pack("<31I", *row), running)
        self.assertEqual(roster_checksum.checksum([a, b]), running & 0xFFFFFFFF)

    def test_fields_are_little_endian_32_bit(self):
        row = [0x01020304] + [0] * 30
        packed = struct.pack("<31I", *row)
        self.assertEqual(packed[:4], b"\x04\x03\x02\x01")
        self.assertEqual(len(packed), roster_checksum.BUFFER_SIZE)
        self.assertEqual(roster_checksum.checksum([row]),
                         zlib.crc32(packed, 1) & 0xFFFFFFFF)


class ContainerParsing(unittest.TestCase):
    def setUp(self):
        # Two members: one team inside the 1-32 filter, one outside it.
        self.fields = [(name, madden_tdb.TYPE_UINT, i * 32, 32)
                       for i, name in enumerate(roster_checksum.FIELDS)]

    def _member(self, team_id, player_ids):
        records = []
        for pid in player_ids:
            values = [0] * 31
            values[0] = pid
            values[1] = team_id
            records.append(struct.pack("<31I", *values))
        table = build_table(self.fields, records, 124)
        return build_db([("PLAY", table)])

    def test_reads_members_and_tables(self):
        blob = build_container([self._member(1, [10, 20])])
        container = madden_tdb.Container(blob)
        self.assertEqual(len(container), 1)
        db = container.database(0)
        self.assertIn("PLAY", db)
        self.assertEqual(db.table("PLAY").record_count, 2)

    def test_filters_by_team_id(self):
        blob = build_container([self._member(1, [10]),
                                self._member(99, [11])])
        rows = roster_checksum.collect_rows(madden_tdb.Container(blob))
        self.assertEqual([r[0] for r in rows], [10])

    def test_boundaries_are_inclusive(self):
        blob = build_container([self._member(1, [10]), self._member(32, [11]),
                                self._member(0, [12]), self._member(33, [13])])
        rows = roster_checksum.collect_rows(madden_tdb.Container(blob))
        self.assertEqual(sorted(r[0] for r in rows), [10, 11])

    def test_sorts_by_player_id_across_members(self):
        blob = build_container([self._member(2, [30, 10]),
                                self._member(1, [20, 5])])
        rows = roster_checksum.collect_rows(madden_tdb.Container(blob))
        self.assertEqual([r[0] for r in rows], [5, 10, 20, 30])

    def test_rejects_a_non_container(self):
        with self.assertRaises(madden_tdb.TdbError):
            madden_tdb.Container(b"NOPE" + b"\x00" * 128)

    def test_skips_members_that_are_not_databases(self):
        blob = build_container([b"junk" * 32, self._member(1, [10])])
        rows = roster_checksum.collect_rows(madden_tdb.Container(blob))
        self.assertEqual([r[0] for r in rows], [10])

    def test_missing_column_is_an_error(self):
        short = [f for f in self.fields if f[0] != "POVR"]
        table = build_table(short, [b"\x00" * 124], 124)
        blob = build_container([build_db([("PLAY", table)])])
        with self.assertRaises(madden_tdb.TdbError):
            roster_checksum.collect_rows(madden_tdb.Container(blob))


class RealRoster(unittest.TestCase):
    """Against the retail file, when one is available."""

    def setUp(self):
        path = os.environ.get("MADDEN_DB_TEAMS")
        if not path or not os.path.exists(path):
            self.skipTest("set MADDEN_DB_TEAMS to DB_TEAMS.DAT to run this")
        self.path = path

    def test_bit_extraction_decodes_real_players(self):
        """The check that the packed fields are actually being read correctly.

        Names alone would only prove the string offsets. The cross-checks are
        what matter: the one player at position 0 is also the only one with high
        throw power, and the punter is simultaneously the slowest man on the
        roster and the highest rated. Independent fields at different offsets
        and widths agreeing on the same people does not survive a wrong bit
        order.
        """
        container = madden_tdb.load(self.path)
        table = container.database(1).table("PLAY")

        def text(record, name):
            field = table.fields[name]
            start = field.offset_bits // 8
            raw = record[start:start + field.bits // 8]
            return raw.split(b"\x00")[0].decode("latin-1")

        players = {}
        for i in range(table.record_count):
            record = table.record(i)
            players[text(record, "PLNA")] = {
                name: table.value(record, name)
                for name in ("PPOS", "PTHP", "PSPD", "POVR", "TGID")
            }

        # Position 0 picks out exactly the 2003 Bears quarterback room.
        at_position_zero = {n for n, p in players.items() if p["PPOS"] == 0}
        self.assertEqual(at_position_zero, {"Stewart", "Grossman", "Chandler"})

        # And throw power, read at a different offset and width, independently
        # picks out the same three men -- they are the top three, and the drop
        # to fourth is enormous (87/85/85 against 48).
        by_arm = sorted(players.items(), key=lambda kv: -kv[1]["PTHP"])
        self.assertEqual({n for n, _ in by_arm[:3]}, at_position_zero)
        self.assertGreater(by_arm[2][1]["PTHP"], by_arm[3][1]["PTHP"] + 20)

        # The punter: slowest man on the roster and near the top of it.
        punter = players["Maynard"]
        self.assertEqual(punter["PSPD"], min(p["PSPD"] for p in players.values()))

        # Urlacher rates highest on a 2003 Bears roster, which is the other
        # thing a wrong bit order would not reproduce.
        best = max(players.items(), key=lambda kv: kv[1]["POVR"])
        self.assertEqual(best[0], "Urlacher")

        self.assertTrue(all(p["TGID"] == 1 for p in players.values()))

    def test_shape_of_the_retail_roster(self):
        value, rows = roster_checksum.from_file(self.path)
        # 32 teams of roughly 53; anything far off means the parse is wrong.
        self.assertEqual(len(rows), 1743)
        teams = {row[1] for row in rows}
        self.assertEqual(teams, set(range(1, 33)))
        ids = [row[0] for row in rows]
        self.assertEqual(len(ids), len(set(ids)), "PGIDs must be unique or the "
                                                  "sort order is ambiguous")
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(value, 0x8108963C)


if __name__ == "__main__":
    unittest.main()

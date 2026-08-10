"""The playbook reader.

Game data is not in the repository, so these build TDB bytes by hand. That is
worth the effort here for one reason: the reader's whole job is resolving a
graph of 16-bit ids across nineteen optional tables, and the failure mode that
matters is a *wrong join*, not a crash. A fixture with two plays and three
positions catches a wrong join; a real playbook would only hide it in volume.

The fixture builder mirrors `madden_tdb`'s reader rather than importing it, so a
change to the reader that silently alters the layout it accepts fails here
instead of passing by construction.
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import madden_play as play
from tools import madden_tdb as tdb


def _table(name, fields, rows):
    """One TDB table: (name, [(col, bits)], [ {col: value} ]).

    Fields are packed least-significant-bit first, which is the convention
    `madden_tdb.read_bits` established empirically.
    """
    offset = 0
    defs = []
    for col, bits in fields:
        defs.append((col, offset, bits))
        offset += bits
    record_bytes = (offset + 7) // 8

    header = bytearray(40)          # _TABLE_HEADER_SIZE
    struct.pack_into("<I", header, 8, record_bytes)
    struct.pack_into("<I", header, 12, offset)
    struct.pack_into("<H", header, 20, max(len(rows), 1))
    struct.pack_into("<H", header, 22, len(rows))
    header[28] = len(defs)

    body = bytearray()
    for col, off, bits in defs:
        entry = bytearray(16)       # _TABLE_FIELD_SIZE
        struct.pack_into("<I", entry, 0, 0)
        struct.pack_into("<I", entry, 4, off)
        entry[8:12] = col.encode("latin-1").ljust(4, b" ")[:4]
        struct.pack_into("<I", entry, 12, bits)
        body += entry

    for row in rows:
        record = bytearray(record_bytes)
        for col, off, bits in defs:
            value = row.get(col, 0)
            if isinstance(value, bytes):
                record[off // 8:off // 8 + bits // 8] = value.ljust(bits // 8, b"\0")
                continue
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
    struct.pack_into("<I", header, 16, len(tables))
    return bytes(header) + bytes(directory) + bytes(blob)


class Fixture(unittest.TestCase):
    """A two-play book: one pass, one run, with assignments on the pass."""

    def setUp(self):
        raw = _database([
            _table("FORM", [("FORM", 8), ("FTYP", 8), ("name", 64)],
                   [{"FORM": 1, "FTYP": 2, "name": b"I-Form"}]),
            _table("SETL", [("SETL", 8), ("FORM", 8), ("SETT", 8), ("SITT", 8),
                            ("SLF_", 32), ("name", 64)],
                   [{"SETL": 3, "FORM": 1, "name": b"Normal"}]),
            _table("PLYL", [("SETL", 8), ("PLYL", 16), ("SITT", 8), ("PLYT", 8),
                            ("PLF_", 32), ("name", 64), ("risk", 8), ("motn", 8)],
                   [{"SETL": 3, "PLYL": 10, "PLYT": 2, "name": b"HB Dive"},
                    {"SETL": 3, "PLYL": 11, "PLYT": 5, "name": b"PA Deep"}]),
            _table("PBPL", [("PBPL", 16), ("PLYL", 16), ("PBST", 16), ("ord_", 8)],
                   [{"PBPL": 100, "PLYL": 10}, {"PBPL": 101, "PLYL": 11}]),
            _table("PBAI", [("PBPL", 16), ("AIGR", 8), ("prct", 8)],
                   [{"PBPL": 100, "AIGR": 7, "prct": 60},
                    {"PBPL": 101, "AIGR": 7, "prct": 40},
                    {"PBPL": 101, "AIGR": 9, "prct": 90}]),
            _table("PLYS", [("PSAL", 16), ("ARTL", 16), ("PLYL", 16), ("poso", 8)],
                   [{"PSAL": 500, "ARTL": 0, "PLYL": 11, "poso": 2},
                    {"PSAL": 501, "ARTL": 900, "PLYL": 11, "poso": 1},
                    {"PSAL": 502, "ARTL": 0, "PLYL": 10, "poso": 1}]),
            _table("PSAL", [("val1", 8), ("val2", 8), ("val3", 8), ("PSAL", 16),
                            ("code", 8), ("step", 8)],
                   [{"PSAL": 501, "step": 1, "code": 4, "val1": 12},
                    {"PSAL": 501, "step": 0, "code": 3, "val1": 7},
                    {"PSAL": 500, "step": 0, "code": 9}]),
        ])
        self.book = play.Playbook(tdb.Database(raw, 0))

    def test_plays_resolve_their_set_and_name(self):
        plays = {p.id: p for p in self.book.plays()}
        self.assertEqual(plays[10].name, "HB Dive")
        self.assertEqual(plays[11].type, 5)
        self.assertEqual(plays[11].set, 3)

    def test_book_plays_maps_inclusion_ids_not_play_ids(self):
        # PBAI keys off PBPL, so confusing the two silently mis-attributes
        # every AI weight -- the wrong-join failure this suite exists for.
        self.assertEqual(self.book.book_plays(), {100: 10, 101: 11})

    def test_ai_groups_partition_by_group(self):
        groups = self.book.ai_groups()
        self.assertEqual(sorted(groups), [7, 9])
        self.assertEqual(len(groups[7]), 2)
        self.assertEqual(sum(e.weight for e in groups[7]), 100)
        self.assertEqual(groups[9][0].play, 101)

    def test_assignments_are_per_play_and_position_ordered(self):
        rows = self.book.assignments(11)
        self.assertEqual([r.position for r in rows], [1, 2])
        self.assertEqual(rows[0].art, 900)
        self.assertEqual(rows[1].art, 0)

    def test_step_chains_are_ordered_by_step_not_file_order(self):
        # The fixture stores step 1 before step 0 deliberately.
        steps = self.book.assignments(11)[0].steps
        self.assertEqual([s.index for s in steps], [0, 1])
        self.assertEqual([s.code for s in steps], [3, 4])

    def test_assignments_for_an_unknown_play_are_empty_not_an_error(self):
        self.assertEqual(self.book.assignments(999), [])


class Shape(unittest.TestCase):
    def test_a_database_without_the_play_tables_is_refused(self):
        raw = _database([_table("TEAM", [("TGID", 8)], [{"TGID": 1}])])
        with self.assertRaises(play.PlayError):
            play.Playbook(tdb.Database(raw, 0))

    def test_absent_optional_tables_yield_nothing_rather_than_raising(self):
        # A walker over a thousand container members must not have to guard
        # every accessor.
        raw = _database([
            _table("PLYL", [("SETL", 8), ("PLYL", 16), ("SITT", 8), ("PLYT", 8),
                            ("PLF_", 32), ("name", 64), ("risk", 8), ("motn", 8)],
                   [{"PLYL": 1}]),
            _table("PBPL", [("PBPL", 16), ("PLYL", 16), ("PBST", 16), ("ord_", 8)],
                   [{"PBPL": 1, "PLYL": 1}]),
        ])
        book = play.Playbook(tdb.Database(raw, 0))
        self.assertEqual(book.formations(), [])
        self.assertEqual(book.ai_groups(), {})
        self.assertEqual(book.assignments(1), [])
        self.assertEqual(book.art(5), [])


class Text(unittest.TestCase):
    def test_name_is_sliced_not_read_as_a_bit_packed_integer(self):
        name, data = _table("PLYL", [("SETL", 8), ("PLYL", 16), ("SITT", 8),
                                     ("PLYT", 8), ("PLF_", 32), ("name", 64),
                                     ("risk", 8), ("motn", 8)],
                            [{"PLYL": 1, "name": b"Slant"}])
        raw = _database([(name, data),
                         _table("PBPL", [("PBPL", 16), ("PLYL", 16)],
                                [{"PBPL": 1, "PLYL": 1}])])
        book = play.Playbook(tdb.Database(raw, 0))
        self.assertEqual(book.plays()[0].name, "Slant")

    def test_a_missing_name_column_is_empty_rather_than_fatal(self):
        table = tdb.Table(_table("X", [("a", 8)], [{"a": 1}])[1], 0)
        self.assertEqual(play.text(table, table.record(0), "name"), "")


if __name__ == "__main__":
    unittest.main()

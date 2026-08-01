"""Building a LEAG container small enough for the console to accept.

The console refuses a payload larger than its buffer -- measured on hardware,
393,216 bytes transfers whole while 2,723,072 and 8,439,360 are both hung up on
mid-transfer. Retail DB_TEAMS.DAT is 8.4 MB, so a subset has to be built.

Validity is the whole point of these tests. Truncating a container leaves a
directory pointing past the end of the file, and handing that to the game's
loader risks crashing a console rather than teaching anything.
"""

from __future__ import annotations

import os
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import build_roster, madden_tdb  # noqa: E402


def synthetic_container(member_count=4, players_per_member=3):
    """A TERF container with the same geometry as the retail one."""
    fields = [(n, madden_tdb.TYPE_UINT, i * 32, 32)
              for i, n in enumerate(("PGID", "TGID", "PPOS"))]

    def table(team_id):
        records = [struct.pack("<3I", 100 + team_id * 10 + k, team_id, 0)
                   for k in range(players_per_member)]
        header = bytearray(40)
        struct.pack_into("<I", header, 8, 12)
        struct.pack_into("<I", header, 12, 96)
        struct.pack_into("<H", header, 20, len(records))
        struct.pack_into("<H", header, 22, len(records))
        header[28] = len(fields)
        blob = bytes(header)
        for name, kind, offset, bits in fields:
            blob += struct.pack("<II", kind, offset) + name.encode() + struct.pack("<I", bits)
        return blob + b"".join(records)

    def database(team_id):
        head = bytearray(24)
        head[0:2] = b"DB"
        struct.pack_into("<I", head, 16, 1)
        body = table(team_id)
        directory = b"PLAY" + struct.pack("<I", 0)
        struct.pack_into("<I", head, 8, len(head) + len(directory) + len(body))
        return bytes(head) + directory + body

    dbs = [database(t + 1) for t in range(member_count)]
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


class Building(unittest.TestCase):
    def setUp(self):
        self.source = synthetic_container(member_count=6)

    def test_selected_members_survive_a_round_trip(self):
        blob = build_roster.build(self.source, [0, 2, 4])
        container = madden_tdb.Container(blob)
        self.assertEqual(len(container), 3)
        teams = []
        for i in range(3):
            table = container.database(i).table("PLAY")
            teams.append(table.value(table.record(0), "TGID"))
        self.assertEqual(teams, [1, 3, 5])   # members 0,2,4 -> team ids 1,3,5

    def test_output_is_smaller_than_the_source(self):
        blob = build_roster.build(self.source, [0])
        self.assertLess(len(blob), len(self.source))

    def test_every_member_reads_back_as_a_database(self):
        # A truncated container also "parses" until something dereferences a
        # directory entry pointing past the end. Check each one opens.
        blob = build_roster.build(self.source, [0, 1, 2])
        container = madden_tdb.Container(blob)
        for i in range(len(container)):
            self.assertIsNotNone(container.database(i), "member %d" % i)

    def test_verify_rejects_a_container_with_no_players(self):
        empty = synthetic_container(member_count=2, players_per_member=0)
        blob = build_roster.build(empty, [0])
        with self.assertRaises(build_roster.BuildError):
            build_roster.verify(blob, 1)

    def test_an_out_of_range_member_is_refused(self):
        with self.assertRaises(build_roster.BuildError):
            build_roster.build(self.source, [99])

    def test_no_members_is_refused(self):
        with self.assertRaises(build_roster.BuildError):
            build_roster.build(self.source, [])

    def test_budget_is_respected(self):
        whole = len(build_roster.build(self.source, list(range(6))))
        chosen = build_roster.members_within(self.source, whole // 2)
        self.assertTrue(chosen)
        self.assertLessEqual(len(build_roster.build(self.source, chosen)),
                             whole // 2)


class RealRoster(unittest.TestCase):
    def setUp(self):
        path = os.environ.get("MADDEN_DB_TEAMS")
        if not path or not os.path.exists(path):
            self.skipTest("set MADDEN_DB_TEAMS to DB_TEAMS.DAT to run this")
        self.source = Path(path).read_bytes()

    def test_a_container_is_not_a_servable_payload(self):
        """A TERF container can never be installed as a roster.

        0x004c9e90 requires the first word to be 0x08004244 -- "DB" plus version
        0x0800 -- so a container header is refused outright. The payload is a
        single raw TDB, which is what member 0 of template.dat is. This is
        pinned because an earlier attempt served a container and the failure
        looked like a size problem.
        """
        blob = build_roster.build(self.source, [1, 2])
        self.assertEqual(blob[:4], b"TERF")
        self.assertNotEqual(blob[:2], b"DB")


if __name__ == "__main__":
    unittest.main()


class Resealing(unittest.TestCase):
    """The TDB's own block checksums.

    A block whose checksum no longer matches is refused with error 43, so any
    edit has to reseal. The layout was recovered by reproducing the stored
    values on an untouched file rather than by reading the checker, and the test
    below is the same argument: resealing something unedited must change
    nothing at all. If a boundary were wrong, some byte would move.
    """

    def setUp(self):
        path = os.environ.get("MADDEN_LEAG")
        if not path or not os.path.exists(path):
            self.skipTest("set MADDEN_LEAG to a raw LEAG database to run this")
        self.blob = Path(path).read_bytes()

    def test_resealing_an_untouched_file_changes_nothing(self):
        from tools import mark_roster
        self.assertEqual(mark_roster.reseal(self.blob), self.blob)

    def test_an_edit_changes_only_the_name_and_its_checksums(self):
        from tools import mark_roster
        marked = mark_roster.rename_first_player(self.blob, "ZZTEST")
        self.assertEqual(len(marked), len(self.blob),
                         "the console demands the exact on-disc size")
        changed = sum(1 for a, b in zip(self.blob, marked) if a != b)
        # The name field plus the checksum words that cover it.
        self.assertLess(changed, 64)
        self.assertGreater(changed, 0)

    def test_the_edit_survives_a_reparse(self):
        from tools import madden_tdb, mark_roster
        marked = mark_roster.rename_first_player(self.blob, "ZZTEST")
        table = madden_tdb.Database(marked, 0).table("PLAY")
        field = table.fields["PLNA"]
        start = table._records + field.offset_bits // 8
        surname = marked[start:start + field.bits // 8].split(b"\x00")[0]
        self.assertEqual(surname, b"ZZTEST")


class TdbChecksum(unittest.TestCase):
    def test_algorithm_is_msb_first_no_final_xor(self):
        from tools import mark_roster
        # Poly 0x04C11DB7, init 0xFFFFFFFF, MSB-first, no final inversion --
        # deliberately not zlib's reflected CRC-32, which the OUTER manifest
        # checksum uses. Two different algorithms in one payload.
        import zlib
        data = b"the quick brown fox"
        self.assertNotEqual(mark_roster.tdb_crc(data),
                            zlib.crc32(data) & 0xFFFFFFFF)
        self.assertEqual(mark_roster.tdb_crc(b""), 0xFFFFFFFF)

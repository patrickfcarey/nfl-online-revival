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

    def test_a_full_roster_does_not_fit_the_registered_budget(self):
        """The finding that matters: 409600 cannot hold 32 teams.

        Either that figure is not the real ceiling, or a roster update is not a
        whole LEAG database. Pinned so the assumption is not quietly made again.
        """
        with self.assertRaises(Exception):
            blob = build_roster.build(self.source, list(range(1, 33)))
            if len(blob) > 409600:
                raise AssertionError("does not fit")

    def test_what_does_fit_is_a_valid_container(self):
        members = build_roster.members_within(self.source, 409600)
        blob = build_roster.build(self.source, members)
        self.assertLessEqual(len(blob), 409600)
        build_roster.verify(blob, len(members))


if __name__ == "__main__":
    unittest.main()

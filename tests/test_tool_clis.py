"""The command-line front ends of the roster tools.

Coverage of these modules stopped at their `main()` functions, which is exactly
where a tool decides whether to write a file and what to tell you about it. The
library half being right is not much comfort if the wrapper writes the output
before validating it, or exits 0 on a failure.

Two properties get most of the attention here, because both have cost this
project real time:

* **an exit code that means what it says.** `patch_iso_roster` once exited 0
  having copied 3.2 GB and patched nothing.
* **nothing is written on a refused input.** These tools produce files a
  console installs by wiping its league database first, so a half-valid output
  is worse than no output.

The fixtures come from the suites that already build them, rather than a second
set that could drift from the first.
"""

from __future__ import annotations

import contextlib
import io
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools import build_roster, madden_tdb, mark_roster  # noqa: E402
from tools import pine, roster_checksum  # noqa: E402

try:                                        # discovery puts tests/ on the path
    from tests.test_build_year_roster import make_league, player
    from tests.test_pine import FakePine
except ImportError:                         # running the module directly
    from test_build_year_roster import make_league, player   # noqa: F401
    from test_pine import FakePine


def _container(members):
    """A TERF archive, the shape madden_tdb.load expects."""
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


def _write(data, suffix=".dat"):
    handle, path = tempfile.mkstemp(suffix=suffix)
    os.write(handle, data)
    os.close(handle)
    return path


def _out(path):
    handle, out = tempfile.mkstemp(suffix=path)
    os.close(handle)
    os.unlink(out)
    return out


@contextlib.contextmanager
def captured():
    """Swallow stdout and stderr; these tools are chatty by design."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


class MarkRoster(unittest.TestCase):
    """Renaming one player, so an install can be *seen* rather than inferred."""

    def setUp(self):
        self.source = _write(make_league(teams=(1, 2), per_team=3, pool=2))
        self.addCleanup(os.unlink, self.source)
        self.output = _out(".dat")

    def test_writes_the_surname_and_keeps_the_size(self):
        with captured():
            code = mark_roster.main([self.source, self.output,
                                     "--surname", "ZZTEST"])
        self.assertEqual(code, 0)
        self.addCleanup(os.unlink, self.output)
        marked = Path(self.output).read_bytes()
        self.assertEqual(len(marked), os.path.getsize(self.source))
        table = madden_tdb.Database(marked, 0).table("PLAY")
        self.assertEqual(mark_roster._surname(table, marked), "ZZTEST")

    def test_the_default_surname_is_the_documented_one(self):
        with captured():
            mark_roster.main([self.source, self.output])
        self.addCleanup(os.unlink, self.output)
        marked = Path(self.output).read_bytes()
        table = madden_tdb.Database(marked, 0).table("PLAY")
        self.assertEqual(mark_roster._surname(table, marked), "ZZTEST")

    def test_the_block_checksums_are_resealed(self):
        # A block whose checksum no longer matches is error 43 on the console.
        with captured():
            mark_roster.main([self.source, self.output])
        self.addCleanup(os.unlink, self.output)
        marked = Path(self.output).read_bytes()
        self.assertEqual(mark_roster.reseal(marked), marked)

    def test_a_missing_source_exits_two(self):
        with captured():
            self.assertEqual(
                mark_roster.main(["/nonexistent.dat", self.output]), 2)
        self.assertFalse(os.path.exists(self.output))

    def test_a_container_is_refused_rather_than_marked(self):
        """The console requires a raw TDB; a TERF is rejected outright.

        Passing the archive instead of member 0 is an easy mistake, and the
        resulting file would fail at 0x004c9e90 after the league database had
        already been wiped.
        """
        archive = _write(_container([make_league(), b"second"]))
        self.addCleanup(os.unlink, archive)
        with captured() as (_o, err):
            self.assertEqual(mark_roster.main([archive, self.output]), 2)
        self.assertIn("0x08004244", err.getvalue())
        self.assertFalse(os.path.exists(self.output))

    def test_an_empty_surname_is_refused(self):
        with self.assertRaises(ValueError):
            mark_roster.rename_first_player(
                Path(self.source).read_bytes(), "")

    def test_a_database_without_players_is_refused(self):
        empty = struct.pack("<I", 0x08004244) + b"\x00" * 60
        with self.assertRaises((ValueError, madden_tdb.TdbError)):
            mark_roster.rename_first_player(empty, "ZZTEST")

    def test_a_long_surname_is_truncated_not_overflowed(self):
        blob = Path(self.source).read_bytes()
        marked = mark_roster.rename_first_player(blob, "X" * 200)
        self.assertEqual(len(marked), len(blob))


class RosterChecksum(unittest.TestCase):
    """The CSUM the server announces, from a container on disk."""

    def setUp(self):
        self.path = _write(_container([make_league(teams=tuple(range(1, 5)),
                                                   per_team=4, pool=2)]))
        self.addCleanup(os.unlink, self.path)

    def test_from_file_returns_a_value_and_its_rows(self):
        value, rows = roster_checksum.from_file(self.path)
        self.assertTrue(rows)
        self.assertEqual(value, roster_checksum.checksum(rows))
        self.assertLess(value, 1 << 32)

    def test_only_players_on_teams_one_to_thirty_two_are_counted(self):
        _value, rows = roster_checksum.from_file(self.path)
        team = roster_checksum.FIELDS.index("TGID")
        self.assertTrue(all(1 <= row[team] <= 32 for row in rows))
        self.assertEqual(len(rows), 4 * 4, "the free-agent pool leaked in")

    def test_the_cli_prints_both_forms_by_default(self):
        with captured() as (out, _err):
            self.assertEqual(roster_checksum.main([self.path]), 0)
        value, _rows = roster_checksum.from_file(self.path)
        self.assertIn("%d  (0x%08x)" % (value, value), out.getvalue())

    def test_the_wire_format_is_decimal(self):
        with captured() as (out, _err):
            roster_checksum.main([self.path, "--format", "decimal", "--quiet"])
        value, _rows = roster_checksum.from_file(self.path)
        self.assertEqual(out.getvalue().strip(), str(value))

    def test_hex_is_available_for_eyeballing(self):
        with captured() as (out, _err):
            roster_checksum.main([self.path, "--format", "hex", "--quiet"])
        value, _rows = roster_checksum.from_file(self.path)
        self.assertEqual(out.getvalue().strip(), "0x%08x" % value)

    def test_diagnostics_go_to_stderr_so_the_value_can_be_piped(self):
        with captured() as (out, err):
            roster_checksum.main([self.path, "--format", "decimal"])
        self.assertIn("rows", err.getvalue())
        self.assertNotIn("rows", out.getvalue())

    def test_a_file_with_no_team_players_exits_two(self):
        # A plausible file that is the wrong one.
        lonely = _write(_container([make_league(teams=(), per_team=0, pool=3)]))
        self.addCleanup(os.unlink, lonely)
        with captured() as (_o, err):
            self.assertEqual(roster_checksum.main([lonely]), 2)
        self.assertIn("wrong file", err.getvalue())

    def test_an_unreadable_file_exits_two(self):
        with captured():
            self.assertEqual(roster_checksum.main(["/nonexistent.DAT"]), 2)


class BuildRoster(unittest.TestCase):
    """Extracting the servable payload out of a container."""

    def setUp(self):
        self.league = make_league(teams=(1, 2), per_team=3, pool=2)
        self.path = _write(_container([self.league, b"second member"]))
        self.addCleanup(os.unlink, self.path)
        self.output = _out(".dat")

    def test_extracting_member_zero_yields_the_raw_tdb(self):
        """THIS is the servable payload -- the console refuses a container."""
        with captured():
            self.assertEqual(
                build_roster.main([self.path, self.output,
                                   "--extract-member", "0"]), 0)
        self.addCleanup(os.unlink, self.output)
        self.assertEqual(Path(self.output).read_bytes(), self.league)

    def test_extracting_a_member_that_is_not_a_tdb_is_refused(self):
        with captured() as (_o, err):
            self.assertEqual(
                build_roster.main([self.path, self.output,
                                   "--extract-member", "1"]), 2)
        self.assertIn("not a TDB", err.getvalue())
        self.assertFalse(os.path.exists(self.output))

    def test_a_member_index_past_the_end_is_refused(self):
        with captured():
            self.assertEqual(
                build_roster.main([self.path, self.output,
                                   "--extract-member", "99"]), 2)

    def test_an_unreadable_source_exits_two(self):
        with captured():
            self.assertEqual(
                build_roster.main(["/nonexistent.DAT", self.output,
                                   "--extract-member", "0"]), 2)

    def test_a_subset_container_reads_back_as_a_container(self):
        with captured():
            code = build_roster.main([self.path, self.output, "--members", "0"])
        self.assertEqual(code, 0)
        self.addCleanup(os.unlink, self.output)
        rebuilt = madden_tdb.Container(Path(self.output).read_bytes())
        self.assertEqual(len(rebuilt), 1)
        self.assertIn("PLAY", rebuilt.database(0))

    def test_a_build_with_no_members_is_refused(self):
        with self.assertRaises(build_roster.BuildError):
            build_roster.build(Path(self.path).read_bytes(), [])

    def test_a_member_outside_the_container_is_refused(self):
        with self.assertRaises(build_roster.BuildError):
            build_roster.build(Path(self.path).read_bytes(), [99])

    def test_verify_rejects_a_container_with_no_players(self):
        blob = _container([b"not a database at all"])
        with self.assertRaises((build_roster.BuildError, madden_tdb.TdbError)):
            build_roster.verify(blob, 1)

    def test_a_result_over_the_budget_is_refused(self):
        with captured() as (_o, err):
            code = build_roster.main([self.path, self.output,
                                      "--members", "0", "--max-bytes", "16"])
        self.assertEqual(code, 2)
        self.assertIn("exceeds", err.getvalue())


class PineCli(unittest.TestCase):
    def setUp(self):
        self.server = FakePine()
        self.addCleanup(self.server.stop)

    def test_info_reports_the_running_game(self):
        self.server.replies = [struct.pack("<I", 11) + b"Madden\x00",
                               struct.pack("<I", 11) + b"SLUS-20752\x00",
                               struct.pack("<I", 4) + b"2.0\x00"]
        with captured() as (out, _err):
            self.assertEqual(pine.main(["--socket", self.server.path,
                                        "--info"]), 0)
        self.assertIn("SLUS-20752", out.getvalue())

    def test_a_read_prints_the_value(self):
        self.server.replies = [struct.pack("<I", 0x1234)]
        with captured() as (out, _err):
            self.assertEqual(
                pine.main(["--socket", self.server.path, "--read", "0x600b2c"]),
                0)
        self.assertIn("4660", out.getvalue())

    def test_a_string_read_is_nul_terminated(self):
        self.server.replies = [b"ZZTE", b"ST\x00\x00"] * 64
        with captured() as (out, _err):
            pine.main(["--socket", self.server.path, "--string", "0x100"])
        self.assertIn("ZZTEST", out.getvalue())

    def test_a_write_reports_what_it_wrote(self):
        with captured() as (out, _err):
            self.assertEqual(
                pine.main(["--socket", self.server.path,
                           "--write", "0x100", "0x2a"]), 0)
        self.assertIn("0x100", out.getvalue().replace("0x00000100", "0x100"))

    def test_an_unreachable_socket_exits_two(self):
        with captured() as (_o, err):
            self.assertEqual(pine.main(["--socket", "/nonexistent.sock",
                                        "--info"]), 2)
        self.assertIn("EnablePINE", err.getvalue())


if __name__ == "__main__":
    unittest.main()

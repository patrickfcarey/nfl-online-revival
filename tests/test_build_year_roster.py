"""The year-roster builder: the largest untested thing that ships to hardware.

`tools/build_year_roster.py` produced the 2023 roster on the USB kit, and until
now nothing checked any of it. The failure mode that matters is not a crash --
it is a file the console cheerfully installs with the wrong men on the wrong
teams, or with a geometry that fails the checksum after the install path has
already wiped the league database (0x004c9ee8).

No game data lives in this repository and none should, so everything here runs
against a synthetic TDB with the same geometry: 40-byte table headers, 16-byte
field definitions, bit-packed records, and the trailing checksum word after
each block that `reseal` expects to find.

The test worth keeping is `TeamIds.test_the_games_table_wins_over_the_files_own_id`.
The scraped rosters and the game disagree about team ids 30, 31 and 32 -- the
game has 30 Titans, 31 Vikings, 32 Texans; the data has 30 Texans, 31 Titans,
32 Vikings -- and the twenty-nine that agree make a wrong build look correct.
That is how Derrick Henry ended up a Viking.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import build_year_roster as byr  # noqa: E402
from tools import madden_tdb  # noqa: E402
from tools.mark_roster import reseal  # noqa: E402

# ---------------------------------------------------------------------------
# a synthetic league database
# ---------------------------------------------------------------------------

#: 8-bit columns every PLAY record carries, in the order they are laid out.
_BYTE_FIELDS = (
    "PPOS", "PJEN", "PHGT", "PWGT", "PUCL", "PHAN", "PTEN", "PSTY",
    "POVR", "PSPD", "PACC", "PSTR", "PAGI", "PAWR", "PCTH", "PCAR",
    "PTHP", "PTHA", "PKPR", "PKAC", "PRBK", "PPBK", "PTAK", "PBTK",
    "PJMP", "PKRT", "PINJ", "PSTA", "PTGH", "PIMP",
)

_PLAY_RECORD_BYTES = 64
_TEAM_RECORD_BYTES = 8


def _play_fields():
    """(name, type, offset_bits, bits) for the PLAY table."""
    fields = [("PFNA", madden_tdb.TYPE_STRING, 0, 96),
              ("PLNA", madden_tdb.TYPE_STRING, 96, 128),
              ("TGID", madden_tdb.TYPE_UINT, 224, 16),
              ("PGID", madden_tdb.TYPE_UINT, 240, 16)]
    offset = 256
    for name in _BYTE_FIELDS:
        fields.append((name, madden_tdb.TYPE_UINT, offset, 8))
        offset += 8
    assert offset <= _PLAY_RECORD_BYTES * 8, "record layout overflows"
    return fields


def _team_fields():
    return [("TGID", madden_tdb.TYPE_UINT, 0, 16),
            ("TSNA", madden_tdb.TYPE_STRING, 16, 32)]


def _table(fields, records, record_bytes):
    """40-byte header, field definitions, then packed records.

    No trailing checksum word here: in the real format each block's checksum
    lands inside the *next* structure's first field, so the space comes from
    packing the tables contiguously and adding four bytes once, at the end.
    """
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
        blob += bytes(record).ljust(record_bytes, b"\x00")
    return blob


def _database(tables):
    header = bytearray(24)
    header[0:2] = b"DB"
    header[2:4] = struct.pack("<H", 0x0800)      # the version the loader wants
    struct.pack_into("<I", header, 16, len(tables))
    directory = b""
    body = b""
    for name, blob in tables:
        directory += name.encode("latin-1") + struct.pack("<I", len(body))
        body += blob
    # Four bytes for the final block's checksum, which has no following
    # structure to live inside.
    body += b"\x00" * 4
    struct.pack_into("<I", header, 8, len(header) + len(directory) + len(body))
    return bytes(header) + directory + body


def _packed(fields, values):
    """Build one record by writing each field with the module's own writer."""
    record = bytearray(_PLAY_RECORD_BYTES)

    class _Fake:
        pass

    fake = _Fake()
    fake.fields = {f[0]: madden_tdb.Field(f[0], f[1], f[2], f[3])
                   for f in fields}
    for name, value in values.items():
        if isinstance(value, str):
            byr._set_text(record, fake, name, value)
        else:
            byr._set(record, fake, name, value)
    return record


def make_league(teams=(1, 2), per_team=3, pool=4, abbreviations=None):
    """A league database: a TEAM table and a PLAY table with known contents."""
    play_fields = _play_fields()
    records = []
    pgid = 1
    for team_id in teams:
        for _ in range(per_team):
            records.append(_packed(play_fields, {
                "TGID": team_id, "PGID": pgid, "PFNA": "Old",
                "PLNA": "Player%d" % pgid, "POVR": 50}))
            pgid += 1
    for _ in range(pool):
        records.append(_packed(play_fields, {
            "TGID": 1009, "PGID": pgid, "PFNA": "Free",
            "PLNA": "Agent%d" % pgid, "POVR": 40}))
        pgid += 1

    abbreviations = abbreviations or {t: "T%02d" % t for t in teams}
    team_records = []
    for team_id in teams:
        record = bytearray(_TEAM_RECORD_BYTES)
        struct.pack_into("<H", record, 0, team_id)
        name = abbreviations[team_id].encode("latin-1")[:3]
        record[2:2 + len(name)] = name
        team_records.append(record)

    return _database([
        ("TEAM", _table(_team_fields(), team_records, _TEAM_RECORD_BYTES)),
        ("PLAY", _table(play_fields, records, _PLAY_RECORD_BYTES)),
    ])


def player(first, last, position="QB", jersey=12, height=74, weight=220,
           overall=80, published=True, **ratings):
    values = dict(byr.COMMON)
    values["POVR"] = overall
    values.update(ratings)
    return {"first": first, "last": last, "position": position,
            "jersey": jersey, "height": height, "weight": weight,
            "ratings": values, "published": published}


# ---------------------------------------------------------------------------


class Fixture(unittest.TestCase):
    """The synthetic database has to be readable by the real reader first."""

    def test_it_parses_as_a_tdb(self):
        blob = make_league()
        database = madden_tdb.Database(blob, 0)
        self.assertIn("PLAY", database)
        self.assertIn("TEAM", database)
        self.assertEqual(database.table("PLAY").record_count, 2 * 3 + 4)

    def test_reseal_accepts_its_geometry(self):
        # If reseal cannot walk it, every rewrite test below would be checking
        # a file the console would reject.
        sealed = reseal(make_league())
        self.assertEqual(len(sealed), len(make_league()))
        self.assertEqual(reseal(sealed), sealed, "checksums are not stable")


class Normalise(unittest.TestCase):
    def test_strips_suffixes_and_punctuation(self):
        self.assertEqual(byr.normalise("Odell Beckham Jr."),
                         byr.normalise("Odell Beckham"))
        self.assertEqual(byr.normalise("Robert Griffin III"),
                         byr.normalise("Robert Griffin"))
        self.assertEqual(byr.normalise("A.J. Brown"), "ajbrown")
        self.assertEqual(byr.normalise("D'Andre Swift"), "dandreswift")

    def test_is_case_and_space_insensitive(self):
        self.assertEqual(byr.normalise("  JUSTIN   fields "),
                         byr.normalise("Justin Fields"))

    def test_distinct_names_stay_distinct(self):
        self.assertNotEqual(byr.normalise("Josh Allen"),
                            byr.normalise("Keenan Allen"))


class PlayerName(unittest.TestCase):
    """Six spellings across sixteen ratings files.

    Handling only the newest silently yields 0% coverage for older years, which
    reads as a data problem rather than a parsing one.
    """

    def test_every_known_spelling(self):
        for record in ({"Full Name": "Justin Fields"},
                       {"Name": "Justin Fields"},
                       {"NAME": "Justin Fields"},
                       {"First Name": "Justin", "Last Name": "Fields"},
                       {"FIRSTNAME": "Justin", "LASTNAME": "Fields"},
                       {"FirstName": "Justin", "LastName": "Fields"}):
            self.assertEqual(byr.player_name(record), "Justin Fields",
                             "failed for %s" % sorted(record))

    def test_a_record_with_no_name_yields_empty(self):
        self.assertEqual(byr.player_name({"Overall Rating": 80}), "")


class Positions(unittest.TestCase):
    def test_every_alias_maps_into_the_games_own_list(self):
        for modern, madden in byr.POSITION_ALIASES.items():
            self.assertIn(madden, byr.POSITIONS,
                          "%s maps to %s, which the game has no slot for"
                          % (modern, madden))

    def test_the_aliases_that_would_misplace_a_player(self):
        # A safety at defensive end is the failure this table exists to stop.
        self.assertEqual(byr.POSITION_ALIASES["S"], "FS")
        self.assertEqual(byr.POSITION_ALIASES["RB"], "HB")
        self.assertEqual(byr.POSITION_ALIASES["EDGE"], "LE")
        self.assertEqual(byr.POSITION_ALIASES["NT"], "DT")

    def test_the_order_is_the_one_read_back_off_retail(self):
        self.assertEqual(byr.POSITIONS[0], "QB")
        self.assertEqual(byr.POSITIONS[7], "C")
        self.assertEqual(byr.POSITIONS[14], "MLB")
        self.assertEqual(byr.POSITIONS[20], "P")
        self.assertEqual(len(byr.POSITIONS), 21)

    def test_every_position_has_a_baseline(self):
        for position in byr.POSITIONS:
            self.assertIn(position, byr.BASELINE)


class Ratings(unittest.TestCase):
    def test_published_ratings_are_clamped_to_the_field_width(self):
        out = byr.real_ratings({"Overall Rating": 150, "Speed": -20})
        self.assertEqual(out["POVR"], 99)
        self.assertEqual(out["PSPD"], 0)

    def test_missing_and_unparseable_values_fall_back_to_common(self):
        out = byr.real_ratings({"Overall Rating": None, "Speed": "N/A",
                                "Strength": "", "Agility": "not a number"})
        for column in ("POVR", "PSPD", "PSTR", "PAGI"):
            self.assertEqual(out[column], byr.COMMON[column])

    def test_float_strings_are_accepted(self):
        self.assertEqual(byr.real_ratings({"Overall Rating": "78.6"})["POVR"], 78)

    def test_every_rating_column_is_covered_by_common(self):
        # Otherwise a player inherits whatever the previous occupant had.
        for column in byr.RATING_COLUMNS.values():
            self.assertIn(column, byr.COMMON)

    def test_estimates_stay_inside_the_field_range(self):
        for position in byr.POSITIONS:
            for age in (0, 21, 24, 30, 40):
                for years in (0, 5, 20):
                    for column, value in byr.estimate_ratings(
                            position, age, years).items():
                        self.assertGreaterEqual(value, 0, column)
                        self.assertLessEqual(value, 99, column)

    def test_awareness_rises_with_experience(self):
        rookie = byr.estimate_ratings("QB", 22, 0)["PAWR"]
        veteran = byr.estimate_ratings("QB", 32, 10)["PAWR"]
        self.assertGreater(veteran, rookie)

    def test_speed_declines_past_thirty(self):
        young = byr.estimate_ratings("WR", 24, 2)["PSPD"]
        old = byr.estimate_ratings("WR", 36, 12)["PSPD"]
        self.assertGreater(young, old)


class RatingKeys(unittest.TestCase):
    """Sixteen ratings files, six spellings of "overall" between them."""

    def test_every_observed_spelling_folds_together(self):
        for spelling in ("OVERALL", "OVERALL RATING", "Overall",
                         "Overall Rating", "OverallRating", "overall_rating"):
            self.assertEqual(byr._RATING_BY_KEY.get(byr._rating_key(spelling)),
                             "POVR", spelling)

    def test_the_camel_case_suffix_is_stripped(self):
        for spelling, column in (("SpeedRating", "PSPD"),
                                 ("AgilityRating", "PAGI"),
                                 ("BreakTackleRating", "PBTK")):
            self.assertEqual(byr._RATING_BY_KEY.get(byr._rating_key(spelling)),
                             column, spelling)

    def test_ovr_is_accepted(self):
        # 2009 and 2015 use it and nothing else.
        self.assertEqual(byr._RATING_BY_KEY.get(byr._rating_key("OVR")), "POVR")

    def test_split_attributes_do_not_collide_with_the_plain_one(self):
        """A footwork score must never land in the run-block column.

        These are separate attributes the 2004-era TDB has no column for, so
        matching by prefix rather than by exact folded name would corrupt the
        rating it does have.
        """
        for other in ("RUNBLOCK FOOTWORK", "PASSBLOCK STRENGTH",
                      "THROW ACCURACY DEEP", "CATCH IN TRAFFIC"):
            self.assertIsNone(byr._RATING_BY_KEY.get(byr._rating_key(other)),
                              other)

    def test_ratings_are_read_whatever_the_spelling(self):
        for spelling in ("OVERALL", "Overall", "Overall Rating", "OVR"):
            self.assertEqual(byr.real_ratings({spelling: 88})["POVR"], 88,
                             spelling)

    def test_an_unreadable_file_shows_as_a_low_rated_fraction(self):
        """The number that would have caught the flat-60 rosters.

        A record matched by name but carrying no recognised column still
        reports success everywhere else.
        """
        self.assertEqual(byr.rated_fraction({"col_1": 5, "col_2": 6}), 0.0)
        self.assertGreater(byr.rated_fraction({"Overall Rating": 80,
                                               "Speed": 90}), 0.0)

    def test_split_throw_accuracy_is_reconstituted(self):
        # Every input is still a published EA rating for that player.
        out = byr.real_ratings({"Throw Accuracy Deep": 80,
                                "Throw Accuracy Mid": 90,
                                "Throw Accuracy Short": 94})
        self.assertEqual(out["PTHA"], 88)

    def test_a_plain_throw_accuracy_wins_over_the_split_one(self):
        out = byr.real_ratings({"Throw Accuracy": 70,
                                "Throw Accuracy Deep": 99,
                                "Throw Accuracy Short": 99})
        self.assertEqual(out["PTHA"], 70)


class NameMatching(unittest.TestCase):
    """Formal first names in the rosters, common ones in the ratings."""

    def setUp(self):
        self.ratings = {
            byr.normalise("Greg Olsen"): {"Full Name": "Greg Olsen",
                                          "Overall Rating": 88},
            byr.normalise("Josh McCown"): {"Full Name": "Josh McCown",
                                           "Overall Rating": 70},
            byr.normalise("Mike Williams"): {"Full Name": "Mike Williams",
                                             "Overall Rating": 80},
            byr.normalise("Michael Williams"): {"Full Name": "Michael Williams",
                                                "Overall Rating": 60},
        }
        self.by_surname = byr.surname_index(self.ratings)

    def test_an_exact_name_matches(self):
        got = byr.match_player("Greg", "Olsen", self.ratings, self.by_surname)
        self.assertEqual(got["Overall Rating"], 88)

    def test_a_formal_first_name_falls_back_to_the_common_one(self):
        for formal, common in (("Gregory", "Olsen"), ("Joshua", "McCown")):
            got = byr.match_player(formal, common, self.ratings,
                                   self.by_surname)
            self.assertIsNotNone(got, formal)

    def test_an_exact_match_wins_before_the_fallback_can_be_ambiguous(self):
        # "Mike Williams" is present verbatim, so the surname fallback -- which
        # would see two Williamses -- never runs.
        got = byr.match_player("Mike", "Williams", self.ratings,
                               self.by_surname)
        self.assertEqual(got["Overall Rating"], 80)

    def test_an_ambiguous_fallback_is_left_unmatched(self):
        """Handing one man's ratings to another is worse than an empty slot.

        Measured across eighteen years this arises once, and the answer there
        has to be "leave the slot alone" rather than pick.
        """
        ratings = {
            byr.normalise("Josh Allen"): {"Full Name": "Josh Allen"},
            byr.normalise("Joshua Allen"): {"Full Name": "Joshua Allen"},
        }
        by_surname = byr.surname_index(ratings)
        # "Jos" prefixes both, and neither is exact.
        self.assertIsNone(byr.match_player("Jos", "Allen", ratings, by_surname))
        # One candidate only: the fallback is allowed to act.
        self.assertIsNotNone(byr.match_player("Gregory", "Olsen",
                                              self.ratings, self.by_surname))

    def test_a_short_first_name_is_never_prefix_matched(self):
        # Two letters would pair "Al Woods" with "Alex Woods".
        ratings = {byr.normalise("Alex Woods"): {"Full Name": "Alex Woods"}}
        self.assertIsNone(byr.match_player("Al", "Woods", ratings,
                                           byr.surname_index(ratings)))

    def test_an_unknown_surname_matches_nothing(self):
        self.assertIsNone(byr.match_player("Nobody", "Atall", self.ratings,
                                           self.by_surname))

    def test_the_surname_index_groups_by_last_name(self):
        self.assertEqual(len(self.by_surname[byr.normalise("Williams")]), 2)


class Nicknames(unittest.TestCase):
    """First-name nicknames the prefix rule cannot reach."""

    def _index(self, *names):
        # No Position: these cases exercise the nickname logic, not the guard
        # (which has its own test class and passes on an unknown position).
        idx = {byr.normalise(n): {"Full Name": n, "Overall Rating": 80}
               for n in names}
        return idx, byr.surname_index(idx)

    def test_the_initials_family_matches(self):
        # tj is a prefix of nothing, which is why the prefix rule misses it.
        idx, by_surname = self._index("T.J. Lang")
        self.assertIsNotNone(byr.nickname_match("Thomas", "Lang", "LG",
                                                idx, by_surname))

    def test_a_classic_nickname_matches_both_directions(self):
        idx, by_surname = self._index("Mike Williams")
        self.assertIsNotNone(byr.nickname_match("Michael", "Williams", "WR",
                                                idx, by_surname))
        idx, by_surname = self._index("Michael Williams")
        self.assertIsNotNone(byr.nickname_match("Mike", "Williams", "WR",
                                                idx, by_surname))

    def test_an_ambiguous_expansion_is_refused(self):
        # Joe and Joey Porter both present -> neither may be chosen.
        idx, by_surname = self._index("Joe Porter", "Joey Porter")
        self.assertIsNone(byr.nickname_match("Joseph", "Porter", "LOLB",
                                             idx, by_surname))

    def test_a_position_mismatch_is_rejected(self):
        """Dimitri Patterson the corner is not Mike Patterson the tackle."""
        idx = {byr.normalise("Mike Patterson"): {"Full Name": "Mike Patterson",
                                                 "Position": "DT",
                                                 "Overall Rating": 80}}
        by_surname = byr.surname_index(idx)
        # Dimitri -> would need a nickname link; use a real one: the guard is
        # what we are testing, so match Michael->Mike but with a CB roster pos.
        self.assertIsNone(byr.nickname_match("Michael", "Patterson", "CB",
                                             idx, by_surname))

    def test_an_unrelated_first_name_does_not_match(self):
        idx, by_surname = self._index("Greg Olsen")
        self.assertIsNone(byr.nickname_match("Zebulon", "Olsen", "TE",
                                             idx, by_surname))


class ClaimArbitration(unittest.TestCase):
    """A weaker match may never take a record a stronger one owns.

    Green Bay carried both Tony Brown and Anthony Brown in 2018 -- two
    different men -- and the nickname link handed Tony the record Anthony owns
    by exact name. Seven such pairs existed across the eighteen years
    (Nick/Nicholas Williams, Joe/Joseph Walker, Mike/Michael Jordan), each one
    a real player wearing another real player's ratings.
    """

    def setUp(self):
        self.idx = {
            byr.normalise("Anthony Brown"): {"Full Name": "Anthony Brown",
                                             "Overall Rating": 75},
            byr.normalise("Tony Brown"): {"Full Name": "Tony Brown",
                                          "Overall Rating": 60},
        }
        self.by_surname = byr.surname_index(self.idx)

    def test_an_unclaimed_record_is_still_matched(self):
        idx = {byr.normalise("Anthony Brown"): {"Full Name": "Anthony Brown",
                                                "Overall Rating": 75}}
        by_surname = byr.surname_index(idx)
        self.assertIsNotNone(
            byr.nickname_match("Tony", "Brown", "CB", idx, by_surname,
                               claimed=set()))

    def test_a_claimed_record_is_refused(self):
        claimed = {byr.normalise("Anthony Brown")}
        self.assertIsNone(
            byr.nickname_match("Tony", "Brown", "CB", self.idx,
                               self.by_surname, claimed))

    def test_claims_are_gathered_across_the_whole_roster(self):
        """The owner may be on a different team.

        Gathering claims per team would make the answer depend on which team
        happened to be processed first.
        """
        roster = {"teams": [
            {"abbreviation": "DAL",
             "players": [{"name": {"first": "Anthony", "last": "Brown"}}]},
            {"abbreviation": "GB",
             "players": [{"name": {"first": "Tony", "last": "Brown"}}]},
        ]}
        claimed = byr.claimed_records(roster, self.idx, self.by_surname)
        self.assertIn(byr.normalise("Anthony Brown"), claimed)
        self.assertIsNone(
            byr.nickname_match("Tony", "Brown", "CB", self.idx,
                               self.by_surname, claimed))

    def test_no_claims_leaves_matching_unchanged(self):
        # Passing None must behave exactly as before the guard existed.
        self.assertIsNotNone(
            byr.nickname_match("Tony", "Brown", "CB", self.idx,
                               self.by_surname, None))


class PositionGuard(unittest.TestCase):
    """The defence against the name-keyed index collapsing two players."""

    def _rec(self, position):
        return {"Full Name": "X", "Position": position}

    def test_matching_buckets_pass(self):
        self.assertTrue(byr._same_person("CB", self._rec("DB")))
        self.assertTrue(byr._same_person("HB", self._rec("RB")))

    def test_a_clear_mismatch_fails(self):
        self.assertFalse(byr._same_person("DT", self._rec("HB")))
        self.assertFalse(byr._same_person("QB", self._rec("CB")))

    def test_a_centre_is_exempt(self):
        # Long-snappers are labelled C here and TE/LB in the ratings.
        self.assertTrue(byr._same_person("C", self._rec("LB")))

    def test_an_unknown_position_passes(self):
        # The guard catches collisions; it does not second-guess missing data.
        self.assertTrue(byr._same_person("", self._rec("DT")))
        self.assertTrue(byr._same_person("CB", {"Full Name": "X"}))


class AdjacentSeason(unittest.TestCase):
    """A miss's ratings from the nearest OTHER season."""

    class _FakeSeasons:
        def __init__(self, per_season):
            self._data = {}
            for season, names in per_season.items():
                idx = {byr.normalise(n): {"Full Name": n, "Overall Rating": ovr,
                                          "Position": pos}
                       for n, ovr, pos in names}
                self._data[season] = (idx, byr.surname_index(idx))

        def get(self, season):
            return self._data.get(season)

    def test_the_nearest_season_wins(self):
        seasons = self._FakeSeasons({
            2012: [("Joe Flacco", 88, "QB")],
            2010: [("Joe Flacco", 80, "QB")]})
        hit = byr.adjacent_season_ratings("Joe", "Flacco", "QB", 2011, seasons)
        self.assertIsNotNone(hit)
        # 2012 and 2010 are equidistant; the season ahead is preferred.
        self.assertEqual(hit[1], 2012)

    def test_it_prefers_the_closer_season_over_a_farther_one(self):
        # Distance 1 (2014) must beat distance 2 (2017) for a 2015 target.
        seasons = self._FakeSeasons({
            2014: [("Sam Bradford", 78, "QB")],
            2017: [("Sam Bradford", 82, "QB")]})
        hit = byr.adjacent_season_ratings("Sam", "Bradford", "QB", 2015, seasons)
        self.assertEqual(hit[1], 2014)

    def test_nothing_within_the_span_returns_none(self):
        seasons = self._FakeSeasons({2019: [("Old Timer", 70, "QB")]})
        self.assertIsNone(
            byr.adjacent_season_ratings("Old", "Timer", "QB", 2015, seasons))

    def test_a_position_collision_across_seasons_is_rejected(self):
        # A different Michael Bennett (RB) in the adjacent file, not the DT.
        seasons = self._FakeSeasons({
            2008: [("Michael Bennett", 74, "HB")]})
        self.assertIsNone(
            byr.adjacent_season_ratings("Michael", "Bennett", "DT", 2009,
                                        seasons))


class FindRatings(unittest.TestCase):
    """The cascade: same-year always beats an adjacent season."""

    def _index(self, *names):
        idx = {byr.normalise(n): {"Full Name": n, "Overall Rating": 90,
                                  "Position": "QB"} for n in names}
        return idx, byr.surname_index(idx)

    def test_a_same_year_exact_match_is_tagged_same(self):
        idx, by_surname = self._index("Aaron Rodgers")
        seasons = AdjacentSeason._FakeSeasons({})
        rec, source = byr.find_ratings("Aaron", "Rodgers", "QB", idx,
                                       by_surname, 2013, 2013, seasons)
        self.assertIsNotNone(rec)
        self.assertEqual(source, "same")

    def test_a_nickname_still_counts_as_same_year(self):
        idx, by_surname = self._index("Mike Vick")
        seasons = AdjacentSeason._FakeSeasons({})
        _rec, source = byr.find_ratings("Michael", "Vick", "QB", idx,
                                        by_surname, 2013, 2013, seasons)
        self.assertEqual(source, "same")

    def test_the_adjacent_fallback_only_runs_when_same_year_misses(self):
        idx, by_surname = self._index("Somebody Else")   # target has not him
        seasons = AdjacentSeason._FakeSeasons({
            2014: [("Nick Foles", 85, "QB")]})
        rec, source = byr.find_ratings("Nick", "Foles", "QB", idx, by_surname,
                                       2013, 2013, seasons)
        self.assertIsNotNone(rec)
        self.assertEqual(source, "adjacent:2014")

    def test_the_target_season_is_never_re_searched_as_adjacent(self):
        # exclude={season} keeps the fallback off the file the baseline missed.
        idx, by_surname = self._index("Present Player")
        seasons = AdjacentSeason._FakeSeasons({})
        _rec, source = byr.find_ratings("Absent", "Player", "QB", idx,
                                        by_surname, 2013, 2013, seasons)
        self.assertIsNone(source)


class BitWriting(unittest.TestCase):
    """`_set` and `_set_text` write the bytes the console reads back."""

    def setUp(self):
        self.fields = _play_fields()
        self.table = madden_tdb.Database(make_league(), 0).table("PLAY")

    def test_values_round_trip_through_the_reader(self):
        record = bytearray(_PLAY_RECORD_BYTES)
        for column, value in (("TGID", 32), ("PGID", 1743), ("PPOS", 20),
                              ("PJEN", 99), ("PHGT", 76), ("PWGT", 95),
                              ("POVR", 99), ("PSPD", 0)):
            byr._set(record, self.table, column, value)
        for column, value in (("TGID", 32), ("PGID", 1743), ("PPOS", 20),
                              ("PJEN", 99), ("PHGT", 76), ("PWGT", 95),
                              ("POVR", 99), ("PSPD", 0)):
            self.assertEqual(self.table.value(bytes(record), column), value,
                             column)

    def test_a_value_wider_than_the_field_is_clamped_not_wrapped(self):
        # Wrapping would put a 300-pound lineman at 44 rather than at the cap.
        record = bytearray(_PLAY_RECORD_BYTES)
        byr._set(record, self.table, "PWGT", 5000)
        self.assertEqual(self.table.value(bytes(record), "PWGT"), 255)

    def test_writing_a_column_the_table_lacks_is_a_no_op(self):
        record = bytearray(_PLAY_RECORD_BYTES)
        byr._set(record, self.table, "NOPE", 5)          # must not raise
        self.assertEqual(bytes(record), bytes(_PLAY_RECORD_BYTES))

    def test_text_is_nul_terminated(self):
        record = bytearray(_PLAY_RECORD_BYTES)
        byr._set_text(record, self.table, "PFNA", "Justin")
        field = self.table.fields["PFNA"]
        start, width = field.offset_bits // 8, field.bits // 8
        self.assertEqual(record[start:start + width].split(b"\x00")[0],
                         b"Justin")

    def test_text_longer_than_the_field_is_truncated_with_room_for_the_nul(self):
        record = bytearray(_PLAY_RECORD_BYTES)
        field = self.table.fields["PFNA"]
        width = field.bits // 8
        byr._set_text(record, self.table, "PFNA", "X" * (width + 40))
        start = field.offset_bits // 8
        stored = record[start:start + width]
        self.assertEqual(len(stored), width, "the field changed size")
        self.assertIn(b"\x00", stored, "no terminator survived truncation")

    def test_a_shorter_name_erases_the_previous_occupant(self):
        # Records are overwritten in place, so a leftover tail would graft the
        # old player's name onto the new one.
        record = bytearray(_PLAY_RECORD_BYTES)
        byr._set_text(record, self.table, "PLNA", "Longbottom")
        byr._set_text(record, self.table, "PLNA", "Ng")
        field = self.table.fields["PLNA"]
        start, width = field.offset_bits // 8, field.bits // 8
        self.assertEqual(record[start:start + width].split(b"\x00")[0], b"Ng")


class TeamIds(unittest.TestCase):
    def test_reads_abbreviations_out_of_the_games_own_table(self):
        blob = make_league(teams=(1, 2, 3),
                           abbreviations={1: "CHI", 2: "GB", 3: "DET"})
        self.assertEqual(byr.team_ids_from_game(blob),
                         {"CHI": 1, "GB": 2, "DET": 3})

    def test_the_games_table_wins_over_the_files_own_id(self):
        """The bug that put Derrick Henry on the Vikings.

        The scraped rosters carry their own tgId, and it agrees with the game
        for twenty-nine teams. Trusting it moves three entire rosters onto the
        wrong clubs while everything else looks right.
        """
        blob = make_league(teams=(30, 31, 32),
                           abbreviations={30: "TEN", 31: "MIN", 32: "HOU"})
        mapping = byr.team_ids_from_game(blob)
        self.assertEqual(mapping["TEN"], 30)
        self.assertEqual(mapping["MIN"], 31)
        self.assertEqual(mapping["HOU"], 32)

    def test_ids_outside_the_league_are_ignored(self):
        blob = make_league(teams=(1, 1009), abbreviations={1: "CHI",
                                                           1009: "FA"})
        self.assertEqual(byr.team_ids_from_game(blob), {"CHI": 1})

    def test_moved_franchises_reach_a_2003_era_table(self):
        """Madden NFL 2004 knows them by where they played then."""
        era2003 = {"OAK": 1, "SD": 2, "STL": 3, "WAS": 4, "JAX": 5}
        for modern, expected in (("LV", "OAK"), ("LAC", "SD"), ("LAR", "STL"),
                                 ("WSH", "WAS"), ("JAC", "JAX")):
            self.assertEqual(byr.resolve_team(modern, era2003),
                             era2003[expected], modern)

    def test_the_same_names_reach_a_modern_table(self):
        """A Madden 09 Deluxe template calls them LVR, LAC and LAR.

        Rewriting LAC to SD unconditionally was right for one era and a hard
        refusal in the other, because that game has no team called SD. The
        scraped name has to be tried first.
        """
        modern = {"LVR": 1, "LAC": 2, "LAR": 3, "WAS": 4, "JAX": 5}
        for scraped, expected in (("LV", "LVR"), ("LAC", "LAC"),
                                  ("LAR", "LAR"), ("WSH", "WAS")):
            self.assertEqual(byr.resolve_team(scraped, modern),
                             modern[expected], scraped)

    def test_a_direct_match_always_wins_over_an_alias(self):
        # Both names present: the game's own spelling must not be overridden.
        both = {"LAC": 1, "SD": 2}
        self.assertEqual(byr.resolve_team("LAC", both), 1)

    def test_an_unknown_team_resolves_to_nothing(self):
        self.assertIsNone(byr.resolve_team("XXX", {"CHI": 1}))

    def test_aliases_are_symmetric_for_the_moved_franchises(self):
        # Whichever spelling the data uses, the other era must be reachable.
        for a, b in (("LV", "OAK"), ("LAC", "SD"), ("LAR", "STL")):
            self.assertIn(b, byr.ABBREVIATION_ALIASES[a], "%s -> %s" % (a, b))
            self.assertIn(a, byr.ABBREVIATION_ALIASES[b], "%s -> %s" % (b, a))


class Rewrite(unittest.TestCase):
    """The invariants the console enforces, and one it cannot."""

    def setUp(self):
        self.blob = make_league(teams=(1, 2), per_team=3, pool=4)
        self.by_team = {
            1: [player("Justin", "Fields", "QB", 1, 75, 228, overall=90),
                player("DJ", "Moore", "WR", 2, 72, 210, overall=88),
                player("Cole", "Kmet", "TE", 85, 78, 260, overall=80),
                player("Extra", "Onea", "HB", 30, 70, 200, overall=60)],
            2: [player("Jordan", "Love", "QB", 10, 76, 219, overall=85),
                player("Josh", "Jacobs", "HB", 8, 70, 223, overall=84)],
        }

    def _rewrite(self):
        return byr.rewrite(self.blob, self.by_team)

    def test_the_size_never_changes(self):
        """The console allocates from its own league file's size and CRCs the
        whole buffer, so a different length cannot verify (0x00305f94)."""
        result = self._rewrite()[0]
        self.assertEqual(len(result), len(self.blob))

    def test_the_magic_the_loader_demands_survives(self):
        result = self._rewrite()[0]
        self.assertEqual(struct.unpack_from("<I", result, 0)[0], 0x08004244)

    def test_the_geometry_is_untouched(self):
        result = self._rewrite()[0]
        before = madden_tdb.Database(self.blob, 0).table("PLAY")
        after = madden_tdb.Database(result, 0).table("PLAY")
        self.assertEqual(after.record_count, before.record_count)
        self.assertEqual(after.record_bytes, before.record_bytes)
        self.assertEqual(after.field_order, before.field_order)

    def test_team_ids_are_never_rewritten(self):
        # TGID is in the checksum and decides which club a record belongs to.
        result = self._rewrite()[0]
        before = madden_tdb.Database(self.blob, 0).table("PLAY")
        after = madden_tdb.Database(result, 0).table("PLAY")
        self.assertEqual([r[0] for r in before.rows(["TGID"])],
                         [r[0] for r in after.rows(["TGID"])])

    def test_players_land_on_the_right_team_best_first(self):
        result = self._rewrite()[0]
        table = madden_tdb.Database(result, 0).table("PLAY")
        got = {}
        for index in range(table.record_count):
            record = table.record(index)
            team = table.value(record, "TGID")
            if not 1 <= team <= 32:
                continue
            field = table.fields["PLNA"]
            start, width = field.offset_bits // 8, field.bits // 8
            name = record[start:start + width].split(b"\x00")[0].decode()
            got.setdefault(team, []).append(name)
        self.assertEqual(got[1], ["Fields", "Moore", "Kmet"])
        self.assertEqual(got[2][:2], ["Love", "Jacobs"])

    def test_the_encodings_the_console_reads_back(self):
        result = self._rewrite()[0]
        table = madden_tdb.Database(result, 0).table("PLAY")
        record = table.record(0)                      # Justin Fields
        self.assertEqual(table.value(record, "PHGT"), 75, "PHGT is inches")
        self.assertEqual(table.value(record, "PWGT"), 228 - 160,
                         "PWGT is pounds minus 160")
        self.assertEqual(table.value(record, "PJEN"), 1, "PJEN is the number")
        self.assertEqual(table.value(record, "PPOS"), byr.POSITIONS.index("QB"))
        self.assertEqual(table.value(record, "POVR"), 90)

    def test_a_light_player_does_not_wrap_below_the_offset(self):
        self.by_team = {1: [player("Tiny", "Guy", "K", 3, 68, 150)]}
        result = self._rewrite()[0]
        table = madden_tdb.Database(result, 0).table("PLAY")
        self.assertEqual(table.value(table.record(0), "PWGT"), 0)

    def test_records_past_the_supplied_players_are_left_alone(self):
        # Team 2 supplies two players for three records; the third keeps its
        # retail contents rather than becoming a duplicate or a blank.
        result = self._rewrite()[0]
        before = madden_tdb.Database(self.blob, 0).table("PLAY")
        after = madden_tdb.Database(result, 0).table("PLAY")
        self.assertEqual(after.record(5), before.record(5))

    def test_the_free_agent_pool_is_filled_from_the_leftovers(self):
        _result, _written, _skipped, _published, free_written = self._rewrite()
        # Team 1 supplies a fourth player; with FREE_AGENT_FROM at 55 nobody
        # overflows, so the pool keeps its retail contents.
        self.assertEqual(free_written, 0)

    def test_overflow_players_reach_the_pool(self):
        self.by_team = {1: [player("P%d" % i, "L%d" % i, overall=90 - i)
                            for i in range(byr.FREE_AGENT_FROM + 2)]}
        blob = make_league(teams=(1,), per_team=1, pool=3)
        _result, _w, _s, _p, free_written = byr.rewrite(blob, self.by_team)
        self.assertEqual(free_written, 2, "the two leftovers should be pooled")

    def test_the_checksums_are_resealed(self):
        """A stale block checksum is error 43 on the console.

        reseal being idempotent over the output is the check: if any block were
        left with the pre-edit value, resealing again would change the bytes.
        """
        result = self._rewrite()[0]
        self.assertEqual(reseal(result), result)

    def test_the_output_differs_from_the_input(self):
        # Guards against a rewrite that silently does nothing and still passes
        # every structural assertion above.
        self.assertNotEqual(self._rewrite()[0], self.blob)

    def test_every_record_is_accounted_for_exactly_once(self):
        """`skipped` spans both kinds of untouched record.

        It counts team records with no player left to place *and* pool records
        with no leftover to place -- which is why main() reports it as "records
        unchanged" rather than as anything team-specific. The invariant worth
        pinning is that the three counts partition the table, so a record can
        never be both written and skipped, or silently neither.
        """
        _result, written, skipped, published, free_written = self._rewrite()
        table = madden_tdb.Database(self.blob, 0).table("PLAY")
        self.assertEqual(written + free_written + skipped, table.record_count)
        self.assertEqual(published, written, "all fixtures are published")

    def test_written_counts_only_records_on_real_teams(self):
        _result, written, _skipped, _published, _free = self._rewrite()
        # Three for team 1, two for team 2 -- the fourth player of team 1 has
        # no record to occupy.
        self.assertEqual(written, 5)


class BuildPlayers(unittest.TestCase):
    """Reading the scraped data, against a synthetic data repository."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        (self.repo / "data" / "canonical").mkdir(parents=True)
        (self.repo / "data" / "raw" / "madden-ratings").mkdir(parents=True)
        self._original = byr.DATA_REPO
        byr.DATA_REPO = self.repo

    def tearDown(self):
        byr.DATA_REPO = self._original

    def _write(self, year, teams, ratings, ratings_year=None):
        (self.repo / "data" / "canonical" / ("roster-%d.json" % year)).write_text(
            json.dumps({"teams": teams}))
        (self.repo / "data" / "raw" / "madden-ratings" /
         ("madden24-%d.json" % (ratings_year or year))).write_text(
            json.dumps({"teams": {"All": ratings}}))

    @staticmethod
    def _team(abbreviation, tg_id, players):
        return {"abbreviation": abbreviation, "tgId": tg_id,
                "players": players}

    @staticmethod
    def _entry(first, last, position="QB", jersey=1, age=25, years=3):
        return {"name": {"first": first, "last": last}, "position": position,
                "jerseyNumber": jersey, "age": age, "yearsPro": years,
                "measurables": {"heightIn": 74, "weightLb": 220}}

    def test_unrated_players_are_left_out_by_default(self):
        """"To be clear there are no ratings for these players? Then I don't
        want it." Refusing to guess is the default for that reason."""
        self._write(2023,
                    [self._team("CHI", 5, [self._entry("Justin", "Fields"),
                                           self._entry("Nobody", "Known")])],
                    [{"Full Name": "Justin Fields", "Overall Rating": 88}])
        by_team, matched, adjacent, estimated, season = byr.build_players(
            2023, team_ids={"CHI": 5})
        self.assertEqual([p["last"] for p in by_team[5]], ["Fields"])
        self.assertEqual((matched, estimated, season), (1, 1, 2023))

    def test_estimates_are_opt_in_and_marked(self):
        self._write(2023,
                    [self._team("CHI", 5, [self._entry("Justin", "Fields"),
                                           self._entry("Nobody", "Known")])],
                    [{"Full Name": "Justin Fields", "Overall Rating": 88}])
        by_team, _m, _adj, _e, _s = byr.build_players(
            2023, estimate_missing=True, team_ids={"CHI": 5})
        marks = {p["last"]: p["published"] for p in by_team[5]}
        self.assertEqual(marks, {"Fields": True, "Known": False})

    def test_the_game_id_is_used_not_the_files_tg_id(self):
        # The file says 32; the game says 5. The game wins.
        self._write(2023,
                    [self._team("CHI", 32, [self._entry("Justin", "Fields")])],
                    [{"Full Name": "Justin Fields", "Overall Rating": 88}])
        by_team, _m, _adj, _e, _s = byr.build_players(2023, team_ids={"CHI": 5})
        self.assertEqual(list(by_team), [5])

    def test_an_unknown_abbreviation_fails_loudly(self):
        self._write(2023,
                    [self._team("XXX", 5, [self._entry("Justin", "Fields")])],
                    [{"Full Name": "Justin Fields", "Overall Rating": 88}])
        with self.assertRaises(byr.BuildError) as caught:
            byr.build_players(2023, team_ids={"CHI": 5})
        self.assertIn("ABBREVIATION_ALIASES", str(caught.exception))

    def test_players_are_sorted_best_first(self):
        self._write(2023,
                    [self._team("CHI", 5, [self._entry("Third", "Man"),
                                           self._entry("First", "Man"),
                                           self._entry("Second", "Man")])],
                    [{"Full Name": "Third Man", "Overall Rating": 60},
                     {"Full Name": "First Man", "Overall Rating": 95},
                     {"Full Name": "Second Man", "Overall Rating": 80}])
        by_team, _m, _adj, _e, _s = byr.build_players(2023, team_ids={"CHI": 5})
        self.assertEqual([p["first"] for p in by_team[5]],
                         ["First", "Second", "Third"])

    def test_a_position_the_game_cannot_hold_is_dropped(self):
        self._write(2023,
                    [self._team("CHI", 5, [
                        self._entry("Justin", "Fields", position="QB"),
                        self._entry("Odd", "Job", position="WILDCAT")])],
                    [{"Full Name": "Justin Fields", "Overall Rating": 88},
                     {"Full Name": "Odd Job", "Overall Rating": 88}])
        by_team, _m, _adj, _e, _s = byr.build_players(2023, team_ids={"CHI": 5})
        self.assertEqual([p["last"] for p in by_team[5]], ["Fields"])

    def test_ratings_fall_back_to_the_newest_season_at_or_before(self):
        self._write(2025,
                    [self._team("CHI", 5, [self._entry("Justin", "Fields")])],
                    [{"Full Name": "Justin Fields", "Overall Rating": 88}],
                    ratings_year=2023)
        _by_team, _m, _adj, _e, season = byr.build_players(2025, team_ids={"CHI": 5})
        self.assertEqual(season, 2023)

    def test_a_missing_roster_year_is_a_clear_error(self):
        self._write(2023, [], [])
        with self.assertRaises(byr.BuildError) as caught:
            byr.build_players(1999, team_ids={})
        self.assertIn("no roster for 1999", str(caught.exception))


class Cli(unittest.TestCase):
    """One command, from a retail disc file to something servable."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        (self.repo / "data" / "canonical").mkdir(parents=True)
        (self.repo / "data" / "raw" / "madden-ratings").mkdir(parents=True)
        self._original = byr.DATA_REPO

        league = make_league(teams=(1, 2), per_team=3, pool=2,
                             abbreviations={1: "CHI", 2: "GB"})
        self.league = league
        # A TERF archive whose member 0 is the league database, so --template
        # can be pointed at a disc file directly.
        dir_size = 8 + 8
        header = bytearray(0x40)
        header[0:4] = b"TERF"
        struct.pack_into("<I", header, 0x04, 0x40)
        struct.pack_into("<H", header, 0x0E, 1)
        directory = bytearray(dir_size)
        directory[0:4] = b"DIR1"
        struct.pack_into("<I", directory, 4, dir_size)
        struct.pack_into("<II", directory, 8, 0, len(league))
        self.template = Path(tempfile.mkstemp(suffix=".DAT")[1])
        self.template.write_bytes(bytes(header) + bytes(directory) + league)
        self.addCleanup(os.unlink, self.template)

        self.raw = Path(tempfile.mkstemp(suffix=".dat")[1])
        self.raw.write_bytes(league)
        self.addCleanup(os.unlink, self.raw)

        (self.repo / "data" / "canonical" / "roster-2023.json").write_text(
            json.dumps({"teams": [
                {"abbreviation": "CHI", "tgId": 5, "players": [
                    {"name": {"first": "Justin", "last": "Fields"},
                     "position": "QB", "jerseyNumber": 1, "age": 25,
                     "yearsPro": 3,
                     "measurables": {"heightIn": 75, "weightLb": 228}}]},
                {"abbreviation": "GB", "tgId": 6, "players": [
                    {"name": {"first": "Jordan", "last": "Love"},
                     "position": "QB", "jerseyNumber": 10, "age": 26,
                     "yearsPro": 4,
                     "measurables": {"heightIn": 76, "weightLb": 219}}]}]}))
        (self.repo / "data" / "raw" / "madden-ratings" /
         "madden24-2023.json").write_text(json.dumps({"teams": {"All": [
             {"Full Name": "Justin Fields", "Overall Rating": 88},
             {"Full Name": "Jordan Love", "Overall Rating": 85}]}}))
        self.output = Path(tempfile.mkstemp(suffix=".dat")[1])
        os.unlink(self.output)

    def tearDown(self):
        byr.DATA_REPO = self._original
        if self.output.exists():
            os.unlink(self.output)

    def _run(self, *extra):
        argv = ["-o", str(self.output), "--year", "2023",
                "--data-repo", str(self.repo)] + list(extra)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), \
                contextlib.redirect_stderr(buffer):
            code = byr.main(argv)
        return code, buffer.getvalue()

    def test_a_template_is_unpacked_and_written(self):
        code, output = self._run("--template", str(self.template))
        self.assertEqual(code, 0, output)
        self.assertEqual(self.output.stat().st_size, len(self.league))
        self.assertIn("roster CSUM", output)

    def test_a_raw_league_database_works_too(self):
        code, output = self._run(str(self.raw))
        self.assertEqual(code, 0, output)
        self.assertEqual(self.output.stat().st_size, len(self.league))

    def test_the_players_reach_the_file(self):
        self._run(str(self.raw))
        table = madden_tdb.Database(self.output.read_bytes(), 0).table("PLAY")
        field = table.fields["PLNA"]
        start, width = field.offset_bits // 8, field.bits // 8
        names = {table.record(i)[start:start + width].split(b"\x00")[0].decode()
                 for i in range(table.record_count)}
        self.assertIn("Fields", names)
        self.assertIn("Love", names)

    def test_neither_source_nor_template_is_refused(self):
        code, output = self._run()
        self.assertEqual(code, 2)
        self.assertIn("either a raw LEAG database or --template", output)

    def test_a_container_passed_as_a_raw_database_is_refused(self):
        # The console requires 0x08004244; a TERF header is not that.
        code, output = self._run(str(self.template))
        self.assertEqual(code, 2)
        self.assertIn("not a raw TDB", output)
        self.assertFalse(self.output.exists())

    def test_an_unreadable_source_is_refused(self):
        code, _output = self._run("/nonexistent/LEAG.dat")
        self.assertEqual(code, 2)

    def test_a_year_with_no_scraped_roster_is_refused(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), \
                contextlib.redirect_stderr(buffer):
            code = byr.main([str(self.raw), "-o", str(self.output),
                             "--year", "1999", "--data-repo", str(self.repo)])
        self.assertEqual(code, 2)
        self.assertIn("no roster for 1999", buffer.getvalue())

    def test_the_reported_ratings_are_all_published(self):
        _code, output = self._run(str(self.raw))
        self.assertIn("all 2 players carry the ratings EA published", output)


if __name__ == "__main__":
    unittest.main()

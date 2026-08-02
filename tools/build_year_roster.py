"""Write a current NFL roster into Madden NFL 2004's league database.

This is what the whole exercise was for. The game asks its server for a roster
update, and now it can be handed one containing players who were not born when
it shipped.

HOW IT WORKS

The output must be a byte-for-byte structural twin of the disc's own league
database: exactly 253,044 bytes, the same tables, the same record count, the
same geometry. Only field *values* change. So rather than build a database, this
walks the retail one record at a time and overwrites the players in place:

* a record on a real team (``TGID`` 1-32) is replaced by the next-best current
  player for that team;
* a record in the free-agent pool (``TGID`` 1009) is filled from the players who
  did not make a 53-man roster -- the leftovers, best first.

Only the first group is in the roster checksum: the query behind it reads
``where TGID >= 1 and TGID <= 32``, so the free-agent pool is outside it and
filling it changes no announced value. It matters for franchise signings and for
nothing else, which is why it is done last and cannot break anything.

Nothing moves, nothing resizes, and the file the console demands is the file it
gets. See ``docs/roster-delivery.md`` for why those constraints are absolute.

WHERE THE DATA COMES FROM

``NCAA-Draft-Class-Editor`` carries scraped rosters (``roster-<year>.json``:
names, teams, positions, jersey numbers, height, weight, age, years of service)
and Madden's own published launch ratings for the seasons it has them.

Ratings only exist for seasons Madden published, and the newest of those is
Madden 24, the 2023 season. Build a **matching** year -- 2023 against Madden 24
-- and every player carries the ratings EA shipped for him.

Build a later year and there is nothing authoritative for anyone who entered
the league since, so ``--estimate-missing`` will invent ratings from position,
age and experience. That is a plausible-looking model and nothing more, which is
why it is off by default: without it, a player with no published ratings is
simply left out. Every team has more than 54 rated players for 2023, so nothing
is lost by refusing to guess.

ENCODINGS, all confirmed against retail records rather than assumed:

* ``PHGT`` is height in inches (Urlacher 76, Kreutz 74).
* ``PWGT`` is pounds minus 160 -- an 8-bit field could not hold a lineman
  otherwise.
* ``PJEN`` is the jersey number (Kreutz 57, Robinson 98).
* ``PPOS`` is the standard Madden order, QB=0 through P=20, verified by reading
  the retail roster back: Kreutz is a C, Urlacher an MLB, Maynard a P.

ONE COMMAND, from a retail disc file to something servable::

    python3 tools/build_year_roster.py --year 2025 \
        --template extract/madden_TEMPLATE.DAT -o rosters/2025.dat

It extracts member 0 itself, so no separate step is needed. Point the server at
the result::

    ROSTER_PAYLOAD=rosters/2025.dat ./serve-madden.sh

Any year with a scraped roster works the same way; the ratings simply come from
whichever published Madden set is newest at or before it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import madden_tdb  # noqa: E402
from tools.mark_roster import reseal  # noqa: E402

#: Where the scraped data lives. Overridable, because it sits on the machine
#: that does the scraping rather than on whatever machine serves the roster --
#: the usual flow is to build here and copy the finished .dat across.
DATA_REPO = Path(os.environ.get("NFL_DATA_REPO",
                                "/mnt/c/GitHub/NCAA-Draft-Class-Editor"))

#: Standard Madden position order, confirmed against retail records.
POSITIONS = ["QB", "HB", "FB", "WR", "TE", "LT", "LG", "C", "RG", "RT",
             "LE", "RE", "DT", "LOLB", "MLB", "ROLB", "CB", "FS", "SS", "K", "P"]

#: Modern position names that did not exist, or are spelled differently, in a
#: 2003 roster. Mapping them wrong puts a safety at defensive end.
POSITION_ALIASES = {
    "RB": "HB", "OL": "LG", "G": "LG", "OG": "LG", "OT": "LT", "T": "LT",
    "DL": "DT", "NT": "DT", "DE": "LE", "EDGE": "LE",
    "LB": "MLB", "OLB": "LOLB", "ILB": "MLB",
    "DB": "CB", "S": "FS", "SAF": "FS", "FB/TE": "FB",
    "PK": "K", "LS": "C", "KR": "WR", "PR": "WR",
}

#: Madden's published rating names -> the TDB columns that hold them.
#:
#: These are the *canonical* spellings, which is what Madden 24's file uses.
#: Every other year spells them differently, so nothing is matched literally --
#: see :func:`_rating_key`.
RATING_COLUMNS = {
    "Overall Rating": "POVR", "Speed": "PSPD", "Acceleration": "PACC",
    "Strength": "PSTR", "Agility": "PAGI", "Awareness": "PAWR",
    "Catching": "PCTH", "Carrying": "PCAR", "Throw Power": "PTHP",
    "Throw Accuracy": "PTHA", "Kick Power": "PKPR", "Kick Accuracy": "PKAC",
    "Run Block": "PRBK", "Pass Block": "PPBK", "Tackle": "PTAK",
    "Break Tackle": "PBTK", "Jumping": "PJMP", "Kick Return": "PKRT",
    "Injury": "PINJ", "Stamina": "PSTA", "Toughness": "PTGH",
    "Impact Blocking": "PIMP",
}

#: Accepted spellings per column, already normalised by :func:`_rating_key`.
#:
#: **Sixteen ratings files, six spellings of "overall" between them**:
#: ``OVERALL``, ``OVERALL RATING``, ``Overall``, ``Overall Rating``,
#: ``OverallRating`` -- and two years with no overall column at all. Matching
#: only the newest spelling is not a small miss: `real_ratings` finds nothing,
#: every value falls back to the COMMON defaults, and the build cheerfully
#: reports "all N players carry the ratings EA published" because that message
#: counts players matched *by name*, not values actually read.
#:
#: The symptom was a 2018 roster where the highest-rated player in the league
#: was 60 -- the default -- and 100% of players sat on it. Measured across the
#: set, only five of eighteen years had any rating spread at all.
#:
#: Where a family of related columns exists (``THROW ACCURACY`` alongside
#: ``THROW ACCURACY DEEP/MID/SHORT``, ``RUN BLOCK`` alongside ``RUNBLOCK
#: FOOTWORK``), only the plain one is listed: the variants are separate
#: attributes the 2004-era TDB has no column for, and matching them by prefix
#: would write a footwork score into the run-block field.
RATING_ALIASES = {
    "POVR": ("overall", "ovr"), "PSPD": ("speed",),
    "PACC": ("acceleration",),
    "PSTR": ("strength",), "PAGI": ("agility",), "PAWR": ("awareness",),
    "PCTH": ("catching", "catch"), "PCAR": ("carrying",),
    "PTHP": ("throwpower",), "PTHA": ("throwaccuracy",),
    "PKPR": ("kickpower",), "PKAC": ("kickaccuracy",),
    "PRBK": ("runblock",), "PPBK": ("passblock",), "PTAK": ("tackle",),
    "PBTK": ("breaktackle",), "PJMP": ("jumping",), "PKRT": ("kickreturn",),
    "PINJ": ("injury",), "PSTA": ("stamina",), "PTGH": ("toughness",),
    "PIMP": ("impactblocking", "impactblock"),
}

#: Columns rebuilt from the several ratings that replaced them.
#:
#: Madden split throw accuracy into deep/mid/short around 2015, and the
#: 2004-era TDB has one column. Every input here is still a published EA
#: rating for that player -- this reconstitutes the single number they were
#: split from, it does not invent one. Used only when the plain rating is
#: absent, so a file that still carries it wins.
#:
#: Without this a 2015 quarterback keeps the COMMON default of 20, which is the
#: value meant for players who never throw.
DERIVED_RATINGS = {
    "PTHA": ("throwaccuracydeep", "throwaccuracymid", "throwaccuracymed",
             "throwaccuracyshort"),
}

#: normalised spelling -> TDB column.
_RATING_BY_KEY = {alias: column
                  for column, aliases in RATING_ALIASES.items()
                  for alias in aliases}


def _rating_key(name: str) -> str:
    """Fold one file's spelling of a rating onto a canonical key.

    Case, spaces and underscores go, and a trailing ``rating`` is dropped so
    ``OverallRating``, ``OVERALL RATING`` and ``Overall`` all land together.
    """
    folded = name.lower().replace("_", " ").replace(" ", "")
    if folded.endswith("rating") and folded != "rating":
        folded = folded[:-len("rating")]
    return folded

#: Rough positional baselines for players with no published ratings. Deliberately
#: unexciting: the point is a roster that plays sensibly, not invented stars.
BASELINE = {
    "QB":   {"PSPD": 62, "PACC": 66, "PSTR": 55, "PAGI": 66, "PTHP": 82, "PTHA": 74},
    "HB":   {"PSPD": 88, "PACC": 89, "PSTR": 62, "PAGI": 86, "PCAR": 82, "PBTK": 70},
    "FB":   {"PSPD": 74, "PACC": 76, "PSTR": 78, "PAGI": 68, "PRBK": 66, "PIMP": 74},
    "WR":   {"PSPD": 90, "PACC": 90, "PSTR": 55, "PAGI": 88, "PCTH": 78, "PJMP": 82},
    "TE":   {"PSPD": 76, "PACC": 78, "PSTR": 70, "PAGI": 72, "PCTH": 72, "PRBK": 60},
    "LT":   {"PSPD": 55, "PACC": 60, "PSTR": 88, "PAGI": 54, "PPBK": 78, "PRBK": 76},
    "LG":   {"PSPD": 54, "PACC": 59, "PSTR": 89, "PAGI": 52, "PPBK": 74, "PRBK": 78},
    "C":    {"PSPD": 55, "PACC": 60, "PSTR": 86, "PAGI": 54, "PPBK": 75, "PRBK": 76},
    "RG":   {"PSPD": 54, "PACC": 59, "PSTR": 89, "PAGI": 52, "PPBK": 74, "PRBK": 78},
    "RT":   {"PSPD": 55, "PACC": 60, "PSTR": 88, "PAGI": 54, "PPBK": 77, "PRBK": 76},
    "LE":   {"PSPD": 74, "PACC": 78, "PSTR": 82, "PAGI": 70, "PTAK": 72},
    "RE":   {"PSPD": 76, "PACC": 80, "PSTR": 80, "PAGI": 72, "PTAK": 72},
    "DT":   {"PSPD": 62, "PACC": 68, "PSTR": 90, "PAGI": 58, "PTAK": 74},
    "LOLB": {"PSPD": 80, "PACC": 82, "PSTR": 72, "PAGI": 78, "PTAK": 78},
    "MLB":  {"PSPD": 76, "PACC": 79, "PSTR": 76, "PAGI": 74, "PTAK": 82},
    "ROLB": {"PSPD": 80, "PACC": 82, "PSTR": 72, "PAGI": 78, "PTAK": 78},
    "CB":   {"PSPD": 91, "PACC": 91, "PSTR": 48, "PAGI": 89, "PCTH": 50, "PJMP": 84},
    "FS":   {"PSPD": 87, "PACC": 88, "PSTR": 56, "PAGI": 84, "PTAK": 66},
    "SS":   {"PSPD": 85, "PACC": 86, "PSTR": 64, "PAGI": 80, "PTAK": 74},
    "K":    {"PSPD": 50, "PACC": 55, "PSTR": 45, "PAGI": 50, "PKPR": 84, "PKAC": 80},
    "P":    {"PSPD": 50, "PACC": 55, "PSTR": 45, "PAGI": 50, "PKPR": 86, "PKAC": 76},
}

#: Columns every player gets, so nothing inherits a stale value from whoever
#: previously occupied the record.
COMMON = {"PINJ": 80, "PSTA": 82, "PTGH": 80, "PKRT": 30, "PIMP": 55,
          "PCTH": 40, "PCAR": 35, "PTHP": 20, "PTHA": 20, "PKPR": 25,
          "PKAC": 25, "PRBK": 35, "PPBK": 35, "PTAK": 35, "PBTK": 30,
          "PJMP": 65, "PSPD": 70, "PACC": 72, "PSTR": 60, "PAGI": 70,
          "PAWR": 60, "POVR": 60}


class BuildError(Exception):
    pass


def normalise(name: str) -> str:
    """A join key that survives suffixes and punctuation."""
    name = re.sub(r"\s+(Jr\.?|Sr\.?|I{2,}|IV|V)$", "", name.strip(), flags=re.I)
    return re.sub(r"[^a-z]", "", name.lower())


def load_ratings(year: int) -> Tuple[Dict[str, dict], int]:
    """Madden's published ratings, newest season at or before *year*."""
    directory = DATA_REPO / "data" / "raw" / "madden-ratings"
    best: Optional[Path] = None
    best_season = -1
    for path in directory.glob("madden*-*.json"):
        season = int(path.stem.rsplit("-", 1)[1])
        if season <= year and season > best_season:
            best, best_season = path, season
    if best is None:
        raise BuildError("no Madden ratings at or before %d in %s" % (year, directory))
    payload = json.loads(best.read_text())
    index: Dict[str, dict] = {}
    for players in payload["teams"].values():
        for player in players:
            key = normalise(player_name(player))
            if key:
                index[key] = player
    return index, best_season


#: Every published ratings year spells the name differently -- six variants
#: across sixteen files, including some with no name column at all. Handling
#: only the newest spelling silently yields 0% coverage for older years, which
#: looks like a data problem rather than a parsing one.
_NAME_FIELDS = ("Full Name", "Name", "NAME")
_FIRST_FIELDS = ("First Name", "FIRSTNAME", "FIRST NAME", "FirstName", "First")
_LAST_FIELDS = ("Last Name", "LASTNAME", "LAST NAME", "LastName", "Last")


def player_name(record: dict) -> str:
    """The player's full name, whatever this year's file calls it."""
    for field in _NAME_FIELDS:
        if record.get(field):
            return str(record[field])
    first = next((record[f] for f in _FIRST_FIELDS if record.get(f)), "")
    last = next((record[f] for f in _LAST_FIELDS if record.get(f)), "")
    return ("%s %s" % (first, last)).strip()


#: Shortest first name that may be prefix-matched. Two letters would pair
#: "Al Woods" with "Alex Woods"; three keeps it to the Greg/Gregory family.
_MIN_PREFIX = 3


def surname_index(ratings: Dict[str, dict]) -> Dict[str, list]:
    """Group the ratings by surname, for the fallback match below."""
    out: Dict[str, list] = {}
    for key, record in ratings.items():
        parts = player_name(record).split()
        if len(parts) >= 2:
            out.setdefault(normalise(parts[-1]), []).append(
                (normalise(parts[0]), key))
    return out


def match_player(first: str, last: str, ratings: Dict[str, dict],
                 by_surname: Dict[str, list]) -> Optional[dict]:
    """Find a player's published ratings, tolerating the formal-name problem.

    The scraped rosters carry formal first names and Madden's files carry the
    common ones: Gregory Olsen against Greg Olsen, Joshua McCown against Josh
    McCown, Matthew Forte against Matt Forte. Exact matching alone leaves
    7-25% of a year's misses on the table, and every one of those is a roster
    slot that keeps whoever the template had there instead.

    So a miss falls back to the surname plus a first-name prefix in either
    direction -- and **only when exactly one candidate fits**. Two players who
    share a surname and a compatible prefix are left unmatched rather than
    guessed at: the NFL has had several Mike Williamses, and handing one man's
    ratings to another is worse than leaving a slot alone. Measured across the
    set, that ambiguity arises once in eighteen years.
    """
    exact = ratings.get(normalise(first + " " + last))
    if exact is not None:
        return exact
    first_key, last_key = normalise(first), normalise(last)
    if len(first_key) < _MIN_PREFIX:
        return None
    hits = [key for candidate, key in by_surname.get(last_key, ())
            if len(candidate) >= _MIN_PREFIX
            and (candidate.startswith(first_key)
                 or first_key.startswith(candidate))]
    if len(hits) == 1:
        return ratings[hits[0]]
    return None


def estimate_ratings(position: str, age: int, years_pro: int) -> Dict[str, int]:
    """Plausible ratings for a player with none published.

    An estimate, and labelled as one everywhere it is used. Young players are
    slightly faster and markedly less aware; that is the whole model.
    """
    ratings = dict(COMMON)
    ratings.update(BASELINE.get(position, {}))
    # Awareness is the rating experience actually buys.
    ratings["PAWR"] = max(40, min(92, 48 + years_pro * 4))
    # A little physical decline past thirty, a little youth before it.
    if age and age >= 30:
        for key in ("PSPD", "PACC", "PAGI"):
            ratings[key] = max(35, ratings.get(key, 60) - (age - 29) * 2)
    elif age and age <= 24:
        for key in ("PSPD", "PACC"):
            ratings[key] = min(99, ratings.get(key, 60) + 2)
    physical = (ratings.get("PSPD", 60) + ratings.get("PSTR", 60)) // 2
    ratings["POVR"] = max(40, min(90, (physical + ratings["PAWR"]) // 2))
    return ratings


def real_ratings(record: dict) -> Dict[str, int]:
    """Pull the published ratings out of a Madden record.

    Matched on the folded key rather than the literal one, because each year's
    file spells them differently. Anything unrecognised is left at its COMMON
    default -- which is correct for a column this file does not carry, and is
    also why :func:`rated_fraction` exists: a record that matched by name but
    contributed no values is indistinguishable from a real one unless somebody
    counts.
    """
    out = dict(COMMON)
    folded = {}
    for name, raw in record.items():
        if raw in (None, "", "N/A"):
            continue
        key = _rating_key(str(name))
        folded[key] = raw
        column = _RATING_BY_KEY.get(key)
        if column is None:
            continue
        try:
            out[column] = max(0, min(99, int(float(raw))))
        except (TypeError, ValueError):
            continue
    # Only where the plain rating was absent: a file that still carries it wins.
    for column, parts in DERIVED_RATINGS.items():
        if any(_RATING_BY_KEY.get(k) == column for k in folded):
            continue
        values = []
        for part in parts:
            try:
                values.append(float(folded[part]))
            except (KeyError, TypeError, ValueError):
                continue
        if values:
            out[column] = max(0, min(99, int(sum(values) / len(values))))
    return out


def rated_fraction(record: dict) -> float:
    """How much of this record actually carried readable ratings, 0.0 to 1.0.

    The build reports "all N players carry the ratings EA published", and that
    message counts players matched *by name*. A file whose columns are spelled
    in a way nothing recognises still matches every name and still reports
    100%, while writing the default 60 to everyone. This is the number that
    catches it.
    """
    seen = {_RATING_BY_KEY.get(_rating_key(str(k)))
            for k, v in record.items() if v not in (None, "", "N/A")}
    seen.discard(None)
    return len(seen) / float(len(RATING_ALIASES))


def team_ids_from_game(blob: bytes) -> Dict[str, int]:
    """Map a team abbreviation to the id THIS GAME uses, from its TEAM table.

    Not from the scraped data's own ``tgId``. The two agree for twenty-nine
    teams and disagree for the last three -- the game has 30 Titans, 31 Vikings,
    32 Texans, while the scraped rosters have 30 Texans, 31 Titans, 32 Vikings.
    Trusting the file would put three entire rosters on the wrong clubs, and the
    twenty-nine that matched would make it look fine.

    The game's own table is the authority, so read it.
    """
    table = madden_tdb.Database(blob, 0).table("TEAM")
    short = table.fields.get("TSNA")          # the abbreviation, e.g. "CHI"
    if short is None:
        raise BuildError("the TEAM table has no TSNA column to map teams by")
    start = short.offset_bits // 8
    width = short.bits // 8
    mapping: Dict[str, int] = {}
    for index in range(table.record_count):
        record = table.record(index)
        team_id = table.value(record, "TGID")
        if not 1 <= team_id <= 32:
            continue
        name = record[start:start + width].split(b"\x00")[0].decode("latin-1")
        if name:
            mapping[name.upper()] = team_id
    return mapping


#: Other names the same franchise goes by, tried in order when the scraped
#: abbreviation is not one the game itself uses.
#:
#: **The era of the template decides which way this points, so it cannot be a
#: one-way map.** Madden NFL 2004 knows the Raiders as OAK, the Chargers as SD
#: and the Rams as STL. A Madden 09 Deluxe template -- rebuilt by the community
#: against a modern roster -- knows them as LVR, LAC and LAR. Rewriting LAC to
#: SD unconditionally was correct for one and wrong for the other, and the
#: symptom was not a wrong roster but a hard refusal, because the game had no
#: team called SD.
#:
#: So :func:`resolve_team` tries the scraped name first and only then these,
#: which lets one table serve both without knowing which disc it is looking at.
ABBREVIATION_ALIASES = {
    "LV": ("LVR", "OAK", "RAI"), "LVR": ("LV", "OAK", "RAI"),
    "OAK": ("LVR", "LV"),
    "LAC": ("SD", "SDG"), "SD": ("LAC", "SDG"),
    "LAR": ("STL", "RAM"), "STL": ("LAR", "RAM"), "SL": ("STL", "LAR"),
    "WSH": ("WAS",), "WAS": ("WSH",),
    "ARZ": ("ARI",), "BLT": ("BAL",), "CLV": ("CLE",), "HST": ("HOU",),
    "JAC": ("JAX",), "JAX": ("JAC",),
    "GNB": ("GB",), "GB": ("GNB",), "KAN": ("KC",), "KC": ("KAN",),
    "NWE": ("NE",), "NE": ("NWE",), "NOR": ("NO",), "NO": ("NOR",),
    "SFO": ("SF",), "SF": ("SFO",), "TAM": ("TB",), "TB": ("TAM",),
}


def resolve_team(abbreviation: str, team_ids: Dict[str, int]) -> Optional[int]:
    """The game's id for a scraped abbreviation, or None if it has no such team.

    Direct match first. Only when the game does not use that name at all do the
    aliases get a turn, which is what lets the same table serve a 2003-era
    template and a modern one.
    """
    if abbreviation in team_ids:
        return team_ids[abbreviation]
    for candidate in ABBREVIATION_ALIASES.get(abbreviation, ()):
        if candidate in team_ids:
            return team_ids[candidate]
    return None


# ---------------------------------------------------------------------------
# matching beyond exact + formal prefix
# ---------------------------------------------------------------------------
#
# Exact names and formal-first-name prefixes still leave 20-40% of a year's
# players unmatched, and each miss is a roster slot that keeps a wrong player.
# Two further techniques close most of the gap. Both were measured against the
# real data before being trusted: together they cut unfilled team slots across
# 2008-2023 from ~1300 to ~80.

#: Coarse position buckets, used only to reject a same-surname match whose
#: position is plainly incompatible -- the defence against the name-keyed index
#: silently collapsing two players who share a name (a DT recovering the record
#: of a running back with the same name). C is exempt: long-snappers are
#: labelled C in the roster and TE/LB in the ratings, and they are one man.
_POSITION_BUCKET = {
    "QB": "qb", "HB": "rb", "FB": "rb", "WR": "pass", "TE": "pass",
    "LT": "ol", "LG": "ol", "C": "ol", "RG": "ol", "RT": "ol",
    "LE": "front7", "RE": "front7", "DT": "front7",
    "LOLB": "front7", "MLB": "front7", "ROLB": "front7",
    "CB": "db", "FS": "db", "SS": "db", "K": "kp", "P": "kp",
}


def _position_bucket(position: str) -> Optional[str]:
    if not position:
        return None
    folded = str(position).upper().strip()
    return _POSITION_BUCKET.get(POSITION_ALIASES.get(folded, folded))


def _record_position(record: dict) -> Optional[str]:
    for key in record:
        if key.lower() in ("position", "pos"):
            return record[key]
    return None


def _same_person(roster_position: str, record: dict) -> bool:
    """False only when the position buckets clearly disagree.

    Unknown on either side, or a centre on the roster side, passes -- the guard
    exists to catch collisions, not to second-guess a real position change.
    """
    roster_bucket = _position_bucket(roster_position)
    record_bucket = _position_bucket(_record_position(record))
    if roster_bucket and record_bucket and roster_bucket != record_bucket:
        folded = str(roster_position).upper()
        return POSITION_ALIASES.get(folded, folded) == "C"
    return True


#: First-name nicknames the prefix rule cannot reach, every pair attested at
#: least twice in the roster/ratings data rather than guessed. The dominant
#: family is initials -- A.J., C.J., T.J. -- which no prefix of a formal name
#: produces. Textbook pairs that never actually occur in eighteen years of data
#: (Robert->Bob, William->Bill) are deliberately absent.
NICKNAMES = {
    "aaron": ("aj",), "adam": ("aj",), "alexander": ("zander",),
    "andrew": ("andy", "aj", "drew"), "anthony": ("tony", "aj"),
    "benjamin": ("bj",), "bryant": ("bj",),
    "calvin": ("cj",), "casey": ("cj",), "charles": ("charlie",),
    "chauncey": ("cj",), "christopher": ("cj",), "claude": ("cj",),
    "clifford": ("cj",), "clinton": ("cj",), "coleridge": ("cj",),
    "colton": ("cj",), "cortez": ("cj",),
    "daniel": ("danny", "dj"), "danny": ("dj",), "david": ("dave", "dj"),
    "demarcus": ("dj", "dee"), "dennis": ("dj",), "derek": ("dj",),
    "edward": ("ed", "eddie"), "emmanuel": ("manny",), "erik": ("ej",),
    "freddie": ("freddy",), "gene": ("geno",), "henry": ("hank",),
    "isaac": ("ike",), "jacob": ("jake",), "jacoby": ("coby",),
    "james": ("jim", "jimmy", "jj"), "jameson": ("jamey",), "jason": ("jay",),
    "jeffrey": ("jj",), "john": ("jack", "jt", "jp"),
    "johnathan": ("johnny",), "jonathan": ("jp",),
    "joseph": ("joe", "joey", "jc"), "justin": ("jj", "jr"),
    "kenneth": ("kenny",), "lawrence": ("larry", "lj"), "lucas": ("luke",),
    "manuel": ("manny",), "michael": ("mike",),
    "nathan": ("nate",), "nathaniel": ("nate",), "nicholas": ("nick",),
    "phillip": ("philip",), "reginald": ("reggie",),
    "richard": ("richie", "rj"), "robert": ("bobby",), "roderick": ("ricky",),
    "rudolph": ("rudy",), "stephen": ("steve",),
    "taylor": ("tj",), "terrell": ("tj",), "theodore": ("teddy", "ted"),
    "thomas": ("tom", "tommy", "tj"), "timothy": ("tj",), "travis": ("tj",),
    "trent": ("tj",), "tyler": ("ty",), "vincent": ("vinny",),
    "william": ("billy",),
}

_NICKNAME_PAIRS = frozenset(
    pair for full, nicks in NICKNAMES.items() for nick in nicks
    for pair in ((full, nick), (nick, full)))


def nickname_match(first: str, last: str, position: str,
                   ratings: Dict[str, dict],
                   by_surname: Dict[str, list]) -> Optional[dict]:
    """Ratings for a player whose first name is a nickname, or None.

    Same discipline as the prefix rule: accept only when exactly one
    same-surname candidate fits, and reject a clear position mismatch (Dimitri
    Patterson the corner is not Mike Patterson the tackle). The initials family
    is the reason this is not a prefix check -- ``tj`` is a prefix of nothing.
    """
    first_key, last_key = normalise(first), normalise(last)
    hits = [key for candidate, key in by_surname.get(last_key, ())
            if (first_key, candidate) in _NICKNAME_PAIRS]
    if len(hits) != 1:
        return None
    record = ratings[hits[0]]
    return record if _same_person(position, record) else None


#: A recovered rating from a neighbouring season is approximate -- typically
#: within 2-4 OVR points -- but it is a real published rating for the right
#: player, which beats the flat default a miss otherwise gets. Two seasons is
#: the cap: past that the drift roughly doubles per step.
ADJACENT_SPAN = 2


class SeasonRatings:
    """Lazily loaded ``(index, surname_index)`` per published season.

    One instance per build, shared across every lookup: the first lookup that
    needs a season pays its file load, the rest are dict hits. An eighteen-year
    sweep therefore loads each ratings file once.
    """

    def __init__(self) -> None:
        directory = DATA_REPO / "data" / "raw" / "madden-ratings"
        self.available = sorted(
            {int(path.stem.rsplit("-", 1)[1])
             for path in directory.glob("madden*-*.json")})
        self._cache: Dict[int, Tuple[Dict[str, dict], Dict[str, list]]] = {}

    def get(self, season: int
            ) -> Optional[Tuple[Dict[str, dict], Dict[str, list]]]:
        if season not in self.available:
            return None
        if season not in self._cache:
            index, _got = load_ratings(season)
            self._cache[season] = (index, surname_index(index))
        return self._cache[season]


def adjacent_season_ratings(first: str, last: str, position: str,
                            target_year: int, seasons: "SeasonRatings",
                            exclude=()) -> Optional[Tuple[dict, int]]:
    """A miss's ratings from the nearest OTHER season, or None.

    Tried outward -- Y+1, Y-1, Y+2, Y-2 -- so the closest season wins and, at
    equal distance, the season ahead is preferred: a miss is most often a
    player Madden had not rated yet rather than one it had dropped. Matches by
    the same name discipline as the same-year path (exact, prefix, nickname),
    so it never guesses an identity; the cost is staleness, not a wrong person.
    """
    for distance in range(1, ADJACENT_SPAN + 1):
        for season in (target_year + distance, target_year - distance):
            if season in exclude:
                continue
            loaded = seasons.get(season)
            if loaded is None:
                continue
            index, by_surname = loaded
            record = match_player(first, last, index, by_surname)
            if record is None:
                record = nickname_match(first, last, position, index,
                                        by_surname)
            if record is not None and _same_person(position, record):
                return record, season
    return None


def find_ratings(first: str, last: str, position: str,
                 ratings: Dict[str, dict], by_surname: Dict[str, list],
                 year: int, season: int, seasons: "SeasonRatings"
                 ) -> Tuple[Optional[dict], Optional[str]]:
    """Resolve a player's ratings, returning ``(record, source)``.

    ``source`` is ``"same"`` for a target-season match or ``"adjacent:<n>"``
    for a neighbouring-season fallback. Same-year always wins -- exact, then
    formal prefix, then nickname -- and the adjacent search runs only when all
    of those miss, because a same-year rating is what EA said that player was
    *that* season.
    """
    record = match_player(first, last, ratings, by_surname)
    if record is None:
        record = nickname_match(first, last, position, ratings, by_surname)
    if record is not None:
        return record, "same"
    hit = adjacent_season_ratings(first, last, position, year, seasons,
                                  exclude={season})
    if hit is not None:
        return hit[0], "adjacent:%d" % hit[1]
    return None, None


def build_players(year: int, estimate_missing: bool = False,
                  team_ids: Optional[Dict[str, int]] = None
                  ) -> Tuple[Dict[int, List[dict]], int, int, int, int]:
    """Every current player, per team, best first.

    Returns ``(by_team, same_year, adjacent, estimated, season)``: how many
    players were rated from the target season, how many from a neighbouring
    season, and how many were left out (or estimated, with --estimate-missing).
    """
    roster_path = DATA_REPO / "data" / "canonical" / ("roster-%d.json" % year)
    if not roster_path.exists():
        raise BuildError("no roster for %d at %s" % (year, roster_path))
    roster = json.loads(roster_path.read_text())
    ratings_index, ratings_season = load_ratings(year)
    by_surname = surname_index(ratings_index)
    seasons = SeasonRatings()

    by_team: Dict[int, List[dict]] = {}
    matched = adjacent = estimated = 0
    for team in roster["teams"]:
        # The game's id for this abbreviation, never the file's own tgId.
        abbreviation = (team.get("abbreviation") or "").upper()
        if team_ids:
            team_id = resolve_team(abbreviation, team_ids)
            if team_id is None:
                raise BuildError(
                    "the game has no team %r, and none of its aliases %s are "
                    "in the TEAM table (which has: %s). Add it to "
                    "ABBREVIATION_ALIASES."
                    % (abbreviation,
                       list(ABBREVIATION_ALIASES.get(abbreviation, ())),
                       " ".join(sorted(team_ids))))
        else:
            team_id = team["tgId"]
        if not 1 <= team_id <= 32:
            continue
        players = []
        for entry in team["players"]:
            first = entry["name"]["first"]
            last = entry["name"]["last"]
            position = entry.get("position", "").upper()
            position = POSITION_ALIASES.get(position, position)
            if position not in POSITIONS:
                continue                      # a position the game cannot hold
            published, source = find_ratings(
                first, last, entry.get("position", ""), ratings_index,
                by_surname, year, ratings_season, seasons)
            if published is not None:
                ratings = real_ratings(published)
                published_ratings = True
                if source == "same":
                    matched += 1
                else:
                    adjacent += 1
            elif estimate_missing:
                ratings = estimate_ratings(position, entry.get("age") or 0,
                                           entry.get("yearsPro") or 0)
                published_ratings = False
                source = "estimate"
                estimated += 1
            else:
                # No published ratings and no licence to invent any. Leaving
                # him out is honest; a made-up rating is not.
                estimated += 1
                continue
            measurables = entry.get("measurables") or {}
            players.append({
                "first": first, "last": last, "position": position,
                "jersey": entry.get("jerseyNumber") or 0,
                "height": measurables.get("heightIn") or 73,
                "weight": measurables.get("weightLb") or 200,
                "ratings": ratings,
                "published": published_ratings,
                "source": source,
            })
        players.sort(key=lambda p: -p["ratings"]["POVR"])
        by_team[team_id] = players
    return by_team, matched, adjacent, estimated, ratings_season


def _set(record: bytearray, table, column: str, value: int) -> None:
    """Write one bit-packed field, LSB-first within byte and within field."""
    field = table.fields.get(column)
    if field is None:
        return
    value = max(0, min(value, (1 << field.bits) - 1))
    for i in range(field.bits):
        position = field.offset_bits + i
        index = position >> 3
        if index >= len(record):
            break
        mask = 1 << (position & 7)
        if (value >> i) & 1:
            record[index] |= mask
        else:
            record[index] &= 0xFF ^ mask


#: Players past this many on a team's depth chart go to the free-agent pool
#: instead. Retail carries 53-55 per team, and the records are already
#: allocated, so this only decides who is a leftover.
FREE_AGENT_FROM = 55


def _write_player(record: bytearray, table, player: dict) -> None:
    """Overwrite one record with a player. Field values only; nothing moves."""
    _set_text(record, table, "PFNA", player["first"])
    _set_text(record, table, "PLNA", player["last"])
    _set(record, table, "PPOS", POSITIONS.index(player["position"]))
    _set(record, table, "PJEN", player["jersey"])
    _set(record, table, "PHGT", player["height"])
    _set(record, table, "PWGT", max(0, player["weight"] - 160))
    for column, value in player["ratings"].items():
        _set(record, table, column, value)


def _set_text(record: bytearray, table, column: str, text: str) -> None:
    field = table.fields.get(column)
    if field is None:
        return
    width = field.bits // 8
    start = field.offset_bits // 8
    encoded = text.encode("latin-1", "replace")[:width - 1]
    record[start:start + width] = encoded + b"\x00" * (width - len(encoded))


def rewrite(blob: bytes, by_team: Dict[int, List[dict]]
            ) -> Tuple[bytes, int, int, int, int]:
    """Replace every team player in place, leaving the rest untouched.

    Returns ``(sealed, written, skipped, written_published, free_written)``.
    ``skipped`` spans both kinds of untouched record -- a team record with no
    player left to place, and a pool record with no leftover -- which is why
    ``main`` reports it as "records unchanged". The three counts partition the
    table.
    """
    database = madden_tdb.Database(blob, 0)
    table = database.table("PLAY")
    out = bytearray(blob)
    cursor = {team: 0 for team in by_team}
    # Everyone who did not make a 53-man roster, best first, for the free-agent
    # pool. Ordering by rating means the pool is the next men up rather than an
    # arbitrary tail.
    leftovers: List[dict] = []
    for team_id, queue in by_team.items():
        leftovers.extend(queue[FREE_AGENT_FROM:])
    leftovers.sort(key=lambda p: -p["ratings"]["POVR"])
    free_cursor = 0

    written = skipped = free_written = 0
    # Counted over the players actually WRITTEN, not over every candidate.
    # The written set is the top of each roster, so it carries published
    # ratings far more often than the pool as a whole -- reporting the pool
    # figure would understate the roster that ships.
    written_published = 0

    for index in range(table.record_count):
        record = bytearray(table.record(index))
        team_id = table.value(bytes(record), "TGID")
        if not 1 <= team_id <= 32:
            # The free-agent pool. Outside the checksum, so this is cosmetic
            # for online play and useful only in franchise mode.
            if free_cursor < len(leftovers):
                player = leftovers[free_cursor]
                free_cursor += 1
                _write_player(record, table, player)
                start = table._records + index * table.record_bytes
                out[start:start + table.record_bytes] = record
                free_written += 1
            else:
                skipped += 1
            continue
        queue = by_team.get(team_id) or []
        position = cursor.get(team_id, 0)
        if position >= len(queue):
            skipped += 1                       # ran out of current players
            continue
        player = queue[position]
        cursor[team_id] = position + 1

        _write_player(record, table, player)

        start = table._records + index * table.record_bytes
        out[start:start + table.record_bytes] = record
        written += 1
        if player["published"]:
            written_published += 1

    return reseal(bytes(out)), written, skipped, written_published, free_written


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write a current NFL roster into Madden NFL 2004's league "
                    "database, in place and at the same size.")
    parser.add_argument("source", nargs="?",
                        help="a raw LEAG database (template.dat member 0). "
                             "Omit it and use --template instead.")
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--year", type=int, default=2023,
                        help="the season to build. 2023 is the newest year "
                             "with published Madden ratings for everyone.")
    parser.add_argument("--estimate-missing", action="store_true",
                        help="invent ratings for players Madden never rated, "
                             "instead of leaving them out. Off by default.")
    parser.add_argument("--data-repo",
                        help="where the scraped rosters and Madden ratings "
                             "live (default $NFL_DATA_REPO, or the checkout "
                             "beside this one)")
    parser.add_argument("--template",
                        help="template.dat itself; member 0 is extracted from "
                             "it, so a retail disc file can be used directly")
    args = parser.parse_args(argv)

    if args.data_repo:
        global DATA_REPO
        DATA_REPO = Path(args.data_repo)

    if not args.source and not args.template:
        print("error: give either a raw LEAG database or --template",
              file=sys.stderr)
        return 2

    try:
        if args.template:
            archive = madden_tdb.Container(Path(args.template).read_bytes())
            offset, size = archive._members[0]
            blob = archive._data[archive._member_base + offset:][:size]
        else:
            blob = Path(args.source).read_bytes()
    except (OSError, madden_tdb.TdbError, IndexError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    if blob[:2] != b"DB":
        print("error: %s is not a raw TDB (magic %r); the console requires "
              "0x08004244" % (args.source, blob[:2]), file=sys.stderr)
        return 2

    try:
        team_ids = team_ids_from_game(blob)
        by_team, same_year, adjacent_year, unrated, season = build_players(
            args.year, estimate_missing=args.estimate_missing,
            team_ids=team_ids)
        result, written, skipped, published, free_written = rewrite(blob, by_team)
    except (BuildError, madden_tdb.TdbError, KeyError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if len(result) != len(blob):
        print("error: size changed %d -> %d; the console demands the exact "
              "on-disc size" % (len(blob), len(result)), file=sys.stderr)
        return 2

    Path(args.output).write_bytes(result)
    import zlib
    print("%s: %d bytes, crc32 %d" % (args.output, len(result),
                                      zlib.crc32(result) & 0xFFFFFFFF))
    print("  %d players on teams, %d free agents, %d records unchanged"
          % (written, free_written, skipped))
    if adjacent_year:
        # Some players were rated from a neighbouring season. Say so rather than
        # claim they carry this season's numbers, which they do not.
        print("  ratings: %d rated -- %d from the %d season, %d from adjacent "
              "seasons (within +-%d); %d unrated%s"
              % (same_year + adjacent_year, same_year, season, adjacent_year,
                 ADJACENT_SPAN, unrated,
                 " (estimated)" if args.estimate_missing else " (left out)"))
    elif written == published:
        print("  ratings: all %d players carry the ratings EA published for "
              "the %d season" % (published, season))
    else:
        print("  ratings: %d published (Madden %d), %d estimated -- %.0f%% real"
              % (published, season, written - published,
                 100.0 * published / max(1, written)))

    # The console recomputes this over the installed roster, so the server has
    # to announce the NEW value or every console will be told its fresh roster
    # is already stale.
    from tools import roster_checksum
    table = madden_tdb.Database(result, 0).table("PLAY")
    team_index = roster_checksum.FIELDS.index("TGID")
    rows = sorted((r for r in table.rows(roster_checksum.FIELDS)
                   if 1 <= r[team_index] <= 32), key=lambda r: r[0])
    print("  roster CSUM: %d -- the server derives this itself from "
          "--roster-db, but it must match this file"
          % roster_checksum.checksum(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())

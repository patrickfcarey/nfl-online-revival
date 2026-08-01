"""Computing the roster checksum Madden NFL 2004 compares against ``CSUM``.

The title asks the server for a roster ``DATE`` and ``CSUM`` in the ``news``
reply, computes the same checksum over its own roster, and offers to download an
update when the two differ. Sending a value it agrees with is what stops the
"your rosters are out of date" prompt honestly, rather than by patching the
comparison out.

WHERE THIS COMES FROM
=====================

Three pieces of the executable, all read rather than guessed:

``0x0012a968`` runs the check::

    jal   0x0012a888        ; compute the local checksum
    lw    v1, -19396(gp)    ; the CSUM the server sent
    xor   v0, v0, v1
    sltiu v0, v0, 1         ; 1 when identical

``0x0012a888`` is the loop. It opens a cursor with the query text at
``0x00579b90``::

    use 'GAEL' declare <cur> cursor for select * from 'YALP'
    where ('DIGT' >= 1) and ('DIGT' <= 32) order by 'DIGP'

-- database ``LEAG``, table ``PLAY``, team ids 1 through 32, ordered by player
id. It then fetches each row through ``0x0012a730``, which is *not* arithmetic:
it is a varargs marshaller that hands the query VM the format string at
``0x00579958`` plus 31 pointers spaced four bytes apart across a 124-byte
buffer. That format string is the authority for :data:`FIELDS`, and it names its
columns byte-reversed -- ``'DIGP'`` for ``PGID``, ``'RVOP'`` for ``POVR``.

``0x0039d7e8(buf, len, seed)`` accumulates::

    nor  v1, zero, a2       ; crc = ~seed
    lbu  v0, 0(t0)
    srl  a1, v1, 8
    xor  v0, v1, v0
    andi v0, v0, 0x00ff
    sll  v0, v0, 2
    lw   v1, 0(v0+table)
    xor  v1, v1, a1         ; crc = tab[(crc^b)&0xff] ^ (crc>>8)
    nor  v0, zero, v1       ; return ~crc

Its 256-entry table at ``0x00565510`` was checked entry by entry against the
reflected ``0xEDB88320`` polynomial and matches exactly, so the routine is
ordinary zlib CRC-32 and :func:`zlib.crc32` stands in for it directly.

THE ONE SURPRISE
================

The loop does not start from 0 or from ``0xFFFFFFFF``. It seeds the running
value with the **row count**, read as a 16-bit quantity::

    lhu   s0, 16(sp)        ; s0 = number of rows the cursor returned
    jal   0x0012a730        ; fetch a row into the 124-byte buffer
    daddu a2, s0, zero      ; ...which is passed as the seed
    jal   0x0039d7e8
    addiu a1, zero, 124
    daddu s0, v0, zero      ; running value

That is the detail that makes this worth writing down: every other part of the
algorithm is a stock CRC that anyone would guess, and seeding it with the row
count is the part that no amount of guessing recovers.
"""

from __future__ import annotations

import argparse
import struct
import sys
import zlib
from typing import Dict, List, Optional, Sequence, Tuple

try:
    from . import madden_tdb
except ImportError:  # run as a script
    import madden_tdb  # type: ignore

#: The 31 columns, in the order the marshaller passes them, straight off the
#: format string at 0x00579958. Order is load-bearing: the CRC runs over the
#: buffer in memory order, so a permutation silently produces a wrong number.
FIELDS: Tuple[str, ...] = (
    "PGID", "TGID", "PPOS", "PACC", "PAGI", "PAWR", "PBTK", "PCAR",
    "PCTH", "PJMP", "PIMP", "PINJ", "PKAC", "PKPR", "PPBK", "PRBK",
    "PSPD", "PSTA", "PSTR", "PTAK", "PTHA", "PTHP", "PTGH", "PKRT",
    "POVR", "PUCL", "PHGT", "PWGT", "PHAN", "PTEN", "PSTY",
)

#: 31 fields at 4 bytes each. The call site passes this literally as the length.
BUFFER_SIZE = 124

#: The ``where`` clause, as the query text spells it.
TEAM_ID_MIN = 1
TEAM_ID_MAX = 32

#: The table and database the query names, after un-reversing the 4CCs.
TABLE = "PLAY"

assert len(FIELDS) == 31
assert len(FIELDS) * 4 == BUFFER_SIZE


def collect_rows(container: "madden_tdb.Container") -> List[List[int]]:
    """Every player the query would see, in the order it would see them.

    ``DB_TEAMS.DAT`` splits the league across container members -- one per team,
    plus a free-agent pool and a long tail of historical squads -- so the
    ``TGID`` filter selects the 32 current teams out of all of them.

    Note this file is **not** what backs ``LEAG`` at runtime: that is member 0
    of ``template.dat``, registered by the single call at 0x002fa148. Both carry
    the same shipped roster and both yield the same 1743 rows and the same
    checksum, which is why reading it from here works and why the mistaken
    belief that the runtime "merges" these members went unnoticed.
    """
    rows: List[List[int]] = []
    team_index = FIELDS.index("TGID")
    for database in container.databases():
        if TABLE not in database:
            continue
        table = database.table(TABLE)
        for row in table.rows(FIELDS):
            if TEAM_ID_MIN <= row[team_index] <= TEAM_ID_MAX:
                rows.append(row)
    # `order by PGID` gives no direction, and ascending is correct -- proven at
    # the parser, not argued from a head-count. 0x004ce688 handles the token
    # after a sort key: `asc` (keyword id 33) stores 0 at 0x004ce7c4, `desc`
    # stores 1 at 0x004ce7e0, and an omitted direction falls through to
    # 0x004ce7a0, which stores the same 0. Omitted is byte-identical to `asc`.
    #
    # An earlier comment here claimed "every other query in the executable
    # spells asc explicitly". That was false and load-bearing: of 526 sort keys,
    # 326 say asc, 68 say desc, 118 take the direction from a runtime vararg,
    # and 14 omit it as this one does.
    rows.sort(key=lambda r: r[0])
    return rows


def checksum(rows: Sequence[Sequence[int]]) -> int:
    """The value the title computes for these rows."""
    # lhu: the seed is the row count truncated to 16 bits.
    accumulator = len(rows) & 0xFFFF
    for row in rows:
        packed = struct.pack("<31I", *(value & 0xFFFFFFFF for value in row))
        accumulator = zlib.crc32(packed, accumulator)
    return accumulator & 0xFFFFFFFF


def from_file(path: str) -> Tuple[int, List[List[int]]]:
    container = madden_tdb.load(path)
    rows = collect_rows(container)
    return checksum(rows), rows


def _diagnostics(rows: Sequence[Sequence[int]]) -> None:
    per_team: Dict[int, int] = {}
    team_index = FIELDS.index("TGID")
    for row in rows:
        per_team[row[team_index]] = per_team.get(row[team_index], 0) + 1
    ids = [row[0] for row in rows]
    duplicates = len(ids) - len(set(ids))

    print("rows              : %d" % len(rows), file=sys.stderr)
    print("teams             : %d" % len(per_team), file=sys.stderr)
    print("players per team  : %d-%d" % (min(per_team.values()),
                                         max(per_team.values())), file=sys.stderr)
    print("PGID range        : %d-%d" % (min(ids), max(ids)), file=sys.stderr)
    print("duplicate PGIDs   : %d%s" % (
        duplicates,
        "" if not duplicates else "  <-- sort order is ambiguous, see notes"),
        file=sys.stderr)
    if len(rows) > 0xFFFF:
        print("WARNING: row count exceeds 16 bits; the seed wraps.", file=sys.stderr)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute Madden NFL 2004's roster checksum from DB_TEAMS.DAT.")
    parser.add_argument("database", help="path to DB_TEAMS.DAT")
    parser.add_argument("--quiet", action="store_true",
                        help="print only the checksum")
    parser.add_argument("--format", choices=("decimal", "hex", "both"),
                        default="both",
                        help="the wire wants decimal; hex is for eyeballing")
    args = parser.parse_args(argv)

    try:
        value, rows = from_file(args.database)
    except (OSError, madden_tdb.TdbError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if not rows:
        print("error: no PLAY rows matched teams %d-%d; wrong file?"
              % (TEAM_ID_MIN, TEAM_ID_MAX), file=sys.stderr)
        return 2

    if not args.quiet:
        _diagnostics(rows)

    if args.format == "decimal":
        print(value)
    elif args.format == "hex":
        print("0x%08x" % value)
    else:
        print("%d  (0x%08x)" % (value, value))
    return 0


if __name__ == "__main__":
    sys.exit(main())

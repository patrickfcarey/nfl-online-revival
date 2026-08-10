"""Read Madden NFL 2004 playbooks: formations, plays, and per-position assignments.

The static analysis of the engine kept running into the same wall. Reverse
engineering says a zone defender steers to "a landmark from the play record"
and a pulling guard follows "an authored direction byte" -- but the values
themselves live in play data, so every conclusion stopped at "and the rest is
authored". `docs/tooling-gaps.md` scored this as the single biggest unlock:
roughly fourteen findings across the gameplay documents are blocked on being
able to read a playbook.

Playbooks are ordinary TDB databases in a TERF container, so `madden_tdb`
already does the hard part. What this module adds is the *shape*: nineteen
tables that only make sense as a graph, joined by 16-bit ids that are
meaningless in isolation.

    FORM  formation          -- I-Form, Shotgun, 4-3
      +-- SETL  set          -- the personnel/alignment under a formation
            +-- PLYL  play   -- the play itself: type, flags, name
                  +-- PLYS   -- one row per position: which art, which steps
                        +-- PSAL  step chain   <- the per-position assignment
                        +-- ARTL  route art    <- the drawn route, 5 points

    PBPL  a playbook's inclusion list (which PLYL rows this book contains)
      +-- PBAI  AI groups    -- (play, group, weight): what the CPU may call

Two of those deserve emphasis, because they are what the open questions want:

* **PBAI** is the CPU's candidate pool. `docs/ai-play-calling.md` established
  that the play caller's only filter is `AIGR == <group>`, so the size of a
  group *is* the AI's choice set. That document had to infer "under 18 plays
  per group" from the empty template's row cap; with a real playbook this
  becomes a measurement.
* **PSAL** is a chain of `(code, step, val1..3)` records per position. This is
  the file-side of the assignment the engine installs at play setup, and it is
  the most likely place to find the authored values behind zone landmarks,
  rush lanes, and pull paths.

Where a table is absent -- playbook databases are not all identical, and the
create-a-playbook template ships empty -- the accessor returns nothing rather
than raising. A caller walking 1088 members of `PLADATA.DAT` should not have to
guard every call.

Names are fixed-width byte fields inside the packed record, not bit-packed
integers: `read_bits` on a 192-bit name would produce a 24-byte number. `text()`
takes the bytes directly.
"""

from __future__ import annotations

import argparse
import sys
from typing import Dict, Iterator, List, NamedTuple, Optional, Sequence

from . import madden_tdb as tdb


#: Tables a member must have before we will call it a playbook. PLYL and PBPL
#: are the minimum that makes the graph navigable; everything else is optional
#: because the shipped books and the editor template do not agree on the rest.
PLAYBOOK_TABLES = ("PLYL", "PBPL")


class PlayError(Exception):
    """The database is not shaped like a playbook."""


def text(table: "tdb.Table", record: bytes, name: str) -> str:
    """A fixed-width name column, trimmed at the first NUL.

    Name fields are byte-aligned runs inside the record rather than bit-packed
    values, so they are sliced out rather than read through `read_bits`.
    """
    field = table.fields.get(name)
    if field is None:
        return ""
    start = field.offset_bits // 8
    raw = record[start:start + field.bits // 8]
    return raw.split(b"\0", 1)[0].decode("latin-1", "replace").strip()


class Formation(NamedTuple):
    id: int
    type: int
    name: str


class Set(NamedTuple):
    id: int
    formation: int
    type: int
    situation: int
    flags: int
    name: str


class Play(NamedTuple):
    id: int
    set: int
    type: int
    situation: int
    flags: int
    name: str
    risk: int
    motion: int


class AiEntry(NamedTuple):
    """One `(play, AI group, weight)` row -- the CPU's candidate pool."""
    play: int          # PBPL id, not PLYL: resolve through the book's plays
    group: int
    weight: int


class Step(NamedTuple):
    """One record of a position's assignment chain."""
    chain: int         # PSAL id shared by every step of one chain
    index: int         # `step` -- ordering within the chain
    code: int
    values: tuple      # val1, val2, val3


class Assignment(NamedTuple):
    """What one position is told to do on one play."""
    position: int      # `poso`
    chain: int         # PSAL id
    art: int           # ARTL id, 0 when the position has no drawn route
    steps: List[Step]


class Playbook:
    """One playbook database, with its foreign keys resolved on demand."""

    def __init__(self, database: "tdb.Database") -> None:
        missing = [t for t in PLAYBOOK_TABLES if t not in database]
        if missing:
            raise PlayError("not a playbook: no %s" % ", ".join(missing))
        self.db = database
        self._steps: Optional[Dict[int, List[Step]]] = None

    # -- optional-table helper ------------------------------------------

    def _table(self, name: str) -> Optional["tdb.Table"]:
        """The table, or None. Absent tables are normal, not an error."""
        if name not in self.db:
            return None
        return self.db.table(name)

    def _each(self, name: str) -> Iterator[tuple]:
        table = self._table(name)
        if table is None:
            return
        for i in range(table.record_count):
            yield table, table.record(i)

    # -- the graph -------------------------------------------------------

    def formations(self) -> List[Formation]:
        return [Formation(t.value(r, "FORM"), t.value(r, "FTYP"), text(t, r, "name"))
                for t, r in self._each("FORM")]

    def sets(self) -> List[Set]:
        return [Set(t.value(r, "SETL"), t.value(r, "FORM"), t.value(r, "SETT"),
                    t.value(r, "SITT"), t.value(r, "SLF_"), text(t, r, "name"))
                for t, r in self._each("SETL")]

    def plays(self) -> List[Play]:
        return [Play(t.value(r, "PLYL"), t.value(r, "SETL"), t.value(r, "PLYT"),
                     t.value(r, "SITT"), t.value(r, "PLF_"), text(t, r, "name"),
                     t.value(r, "risk"), t.value(r, "motn"))
                for t, r in self._each("PLYL")]

    def book_plays(self) -> Dict[int, int]:
        """`PBPL id -> PLYL id` -- which plays this book actually contains.

        The distinction matters: `PLYL` is the league-wide play list, `PBPL` is
        this book's selection from it, and `PBAI` keys off the latter.
        """
        return {t.value(r, "PBPL"): t.value(r, "PLYL")
                for t, r in self._each("PBPL")}

    def ai_entries(self) -> List[AiEntry]:
        return [AiEntry(t.value(r, "PBPL"), t.value(r, "AIGR"), t.value(r, "prct"))
                for t, r in self._each("PBAI")]

    def ai_groups(self) -> Dict[int, List[AiEntry]]:
        """The CPU's candidate pool, grouped -- the answer to "how many plays
        can the AI actually pick from in this situation"."""
        groups: Dict[int, List[AiEntry]] = {}
        for entry in self.ai_entries():
            groups.setdefault(entry.group, []).append(entry)
        return groups

    # -- assignments -----------------------------------------------------

    def step_chains(self) -> Dict[int, List[Step]]:
        """Every PSAL chain, keyed by id and ordered by `step`.

        Built once and cached: a shipped book has thousands of rows and callers
        ask per play.
        """
        if self._steps is None:
            chains: Dict[int, List[Step]] = {}
            for t, r in self._each("PSAL"):
                step = Step(t.value(r, "PSAL"), t.value(r, "step"),
                            t.value(r, "code"),
                            (t.value(r, "val1"), t.value(r, "val2"),
                             t.value(r, "val3")))
                chains.setdefault(step.chain, []).append(step)
            for chain in chains.values():
                chain.sort(key=lambda s: s.index)
            self._steps = chains
        return self._steps

    def assignments(self, play_id: int) -> List[Assignment]:
        """Per-position assignments for a `PLYL` id, ordered by position."""
        chains = self.step_chains()
        out: List[Assignment] = []
        for t, r in self._each("PLYS"):
            if t.value(r, "PLYL") != play_id:
                continue
            chain = t.value(r, "PSAL")
            out.append(Assignment(t.value(r, "poso"), chain,
                                  t.value(r, "ARTL"), chains.get(chain, [])))
        out.sort(key=lambda a: a.position)
        return out

    def art(self, art_id: int) -> List[tuple]:
        """The drawn route for an `ARTL` id, as up to five `(t, u, v, x, y)`.

        Points whose fields are all zero are trailing padding and are dropped;
        a three-point route and a five-point route share one record shape.
        """
        for t, r in self._each("ARTL"):
            if t.value(r, "ARTL") != art_id:
                continue
            points = []
            for i in range(1, 6):
                suffix = "%02d" % i
                point = tuple(t.value(r, p + suffix)
                              for p in ("at", "au", "av", "ax", "ay"))
                if any(point):
                    points.append(point)
            return points
        return []


#: Shipped play content is not a TDB. `PLADATA.DAT` holds ~1038 LZH1-compressed
#: members whose payload begins with this magic -- a different format entirely,
#: carrying skeleton/animation names ("rhip", "lknee", "up_torso") alongside the
#: play geometry. Reversing it is a separate job; `scan_container` reports it so
#: nobody spends another hour discovering that a playbook reader finds no
#: playbooks in the file named after play data.
DMF_MAGIC = b"DMF\0"


class Member(NamedTuple):
    """One container member, classified without decompressing the payload."""
    index: int
    kind: str          # 'playbook', 'tdb', 'dmf', 'empty', or 'other'
    compressed: int
    size: int


def scan_container(path: str, probe: int = 64) -> List[Member]:
    """Classify every member of a TERF container, cheaply.

    `read_terf` decompresses eagerly, which on a 61 MB file with a thousand
    members is minutes of bit-at-a-time Python before the first answer. This
    walks the directory, and decompresses only the first *probe* bytes worth of
    each member -- enough to read a magic number and no more.
    """
    import struct

    from . import lzh1

    data = open(path, "rb").read()
    chunks = {}
    pos = 0
    while pos + 8 <= len(data):
        tag = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        if size < 8:
            break
        chunks[tag] = (pos, size)
        pos += size
    if b"TERF" not in chunks or b"DIR1" not in chunks:
        raise PlayError("not a TERF container: %s" % path)

    head, _ = chunks[b"TERF"]
    count = struct.unpack_from("<H", data, head + 14)[0]
    dpos, dsize = chunks[b"DIR1"]
    directory = struct.unpack_from("<%dI" % ((dsize - 8) // 4), data, dpos + 8)
    comp = None
    if b"COMP" in chunks:
        cpos, csize = chunks[b"COMP"]
        comp = struct.unpack_from("<%dI" % ((csize - 8) // 4), data, cpos + 8)
    base = chunks[b"DATA"][0]

    out: List[Member] = []
    for i in range(count):
        offset, packed = directory[2 * i], directory[2 * i + 1]
        codec, size = (comp[2 * i], comp[2 * i + 1]) if comp else (0, packed)
        if not size:
            out.append(Member(i, "empty", packed, size))
            continue
        blob = data[base + offset:base + offset + packed]
        if codec == 5:
            # Decompressing the whole member just to read four bytes is the
            # cost this function exists to avoid; a short output is expected
            # and not an error.
            try:
                head_bytes = lzh1.lzh1_decompress(blob, min(probe, size))
            except Exception:
                head_bytes = b""
        else:
            head_bytes = blob[:probe]
        if head_bytes[:4] == DMF_MAGIC:
            kind = "dmf"
        elif head_bytes[:2] == b"DB":
            kind = "tdb"
        else:
            kind = "other"
        out.append(Member(i, kind, packed, size))
    return out


def playbooks(container: "tdb.Container") -> Iterator[tuple]:
    """Every member of a container that is a playbook, as `(index, Playbook)`.

    `PLADATA.DAT` holds over a thousand members and not all of them are
    playbooks, so this skips rather than raises.
    """
    for index in range(len(container)):
        database = container.database(index)
        if database is None:
            continue
        try:
            yield index, Playbook(database)
        except PlayError:
            continue


def load(path: str, member: Optional[int] = None) -> "Playbook":
    """The playbook at *member*, or the first one in the file."""
    container = tdb.load(path)
    if member is not None:
        database = container.database(member)
        if database is None:
            raise PlayError("member %d is not a database" % member)
        return Playbook(database)
    for _index, book in playbooks(container):
        return book
    raise PlayError("no playbook member in %s" % path)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def _summarise(index: int, book: Playbook) -> str:
    counts = []
    for name in ("FORM", "SETL", "PLYL", "PBPL", "PBAI", "PLYS", "PSAL", "ARTL"):
        table = book._table(name)
        if table is not None and table.record_count:
            counts.append("%s=%d" % (name, table.record_count))
    return "[%4d] %s" % (index, " ".join(counts) if counts else "(empty)")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read Madden NFL 2004 playbooks out of a TERF container "
                    "(PLADATA.DAT, or TEMPLATE.DAT's editor template).")
    parser.add_argument("path", help="a TERF container holding playbook TDBs")
    parser.add_argument("--member", type=int,
                        help="which container member to read (default: the "
                             "first that looks like a playbook)")
    parser.add_argument("--list", action="store_true",
                        help="list every playbook member and its row counts")
    parser.add_argument("--scan", action="store_true",
                        help="classify every container member without "
                             "decompressing it (use on PLADATA.DAT)")
    parser.add_argument("--plays", action="store_true",
                        help="list the plays in the book")
    parser.add_argument("--ai-groups", action="store_true",
                        help="the CPU's candidate pool, by AI group")
    parser.add_argument("--play", type=int, metavar="PLYL",
                        help="show one play's per-position assignments")
    args = parser.parse_args(argv)

    try:
        container = tdb.load(args.path)
    except (OSError, tdb.TdbError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if args.scan:
        from collections import Counter
        members = scan_container(args.path)
        kinds = Counter(m.kind for m in members)
        for kind, n in kinds.most_common():
            print("%-9s %5d" % (kind, n))
        if kinds.get("dmf"):
            print()
            print("This container holds DMF play content, not playbook TDBs.")
            print("Shipped plays are DMF; the TDB playbook schema (PLYL/PBAI/")
            print("PSAL) is the create-a-playbook side. See docs/play-data.md.")
        return 0

    if args.list:
        found = 0
        for index, book in playbooks(container):
            print(_summarise(index, book))
            found += 1
        print("%d playbook member%s of %d"
              % (found, "" if found == 1 else "s", len(container)))
        return 0 if found else 1

    try:
        book = (Playbook(container.database(args.member))
                if args.member is not None else load(args.path))
    except (PlayError, TypeError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if args.plays:
        plays = book.plays()
        for play in plays:
            print("PLYL %-5d set %-4d type %-3d %s"
                  % (play.id, play.set, play.type, play.name))
        print("%d plays" % len(plays))
        return 0 if plays else 1

    if args.ai_groups:
        groups = book.ai_groups()
        if not groups:
            print("no PBAI rows: this book gives the CPU nothing to choose from")
            return 1
        contents = book.book_plays()
        names = {p.id: p.name for p in book.plays()}
        for group in sorted(groups):
            entries = groups[group]
            total = sum(e.weight for e in entries)
            print("AI group %-4d %d plays, total weight %d"
                  % (group, len(entries), total))
            for entry in sorted(entries, key=lambda e: -e.weight):
                play = contents.get(entry.play)
                print("    weight %-4d PBPL %-5d %s"
                      % (entry.weight, entry.play,
                         names.get(play, "(play %s)" % play)))
        return 0

    if args.play is not None:
        rows = book.assignments(args.play)
        if not rows:
            print("no PLYS rows for play %d" % args.play)
            return 1
        for row in rows:
            steps = " ".join("%d:%d%s" % (s.index, s.code,
                                          "" if not any(s.values) else str(s.values))
                             for s in row.steps)
            print("pos %-3d chain %-5d art %-5d %s"
                  % (row.position, row.chain, row.art, steps or "(no steps)"))
        return 0

    print(_summarise(args.member if args.member is not None else 0, book))
    print("pass --plays, --ai-groups, --play N, or --list")
    return 0


if __name__ == "__main__":
    sys.exit(main())

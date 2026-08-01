"""Build a LEAG container the console can install, within a size budget.

The console will not accept an arbitrarily large roster download. Measured on
hardware: 393,216 bytes transfers completely, while 2,723,072 and 8,439,360 are
both refused mid-transfer, the console hanging up as soon as it reads a
``Content-Length`` past its buffer. The retail ``DB_TEAMS.DAT`` is 8.4 MB, so it
can never be served whole.

This writes a **valid TERF container** holding a chosen subset of the members of
an existing one. Valid is the point: truncating a container leaves a directory
whose entries point past the end of the file, and handing that to the game's
loader is a good way to crash a console rather than to learn anything.

The output is byte-compatible with what :mod:`tools.madden_tdb` reads back, and
the tool verifies that before writing, because a container that only *looks*
right is exactly the failure this exists to avoid.

Usage::

    # everything that fits, largest budget first
    python3 tools/build_roster.py DB_TEAMS.DAT out.dat --max-bytes 409600

    # or name the members explicitly (0 is the free-agent pool, 1-32 the teams)
    python3 tools/build_roster.py DB_TEAMS.DAT out.dat --members 1,2,3

Serve the result with ``--roster-payload``; the server computes the CRC it
advertises from the bytes it actually serves, so nothing needs to agree by hand.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path
from typing import List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import madden_tdb  # noqa: E402

#: The header is 64 bytes and the directory starts there, as `madden_tdb` reads.
HEADER_SIZE = 0x40
DIR_MAGIC = b"DIR1"

#: Members are aligned in the retail file; keeping the same alignment means the
#: output differs from the original only in which members it contains.
ALIGNMENT = 64


class BuildError(Exception):
    pass


def _aligned(value: int) -> int:
    return (value + ALIGNMENT - 1) // ALIGNMENT * ALIGNMENT


def build(source: bytes, members: Sequence[int]) -> bytes:
    """A new TERF container holding just the named members, in order."""
    container = madden_tdb.Container(source)
    if not members:
        raise BuildError("no members selected")
    for index in members:
        if not 0 <= index < len(container):
            raise BuildError("member %d is outside 0..%d"
                             % (index, len(container) - 1))

    # Directory: 8-byte magic+size, then one (offset, size) pair per member.
    dir_size = _aligned(8 + len(members) * 8)
    body_base = HEADER_SIZE + dir_size

    header = bytearray(HEADER_SIZE)
    header[0:4] = b"TERF"
    struct.pack_into("<I", header, 0x04, HEADER_SIZE)
    struct.pack_into("<H", header, 0x0E, len(members))
    # Copy the retail header's remaining bytes verbatim: they are not understood
    # and guessing at them is how a plausible-looking file gets rejected.
    header[0x08:0x0E] = source[0x08:0x0E]

    directory = bytearray(dir_size)
    directory[0:4] = DIR_MAGIC
    struct.pack_into("<I", directory, 4, dir_size)

    body = bytearray()
    for slot, index in enumerate(members):
        offset, size = container._members[index]      # (offset, size)
        start = container._member_base + offset
        chunk = source[start:start + size]
        if len(chunk) != size:
            raise BuildError("member %d is truncated in the source" % index)
        struct.pack_into("<II", directory, 8 + slot * 8, len(body), size)
        body += chunk
        body += b"\x00" * (_aligned(len(body)) - len(body))

    return bytes(header) + bytes(directory) + bytes(body)


def members_within(source: bytes, budget: int,
                   preferred: Optional[Sequence[int]] = None) -> List[int]:
    """As many members as fit, taking `preferred` first.

    Defaults to the NFL teams (1-32) before anything else, since those are the
    rows the roster checksum covers.
    """
    container = madden_tdb.Container(source)
    count = len(container)
    # Default to the NFL teams, but only those the container actually has --
    # a smaller container (or a test fixture) has no member 32 to ask for.
    order = [i for i in (preferred if preferred else range(1, 33)) if i < count]
    order += [i for i in range(count) if i not in order]

    chosen: List[int] = []
    for index in order:
        candidate = chosen + [index]
        if len(build(source, candidate)) <= budget:
            chosen = candidate
        elif chosen:
            # Keep trying smaller members rather than stopping at the first
            # one that does not fit.
            continue
    return sorted(chosen)


def verify(blob: bytes, expected: int) -> None:
    """Read the result back, so a malformed container never reaches a console."""
    container = madden_tdb.Container(blob)
    if len(container) != expected:
        raise BuildError("wrote %d members but the directory says %d"
                         % (expected, len(container)))
    seen = 0
    for index in range(len(container)):
        database = container.database(index)
        if database is None:
            raise BuildError("member %d did not read back as a database" % index)
        if madden_tdb.fourcc(b"YALP") in database:      # "PLAY"
            table = database.table("PLAY")
            seen += table.record_count
    if seen == 0:
        raise BuildError("no PLAY rows survived; this is not a roster")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a LEAG container small enough for the console to "
                    "accept as a roster update.")
    parser.add_argument("source", help="a retail container, e.g. DB_TEAMS.DAT")
    parser.add_argument("output")
    parser.add_argument("--max-bytes", type=lambda v: int(v, 0), default=409600,
                        help="size budget (default 409600, the figure the "
                             "runtime registers for the league database)")
    parser.add_argument("--members",
                        help="explicit comma-separated member indices; "
                             "0 is the free-agent pool, 1-32 the NFL teams")
    args = parser.parse_args(argv)

    try:
        source = Path(args.source).read_bytes()
    except OSError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    try:
        if args.members:
            members = [int(p) for p in args.members.split(",") if p.strip()]
        else:
            members = members_within(source, args.max_bytes)
        blob = build(source, members)
        verify(blob, len(members))
    except (BuildError, madden_tdb.TdbError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if len(blob) > args.max_bytes:
        print("error: %d bytes exceeds the %d budget"
              % (len(blob), args.max_bytes), file=sys.stderr)
        return 2

    Path(args.output).write_bytes(blob)

    container = madden_tdb.Container(blob)
    rows = 0
    teams = set()
    for index in range(len(container)):
        database = container.database(index)
        if database is None or "PLAY" not in database:
            continue
        table = database.table("PLAY")
        for record_index in range(table.record_count):
            rows += 1
            teams.add(table.value(table.record(record_index), "TGID"))
    print("%s: %d bytes, %d member(s), %d players, %d team id(s)"
          % (args.output, len(blob), len(members), rows, len(teams)))
    print("members: %s" % ", ".join(str(m) for m in members))
    return 0


if __name__ == "__main__":
    sys.exit(main())

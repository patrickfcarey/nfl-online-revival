"""Build a LEAG payload the console can install.

**The payload is NOT a TERF container.** It is a single raw Madden TDB, and it
must be exactly the size of member 0 of ``template.dat`` -- 253,044 bytes on the
retail disc. Both facts were established from the executable and then confirmed
byte-for-byte:

* ``0x004c9e90`` refuses anything whose first word is not ``0x08004244``
  (``"DB"`` plus version 0x0800), so a container header is rejected outright.
* ``0x00352738`` sizes its buffer from ``0x003b6cf0`` -- the on-disc size of
  member ``gp+10800`` of archive ``gp+10796`` -- and ``0x00305f94`` refuses a
  ``Content-Length`` larger than it, on the header alone.
* ``0x003527c0`` CRCs *that whole buffer*, not the bytes received, so a short
  payload hashes uninitialised heap and can never verify.

The 409,600 at ``gp+10792`` is a red herring: it is read once at boot and the
callee ignores it. It is the in-RAM database capacity, not the download size.

There are also **two** checksum layers. Ours is the ``CRC`` field of the
manifest, plain ``zlib.crc32`` seed 0 over the served bytes. Inside the TDB
there are ten more, one 4-byte LE word after each block, CRC-32 with polynomial
0x04C11DB7 MSB-first, init 0xFFFFFFFF and no final XOR (``0x004ca810``). Editing
records means recomputing those too.

----

This module also builds TERF containers, which is what it was written for before
any of the above was known. That is kept for inspecting and subsetting archives;
it is **not** how a roster is delivered.

The console accepts exactly one size: the on-disc size of the member backing
``LEAG``, 253,044 bytes on retail. Anything larger is refused on the
``Content-Length`` alone, before a byte of body is read (``0x00305f94``);
anything smaller fails the checksum, because ``0x003527c0`` hashes the whole
allocated buffer at that fixed size rather than the bytes received, so the
uninitialised tail is included. The retail ``DB_TEAMS.DAT`` is 8.4 MB and can
never be served at all.

An earlier version of this note claimed "393,216 bytes transfers completely,
while larger payloads are refused mid-transfer". That was wrong twice over. A
393,216-byte body exceeds the cap and is refused unread; what actually completed
was our own ``sendall`` returning once the kernel accepted the bytes, which says
nothing about whether the console read them. The apparent ceiling was the
socket send buffer.

This writes a **valid TERF container** holding a chosen subset of the members of
an existing one. Valid is the point: truncating a container leaves a directory
whose entries point past the end of the file, and handing that to the game's
loader is a good way to crash a console rather than to learn anything.

The output is byte-compatible with what :mod:`tools.madden_tdb` reads back, and
the tool verifies that before writing, because a container that only *looks*
right is exactly the failure this exists to avoid.

Usage::

    # THE SERVABLE PAYLOAD: member 0 of template.dat, a raw TDB
    python3 tools/build_roster.py TEMPLATE.DAT roster.dat --extract-member 0

The container modes below build TERF archives, which the console **cannot
install** -- 0x004c9e90 refuses anything whose first word is not 0x08004244.
They exist for inspecting and subsetting archives, not for serving::

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
    parser.add_argument("--max-bytes", type=lambda v: int(v, 0), default=253044,
                        help="size budget. The default is the size of "
                             "template.dat member 0, which is what the console "
                             "allocates and CRCs. 409600 was an earlier guess "
                             "and is wrong -- that is the in-RAM capacity.")
    parser.add_argument("--extract-member", type=int, metavar="N",
                        help="write member N of the source out as a raw TDB. "
                             "THIS is the servable roster payload: member 0 of "
                             "template.dat is the LEAG database itself.")
    parser.add_argument("--members",
                        help="explicit comma-separated member indices; "
                             "0 is the free-agent pool, 1-32 the NFL teams")
    args = parser.parse_args(argv)

    try:
        source = Path(args.source).read_bytes()
    except OSError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if args.extract_member is not None:
        try:
            container = madden_tdb.Container(source)
            offset, size = container._members[args.extract_member]
            blob = source[container._member_base + offset:][:size]
        except (IndexError, madden_tdb.TdbError) as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
        if blob[:2] != b"DB":
            print("error: member %d is not a TDB (magic %r); the loader "
                  "requires 'DB'" % (args.extract_member, blob[:2]),
                  file=sys.stderr)
            return 2
        Path(args.output).write_bytes(blob)
        import zlib
        print("%s: %d bytes, crc32 %d (0x%08x)"
              % (args.output, len(blob), zlib.crc32(blob) & 0xFFFFFFFF,
                 zlib.crc32(blob) & 0xFFFFFFFF))
        print("serve with --roster-payload; the server derives the CRC itself.")
        return 0

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

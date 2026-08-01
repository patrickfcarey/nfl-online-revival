"""Rename one player, so an install becomes observable instead of inferred.

Serving the disc's own member back to the console proves the transport but
nothing else: the bytes that end up in memory are identical either way, so no
inspection afterwards can distinguish "installed our payload" from "loaded the
disc". The install was established from process evidence -- the buffer was
allocated, filled, checksummed and freed -- rather than from any end state.

This removes that last inference. It rewrites one player's surname in place,
which changes what the console *displays*: if the name appears on screen, our
bytes are in the league database and nothing else can explain it.

Two things make the edit safe to do in place:

* ``PLAY`` records are fixed-width (108 bytes) and the name fields are
  fixed-width too, so nothing moves and the file length is unchanged. That
  matters because the console demands exactly the on-disc size.
* Only the bytes inside one field change, so every other field, table and
  record keeps its offset.

The TDB carries its own per-block checksums -- CRC-32 with polynomial
0x04C11DB7, MSB-first, init 0xFFFFFFFF, no final XOR (``0x004ca810``) -- and a
block whose checksum no longer matches is rejected with error 43. Those are
recomputed here. The outer ``CRC`` the manifest advertises is computed by the
server from the bytes it serves, so it needs no attention.

Usage::

    python3 tools/mark_roster.py leag_member0.dat marked.dat --surname ZZTEST
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import madden_tdb  # noqa: E402


def tdb_crc(data: bytes) -> int:
    """The TDB's own block checksum: poly 0x04C11DB7, MSB-first, no final XOR."""
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 \
                else (crc << 1) & 0xFFFFFFFF
    return crc


def reseal(blob: bytes) -> bytes:
    """Recompute every block checksum in a TDB.

    The layout, recovered by reproducing the stored values on an untouched file
    rather than by reading the checker:

    * bytes 0..19 are the file header; its checksum sits at +20.
    * the table directory (24 .. 24 + 8*tables) is checksummed into the FIRST
      table's word at +0 -- the field a JS reader calls ``priorcrc``, which is
      exactly what it is: the checksum of the *prior* block.
    * each table header runs +4..+36 with its checksum at +36.
    * each table's data block runs from its +40 to the next table's header, and
      its checksum lands in that next table's +0. The last table's data runs to
      the final four bytes of the file, which hold its checksum.

    So the chain interleaves: every block is followed by its own checksum word,
    and those words happen to fall inside the next structure's first field.
    """
    out = bytearray(blob)
    u32 = lambda o: struct.unpack_from("<I", out, o)[0]

    tables = u32(16)
    base = 24 + tables * 8
    heads = [base + u32(28 + i * 8) for i in range(tables)]

    # File header, then the directory into table 0's leading word.
    struct.pack_into("<I", out, 20, tdb_crc(bytes(out[0:20])))
    struct.pack_into("<I", out, heads[0], tdb_crc(bytes(out[24:base])))

    for index, head in enumerate(heads):
        struct.pack_into("<I", out, head + 36,
                         tdb_crc(bytes(out[head + 4:head + 36])))
        end = heads[index + 1] if index + 1 < tables else len(out) - 4
        slot = heads[index + 1] if index + 1 < tables else len(out) - 4
        struct.pack_into("<I", out, slot, tdb_crc(bytes(out[head + 40:end])))
    return bytes(out)


def rename_first_player(blob: bytes, surname: str) -> bytes:
    """Rewrite the surname of record 0 of PLAY, in place."""
    database = madden_tdb.Database(blob, 0)
    if "PLAY" not in database:
        raise ValueError("no PLAY table; this is not a league database")
    table = database.table("PLAY")
    field = table.fields.get("PLNA")
    if field is None:
        raise ValueError("no PLNA field")

    width = field.bits // 8
    encoded = surname.encode("latin-1")[:width - 1]
    if not encoded:
        raise ValueError("empty surname")

    out = bytearray(blob)
    record_start = table._records
    name_start = record_start + field.offset_bits // 8
    # Fixed-width field: overwrite and NUL-fill, so nothing after it moves.
    out[name_start:name_start + width] = encoded + b"\x00" * (width - len(encoded))
    # A block whose checksum no longer matches is refused with error 43.
    return reseal(bytes(out))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rename one player so a roster install can be seen on "
                    "screen rather than inferred.")
    parser.add_argument("source", help="a raw LEAG database (not a container)")
    parser.add_argument("output")
    parser.add_argument("--surname", default="ZZTEST",
                        help="the surname to write (default ZZTEST)")
    args = parser.parse_args(argv)

    try:
        blob = Path(args.source).read_bytes()
    except OSError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if blob[:2] != b"DB":
        print("error: %s is not a raw TDB (magic %r). The console rejects "
              "anything whose first word is not 0x08004244."
              % (args.source, blob[:2]), file=sys.stderr)
        return 2

    try:
        database = madden_tdb.Database(blob, 0)
        table = database.table("PLAY")
        before = _surname(table, blob)
        marked = rename_first_player(blob, args.surname)
        after = _surname(madden_tdb.Database(marked, 0).table("PLAY"), marked)
    except (ValueError, madden_tdb.TdbError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if len(marked) != len(blob):
        print("error: length changed from %d to %d; the console demands the "
              "exact size" % (len(blob), len(marked)), file=sys.stderr)
        return 2

    Path(args.output).write_bytes(marked)
    import zlib
    print("%s: %d bytes, crc32 %d" % (args.output, len(marked),
                                      zlib.crc32(marked) & 0xFFFFFFFF))
    print("player 0 surname: %r -> %r" % (before, after))
    print()
    print("Serve this with --roster-payload. If that surname shows up in the")
    print("console's roster, the install is observed rather than inferred.")
    print()
    print("The TDB's own per-block checksums are recomputed, so the inner")
    print("checks at 0x004ca810 should pass as they do for the disc file.")
    return 0


def _surname(table, blob: bytes) -> str:
    field = table.fields["PLNA"]
    start = table._records + field.offset_bits // 8
    return blob[start:start + field.bits // 8].split(b"\x00")[0].decode("latin-1")


if __name__ == "__main__":
    sys.exit(main())

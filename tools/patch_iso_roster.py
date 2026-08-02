"""Bake a roster into the disc image, so no server is needed to play.

A roster delivered over the network lives in RAM and nothing more. The console
reloads `template.dat` from the disc every boot, so an installed roster is gone
the moment the game restarts -- fine for proving the delivery chain works, no
use at all for actually playing.

Writing the roster into the image makes it permanent. The game then boots with
it, offline, with no server, no patches and no online menu involved.

WHY THIS IS SAFE TO DO IN PLACE

`LEAG` is member 0 of `/DATA/TEMPLATE.DAT`, and a roster built by
`build_year_roster.py` is exactly the size of the member it replaces -- 253,044
bytes, because the console demands that size and the builder preserves it. So
the bytes can be overwritten where they lie: no file moves, no directory entry
changes, no ISO rebuild, and the image stays byte-identical everywhere else.

The tool refuses if the sizes differ, which is the only way this could corrupt
an image.

Usage::

    python3 tools/patch_iso_roster.py game.iso rosters/2023.dat -o patched.iso
    python3 tools/patch_iso_roster.py game.iso rosters/2023.dat --in-place
"""

from __future__ import annotations

import argparse
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import madden_tdb  # noqa: E402

SECTOR = 2048
TEMPLATE_PATH = "/DATA/TEMPLATE.DAT"


class PatchError(Exception):
    pass


def locate(iso: Path, path: str = TEMPLATE_PATH) -> Tuple[int, int]:
    """(byte offset, size) of the template inside the image.

    Uses xorriso when it is there and falls back to scanning when it is not,
    because it very often is not -- and a missing tool that makes the patch
    quietly do nothing is worse than one that fails loudly. The first version
    of this exited 0 after copying 3.2 GB and patching nothing at all.
    """
    try:
        return _locate_with_xorriso(iso, path)
    except PatchError:
        return _locate_by_scanning(iso)


def _locate_by_scanning(iso: Path) -> Tuple[int, int]:
    """Find the TERF container by looking for it.

    Files inside an ISO9660 image start on a 2048-byte sector boundary, so the
    search only has to look there. A TERF header carries its own directory
    offset and member count, which is enough to tell the real one from a chance
    occurrence of four bytes.
    """
    size = iso.stat().st_size
    chunk = 1 << 24
    with open(iso, "rb") as handle:
        position = 0
        while position < size:
            handle.seek(position)
            block = handle.read(chunk + SECTOR)
            if not block:
                break
            start = 0
            while True:
                found = block.find(b"TERF", start)
                if found < 0 or found >= chunk:
                    break
                absolute = position + found
                start = found + 1
                if absolute % SECTOR:
                    continue                  # files are sector-aligned
                header = block[found:found + 16]
                if len(header) < 16:
                    continue
                dir_offset = struct.unpack_from("<I", header, 4)[0]
                members = struct.unpack_from("<H", header, 0x0E)[0]
                # The template holds twelve members; DB_TEAMS holds 232. Both
                # are on the disc, so check the count as well as the magic.
                if dir_offset == 0x40 and 2 <= members <= 64:
                    handle.seek(absolute)
                    candidate = handle.read(4 * 1024 * 1024)
                    try:
                        container = madden_tdb.Container(candidate)
                        offset, member_size = container._members[0]
                        if member_size == 253044:
                            total = max(o + s for o, s in container._members)
                            return absolute, container._member_base + total
                    except Exception:
                        pass
            position += chunk
    raise PatchError("no league container found in %s -- is this the right "
                     "disc image?" % iso)


def _locate_with_xorriso(iso: Path, path: str) -> Tuple[int, int]:
    """(byte offset, size) of a file inside the image, via xorriso."""
    try:
        result = subprocess.run(
            ["xorriso", "-indev", str(iso), "-find", path,
             "-exec", "report_lba", "--"],
            capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PatchError("xorriso unavailable: %s" % exc)
    for line in result.stdout.splitlines():
        if path not in line:
            continue
        # "File data lba:  0 ,  1461725 ,  1330 ,  2723072 , '/DATA/TEMPLATE.DAT'"
        parts = [p.strip() for p in line.split(",")]
        try:
            lba = int(parts[1])
            size = int(parts[3])
        except (IndexError, ValueError):
            continue
        return lba * SECTOR, size
    raise PatchError("%s is not in %s" % (path, iso))


def member_span(template: bytes, index: int = 0) -> Tuple[int, int]:
    """(offset, size) of one member inside a TERF container."""
    container = madden_tdb.Container(template)
    offset, size = container._members[index]
    return container._member_base + offset, size


def patch(iso: Path, roster: bytes) -> Tuple[int, int]:
    """Overwrite LEAG in the image. Returns (absolute offset, bytes written)."""
    template_offset, template_size = locate(iso)

    with open(iso, "rb") as handle:
        handle.seek(template_offset)
        template = handle.read(template_size)
    if template[:4] != b"TERF":
        raise PatchError("what is at LBA %d is not a TERF container (%r)"
                         % (template_offset // SECTOR, template[:4]))

    inner_offset, inner_size = member_span(template)
    if len(roster) != inner_size:
        raise PatchError(
            "the roster is %d bytes but member 0 is %d. They must match "
            "exactly -- the console allocates that size and checksums the whole "
            "buffer, and anything else would move every file after it."
            % (len(roster), inner_size))
    if roster[:2] != b"DB":
        raise PatchError("the roster is not a raw TDB (magic %r)" % roster[:2])

    absolute = template_offset + inner_offset
    with open(iso, "r+b") as handle:
        handle.seek(absolute)
        handle.write(roster)
    return absolute, len(roster)


def verify(iso: Path, roster: bytes) -> bool:
    """Read it back off the disc, to be sure rather than hopeful."""
    template_offset, template_size = locate(iso)
    with open(iso, "rb") as handle:
        handle.seek(template_offset)
        template = handle.read(template_size)
    inner_offset, inner_size = member_span(template)
    return template[inner_offset:inner_offset + inner_size] == roster


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write a roster into a Madden NFL 2004 disc image, so the "
                    "game boots with it and needs no server.")
    parser.add_argument("iso")
    parser.add_argument("roster", help="a raw LEAG database, e.g. rosters/2023.dat")
    parser.add_argument("-o", "--output",
                        help="write a patched copy here, leaving the original "
                             "untouched (needs room for another copy)")
    parser.add_argument("--in-place", action="store_true",
                        help="modify the image given. There is no undo.")
    args = parser.parse_args(argv)

    source = Path(args.iso)
    if not source.exists():
        print("error: no image at %s" % source, file=sys.stderr)
        return 2
    if not args.output and not args.in_place:
        print("error: give -o OUTPUT, or --in-place to modify this image",
              file=sys.stderr)
        return 2

    try:
        roster = Path(args.roster).read_bytes()
    except OSError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    target = source
    if args.output:
        target = Path(args.output)
        print("copying %s -> %s (%.1f GB)"
              % (source, target, source.stat().st_size / 1e9))
        shutil.copy2(source, target)

    try:
        offset, written = patch(target, roster)
    except (PatchError, madden_tdb.TdbError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if not verify(target, roster):
        print("error: the image does not read back what was written",
              file=sys.stderr)
        return 2

    print("%s: wrote %d bytes at offset %d" % (target, written, offset))
    # The patch is done and verified by this point, so nothing below may fail
    # the command. Counting the players is a courtesy; a roster whose tables
    # this reader cannot walk still installed correctly, and raising here would
    # report a completed write as an error and invite someone to run it again.
    try:
        table = madden_tdb.Database(roster, 0).table("PLAY")
    except madden_tdb.TdbError as exc:
        print("  (could not count players: %s)" % exc)
    else:
        print("  %d players now on the disc; the game boots with them, "
              "offline, with no server." % table.record_count)
    return 0


if __name__ == "__main__":
    sys.exit(main())

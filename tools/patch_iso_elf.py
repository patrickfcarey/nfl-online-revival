"""Put a baked ELF back into the disc image, where the console will find it.

`tools/bake_pnach.py` writes the patched executable; this drops it into the
image. Together they are the ship loop: pnach for iteration, a baked ISO for
the product (`docs/pnach-to-iso-pipeline.md`).

WHY THIS IS SAFE TO DO IN PLACE

A word patch does not change the executable's length, so the ELF that comes out
of the baker is byte-for-byte the same size as the one on the disc. That means
its bytes can be overwritten where they lie: no file moves, no directory record
edits, no ISO rebuild, and the image stays identical everywhere else -- exactly
the trick `patch_iso_roster.py` uses for a roster.

The size check is the whole safety argument, so the tool refuses on a mismatch
rather than truncating, spilling into the next file, or leaving a tail of the
old executable behind. (A *larger* ELF -- new caves past `filesz` -- is Phase
P2, and needs the directory record repointed. Deliberately not attempted here.)

FINDING THE FILE

`locate()` follows the prior art: ask `xorriso` first, and when it is not
installed -- which it very often is not -- fall back to reading ISO9660's own
directory records. The fallback is not a heuristic scan: it walks the primary
volume descriptor at sector 16 to the root directory and reads the extent and
length the filesystem itself records, which is the same pair xorriso reports.
A missing tool that makes the patch quietly do nothing is worse than one that
fails loudly; the roster tool learned that by exiting 0 after copying 3.2 GB
and patching nothing.

Usage::

    python3 tools/patch_iso_elf.py game.iso baked.elf -o patched.iso
    python3 tools/patch_iso_elf.py game.iso baked.elf --in-place
    python3 tools/patch_iso_elf.py game.iso --extract stock.elf
"""

from __future__ import annotations

import argparse
import shutil
import struct
import sys
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# One implementation of the xorriso call and one PatchError, not two: this tool
# is a sibling of the roster patcher and shares its posture about both.
from tools.patch_iso_roster import (  # noqa: E402
    SECTOR, PatchError, _locate_with_xorriso,
)

#: Where ISO9660 puts the primary volume descriptor, always.
PVD_SECTOR = 16
#: Offset of the root directory record inside the PVD.
ROOT_RECORD = 156
#: Directory record flag bit 1: this entry is a directory.
FLAG_DIRECTORY = 0x02
#: The boot executable when SYSTEM.CNF cannot be read (this game's).
DEFAULT_BOOT = "SLUS_207.52;1"
#: How deep the directory walk will recurse. A PS2 boot ELF is at the root.
MAX_DEPTH = 4


def _normalise(name: str) -> str:
    """`/SLUS_207.52;1` and `slus_207.52` compare equal.

    ISO9660 stores names uppercase with a `;version` suffix, but everyone
    writes them the way the docs do.
    """
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    return name.split(";", 1)[0].upper()


# --------------------------------------------------------------------------
# ISO9660, the parts needed to say where one file lives
# --------------------------------------------------------------------------

def _records(extent: bytes) -> Iterator[Tuple[str, int, int, int]]:
    """(name, lba, size, flags) for each record in a directory extent.

    Records never straddle a sector: a zero length byte means "nothing more in
    this sector", so the walk jumps to the next one rather than stopping.
    """
    position = 0
    while position < len(extent):
        length = extent[position]
        if length == 0:
            position = (position // SECTOR + 1) * SECTOR
            continue
        if position + length > len(extent) or length < 33:
            return
        record = extent[position:position + length]
        lba = struct.unpack_from("<I", record, 2)[0]
        size = struct.unpack_from("<I", record, 10)[0]
        flags = record[25]
        name_length = record[32]
        raw = record[33:33 + name_length]
        if raw in (b"\x00", b"\x01"):        # this directory, and its parent
            position += length
            continue
        yield raw.decode("latin-1"), lba, size, flags
        position += length


def _read(handle, lba: int, size: int) -> bytes:
    handle.seek(lba * SECTOR)
    return handle.read(size)


def _locate_by_directory(iso: Path, name: str) -> Tuple[int, int]:
    """(byte offset, size) of a file, read out of the image's own directory.

    Breadth-first from the root, because the boot executable is at the root and
    a deep recursion into a data-heavy disc buys nothing.
    """
    wanted = _normalise(name)
    with open(iso, "rb") as handle:
        pvd = _read(handle, PVD_SECTOR, SECTOR)
        if len(pvd) < SECTOR or pvd[1:6] != b"CD001" or pvd[0] != 1:
            raise PatchError(
                "%s has no ISO9660 primary volume descriptor at sector %d -- "
                "is this a disc image?" % (iso, PVD_SECTOR))
        root = pvd[ROOT_RECORD:ROOT_RECORD + 34]
        pending = [(struct.unpack_from("<I", root, 2)[0],
                    struct.unpack_from("<I", root, 10)[0], 0)]
        seen = set()
        while pending:
            lba, size, depth = pending.pop(0)
            if (lba, size) in seen or size == 0:
                continue
            seen.add((lba, size))
            extent = _read(handle, lba, size)
            children = []
            for entry, child_lba, child_size, flags in _records(extent):
                if flags & FLAG_DIRECTORY:
                    if depth < MAX_DEPTH:
                        children.append((child_lba, child_size, depth + 1))
                elif _normalise(entry) == wanted:
                    return child_lba * SECTOR, child_size
            pending.extend(children)
    raise PatchError("%s is not in %s -- the image's directory does not list "
                     "it" % (name, iso))


def locate(iso: Path, name: str = DEFAULT_BOOT) -> Tuple[int, int]:
    """(byte offset, size) of a file inside the image.

    xorriso when it is installed, the image's own directory records when it is
    not. Both answer with the extent and length the filesystem recorded, so the
    two agree; the fallback exists because a tool that silently does nothing is
    the worse failure.
    """
    plain = _normalise(name)
    for candidate in ("/" + plain + ";1", "/" + plain):
        try:
            return _locate_with_xorriso(iso, candidate)
        except PatchError:
            continue
    return _locate_by_directory(iso, name)


def read_file(iso: Path, name: str = DEFAULT_BOOT) -> bytes:
    offset, size = locate(iso, name)
    with open(iso, "rb") as handle:
        handle.seek(offset)
        return handle.read(size)


def boot_name(iso: Path) -> str:
    """The executable SYSTEM.CNF boots, or this game's, if it cannot be read.

    `BOOT2 = cdrom0:\\SLUS_207.52;1` -- the console reads this line and so does
    the emulator, which makes it the only authority on which file matters.
    """
    try:
        text = read_file(iso, "SYSTEM.CNF;1").decode("latin-1")
    except (PatchError, OSError):
        return DEFAULT_BOOT
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip().upper() == "BOOT2":
            candidate = value.strip().replace("\\", "/").rsplit("/", 1)[-1]
            if candidate:
                return candidate
    return DEFAULT_BOOT


# --------------------------------------------------------------------------
# the patch
# --------------------------------------------------------------------------

def patch(iso: Path, elf: bytes, name: Optional[str] = None) -> Tuple[int, int]:
    """Overwrite the boot executable in place. Returns (offset, bytes written)."""
    target = name or boot_name(iso)
    offset, size = locate(iso, target)
    with open(iso, "rb") as handle:
        handle.seek(offset)
        current = handle.read(size)
    if current[:4] != b"\x7fELF":
        raise PatchError(
            "what is at LBA %d is not an ELF (%r) -- refusing to write over a "
            "file this tool cannot identify" % (offset // SECTOR, current[:4]))
    if elf[:4] != b"\x7fELF":
        raise PatchError("the replacement is not an ELF (magic %r)" % elf[:4])
    if len(elf) != size:
        raise PatchError(
            "the ELF is %d bytes but %s on the disc is %d. They must match "
            "exactly: this writes the bytes where they lie, and anything else "
            "would spill into the next file or leave the tail of the old "
            "executable behind. A grown ELF needs the directory record "
            "repointed (Phase P2), which this tool does not do."
            % (len(elf), target, size))
    with open(iso, "r+b") as handle:
        handle.seek(offset)
        handle.write(elf)
    return offset, len(elf)


def verify(iso: Path, elf: bytes, name: Optional[str] = None) -> bool:
    """Read it back off the disc, to be sure rather than hopeful."""
    return read_file(iso, name or boot_name(iso)) == elf


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write a baked executable into a PS2 disc image, so the "
                    "game boots patched with no cheat engine running.")
    parser.add_argument("iso")
    parser.add_argument("elf", nargs="?",
                        help="the patched executable, from tools/bake_pnach.py")
    parser.add_argument("-o", "--output",
                        help="write a patched copy here, leaving the original "
                             "untouched (needs room for another copy)")
    parser.add_argument("--in-place", action="store_true",
                        help="modify the image given. There is no undo.")
    parser.add_argument("--name",
                        help="the file to replace (default: whatever "
                             "SYSTEM.CNF's BOOT2 names)")
    parser.add_argument("--extract", metavar="PATH",
                        help="write the image's current executable here and "
                             "stop -- the byte-diff arm of verification")
    args = parser.parse_args(argv)

    source = Path(args.iso)
    if not source.exists():
        print("error: no image at %s" % source, file=sys.stderr)
        return 2

    if args.extract:
        try:
            data = read_file(source, args.name or boot_name(source))
            Path(args.extract).write_bytes(data)
        except (PatchError, OSError) as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
        print("%s: %d bytes extracted from %s"
              % (args.extract, len(data), source))
        return 0

    if not args.elf:
        print("error: give the patched ELF to write (or --extract PATH)",
              file=sys.stderr)
        return 2
    if not args.output and not args.in_place:
        print("error: give -o OUTPUT, or --in-place to modify this image",
              file=sys.stderr)
        return 2

    try:
        elf = Path(args.elf).read_bytes()
    except OSError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    target = source
    if args.output:
        target = Path(args.output)
        if target.exists() and target.samefile(source):
            print("error: -o names the input image; use --in-place if that is "
                  "what you mean", file=sys.stderr)
            return 2
        print("copying %s -> %s (%.1f GB)"
              % (source, target, source.stat().st_size / 1e9))
        shutil.copy2(source, target)

    name = args.name or boot_name(target)
    try:
        offset, written = patch(target, elf, name)
    except PatchError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if not verify(target, elf, name):
        print("error: the image does not read back what was written",
              file=sys.stderr)
        return 2

    print("%s: %s replaced -- %d bytes at offset %d (LBA %d), image size "
          "unchanged" % (target, name, written, offset, offset // SECTOR))
    print("  the CRC has moved off 14F8B841: old savestates and per-game "
          "settings no longer match this build (docs/pnach-to-iso-pipeline.md, "
          "T2/T3). Clear the cheats dir before validating the bake (T4).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

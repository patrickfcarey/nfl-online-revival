"""Put a patched `default.xbe` back into the Xbox disc image.

`tools/patch_xbe.py` writes the executable; this drops it into the XISO, the way
`tools/patch_iso_elf.py` drops a baked ELF into the PS2 image. Together they are
the Xbox ship loop (`docs/pnach-to-xbe-pipeline.md` §8).

WHY THIS IS SAFE TO DO IN PLACE

A same-size executable can be overwritten where it lies: no file moves, no
directory record is edited, no image is rebuilt, and every other byte of the 3.11
GB dump stays identical. `default.xbe` is 4,890,624 bytes = exactly 2,388 sectors
of 2,048, so its extent is whole-sector and the arithmetic has no remainder to
get wrong. The size check is the entire safety argument, so a mismatch is a
refusal rather than a truncation, a spill into the next file, or a tail of the
old executable left behind.

⚠ THE TRAP THIS TOOL EXISTS TO GUARD, MEASURED ON THE OPERATOR'S IMAGE

The two sectors after `default.xbe` (2653-2654) look like padding, and a naive
"the next *file* starts at 2655" calculation reports 4,096 free bytes of growth
headroom. They are **the `/DATA/` directory table** -- 66 of the disc's 67
records live there. Growing the XBE in place would overwrite the filesystem and
leave an image that still mounts, still boots, and cannot find a single asset.
So this tool's occupancy model counts directory tables as occupied space, the
same as files, and asserts the write span overlaps nothing else before it writes
a byte. `headroom()` on this image returns 0 sectors, blocked by `/DATA`.

THE GROWTH PATH -- designed, checked for, deliberately not implemented

Any cave means a new XBE section means a bigger file, and in-place growth is
impossible (above). The design recorded in §8.1, which `growth_plan()` computes
but does not execute:

1. Pad the grown XBE to a whole sector and **append it at the end of the image**
   (the image is 1,520,832 sectors exactly -- no partial tail sector to fix up).
2. **Repoint its directory record: two u32s, eight bytes total** -- the new
   `start_sector` at record+4 and the **true byte size** (not the padded length)
   at record+8. The reader uses `size` for extraction, so a padded value there
   corrupts every downstream byte-diff.
3. Leave the vacated 2,388 sectors as garbage. The image is 100% packed by
   construction; there is nothing to reclaim and a relayout to lose.

There is no ISO-level checksum, no volume-size field to keep in step, and no
other record references `default.xbe` -- which is why the growth path is eight
bytes of structural edit and not a rebuild. It still needs its own acceptance
test before anyone runs it on the operator's only dump.

Usage::

    python3 tools/patch_xiso.py game.iso patched.xbe -o out.iso
    python3 tools/patch_xiso.py game.iso patched.xbe --in-place
    python3 tools/patch_xiso.py game.iso --extract stock.xbe
    python3 tools/patch_xiso.py game.iso --audit          # layout, no writing
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path
from typing import Iterator, List, NamedTuple, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recon import xdvdfs  # noqa: E402
# One definition of the XDVDFS record layout, not two: these are the reader's
# own constants, and a private copy here is how the two would drift apart.
from recon.xdvdfs import (  # noqa: E402
    _ENTRY_ALIGN, _ENTRY_HEADER_SIZE, _ENTRY_STRUCT, _NO_CHILD, _PAD_HEADER,
)

SECTOR = xdvdfs.SECTOR_SIZE
XBE_MAGIC = b"XBEH"
DEFAULT_NAME = "default.xbe"

#: Offsets of the two fields a future repoint would rewrite, inside a record.
START_SECTOR_FIELD = 4
SIZE_FIELD = 8


class PatchError(Exception):
    """The image is not what we think it is, or the write would not be safe."""


class Record(NamedTuple):
    """One directory record, with the file offset of the record itself.

    `Image.entries()` gives the file's extent; a repoint needs the *record's*
    address, which is why this walk exists alongside the reader's.
    """

    path: str
    start_sector: int
    size: int
    attributes: int
    table_sector: int
    table_offset: int           # byte offset inside that directory table
    offset: int                 # file offset of the record itself

    @property
    def is_directory(self) -> bool:
        return bool(self.attributes & xdvdfs.ATTR_DIRECTORY)

    @property
    def start_field(self) -> int:
        return self.offset + START_SECTOR_FIELD

    @property
    def size_field(self) -> int:
        return self.offset + SIZE_FIELD

    @property
    def sectors(self) -> int:
        return _sectors(self.size)

    @property
    def end_sector(self) -> int:
        return self.start_sector + self.sectors


class Extent(NamedTuple):
    """A sector range something owns. Directory tables count, and that is why."""

    start_sector: int
    sectors: int
    label: str

    @property
    def end_sector(self) -> int:
        return self.start_sector + self.sectors


class GrowthPlan(NamedTuple):
    """What a size change would require. Computed, reported, never executed."""

    current_size: int
    new_size: int
    same_size: bool
    headroom_sectors: int
    blocked_by: Optional[str]
    append_sector: int
    padded_size: int
    record_offset: int


def _sectors(size: int) -> int:
    return (size + SECTOR - 1) // SECTOR


# --------------------------------------------------------------------------
# the directory, with record addresses
# --------------------------------------------------------------------------

def _walk_table(table: bytes, sector: int
                ) -> Iterator[Tuple[int, int, int, int, str]]:
    """(offset, start_sector, size, attributes, name) for each record.

    The same btree walk as the reader, keeping the offset the reader discards.
    0xFF fill is padding only when the whole 14-byte header is 0xFF -- a leaf
    written with 0xFFFF children starts with four such bytes and is a real file
    (`recon/xdvdfs.py`, the case that caught the reader's first draft).
    """
    pending = [0]
    seen = set()
    while pending:
        offset = pending.pop()
        if offset in seen:
            raise PatchError("directory table at sector %d has a cycle at "
                             "offset %d" % (sector, offset))
        seen.add(offset)
        if offset + _ENTRY_HEADER_SIZE > len(table):
            continue
        if table[offset:offset + _ENTRY_HEADER_SIZE] == _PAD_HEADER:
            continue
        left, right, start, size, attributes, name_length = \
            _ENTRY_STRUCT.unpack_from(table, offset)
        name_start = offset + _ENTRY_HEADER_SIZE
        if name_length == 0 or name_start + name_length > len(table):
            raise PatchError("directory table at sector %d: the record at "
                             "offset %d declares a %d-byte name that does not "
                             "fit" % (sector, offset, name_length))
        for child in (left, right):
            if child in _NO_CHILD:
                continue
            child_offset = child * _ENTRY_ALIGN
            if child_offset + _ENTRY_HEADER_SIZE <= len(table):
                pending.append(child_offset)
        yield (offset, start, size,
               attributes,
               table[name_start:name_start + name_length].decode("latin-1"))


def find_record(image: xdvdfs.Image, handle, path: str = DEFAULT_NAME) -> Record:
    """The directory record for *path*, and where that record lives in the file.

    Case-insensitive, and it walks `/DATA/...` too, though the only caller wants
    the boot executable at the root.
    """
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    if not parts:
        raise PatchError("no path given")
    sector, size, prefix = image.root_sector, image.root_size, ""
    for depth, wanted in enumerate(parts):
        handle.seek(image.base + sector * SECTOR)
        table = handle.read(size)
        if len(table) < size:
            raise PatchError("the directory table at sector %d runs past the "
                             "end of the image" % sector)
        matches = [record for record in _walk_table(table, sector)
                   if record[4].upper() == wanted.upper()]
        if not matches:
            raise PatchError("%s is not in the image -- its own directory does "
                             "not list it" % path)
        if len(matches) > 1:
            raise PatchError("%s appears %d times in the directory table at "
                             "sector %d; refusing to guess which record is the "
                             "file" % (wanted, len(matches), sector))
        offset, start, entry_size, attributes, name = matches[0]
        record = Record(path=prefix + "/" + name, start_sector=start,
                        size=entry_size, attributes=attributes,
                        table_sector=sector, table_offset=offset,
                        offset=image.base + sector * SECTOR + offset)
        if depth == len(parts) - 1:
            return record
        if not record.is_directory:
            raise PatchError("%s is a file, not a directory" % record.path)
        sector, size, prefix = start, entry_size, record.path
    raise PatchError("unreachable")            # pragma: no cover


def check_record_fields(handle, image: xdvdfs.Image, record: Record) -> None:
    """Re-read the two u32s a repoint would rewrite, and confirm they are those.

    The measured layout (`start_sector` at record+4, `size` at record+8) is the
    whole basis of the future eight-byte growth edit. Deriving it once and
    trusting it forever is how this project has been wrong before, so it is
    re-read from the image every run.
    """
    handle.seek(record.offset)
    raw = handle.read(_ENTRY_HEADER_SIZE)
    if len(raw) < _ENTRY_HEADER_SIZE:
        raise PatchError("the directory record for %s runs past the end of the "
                         "image" % record.path)
    start = struct.unpack_from("<I", raw, START_SECTOR_FIELD)[0]
    size = struct.unpack_from("<I", raw, SIZE_FIELD)[0]
    if (start, size) != (record.start_sector, record.size):
        raise PatchError(
            "the directory record for %s at file offset %#x does not read back "
            "as the walk decoded it (+%d = %d, +%d = %d; expected %d, %d). The "
            "record layout is the basis of every offset here."
            % (record.path, record.offset, START_SECTOR_FIELD, start,
               SIZE_FIELD, size, record.start_sector, record.size))


# --------------------------------------------------------------------------
# occupancy -- what owns which sectors, directory tables included
# --------------------------------------------------------------------------

def occupancy(image: xdvdfs.Image) -> List[Extent]:
    """Every sector range the filesystem owns, sorted.

    Directory tables are in here on purpose: `/DATA/`'s table is what sits in the
    "free space" immediately after `default.xbe`, and a census that only counts
    files reports 4 KB of growth headroom that would destroy the disc.
    """
    extents = [Extent(xdvdfs.VOLUME_SECTOR, 1, "the volume descriptor"),
               Extent(image.root_sector, _sectors(image.root_size),
                      "the root directory table")]
    for entry in image.entries():
        label = (("%s (directory table)" % entry.path) if entry.is_directory
                 else entry.path)
        extents.append(Extent(entry.start_sector, _sectors(entry.size), label))
    return sorted(extents)


def headroom(extents: Sequence[Extent], record: Record
             ) -> Tuple[int, Optional[str]]:
    """(free sectors after the file, what ends that run) -- 0 and `/DATA` here.

    When nothing at all follows the file this reports 0 rather than "to the end
    of the image": the growth path appends and repoints, it never extends a file
    in place, so counting trailing slack as headroom would only invite the edit
    this whole module exists to refuse.
    """
    following = [extent for extent in extents
                 if extent.start_sector >= record.end_sector
                 and extent.label != record.path]
    if not following:
        return (0, None)
    nearest = min(following, key=lambda extent: extent.start_sector)
    return (nearest.start_sector - record.end_sector, nearest.label)


def assert_span_exclusive(extents: Sequence[Extent], record: Record,
                          length: int) -> None:
    """The bytes about to be written may touch nothing but this file's extent.

    Same-size writes pass this trivially; it exists so that the day someone adds
    the growth path, the assertion fires before the `/DATA/` table is eaten
    rather than after.
    """
    first = record.start_sector
    last = first + _sectors(length)
    for extent in extents:
        if extent.label == record.path and extent.start_sector == first:
            continue
        if extent.start_sector < last and first < extent.end_sector:
            raise PatchError(
                "writing %d bytes at sector %d would run into %s (sectors "
                "%d-%d).%s"
                % (length, first, extent.label, extent.start_sector,
                   extent.end_sector - 1,
                   " That extent is a directory table, not padding: "
                   "overwriting it leaves an image that still mounts and "
                   "cannot find a single asset -- on the retail disc the two "
                   "sectors right after default.xbe are exactly that."
                   if "directory table" in extent.label else
                   " Growth means appending the file and repointing its "
                   "record, never spilling into the next extent."))


def growth_plan(image: xdvdfs.Image, extents: Sequence[Extent], record: Record,
                new_size: int) -> GrowthPlan:
    """What a size change would require. This tool computes it and stops.

    TODO(growth): implementing this means (1) appending the padded XBE at
    `append_sector`, which is the end of the image, (2) writing `new_start_sector`
    as a u32 at `record.start_field` and the **true** byte size at
    `record.size_field`, and (3) leaving the old extent as garbage. Eight bytes
    of structural edit; see the module docstring and §8.1. It needs its own
    acceptance test -- extract the file back out of the rewritten image and
    byte-compare -- before it is ever pointed at the operator's dump.
    """
    free, blocker = headroom(extents, record)
    image_size = image.size if image.size is not None else 0
    return GrowthPlan(
        current_size=record.size,
        new_size=new_size,
        same_size=new_size == record.size,
        headroom_sectors=free,
        blocked_by=blocker,
        append_sector=_sectors(image_size),
        padded_size=_sectors(new_size) * SECTOR,
        record_offset=record.offset,
    )


# --------------------------------------------------------------------------
# the patch
# --------------------------------------------------------------------------

def _open_image(iso: Path) -> xdvdfs.Image:
    try:
        return xdvdfs.Image.open(str(iso))
    except xdvdfs.XdvdfsError as exc:
        raise PatchError("%s: %s" % (iso, exc))


def survey(iso: Path, name: str = DEFAULT_NAME) -> dict:
    """Everything the write depends on, read out of the image. Writes nothing."""
    with _open_image(iso) as image:
        with open(iso, "rb") as handle:
            record = find_record(image, handle, name)
            check_record_fields(handle, image, record)
            extents = occupancy(image)
            free, blocker = headroom(extents, record)
            handle.seek(image.base + record.start_sector * SECTOR)
            magic = handle.read(4)
        return {
            "image": str(iso),
            "image_size": image.size,
            "image_sectors": (image.size // SECTOR) if image.size else None,
            "partial_tail_sector": bool(image.size % SECTOR) if image.size else None,
            "base": image.base,
            "root_sector": image.root_sector,
            "root_size": image.root_size,
            "entries": len(extents) - 2,
            "file": record.path,
            "start_sector": record.start_sector,
            "size": record.size,
            "sectors": record.sectors,
            "whole_sector_extent": record.size % SECTOR == 0,
            "file_offset": image.base + record.start_sector * SECTOR,
            "magic": magic.decode("latin-1", "replace"),
            "record_offset": record.offset,
            "start_sector_field": record.start_field,
            "size_field": record.size_field,
            "headroom_sectors": free,
            "headroom_blocked_by": blocker,
        }


def patch(iso: Path, xbe: bytes, name: str = DEFAULT_NAME) -> Record:
    """Overwrite the executable in place. Returns the record it wrote over."""
    if xbe[:4] != XBE_MAGIC:
        raise PatchError("the replacement is not an XBE (magic %r, expected "
                         "%r)" % (xbe[:4], XBE_MAGIC))
    with _open_image(iso) as image:
        with open(iso, "rb") as handle:
            record = find_record(image, handle, name)
            check_record_fields(handle, image, record)
            extents = occupancy(image)
            handle.seek(image.base + record.start_sector * SECTOR)
            current = handle.read(4)

        if record.is_directory:
            raise PatchError("%s is a directory" % record.path)
        if current != XBE_MAGIC:
            raise PatchError(
                "what is at sector %d is not an XBE (%r) -- refusing to write "
                "over a file this tool cannot identify"
                % (record.start_sector, current))

        plan = growth_plan(image, extents, record, len(xbe))
        if not plan.same_size:
            raise PatchError(
                "the XBE is %d bytes but %s on the disc is %d. They must match "
                "exactly: this writes the bytes where they lie. There are %d "
                "free sectors after it%s, so it cannot simply grow -- a grown "
                "XBE must be appended at sector %d and its directory record "
                "repointed (two u32s at file offset %#x), which this tool "
                "deliberately does not do yet."
                % (len(xbe), record.path, record.size, plan.headroom_sectors,
                   "" if plan.blocked_by is None
                   else " (%s starts there)" % plan.blocked_by,
                   plan.append_sector, plan.record_offset))
        assert_span_exclusive(extents, record, len(xbe))

        start = image.base + record.start_sector * SECTOR
        if image.size is not None and start + len(xbe) > image.size:
            raise PatchError("%s runs past the end of the image" % record.path)

    with open(iso, "r+b") as handle:
        handle.seek(start)
        handle.write(xbe)
        handle.flush()
    return record


def read_file(iso: Path, name: str = DEFAULT_NAME) -> bytes:
    with _open_image(iso) as image:
        try:
            entry = image.find(name)
        except xdvdfs.XdvdfsError as exc:
            raise PatchError("%s" % exc)
        return image.read(entry)


def verify(iso: Path, xbe: bytes, record: Record,
           image_size: Optional[int] = None,
           name: str = DEFAULT_NAME) -> List[str]:
    """Re-extract the executable from the produced image and byte-compare it.

    Also checks the two things a same-size write must not have done: change the
    image's length, or move the file's directory record.
    """
    problems: List[str] = []
    size = iso.stat().st_size
    if image_size is not None and size != image_size:
        problems.append("the image is %d bytes, it was %d -- a same-size write "
                        "must not change its length" % (size, image_size))
    with _open_image(iso) as image:
        with open(iso, "rb") as handle:
            try:
                fresh = find_record(image, handle, name)
            except PatchError as exc:
                return problems + ["%s" % exc]
            if (fresh.start_sector, fresh.size, fresh.offset) != \
                    (record.start_sector, record.size, record.offset):
                problems.append(
                    "the directory record moved or changed: sector %d size %d "
                    "at %#x, was sector %d size %d at %#x"
                    % (fresh.start_sector, fresh.size, fresh.offset,
                       record.start_sector, record.size, record.offset))
            handle.seek(image.base + fresh.start_sector * SECTOR)
            got = handle.read(fresh.size)
    if len(got) != len(xbe):
        problems.append("read back %d bytes, wrote %d" % (len(got), len(xbe)))
        return problems
    if got != xbe:
        first = next(index for index in range(len(xbe)) if got[index] != xbe[index])
        differing = sum(1 for index in range(len(xbe)) if got[index] != xbe[index])
        problems.append("the image does not read back what was written: %d "
                        "byte(s) differ, first at file offset %#x of the XBE "
                        "(%02X, wrote %02X)"
                        % (differing, first, got[first], xbe[first]))
    return problems


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print_survey(facts: dict) -> None:
    print("%s: %d bytes (%s sectors%s), base %#x, %d entries"
          % (facts["image"], facts["image_size"], facts["image_sectors"],
             ", partial tail sector" if facts["partial_tail_sector"] else "",
             facts["base"], facts["entries"]))
    print("  %s: sector %d, %d bytes = %d sectors%s, file offset %#x, magic %r"
          % (facts["file"], facts["start_sector"], facts["size"],
             facts["sectors"],
             " exactly" if facts["whole_sector_extent"] else " (partial last)",
             facts["file_offset"], facts["magic"]))
    print("  its record: file offset %#x  (start_sector u32 at %#x, size u32 at "
          "%#x)" % (facts["record_offset"], facts["start_sector_field"],
                    facts["size_field"]))
    print("  headroom after it: %d sector%s%s"
          % (facts["headroom_sectors"],
             "" if facts["headroom_sectors"] == 1 else "s",
             "" if facts["headroom_blocked_by"] is None
             else " -- %s starts there, so growth means append + repoint"
                  % facts["headroom_blocked_by"]))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write a patched default.xbe into an Xbox disc image "
                    "(XISO), same-size only, and prove it read back.",
        epilog="The two sectors after default.xbe on this disc are the /DATA/ "
               "directory table, not padding: growth means appending the file "
               "and repointing its record, which this tool refuses to fake.")
    parser.add_argument("iso")
    parser.add_argument("xbe", nargs="?",
                        help="the patched executable, from tools/patch_xbe.py")
    parser.add_argument("-o", "--output",
                        help="write a patched copy here, leaving the original "
                             "untouched (needs room for another copy)")
    parser.add_argument("--in-place", action="store_true",
                        help="modify the image given. There is no undo.")
    parser.add_argument("--name", default=DEFAULT_NAME,
                        help="the file to replace (default: %s)" % DEFAULT_NAME)
    parser.add_argument("--extract", metavar="PATH",
                        help="write the image's current executable here and "
                             "stop -- the byte-diff arm of verification")
    parser.add_argument("--audit", action="store_true",
                        help="report the layout the write depends on and stop")
    parser.add_argument("--manifest",
                        help="write a JSON record of the write here "
                             "('-' for stdout)")
    args = parser.parse_args(argv)

    source = Path(args.iso)
    if not source.exists():
        print("error: no image at %s" % source, file=sys.stderr)
        return 2

    if args.audit:
        try:
            facts = survey(source, args.name)
        except (PatchError, OSError) as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
        _print_survey(facts)
        if args.manifest:
            text = json.dumps(facts, indent=2)
            if args.manifest == "-":
                print(text)
            else:
                Path(args.manifest).write_text(text + "\n", encoding="utf-8")
        return 0

    if args.extract:
        try:
            with _open_image(source) as image:
                entry = image.find(args.name)
                written = image.extract(entry, args.extract)
        except (PatchError, xdvdfs.XdvdfsError, OSError) as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
        print("%s: %d bytes extracted from %s" % (args.extract, written, source))
        return 0

    if not args.xbe:
        print("error: give the patched XBE to write (or --extract PATH, or "
              "--audit)", file=sys.stderr)
        return 2
    if not args.output and not args.in_place:
        print("error: give -o OUTPUT, or --in-place to modify this image",
              file=sys.stderr)
        return 2

    try:
        xbe = Path(args.xbe).read_bytes()
    except OSError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    target = source
    if args.output:
        target = Path(args.output)
        if target.resolve() == source.resolve() or (
                target.exists() and target.samefile(source)):
            print("error: -o names the input image; use --in-place if that is "
                  "what you mean", file=sys.stderr)
            return 2
        print("copying %s -> %s (%.2f GB)"
              % (source, target, source.stat().st_size / 1e9))
        try:
            shutil.copy2(source, target)
        except OSError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2

    before = target.stat().st_size
    try:
        record = patch(target, xbe, args.name)
    except (PatchError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        print("nothing was written." if target is source
              else "the copy is unmodified.", file=sys.stderr)
        return 2

    problems = verify(target, xbe, record, image_size=before, name=args.name)
    if problems:
        print("error: the image does not read back what was written:",
              file=sys.stderr)
        for problem in problems:
            print("  %s" % problem, file=sys.stderr)
        return 2

    print("%s: %s replaced -- %d bytes at sector %d (file offset %#x), image "
          "size unchanged (%d bytes)"
          % (target, record.path, len(xbe), record.start_sector,
             record.start_sector * SECTOR, before))
    print("  verified: the file re-extracted from the image is byte-identical "
          "to %s, and its directory record at %#x is untouched"
          % (args.xbe, record.offset))

    if args.manifest:
        manifest = {
            "tool": "tools/patch_xiso.py",
            "manifest_version": 1,
            "image": str(target),
            "source_image": str(source),
            "image_size": before,
            "in_place": bool(args.in_place),
            "file": record.path,
            "start_sector": record.start_sector,
            "size": record.size,
            "file_offset": record.start_sector * SECTOR,
            "record_offset": record.offset,
            "xbe": str(args.xbe),
            "xbe_sha256": _sha256(xbe),
            # No hash of the image: it is 3.11 GB and hashing it would cost more
            # than the write. The XBE's hash plus the extent is the revert
            # record, since nothing else on the image changed.
            "note": "same-size in-place overwrite; no directory record edited",
        }
        text = json.dumps(manifest, indent=2)
        if args.manifest == "-":
            print(text)
        else:
            try:
                Path(args.manifest).write_text(text + "\n", encoding="utf-8")
            except OSError as exc:
                print("error: manifest: %s" % exc, file=sys.stderr)
                return 2
            print("manifest: %s" % args.manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())

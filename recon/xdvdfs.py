"""A dependency-free reader for Xbox game discs (XDVDFS / "XISO").

Enough of the original Xbox's disc filesystem to list and extract the data tree
and the executable out of a disc image, with no extract-xiso, no mounting, and
no third-party package. Written for the Madden NFL 2004 port investigation
(``docs/xbox-madden-2004-plan.md``), where the whole thesis is that *the data
survives compilation* -- so getting at the data is the first instrument needed.

THE FORMAT, AS OBSERVED
=======================

Every offset below was read out of the operator's own dump
(``Madden NFL 2004 (USA).xiso.iso``, 3,114,663,936 B) rather than carried in
from a spec.

Sectors are 2048 bytes. The **volume descriptor** occupies sector 32 of the
game partition, so it sits at ``base + 0x10000`` in the file, and it opens and
closes with the same 20-byte magic::

    00010000  4d 49 43 52 4f 53 4f 46 54 2a 58 42 4f 58 2a 4d  |MICROSOFT*XBOX*M|
    00010010  45 44 49 41 08 01 00 00 30 00 00 00 60 ba 65 1b  |EDIA....0...`.e.|

``base`` is 0 for a bare game-partition rip like that one; a full-disc dump
puts the partition further in, which is why :func:`find_base` probes a list of
known offsets instead of assuming. The two u32s after the magic are the root
directory's start sector (``0x108`` = 264) and its size in bytes (``0x30`` =
48); an 8-byte Windows FILETIME follows, then zero padding to the trailing
magic at ``0x7EC``.

A **directory table** is a binary tree packed into that directory's own bytes.
Each entry is 4-byte aligned::

    u16 left      offset to the left child, in 4-byte WORDS from the table start
    u16 right     offset to the right child, same units
    u32 start_sector
    u32 size      in bytes
    u8  attributes    0x10 = directory, 0x01 read-only, 0x02 hidden, 0x20 archive
    u8  name_length
    u8  name[name_length]     (then pad to the next 4-byte boundary)

The whole root table, at sector 264, is 48 bytes::

    00000000  00 00 05 00 5d 0a 00 00 00 10 00 00 10 04 44 41  |....].........DA|
    00000010  54 41 ff ff 00 00 00 00 09 01 00 00 00 a0 4a 00  |TA............J.|
    00000020  20 0b 64 65 66 61 75 6c 74 2e 78 62 65 ff ff ff  | .default.xbe...|

-- ``DATA``, a directory (attr ``0x10``) at sector 0xA5D with a right child at
word 5 (byte 20), then at byte 20 ``default.xbe``, 0x4AA000 = 4,890,624 bytes at
sector 0x109 = 265, ``left = right = 0``.

**Padding is 0xFF, and it lives between entries, not only after them.** The
``ff ff`` at byte 18 is not a field of any record: the ``DATA`` entry's name
ends at byte 18 and the next entry must start 4-byte aligned, so two bytes of
fill sit in the gap. (Read hastily, those two bytes look like a ``left`` field
belonging to ``default.xbe``, which is how this reader's first draft came to
claim the disc used ``0xFFFF`` child pointers. It does not: across all 67
entries on this disc, every child field is either 0 or a real word offset --
``0xFFFF`` appears nowhere.) A reader that scans a table linearly and stops at
the first ``0xFF`` therefore stops after entry one; end-of-table is recognised
here by a full 14-byte header of ``0xFF``, and otherwise by bounds.

**The 0xFFFF question.** Descriptions of this format usually say a left-child
offset of ``0xFFFF`` terminates the table, and other authoring tools do write
``0xFFFF`` for "no child". Both spellings are accepted here, which is also why
the padding test reads the entire header rather than its first four bytes: a
leaf written the other way has ``0xFFFF`` in *both* child fields, and testing
four bytes alone would discard it as fill.

The closed-set check that the walk is right: it yields exactly the 66 files in
``extract/xbox/inventory.txt``, with matching sectors and sizes and no others,
and an independent linear scan of both tables finds the same 67 records.

Sizes are not rounded up to a sector, so the last sector of a file is partly
padding; extraction truncates to the declared size.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from typing import BinaryIO, Iterator, List, NamedTuple, Optional, Sequence

#: The volume-descriptor magic, present at both ends of that sector.
MAGIC = b"MICROSOFT*XBOX*MEDIA"

SECTOR_SIZE = 2048
#: The volume descriptor is sector 32 of the game partition.
VOLUME_SECTOR = 32
_VOLUME_OFFSET = VOLUME_SECTOR * SECTOR_SIZE     # 0x10000
_MAGIC_TAIL_OFFSET = SECTOR_SIZE - len(MAGIC)    # 0x7EC

#: Where the game partition can start inside a dump. 0 is a bare game-partition
#: rip (what we have); the others are the layouts full-disc dumps use. Probed in
#: order, so the common case costs one read.
KNOWN_BASES = (0, 0x18300000, 0x0FD90000, 0x02080000)

#: Directory-entry layout: left, right, start sector, size, attributes, name len.
_ENTRY_STRUCT = struct.Struct("<HHIIBB")
_ENTRY_HEADER_SIZE = _ENTRY_STRUCT.size          # 14
_ENTRY_ALIGN = 4

ATTR_READONLY = 0x01
ATTR_HIDDEN = 0x02
ATTR_SYSTEM = 0x04
ATTR_DIRECTORY = 0x10
ATTR_ARCHIVE = 0x20

#: A child offset meaning "there is no child". 0 is the documented value; 0xFFFF
#: is what this disc actually writes -- see the module docstring.
_NO_CHILD = (0x0000, 0xFFFF)

#: Directory tables are padded with 0xFF. Padding is recognised only when the
#: *whole* 14-byte header is 0xFF -- a leaf entry has 0xFFFF in both child
#: fields, so testing the first four bytes alone throws real files away.
_PAD_BYTE = 0xFF
_PAD_HEADER = bytes([_PAD_BYTE]) * _ENTRY_HEADER_SIZE

#: Guard rails. A directory table larger than this, or a tree deeper than this,
#: means we are decoding noise as structure rather than reading a real disc.
_MAX_DIRECTORY_BYTES = 4 * 1024 * 1024
_MAX_DEPTH = 32
_COPY_CHUNK = 1 << 20


class XdvdfsError(Exception):
    """The bytes are not the disc image we expect."""


class Entry(NamedTuple):
    """One file or directory, with its path already joined from the walk."""

    path: str
    start_sector: int
    size: int
    attributes: int

    @property
    def is_directory(self) -> bool:
        return bool(self.attributes & ATTR_DIRECTORY)

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    def offset(self, base: int = 0) -> int:
        """Byte offset of this entry's data inside the image file."""
        return base + self.start_sector * SECTOR_SIZE


def find_base(handle: BinaryIO, size: Optional[int] = None) -> int:
    """The image's game-partition base offset, or raise.

    Probes :data:`KNOWN_BASES` for the volume magic rather than assuming 0, so a
    full-disc dump works without the caller knowing which layout it has.
    """
    if size is None:
        size = _stream_size(handle)
    for base in KNOWN_BASES:
        if size is not None and base + _VOLUME_OFFSET + SECTOR_SIZE > size:
            continue
        handle.seek(base + _VOLUME_OFFSET)
        if handle.read(len(MAGIC)) == MAGIC:
            return base
    raise XdvdfsError(
        "no XDVDFS volume descriptor: %r not found at any of %s. Is this a "
        "raw disc image (not a compressed or split dump)?"
        % (MAGIC.decode("ascii"),
           ", ".join("%#x+%#x" % (b, _VOLUME_OFFSET) for b in KNOWN_BASES)))


class Image:
    """An open XDVDFS image: walk it, then read entries out of it."""

    def __init__(self, handle: BinaryIO, base: Optional[int] = None) -> None:
        self._handle = handle
        self.size = _stream_size(handle)
        self.base = find_base(handle, self.size) if base is None else base

        handle.seek(self.base + _VOLUME_OFFSET)
        descriptor = handle.read(SECTOR_SIZE)
        if len(descriptor) < SECTOR_SIZE or descriptor[:len(MAGIC)] != MAGIC:
            raise XdvdfsError("no volume descriptor at %#x"
                              % (self.base + _VOLUME_OFFSET))
        # The tail magic is the cheap way to catch a base that matched by luck.
        if descriptor[_MAGIC_TAIL_OFFSET:] != MAGIC:
            raise XdvdfsError(
                "volume descriptor at %#x has the head magic but not the tail "
                "magic at +%#x -- decoding it would be guesswork"
                % (self.base + _VOLUME_OFFSET, _MAGIC_TAIL_OFFSET))
        self.root_sector, self.root_size = struct.unpack_from("<II", descriptor, 0x14)
        #: Windows FILETIME of the image build, unconverted (informational).
        self.filetime = struct.unpack_from("<Q", descriptor, 0x1C)[0]

    # -- construction ------------------------------------------------------

    @classmethod
    def open(cls, path: str, base: Optional[int] = None) -> "Image":
        return cls(open(path, "rb"), base=base)

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "Image":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- walking -----------------------------------------------------------

    def entries(self) -> Iterator[Entry]:
        """Every file and directory.

        A directory's own records come first, then each subdirectory's contents.
        Order within one table follows the btree walk, not the sorted order the
        tree encodes; callers that want a stable listing should sort.
        """
        yield from self._walk_directory(self.root_sector, self.root_size, "", 0)

    def files(self) -> Iterator[Entry]:
        """Every file, skipping the directories themselves."""
        for entry in self.entries():
            if not entry.is_directory:
                yield entry

    def find(self, path: str) -> Entry:
        """The entry at *path* (``/DATA/DB_TEAMS.DAT``), case-insensitively."""
        wanted = "/" + path.strip("/").upper()
        for entry in self.entries():
            if entry.path.upper() == wanted:
                return entry
        raise XdvdfsError("no such entry on the image: %r" % (path,))

    def _walk_directory(self, sector: int, size: int, prefix: str,
                        depth: int) -> Iterator[Entry]:
        if size == 0:
            return
        if depth > _MAX_DEPTH:
            raise XdvdfsError("directory nesting deeper than %d at %r -- "
                              "refusing to recurse further" % (_MAX_DEPTH, prefix))
        if size > _MAX_DIRECTORY_BYTES:
            raise XdvdfsError("directory table at sector %d claims %d bytes; "
                              "that is not a directory" % (sector, size))
        table = self._read_at(sector * SECTOR_SIZE, size)
        if len(table) < size:
            raise XdvdfsError("directory table at sector %d runs past the end "
                              "of the image" % sector)

        subdirectories: List[Entry] = []
        # An explicit stack, not recursion: the tree is only a lookup index and
        # a pathological one should not blow the interpreter stack.
        pending = [0]
        seen = set()
        while pending:
            offset = pending.pop()
            if offset in seen:
                raise XdvdfsError("directory table at sector %d has a cycle at "
                                  "offset %d" % (sector, offset))
            seen.add(offset)
            entry = self._parse_entry(table, offset, sector, prefix)
            if entry is None:
                continue
            left, right, record = entry
            for child in (left, right):
                if child in _NO_CHILD:
                    continue
                child_offset = child * _ENTRY_ALIGN
                if child_offset + _ENTRY_HEADER_SIZE > len(table):
                    # A pointer outside the table is the end of the data, not a
                    # record: stop rather than decode padding as a file.
                    continue
                pending.append(child_offset)
            yield record
            if record.is_directory:
                subdirectories.append(record)

        for directory in subdirectories:
            yield from self._walk_directory(directory.start_sector, directory.size,
                                            directory.path, depth + 1)

    def _parse_entry(self, table: bytes, offset: int, sector: int, prefix: str):
        """(left, right, Entry) for the record at *offset*, or None if padding."""
        if offset + _ENTRY_HEADER_SIZE > len(table):
            return None
        if table[offset:offset + _ENTRY_HEADER_SIZE] == _PAD_HEADER:
            return None  # 0xFF fill, not a record
        left, right, start, size, attributes, name_length = \
            _ENTRY_STRUCT.unpack_from(table, offset)
        name_start = offset + _ENTRY_HEADER_SIZE
        if name_length == 0 or name_start + name_length > len(table):
            raise XdvdfsError(
                "directory table at sector %d: entry at offset %d declares a "
                "%d-byte name that does not fit" % (sector, offset, name_length))
        # latin-1 never raises; these names are ASCII in practice and a decode
        # error here would lose a whole subtree.
        name = table[name_start:name_start + name_length].decode("latin-1")
        return left, right, Entry(prefix + "/" + name, start, size, attributes)

    # -- reading -----------------------------------------------------------

    def read(self, entry: Entry, limit: Optional[int] = None) -> bytes:
        """*entry*'s bytes, at most *limit* of them.

        Files on this disc run to hundreds of megabytes; pass a limit unless the
        whole thing is genuinely wanted, or use :meth:`extract`.
        """
        if entry.is_directory:
            raise XdvdfsError("%s is a directory" % entry.path)
        want = entry.size if limit is None else min(entry.size, limit)
        return self._read_at(entry.start_sector * SECTOR_SIZE, want)

    def extract(self, entry: Entry, destination: str,
                limit: Optional[int] = None) -> int:
        """Copy *entry* to *destination* in chunks. Returns bytes written."""
        if entry.is_directory:
            raise XdvdfsError("%s is a directory" % entry.path)
        want = entry.size if limit is None else min(entry.size, limit)
        start = self.base + entry.start_sector * SECTOR_SIZE
        if self.size is not None and start + want > self.size:
            raise XdvdfsError("%s runs past the end of the image (%d bytes at "
                              "%#x, image is %d)" % (entry.path, want, start, self.size))
        self._handle.seek(start)
        written = 0
        with open(destination, "wb") as out:
            while written < want:
                chunk = self._handle.read(min(_COPY_CHUNK, want - written))
                if not chunk:
                    raise XdvdfsError("image ended %d bytes into %s"
                                      % (written, entry.path))
                out.write(chunk)
                written += len(chunk)
        return written

    def _read_at(self, offset: int, length: int) -> bytes:
        self._handle.seek(self.base + offset)
        return self._handle.read(length)


def _stream_size(handle: BinaryIO) -> Optional[int]:
    try:
        return os.fstat(handle.fileno()).st_size
    except (OSError, AttributeError, ValueError):
        # BytesIO and friends: fall back to seeking, and restore the position.
        try:
            here = handle.tell()
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(here)
            return size
        except (OSError, ValueError):
            return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_list(args: argparse.Namespace) -> int:
    with Image.open(args.image) as image:
        if not args.quiet:
            print("base %#x  root sector %d  root size %d"
                  % (image.base, image.root_sector, image.root_size), file=sys.stderr)
        count = 0
        for entry in image.entries():
            if entry.is_directory and not args.all:
                continue
            print("%s\t%d\t%d" % (entry.path, entry.start_sector, entry.size))
            count += 1
        if not args.quiet:
            print("(%d entries)" % count, file=sys.stderr)
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    with Image.open(args.image) as image:
        entry = image.find(args.path)
        destination = args.output or os.path.basename(entry.path)
        written = image.extract(entry, destination, limit=args.max_bytes)
        print("%s -> %s  %d byte(s)%s"
              % (entry.path, destination, written,
                 "" if written == entry.size else " (of %d; truncated)" % entry.size))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xdvdfs", description="List and extract files from an Xbox XISO image.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="print path/sector/size, TAB separated")
    p_list.add_argument("image")
    p_list.add_argument("--all", action="store_true",
                        help="include directories, not only files")
    p_list.add_argument("--quiet", action="store_true", help="no header or count")
    p_list.set_defaults(func=_cmd_list)

    p_get = sub.add_parser("extract", help="copy one file out of the image")
    p_get.add_argument("image")
    p_get.add_argument("path", help="e.g. /DATA/DB_TEAMS.DAT (case-insensitive)")
    p_get.add_argument("-o", "--output", help="destination (default: the basename)")
    p_get.add_argument("--max-bytes", type=int,
                       help="stop after N bytes -- for sampling a huge file's head")
    p_get.set_defaults(func=_cmd_extract)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except XdvdfsError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    except OSError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())

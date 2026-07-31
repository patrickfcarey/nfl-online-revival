"""Reading Madden NFL 2004's on-disc database (``DB_TEAMS.DAT``).

The file is two nested formats, and both had to be recovered by inspection --
neither is the ``DB``-magic layout that later Madden titles use directly.

**Outer: a TERF container.** A 64-byte header, then a ``DIR1`` block holding one
``(offset, size)`` pair per member. Offsets are relative to the *end* of the
directory block, not to the file, which is the one detail that makes the whole
thing decode. The 2004 disc ships 232 members: index 0 is the free-agent pool,
1-32 are the NFL teams in order, and the rest are historical squads.

**Inner: a standard Madden TDB.** ``DB`` magic, a table directory of 4-character
names and offsets, then per-table headers, field definitions and bit-packed
records. Field values are *not* byte-aligned -- ``PGID`` is 15 bits starting at
bit 317 of a 108-byte record -- and both the bit order within a byte and the bit
order within a field are least-significant-first.

The bit-extraction convention here matches the reader in
``NCAA-Draft-Class-Editor/tools/parse_madden_tdb.py``, which established it
empirically against a known-good Madden 08 sample. Nothing here re-derives it.

Field names are stored **byte-reversed** on disc and in the executable: the
table is ``'YALP'`` for ``PLAY`` and the column is ``'DIGP'`` for ``PGID``. That
is a property of how 4-character codes are built as 32-bit immediates, not a
separate obfuscation, and :func:`fourcc` is the only place it is undone.
"""

from __future__ import annotations

import struct
from typing import Dict, Iterator, List, NamedTuple, Optional, Sequence

#: Field type codes in a TDB field definition.
TYPE_STRING = 0
TYPE_BINARY = 1
TYPE_SINT = 2
TYPE_UINT = 3
TYPE_FLOAT = 4

_TERF_MAGIC = b"TERF"
_DIR_MAGIC = b"DIR1"
_DB_MAGIC = b"DB"

#: Offsets inside the TERF header.
_TERF_DIR_OFFSET = 0x04
_TERF_MEMBER_COUNT = 0x0E

#: A TDB file header is 24 bytes, then 8 bytes per table directory entry.
_DB_HEADER_SIZE = 24
_DB_DIRENT_SIZE = 8
#: A table header is 40 bytes, then 16 bytes per field definition.
_TABLE_HEADER_SIZE = 40
_TABLE_FIELD_SIZE = 16


class TdbError(Exception):
    """The bytes are not the database we expect."""


def fourcc(raw: bytes) -> str:
    """Undo the byte reversal applied to 4-character codes."""
    return raw[::-1].decode("latin-1")


class Field(NamedTuple):
    name: str
    type: int
    offset_bits: int
    bits: int


class Table:
    """One table: its record geometry, its columns, and its packed rows."""

    def __init__(self, data: bytes, offset: int) -> None:
        self._data = data
        self._offset = offset
        self.record_bytes = _u32(data, offset + 8)
        self.record_bits = _u32(data, offset + 12)
        self.max_records = _u16(data, offset + 20)
        self.record_count = _u16(data, offset + 22)
        field_count = data[offset + 28]
        self.fields: Dict[str, Field] = {}
        self.field_order: List[str] = []
        for i in range(field_count):
            base = offset + _TABLE_HEADER_SIZE + i * _TABLE_FIELD_SIZE
            # A field definition stores its name unreversed, unlike the
            # executable's query text.
            name = data[base + 8:base + 12].decode("latin-1")
            self.fields[name] = Field(name, _u32(data, base), _u32(data, base + 4),
                                      _u32(data, base + 12))
            self.field_order.append(name)
        self._records = offset + _TABLE_HEADER_SIZE + field_count * _TABLE_FIELD_SIZE

    def record(self, index: int) -> bytes:
        if not 0 <= index < self.record_count:
            raise IndexError("record %d of %d" % (index, self.record_count))
        start = self._records + index * self.record_bytes
        return self._data[start:start + self.record_bytes]

    def value(self, record: bytes, name: str) -> int:
        """One integer column out of a packed record."""
        field = self.fields.get(name)
        if field is None:
            raise TdbError("table has no column %r" % (name,))
        return read_bits(record, field.offset_bits, field.bits)

    def rows(self, columns: Sequence[str]) -> Iterator[List[int]]:
        """Every record, as the requested columns in the requested order."""
        missing = [c for c in columns if c not in self.fields]
        if missing:
            raise TdbError("table is missing columns: %s" % ", ".join(missing))
        picked = [self.fields[c] for c in columns]
        for i in range(self.record_count):
            record = self.record(i)
            yield [read_bits(record, f.offset_bits, f.bits) for f in picked]


class Database:
    """One TDB: a set of named tables."""

    def __init__(self, data: bytes, base: int) -> None:
        if data[base:base + 2] != _DB_MAGIC:
            raise TdbError("no DB magic at %#x" % base)
        self._data = data
        self._base = base
        self.size = _u32(data, base + 8)
        count = _u32(data, base + 16)
        if count > 4096:
            raise TdbError("implausible table count %d at %#x" % (count, base))
        self._tables: Dict[str, int] = {}
        for i in range(count):
            entry = base + _DB_HEADER_SIZE + i * _DB_DIRENT_SIZE
            name = data[entry:entry + 4].decode("latin-1")
            self._tables[name] = _u32(data, entry + 4)
        # Table offsets are relative to the end of the directory.
        self._table_base = base + _DB_HEADER_SIZE + count * _DB_DIRENT_SIZE

    @property
    def table_names(self) -> List[str]:
        return list(self._tables)

    def __contains__(self, name: str) -> bool:
        return name in self._tables

    def table(self, name: str) -> Table:
        if name not in self._tables:
            raise TdbError("no table %r (have %s)"
                           % (name, ", ".join(sorted(self._tables))))
        return Table(self._data, self._table_base + self._tables[name])


class Container:
    """A TERF archive of databases."""

    def __init__(self, data: bytes) -> None:
        if data[:4] != _TERF_MAGIC:
            raise TdbError("not a TERF container (magic %r)" % data[:4])
        self._data = data
        dir_offset = _u32(data, _TERF_DIR_OFFSET)
        if data[dir_offset:dir_offset + 4] != _DIR_MAGIC:
            raise TdbError("no DIR1 block at %#x" % dir_offset)
        dir_size = _u32(data, dir_offset + 4)
        self.member_count = _u16(data, _TERF_MEMBER_COUNT)
        # Member offsets are relative to the end of the directory block.
        self._member_base = dir_offset + dir_size
        entries = dir_offset + 8
        self._members: List[tuple] = []
        for i in range(self.member_count):
            entry = entries + i * 8
            self._members.append((_u32(data, entry), _u32(data, entry + 4)))

    def __len__(self) -> int:
        return self.member_count

    def database(self, index: int) -> Optional[Database]:
        """Member *index*, or ``None`` if it is not a TDB.

        Not every member has to be a database; callers that walk the whole
        container want to skip those rather than fail.
        """
        offset, _size = self._members[index]
        base = self._member_base + offset
        if self._data[base:base + 2] != _DB_MAGIC:
            return None
        return Database(self._data, base)

    def databases(self) -> Iterator[Database]:
        for i in range(self.member_count):
            db = self.database(i)
            if db is not None:
                yield db


def read_bits(record: bytes, offset_bits: int, num_bits: int) -> int:
    """Pull an unsigned field out of a bit-packed record.

    Least-significant-bit-first both within each byte and within the field --
    the convention the Madden 08 reader established empirically. Bits past the
    end of the record read as zero rather than raising, because the last field
    of a record can extend into the trailing pad.
    """
    value = 0
    for i in range(num_bits):
        position = offset_bits + i
        index = position >> 3
        if index < len(record):
            value |= ((record[index] >> (position & 7)) & 1) << i
    return value


def load(path: str) -> Container:
    with open(path, "rb") as handle:
        return Container(handle.read())


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]

"""The typed view of the game world, over `addresses.yaml`.

Layer 3 of the harness (`docs/lab-design.md`). Everything above this layer asks
questions in the game's own vocabulary -- "which defender is the fullback
engaged with", "what is this receiver's effective Speed" -- and every address
that answers one lives in `addresses.yaml`, not here. A wrong number is then a
one-line data fix with a citation attached, which is the whole reason the map is
data.

Two readers, one protocol
-------------------------
`World` needs `read(addr, size) -> int` and `read_bytes(addr, n) -> bytes` and
nothing else. `DumpReader` supplies them from a 32 MB EE image on disk, which is
how this is tested with no emulator in the room and how savestates are read
offline; `EmuReader` supplies them from layer 1's `Emu` over PINE. Virtual
addresses map 1:1 onto offsets in the dump, so the two paths differ only in
latency and in whether the world underneath is moving.

Three things layer 1 established about the live path, all of which shape the
code below:

* **Batch or lose.** PINE executes concatenated commands in one round trip.
  Every accessor here that reads more than one field builds a single spec list
  and issues one `read_many`. A player snapshot is one round trip, not twenty.
* **A read of an unmapped address returns 0, not an error.** So a drifted
  address is indistinguishable from a legitimately zero field, and a whole
  world of plausible zeros is a real failure mode. `World.verify()` exists for
  that: it asserts against values that are known to be nonzero and known to be
  specific, and it raises rather than letting the caller proceed.
* **Reads are not synchronised with emulation.** PINE's server thread takes no
  lock against the EE thread, so a multi-field read can tear -- a player's x
  from one frame and his y from the next. Batching narrows the window; nothing
  closes it. Position and velocity are therefore adjacent within their batch,
  and `World.snapshot()` brackets itself with the frame counter and reports
  whether it moved, so a torn sample is visible in the data instead of being
  quietly averaged into a result later.

Null is the normal reading at a menu
------------------------------------
Every runtime pointer in the map except the ball spot is null in
`extract/ee_mainmenu.bin`, because the game had not built a play. That is
correct, not broken: `players()` returns an empty list, `ball()` and `phase()`
return `None`, and `in_play` is False. Nothing here raises because a play does
not exist yet.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:  # PyYAML is the only dependency and it is already in this repo's env.
    import yaml
except ImportError as exc:  # pragma: no cover - environment problem, not logic
    raise ImportError("world.py needs PyYAML to read addresses.yaml") from exc

#: Where the map lives. Kept next to this module so the two version together.
DEFAULT_MAP = Path(__file__).with_name("addresses.yaml")

#: PS2 main RAM. A dump shorter than this is not an EE image.
EE_SIZE = 32 * 1024 * 1024

#: Scalar type codes used in the map: (width, struct format or None).
#: `ptr`, `handle`, `fn_ptr` and `cstr_ptr` are all 32-bit words -- they are
#: distinct names because what you may legally do with the value differs, and
#: the name is what tells a reader of the YAML which it is.
_SCALARS: Dict[str, Tuple[int, Optional[str]]] = {
    "u8": (1, "<B"), "u16": (2, "<H"), "u32": (4, "<I"),
    "s8": (1, "<b"), "s16": (2, "<h"), "s32": (4, "<i"),
    "f32": (4, "<f"),
    "ptr": (4, "<I"), "handle": (4, "<I"),
    "fn_ptr": (4, "<I"), "cstr_ptr": (4, "<I"),
    "fourcc": (4, None),
}

#: A read spec, as layer 1's `Emu.read_many` takes it.
Spec = Tuple[int, int]


def side_key(side: int) -> str:
    """Side 0 is the offence in every dump seen so far. Named, not numbered."""
    return {0: "offence", 1: "defence"}.get(side, "side%d" % side)


class WorldError(RuntimeError):
    """Base for everything this module raises."""


class VerifyFailed(WorldError):
    """`verify()` found the map and the memory disagreeing.

    Raised rather than returned because the alternative -- carrying on with a
    world full of zeros that a PINE read of a dead address produced -- is how
    this project has generated wrong answers before.
    """

    def __init__(self, failures: Sequence[str]) -> None:
        self.failures = list(failures)
        super().__init__(
            "address map does not match memory:\n  " + "\n  ".join(self.failures))


class MapError(WorldError):
    """The YAML asked for something this code cannot express."""


class NotReadable(WorldError):
    """The value exists in the engine but is not obtainable by reading memory.

    Raised rather than returning a plausible number, because the whole failure
    mode this layer guards against is a caller acting on a value that looks
    like an answer and is not one.
    """


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def read_many(reader: Any, specs: Sequence[Spec]) -> List[int]:
    """Read a batch, using the reader's own batching when it has any.

    Layer 1's `Emu` does; `DumpReader` does; a minimal reader supplied by a
    test need not. The fallback is a loop, which is correct but slow -- that is
    the right trade, because a reader that cannot batch is a reader whose reads
    are cheap.
    """
    batched = getattr(reader, "read_many", None)
    if batched is not None:
        return list(batched(specs))
    return [reader.read(addr, size) for addr, size in specs]


class DumpReader:
    """A 32 MB EE image on disk, read by mmap.

    Virtual address == file offset, verified: the ELF's loadable segment
    matches the image to 99.86% and `0x0019BD7C` reads `24050032` exactly as
    the static analysis predicts. So there is no translation here at all, which
    is the point -- a bug in an address is not hidden behind a bug in a mapping.
    """

    def __init__(self, path: "str | Path") -> None:
        self.path = Path(path)
        self._fh = open(self.path, "rb")
        try:
            import mmap as _mmap
            self._mm = _mmap.mmap(self._fh.fileno(), 0, access=_mmap.ACCESS_READ)
        except Exception:
            self._fh.close()
            raise
        if len(self._mm) < EE_SIZE:
            size = len(self._mm)
            self.close()
            raise WorldError(
                "%s is %d bytes; an EE image is %d" % (self.path, size, EE_SIZE))

    @classmethod
    def from_savestate(cls, path: "str | Path") -> "DumpReader":
        """Extract `eeMemory.bin` from a PCSX2 `.p2s` and read that.

        The savestate is a ZIP whose members use Zstandard (method 93), which
        `zipfile` will not decompress. The member is a bare zstd frame, so the
        raw bytes are pulled out by hand and handed to `zstandard`.
        """
        import tempfile
        import zipfile

        try:
            import zstandard
        except ImportError as exc:
            raise WorldError(
                "reading a .p2s needs the `zstandard` package (PCSX2 stores "
                "members with Zstandard, method 93). Extract eeMemory.bin "
                "another way and use DumpReader() on it instead."
            ) from exc

        with zipfile.ZipFile(path) as zf:
            info = zf.getinfo("eeMemory.bin")
            with zf.fp as _unused:  # noqa: F841 - keep the handle alive below
                pass
        # Re-open, because reading the raw member needs the local header parsed
        # rather than zipfile's decompressing stream.
        with open(path, "rb") as fh:
            fh.seek(info.header_offset)
            head = fh.read(30)
            if head[:4] != b"PK\x03\x04":
                raise WorldError("%s: eeMemory.bin has no local header" % path)
            name_len, extra_len = struct.unpack_from("<HH", head, 26)
            fh.seek(info.header_offset + 30 + name_len + extra_len)
            raw = fh.read(info.compress_size)

        blob = zstandard.ZstdDecompressor().decompress(raw, max_output_size=EE_SIZE)
        tmp = tempfile.NamedTemporaryFile(suffix=".eeMemory.bin", delete=False)
        try:
            tmp.write(blob)
        finally:
            tmp.close()
        return cls(tmp.name)

    def read(self, address: int, size: int = 4) -> int:
        return int.from_bytes(self.read_bytes(address, size), "little")

    def read_bytes(self, address: int, count: int) -> bytes:
        if address < 0 or address + count > len(self._mm):
            raise WorldError(
                "read of %d bytes at 0x%08X is outside the image" % (count, address))
        return self._mm[address:address + count]

    def read_many(self, specs: Sequence[Spec]) -> List[int]:
        return [self.read(addr, size) for addr, size in specs]

    def close(self) -> None:
        mm = getattr(self, "_mm", None)
        if mm is not None:
            mm.close()
            self._mm = None
        if not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> "DumpReader":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


class EmuReader:
    """Layer 1's `Emu`, narrowed to the two calls this layer is allowed.

    `Emu` already satisfies the reader protocol, so this adapter exists for one
    reason: it is a wall. Nothing above layer 3 gets a handle that can write to
    a live console, because "never write to game memory outside a trial's
    declared setup" is only enforceable if the observation path physically
    cannot.
    """

    def __init__(self, emu: Any) -> None:
        self._emu = emu

    def read(self, address: int, size: int = 4) -> int:
        return self._emu.read(address, size)

    def read_bytes(self, address: int, count: int) -> bytes:
        return self._emu.read_bytes(address, count)

    def read_many(self, specs: Sequence[Spec]) -> List[int]:
        return list(self._emu.read_many(specs))


# ---------------------------------------------------------------------------
# The map
# ---------------------------------------------------------------------------

class Field:
    """One entry under `structs.<name>.fields`."""

    __slots__ = ("name", "offset", "type", "count", "enum", "source",
                 "confidence", "note")

    def __init__(self, name: str, spec: Dict[str, Any]) -> None:
        self.name = name
        self.offset = int(spec["offset"])
        self.type = str(spec.get("type", "u32"))
        self.count = int(spec.get("count", 1))
        self.enum = spec.get("enum")
        self.source = spec.get("source")
        self.confidence = spec.get("confidence")
        self.note = spec.get("note")

    @property
    def width(self) -> int:
        try:
            return _SCALARS[self.type][0]
        except KeyError:
            raise MapError("field %s has non-scalar type %r" % (self.name, self.type))

    def specs(self, base: int) -> List[Spec]:
        """The read batch for this field at `base`."""
        w = self.width
        return [(base + self.offset + i * w, w) for i in range(self.count)]

    def decode(self, words: Sequence[int]) -> Any:
        vals = [_decode(self.type, w) for w in words]
        return vals if self.count > 1 else vals[0]


def _decode(type_: str, raw: int) -> Any:
    """Turn a little-endian integer of the right width into the field's type."""
    width, fmt = _SCALARS[type_]
    if type_ == "fourcc":
        return raw.to_bytes(4, "little").decode("latin1")
    if fmt in ("<B", "<H", "<I"):
        return raw
    return struct.unpack(fmt, raw.to_bytes(width, "little"))[0]


class AddressMap:
    """`addresses.yaml`, parsed. Read-only.

    Deliberately thin: it hands back what the file says, including the
    `confidence` and `source` on every field, so a caller that wants to refuse
    to act on a `doc`-grade address can.
    """

    def __init__(self, data: Dict[str, Any]) -> None:
        self.raw = data
        self.meta: Dict[str, Any] = data.get("meta", {})
        self.globals: Dict[str, Any] = data.get("globals", {})
        self.pointers: Dict[str, Any] = data.get("pointers", {})
        self.structs: Dict[str, Any] = data.get("structs", {})
        self.derived: Dict[str, Any] = data.get("derived", {})
        self.enums: Dict[str, Any] = data.get("enums", {})
        self._fields: Dict[str, Dict[str, Field]] = {}
        for sname, sspec in self.structs.items():
            self._fields[sname] = {
                fname: Field(fname, fspec)
                for fname, fspec in (sspec.get("fields") or {}).items()
            }

    @classmethod
    def load(cls, path: "str | Path" = DEFAULT_MAP) -> "AddressMap":
        with open(path, "r", encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh))

    def fields(self, struct_name: str) -> Dict[str, Field]:
        try:
            return self._fields[struct_name]
        except KeyError:
            raise MapError("no struct %r in the map" % struct_name)

    def field(self, struct_name: str, field_name: str) -> Field:
        try:
            return self.fields(struct_name)[field_name]
        except KeyError:
            raise MapError("struct %r has no field %r" % (struct_name, field_name))

    def stride(self, struct_name: str) -> int:
        spec = self.structs.get(struct_name) or {}
        if "stride" not in spec:
            raise MapError("struct %r has no stride" % struct_name)
        return int(spec["stride"])

    def enum_name(self, enum: str, value: int) -> Optional[str]:
        """The name for `value`, or None when the enum does not cover it.

        None is a real answer here and not a failure. Most of these enums are
        partial by construction -- 24 of 115 AI states are named -- and a
        caller that gets None has learned something true.
        """
        spec = self.enums.get(enum) or {}
        values = spec.get("values") or {}
        if value in values:
            return str(values[value])
        for label, bounds in (spec.get("ranges") or {}).items():
            lo, hi = int(bounds[0]), int(bounds[1])
            if lo <= value <= hi:
                return str(label)
        return None

    def confidence(self, struct_name: str, field_name: str) -> Optional[str]:
        return self.field(struct_name, field_name).confidence


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

class Handle:
    """The engine's universal object reference.

    Every cross-reference between objects -- ball carrier, engagement link,
    double-team registry -- stores one of these rather than a pointer. The
    resolver at `0x0013B798` reads `kind` out of byte 0 and jumps a 10-entry
    table; kind 1 is a player and resolves to `GetPlayer(side, index)`.
    """

    __slots__ = ("word",)
    PLAYER_KIND = 1

    def __init__(self, word: int) -> None:
        self.word = word & 0xFFFFFFFF

    @property
    def kind(self) -> int:
        return self.word & 0xFF

    @property
    def side(self) -> int:
        return (self.word >> 8) & 0xFF

    @property
    def index(self) -> int:
        return (self.word >> 16) & 0xFF

    @property
    def is_player(self) -> bool:
        return self.kind == self.PLAYER_KIND

    def __repr__(self) -> str:
        if self.is_player:
            return "Handle(player %d:%d)" % (self.side, self.index)
        return "Handle(kind=%d, 0x%08X)" % (self.kind, self.word)

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, Handle) and other.word == self.word

    def __hash__(self) -> int:
        return hash(self.word)


class Player:
    """One player object. Reads are lazy and batched.

    `snapshot()` is the accessor that matters -- it pulls every mapped field in
    one round trip. The individual properties exist for interactive poking and
    each costs a read, which is fine offline and wasteful live.
    """

    def __init__(self, world: "World", base: int, side: int, index: int) -> None:
        self.world = world
        self.base = base
        self.side = side
        self.index = index

    # -- single fields -----------------------------------------------------
    def field(self, name: str) -> Any:
        f = self.world.map.field("player", name)
        words = read_many(self.world.reader, f.specs(self.base))
        return f.decode(words)

    @property
    def handle(self) -> Handle:
        """This player's own handle. It is literally the object's first word."""
        return Handle(self.world.reader.read(self.base, 4))

    @property
    def position(self) -> int:
        return int(self.field("position"))

    @property
    def position_name(self) -> Optional[str]:
        return self.world.map.enum_name("position", self.position)

    @property
    def xyz(self) -> Tuple[float, float, float]:
        """Position, read as one batch so the three axes cannot tear apart."""
        m = self.world.map
        specs: List[Spec] = []
        for name in ("pos_x", "pos_y", "pos_z"):
            specs.extend(m.field("player", name).specs(self.base))
        raw = read_many(self.world.reader, specs)
        return tuple(_decode("f32", w) for w in raw)  # type: ignore[return-value]

    @property
    def velocity(self) -> Tuple[float, float, float]:
        m = self.world.map
        specs: List[Spec] = []
        for name in ("vel_x", "vel_y", "vel_z"):
            specs.extend(m.field("player", name).specs(self.base))
        raw = read_many(self.world.reader, specs)
        return tuple(_decode("f32", w) for w in raw)  # type: ignore[return-value]

    def ratings(self) -> Dict[str, int]:
        """The 21 effective ratings, keyed by attribute name.

        These are the 0..255 values the engine actually reads --
        `trunc(rating * 2.55)` -- rebuilt from the roster value, the skill
        class and the sliders before every play. There is no raw 0..100
        rating anywhere in the player object to compare them against.
        """
        f = self.world.map.field("player", "ratings")
        raw = read_many(self.world.reader, f.specs(self.base))
        names = self.world.map.enums["attribute"]["values"]
        return {str(names[i]): v for i, v in enumerate(raw)}

    def rating(self, attr: "int | str") -> int:
        """One effective rating, by attribute index or fourcc suffix."""
        if isinstance(attr, str):
            names = self.world.map.enums["attribute"]["values"]
            wanted = attr.upper()
            match = [i for i, n in names.items() if str(n).upper() == wanted]
            if not match:
                raise MapError("no attribute named %r" % attr)
            attr = int(match[0])
        f = self.world.map.field("player", "ratings")
        if not 0 <= attr < f.count:
            raise MapError("attribute index %d is outside 0..%d" % (attr, f.count - 1))
        raw = self.world.reader.read(self.base + f.offset + 2 * attr, 2)
        return raw

    def engaged_with(self) -> Optional["Player"]:
        """Whoever this player is engaged with, or None."""
        return self.world.resolve(Handle(int(self.field("engagement_link"))))

    # -- the batched read --------------------------------------------------
    def snapshot(self, fields: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        """Every mapped field (or a named subset) in one round trip.

        Field order follows the map, which puts position next to position and
        velocity next to velocity. That is deliberate: PINE reads are not
        synchronised with emulation, and adjacency is the only lever available
        for narrowing how far apart two related samples can be taken.
        """
        m = self.world.map
        chosen = list(fields) if fields is not None else list(m.fields("player"))
        specs: List[Spec] = []
        spans: List[Tuple[str, Field, int]] = []
        for name in chosen:
            f = m.field("player", name)
            s = f.specs(self.base)
            spans.append((name, f, len(s)))
            specs.extend(s)
        raw = read_many(self.world.reader, specs)
        out: Dict[str, Any] = {"side": self.side, "index": self.index}
        pos = 0
        for name, f, n in spans:
            out[name] = f.decode(raw[pos:pos + n])
            pos += n
        return out

    def __repr__(self) -> str:
        return "Player(%d:%d @0x%08X)" % (self.side, self.index, self.base)


class Ball:
    """The live ball record, resolved through the ball manager."""

    def __init__(self, world: "World", base: int) -> None:
        self.world = world
        self.base = base

    def _handle(self, field: str) -> Handle:
        f = self.world.map.field("ball", field)
        return Handle(self.world.reader.read(self.base + f.offset, f.width))

    @property
    def carrier(self) -> Optional[Player]:
        """Whoever currently has the ball, or None while it is loose.

        `SetBallCarrier 0x001FFF18` writes the same handle to both `+0xB4` and
        `+0xB8`; only `+0xB4` is cleared elsewhere, which makes it the live
        field and `+0xB8` the sticky one. Confirmed pre-snap in the in-play
        dump: `+0xB4` null, `+0xB8` still holding the halfback.
        """
        return self.world.resolve(self._handle("carrier"))

    @property
    def last_carrier(self) -> Optional[Player]:
        return self.world.resolve(self._handle("last_carrier"))

    def __repr__(self) -> str:
        return "Ball(@0x%08X)" % self.base


class DoubleTeam:
    """One slot of the four-entry double-team registry."""

    def __init__(self, world: "World", base: int, slot: int) -> None:
        self.world = world
        self.base = base
        self.slot = slot

    def snapshot(self) -> Dict[str, Any]:
        m = self.world.map
        fields = m.fields("dt_record")
        specs: List[Spec] = []
        order: List[Tuple[str, Field]] = []
        for name, f in fields.items():
            specs.extend(f.specs(self.base))
            order.append((name, f))
        raw = read_many(self.world.reader, specs)
        out: Dict[str, Any] = {"slot": self.slot}
        for (name, f), word in zip(order, raw):
            out[name] = Handle(word) if f.type == "handle" else word
        return out


# ---------------------------------------------------------------------------
# The world
# ---------------------------------------------------------------------------

class World:
    """Everything observable, in the game's vocabulary."""

    def __init__(self, reader: Any, address_map: Optional[AddressMap] = None) -> None:
        self.reader = reader
        self.map = address_map or AddressMap.load()

    # -- primitives --------------------------------------------------------
    def deref(self, pointer_name: str) -> int:
        """One documented runtime pointer, dereferenced. 0 means "no play"."""
        spec = self.map.pointers.get(pointer_name)
        if spec is None:
            raise MapError("no pointer %r in the map" % pointer_name)
        return self.reader.read(int(spec["addr"]), 4)

    @property
    def in_play(self) -> bool:
        """True when the engine has built a play.

        The player array pointer is the test the design doc names, and it is
        the one that gates almost everything else.
        """
        return self.deref("player_array") != 0

    def resolve(self, handle: Handle) -> Optional[Player]:
        """A handle to a player, or None for a null or non-player handle."""
        if not handle.is_player:
            return None
        return self.player(handle.side, handle.index)

    # -- options -----------------------------------------------------------
    def option_rows(self) -> List[Tuple[int, str, int]]:
        """All 131 options as (index, fourcc, value), in one batch.

        The list form is canonical because the table is not a dict: indices 7
        and 8 both carry the fourcc `OFMO`, a shipped duplicate, so one of them
        is unaddressable by name.
        """
        names = self.map.globals["option_names"]
        values = self.map.globals["option_values"]
        n = int(names["count"])
        specs = [(int(names["addr"]) + 4 * i, 4) for i in range(n)]
        specs += [(int(values["addr"]) + 2 * i, 2) for i in range(n)]
        raw = read_many(self.reader, specs)
        out = []
        for i in range(n):
            out.append((i, _decode("fourcc", raw[i]), raw[n + i]))
        return out

    def options(self) -> Dict[str, int]:
        """The option table keyed by fourcc.

        A duplicated fourcc keeps its first index; use `option_rows()` when
        that matters, which for `OFMO` it does.
        """
        out: Dict[str, int] = {}
        for _i, name, value in self.option_rows():
            out.setdefault(name, value)
        return out

    def option(self, key: "int | str") -> int:
        rows = self.option_rows()
        if isinstance(key, int):
            return rows[key][2]
        for _i, name, value in rows:
            if name == key:
                return value
        raise MapError("no option named %r" % key)

    def attribute_names(self) -> List[str]:
        """The 21 attribute fourccs, read out of memory rather than the map."""
        spec = self.map.globals["attribute_names"]
        n = int(spec["count"])
        raw = read_many(self.reader, [(int(spec["addr"]) + 4 * i, 4) for i in range(n)])
        return [_decode("fourcc", w) for w in raw]

    # -- players -----------------------------------------------------------
    def _array(self) -> Optional[Tuple[int, int, int]]:
        """(base, per_side, total) from the array descriptor, or None."""
        desc = self.deref("player_array")
        if desc == 0:
            return None
        f = self.map.fields("player_array_desc")
        specs: List[Spec] = []
        for name in ("base", "per_side", "total"):
            specs.extend(f[name].specs(desc))
        base, per_side, total = read_many(self.reader, specs)
        return base, per_side, total

    def player(self, side: int, index: int) -> Optional[Player]:
        """`GetPlayer(side, index)`, reimplemented from `0x001655B0`.

        `base + (side * per_side + index) * 5312`. The stride is not in any
        document -- it is the `addiu v1, zero, 5312` in the accessor itself.
        """
        arr = self._array()
        if arr is None:
            return None
        base, per_side, _total = arr
        stride = self.map.stride("player")
        return Player(self, base + (side * per_side + index) * stride, side, index)

    def players(self) -> List[Player]:
        """Both sides, in array order. Empty when no play exists."""
        arr = self._array()
        if arr is None:
            return []
        base, per_side, total = arr
        stride = self.map.stride("player")
        out = []
        for slot in range(total):
            side, index = divmod(slot, per_side) if per_side else (0, slot)
            out.append(Player(self, base + slot * stride, side, index))
        return out

    # -- ball, phase, clock ------------------------------------------------
    def ball(self) -> Optional[Ball]:
        mgr = self.deref("ball_manager")
        if mgr == 0:
            return None
        m = self.map.structs["ball_manager"]["fields"]
        active = self.reader.read(mgr + int(m["active"]["offset"]), 1)
        stride = self.map.stride("ball")
        return Ball(self, mgr + int(m["balls"]["offset"]) + active * stride)

    def frames_since_snap(self) -> Optional[int]:
        """The engine's own frame counter, reset at play init.

        `[[0x00601280] + 0x54]`. The double indirection is not optional -- an
        earlier draft in `docs/` wrote it as `[0x00601280 + 84]`, which reads a
        fixed global and returns garbage.
        """
        obj = self.deref("play_manager")
        if obj == 0:
            return None
        f = self.map.field("play_manager", "frames_since_snap")
        return self.reader.read(obj + f.offset, f.width)

    def phase(self) -> int:
        """Always raises. There is no play-phase value to read.

        `docs/lab-design.md` asks for this and `docs/` calls `0x0015ADA0` the
        play phase, so the method exists to give the answer a name -- but the
        answer is that the design assumed something that is not there.
        `0x0015ADA0` does not read a phase word: during gameplay it finds the
        player holding the ball and tests his authored play-record class, so
        what it returns is a property of the called play, not of where the
        play has got to. The `[[0x00600C70]+0x98]` word an earlier revision of
        the map guessed at is only returned on screen id 0x0E, and the in-play
        dump reads screen id 2.

        Use `frames_since_snap()` for the clock and the players' `ai_state`
        for what they are currently doing -- pre-snap reads 42 on defence and
        69/70 on offence, so the pre-snap/live distinction is observable there.
        """
        raise NotReadable(
            "no play-phase word exists; see derived.phase in addresses.yaml. "
            "Use frames_since_snap() and Player.ai_state instead.")

    def gamestate_words(self) -> Optional[Tuple[int, int]]:
        """The two words at `[0x00600C70]+0x98/+0x9C`, whatever they are.

        Exposed because they are the only fields anything touches on that
        object and somebody will want to watch them change. Not interpreted.
        """
        obj = self.deref("gamestate")
        if obj == 0:
            return None
        a = self.map.field("gamestate", "phase")
        b = self.map.field("gamestate", "phase_prev")
        got = read_many(self.reader, [(obj + a.offset, a.width), (obj + b.offset, b.width)])
        return got[0], got[1]

    def double_teams(self) -> List[DoubleTeam]:
        obj = self.deref("play_manager")
        if obj == 0:
            return []
        spec = self.map.structs["play_manager"]["fields"]["dt_registry"]
        off, stride, count = int(spec["offset"]), int(spec["stride"]), int(spec["count"])
        return [DoubleTeam(self, obj + off + k * stride, k) for k in range(count)]

    def state_name(self, state_id: int) -> Optional[str]:
        """The name of an AI state, or None -- most of the 115 are unnamed."""
        return self.map.enum_name("ai_state", state_id & 0x7F)

    def state_row(self, state_id: int) -> List[int]:
        """The six function pointers of one AI state descriptor."""
        spec = self.map.globals["ai_states"]
        if not 0 <= state_id < int(spec["count"]):
            raise MapError("state %d is outside 0..%d"
                           % (state_id, int(spec["count"]) - 1))
        base = int(spec["addr"]) + int(spec["stride"]) * state_id
        return read_many(self.reader, [(base + 4 * i, 4) for i in range(6)])

    def certified_batch(self, parts: Sequence[Tuple["Player", Sequence[str]]]
                        ) -> Tuple[Optional[int], Optional[int],
                                   List[Dict[str, Any]]]:
        """Every requested field of every requested player, in ONE batch,
        bracketed by two reads of the game clock.

        Returns `(tick_before, tick_after, decoded)` where `decoded[i]` maps
        field name to value for `parts[i]`. When the two ticks are equal the
        whole sample provably lies within a single game frame; when they are
        not, the caller knows the sample straddled a boundary and can retry or
        mark it torn.

        The point is what it replaces. Sampling one entity at a time was ~23
        round trips smeared across game time, so a "frame" mixed values from
        two or three real frames -- which is precisely what made a bitwise-
        deterministic engine measure as 79% reproducible on positions. The
        PINE server executes a whole message in one pass over one buffer with
        no socket waits inside it (layer 1), so a single batch is as close to
        atomic as this transport can get; the clock bracket turns "close to"
        into a per-sample certificate instead of a hope.

        The clock is the play manager's frames-since-snap counter, resolved
        through its pointer *before* the batch is built -- a batch spec is a
        list of fixed addresses and cannot chase a pointer mid-flight. The
        pointer is stable for the life of a play; if there is no play (the
        pointer is null) certification is impossible and both ticks are None.
        """
        clock_specs: List[Spec] = []
        obj = self.deref("play_manager")
        if obj != 0:
            f = self.map.field("play_manager", "frames_since_snap")
            clock_specs = [(obj + f.offset, f.width)]

        #: The two derived triples: not map fields but compositions of three.
        #: `Player.xyz`/`.velocity` exist so the axes are read in one batch;
        #: here the whole world already is one batch, so they simply expand.
        composites = {"xyz": ("pos_x", "pos_y", "pos_z"),
                      "velocity": ("vel_x", "vel_y", "vel_z")}

        specs: List[Spec] = list(clock_specs)
        spans: List[List[Tuple[str, List[Field], int]]] = []
        for player, fields in parts:
            plan: List[Tuple[str, List[Field], int]] = []
            for name in fields:
                members = [self.map.field("player", part)
                           for part in composites.get(name, (name,))]
                s: List[Spec] = []
                for member in members:
                    s.extend(member.specs(player.base))
                plan.append((name, members, len(s)))
                specs.extend(s)
            spans.append(plan)
        specs.extend(clock_specs)

        raw = read_many(self.reader, specs)
        pos = len(clock_specs)
        tick_before = raw[0] if clock_specs else None
        tick_after = raw[-1] if clock_specs else None

        decoded: List[Dict[str, Any]] = []
        for plan in spans:
            out: Dict[str, Any] = {}
            for name, members, n in plan:
                if len(members) == 1:
                    out[name] = members[0].decode(raw[pos:pos + n])
                else:
                    parts_raw, cursor = [], pos
                    for member in members:
                        width = len(member.specs(0))
                        parts_raw.append(member.decode(raw[cursor:cursor + width]))
                        cursor += width
                    out[name] = tuple(parts_raw)
                pos += n
            decoded.append(out)
        return tick_before, tick_after, decoded

    # -- the whole world ---------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """One tidy dict of everything, bracketed by the frame counter.

        `torn` is the honest part. PINE cannot pause the EE, so a snapshot that
        spans a frame boundary mixes two worlds. Reading the counter at both
        ends does not prevent that; it makes it visible, so a row can be
        dropped later instead of quietly biasing a result.
        """
        before = self.frames_since_snap()
        ball = self.ball()
        carrier = ball.carrier if ball is not None else None
        out: Dict[str, Any] = {
            "in_play": self.in_play,
            "frames_since_snap": before,
            "gamestate_words": self.gamestate_words(),
            "carrier": (carrier.side, carrier.index) if carrier else None,
            "options": self.options(),
            "players": [p.snapshot() for p in self.players()],
            "double_teams": [d.snapshot() for d in self.double_teams()],
        }
        after = self.frames_since_snap()
        out["torn"] = before != after
        return out

    def roster_report(self) -> Dict[str, Any]:
        """Position counts per side -- the cheapest "is this world sane" check.

        A wrong stride or a wrong base does not fail loudly; it produces
        records. What it cannot produce is a legal formation, so counting
        positions is the test that actually discriminates. In
        `extract/ee_inplay.bin` this returns 11 a side with one QB, one HB,
        three WR, one TE and five OL against a 4-3.
        """
        report: Dict[str, Any] = {"sides": {}}
        for p in self.players():
            side = report["sides"].setdefault(side_key(p.side), {})
            name = p.position_name or ("pos%d" % p.position)
            side[name] = side.get(name, 0) + 1
        report["total"] = len(self.players())
        return report

    # -- self-check --------------------------------------------------------
    def verify(self) -> None:
        """Assert the map against memory. Raises `VerifyFailed`.

        This exists because of one property of the live path: a PINE read of an
        unmapped address returns 0 rather than failing. A drifted address is
        therefore indistinguishable from a zero field, and the failure mode is
        a full world of plausible numbers. Every check below is against a value
        that is both nonzero and specific, so a wrong build or a wrong address
        cannot pass by accident.

        Everything here is static data, present at the main menu. It does not
        test whether a play exists -- `in_play` is for that.
        """
        bad: List[str] = []

        def check(label: str, got: Any, want: Any) -> None:
            if got != want:
                bad.append("%s: read %r, expected %r" % (label, got, want))

        # A word of ELF text. Pins the image itself: right game, right build,
        # right 1:1 address mapping.
        check("0x0019BD7C (ELF text anchor)", self.reader.read(0x0019BD7C, 4),
              0x24050032)

        rows = self.option_rows()
        check("option table length", len(rows), 131)
        if len(rows) == 131:
            check("option[0] name", rows[0][1], "OQLN")
            check("option[7] name", rows[7][1], "OFMO")
            check("option[8] name", rows[8][1], "OFMO")
            check("option[62] name", rows[62][1], "EPPN")

        attrs = self.attribute_names()
        expected = ["P" + str(n) for n in
                    self.map.enums["attribute"]["values"].values()]
        check("attribute fourccs", attrs, expected)

        # The shared exit stub sits in column 4 of every live AI state row --
        # a nonzero value in the middle of the table, so a truncated or shifted
        # table cannot pass.
        stub = self.state_row(0)[4]
        check("ai_states[0].exit stub", stub, 0x001B0528)
        check("ai_states[47].enter (lead block)", self.state_row(47)[0], 0x001B66A8)
        check("ai_states[47].ai_think", self.state_row(47)[2], 0x001B6870)

        # The ptrk recency weights are exact powers-of-two fractions, so any
        # drift shows up immediately rather than as a plausible float.
        spec = self.map.globals["ptrk_recency_weights"]
        weights = [_decode("f32", w) for w in read_many(
            self.reader, [(int(spec["addr"]) + 4 * i, 4) for i in range(4)])]
        want = [1 / 24.0, 1 / 48.0, 1 / 96.0, 1 / 192.0]
        # float32 rounding, so compare at single precision rather than exactly.
        if any(abs(g - e) > 1e-6 * e for g, e in zip(weights, want)):
            bad.append("ptrk recency weights: read %r, expected %r" % (weights, want))

        # The penalty name table doubles as the penalty enum, and a string
        # pointer that has drifted lands somewhere that is not a C string.
        spec = self.map.globals["penalty_names"]
        first = self.reader.read(int(spec["addr"]), 4)
        check("penalty_names[0]", self.read_cstr(first), "CLIPPING")

        if bad:
            raise VerifyFailed(bad)

    def read_cstr(self, address: int, limit: int = 64) -> str:
        """A NUL-terminated string. Used for the penalty names."""
        raw = self.reader.read_bytes(address, limit)
        return raw.split(b"\0", 1)[0].decode("latin1")

    def penalty_names(self) -> List[str]:
        spec = self.map.globals["penalty_names"]
        n = int(spec["count"])
        ptrs = read_many(self.reader, [(int(spec["addr"]) + 4 * i, 4) for i in range(n)])
        return [self.read_cstr(p) for p in ptrs]


def open_dump(path: "str | Path" = "extract/ee_mainmenu.bin",
              address_map: Optional[AddressMap] = None) -> World:
    """A `World` over a memory image on disk. The offline entry point."""
    return World(DumpReader(path), address_map)

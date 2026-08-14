"""Bake a pnach's word patches into the ELF, so the fixes ship with the disc.

The dev loop pokes EE memory through the emulator's cheat engine: a pnach is
re-stamped every vsync, which is unbeatable for iteration and useless as a
product. Baking writes the same words into `SLUS_207.52` itself, once, at
mastering -- no cheat files, no emulator settings, eventually real hardware.

WHY A WORD PATCH CAN BE BAKED AT ALL

A `patch=1,EE,ADDR,word,VALUE` line names a virtual address, and every address
in the current set lives in the executable's one PT_LOAD segment, whose file
bytes the loader copies to that address unchanged. So `ADDR` has a byte in the
file -- `p_offset + (ADDR - p_vaddr)` -- and writing the word there is exactly
what the cheat engine would have written at runtime. The file does not change
size, so nothing downstream (the ISO's layout, every other file's position)
moves either. `tools/patch_iso_elf.py` then drops the result back into the
image the same way `patch_iso_roster.py` drops in a roster.

THE THREE OUTCOMES, AND WHY TWO OF THEM REFUSE

Not every address has a byte in the file, and the difference is invisible in
the pnach -- which is the whole reason this tool classifies before it writes:

* **file-backed** (`p_vaddr <= addr`, word ends within `p_filesz`) -- writable.
  The entire deployed set is this: code words, cave bodies, data constants.
* **.bss** (past `p_filesz`, inside `p_memsz`) -- REFUSED. That region has no
  bytes in the file at all; the loader zeroes it before `main()` runs, so a
  baked word would be erased on the way up. Such a patch needs an init hook --
  code that writes it at runtime -- which this pipeline does not have yet
  (`docs/pnach-to-iso-pipeline.md`, Phase P1).
* **outside every segment** -- an error. Nothing maps that address; the pnach
  is stale, or aimed at the IOP, or a typo.

WHAT THIS TOOL DELIBERATELY WILL NOT DO

* **Guess a width.** `byte`, `short` and `extended` writes are a different
  number of bytes; this project has been burned by silent width bugs, so an
  unsupported width is a loud error rather than a plausible-looking write.
* **Decide T1 for you.** A baked word is written *once*; a pnach word is
  re-stamped every vsync. The two are equivalent only if the game never writes
  that address itself. No static check settles that -- the writer census is
  manual (`docs/pnach-to-iso-pipeline.md`, trap T1). The manifest exists so
  that census has something to work from, and so any bake can be reverted.
* **Touch the input.** `-o` is required and may not name the input file.

Usage::

    python3 tools/bake_pnach.py extract/SLUS_207.52 \\
        patches/14F8B841.c1-plus-doubleteam.pnach -o baked.elf --verify
    python3 tools/bake_pnach.py extract/SLUS_207.52 patches/*.pnach --audit
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: The project's shorthand for this executable's mapping (`vaddr = file_offset
#: + 0xFF000`). The tool derives the real delta from the program headers and
#: only compares it against this, because a hardcoded constant is how a tool
#: keeps writing confidently to the wrong place after the binary changes.
CONVENTION_DELTA = 0xFF000

PT_LOAD = 1
EM_MIPS = 8

#: pnach header lines that carry no patch. Anything else that is not a
#: `patch=` line is an error: a directive this tool silently skipped would be a
#: patch that silently did not ship.
METADATA_KEYS = frozenset((
    "gametitle", "comment", "author", "description", "credits", "date",
    "version", "cheat", "name",
))

#: Some editors leave a byte-order mark on the first line.
_BOM = "\ufeff"

FILE_BACKED = "file-backed"
BSS = "bss"
OUTSIDE = "outside"


class BakeError(Exception):
    pass


class Patch(NamedTuple):
    """One `patch=` line, parsed."""

    source: str
    line: int
    enabled: bool           # patch=1 applies; patch=0 is parked, not applied
    vaddr: int
    value: int


class Segment(NamedTuple):
    index: int
    offset: int
    vaddr: int
    filesz: int
    memsz: int

    @property
    def delta(self) -> int:
        return self.vaddr - self.offset


class Placement(NamedTuple):
    """A patch, and where (if anywhere) it lands in the file."""

    patch: Patch
    kind: str                       # FILE_BACKED | BSS | OUTSIDE
    file_offset: Optional[int]
    segment: Optional[int]
    reason: str


def _hex32(value: int) -> str:
    return "0x%08X" % value


# --------------------------------------------------------------------------
# the pnach
# --------------------------------------------------------------------------

def _hex_field(text: str, what: str, source: str, line: int) -> int:
    body = text[2:] if text[:2].lower() == "0x" else text
    if not body or any(c not in "0123456789abcdefABCDEF" for c in body):
        raise BakeError("%s:%d: %s %r is not hexadecimal"
                        % (source, line, what, text))
    value = int(body, 16)
    if value > 0xFFFFFFFF:
        raise BakeError("%s:%d: %s %r does not fit in 32 bits"
                        % (source, line, what, text))
    return value


def _parse_patch(body: str, source: str, line: int) -> Patch:
    fields = [field.strip() for field in body.split(",")]
    if len(fields) != 5:
        raise BakeError(
            "%s:%d: expected 'patch=mode,cpu,address,width,value' -- five "
            "fields, got %d: %s" % (source, line, len(fields), body.strip()))
    mode, cpu, address, width, value = fields
    if mode not in ("0", "1"):
        raise BakeError("%s:%d: patch mode %r is neither 0 nor 1"
                        % (source, line, mode))
    if cpu.upper() != "EE":
        raise BakeError(
            "%s:%d: %s patches do not bake into the EE executable. This tool "
            "writes SLUS_207.52; an %s patch belongs to another binary."
            % (source, line, cpu.upper(), cpu.upper()))
    if width.lower() != "word":
        known = ("byte", "short", "extended")
        raise BakeError(
            "%s:%d: width %r. Only 'word' is baked. A %s write covers a "
            "different number of bytes and guessing which ones has cost this "
            "project real time -- rewrite the line as word patches, or teach "
            "this tool that width deliberately."
            % (source, line, width,
               width.lower() if width.lower() in known else "non-word"))
    return Patch(source=source, line=line, enabled=(mode == "1"),
                 vaddr=_hex_field(address, "address", source, line),
                 value=_hex_field(value, "value", source, line))


def parse_pnach(text: str, source: str = "<pnach>") -> List[Patch]:
    """Every `patch=` line in a pnach, in file order.

    Comments (`//`, `#`, `;`), blank lines, `[section]` headers and the known
    header keys are skipped. Anything else raises: an unrecognised directive is
    far more likely to be a patch this tool would have dropped than something
    safe to ignore, and a dropped patch is invisible in the output.
    """
    patches: List[Patch] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.lstrip(_BOM).split("//", 1)[0].strip()
        if not line or line[0] in "#;[":
            continue
        key, sep, body = line.partition("=")
        key = key.strip().lower()
        if not sep:
            raise BakeError("%s:%d: not a 'key=value' line: %s"
                            % (source, number, line))
        if key in METADATA_KEYS:
            continue
        if key != "patch":
            raise BakeError(
                "%s:%d: unknown directive %r. Known: patch, %s. Refusing "
                "rather than skipping a line that may have been a patch."
                % (source, number, key, ", ".join(sorted(METADATA_KEYS))))
        patches.append(_parse_patch(body, source, number))
    return patches


def load_pnach(path: Path) -> List[Patch]:
    data = path.read_bytes()
    return parse_pnach(data.decode("utf-8", errors="replace"), str(path))


# --------------------------------------------------------------------------
# the ELF
# --------------------------------------------------------------------------

class Elf32:
    """Just enough ELF32 to say which file byte backs a virtual address.

    `recon.mipsdis.Elf32` does the same mapping for disassembly, but drops
    `p_memsz` -- and `p_memsz` is precisely the field that separates a word
    this tool can write from one it must refuse.
    """

    def __init__(self, data: bytes, path: str = "<elf>") -> None:
        self.data = bytes(data)
        self.path = path
        if len(self.data) < 52 or self.data[:4] != b"\x7fELF":
            raise BakeError("%s is not an ELF file" % path)
        if self.data[4] != 1:
            raise BakeError("%s is not ELF32 (class %d)" % (path, self.data[4]))
        if self.data[5] != 1:
            raise BakeError("%s is not little-endian; this tool writes "
                            "little-endian words" % path)
        machine = struct.unpack_from("<H", self.data, 18)[0]
        if machine != EM_MIPS:
            raise BakeError("%s is not a MIPS executable (e_machine %d); an "
                            "EE pnach has no business in it" % (path, machine))
        self.entry = struct.unpack_from("<I", self.data, 24)[0]
        ph_off = struct.unpack_from("<I", self.data, 28)[0]
        ph_size = struct.unpack_from("<H", self.data, 42)[0]
        ph_count = struct.unpack_from("<H", self.data, 44)[0]
        if ph_size < 32 or ph_count == 0:
            raise BakeError("%s has no usable program headers "
                            "(phentsize %d, phnum %d)"
                            % (path, ph_size, ph_count))
        self.segments: List[Segment] = []
        for index in range(ph_count):
            base = ph_off + index * ph_size
            if base + 32 > len(self.data):
                raise BakeError("%s: program header %d runs past the end of "
                                "the file" % (path, index))
            p_type, p_offset, p_vaddr = struct.unpack_from("<III", self.data,
                                                           base)
            p_filesz, p_memsz = struct.unpack_from("<II", self.data, base + 16)
            if p_type != PT_LOAD:
                continue
            if p_offset + p_filesz > len(self.data):
                raise BakeError(
                    "%s: segment %d claims %d bytes at offset %d but the file "
                    "is only %d bytes -- truncated?"
                    % (path, index, p_filesz, p_offset, len(self.data)))
            if p_memsz < p_filesz:
                raise BakeError("%s: segment %d has memsz < filesz"
                                % (path, index))
            self.segments.append(Segment(index, p_offset, p_vaddr, p_filesz,
                                         p_memsz))
        if not self.segments:
            raise BakeError("%s has no PT_LOAD segment to write into" % path)

    @classmethod
    def from_path(cls, path: Path) -> "Elf32":
        return cls(path.read_bytes(), str(path))

    def word(self, file_offset: int) -> int:
        return struct.unpack_from("<I", self.data, file_offset)[0]


def convention_notes(elf: Elf32) -> List[str]:
    """Warn when a segment's mapping is not the project's familiar one.

    The delta used is always the one derived from the headers. This only says
    so out loud when it is not `0xFF000`, because every address in every note
    in `docs/` was written under that assumption.
    """
    notes = []
    for seg in elf.segments:
        if seg.delta != CONVENTION_DELTA:
            notes.append(
                "segment %d maps vaddr = file_offset + 0x%X, not the "
                "project's usual 0x%X -- using the derived value, but every "
                "address in docs/ assumes the other one."
                % (seg.index, seg.delta, CONVENTION_DELTA))
    return notes


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

def classify(patch: Patch, elf: Elf32) -> Placement:
    """Where a patch's word lands: in the file, in .bss, or nowhere."""
    for seg in elf.segments:
        if not seg.vaddr <= patch.vaddr < seg.vaddr + seg.memsz:
            continue
        file_end = seg.vaddr + seg.filesz
        if patch.vaddr + 4 <= file_end:
            return Placement(patch, FILE_BACKED,
                             seg.offset + (patch.vaddr - seg.vaddr),
                             seg.index, "")
        if patch.vaddr < file_end:
            return Placement(
                patch, BSS, None, seg.index,
                "the word straddles the end of segment %d's file image "
                "(%s); only its first %d bytes exist in the file"
                % (seg.index, _hex32(file_end), file_end - patch.vaddr))
        return Placement(
            patch, BSS, None, seg.index,
            "past segment %d's filesz -- .bss (%s..%s) has no bytes in the "
            "file, and the loader zeroes it before main() runs"
            % (seg.index, _hex32(file_end), _hex32(seg.vaddr + seg.memsz)))
    spans = ", ".join("%s..%s" % (_hex32(s.vaddr), _hex32(s.vaddr + s.memsz))
                      for s in elf.segments)
    return Placement(patch, OUTSIDE, None, None,
                     "no segment maps it (loaded: %s)" % spans)


def collisions(placements: Sequence[Placement]) -> Tuple[List[str], List[str]]:
    """(duplicates, conflicts) among the applied patches.

    A duplicate is the same address written the same value twice -- harmless,
    worth saying. A conflict is two applied patches whose four-byte spans
    overlap with different values: under the cheat engine the last one wins
    every vsync and the earlier is invisible, so baking it is a coin toss
    between two intentions. That is a refusal, not a note.
    """
    duplicates: List[str] = []
    conflicts: List[str] = []
    by_address: Dict[int, List[Placement]] = {}
    for place in placements:
        if place.patch.enabled:
            by_address.setdefault(place.patch.vaddr, []).append(place)
    for vaddr, group in sorted(by_address.items()):
        if len(group) < 2:
            continue
        where = ", ".join("%s:%d" % (p.patch.source, p.patch.line)
                          for p in group)
        if len({p.patch.value for p in group}) == 1:
            duplicates.append("%s written %s times, all %s (%s)"
                              % (_hex32(vaddr), len(group),
                                 _hex32(group[0].patch.value), where))
        else:
            conflicts.append(
                "%s written %s times with different values: %s (%s)"
                % (_hex32(vaddr), len(group),
                   ", ".join(_hex32(p.patch.value) for p in group), where))
    # Overlapping but not identical addresses: only reachable with unaligned
    # patch lines, which is exactly when nobody is checking by eye.
    owner: Dict[int, Placement] = {}
    for place in placements:
        if not place.patch.enabled or place.file_offset is None:
            continue
        for byte in range(place.file_offset, place.file_offset + 4):
            previous = owner.get(byte)
            if previous is not None and previous.patch.vaddr != place.patch.vaddr:
                conflicts.append(
                    "%s (%s:%d) overlaps %s (%s:%d) at file byte %d"
                    % (_hex32(place.patch.vaddr), place.patch.source,
                       place.patch.line, _hex32(previous.patch.vaddr),
                       previous.patch.source, previous.patch.line, byte))
            owner[byte] = place
    return duplicates, sorted(set(conflicts))


# --------------------------------------------------------------------------
# the bake
# --------------------------------------------------------------------------

def bake(elf: Elf32, placements: Sequence[Placement]
         ) -> Tuple[bytes, List[dict]]:
    """Apply every applicable patch. Returns (new bytes, manifest records).

    Callers must have refused on `.bss` and `outside` first; this asserts it
    rather than skipping quietly.
    """
    data = bytearray(elf.data)
    records: List[dict] = []
    for place in placements:
        patch = place.patch
        record = {
            "source": patch.source,
            "line": patch.line,
            "enabled": patch.enabled,
            "class": place.kind,
            "vaddr": _hex32(patch.vaddr),
            "new": _hex32(patch.value),
        }
        if place.kind != FILE_BACKED:
            if patch.enabled:
                raise BakeError("refused patch reached bake(): %s:%d %s (%s)"
                                % (patch.source, patch.line,
                                   _hex32(patch.vaddr), place.reason))
            record["written"] = False
            record["note"] = ("patch=0, and %s -- parked, not applied"
                              % place.reason)
            records.append(record)
            continue
        record["file_offset"] = _hex32(place.file_offset)
        record["old"] = _hex32(struct.unpack_from("<I", data,
                                                  place.file_offset)[0])
        record["aligned"] = (patch.vaddr % 4 == 0)
        if patch.enabled:
            struct.pack_into("<I", data, place.file_offset, patch.value)
            record["written"] = True
            record["changed"] = record["old"] != record["new"]
        else:
            record["written"] = False
            record["note"] = "patch=0 -- parked in the pnach, not applied"
        records.append(record)
    return bytes(data), records


def verify(before: bytes, after: bytes, records: Sequence[dict]) -> List[str]:
    """Read the result back and complain about anything that is not intended.

    Three questions, all cheap: is it still the same size, did every word land,
    and did anything *else* change. The third is the one that catches a bug in
    this tool rather than in the pnach.

    "Intended" is the manifest replayed onto the input in file order, which is
    also how the cheat engine resolves two lines aimed at one address: the last
    one wins. A word an allowed conflict later overwrote is not reported as a
    word that failed to land -- it is reported by the replay, or not at all.
    """
    problems: List[str] = []
    if len(after) != len(before):
        problems.append("the output is %d bytes, the input was %d -- a word "
                        "bake must not change the size"
                        % (len(after), len(before)))
        return problems

    written = [r for r in records if r.get("written")]
    expected = bytearray(before)
    owner: Dict[int, int] = {}
    for index, record in enumerate(written):
        offset = int(record["file_offset"], 16)
        struct.pack_into("<I", expected, offset, int(record["new"], 16))
        for byte in range(offset, offset + 4):
            owner[byte] = index

    for index, record in enumerate(written):
        offset = int(record["file_offset"], 16)
        if any(owner[byte] != index for byte in range(offset, offset + 4)):
            continue                        # a later line overwrote this word
        got = struct.unpack_from("<I", after, offset)[0]
        if _hex32(got) != record["new"]:
            problems.append("%s (file offset %s): expected %s, read %s"
                            % (record["vaddr"], record["file_offset"],
                               record["new"], _hex32(got)))

    if bytes(expected) != after:             # one memcmp; only then, the hunt
        for index in range(len(before)):
            if expected[index] == after[index]:
                continue
            problems.append(
                "byte %d is %02X, the manifest says %02X (%s)"
                % (index, after[index], expected[index],
                   "no patch covers it" if index not in owner
                   else "inside a patched word"))
            if len(problems) > 8:
                problems.append("... and more")
                break
    return problems


def summarise(placements: Sequence[Placement], records: Sequence[dict],
              duplicates: Sequence[str], conflicts: Sequence[str]) -> dict:
    applied = [p for p in placements if p.patch.enabled]
    return {
        "patch_lines": len(placements),
        "applied": len(applied),
        "parked": len(placements) - len(applied),
        "file_backed": sum(1 for p in applied if p.kind == FILE_BACKED),
        "bss": sum(1 for p in applied if p.kind == BSS),
        "outside": sum(1 for p in applied if p.kind == OUTSIDE),
        "words_written": sum(1 for r in records if r.get("written")),
        "already_matching": sum(1 for r in records
                                if r.get("written") and not r.get("changed")),
        "duplicate_addresses": len(duplicates),
        "conflicts": len(conflicts),
    }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_manifest(elf: Elf32, output: Optional[Path], after: Optional[bytes],
                   records: Sequence[dict], summary: dict,
                   duplicates: Sequence[str], conflicts: Sequence[str],
                   notes: Sequence[str], sources: Sequence[Path]) -> dict:
    """The audit and revert record: every word, its old value and its new one."""
    return {
        "tool": "tools/bake_pnach.py",
        "manifest_version": 1,
        "elf": {
            "input": str(elf.path),
            "size": len(elf.data),
            "sha256_before": _sha256(elf.data),
            "output": str(output) if output else None,
            "sha256_after": _sha256(after) if after is not None else None,
            "entry": _hex32(elf.entry),
            "segments": [
                {
                    "index": seg.index,
                    "file_offset": _hex32(seg.offset),
                    "vaddr": _hex32(seg.vaddr),
                    "filesz": _hex32(seg.filesz),
                    "memsz": _hex32(seg.memsz),
                    "delta": _hex32(seg.delta),
                    "matches_convention": seg.delta == CONVENTION_DELTA,
                }
                for seg in elf.segments
            ],
        },
        "pnach": [
            {"path": str(path), "sha256": _sha256(path.read_bytes())}
            for path in sources
        ],
        "summary": summary,
        "duplicates": list(duplicates),
        "conflicts": list(conflicts),
        "notes": list(notes),
        "patches": list(records),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _refusal(placements: Sequence[Placement], kind: str) -> List[Placement]:
    return [p for p in placements if p.patch.enabled and p.kind == kind]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bake a pnach's word patches into the ELF they patch, so "
                    "the game carries them with no cheat engine running.",
        epilog="A baked word is written once, at mastering; a pnach word is "
               "re-stamped every vsync. They are equivalent only if the game "
               "never writes that address itself -- see trap T1 in "
               "docs/pnach-to-iso-pipeline.md. This tool cannot check that.")
    parser.add_argument("elf", help="the target, e.g. extract/SLUS_207.52")
    parser.add_argument("pnach", nargs="+",
                        help="one or more pnach files, applied in order")
    parser.add_argument("-o", "--output",
                        help="write the patched ELF here (required unless "
                             "--audit); never the input")
    parser.add_argument("--manifest",
                        help="write the JSON manifest here ('-' for stdout). "
                             "Default: OUTPUT.manifest.json, or stdout when "
                             "auditing.")
    parser.add_argument("--audit", action="store_true",
                        help="classify and report only; write no ELF")
    parser.add_argument("--verify", action="store_true",
                        help="re-read the output and confirm every word "
                             "landed, and that nothing else moved")
    parser.add_argument("--allow-conflicts", action="store_true",
                        help="bake anyway when two patches fight over one "
                             "address (last line wins, as the cheat engine "
                             "would)")
    args = parser.parse_args(argv)

    source = Path(args.elf)
    if not source.exists():
        print("error: no ELF at %s" % source, file=sys.stderr)
        return 2
    if not args.audit and not args.output:
        print("error: give -o OUTPUT (or --audit to classify without writing)",
              file=sys.stderr)
        return 2

    output = Path(args.output) if args.output else None
    if output is not None:
        if output.exists() and output.samefile(source):
            print("error: -o names the input. The original is the only copy "
                  "of the stock binary; write somewhere else.", file=sys.stderr)
            return 2
        if output.resolve() == source.resolve():
            print("error: -o names the input. Write somewhere else.",
                  file=sys.stderr)
            return 2

    try:
        elf = Elf32.from_path(source)
        sources = [Path(p) for p in args.pnach]
        patches: List[Patch] = []
        for path in sources:
            patches.extend(load_pnach(path))
    except (BakeError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    notes = convention_notes(elf)
    for seg in elf.segments:
        print("%s: PT_LOAD %d  offset %s  vaddr %s  filesz %s  memsz %s  "
              "(vaddr = file_offset + 0x%X)"
              % (source, seg.index, _hex32(seg.offset), _hex32(seg.vaddr),
                 _hex32(seg.filesz), _hex32(seg.memsz), seg.delta))
    for note in notes:
        print("warning: %s" % note)

    placements = [classify(patch, elf) for patch in patches]
    duplicates, conflicts = collisions(placements)
    applied = [p for p in placements if p.patch.enabled]
    print("%d patch line%s (%d applied, %d parked at patch=0)"
          % (len(placements), "" if len(placements) == 1 else "s",
             len(applied), len(placements) - len(applied)))
    print("  file-backed %d | .bss %d | outside %d"
          % (sum(1 for p in applied if p.kind == FILE_BACKED),
             sum(1 for p in applied if p.kind == BSS),
             sum(1 for p in applied if p.kind == OUTSIDE)))
    for text in duplicates:
        print("  duplicate: %s" % text)
    for text in conflicts:
        print("  conflict:  %s" % text)

    failed = False
    for kind, headline in ((OUTSIDE,
                            "outside every loaded segment. The pnach is "
                            "stale, aimed at another binary, or mistyped."),
                           (BSS,
                            "in .bss, which has no bytes in the file. Baking "
                            "cannot express them: the loader zeroes that "
                            "region before main() runs. They need an init "
                            "hook, which this pipeline does not have yet "
                            "(docs/pnach-to-iso-pipeline.md, P1).")):
        bad = _refusal(placements, kind)
        if not bad:
            continue
        failed = True
        print("error: %d patch line%s %s %s"
              % (len(bad), "" if len(bad) == 1 else "s",
                 "lands" if len(bad) == 1 else "land", headline),
              file=sys.stderr)
        for place in bad[:20]:
            print("  %s:%d  %s = %s -- %s"
                  % (place.patch.source, place.patch.line,
                     _hex32(place.patch.vaddr), _hex32(place.patch.value),
                     place.reason), file=sys.stderr)
        if len(bad) > 20:
            print("  ... and %d more" % (len(bad) - 20), file=sys.stderr)
    if conflicts and not args.allow_conflicts:
        failed = True
        print("error: %d address conflict%s -- two applied patches want "
              "different words in the same place. Resolve the pnach, or pass "
              "--allow-conflicts to take the last line as the cheat engine "
              "would." % (len(conflicts), "" if len(conflicts) == 1 else "s"),
              file=sys.stderr)
    if failed:
        print("nothing was written.", file=sys.stderr)
        return 2

    try:
        after, records = bake(elf, placements)
    except BakeError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    summary = summarise(placements, records, duplicates, conflicts)

    if not args.audit:
        try:
            output.write_bytes(after)
        except OSError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
        print("%s: %d words written (%d already matched), %d bytes, size "
              "unchanged" % (output, summary["words_written"],
                             summary["already_matching"], len(after)))

    if args.verify:
        try:
            written_back = output.read_bytes() if output else after
        except OSError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
        problems = verify(elf.data, written_back, records)
        if problems:
            print("error: the output does not read back as intended:",
                  file=sys.stderr)
            for problem in problems:
                print("  %s" % problem, file=sys.stderr)
            return 2
        print("verify: %d/%d words read back as intended; no other byte "
              "differs from the input"
              % (summary["words_written"], summary["words_written"]))

    manifest = build_manifest(elf, output, None if args.audit else after,
                              records, summary, duplicates, conflicts, notes,
                              sources)
    target = args.manifest
    if target is None:
        target = "-" if args.audit else str(output) + ".manifest.json"
    text = json.dumps(manifest, indent=2)
    if target == "-":
        print(text)
    else:
        try:
            Path(target).write_text(text + "\n", encoding="utf-8")
        except OSError as exc:
            print("error: manifest: %s" % exc, file=sys.stderr)
            return 2
        print("manifest: %s (%d records -- old and new word for every line, "
              "which is also how to revert)" % (target, len(records)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Write byte patches into an Xbox executable, so the fixes ship inside the XBE.

The Xbox half of the ship loop. `tools/bake_pnach.py` writes words into the PS2
ELF; this writes bytes into `default.xbe`, and `tools/patch_xiso.py` drops the
result back into the disc image (`docs/pnach-to-xbe-pipeline.md` §§6, 8). It is
deliberately *plumbing*: it does not decide what to patch, it takes a patch spec
as input and is answerable only for putting those bytes where they belong,
refusing loudly when it cannot, and proving afterwards that it did.

WHY A BYTE PATCH CAN BE WRITTEN INTO THE FILE AT ALL

An XBE is the Xbox's PE analogue: a header block that loads at `ImageBase`, then
sections, each with a raw file range and a virtual range. A patch names a
*virtual* address because that is what a disassembler, a debugger and the
correspondence map all speak. When that address falls inside a section's
`[raw_off, raw_off + raw_size)` window it has exactly one file byte behind it --
`raw_off + (va - vaddr)` -- and writing there is what the loader will map.

THE FIVE OUTCOMES, AND WHY FOUR OF THEM REFUSE

Not every virtual address has a file byte, and the difference is invisible in the
patch spec, which is the whole reason this tool classifies before it writes:

* **file-backed** -- writable. The normal case: code and data inside a section.
* **zero-fill** -- REFUSED. Five sections of this image declare more virtual size
  than raw size; `.data` alone declares 674,796 bytes of it. That is the Xbox's
  `.bss`: the loader zeroes it on the way up, so a byte written there has no file
  home and would be erased before `main()` even if it did. Same answer as
  `bake_pnach.py` gives PS2 `.bss` -- refuse by name, print the span, write
  nothing.
* **straddle** -- REFUSED. A span that starts inside one section and ends past
  its end is two patches wearing one address; the second half would land in the
  next section, or in a file gap that is mapped nowhere.
* **headers** -- REFUSED. The header block is mapped, so an address in it looks
  writable, but every byte there is a structural field (section table, cert,
  digests). Editing those is the section-append phase's job, under its own
  verification, not a byte patch's.
* **outside** -- an error. Nothing maps it: the spec is stale, aimed at the PS2
  binary, or mistyped.

WHAT THIS TOOL DELIBERATELY WILL NOT DO

* **Change the file's size.** A replacement is exactly as long as the span it
  replaces. Growth means a new section, a relocated section table and a repointed
  directory record on the disc; that is a later phase with its own acceptance
  tests, and pretending a byte patch can do it is how a file grows by two bytes
  and stops booting.
* **Guess a stock value.** A patch may declare the bytes it expects to find
  (`:expect`), and a mismatch is a refusal -- it is the cheapest available proof
  that the address is wrong, and it costs nothing to check.
* **Invent a digest.** Section headers carry a SHA-1 at `+0x24`. Ten of the
  eleven in the retail image verify as `SHA-1( le32(raw_size) || raw_bytes )`;
  `.text` does not reproduce under that rule or the obvious variants
  (`docs/pnach-to-xbe-pipeline.md` §7b). So a touched section whose digest
  verified beforehand gets a recomputed one, and a touched section whose digest
  did *not* verify is left alone and reported loudly. Writing a "correct" digest
  over a section whose rule we demonstrably do not know would be a guess wearing
  a checksum's clothes.
* **Touch the input.** `-o` is required and may not name the input file.

Usage::

    python3 tools/patch_xbe.py extract/xbox/default.xbe \\
        --patch 0x000A4496=EB23 -o out.xbe --verify
    python3 tools/patch_xbe.py extract/xbox/default.xbe patches/c1.json --audit

Patch spec, either format (the tool sniffs which)::

    # text: VA = new bytes [: bytes expected to be there now]
    0x000A4496 = EB 23 : 74 23

    {"patches": [{"va": "0x000A4496", "new": "EB23", "expect": "7423",
                  "note": "C1: force the eligibility branch"}]}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recon.xbe import SECTION_HEADER_SIZE, Xbe, XbeError  # noqa: E402

#: Where this title's image is based. Derived from the header every time; the
#: constant only cross-checks, because a hardcoded base is how a tool keeps
#: writing confidently to the wrong place after the binary changes.
CONVENTION_BASE = 0x00010000

#: The SHA-1 each section header carries, and where it sits inside the header.
DIGEST_OFFSET = 0x24
DIGEST_SIZE = 20

#: The one digest rule that reproduces on this image (10 of 11 sections).
DIGEST_RULE = "SHA-1(le32(raw_size) || raw_bytes)"

FILE_BACKED = "file-backed"
ZERO_FILL = "zero-fill"
STRADDLE = "straddle"
HEADERS = "headers"
OUTSIDE = "outside"

#: JSON keys a patch record may carry. Anything else is an error: a key this
#: tool silently ignored would be an intent that silently did not ship.
JSON_KEYS = {"va", "address", "new", "bytes", "expect", "stock", "enabled",
             "note", "length"}

#: Some editors leave a byte-order mark on the first line.
_BOM = "\ufeff"


class PatchError(Exception):
    pass


class Patch(NamedTuple):
    """One patch: replace the bytes at a virtual address with these."""

    source: str
    line: int
    enabled: bool               # a parked patch is recorded and not written
    vaddr: int
    new: bytes
    expect: Optional[bytes]     # bytes the spec says are there now, or None
    note: str

    @property
    def length(self) -> int:
        return len(self.new)


class Placement(NamedTuple):
    """A patch, and where (if anywhere) it lands in the file."""

    patch: Patch
    kind: str                   # FILE_BACKED | ZERO_FILL | STRADDLE | ...
    file_offset: Optional[int]
    section: Optional[int]      # index into Xbe.sections
    reason: str


def _hex32(value: int) -> str:
    return "0x%08X" % value


def _hexbytes(data: Optional[bytes]) -> Optional[str]:
    return None if data is None else data.hex().upper()


# --------------------------------------------------------------------------
# the patch spec
# --------------------------------------------------------------------------

def _parse_hex_int(text: str, what: str, source: str, line: int) -> int:
    body = text[2:] if text[:2].lower() == "0x" else text
    if not body or any(c not in "0123456789abcdefABCDEF" for c in body):
        raise PatchError("%s:%d: %s %r is not hexadecimal"
                         % (source, line, what, text))
    value = int(body, 16)
    if value > 0xFFFFFFFF:
        raise PatchError("%s:%d: %s %r does not fit in 32 bits"
                         % (source, line, what, text))
    return value


def _parse_hex_bytes(text: str, what: str, source: str, line: int) -> bytes:
    """`EB 0F`, `EB0F`, `eb-0f` and `0xEB,0x0F` all mean the same two bytes."""
    body = text.strip()
    for junk in (" ", "\t", ",", "-", "_", "0x", "0X"):
        body = body.replace(junk, "")
    if not body:
        raise PatchError("%s:%d: %s is empty -- a patch that replaces nothing "
                         "is a patch that did not ship" % (source, line, what))
    if len(body) % 2:
        raise PatchError("%s:%d: %s %r has an odd number of hex digits; bytes "
                         "come in pairs" % (source, line, what, text))
    try:
        return bytes.fromhex(body)
    except ValueError:
        raise PatchError("%s:%d: %s %r is not hexadecimal"
                         % (source, line, what, text))


def _build(source: str, line: int, vaddr_text: str, new_text: str,
           expect_text: Optional[str], enabled: bool = True,
           note: str = "", declared_length: Optional[int] = None) -> Patch:
    vaddr = _parse_hex_int(vaddr_text, "address", source, line)
    new = _parse_hex_bytes(new_text, "replacement", source, line)
    expect = (None if expect_text is None
              else _parse_hex_bytes(expect_text, "expected bytes", source, line))
    if expect is not None and len(expect) != len(new):
        raise PatchError(
            "%s:%d: %s replaces %d byte%s but declares %d expected -- this "
            "tool is same-size only, and a replacement of a different length "
            "would move every byte after it. Growth needs a new section "
            "(docs/pnach-to-xbe-pipeline.md §7b), not a byte patch."
            % (source, line, _hex32(vaddr), len(new),
               "" if len(new) == 1 else "s", len(expect)))
    if declared_length is not None and declared_length != len(new):
        raise PatchError(
            "%s:%d: %s declares length %d but gives %d byte%s. Same-size only: "
            "say what you mean or the file changes size."
            % (source, line, _hex32(vaddr), declared_length, len(new),
               "" if len(new) == 1 else "s"))
    return Patch(source=source, line=line, enabled=enabled, vaddr=vaddr,
                 new=new, expect=expect, note=note)


def parse_cli_patch(text: str, index: int = 1) -> Patch:
    """`--patch VA=NEWBYTES[:EXPECTBYTES]`."""
    source = "--patch"
    address, sep, rest = text.partition("=")
    if not sep:
        raise PatchError("%s:%d: %r is not VA=HEXBYTES (optionally "
                         "VA=HEXBYTES:EXPECTEDBYTES)" % (source, index, text))
    new, has_expect, expect = rest.partition(":")
    return _build(source, index, address.strip(), new,
                  expect if has_expect else None)


def parse_text(text: str, source: str = "<patches>") -> List[Patch]:
    """One `VA = NEWBYTES [: EXPECTEDBYTES]` per line, `#`/`//`/`;` comments.

    Anything else raises. A directive this tool skipped would be a patch that
    silently did not ship, which is the failure `bake_pnach.py` was written to
    make impossible on the PS2 side.
    """
    patches: List[Patch] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.lstrip(_BOM).split("//", 1)[0].split("#", 1)[0]
        line = line.split(";", 1)[0].strip()
        if not line:
            continue
        address, sep, rest = line.partition("=")
        if not sep:
            raise PatchError("%s:%d: not a 'VA = BYTES' line: %s"
                             % (source, number, line))
        new, has_expect, expect = rest.partition(":")
        patches.append(_build(source, number, address.strip(), new,
                              expect if has_expect else None))
    return patches


def parse_json(text: str, source: str = "<patches>") -> List[Patch]:
    """`[{...}]` or `{"patches": [{...}]}`; keys in :data:`JSON_KEYS`."""
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise PatchError("%s: not valid JSON: %s" % (source, exc))
    if isinstance(document, dict):
        if "patches" not in document:
            raise PatchError("%s: a JSON patch spec needs a 'patches' list"
                             % source)
        records = document["patches"]
    else:
        records = document
    if not isinstance(records, list):
        raise PatchError("%s: 'patches' is %s, not a list"
                         % (source, type(records).__name__))

    patches: List[Patch] = []
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            raise PatchError("%s: patch %d is %s, not an object"
                             % (source, index, type(record).__name__))
        unknown = sorted(set(record) - JSON_KEYS)
        if unknown:
            raise PatchError(
                "%s: patch %d has unknown key%s %s. Known: %s. Refusing rather "
                "than ignoring a field that may have been the intent."
                % (source, index, "" if len(unknown) == 1 else "s",
                   ", ".join(repr(k) for k in unknown),
                   ", ".join(sorted(JSON_KEYS))))
        address = record.get("va", record.get("address"))
        new = record.get("new", record.get("bytes"))
        if address is None or new is None:
            raise PatchError("%s: patch %d needs both 'va' and 'new'"
                             % (source, index))
        expect = record.get("expect", record.get("stock"))
        enabled = record.get("enabled", True)
        if not isinstance(enabled, bool):
            raise PatchError("%s: patch %d: 'enabled' is %r, not true/false"
                             % (source, index, enabled))
        length = record.get("length")
        if length is not None and not isinstance(length, int):
            raise PatchError("%s: patch %d: 'length' is %r, not a number"
                             % (source, index, length))
        patches.append(_build(
            source, index,
            address if isinstance(address, str) else "%X" % address,
            new if isinstance(new, str) else "",
            None if expect is None else str(expect),
            enabled=enabled, note=str(record.get("note", "")),
            declared_length=length))
    return patches


def load_patch_file(path: Path) -> List[Patch]:
    """Text or JSON, sniffed from the content rather than the suffix."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise PatchError("%s" % exc)
    head = text.lstrip(_BOM + " \t\r\n")
    if head[:1] in ("{", "["):
        return parse_json(text, str(path))
    return parse_text(text, str(path))


# --------------------------------------------------------------------------
# classification -- the offset arithmetic and the four refusals
# --------------------------------------------------------------------------

def check_sections(xbe: Xbe) -> None:
    """Every section's raw range must be inside the file. Loud if not."""
    for index, section in enumerate(xbe.sections):
        end = section.raw_off + section.raw_size
        if end > len(xbe.data):
            raise PatchError(
                "section %d (%s) claims file bytes %s..%s but the file is only "
                "%d bytes -- truncated download, or not the image the headers "
                "describe" % (index, section.name, _hex32(section.raw_off),
                              _hex32(end), len(xbe.data)))
        if section.vsize < section.raw_size:
            raise PatchError("section %d (%s) has virtual size %s below its raw "
                             "size %s" % (index, section.name,
                                          _hex32(section.vsize),
                                          _hex32(section.raw_size)))


def classify(patch: Patch, xbe: Xbe) -> Placement:
    """Where a patch's bytes land: in the file, in zero-fill, or nowhere."""
    start = patch.vaddr
    end = patch.vaddr + patch.length
    for index, section in enumerate(xbe.sections):
        mapped_end = section.vaddr + section.vsize
        if not section.vaddr <= start < mapped_end:
            continue
        file_end = section.vaddr + section.raw_size
        if start >= file_end:
            return Placement(
                patch, ZERO_FILL, None, index,
                "section %s declares %s..%s as zero fill with no bytes in the "
                "file (%d bytes of it) -- the Xbox's .bss. The loader zeroes it "
                "before main() runs" % (section.name, _hex32(file_end),
                                        _hex32(mapped_end),
                                        section.vsize - section.raw_size))
        if end <= file_end:
            return Placement(patch, FILE_BACKED,
                             section.raw_off + (start - section.vaddr),
                             index, "")
        if end <= mapped_end:
            return Placement(
                patch, ZERO_FILL, None, index,
                "the span crosses the end of section %s's file image (%s); "
                "only its first %d of %d bytes exist in the file, the rest is "
                "zero fill" % (section.name, _hex32(file_end),
                               file_end - start, patch.length))
        return Placement(
            patch, STRADDLE, None, index,
            "the span runs past the end of section %s (%s) into whatever is "
            "mapped next; a patch that straddles a section boundary is two "
            "patches wearing one address"
            % (section.name, _hex32(mapped_end)))

    if xbe.base <= start < xbe.base + xbe.size_of_headers:
        return Placement(
            patch, HEADERS, None, None,
            "inside the XBE header block (%s..%s): the section table, the "
            "certificate and the section digests live there. Structural edits "
            "belong to the section-append phase, not to a byte patch"
            % (_hex32(xbe.base), _hex32(xbe.base + xbe.size_of_headers)))

    spans = ", ".join("%s..%s" % (_hex32(s.vaddr), _hex32(s.vaddr + s.vsize))
                      for s in xbe.sections)
    return Placement(patch, OUTSIDE, None, None,
                     "no section maps it (mapped: %s)" % spans)


def collisions(placements: Sequence[Placement]) -> Tuple[List[str], List[str]]:
    """(duplicates, conflicts) among the applied patches.

    A duplicate is the same span written the same bytes twice -- harmless, worth
    saying. A conflict is two applied patches whose spans overlap and disagree:
    the file can only hold one of them, so writing either is a coin toss between
    two intentions. That is a refusal, not a note.
    """
    duplicates: List[str] = []
    conflicts: List[str] = []
    owner: Dict[int, int] = {}
    reported = set()
    live = [(index, place) for index, place in enumerate(placements)
            if place.patch.enabled and place.file_offset is not None]
    for index, place in live:
        for byte in range(place.file_offset, place.file_offset + place.patch.length):
            previous = owner.get(byte)
            if previous is not None and previous != index \
                    and (previous, index) not in reported:
                reported.add((previous, index))
                first, second = placements[previous].patch, place.patch
                same = (first.vaddr == second.vaddr and first.new == second.new)
                text = ("%s (%s:%d) and %s (%s:%d) overlap from file byte %d"
                        % (_hex32(second.vaddr), second.source, second.line,
                           _hex32(first.vaddr), first.source, first.line, byte))
                (duplicates if same else conflicts).append(
                    text + (" with the same bytes" if same
                            else " with different bytes"))
            owner[byte] = index
    return sorted(set(duplicates)), sorted(set(conflicts))


def stock_mismatches(placements: Sequence[Placement], data: bytes) -> List[str]:
    """Every applied patch whose `expect` is not what the file holds."""
    problems = []
    for place in placements:
        patch = place.patch
        if (not patch.enabled or patch.expect is None
                or place.file_offset is None):
            continue
        found = data[place.file_offset:place.file_offset + patch.length]
        if found != patch.expect:
            problems.append(
                "%s (%s:%d): expected %s, the file holds %s -- the address is "
                "wrong, or this XBE is not the build the patch was written "
                "against" % (_hex32(patch.vaddr), patch.source, patch.line,
                             _hexbytes(patch.expect), _hexbytes(found)))
    return problems


# --------------------------------------------------------------------------
# section digests
# --------------------------------------------------------------------------

def section_header_offset(xbe: Xbe, index: int) -> int:
    """File offset of section *index*'s header. Headers load at `base`."""
    table = xbe.section_headers_va - xbe.base
    offset = table + index * SECTION_HEADER_SIZE
    if offset < 0 or offset + SECTION_HEADER_SIZE > len(xbe.data):
        raise PatchError("section header %d is outside the file" % index)
    return offset


def computed_digest(data: bytes, raw_off: int, raw_size: int) -> bytes:
    """The rule that reproduces 10 of this image's 11 stored digests."""
    return hashlib.sha1(raw_size.to_bytes(4, "little")
                        + bytes(data[raw_off:raw_off + raw_size])).digest()


def stored_digest(data: bytes, header_offset: int) -> bytes:
    start = header_offset + DIGEST_OFFSET
    return bytes(data[start:start + DIGEST_SIZE])


def digest_survey(xbe: Xbe) -> List[dict]:
    """Per section: the stored digest, the rule's digest, and whether they agree.

    Run against the *input*, so a later "this one did not verify beforehand" is
    a measurement rather than a consequence of our own writing.
    """
    survey = []
    for index, section in enumerate(xbe.sections):
        header = section_header_offset(xbe, index)
        stored = stored_digest(xbe.data, header)
        rule = computed_digest(xbe.data, section.raw_off, section.raw_size)
        survey.append({
            "section": section.name,
            "index": index,
            "header_offset": _hex32(header),
            "digest_offset": _hex32(header + DIGEST_OFFSET),
            "stored": stored.hex(),
            "rule": rule.hex(),
            "verified_before": stored == rule,
        })
    return survey


def apply_digests(data: bytearray, xbe: Xbe, touched: Sequence[int],
                  survey: Sequence[dict], fix: bool
                  ) -> Tuple[List[dict], List[str]]:
    """Recompute the digest of every touched section whose rule holds.

    Returns (records, warnings). A section whose *stored* digest did not verify
    before we touched it -- `.text` in the retail image -- is left exactly as it
    was: writing a digest under a rule that demonstrably does not describe this
    section would be a guess wearing a checksum's clothes. The warning says so,
    and says what it costs.
    """
    records: List[dict] = []
    warnings: List[str] = []
    for index in sorted(set(touched)):
        section = xbe.sections[index]
        entry = survey[index]
        header = section_header_offset(xbe, index)
        record = {
            "section": section.name,
            "index": index,
            "digest_offset": _hex32(header + DIGEST_OFFSET),
            "rule": DIGEST_RULE,
            "verified_before": entry["verified_before"],
            "before": entry["stored"],
        }
        if not fix:
            record["action"] = "left stale (--no-fix-digests)"
            record["after"] = entry["stored"]
            warnings.append(
                "section %s is modified and its digest is left as it is "
                "(--no-fix-digests): it describes the bytes that used to be "
                "there. A loader that enforces section digests may reject this "
                "XBE; a softmodded kernel launching from HDD generally does not."
                % section.name)
        elif not entry["verified_before"]:
            record["action"] = "left untouched (stored digest does not follow the rule)"
            record["after"] = entry["stored"]
            warnings.append(
                "section %s is modified, but its stored digest does not "
                "reproduce under %s -- it did not before this run either "
                "(docs/pnach-to-xbe-pipeline.md §7b records the same anomaly "
                "for .text). Its digest is left exactly as it is rather than "
                "overwritten with a value computed under a rule this section "
                "demonstrably does not follow. Consequence: the section is "
                "modified and carries its original digest. A loader that "
                "enforces section digests may reject this XBE; a softmodded "
                "kernel launching from HDD generally does not."
                % (section.name, DIGEST_RULE))
        else:
            fresh = computed_digest(data, section.raw_off, section.raw_size)
            start = header + DIGEST_OFFSET
            data[start:start + DIGEST_SIZE] = fresh
            record["action"] = "recomputed"
            record["after"] = fresh.hex()
        records.append(record)
    return records, warnings


# --------------------------------------------------------------------------
# the write
# --------------------------------------------------------------------------

def apply_patches(xbe: Xbe, placements: Sequence[Placement]
                  ) -> Tuple[bytearray, List[dict], List[int]]:
    """Apply every applicable patch. Returns (bytes, records, touched sections).

    Callers must have refused on everything but `file-backed` first; this
    asserts it rather than skipping quietly.
    """
    data = bytearray(xbe.data)
    records: List[dict] = []
    touched: List[int] = []
    for place in placements:
        patch = place.patch
        section = (xbe.sections[place.section].name
                   if place.section is not None else None)
        record = {
            "source": patch.source,
            "line": patch.line,
            "enabled": patch.enabled,
            "class": place.kind,
            "vaddr": _hex32(patch.vaddr),
            "section": section,
            "length": patch.length,
            "new": _hexbytes(patch.new),
            "expect": _hexbytes(patch.expect),
            "note": patch.note,
        }
        if place.kind != FILE_BACKED:
            if patch.enabled:
                raise PatchError("refused patch reached the writer: %s:%d %s "
                                 "(%s)" % (patch.source, patch.line,
                                           _hex32(patch.vaddr), place.reason))
            record["written"] = False
            record["reason"] = place.reason
            records.append(record)
            continue
        offset = place.file_offset
        record["file_offset"] = _hex32(offset)
        record["old"] = _hexbytes(bytes(data[offset:offset + patch.length]))
        if patch.enabled:
            data[offset:offset + patch.length] = patch.new
            record["written"] = True
            record["changed"] = record["old"] != record["new"]
            if place.section is not None:
                touched.append(place.section)
        else:
            record["written"] = False
            record["reason"] = "parked (enabled=false), not applied"
        records.append(record)
    return data, records, touched


def verify(before: bytes, after: bytes, records: Sequence[dict],
           digests: Sequence[dict]) -> List[str]:
    """Read the result back and complain about anything that is not intended.

    Three questions, all cheap: is it still the same size, did every byte land,
    and did anything *else* change. The third is the one that catches a bug in
    this tool rather than in the patch spec -- and "anything else" includes a
    digest, so a recomputed digest is checked against the manifest exactly like a
    patched instruction, and a stray byte anywhere in 4.9 MB is found.
    """
    problems: List[str] = []
    if len(after) != len(before):
        problems.append("the output is %d bytes, the input was %d -- a byte "
                        "patch must not change the size"
                        % (len(after), len(before)))
        return problems

    expected = bytearray(before)
    spans: List[Tuple[int, int, str]] = []
    for record in records:
        if not record.get("written"):
            continue
        offset = int(record["file_offset"], 16)
        new = bytes.fromhex(record["new"])
        expected[offset:offset + len(new)] = new
        spans.append((offset, len(new), record["vaddr"]))
    for record in digests:
        if record.get("action") != "recomputed":
            continue
        offset = int(record["digest_offset"], 16)
        fresh = bytes.fromhex(record["after"])
        expected[offset:offset + len(fresh)] = fresh
        spans.append((offset, len(fresh), "digest of %s" % record["section"]))

    covered = set()
    for offset, length, _label in spans:
        covered.update(range(offset, offset + length))

    for offset, length, label in spans:
        got = bytes(after[offset:offset + length])
        want = bytes(expected[offset:offset + length])
        if got != want:
            problems.append("%s (file offset %s): expected %s, read %s"
                            % (label, _hex32(offset), want.hex().upper(),
                               got.hex().upper()))

    if bytes(expected) != after:            # one memcmp; only then, the hunt
        for index in range(len(before)):
            if expected[index] == after[index]:
                continue
            if index in covered:
                continue                    # already reported, span by span
            problems.append(
                "byte %d (file offset %s) is %02X, the input had %02X and no "
                "patch covers it" % (index, _hex32(index), after[index],
                                     before[index]))
            if len(problems) > 8:
                problems.append("... and more")
                break
    return problems


def reparse(after: bytes, xbe: Xbe) -> List[str]:
    """The output must still parse as the same XBE. Structural, not byte-wise."""
    try:
        fresh = Xbe(after, path="<output>")
    except XbeError as exc:
        return ["the output does not parse as an XBE any more: %s" % exc]
    problems = []
    if fresh.entry != xbe.entry:
        problems.append("the entry point moved: %s -> %s"
                        % (_hex32(xbe.entry), _hex32(fresh.entry)))
    if len(fresh.sections) != len(xbe.sections):
        problems.append("the section count changed: %d -> %d"
                        % (len(xbe.sections), len(fresh.sections)))
        return problems
    for old, new in zip(xbe.sections, fresh.sections):
        if (old.name, old.vaddr, old.vsize, old.raw_off, old.raw_size) != \
                (new.name, new.vaddr, new.vsize, new.raw_off, new.raw_size):
            problems.append("section %s's geometry changed" % old.name)
    return problems


def summarise(placements: Sequence[Placement], records: Sequence[dict],
              duplicates: Sequence[str], conflicts: Sequence[str],
              digests: Sequence[dict]) -> dict:
    applied = [p for p in placements if p.patch.enabled]
    return {
        "patches": len(placements),
        "applied": len(applied),
        "parked": len(placements) - len(applied),
        "file_backed": sum(1 for p in applied if p.kind == FILE_BACKED),
        "zero_fill": sum(1 for p in applied if p.kind == ZERO_FILL),
        "straddle": sum(1 for p in applied if p.kind == STRADDLE),
        "headers": sum(1 for p in applied if p.kind == HEADERS),
        "outside": sum(1 for p in applied if p.kind == OUTSIDE),
        "bytes_written": sum(r["length"] for r in records if r.get("written")),
        "spans_written": sum(1 for r in records if r.get("written")),
        "already_matching": sum(1 for r in records
                                if r.get("written") and not r.get("changed")),
        "duplicate_spans": len(duplicates),
        "conflicts": len(conflicts),
        "digests_recomputed": sum(1 for d in digests
                                  if d.get("action") == "recomputed"),
        "digests_left_alone": sum(1 for d in digests
                                  if d.get("action") != "recomputed"),
    }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def build_manifest(xbe: Xbe, output: Optional[Path], after: Optional[bytes],
                   records: Sequence[dict], summary: dict,
                   duplicates: Sequence[str], conflicts: Sequence[str],
                   digests: Sequence[dict], survey: Sequence[dict],
                   warnings: Sequence[str], sources: Sequence[Path]) -> dict:
    """The audit and revert record: old and new bytes for every edit."""
    return {
        "tool": "tools/patch_xbe.py",
        "manifest_version": 1,
        "xbe": {
            "input": str(xbe.path),
            "size": len(xbe.data),
            "sha256_before": _sha256(xbe.data),
            "output": str(output) if output else None,
            "sha256_after": _sha256(after) if after is not None else None,
            "base": _hex32(xbe.base),
            "matches_convention_base": xbe.base == CONVENTION_BASE,
            "entry": _hex32(xbe.entry),
            "build_type": xbe.build_type,
            "title_id": _hex32(xbe.cert["title_id"]),
            "size_of_image": _hex32(xbe.size_of_image),
            "size_of_headers": _hex32(xbe.size_of_headers),
            "sections": [
                {
                    "index": index,
                    "name": section.name,
                    "vaddr": _hex32(section.vaddr),
                    "vsize": _hex32(section.vsize),
                    "raw_offset": _hex32(section.raw_off),
                    "raw_size": _hex32(section.raw_size),
                    "zero_fill": section.vsize - section.raw_size,
                    "executable": section.executable,
                    "writable": section.writable,
                }
                for index, section in enumerate(xbe.sections)
            ],
        },
        "spec": [
            {"path": str(path), "sha256": _sha256(path.read_bytes())}
            for path in sources
        ],
        "summary": summary,
        "duplicates": list(duplicates),
        "conflicts": list(conflicts),
        "warnings": list(warnings),
        "digest_rule": DIGEST_RULE,
        "digest_survey": list(survey),
        "digests": list(digests),
        "patches": list(records),
    }


def _atomic_write(path: Path, data: bytes) -> None:
    """Write through a temp file and rename: an aborted run leaves no half XBE."""
    partial = path.with_name(path.name + ".partial")
    with open(partial, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _refused(placements: Sequence[Placement], kind: str) -> List[Placement]:
    return [p for p in placements if p.patch.enabled and p.kind == kind]


REFUSALS = (
    (OUTSIDE, "outside every section. The address is stale, aimed at the PS2 "
              "binary, or mistyped."),
    (ZERO_FILL, "in virtual zero fill, which has no bytes in the file. This is "
                "the Xbox's .bss: the loader zeroes it before main() runs, so a "
                "patch there cannot be expressed in the file at all "
                "(docs/pnach-to-xbe-pipeline.md §6)."),
    (STRADDLE, "straddling a section boundary. Split it into one patch per "
               "section, or fix the address."),
    (HEADERS, "inside the XBE header block. Section table, certificate and "
              "digests live there; structural edits are the section-append "
              "phase's job, not a byte patch's."),
)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write byte patches into an Xbox executable (XBE), "
                    "same-size only, with a manifest and a read-back verify.",
        epilog="A patch is (virtual address, replacement bytes). The tool "
               "classifies every one against the section table before it writes "
               "anything, and refuses zero fill, straddles, header edits and "
               "unmapped addresses by name.")
    parser.add_argument("xbe", help="the target, e.g. extract/xbox/default.xbe")
    parser.add_argument("spec", nargs="*",
                        help="patch files (text or JSON), applied in order")
    parser.add_argument("--patch", action="append", default=[],
                        metavar="VA=HEX",
                        help="a patch on the command line: 0x000A4496=EB23, or "
                             "0x000A4496=EB23:7423 to assert the stock bytes")
    parser.add_argument("-o", "--output",
                        help="write the patched XBE here (required unless "
                             "--audit); never the input")
    parser.add_argument("--manifest",
                        help="write the JSON manifest here ('-' for stdout). "
                             "Default: OUTPUT.manifest.json, or stdout when "
                             "auditing.")
    parser.add_argument("--audit", action="store_true",
                        help="classify and report only; write no XBE")
    parser.add_argument("--verify", action="store_true",
                        help="re-read the output and confirm every byte landed, "
                             "and that nothing outside a declared span moved")
    parser.add_argument("--fix-digests", dest="fix_digests",
                        action="store_true", default=True,
                        help="recompute the SHA-1 of every modified section "
                             "whose stored digest follows the known rule "
                             "(default)")
    parser.add_argument("--no-fix-digests", dest="fix_digests",
                        action="store_false",
                        help="leave section digests alone; they will describe "
                             "the old bytes")
    parser.add_argument("--allow-conflicts", action="store_true",
                        help="write anyway when two patches overlap with "
                             "different bytes (last one wins)")
    args = parser.parse_args(argv)

    source = Path(args.xbe)
    if not source.exists():
        print("error: no XBE at %s" % source, file=sys.stderr)
        return 2
    if not args.audit and not args.output:
        print("error: give -o OUTPUT (or --audit to classify without writing)",
              file=sys.stderr)
        return 2

    output = Path(args.output) if args.output else None
    if output is not None:
        if output.resolve() == source.resolve() or (
                output.exists() and output.samefile(source)):
            print("error: -o names the input. The stock XBE is the only copy of "
                  "what the disc shipped; write somewhere else.",
                  file=sys.stderr)
            return 2

    try:
        xbe = Xbe.load(str(source))
        check_sections(xbe)
        sources = [Path(p) for p in args.spec]
        patches: List[Patch] = []
        for path in sources:
            patches.extend(load_patch_file(path))
        for index, text in enumerate(args.patch, 1):
            patches.append(parse_cli_patch(text, index))
    except (PatchError, XbeError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if not patches:
        print("error: no patches given. Pass a patch file, or --patch VA=HEX.",
              file=sys.stderr)
        return 2

    print("%s: %s  base %s  %d sections  %d bytes"
          % (source, xbe.cert["title_name"], _hex32(xbe.base),
             len(xbe.sections), len(xbe.data)))
    if xbe.base != CONVENTION_BASE:
        print("warning: image base is %s, not the project's usual %s -- using "
              "the header's value" % (_hex32(xbe.base),
                                      _hex32(CONVENTION_BASE)))

    placements = [classify(patch, xbe) for patch in patches]
    duplicates, conflicts = collisions(placements)
    applied = [p for p in placements if p.patch.enabled]
    print("%d patch%s (%d applied, %d parked)"
          % (len(placements), "" if len(placements) == 1 else "es",
             len(applied), len(placements) - len(applied)))
    print("  file-backed %d | zero-fill %d | straddle %d | headers %d | "
          "outside %d"
          % (sum(1 for p in applied if p.kind == FILE_BACKED),
             sum(1 for p in applied if p.kind == ZERO_FILL),
             sum(1 for p in applied if p.kind == STRADDLE),
             sum(1 for p in applied if p.kind == HEADERS),
             sum(1 for p in applied if p.kind == OUTSIDE)))
    for place in placements:
        if place.kind != FILE_BACKED:
            continue
        print("  %s -> file %s  %s  %d byte%s%s"
              % (_hex32(place.patch.vaddr), _hex32(place.file_offset),
                 xbe.sections[place.section].name, place.patch.length,
                 "" if place.patch.length == 1 else "s",
                 "" if place.patch.enabled else "  (parked)"))
    for text in duplicates:
        print("  duplicate: %s" % text)
    for text in conflicts:
        print("  conflict:  %s" % text)

    failed = False
    for kind, headline in REFUSALS:
        bad = _refused(placements, kind)
        if not bad:
            continue
        failed = True
        print("error: %d patch%s %s %s"
              % (len(bad), "" if len(bad) == 1 else "es",
                 "lands" if len(bad) == 1 else "land", headline),
              file=sys.stderr)
        for place in bad[:20]:
            print("  %s:%d  %s = %s -- %s"
                  % (place.patch.source, place.patch.line,
                     _hex32(place.patch.vaddr), _hexbytes(place.patch.new),
                     place.reason), file=sys.stderr)
        if len(bad) > 20:
            print("  ... and %d more" % (len(bad) - 20), file=sys.stderr)

    mismatches = stock_mismatches(placements, xbe.data)
    if mismatches:
        failed = True
        print("error: %d patch%s does not match the bytes it declared it would "
              "replace:" % (len(mismatches),
                            "" if len(mismatches) == 1 else "es"),
              file=sys.stderr)
        for text in mismatches:
            print("  %s" % text, file=sys.stderr)
    if conflicts and not args.allow_conflicts:
        failed = True
        print("error: %d overlap%s between applied patches -- the file can only "
              "hold one of them. Resolve the spec, or pass --allow-conflicts to "
              "take the last." % (len(conflicts),
                                  "" if len(conflicts) == 1 else "s"),
              file=sys.stderr)
    if failed:
        print("nothing was written.", file=sys.stderr)
        return 2

    survey = digest_survey(xbe)
    try:
        data, records, touched = apply_patches(xbe, placements)
    except PatchError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    digests, warnings = apply_digests(data, xbe, touched, survey,
                                      args.fix_digests)
    after = bytes(data)
    summary = summarise(placements, records, duplicates, conflicts, digests)

    for entry in survey:
        if not entry["verified_before"] and entry["index"] not in touched:
            print("note: section %s's stored digest does not reproduce under "
                  "%s (a property of the stock image, not of this run)"
                  % (entry["section"], DIGEST_RULE))
    for text in warnings:
        print("warning: %s" % text, file=sys.stderr)

    if not args.audit:
        try:
            _atomic_write(output, after)
        except OSError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
        print("%s: %d byte%s written across %d span%s (%d already matched), "
              "file still %d bytes"
              % (output, summary["bytes_written"],
                 "" if summary["bytes_written"] == 1 else "s",
                 summary["spans_written"],
                 "" if summary["spans_written"] == 1 else "s",
                 summary["already_matching"], len(after)))
        print("  digests: %d recomputed, %d left as they were"
              % (summary["digests_recomputed"], summary["digests_left_alone"]))

    if args.verify:
        try:
            # --audit wrote nothing, so verify what is in hand, not a
            # stale file left over from an earlier run.
            written_back = (after if args.audit or output is None
                            else output.read_bytes())
        except OSError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
        problems = verify(xbe.data, written_back, records, digests)
        problems.extend(reparse(written_back, xbe))
        if problems:
            print("error: the output does not read back as intended:",
                  file=sys.stderr)
            for problem in problems:
                print("  %s" % problem, file=sys.stderr)
            return 2
        print("verify: %d/%d span%s read back as intended, %d digest%s checked; "
              "no other byte differs from the input, and it still parses as the "
              "same XBE"
              % (summary["spans_written"], summary["spans_written"],
                 "" if summary["spans_written"] == 1 else "s",
                 summary["digests_recomputed"],
                 "" if summary["digests_recomputed"] == 1 else "s"))

    manifest = build_manifest(xbe, output, None if args.audit else after,
                              records, summary, duplicates, conflicts, digests,
                              survey, warnings, sources)
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
        print("manifest: %s (%d record%s -- old and new bytes for every edit, "
              "which is also how to revert)"
              % (target, len(records), "" if len(records) == 1 else "s"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

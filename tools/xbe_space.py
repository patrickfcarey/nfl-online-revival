#!/usr/bin/env python3
"""Where -- and how much -- free space exists in an XBE for new code.

    python3 tools/xbe_space.py extract/xbox/default.xbe
    python3 tools/xbe_space.py extract/xbox/default.xbe --json survey.json
    python3 tools/xbe_space.py extract/xbox/default.xbe --sections .text,DOLBY
    python3 tools/xbe_space.py extract/xbox/default.xbe --no-census   # fast, stdlib only

This tool answers exactly one question and never patches anything. It is the
Xbox counterpart of the PS2 survey in ``docs/code-caves.md`` and it inherits
that survey's hard-won posture: **it reports evidence, not verdicts.** Four
regions this project once documented as "safe" were later found live -- reached
by a tail-call, by a mid-function address, by a pointer word in ``.data``, and
by an address the compiler built in a register 124 bytes earlier. So every
region here carries the axes that were checked, the counts that came back
empty, and a standing note that a *clean static census is necessary, not
sufficient*: a region stays UNPROVEN until an execute-breakpoint test passes on
it at runtime, per region, not once for the survey.

Five classes are reported, and only two of them are space:

1. **Section-append headroom.** The XBE is PE-derived, so *adding a section is
   a legitimate operation* -- we are not limited to scavenging dead bytes, the
   way the PS2 ELF forced us to be. This is normally the answer. What it costs
   is header room, and that is what class 1 measures: the occupied spans of the
   header block, the free run at the end of it, whether the section table can
   grow **in place** (usually it cannot -- the shared-page refcount array sits
   immediately behind it), and how many sections the headroom can carry if the
   table is relocated into it.
2. **In-file slack** -- the file-alignment padding between sections and the
   tail after the last one. ⚠ On a typical XBE this is *not* a cave and must
   not be read as one: every byte of it lies outside all sections'
   ``[raw_off, raw_off + raw_size)``, so it has **no virtual address**,
   ``Xbe.off_to_va`` returns ``None``, and code placed there is never loaded
   and can never be jumped to. The tool proves that per byte rather than
   asserting it, because a draft of this spec called the same bytes "ideal for
   small caves" and would have shipped a silent no-op. The one exception it
   watches for: slack *below* ``SizeOfHeaders`` is mapped, and if any is found
   that is a real finding.
3. **Zero-reference dead code** -- the PS2 five-axis census ported to x86.
   Split the code at ``ret``/``jmp``/``int3`` boundaries so no fragment can be
   entered by fall-through, then require that **nothing** targets any byte of
   it: no ``call``/``jmp``/``jcc`` (rel8 or rel32, computed both directions),
   and no 4-byte absolute VA anywhere in the image landing inside it. x86 makes
   the last axis easier than MIPS did -- absolute addresses are literal words.
4. **Virtual-address room** -- the gaps in the VA layout, for the rare segment
   that must land at a particular address. On a packed retail image these are
   worth tens of bytes; the real room is always above the image.
5. **Virtual zero-fill** (``vsize > raw_size``) -- a *refusal* class, listed so
   it cannot be mistaken for free space. It is the exact analogue of PS2
   ``.bss``: allocated at runtime, no bytes in the file, nothing to bake.

Only classes 1 and 3 yield space you can put code in. The tool says so in the
headline rather than making the reader total the columns.

capstone is needed for class 3 only; without it the tool still runs and reports
the census as not-run. Everything else is standard library, so this works on
the rig with no install step, like the rest of ``recon/``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recon.xbe import SECTION_HEADER_SIZE, Xbe, XbeError  # noqa: E402

SURVEY_VERSION = 1

PAGE = 0x1000

#: XBE header fields this tool reads or reports as "must be updated".
H_SIZE_OF_HEADERS = 0x108
H_SIZE_OF_IMAGE = 0x10C
H_SIZE_OF_IMAGE_HEADER = 0x110
H_CERTIFICATE_ADDR = 0x118
H_NUMBER_OF_SECTIONS = 0x11C
H_SECTION_HEADERS_ADDR = 0x120
H_ENTRY_POINT_XOR = 0x128
H_TLS_ADDR = 0x12C
H_KERNEL_THUNK_XOR = 0x158
H_LIBRARY_VERSIONS_ADDR = 0x164
H_LOGO_BITMAP_ADDR = 0x170
H_LOGO_BITMAP_SIZE = 0x174

#: Offsets inside one 0x38-byte section header.
S_NAME_ADDR = 0x14
S_HEAD_SHARED_REF = 0x1C
S_TAIL_SHARED_REF = 0x20
S_DIGEST = 0x24
DIGEST_SIZE = 20

SECTION_FLAG_NAMES = (
    (0x00000001, "WRITABLE"),
    (0x00000002, "PRELOAD"),
    (0x00000004, "EXECUTABLE"),
    (0x00000008, "INSERTED_FILE"),
    (0x00000010, "HEAD_PAGE_READ_ONLY"),
    (0x00000020, "TAIL_PAGE_READ_ONLY"),
)

#: x86 instructions after which execution does not continue to the next byte.
#: A fragment whose predecessor is one of these cannot be entered by
#: fall-through -- the structural half of the census, and the reason a `ret`
#: in the middle of a live function does not make the tail of that function
#: look reachable-by-accident.
TERMINATORS = frozenset((
    "ret", "retf", "iret", "iretd", "iretq",
    "jmp", "ljmp",
    "int3", "ud0", "ud1", "ud2", "hlt",
))

#: Direct-transfer mnemonics whose single operand, when it decodes as a literal
#: address, is a control-flow reference.
CONTROL_FLOW = frozenset((
    "call", "lcall", "jmp", "ljmp",
    "ja", "jae", "jb", "jbe", "jc", "jcxz", "je", "jecxz", "jg", "jge", "jl",
    "jle", "jna", "jnae", "jnb", "jnbe", "jnc", "jne", "jng", "jnge", "jnl",
    "jnle", "jno", "jnp", "jns", "jnz", "jo", "jp", "jpe", "jpo", "js", "jz",
    "loop", "loope", "loopne", "loopnz", "loopz",
))

#: The census's standing caveat, attached to every class-3 region.
UNPROVEN_NOTE = (
    "UNPROVEN. A clean static census is necessary, not sufficient. Per "
    "docs/code-caves.md test 1, this region is not usable until an "
    "execute-breakpoint on it survives boot -> menus -> a full quarter -> "
    "halftime -> save/load without tripping. Per region, not once for the "
    "survey."
)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _u32(data, off):
    return struct.unpack_from("<I", data, off)[0]


def flag_names(flags):
    names = [name for bit, name in SECTION_FLAG_NAMES if flags & bit]
    left = flags & ~sum(bit for bit, _ in SECTION_FLAG_NAMES)
    if left:
        names.append("0x%X" % left)
    return names


def _cstring_len(data, off, limit=256):
    end = data.find(b"\0", off, off + limit)
    return (end - off + 1) if end >= 0 else limit


def _fmt_bytes(n):
    if n >= 1024:
        return "%s B (%.1f KB)" % ("{:,}".format(n), n / 1024.0)
    return "%s B" % ("{:,}".format(n),)


# ---------------------------------------------------------------------------
# class 1 -- the header block, and what it takes to append a section
# ---------------------------------------------------------------------------

def header_layout(xbe):
    """Every occupied span of the header block, and the free runs between them.

    The header block is the start of the file and it maps at ``base``, so a
    file offset here is also ``base + off``. Everything that must be *inside*
    the headers -- the certificate, the section table, the shared-page refcount
    array, the section-name pool, the library table, the debug strings, the
    logo bitmap -- is enumerated from its own header pointer rather than from a
    remembered constant, so this reads correctly on any XBE.
    """
    data = xbe.data
    base = xbe.base
    spans = []

    def add(start, size, what):
        if size > 0:
            spans.append({"off": start, "size": size, "what": what})

    add(0x0, 0x4, "magic 'XBEH'")
    add(0x4, 0x100, "digital signature (RSA-2048, 256 B)")
    add(0x104, _u32(data, H_SIZE_OF_IMAGE_HEADER) - 0x104, "image header fields")

    cert_off = xbe.cert_va - base
    add(cert_off, xbe.cert["size"], "certificate")

    table_off = xbe.section_headers_va - base
    add(table_off, xbe.section_count * SECTION_HEADER_SIZE, "section table")

    # The shared-page reference-count array. Section i's *tail* slot is section
    # i+1's *head* slot -- that overlap is how the format expresses two
    # sections sharing a physical page -- so the array's extent is the span
    # from the lowest head pointer to the highest tail pointer plus one u16.
    refs = []
    for i in range(xbe.section_count):
        hdr = table_off + i * SECTION_HEADER_SIZE
        refs.append(_u32(data, hdr + S_HEAD_SHARED_REF) - base)
        refs.append(_u32(data, hdr + S_TAIL_SHARED_REF) - base)
    refs = [r for r in refs if 0 <= r < len(data)]
    ref_lo, ref_hi = (min(refs), max(refs) + 2) if refs else (0, 0)
    add(ref_lo, ref_hi - ref_lo, "head/tail shared-page refcount array (%d u16)"
        % ((ref_hi - ref_lo) // 2))

    name_offs = []
    for i in range(xbe.section_count):
        hdr = table_off + i * SECTION_HEADER_SIZE
        name_offs.append(_u32(data, hdr + S_NAME_ADDR) - base)
    if name_offs:
        lo = min(name_offs)
        hi = max(o + _cstring_len(data, o) for o in name_offs)
        add(lo, hi - lo, "section-name string pool")

    if xbe.library_versions_va:
        add(xbe.library_versions_va - base, xbe.library_count * 0x10,
            "library-version table")

    for field, what in ((0x154, "debug unicode filename"),
                        (0x14C, "debug pathname"),
                        (0x150, "debug filename")):
        va = _u32(data, field)
        if not va:
            continue
        off = va - base
        if not 0 <= off < len(data):
            continue
        if field == 0x154:
            # UTF-16: the terminator is an aligned pair, so step by 2. A plain
            # find(b"\0\0") lands one byte early on 'e\0\0\0' and understates
            # the string by a byte.
            end = off
            while end + 2 <= len(data) and data[end:end + 2] != b"\0\0":
                end += 2
            add(off, end - off + 2, what)
        else:
            add(off, _cstring_len(data, off, 512), what)

    logo_va, logo_size = _u32(data, H_LOGO_BITMAP_ADDR), _u32(data, H_LOGO_BITMAP_SIZE)
    if logo_va and logo_size:
        add(logo_va - base, logo_size, "logo bitmap")

    spans.sort(key=lambda s: (s["off"], s["size"]))

    # Merge the occupied spans (the debug filename is a pointer *into* the
    # pathname string on this image, so they legitimately overlap) and take the
    # complement inside [0, first section's raw data).
    ceiling = min([s.raw_off for s in xbe.sections if s.raw_off] or [len(data)])
    merged = []
    for s in spans:
        if merged and s["off"] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], s["off"] + s["size"])
        else:
            merged.append([s["off"], s["off"] + s["size"]])

    free = []
    cursor = 0
    for lo, hi in merged:
        if lo > cursor:
            free.append((cursor, lo))
        cursor = max(cursor, hi)
    if cursor < ceiling:
        free.append((cursor, ceiling))

    runs = []
    for lo, hi in free:
        blob = data[lo:hi]
        # Below SizeOfHeaders the loader maps these bytes and off_to_va answers
        # for them; above it they are file padding with no address until
        # SizeOfHeaders is grown. Both are reported, because a mapped free run
        # inside the headers would be a genuinely different (and better) find.
        mapped = min(hi, xbe.size_of_headers) - lo
        runs.append({
            "off": lo, "end": hi, "size": hi - lo,
            "zero_filled": not any(blob),
            "mapped_now_bytes": max(mapped, 0),
            "va_if_mapped": "0x%08X" % (base + lo),
        })
    return {"occupied": spans, "free_runs": runs, "ceiling_off": ceiling}


def section_append(xbe, layout):
    """What it takes to add a section, and the hard caps on doing it."""
    data = xbe.data
    base = xbe.base
    table_off = xbe.section_headers_va - base
    table_end = table_off + xbe.section_count * SECTION_HEADER_SIZE

    # Can the table grow where it is? Only if the bytes immediately behind it
    # are free. On every retail XBE checked so far they are not: the shared-page
    # refcount array starts at the table's last byte.
    blocker = None
    for span in layout["occupied"]:
        if span["off"] <= table_end < span["off"] + span["size"] or span["off"] == table_end:
            blocker = span
            break
    grow_in_place = blocker is None

    headroom = max((r for r in layout["free_runs"]), key=lambda r: r["size"], default=None)
    headroom_size = headroom["size"] if headroom else 0
    # Two numbers, both true, and confusing them is easy: the free RUN starts
    # where the last header structure ends, while SizeOfHeaders may already
    # cover the first bytes of it (2 B on the retail Madden image, which is why
    # the hand survey recorded 1,624 and the run measures 1,626).
    above_soh = max(0, (headroom["end"] - max(headroom["off"], xbe.size_of_headers))
                    if headroom else 0)

    # Relocating the whole table into the headroom is the general recipe. Cost
    # per new section: one 0x38 header, its name string, and two fresh u16
    # refcount slots (legal because those fields are absolute VAs into the
    # header block, not indices into an implicit array).
    name_cost = 12  # a conservative 11-char name + NUL
    per_new = SECTION_HEADER_SIZE + name_cost + 4
    reloc_cost = xbe.section_count * SECTION_HEADER_SIZE
    # If the table can grow where it is, the headroom pays only for the new
    # entries; if it cannot, it pays to re-home the existing table first.
    budget = headroom_size if grow_in_place else headroom_size - reloc_cost
    max_new = max(0, budget // per_new)

    sections_by_raw = sorted((s for s in xbe.sections if s.raw_off),
                             key=lambda s: s.raw_off)
    raw_align = PAGE
    for s in sections_by_raw:
        raw_align = min(raw_align, s.raw_off & -s.raw_off if s.raw_off else PAGE)
    file_end = len(data)
    image_top = base + xbe.size_of_image

    return {
        "section_table": {
            "off": table_off, "va": xbe.section_headers_va,
            "end_off": table_end,
            "entry_size": SECTION_HEADER_SIZE,
            "entries": xbe.section_count,
            "can_grow_in_place": grow_in_place,
            "blocked_by": None if grow_in_place else
                          ("%s at 0x%X" % (blocker["what"], blocker["off"])),
        },
        "header_headroom_bytes": headroom_size,
        "header_headroom_off": headroom["off"] if headroom else None,
        "header_headroom_end": headroom["end"] if headroom else None,
        "header_headroom_above_size_of_headers": above_soh,
        "header_headroom_zero_filled": headroom["zero_filled"] if headroom else None,
        "header_ceiling_off": layout["ceiling_off"],
        "relocate_table_cost_bytes": reloc_cost,
        "bytes_per_new_section": per_new,
        "max_new_sections": max_new,
        "raw_placement_off": file_end,
        "raw_placement_aligned": file_end % raw_align == 0,
        "raw_alignment_observed": raw_align,
        "va_placement": (image_top + PAGE - 1) & ~(PAGE - 1),
        "image_top_va": image_top,
        "fields_to_update": [
            {"off": H_SIZE_OF_HEADERS, "name": "SizeOfHeaders",
             "value": xbe.size_of_headers,
             "why": "must cover the relocated table, names and refcount slots; "
                    "must stay <= 0x%X or the first section's raw data moves"
                    % layout["ceiling_off"]},
            {"off": H_SIZE_OF_IMAGE, "name": "SizeOfImage",
             "value": xbe.size_of_image,
             "why": "must cover the new section's virtual range"},
            {"off": H_NUMBER_OF_SECTIONS, "name": "NumberOfSections",
             "value": xbe.section_count, "why": "+1 per appended section"},
            {"off": H_SECTION_HEADERS_ADDR, "name": "SectionHeadersAddress",
             "value": xbe.section_headers_va,
             "why": "repointed at the relocated table"},
        ],
    }


# ---------------------------------------------------------------------------
# class 2 -- in-file slack, and the proof that it has no virtual address
# ---------------------------------------------------------------------------

def file_slack(xbe):
    """Padding between sections and the tail, with a per-byte mapping proof.

    The trap this function exists to close: slack looks like the obvious place
    for a small cave and it is nothing of the kind. Bytes outside every
    section's ``[raw_off, raw_off + raw_size)`` are not loaded, have no virtual
    address and cannot be branched to. So rather than asserting that, the scan
    asks ``off_to_va`` for **every byte** of every run and reports the count
    that answered.

    With a well-formed section table that count is necessarily zero -- the runs
    are the complement of the section spans, so the question answers itself.
    It is asked anyway, every run, because "slack has no virtual address" is
    exactly the shape of remembered fact this project has been burned by, and
    re-deriving it costs thirty thousand dictionary lookups. If it ever comes
    back non-zero the section map and the run walk have disagreed, and that is
    a finding about the image, not a rounding error: the report says so instead
    of printing the reassuring sentence anyway.
    """
    data = xbe.data
    runs = []
    ordered = sorted((s for s in xbe.sections if s.raw_off),
                     key=lambda s: s.raw_off)
    cursor, prev = xbe.size_of_headers, "(headers)"
    edges = [(s.raw_off, s.name, s) for s in ordered] + [(len(data), None, None)]
    for start, name, _sec in edges:
        if start > cursor:
            blob = data[cursor:start]
            mapped = [o for o in range(cursor, start) if xbe.off_to_va(o) is not None]
            runs.append({
                "off": cursor, "end": start, "size": start - cursor,
                "after": prev, "before": name or "(end of file)",
                "is_header_headroom": prev == "(headers)",
                "zero_filled": not any(blob),
                "mapped_bytes": len(mapped),
                "first_mapped_off": mapped[0] if mapped else None,
                "first_mapped_va": ("0x%08X" % xbe.off_to_va(mapped[0])) if mapped else None,
            })
        if _sec is not None:
            cursor = max(cursor, _sec.raw_off + _sec.raw_size)
            prev = name
    total = sum(r["size"] for r in runs)
    mapped_total = sum(r["mapped_bytes"] for r in runs)
    return {
        "runs": runs,
        "total_bytes": total,
        "mapped_bytes": mapped_total,
        "usable_for_code_bytes": mapped_total,
        "verdict": (
            "NOT USABLE FOR CODE. All %s of slack lies outside every section's "
            "[raw_off, raw_off+raw_size); off_to_va() returned None for every "
            "one of those bytes, so nothing is loaded there and nothing can "
            "jump to it. It is usable only as the RAW HOME of a section you "
            "also declare (class 1)." % _fmt_bytes(total)
        ) if mapped_total == 0 else (
            "FINDING: %d slack bytes DO have a virtual address (first at file "
            "0x%X). That is loadable space and must be examined individually."
            % (mapped_total, next(r["first_mapped_off"] for r in runs
                                  if r["mapped_bytes"]))
        ),
    }


# ---------------------------------------------------------------------------
# class 4 -- virtual-address room; class 5 -- virtual zero-fill
# ---------------------------------------------------------------------------

def va_room(xbe):
    ordered = sorted(xbe.sections, key=lambda s: s.vaddr)
    gaps = []
    for prev, nxt in zip(ordered, ordered[1:]):
        gap = nxt.vaddr - (prev.vaddr + prev.vsize)
        if gap > 0:
            gaps.append({"va": prev.vaddr + prev.vsize, "size": gap,
                         "after": prev.name, "before": nxt.name,
                         "file_backed": xbe.va_to_off(prev.vaddr + prev.vsize) is not None})
    top = xbe.base + xbe.size_of_image
    return {
        "gaps": gaps,
        "total_gap_bytes": sum(g["size"] for g in gaps),
        "largest_gap_bytes": max([g["size"] for g in gaps] or [0]),
        "image_top_va": top,
        "last_section_end_va": ordered[-1].vaddr + ordered[-1].vsize if ordered else None,
        "verdict": (
            "Inter-section gaps total %d bytes (largest %d) -- alignment "
            "crumbs, useless for code. The room is ABOVE the image: everything "
            "at or above 0x%08X is unclaimed by this XBE."
            % (sum(g["size"] for g in gaps),
               max([g["size"] for g in gaps] or [0]), top)),
    }


def virtual_zero_fill(xbe):
    """`vsize > raw_size` -- allocated at runtime, no bytes in the file.

    A refusal class. It is the PS2 ``.bss`` answer with a different spelling:
    the loader zeroes it, so anything baked there is erased before the first
    instruction runs, and ``va_to_off`` already refuses to hand out an offset.
    Listed only so a reader totalling the columns cannot mistake it for space.
    """
    items = []
    for s in xbe.sections:
        if s.vsize > s.raw_size:
            items.append({"section": s.name, "size": s.vsize - s.raw_size,
                          "va": s.vaddr + s.raw_size,
                          "va_end": s.vaddr + s.vsize})
    return {
        "regions": items,
        "total_bytes": sum(i["size"] for i in items),
        "verdict": "OCCUPIED AND UNBAKEABLE -- zero-filled by the loader, no "
                   "file bytes, va_to_off() refuses it. Not free space.",
    }


# ---------------------------------------------------------------------------
# constraints -- each can invalidate an otherwise-free region
# ---------------------------------------------------------------------------

DIGEST_RULE = "SHA-1( le32(raw_size) || raw_bytes )"


def section_digests(xbe):
    """Verify every section digest under the rule, and report the exceptions.

    Ten of eleven sections of the retail Madden image reproduce under
    ``SHA-1(le32(raw_size) || raw_bytes)``; ``.text`` does not, under that rule
    or any near variant tried. The tool does not pick a reading for the caller:
    it reports which sections verify and leaves the interpretation, because the
    engineering answer makes the question moot -- recompute the digest under
    the verified rule for every section an emitter touches, and compute a
    correct one for any section it appends.
    """
    data = xbe.data
    table = xbe.section_headers_va - xbe.base
    out = []
    for i, s in enumerate(xbe.sections):
        hdr = table + i * SECTION_HEADER_SIZE
        stored = data[hdr + S_DIGEST:hdr + S_DIGEST + DIGEST_SIZE]
        calc = hashlib.sha1(struct.pack("<I", s.raw_size)
                            + data[s.raw_off:s.raw_off + s.raw_size]).digest()
        out.append({
            "section": s.name, "digest_off": hdr + S_DIGEST,
            "stored": stored.hex(), "computed": calc.hex(),
            "verifies": stored == calc,
            "populated": any(stored),
        })
    ok = [d["section"] for d in out if d["verifies"]]
    bad = [d["section"] for d in out if not d["verifies"]]
    return {
        "rule": DIGEST_RULE,
        "sections": out,
        "verify_count": len(ok),
        "total": len(out),
        "exceptions": bad,
        "emitter_requirement": (
            "Recompute %s for every section EMIT modifies, and compute one for "
            "any appended section. Do not leave a modified section carrying its "
            "old digest on the theory that nothing checks." % DIGEST_RULE),
    }


def integrity(xbe):
    data = xbe.data
    sig = data[0x4:0x104]
    return {
        "signature_off": 0x4, "signature_size": 0x100,
        "signature_populated": any(sig),
        "signature_nonzero_bytes": sum(1 for b in sig if b),
        "certificate_off": xbe.cert_va - xbe.base,
        "certificate_size": xbe.cert["size"],
        "entry_point_obfuscated": True,
        "entry_point_raw": _u32(data, H_ENTRY_POINT_XOR),
        "entry_point": xbe.entry,
        "kernel_thunk_raw": _u32(data, H_KERNEL_THUNK_XOR),
        "kernel_thunk": xbe.kernel_thunk,
        "build_type": xbe.build_type,
        "note": (
            "The entry point and kernel-thunk fields are stored XOR'd with a "
            "build-type key. Any tool that rewrites them must re-XOR with the "
            "SAME key (%s here) -- and note that the plain entry VA therefore "
            "never appears as a literal word in the file, so a pointer scan "
            "does not see it. This survey adds the entry point as its own "
            "reference axis for exactly that reason." % xbe.build_type),
    }


def tls_and_thunks(xbe):
    out = {"tls_va": xbe.tls_va, "tls_present": bool(xbe.tls_va)}
    if xbe.tls_va:
        off = xbe.va_to_off(xbe.tls_va)
        if off is not None:
            (start, end, index, callbacks, zerofill, chars) = struct.unpack_from(
                "<6I", xbe.data, off)
            out.update({
                "tls_off": off,
                "tls_raw_data": [start, end],
                "tls_index_va": index,
                "tls_callbacks_va": callbacks,
                "tls_callbacks_present": bool(callbacks),
                "tls_zero_fill": zerofill,
                "tls_characteristics": chars,
                "tls_index_is_in_zero_fill": xbe.va_to_off(index) is None if index else None,
            })
    thunk_va = xbe.kernel_thunk
    off = xbe.va_to_off(thunk_va)
    count = 0
    if off is not None:
        while off + 4 * count + 4 <= len(xbe.data) and _u32(xbe.data, off + 4 * count):
            count += 1
    out.update({
        "kernel_thunk_va": thunk_va,
        "kernel_thunk_off": off,
        "kernel_thunk_entries": count,
        "kernel_thunk_bytes": count * 4 + 4,
        "kernel_thunk_end_va": thunk_va + count * 4 + 4 if off is not None else None,
        "kernel_thunk_section": (xbe.section_for_va(thunk_va).name
                                 if xbe.section_for_va(thunk_va) else None),
        "note": "The kernel import thunk table is live data at the head of its "
                "section. A census that treats that section as ordinary data "
                "will call it free; it is not.",
    })
    return out


def alignment_rules(xbe):
    ordered = sorted((s for s in xbe.sections if s.raw_off), key=lambda s: s.raw_off)
    raw_aligned = all(s.raw_off % PAGE == 0 for s in ordered)
    va_aligned = [s for s in xbe.sections if s.vaddr % PAGE == 0]
    return {
        "raw_offsets_page_aligned": raw_aligned,
        "raw_offsets": {s.name: s.raw_off for s in ordered},
        "vaddrs_page_aligned_count": len(va_aligned),
        "vaddrs_page_aligned": [s.name for s in va_aligned],
        "file_size": len(xbe.data),
        "file_size_page_aligned": len(xbe.data) % PAGE == 0,
        "rule": (
            "Measured on this image: every section's raw offset is 0x1000-"
            "aligned; virtual addresses are NOT -- sections are packed tightly "
            "and the head/tail shared-page refcounts are how the format lets "
            "two sections share a page. An appended section should therefore "
            "be 0x1000-aligned in BOTH spaces: page-aligned raw so it matches "
            "every other section, page-aligned VA so it shares a page with "
            "nothing and its refcount slots stay trivially correct."),
    }


def trailing_section(xbe):
    by_va = sorted(xbe.sections, key=lambda s: s.vaddr)
    by_raw = sorted((s for s in xbe.sections if s.raw_off), key=lambda s: s.raw_off)
    last_va, last_raw = by_va[-1], by_raw[-1]
    return {
        "last_by_va": last_va.name, "last_by_raw": last_raw.name,
        "same": last_va.name == last_raw.name,
        "flags": flag_names(last_va.flags),
        "executable": last_va.executable,
        "raw_end": last_raw.raw_off + last_raw.raw_size,
        "va_end": last_va.vaddr + last_va.vsize,
        "verdict": (
            "Appending AFTER %s in the file and ABOVE it in VA touches nothing "
            "it owns: it is last in both orders, so no existing raw offset or "
            "virtual address moves." % last_va.name
            if last_va.name == last_raw.name else
            "CAUTION: the last section by VA (%s) is not the last by raw offset "
            "(%s). Appending must not assume one implies the other."
            % (last_va.name, last_raw.name)),
    }


# ---------------------------------------------------------------------------
# class 3 -- the x86 zero-reference census
# ---------------------------------------------------------------------------

class ReferenceIndex:
    """Every way execution or data can reach an address in the censused span.

    Four axes, and the closed set matters more than any one of them:

    ``call`` / ``jmp`` / ``jcc``
        direct transfers taken from a *decoded* instruction stream, so a rel8
        displacement is only counted where an instruction really begins. An
        unaligned byte-scan for rel8 forms is worthless -- one byte in sixteen
        is a ``7x`` opcode and half of those "reach" any given 400-byte window
        by chance, which would reject every region in the image.
    ``rel32``
        the same scan done *unaligned* over the whole file for the rel32 forms
        only (``E8``/``E9``). Those are safe to over-scan: a random rel32 lands
        inside a 3.5 MB span with probability ~1/1200, so the false-positive
        rate is negligible while the axis catches any call the linear sweep
        missed through decode drift.
    ``word``
        every 4-byte little-endian word anywhere in the image whose value lands
        in the span -- the vtable / jump-table / function-pointer test that
        caught the PS2 survey's fourth bad region. Deliberately unaligned and
        deliberately noisy: a coincidental integer that looks like an address
        costs us a candidate cave, which is the safe direction to be wrong in.
    ``entry``
        the entry point, added by hand because it is stored XOR'd and therefore
        appears nowhere as a literal.
    """

    def __init__(self, xbe, span_lo, span_hi, insns):
        self.lo, self.hi = span_lo, span_hi
        self.reached = {}
        self._index_decoded(insns)
        self._index_rel32(xbe)
        self._index_words(xbe)
        if span_lo <= xbe.entry < span_hi:
            self._add(xbe.entry, None, "entry")

    def _add(self, target, source, axis):
        if self.lo <= target < self.hi:
            self.reached.setdefault(target, []).append((source, axis))

    def _index_decoded(self, insns):
        for addr, size, mnemonic, ops in insns:
            if mnemonic not in CONTROL_FLOW or not ops.startswith("0x"):
                continue
            try:
                target = int(ops, 16)
            except ValueError:
                continue
            axis = ("call" if mnemonic in ("call", "lcall")
                    else "jmp" if mnemonic in ("jmp", "ljmp") else "jcc")
            self._add(target, addr, axis)

    def _index_rel32(self, xbe):
        data = xbe.data
        for off in range(len(data) - 5):
            if data[off] not in (0xE8, 0xE9):
                continue
            va = xbe.off_to_va(off)
            if va is None:
                continue
            rel = struct.unpack_from("<i", data, off + 1)[0]
            self._add((va + 5 + rel) & 0xFFFFFFFF, va, "rel32")

    def _index_words(self, xbe):
        # Sources on this axis are recorded as FILE OFFSETS, not VAs -- most of
        # them are in data sections and half the file has no meaningful "source
        # instruction" anyway. `hits` therefore never treats a word hit as
        # internal control flow, which is the conservative reading: a pointer
        # word sitting inside a candidate cave and aiming at that cave is not
        # something to wave through.
        n = len(xbe.data)
        view = memoryview(xbe.data)
        for phase in range(4):
            usable = ((n - phase) // 4) * 4
            for i, (word,) in enumerate(struct.iter_unpack("<I", view[phase:phase + usable])):
                if self.lo <= word < self.hi:
                    self.reached.setdefault(word, []).append((phase + 4 * i, "word"))

    #: Axes whose source is a virtual address, and which therefore can be
    #: "internal" to a region (a cave's own branches are not liveness).
    _VA_SOURCED = frozenset(("call", "jmp", "jcc", "rel32"))

    def hits(self, lo, hi, external_only=True):
        out = []
        for target in range(lo, hi):
            for source, axis in self.reached.get(target, ()):
                if (external_only and axis in self._VA_SOURCED
                        and source is not None and lo <= source < hi):
                    continue
                out.append((source, target, axis))
        return out

    def any_hit(self, lo, hi):
        for target in range(lo, hi):
            if target in self.reached:
                return True
        return False


def disassemble_section(section, data, capstone_mod):
    """Linear sweep of one section, resynchronising past undecodable bytes.

    Linear sweep is the right primitive here even though it can drift: the
    census needs *instruction boundaries over the whole section*, not a call
    graph, and MSVC packs x86 back to back with ``int3`` between functions, so
    the sweep re-aligns naturally. Drift is measured rather than assumed --
    ``census`` reports the decode-failure count and the fraction of harvested
    call targets that land on a boundary the sweep agrees with, so the reader
    can see how much the sweep is worth on this image.
    """
    md = capstone_mod.Cs(capstone_mod.CS_ARCH_X86, capstone_mod.CS_MODE_32)
    blob = data[section.raw_off:section.raw_off + section.raw_size]
    insns, off, failures = [], 0, 0
    chunk = 8192
    while off < len(blob):
        consumed = 0
        for (addr, size, mnemonic, ops) in md.disasm_lite(
                blob[off:off + chunk], section.vaddr + off):
            insns.append((addr, size, mnemonic, ops))
            consumed += size
            if consumed >= chunk - 16:
                break
        if consumed == 0:
            failures += 1
            off += 1
        else:
            off += consumed
    return insns, failures


def census_section(xbe, section, min_size, capstone_mod):
    """The five-axis census, ported to x86, over one executable section."""
    lo, hi = section.vaddr, section.vaddr + section.raw_size
    insns, failures = disassemble_section(section, xbe.data, capstone_mod)
    index = ReferenceIndex(xbe, lo, hi, insns)

    boundaries = {a for a, _, _, _ in insns}
    harvested = {t for t, srcs in index.reached.items()
                 if any(ax in ("call", "rel32") for _, ax in srcs)}
    corroborated = len(harvested & boundaries)

    # Fall-through guard: a fragment may only start where the *previous*
    # instruction cannot continue into it. The section's first instruction is
    # guarded by construction (nothing above it inside the section).
    guarded, previous_terminates = set(), True
    for addr, _size, mnemonic, _ops in insns:
        if previous_terminates:
            guarded.add(addr)
        previous_terminates = mnemonic in TERMINATORS

    regions = []
    i, covered_to = 0, lo
    while i < len(insns):
        addr, _size, _mn, _ops = insns[i]
        if addr < covered_to or addr not in guarded:
            i += 1
            continue
        j, last_terminator_end, cursor, gap_bytes = i, None, addr, 0
        while j < len(insns):
            a, s, m, _o = insns[j]
            # Check from the END OF THE PREVIOUS INSTRUCTION, not from this
            # instruction's start. Where the linear sweep hit an undecodable
            # byte it resynchronised by skipping forward, so a candidate region
            # can span bytes that are in no instruction -- and those bytes can
            # be referenced. Checking per-instruction missed exactly that and
            # the self-check below caught it on the retail image.
            if index.any_hit(cursor, a + s):
                break
            gap_bytes += a - cursor
            cursor = a + s
            if m in TERMINATORS:
                last_terminator_end = cursor
            j += 1
        if last_terminator_end is not None and last_terminator_end - addr >= min_size:
            body = [ins for ins in insns[i:j] if ins[0] + ins[1] <= last_terminator_end]
            internal = index.hits(addr, last_terminator_end, external_only=False)
            padding = sum(s for _a, s, m, _o in body if m == "int3")
            decoded = sum(s for _a, s, _m, _o in body)
            regions.append({
                "va": addr,
                "file_off": xbe.va_to_off(addr),
                "size": last_terminator_end - addr,
                "section": section.name,
                "executable": section.executable,
                "instructions": len(body),
                "int3_padding_bytes": padding,
                "undecoded_bytes": last_terminator_end - addr - decoded,
                "internal_refs": len(internal),
                "shape": _shape(body),
                "first_mnemonics": [m for _a, _s, m, _o in body[:6]],
            })
            covered_to = last_terminator_end
        i = max(j, i + 1)

    regions.sort(key=lambda r: -r["size"])
    return {
        "section": section.name,
        "vaddr": lo, "size": section.raw_size,
        "executable": section.executable,
        "instructions_decoded": len(insns),
        "decode_failures": failures,
        "bytes_covered": sum(s for _a, s, _m, _o in insns),
        "distinct_referenced_addresses": len(index.reached),
        "call_targets_harvested": len(harvested),
        "call_targets_on_a_sweep_boundary": corroborated,
        "regions": regions,
        "total_bytes": sum(r["size"] for r in regions),
    }, index


def _shape(body):
    """A one-word character for the region, so the report is readable.

    Worth the ten lines: a run of ``jmp dword ptr [...]`` is an *import thunk
    band*, and an allocator that overwrote one without noticing would silently
    remove an import rather than corrupt anything -- a different failure mode
    from clobbering a dead leaf function, and one worth naming.
    """
    real = [(m, o) for _a, _s, m, o in body if m != "int3"]
    if not real:
        return "int3 padding only"
    if all(m == "jmp" and o.startswith("dword ptr [") for m, o in real):
        return "import-thunk band (jmp [__imp_*])"
    if sum(1 for m, _ in real if m in ("fld", "fstp", "fmul", "fadd", "fdiv",
                                       "movaps", "mulps", "addps")) > len(real) // 4:
        return "float/vector routine"
    return "code"


def census(xbe, section_names, min_size):
    try:
        import capstone
    except ImportError:
        return {
            "run": False,
            "reason": "capstone is not installed; class 3 needs a disassembler. "
                      "Everything else in this survey is standard library.",
            "sections": [], "regions": [], "total_bytes": 0,
        }, {}

    out, indexes = [], {}
    for name in section_names:
        section = xbe.section_by_name(name)
        if section is None:
            out.append({"section": name, "error": "no such section"})
            continue
        if not section.raw_size:
            out.append({"section": name, "error": "section has no file bytes"})
            continue
        result, index = census_section(xbe, section, min_size, capstone)
        out.append(result)
        indexes[name] = index

    regions = [r for s in out for r in s.get("regions", [])]
    regions.sort(key=lambda r: -r["size"])
    return {
        "run": True,
        "capstone_version": ".".join(str(v) for v in capstone.cs_version()[:2]),
        "min_size": min_size,
        "sections": out,
        "regions": regions,
        "total_bytes": sum(r["size"] for r in regions),
        "buckets": {str(t): {"count": sum(1 for r in regions if r["size"] >= t),
                             "bytes": sum(r["size"] for r in regions if r["size"] >= t)}
                    for t in (32, 64, 128, 256, 512)},
    }, indexes


def verify_no_reported_region_is_referenced(census_result, indexes):
    """Post-condition: re-ask the index about every region we are about to print.

    Cheap, and it is the single claim the whole class-3 report rests on. The
    PS2 survey shipped four regions that were referenced; this is the assertion
    that would have caught them, run every time rather than during review.
    """
    failures = []
    for region in census_result.get("regions", []):
        index = indexes.get(region["section"])
        if index is None:
            continue
        hits = index.hits(region["va"], region["va"] + region["size"])
        if hits:
            failures.append({"va": region["va"], "size": region["size"],
                             "hits": len(hits)})
    return {"checked": len(census_result.get("regions", [])),
            "failures": failures, "ok": not failures}


# ---------------------------------------------------------------------------
# survey assembly
# ---------------------------------------------------------------------------

CANNOT_PROVE = [
    "Runtime overwrites. Nothing here proves the image does not rewrite its "
    "own code -- an overlay loader, a decompressor, or a copy through a "
    "computed base. Computed store bases cannot be enumerated statically.",
    "Invisible reachability. An address arriving from a data file, a computed "
    "indirect call through a pointer that is never a literal, or a callback "
    "registered by handle evades every axis here. The axes are strong, and "
    "they are static.",
    "Whether the console enforces the section digests or the certificate "
    "signature. That is a property of the boot path (retail vs softmod), not "
    "of this file, and it is not decidable from the image.",
    "Linear-sweep drift. The census decodes instruction boundaries by linear "
    "sweep; where it drifts, a rel8 jcc into a candidate region could be "
    "missed. The decode-failure count and the call-target corroboration rate "
    "are reported so the reader can judge how much the sweep is worth here.",
]


def survey(path, section_names=(".text",), min_size=32, run_census=True):
    xbe = Xbe.load(path)
    layout = header_layout(xbe)
    append = section_append(xbe, layout)
    slack = file_slack(xbe)
    vroom = va_room(xbe)
    zerofill = virtual_zero_fill(xbe)
    digests = section_digests(xbe)

    if run_census:
        dead, indexes = census(xbe, section_names, min_size)
    else:
        dead, indexes = {"run": False, "reason": "--no-census", "sections": [],
                         "regions": [], "total_bytes": 0}, {}
    self_check = verify_no_reported_region_is_referenced(dead, indexes)

    sections = [{
        "name": s.name, "vaddr": s.vaddr, "vsize": s.vsize,
        "raw_off": s.raw_off, "raw_size": s.raw_size,
        "flags": s.flags, "flag_names": flag_names(s.flags),
        "executable": s.executable, "writable": s.writable,
        "zero_fill_bytes": max(0, s.vsize - s.raw_size),
    } for s in xbe.sections]

    inventory = []
    inventory.append({
        "class": "1-section-append",
        "va": append["va_placement"], "file_off": append["raw_placement_off"],
        "size": None, "executable": True, "usable_for_code": True,
        "proven": False,
        "evidence": "%s of zero-filled header headroom at file 0x%X (all above "
                    "SizeOfHeaders 0x%X); section table is %d x 0x38 at 0x%X and "
                    "%s grow in place (%s); file ends at 0x%X, %s-aligned; image "
                    "top VA 0x%08X." % (
                        _fmt_bytes(append["header_headroom_bytes"]),
                        append["header_headroom_off"] or 0, xbe.size_of_headers,
                        xbe.section_count, append["section_table"]["off"],
                        "CAN" if append["section_table"]["can_grow_in_place"] else "CANNOT",
                        append["section_table"]["blocked_by"] or "unobstructed",
                        append["raw_placement_off"],
                        "page" if append["raw_placement_aligned"] else "NOT page",
                        append["image_top_va"]),
        "risk": "Requires the emitter to relocate the section table, update 4 "
                "header fields, recompute the appended section's digest, and "
                "repack the ISO (the XBE grows). No existing byte moves.",
    })
    inventory.append({
        "class": "2-file-slack",
        "va": None, "file_off": slack["runs"][0]["off"] if slack["runs"] else None,
        "size": slack["total_bytes"],
        "executable": False,
        "usable_for_code": slack["usable_for_code_bytes"] > 0,
        "proven": True,
        "evidence": "off_to_va() asked for all %s of slack; %d bytes answered."
                    % (_fmt_bytes(slack["total_bytes"]), slack["mapped_bytes"]),
        "risk": slack["verdict"],
    })
    for region in dead.get("regions", []):
        inventory.append({
            "class": "3-dead-code",
            "va": region["va"], "file_off": region["file_off"],
            "size": region["size"], "executable": region["executable"],
            "usable_for_code": True, "proven": False,
            "evidence": "%d instructions, fall-through guarded, zero hits on "
                        "call/jmp/jcc/rel32/word/entry across all %d bytes; "
                        "%d internal refs; %d undecoded bytes; %s"
                        % (region["instructions"], region["size"],
                           region["internal_refs"], region["undecoded_bytes"],
                           region["shape"]),
            "risk": UNPROVEN_NOTE + (
                "  Region contains %d internal branch targets -- a PARTIAL "
                "overwrite would corrupt a live-looking body; overwrite whole "
                "or not at all." % region["internal_refs"]
                if region["internal_refs"] else "") + (
                "  Region contains %d bytes the linear sweep could not decode; "
                "treat its instruction accounting with suspicion."
                % region["undecoded_bytes"] if region["undecoded_bytes"] else "") + (
                "  This is an import-thunk band: overwriting it does not corrupt "
                "anything, but it permanently removes the ability to call those "
                "imports." if region["shape"].startswith("import-thunk") else ""),
        })
    inventory.append({
        "class": "4-va-room",
        "va": vroom["image_top_va"], "file_off": None, "size": None,
        "executable": True, "usable_for_code": True, "proven": False,
        "evidence": "Inter-section VA gaps total %d bytes (largest %d). Above "
                    "0x%08X nothing is claimed." % (
                        vroom["total_gap_bytes"], vroom["largest_gap_bytes"],
                        vroom["image_top_va"]),
        "risk": "Only meaningful together with class 1 -- a VA is not memory "
                "until a section header declares it.",
    })
    inventory.append({
        "class": "5-virtual-zero-fill",
        "va": zerofill["regions"][0]["va"] if zerofill["regions"] else None,
        "file_off": None, "size": zerofill["total_bytes"],
        "executable": False, "usable_for_code": False, "proven": True,
        "evidence": "%d sections declare more virtual size than raw size."
                    % len(zerofill["regions"]),
        "risk": zerofill["verdict"],
    })

    headline = {
        "route_a_append_section": {
            "bytes": None,
            "summary": "Unbounded by the XBE format. SizeOfImage is a u32 and "
                       "currently 0x%08X; the append itself costs only header "
                       "room, of which %s is free. Max appended sections "
                       "without moving any section data: %s."
                       % (xbe.size_of_image,
                          _fmt_bytes(append["header_headroom_bytes"]),
                          append["max_new_sections"]),
            "header_headroom_bytes": append["header_headroom_bytes"],
            "max_new_sections": append["max_new_sections"],
        },
        "route_b_dead_code": {
            "bytes": dead.get("total_bytes", 0),
            "summary": ("%s in %d zero-reference regions >= %d B, largest %s. "
                        "Every one UNPROVEN until a runtime execute-breakpoint "
                        "test passes on it."
                        % (_fmt_bytes(dead.get("total_bytes", 0)),
                           len(dead.get("regions", [])), min_size,
                           _fmt_bytes(dead["regions"][0]["size"]))
                        if dead.get("regions") else
                        "not run (%s)" % dead.get("reason", "no regions")),
        },
        "route_c_file_slack": {
            "bytes": slack["usable_for_code_bytes"],
            "summary": "%s exists; %d bytes of it are loadable. %s"
                       % (_fmt_bytes(slack["total_bytes"]),
                          slack["mapped_bytes"],
                          "Usable only as the raw home of an appended section."
                          if not slack["mapped_bytes"] else "See the finding."),
        },
        "route_d_va_gaps": {
            "bytes": vroom["total_gap_bytes"],
            "summary": "%d bytes of inter-section VA gap, largest %d -- crumbs."
                       % (vroom["total_gap_bytes"], vroom["largest_gap_bytes"]),
        },
        "route_e_virtual_zero_fill": {
            "bytes": 0,
            "summary": "%s exists and is NOT space -- loader-zeroed, unbakeable."
                       % _fmt_bytes(zerofill["total_bytes"]),
        },
    }

    return {
        "tool": "xbe_space", "version": SURVEY_VERSION,
        "image": {
            "path": path, "file_size": len(xbe.data),
            "title_name": xbe.cert["title_name"], "title_id": xbe.cert["title_id"],
            "build_type": xbe.build_type, "base": xbe.base,
            "size_of_headers": xbe.size_of_headers,
            "size_of_image": xbe.size_of_image,
            "image_top_va": xbe.base + xbe.size_of_image,
            "entry": xbe.entry, "kernel_thunk": xbe.kernel_thunk,
            "section_count": xbe.section_count,
        },
        "headline": headline,
        "sections": sections,
        "classes": {
            "section_append": append,
            "header_layout": layout,
            "file_slack": slack,
            "dead_code": dead,
            "va_room": vroom,
            "virtual_zero_fill": zerofill,
        },
        "constraints": {
            "section_digests": digests,
            "integrity": integrity(xbe),
            "tls_and_thunks": tls_and_thunks(xbe),
            "alignment": alignment_rules(xbe),
            "trailing_section": trailing_section(xbe),
        },
        "self_check": self_check,
        "inventory": inventory,
        "cannot_prove": CANNOT_PROVE,
    }


# ---------------------------------------------------------------------------
# human-readable report
# ---------------------------------------------------------------------------

def _rule(title):
    return ["", "=" * 78, title, "=" * 78]


def format_text(s):
    out = []
    img = s["image"]
    out += _rule("XBE FREE-SPACE SURVEY -- %s" % img["path"])
    out.append("image      %r  id 0x%08X  %s  base 0x%08X"
               % (img["title_name"], img["title_id"], img["build_type"], img["base"]))
    out.append("file       %s   SizeOfHeaders 0x%X   SizeOfImage 0x%08X -> top 0x%08X"
               % (_fmt_bytes(img["file_size"]), img["size_of_headers"],
                  img["size_of_image"], img["image_top_va"]))
    out.append("sections   %d   entry 0x%08X   kernel thunk 0x%08X"
               % (img["section_count"], img["entry"], img["kernel_thunk"]))

    out += _rule("HEADLINE -- how much space is available for new code, by route")
    for key, label in (("route_a_append_section", "A. append a section"),
                       ("route_b_dead_code", "B. zero-reference dead code"),
                       ("route_c_file_slack", "C. in-file slack"),
                       ("route_d_va_gaps", "D. inter-section VA gaps"),
                       ("route_e_virtual_zero_fill", "E. virtual zero-fill")):
        h = s["headline"][key]
        out.append("  %-28s %s" % (label, h["summary"]))
    out.append("")
    out.append("  Only routes A and B are space you can put code in. C is the raw")
    out.append("  BACKING for A, not a cave. D and E are not space at all.")

    out += _rule("SECTIONS")
    out.append("  %-12s %-10s %-10s %-10s %-10s %-9s %s"
               % ("name", "vaddr", "vsize", "raw off", "raw size", "zero-fill", "flags"))
    for sec in s["sections"]:
        out.append("  %-12s 0x%08X 0x%08X 0x%08X 0x%08X %9d %s"
                   % (sec["name"], sec["vaddr"], sec["vsize"], sec["raw_off"],
                      sec["raw_size"], sec["zero_fill_bytes"],
                      "|".join(sec["flag_names"])))
    execs = [sec["name"] for sec in s["sections"] if sec["executable"]]
    out.append("  executable: %d of %d (%s)"
               % (len(execs), len(s["sections"]), ", ".join(execs)))
    out.append("  NOT executable: %s"
               % (", ".join(sec["name"] for sec in s["sections"]
                            if not sec["executable"]) or "(none)"))

    # --- class 1
    ap = s["classes"]["section_append"]
    out += _rule("CLASS 1 -- SECTION-APPEND HEADROOM  (the primary answer)")
    out.append("  The XBE is PE-derived: adding a section is a legitimate operation,")
    out.append("  so the budget is not the scavenged-bytes budget the PS2 ELF forced.")
    out.append("")
    out.append("  header block occupancy (file 0x000 .. 0x%X):" % ap["header_ceiling_off"])
    for span in s["classes"]["header_layout"]["occupied"]:
        out.append("    0x%04X..0x%04X  %6d B  %s"
                   % (span["off"], span["off"] + span["size"], span["size"], span["what"]))
    out.append("  free runs inside the header block:")
    for run in s["classes"]["header_layout"]["free_runs"]:
        out.append("    0x%04X..0x%04X  %6d B  zero=%s  mapped now: %d B  (VA %s once "
                   "SizeOfHeaders covers it)"
                   % (run["off"], run["end"], run["size"], run["zero_filled"],
                      run["mapped_now_bytes"], run["va_if_mapped"]))
    st = ap["section_table"]
    out.append("")
    out.append("  headroom        %d B free at 0x%X..0x%X, of which %d B lie above"
               % (ap["header_headroom_bytes"], ap["header_headroom_off"],
                  ap["header_headroom_end"], ap["header_headroom_above_size_of_headers"]))
    out.append("                  SizeOfHeaders (0x%X). All zero-filled: %s."
               % (img["size_of_headers"], ap["header_headroom_zero_filled"]))
    out.append("                  It is headroom in BOTH spaces: the header block maps at")
    out.append("                  base 0x%08X and the first section starts at VA 0x%08X,"
               % (img["base"], min(sec["vaddr"] for sec in s["sections"])))
    out.append("                  so the whole run is inside the already-mapped first page.")
    out.append("")
    out.append("  section table   0x%X..0x%X  (%d x 0x%X)  VA 0x%08X"
               % (st["off"], st["end_off"], st["entries"], st["entry_size"], st["va"]))
    out.append("  grow in place?  %s%s"
               % ("YES" if st["can_grow_in_place"] else "NO",
                  "" if st["can_grow_in_place"] else " -- blocked by %s" % st["blocked_by"]))
    out.append("  recipe          relocate the whole table into the headroom "
               "(%d B), repoint" % ap["relocate_table_cost_bytes"])
    out.append("                  SectionHeadersAddress, bump NumberOfSections, put the")
    out.append("                  new name string and two fresh u16 refcount slots in the")
    out.append("                  headroom too. Those refcount fields are absolute VAs, not")
    out.append("                  indices, which is what makes relocating them legal.")
    out.append("  cost/section    %d B of header room -> max %s appended section(s)"
               % (ap["bytes_per_new_section"], ap["max_new_sections"]))
    out.append("  raw placement   file end 0x%X (%s-aligned)"
               % (ap["raw_placement_off"],
                  "page" if ap["raw_placement_aligned"] else "NOT page"))
    out.append("  VA placement    0x%08X (first page above image top 0x%08X)"
               % (ap["va_placement"], ap["image_top_va"]))
    out.append("  fields to update:")
    for f in ap["fields_to_update"]:
        out.append("    +0x%03X %-22s now 0x%X" % (f["off"], f["name"], f["value"]))
        out.append("           %s" % f["why"])
    out.append("  HARD CAP: SizeOfHeaders must stay <= 0x%X. Above that the first"
               % ap["header_ceiling_off"])
    out.append("  section's raw data has to move, and then every raw offset moves with it.")

    # --- class 2
    sl = s["classes"]["file_slack"]
    out += _rule("CLASS 2 -- IN-FILE SLACK   ***  NOT USABLE FOR CODE  ***")
    out.append("  %-22s %8s  %-10s %s" % ("run", "size", "zero?", "bytes with a VA"))
    for run in sl["runs"]:
        out.append("  0x%06X..0x%06X   %6d B  %-10s %d   (%s -> %s)%s"
                   % (run["off"], run["end"], run["size"], run["zero_filled"],
                      run["mapped_bytes"], run["after"], run["before"],
                      "  <- this run IS class 1's header headroom"
                      if run.get("is_header_headroom") else ""))
    out.append("  TOTAL %s;  bytes that have a virtual address: %d"
               % (_fmt_bytes(sl["total_bytes"]), sl["mapped_bytes"]))
    out.append("")
    for line in _wrap(sl["verdict"], 74):
        out.append("  " + line)
    out.append("")
    out.append("  Why this is called out so loudly: an earlier draft of this survey")
    out.append("  described the same bytes as 'ideal for small caves'. Code written")
    out.append("  there is never loaded and nothing can branch to it -- a silent no-op,")
    out.append("  the worst possible failure for a patch tool.")

    # --- class 3
    dead = s["classes"]["dead_code"]
    out += _rule("CLASS 3 -- ZERO-REFERENCE DEAD CODE  (the PS2 five-axis census, x86)")
    if not dead.get("run"):
        out.append("  NOT RUN: %s" % dead.get("reason"))
    else:
        out.append("  axes, all required empty: call / jmp / jcc (decoded, rel8+rel32)")
        out.append("                            rel32 (unaligned scan of the whole file)")
        out.append("                            word  (any 4-byte LE VA anywhere in the image)")
        out.append("                            entry (added by hand -- it is stored XOR'd)")
        out.append("  structural: the region's first byte cannot be reached by fall-through.")
        out.append("")
        for sec in dead["sections"]:
            if "error" in sec:
                out.append("  %-11s %s" % (sec["section"], sec["error"]))
                continue
            out.append("  %-11s %s decoded, %d decode failures (%.4f%% byte coverage)"
                       % (sec["section"], "{:,}".format(sec["instructions_decoded"]),
                          sec["decode_failures"],
                          100.0 * sec["bytes_covered"] / sec["size"]))
            out.append("              %s distinct referenced addresses; %d/%d harvested "
                       "call targets land on a sweep boundary (%.1f%%)"
                       % ("{:,}".format(sec["distinct_referenced_addresses"]),
                          sec["call_targets_on_a_sweep_boundary"],
                          sec["call_targets_harvested"],
                          100.0 * sec["call_targets_on_a_sweep_boundary"]
                          / max(sec["call_targets_harvested"], 1)))
            # A section whose code is entered almost entirely indirectly gives
            # the census very little to work with, and it will over-report. Say
            # so on the section's own line rather than letting a big total for
            # a library blob sit next to a well-corroborated .text total.
            if sec["bytes_covered"] / sec["size"] < 0.995 or \
                    sec["call_targets_harvested"] < 100:
                out.append("              ** WEAK EVIDENCE: only %d direct call targets "
                           "and %.1f%% decode coverage here. Regions"
                           % (sec["call_targets_harvested"],
                              100.0 * sec["bytes_covered"] / sec["size"]))
                out.append("              reported in %s are over-reported by "
                           "construction -- treat them as candidates to test, not"
                           % sec["section"])
                out.append("              as a budget. .text is the default for this reason.")
        out.append("")
        out.append("  size buckets:")
        for threshold, bucket in sorted(dead["buckets"].items(), key=lambda kv: int(kv[0])):
            out.append("    >= %4s B   %5d regions   %s"
                       % (threshold, bucket["count"], _fmt_bytes(bucket["bytes"])))
        out.append("")
        out.append("  top regions:")
        out.append("    %-10s %-10s %7s %5s %5s %5s  %s"
                   % ("vaddr", "file", "size", "insn", "int3", "undec", "shape"))
        for r in dead["regions"][:25]:
            out.append("    0x%08X 0x%08X %6d B %5d %5d %5d  %s"
                       % (r["va"], r["file_off"], r["size"], r["instructions"],
                          r["int3_padding_bytes"], r["undecoded_bytes"], r["shape"]))
        if len(dead["regions"]) > 25:
            out.append("    ... %d more" % (len(dead["regions"]) - 25))
        out.append("")
        for line in _wrap(UNPROVEN_NOTE, 74):
            out.append("  " + line)
        chk = s["self_check"]
        out.append("")
        out.append("  self-check: re-asked the reference index about all %d reported "
                   "regions -> %s" % (chk["checked"], "OK, none referenced"
                                      if chk["ok"] else
                                      "FAILED on %d" % len(chk["failures"])))

    # --- class 4 / 5
    vr = s["classes"]["va_room"]
    out += _rule("CLASS 4 -- VIRTUAL-ADDRESS ROOM")
    for g in vr["gaps"]:
        out.append("  0x%08X  %4d B  between %-11s and %s"
                   % (g["va"], g["size"], g["after"], g["before"]))
    out.append("  total %d B, largest %d B" % (vr["total_gap_bytes"], vr["largest_gap_bytes"]))
    for line in _wrap(vr["verdict"], 74):
        out.append("  " + line)

    zf = s["classes"]["virtual_zero_fill"]
    out += _rule("CLASS 5 -- VIRTUAL ZERO-FILL   ***  OCCUPIED, UNBAKEABLE  ***")
    for r in zf["regions"]:
        out.append("  %-11s %9s B  VA 0x%08X..0x%08X"
                   % (r["section"], "{:,}".format(r["size"]), r["va"], r["va_end"]))
    out.append("  TOTAL %s" % _fmt_bytes(zf["total_bytes"]))
    for line in _wrap(zf["verdict"], 74):
        out.append("  " + line)

    # --- constraints
    c = s["constraints"]
    out += _rule("CONSTRAINTS -- each can invalidate an otherwise-free region")
    dg = c["section_digests"]
    out.append("  SECTION DIGESTS   rule tested: %s" % dg["rule"])
    for d in dg["sections"]:
        out.append("    %-11s %-8s stored %s%s"
                   % (d["section"], "verifies" if d["verifies"] else "MISMATCH",
                      d["stored"][:16] + "...",
                      "" if d["verifies"] else "  computed %s..." % d["computed"][:16]))
    out.append("    -> %d of %d verify.%s"
               % (dg["verify_count"], dg["total"],
                  ("  Exception(s): %s" % ", ".join(dg["exceptions"]))
                  if dg["exceptions"] else ""))
    for line in _wrap(dg["emitter_requirement"], 72):
        out.append("    " + line)
    if dg["exceptions"]:
        out.append("    Two readings, and this tool does not pick one: either the image")
        out.append("    ships a stale digest for %s -- in which case a console that boots"
                   % ", ".join(dg["exceptions"]))
        out.append("    it plainly does not enforce them -- or those sections use a rule")
        out.append("    not found here. Either way the emitter's duty is the same.")

    ig = c["integrity"]
    out.append("")
    out.append("  SIGNATURE         %d/%d nonzero bytes at file 0x%X (RSA-2048 over the"
               % (ig["signature_nonzero_bytes"], ig["signature_size"], ig["signature_off"]))
    out.append("                    header+cert). Present. Whether the boot path checks it")
    out.append("                    is a property of the console, not of this file.")
    out.append("  ENTRY / THUNK     stored XOR'd (%s keys): entry 0x%08X (raw 0x%08X),"
               % (ig["build_type"], ig["entry_point"], ig["entry_point_raw"]))
    out.append("                    thunk 0x%08X (raw 0x%08X). The plain entry VA appears"
               % (ig["kernel_thunk"], ig["kernel_thunk_raw"]))
    out.append("                    nowhere as a literal, so the census adds it as its own axis.")

    tl = c["tls_and_thunks"]
    out.append("")
    out.append("  TLS               directory VA 0x%08X; AddressOfCallBacks 0x%08X (%s),"
               % (tl.get("tls_va", 0), tl.get("tls_callbacks_va", 0),
                  "callbacks present -- PRESERVE THEM"
                  if tl.get("tls_callbacks_present") else "none"))
    out.append("                    index VA 0x%08X, zero-fill %d B."
               % (tl.get("tls_index_va", 0), tl.get("tls_zero_fill", 0)))
    out.append("  KERNEL THUNKS     %d entries (%d B) at VA 0x%08X in %s -- live data at"
               % (tl["kernel_thunk_entries"], tl["kernel_thunk_bytes"],
                  tl["kernel_thunk_va"], tl["kernel_thunk_section"]))
    out.append("                    the head of that section. Not free.")

    al = c["alignment"]
    out.append("")
    out.append("  ALIGNMENT         all %d raw offsets page-aligned: %s;  page-aligned "
               "vaddrs: %d of %d"
               % (len(s["sections"]), al["raw_offsets_page_aligned"],
                  al["vaddrs_page_aligned_count"], len(s["sections"])))
    for line in _wrap(al["rule"], 72):
        out.append("    " + line)

    tr = c["trailing_section"]
    out.append("")
    out.append("  TRAILING SECTION  last by VA: %s; last by raw: %s; flags %s"
               % (tr["last_by_va"], tr["last_by_raw"], "|".join(tr["flags"])))
    for line in _wrap(tr["verdict"], 72):
        out.append("    " + line)

    out += _rule("WHAT THIS TOOL CANNOT PROVE STATICALLY")
    for item in s["cannot_prove"]:
        for i, line in enumerate(_wrap(item, 72)):
            out.append(("  - " if i == 0 else "    ") + line)

    out += _rule("RANKED INVENTORY")
    out.append("  %-20s %-10s %-10s %-8s %-5s %s"
               % ("class", "va", "file", "size", "exec", "usable"))
    for item in sorted(s["inventory"],
                       key=lambda i: (i["class"], -(i["size"] or 0))):
        out.append("  %-20s %-10s %-10s %-8s %-5s %s"
                   % (item["class"],
                      "0x%08X" % item["va"] if item["va"] is not None else "-",
                      "0x%08X" % item["file_off"] if item["file_off"] is not None else "-",
                      "{:,}".format(item["size"]) if item["size"] is not None else "unbounded",
                      "yes" if item["executable"] else "no",
                      "yes" if item["usable_for_code"] else "NO"))
    out.append("")
    out.append("  (class 3 regions are listed individually above and in the JSON;")
    out.append("   the inventory rows here carry their evidence and risk fields.)")
    out.append("")
    return "\n".join(out)


def _wrap(text, width):
    words, line, out = text.split(), "", []
    for word in words:
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = (line + " " + word) if line else word
    if line:
        out.append(line)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Report where and how much free space exists in an XBE for "
                    "new code. Reports evidence; never patches anything.")
    parser.add_argument("xbe", help="path to default.xbe")
    parser.add_argument("--json", metavar="PATH",
                        help="also write the survey as JSON ('-' for stdout)")
    parser.add_argument("--sections", default=".text",
                        help="comma-separated sections to census for dead code "
                             "(default .text; 'all' = every executable section)")
    parser.add_argument("--min-size", type=int, default=32,
                        help="smallest dead-code region to report (default 32)")
    parser.add_argument("--no-census", action="store_true",
                        help="skip class 3 (fast, standard library only)")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress the text report (use with --json)")
    args = parser.parse_args(argv)

    try:
        probe = Xbe.load(args.xbe)
    except (OSError, XbeError) as exc:
        print("xbe_space: %s" % exc, file=sys.stderr)
        return 2

    if args.sections == "all":
        names = [s.name for s in probe.sections if s.executable and s.raw_size]
    else:
        names = [n.strip() for n in args.sections.split(",") if n.strip()]

    result = survey(args.xbe, section_names=names, min_size=args.min_size,
                    run_census=not args.no_census)

    if not args.quiet:
        print(format_text(result))
    if args.json:
        blob = json.dumps(result, indent=2, sort_keys=False)
        if args.json == "-":
            print(blob)
        else:
            with open(args.json, "w") as handle:
                handle.write(blob + "\n")
            if not args.quiet:
                print("JSON written to %s" % args.json)
    return 0 if result["self_check"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

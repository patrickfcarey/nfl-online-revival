"""Is this address range really dead? The five-axis census, in one pass.

    python3 -m recon.cave_census extract/SLUS_207.52 0x00443270:480
    python3 -m recon.cave_census extract/SLUS_207.52 0x00139A68:456 0x0045F598:624

Why this exists: this project has cleared **three** live regions as dead code
caves and written patches on top of them. Each escaped a different way, and
none of them would have escaped this census:

* **cave #1 `0x00139A68`** -- four interior addresses are materialised at
  `0x00139E2C..44` as ``addiu rD, rBase, simm`` off a base register that a
  ``lui`` loaded 124 bytes earlier, then registered as callbacks. A
  ``lui``+``addiu`` *pair* search for the target finds nothing, because the
  pair never completes to the target: the low half is added to a base the
  compiler is reusing for four different addresses. Ten of the eleven lines
  of the worked patch example would have overwritten live code.
* **cave #3 `0x0045F598`** -- same shape, two interior addresses at
  `0x00460178/80` registered as a handler pair.
* the four functions recorded as "zero callers" -- reached by tail-call ``j``
  (not ``jal``), by a *mid-function* address, and by a function-pointer word
  sitting in ``.data``.

The lesson those three paid for is that **searching for a target address is
the wrong shape of search**. This module inverts it: propagate register values
forward across the whole image, record *every* address the code forms, and
then ask which of them land in the range. Nothing has to be guessed in
advance, and one pass answers the question for any number of candidate caves.

The five axes, all required to be empty before a range may be called dead:

===========  ====================================================
``jal``      call targets landing in the range
``j``        tail-call / plain jumps landing in the range
``branch``   every branch form -- beq/bne/blez/bgtz, the -likely
             forms, REGIMM (incl. ``-al``), and COP1 ``bc1x`` --
             from a source *outside* the range
``formed``   addresses built in registers: ``lui``+``addiu``/``ori``
             at any distance, **and** any ``addiu`` off a register
             already holding a tracked value (the axis that missed
             caves #1 and #3)
``word``     32-bit words anywhere in the file equal to an in-range
             address -- the vtable / jump-table / callback-table test
===========  ====================================================

Plus a sixth, structural test: a range may not be entered by **fall-through**.
The word before it must be a delay slot belonging to an unconditional transfer
(``jr``/``j``/``jr ra``), or execution simply walks in from the function above.

What it still cannot settle (unchanged, and stated in ``code-caves.md``): an
address arriving from a data file, a computed ``jalr`` through a pointer that
is never materialised as a literal, and runtime overwriting of ``.text``. A
clean census is necessary, not sufficient -- the runtime execute-breakpoint
test (``code-caves.md`` liveness test 1) still gates first use of every cave.
"""
from __future__ import annotations

import sys
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple

from .mipsdis import Elf32, disassemble, writes_gpr

#: Words outside this band are not plausible code/data pointers for this
#: image (PT_LOAD runs 0x00100000..~0x0065A000); constraining the pointer
#: test this way keeps ordinary small integers out of the results.
ADDR_LO = 0x00100000
ADDR_HI = 0x00700000

_BRANCH_OPS = {0x04, 0x05, 0x06, 0x07,          # beq bne blez bgtz
               0x14, 0x15, 0x16, 0x17}          # the -likely forms
_REGIMM = 0x01                                  # bltz/bgez/bltzl/bgezl/-al
_COP1 = 0x11                                    # bc1f/bc1t/bc1fl/bc1tl (rs=8)


class Reference(NamedTuple):
    """One way that execution or data can reach an address in the range."""

    vaddr: int          #: where the reference itself lives
    target: int         #: the in-range address it reaches
    axis: str           #: 'jal' | 'j' | 'branch' | 'formed' | 'word'
    detail: str

    def __str__(self) -> str:
        return ("0x%08X  %-7s -> 0x%08X  %s"
                % (self.vaddr, self.axis, self.target, self.detail))


class ImageIndex:
    """Every address the image reaches, indexed once for many queries.

    Building this costs a couple of seconds and a few tens of MB; censusing
    an additional range afterwards is a dictionary lookup. That asymmetry is
    deliberate -- the survey that produced the poisoned caves checked regions
    one at a time and the per-region cost is what discouraged re-checking.
    """

    def __init__(self, elf: Elf32) -> None:
        self.elf = elf
        #: target -> list of (source vaddr, axis, detail)
        self.reached: Dict[int, List[Tuple[int, str, str]]] = {}
        #: vaddr -> word, for the fall-through test
        self._word_at: Dict[int, int] = {}
        self._build()

    def _add(self, target: int, vaddr: int, axis: str, detail: str) -> None:
        self.reached.setdefault(target, []).append((vaddr, axis, detail))

    def _build(self) -> None:
        # `val` propagates register contents forward. A register is dropped
        # the moment anything writes it, which is what makes an unbounded
        # pairing distance safe -- distance was never the right test, and a
        # 64-byte window is exactly how cave #1 was cleared.
        val: Dict[int, Tuple[int, int]] = {}     # reg -> (value, origin lui)

        for vaddr, w in self.elf.words():
            self._word_at[vaddr] = w
            op = w >> 26
            rs, rt = (w >> 21) & 31, (w >> 16) & 31
            imm = w & 0xFFFF

            # --- axis: word (any word that looks like a pointer) ----------
            if ADDR_LO <= w < ADDR_HI:
                self._add(w, vaddr, "word", "32-bit word equal to the address")

            # --- axis: jal / j -------------------------------------------
            if op in (0x02, 0x03):
                target = ((vaddr + 4) & 0xF0000000) | ((w & 0x03FFFFFF) << 2)
                self._add(target, vaddr, "jal" if op == 0x03 else "j",
                          "%s 0x%08X" % ("jal" if op == 0x03 else "j", target))

            # --- axis: branch (every form) --------------------------------
            elif (op in _BRANCH_OPS
                  or op == _REGIMM
                  or (op == _COP1 and rs == 0x08)):
                simm = imm - 0x10000 if imm & 0x8000 else imm
                self._add(vaddr + 4 + (simm << 2), vaddr, "branch",
                          "branch (op 0x%02X)" % op)

            # --- axis: formed (register value propagation) ----------------
            if op == 0x0F:                                   # lui
                if rt:
                    val[rt] = ((imm << 16) & 0xFFFFFFFF, vaddr)
                continue
            if op in (0x09, 0x0D) and rs in val:             # addiu / ori
                base, origin = val[rs]
                if op == 0x09:
                    simm = imm - 0x10000 if imm & 0x8000 else imm
                    formed = (base + simm) & 0xFFFFFFFF
                    how = "addiu"
                else:
                    formed = base | imm
                    how = "ori"
                if ADDR_LO <= formed < ADDR_HI:
                    self._add(formed, vaddr, "formed",
                              "%s off r%d (lui at 0x%08X, %d bytes back)"
                              % (how, rs, origin, vaddr - origin))
                # `addiu rD, rS, lo` with rD != rS leaves rS holding the base,
                # so one base register can serve several addresses. That is
                # precisely cave #1's shape: four callbacks off one base.
                if rt:
                    val[rt] = (formed, origin)
                continue

            clobbered = writes_gpr(w)
            if clobbered is not None:
                val.pop(clobbered, None)

    # -- queries ------------------------------------------------------------

    def word(self, vaddr: int) -> Optional[int]:
        return self._word_at.get(vaddr)

    def refs_into(self, base: int, size: int) -> List[Reference]:
        """External references landing anywhere in ``[base, base+size)``.

        References whose *source* is inside the range are internal control
        flow and are excluded -- a cave's own branches are not liveness.
        """
        end = base + size
        hits: List[Reference] = []
        for target in range(base, end, 4):
            for vaddr, axis, detail in self.reached.get(target, ()):
                if base <= vaddr < end:
                    continue
                hits.append(Reference(vaddr, target, axis, detail))
        hits.sort(key=lambda r: (r.axis, r.vaddr))
        return hits

    def entry(self, base: int) -> str:
        """How, if at all, execution can walk into ``base`` from above.

        ``'guarded'``  the word two back is an unconditional transfer, so the
                       word directly above the range is its delay slot and
                       nothing can fall in (cave #7's shape).
        ``'data'``     the words above do not decode as instructions, so the
                       neighbour is not code at all -- linker padding or a
                       data section (cave #11's shape, the lowest-risk kind).
        ``'fallsthru'` the words above are live code that simply runs on.
        ``'unknown'``  the range starts at the edge of the image.
        """
        above = [self.word(base - 4 * n) for n in (1, 2, 3, 4)]
        if any(w is None for w in above):
            return "unknown"
        # A word that does not decode is not an instruction; the neighbour is
        # data. Checked before the delay-slot test because a data word can
        # accidentally look like `jr`.
        if any(disassemble(w, base).startswith(".word") for w in above):
            return "data"
        prev = above[1]                              # the word two back
        op = prev >> 26
        if op in (0x02, 0x03):                       # j / jal
            return "guarded"
        if op == 0x00 and (prev & 0x3F) in (0x08, 0x09):   # jr / jalr
            return "guarded"
        return "fallsthru"


class CaveVerdict(NamedTuple):
    base: int
    size: int
    refs: List[Reference]
    entry: str

    @property
    def dead(self) -> bool:
        """True when no axis reaches the range and it cannot be fallen into."""
        return not self.refs and self.entry in ("guarded", "data")

    def by_axis(self) -> Dict[str, List[Reference]]:
        out: Dict[str, List[Reference]] = {}
        for r in self.refs:
            out.setdefault(r.axis, []).append(r)
        return out

    def report(self) -> str:
        lines = ["0x%08X..0x%08X  (%d bytes / %d words)"
                 % (self.base, self.base + self.size, self.size,
                    self.size // 4)]
        axes = self.by_axis()
        for axis in ("jal", "j", "branch", "formed", "word"):
            found = axes.get(axis, [])
            lines.append("  %-7s %s" % (axis, "clean" if not found
                                        else "%d REFERENCE(S)" % len(found)))
            for r in found[:8]:
                lines.append("            %s" % (r,))
            if len(found) > 8:
                lines.append("            ... %d more" % (len(found) - 8))
        lines.append("  %-7s %s" % ("entry", {
            "guarded": "cannot fall through (preceded by a delay slot)",
            "data": "no code above (linker padding / data section)",
            "fallsthru": "FALLS THROUGH from live code above -- not safe",
            "unknown": "unknown (range starts at the image edge)",
        }[self.entry]))
        lines.append("  VERDICT: %s" % (
            "DEAD -- no static reference on any axis" if self.dead
            else "NOT DEAD -- do not use as a cave"))
        return "\n".join(lines)


def census(elf: Elf32, ranges: Iterable[Tuple[int, int]],
           index: Optional[ImageIndex] = None) -> List[CaveVerdict]:
    """Census each ``(base, size)`` range against the whole image."""
    idx = index or ImageIndex(elf)
    return [CaveVerdict(base, size, idx.refs_into(base, size), idx.entry(base))
            for base, size in ranges]


def _parse_range(spec: str) -> Tuple[int, int]:
    if ":" not in spec:
        raise ValueError("range must be ADDR:SIZE, got %r" % spec)
    addr, size = spec.split(":", 1)
    return int(addr, 0), int(size, 0)


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    elf = Elf32(argv[0])
    ranges = [_parse_range(s) for s in argv[1:]]
    index = ImageIndex(elf)
    verdicts = census(elf, ranges, index)
    for v in verdicts:
        print(v.report())
        print()
    return 0 if all(v.dead for v in verdicts) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

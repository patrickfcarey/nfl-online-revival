"""A minimal MIPS disassembler and cross-referencer for PS2 executables.

Host binutils on the rig has no MIPS target and capstone is not installed, so
this covers the subset needed to read a PS2 game's control flow: loads, stores,
arithmetic, branches and jumps. It is deliberately small -- the goal is to find
*where a result is tested*, not to produce a faithful listing.

Two things make a static patch findable without a symbol table:

* ``find_address_refs`` locates the ``lui``/``addiu`` pair that materialises a
  32-bit address, which is how MIPS references a string or global. Follow those
  back and you have the code that uses a named thing.
* ``find_jal_targets`` finds every call to an address, so once a function is
  identified its call sites are one pass away.

Addresses are virtual (as loaded), translated through the ELF program headers.
"""

from __future__ import annotations

import struct
from typing import Dict, Iterator, List, NamedTuple, Optional, Tuple

_REGS = ["zero", "at", "v0", "v1", "a0", "a1", "a2", "a3",
         "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
         "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7",
         "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra"]

_SPECIAL = {
    0x00: "sll", 0x02: "srl", 0x03: "sra", 0x04: "sllv", 0x06: "srlv",
    0x07: "srav", 0x08: "jr", 0x09: "jalr", 0x0C: "syscall", 0x0D: "break",
    0x10: "mfhi", 0x12: "mflo", 0x18: "mult", 0x19: "multu", 0x1A: "div",
    0x1B: "divu", 0x20: "add", 0x21: "addu", 0x22: "sub", 0x23: "subu",
    0x24: "and", 0x25: "or", 0x26: "xor", 0x27: "nor", 0x2A: "slt",
    0x2B: "sltu",
    # R5900 is MIPS III/IV based: the 64-bit forms and the conditional moves
    # are common in compiler output here. Leaving them as ".word" invites
    # reading a conditional move as an unconditional assignment, which is
    # exactly the mistake that produced a wrong patch note once.
    0x0A: "movz", 0x0B: "movn", 0x2C: "dadd", 0x2D: "daddu", 0x2E: "dsub",
    0x2F: "dsubu", 0x14: "dsllv", 0x16: "dsrlv", 0x17: "dsrav",
    0x38: "dsll", 0x3A: "dsrl", 0x3B: "dsra", 0x3C: "dsll32",
    0x3E: "dsrl32", 0x3F: "dsra32",
}
_OPCODES = {
    0x02: "j", 0x03: "jal", 0x04: "beq", 0x05: "bne", 0x06: "blez",
    0x07: "bgtz", 0x08: "addi", 0x09: "addiu", 0x0A: "slti", 0x0B: "sltiu",
    0x0C: "andi", 0x0D: "ori", 0x0E: "xori", 0x0F: "lui",
    0x20: "lb", 0x21: "lh", 0x23: "lw", 0x24: "lbu", 0x25: "lhu",
    0x28: "sb", 0x29: "sh", 0x2B: "sw",
    0x18: "daddi", 0x19: "daddiu", 0x37: "ld", 0x3F: "sd", 0x27: "lwu",
}


class Segment(NamedTuple):
    vaddr: int
    offset: int
    size: int


class Elf32:
    """Just enough ELF32 to map virtual addresses onto file bytes."""

    def __init__(self, path: str) -> None:
        self.path = path
        with open(path, "rb") as handle:
            self.data = handle.read()
        if self.data[:4] != b"\x7fELF":
            raise ValueError("%s is not an ELF file" % path)
        if self.data[4] != 1:
            raise ValueError("only ELF32 is supported")
        self.entry = struct.unpack_from("<I", self.data, 24)[0]
        ph_off = struct.unpack_from("<I", self.data, 28)[0]
        ph_size = struct.unpack_from("<H", self.data, 42)[0]
        ph_count = struct.unpack_from("<H", self.data, 44)[0]
        self.segments: List[Segment] = []
        for index in range(ph_count):
            base = ph_off + index * ph_size
            p_type, p_offset, p_vaddr = struct.unpack_from("<III", self.data, base)
            p_filesz = struct.unpack_from("<I", self.data, base + 16)[0]
            if p_type == 1 and p_filesz:  # PT_LOAD
                self.segments.append(Segment(p_vaddr, p_offset, p_filesz))

    def offset_of(self, vaddr: int) -> Optional[int]:
        for seg in self.segments:
            if seg.vaddr <= vaddr < seg.vaddr + seg.size:
                return seg.offset + (vaddr - seg.vaddr)
        return None

    def vaddr_of(self, offset: int) -> Optional[int]:
        for seg in self.segments:
            if seg.offset <= offset < seg.offset + seg.size:
                return seg.vaddr + (offset - seg.offset)
        return None

    def word(self, vaddr: int) -> Optional[int]:
        off = self.offset_of(vaddr)
        if off is None or off + 4 > len(self.data):
            return None
        return struct.unpack_from("<I", self.data, off)[0]

    def words(self) -> Iterator[Tuple[int, int]]:
        """Yield (vaddr, word) across every loadable segment."""
        for seg in self.segments:
            for pos in range(0, seg.size - 3, 4):
                yield (seg.vaddr + pos,
                       struct.unpack_from("<I", self.data, seg.offset + pos)[0])


def _signed16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def disassemble(word: int, vaddr: int = 0) -> str:
    """Render one instruction. Unknown encodings come back as .word."""
    op = word >> 26
    rs, rt, rd = (word >> 21) & 0x1F, (word >> 16) & 0x1F, (word >> 11) & 0x1F
    shamt, funct = (word >> 6) & 0x1F, word & 0x3F
    imm = word & 0xFFFF
    simm = _signed16(imm)

    if word == 0:
        return "nop"
    if op == 0x00:
        name = _SPECIAL.get(funct)
        if name is None:
            return ".word 0x%08x" % word
        if name in ("jr", "jalr"):
            return "%s %s" % (name, _REGS[rs])
        if name in ("sll", "srl", "sra"):
            return "%s %s, %s, %d" % (name, _REGS[rd], _REGS[rt], shamt)
        if name in ("mfhi", "mflo"):
            return "%s %s" % (name, _REGS[rd])
        if name in ("mult", "multu", "div", "divu"):
            return "%s %s, %s" % (name, _REGS[rs], _REGS[rt])
        if name in ("movz", "movn"):
            # Conditional: rd is written only when rt is zero / non-zero.
            return "%s %s, %s, %s" % (name, _REGS[rd], _REGS[rs], _REGS[rt])
        if name in ("dsll", "dsrl", "dsra", "dsll32", "dsrl32", "dsra32"):
            return "%s %s, %s, %d" % (name, _REGS[rd], _REGS[rt], shamt)
        return "%s %s, %s, %s" % (name, _REGS[rd], _REGS[rs], _REGS[rt])
    name = _OPCODES.get(op)
    if name is None:
        return ".word 0x%08x" % word
    if name in ("j", "jal"):
        target = ((vaddr + 4) & 0xF0000000) | ((word & 0x03FFFFFF) << 2)
        return "%s 0x%08x" % (name, target)
    if name in ("beq", "bne"):
        return "%s %s, %s, 0x%08x" % (name, _REGS[rs], _REGS[rt],
                                      vaddr + 4 + (simm << 2))
    if name in ("blez", "bgtz"):
        return "%s %s, 0x%08x" % (name, _REGS[rs], vaddr + 4 + (simm << 2))
    if name == "lui":
        return "lui %s, 0x%04x" % (_REGS[rt], imm)
    if name in ("lb", "lh", "lw", "lbu", "lhu", "sb", "sh", "sw", "ld", "sd",
                "lwu"):
        return "%s %s, %d(%s)" % (name, _REGS[rt], simm, _REGS[rs])
    if name in ("andi", "ori", "xori"):
        return "%s %s, %s, 0x%04x" % (name, _REGS[rt], _REGS[rs], imm)
    return "%s %s, %s, %d" % (name, _REGS[rt], _REGS[rs], simm)


def dump(elf: Elf32, start: int, count: int = 32) -> str:
    """A short listing beginning at *start*."""
    lines = []
    for index in range(count):
        vaddr = start + index * 4
        word = elf.word(vaddr)
        if word is None:
            break
        lines.append("  %08x  %08x  %s" % (vaddr, word, disassemble(word, vaddr)))
    return "\n".join(lines)


def find_address_refs(elf: Elf32, target: int,
                      window: int = 64) -> List[Tuple[int, str]]:
    """Find code that materialises *target* as a 32-bit address.

    MIPS builds an address from ``lui reg, hi`` plus a following
    ``addiu/ori reg, reg, lo``. The low half is sign-extended by ``addiu``, so
    the high half is adjusted when the low half is negative -- getting that
    wrong is the usual reason a search like this finds nothing.
    """
    lo = target & 0xFFFF
    hi = (target >> 16) & 0xFFFF
    hi_adjusted = (hi + 1) & 0xFFFF if lo & 0x8000 else hi

    candidates: Dict[int, List[Tuple[int, int]]] = {}
    hits: List[Tuple[int, str]] = []
    for vaddr, word in elf.words():
        op = word >> 26
        rt = (word >> 16) & 0x1F
        imm = word & 0xFFFF
        if op == 0x0F and imm in (hi, hi_adjusted):        # lui
            candidates.setdefault(rt, []).append((vaddr, imm))
            continue
        if op in (0x09, 0x0D):                              # addiu / ori
            rs = (word >> 21) & 0x1F
            pending = candidates.get(rs)
            if not pending:
                continue
            want_hi = hi_adjusted if op == 0x09 else hi     # ori does not sign-extend
            for lui_addr, lui_imm in list(pending):
                if lui_imm != want_hi or vaddr - lui_addr > window:
                    continue
                if imm == lo:
                    hits.append((lui_addr,
                                 "lui/%s pair -> 0x%08x (completed at 0x%08x)"
                                 % ("addiu" if op == 0x09 else "ori",
                                    target, vaddr)))
                    pending.remove((lui_addr, lui_imm))
    return hits


def find_jal_targets(elf: Elf32, target: int) -> List[int]:
    """Every ``jal`` that calls *target*."""
    hits = []
    for vaddr, word in elf.words():
        if (word >> 26) == 0x03:
            called = ((vaddr + 4) & 0xF0000000) | ((word & 0x03FFFFFF) << 2)
            if called == target:
                hits.append(vaddr)
    return hits


def find_immediate(elf: Elf32, value: int) -> List[Tuple[int, str]]:
    """Find instructions carrying *value* as a 16-bit immediate.

    Useful for chasing an error code seen on screen back to the comparison that
    produced it.
    """
    needle = value & 0xFFFF
    hits = []
    for vaddr, word in elf.words():
        op = word >> 26
        if op in (0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E) and (word & 0xFFFF) == needle:
            hits.append((vaddr, disassemble(word, vaddr)))
    return hits

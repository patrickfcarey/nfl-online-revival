"""Scratch: wrap recon.mipsdis with COP1 (FPU) decoding for read-only recon."""
import sys
sys.path.insert(0, '/mnt/c/GitHub/nfl-online-revival')
from recon.mipsdis import Elf32, disassemble as base_dis, _signed16

FREG = ["f%d" % i for i in range(32)]

COP1_FUNCT = {
    0x00: "add.s", 0x01: "sub.s", 0x02: "mul.s", 0x03: "div.s",
    0x04: "sqrt.s", 0x05: "abs.s", 0x06: "mov.s", 0x07: "neg.s",
    0x16: "rsqrt.s", 0x18: "adda.s", 0x19: "suba.s", 0x1a: "mula.s",
    0x1c: "madd.s", 0x1d: "msub.s", 0x1e: "madda.s", 0x1f: "msuba.s",
    0x24: "cvt.w.s", 0x28: "max.s", 0x29: "min.s",
    0x30: "c.f.s", 0x32: "c.eq.s", 0x34: "c.lt.s", 0x36: "c.le.s",
}


def dis(word, vaddr=0):
    op = word >> 26
    if op == 0x11:  # COP1
        fmt = (word >> 21) & 0x1F
        ft = (word >> 16) & 0x1F
        fs = (word >> 11) & 0x1F
        fd = (word >> 6) & 0x1F
        funct = word & 0x3F
        imm = _signed16(word & 0xFFFF)
        if fmt == 0x00:
            return "mfc1 %s, %s" % (["zero","at","v0","v1","a0","a1","a2","a3","t0","t1","t2","t3","t4","t5","t6","t7","s0","s1","s2","s3","s4","s5","s6","s7","t8","t9","k0","k1","gp","sp","fp","ra"][ft], FREG[fs])
        if fmt == 0x04:
            return "mtc1 %s, %s" % (["zero","at","v0","v1","a0","a1","a2","a3","t0","t1","t2","t3","t4","t5","t6","t7","s0","s1","s2","s3","s4","s5","s6","s7","t8","t9","k0","k1","gp","sp","fp","ra"][ft], FREG[fs])
        if fmt == 0x02:
            return "cfc1 r%d, %d" % (ft, fs)
        if fmt == 0x06:
            return "ctc1 r%d, %d" % (ft, fs)
        if fmt == 0x08:  # BC1
            nd_tf = ft
            names = {0: "bc1f", 1: "bc1t", 2: "bc1fl", 3: "bc1tl"}
            return "%s 0x%08x" % (names.get(nd_tf, "bc1?"), vaddr + 4 + imm * 4)
        if fmt == 0x10:  # single
            name = COP1_FUNCT.get(funct, "cop1.s?%02x" % funct)
            if name.startswith("c."):
                return "%s %s, %s" % (name, FREG[fs], FREG[ft])
            if name in ("mov.s", "abs.s", "neg.s", "sqrt.s", "cvt.w.s", "rsqrt.s"):
                return "%s %s, %s" % (name, FREG[fd], FREG[fs])
            if name.endswith("a.s"):
                return "%s %s, %s" % (name, FREG[fs], FREG[ft])
            return "%s %s, %s, %s" % (name, FREG[fd], FREG[fs], FREG[ft])
        if fmt == 0x14:  # word
            if funct == 0x20:
                return "cvt.s.w %s, %s" % (FREG[fd], FREG[fs])
        return ".cop1 0x%08x" % word
    if op == 0x31:
        return "lwc1 %s, %d(%s)" % (FREG[(word >> 16) & 0x1F], _signed16(word & 0xFFFF),
                                    ["zero","at","v0","v1","a0","a1","a2","a3","t0","t1","t2","t3","t4","t5","t6","t7","s0","s1","s2","s3","s4","s5","s6","s7","t8","t9","k0","k1","gp","sp","fp","ra"][(word >> 21) & 0x1F])
    if op == 0x39:
        return "swc1 %s, %d(%s)" % (FREG[(word >> 16) & 0x1F], _signed16(word & 0xFFFF),
                                    ["zero","at","v0","v1","a0","a1","a2","a3","t0","t1","t2","t3","t4","t5","t6","t7","s0","s1","s2","s3","s4","s5","s6","s7","t8","t9","k0","k1","gp","sp","fp","ra"][(word >> 21) & 0x1F])
    return base_dis(word, vaddr)


def d(elf, start, count=32):
    out = []
    for i in range(count):
        v = start + i * 4
        w = elf.word(v)
        if w is None:
            break
        out.append("  %08x  %08x  %s" % (v, w, dis(w, v)))
    return "\n".join(out)

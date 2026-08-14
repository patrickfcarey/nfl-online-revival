"""Disassembler for the Madden NFL 2004 situational-policy bytecode (disc asset #69).

Format reversed from the PS2 ELF (SLUS_207.52); the asset is byte-identical on
Xbox, so one disassembler serves both.  Every rule below cites the instruction
that proves it.

  interpreter          0x0024bfc0   opcode = high nibble, operand = low nibble,
                                    bound `sltiu v0, a0, 8`, table 0x00586ac0
  operand reader       0x0024bea0   var-ref vs 14-bit immediate
  comparator table     0x00586aa0   7 entries (index 7 is NULL)
  native cmd handler   0x0024bb50   13-entry table 0x00586a20
  exit dispatcher      0x0024c468   table 0x00586ae0 (5 script classes)
  loader/init          0x0024c750   byte-swaps + relocates 10 header dwords
  exec entry           0x0024c7c8   runs header[class], then dispatches

Header: ten big-endian u32 file offsets (`0x0024bf10` byte-swaps 10 words and
adds the base pointer).  Entry `n` for n<5 is reached through the primary exec
`0x0024c7c8`; entries 5..9 through the secondary `0x0024c930` (globals +20 =
script+20, `0x0024c750`).

Statement encoding.  A statement starts at address A with one byte:
`op = b >> 4`, `n = b & 0x0f`.  Every branch offset in the file is a signed
big-endian 16-bit value relative to A (the interpreter adds the offset to A+1
and subtracts 1 at use: `addiu v0, v1, -1` at 0x0024c070 / 0x0024c2a8).

  op0  RET            pop a continuation pushed by op2 (0x0024c040)
  op1  IF n           s16 else-offset, then n * (value, cmp-byte, value);
                      all must hold (AND) or control goes to A+else (0x0024c0b0)
  op2  SWITCH n       value (the subject), s16 join-offset, s16 default-offset,
                      then a chain of n case labels (0x0024c150)
  op3  END(4)         ctx[86] = n; interpreter exits with code 4 (0x0024c2b4)
  op4  END(1) b       ctx[86] = b; exits with code 1 (0x0024c2c0)
  op5  END(2) w       ctx[86] = u16 little-endian; exits with code 2 (0x0024c2d4)
  op6  CASE           a case label reached by falling through: skips its own
                      value + s16 next-offset and runs the body (0x0024c300)
  op7  SETPLAY w      ctx[84] = u16 little-endian, then falls through (0x0024c344)

Where the file lives: `GAMEDATA.DAT` member 69, stored with **codec 0 (raw)**,
so the bytes are on the disc uncompressed --  PS2 file offset 0x001fccc0, Xbox
0x001fd500, 28,301 bytes, sha256 82b087e3... on both.

Values (0x0024bea0): a byte with bits 10xxxxxx is variable `b & 0x7f`, read as
a signed halfword from ctx + 2*index; anything else is a two-byte big-endian
immediate, sign-extended from bit 13 (`andi v0, a1, 0x2000`).  In *case-label*
position only, bytes 0x40-0x7f and 0xc0-0xff are intercepted before the reader
(0x0024c200, 0x0024c30c) and mean "ask the engine to pick a play from the group
in the switch subject": handler cmd11 (0x0024bc8c) calls the selector
0x00249498 with flag = b & 0x3f, on the opposing side when (b & 0xc0) == 0xc0.
The case matches iff the pick succeeded (`movn a2, s1, v0`, 0x0024c23c).
"""
from __future__ import annotations

import struct

# 0x00586aa0, read out of the ELF; index 7 is a null pointer (never emitted).
COMPARATORS = {
    0: "==",   # 0x0024bf70  xor; sltiu 1
    1: "==",   # 0x0024bf70  same leaf
    2: "<",    # 0x0024bf80  slt a0, a1
    3: ">",    # 0x0024bf88  slt a1, a0
    4: "<=",   # 0x0024bf90  slt a1, a0; xori 1
    5: ">=",   # 0x0024bfa0  slt a0, a1; xori 1
    6: "!=",   # 0x0024bfb0  xor; sltu zero
}

# ctx halfword slots filled by the snapshot builder 0x0024b570 (common part),
# 0x0024b9e8/0x0024ba98 (per exec) and 0x0024b230/0x0024b4e0 (per class).
# Index = byte offset / 2.  A trailing "?" means the *source* is verified but
# the meaning is inferred; see docs/situational-policy-script.md.
VAR_NAMES = {
    0: "v0?",              # 0x0025a1d8(us)
    1: "v1?",              # 0x0025a1d8(them)
    2: "score_diff",       # sit+0x46 - sit+0x44  (0x002609c0)
    3: "down",             # sit+0x38 remapped 0..4 by the switch at 0x0024b6e0
    4: "togo",             # sit+0x08 (float) - ball spot; 1..25 in the script
    5: "to_goal",          # 50.0 - ball spot: yards to the opponent's goal line
    6: "quarter",          # 0x0013eb70() = clock period, 1..5
    7: "qtr_clock",        # 0x0013eca0(1) = seconds left in the quarter
    8: "half_secs",        # + one quarter length on Q1/Q3 (0x0013ecc0)
    9: "game_secs",        # + 3/2/1 quarter lengths on Q1/Q2/Q3
    10: "rand100_a",       # 0x002f9428(0, 100)
    11: "rand100_b",
    12: "play_clock",      # 0x0013eca0(0)
    13: "clock13?",
    14: "wind_dir?",       # 0x0014c080() * 2.146e-5; only ever tested vs 225/315
    15: "clock_running",   # 0x0013ed00(1) and 0x0013eca0(1) != 0
    16: "pat_kind?",       # 1 or 2, only in the PAT/2-point state (0x0024b710)
    17: "is_pat?",         # 1 in raw play-state 6 (0x0024b738)
    18: "const22",         # 0x00260688(22)
    19: "question",        # 18 by default; 0x0015fa48(0) in class 4 (0x0024b280)
    20: "cmd10_arg",       # *(u16*)(0x00248338(us) + 24)
    21: "state21",         # written by 0x0024b9e8 / 0x0024ba98
    22: "us_play22",       # *(u16*)(0x00248360(us) + 4)
    23: "them_play22",
    24: "q_arg0",          # class-4 question operands (0x0024b230/0x0024b4e0):
    25: "q_arg1",          #   24 = a rating delta, 25/28 = distances,
    26: "q_arg2",          #   26 = a copy of V3, 27 = a side comparison
    27: "q_arg3",
    28: "q_arg4",
    29: "const50_a",       # hardcoded 50 at 0x0024b9a4 -- see the doc: the
    30: "const50_b",       #   "> 60/64/65/75" tests on it can never be true
    31: "state_is_2",      # 0x0015ada0() == 2
    32: "state_is_3",
    33: "flag_0x00243f98",
    34: "flag_0x00243f88",
    35: "coach_A",         # 0x0014b9a8()->f32@0x14 * 100   (coach profile)
    36: "coach_B",         # (s8)*(0x0014b9a8()+0x45)
    37: "coach_C",         # 0x0014b9a8()->f32@0x24 * 100
    38: "coach_D",         # 0x0017b868()
    39: "timeout_us?",     # 0x0016b590(us); 255 is a sentinel (0x0024c3b0)
    40: "timeout_them?",
    41: "_vm_exit_or_subject",  # ctx+82: op2 subject in, exit code out
    42: "_vm_pending_play",     # ctx+84
    43: "_vm_result",           # ctx+86, the END value
}

HEADER_ENTRIES = 10


class Value:
    """A decoded operand: a variable reference or an immediate."""

    __slots__ = ("kind", "n", "size")

    def __init__(self, kind: str, n: int, size: int) -> None:
        self.kind = kind
        self.n = n
        self.size = size

    def __str__(self) -> str:
        if self.kind == "var":
            name = VAR_NAMES.get(self.n)
            return "V%d(%s)" % (self.n, name) if name else "V%d" % self.n
        return str(self.n)


def read_value(data: bytes, pos: int) -> Value:
    """0x0024bea0."""
    b = data[pos]
    if (b & 0xC0) == 0x80:
        return Value("var", b & 0x7F, 1)
    raw = (b << 8) | data[pos + 1]
    if raw & 0x2000:
        raw -= 0x4000
    return Value("imm", raw, 2)


def _s16(data: bytes, pos: int) -> int:
    return struct.unpack_from(">h", data, pos)[0]


class Stmt:
    __slots__ = ("addr", "op", "n", "size", "text", "succ", "join")

    def __init__(self, addr, op, n, size, text, succ, join=None):
        self.addr = addr
        self.op = op
        self.n = n
        self.size = size
        self.text = text
        self.succ = succ          # addresses control can reach next
        self.join = join          # op2's pushed continuation, for op0


def _case_value(data: bytes, pos: int):
    """A case label's value field, with the cmd11 interception (0x0024c200)."""
    b = data[pos]
    if (b & 0xC0) in (0x40, 0xC0):
        side = "THEM" if (b & 0xC0) == 0xC0 else "US"
        return "SELECT_PLAY(group=<subject>, side=%s, flag=0x%02x)" % (side, b & 0x3F), 1
    return str(read_value(data, pos)), read_value(data, pos).size


def decode(data: bytes, addr: int) -> Stmt:
    """Decode the statement at *addr*."""
    b = data[addr]
    op, n = b >> 4, b & 0x0F
    p = addr + 1
    if op == 0:
        return Stmt(addr, op, n, 1, "RET", [])
    if op == 1:
        els = _s16(data, p)
        p += 2
        terms = []
        for _ in range(n):
            left = read_value(data, p)
            p += left.size
            cmp_byte = data[p]
            p += 1
            right = read_value(data, p)
            p += right.size
            terms.append("%s %s %s"
                         % (left, COMPARATORS.get(cmp_byte, "?%d" % cmp_byte), right))
        text = "IF %s ELSE -> %04x" % (" AND ".join(terms), addr + els)
        return Stmt(addr, op, n, p - addr, text, [p, addr + els])
    if op == 2:
        subj = read_value(data, p)
        p += subj.size
        join = addr + _s16(data, p)
        dflt = addr + _s16(data, p + 2)
        p += 4
        text = ("SWITCH %s (%d cases, join -> %04x, default -> %04x)"
                % (subj, n, join, dflt))
        succ = [p, dflt] if n else [dflt]
        return Stmt(addr, op, n, p - addr, text, succ, join)
    if op == 3:
        return Stmt(addr, op, n, 1, "END result=%d (exit 4)" % n, [])
    if op == 4:
        return Stmt(addr, op, n, 2, "END result=%d (exit 1)" % data[p], [])
    if op == 5:
        w = data[p] | (data[p + 1] << 8)
        return Stmt(addr, op, n, 3, "END result=%d (exit 2)" % w, [])
    if op == 6:
        val, size = _case_value(data, p)
        p += size
        nxt = addr + _s16(data, p)
        p += 2
        return Stmt(addr, op, n, p - addr,
                    "CASE %s (next -> %04x)" % (val, nxt), [p, nxt])
    if op == 7:
        w = data[p] | (data[p + 1] << 8)
        return Stmt(addr, op, n, 3, "SETPLAY %d" % w, [p + 2])
    raise ValueError("opcode %d out of range at %04x" % (op, addr))


def entry_points(data: bytes):
    """The ten relocation targets byte-swapped by 0x0024bf10."""
    return list(struct.unpack_from(">%dI" % HEADER_ENTRIES, data, 0))


def walk(data: bytes, roots=None):
    """Recursive-descent disassembly. -> {addr: Stmt}, in address order."""
    if roots is None:
        roots = entry_points(data)
    out = {}
    work = [a for a in roots if a]
    while work:
        a = work.pop()
        if a in out or not (0 <= a < len(data)):
            continue
        try:
            st = decode(data, a)
        except (ValueError, IndexError) as exc:
            out[a] = Stmt(a, -1, 0, 1, "<undecodable: %s>" % exc, [])
            continue
        out[a] = st
        work.extend(st.succ)
        if st.join is not None:
            work.append(st.join)
    return dict(sorted(out.items()))


def listing(data: bytes, roots=None) -> str:
    stmts = walk(data, roots)
    entries = {a: i for i, a in enumerate(entry_points(data))}
    lines = []
    prev_end = None
    for addr, st in stmts.items():
        if prev_end is not None and addr != prev_end:
            lines.append("        ... %+d bytes ..." % (addr - prev_end))
        if addr in entries:
            lines.append("=== ENTRY %d ===" % entries[addr])
        raw = data[addr:addr + st.size].hex()
        lines.append("  %04x  %-20s  %s" % (addr, raw, st.text))
        prev_end = addr + st.size
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    blob = open(sys.argv[1], "rb").read()
    roots = [int(x, 0) for x in sys.argv[2:]] or None
    print("header:", " ".join("%04x" % v for v in entry_points(blob)))
    print(listing(blob, roots))

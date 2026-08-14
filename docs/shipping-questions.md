# Two shipping questions: the ELF grow path, and the Xbox seam's six callers

Answered 2026-08-14. Static only — the PS2 ELF, the two captured EE RAM dumps
(`extract/ee_mainmenu.bin`, `extract/ee_inplay.bin`, 32 MB each, offset ==
EE physical address, verified against the loaded image), and
`extract/xbox/default.xbe`. No rig, nothing executed.

Q1 answers ledger item **B6** (pnach→ISO pipeline Phase P2, "placement is the
open question"). Q2 answers `xbox-hook-map.md` §8 item 2.

---

# Q1 — where a second PT_LOAD can live in EE memory

## The short answer

**Recommended new segment: `vaddr = paddr = 0x00660000`, size `0x10000`
(64 KB), created by two single-word patches to the pool initialiser at
`0x002F9A00`.** Confidence: **A** for the memory map (the static computation
reproduces both live dumps byte-for-byte); **B** for the recommendation itself
(the arithmetic is certain, the *sufficiency* of the pool it borrows from is
one observation, not a peak).

And a finding that changes the shape of P2:

**Up to 64,744 bytes of provably-dead, all-zero file space already exist inside
the ELF at file `0x0050A5C0 .. 0x0051A2A8`, covered by no PT_LOAD.** A second
program header pointed at it gives new loadable code **without growing the file
at all** — so the ISO stays a same-size bake and `tools/patch_iso_elf.py` works
unchanged. The ISO directory-record surgery is only needed above ~63 KB.

## Finding 1 — there is no unclaimed EE RAM. Every byte above `_end` is a pool.

The literal question ("what range is provably free?") has a closed-set answer:
**16 bytes, at `0x01FFFFF0..0x02000000`.** Everything else from the end of the
image to the top of RAM is handed to one of four allocator pools at boot.

`0x002F9A00` is the memory-map initialiser. Resolved values are shown; note
`addiu` **sign-extends**, which is why the descriptor addresses are `0x00549xxx`
and not the `0x00559xxx` a naive read gives (this document's first pass made
exactly that error and the live dump caught it):

```
002f9a00  27bdffe0  addiu sp,sp,-0x20
002f9a04  3c020055  lui   v0,0x0055
002f9a08  3c0d0066  lui   t5,0x0066
002f9a0c  3c0b0006  lui   t3,0x0006
002f9a14  24589d90  addiu t8,v0,0x9d90    ; t8 = 0x00549D90   descriptor "DEBUG"
002f9a18  25ad0000  addiu t5,t5,0x0000    ; t5 = 0x00660000   = _end
002f9a1c  356bb000  ori   t3,t3,0xb000    ; t3 = 0x0006B000
002f9a24  ...       addiu a1,a1,0x9d68    ; a1 = 0x00549D68   descriptor "SOUND"
002f9a28  01ab8821  addu  s1,t5,t3        ; s1 = 0x006CB000
002f9a30  3c0801d5  lui   t0,0x01d5
002f9a38  24021800  addiu v0,zero,0x1800
002f9a3c  262e1800  addiu t6,s1,0x1800    ; t6 = 0x006CC800
002f9a40  aca20010  sw    v0,16(a1)       ; SOUND.size = 0x1800
002f9a44  24849db8  addiu a0,a0,0x9db8    ; a0 = 0x00549DB8   descriptor "STATE"
002f9a48  3508aff0  ori   t0,t0,0xaff0    ; t0 = 0x01D5AFF0
002f9a50  ac8b0010  sw    t3,16(a0)       ; STATE.size = 0x0006B000
002f9a54  010e4023  subu  t0,t0,t6        ; t0 = 0x0168E7F0
002f9a58  24e79cd0  addiu a3,a3,0x9cd0    ; a3 = 0x00549CD0   descriptor "MAIN"
002f9a60  ace80010  sw    t0,16(a3)       ; MAIN.size  = 0x0168E7F0
002f9a64  01c88021  addu  s0,t6,t0        ; s0 = 0x01D5AFF0
002f9a68  24c69d40  addiu a2,a2,0x9d40    ; a2 = 0x00549D40   descriptor "DB"
002f9a6c  3c0a002a  lui   t2,0x002a
002f9a70  acd0000c  sw    s0,12(a2)       ; DB.base    = 0x01D5AFF0
002f9a74  354a5000  ori   t2,t2,0x5000    ; t2 = 0x002A5000
002f9a78  ac8d000c  sw    t5,12(a0)       ; STATE.base = 0x00660000
002f9a7c  020a6021  addu  t4,s0,t2        ; t4 = 0x01FFFFF0
002f9a80  acb1000c  sw    s1,12(a1)       ; SOUND.base = 0x006CB000
002f9a84  3c090800  lui   t1,0x0800       ; t1 = 0x08000000   (128 MB devkit)
002f9a88  acca0010  sw    t2,16(a2)       ; DB.size    = 0x002A5000
002f9a8c  012c4823  subu  t1,t1,t4        ; t1 = 0x06000010
002f9a90  acee000c  sw    t6,12(a3)       ; MAIN.base  = 0x006CC800
002f9a94  01897821  addu  t7,t4,t1        ; t7 = 0x08000000
002f9a98  3c030200  lui   v1,0x0200       ; v1 = 0x02000000   (32 MB retail)
002f9a9c  af0c000c  sw    t4,12(t8)       ; DEBUG.base = 0x01FFFFF0
002f9aa0  006f182b  sltu  v1,v1,t7        ; 0x02000000 < 0x08000000 -> 1
002f9aa4  10600002  beq   v1,zero,0x002f9ab0
002f9aa8  af090010  sw    t1,16(t8)       ; DEBUG.size = 0x06000010 ...
002f9aac  af000010  sw    zero,16(t8)     ; ... clamped to 0 on 32 MB hardware
```

Descriptor stride is `0x28`; `+0x00` is an ASCII name, `+0x0C` base, `+0x10`
size, `+0x14`/`+0x18` max-allocation / max-free-block counts.

**This is the boot path, not a debug path.** `0x002F9A00` has exactly one `jal`
caller, `0x002F9AFC`, inside `0x002F9AC0`; `0x002F9AC0` has exactly one caller,
`0x00100280` — a four-instruction function immediately after crt0's entry block,
unconditional, with a failure branch. Closed set over every `jal` in `.text`.

| descriptor | name | base | size | span |
|---|---|---|---|---|
| `0x00549DB8` | `STATE` | `0x00660000` | `0x0006B000` | `0x00660000 – 0x006CB000` (428 KB) |
| `0x00549D68` | `SOUND` | `0x006CB000` | `0x00001800` | `0x006CB000 – 0x006CC800` (6 KB) |
| `0x00549CD0` | `MAIN`  | `0x006CC800` | `0x0168E7F0` | `0x006CC800 – 0x01D5AFF0` (22.6 MB) |
| `0x00549D40` | `DB`    | `0x01D5AFF0` | `0x002A5000` | `0x01D5AFF0 – 0x01FFFFF0` (2.6 MB) |
| `0x00549D90` | `DEBUG` | `0x01FFFFF0` | `0` | empty on retail (would be 96 MB on a 128 MB devkit) |

**Contiguous, gapless, and exactly the whole of EE RAM above `_end`.**

The registry allocator named in the B6 brief, `0x0039D6C8` (the path the `ptrk`
ctor takes), carries **no base or limit constants of its own** — it calls
`0x0039D358`, then `0x0046D038` with `lw a0,16(s0)` (a pool handle from the
object's descriptor), i.e. it funnels into this same pool manager
(`0x0046C938`/`0x0046C9A8`/`0x0046CA90`/`0x0046D038`). There is no second
memory map to find.

**Live corroboration (both dumps, independently):** every one of those ten
numbers is present at the computed descriptor address in `ee_mainmenu.bin` *and*
`ee_inplay.bin` — e.g. `[0x00549DC4] = 0x00660000`, `[0x00549CE0] = 0x0168E7F0`,
`[0x00549D9C] = 0x01FFFFF0`. Each pool's arena also carries an in-arena copy of
`{base,end,size}` at `pool+0x24/0x28/0x2C` and the pools are linked
(`STATE+0x18 → 0x006CB010`, `SOUND+0x18 → 0x01D5B000`, `DB+0x18 → 0x006CC810`,
`MAIN+0x18 → 0`). The `MAIN` descriptor even carries the ASCII
`host:memory.log` at `+0x2C` — this is the shipped memory manager, not a
vestigial one.

**Second, independent live corroboration:** sweeping the image's own
`.data`/`.sdata`/`.bss` in the live dumps for words in `[0x00660000,0x02000000)`
yields 4,998 pointers in-play / 2,513 at the menu, **min `0x00660000`, max
`0x01FFFFF0`** — exactly the pool span, from both ends.

**Third:** the highest non-zero byte in *both* 32 MB dumps is `0x01FFFFEF` —
the byte immediately below the `DEBUG` base. The 16 bytes above it are the only
untouched, unclaimed EE RAM in the machine.

### The 1 MB occupancy map (why "a high fixed address like 0x01000000" is dead)

`ee_inplay.bin`, non-zero bytes per megabyte: `0x00800000`–`0x01F00000` runs
57%–96% occupied throughout. `0x01000000` specifically is **73.2%** written.
There is no sparse upper half; the game fills the machine.

Per-pool, non-zero bytes:

| pool | size | menu | in-play |
|---|---|---|---|
| `STATE` | 438,272 | 515 B (0.1%) | 78,538 B (17.9%) |
| `SOUND` | 6,144 | 71 B | 145 B |
| `MAIN` | 23,652,336 | 72.9% | 70.6% |
| `DB` | 2,772,992 | 9.0% | 27.3% |

## Finding 2 — the closed-set negative on `0x00660000`

Every `lui _,0x0066` in `.text` (32 sites) was disassembled and its completing
instruction resolved. **Exactly four materialise `0x00660000`:**

| site | pair | role |
|---|---|---|
| `0x00100120` | `0x00100128` | crt0 `.sbss`+`.bss` clear **upper bound** |
| `0x00100180` | `0x00100188` | crt0 `InitHeap(_end, 0)` — syscall 61, heap size **0** |
| `0x002F9A08` | `0x002F9A18` | the pool initialiser above |
| `0x002FB120` | `0x002FB144` | backtrace filter upper bound (below) |

Two more resolve to `0x0065A000` (crt0's `InitMainThread(gp, _stack, 0x6000,
_args, _root)`, syscall 60, and the backtrace code's stack-top computation). The
remaining 26 all resolve to `0x00659E00`–`0x00659FD0`, i.e. `.bss` globals below
`MemSiz` (`0x00659FDC`). A widened scan for *any* address materialisation in
`[0x0065A000, 0x02000000)` over `.text`+`.vutext` (48-instruction pairing window,
register-kill tracking) returns nothing else: the only other `addiu`-completed
hits are `0x007EFF81` at `0x004BC3B0` and `0x00C00002` at `0x0014E774`, both
disassembled and both **not addresses** — the first is IEEE-754 exponent-bias
arithmetic (`lui v0,0x007f; addiu t0,v0,-0x7F`) inside a soft-float routine.

Two consequences that matter for placement:

- **crt0's `.bss` clear stops at exactly `0x00660000`.** A segment placed at
  `0x00660000` or above is *not* zeroed by crt0 and survives boot intact.
  A segment placed *below* it would be wiped — a real trap for anyone who
  thinks "just extend `MemSiz`".
- **The kernel heap is zero-sized.** `InitHeap(0x00660000, 0)` means nothing
  ever `sbrk`s into the region; the game's own pools are the only claimant.

There is also a hard-coded "is this a code address" window in the memory
manager's backtrace collector:

```
002fb130  8e230000  lw    v1,0(s1)         ; walk the stack
002fb138  3c020010  lui   v0,0x0010
002fb13c  0043102b  sltu  v0,v0,v1         ; v1 > 0x00100000 ?
002fb140  10400007  beq   v0,zero,0x002fb160
002fb144  26e20000  addiu v0,s7,0x0000     ; s7 = 0x0066 << 16  -> v0 = 0x00660000
002fb148  0062102b  sltu  v0,v1,v0         ; v1 < 0x00660000 ?
002fb14c  10400004  beq   v0,zero,0x002fb160
```

It collects up to 6 stack words inside `[0x00100000, 0x00660000)` as a call
chain for the allocation log. Code in a new segment above `0x00660000` will not
be recognised there — cosmetic, affects only the memory log, and fixable with
one more word (`lui s7,0x0066` → `lui s7,0x0067`).

## Recommendation — take 64 KB off the bottom of `STATE`

`STATE`'s base and size are materialised by two adjacent `lui`s, and
`s1 = t5 + t3` (the `SOUND` base) is computed from both. **Add N to the base and
subtract N from the size and every other pool boundary in the machine is
byte-identical.** For N = `0x10000`:

| addr | now | patch to | effect |
|---|---|---|---|
| `0x002F9A08` | `3C0D0066` `lui t5,0x0066` | `3C0D0067` `lui t5,0x0067` | `STATE.base` `0x00660000` → `0x00670000` |
| `0x002F9A0C` | `3C0B0006` `lui t3,0x0006` | `3C0B0005` `lui t3,0x0005` | `STATE.size` `0x0006B000` → `0x0005B000` |
| `0x002FB120` | `3C170066` `lui s7,0x0066` | `3C170067` `lui s7,0x0067` | optional: widen the backtrace window |

Invariant check: `0x00670000 + 0x0005B000 = 0x006CB000` — `SOUND`, `MAIN`, `DB`
and `DEBUG` keep their exact current bases and sizes. Result: **`[0x00660000,
0x00670000)` — 65,536 bytes, owned by nobody.**

All three are file-backed `.text` words (`file = vaddr − 0xFF000`), so they bake
through the existing P1 path with no new mechanism.

**Why this pool, and the honest risk.** `STATE` is the least-loaded pool
(17.9% in-play, 0.1% at the menu) and its descriptor caps it at 100 live
allocations (`+0x14 = 0x64`); the situation object lives there (live pointer
`[0x00601F4C] = 0x00661350` in-play). A 64 KB bite leaves 372,736 B against an
observed 78,538 B footprint. *Hypothesis*, not Finding: that margin holds across
a whole session — a single in-play sample is not a high-water mark.

**The alternative if `STATE` proves tight:** take it off the *top of RAM*
instead by shrinking `DB` — patch `0x002F9A6C`/`0x002F9A74`
(`lui t2,0x002a` / `ori t2,t2,0x5000` = `0x002A5000`) down by N. That moves only
`DEBUG`'s base (already size 0) and leaves a hole at `[0x01FFFFF0−N,
0x01FFFFF0)`. `DB` is 2.6 MB at 27.3%, so the same 64 KB is a 2.4% bite instead
of a 15% one. It is the better choice if a live check shows `STATE` under
pressure; it is the worse choice for locality and for the backtrace window.

**Rejected, with reasons:**

- *A high fixed address (`0x01000000`, the 16 MB mark).* `MAIN` owns it and
  in-play it is 73.2% written. Dead.
- *Above `0x02000000`.* Does not exist on retail hardware — and the game's own
  `sltu v1,0x02000000,t7` guard at `0x002F9AA0` proves it knows that.
- *`0x01FFFFF0..0x02000000`.* Provably free and provably useless: 16 bytes.
- *The low 1 MB (`0x00000000..0x00100000`).* EE kernel; `LoadExecPS2` owns it.
  Not provably free, and not worth proving.
- *Extending `MemSiz` into `0x0065A000` (the `.stack` **address**).* That is the
  live main-thread stack — `InitMainThread(_gp, 0x0065A000, 0x6000, …)` at
  `0x00100174`. Different thing entirely from the `.stack` **file bytes** below.

## Finding 3 — 64,744 bytes of dead file space already exist

`code-caves.md` rejected `.stack` and the DVP overlay sections as caves because
they are "outside PT_LOAD — never loaded". That is the correct verdict for a
*pnach* cave and exactly the wrong one here: **being outside every PT_LOAD is
what makes those file bytes free to repurpose**, and a second program header is
precisely the thing that makes them loadable.

| file range | section | bytes | content |
|---|---|---|---|
| `0x0050A579 – 0x0050A5A8` | (PT_LOAD `FileSiz` tail → `.reginfo`) | 47 | all zero |
| `0x0050A5A8 – 0x0050A5C0` | `.reginfo` | 24 | **live — do not touch** |
| `0x0050A5C0 – 0x005142A8` | 26 × `.DVP.overlay.*` | 40,168 | **all zero** |
| `0x005142A8 – 0x0051A2A8` | `.stack` (PROGBITS, `sh_flags = 0`) | 24,576 | **all zero** |
| `0x0051A2A8 – 0x0051B234` | `.DVP.ovlytab`, `.ovlystrtab`, `.shstrtab`, section header table | 4,012 | live |

**`0x0050A5C0 .. 0x0051A2A8` is 64,744 contiguous, all-zero bytes covered by no
PT_LOAD.** The ELF file ends at `0x0051B234` with the section header table
running exactly to the last byte; nothing follows.

So P2 splits into two phases:

- **P2a — the same-size second segment (recommended first).** New PT_LOAD at
  file `0x0050A5C0`, `vaddr = paddr = 0x00660000`, up to 63.2 KB. **ELF file
  size unchanged → `tools/patch_iso_elf.py` ships it today, no ISO surgery, no
  directory record, no relayout.** Combined with the 9.2 KB of caves that is
  ~72 KB of injectable code. Prove the mechanism here, on a trivial segment,
  before the coach-brain depends on it.
- **P2b — the grown file.** Only if the coach-brain needs more than ~63 KB.

## The ELF mechanics, exactly

Nothing moves. The program header table is at `0x34`, one entry of 32 bytes, so
it occupies `0x34..0x54`; the first section data is at `0x1000`; **`0x54..0x1000`
is 4,012 bytes and verified all zero.** Room for 125 more program headers.

1. `e_phnum` at file `0x2C` (`<H`): `1` → `2`.
2. Write a 32-byte `Elf32_Phdr` at file `0x54`:
   `p_type = PT_LOAD (1)`, `p_offset = 0x0050A5C0`, `p_vaddr = 0x00660000`,
   **`p_paddr = 0x00660000`** (set it equal to `p_vaddr`: PH0 already does, and
   some PS2 ELF loaders copy to `p_paddr`, so leaving them different is an
   unforced risk), `p_filesz = p_memsz = the code size`, `p_flags = 7 (RWX)`
   matching PH0, `p_align = 0x10` (congruence `p_offset ≡ p_vaddr mod p_align`
   holds; both are `0x10`-aligned).
3. Nothing else in the header changes. `e_shoff`, all 42 section headers, and
   every existing byte stay put.
4. **`tools/bake_pnach.py` needs no change.** It already loops over every
   program header, keeps a `Segment` per `PT_LOAD`, derives each one's delta
   from the headers rather than hard-coding `0xFF000`, and `convention_notes()`
   already prints a warning when a segment's delta is not the familiar one.
   A patch into the new segment classifies as file-backed automatically. The
   new segment's mapping is `file = vaddr − 0x155A40`
   (`0x00660000 − 0x0050A5C0`), and the baker will say so out loud.
5. The section headers still label those file bytes `.DVP.overlay.*` /
   `.stack`. Loaders use program headers, not section headers, so this is
   cosmetic; rename or drop them only for tidiness.

For **P2b** (file grows): append at the end of the file (offset `0x0051B234`,
rounded up), point the new `p_offset` there. Still no existing byte moves —
appending *after* the section header table leaves every `sh_offset` valid.

## The ISO mechanics, exactly (P2b only)

`tools/patch_iso_elf.py` already walks the ISO9660 directory to the boot file
and reads `lba` at record `+2` and `size` at record `+10`; it refuses on a size
mismatch by design. To grow:

1. Append the enlarged ELF at the end of the image, sector-aligned (2048).
2. Rewrite the directory record's extent and length. **Both-endian, all four
   fields:** LBA little-endian at `+2` *and* big-endian at `+6`; size
   little-endian at `+10` *and* big-endian at `+14`. Writing only the LE halves
   is the classic bug — most readers use LE and it appears to work.
3. Bump the PVD's *volume space size* (sector 16, offset `+80`, both-endian) to
   cover the appended sectors.
4. The old extent becomes dead space. On a DVD5 that is 5 MB of nothing —
   cheaper than a relayout.
5. Path tables list directories only, so a file move needs no path-table edit.
6. `_records()` must start yielding each record's byte offset in the image —
   it currently discards it.

**Verify on the operator's actual image before trusting any of this:** that it
is 2048-byte user-data sectors (not 2352 raw); that the boot file's record
appears exactly once (no Joliet/UDF second tree carrying a second copy of the
extent — PS2 discs are normally plain ISO9660, but "normally" is not evidence);
and that the appended sectors are inside the disc's declared size.

## What a live check must confirm before this is trusted

1. **The invariant.** After boot, read the five descriptors: `STATE` base
   `0x00670000` size `0x0005B000`, and `SOUND` `0x006CB000`/`0x1800`, `MAIN`
   `0x006CC800`/`0x0168E7F0`, `DB` `0x01D5AFF0`/`0x002A5000`, `DEBUG`
   `0x01FFFFF0`/`0` **unchanged**. Any drift means the patch was wrong.
2. **The segment landed and survives.** Checksum `[0x00660000, 0x00670000)`
   at the title screen, at the main menu, in-play, and after a full quarter.
   It must be byte-identical every time. (A trivial segment carrying a known
   pattern is the right first test — do this before any real code goes there.)
3. **Nothing allocates below `0x00670000`.** Walk `STATE`'s block list from the
   arena header at the new base; confirm no live pointer in the image's
   data/bss falls in the hole (the sweep in Finding 1 is the ready-made test).
4. **`STATE` does not run dry.** It is capped at 100 allocations. Stress it:
   full game, franchise, every menu, replays. The shipped memory logger
   (`host:memory.log`, named in the `MAIN` descriptor) is the instrument if it
   can be enabled — it would give real high-water marks instead of the
   single-sample estimate this recommendation rests on.
5. **PCSX2 honours a second program header.** Prove it with the trivial segment
   before anything depends on it; and confirm the real BIOS loader agrees if
   hardware is ever in scope.
6. **CRC.** The ELF changes, so trap T2 applies in full — every savestate
   detaches and must be re-baselined.

---

# Q2 — the Xbox seam's six call sites, classified

Method: capstone 5.0.7 + `recon/xbe.py`. Every function head is corroborated by
`0xCC` (`int3`) padding and/or by being a harvested `E8 rel32` target; all
disassembly is function-relative from a corroborated head, never a linear sweep.
The call index — **67,763 `E8` sites → 12,337 distinct targets** — reproduces the
hook map's number exactly.

## The answer

**None of the four unknown sites is special teams, a menu path, practice, or
replay. All four are the play-call state's own AI selection — the same code path
as the VM pair.** They live in the module that owns the "pick a play" screen: the
`'pcal'` object's own translation unit.

| call site | enclosing fn | group id (quoted) | what it is | reachable? | conf |
|---|---|---|---|---|---|
| `0x00076E6B` | `0x00076E30` arm mode 2 | `00076E68  6a 02  push 2` | AI re-pick, group **2** | **yes** — `pcal+0x1C = 2` | **A** |
| `0x00076E92` | `0x00076E30` arm mode 3 | `00076E8F  6a 20  push 0x20` | AI re-pick, group **0x20** | **no producer found** | A (site) / B (dead) |
| `0x00076EBB` | `0x00076E30` arm mode 4 | `00076EB8  6a 21  push 0x21` | AI re-pick, group **0x21** | **yes** — `pstp+0x14C = 4` | **A** |
| `0x000771C6` | `0x00077060` | `000771C3  6a 0a  push 0xa` | AI pick on **entering the play-call state**, group **0x0A** | **yes** | **A** |
| `0x001336C5` | `0x001335F0` cmd 8 | from the VM command word, `and edx,0xFFFF7FFF` | VM handler (known) | yes | A |
| `0x001338C0` | `0x00133860` cmd 8 | from the VM command word, `and ecx,0xFFFF7FFF` | VM handler (known) | yes | A |

**Finding — none of the four sets bit 15.** All four are `push imm8`
sign-extended to `0x00000002` / `0x00000020` / `0x00000021` / `0x0000000A`. The
two VM sites explicitly clear bit 15 (`0x001336BD` / `0x001338B8`:
`81 e2 ff 7f ff ff`); the four literal pushes never had it set. So the group word
reaching the seam is clean at all six sites — but only because two of them
sanitise it and four are constants.

## The module is `'pcal'`, not special teams

`0x00077000` is a registry constructor of exactly the `ptrk`/`fatg` shape:

```
00077002  68 6c 61 63 70     push 0x7063616c        ; fourcc, LE bytes 'lacp' -> 'pcal'
00077009  53                 push ebx               ; 0
0007700A  6a 58              push 0x58              ; size = 88 B
0007700C  68 c8 2b 53 00     push 0x532bc8          ; &global
00077012  e8 e9 f7 1d 00     call 0x256800          ; the same registry create as ptrk/fatg
```

So **`[0x00532BC8]` is the `'pcal'` (play-call) object, 88 bytes.**

Two independent corroborations that the whole neighbourhood is the play-call
screen:

- **Strings.** Sweeping *every* `.rdata`/`.data` C-string reference out of
  `.text` in `[0x00075000, 0x0007A000)` returns **five hits total**, four of them
  in one function: `0x00078638 → 0x0044F8C8 'Offense pick a play!'`,
  `0x00078698 → 0x0044F8F8 'Defense pick a play!  %d'`,
  `0x000786A7`/`0x000786B6 → 'Defense pick a play!'`. (The fifth is `'tsop'` at
  `0x000797E9`.)
- **Closed-set negative on special teams.** No `punt` / `kickoff` / `onside` /
  `field goal` / `extra point` / `FG` / `PAT` / `coin toss` string in the 4.89 MB
  image is referenced from anywhere in `0x00075800–0x00079000`. Every one of them
  is referenced from `0x00048xxx`, `0x00097xxx`, `0x0013Fxxx`, `0x0016Bxxx`,
  `0x0018Exxx`, `0x0019Dxxx`, `0x001D2xxx` — a different module entirely.
- **The positive control.** Special-teams *play names* do exist in the image, as
  a contiguous table at `.data` `0x00467500–0x00467610`: `'Punt'`,
  `'Max Prot. Punt'`, `'Max Cover Punt'`, `'Punt Left/Middle/Right'`,
  `'Fake Punt Pass'`, `'Fake Punt PA Pass'`, `'RB Direct Snap Handoff'`,
  `'Field Goal'`, `'Fake FG-Pass'`, `'Fake FG - TE Pass'`, `'Fake FG-Run'`.
  Nine of those strings were traced: **all nine are referenced from exactly one
  function, `0x0016C5C0`, and none from anywhere in the `'pcal'` module.** So
  the negative above is a real absence, not a failed search.

**Correction to `docs/xbox-hook-map.md` §5.** The row "`0x00076E30` … special
teams (jump-table dispatch on a 1..4 mode)" is **half right and half wrong**: the
1..4 jump-table dispatch is confirmed exactly; the *special teams* label is
refuted.

## `0x00076E30` — the four-arm mode dispatch, decoded

```
; int PcalReselect(int mode)      cdecl, one stack arg, caller-cleaned, plain `ret`
00076E30  51                       push ecx
00076E31  a1 90 30 53 00           mov  eax, dword ptr [0x533090]   ; 'scru' (situation)
00076E41  8a 48 40                 mov  cl, byte ptr [eax + 0x40]   ; possession
00076E4A  80 f3 01                 xor  bl, 1                       ; ebx = possession ^ 1
00076E4D  8b 44 24 0c              mov  eax, dword ptr [esp + 0xc]  ; arg0 = mode
00076E51  48                       dec  eax
00076E52  83 f8 03                 cmp  eax, 3
00076E55  0f 87 91 00 00 00        ja   0x76eec                     ; mode not in 1..4 -> return
00076E5D  ff 24 85 2c 6f 07 00     jmp  dword ptr [eax*4 + 0x76f2c]
```

Jump table at `0x00076F2C`, read out of the file — **exactly four entries, the
fifth dword is `CCCCCCCC` padding**:

| mode | arm | behaviour |
|---|---|---|
| 1 | `0x00076EEF` | **no seam call.** Blits `plbk[t]+0x2BF8 → +0x28` (`rep movsd 0x57A` = 5608 B) for **both** teams, then `[prep+0x13C] = 2` via `0x00089FB0` |
| 2 | `0x00076E64` | `push 2; push side; call 0x1311C0` — the seam |
| 3 | `0x00076E8B` | `push 0x20; push side; call 0x1311C0` |
| 4 | `0x00076EB4` | `push 0x21; push side; call 0x1311C0` |

Arm 2 in full, from the table entry (the byte-boundary matters — reading back
from the call site desynchronises):

```
00076E64  8b 74 24 0c              mov  esi, dword ptr [esp + 0xc]  ; side
00076E68  6a 02                    push 2                           ; <<< GROUP
00076E6A  56                       push esi
00076E6B  e8 50 a3 0b 00           call 0x1311c0                    ; <<< THE SEAM
00076E70  56                       push esi
00076E71  e8 5a 8e 0b 00           call 0x12fcd0                    ; commit the pick
```

Arms 3 and 4 are byte-for-byte the same but for the pushed immediate.

## Reachability — and a correction to the first pass

`0x00076E30` has exactly **two** `E8` callers and **zero** absolute pointers
anywhere in the file, so its `mode` argument has exactly two producers. They are
**different**, and each must be bounded separately:

```
; caller 1 -- 0x00078B50, mode comes from the pcal object
00078B98  89 58 1c                 mov  dword ptr [eax + 0x1c], ebx  ; pcal+0x1C = 0
00078BA4  e8 67 b5 0b 00           call 0x134110                     ; run play-call script VM
00078BAF  8b 41 1c                 mov  eax, dword ptr [ecx + 0x1c]  ; <- the script's verdict
00078BBD  e8 6e e2 ff ff           call 0x76e30

; caller 2 -- 0x00078BE0, mode arrives in ESI (register parameter, never written
; in that function), supplied by 0x000851F6 inside the state-4 UPDATE handler
000851F0  8b b2 4c 01 00 00        mov  esi, dword ptr [edx + 0x14c] ; edx = [0x532bec] = 'pstp'
000851F6  e8 e5 39 ff ff           call 0x78be0
```

**Producer 1 — `pcal+0x1C ∈ {0, 1, 2}`.** The only non-zero writers in the image
are two arms of VM dispatcher A:
`0x001337E4 c7 41 1c 01 00 00 00` (cmd 5 → 1) and
`0x001337F3 c7 42 1c 02 00 00 00` (cmd 12 → 2).

**Producer 2 — `pstp+0x14C ∈ {0, 1, 4}.** This is where the first pass was
wrong.** A sweep of all 107 `.text` references to `[0x00532BEC]` across their 35
enclosing functions, plus an independent byte-pattern sweep of all `0x360F4C`
bytes of `.text` for `C7 8x 4C 01 00 00 imm32`, finds the writers in
`0x00084460` (14 bytes of `0xCC` before its head):

```
000844EE  8b c5                    mov  eax, ebp                    ; a post-play result code
000844F0  83 e8 06                 sub  eax, 6
000844F3  74 2c                    je   0x84521                     ; ebp==6 -> [pstp+0x14C] = 0
000844F5  83 e8 02                 sub  eax, 2
000844F8  74 15                    je   0x8450f                     ; ebp==8 -> [pstp+0x14C] = 4
000844FA  48                       dec  eax
000844FB  75 32                    jne  0x8452f
000844FD  c7 87 4c 01 00 00 01 …   mov  dword ptr [edi + 0x14c], 1  ; ebp==9 -> 1
0008450F  c7 87 4c 01 00 00 04 …   mov  dword ptr [edi + 0x14c], 4
00084521  89 9f 4c 01 00 00        mov  dword ptr [edi + 0x14c], ebx
```

**Correction.** The first pass bounded only `pcal+0x1C` and concluded that
*both* `0x20` and `0x21` were unreachable. That is wrong for `0x21`: mode 4 is
written at `0x0008450F` on post-play result code 8, and mode 4 is the arm that
pushes `0x21`. **Group `0x21` is live.**

Union of the two producers is `{0, 1, 2, 4}`. **Mode 3 — and only mode 3, i.e.
the `push 0x20` site at `0x00076E92` — has no producer anywhere in either
sweep.** *Hypothesis* (not Finding): it is dead code. Residual risk, stated:
a write through a pointer received as a parameter escapes both sweeps.

## `0x00077060` — the play-call state's ENTER handler

`0x00077060` has one `E8` caller and **one absolute pointer**, at `.rdata`
`0x0041883C`. That pointer is the crack. `0x00418800` is a 16 × 12-byte
game-state handler table `{enter, mid, exit}`, driven by:

```
0006FC58  8b 0c 8d 08 88 41 00     mov  ecx, [ecx*4 + 0x418808]   ; table[cur].exit
0006FC69  ff d1                    call ecx
0006FC8E  8b 04 85 00 88 41 00     mov  eax, [eax*4 + 0x418800]   ; table[new].enter
0006FC9A  ff d0                    call eax
```

Read out of the file, `0x0041883C` = `table[5].enter` = `0x00077060`
(table row 5: enter `0x00077060`, mid `0x00077940`, exit `0x000781D0`).
Row 4 is `{0x000849A0, 0x00084FE0, 0x000852B0}` — the `'pstp'` (post-play) state,
which is the module that owns producer 2 above.

The seam call inside it, with its guard:

```
000771A0  e8 6b cf 0b 00           call 0x134110                   ; run play-call script 0
000771B7  a1 c8 2b 53 00           mov  eax, dword ptr [0x532bc8]
000771BC  8b 48 1c                 mov  ecx, dword ptr [eax + 0x1c] ; the script's verdict
000771BF  85 c9 / 74 0b            test ecx,ecx / je 0x771ce        ; 0 -> skip the AI pick
000771C3  6a 0a                    push 0xa                         ; <<< GROUP 0x0A
000771C5  53                       push ebx                         ; side = possession
000771C6  e8 f5 9f 0b 00           call 0x1311c0                    ; <<< THE SEAM
000771CE  a1 6c da 51 00           mov  eax, dword ptr [0x51da6c]
000771D3  83 20 fc                 and  dword ptr [eax], 0xfffffffc ; 'plbk' flags &= ~3
```

The other caller, `0x0001AE6B` inside `0x0001ADB0`, explicitly gates on the state
(`0x0001AE27  cmp eax,5 / jne`) and fires off a counter at `[0x0050DC60]` — the
play-call **timeout / auto-pick**. So `0x00077060` runs both on entry to the
play-call screen and when the clock runs out on it.

## Two more corrections to `docs/xbox-hook-map.md`

**1. `0x00133580` is not the VM dispatcher — there are two dispatchers, and it
is neither.** `0x00133580` is a ~100-byte routine ending at `0x001335E3`,
followed by **12 `int3` bytes**. The dispatchers are:

| fn | guard | table | arms | seam call | side used |
|---|---|---|---|---|---|
| `0x001335F0` (12 B `0xCC` pad, 0 `E8` callers) | `cmp ecx,0xc / ja` | `0x0013382C` | **13** (cmd 0–12) | `0x001336C5` in **arm 8** (`0x001336B3`) | possession |
| `0x00133860` (starts exactly where A's table ends) | `cmp ecx,0xb / ja` | `0x00133984` | **12** (cmd 0–11) | `0x001338C0` in **arm 8** (`0x001338AE`) | `possession ^ 1` |

Dispatcher A's table occupies `0x0013382C..0x00133860` (13 × 4), so
`0x00133860` cannot be fallen into — that is what corroborates it as a head
despite having no `0xCC` padding. Dispatcher B's table has a 13th dword of
`CCCCCCCC`, confirming exactly 12 arms. The doc's "12-arm table at `0x00133984`"
is right about the table and wrong about which function owns it. Also, the
§7 claim "`0x00133580` has zero `E8` callers" is literally false: it has one,
`0x0013389F`, which sits nine bytes into dispatcher B's **arm 0**
(`0x00133896`, read out of the table) — a forward call from an arm into the
separate helper, not a recursion. The *intent* of the claim (no external
caller) survives. And `0x001311C0` and
`0x00133580` really do appear as absolute LE32 pointers **nowhere** in the
4,890,624-byte file — re-verified, 0 hits each, so the six `E8` sites are the
complete caller set for the seam.

**2. Open item #7 is resolved statically: the tendency kill switch is
data-driven, not a menu option.** `[0x00532B48]` is the `'prac'` object (ctor
`0x00052CA0`), and `+0x17E` is set from the game-mode type it loads from the DB:

```
00052CE4  e8 47 c5 2c 00           call 0x31f230                   ; the DB query engine
00052CF3  89 86 78 01 00 00        mov  dword ptr [esi + 0x178], eax  ; mode type
00052CF0  83 f8 03                 cmp  eax, 3   / je 0x52d0d
00052CFB  83 f8 0a                 cmp  eax, 0xa / je 0x52d0d
00052D00  83 f8 0d                 cmp  eax, 0xd / je 0x52d0d
00052D05  88 9e 7e 01 00 00        mov  byte ptr [esi + 0x17e], bl  ; 0 -- cheats ON
00052D0D  c6 86 7e 01 00 00 01     mov  byte ptr [esi + 0x17e], 1   ; 1 -- cheats OFF
```

**The `ptrk` tendency cheats are already disabled in game-mode types 3, 10 and
13.** The hook map's hope of a free Phase-4 acceptance run stands, but the lever
is "enter one of those three modes" (or poke the byte), not "flip an options
toggle".

## Group ids — bounded, not named

The group argument is forwarded by the seam into `0x00203F00`, which binds it
into the `'IABP' where 'RGIA' = …` query — so group ids are `PBAI.AIGR` **column
values in the game database**, not code constants. Nothing in the image maps an
id to a label.

They *are* enumerated, though, in two byte arrays read by `0x002013B0` /
`0x00201240` / `0x002012D0` (selected by `byte [0x00533A98]`), read out of
`.data`:

```
0x00494314 (21): 03 0D 01 22 05 11 04 07 06 23 16 14 0A 08 15 02 20 21 24 25 26
0x0049432C (17): 05 16 01 06 12 13 07 00 14 15 02 0A 0B 0C 0D 17 18
```

All four unknown ids are members. `0x02`, `0x20`, `0x21` are **contiguous at
indices 15–17** of the 21-id table; `0x20`/`0x21` appear in that table only.
`0x0A` is in both. `0x00203F00`'s only other caller is itself (`0x002042A7`, a
retry inside its own body) — so **the seam is the only way into the selection
engine**, which is what makes a single hook point sufficient.

## Naming, as a by-product

Decoding the fourcc/size/global triple at all 69 `call 0x00256800` sites names
most of the cast, and settles two labels the hook map guessed at:

| global | fourcc | size | note |
|---|---|---|---|
| `0x0051DA6C` | `plbk` | `0x15FD4` | the playbook table (= 2 × `0xAFBC` + `0x5C`) |
| `0x00533090` | `scru` | `0x120` | the hook map's "situation object" |
| `0x00532BC8` | **`pcal`** | `0x58` | **the play-call object** — new |
| `0x00532BEC` | **`pstp`** | `0x154` | post-play / result, game state 4 — new |
| `0x00532BAC` | `gply` | `0x10` | the game-state machine (state id at `+0x00`) |
| `0x00532B48` | **`prac`** | `0x184` | the hook map's "settings object" is the **practice** object |
| `0x00532BD8` | `pctl` | `0x14` | player control |
| `0x00534D84` | `rand` | `0x18` | RNG |

Game-state table `0x00418800`, 16 × `{enter, mid, exit}`, driven by
`0x0006FC40`: 1 = `preg`, 2 = `prep`, 3 = `dply`, 4 = **`pstp`**,
5 = **`pcal`**, 6 = `qend`, 7 = `gend`, 9 = `ovrt`. That is what identifies
`0x00077060` as "the play-call screen opening" rather than by inference.

## Gating recommendation

**Do not gate on "is this special teams" — the question does not arise.** All six
call sites are the same code path: the AI choosing a play from a group in the
play-call flow, through the one selection engine, on the same `plbk`/`scru`
state. Project rule 1's scope test **passes** here: same topic *and* same path.

Concretely, a selector hooked at `0x001311C0` inherits:

| when | site | group |
|---|---|---|
| play-call screen opens (and on its timeout) | `0x000771C6` | `0x0A` |
| VM script command, dispatcher A cmd 8 | `0x001336C5` | script-chosen |
| VM script command, dispatcher B cmd 8 | `0x001338C0` | script-chosen |
| post-play result code 8 → mode 4 | `0x00076EBB` | `0x21` |
| VM cmd 12 → `pcal+0x1C = 2` → mode 2 | `0x00076E6B` | `2` |
| (no producer found) | `0x00076E92` | `0x20` |

Three practical rules for the hook:

1. **Mask bit 15 inside the hook** (`group & 0x7FFF`). Four of the six callers
   pass a raw constant that never had the flag; two sanitise it themselves. If
   the selector reads bit 15 as a flag it must not assume the caller cleared it,
   and must not treat "clear" as meaningful at the four literal sites.
2. **If the coach-brain should own only the primary pick, gate on the group id,
   not on the call site.** `0x0A` (screen-open) is the primary pick; `2` and
   `0x21` are situational re-picks driven by a post-play result. Gating on group
   is one compare; gating on return address is fragile.
3. **`0x00076E30` mode 1 takes no seam call at all** — it blits both teams'
   staged plays directly. A selector at the seam will never see it. If the
   coach-brain must own *every* play the AI ends up running, that arm is a
   second, separate hook site and belongs in its own requirement with its own
   acceptance test.

## What is not determined

- **The meaning of groups `2`, `0x0A`, `0x20`, `0x21`.** They are `AIGR` values
  resolved against the game DB at runtime; the XBE holds only the id bytes.
- **Which of the two id tables is offense vs defense** (`byte [0x00533A98]`
  selects; its writers were not traced).
- **What play-call script 0 decides** — the bytecode is an asset at
  `[0x51DA80][0]`, run by the interpreter `0x00133AC0`. So *when* `pcal+0x1C`
  becomes 1 vs 2 is not statically knowable.
- **Whether mode 3 (group `0x20`) is truly dead.** No producer in either sweep;
  a write through an aliased pointer passed as a parameter would be missed.
- **What `ebp` is at `0x000844EE`** — the post-play result code that selects mode
  0/4/1. Naming its values would name the situations that trigger a re-pick.

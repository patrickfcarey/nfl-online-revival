# T3 — scale the N-1 fold so moderate doubles WALK the man back

> # !! FINETUNE PLACEHOLDER — THIS IS A CALIBRATION SURFACE, NOT A FINAL VALUE !!
>
> **k = 0.8 is a first-pass value the operator judged "just about right" in
> live play on slot 9 (2026-08-12).** It sets the CENTER of the win/lose/drive
> spectrum; live-game jitter spreads outcomes around it. Before this is
> "done": sweep k across matchups and personnel, validate with the seed plan
> (`seed-testing-plan.md`), and tune the sibling surfaces (T1 shed cadence,
> T2 move variety, R11a/b losability). Retune = one word at `0x004F4BCC` +
> reboot. **Grep `FINETUNE PLACEHOLDER` across the repo for every such dial.**


Authored 2026-08-12, static/offline, against `extract/SLUS_207.52`
(SLUS-20752, CRC 14F8B841) and the deployed
`patches/14F8B841.n1-fold.pnach`. Every word below was round-tripped
through `recon/mipsdis.py` (FPU via `recon/fpudis.py`) this pass, the
cave census was re-run this pass, and the deployed pnach was re-read
line-by-line — nothing is quoted from memory of prior sessions.

**Requirement (operator, 2026-08-12, drive-lane 3 "OPERATOR VERDICT").**
N-1 pancakes the doubled defender every time because it folds the
helper's RAW W+STR — an enormous margin — so the extreme outcome always
fires. T3 multiplies the fold's three contributions by a single scale
factor `k` (ship k = 0.5) so the margin lands in the sustained-drive
band (grid cells 50/53/54: walking him backward), reserving the pancake
for lopsided matchups. `k` is ONE f32 data word; retuning is a one-line
pnach edit. k = 1.0 degenerates to bit-exact N-1.

---

## 1. Implementation choice, and why

Three candidate shapes were weighed against the two hard constraints
(do not move the epilogue `0x004F4B9C` — six gate branches target it —
and minimal pnach diff over the live N-1 file):

1. **Insert `lui/mtc1` + three `mul.s` in line** — moves the epilogue
   20 bytes; all six branch offsets need re-encoding and every word
   from the insertion point down shifts: ~30 changed lines. Rejected.
2. **Scale the three sums f5/f8/f10** — f5 is reused as an input to
   f10 (`add.s f10, f5, f4` at `0x004F4B80`), so scaling f5 in place
   double-scales comp3 unless the sum block is restructured: 4 changed
   + 11 added lines, and the tail logic changes shape. Rejected.
3. **CHOSEN: scale the five INPUT terms** (f1 = W, f2 = STR,
   f7 = STR/2, f3 = AGI, f4 = AWR) once, immediately after the
   int→float conversions and before any sum. Every downstream sum and
   store is then correct *unchanged*: f5 = k·W + k·STR,
   f8 = k·AGI + k·(STR/2), f10 = k·(W+STR+AWR+AGI). One existing word
   becomes a `j` into the cave's own free tail (censused-dead space at
   `0x004F4BA8`); the extension does the k-load, five `mul.s`, and the
   displaced `add.s f5`, then jumps back. **The epilogue and every
   instruction before/after the fold keep their addresses; all six
   gate-branch words are byte-identical.** Diff = 1 changed word +
   10 added words.

**Scaling inputs ≡ scaling contributions.** For k = 0.5 (any power of
two): multiplying an f32 by 0.5 is exact (exponent decrement), and
truncation/rounding of a mantissa commutes with exact power-of-two
scaling, so `k·W + k·STR` is bit-identical to `k·(W+STR)` at every
add — the shipped k = 0.5 contribution is exactly half the N-1
contribution, and **k = 1.0 (`0x3F800000`) reproduces N-1's comps
bit-exactly** (×1.0 is exact), which is the built-in regression
setting. For non-power-of-two k in a sweep, the difference from
k·(sum) is ≤ 1 ulp per add — orders of magnitude below the tuning
granularity.

**k as a data word, not a `lui` immediate.** The compiler's own
constant idiom `lui at, 0x4334 / mtc1 at, f0` exists at
`0x001F109C-0x001F10A0` and would cost one word less, but it puts k in
an instruction's hi-half (8-bit-ish granularity, and retuning means
editing code). Instead k lives as a raw f32 word at `0x004F4BCC`,
loaded with `lui at, 0x004F / lwc1 f0, 0x4BCC(at)` — full f32
precision, and the retune line is literally `word,3F000000`. (Note:
the file uses `patch=1`, re-applied every vsync — required because the
harness's `load_state` restores pre-patch memory — so the pnach line,
not a live poke, is the single source of truth for k. A PINE/debugger
poke to `0x004F4BCC` would be overwritten at the next vsync.)

### FPU / integer register audit for the extension

* **f0** — the only new register T3 touches. Dead across the site:
  n1-cave.md §1 (re-derived there this session) proved f0–f21 all dead
  at `0x001F153C` and the host redefines f0–f4 before any use after
  the call. The N-1 cave itself used only f1–f11; f0 was in its
  "never touched" set and is hereby claimed. The extension runs after
  both nested `jal`s, so no callee sees or needs f0.
* **f1, f2, f3, f4, f7** — scaled in place. Audited every read after
  `0x004F4B60` in the deployed listing: f1/f2 feed only f5; f3/f7 feed
  only f8 and f10; f4 feeds only f10. All consumers WANT the scaled
  value. Nothing after `0x004F4B90` reads any of them; the host
  redefines f1–f4 before use (n1-cave §1).
* **f6** — loaded in the outbound `j`'s delay slot (comp1), NOT
  touched by the extension.
* **at** — clobbered by the `lui`; already in the cave's written set
  (`addiu at, zero, 2` at `0x004F4AC4`), dead here, host does not
  read it.
* Stack, s-registers, gates, canaries, epilogue: untouched. The
  extension is reached ONLY from the post-gate fold path, so every
  gate-failure path (slots 6/7, recordless pairs) executes zero new
  instructions.

---

## 2. Full annotated listing (final T3 layout, whole cave)

Produced by applying the deployed pnach plus the T3 diff to the image
and disassembling `0x004F4AA0..0x004F4BCC` with `recon.mipsdis` — the
listing below is the disassembler's output of the final byte layout,
annotated. Unmarked words are byte-identical to deployed N-1.

```
; entry: redirected jal from 0x001F153C (unchanged).
004F4AA0  27BDFFF0  addiu sp, sp, -16
004F4AA4  FFBF0000  sd    ra, 0(sp)
004F4AA8  0200202D  daddu a0, s0, zero
004F4AAC  0C07C310  jal   0x001f0c40        ; displaced contest stamp
004F4AB0  0280282D  daddu a1, s4, zero      ; (ds)
004F4AB4  3C080051  lui   t0, 0x0051
004F4AB8  24090001  addiu t1, zero, 1
004F4ABC  AD09497C  sw    t1, 0x497C(t0)    ; CANARY A
004F4AC0  92880437  lbu   t0, 0x437(s4)
004F4AC4  24010002  addiu at, zero, 2
004F4AC8  15010034  bne   t0, at, 0x004F4B9C    ; gate 1 -> exit
004F4ACC  92890436  lbu   t1, 0x436(s4)     ; (ds)
004F4AD0  8F8ABB90  lw    t2, -17520(gp)    ; [0x00601280]
004F4AD4  00095880  sll   t3, t1, 2
004F4AD8  00096100  sll   t4, t1, 4
004F4ADC  016C5821  addu  t3, t3, t4
004F4AE0  014B5021  addu  t2, t2, t3
004F4AE4  914D0014  lbu   t5, 0x14(t2)
004F4AE8  11A0002C  beq   t5, zero, 0x004F4B9C  ; gate 2 -> exit
004F4AEC  8E8E0000  lw    t6, 0(s4)         ; (ds)
004F4AF0  8D4F000C  lw    t7, 0x0C(t2)
004F4AF4  15CF0029  bne   t6, t7, 0x004F4B9C    ; insurance A -> exit
004F4AF8  8E0E0000  lw    t6, 0(s0)         ; (ds)
004F4AFC  8D4F0004  lw    t7, 0x04(t2)
004F4B00  15CF0026  bne   t6, t7, 0x004F4B9C    ; insurance B -> exit
004F4B04  00000000  nop                     ; (ds)
004F4B08  0C04EDE6  jal   0x0013b798        ; resolve helper handle
004F4B0C  25440008  addiu a0, t2, 8         ; (ds)
004F4B10  10400022  beq   v0, zero, 0x004F4B9C  ; resolve fail -> exit
004F4B14  00000000  nop                     ; (ds)
004F4B18  8C5803E0  lw    t8, 0x3E0(v0)
004F4B1C  2718FFF9  addiu t8, t8, -7
004F4B20  2F180002  sltiu t8, t8, 2
004F4B24  1300001D  beq   t8, zero, 0x004F4B9C  ; gate 3 -> exit
004F4B28  00000000  nop                     ; (ds)
; ---- fold inputs (unchanged) ----
004F4B2C  C4410AEC  lwc1  f1, 0xAEC(v0)     ; f1 = W_h
004F4B30  94580B8E  lhu   t8, 0xB8E(v0)     ; STR_h
004F4B34  94590B72  lhu   t9, 0xB72(v0)     ; AGI_h
004F4B38  94480B74  lhu   t0, 0xB74(v0)     ; AWR_h
004F4B3C  44981000  mtc1  t8, f2
004F4B40  468010A0  cvt.s.w f2, f2          ; f2 = STR_h
004F4B44  0018C042  srl   t8, t8, 1
004F4B48  44983800  mtc1  t8, f7
004F4B4C  468039E0  cvt.s.w f7, f7          ; f7 = floor(STR_h/2)
004F4B50  44991800  mtc1  t9, f3
004F4B54  468018E0  cvt.s.w f3, f3          ; f3 = AGI_h
004F4B58  44882000  mtc1  t0, f4
004F4B5C  46802120  cvt.s.w f4, f4          ; f4 = AWR_h
; ---- T3: detour to the scale block (THE ONE CHANGED WORD) ----
004F4B60  0813D2EA  j     0x004F4BA8        ; WAS 46020940 add.s f5,f1,f2
004F4B64  C6460010  lwc1  f6, 16(s2)        ; (ds) comp1 load -- unchanged,
                                            ;   f6 untouched by the extension
; ---- accumulate (unchanged; inputs arrive pre-scaled) ----
004F4B68  46053180  add.s f6, f6, f5        ; <- extension returns HERE
004F4B6C  E6460010  swc1  f6, 16(s2)        ; comp1 += k*(W+STR)
004F4B70  46071A00  add.s f8, f3, f7        ; f8 = k*AGI + k*(STR/2)
004F4B74  C6490014  lwc1  f9, 20(s2)
004F4B78  46084A40  add.s f9, f9, f8
004F4B7C  E6490014  swc1  f9, 20(s2)        ; comp2 += k*(AGI+STR/2)
004F4B80  46042A80  add.s f10, f5, f4       ; k*(W+STR) + k*AWR
004F4B84  46035280  add.s f10, f10, f3      ; ... + k*AGI
004F4B88  C64B0018  lwc1  f11, 24(s2)
004F4B8C  460A5AC0  add.s f11, f11, f10
004F4B90  E64B0018  swc1  f11, 24(s2)       ; comp3 += k*(W+STR+AWR+AGI)
004F4B94  3C090051  lui   t1, 0x0051
004F4B98  AD224978  sw    v0, 0x4978(t1)    ; CANARY B
004F4B9C  DFBF0000  ld    ra, 0(sp)         ; "exit:" -- ADDRESS UNMOVED
004F4BA0  03E00008  jr    ra
004F4BA4  27BD0010  addiu sp, sp, 16        ; (ds)
; ---- T3 EXTENSION (10 new words in the cave's censused-dead tail) ----
004F4BA8  3C01004F  lui   at, 0x004F
004F4BAC  C4204BCC  lwc1  f0, 0x4BCC(at)    ; f0 = K  (data word below;
                                            ;   0x4BCC < 0x8000, no sign-ext)
004F4BB0  46000842  mul.s f1, f1, f0        ; k*W
004F4BB4  46001082  mul.s f2, f2, f0        ; k*STR
004F4BB8  460039C2  mul.s f7, f7, f0        ; k*(STR/2)
004F4BBC  460018C2  mul.s f3, f3, f0        ; k*AGI
004F4BC0  46002102  mul.s f4, f4, f0        ; k*AWR
004F4BC4  0813D2DA  j     0x004F4B68        ; back to the accumulate block
004F4BC8  46020940  add.s f5, f1, f2        ; (ds) displaced: f5 = k*(W+STR)
004F4BCC  3F000000  K: .float 0.5           ; THE TUNING WORD (never executed;
                                            ;   decodes as `lui zero,0` = nop
                                            ;   even if it ever were)
```

Cave usage: 264 B deployed + 40 B extension = 304 B; free tail shrinks
to 304 B at `0x004F4BD0`.

## 3. Branch/jump verification — every transfer, recomputed from the final layout

**No instruction between any gate branch and the epilogue moved** (the
change at `0x004F4B60` replaces content in place; all additions sit
past `0x004F4BA4`), so the six deployed branch WORDS are byte-identical
and were re-verified arithmetically from the final layout,
offset = (target − (branch+4)) / 4:

| branch | addr | word | offset | branch+4 + 4·offset |
|---|---|---|---|---|
| `bne t0,at`  | 004F4AC8 | 15010034 | 0x34 (52) | 004F4ACC + 0x0D0 = **004F4B9C** ✓ |
| `beq t5,zero`| 004F4AE8 | 11A0002C | 0x2C (44) | 004F4AEC + 0x0B0 = **004F4B9C** ✓ |
| `bne t6,t7`  | 004F4AF4 | 15CF0029 | 0x29 (41) | 004F4AF8 + 0x0A4 = **004F4B9C** ✓ |
| `bne t6,t7`  | 004F4B00 | 15CF0026 | 0x26 (38) | 004F4B04 + 0x098 = **004F4B9C** ✓ |
| `beq v0,zero`| 004F4B10 | 10400022 | 0x22 (34) | 004F4B14 + 0x088 = **004F4B9C** ✓ |
| `beq t8,zero`| 004F4B24 | 1300001D | 0x1D (29) | 004F4B28 + 0x074 = **004F4B9C** ✓ |

The disassembler independently printed `0x004f4b9c` as the target of
all six in the final-layout dump (§2 was generated from it). New
transfers, J-type encoding `0x08000000 | (target >> 2)`:

* `j 0x004F4BA8` = `0x08000000 | 0x0013D2EA` = **0x0813D2EA** ✓
  (round-trip: "j 0x004f4ba8"); delay slot = the unchanged
  `lwc1 f6, 16(s2)`.
* `j 0x004F4B68` = `0x08000000 | 0x0013D2DA` = **0x0813D2DA** ✓
  (round-trip: "j 0x004f4b68"); delay slot = the displaced
  `add.s f5, f1, f2` (byte-identical to the word it displaced).
* Both targets are in the same 256 MB region as their `j` (high nibble
  0), so the `(vaddr+4) & 0xF0000000` term is 0 on both. ✓
* The two deployed `jal`s (`0x0C07C310`, `0x0C04EDE6`) and the site
  word `0x0C13D2A8` are untouched.

Execution order on the fold path:
`...cvt.s.w f4` → `j`+`lwc1 f6`(ds) → `lui/lwc1 f0` → 5× `mul.s` →
`j`+`add.s f5`(ds) → `add.s f6,f6,f5` at `0x004F4B68` → unchanged to
the epilogue. `lwc1 f0` → `mul.s ..,f0` back-to-back is safe: COP1
loads have no delay slot on the R5900 (hardware-interlocked, same
guarantee the deployed `mtc1 → cvt.s.w` idiom already relies on).

## 4. THE MINIMAL PNACH DIFF (apply over `patches/14F8B841.n1-fold.pnach`)

**Changed: exactly ONE line.** (Old word = deployed N-1 line 92.)

| addr | old word | new word | meaning |
|---|---|---|---|
| 004F4B60 | 46020940 (`add.s f5,f1,f2`) | 0813D2EA (`j 0x004F4BA8`) | detour to scale block |

**Added: exactly TEN lines** (none of these addresses appears in ANY
pnach in `patches/` — grepped this pass; the "old" column is the
censused-dead function bytes the ELF has there, listed for census
honesty — they are unreachable, per §5):

| addr | old (dead ELF) | new | meaning |
|---|---|---|---|
| 004F4BA8 | 27BDFFA0 | 3C01004F | `lui at, 0x004F` |
| 004F4BAC | FFB40040 | C4204BCC | `lwc1 f0, 0x4BCC(at)` — load K |
| 004F4BB0 | FFB30030 | 46000842 | `mul.s f1, f1, f0` |
| 004F4BB4 | 0080A02D | 46001082 | `mul.s f2, f2, f0` |
| 004F4BB8 | FFB20020 | 460039C2 | `mul.s f7, f7, f0` |
| 004F4BBC | 0000982D | 460018C2 | `mul.s f3, f3, f0` |
| 004F4BC0 | FFB10010 | 46002102 | `mul.s f4, f4, f0` |
| 004F4BC4 | 24B2FFFF | 0813D2DA | `j 0x004F4B68` |
| 004F4BC8 | FFB00000 | 46020940 | (ds) `add.s f5, f1, f2` |
| 004F4BCC | 00C0882D | 3F000000 | **K = 0.5f — THE TUNING WORD** |

### Ready-to-paste pnach lines

```
// T3 -- scale the N-1 helper fold by K (walk-back band instead of
// auto-pancake). ONE changed word (004F4B60: add.s -> j ext) + ten added
// (scale block + K in cave #4's censused-dead tail, re-censused 2026-08-12).
// K at 004F4BCC, f32: 0.5=3F000000. K=3F800000 (1.0) == bit-exact N-1.
// Epilogue 004F4B9C unmoved; all six gate branch words byte-identical.
patch=1,EE,004F4B60,word,0813D2EA
patch=1,EE,004F4BA8,word,3C01004F
patch=1,EE,004F4BAC,word,C4204BCC
patch=1,EE,004F4BB0,word,46000842
patch=1,EE,004F4BB4,word,46001082
patch=1,EE,004F4BB8,word,460039C2
patch=1,EE,004F4BBC,word,460018C2
patch=1,EE,004F4BC0,word,46002102
patch=1,EE,004F4BC4,word,0813D2DA
patch=1,EE,004F4BC8,word,46020940
patch=1,EE,004F4BCC,word,3F000000
```

Coordinator note: the `004F4B60` line REPLACES the existing
`patch=1,EE,004F4B60,word,46020940` (n1-fold.pnach line 92) — do not
leave both; with `patch=1` both would execute each vsync and last-wins
would mask the duplication rather than surface it. The ten added lines
are new addresses. Everything else in the deployed file (site hook,
P11 word `001F21E8`, instrumentation block at `0x00514920`, canaries)
is untouched by this diff.

## 5. Verification record (all re-derived this pass)

* **Census.** `python3 -m recon.cave_census extract/SLUS_207.52
  0x004F4AA0:608` → jal/j/branch/formed/word ALL clean, entry cannot
  fall through: the full 608 B region `0x004F4AA0..0x004F4D00` —
  which contains the entire extension and K — is DEAD. (The tail holds
  a dead function's bytes, not zeros; that is what the census verdict
  licenses overwriting. Runtime-liveness caveat inherited, §7.)
* **Pnach cross-check.** Deployed `14F8B841.n1-fold.pnach` read in
  full (91 lines): its cave section matches n1-cave.md §7
  word-for-word; only it touches `0x004F4B60`; NO pnach in `patches/`
  writes any of `0x004F4BA8..0x004F4BCC`.
* **Round-trip.** Every changed/added word disassembled back to the
  intended mnemonic by `recon.mipsdis`/`fpudis` (listing in §2 is that
  output). K's word `0x3F000000` decodes as `lui zero, 0` — a
  write-to-zero no-op even in the impossible case of execution.
* **Compiler-emitted encoding proof, `mul.s`:** the task's reference
  site `0x001F10B0` = `4601A502` `mul.s f20, f20, f1` confirms the
  field layout `0x46000002 | ft<<16 | fs<<11 | fd<<6`; stronger, ALL
  FIVE T3 `mul.s` words occur verbatim as compiler output in the image
  (0x46000842 ×135, 0x46001082 ×45, 0x460039C2 ×2, 0x460018C2 ×35,
  0x46002102 ×14 occurrences), e.g. `0x0030FF30-40` where the compiler
  scales f5/f7/f6 by f0 in place — the exact T3 pattern.
* **`lwc1` encoding:** compiler words `C780xxxx` (`lwc1 f0, off(gp)`,
  e.g. `0x00100918`) fix the opcode/ft fields; T3's `C4204BCC` differs
  only in rs (at=1 vs gp=28): `0x31<<26 | 1<<21 | 0<<16 | 0x4BCC`. The
  `lui`-constant idiom precedent for the alternative form is at
  `0x001F109C` (`3C014334` `lui at, 0x4334` feeding `mtc1 at, f0`) —
  same `at`-as-FPU-constant-scratch convention T3 follows.

## 6. K — the tuning word, and the sweep

**K lives at `0x004F4BCC`, one f32 word, read fresh on every fold.**
Retune = edit that single pnach line, reboot (the harness's normal
per-config cycle; `patch=1` re-applies each vsync, so the line always
wins — including over `load_state`, which is why a live poke is NOT
the sweep mechanism). Precomputed values:

| k | word | expectation (pre-registered, Hypothesis-grade) |
|---|---|---|
| 1.00 | 3F800000 | bit-exact N-1: dy ≈ +2.09, clips {56} — the regression setting |
| 0.75 | 3F400000 | drive with frequent pancakes |
| 0.60 | 3F19999A | drive band, occasional pancake |
| **0.50** | **3F000000** | **SHIP: walk-back cells 50/53/54, pancake only on lopsided pairs** |
| 0.40 | 3ECCCCCD | weaker walk; watch for handoff returning |
| 0.30 | 3E99999A | near the estimated col-1 floor (margin ≈ 0.193 vs 0.175) |
| 0.25 | 3E800000 | expected REVERT to P11 handoff (margin ≈ 0.166 < 0.175) — the falsification arm |

One-session sweep protocol: keep the rest of the patch set frozen;
for each k, edit the one line, boot, run slot 9 ×3 (determinism rule),
record DE_dy, pair window, clips 5/6, carrier_yards, canaries A/B.
Bracket first (0.25 / 0.5 / 1.0 — revert / target / pancake), then
bisect inside whichever interval brackets the walk-back look. The
operator's eyes pick the final k; the numbers only bound the band.

Why these numbers (mechanism, from lanes 1/3): the fold's consumers
are all margin machines on the comps — the 6×5 grid's col-1 needs
blocker `+0x414` > defender's AND staged `+0x404` > 0.175 run
[0x005ff134] to cycle the drive cells {50,53,54}; the pancake-pool
converters roll `rand(150) < margin%` on the `+0x41C` margin (149 even
/ 168 odd, and 56/149/168 is the engine's own pancake predicate). One
k scales every one of those margins together: lane 3's estimate
(stock comp1 ≈ 745, raw fold ≈ +595) gives margin ≈ 595k/(745+595k) —
0.444 at k=1 (pancake rolls dominant), 0.285 at k=0.5 (drive threshold
cleared, pancake roll odds roughly halved), floor at k ≈ 0.27 where
col-1 fails and the P11 handoff returns. The 745/595 inputs are
lane-3 estimates, so the floor is a Hypothesis to be measured, not a
derivation. Per-play variety around the centre comes free: the stock
comp terms carry the engine's ×[1.00,1.33) jitter and the converters
roll dice; only the fold contribution is deterministic.

## 7. Oracle (pre-registered acceptance, k = 0.5)

**Must change** (slot 9, ×3): DE_dy stays POSITIVE but lands below
N-1's +2.09; clips 5/6 shift away from pure {56} toward the walk-back
family — accept if the set intersects {50, 53, 54} (grid drive cells)
or the engage-in-motion starts {148, 150, 151}, with 56/149/168
allowed as a minority, not the totality; pair window remains ≥ the
N-1 range (2..92+); canary A = 1, canary B = helper base (the gate
chain is untouched, so both must behave exactly as under N-1).

**Must NOT change**: carrier_yards unchanged-or-better vs N-1's runs;
slot 6 (lead blocker) and slot 7 (pass pro, post-P11 re-baselined
card) byte-identical behaviour — they reach the cave only to fail
gate 1 and never execute one new instruction; defender-side comps
untouched; canary B stays 0 on recordless plays. Failure modes and
their reads: dy ≤ 0 or clips {147,161} = k below the band (raise k);
clips still all-{56} with dy ≈ +2 = k too high or the band is narrower
than estimated (lower k); anything moved on slots 6/7 = NOT a tuning
result — pull the patch, the diff has a fault, since those paths are
provably instruction-identical.

Per project rule 2: T3 is tested alone over the live N-1 file first
(this diff, k=0.5, own savestate runs), then integrated with the full
set plus `tests/test_madden_lab_*.py`. The k=1.0 setting doubles as
the "patch present, behaviour identical to N-1" control if the
coordinator wants a positive control before sweeping.

## 8. What I could not establish (static session limits)

1. **The grid's numeric band edges.** Lane 1 maps WHICH cells exist
   and their gate ratios (col-1 needs `+0x414` margin AND `+0x404` >
   0.175/0.06); it does NOT give a closed-form margin→cell→clip map,
   and the selector that started the measured {56} (late contact
   starter vs converter pool) is not pinned. The k that CENTRES the
   walk-back band is therefore an empirical sweep target, not a
   derivation — §6's floor estimate rests on lane 3's 745/595
   estimates.
2. **Whether BreakBlockContest's scores read the folded comps** (it
   receives both `+0x404` blocks as arguments; the carried summary
   says ratings). If it does, k also scales the shed-contest odds —
   direction is the same (smaller k = defender sheds more), so the
   sweep covers it, but the coupling is unmeasured.
3. **Runtime liveness of the extension bytes** — same caveat as N-1:
   the census is static; the execute-breakpoint liveness test on first
   deploy still applies (now to `0x004F4BA8..0x004F4BCC`). The dead
   words being a function prologue, not zeros, makes the static
   verdict no weaker, but it is still static.
4. **EE FPU rounding mode interaction** — the bit-exactness argument
   (§1) holds for power-of-two k under any mantissa-truncating or
   nearest rounding; for swept non-power-of-two k the ≤1-ulp claim is
   from arithmetic reasoning, not measured on the R5900's non-IEEE
   FPU. Irrelevant at tuning granularity; noted for honesty.
5. **Whether k needs to differ per comp** (e.g. keep comp3's pancake
   pool scaled harder than comp1's drive gate). One k scales all
   three; if the sweep cannot find a k that both walks and reserves
   pancakes, the next lever is three constants (three more data words
   and three lwc1s in the same tail) — designed but deliberately not
   shipped: one variable at a time.

## SWEEP RESULT k=0.5 (2026-08-12): the knob is the whole spectrum

Deployed and run on slot 9 (3/3 identical). Against N-1's k=1.0:

    k=1.0  DE_dy +2.09  clips {56}       PANCAKE (blocker dominates)
    k=0.5  DE_dy -1.15  clips {147,61}   DEFENDER WINS (double team LOST)

The response is a STEP, not a ramp: half the fold drops the margin below the
blocker-win grid-cell boundary and the defender wins outright (near the P11-only
-1.41). So on the frozen savestate, k selects a point on lose<->pancake with a
narrow walk-back band at the boundary; the walk-back center sits between 0.5
and 1.0.

**This makes k the single knob for BOTH T3 (outcome variety) and R11a
(losable).** In LIVE play the contest jitter spreads outcomes around the
k-centered margin (which is why the operator saw variety on repeated snaps even
though the harness replay is deterministic). So k sets the CENTER of the live
mix, not one outcome. Pre-flight lesson also banked: after an emulator restart
the runner-class pad must be hotplug-bound -- fire ONE snap and check
frames_since_snap before a real run (a full trial recorded 3 empty iterations
when input never reached the game; the operator caught it: "no play was ever
run").

Recommended next: k around 0.80-0.85 (biases live play to mostly-wins, pancakes
on mismatches, occasional losses to elite defenders), FELT in live play rather
than harness replay -- the replay only shows the center clip, the operator's
hands show the spread. k=0.8 = 0x3F4CCCCD, k=0.85 = 0x3F59999A.

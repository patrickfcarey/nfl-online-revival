# Default-slider behaviour uplift — verified tuning lever catalog

Answers open question #7 (`open-investigations.md`): make Madden NFL 2004
fire the animations and behaviours you can normally only reach with
extreme slider settings — contested catches, disciplined-but-fast
coverage — at **default** slider settings, by moving the *base* constants
so the sliders stay meaningful around the new baseline.

Every gameplay slider is `x' = x·(1 + S·0.02·(v−50))`, so slider 50 is
exact identity: moving a base moves the default behaviour and leaves the
transform untouched. Levers below were verified against `SLUS_207.52` by
reading the raw ELF bytes at each `file_offset = vaddr − 0xFF000` and
re-decoding every proposed word. This is a catalog of levers, not a
finished tune; enable one thing at a time.

The draft pnach fragment (section 4) is ready to paste into
`patches/14F8B841.pnach` — every line is commented out.

## Two doc corrections this lane produced

* Knockdown transform operand: the `0.75` is a **code literal**
  (`lui at, 0x3f40` at `0x00144894`); the `0.02` is pool word
  `[0x005FDADC]` (`gp−31764`). Earlier text had the gp offset wrong.
* The five coverage break-off blocks are **not all** byte-identical:
  state 40 rolls at **AWR/2** (`0x001ec724`–`0x001ec730`), half of states
  22/37/38/39 — it is the already-disciplined zone. Any symmetric retune
  must account for that. (Folded into `sdchargersfanboy.md`.)

## The two big levers, in plain terms

1. **Contest the ball at default.** The swat/knockdown is the only roll
   that puts a thrown ball on the ground; it runs per frame while the ball
   is in flight for any defender passing four geometry gates in
   `0x0019b338`, with **no pass-depth term** anywhere. Because the roll
   *repeats every eligible frame* (a 50% chance already compounds to ~88%
   by the third frame), **widening the gates buys more contested-ball
   animations — the stated goal — while raising the base mostly buys more
   incompletions.** Widen first (A4/A5/A7/A8), move the base second and
   modestly (A1, 50→60; 60 is the largest base that keeps the whole slider
   live).
2. **Disciplined-but-fast coverage.** Every coverage state runs "abandon
   my assignment and chase the ball?" as `rand(0,255) < AWR` (`P =
   AWR/255`) on a countdown that shrinks as AWR rises. Reaction *speed*
   and break-off *probability* are separate instructions, so they can be
   tuned separately: lower the probability (B-i, change the RNG
   denominator from 255 to 384/511 at all five states) to keep safeties
   in their zone, while leaving the cadence alone so they still react
   fast. This is the decoupling the question asks for — position fixed by
   B-i, play-on-the-ball fixed by the A levers, and nothing in the swat
   chain reads AWR, so the two halves cannot fight.

## Lever catalog

`DS` = delay-slot. Code = instruction word; data = float in an initialised
pool (patched the same way — all addresses are inside the loaded image).

### A — Contest-the-ball (knockdown/swat), function `0x0019b338`

| id | vaddr | kind | current | meaning | example patch |
|---|---|---|---|---|---|
| A1 | `0019BD7C` | code | `24050032` `addiu a1,0,50` | base chance @ slider 50 | `2405003C` (60); DS of plain jal, `0x00144838` has 1 caller (knockdown-only) |
| A2 | `00144894` | code | `3c013f40` (0.75) | slider steepness S | `3C013F00` (0.5) |
| A3 | `005FDADC` | data | `3CA3D70A` (0.02) | second steepness factor | redundant with A2 — pick one |
| A4 | `0019BD5C` | code | `3c0140c0` (6.0) | distance-to-catch gate `d<6.0` | `3C0140F0` (7.5); do NOT patch `0x004ad760` itself (100+ callers) |
| A5 | `0019BC3C` | code | `3c0140e0` (7.0) | height upper bound | `3C014100` (8.0) |
| A6 | `0019BC54` | code | `3c013fc0` (1.5) | height lower bound | `3C013F80` (1.0); feeds a branch-likely — change the operand, not the branch |
| A7 | `0019BD18`+`1C` | code | →`0x0078E38E` (170°) | gate: ball must arrive within 170° head-on | lower widens (150° = `3C03006A`/`3463AAAA`) |
| A8 | `0019BD40`+`44` | code | →`0x003FFFFF` (90°) | gate: defender-facing vs ball ≤90° | raise widens (120° = `3C030055`/`34635555`) |
| A9 | `0019BD8C` | code | `24050064` `addiu a1,0,100` | RNG denominator (`P=chance/n`) | `24050050` (80); DS of plain jal; **bypasses the 0–100 clamp — use A1 *or* A9, not both** |

Knockdown base table (real float32, round-toward-zero, at slider 0/50/100):
base 50 → 13/50/87 (stock); base 60 → 15/60/100 (slider dead only above 95);
base 65 → 17/65/100 (dead above 86); base 75 → 19/75/100 (dead above 73).

### B — Coverage discipline vs cadence (five states: 22 man, 37/38/39/40 zone)

**B-i — break-off probability denominator** (`P = AWR′/n`), all five currently
`240500FF` (`addiu a1,0,255`), all DS of plain `jal 0x002f9428`:

| state | vaddr | | state | vaddr |
|---|---|---|---|---|
| 22 | `001BE964` | | 39 | `001EA398` |
| 37 | `001ED9D0` | | 40 | `001EC788` |
| 38 | `001EEB1C` | | | |

`n=384` → `P ×= 0.664`; `n=511` → `P ×= 0.499`. Patch **all five or none**
(inconsistent defense otherwise). The `situation==6` fast path bypasses the
roll entirely, so obvious reads stay instant regardless of `n`.

**B-ii — CPU-only `ptrk` anti-repetition boost**, neutralise by nop'ing the
write-back (currently `3050FFFF` `andi s0,v0,0xffff`): states 22 `001BE944`,
37 `001ED9BC`, 38 `001EEB0C`, 39 `001EA384`, 40 `001EC768`. Nop the `andi`,
not the `addu` (the latter would slash AWR instead of neutralising). Gated on
no-human-on-side, so this is single-player-feel only; never fires in H2H.

**B-iii — cadence jitter** (`srl a1,·,5` → shift 6 halves the jitter): states
22 `001BE278`, 85 `001D2E3C`, 37 `001ED7FC`, 38 `001EE8F4`, 39 `001EA194`,
40 `001EC5A0`. Note: does *nothing* for a maxed-AWR defender (the floor is the
skill term). **B-iv — skill term** (the floor, `0x001535f8`, 5 callers, all
coverage): class 1/Pro = 6 (`00153620`), class 3/All-Madden = 2 (`00153638`,
in a branch-likely delay slot — flag), class 0 = 11 (`00153648`), class 2 = 4
(`0015364C`), and a +2-vs-CPU-offense term (`00153660`).

Rejected non-lever (evaluated as asked): the `lhu`→`lbu` trick on AWR is a
verified no-op (AWR ≤ 255, high byte always 0); the `+0xB75` variant is a hard
off, not a graded reduction. B-i's denominator is the only clean graded lever.

### C — Penalty realism, `ApplySlider 0x0025d2c8`, 10 call sites

Facemask base 2000.0 `0025E710` (per-10000 rate → 20% at slider 50); Holding
mult 300.0 `0025E658`; Clipping base 7500.0 `[0x005FFDDC]` (data); Roughing
Passer base 75.0 `0025E7D0`; Roughing Kicker base 75.0 ×2 `0025EF8C`/`0025F04C`.
Caps mostly 10000.0/100.0 in the pool. **C12 decoupler:** KR/PR catch
interference (idx 5) ships base=cap=100, inert above slider 50; changing
`mov.s f12,f0` (`46000306`) to `add.s f12,f0,f0` (`46000300`) at `0025EA54`/
`0025ECFC`/`0025EEBC` makes cap 200 so the top half of that slider comes alive.
`0x0025d948` (C13) has a runtime index — shared across penalty types, handle
with care.

### D — Slider steepness pool `0x005FDAB4`–`0x005FDAF8` (data, every word single-reader)

18 words, one per transform component; S values 0.5/0.35 (blocking), 0.75 (WR
catch, knockdown), 0.55 (INT), 0.70 (break block), 0.40 (tackle), 0.20 (kick
length), 0.45/0.1125 (rating rescaler). These change slider *steepness* around
any base and never move the value at slider 50. Use D to *recover* slider range
after raising a base (e.g. base 65 + S 0.75→0.55).

### E — `ptrk` recency weights (data)

`0x00540FE0` = {1/24, 1/48, 1/96, 1/192} repetition weights (single reader
`0x0024E124`); `0x00540FF0` = success weights. Halving E1 softens *all* CPU
adaptation consumers at once (broader than B-ii, which hits coverage only).

### F — Additional same-shape levers (verified)

Reaction timers `21−(AWR+TAK)/32` (`0019EF58`) and `31−(AWR+TAK)/32`
(`001CB988`); the four-way per-tick state-change split AWR/8·/4·/2·/1
(`001CB748`/`001CB78C`/`001CB7E8`); generic AI timer refill `rand(0,(255−AWR)/16)
+ (255−AWR)/32` (`001CB6D4`/`001CB6DC` — note `/16 + /32`, correcting the doc's
earlier `/32`).

## Interaction map

**Compose cleanly:** A-gates + A1-modest (more contest animations *and* better
odds); A-anything + B-i (the marquee "contested catches without warping" —
B-i fixes position, A fixes the play on the ball); B-i with cadence left alone
(the decoupling); B-ii + B-i on CPU defenses (variance killer + scale);
penalty bases (C) orthogonal to everything; D orthogonal by construction.

**Conflict / double-count:** A2 vs A3 (same product); A1 vs A9 (same
probability, A9 defeats the clamp); B-i and B-ii both aggressive on CPU
defenses reads as passive (prefer `n=384`+ptrk-on, or `n=511`+ptrk-off, not
both); B-iv vs B-iii (floor vs jitter — tune one); don't narrow the height
window below 6.0 (silently disables the catch-animation swat path).

## The one runtime unknown that picks the strategy

The swat roll repeats every eligible frame, and how many frames the 6-unit
window lasts on a deep ball decides whether the *gate* levers (A4–A8, more
bodies playing the ball) or the *base* lever (A1, higher odds per attempt)
is the better default-uplift knob. Static analysis can't supply `N`; it needs
a PINE/savestate measurement on the rig — watch the swat-eligibility gate in
`0x0019b338` across a deep pass and count frames where all four gates hold.

## Draft pnach fragment

The full commented fragment (widen-the-gates, knockdown base 50→60, the
five-state break-off retune, the optional CPU-ptrk-off block, and the
facemask/clipping penalty bases) is reproduced verbatim in the Lane M report
and is ready to append to `patches/14F8B841.pnach`. Every line is disabled;
enable one at a time and attribute the change before stacking the next.

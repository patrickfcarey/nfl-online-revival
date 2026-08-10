# Fact-check ledger, August 2026

Eight independent verification passes over every gameplay document in
`docs/` plus the online-protocol track, run 2026-08-08 → 2026-08-10
against `extract/SLUS_207.52`. Each checker re-derived every factual
claim from the binary rather than reading the docs' reasoning — separate
sweeps, separate scratch tooling, no access to each other's results.

**This pass was verification only. Nothing here has been applied.** The
docs still say what they said; this file records what survived.

## Bottom line

The architecture held up. Every headline census reproduced: the 131-entry
option array and its single universal transform, the double-team system,
the two-system pass/run split, no position in the tackle score, no tackler
in the fumble, no toughness consumers, no teammate-separation term in zone
bunching, and the `ptrk` module's closed input census. So did the
bit-exact work — the 13–87 knockdown span (checked in both rounding
modes), the 278-site rating census (byte-exact), every band edge, the
38-row and 25-row protocol tables (byte-for-byte), and the roster
checksum (**reproduced live**, `0x8108963c`).

What failed was narrower and sharper than expected: a cluster of
**negatives** — "nothing writes this", "nothing references that" — all of
which turn out to share one root cause.

## Tier 1 — findings that invalidate planned work

### 1. Code cave #1 is live code

`code-caves.md` ranks `0x00139A68` (456 bytes) first and builds its worked
pnach example on it. The function at `0x00139DA0` materialises all four
interior fragment starts, including the region start, and registers them
as callbacks:

```
00139df0  lui  t0, 0x6365 + ori 0x6c62   = the 'celb' tag
00139e00  addiu a2, zero, 292            = the object size
00139e2c  addiu t0, fp, -26008  -> 0x00139A68   the region START
00139e38  addiu a1, s7, -25952  -> 0x00139AA0
00139e3c  addiu a2, s6, -25560  -> 0x00139C28
00139e44  addiu a3, s5, -25776  -> 0x00139B50
```

`0x00139C28` disassembles to `jr ra; addiu v0, zero, 292` — the
size-getter for the object built two instructions earlier — and it
occupies the **final eight bytes of the claimed cave**. Reachable from a
`.data` descriptor in an FMV-tagged block.

**Ten of the eleven lines in the worked example would overwrite it.** The
patch logic and the site patch are correct; only the cave address is
wrong. `patches/14F8B841.pnach` is unaffected — its one active line is
the DNAS bypass.

**Cave #3 (`0x0045F598`) is live for the same reason** — two interior
addresses registered as a handler pair, in a cluster that also builds a
thread and a semaphore pair, reachable from boot.

Re-tested with a 4 KB pairing window and a misaligned pointer scan, caves
**#2, #4, #5, #6, #7 and #11 still pass**. #11 (`0x00514920`, the linker
gap between `.vutext` and `.data`) is the cleanest and is the obvious
rehost. The survey total drops from "9,248 bytes across 56 regions,
provably zero-reference" to at most 8,168, and *provably* no longer holds
for anything cleared the old way.

### 2. `player+0xB07` has a writer — the HB-vision headline collapses

`hb-vision-and-moves.md` says the byte "has no writer anywhere in the
executable" and that "the roster loader writes the bytes on either side of
it and skips it." Both halves are false, and the second is backwards:

```
0017d010  jal   0x001655b0        ; GetPlayer
0017d018  daddu s0, v0, zero      ; s0 = the player
0017d020  addiu s1, s0, 2792      ; = player+0xAE8
0017d028  daddu a2, s1, zero      ; passed as an ARGUMENT
0017d02c  jal   0x00169120        ; roster-record fill
                                  ; ...where fp = that base:
0016949c  sb t1, 29(fp)           ; player+0xB05
001694a0  sb v0, 30(fp)           ; player+0xB06
001694a4  sb t2, 31(fp)           ; player+0xB07
001694b0  sb t5, 33(fp)           ; player+0xB09
```

A second writer confirms it: the lineup block-copier at `0x0011d648`
copies 136 bytes across `player+0xAE8..0xB6F` for all 22 on-field players.

Consequences: **"halfbacks may have no vision at all, by accident" is
unsupported**; **fix V2 — billed as "the big one" — loses its rationale**,
since it existed to rescue style-0 halfbacks from a 0% gap-steer that was
never universal; and the PINE read remains worth doing as a *measurement*
of the value, not as a tiebreak on whether the byte is dead.
`robo-qb.md` was right that the byte is roster-sourced.

### 3. `+0x41C` is not a dead field — fix T7 rests on a false premise

`pass-rush.md`'s "computed and never read — a dead field, and a free hook
for a fix" has now been **refuted three times independently**. It is read
by a live margin contest at `0x001f1720` (both players' values, feeding
`(winner−loser)/winner × 100` into a `rand(0,150)` gate), by the
late-phase rep behind `sltiu ...,46` at `0x001f02f4`, by the pancake roll
at `0x001f175c/68`, and by the Pass/Run Block slider's 0.35 term.
**Repurposing it would corrupt all four.**

### 4. Lead-blocker fix A3 cites the wrong instruction

The ×2 velocity lead is at `0x001b627c` (x) and **`0x001b6290`** (y). The
doc cites `0x001b6294`, which is `add.s f2,f2,f0` — the add of the y-lead
*into* the position. Patching the cited address **removes the y-lead
entirely** instead of reducing it.

### 5. The "Boise dive" mechanism may be on the wrong side of the ball

`ai-play-calling.md` attributes the CPU's play repetition to the class
renormalisation in `0x0024D1C8` (the 80/20 split with the 0.80 fallback).
The mechanism is confirmed exactly as documented — but the dispatcher
routes `side == [gamestate+64]` to a *different* weighting, `0x0024D070`,
and two independent lines of evidence say `[gs+64]` is the **possession**
side. If so, the renormalisation serves the **defence**, and the real
offence weighting applies only symmetric ±(coach−50)×0.01 nudges, which
cannot produce the observed behaviour. The two functions' coach-field
defaults differ (50 vs 80), consistent with offence-tendency-50 /
defence-tendency-80.

Not decidable statically. **Rig test: breakpoint both, watch which runs
for the CPU offence.** This also determines whether claim 16's census
sentence ("every `ptrk` read in the offence weighting is of the opponent")
is about the offence at all — though the "no self-anti-repetition"
conclusion survives either way.

## Tier 2 — refuted claims

| Doc | Claim | Finding |
|---|---|---|
| `ai-play-calling.md` | matchup memory adds **yards**; a 15-yd gain ≈ ×16 | Yards feed a *threshold test*; what reaches the weight is a flat **0.35**. A 15-yd memory moves the multiplier ~1.0 → ~1.35. |
| `play-tendency-ai.md` | the whole percentage table | Computed on the 0–100 scale; the in-memory array is **0–255**. Formula right, **every cell wrong** — a display-85 safety is 84.7% on Pro, not 33%. |
| `rating-thresholds.md` | a `>>6` quantiser row (4 bands, QB pocket cadence) | **Does not exist.** No rating is ever shifted by 6. |
| `sdchargersfanboy.md` | the ball-in-air branch is not AWR-gated | It is — every in-air path runs `0x00147208`, which rolls `rand(0,N) < AWR`. The doc's own "all AWR sites are monotone" census implies it. |
| `sdchargersfanboy.md` | "the only roll that puts a thrown ball on the ground" | The *slider* has one consumer; the swat *outcome* `0x001b2aa0` has eight callers. |
| `play-tendency-ai.md` | "none symmetric / never fires in human-vs-human" | `0x00147674` has **no controller gate** — it fires for either defence, online included. Contradicts the doc's own table row. |
| `robo-qb.md` | accuracy slider crosses zero at 0.375; below it the perfect chance rises | Guarded by `c.le.s 0.5, slider` + `bc1f`. **Below 0.5 nothing happens.** |
| `robo-qb.md` | 19 reads of `+0xB07` | Exactly **nine** byte readers. `hb-vision`'s count was right. |
| `punt-logic.md` | exactly three references to ±27.6667 exist | **Seven** such words; four referenced outside the solver. Fix E is still safe; the uniqueness proof is not. |
| `cpu-dt-animations.md` | `ptrk` block-shed boost up to **+94%** | Max **+46.9%** (`+(score/2)·f`, f ≤ 0.9375). `play-tendency-ai.md`'s ×1.47 is right. |
| ~~`tackle-contest.md`~~ | ~~weight levels "+19 DT / +11 CB"~~ | **THIS CORRECTION WAS ITSELF WRONG — WITHDRAWN 2026-08-10.** See below. |
| `catch-and-fumble.md` | 12× fumble-rate spread | **9.3×**. |
| `default-uplift-tuning.md` | "60 is the largest base that keeps the whole slider live" | **57.** Self-contradictory with the doc's own table one paragraph later. |
| `default-uplift-tuning.md` | section D lists 0.5 and 0.75 as pool words | Both are **code literals**; the 18-word pool is eleven copies of 0.02 plus {0.35, 0.55, 0.70, 0.40, 0.20, 0.45, 0.1125}. The doc's own corrections section already says this for 0.75. |
| `ea-protocol.md` | the words after `cdev` form a pointer table incl. `0x004e1e40`/`0x004e1da8` | Not at that boundary. Low stakes — the doc hedges it as a layout coincidence. |

### Withdrawn: the weight correction was backwards

Read against a live memory dump on 2026-08-10, `player+0xAEC` is an **f32
in real pounds**. The quarterback reads 226.0 (Brad Johnson weighed 226 lb),
linemen 299–322, a corner 185. Under "pounds − 160" those linemen would be
459–482 lb, which is nonsense.

**So `tackle-contest.md`'s original "+19 DT / +11 CB" was right**, and the
correction above was wrong: 305/16 = 19 and 190/16 = 11 only work in real
pounds. Pounds-minus-160 is the roster *database* column `PWGT`; the player
object in memory holds the decoded value. All 19 load sites are `lwc1`, and
one site adds `trunc(weight)` directly to 0–255 ratings.

A caution worth carrying: this error came from reasoning about a field's
encoding from the code that *writes the database* rather than from the
object the engine actually reads. Live memory settled in one read what two
static passes got wrong in opposite directions.

### A community premise that is also wrong: linebackers can jam

Not a doc error — `press-and-routes.md` reports this correctly — but it is
the single most actionable finding for the open feature requests.

The jam function `0x001a0f28` has exactly four callers. Man-coverage
**enter** has an explicit LB arm (`addiu v0,-13; sltiu v0,3` → positions
13/14/15) at probability **0.8**. Man-coverage **think** jams with **no
position check at all**. The shared eligibility helper `0x001b9360` gates
on play phase, downfield position, no-double-jam, ~125° facing and 3.0
lateral — and contains **no position, weight, height or size test**.

**Open question #20 needs rescoping, not a new capability.** Jam
initiation is a flat `RandFloat < p` with no rating input (p ∈ {1.0, 0.9,
0.8, 0.5, 0.3} by path); the press contest is
`65 + (50/255)·[(dSTR+dAGI)/2 − (rSTR+rAGI)/2]` — **strength and agility,
not awareness.**

## Tier 3 — imprecisions worth fixing

**Numbers and edges.** Knockdown base 60 at slider 0 is **16, not 15**
(two checkers disagreed; the R5900 COP1 always rounds toward zero, and
base 60 is the only row where the mode matters, because 60 × 0.75 = 45.0
is exact). Slider saturation begins **at** v = 95/86/73, not above. The
`165.75` family has **six** members, not four — the two the patch list
misses gate **agility** (the juke and spin gates), not strength, and
truncation puts the real edge at rating **66**. The strength clamp
constants are 100/200, giving ratings ≈ [39.2, 78.4]. Max raw QB threat is
**7.0**, not 8.5 — 8.5 is the radar *radius*, and a `min.s` cap sits in an
unflagged delay slot (the −9.0 conclusion survives a fortiori). The
endzone backwards-penalty tables hold 30/40/30 and 45/60/45 with one
extreme at **85/200/85** — no table has 55–60. Dive's range limit is
`4.0 − style term` = 2.5/4.0/3.25; 4.0 is the style-1 value only. The
`ptrk` weight table's "single reader" is a `lui` at `0x0024E120` and an
`addiu` at `0x0024E128`, not `0x0024E124`.

**Counts and censuses.** `rating-thresholds.md`'s four fate buckets sum to
**249, not 278** (the 210 + 68 = 278 headline itself reproduced
byte-exact). "25 difficulty modifier functions" → 27 sites in 26
functions. "346 read sites" of `+0xB70` is not reproducible; the floor is
277. "83 AWR sites" → 76 in literal form. `code-caves.md`'s DVP overlays
are **25 / 39.2 KB**, not 26 / ~64 KB. `qb-read.md`'s nine call sites are
correct, but the prose accounts for only seven.

**Addresses.** The FG aim solver is at **`0x0015B1F8`** (`0x0015B1F4` is
padding). `zone-bunching.md`'s `0x0016589C` is a nop pad, not part of
`FindNearestPlayer`; the live function begins `0x001658A0` — **three
checkers landed on this independently**. `slider-behavior.md` attributes
the 111–116 dead range to a branch-likely; it rides plain branches, and
the cited `bnel` belongs to the ≥126 SETT path. `pitch-play-runner.md`'s
`lui 0x3f80` is at `0x001dfd98`, not `0x001dfdc8` (the store). Cited as an
AWR consumer, `0x001dfcd8` does not hold up — no `+0xB74` read in the
function, its callers, or the surrounding logic.

**Semantics.** `robo-qb.md`'s "exit predicate returns true
unconditionally" — it is a shared no-op stub returning **0**; the
"no sack-specific code" conclusion stands. Its "1-in-86 dump-off" tests
`roll == 86`; the probability depends on an unestablished generator range.
A non-QB passer **bypasses the scramble type-byte gate entirely**.
`punt-logic.md`'s "a human punter can never reach this code" is
overstated — the Madden-card path reaches the solver before any controller
check, contradicting the doc's own card paragraph — and its formation gate
omits a second condition. `slider-behavior.md` still describes the block
sliders as scaling a "3-float blocker impulse"; they scale the three
contest score components.

**Patch-site nits.** NOPing the third 40/44/55 sibling at `0x001F222C`
stores the *band*, not 30 — that site needs `move v0,s4`. N6's
single-word patch yields **0.547**, not 0.55.

### One unresolved conflict between two docs

`hb-vision-and-moves.md` corrects `pitch-play-runner.md` to say the
carrier levers are **not** AI-only, because the dispatcher falls through
to AI-think. FC8 confirms the fall-through **but** finds state-1 AI-think
self-gates at `0x001dfeec` (`beq s1, 0x00200040()` — "am I the
user-controlled player"), so the levers are effectively CPU-only after
all. **Both docs are now wrong in different directions**:
`pitch-play-runner.md`'s conclusion is right for the wrong reason, and
`hb-vision-and-moves.md`'s correction is wrong. Needs one ruling.

## Tier 4 — unflagged conditional-move and delay-slot dependencies

These are the recurring trap in this codebase, and each of these carries a
live claim that inverts if the instruction is read as unconditional.

* **`movn` at `0x00153668`** — the difficulty skill term is `s0+2`
  (13/8/6/4) **unless the opposing side has a human**. The published
  11/6/4/2 holds only in the CPU-defence-vs-human case.
* **`movz` situation latches** in all four zone thinks (`0x001eeab8`,
  `0x001ea330`, `0x001ed968`, `0x001ec708`). Situation 6 **bypasses the
  awareness roll entirely** — the break-off can fire with no roll at all.
  Neither doc mentions this.
* **`bnel` at `0x001EF8C8/CC` and `0x001EF8DC/E0`** (plus four more sites)
  — the pass-block vs run-block **attribute selection** sits in likely
  slots. Misreading them swaps which rating is in play.
* **`movz` at `0x001f0408/0c` and `0x001f188c/90`** — cuts the defender's
  escape threshold to 1/16. Reading it as unconditional inverts the odds.
* **`movn` at `0x001a0b00`, `0x001a0b2c`; `movz` at `0x001a0b70`** — the
  press contest's flat +50 modifier and its severity picks.
* **`min.s` at `0x001fdd74`** — caps raw QB threat at 7.0, in an
  always-executed delay slot.
* **`beql` at `0x0024D508/0C` and `0x0024D570/74`** — the class-34 rescale
  multiply and the success-factor add run **only** in likely slots.
* **`movz` at `0x001cb75c/0x001cb7a0`** — the ×2 arm of the four-way AWR
  split. **`beql` at `0x001444d8`** — the master-flag test in the blocking
  transform. **`movn` at `0x001EFD58`** — the 4-attribute average.

## Tooling defects found

**1. `find_address_refs` produces false negatives.** It pairs `lui` with
its `addiu` inside a 64-byte window. Demonstrated directly:

```
find_address_refs(e, 0x139A68) -> []
find_address_refs(e, 0x139AA0) -> []
find_address_refs(e, 0x139B50) -> []
find_address_refs(e, 0x139C28) -> []
```

— all empty, while the references sit in plain sight at spreads of 124–128
bytes. **This is the root cause of Tier 1 item 1.**

**2. No cross-function base tracking.** The `+0xB07` writer passes
`player+0xAE8` as an *argument*; the `addiu` and the `sb` are in different
functions. No single-function sweep can see it. **This is the root cause
of Tier 1 items 2 and 3.**

Together these two gaps account for every Tier 1 finding. The lesson
generalises:

> **Every "no writer anywhere" / "dead field" / "zero-reference" negative
> in this project is unproven until re-checked with a wide pairing window
> and cross-function base tracking.**

The one still outstanding and load-bearing is `play-data.md`'s
"`playRecord+28` has no writer in the ELF" — the claim that makes the QB
progression table look like authored play data. It is exactly this shape
and has not been re-checked.

**3. `fpudis` mis-decodes `sqrt.s`** — confirmed material, not cosmetic.
Both punt-solver square roots put the operand in `ft` while the tool
prints `fs`, so the listing reads as `sqrt(f0)` and destroys the
flight-time derivation. `punt-logic.md`'s formula matches the *correct*
decode, meaning its author compensated by hand. **`rsqrt.s` has the same
defect** (`fd = fs/sqrt(ft)`) and is also printed wrong.

## What could not be checked, and what would settle it

**No game data on this machine.** `extract/` holds only `SLUS_207.52` and
`TEMPLATE.DAT`. That leaves unverifiable: every UIS-bytecode claim (screen
order, the penalty-screen permutation, the A.I.-screen widget→option
binding, script method ids 229/230, the ×5 tick formatter, LZH1 bit-exact
validation); `play-data.md`'s `PLADATA.DAT` claims (1038 LZH1 members, the
DMF content, the 74/208/756 scan census) and the `DB_TEAMS.DAT` /
`GAMEDATA.DAT` negatives; and the protocol track's byte-exact `@dir`,
login and buddy examples, which depend on `captures/*.jsonl`. Every
**ELF-side** half of these was confirmed.

**Needs the rig.** Which weighting function serves the CPU offence
(`0x24D070` vs `0x24D1C8`) — the highest-value single measurement, since
it decides Tier 1 item 5. FPU rounding mode of `cvt.w.s`, which decides
the 50/90 band flips *and* whether the power-move gate passes at 65 or 66.
Empirical liveness soaks on caves #1 and #3 (predicted to trip) and test-1
soaks on the six that passed statically. `player+0xB07`'s actual value for
a halfback. `0x16B590 == 255` as "no controller". The wind-vector sign
convention. The `ptrk` ring's side semantics.

## What survived — the load-bearing structure

Worth stating plainly, because the list above is all failures.

Confirmed by independent re-derivation, usually to the address: the
options array, the universal transform and its exactly-eleven callers, the
31-byte block and master enable, the penalty ramp with raw=50 as the only
bit-exact identity, and every per-penalty quirk. The double-team system
end to end — registry, role bytes, scorer, 7→8 promotion, peel-off,
debuff-not-sum — plus the kind 7/8/9 taxonomy and state 32's ownership of
kinds 5/6. The pass/run two-system split and the pass-pro cadence
overflow, arithmetic re-derived. The tackle contest and its class bonus.
The catch process, post-catch strip and fumble base rates (the last
reproducing *exactly*, including the exact-zero saturation at 100/100).
The `ptrk` object layout, weight table, 0.9375 cap and closed input
census. Zone bunching's entire geometry and its headline negative. The
79-function zero-rating-read closure (reproduced at 81 functions). The
move priority rows and every gate and probability. The QB radar,
suppression counter, throwaway roll and the whole scramble chain,
including `P = 0.0725 + SPD/200` proven identical to the coded form. The
punt coffin solver, including its flight time as an exact
`t = vz/g + sqrt((vz²+2gh)/g²)`. The play-call enumerator in every part —
one predicate, no filters, no top-N, and the **225-record buffer whose
fetch loop has no bound check**. And the protocol track almost in full,
including both tag tables byte-for-byte and the roster checksum
reproduced live.

**Every cited patch word in `default-uplift-tuning.md`, `punt-logic.md`,
`zone-bunching.md` and `pitch-play-runner.md` was verified byte-exact.**
Zero stale addresses in those tables — the errors that did surface are in
prose and in the negatives, not in the patch sites.

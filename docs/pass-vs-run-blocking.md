# Pass blocking vs run blocking: two systems, not one

Investigated 2026-08-10 against `SLUS_207.52` (Madden NFL 2004), in
answer to open question #14c — and it closes a long-standing gap in
`slider-behavior.md`.

## They are genuinely separate systems

Not one behaviour with different constants. **Two separate AI states with
separately-authored steering code**, ~700 instructions each, and *no
shared steering function* — every helper of each state has exactly one
caller, its own state's think.

| | **pass protection** | **run block** | lead/pull |
|---|---|---|---|
| state | **31** | **33** | 47 |
| enter | `0x001cabd8` | `0x001dc110` | `0x001b66a8` |
| AI think | `0x001cb008` | `0x001dc2d8` | `0x001b6870` |
| block mode (`+0x3F0`) | **1** | **2** | **3** |

`player+0x3F0` is the **block-mode enum**, written by one 2-instruction
leaf (`SetBlockMode 0x001f7568`, 37 call sites): 0 = not blocking, 1 =
pass, 2 = run, 3 = lead/pull. It is **not** a play-phase flag — the mode
comes from the play-authored assignment byte, and states 31↔33 convert
into each other live when the play changes character.

## What each actually does

**Pass protection has a pocket.** Its enter computes a lateral set-point
and a **depth line** (`LOS_y − 1.25·|lateral|`), plus a QB-relative
jittered landmark. Phase 1 meets the rusher at the pass set with a ±90°
sidestep and an explicit **backpedal** fallback; phase 2 anchors and
mirrors, with a ¾-angle leverage swing back across the QB and a hard
**`speed = 0.0` anchor** when the rusher is within half a yard laterally
and not driving at the quarterback. Speed is *inverse*-distance — the
closer the rusher, the slower the blocker moves.

**Run blocking has no landmark at all.** It leads the defender by
**velocity × 10**, rotates its bearing half-way toward the ball carrier
when close, and aims at the ball-side shoulder — drive and seal. Speed is
*proportional* to distance, capping at 1.0.

Both have an LOS constraint, with **opposite signs**: the pass blocker is
frozen if he drifts past the line, the run blocker is pushed toward it.

## The contest is different too — eight phase-exclusive terms

Beyond the PPBK/PRBK attribute swap, `0x001f0c40` applies:

* **A pass-only pocket-collapse decay.** All three of the blocker's
  contest scores are multiplied by `(1 − k·rand)` where `k` ramps with
  frames since the snap — **the pass blocker loses up to 95% of his
  contest score over the first three seconds**, and the rusher gets a
  small free bonus. A second, absolute decay runs alongside it.
* **A pass-only 4× swing** when the QB appears to leave the pocket: the
  blocker's components are halved and the rusher's doubled.
* **Pass-only team/coach modifiers** (attributes 21 and 22).
* **Run-only home-team +10%**, and **run-only +25%·difficulty for a CPU
  defender against a human blocker**. Pass blocking has neither — worth
  knowing if asymmetric CPU pass rush is ever a complaint.

## What the Pass/Run Block sliders actually scale (gap closed)

`slider-behavior.md` recorded these as scaling "a 3-float blocker impulse
vector". **That was wrong.** They scale the **three block-contest score
components** themselves:

| field | what it decides | slider strength |
|---|---|---|
| `+0x414` comp1 | the early-phase rep (< 46 frames) **and the drive speed** | 0.5 |
| `+0x418` comp2 | **who drives whom** — selects the shared bearing/facing | 0.5 |
| `+0x41C` comp3 | the late-phase rep (≥ 46 frames) | 0.35 |

Applied **only to the blocker**, never the defender. Slot 1 = Pass
Blocking on a pass play, slot 4 = Run Blocking otherwise. So the slider
moves the blocker's chance of winning the rep *and* the speed at which the
locked pair translates. (Note this also identifies `+0x41C` — which
`pass-rush.md` called a dead field — as the **late-phase** component. It
is read, just later in the rep.)

## "Action figures", stated precisely

> A 14–30-frame rigid two-body translation along a frozen shared axis
> whose only magnitude is a rating ratio, with no force, no positional
> coupling, and no leverage.

* The entire physical output of a block is **one scalar** — a normalised
  rating margin — written *identically into both players*, along with an
  identical bearing. Only the facings differ, by exactly 180°.
* **Zero stores to any player's position** anywhere in the block code.
  Neither body is ever pushed; there is no positional coupling at all.
* No pad level, no hand placement, no gradual give. The triple is frozen
  for 14–30 frames, so a block is a staircase of rigid steps.
* Nothing accumulates between reps — every score is recomputed from raw
  ratings each time.

## A second explanation for the 40/44/55 evidence

This lane found a **sign-extension bug** in the pass-pro steering
throttle. The re-evaluation interval is computed from `PPBK + AWR`, but
the pass version truncates the sum to a *signed* 8-bit value where the
run-block twin does the same arithmetic **correctly**:

| PPBK + AWR (effective) | interval | intended |
|---|---|---|
| ≤ 127 | 4 frames | 4 |
| 128–255 | 3 frames | 3 |
| **256–383** | **6 frames** | 2 |
| 384–510 | 5 frames | 1 |

The overflow edge sits at roughly **PPBK + AWR = 100 on the 0–100 scale**
— with a typical lineman's awareness of 60, that is **pass block 40 → 41**.
The throttled body contains the **assignment-drop test**, so a blocker in
the fast band runs that test twice as often and sheds his man more
readily in the approach phase — which reads on screen as "he watched the
rusher run past him".

**Note this is a *different* mechanism from the one in
`rating-thresholds.md`**, which explains the same observation through the
`>>4` engagement-lock band with an edge at exactly 44. Both are real
arithmetic; they are not mutually exclusive (different code, different
phase), but **which one the tester actually saw is unresolved** and would
need a rig measurement. The behavioural direction of this lane's
explanation is inference; the band arithmetic in both is certain.

## Fix candidates

| # | change | site | risk |
|---|---|---|---|
| **P1** | **Fix the cadence overflow** — replace the truncating shift pair with the run path's correct arithmetic. Restores the intended 4/3/2/1 ladder and removes the ~40 cliff | `0x001c9efc`, `0x001c9f00` | **Low**, 2 words |
| P2 | Soften the assignment-drop test so a pass blocker cannot shed his man during the approach | `0x001ca0a8` / `0x001ca0c8` / `0x001ca104` | Med |
| **P3** | **Pocket hold time** — four single-use data floats governing the collapse decay (0.95 over 180 frames, 0.45 over 300). Lower the 0.95 and pockets hold | `0x005FF104`, `0x005FF100`, `0x005FF118`, `0x005FF114` | **Low**, data only |
| P4 | Halve the 4× QB-out-of-pocket swing so protection does not evaporate the moment the QB drifts | `0x001f1348`, `0x001f1360` | Med |
| P5 | LOS anchor distance (both arms must match) | `0x001f15cc`, `0x001f16b0` | Low |

## Toolchain bug found (important)

**`recon/mipsdis.py` prints `SLLV`/`SRLV`/`SRAV` with `rs` and `rt`
swapped.** The generic formatter emits `rd, rs, rt`; these three are
`rd, rt, rs`. In the break-tackle roll this makes the listing read
"shift the counter by the score" when the semantics is "shift the score
by the counter" — which made an entire live function look like dead code
for twenty minutes. Add this to the standing disassembler debt alongside
REGIMM, MMI and 3-operand `mult`.

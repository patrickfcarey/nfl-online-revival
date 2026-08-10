# The sdchargersfanboy question: maxed Awareness gets safeties burned deep

> "In All-Madden difficulty, when I have 20/20 AWR and 20/20 Tackling maxed
> out, the SSs just lose all tracking and get burned in deep coverage. But
> when I max out Knockdowns at 20/20, they get an AWR boost and don't get
> beat as hard on deep routes."

Investigated 2026-08-09 against the SLUS-20752 executable (three dedicated
scan lanes on top of the verified baseline in `slider-behavior.md`).
Verdict up front: **the observation is real, both halves, and the code
explains it exactly — though the second half isn't an awareness boost, it's
something better.**

## The one-paragraph answer

In this engine the Awareness attribute is not intelligence — it is
*decisiveness*. Every coverage state (man and all zones) runs a periodic
"should I abandon my assignment and chase the ball?" roll whose probability
is literally `AWR/255` per evaluation, and whose evaluation interval
*shrinks* as AWR rises. Max the Awareness slider and a good safety's
effective AWR pins at the 255 ceiling: he re-evaluates every 2 frames on
All-Madden and leaves coverage with 100% probability the first moment the
ball situation looks like a run, a screen, or a scramble — which is
exactly what play-action forges. That is "loses all tracking." Maxing
Knockdowns doesn't touch awareness at all; it raises the only roll in the
game that puts a thrown ball on the ground — an un-depth-gated per-frame
swat check for any defender within 6 units of the catch point — from 50%
to 87%. Deep bombs stop completing, which *feels* like the safety got
smarter. He didn't; he got a better bat.

## First, the things this is NOT

* **Not a mislabeled slider.** The widget→storage binding was extracted
  from the UI bytecode itself (the screens bind through a settings-id
  layer, master table at `0x00545918`): the defense tab binds Awareness →
  option 31/46 (human/CPU), Knockdowns → 32/47, Interceptions → 33/48,
  Break Block → 34/49, Tackling → 35/50 — exact, no shift. The penalty
  screen was verified as a control and also binds exactly.
* **Not All-Madden super-receivers.** The difficulty classes map
  0=Rookie (actually *nerfs* the CPU ×6/7), 1=Pro (identity), 2=All-Pro
  (boosts only CPU Awareness), 3=All-Madden (every attribute → `2v/3+85`
  — *except attribute 13, which is Speed*). Two consequences: All-Madden
  never makes receivers faster, and the boost is a floor-raiser worth only
  +2–4 points to an already-good receiver. Also: the lone human's team is
  class 1 — untouched — at every difficulty. The deep burns are not
  stat-cheat receivers outrunning the safety.
* **Not slider values being ignored.** The opposite: at max slider, any
  defender with roster AWR/TAK of 69+ saturates the hard 255 internal cap
  (the transform is `x·1.45` at slider max, clamp 255). The slider works;
  what it *does* is the problem.

(On "20/20": the stored range is 0–100 and the value moves between screen
and database raw; the 0–20 tick display is a widget-formatter detail whose
×5 mapping we could not pin statically, but EAsy Play writing 75/25 —
i.e. 15/5 in ticks — is consistent with it. "20/20" = stored 100.)

## Mechanism 1: why maxed AWR/TAK loses deep tracking

Every coverage state — man (state 22) and the zone family (37/38/39/40)
in the 93-state defender AI machine at `0x00527238` — runs the identical
two-part idiom (quoted here from state 38's think function `0x001ee7d0`;
states 22/37/39 are byte-for-byte equivalent, and state 40 runs the same
shape at **AWR/2** — `0x001ec724`–`0x001ec730` — making it the one
already-disciplined zone):

1. **Decision cadence.** A countdown refilled with
   `skillTerm + rand(0, (255−AWR)/32)`, where the skill term is 11/6/4/**2**
   for Rookie/Pro/All-Pro/**All-Madden**. At AWR 255 the random part is 0:
   the state re-evaluates **every 2 frames** on All-Madden.
2. **The break-off roll.** On each evaluation, if the ball-situation code
   (`0x00145ef8`) reads "run/screen/scramble-like", roll
   `RandomInt(0,255) < AWR` — i.e. **P(abandon coverage) = AWR/255** —
   and on success request **state 2: ball pursuit** (`0x001eeb18`–
   `0x001eeb94`; man coverage's copy at `0x001be960`).

Maxed Awareness slider ⇒ effective AWR 255 ⇒ the roll always succeeds and
is taken every 2 frames. The safety ditches his deep assignment on the
first frame the offense shows him a run look. Play-action is precisely a
forged run look. And the asymmetry that makes it fatal: the ball-in-the-air
branch (switch to state 24, "play the ball") is **not** AWR-gated — the
slider that pulled him out of position does nothing to help him recover
once the bomb is up.

Tackling makes it worse because TAK rides shotgun in the cadence terms —
`21 − (AWR+TAK)/32` and `31 − (AWR+TAK)/32` reaction timers (`0x0019ef90`,
`0x001cb97c`) — which is presumably why the report names both sliders.

The design note that falls out of the whole census: **no code anywhere
makes high AWR improve the *quality* of a decision** — there is no route
prediction, no play-action recognition check, no "deepest receiver" scan
that awareness feeds. All 83 AWR read sites in the gameplay image are
monotone cadence-shorteners or probability-raisers. Awareness is a
hair-trigger dial. At EA's tuning midpoint (50) it reads as "smart" —
defenders converge on real runs faster. At the extremes the sliders
unlock, decisiveness becomes bait-ability. The honest label for the
slider would be *Aggressiveness*.

## Mechanism 2: why maxed Knockdowns "fixes" it

The Knockdowns slider has exactly one consumer in the executable: the
swat roll at `0x0019bd74`. Its context (walked fully):

* Runs per frame during play phase 4 (**ball in flight**) for defenders
  whose AI update lands on it, when three geometry gates pass: contest
  point at catchable height (1.5 < z < 7.0, or the receiver is already in
  his catch animation), facing gates, and **distance to the catch point
  < 6.0 units**.
* **There is no pass-depth term anywhere in the chain.** A 45-yard bomb
  where the safety arrives within 6 units is exactly as swattable as a
  slant.
* Chance = `clamp(0,100, 50 + 0.75·(v−50))` vs `RandomInt(0,100)`:
  **13% / 50% / 87%** at slider 0 / 50 / 100.
* Success commits the defender to the leap-and-bat animation (68), which
  the ball-arrival resolver (`0x00255250`) recognizes and turns into a
  defended ball.

So maxing Knockdowns converts contested deep balls into knockdowns at
nearly twice the default per-frame rate. The safety is in the same
(bad) position — but the ball hits the ground, so the *perception* is
"my SS got an AWR boost and stopped getting beat." The boost is real;
it's just a bat, not a brain.

One caveat worth a runtime measurement: the roll repeats every eligible
frame, and failures just retry. Compounded over N frames, slider 50
already reaches ~88% by N=3 — so how much slider 100 adds in practice
depends on how long the 6-unit window lasts on a deep ball. That needs a
PINE/savestate measurement on the rig, not static analysis.

## What to actually run (practical advice)

* **Do not max defensive Awareness against a play-action offense.** In
  this engine it maximizes bite rate. Values *below* 50 make safeties
  slower to commit — which is indistinguishable from more disciplined.
* **Knockdowns is the deep-ball lever**, full stop. It is the only slider
  on the pass-defense path that produces incompletions.
* Tackling contributes to the jumpiness through the shared timers; if the
  goal is deep discipline, it is not a free maxout either.
* Interceptions, for the record, is purely the catch roll — it does not
  change whether a defender attempts the play (`0x00254de8` reads no
  slider).

## Open items from this investigation

* ~~The CPU-only awareness multiplier~~ — **resolved, see
  `play-tendency-ai.md`.** It is an anti-repetition play-history tracker
  (`'ptrk'`): `f` is a recency-weighted count of how often the offense's
  current play was called in its last 48 snaps (max 0.9375, ~+94% AWR on
  the break-off roll). CPU defenses only; never fires in human-vs-human.
  Practical corollary for the reporter: if the deep shot that keeps
  getting jumped is the *same play* called repeatedly, the CPU's safeties
  are being told so — at All-Madden, 12 repeats in 12 plays saturates
  their break-off roll at 100% regardless of sliders.
* The per-frame swat-window length on deep balls (runtime, rig).
* The tick-display ×5 formatter (cosmetic).
* Two further unexplained multipliers on the effective-ratings path,
  fetched from the per-side team-strategy table (`gp−20092`, types 3 and
  6, default 1.0) — same table as the unresolved ×4 penalty gate.

## Evidence index

| claim | address |
|---|---|
| coverage break-off roll `rand(255) < AWR` → state 2 | `0x001eeb18`–`0x001eeb94` (state 38); `0x001be960` (man) |
| decision cadence `skillTerm + rand(0,(255−AWR)/32)` | `0x001ee8d0`–`0x001ee908` |
| skill term 11/6/4/2 by difficulty class | `0x001535f8` |
| reaction timers `21/31 − (AWR+TAK)/32` | `0x0019ef90`, `0x001cb97c` |
| ball-in-air branch not AWR-gated | `0x001eeacc` (situation 4 → state 24) |
| difficulty classes, Speed excluded, human always class 1 | `0x00153068`, `0x00152ff0` |
| rating transform ×1.45, clamp 255 | `0x00144b18`, `0x00144c7c` |
| knockdown roll, gates, no depth term | `0x0019bd74`, chain in `0x0019b338` |
| swat animation → defended ball | `0x001b2aa0` → `0x001b2958` → `0x00255250` |
| UI defense-tab binding (no shift) | settings-id table `0x00545918`; screen `UIS_PAUC.DAT` sub-file 3 |
| CPU-only multiplier `AWR + AWR·f` | `0x001eeae4`–`0x001eeb08`, getter `0x0024e188` |

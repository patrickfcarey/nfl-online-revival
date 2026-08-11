# Lead-blocker behaviour — requirements before the patch

A design spec for fixing the pulling guard on the misdirection play, agreed
2026-08-11. The rule here: **every requirement has a measurable acceptance
test in the harness.** The definitive patch ships when the tests pass, not
when a number looks better. Nothing is patched until this doc is settled.

## The problem, measured

On the misdirection play (single-back, RG pulls), against the live game:

| metric | baseline | what it means |
|---|---|---|
| `carrier_yards` | **~0.95** | the run is stuffed for a yard, every play |
| `lead_blocker_block_depth` | **~0.53** | the guard commits his block half a yard past the line |

He is not blocking the wrong *type* of man — he often finds the ROLB, a
linebacker — he blocks him **too early, at the line**, before the hole
develops, so the back has nowhere to go. See `fb-wr-blocking.md` for why the
lead blocker steers himself (state 47, excluded from the assignment system);
the levers are in `lead-blocker-targeting.md` and disassembled at `0x001B61A0`.

## Situations this must not break

The fix touches state 47, the single lead-block state, so it touches **every**
lead blocker, not just the misdirection puller. The requirements are written
to be route-relative precisely so these all keep working:

| situation | who | where he blocks | trap for a naive depth gate |
|---|---|---|---|
| misdirection iso *(measured)* | pulling guard | 2nd level, through the hole | none — this is the target case |
| fullback iso / lead dive | FB | 2nd level, through the hole | none — same pattern, needs its own savestate |
| power / counter | pulling guard/tackle | **kick-out at the line** | a "must be 4 yd deep" gate forbids the kick-out |
| toss / sweep | G/TE/H-back | **force defender on the edge, near LOS** | forced past his man |
| **screen pass** | releasing O-line | **rushers at/behind the LOS** | forced downfield; screen collapses |
| draw | line + back | mixed | depends on assignment |

**A blanket depth rule breaks the bottom four. A route-relative rule does
not.** This table is the acceptance surface: the definitive patch is validated
against a savestate of *each* row, not just the misdirection play, and none
may regress.

## Requirements

Each is: the rule, why, the **acceptance test** (harness metric + threshold),
the code mechanism, and any decision or risk taken.

### R1 — Engage at his landmark, not before  (REVISED 2026-08-11)

**Rule.** The lead blocker may not commit a block until he has reached his
**authored landmark** — the route point state 47's enter already reads
(`record+2` bearing, or `record+1/+3` x/y). Not a fixed depth.

**Why the revision.** The first draft said "clear the line to ~4–5 yards," and
that is wrong for every lead-block meant to happen *at* the line — kick-out
blocks on power, the force block on a toss/sweep, and screen-pass release (see
"Situations this must not break", below). Those share state 47, so a blanket
depth gate breaks them. The real defect is not shallowness; it is that he
**abandons his route to hit the first man he sees.** "Run your route, engage
at its end" lands him deep on an iso, at the edge on a kick-out, and near the
line on a screen — automatically, because the route encodes the intent.

**Acceptance test — per play type.** `lead_blocker_block_depth` must land near
the play's authored landmark depth, not a constant:

| play | landmark | expected block depth |
|---|---|---|
| misdirection iso (baseline) | through the hole, 2nd level | ~4–5 yd (from 0.53) |
| kick-out / power | edge defender at the line | ~0–1 yd (must stay shallow) |
| toss / sweep | force defender on the edge | near the LOS |
| screen | flat / near the line | at or behind the LOS |

So the metric compares his commit point to his **landmark**, read from the
state-chain record — a new metric `lead_blocker_commit_vs_landmark`, which
should be small (he engages at the end of his route) on *every* play type.

**Mechanism.** Gate the commit (`c.lt.s f0, f20` at `0x001B623C`) on
"have I reached my landmark yet", using the landmark he already carries.
Small cave hook. No fixed constant — the play supplies the depth.

**Decision / risk (accepted).** On the iso, a down lineman who beats the block
and is loose at the line goes unblocked (the puller is past him). Accepted for
the iso; and route-relative means kick-out/screen blockers are *not* forced
past their men, which is what makes this safe.

### R2 — Only target what is in front of his vision

**Rule.** He may only target a defender inside his forward vision cone, and
**never a man he has already passed** (no chasing backward).

**Why.** Real blocking is who's in front of you; a blocker who spins back for a
trailing defender abandons the lead.

**Acceptance test.** New metric `lead_blocker_backward_targets` = frames he is
engaged with a defender behind him in the attack direction. Must be **0**.

**Mechanism.** The steering already uses a 60° cone (`0x001B61A4/AC`), and
**O1 is now resolved: the cone is measured off his current facing/heading**
(`player+0x1A8`), not his route bearing. So the engine already blocks
*forward, relative to where he is looking* — R2's core is satisfied by the
shipped code. What remains is narrower: the 60° half-angle is generous (it
admits a man up to 60° off his facing, i.e. well off to the side), and a
man directly behind is already excluded. So R2 becomes **"narrow the cone"**
(a data-tunable, `0x001B61A4/AC`) rather than "build a vision system", plus a
confirm that nothing re-adds a passed man. This is much cheaper than feared.

**How O1 was resolved.** The cone test (`0x001FEC78`) takes each candidate's
blocker→defender bearing (atan2 at `0x00469E78`) and compares it against the
reference passed in as `player+0x1A8`, accepting within the 60° half-angle.
Read against the in-play dump, `+0x1A8` is ~90° (facing downfield) for the
whole offense, distinct from the authored route bearing at `+0x164` (46.8°
for everyone — the play direction). So the reference is facing, not route.

### R3 — Prefer the second level, fall back to nearest

**Rule.** Among eligible in-vision defenders, prefer a linebacker or safety
(position 13–18); take a down lineman only if no second-level man is reachable.

**Why.** The designed block is on the force defender at the second level. The
fallback keeps him from freezing when there is no LB/S to take (and partly
covers R1's accepted risk: a truly free rusher becomes "nearest eligible").

**Acceptance test.** New metric `lead_blocker_target_is_second_level` = the
first man he engages has position 13–18. True on **≥ 80%** of plays (not 100%:
the fallback is legitimate).

**Mechanism.** The target-finder (`0x001FEB98`) returns one defender; today it
is proximity-first. Ranking by defender position/type is a cave routine that
wraps or replaces the finder — the largest piece of work here.

### R8 — Zone schemes take a lateral first step before engaging

**Rule.** In a zone blocking scheme the lineman must take a **big lateral
step playside first**, before he may attempt any block.

**Why.** That first step is the whole basis of zone blocking — it establishes
the playside track and lets the double-team/climb rules resolve. A lineman who
engages before stepping is doing man blocking with a zone call.

**Acceptance test.** New metric `first_step_lateral` = the blocker's net
lateral (x) displacement before his first engagement. Must exceed a step
(~0.7–1.0 yd) on a zone call, and R1's landmark gate must not fire before it.

**Status: unscoped.** Two unknowns first: whether the shipped engine
distinguishes a zone call at all in the blocking data (the assignment *class*
byte may or may not encode scheme), and whether this belongs in state 47 or in
the ordinary run-block state 33 — most zone-scheme linemen are not lead
blockers, so this requirement probably lives outside state 47 and outside the
current patch's blast radius. Do not fold it in until that is settled.

### R4 — Retarget along the route

**Rule.** He re-picks his man every few frames as defenders flow, rather than
locking at first sight.

**Why.** `lead-blocker-targeting.md` requirement #2; a static target is how he
ends up on the wrong man when the defense rotates.

**Acceptance test.** `lead_blocker_partners >= 1` with retargets visible in the
`engagement_link` series (he changes target at least once on plays where the
defense flows). Already partly measured; refine if R3's finder changes.

### R5 — Awareness governs correctness

**Rule.** High awareness → he retargets to the right man; low awareness →
honest mis-reads.

**Why.** `lead-blocker-targeting.md` requirement #3 — this is what keeps the
fix from making every guard a genius, and ties blocking quality to a rating.

**Acceptance test.** Sweep the AWR of the pulling guard (a **data** poke, which
PINE *can* do — ratings are re-read each frame) and show
`lead_blocker_target_is_second_level` rises with AWR. Deferred until R1–R3
land, but the roll belongs in the same cave.

### R6 — Honest misses, never warps

**Rule.** When he can't reach the right block, he misses believably — no
position-snap, no warp into contact.

**Why.** `lead-blocker-targeting.md` requirement #4.

**Acceptance test.** The operator ask `ASK_WARP` (already in the spec) returns
"no" — no block lands by a teleport. Human eyes; there is no memory read for
"looked wrong."

### R7 — No regression on ordinary line play

**Rule.** The patch changes lead blockers only. Pass pro and run blocking by
the five linemen are untouched.

**Why.** State 47 is lead-block-only, so this should hold by construction — but
it must be *shown*, or a yardage gain could be the O-line breaking, not the fix.

**Acceptance test.** `pool_blockers` and the linemen's `block_mode`/engagement
distributions are unchanged from baseline (within run-to-run noise).

## Testing protocol — a standing rule

Every patch is tested **individually before it is combined with any other**,
and only then as an integrated whole. No patch reaches the integration step
until it has passed in isolation.

**1. Per-patch, in isolation.** Apply exactly one patch, nothing else. It
passes only if **both** hold:

* *It does its job* — it moves its own requirement's acceptance metric in the
  intended direction (e.g. R1's patch moves `lead_blocker_commit_vs_landmark`
  toward zero).
* *It breaks nothing* — the regression surface is unchanged within noise: R7
  (ordinary line play), and every row of the "situations this must not break"
  table, each on its own savestate. A patch that fixes the iso and collapses
  the screen has failed, not half-passed.

**2. Integration.** With all patches applied together, run the **full
acceptance suite** — every requirement's metric across every play-type
savestate — plus the automated unit/regression suite (`python3
tests/test_madden_lab_*.py`, currently green) to prove the harness itself is
sound. The definitive patch ships only when the whole suite passes.

**Design consequence for O3.** "Test each patch individually" is in tension
with "collapse R1–R3 into one cave routine." A single monolithic routine
cannot be isolation-tested per requirement. So the cave must be built with
**per-requirement toggles** (a byte or a branch each, flipped off to disable
that requirement), or R1–R3 stay as separable hooks. Either is fine; a
routine that can only be tested all-at-once is not. This is now an input to
the O3 decision, not an afterthought.

## The outcome test that matters

R1–R7 are the *mechanism*. The point is the play:

**`carrier_yards` shifts up meaningfully from the ~0.95 baseline** — target a
median over ~4 yards and a real tail of big gains — **while R7 holds.** If
depth and target improve but yards don't, the requirements were wrong and we
revisit, rather than tuning until the number moves.

## Open questions to settle first

* **O2 — RESOLVED 2026-08-11 (partly), and it reframes R1.** For the
  misdirection pull, the authored landmark is a **bearing only**: state 47's
  enter writes `record+2` as a 24-bit BAM to `self+0x164` (measured 143.4° on
  this play) and **branches past the point decode whenever that bearing is
  non-zero**, leaving `+0x158`/`+0x15C` at their defaults. So this play tells
  the guard a *direction* and never a *destination* — which is a strong
  candidate explanation for why he commits at the line: nothing authored says
  how far to pull. Measured against it, his actual travel is 180.9° (flat
  left) versus the authored 143.4° (left and downfield). **Consequence for
  R1:** "engage at your landmark" is not implementable as written for a
  bearing-only play; the gate needs a distance the play does not supply, so R1
  must define one (e.g. clear the down-line box along the bearing). Still open:
  whether any play type uses the point form, and what `+0x158`/`+0x15C` mean
  when it does.
* **O1 — the vision cone's reference. RESOLVED 2026-08-11: facing.** The cone
  (`0x001FEC78`) measures each defender's bearing against `player+0x1A8`, the
  blocker's current facing/heading, not his route bearing (`+0x164`). Verified
  against the in-play dump. Consequence: R2 shrinks to "narrow the cone" plus a
  no-backward-chase confirm; the vision system already exists and is
  facing-relative. See R2.
* **O2 — the LOS/hole reference at runtime.** R1 needs "past the down line."
  Is that LOS + a constant, or the actual hole location the play defines?
  Constant is simpler and probably enough; confirm the play doesn't move it.
* **O3 — how much of R1–R3 is one cave routine.** They share inputs (his
  position, the defender list, the LOS). Likely one hooked routine rather than
  three patches; decide before cutting the cave, and re-check the cave is dead
  with the repaired wide-window scan (`code-caves.md`).
* **O4 — does screen-pass (and edge) blocking use state 47?** The decider for
  the whole blast radius. If releasing screen linemen and edge/kick-out
  blockers run state 47, the route-relative R1 is mandatory and each must be
  in the validation set. If screens use a different mechanism (state 31 pass
  pro → a screen release), they are untouched and the concern narrows. Resolve
  by finding which state a screen lineman and a kick-out puller are in at
  contact — a harness snapshot on a screen savestate and a power savestate,
  which needs those saves from the operator. **This gates the cave design.**

## Harness work this implies

* Two new metrics: `lead_blocker_backward_targets` (R2),
  `lead_blocker_target_is_second_level` (R3).
* The AWR data-sweep harness for R5 (PINE data pokes, no reboot).
* Everything else is already measured.

## Status

Requirements agreed; **O1–O3 to resolve, then the cave routine is designed
against R1–R3, then swept against the acceptance tests.** No patch is written
until O1–O3 are answered and the two new metrics exist to test against.

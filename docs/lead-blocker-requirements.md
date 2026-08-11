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

## Requirements

Each is: the rule, why, the **acceptance test** (harness metric + threshold),
the code mechanism, and any decision or risk taken.

### R1 — Clear the line before engaging

**Rule.** The lead blocker may not commit a block until he is past the down
linemen, at second-level depth (~4–5 yards past the LOS).

**Why.** A puller's job is to get to the hole and lead through it; engaging at
the line seals nothing and kills the play — the measured defect.

**Acceptance test.** `lead_blocker_block_depth >= 4.0` yards (median over the
run). Baseline is 0.53, so this is the headline number to move.

**Mechanism.** A gate on the commit test (`c.lt.s f0, f20` at `0x001B623C`):
suppress the commit while his own Y is less than LOS + ~4. Needs his position
and the LOS reference, so a small cave hook, not a one-word change.

**Decision / risk (accepted).** A down lineman who *beats* the guard-or-tackle
and is loose at the line will go unblocked, because the guard is now forbidden
to engage there. Accepted: the O-line owns the down linemen; the puller owns
the second level. R3's fallback softens it (below).

### R2 — Only target what is in front of his vision

**Rule.** He may only target a defender inside his forward vision cone, and
**never a man he has already passed** (no chasing backward).

**Why.** Real blocking is who's in front of you; a blocker who spins back for a
trailing defender abandons the lead.

**Acceptance test.** New metric `lead_blocker_backward_targets` = frames he is
engaged with a defender behind him in the attack direction. Must be **0**.

**Mechanism.** The steering already uses a 60° cone (`0x001B61A4/AC`). Two
things to settle first (open question O1): whether that cone is measured off
his **facing/heading** or off his **route bearing** — for a puller whose body
faces across the field these differ sharply. Then: keep the cone forward-only
and add the already-passed exclusion.

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

## The outcome test that matters

R1–R7 are the *mechanism*. The point is the play:

**`carrier_yards` shifts up meaningfully from the ~0.95 baseline** — target a
median over ~4 yards and a real tail of big gains — **while R7 holds.** If
depth and target improve but yards don't, the requirements were wrong and we
revisit, rather than tuning until the number moves.

## Open questions to settle first

* **O1 — the vision cone's reference.** Facing/heading vs route bearing at
  `0x001B61A0`. Resolve by disassembling how the finder's angle argument
  (`a2`) is derived. Blocks R2.
* **O2 — the LOS/hole reference at runtime.** R1 needs "past the down line."
  Is that LOS + a constant, or the actual hole location the play defines?
  Constant is simpler and probably enough; confirm the play doesn't move it.
* **O3 — how much of R1–R3 is one cave routine.** They share inputs (his
  position, the defender list, the LOS). Likely one hooked routine rather than
  three patches; decide before cutting the cave, and re-check the cave is dead
  with the repaired wide-window scan (`code-caves.md`).

## Harness work this implies

* Two new metrics: `lead_blocker_backward_targets` (R2),
  `lead_blocker_target_is_second_level` (R3).
* The AWR data-sweep harness for R5 (PINE data pokes, no reboot).
* Everything else is already measured.

## Status

Requirements agreed; **O1–O3 to resolve, then the cave routine is designed
against R1–R3, then swept against the acceptance tests.** No patch is written
until O1–O3 are answered and the two new metrics exist to test against.

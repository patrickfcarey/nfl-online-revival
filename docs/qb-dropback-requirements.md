# QB dropback — the collision class, and why it may need a wholesale fix

Captured 2026-08-14 from an operator observation. **Not started** — parked while
the Xbox performance work takes priority. This document exists so the reasoning
is not re-derived later.

## The observation (operator, evidence)

> "The right guard still slows down because it runs into the quarterback."

**Correction on record:** this is **pre-existing**, not caused by C1. C1 changed
*which defender* a blocker may target; this is about a blocker's *path* colliding
with a teammate. Different code path (rule 1). C1 may have made it more visible
by sending the puller on a different track, but it did not create it.

## Why this is a CLASS problem, not a play bug (operator's framing)

The QB's post-snap movement crosses the paths of several teammates in the first
second. The puller is the one that shows, but the same geometry catches:

- pulling guards/tackles on power and counter (observed)
- the FB on a lead track through the mesh
- linemen setting back into the pocket
- receivers on shallow crossers behind the LOS
- the ball carrier's own track on misdirection

Fixing the puller alone leaves the rest. The operator's call — fix the dropback
as a whole — is the right scope if the mechanism below turns out to be what we
suspect.

## The load-bearing hypothesis (UNVERIFIED — this gates everything)

**The dropback may be root-motion-driven, in which case the QB cannot yield to
anyone by construction.** This project already proved the equivalent on the
blocking side: during a two-man clip, root motion owns the transform and *nothing
else writes the player's position* — the finding that killed the P8/P9/P10
position-writing arc (`docs/motion-block-cave.md`, `drive-lanes/3-native-drive.md`).

If the dropback is the same shape, then no amount of per-play tuning can make the
QB avoid a teammate, because there is no mechanism through which avoidance could
act. That would explain why this defect is persistent and general rather than
specific to one play.

## THE INVESTIGATION THAT PICKS THE FIX (do this first)

> **Is the QB's dropback root-motion-driven, or steered?**

We already own the tools to answer it: the root-motion converter/applier pair is
`0x0018F9E0` → `0x0018F980`, and the position-writer census method (all writers of
`+0x190`/`+0x194` live during a given state) is established in
`drive-lanes/3-native-drive.md` §1.3.

- **If ROOT MOTION** → the QB is on rails. The fix is to author or blend the
  dropback path so it clears the traffic lanes — a bigger, animation-side job.
- **If STEERED** → he has a velocity we can shape, and the operator's proposed
  fix applies directly and cheaply (below).

Also to map, either way: where the **mesh point** and the QB's **turn rate** are
computed, and whether rotation is separable from travel.

## The operator's proposed fix (applies if the dropback is steered)

> "Maybe the QB needs to turn faster but meet the RB at the same target point, so
> he gets out of the way faster."

**Why the shape is right:** the QB's body occupies the puller's lane *during the
turn*. Front-load the rotation — same mesh point, same handoff timing, rotation
completed sooner — and he vacates the lane earlier while the puller runs clean.
Only the velocity profile inside an unchanged path and timing envelope changes,
so **nothing warps**. It also matches coaching: the QB's first step is taught to
clear the pulling lane, and arriving early to "ride" the mesh is normal.

**Two constraints, or it breaks other things:**
1. **The mesh must not move in space or time.** Handoff timing is load-bearing —
   early and the back is not there. Absorb the slack *before* the mesh, never by
   arriving at it early.
2. **Gate it to plays that pull.** A power/counter QB action differs from a zone
   or play-action one; an ungated change perturbs every dropback, and
   `qb_dropback` is a standing measured metric (7.167 baseline; P11 moved it to
   8.753 — see `block-dominance-requirements.md`).

## Acceptance test

- **Does its job:** on a pulling play, the puller's path is clean — no
  teammate-collision slowdown; measure his speed profile across the pull and show
  no deceleration event at the QB crossing.
- **Breaks nothing:** `qb_dropback` unchanged within noise on non-pulling pass
  plays; handoff timing and success unchanged; no interpenetration (the fix must
  not be mutual no-collide, which hides the symptom unphysically).
- **No warps** (R6): no position snap on either player.

## Status

Parked. Needs the root-motion-vs-steered investigation before any design is
committed. Prerequisite for nothing else; nothing else blocks it.

# Pulling / lead-blocker targeting (open question #5)

Investigated 2026-08-09 against `SLUS_207.52` (Madden NFL 2004). The
report: pulling and lead blockers block the wrong defender, or nobody
useful. This doc records both the mechanism and the **owner's design
intent for the fix**, which the diagnosis shows the shipped code violates.

## Owner's design spec (the acceptance criteria for any fix)

Stated during the investigation, in priority order — this is the target
behavior, not the current behavior:

1. **Objective: land the block** on the correct defender, as often as good
   decision-making allows.
2. **Means (mandatory): run the designed route and retarget correctly
   along it.** Retargeting every few frames is desired and cheap; the goal
   is that the re-pick chooses the *right* defender. The route is the
   primary steering objective — never abandon it to chase.
3. **Correctness is an Awareness probability roll.** On each retarget
   evaluation, roll against effective AWR: high AWR → picks the right
   on-route target; low AWR → mis-reads and honestly misses. This gives
   AWR a genuine decision-quality role for the first time in the engine.
4. **Acceptable failure: honest misses.** When correct retargeting can't
   win the block while staying on-route, a missed block is the correct,
   realistic outcome. **Forbidden:** faking the block via position-snap,
   oversized engage radius, or route abandonment (the warp/magnet behavior
   the project fights everywhere else).

## The one-paragraph diagnosis

Blocking is not run by a route-aware or threat-ranked defender selector.
A **single global per-frame engagement manager** (`0x001f7298`, called from
the gameplay tick, live phases 3/4) pairs a blocker to a defender **purely
on proximity**, holds the pairing for a **block-rating-scaled countdown
(~15–30 frames)**, and while paired **overwrites the blocker's locomotion
command to drive straight at the defender's *current* position, discarding
the route the blocker's state machine was following.** There is no lead/
pursuit term, no route blend, no already-engaged dedup, and Awareness is
never consulted. So the shipped code already does the two things the owner
forbids (overrides the route, aims to snap onto a defender) and does none
of the four things the owner wants.

## Mechanism (evidence)

Blocking is layered on top of the 93-state machine, which drives only the
blocker's *pre-engagement* locomotion (it follows its play-authored pull
path as an unengaged player, engagement kind 1 at `player+0x3E0`).
Everything about who-to-block is the global manager's, keyed by the
engagement kind:

* **kind 4** = blocker approaching an assigned defender (pre-contact); the
  steering pass `0x001f1c20` processes only kind-4 players.
* **kinds 5/6** = mutual block engagement (contact/drive).

**Target pairing is proximity-only.** The manager choosers
(`0x001f00d8`/`0x001f06a0`, frame-parity selected) take a player's
*pre-existing* pursuit target and consummate a mutual engagement when
distance thresholds are met — `SetTarget(self, target, 5)` +
`reverse(target, self, 5)`. No threat ranking, no route, no play-assigned
defender id. (A separate class-2 "block manager" `0x00242070` designates
*which teammate is the lead blocker* by distance to the ball carrier on a
60-frame timer — but that picks the blocker, not the defender he hits.)

**The route override (the core bug under the owner's spec).** In the kind-4
drive, the locomotion command is copied wholesale from the target geometry
into the blocker:

```
001f1de4  lw   v0, 44(s3)      ; bearing derived from the target's CURRENT position
001f1dec  sw   v0, 492(s1)     ; self+0x1EC desired bearing  <- overwrites the route
001f1df0  swc1 f0, 488(s1)     ; self+0x1E8 speed
001f1df8  sw   v0, 496(s1)     ; self+0x1F0 facing
```

Field census of the drive chain resolves every position read to self+0x190
and target+0x190 (current positions) — **no route landmark, and no read of
the target's velocity (no lead)**, so a moving defender is aimed at where
he *is* and over-run, exactly like the runner in `pitch-play-runner.md`.

**Cadence and lock/release.** Kind-4 is stamped with a countdown
`+0x432 = 30 − blockRating/16` frames (block rating = PPBK/PRBK, attrs
11/12; **Awareness is never read**). The release pass `0x001f5b60`
decrements it and, on underflow, `SetTarget(self, 0, 1)` (release to idle),
then re-acquires by the same proximity rule with no distance ceiling on the
approach — so it can re-lock onto a useless far defender ("blocks nobody
useful"). **The blocker already re-evaluates every ~15–30 frames** — so the
owner's "retarget every couple of frames" premise is confirmed as cheap and
already present; only the *criterion* and *route-primacy* are wrong.

**Three proven defects:** (i) target overrides route; (ii) no already-
engaged dedup (two blockers can pair the same defender — the `+0x3E0` read
is only of the iterating player, never the target); (iii) selection is
proximity + a rating timer, never correctness.

## Fix candidates (reframed to the owner's spec)

The cadence is free (already present); the fix is criterion + route-primacy
+ an AWR gate. All are **code caves**, not data edits — the play-assignment
scheme carries no defender-choice weights to retune. All reuse existing
primitives: `FindNearestPlayer 0x001657c0`, `RandomInt 0x002f9428`,
effective AWR at `player+0xB74` (confirmed readable for OL/FB), the `+0x432`
cadence, the `+0x3E0/3E4/0x404` engagement state.

| # | change | where | maps to spec | risk |
|---|---|---|---|---|
| **A** | demote target to a *lean*: stop the wholesale locomotion overwrite (`0x1f1dec/1df0/1df8`), keep the state machine's route bearing, apply target geometry only as a bounded lean | cave replacing 3 stores | means: route primary (#2), forbidden: no override (#4) | Med-High |
| **B** | AWR-gated correct selection: at the re-decision stamp (`0x1ef820`, near `0x1ef8e8–0x1efa38`) add `lh AWR,2932(self); RandomInt(0,255); sltu` → win = accept on-route defender, loss = keep previous/none | cave | correctness roll (#3), honest miss (#4) | Med |
| **C** | on-route selection window: constrain the accepted defender to one on/near the pull path (`FindNearestPlayer` on the defense side, seeded ahead along the route, reject off-route) | cave | correct selection along route (#2) | Med-High |
| **D** | already-engaged dedup: before `SetTarget kind=5` at `0x1f0600`, read `[target+0x3E0]`, skip if already engaged by another blocker | cave at `0x1f0600` | correct selection (#2) | Low-Med |
| **ANTI-GOALS (reject)** | do NOT widen the 3.0 engage radius (`0x1ef9e8`) and do NOT keep the wholesale position-drive at `0x1f1dec` — both "guarantee the block by magnet/override", the warp behavior the owner forbids. The current `0x1f1dec` override IS already the anti-goal. | — | violates #4 | — |

**Feasibility bottom line:** the owner's *"retarget every couple of frames,
pick the right defender, stay on the route, gated by Awareness, honest
misses"* is achievable in this engine's primitives — but it is a **code
cave (A+C+B+D)**, not a data edit, and all cave work is **blocked on the
free-space survey** (not yet done). The cadence itself needs no change.

## Cross-references and toolchain

* Same root shape as `pitch-play-runner.md` (#2) and `zone-bunching.md`
  (#6): steer at a reference's current position, cone/proximity-limited, no
  lead, no separation/dedup. The runner and the blocker over-run the same
  way from opposite sides of the pull.
* Block rating (PPBK/PRBK) drives only cadence, never selection — same
  "ratings are decisiveness, not decision quality" finding as
  `sdchargersfanboy.md`. Fix B would change that for blocking.
* **Correction for the docs:** `SetTarget`'s real entry is `0x001f7398`
  (the jal target of all 45 callers); `0x001f73a0` cited earlier is +8,
  mid-prologue.
* Sixth lane to rebuild the enhanced disassembler in scratch — folding
  REGIMM/MMI/3-op-mult/gp-annotation into `recon/mipsdis.py` is overdue.
* All cave-based fixes across #2/#5/#6/#9 share one blocker: **no
  free-space survey has been done.** That survey is the next enabling step
  for any of these to become real patches.

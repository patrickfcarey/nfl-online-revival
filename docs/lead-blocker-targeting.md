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
5. **Minimum-steps gate before targeting (per play).** Selection must not
   begin until the blocker has traveled a minimum distance/step count
   along its assigned route — ideally authored per play. A too-early
   target is worse than a slightly delayed correct one: engaging before
   the pull develops grabs the first man passed instead of the designed
   defender downfield. The gate withholds *selection* only, not
   locomotion — the blocker keeps running his route during the delay.

   **Verified feasible, cheaply.** The engine keeps a true frames-since-
   snap counter at **`*(0x00601280) + 84`** — a double indirection: `gp−17520`
   holds a *pointer*, and the counter is at +84 off it. (Written here as
   `[0x00601280+84]` in an earlier draft, which would read garbage.) Reset
   at play init `0x001f7034`, incremented at `0x001f5b94`, already used as a gate elsewhere
   (`sltiu` idioms at `0x001f5214`, `0x001f02f4`). The gate: keep the
   blocker at engagement kind 1 until `frames ≥ THRESH` by gating the
   kind-4 stamp in `0x001ef820` (and the kind-5 pairing at `0x001f0600`).
   Because both the steering pass and the choosers are kind-gated, **the
   engagement machinery at `0x001f1dec` never fires during the delay** —
   the blocker keeps running his pull path; selection alone is withheld.
   Per-play authoring of the threshold is feasible via a state-chain
   `p`-param but unconfirmable without a play file; start with a global
   constant. Caveat: a *defender* can still capture him during the window
   (see the hang-up section) — full protection needs the capture
   exemption below.

## What is known, and what is not

This doc had to be rewritten once. The first version told a single tidy
story — "the blocker abandons his route and drives at the defender's
current position" — that rested on misreading one register. An
adversarial pass removed that claim. What follows separates what survived
from what is still open, because the open part is the part the fix spec
depends on.

### Known: target SELECTION is broken (verified twice)

* **Pairing is proximity-only.** The global per-frame engagement manager
  (`0x001f7298`, live phases 3/4) runs choosers `0x001f00d8`/`0x001f06a0`
  (frame-parity selected) which take a player's *pre-existing pursuit
  target* and consummate a mutual engagement once distance thresholds are
  met: `SetTarget(self, target, 5)` + `reverse(target, self, 5)` at
  `0x001f0600`. **No threat ranking, no route awareness, no play-assigned
  defender id.**
* **No already-engaged dedup.** The target's engagement kind
  (`target+0x3E0`) is never read before the pairing is formed — verified
  by field census over both choosers. Two blockers can take the same
  defender.
* **Awareness is never consulted** anywhere in the block-target path.
  Block rating (PPBK/PRBK, attrs 11/12) only sets a timer.
* **Re-selection already happens on a cadence**: kind-4 is stamped with
  `+0x432 = 30 − blockRating/16` frames at `0x001ef820`, so a blocker
  re-decides every ~15–30 frames. The owner's "retarget every couple of
  frames" premise needs no new machinery — only a better criterion.
* **A separate designator picks *which teammate* is the lead blocker**
  (class-2 manager `0x00242070`, by distance to the ball carrier, on a
  60-frame timer). That chooses the blocker, not the defender he hits.

### Open: what actually MOVES an approaching blocker

Engagement kinds: **1** = unengaged (running his play-authored
assignment), **4** = approaching an assigned defender, **5/6** = mutual
contact. The steering pass `0x001f1c20` processes only kind-4 players and
commits three values into the locomotion block:

```
001f1c9c  addiu s3, s1, 992    ; s3 = SELF+0x3E0 -- own engagement record
001f1de4  lw   v0, 44(s3)      ; self+0x40C
001f1dec  sw   v0, 492(s1)     ; self+0x1EC desired bearing
001f1df0  swc1 f0, 488(s1)     ; self+0x1E8 speed   (from self+0x404)
001f1df8  sw   v0, 496(s1)     ; self+0x1F0 facing  (from self+0x410)
001f1e08/0e0c/0e18             ; the SAME three stores into the TARGET (s2)
```

The values are **staged in the blocker's own engagement record**, not
computed here from the target's position, and the identical triple is
written into *both* players alongside `[+0x1F4] = 5` — the signature of a
mutual lock-in as kind goes 4→5, gated by a one-shot flag (`self+0x42E`,
set at `0x001efa5c`).

**So the central question is unanswered: nobody has walked the code that
writes `+0x404/0x40C/0x410`.** Until that is done we cannot say whether
an approaching blocker runs his route, beelines at the defender, or
blends the two — and route primacy is exactly what the owner's spec is
about. *(Investigation in progress; this section will be replaced with
the answer.)*

Consequences while it is open:

* The confirmed defects are **wrong selection** and **no dedup**. Route
  override is a *suspicion*, not a finding.
* **Fix A (demote the target to a lean) is blocked.** Patching those
  three stores today would break the engagement lock-in, not restore
  route primacy.
* Fixes B (AWR-gated selection), C (on-route selection window) and D
  (dedup) are all selection-side and can proceed independently.

### Known: the lock and release cycle

**Cadence and lock/release.** Kind-4 is stamped with a countdown
`+0x432 = 30 − blockRating/16` frames (block rating = PPBK/PRBK, attrs
11/12 — note the attr choice rides a branch-likely at `0x001ef8dc`;
**Awareness is never read**). The release pass `0x001f5b60` decrements it,
gated to the possession team (`0x001f5c7c`), and on underflow
(`0x001f5c90 bgez` — REGIMM, hand-decoded) **promotes rather than
releases**: `SetTarget(self, target, 2)` plus reverse kind 9. *(Corrected
— the first version said it released to idle with `SetTarget(self,0,1)`.
That call exists, but on two other paths: kind 7 after 60 frames at
`0x001f5c34`, and the kind→1 arm of `0x001ef820` at `0x001efa9c`.)*
Re-acquisition then happens by the same proximity rule with no distance
ceiling — so a blocker can end up on a useless far defender ("blocks
nobody useful"). **The blocker already re-evaluates every ~15–30 frames**,
so the owner's "retarget every couple of frames" premise is confirmed as
cheap and already present. The *criterion* is demonstrably wrong;
whether *route primacy* is also violated is the open question above.

**Two proven defects:** no already-engaged dedup (the `+0x3E0` read is
only of the iterating player, never the target), and selection by
proximity + a rating timer rather than correctness. A third — route
override — was claimed in the first version and is now the open question
above.

## Fix candidates (reframed to the owner's spec)

The cadence is free (already present). The fix is the selection
*criterion* plus an AWR gate — and route primacy too, if the open
question turns out to need it. All are **code caves**, not data edits — the play-assignment
scheme carries no defender-choice weights to retune. All reuse existing
primitives: `FindNearestPlayer 0x001657c0`, `RandomInt 0x002f9428`,
effective AWR at `player+0xB74` (confirmed readable for OL/FB), the `+0x432`
cadence, the `+0x3E0/3E4/0x404` engagement state.

| # | change | where | maps to spec | risk |
|---|---|---|---|---|
| **A** | *(BLOCKED — do not implement yet)* demote target to a lean, if a route override is proven to exist. The three stores at `0x1f1dec/1df0/1df8` are the **engagement lock-in**, not a per-frame drive; patching them without first walking the writer of `self+0x404/0x40C/0x410` would break engagement rather than restore route primacy | cave, pending | means: route primary (#2) | **blocked** |
| **B** | AWR-gated correct selection: at the re-decision stamp (`0x1ef820`, near `0x1ef8e8–0x1efa38`) add `lh AWR,2932(self); RandomInt(0,255); sltu` → win = accept on-route defender, loss = keep previous/none | cave | correctness roll (#3), honest miss (#4) | Med |
| **C** | on-route selection window: constrain the accepted defender to one on/near the pull path (`FindNearestPlayer` on the defense side, seeded ahead along the route, reject off-route) | cave | correct selection along route (#2) | Med-High |
| **D** | already-engaged dedup: before `SetTarget kind=5` at `0x1f0600`, read `[target+0x3E0]`, skip if already engaged by another blocker | cave at `0x1f0600` | correct selection (#2) | Low-Med |
| **ANTI-GOALS (reject)** | do NOT widen the 3.0 engage radius (`0x1ef9e8`) and do NOT keep the wholesale position-drive at `0x1f1dec` — both "guarantee the block by magnet/override", the warp behavior the owner forbids. The current `0x1f1dec` override IS already the anti-goal. | — | violates #4 | — |

**Feasibility bottom line:** the owner's *"retarget every couple of frames,
pick the right defender, stay on the route, gated by Awareness, honest
misses"* is achievable in this engine's primitives as a **code cave**, not
a data edit. Caves are now surveyed and proven (`code-caves.md`), and the
minimum-steps gate (#5) has a fully-encoded worked example there. **B, C
and D are ready to design against that cave budget; A is blocked** until
the `+0x404/0x40C/0x410` writer is walked. The cadence itself needs no
change.

## The hang-up: pulling guards stuck on the line (second owner question)

Distinct from wrong targeting. Diagnosis, three mechanisms checked:

* **CONFIRMED CAUSE — captured by a defender, with no escape.** The
  mutual proximity pairing has **no role exemption for a pull-path
  runner** (the engage gate `0x00200130` reads a global flag, not a role;
  `EngageBlock 0x001a6618` checks no assignment byte). A DT whose pursuit
  resolves onto the puller engages him on contact — realistic in itself —
  but **no offensive-line or pull-role state can shed**. *(Corrected: an
  earlier version said `TryShedMove`/`BreakBlockContest` were
  defender-only. They are not — state 30's AIthink calls `TryShedMove`
  at `0x001cb9e8`, and state 30 is installed by the **ball carrier**
  (`0x001df8cc` → `0x001b00a0`, from state 1's AIthink). So an offensive
  player *can* run the shed contest — just never a pulling guard, who has
  no path to state 30.)* The captured puller is therefore passive until
  the engagement timer (`+0x42C`, set mutually by the *defender* at
  `0x001a7320/33c`) expires: **15 or 30 frames (0.25–0.5 s) per capture,
  re-armed on every re-contact** — he can be chained and never reach the
  corner.
* **Teammate body traffic: not an independent cause.** No teammate-
  collision stall exists in the walked locomotion path (consistent with
  Lane K's no-separation finding). But the cheapest lever lives here:
  **the pull path is authored per-play data** — waypoint floats at play
  record +140…171 (via `0x00242848`). Deepening the path to route the
  puller behind the pile is a pure data fix, per play, no warping.
* **The tangle trigger: not a teammate cause.** The two-man interaction
  driver iterates the defense side only; no offense-offense tangle
  exists. The tangle *is* the visible form of the capture lock.
* **Secondary: the pivot.** Reversing ~120–180° at the snap under the
  ~25°/frame turn gate costs ~5–7 frames of arcing through LOS traffic,
  widening the capture window. The turn-rate constant is a lever but
  global (all players).

Hang-up fix levers, in recommended order:

| lever | type | where | risk |
|---|---|---|---|
| **Deepen/widen the pull path per play** | **data** | play record +140…171 waypoints | Low — needs a play-file read to confirm waypoint semantics |
| Capture exemption pre-landmark (skip engaging a pull-role player who hasn't reached his landmark; reuse the frame gate) | code cave | before `0x001a6618` / `0x001f0600` | Med — must lapse once the landmark is reached |
| Offensive escape / shorter lock for pullers (cap at ≤15 frames or add a break-out roll) | code cave | `+0x42C` setter `0x001f7a28` | Med — gate to pull roles |
| Faster pivot | code | turn-rate clamp | Med-High — global |

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

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

## The mechanism (resolved 2026-08-09)

Three passes were needed to get this right. The first told a tidy story
built on a misread register; an adversarial pass removed it; a third
walked the code that was actually missing. The result vindicates the
*community's* description — a blocker really does stop running his route
and drive at a stale axis — while placing it somewhere different from the
original claim.

**First, a taxonomy correction that reframes everything.** This doc used
to say "kind 4 = approaching an assigned defender (pre-contact)". Wrong.
**Kind 4 is contact**: promotion into it requires `distance < 2.1 yd`
(`0x001F7590`, constant at `0x005FF1C0`), the "is engaged" predicate
returns true for kinds 4/5/6, and the kind-4 pass plays a block animation
on both players. **The approach phase is kind 2/3**, and the engagement
manager does not touch a kind-2/3 blocker's locomotion at all.

### Pre-contact (kind 1/2/3): route primacy IS respected

The blocker runs **state 47** (enter `0x001B66A8`, AIthink `0x001B6870`;
state 72 is a wrapper that installs it). Its enter latches the
play-authored direction from the assignment record into the state block
(`0x001B66DC` reads the authored direction byte; `0x001B66F4` stores it
as a BAM bearing), and every frame the steering function `0x001B61A0`:

* **defaults to the authored bearing** — that is its return value when
  nothing else applies;
* looks for a defender in a cone ahead, and if he is not already engaged,
  **leads him by 2× his velocity** (`0x001B6274`–`0x001B6294`);
* converts that only into a **bounded lean off the blocker's own current
  facing**, capped around 60° and scaled by closeness — the aim bearing
  is never used directly;
* **snaps back to the authored route** on two separate clamps: if the
  lean would point more directly at the defender than the route does
  (`0x001B659C`), and if the blocker's motion has drifted more than 90°
  from the authored direction, in which case **speed is zeroed and the
  bearing reset to the route** (`0x001B6AF0`–`0x001B6B10`).

That is a route-with-a-lean, with a genuine velocity lead — close to what
the owner's spec asks for. **The old claim that the target overrides the
route is false for the approach**, and the "no lead term" claim is false
too: there is a lead, and it is *2×*, which over-leads a defender who
cuts.

### On contact (kind 4): route primacy is totally abandoned

At lock-in, `0x001F14D0` computes a bearing/speed/facing triple **once**
and stages it in the blocker's own engagement record
(`+0x404/+0x40C/+0x410`). What it computes is a **mutual shove axis**, not
a chase vector — the facing is the defender→ball-carrier pursuit line
±180°, the bearing is one of {the blocker's own facing, the defender's
bearing, that pursuit line}, and the speed is a rating-contest scalar.
The same values are written symmetrically into *both* players.

Then, every frame until the engagement ends, the kind-4 pass re-stamps
that frozen triple into the blocker's locomotion block:

```
001f2054  lw   v0, 44(s3)      ; SELF+0x40C  (staged at lock-in)
001f2064  sw   v0, 492(s1)     ; SELF+0x1EC  desired bearing
001f2068  swc1 f0, 488(s1)     ; SELF+0x1E8  speed
001f2070  sw   v0, 496(s1)     ; SELF+0x1F0  facing
```

The one-shot flag `+0x42E` gates only the *first* third of the pass; the
locomotion stores are on the per-frame path and there is no route out of
the kind-4 pass that skips them. State 47 keeps computing the route
bearing each frame and it is **discarded** — the manager runs after the
AI loop, so its write wins.

**So the override is real, it lasts 15–30 frames per block "rep", and the
axis it drives is frozen at contact** — the blocker keeps shoving along
the lock-in line after the defender has moved off it. That staleness,
plus the 2× over-lead during the approach, is a sufficient mechanism for
the over-run the community reports, with no current-position tracking
anywhere in the code.

### Still true from the earlier passes

* **Selection is proximity-only** — the pairing takes a pre-existing
  pursuit target and consummates it on distance; no threat ranking, no
  play-assigned defender.
* **No already-engaged dedup** — the target's kind is never read before
  pairing, so two blockers can take the same man.
* **Awareness is never consulted** anywhere in the block path; block
  rating (PPBK/PRBK) only sizes the countdown.
* **Re-selection already runs every ~15–30 frames** (`+0x432 =
  30 − blockRating/16`), and the countdown expiring drops the pair to
  kind 2 + reverse 9 rather than releasing to idle.

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
| **A** | **UNBLOCKED, but the address moved.** Demote the contact override to a lean: the per-frame stores are `0x001F2064` / `0x001F2068` / `0x001F2070` (and the animating arm `0x001F1F64/68/70`, which falls through to them) — **not** `0x1f1dec/1df0/1df8`, which fire only once at lock-in and patching them alone does almost nothing. Blend against the state-47 route bearing the AI think already wrote that frame; it is still sitting in `+0x1EC` when the manager runs | cave | means: route primary (#2) | Med |
| **A2** | Fix the *staleness* instead: recompute the drive axis periodically rather than freezing it at lock-in — `0x001F14D0` is the stager and has three call sites, one behind the `+0x42E` one-shot | cave | means: route primary (#2) | Med |
| **A3** | Reduce the 2× velocity over-lead during the approach (`0x001B627C` / `0x001B6294`) — this is the *approach*-phase contributor to over-running a cutting defender | in-place | means: better pursuit (#1) | Low |
| **B** | AWR-gated correct selection: at the re-decision stamp (`0x1ef820`, near `0x1ef8e8–0x1efa38`) add `lh AWR,2932(self); RandomInt(0,255); sltu` → win = accept on-route defender, loss = keep previous/none | cave | correctness roll (#3), honest miss (#4) | Med |
| **C** | on-route selection window: constrain the accepted defender to one on/near the pull path (`FindNearestPlayer` on the defense side, seeded ahead along the route, reject off-route) | cave | correct selection along route (#2) | Med-High |
| **D** | already-engaged dedup: before `SetTarget kind=5` at `0x1f0600`, read `[target+0x3E0]`, skip if already engaged by another blocker | cave at `0x1f0600` | correct selection (#2) | Low-Med |
| **ANTI-GOALS (reject)** | do NOT widen the 3.0 engage radius (`0x1ef9e8`) and do NOT keep the wholesale position-drive at `0x1f1dec` — both "guarantee the block by magnet/override", the warp behavior the owner forbids. The current `0x1f1dec` override IS already the anti-goal. | — | violates #4 | — |

**Feasibility bottom line:** the owner's *"retarget every couple of frames,
pick the right defender, stay on the route, gated by Awareness, honest
misses"* is achievable in this engine's primitives as a **code cave**, not
a data edit. Caves are surveyed and proven (`code-caves.md`), and the
minimum-steps gate (#5) has a fully-encoded worked example there. **All
of A, B, C and D are now designable**; the cadence itself needs no change.

Note the spec is closer to shipped behaviour than anyone expected: the
*approach* already runs a bounded lean off an authored route with a
velocity lead and two snap-back clamps. The work is (a) fixing what
happens at contact, where the route is dropped for a frozen axis, and
(b) fixing *whom* he picks, which is where the AWR roll and the dedup go.

**Revised rationale for the minimum-steps gate (#5):** it is still sound,
but not because it "withholds selection while the route runs" — the
approach steering was never the problem. Gating the kind-4 stamp at
`0x001EF820` keeps the blocker in state 47, which already runs his route
correctly. It withholds *the override*, not the selection.

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

* Related to `pitch-play-runner.md` (#2) and `zone-bunching.md` (#6),
  but **not the same shape** — an earlier version claimed it was. The
  runner and the zone defender steer at a reference's *current* position
  with no lead. The blocker is the opposite: during the approach he
  *does* lead (2× velocity, which over-leads), and at contact he stops
  tracking the defender altogether in favour of a frozen axis. Three
  different failure modes that happen to look alike on screen.
* Block rating (PPBK/PRBK) drives only cadence, never selection — same
  "ratings are decisiveness, not decision quality" finding as
  `sdchargersfanboy.md`. Fix B would change that for blocking.
* **Correction for the docs:** `SetTarget`'s real entry is `0x001f7398`
  (the jal target of all 45 callers); `0x001f73a0` cited earlier is +8,
  mid-prologue.
* Sixth lane to rebuild the enhanced disassembler in scratch — folding
  REGIMM/MMI/3-op-mult/gp-annotation into `recon/mipsdis.py` is overdue.
* The free-space survey is **done** (`code-caves.md`): ~9.2 KB of dead
  code, one-word `j` reachability from any site, and a worked example
  that happens to be this doc's minimum-steps gate. Every cave still
  needs its "is-it-really-dead" breakpoint test on the rig first.
* **Open here:** the kind 5/6 processing pass was never located (it is
  not `0x001F1C20`, `0x001F20F8`, or `0x001EF820`). Not needed for the
  fixes above, but it is the last unmapped part of the block cycle.

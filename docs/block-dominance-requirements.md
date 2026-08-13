# Block dominance — requirements before any patch

The campaign charter for making blocks *decisive*, agreed 2026-08-12. Four
interlocking requirements emerged in one design session; they share a single
keystone (defender eligibility) and a natural attack order. **Nothing here is
patched until its requirement is settled with a measurable acceptance test**
(rule 3), each patch is isolation-tested before integration (rule 2), and every
address is re-derived against the binary at patch time (rule 4). Requirements
that live in a different code path from the change in flight are captured
separately and marked, never folded in (rule 1).

The double-team fix is **done** (`double-team-requirements.md`, `drive-lanes/`):
P1+P4+P11+N-1+T3, operator-confirmed. This charter is the next ring out — the
same "mass and strength should decide a block" principle, extended to single
blocks, mismatches, and the defenders the engine currently won't let anyone
block.

## The vision (operator, 2026-08-12)

> A large man just needs to get his hands on someone — even one hand — and a
> CB or LB or SS is effectively removed from the play. It's extremely true in
> football.

Three things follow, and they must be built in order because the first gates
the rest.

## Dependency map

```
   ┌─ C1  ELIGIBILITY (state filter @ 0x001F2D60)   ← KEYSTONE, highest blast radius
   │        a coverage CB/LB/SS is not a legal block target for anyone
   │
   ├──▶ GRAV   gravity: mass+strength-scaled capture radius + a small warp
   │              ("one hand and you're removed") — needs a target it may engage
   ├──▶ BOS    big-on-small guaranteed skates-or-pancake (310 lb vs 185 CB)
   │              — needs to be able to engage the small man
   └──▶ LB     lead-blocker targeting (#5): lower radius / delay before target
                  — the delay form is a *natural partial C1* (a blitzer self-promotes
                    into an eligible state), so it is also the cheapest first cut
```

GRAV, BOS, and the LB corner case are **inert until the target is eligible**.
C1 is therefore first — and, because it is global, also the riskiest.

---

## C1 — Eligibility: the right defenders must be legal block targets

**Verified this session.** The block-target pool filter at `0x001F2D60` admits
a defender only if his `ai_state` ∈ {2 pursuit, 30 rush/engaged, 51 authored
wait} or he is human-controlled (flag `0x4000` at `+0xC`). The state tests are
quoted:

```
001F2D60  beq  s6, zero, 0x001f2da0    ; C1-full hook (force this branch = admit all)
001F2D68  lw   v0, 0xC(a2)             ; defender flags
001F2D6C  andi v0, v0, 0x4000          ; human-controlled -> admit
001F2D78  lw   v0, 0x2FC(a2)           ; -> ai_state object
001F2D80  lbu  a0, 0(v0)               ; a0 = ai_state
001F2D84  beq  a0, 2  -> admit
001F2D8C  beq  a0, 30 -> admit
001F2D94  bne  a0, 51 -> 0x001f2e30    ; anything else: REJECT
```

Every other state — a corner in man/zone coverage, a two-deep safety, a
dropping zone LB — is rejected. **This is why the pulling guard chases a DT and
the blitzing corner goes free (`lead-blocker-requirements.md`), and why GRAV and
BOS cannot touch a coverage DB.** It is the keystone.

**Rule.** A defender who is *relevant to the play near a blocker* must be an
eligible block target, even from a coverage state. **Not** every defender
everywhere — a deep-third corner 25 yd away must stay unblockable.

**Why not "all qualify" globally.** The filter is global: it feeds every
blocker on every play. Flipping it unconditionally (the documented C1-full:
`beq s6,zero` → `beq zero,zero` = `0x1000000f` at `0x001F2D60`) also lets all
five linemen chase DBs downfield, abandon the pocket, and draw ineligible-man-
downfield. The filter exists for a reason. Naive "everyone blockable, always"
is not football — so C1 must be **scoped to relevance** (C1b), not global.

**Two forms — Need-decision (operator's):**
- **C1-full** — one word at `0x001F2D60`; all defenders eligible. Simplest,
  highest risk.
- **C1b (recommended)** — admit a coverage defender only when relevant: within
  the blocker's assignment area / inside the tackle box / within N yd of the
  ball. A relevance test in a cave, gated. Bounded blast radius.

**Good news that shrinks the job:** many defenders we actually want blockable
are *already* eligible. A blitzing CB and a run-fit LB/SS coming downhill are
often already in state 2/30. **Measure which state the specific target defenders
occupy before widening anything** — the real gap may be much narrower than "all
coverage."

**Acceptance test.**
- *Does its job:* new metric `target_admitted` — on a play where a CB/LB/SS is
  in the run fit / near a blocker but in a coverage state, that defender appears
  in the block-target pool (the finder returns him, or he passes `0x001F2D60`).
- *Breaks nothing (the load-bearing arm):* new metric `linemen_downfield` =
  frames any of the five linemen is engaged with a defender outside the tackle
  box / > X yд downfield. Must stay ~0. And `pool_blockers` + the linemen's
  `block_mode`/engagement distributions unchanged from baseline (R7). Every row
  of the "situations this must not break" table (`lead-blocker-requirements.md`)
  on its own savestate.

**Mechanism.** Filter at `0x001F2D60` (verified). C1b needs a relevance test
(distance-to-ball / in-box) in a cave; C1-full is the one-word branch force.

**Status.** BLOCKED on: the C1-full-vs-C1b decision; a measurement of which
states the wanted defenders actually occupy; and a savestate where an in-box
coverage defender should be blockable but isn't. **This is the highest-blast-
radius change in the whole project — it gets the heaviest regression pass.**

### C1 DIAGNOSTIC RESULT (2026-08-12): CONFIRMED — eligibility was the whole wall

C1-full deployed in isolation (`patches/14F8B841.c1-eligibility-diag.pnach`,
one word `001F2D60 = 1000000F`) on the rig. Operator, live play, first try:

> "the Right guard pulling is now covering the CB/LB/or RT as need fit. it's
> actually working as you would hope."

**The pulling guard's targeting logic was never broken — the correct man was
invisible to it.** Making every defender eligible let the existing finder pick
the right block. This confirms the lead-blocker diagnosis
(`lead-blocker-requirements.md`) and the eligibility keystone at one stroke.

**It also uncovered the next layer, not created it.** The operator saw WRs now
*attempt* CB blocks (previously impossible — the CB was ineligible, issue #15)
and *execute them badly* — poor lead, bad angle, the CB slips past. The defect
moved downstream from "can't select the man" to "blocks him poorly." Captured
as WR below, a separate code path (rule 1).

**OPEN — the blast-radius question that decides C1-full vs C1b:** whether the
interior five linemen chase DBs downfield / abandon the pocket under C1-full.
Not yet reported by the operator; if absent, C1-full is closer to shippable
than predicted and C1b may be unnecessary. Awaiting operator eyes on the O-line.

### C1 BLAST-RADIUS RESULT (2026-08-12): the predicted regression did NOT occur

Operator, live play:
> "the o line is not 'chasing' dbs, they are just rightfully coming into their
> list of targets now correctly. i saw a wr block a cb, and then when the cb was
> breaking free of the block the pulling guard then arrived and finished the
> block. it was actually amazing."

**The "linemen chase DBs downfield" prediction was WRONG** (agent's, refuted by
operator eyes). C1-full produced emergent, correct *team* blocking — a WR and a
puller combining on one corner — not chaos.

**Mechanism, verified this session — why C1-full is self-scoping:** the
target-finder `0x001FEB98` is a **nearest-in-cone** search, not "pick any
eligible man". It seeds best-distance with a max sentinel (`[0x005ff248] =
32767.0`), calls the cone test (`jal 0x001fec78`), and keeps the **minimum**
distance candidate (`c.lt.s f1,f0` / `bc1fl`); a hard local corridor gate
(`0x004AE048`, prior review) bounds it further. So a blocker always takes the
CLOSEST in-cone defender — for an interior lineman that is the DT/LB in his
face, never a deep DB. **Eligibility was the ONLY over-exclusion; the distance +
cone gates already supply the relevance C1b was meant to add.** A DB wins
"closest" only for a blocker actually near one (a WR; a puller at the 2nd
level) — exactly the observed layered block.

**Consequence: C1-full may be shippable and C1b may be unnecessary.** NOT yet
promoted to a finding — this is a handful of plays, not a proven negative
([[sample-paths-not-endpoints]]). To promote: a regression sweep
(`linemen_downfield` metric + the must-not-break table across play types),
watching the residual misfire cases — **pass pro** (a lineman leaving a rushing
DL for a closer coverage DB drifting into the backfield; blitz pickup is
correct, abandoning a rusher is the bug), **screens**, and **goal line**. If it
stays clean, C1-full ships as-is and WR execution becomes the main event.

---

## GRAV — Gravity: a mass+strength-scaled capture, and a small intentional warp

**A deliberate, scoped exception to the project's no-warp rule** (`R6 — honest
misses, never warps`), introduced by the operator 2026-08-12, for extreme
mismatch **only**. Even matchups still miss honestly; only the lopsided case
gets the assist. The no-warp principle survives everywhere it mattered.

**Rule.** The larger the blocker's advantage in **both size and strength**, the
larger his engagement *capture radius* (he latches from further — "one hand is
enough"), plus a **small one-shot position nudge at latch** to close the visual
gap. Magnitude scales with the mismatch; it is a nudge, never a teleport.

**Why.** Stock contact is a **binary `< 2.1 yд` distance test** that flips the
engagement kind to 4 (`block-cycle.md` cause #1) — no mass, no collision. A DB
who holds 2.2 yд never gets engaged and slips every block. A 320 lb man should
latch onto a 185 CB from further and it should stick. Football-true.

**Two levers, both mismatch-gated:**
1. **Capture radius scaled by mismatch** — the `2.1 yд` contact threshold →
   `2.1 × f(mass_ratio, str_ratio)`. Mostly a gated change on the contact test.
2. **Position warp at latch** — small (≤ ~0.5 yд), **one-shot** (latching, which
   is the mechanism that *works* — accumulating per-frame writes starved
   P8/P9/P10, `motion-block-cave.md`), scaled by mismatch. This is the actual
   "warp" the operator is authorizing.

**Acceptance test.**
- *Does its job:* new metric `engage_separation` = blocker→defender distance at
  the frame kind flips to 4. Rises with mismatch (≈2.1 stock → ≈3–4 yд at 1.68×
  mass). And `defender_slipped_unblocked` (the mismatched defender reaches the
  carrier/backfield untouched) drops toward 0.
- *Stays "small":* `blocker_warp_max` = largest single-frame position jump of
  the blocker **≤ 0.5 yд**. A teleport fails this.
- *Breaks nothing:* matched pair (mass_ratio < ~1.2) → `engage_separation` and
  warp byte-identical to stock. Gate on **both** ratios (a big-but-weak or
  strong-but-small man gets little/none).

**Mechanism.** The contact-distance test (`block-cycle.md` cause #1, ~2.1 yд —
**EXACT SITE TO BE DERIVED**; the kind machine is `0x001ef820`) + a latching
position writer (the audited body, `motion-block-cave.md §6.2`). Weight `+0xAEC`,
STR `+0xB8E` (idx 15, both verified in N-1).

**Status.** Design. Blocked on: **C1** (a coverage-DB target to engage); the
exact contact-test site; a mismatch savestate.

---

## BOS — Big-on-small: guaranteed skates or pancake

The operator's requirement: a 310 lb man on a 185 CB is **guaranteed** to put
him on skates or pancake him. Different code path from the double team — a
**single** block has no helper, so N-1 (gated on `dt_role==2`) never fires for
it. This is the big-on-small S-series (`on-skates-requirements.md` S4-D).

**Why it isn't already automatic — verified.** The native shed contest scores
`(PPBK|PRBK) + STR/3 + move terms` (`drive-lanes §1.2`) — **STR, not weight**.
A 310 lb man's *mass* never enters whether he wins; only his STR does. So a
stock mismatch is a coin-flip on the STR roll.

**The drive is native and reachable — verified.** N-1 measured the doubled DE
driven back **+2.09 yд off his logical position** (`double_team.py:92`,
`+0x190/+0x194`). "Skates" (upright back-pedal) = the drive grid cells
**{50,53,54,58}**; "pancake" (to the ground) = **{56,149,168}**; the winning
**margin** picks which. The machinery was always the engine's — BOS just makes
the big man *earn* it.

**Rule.** On a single block where the blocker outweighs the defender by ≥ ~1.5×,
fold the mass differential into the contest comps so he wins decisively, tuned
so the outcome centers in the skates-or-pancake band; short-circuit above the
ratio threshold for the *guarantee*.

**Acceptance test.**
- *Does its job:* the pair resolves into the drive-or-pancake family (skates
  {50,53,54,58} or pancake {56,149,168}); defender **dy ≥ +1.0 yд** backward;
  ~100% of reps across a seed sweep.
- *Breaks nothing:* a matched pair (ratio < 1.2) byte-identical; slots 6/7
  unchanged.
- *Gate:* fires only when mass_ratio > ~1.5 (a finetune knob like `k`).

**Mechanism.** A mass-ratio-gated fold at the lock-in `0x001f0c40` — a **sibling
of N-1** — into comps `+0x414/+0x418/+0x41C`; margin tuned by the same kind of
`k` scale as T3. Weight `+0xAEC`. No new displacement host needed (the +2.09
proves the native route moves the man).

**Status.** Mechanism known (N-1 sibling). Blocked on: a mismatch savestate; and
**C1** *only if* the small defender is a coverage DB — for an already-eligible
small defender (a blitzing DB, a run-fit LB in state 2), BOS works without C1.

---

## LB — Lead-blocker targeting (#5): lower radius / delay before targeting

Fully spec'd in **`lead-blocker-requirements.md`** (R1–R8) and the operator's
delay-gate hypothesis (`lead-blocker-investigation-state` memory). Captured here
only for its place in the dependency map and its two operator levers this
session:

- **Lower the targeting radius** = the **range `< 3.5`** admission gate
  (`0x004AE048`) — one of *three* filters on a candidate (cone ±60°
  `0x001B61A4/AC`; range `< 3.5`; lateral corridor `< 1.0`). A data tunable.
  *(Re-verify these sites — from the 2026-08-11 hostile review.)*
- **1–2 steps before targeting** = the operator's own best-fix hypothesis: a
  minimum-travel gate. **This is a natural partial C1** — a blitzing corner
  self-promotes from coverage (ineligible) into rush state 30 (eligible) on his
  own, so waiting a step makes the right man legal *without touching the global
  filter*. That makes the delay gate the **cheapest, lowest-risk first cut** of
  the whole charter.

**Acceptance test (existing).** `carrier_yards` up from the ~0.17 corrected
baseline; `lead_blocker_target_is_second_level` ≥ 80%; `lead_blocker_backward_targets`
= 0; per-play-type landmark depths; R7 no regression.

**Cheapest next step (already teed up).** Read the blitzing CB's `ai_state` at
tick 82 vs 100/120/140 on an existing capture — if it flips into 2/30, the delay
gate alone may fix the measured play and prove the partial-C1 idea. Then sweep N.

---

## WR — receiver / perimeter block execution (uncovered by the C1 diagnostic)

**A separate code path from the pulling-guard lead block (rule 1).** Surfaced
2026-08-12 the moment C1 made coverage DBs eligible: WRs now *attempt* to block
corners and *do it badly*.

**Operator observation (evidence, not yet diagnosed):**
> "the wide receivers ... are bad at targetting because of route choices and all
> kinds of things ... they definitely are leading poorly. They are letting CBs
> slip by them a LOT."

**What it is / is not.** NOT an eligibility defect — C1 fixed that; the WR can
now pick the corner. It is an *execution* defect: target choice, lead/approach
angle, and sustain. The operator names ≥2 sub-defects (route/positioning; poor
lead) — treat as distinct until measured.

**Rule (provisional, pending measurement).** A WR/slot assigned to block a
perimeter defender should lead him (get playside/between the defender and the
ball) and sustain, not let him cross face and slip to the carrier.

**Acceptance test (to build).** New metrics on the receiver block: `wr_block_
target` (does he pick the play-relevant defender), `wr_leverage` (is he on the
playside shoulder at contact), `wr_defender_crossface` (frames the blocked
defender gets to the carrier side of the WR — should trend to 0). Thresholds
after a baseline capture.

**Mechanism (unknown — to trace).** Likely the shared target-finder
(`0x001FEB98`) + cone/range gates, but the WR block state and its route
interaction are unconfirmed. `fb-wr-blocking.md` (#15) is the entry point. **Do
not patch until the WR block path is traced and a savestate is measured**
(rules 1, 3, 4).

**Status.** Requirement stub only. Needs: a savestate of a play with a WR
perimeter block (screen / WR-side run / bubble); a trace of the WR block state;
the three metrics. Unmeasured — nothing here is a finding yet.

## MOM — momentum / closing-speed on the initial hit

Operator, 2026-08-12: the initial hit of a fullback running full speed should be
more powerful against a stationary defender. A third sibling of N-1 / BOS — same
lock-in hook, same "fold a term into the comps" mechanism — but the folded input
is **momentum**, not mass.

**Finding (verified this session): the block contest is blind to velocity.**
- Lock-in contest `0x001f0c40` reads none of the kinematic fields (no speed, no
  velocity, no position).
- `BreakBlockContest 0x001a66f8` reads move terms (`+0x10/+0x14`), weight
  (`+0xAEC`), STR, and a leverage constant — **no velocity term**.
So a sprinting FB and a standing FB produce the identical contest. Momentum is
unmodeled; the requirement is real and addressable.

**Rule.** A blocker arriving at speed into a stationary defender wins the initial
contest decisively, scaled by closing speed. Gate: blocker speed high AND
defender speed ≈ 0 (a standing blocker gets nothing = stock behaviour).

**Acceptance test.** On an FB iso / lead dive: defender dy-backward at the
initial contact scales with the FB's closing speed at impact; a slow-approach FB
and a matched-speed pair are byte-identical to stock. Metric: `impact_closing_
speed` (blocker speed the frame before the kind-4 flip) vs resulting outcome cell
/ defender dy.

**Mechanism.** Fold a momentum term (closing_speed × mass, or a closing-speed
bonus) into comp1 at the lock-in — the N-1 cave pattern, gated on defender speed
≈ 0. Blocker speed field: `+0x1E8` (commanded) is the clean proxy; the exact
actual-velocity field AT CONTACT is **TO-DERIVE** (deploy-time read on a live
FB). **Timing subtlety — load-bearing:** the contest fires on the contact frame,
which is the same frame the engine overwrites locomotion (kind-4 flip), so the
speed must be read BEFORE it is zeroed. That timing is the whole point of
"*initial* hit".

**Scope fork (unresolved) — which "hit":**
- FB as **lead blocker** hitting a defender → the block path above.
- FB as **ball carrier** trucking a tackler at speed → a DIFFERENT module (the
  tackle/truck path, `0x001869ec` region), not the block contest. Do not conflate.

**Status.** Requirement stub. Blocked on: the scope fork; the actual-velocity
field at contact; a savestate of an FB hitting a stationary LB at speed.

## Attack order

1. **LB delay gate** — cheapest, lowest-risk, and it validates the partial-C1
   idea (a blitzer self-promotes to eligible). Run the `ai_state`-at-tick check
   first; it may fix the one measured play with no global change.
2. **BOS** — for already-eligible small defenders it needs no C1; it is the
   clean "mismatch is decisive" proof, and a direct N-1 sibling.
3. **C1 (eligibility)** — the keystone for coverage-DB targets. Decide
   full-vs-scoped; measure the target states first; heaviest regression pass.
4. **GRAV** — capture radius + small warp, once C1 gives it a legal target and
   the contact-test site is derived.

## Testing discipline (rules 1 & 2)

- Each patch **isolation-tested** before integration: it must move its own
  acceptance metric *and* leave the regression surface unchanged, every
  must-not-break case on its own savestate. Only then combined and run against
  the full suite + `tests/test_madden_lab_*.py`.
- **Unmeasured defaults to stock, never to a guess.** C1b's scheme rows, GRAV's
  non-mismatch path, BOS's matched-pair path all default to shipped behaviour,
  so nothing unmeasured can regress.
- C1 gets the heaviest pass because it is the only global change here.

## Savestates needed from the operator

| for | state |
|---|---|
| BOS, GRAV | a big OL singly blocking a ~185 CB (screen, or a formation where they meet 1-on-1) |
| LB | the specific play(s) where lead blockers went useless (you were watching one) |
| C1 blast-radius / O4 | a **screen** and a **power/kick-out** savestate (do releasing/kick-out blockers use state 47?) |
| C1 measurement | a play where an in-box coverage LB/SS *should* be blockable but isn't |

## New harness metrics implied

`target_admitted`, `linemen_downfield` (C1); `engage_separation`,
`defender_slipped_unblocked`, `blocker_warp_max` (GRAV);
`lead_blocker_backward_targets`, `lead_blocker_target_is_second_level` (LB,
already pending). BOS reuses the double-team drive metrics.

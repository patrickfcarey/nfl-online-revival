# Runners over-run their blocks on pitch plays (open question #2)

Investigated 2026-08-09 against `SLUS_207.52` (Madden NFL 2004). The
report: ball-carriers over-run their blocks on pitch/toss plays, seemingly
without "vision" of where their blockers are.

## The one-paragraph answer

The ledger hypothesis — "runners follow a path and never read blocks" — is
**refuted.** The AI ball-carrier genuinely reads blocks: it scans its own
team for a lead blocker, finds the nearest threatening defender in a
forward cone, follows that defender's engagement link to the blocker
assigned to him, and steers toward the *gap between defender and blocker*.
The vision is real. The over-run is two separate weaknesses: (a) the
field of view is a **forward cone** (75° for threats, 45° to actually
steer at the gap), so the laterally-developing traffic of a sweep falls
outside it; and (b) the **speed governor is weak and conditionally
gated** — the carrier runs at full speed 1.0 by default and only
throttles once a lead blocker is latched, but the latch routine bails
entirely unless the *carrier* is within 8 yards of the ball spot
laterally. On a pitch he leaves that band almost at once, so the scan
never runs, nothing latches, and he blows past blocks still developing
wide.

## The carrier AI state

**State 1 is the ball-carrier "run with the ball" state** — the single
carrier state for both handoffs and pitches. Enter `0x001dfd20`, AIthink
`0x001dfeb8` (CPU runner), USERthink `0x001e0208` (human runner). It's
reached from the pre-carrier route/chase states (31 route-runner, 33
ball-chase/pitch-receiver) which install state id 1 and fire the
"got the ball" event `0x0012a540` on gaining possession.

Crucially, **state 1's enter reads no play-authored path** (no play-record
calls) — once a player carries the ball, steering is fully reactive,
recomputed every frame. There is no "designed run path" the carrier
follows; the only play-authored steering happens *before* the catch, in
states 31/33.

## Do runners read blocks? Field census says YES

Every player-object read in state 1's AIthink and its helpers resolves to
one of four roles — and unlike the zone-defender census (self/receiver/
carrier only), this one includes **teammates and the block-assignment
link**:

| role | source | use |
|---|---|---|
| self (carrier) | `s0`/`s1` | own position/heading |
| a defender | `0x001feb98` cone-nearest threat | threat to avoid |
| that defender's blocker | `defender+0x3E4` engagement link (`0x0013b798`) | gap-midpoint target |
| teammate lead blocker | own-side roster scan `0x001df2d8` | blocker to follow + speed |
| ball / LOS | `0x00260208` / `0x00200260` | downfield reference |

So the carrier reads teammate positions, teammate engagement state, and
the defender→blocker assignment link. It is not blind and it is not
following a landmark. The block-reading helpers are called from the
AIthink (the human's USERthink calls only controller-read helpers), so
tuning them affects the CPU runner — with one qualifier: `0x001df2d8`
also runs from state-1 *enter*, which executes for the human carrier too.
Its result is only *consumed* by the AIthink-only follow routine, so the
practical effect is nil.

## Steering chain (state 1 AIthink, per frame)

1. **Primary downfield bearing** (`0x001e0008` → `0x001dd018`): atan2 aim
   biased downfield off the LOS/ball spot and own X.
2. **Path/obstacle override** (`0x001fcf58`).
3. **Target selection** (`0x001dd420`): nearest threatening defender in a
   **75° forward cone** (`0x00355555` via `0x001feb98`); if that defender
   is engaged, dereference its blocker and compute the **gap midpoint**
   between them.
4. **Steer-to-gap** (`0x001dd9d0`): applies only within a **45° gate**
   (`0x1FFFFF`) and a 2.0-yard distance gate; a `rand(100)` roll can drop
   it (`movn s4,zero,v0` — the follow is probabilistic).
5. **Lead-blocker latch** (`0x001df2d8`): **the 8.0 window is a
   precondition on the *carrier*, not on the teammate** — the function
   bails before it looks at anyone unless the carrier is within 8.0 of
   the ball spot laterally (`0x001df370`, comparing spot X against
   `self+0x190`) and more than 4.0 from it in Y (`0x001df330`). Only then
   does it scan the 11 own-side players for: engagement kind ∈{1,2} or
   role byte `[+0xBCC]==47`; ≥2.25 behind the spot in Y; >1.0 ahead of
   the carrier in Y; heading within **60°** (`0x002AAAA9`).
6. **Lead-blocker follow / speed** (`0x001df148`): if latched, throttle
   speed (factor 5.6422 at `0x005fee40`, 0.75 cap).
7. **Commit**: bearing → `player+0x1F0`, speed → `player+0x1E8`.

**Pitch-specific:** the toss has a two-phase life the handoff lacks —
phase A (state 33) chases the pitch until distance-to-ball < 3.0 with ball
state 5, running laterally at speed 1.0; phase B (flip to state 1)
immediately aims downfield at that carried speed 1.0 — from a position
already outside the 8-yard band that the latch routine requires. That is
the re-target-too-early moment, and it is why the pitch is worse than the
handoff.

## The over-run mechanism

1. Base speed is 1.0 and stays there (`0x001dfdc8`, `lui 0x3f80`); no
   unconditional "slow as blocks develop" term.
2. The only slowdown is lead-blocker-follow, gated behind that latch —
   and **on a pitch the carrier himself leaves the 8.0-yard band almost
   immediately**, so the scan never even iterates: no latch → no slowdown
   → full speed. (An earlier version of this doc said the *blockers*
   started outside the window. The correction makes the mechanism
   stronger, not weaker, and N1 remains the right lever — for this reason
   instead of the stated one.)
3. Defender avoidance is **cone-limited** (75° threat cone, 45° steer
   gate) — the sweep alley's lateral defenders/blocks fall outside both.
4. On the pitch re-target, bearing snaps downfield at speed 1.0 before
   blocks are in front. No arrival-radius / stop-and-cut waypoint exists
   on the carrier target (unlike zone states' 1.0-yard check).

## Fix candidates (AI-runner-only unless noted)

| # | lever | address | current → patched | effect | risk |
|---|---|---|---|---|---|
| **N1 (rec)** | carrier-position gate 8.0 → 16.0 | `0x001df370` | `3C014100` → `3C014180` | the carrier stays "eligible" further outside the hashes, so the lead-blocker scan still runs on a sweep → follow-slowdown engages | Low |
| N5 (rec) | follow speed factor 5.6422 → ~2.5 | `0x005FEE40` (data) | pool word | harder throttle behind a latched blocker | Low |
| N2 | lead-blocker heading tol 60° → 90° | `~0x001df45c` (2-word) | verify pair | latches blockers not already aimed at | Low-med |
| N3 | defender cone 75° → ~110° | `0x001dd4a0` + `0x001dd4b4` (20 B apart) | verified pair | react to lateral pursuit on sweeps | Med |
| N4 | steer-to-gap gate 45° → ~70° | `0x001dda2c` + `0x001dda30` (`0x001dda34` is the `slt`) | verified pair | steers to off-axis gaps | Med |
| N6 | follow slowdown 0.75 → 0.55 | `0x001df1c4` | `3C013F40` → `3C013F0C` | stronger brake | Low |
| N7 | carrier base speed 1.0 | `0x001dfd98` | — | **shared — also slows the HUMAN carrier; avoid** | High |

**Recommended minimal set: N1 + N5** — one code word, one data word, both
AI-only, directly targeting the over-run (see wide blocks + pace behind
them) with no effect on the human runner.

**Data vs code:** a "block-aware target" is a **code** change — the carrier
reads no play data once carrying, so you cannot author a smarter path as
play-data (the only data lever is N5's float pool word). Re-authoring the
pre-carrier states 31/33 could change *where* the runner catches, not how
he reads blocks after.

## Caveats / open

* ~~`0x00260208` unconfirmed~~ — **confirmed** by the verification pass:
  it loads `[gp−14244]` then `lwu +12` (X) / `lwu +16` (Y), returned
  packed in one 64-bit register (callers split with `dsll32`/`dsrl32`).
* N3/N4 pairs are now located exactly (see the table); N2 still needs its
  pair verified in place. N1/N5/N6 are single-word and ready.
* **`0x001df2d8` is not AI-only**: it also runs from state-1 *enter*
  (`0x001dfe4c`), which executes for the human carrier too. Practical
  impact is nil — the latch is only *consumed* by `0x001df148`, which is
  AIthink-only — but "no effect on the human" needs that qualifier.
* Whether specific pitch plays route the RB through a different
  pre-carrier chain is play-file data (none in repo) — doesn't change the
  carrier state (always 1) or its block-reading.

## Relation to #5 (pulling/lead-blocker targeting)

This is the *runner's* side of the same field geometry Lane O is mapping
from the *blocker's* side. Both over-run for the same root reason —
aiming at a target's current position with cone/window-limited perception
and no lead — so the fixes are complementary: N widens the runner's view
of his blocks; #5 fixes whom the blocker picks and how it tracks.

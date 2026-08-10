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
field of view is a **forward cone** (75° for threats, plus an 8-yard-wide
lateral window for lead blockers), so the wide, laterally-developing
blocks of a sweep/pitch fall outside it; and (b) the **speed governor is
weak and conditional** — the carrier runs at full speed 1.0 by default and
only throttles once a lead blocker latches into that narrow 8-yard window,
which on a pitch happens too late (or never), so he blows past blocks that
are still developing wide.

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
following a landmark. All block-reading helpers are called *only* from the
AIthink, so tuning them affects the CPU runner without touching the human
runner (whose USERthink calls only controller-read helpers).

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
5. **Lead-blocker latch** (`0x001df2d8`): own-team scan for a teammate
   ahead in Y, within **|X| < 8.0** (`lui 0x4100`), heading-aligned within
   **60°**.
6. **Lead-blocker follow / speed** (`0x001df148`): if latched, throttle
   speed (factor 5.6422 at `0x005fee40`, 0.75 cap).
7. **Commit**: bearing → `player+0x1F0`, speed → `player+0x1E8`.

**Pitch-specific:** the toss has a two-phase life the handoff lacks —
phase A (state 33) chases the pitch until distance-to-ball < 3.0 with ball
state 5, running laterally at speed 1.0; phase B (flip to state 1)
immediately aims downfield at that carried speed 1.0, *before* the wide
lead blockers cross into the 8-yard latch window. That's the re-target-
too-early moment.

## The over-run mechanism

1. Base speed is 1.0 and stays there (`0x001dfdc8`, `lui 0x3f80`); no
   unconditional "slow as blocks develop" term.
2. The only slowdown is lead-blocker-follow, gated behind the **|X| < 8.0**
   latch — a pitch's wide blockers start outside it, so no latch → no
   slowdown → full speed.
3. Defender avoidance is **cone-limited** (75° threat cone, 45° steer
   gate) — the sweep alley's lateral defenders/blocks fall outside both.
4. On the pitch re-target, bearing snaps downfield at speed 1.0 before
   blocks are in front. No arrival-radius / stop-and-cut waypoint exists
   on the carrier target (unlike zone states' 1.0-yard check).

## Fix candidates (AI-runner-only unless noted)

| # | lever | address | current → patched | effect | risk |
|---|---|---|---|---|---|
| **N1 (rec)** | lead-blocker X window 8.0 → 16.0 | `0x001df370` | `3C014100` → `3C014180` | wide pitch blockers latch → follow-slowdown engages | Low |
| N5 (rec) | follow speed factor 5.6422 → ~2.5 | `0x005FEE40` (data) | pool word | harder throttle behind a latched blocker | Low |
| N2 | lead-blocker heading tol 60° → 90° | `~0x001df45c` (2-word) | verify pair | latches blockers not already aimed at | Low-med |
| N3 | defender cone 75° → ~110° | `~0x001dd4a0` (2-word) | verify pair | react to lateral pursuit on sweeps | Med |
| N4 | steer-to-gap gate 45° → ~70° | `0x001dda34` (2-word) | verify pair | steers to off-axis gaps | Med |
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

* `0x00260208` (the downfield/LOS reference) was identified by usage as
  the ball-spot/LOS accessor but not fully disassembled — worth a 10-min
  confirm before N-series tuning, as it defines the base downfield vector.
* N2/N3/N4 need their `lui`+`ori` pairs verified in place before patching
  (immediates and addresses given; N1/N5/N6 are single-word and ready).
* Whether specific pitch plays route the RB through a different
  pre-carrier chain is play-file data (none in repo) — doesn't change the
  carrier state (always 1) or its block-reading.

## Relation to #5 (pulling/lead-blocker targeting)

This is the *runner's* side of the same field geometry Lane O is mapping
from the *blocker's* side. Both over-run for the same root reason —
aiming at a target's current position with cone/window-limited perception
and no lead — so the fixes are complementary: N widens the runner's view
of his blocks; #5 fixes whom the blocker picks and how it tracks.

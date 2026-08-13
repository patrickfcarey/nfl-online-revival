# WR block measurement — the objective A/B for W1

Built 2026-08-12 because the eyeball A/B on W1 (repoint the WR pairing-arm slot
`0x00583868` from the do-nothing default `0x001F3848` to the TE/tackle arm
`0x001F3518`) came back **indistinguishable** (`block-dominance-requirements.md`,
W1 RESULT). Eyes can't resolve it amid play variance, so we measure. This doc is
the spec; the harness (`experiments/wr_block.py`, adapted from `double_team.py`)
is built against it once the savestate exists.

## What we are trying to settle

1. **Does W1 do anything?** OFF (default arm) vs ON (TE arm), is there a
   measurable difference in whether/how the WR blocks the CB?
2. **Quantify the bad-angle defect** the A/B surfaced — even when the WR
   commits, the approach angle is poor. Is that better/worse/unchanged under W1?
3. **Decision:** if W1 helps → ship it. If W1 is null or the angle stays bad →
   the TE arm is the wrong shape for a WR in space, and the fix is a **bespoke
   WR arm (cave)**, not arm-reuse.

## The play — formation vs defense (isolate ONE WR-vs-CB block)

The measurement is only as clean as the matchup. Requirements, in priority:

1. **The WR must be ASSIGNED to block, not run a route.** Some run plays author
   the WR a decoy route (confirmed this session). Use the sweep/toss the operator
   already saw him block on — a play where the WR stalk/seals the play-side
   corner.
2. **Split the WR wide and run to his side** — isolates his block on the
   perimeter, away from box traffic, so the harness reads one pairing.
3. **The CB starts NEAR the WR and in a run-supportable state.** Best:
   **press man** (CB jammed on the WR at the snap → immediate, clean
   engagement, minimal travel-to-contact confound). Alternative: **Cover 2**
   (the play-side corner is force/flat, comes up near the LOS). Avoid soft/off
   coverage or a bailing deep-third corner — the block becomes downfield and
   ambiguous. (C1 is ON, so a press-man CB (state ~22) or a Cover-2 flat corner
   (state ~37/38/40) is now an eligible target — verify in the state.)

**Recommended setup:** single-back or I-form, WR split wide to the play side,
**toss/sweep to that side**, defense in **press man** (or Cover 2) so the corner
is on the WR at the snap. Operator picks the exact playbook entries; save
**pre-snap** to a **scratch slot (6+)**.

## Entity identification (do not infer — resolve + control)

Per `rig-emulator-operational`: ask which play the state holds; identify players,
don't guess.
- **The WR:** offensive skill player, position group WR, widest alignment on the
  play side. Confirm his block class engages (he reaches a block state / kind 4),
  not a route — the **post-snap control** that separates block from decoy.
- **His CB:** the defender nearest that WR at the snap (the press/force corner).
- Log both indices and re-confirm each boot (entities can renumber).

## Metrics (per rep, OFF and ON)

| metric | field | what it tells us |
|---|---|---|
| `wr_contact` | WR `+0x3E0` engagement kind reaches **4** | did he actually engage the CB at all |
| `wr_contact_frames` | count of kind-4 frames | did he *sustain* the block or brush past |
| `min_gap` | min `dist(WR, CB)` | closeness of the seal |
| `crossface_frames` | frames the CB is on the **ball side** of the WR | the "CB slips by" defect — should trend to 0 |
| `approach_angle` | WR facing (`+0x1A8`) vs the WR→CB bearing at first contact | the bad-angle defect, quantified |
| `cb_penetration` | CB displacement toward the ball/backfield | did the CB beat the block and make the play |

## A/B protocol (harness-controlled — no manual poking)

The load-state wipes the poke, which the harness exploits for a clean control:
per rep — **load_state** (WR slot reverts to stock) → **poke the condition**
(`0x001F3848` for A / `0x001F3518` for B) → verify read-back → **canned snap
input**, sample ~90 frames → compute metrics. Interleave A/B, **N ≥ 8 reps each**
to average out the contest RNG. No human in the loop, so no play-to-play or
input variance — the one thing the eyeball test couldn't remove.

## Decision rule (pre-registered)

- **W1 ships** iff ON improves the block over OFF beyond rep-to-rep noise on the
  engagement metrics (`wr_contact_frames` up and/or `crossface_frames` down),
  AND does not worsen `approach_angle`.
- **W1 is killed / escalates to a bespoke WR arm** iff ON is within noise of OFF
  on all metrics (the arm-reuse does nothing), or `approach_angle` stays bad
  under both (the TE arm is the wrong shape for space blocking).
- Either way the numbers, not eyes, decide — and the angle metric becomes the
  spec for any bespoke-arm work.

## Must-not-break (regression, always)

The pairing table is global. Confirm the non-WR slots are untouched by W1 and
that ordinary line/TE blocking on the same state is byte-identical OFF vs ON
(only the WR slot changed).

## What is needed to run

1. The savestate above (formation/defense), pre-snap, in a scratch slot — from
   the operator, with the play + defense names (not inferred).
2. Entity resolution + the canned snap input validated against that slot.
3. Then: `experiments/wr_block.py` runs the interleaved A/B and emits the table.

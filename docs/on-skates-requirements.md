# "On skates" — driven blocks, scoped before any patch

Recorded 2026-08-11 from the operator: blocked defenders who LOSE the rep
should be drivable backwards -- feet moving, losing ground continuously --
"on skates". Requirement + scoping only; nothing here is patched.

## Scope boundary (rule 1)

This is the DRIVE/TRANSLATION path of the block system, distinct from the
double-team teardown work in flight. Complementary, not coupled: R6 keeps the
pair together; this makes a held pair GO somewhere. The kind-4 diagnostic
already proved hold time alone moves outcomes (-0.70 -> +0.49 carrier yards);
drive is the multiplier on top.

## Why the operator's "should not be hard" is probably right

The engine already does everything except sustain it:

* A won block IS a rigid two-body translation: shared bearing + drive
  magnitude staged at +0x404/+0x40C, copied into desired_bearing every frame
  the engagement holds; locomotion carries both men. No position writes exist
  or are wanted (the warp anti-goal stands).
* comp1 (+0x414) already governs "the drive speed"; comp2 (+0x418) already
  decides "who drives whom" and selects the shared bearing. The rating margin
  -> motion pipeline is built.
* The reason blocks look static was MEASURED on slot 8: the pair's speed is
  granted ONCE (flags_0c bit 2, a one-shot the block consumes) and then
  DECAYS to zero within frames (0.1908 -> 0.1380 over four). A block is a
  staircase of 14-30-frame frozen steps whose motion dies between steps.

**"On skates" = the drive is re-granted / regenerated for as long as the win
margin holds, with the shared bearing biased toward the loser's backfield.**

## Requirements

* **S1 — sustained drive on a won rep.** While a block is held and the margin
  favours the blocker(s), the pair's translation speed does not decay to
  zero; it tracks the margin. Continuous function of margin -- NOT a switch --
  so stalemates and losses stay planted (that is the R5 story here: gating by
  margin protects ordinary play by construction).
* **S2 — bearing goes backward.** The shared bearing on a won rep points into
  the loser's backfield half-plane, not sideways drift.
* **S3 — mass matters.** Weight (+0xAEC, real pounds) must damp the drive: a
  350-lb nose gives ground slower than a 240-lb end at equal margin. If
  weight is not in the drive math today, adding it is part of this work.
* **S4 — doubles compound.** Two winners on one man drive faster/farther than
  one (ties into R1's 1.5x and R3's >= 1 yd once R6 keeps pairs alive).
* **S5 — pass-pro containment.** A won pass-pro rep must not become a 5-yard
  downfield ride (the PA/ineligible-man visual). Cap or scheme-gate drive
  depth on pass sets.

## Acceptance (episode-scoped, always)

Won single block: pushback measurably > the 15-inch double-team baseline era;
won double: >= 1.0 yd (R3). Operator sees feet churning + continuous rearward
motion, no warps, no skating on stalemates. carrier_yards moves on slot 9.

## Open questions gating the patch

* Q1: where does margin -> pair translation speed actually land (the 0.46
  grants? tug_* +0x420-428? staged_drive +0x404)? The write chain is unmapped.
* Q2: what re-arms flags_0c bit 2 -- can the one-shot become a while-winning
  re-grant? (Smallest-change candidate.)
* Q3: where is the shared bearing computed at lock-in (comp2 winner logic),
  and is it re-aimed at re-evaluation or frozen?
* Q4: is weight in the drive math anywhere today?

Static lane launched for Q1-Q4: docs/dt-lanes/drive-machinery.md.

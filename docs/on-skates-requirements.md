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

## S4-D — the double-team drive law (operator, 2026-08-11)

> "when a second blocker is touching the blocked person, the direction the
> original blocker was going is where they lead him at 2x the force now"

**Direction:** the primary's established drive bearing — the helper adds no
steering, he doubles the momentum already earned. This RESOLVES S2 for
doubles: the engine's existing choice (drive along the winning blocker's own
heading, stores 0x001f159c/15a8) is CORRECT here by design; the sideways
-drift fix applies to singles only.

**Magnitude:** 2x, gated on the helper actually TOUCHING (kind 8 attached, or
helper link == defender with kind >= 4) — not on mere registry membership, so
a shadowing helper adds nothing. Matches R1's "multiply the first guy's
effect", now with the mechanism chosen.

**Implementation path (drive lane, all mapped):** the margin lands at
0x001f15a0 (div.s) and is stored to BOTH men at the 0x001f16dc-e8 confluence;
the per-frame sweep 0x001f2068/84 re-stamps it. The 2x gate is a small cave
at the confluence: if defender dt_role == 2 and helper-in-contact, double
f20 before the stores. Cave #11 has only 3 words free -- next cave needs the
census first (the #1 lesson: reachability before trust).

**Sequencing:** AFTER yes-set+158. Until the record survives the capture,
2x force would apply to ~20 contact frames and then die at 64 regardless.
Order: survive contact -> then double the push through it.

### S4-D REFINED into the unified mass-drive law (operator, same session)

> "proportional to their weight + str combined against the defenders ...
> basically overpowering ... that model could also be applied to when big
> guys block smaller"

**The law (replaces the flat 2x):**

    drive_multiplier = f( SUM_attackers(weight + k*STR) / defender(weight + k*STR) )

* One formula for singles AND doubles -- a double is just the sum having two
  contributors (helper counted only while touching, per S4-D). No special
  cases, no switch.
* "Basically overpowering": a real double (~600 combined lbs vs ~290) lands
  deep in the winning band and stays there -- sustained skates.
* Automatically correct at the edges: big-on-small singles drive (guard
  pancaking a nickel), small-on-big moves nobody (WR on a DE), near-equal =
  stalemate, planted. R5 protection is the shape of the curve itself.

**Inputs, all mapped and verified:** weight +0xAEC (real pounds, verified),
STR from the effective-ratings block +0xB70 (PSTR index 15 in the attribute
table); weight currently has ZERO motion uses (drive lane census), and the
collision layer's 1/(weight*335.4) is the in-engine inverse-mass pattern to
imitate. Implementation stays at the margin confluence 0x001f16dc-e8: the
cave computes the ratio for the locked pair (+ touching helper) and scales
f20. Direction unchanged: the primary's earned bearing.

k (STR's weight vs pounds) and f's clamp band are TUNING -- range cards per
the seed-testing plan once the first sweep exists.

## P5 DIAGNOSTIC RESULT (2026-08-11): lever proven, but it moves the WRONG BODY

256x drive, 10/10 identical. speed_cmd reached **83.72** (normal ~1.0) --
the lever unquestionably reaches the engine. **carrier_yards -0.70 -> +7.07**:
the stuffed dive broke for seven yards, the first real outcome swing of the
project.

**But the doubled DE did not move backward: dy -0.70, dist 0.85 -- identical
to baseline at 256x force.** Conclusion, and it reshapes S1-S3:

* `speed_cmd` (+0x1E8) drives the BLOCKER's own locomotion. The engaged
  defender does not consume it. The yards came from linemen travelling, not
  from anyone being driven.
* So "on skates" needs a SECOND mechanism: whatever moves the defender's body
  during an engagement. The drive lane's Q1 chain (staged_drive -> speed_cmd,
  stored into BOTH men) is real but only half the story -- the defender's
  copy evidently governs his own gait, not displacement by the blocker.
* Next question, now sharp and answerable: during a live engagement, what
  writes the DEFENDER's position/velocity? Candidates: the collision layer
  (0x00213038, inverse mass 1/(w*335.4), weight_copy +0x1E4 -- the only
  mass-aware motion in the image), the shared-bearing translation, or an
  animation-driven root motion that overrides both.

The mass-drive law (weight+STR ratio) stands as the FORCE model; it must be
applied to that second mechanism, not to speed_cmd. P5 stays available as the
"is my lever wired to anything" template -- one hook, eight doublings, done.

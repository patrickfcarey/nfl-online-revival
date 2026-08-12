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

### CORRECTION (operator, same session): P5 buffed BOTH SIDES — the test was symmetric

> "is it possible we also buffed it for the defense? ... otherwise it seems
> theres nothing that changed ... maybe that 72 just pancakes someone because
> theyre so much smaller and easier to win the battle with the huge buffs to
> both sides"

Confirmed in the data. Max speed_cmd per engaged pair, iteration 0:

    TE 83.72 <-> DE 83.72     LT 78.77 <-> #4 78.77
    C  37.55 <-> NT 37.55     LG 16.96 <-> #1 16.96     WR 12.55 <-> CB 12.55

**Identical values, both sides.** The sweep's two stores (0x001f2068,
0x001f2084) write the SAME shared magnitude to both members of the
engagement, so the 256x cancelled out and the relative contest was never
touched. The +7.07 carrier yards came from everyone moving faster, not from
anyone winning.

**Therefore the previous entry's conclusion is WITHDRAWN.** "speed_cmd drives
the blocker and an engaged defender does not consume it" was never tested --
there was no asymmetry to observe it with. Whether a defender can be driven
by this lever is OPEN again.

**The correct next diagnostic is asymmetric**: scale only the offensive
member. The cave already has the player pointer in the store's base register;
the side byte is +0x01 (0 = offense, per the handle layout). Gate the eight
doublings on `lbu at, 1(base); bne at, zero, skip` -- ~3 extra words per arm,
same hooks, same cave. If the DE then goes backwards, the lever is real and
the mass-drive law attaches HERE; if he still does not, the second-mechanism
hunt (collision layer 0x00213038 inverse mass) is the live thread.

## P6 RESULT (2026-08-11): DECISIVE — speed_cmd cannot drive an engaged defender

Asymmetric 256x, gate verified working (boosted speed_cmd appears on side-0
players ONLY; every defender stock). Result across 3 iterations:

    DE_dy  -0.49 / -0.73 / -0.49    (negative = penetrating toward the
    DE_dist 0.62 / 1.15 / 0.62       offensive backfield, i.e. NOT driven)
    carrier -0.70 / +2.67 / -0.70

**Under a 256x one-sided push the doubled defender is immovable.** The lever
governs the blocker's own locomotion; an engaged defender does not consume
it. This is the clean pre-registered negative and it CLOSES the speed_cmd
branch of S1-S3: no tuning of the weight+STR law onto this field would ever
have moved anybody.

**The hunt moves to the second mechanism**, with one strong named candidate:
the collision layer at 0x00213038 -- `1/(weight * 335.4)`, weight_copy
+0x1E4 -- the ONLY mass-aware motion code in the image, and therefore the
natural home of a mass-based drive law. Open questions for it: does it run
while two players are engaged (or is engagement exempt from collision
resolution?), and does anything else write a player's position during a
block (animation root motion is the other candidate). Same P5/P6 template
answers it: hook the write, scale absurdly, look.

Also logged: iteration 1 diverged again (window 2..67, +2.67 yards) -- the
second determinism wobble on record, both under heavy drive scaling.

### The +7.07 was DEFENDERS OVERRUNNING, not blocking working (operator flagged the oddity)

Operator: "those were very weird results." Correct -- the arms invert. Both
sides boosted = +7.07 yards; offence-only boosted = -0.70 (baseline). Helping
only your own team should help MORE.

Defender path length, iteration 0, totalled over all 11:

    P5 both sides boosted   188.8 yd     <- defenders travelled 78% further
    P6 offence only         110.0 yd
    P1+P4 stock drive       106.1 yd     <- P6 is barely above stock

**So P5's seven yards came from the DEFENCE being fast and blowing past the
play, not from the offence blocking better.** P6 leaves the defence stock and
the run returns to -0.70 immediately. The earlier entry calling +7.07 "the
first real outcome swing of the project" is WITHDRAWN -- it was an artifact of
breaking the defence.

Two clean conclusions survive, both negative and both worth the cycles:
* speed_cmd does not drive an engaged defender (P6, one-sided 256x).
* speed_cmd DOES govern free locomotion strongly enough that scaling it wrecks
  pursuit -- which is why any future drive law must be gated to ENGAGED pairs,
  never applied to the field at large.

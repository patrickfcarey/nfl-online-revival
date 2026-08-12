# Synthesis of the three animation lanes — what they agree on, and what they don't

Written 2026-08-11 after all three delivered. **Read this before building any
animation patch**; two lanes reached different pictures and only one can be
right.

## Unanimous, and it is the finding of the night

**The capture hardcodes its animation and never reconsiders.** Lane 1 quotes
`addiu s3, zero, 158` at `0x001f7d08` in the capture service, and state-32's
think re-calls capture at every segment end -- so a captured pair plays 158
forever. Lane 3 independently found the pair-anim dispatcher's trigger is
**geometry-only, never margin**. Same defect from two directions: **nothing
about winning a block selects a winning animation.** Every driving/pancake
clip in the game is unreachable from the double-team path.

Also unanimous: root motion is what moves a blocked body (Route C, already
confirmed by P6/P7 elimination), and the engine has a rich outcome vocabulary
sitting unused.

## The disagreement — DO NOT patch until resolved

| | lane 1 (dispatcher) | lane 3 (clip inventory) |
|---|---|---|
| the driving family | **149/150** (pancake pool, in the yes-set), plus grid cells 50/53/54 run, 58 pass | **pair 161**, 24 variants with outcome classes 7/10/14/16/17/18 |
| the mechanism | capture id 158 hardcoded at `0x001f7d08`; outcome grid `0x00526F90` | dispatcher `0x001ef130`, sector->class table `0x00583300` |
| the recommended patch | P-A: one word, 158 -> 149/150 | scale motion post-`0x0018f9e0` |

Both are internally sourced and quoted. They may both be true of *different
paths* (lane 1 traced the capture/kind-4 path; lane 3 traced a bearing-vs-
facing pair dispatcher) -- or one has mislabelled a table. Neither lane read
the other's site.

**Neither lane established per-clip displacement magnitudes.** Lane 3 says the
per-sequence rows are bit-packed and no owned memory image holds a live pair
anim; lane 1 says root motion lives in clip assets it did not decode. So
*which ids actually move a body* is UNVERIFIED on both sides -- the labels are
selector semantics, not measured displacement.

## The decisive experiment, and it is cheap

Both lanes independently produced the same live probe. Sample the animation id
during a real block and the argument ends:

* current anim id: `u16[[player+0x304] + 0x64k + 4]`, status `+6 == 3`
* pair id: `u16[player+0x3DE]` (authoritative during kinds 5/6)
* participant word: `+0x3DC`

Run it on slot 9 under P1+P4 and read what the TE/RT/DE pair actually plays.
If it reads 158 throughout, lane 1's picture is the live one and P-A is a
one-word test. Then flip the id and watch whether the bodies move -- which
also measures displacement empirically, closing the gap neither lane could.

**Do this before writing any patch.** Three "dead" caves and five refuted
theories tonight all came from building before measuring.

## Tool debt this creates

`experiments/double_team.py` needs the anim-id fields (`+0x3DE`, `+0x3DC`, and
the `[player+0x304]` chain) in FIELDS. That is the fourth probe in a row to
want fields the spec does not sample.

## PROBE RESULT (2026-08-11): the field is paired, and both lanes' id vocabulary is WRONG for it

Ran on slot 9 under P1+P4, masked to engagement kinds 5/6. First live read of
a two-man animation in this project.

**Confirmed strongly: +0x3DE is genuinely the PAIR field.** Every blocker and
his defender read the identical value, every pairing, every frame:

    TE 0:5  <-> DE 1:3    {19: 37}
    C  0:8  <-> NT 1:2    {17: 59, 19: 107}
    LG 0:7  <-> 1:1       {18: 33, 17: 59, 19: 89}
    FB 0:2  <-> 1:0       {18: 34, 15: 26}
    RG 0:9  <-> 1:5       {19: 38}

**But the values are 15/17/18/19 -- NOT 158, 161, or 149/150.** Neither
lane's id vocabulary appears. 65535 (0xFFFF) is the idle/none value; 65281
(0xFF01) appears once on the RT, unexplained.

**And the value CHANGES mid-block** (C/NT run 17 then 19), which refutes
"plays 158 forever" *as a claim about this field*.

Best hypothesis, unverified: +0x3DE holds a CLASS or group-local index rather
than a global clip id -- the observed set overlaps lane 3's sector->class
vocabulary {7,10,14,16,17,18} closely. 158 may still be hardcoded at
0x001f7d08 (verified present in live memory this run: 0x001F7D08 = 2413009E =
`addiu s3, zero, 158`) and simply live in a different word.

**Next step, cheap and decisive:** sample the OTHER candidate address from the
lanes -- the current-anim id at `u16[[player+0x304] + 0x64k + 4]` with status
`+6 == 3`. That is a pointer-chase, so it needs a World accessor rather than a
plain field offset. If it reads 158 on the TE/DE pair while +0x3DE reads 19,
both lanes are right about different words and the picture resolves cleanly.

Lesson banked: the probe was worth running BEFORE any patch. A one-word change
to 0x001f7d08 would have been made on the belief that +0x3DE would show 158,
and the observation would have been unreadable.

### The pointer-chased word WORKS (live, 2026-08-11)

`u16[[player+0x304] + 0x64k + 4]` with status at `+6`, read live on slot 9
pre-snap. Slot 0 carries status 3 (playing) for every player and the ids are
sensible and position-correlated:

    QB 91 | HB 85 | FB 86 | WR 85/85 | TE 86 | OL 86,86,86,86,86
    NT 21 | DE 86

So this IS the current-animation id, and its vocabulary (85/86/91/21) is
DIFFERENT from +0x3DE's (15/17/18/19) -- supporting the reading that +0x3DE
holds a class/group-local index while this word holds the global clip id.

**Blocker: this is a pointer chase, not a flat offset**, so `Player.snapshot`
cannot sample it from `addresses.yaml` as it stands. Sampling it DURING a
block -- the observation that finally names the clips -- needs a harness
accessor. That is now the top tooling task, because every animation patch
depends on being able to read what changed.

## A2 RESULT (rung A2 complete): the double team ALREADY plays 161 — B1 is invalidated

First read of exact clip ids during a live block, via rung A1's anim_id.

    TE  f39 a106/g15 -> f43 a161/g19/k5   37 frames on clip 161
    DE  f39 a106/g15 -> f43 a161/g19/k6   same clip, same frames
    RT  never enters 5/6 (stays k7/k8/k2) -- the helper is attached but is NOT
        a participant in the paired clip

Ids while kind in 5/6, whole line:
    TE/DE   161 x37          <- the DOUBLE TEAM
    RG/#14  161 x38
    C/NT    121 x59, 158 x107  <- shed-lose then capture
    LG/#11  151 x33, 129 x59, 158 x89
    FB/#14  148 x34, 63 x26

**B1 (0x00583390, putting 158 in the yes-set) IS INVALIDATED.** The double
team never enters 158 -- that is the SINGLE-block capture. B1 would have
changed single blocks and left the double untouched, and its oracle
("record survives past 64") could never have fired for that reason.

**Lane 3 is vindicated on the driving family**: 161, the pair family with 24
outcome classes, is what the double team actually plays -- selected, not
hypothetical. Lane 1's 158 and the shed ids 121/129/151 are real too, on the
other pairings, exactly as the group decode predicted.

**And this crowns the diagnosis.** The engine ALREADY selects a driving-class
animation for a double team; it just plays it with no root motion (5-clip-
semantics: pair families ship no static type-9 displacement spec, and the live
pair's motion block reads zeroed). So there is no clip to go find and no
selector to fix -- **the single remaining lever is writing the motion block
itself** (anim-slot+0x10, post-0x0018F9E0), with the weight+STR law as the
magnitude. Ladder fallback F-A is now the main line.

Also new: the RT (the kind-8 helper) never enters kinds 5/6 at all. The paired
clip has two participant roles and the helper is not one of them -- which is
why he trails. Any drive written for the pair must carry him, or he detaches.

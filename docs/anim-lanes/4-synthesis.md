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

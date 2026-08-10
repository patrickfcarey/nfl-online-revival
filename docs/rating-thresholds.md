# Do the PS2 games use rating thresholds?

Investigated 2026-08-10 against `SLUS_207.52` (Madden NFL 2004), in
answer to open question #19 — the most generalisable question this
project has been asked, and the one that came with the best evidence.

> "A backup safety with **40 pass block** would watch defenders run right
> by him. I subbed in a guy with **44** and that was enough to bump and
> fail. A TE with about **55** got the rusher to a dead stop before
> breaking the block. Same consistent results, again and again. The
> rating must have SOME minimum thresholds."

**Their 44 is a band boundary, exactly.** More on that below.

## The verdict: ~87% continuous, but the contact game is banded

A closed census found **278 read sites** of the effective-rating table
(`player+0xB70+2·attr`) — 210 direct plus **68 that a naive sweep misses**,
reached through a base register. Of those:

* **190 (68%)** never reach a comparison at all — they feed float
  multiplies and adds. Pure continuous scaling.
* **23** compare a rating against another *register* (an opponent's
  rating, or an RNG draw). Contests — also continuous.
* **14 (5%)** compare against a literal constant. Only four are hard
  gates; the rest are dead-zones, clamps, or one branch selector.
* **22 (8%)** pass through a coarse quantiser.

So by count the engine is mostly continuous. But the weighting is not
uniform: **the tackle contest and the block-engagement duration — the two
systems players complain about most — are fully banded at 4 bits.**

The honest summary: *"the rating drives a score that feeds a roll" is
right, but in the contact game the rating is rounded to a 16-step ladder
before it ever reaches the score.*

## The quantisers — where the real discreteness is

27 instructions across 22 sites reduce a rating to ≤64 buckets via
`sll rX,16 ; sra rX,16+N`:

| bands | shift | used by |
|---|---|---|
| **16** | `>>4` | the tackle contest, **the block-engagement duration** |
| 32 | `>>3` | the Awareness state-change roll, pursuit predicates |
| 64 | `>>2` | catch / tackle score terms |
| 8 | `>>5` | reaction timers |
| 4 | `>>6` | the QB pocket-pressure cadence |

Band edges for `>>4`, in 0–100 rating terms: **7, 13, 19, 26, 32, 38, 44,
51, 57, 63, 70, 76, 82, 88, 95**. For `>>5`: 13, 26, 38, 51, 63, 76, 88.

## The 40 / 44 / 55 observation, explained

The **only** discretisation of pass-block/run-block anywhere in the image
is `band = effective >> 4`, feeding the engagement lock
`lock = 30 − band` (three instructions: `0x001ef8f0`, `0x001ef920`,
`0x001f2228`). With `effective = trunc(rating × 2.55)`:

| rating | effective | band | lock frames |
|---|---|---|---|
| 38–43 (incl. **40**) | 96–109 | **6** | 24 |
| **44**–50 | 112–127 | **7** | 23 |
| 51–56 (incl. **55**) | 130–142 | **8** | 22 |

**40 and 43 are the same number to the engine. 44 is the first value that
isn't. 55 is one band further.** Their three data points are three
consecutive buckets, and the step they noticed sits exactly on the
boundary the code puts at 44. A continuous model cannot produce that.

The *graded* part of what they saw — bump-and-fail versus dead-stop —
comes from the continuous layer alongside it, a per-frame contest where
pass block buys **0.6375 percentage points of resistance per rating
point**. Over a 20–40 frame rep, 40 → 55 is roughly an order of magnitude
in "was he ever stopped".

**A competing explanation exists.** A parallel lane found a
**sign-extension bug** in the pass-protection steering throttle whose
overflow edge also lands near pass block 40 (see
`pass-vs-run-blocking.md`). Both are real arithmetic in different code
and different phases, and they are not mutually exclusive — but **which
one the tester actually saw is unresolved**, and settling it needs a rig
measurement.

**Honest limit:** no comparison anywhere says "below rating X, contact
does not occur". Engagement *start* reads **no rating at all** — it is
decided by proximity and assignment. A 40-rated blocker does enter the
engagement; what band 6 buys him is the longest pre-set contact phase and
the smallest per-frame resistance, which on screen reads as "the rusher
went straight through him". Also: NCAA 2004 is a sibling build — the
mechanism should transfer, the exact constants may not.

## The confirmed hard gates and dead zones

| what | rating | kind |
|---|---|---|
| pass-rush power moves 4/5 (four separate sites) | **65 STR** | hard gate |
| QB throw power | below **70** every QB is identical (output 0.0) | dead zone |
| agility terms (two sites) | multiplier is exactly 1.0 below **50** | dead zone |
| carrying | below **50** the term is *hard-zeroed* | dead zone → zero |
| strength in the break-tackle helpers | clamped to **[39, 78]** | saturating clamp |
| speed | below **59** stores a constant instead of the computed value | branch selector |
| QB throw placement | floor at **60**, error is exactly zero at **80** | clamp + hinge |
| punter accuracy | useful band **70–100** (`punt-logic.md`) | dead zone |

**A clamp can create a gate without a comparison** — as predicted. The
shed contest clamps both scores to ≥ 0 with conditional moves, and
`RandInt(0,0)` provably returns 0, so a score clamped to zero can *never*
win. That is a hard gate manufactured by a clamp.

## Patchable thresholds

Lowest-risk are the data words: the four **165.75** constants (= 65 ×
2.55) that gate power pass-rush moves, and the QB throw-placement
floor/hinge pair.

Single code words: removing the pass-block band from the engagement lock
(`0x001ef8f4` plus its two siblings) gives every blocker a constant
30-frame lock — **the cleanest "de-band pass protection" patch**.
Similar one-word removals exist for the throw-power, carrying and agility
dead zones.

**Not safely patchable:** the four shifts in the tackle score. Widening
them quadruples the score magnitude *before* the difficulty scaler, the
slider and the `ptrk` boost — it de-bands and rebalances at the same
time. (`tackle-contest.md` proposes the controlled version of this: `>>4`
→ `>>3` on four specific sites *with* a matching class-add retune.)

## Searched and not found

* **No rating-indexed lookup table anywhere** — all seven candidates were
  false positives.
* **No threshold on the raw 0–100 rating** before the effective table is
  built.
* **Pass block and run block are never compared to a literal constant
  anywhere in the image.** The `>>4` quantiser is their *only*
  discretisation — which is why the community's threshold intuition is
  right even though no `if` implements it.
* **No engagement-eligibility rating test.**

## Hazard note on rounding

Ratings 40/44/55 land on `.0/.2/.25` after ×2.55, so truncation and
round-to-nearest agree and the table above is safe. But ratings **50**
(127.5) and **90** (229.5) sit exactly on `.5` and *would* flip band under
round-to-nearest. A PINE read of the FPU control register on the rig
would close that.

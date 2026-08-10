# The catch process, post-catch strips, and what governs fumbles

Investigated 2026-08-10 against `SLUS_207.52` (Madden NFL 2004), in
answer to open question #22.

> "What about knocking the ball out after it has been caught but before
> it counts as a catch? Does catch still matter once the WR has caught
> it, to also hang on to it during contact? I would assume so, but does
> it?"

**Both assumptions are correct, and the engine is more sophisticated
here than anywhere else this project has looked.**

## There is a real "process of the catch"

Possession transfers **immediately** on a successful catch roll — there
is no "hold the animation, then grant". But the ball object enters an
**unsecured-possession mode** at the same moment, and stays there for
about **21 ticks** (**41** on a diving catch). `SecurePossession`
(`0x00258020`) closes the window.

During that window, three separate loss checks are live, and they are
deliberately non-overlapping with the ordinary fumble.

## The post-catch strip — it exists, and it is its own mechanism

`0x00257254`, reachable **only** from the tackle-impact animation
starters (closed set of three callers). It fires exactly when a tackle
animation is committed against a receiver who is still inside the window
and whose possession came **from a pass in flight**:

```
offense = (CATCHING + CARRYING/4) / 318      ← the carrier
defense = (TACKLE   + STRENGTH/4) / 318      ← the tackler
chance  = base(hitClass) × (1 + defense − offense)
        → WR Catching slider (inverted) → difficulty modifier → roll
```

`base(hitClass)` comes from the resolved tackle animation: **0.05 / 0.15 /
0.30**. The big head-on hit gets **2× the strip chance of a standard wrap
and 6× a weak one** — that is the hit-magnitude term the community asked
about.

Answering their attribute list directly: **tackle — yes, full weight;
strength — yes, quarter weight; awareness — no, not in this formula at
all** (it does appear in the ordinary fumble). Worked example: a 90-catch
receiver hit by an 88-tackle linebacker is stripped ~15% of the time on a
standard tackle, **~31% on a big hit**.

**The WR Catching slider is the strip slider**, applied *inverted*: maxed
it cuts a standard strip from 15% to under 4%; zeroed it raises it to 27%.

A second, per-frame "hands" check runs for the first ~10 ticks, resisted
again by catching, with no defender involved — the "did he actually hang
on" roll.

## Crucially: a ball jarred loose in the window is an INCOMPLETION

Both the strip and the general fumble path check the unsecured flag. If
it is set, they post **the same event the failed catch roll posts** and
return the ball to flight. Only after `SecurePossession` does a loss
become a real fumble and turnover.

**This engine implements the process-of-the-catch rule literally** — and
nobody in the community appears to know it.

## Does CATCHING still matter after the catch? Yes — it *replaces* carrying

The switch is explicit:

```
if (unsecured)  security = CATCHING/2 + AWARENESS/32
else            security = CARRYING/2 + AWARENESS/32
```

**Catching is literally substituted for carrying as the ball-security
rating for as long as the catch is unsecured**, and carrying takes over
the instant possession is secured. In the two dedicated post-catch
checks, catching carries full weight and carrying only a quarter.

The reciprocal negative is just as clean: **catching is read by no
function in the tackle or contact family.** Once the ball is secured, it
stops mattering completely.

## What actually governs fumbles (there is no fumble slider)

```
security = (unsecured ? CATCHING : CARRYING)/2 + AWARENESS/32
raw      = (1 − security/134)·0.0285 + 0.0015
         + hit magnitude (capped, worth at most +0.005)
         + weather (wetness)
         + carrier AI-state / assignment terms
         ÷ position divisor  →  Madden Card adjust  →  roll
```

Three findings worth flagging:

* **The tackler's ratings are not read anywhere in the ordinary fumble.**
  The chance function never receives the tackler. A monster hitter's
  strength and tackle do *nothing* to ordinary fumbles — they only matter
  in the post-catch strip. This is the biggest surprise in the lane.
* **Position matters:** receivers and tight ends fumble at **half** the
  base rate; kickers and punters at **double**.
* **Toughness (attr 19) has zero gameplay consumers** — its only read in
  the entire image is the rating/grade builder. It does not touch
  fumbles, tackles, or in-play injuries.

Base rates at default settings, before the position divisor: a 99/99
carrier fumbles at **0.17%** per check, 75/70 at **0.87%**, 50/50 at
**1.60%** — roughly a 12× spread across a roster. Confirms
`slider-behavior.md`'s "no fumble slider" from the other direction: the
chance function calls **none** of the eleven slider transforms. What
controls fumbles is carrying, awareness, position, hit magnitude,
weather, difficulty, and Madden Cards.

**One term is unresolved and enormous:** a `+0.9` addition to the raw
fumble chance when the carrier's per-play assignment class falls in a
particular range. After clamping that is a near-certain fumble, and it is
by far the largest single term in the formula. Its gate is not
identified. **Do not quote fumble probabilities publicly until it is.**

## Thresholds found (for the census in `rating-thresholds.md`)

* Carrying **50.2** — below it, carriers take an alternate fumble branch.
* A 100-carrying / 100-awareness carrier has a base fumble term of
  **exactly zero** (the security term saturates).
* Window constants: **21 ticks** (strip window and secure deadline), **41**
  (diving catch), **20** (recent-catch fumble immunity), **10** (hands-check
  decay), **5 frames** (offense re-contest lockout), **60 frames** (loose-ball
  recovery lockout).

## Hazard notes that cost real time

* **Two documented addresses in our own docs are mid-prologue.** The
  ball-arrival resolver's real entry is `0x00255248` (not `0x00255250`);
  the interaction handler's is `0x002565E0`. Searching for callers of the
  published addresses returns zero.
* **Attribute-offset sweeps are unsound on their own.** Ten gameplay
  sites materialise a base pointer and then read a small offset from it —
  including the entire tackle family. A census done only against literal
  `2928 + 2n` offsets misses them. Both forms must be swept.
* A delay-slot store makes the unsecured flag look like a computed value
  when it is a constant.

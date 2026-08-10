# HB vision and special moves

Investigated 2026-08-10 against `SLUS_207.52` (Madden NFL 2004), closing
open question #12.

> "What gives them vision? Awareness I assume. What is the threshold from
> seeing lanes and being blind, but what also governs special move usage?
> Is it possible to globally improve HB vision without making them all
> elite at special moves?"

## The headline: halfbacks may have no vision at all, by accident

The AI carrier's block-reading gap-steer — the whole mechanism
`pitch-play-runner.md` mapped — fires on a probability that is **not
rating-driven**. It is a flat constant chosen by *running style*:

| style | gap-steer probability |
|---|---|
| 0 | **0%** |
| 1 | 35% |
| 2 | 65% |

The style classifier reads the position byte: receivers, defensive backs
and returners get style 2; quarterbacks and linebackers style 1; tight
ends, linemen and kickers style 0. **Halfbacks and fullbacks are special
— they branch on `player+0xB07`**, where `0` → style 0, `1` → style 2,
`≥2` → style 1.

**And `player+0xB07` has no writer anywhere in the executable.** No
literal store at that offset; no store whose byte range covers it; no
pointer-form write (swept across a 900-byte offset window with an
80-instruction lookahead). The roster loader writes the bytes on either
side of it and skips it. Nine sites read it; nothing writes it.

**If that byte is zero at runtime, every HB and FB in the game is style 0
— a 0% gap-steer probability — and the block-reading vision never
executes for a halfback at all.** That is a one-line explanation for
"HBs have no vision, but a receiver on a reverse looks slippery."

**This needs a PINE read on the rig to settle** — read `player+0xB07` for
a halfback mid-play. It is the single highest-value runtime measurement
currently outstanding.

## What vision actually is: a cadence timer

Closed census over the six steering helpers and their **79-function
transitive closure: zero rating reads.** Every cone, angle and distance
gate is a hard-coded literal. But the whole steering re-plan sits behind
a countdown:

> `period = max(1, (255 − mean(AWR, CAR, AGI)) >> 4)` ticks

| rating | re-plan period |
|---|---|
| 50 | **8 ticks** |
| 70 | 4 |
| 88+ | **1 (every tick)** |

On a skipped tick the carrier re-issues a **stale** bearing. So a
50-rated back is eight times less responsive than an 88 — *that* is
"seeing lanes vs being blind", and it is a timer, exactly as this
engine's law predicts. The band edges land on the published `>>4` ladder
in `rating-thresholds.md`.

## Special moves: the full chain

Selection runs on its own cadence — `16 − (AWR + AGI/2 + BTK/2) >> 5`
ticks, so **awareness carries double the weight** of agility and break
tackle. (These are the three break-tackle reads `tackle-contest.md`
flagged as "move selection": they are a *timer*, not a move strength.)

Then a **7-byte priority row per style** (pure data, three rows) is
walked in order, and the first move whose handler accepts wins:

| style | order |
|---|---|
| 0 | Sprint → Dive → Stiff Arm → Hurdle → Juke → Spin → Lunge |
| 1 | Juke → Sprint → Spin → Dive → Stiff Arm → Hurdle → Lunge |
| 2 | Juke → Spin → Hurdle → Dive → Stiff Arm → Sprint → Lunge |

| move | gates | probability |
|---|---|---|
| Juke | **AGI ≥ 65**, ≤3.7 yd, ≤55° | max **47%** |
| Spin | **AGI ≥ 65**, ≤3.7 yd | max 31% |
| Hurdle | ≤2.5 yd, ≤25°, defender **diving** | **deterministic — no roll** |
| Dive | ≤4.0 yd, ≤60° | max 38% |
| Stiff Arm | ≤3.0 yd, defender abeam | max 38% |
| Sprint | aperture test | **flat by style: 75 / 35 / 15%** |
| Lunge | sideline geometry | none |

The whole move chain reads **only AGI, AWR, BTK and STR** — no speed, no
carrying, nothing else. The AI and the human fire the *same* handler
functions.

*Correction:* `0x00522fe8`, given as the move enum, is a **UI string
table** (the HUD tip text). The AI's move ids are 0–6 with different
numbering.

## The separability question — the actual answer

| | vision | special moves |
|---|---|---|
| ratings | AWR + CAR + AGI (`>>4`) | AWR + AGI/2 + BTK/2 (`>>5`), then per-move |
| style | → the 0/35/65% gap roll | → priority row, sprint % |

**Via ratings the wish is impossible** — they share awareness and
agility, so raising a back's AWR shortens his steering cadence *and* his
move cadence *and* raises his juke and spin odds. No rating touches
vision alone.

**Via code the wish is fully granted** — the two systems share no
*instruction*. Vision lives in the steering cadence and the style gap
roll; moves live in their own cadence and handlers. Disjoint sites, zero
cross-talk. **It needs two patches, not one — and that is the answer.**

## Fix candidates

**Vision:**
* **V2 (the big one)** — give style-0 runners a nonzero gap-steer
  probability. One word. If the `+0xB07` finding holds, this is *the*
  "halfbacks can't see the hole" fix.
* **V1** — de-band the steering cadence so every carrier re-plans each
  tick (or halve the penalty to keep the gradient).
* V3/V4/V5 — widen the steer gate, the threat cone, and the commit
  distance (these confirm `pitch-play-runner.md`'s N3/N4 pairs in place).

**Moves:**
* **M5** — reorder the priority rows. Seven bytes of pure data, no code
  change: power backs would try Dive/Stiff Arm before falling back to
  Sprint.
* **M4** — the sprint short-circuit odds (style 0's **75%** is why power
  backs mash turbo past every move).
* M2 — the two AGI-65 gates on juke and spin are single-reader pool
  words, zero blast radius. (Truncation makes the real gate **AGI ≥ 66**.)

## Correction to `pitch-play-runner.md`: not AI-only

The state dispatcher runs the user-think slot first and **falls through
to the AI think unless it returns exactly 1**. The carrier's user-think
returns 0 on its main path — so **when a human carries the ball and
does not trigger a move that tick, the AI steering and the AI move
selector both run for him.**

So the V and M levers are **not strictly AI-only**, contrary to what that
doc claims. V2 is the safest of the set (it only enables a steering term
the stick overrides anyway). Watch for a human-controlled back
auto-juking before shipping anything from the move column.

## Thresholds for the census

Two more members of the 165.75 family — but **AGI, not STR**: the juke
and spin gates, both single-reader pool words. Truncation puts the real
edge at rating 66.

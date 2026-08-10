# Punter placement: the coffin corner exists, and it is switched off

Investigated 2026-08-10 against `SLUS_207.52` (Madden NFL 2004), in
answer to open question #17.

> "I can put 20/20 punt power and accuracy and they never want to coffin
> corner me. They will often just drill touchbacks and be happy about it.
> 20/20 accuracy and they don't even try to drop the ball inside the 15
> or 10 and get a bounce."

## The answer in one paragraph

The engine **has** a real coffin-corner solver — a ballistic model that
computes flight time, compensates for wind, aims one yard outside the
sideline, and refuses to land the ball past the 1-yard line. It is not
missing. It is gated behind four conditions that must all pass:
**CPU-controlled kicking side**, punt (not any other kick type), the ball
spot beyond the punting team's own 40, **and a 25% coin flip**. Everything
else — three quarters of CPU punts, every punt from deep in your own
territory, and *every* human punt — takes a default path that is nine
instructions of trigonometry driven by three random numbers, with **no
field position input whatsoever**. The punter swings just as hard from
the opponent's 35 as from his own 10.

And the reason the accuracy slider never helps: on the coffin path it is
**overwritten and does nothing**, and on the default path there is no aim
point for it to sharpen. The slider only shrinks noise around an aim that
was chosen at random.

## The default punt: three random numbers

`0x0015981c` (the punt arm of the AI kick-meter setter) in full:

* power target = `100 + 44 × rand`
* yaw input = `1.2 × rand − 0.6`
* pitch input = `0.5 × rand − 0.2`

Fed to the default geometry at `0x0015a620`: elevation 47°–54.5°, yaw
**±21° of pure noise**. There is no field-position term anywhere in it —
which is conspicuous, because the *field goal* arm ten instructions away
(`0x00159610`) **does** read field position (`if 0 < kickerY < 30: power
+= (30 − kickerY)·0.5333`) and then runs an iterative solver to aim at the
uprights. Punts got neither.

## The coffin-corner solver that does exist

`0x001598c0`, and it is a real piece of work:

* forces elevation (45° in mode 1, 50° in mode 0) and **discards the
  pitch input**;
* computes flight time properly: `t = vz/g + sqrt((vz² + 2gh)/g²)`;
* asks `0x00202308` for wind drift over that time;
* picks the sideline to aim at — **±27.6667, one yard outside the
  26.667-yard half-width** — flipping side based on ball position and on
  crosswind over ±10;
* solves the downfield component from the range and the lateral offset;
* **clamps the landing to Y ≤ 49.0 — the 1-yard line** (`0x00159a38` /
  `0x00159a44`). Because it shortens the *aimed distance* while leaving
  launch speed alone, the ball crosses the sideline plane before the end
  zone regardless of power. The mechanism is sound.

Closed-set proof this is the only such code: exactly three references to
±27.6667 exist in the image, all inside this function.

## The gate — why you never see it

`0x0015a3c4` inside `ComputeKickVelocity`. All four must pass:

| gate | address | effect |
|---|---|---|
| kick type must be punt (3) | `0x0015a3e4` | excludes the safety free kick (type 5) |
| kicking side must be CPU | `0x0015a4ac` | **a human punter can never reach this code** |
| ball spot Y > −10 (own 40 or better) | `0x0015a4d4` | no coffin corner from your own territory |
| 25% coin flip | `0x0015a4bc` | three quarters of eligible punts take the default |

There is a second, formation-conditioned path (`0x0015a48c`, also 25%)
that skips the field-position test but requires all eleven punting-team
players within 8 yards laterally of the ball — in a normal punt formation
the gunners are split much wider, so it effectively never fires. *(That
last step is a formation inference, not a binary fact — worth a rig
check.)*

**Corroboration that this is deliberately a feature:** Madden Card slot 24
forces the same solver with mode 1 — 45°, power pinned, jitter suppressed.
A "perfect coffin corner" card sits on top of the identical code path.

## What the punter's ratings actually do

**Nothing to intent. Everything to magnitude and noise.** A 95-accuracy
punter and a 60-accuracy punter choose *exactly the same aim point*.

* **PKPR (power)** — three sites, all magnitude: a meter penalty
  `power −= 45·(1 − (PKPR·W/255)²)`, and a trajectory de-rate of up to
  −20% on the default path.
* **PKAC (accuracy)** — three sites, all error size. Notably it builds a
  **forgiveness dead-zone** on the meter: a miss inside `w` produces
  exactly zero error. **The useful PKAC band is rating 70–100; below 70
  every punter is identical.** (A real rating threshold — logged for the
  threshold census, #19.)

One inversion worth knowing: the difficulty modifier `0x00153be0` queries
the **opposing** side's class, so a *human* punter on All-Madden is held
to a stricter accuracy standard than the CPU punter is.

## Fixes — all one-word, because the logic is already there

| # | site | now | change to | effect |
|---|---|---|---|---|
| **A** | `0x0015a4bc` | `3c013e80` (0.25) | `3c013f80` (1.0) | coffin corner always fires when eligible |
| B | `0x0015a48c` | `3c013e80` | `3c013f80` | same for the formation path |
| C | `0x0015a4d4` | `3c01c120` (−10.0) | `3c01c1f0` (−30.0 = own 20) | widen the field-position band |
| **D** | `0x00159a18` | `3c014244` (49.0) | `3c014220` (40.0 = the 10) | **aim for the 10 instead of the 1** — directly answers "inside the 15 or 10" |
| **E** | `0x005fdd40/44/48` | ±27.6667 | e.g. ±26.0 | **aim inside the sideline for a bounce** instead of directly out of bounds |

All single-reference (censused). Recommended conservative set: **A + D +
C at −30**, tested on the rig.

**Do not flip these blind.** At 100% the punter never punts down the
middle again; and the coffin flag additionally *skips* the PKPR
trajectory de-rate, so weak punters' coffin punts fly ~20% further than
their normal ones.

## What would be a feature, not a fix

* **Field-position-aware punt power.** The AI's power choice has no
  positional term, and adding one properly means inverting the ballistic
  model (an iterative solve, because flight time depends on launch
  speed). The engine already contains the pattern — the FG aim solver
  `0x0015b1f4` loops on `ComputeKickVelocity` — so this is a code-cave
  project (cave #1 per `code-caves.md`), not a constant edit. A cheaper
  middle path: clamp power by field position, mirroring the FG case's
  `(30 − kickerY)·0.5333` term, ~8 cave instructions.
* **Human coffin corner** — gated out by design; removing it would fight
  the player's own stick.

## Open / unresolved

* **Wind sign may be inverted.** The solver *adds* predicted wind drift
  where compensation should subtract it, and the ±10 sideline-selection
  test then aims *away* from a strong crosswind — so wind may push coffin
  punts back toward midfield. Either the wind vector is signed opposite
  to the position axes, or this is a shipped bug. Not walked to the
  bottom.
* Kick types 1, 4 and 5 get **no accuracy slider at all** (the slider
  consumer only matches slots 2/3). Type 5 uses the punt-*length* slider
  but is invisible to punt *accuracy* — looks like an oversight.
* The "gunners are wider than 8 yards" claim that makes the second coffin
  path dead is a formation inference; needs a rig check.

## Hazard flags

The end-zone clamp itself rides a **branch-likely** (`bc1tl` at
`0x00159a40`), so that reading depends on the delay slot executing only
when taken. The kick-type dispatch also arrives through a branch-likely
delay slot (`0x001b34c8`). And **EE `sqrt.s` takes its operand in `ft`,
not `fs`** — reading it as MIPS-IV would compute `sqrt(0)` and destroy
the flight-time derivation.

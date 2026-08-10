# "Robo QB": why he shreds a blitz and folds to a four-man rush

Investigated 2026-08-10 against `SLUS_207.52` (Madden NFL 2004), closing
the pressure half of open question #10. Companion to `qb-read.md`.

> "Robo QB who always knew when everyone was open, to perfection... For
> years, Madden QBs HATED losing to early pressure and would shred you,
> but would fold like a lawn chair to a normal 4 man pressure if a
> D-Lineman got in."

**Both halves are real, and they have nothing to do with each other.**

## The "robo" half is global, not pressure-conditioned

The pixel-perfect throw is one function, and on success it **zeroes the
entire error vector** and skips the error path altogether:

> `p = 0.5 + THA/200`

| throw accuracy | throws with *zero* error |
|---|---|
| 60 | 80% |
| 80 | 90% |
| 99 | 99.5% |

That is the "down to the pixel" report — and it fires **equally against a
blitz and against Cover 2**. It is not a panic bonus; it is how the QB
throws all the time. (The accuracy slider's zero-crossing is at 0.375;
below that the perfect chance goes *up*, independently confirming the
documented inversion. A Madden card also forces the perfect result.)

## The hypothesis was wrong: pressure does not bypass the read

I expected a panic path that skipped the read evaluation. **Refuted.**
What pressure actually does is cancel a read-*suppression* countdown: the
QB starts a play with reads suppressed for 5 ticks, and enough threat
sets that counter to −1 so the *same* evaluation runs immediately.
Pressure makes the read happen **sooner**, not differently.

**Accuracy is path-independent — a closed negative.** All four throw
sites (normal read, throwaway, dump-off, spike) funnel through one
`StartThrow`, and the "kind" byte selects only *animation timing*.
Nothing in the pressure branch touches the error term, the QB-quality
factor, or the slider.

The genuinely pressure-only behaviours are: escape steering, a
**throwaway** above threat 7.0 gated on `RandInt(0,200) < AWR` (so
awareness ≥ 79 always throws it away, 60 → 76%, 40 → 51%), and a rare
1-in-86 dump-off.

## Why he folds to an ordinary rush — three compounding mechanisms

**1. An engaged rusher is arithmetically invisible.** This is the answer.

The QB senses pressure through an **8-sector threat radar** (8.5-yard
radius, 45° sectors, awareness-attenuated in the frontal half-plane). But
a defender who is *blocked* — engagement kind 4/5/6 — gets his threat
reduced by **9.0**, and the maximum possible raw threat is **8.5**.

> **A blocked rusher therefore contributes exactly zero at any distance
> beyond 4 yards.** Inside 4 yards the penalty is −4.0, which an
> awareness-85 QB still cannot overcome. Even at awareness 100 the
> blocked man must be inside **2.5 yards** to register at all.

So the QB's pressure reading is a *step function of the blocker's
engagement state*, not a distance ramp. By the time the block breaks, the
pocket is already at ~2 yards. **He gets essentially no warning frames.**

| one frontal rusher, awareness 85 | flags pressure | reads immediately |
|---|---|---|
| unblocked | under 5.3 yd | under 3.7 yd |
| **blocked** | **never** | **never** |

**2. An unpressured QB has speed zero.** With the pressure flag clear the
AI contributes literally no locomotion — the dropback is an authored
animation, and after it he stands still.

**3. The read is throttled and the throw is floored.** The read cadence is
3–11 frames by awareness, and no throw at all is permitted for the first
**60 frames** when a human is on the other side.

**There is no sack-specific code anywhere** — the state's exit predicate
returns true unconditionally. A sack is simply the defender's tackle
acting on a stationary target that never saw him coming.

## The scramble trigger (#10f), and prep for #11

Two routes to running: the receiver roulette failing to latch, or a
deliberate scramble roll. The roll requires **all** of:

* the QB's "type" byte == 1 (the scrambler type — values 0/1/2);
* CPU offense only;
* once every 75 frames (2.5 s);
* **the defensive play id is 31/33 (p=1.0), 34 (0.8), or 36 (0.7) —
  anything else and he never scrambles at all**;
* no QB-spy veto, no open-receiver veto;
* then `P = 0.0725 + SPD/200` — linear, no knee (50 → 32%, 99 → 57%).

**For the cross-title diff (#11)**, the five things to compare in
2002/2003 are: whether the QB-type byte exists as three values and gates
the roll; the coverage-id switch (which alone makes scrambling impossible
against most shells); the 75-frame cadence; the speed line; and the two
veto classes. Madden 04's better scrambling most likely comes from the
first two existing at all.

## Cross-reference: the mystery byte appears again

Lane AA found that halfback "running style" comes from `player+0xB07`, a
byte with **no writer at a literal offset** — and if it is zero, HB
vision never runs. Lane Z finds the *same byte* used as the QB
"type" (0/1/2, where 1 = scrambler), with the same finding: 19 reads,
zero literal stores, **roster-sourced via a base-pointer struct copy**.

So the byte is almost certainly populated from the roster database rather
than being dead — which softens Lane AA's worst case. **Naming it needs a
TDB pass, and it is now a shared blocker on two separate findings.**

## Escape direction: "step up in the pocket" is a data table

Six 8-float penalty tables select the escape sector, with backwards
sectors penalised and upfield rewarded. One of them is the anti-safety
override: in his own end zone the backwards penalty becomes 55–60 and the
threat threshold is forced high, so **the QB always throws it away rather
than take a safety**.

## Hazards, and a new tool bug

**`recon/fpudis.py` mis-decodes R5900 `sqrt.s`** — the EE takes its
operand in `ft`, not `fs`, so a listing reads `sqrt.s f0, f0` where the
real instruction is `sqrt.s f0, f20`. Taking the listing literally
invents a bug that isn't there. This belongs with the disassembler debt
that was just paid off.

Also: a delay-slot literal trap where the stored value is the branch's
delay slot rather than the loaded one; a byte-wrap in the cadence
counter that makes the period `rand + 2` rather than `rand + 258`; and
the blocked-discount subtraction lives *inside* a branch-likely slot, so
it runs only when taken.

One caveat on the numbers above: the 3.0 and 7.0 thresholds are tested
against threat **plus** the direction-penalty table, not raw threat. Only
the 2.0 flag test is on raw threat.

# How the QB decides where to throw

Investigated 2026-08-10 against `SLUS_207.52` (Madden NFL 2004), in
answer to the read/progression half of open question #10.

> "How do they know where to throw on a given play?" · "Why do they
> rarely throw to the wide open flats to the HB, FB, or TE?" · "Why do
> they lack confidence to throw vs man coverage?" · "Do they have any
> code/teaching/logic on how to attack coverages?"

## The short version

The QB's target is chosen by a **weighted random draw over a
five-entry priority table authored in the play file** — rolled **once
per snap** and never re-rolled. Per frame, on an awareness-scaled
cadence, he re-scores and may upgrade to a better-weighted open
receiver. He cannot throw at all for the **first 60 frames**. Openness
*is* computed — as pure geometry plus the covering defender's AI state —
and coverage recognition exists, but only as **man vs zone per defender**;
there is no cover-2/cover-3 concept anywhere. **No receiver rating of
any kind is read.** Awareness affects only *when* he looks and *how
precisely he perceives* — never the ranking rule.

## The states and the handoff

| state | role |
|---|---|
| **18** | dropback / read / decide |
| **15** | deliver the ball (pass, handoff, pitch) |
| 65 | spike |

State 18 decides and calls `DeliverBall 0x001c7028`, which builds a
4-byte record `{15, kind, target player number, style}` and requests
state 15. State 15's enter copies the target into a global pass
blackboard whose target byte has **exactly one writer in the image** — so
"who gets the ball" reduces entirely to that call's arguments. Nine call
sites, closed: the human's four, the CPU's one, the throwaway, and the
spike.

## The progression table — real, but its provenance is open

State 18's enter fills a five-slot weight array from the eligible-receiver
enumerator, which reads a **five-entry, four-byte table at
`playRecord + 28`**: each entry is `(receiver's player number, priority
weight)`. It only exists on pass plays.

> **Correction, 2026-08-10.** This section previously said no instruction
> anywhere in the ELF writes that table, and concluded the progression is
> authored content and therefore patchable as data. **Withdrawn.** The store
> sweep behind it could not follow a struct base passed into another
> function. Re-run with cross-function tracking, records from the play-record
> getter are written at +28 — by an eight-byte store spanning bytes 24–31 and
> a float at exactly +28, at two sites. Whether those writes touch *this*
> field depends on whether that getter returns more than one record layout,
> which is unresolved. See `play-data.md` for the addresses and both readings.
>
> The table itself is exactly as described below; only "it comes from the play
> file, so we can edit it" is in doubt.

The primary is then a weighted random draw:

* **weight 0 contributes nothing and can never be drawn;**
* **weight 1 is explicitly skipped** — and this rests on a branch-likely
  delay slot, so misreading it inverts the conclusion;
* weight ≥ 2 is drawn with probability `weight / total`.

The latch is cleared exactly once per snap, so **the primary read is
rolled once and never re-rolled**.

## The re-read cadence, and the two-second hold

The whole read block sits behind a countdown refilled with
`rand(0 … (255−AWR)/32) + 2` frames — so a 99-awareness QB re-reads every
**2 frames**, a 50-awareness QB every 2–6. **A non-QB passer's cadence is
doubled** (a halfback throwing an option pass reads at half the rate).

And a hard gate: **the CPU QB cannot throw during the first 60 frames of
the dropback**, no matter how open anyone is. (This rides a conditional
move; misread, it disappears.)

## Why the wide-open checkdown gets skipped — four mechanisms

Notably, **none of them is a position or route-class weighting.** A
census of the position byte across the entire decision chain finds
exactly one read, and it is the *passer's own*.

1. **A hard shallow-box veto.** A predicted catch point less than **3.0
   downfield of the line and within ±6.0 laterally of the ball** is
   stamped "covered" unconditionally. That is precisely the dump-off to a
   back in the middle. A flat route wide of ±6.0 escapes it; the short
   middle checkdown does not.
2. **A minimum distance.** Any receiver within **5.0 of the QB** is
   "covered" by definition.
3. **Weight 0 and weight 1 are unreachable to the CPU.** A back or tight
   end authored as a weight-1 outlet is *never* a legal CPU target — the
   flag exists so a *human* can pick him. **So the flat bias is authored
   content, and it is a data fix.**
4. One game mode forces every receiver with weight ≤ 4 to "covered",
   restricting the QB to his top reads.

## Coverage recognition: real, but per-defender only

Not a closed negative — the openness evaluator loops over defenders and
branches on **the covering defender's AI state id**: state 22 → the man
arm, states 37–40 → the zone arm. The man arm first checks whether that
defender's man-assignment handle actually points at *this* receiver, then
applies much tighter thresholds than the zone arm (separation rings at
0.5 / 2.0, angle tests at 15° / 25° / 35°, distance rings at 3.5 / 5.0 /
8.0 / 10.0). The zone arm instead asks whether the defender's zone covers
the throw lane, and is far more forgiving.

**So the QB does recognise coverage — as `man vs zone, per covering
defender`. There is no shell label, no cover-2/cover-3, and no defensive
play-record read anywhere in the chain** (closed check across ten
functions). "Attacking a coverage" as a concept does not exist; he
attacks individual defenders' assignment states. That also explains the
"no confidence vs man" report: the man arm is simply a stricter test.

*Cross-lane correction:* the "play-shell predicate" Lane W noted near the
jam tests the **offensive play kind**, not a defensive shell.

## Openness is cached — refining Lane W's negative

Lane W was right that no openness value lives *on a player*, but the
engine does cache one: a **global per-side table**, refreshed in halves on
alternating frames and only while the ball carrier is in state 18 or 15.

## Ratings: awareness only, and one genuinely new use

A closed census over 28 functions finds **exactly three rating reads, all
awareness, all on the passer**:

1. the read cadence;
2. permission to bail to a throwaway under pressure;
3. **the catch-point error radius** — and this one is new. The QB's
   *perceived* catch point is displaced by a random vector of radius
   **20 units at awareness ≤ 50, falling linearly to 0 at awareness 100**.

That third is the first site in this engine where awareness degrades a
**perception** rather than a reaction timer — a real refinement to the
"ratings are decisiveness, not decision quality" law.

**Throw accuracy and throw power are read nowhere in the decision chain**
— they are throw-execution only. And **no receiver rating is read at
all**: who is open is pure geometry plus defender state; who gets picked
is the authored weight.

## Hazard flags and one probable shipped bug

The cadence gate and the weighted-pick loop both hinge on **REGIMM**
branches. The "weight 1 is skipped" semantics exists *only* because of a
branch-likely delay slot. Three conditional moves invert if misread,
including the 60-frame hold.

**Probable shipped bug:** in the second-tier scan, the store that updates
the running best score sits in a `jal`'s delay slot, so it executes
unconditionally — a receiver later rejected by three following tests
still raises the bar for every subsequent slot. Same class as the
pass-protection and route-cadence overflows found elsewhere.

Also: ties in the max-weight scan resolve to the lower slot index, a
systematic bias toward whoever the play file lists first.

**Worth a rig check before quoting publicly:** the shallow-box veto's
axis convention. Whether it means "3 yards past the line" or "within 3
yards of the line" depends on a coordinate reading that was inferred, not
proven.

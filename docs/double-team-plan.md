# Plan: catching a real double team, and fixing it

Written 2026-08-11. `double-team-requirements.md` holds what the fix must
achieve; this holds the order of work and the one thing currently blocking it.

## The blocker: we have never observed a double team

Everything known about this system comes from the image plus one incidental
live read. On **slot 8, no double team was registered anywhere on the field** —
all 22 players read `dt_role` 5 (unassigned) and `dt_record` 0. Slot 6 and 7
have not been checked for one.

So the first task is not a patch. It is **getting a play on the rig that
actually authors a double team**, because:

* Every acceptance test in the requirements needs a **baseline** — R1's "≥1.5×"
  has no denominator without one.
* The state-32 sequence (one-shot trigger → speed grant → decay) has been
  confirmed as a *mechanism* but never observed **in state 32**, only in state
  62. Slot 8 passes through state 32 too briefly to catch.
* A negative is silent: a play with no double team looks exactly like a play
  whose double team we failed to sample. `tools/dt_detect.py` exists to make
  that distinction explicit.

## What the engine says about when one can form

Three constraints, all from `block-cycle.md`, none re-verified here:

| constraint | consequence for play selection |
|---|---|
| **DT-1** `0x001F6560` — double teams register on **run block**, not pass protection | **it must be a run play.** Confirmed compatible with slot 8's finding: a pass state had none |
| **DT-2** `0x001F651C` — a **60-frame** post-snap registration window | it forms inside the first second, or not at all. Sample early |
| **DT-3** `0x001F4AE8` — helper assignment is gated on **play type 2** | some run play *types* may be excluded outright, which would explain a good football call producing nothing |

DT-3 is the uncomfortable one: if the play-type gate is narrow, the answer to
"which play?" may be "almost none of them", and the first patch becomes DT-3
rather than anything about speed or strength.

## Play candidates, best first

I do not know Madden 2004's playbook by name and will not invent entries. These
are described by **football properties** so the operator can find the nearest
equivalent in whatever playbook is loaded. Ranked by how central a double team
is to the concept:

1. **Inside Zone / HB Dive from a two-back set (I-Form or Split Backs).**
   The strongest candidate by some distance. A zone scheme's *defining*
   feature is the combo block: two adjacent linemen take a down lineman
   together and one climbs to the linebacker. If any play in the game
   registers a double team, this is it.
2. **Power / Lead (HB Dive or Off-Tackle behind a fullback).** Playside double
   team at the point of attack while the backside guard pulls. This is also the
   family the operator's SME described for slot 6 — LT and LG on the right DT,
   the other DT left for the guard's kick-out.
3. **Counter.** Playside double team plus two pullers.

~~Goal-line or short-yardage dive~~ — **withdrawn, see the defence rule below.**
It was listed on the reasoning that heavy personnel means more linemen and so
more combos. That is backwards: a goal-line *defence* fields five or six down
linemen against five blockers, which leaves **no uncovered lineman** and
therefore no one free to combo. Heavy personnel is the worst case, not the best.

**Do not spend attempts on:** any pass play (DT-1 excludes it), screens, or
tosses/sweeps — on the last two the linemen are moving laterally in space and
are least likely to be paired on one man.

**Formation note.** Slots 6 and 7 are single-back 3-WR. The candidates above
want a **fullback on the field**, so this will need a different personnel
grouping than either existing savestate, and probably a different formation
entirely.

## Which defence — this decides it as much as the play does

A combo block exists only where an **uncovered** lineman stands next to a
**covered** one: the uncovered man helps on his neighbour's defender, then
climbs to a linebacker. So the arithmetic is simply

> **spare blockers = 5 offensive linemen − down defensive linemen**

and the defence controls that number entirely. Zero spare blockers means zero
double teams no matter how the play is drawn.

| defence | down linemen | spare blockers | verdict |
|---|---|---|---|
| **3-4 base, nose head-up on the centre** | 3 | **2** | **best.** A nose tackle in the centre's face is the most-doubled man in football, and either guard can help |
| **4-3 base** (over or under), no blitz | 4 | 1 | good. Exactly one combo available — usually centre + guard on the 1-technique, or guard + tackle on the 3-technique |
| Nickel / dime | usually 4 | 1 | same front as 4-3; only the coverage changes. Fine, but no advantage |
| **Goal line / 5-6 man front** | 5–6 | **0** | **useless.** Every lineman is covered; nobody is free to help |

**Do not call a blitz.** An extra rusher is an extra man to block, which
consumes the spare blocker who would otherwise be the helper. The whole point
is a *surplus* of blockers.

**Run at the doubled man.** The combo happens playside, so the run has to
attack the gap next to the nose or the 3-technique. A run away from the front's
strength gives the helper nobody to pair on.

**Recommendation:** *3-4 base, no blitz, run inside zone or a lead/power dive
straight at the nose tackle.* That is the maximum-probability combination — the
most spare blockers, the most doubled position in football, and the play family
built on combo blocks. If that produces nothing, the gate is in the engine
(DT-3) and not in the play call, which is exactly the conclusion step 4 below
is designed to reach.

## The procedure

1. **Detect, do not guess.** On the rig:

   ```
   python3 tools/dt_detect.py
   ```

   It waits for the snap, watches the 60-frame window, and prints whether any
   player took a `dt_role` other than 5, plus whether anyone entered state 32.
   Roughly thirty seconds per play, and it distinguishes "no double team" from
   "we missed it" by reporting the highest snap frame it reached.

2. **Work down the candidate list** until one registers. Record which play it
   was — that identification is the deliverable of this step and it is
   currently missing from the repo entirely.

3. **Save it as a scratch slot (9+)** at the pre-snap frame, archive it to
   `experiments/states/`, and add a row to that README. Slots 1–5 are the
   operator's; 6, 7 and 8 are taken.

4. **If nothing registers on any candidate**, that is a finding, not a failure:
   it points at DT-3's play-type gate, and the first patch becomes "widen the
   gate" rather than anything in the requirements doc. Test that by applying
   DT-3 (`0x001F4AE8`, branch → `nop`) alone and re-running the detector on the
   same play — which is exactly the per-patch isolation rule 2 requires.

## Then, and only then

5. **Baseline** the registered double team: `dt_role` per player, the primary's
   contest components, the helper's `speed_cmd` and `player+0x0C` bit 2, and
   the doubled defender's displacement. This is R1's denominator and R3's
   "should measure ~0".

6. **Catch the state-32 sequence in state 32** — one player, every frame,
   the way the slot 8 probe finally worked. An 11-player loop samples one frame
   in six and will miss a one-frame trigger; that mistake is already recorded.

7. **Only then** design patches, one requirement at a time, each with its own
   toggle and its own savestate, per rule 2.

## Two traps already paid for

* **`0x005fef3c` (0.46) is not a safe knob.** It looked like a sole-reader
  constant for the two-man animation. A player in **state 33** was measured
  receiving exactly 0.4600, so run blocking shares it.
* **States 47 and 72 differ only in `enter`** — they share `ai_think`,
  `can_leave`, `user_think` and `extra` (`state-dispatch-table.md`). 26 handler
  addresses are shared across states. Check that table before patching any
  handler, or a lead-blocker fix silently becomes a dispatcher fix too.

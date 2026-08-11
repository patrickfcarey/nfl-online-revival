# Defender catch — an observed defect, and the requirement for the fix

Recorded 2026-08-11 from the operator. **Nothing here is designed, measured in
the harness, or patched.** Three open questions gate all of it.

> "A cornerback's ability to catch a ball that hits their hands is directly a
> percentage chance based 100% on their catching ability."

## OPERATOR OBSERVATION — this is a defect, not only a preference

The requirement arrived as a design preference. Within minutes it became a bug
report, and the distinction changes what to go looking for:

> "3 times now the corner back has had it touch their hands and they don't
> catch it" … "4 times" … "now 5" … "it's basically happening every time" …
> "I think it is literally happening every time."

**Five-plus consecutive failures is not a low probability. It is a gate.**
A 30%-catching corner missing five in a row has probability 0.17; a corner
with any normal rating missing *every* one over an extended session has
probability indistinguishable from zero. Something is refusing the catch
outright rather than rolling badly.

That reframes the investigation, and it is worth being explicit about the
redirection because the natural instinct is wrong here:

* **Do not** go looking for a rating formula to rebalance. A formula produces a
  distribution; the operator is reporting a constant.
* **Do** go looking for an early return, a failed admission test, or a branch
  that sends the defender down a deflection path instead of a catch path. This
  engine has a documented habit of exactly this — `punt-logic.md` (#17) found
  coffin-corner logic fully implemented and gated off, and `fb-wr-blocking.md`
  (#15) found the FB and slot WR excluded from the blocker pool by an
  admission test rather than by any tuning value.

Operator observations are treated as evidence in this project because they
have repeatedly corrected the instrument. This one has not yet been reproduced
in the harness, and that is the next step, not a reason to discount it.

### A specific hypothesis, clearly labelled as one

`catch-and-fumble.md` establishes that a ball jarred loose inside the
unsecured-possession window posts **the same event a failed catch roll posts —
an incompletion**, and that a per-frame "hands" check runs for the first ~10
ticks with no defender involved.

If a defender's interception enters that same unsecured window and the hands
check resolves against him, the on-screen result is precisely what the operator
describes: the ball visibly touches his hands and the play is ruled incomplete.
**This is a hypothesis with a plausible mechanism, not a finding.** It is
recorded because it names a specific address range to look at first
(`SecurePossession` at `0x00258020` and the hands check around it), and because
if it is right the defect is in the *window*, not in the catch roll — which
would move the whole fix.

## What this is about, and what it is not

**It is** the roll that happens at the moment a thrown ball arrives in a
defender's hands — the interception attempt itself.

**It is not** the post-catch strip, the unsecured-possession window, or the
per-frame hands check. `catch-and-fumble.md` documents those in detail and they
are a *different mechanism* firing at a *later moment*. A patch aimed at this
requirement must not be validated against those numbers.

**It is not** whether the defender gets to the ball at all. Pursuit, break on
the ball, and coverage positioning decide that; this requirement begins after
the ball is already on his hands.

## Scope: explicitly unscoped from the pass-protection work

This lives in the catch/possession path. The change currently in flight
(`experiments/pass_protection.py`) lives in the block-contest path at
`+0x414`/`+0x418`/`+0x41C`. **Different code, therefore a different change**,
with its own acceptance test — CLAUDE.md rule 1. It is filed here rather than
folded into the work in progress for exactly that reason.

## R1 — The defender catch roll reads Catching and nothing else

**Statement.** When a thrown ball reaches a defender's hands, the probability
that he secures it is a function of his Catching rating alone. No other player
rating contributes.

**Acceptance test.** Two arms, both required:

| arm | vary | hold | expected |
|---|---|---|---|
| A | Catching across its range | every other rating | catch rate tracks the intended curve within ±5 pts |
| B | Awareness, Jumping, Speed, Agility | Catching fixed | catch rate **flat** within noise |

Arm B is the one that actually tests the requirement. Arm A alone would pass on
an engine that reads Catching *plus* three other things.

**Sample size.** This is a Bernoulli outcome, so it needs real N — and on a
deterministic engine a savestate replayed N times returns the same coin flip N
times. The trial has to vary the seed or the arrival conditions, or the metric
is one sample repeated. `pass_protection.py`'s determinism result makes this a
live hazard, not a theoretical one.

## Open questions that gate this — do not build past them

**Q1 — ANSWERED 2026-08-11 against the image: YES, they share the path.**
See "Q1 in full" below. Side is a *parameter* inside shared code, not a router
to separate code — but the side-dependent branches are few, localised, and
**two of them are defender-only**, so a fix does not have to touch receiver
behaviour. That last part materially narrows the blast radius the requirement
first appeared to carry.

**Q2. What does the roll read today — and is it reached at all?** UNKNOWN, and
the operator's near-100% failure rate means the second half is the more urgent
one. `catch-and-fumble.md` gives the post-catch formulas precisely but does not
give the catch roll's own inputs. "100% catching" is a change *from* something,
and that something is unrecorded. If the roll is never reached on the defender
path, its inputs do not matter yet.

**Q2b. Baseline first.** Before any patch, measure the current rate. The
requirement's acceptance test below assumes a distribution to move; if the
observed baseline really is zero, the test that matters is binary — *does a
defender ever complete an interception at all* — and the arms in R1 only become
meaningful after that answers yes.

**Q3. Does the engine represent "the ball hit his hands" as an event?** There
is a per-frame hands check for ~10 ticks *after* possession, but a pre-catch
"ball arrived in the catch radius" event with its own roll is unlocated.
Without it there is no place to hang the requirement.

## Q1 in full — receivers and defenders share the catch path

Derived from `SLUS_207.52`, 2026-08-11. Two of our own published addresses for
this area are mid-prologue (`catch-and-fumble.md`'s hazard note); the real
entries used here are **`0x00255248`** (ball-arrival resolver) and
**`0x002565E0`** (interaction handler).

### The chain is single-threaded

| function | callers |
|---|---|
| interaction handler `0x002565E0` | **1** — `0x00203f20` |
| ball-arrival resolver `0x00255248` | **1** — `0x002566b4`, inside that handler |

There is no second entry point. A ball meeting a player goes through one
handler into one resolver whichever side the player is on.

### The side test does not route to different code

At `0x00203eec`, immediately before the handler call:

```
00203eec  jal 0x00260598          ; offense_team()
00203ef0  lbu s0, 1(s1)           ; the player's side byte (+1)
00203ef4  bne s0, v0, 0x00203f1c  ; DIFFERENT side -> straight to the handler
00203efc  lw s0, 192(s2)          ; SAME side -> read a timestamp
00203f04  addiu s0, s0, 5         ; +5 frames
00203f0c  bne s0, zero, 0x00203f1c
00203f14  beq zero, zero, 0x00203f2c   ; lockout unexpired -> skip the handler
00203f20  jal 0x002565e0          ; BOTH sides arrive here
```

`0x00260598` is `[0x00601F4C]+0x40` — the spot object, the same pointer the LOS
comes from. The function immediately after it, `0x002605b0`, is byte-identical
plus `xori v0, v0, 1`, so the pair is **offense_team() / defense_team()**.

That `+5` is the "**5 frames** offense re-contest lockout" already recorded in
`catch-and-fumble.md`'s window-constant list, which confirms the direction
independently: **the lockout falls on the offence.** A defender reaches the
handler with no such delay. At this level the engine favours the defender.

Inside the handler there is no side test at all — only `kind == 1` (this is a
player) and an optional `state == 28` gate.

### The four places side actually matters

All four are inside shared functions. None of them selects a different one.

| site | effect |
|---|---|
| `0x00203ef4` | 5-frame re-contest lockout, **offence only** |
| `0x002553e0` | the same event resolves to code **153 for a receiver, 179 for a defender** |
| `0x0025545c` | **defender-only** admission gate — see below |
| `0x00255ff4` | "is on offence" computed by `xor` and passed as a boolean into `0x00255538` |

`0x002553e0` reads with care because the answer sits in a delay slot:

```
002553ec  bne v0, v1, 0x002553f8
002553f0  addiu v0, zero, 179     ; delay slot -- ALWAYS executes
002553f4  addiu v0, zero, 153     ; only when the branch is NOT taken
```

Branch taken means `offense_team() != player_side`, i.e. a **defender**, and the
stored code is **179**. A receiver falls through and overwrites with **153**.

### The lead: a defender-only gate that can refuse outright

At `0x0025545c`, on dispatch code 68:

```
0025545c  jal 0x002605b0          ; defense_team()
00255464  lbu v1, 1(s1)           ; player side
00255468  bnel v0, v1, 0x00255484 ; NOT a defender -> skip (branch-likely)
00255470  jal 0x00260688          ; defender only
00255474  daddu a0, zero, zero    ; a0 = 0
00255478  beq v0, zero, 0x002554c8  ; returns 0 -> bail out with 256
```

`bnel` is branch-*likely*: its delay slot is annulled when the branch is not
taken, so only a defender falls into the `0x00260688` call.

`0x00260688` is a bit-flag getter — `[0x00601F4C]+0x3C`, returning bit `a0`:

```
00260688  lw v1, -14244(gp)       ; the spot object
0026068c  lw v0, 60(v1)           ; +0x3C, a 32-bit flag word
00260690  srav v0, v0, a0         ; v0 >>= a0   (see the caution below)
00260698  andi v0, v0, 0x0001
```

`0x002606a0` is its setter. So **a defender's ball interaction on this dispatch
code requires bit 0 of a game flag word to be set, and returns "nothing
happened" (256) if it is clear.** That is exactly the shape of defect the
operator's near-100% failure rate points at, and it is defender-only, so
changing it cannot alter a single receiver catch.

### Bit 0 has exactly one writer, and it is a latch

Read at **81** sites; set or cleared at **one** — `0x00253ba0`, inside the same
ball region:

```
00253b74  jal 0x00260688          ; read bit 0
00253b78  daddu a0, zero, zero
00253b7c  bne v0, zero, 0x00253d84 ; ALREADY SET -> skip; this is a latch
00253b84  lwc1 f1, -23284(gp)      ; 0x005ffbfc = 0.30
00253b8c  add.s f0, f0, f1         ; [sp+36] + 0.30
00253b90  c.lt.s f0, f2            ; < [sp+4] ?
00253b98  bc1f 0x00253c8c          ; not less -> leave the bit clear
00253ba0  jal 0x002606a0
00253ba4  addiu a1, zero, 1        ; a1 = 1 -> SET bit 0
```

So bit 0 is a **once-per-play latch, set when a float clears a 0.30-yard
margin** — and until it latches, the defender-only gate at `0x0025545c` refuses
the interaction and returns 256.

That is a coherent mechanism for the reported symptom: a defender whose hands
meet the ball *before* the latch trips is refused outright, the ball stays in
flight, and on screen it reads as a corner dropping a ball that hit his hands.

**Still not proven to be the cause.** What the two stack floats measure is not
established, so "before the latch" cannot yet be turned into "before the ball
has travelled 0.3 yards past X". The scan that found the single writer also has
a caveat worth carrying: an earlier version of it matched only
`addiu a0, zero, K` and reported **zero** sites for bit 0, missing every
`daddu a0, zero, zero` — the canonical zero idiom, and the form used at both
the gate and the writer. Same false-negative class as the gp-relative misses.
Re-derive before relying.

### TESTED 2026-08-11 — bit 0 is NOT the cause

Read live from slot 8, a savestate the operator set up with **the ball in the
air**:

```
frames_since_snap = 207    spot_flags = 0x00004009    bit0 = 1
frames_since_snap = 248    spot_flags = 0x00004009    bit0 = 1
```

**Bit 0 is already set while the ball is in flight.** It is a latch, so once set
it cannot clear again for the rest of the play — which means the defender-only
gate at `0x0025545c` is **open** for every ball interaction that follows. It
cannot be what refuses the corner's catch.

The candidate is dead, and that is worth recording rather than quietly dropping:
81 read sites, one writer, a clean latch mechanism and a plausible story, and it
is still wrong. The mechanism is real; it just is not this defect's cause. What
the two stack floats at `0x00253b8c` measure remains unestablished, and bits 3
and 14 (also set, `0x4009`) are unidentified.

**The investigation returns to Q2:** what the catch roll reads on the defender
path, and whether it is reached at all. The other three side-dependent sites
from the Q1 census are untested — in particular `0x002553e0`, where the same
event resolves to **153 for a receiver and 179 for a defender**, and
`0x00255ff4`, which passes an "is on offence" boolean into `0x00255538`.

`World.spot_flags()` stays: the word is a general per-play event register with
~900 call sites, and slot 8 shows bits 0, 3 and 14 live.

> **Caution on the listing.** `recon/mipsdis.py` prints `SLLV`/`SRLV`/`SRAV`
> with `rs` and `rt` swapped (`pass-vs-run-blocking.md`, standing disassembler
> debt). The shift above was decoded by hand from the word `0x00821007`:
> rs=`a0`, rt=`v0`, rd=`v0`, funct=7, so it is `v0 = v0 >> a0`. Do not read the
> printed operand order for these three opcodes.

### What this changes about the requirement

The blast radius is **smaller than feared, not larger**. "Shared path" sounded
like "any change touches every catch in the game", and that is true of the
*roll* — but three of the four side-dependent sites are already
side-conditioned, and two are defender-only. A patch aimed at those cannot
regress receiver catches, which removes the largest objection to attempting one.

## A design conflict the operator has to settle

If the roll becomes **100%** Catching, the Interception slider has nothing left
to scale. Two options, and this is a preference, not a technical question:

1. **Slider scales the Catching input** — the rating still fully determines the
   roll, and the slider shifts the whole population up or down.
2. **Slider becomes a no-op on this path** — literal compliance, at the cost of
   removing a control that people currently use to tune INT rates.

Recommend (1): it keeps the requirement's intent — a corner's hands are his
rating — while leaving the tuning surface intact. Not actioned either way.

## Situations this must not break

* Receiver catches, if Q1 shows the code is shared.
* Tipped and deflected balls, which may enter the same path with different
  arrival state.
* Diving catches — a 41-tick unsecured window against the usual 21.
* The process-of-the-catch rule: a ball jarred loose inside the window posts an
  **incompletion**, not a fumble. A patch that turns those into catches has
  broken a rule the engine currently implements correctly.

## Anti-goals

* **No warping.** Raising the interception rate by snapping defenders onto the
  ball satisfies the metric and makes the game worse. Same anti-goal as the
  lead-blocker work.
* **No CPU/human asymmetry.** The roll must not read which side is controlled.

## Status

Requirement recorded, unscoped from the work in flight, and **blocked on Q1–Q3**
— all three are static analysis against `SLUS_207.52`, no rig time needed.

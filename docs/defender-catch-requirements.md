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

**Q1. Is there a distinct defender catch roll at all, or do receivers and
defenders run the same code?** UNKNOWN. This is the blast-radius question: if
the roll is shared, then satisfying this requirement changes **every receiver
catch in the game**, which is a far larger change than the one being asked for
and would need its own regression suite. Nothing may be designed until this is
answered against the binary.

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

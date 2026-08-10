# Press/jam, route running, and receivers held up in traffic

Investigated 2026-08-10 against `SLUS_207.52` (Madden NFL 2004), in
answer to open questions #20, #20b and #21.

## Headline: linebackers can already jam

**The premise recorded in the ledger — "linebackers cannot jam at the
line" — is wrong, and the correction matters because a fix was scoped
against it.** A closed caller census of the jam-initiation function
(exactly four callers, no tail-calls, no data-word references) shows LBs
reach it on **three of the four paths**:

| path | who may jam | gate |
|---|---|---|
| man coverage (state 22) **enter**, CB arm | corners — *and any position* under one condition | receiver's play class |
| man coverage **enter**, LB arm | **positions 13/14/15 — linebackers explicitly** | receiver's play class, probability 0.8 |
| man coverage **think** (per frame) | **no position check at all** — the dispatch table routes all of 13–18 | the shared eligibility helper |
| zone state 37 **enter** | corners only | direction flip |
| zone state 38 (hook/curl) **think** | **no position check** | a receiver must already be *inside the zone rectangle* |

The shared eligibility helper checks play phase, that the defender is
downfield of the receiver, that nobody else is already jamming that man,
a facing difference over ~125°, and lateral distance under 3.0. **No
position check.** Nothing downstream is corner-specific either: a closed
field census over the jam and the press state finds **zero reads of the
position byte and no weight, height or size field anywhere**. The
contest actively *favours* a linebacker over a tight end.

**The real gap is zone, not man.** A linebacker in man coverage jams at
assignment time and every frame after. A linebacker in the underneath
hook zone can only jam a receiver **already inside his zone rectangle** —
which a tight end releasing off the line typically is not yet. Widening
the corner gate on state 37 buys nothing, because linebackers do not run
that state.

So the community's "LBs can't jam" is most likely **"LBs in zone don't
jam a releasing tight end."** The targeted fix:

| # | goal | where | risk |
|---|---|---|---|
| **W1 (recommended)** | let an LB in the hook zone press a releasing receiver | the zone-14 rectangle data — pull the near edge back to the LOS | **Low** — data, per zone kind, no control flow |
| W2 | raise the LB man-press rate | the 0.8 probability float (one reader) | Low |
| W3 | let safeties take the LB man-enter arm | widen the position range from 3 to 6 | Low-Med |

**Verify on the rig before spending anything here** — the prediction is
that LB jams already occur in man coverage today.

## The jam contest: strength and *agility*, not awareness

Two rolls. Initiation is a flat `RandFloat < p` with **no ratings at
all** (p = 1.0 / 0.9 / 0.8 / 0.5 / 0.3 by path). The contest lives in the
press state and fires when the pair closes:

> `P(defender wins) = 65 + (50/255)·[ (dSTR+dAGI)/2 − (rSTR+rAGI)/2 ] + modifiers`

vs a single `RandInt(0,100)` draw.

**Verdict on the community's guess:** strength — confirmed. Awareness —
**refuted**; the second term is **agility**. Awareness appears nowhere in
the jam.

**And the ratings barely matter, which is the real story.** Every
realistic matchup lands between **57% and 74%**, because the base is a
flat 65 and the rating term only reaches ±50 at an impossible ±255
spread. An elite corner against a weak tight end wins 74%; a weak corner
against an elite receiver still wins 57%.

Three additive modifiers apply before the draw, including a **flat +50**
when a play-shell predicate holds (almost certainly bump-and-run;
unconfirmed) — that single term takes any matchup to a **guaranteed
win**, dwarfing every rating.

**Difficulty does not appear in the jam code at all.** It enters only
through the effective-ratings transform, worth +3 to +5 points for a CPU
defender. So "worse on All-Madden" is a small real effect layered on a
flat 65% base. *Note for the online project: the human's difficulty
exemption applies only when exactly one side has a human controller — in
head-to-head both teams take the class, compressing ratings further.*

**"Constantly" is attempt frequency, not per-attempt odds.** The
per-frame jam call retries every frame the geometry allows, so at ~65% a
press is effectively certain.

**What winning does:** not a speed penalty or a computed knock-off-path,
but a **paired canned animation plus a state lock** — the receiver is
pushed into the press state too and his route is frozen for the
animation's length (a frame count only measurable on the rig). Exit
severity picks a clean release, one of two rare animation sets, or (~90%
of the time) a stumble-recover state.

**Correction to the earlier anchor:** the 50% coin flip in state 37's
enter is *not* a gate on whether the jam fires — it picks **which
shoulder** the defender presses (±60°). The attempt gate is a 0.9 roll.

### Press alignment vs the contact mechanic

Man coverage latches an alignment tag once, in the enter, from lateral
distance (< 3.5 → press, < 5.5 → trail, else off), and uses it to select
a **technique** from a function table — where "off" is a literal stub
that runs no technique at all.

But **the contact mechanic is separate and not gated by the tag**: the
jam call sits earlier in the same think, and the technique dispatch is
only reached on the fall-through. A defender tagged "off" still jams if
the geometry allows. Two consequences: man coverage genuinely leads to
contact, and the alignment is **never re-evaluated**, so a receiver who
motions after the tag is set keeps a stale technique.

## #20b — receivers captured by traffic (confirmed, and separate)

Proof is in the receiver's own code: the route state's think reads its
own engagement kind and, if engaged, **abandons the route entirely** —
the whole steering chain is bypassed and the engagement manager (which
runs after the AI loop) stamps the frozen shove axis into his locomotion.

* **No exemption for a route-runner.** The pairing test reads only facing
  difference and distance, then writes the kind into **both** players. No
  AI-state read, no role byte, no eligibility flag.
* **No escape, human or AI.** Closed caller set for the shed move: the
  route state's AI-think calls none of them, and neither does its
  USER-think. A captured receiver is passive until the defender-set timer
  expires — 15–30 frames, re-armed on every re-contact.

**On screen this is indistinguishable from a jam**, through a completely
different code path. That is precisely why player reports cannot separate
#20 from #20b, and why they stay separate findings.

Fixes are all code caves: a **capture exemption** for a releasing
receiver (the honest one — it prevents an engagement that should never
have formed), giving the route state a shed, or shortening the lock for
receivers only.

## #21 — route running: "awareness + athleticism" is refuted

The route is **authored data executed by a steering chain with no quality
term and no contest against the defender.** Waypoints come from the play
record in the state's *enter*; the think reads no play data at all. There
is even an authored release delay encoded in the state record.

**A closed ratings census over eleven functions yields exactly two
hits** — pass blocking and awareness, combined into a **reaction-cadence
byte**. No agility, no acceleration, no speed, no catching anywhere in
the route chain. Same law as everywhere else in this engine: *ratings are
decisiveness, not decision quality* — and here awareness is diluted by
**pass blocking**, of all things.

**A probable shipped bug:** that cadence refill sign-extends from bit 8,
so once pass-block + awareness exceeds 255 the term goes negative and the
cadence gets **worse**. Any competent receiver is on the wrong side of
the fold. (This is the *same class* of overflow found independently in
pass protection — see `pass-vs-run-blocking.md`.)

**Difficulty does enter route running**, newly found: the receiver's
estimate of where the ball is refreshes every 2/4/6/8 frames by
difficulty, with a **rating-independent** ±5-yard error. A 99-awareness
receiver gets the same noise as a 40 — only the refresh rate moves, and
difficulty sets that, not the player.

**No contest against coverage exists.** The only defender the route code
touches is a capture partner. Coverage is entirely the defender's
problem.

## Separation / openness: not found (partial negative)

No cached separation or openness quantity exists on a player. One
near-miss recorded so nobody re-chases it: the route code does loop over
up to five players computing distances — but the side filter is **self's
own team**, so it is teammate route-crossing awareness, not separation
from coverage.

This is a *partial* negative: the QB's target-selection states were not
walked. **Open question #10 still needs the QB-side census** and should
be relaunched as its own lane.

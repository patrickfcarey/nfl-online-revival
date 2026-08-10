# What we have found inside Madden 2004

This is the plain-English version. Everything here was read out of the game's
own program code, then independently re-derived by a second pass that did not
know what the first had concluded. Where the code contradicts something the
community has believed for twenty years, the code wins, and we say so.

**About the confidence scores.** Each section ends with one. It is not a
mood — it reflects how the finding was established. Roughly:

* **95–100%** — re-derived independently more than once, matching down to
  individual instructions, and any arithmetic recomputed exactly.
* **85–94%** — the mechanism is certain; some specific numbers depend on
  details we could not pin down, or one part rests on a single derivation.
* **60–84%** — the structure is solid but a meaningful piece is inferred.
* **Below 60%** — we know something real is there and can point at it, but
  the interpretation could still change.

---

## The one idea that explains most of the game

Almost nothing in this engine is about *how well* a player does something.
It is about **how often he is allowed to think.**

Nearly every rating we traced ends up controlling a countdown timer. A player
with high awareness does not make smarter decisions than a player with low
awareness — he makes the *same* decision, sooner and more often. A low-rated
player is running on stale information: he re-checks the situation every
eight frames instead of every frame, and in between he keeps doing whatever
he last decided to do.

This is why maxing ratings in this game feels strange. You are not buying
better judgement. You are buying reaction speed.

It also explains much of the "the CPU cheats" feeling. On harder difficulties
the CPU's timers get shorter. It is not handed better information or better
outcomes in most places — it is simply allowed to react more often than you
are.

> **Confidence: 95%.** This pattern was found independently in coverage,
> blocking, ball carrying, quarterback reads and pass protection. The one
> caveat is that it is a generalisation across many separate systems rather
> than a single thing we can point at.

---

## Sliders

### There are far more sliders than the menu shows

The game keeps a table of **131 settings**. The gameplay menu exposes a small
fraction. The rest are real, they are read by real code, and several control
things players have wanted access to for years.

### Every slider does the same thing, mathematically

There is exactly **one** routine that applies a slider, and all eleven places
that use one call it. The rule is always:

> **50 is dead centre and changes nothing at all.** Above 50 the underlying
> number scales up, below 50 it scales down, by a fixed amount per slider.

We checked this to the last decimal: at exactly 50 the value comes out
bit-for-bit unchanged. That is exact, not approximate.

What differs between sliders is only *how hard* each pulls — some by half a
percent per point, some by three quarters. None is a switch, and none has a
hidden step. **There is no magic slider value.**

### Sliders are weaker than people think, and some are inert

* Several penalty sliders are **completely inert above 50** — the code sets
  their floor and ceiling to the same value, so raising them does nothing.
* One penalty (Personal Foul) is stored, has a slider position, and is
  **never read by anything.**
* Four penalty types exist in the game's own list and are **never generated
  at all**: Delay of Game, Encroachment, Unsportsmanlike Conduct, Personal
  Foul.
* The Facemask penalty has a 15-yard branch that can never be reached — its
  chance is hard-set to zero while the code that would trigger it is live. An
  unfinished feature sitting in the shipped game.

### A slider that behaves backwards

**Knockdowns** controls how often a defender swats a pass out of the air. Its
range runs from about **13% to 87%**, centred on 50%. Nothing about it is
gated on how deep the pass is — a bomb is as swattable as a five-yard out.
Turning it up does not make defenders better at defending; it makes deep
passes get knocked down.

### Penalty sliders have a kink at 50

Penalties do not scale like everything else. Below 50 the frequency ramps
smoothly toward zero; above 50 it ramps toward a per-penalty ceiling. The two
halves meet at 50, and **only at exactly 50** do both formulas agree
precisely.

Setting a penalty slider to **0 turns it off completely** — a hard off
switch, not "very rare".

### One slider genuinely misbehaves

The False Start slider's effect is **squared**, not linear. Going from 50 to
100 makes false starts roughly **four times** as likely, not twice. The
pre-snap flinch sharing that penalty is linear — so the two halves of "false
start" respond to the same slider at different rates.

> **Confidence: 97%.** The strongest material here. The whole system was
> re-derived twice and every number recomputed bit-for-bit, including the
> 13–87 range checked under two different rounding assumptions. The only
> unverified part is how the menu screens bind to these settings, which needs
> game files we do not have on hand.

---

## Do ratings have hidden thresholds?

**Mostly no — and this surprised us.**

We counted every place the game reads a player rating during play: **278 of
them**. Around 87% feed straight into continuous arithmetic. A 71 is very
slightly better than a 70, and that is all. There is no secret tier system.

The real exceptions matter, though:

* **Power pass-rush moves require Strength 66 or better.** Below that the
  move is simply unavailable.
* **Juke and spin require Agility 66 or better.** Same cliff.
* **Throw Power below 71 contributes literally nothing** to one calculation.
* **Carrying below 50 is hard-zeroed** in one place.
* **Agility below 50 gets no bonus at all** in two places.
* **Strength is clamped to roughly 39–78** in the tackle calculation. A
  340-pound nose tackle and a 250-pound end are equally strong there.
* **Speed below 59** falls back to a fixed value in one movement
  calculation.

And there is broad banding: several systems chop ratings into **16-point
bands**, and inside a band players are identical. Break Tackle 94 and Break
Tackle 88 are **the same number** to the tackle system.

### The 40 / 44 / 55 blocking mystery

Testers reported pass-block ratings behaving as though something changed at
40, 44 and 55. We found two mechanisms that could each produce a step.

The likely answer is **banding in how long a blocker stays locked onto a
defender**. It produces three genuinely distinct behaviours at those values,
and does so regardless of awareness — which matches testers seeing the same
boundary on two very different players. The competing explanation, an
arithmetic overflow in a timer, would predict **44 and 55 behaving
identically**, and testers say they do not.

> **Confidence: 93%** for the threshold catalogue — the 278-site census
> reproduced exactly, and each gate was re-derived. **70%** for the
> 40/44/55 ruling specifically: both mechanisms are real, we are reasoning
> from which one fits the reports better, and only a hardware test settles
> it.

---

## Blocking

### The biggest finding: a defender in coverage cannot be blocked

This is the answer to "why doesn't my slot receiver ever seal the corner?"

The engine assigns blockers to defenders every frame. Before it does, it
builds a list of defenders *eligible to be blocked* — and that list accepts
only defenders who are **chasing the ball carrier, rushing the passer, or
waiting pre-snap**, or who are human-controlled.

**A cornerback in man or zone coverage is in none of those states.** He is
invisible to the blocking system. No blocker of any position can be assigned
to him, and a second independent check backs this up by scoring coverage
defenders below the threshold the system requires.

He becomes blockable only when he abandons coverage to chase the ball.

That is why the complaint is loudest about the slot: the slot defender is the
man a receiver most obviously *should* seal, and with him excluded the
receiver's next-nearest option is a safety several yards away. On screen that
reads as the receiver wandering.

### The fullback has a different problem

A fullback leading through the hole is excluded from the assignment system
**entirely**, for a separate reason: lead blockers are put in a special mode,
and the eligible-blocker list accepts only the two ordinary modes. He is
never considered at all. His only guidance is a simple "lean toward the
general area" behaviour of his own.

The fullback and the receiver therefore fail for unrelated reasons, and
fixing one would not fix the other.

### No play in the game says who to block

Checked exhaustively from both directions. Play data can tell a blocker to
pass protect, run block, lead through a gap, or follow a teammate — but it
can never name an opposing player. Every player reference a play can contain
is looked up on the blocker's **own team**.

"Block the man over number two" does not exist in this game. Targets are
chosen by the engine, live, every frame, on geometry and a threat score.

### Receivers get no blocking logic of their own

The engine has custom pairing logic for halfbacks, tight ends, tackles,
guards and the centre. **Wide receivers get the generic fallback** — the same
path used for anything unrecognised. And the halfback/fullback logic is
**pass-protection only**, so on a run play a fullback is scored exactly like
a receiver.

### Double teams do exist

The community has long said Madden 2004 has none. **That is wrong.** There is
a full system: a registry of who is helping whom, distinct roles for primary
and helper, a scorer deciding when help is warranted, a promotion when a
second blocker joins, and a clean peel-off when he leaves.

One detail worth knowing: when two players block one defender the effect is
**not** the sum of their blocking. The second blocker applies a *penalty* to
the defender rather than adding his own strength — which is why double teams
never feel as decisive as they should.

### Pass blocking and run blocking are separate systems

Two distinct behaviours with separate code, converted between live —
including mid-play if a pass turns into a run. Which one a lineman is doing
depends on the play type, a live flag, and whether anyone is lined up over
him. The block sliders scale **three components of the blocking contest**,
not a single "push" value.

### A real bug in pass protection

There is an arithmetic overflow in how often a pass blocker re-evaluates.
Once a lineman's pass-block and awareness ratings **add up to more than 255**,
a number wraps around and his re-evaluation gets *worse*. An elite lineman
with high awareness can react more slowly than a slightly worse one. The
equivalent run-blocking code does the same calculation correctly, so this is
a mistake rather than a design choice.

> **Confidence: 95%.** The eligibility gates, the double-team system and the
> two-system split were each re-derived independently and matched almost
> instruction for instruction, and the overflow arithmetic was recomputed by
> hand. The one soft spot: we cannot yet read the shipped play files, so we
> can prove a receiver *can* be told to block but not what any given play
> actually tells him.

---

## Pass rush

### Finesse and power are real, and richer than expected

Every rusher gets a hidden **three-axis profile** stamped on him when he
engages: a power score (blocking/tackling plus strength plus **weight**), a
finesse score (adding awareness and agility), and an overall score. Finesse
moves test one axis, power moves test both.

Each is re-rolled with about **33% random jitter** every time a rusher and
blocker lock up — a large amount of noise on top of the ratings.

### The move chosen has nothing to do with ratings

A closed result: the code picking *which* move to attempt reads **no ratings
at all**. It is a flat random draw. Ratings decide whether the move works,
never which one gets tried. One exception — if the rusher already has a shot
at the ball carrier, the bull rush leaves the pool entirely.

### Leverage matters more than anyone realised

Angle matters enormously, and asymmetrically. A good angle is worth **four
times** as much as a bad one — not a ±50% modifier, a factor of four. Bull
rushes are exempt.

### The CPU is not given special animations

The complaint that the CPU gets pass-rush animations a human never sees is
**not** about a controller check; there is no such check. What is real is
that the CPU takes a different route into the same code, one that skips the
strength requirement when drawing a move. So it can *attempt* moves a human
in the same situation is not offered.

> **Confidence: 92%.** The profile, the jitter, the leverage factor and the
> ratings-free move draw were all re-derived independently. The score
> components lean on one derivation each, and one behaviour we describe sits
> behind a conditional whose live value we have not observed on hardware.

---

## Coverage and defensive backs

### Why maxing awareness gets your safety burned deep

Every coverage defender periodically rolls to decide whether to **abandon his
assignment and chase the ball carrier**. The roll is, near enough, "random
number under 255 — is it below my awareness?" So a maxed-awareness defender
abandons coverage **every single time he is asked**.

He is asked more often at higher difficulty and more often with higher
awareness, because both shorten his re-evaluation timer.

A maxed-out safety is therefore not playing better coverage. He is checking
more often whether to leave, and passing that check more often. **Maxing
awareness and tackling makes your deep safety worse at playing deep.**

The Knockdowns slider "fixes" this only in the sense that it lets him swat
the resulting bomb down.

There is also a latch: in some situations the defender breaks off **without
rolling at all**, making the behaviour even less rating-dependent than the
roll alone suggests.

### Zone defenders bunch up because they all watch the same point

Zone defenders slide based on where the ball carrier is, each applying a
simple multiplier. There is **no term anywhere for staying away from a
teammate** — they are not aware of each other at all.

Worse, the field is split into thirds by the hash marks, and crossing a hash
switches the multiplier abruptly. The result is a **1.5-yard jump** in where
several defenders want to be, all at the same instant.

### Linebackers can already jam

A specific community request was that linebackers be able to jam receivers at
the line, especially smaller slot receivers.

**They already can.** The jam routine is reachable from four places, and the
man-coverage code has an explicit linebacker branch — middle and outside —
that jams with **80% probability**. A second path jams with no position check
at all.

The shared eligibility check looks at the phase of the play, whether the
receiver is downfield, whether somebody is already jamming him, roughly which
way the defender faces, and how far apart they are. It contains **no check on
position, weight, height or size anywhere.**

So there is no capability to add. What is seen on the field is about which
path runs and when.

Two related details: the decision to *attempt* a jam is a flat coin-flip with
**no rating input whatsoever**, and the jam *contest* is decided by
**strength and agility** — awareness is never consulted.

> **Confidence: 95%.** The break-off roll, the zone geometry, the
> no-separation finding and the linebacker jam paths were each re-derived
> independently, and the coverage state numbering was verified against the
> engine's own table. Deducted for one thing: the exact distances at which a
> jam is attempted were not pinned down.

---

## The quarterback

### "Robo QB" is real, and it is not about pressure

The pinpoint throw is a single probability:

> **Perfect-throw chance = 50% + (Throw Accuracy ÷ 200)**

A 60-accuracy quarterback throws with **zero error 80% of the time**. An 80,
90%. A 99, virtually always. When the roll succeeds the error is not reduced
— it is **set to nothing at all**.

And it fires identically against a blitz and against a soft zone. It is not a
panic bonus or a comeback mechanic. It is simply how he throws all the time.

### But he folds to an ordinary four-man rush

The quarterback senses pressure through what amounts to a **radar**: eight
directional sectors out to roughly eight yards, with awareness deciding how
much he actually perceives.

A defender who is **currently blocked** has his threat reduced by more than
the maximum threat any defender can generate.

> **A blocked rusher is arithmetically invisible.** Not "less urgent" —
> invisible, at any distance beyond about four yards. Even a 100-awareness
> quarterback needs a blocked man inside two and a half yards to register him
> at all.

So his read of the pocket is not a gradual sense of pressure building. It is
a **switch that flips the instant a block is lost** — by which time the
rusher is on top of him. He gets essentially no warning.

Two things compound it. An unpressured quarterback contributes **no movement
at all** — after the dropback animation he stands still. And no throw is
permitted for the first sixty frames of a play when a human is on the other
side.

There is **no sack-specific code anywhere in the game.** A sack is a tackle
landing on a stationary target who never saw it coming.

### What pressure actually changes

Not panic. He begins every play with reads *suppressed* for a few frames, and
enough threat **cancels that suppression**, so the same evaluation simply
runs sooner. Pressure makes him read earlier, not differently. Accuracy is
untouched by it.

What pressure does uniquely: he scrambles, he throws it away (gated on
awareness — at 79 or above, always), and very rarely he dumps off.

### Why he ignores your back in the flat

Each play carries a list of up to five receivers with priority weights. Two
of those weight values are **unreachable to a CPU quarterback** — he will
never look at a receiver carrying them. So certain backs and tight ends get
ignored because of **a number in the play data**, not a flaw in his logic.

### Scrambling is mostly about the defence's call

The scramble roll requires the quarterback to be flagged as a scrambling
type, requires CPU control, fires at most once every two and a half seconds —
and then checks **which defensive play was called**. Against four specific
calls he may scramble. **Against anything else, never.** If he clears all
that, his chance rises smoothly with speed, from about a third at speed 50 to
a bit over half at 99.

> **Confidence: 93%.** The perfect-throw formula, the blocked-rusher
> discount, the suppression counter and the whole scramble chain were
> re-derived independently, and the scramble speed line was proven identical
> to the coded form. Deducted because the radar's exact perception maths has
> one detail we describe approximately, and the dump-off frequency is a
> mechanism we can point at but not put a number on.

---

## Running backs

### What "vision" actually is

There is no vision *attribute*. What exists is the timer idea again: a ball
carrier re-plans his path on a countdown driven by the average of his
awareness, carrying and agility.

* A 50-rated back re-plans every **eight** frames.
* A 70-rated back, every four.
* An 88 or better, **every single frame.**

On the frames between, he keeps running the direction he last chose. That is
the whole of "seeing the hole versus running blind" — an eight-to-one
difference in how stale his information is.

There is a second layer. Each back carries a **running style** taken from the
roster, and style alone decides how often he reads his blocks at all: one
style never does it, another does 35% of the time, a third 65%. Two backs
with identical ratings can behave very differently because of it.

### Special moves

Move *selection* runs on its own separate timer, in which **awareness counts
double** compared with agility and break tackle.

A fixed priority list per style is then walked in order, and the first move
fitting the situation wins. Power backs try Sprint first; elusive backs try
Juke first. The chain reads only four ratings — agility, awareness, break
tackle, strength. No speed, no carrying.

Rough ceilings: Juke 47%, Dive and Stiff Arm 38%, Spin 31%. **Hurdle has no
roll at all** — right geometry, diving defender, it just happens. And Sprint
short-circuits everything at a flat rate by style, 75% for power backs. That
is why power backs mash turbo past every other move they own.

### Improving vision without making everyone elite at moves

**Through ratings, impossible** — vision and moves share awareness and
agility, so raising either lifts both.

**Through code, entirely possible.** The two systems share no instruction.
It takes two changes rather than one, and that is the real answer.

> **Confidence: 90%.** The two timers, the priority lists and every move gate
> were re-derived independently, including a sweep confirming the steering
> code reads no ratings at all beyond the timer. Lower than neighbouring
> sections because the running-style values themselves come from roster data
> we have not read, so we can describe what each style does without saying
> which backs have which.

---

## Catching, fumbling and tackling

### The catch hands the ball over before it is secured

Possession transfers the moment the catch roll succeeds, but the ball stays
marked **unsecured** for roughly a third of a second. Lose it in that window
and it is scored an **incompletion**, not a fumble. During the window the
game substitutes the receiver's **Catching** rating where it would normally
use Carrying.

### Post-catch strips

A strip weighs the carrier's catching-and-carrying against the defender's
tackling-and-strength. Realistic numbers land near **15%** for an ordinary
hit and **30%** for a big one. A maxed receiver drops under 4%. The slider
works in reverse of what you might guess, and is strong.

### Fumbles

There is **no fumble slider.** It does not exist.

The tackler's ratings **do not enter the ordinary fumble calculation at all**
— only the carrier's, and the force of the hit. And **Toughness has no
gameplay effect whatsoever**: it is read by exactly one rating-display
routine and nothing else, image-wide.

Base rates run from roughly 0.17% to 1.6% depending on the hit — a spread of
about **nine times** between a glancing hit and a big one. A perfectly-rated
carrier reaches an exact zero.

One unexplained term can add a very large amount in specific circumstances.
We know where it is and what triggers it; we do not know what those trigger
values mean.

### Break Tackle does not do what it looks like

The contest is a straight subtraction — tackling, weight and strength against
break tackle, weight and strength — plus a random number that is **large
relative to the ratings themselves**.

* **One point of contest ≈ six points of rating.** Break Tackle 94 and 88 are
  literally the same number here.
* **Position never enters the calculation.** A cornerback and a defensive
  tackle tackle identically except for weight, and weight is clamped, so the
  real gap between them is small.

Each broken tackle in a play halves the chance of breaking the next.

> **Confidence: 91%.** The strip and fumble formulas reproduce exactly,
> including a base rate that reaches precisely zero at maximum ratings, and
> the Toughness negative was re-tested with our strictest search. Held below
> the mid-90s by the unexplained fumble term and because one published table
> of escape percentages could not be reproduced without assuming a detail the
> code does not fix.

---

## Special teams

### Punters never coffin-corner, and the logic is right there

The game contains a **complete, working coffin-corner solver**: it computes
flight time properly, accounts for wind, picks a sideline and aims a yard out
of bounds.

It sits behind four stacked conditions, one a **25% coin flip** and another
requiring CPU control. A human punter essentially never reaches it; the CPU
reaches it rarely.

When the gates fail, the punt is three random numbers with **no reference to
field position at all** — the punter genuinely does not know he is at
midfield. The field-goal code, by contrast, *does* read field position.

### Punter accuracy is mostly a dead rating

Usable range is only about **70 to 100**; every punter at 70 or below behaves
identically. And accuracy affects only the *result* — the aim itself reads no
ratings, so a 60 and a 95 aim exactly the same way.

> **Confidence: 92%.** The solver's flight-time maths was re-derived and
> matches textbook projectile motion exactly, and the gates and the accuracy
> dead zone were each confirmed twice. Deducted for a wind-direction sign we
> cannot settle without running the game, and because one path applies extra
> aim adjustments after the solver that we have not fully characterised.

---

## CPU play calling

### The play pool is smaller than it looks

Choosing a play means asking for every play in the book tagged with the
current situation group — **one filter, that is all**. No formation logic, no
personnel logic, no down-and-distance filtering beyond that tag. Candidates
are weighted and a weighted random draw picks one.

There is a real landmine: the candidate buffer holds 225 plays and the fill
loop **does not check the limit**, so a large enough custom playbook would
write past the end of it.

### The CPU does not avoid repeating itself

A system tracks the last 48 plays and applies an anti-repetition effect — but
to the **opponent's** tendencies, never the caller's own. Nothing stops the
CPU calling the same dive fifteen times.

That tracker also feeds several defensive advantages: coverage defenders
break off sooner against a repeated play, blockers shed faster, tacklers get
a bonus against a human carrier. Most are gated to help only the CPU.

> **Confidence: 94%** for the enumerator and the anti-repetition finding —
> both re-derived independently, with the missing bounds check confirmed
> directly. **45%** for any claim about *why* the CPU rides one play all
> game: we found a mechanism that forces run and pass into a fixed ratio, but
> the evidence now suggests it serves the defence rather than the offence,
> and until a hardware test settles that it should not be treated as the
> explanation.

---

## What we still cannot answer

**We cannot read the shipped play data.** The plays live in a compressed
format not yet reversed. This blocks the most valuable single item on the
list — the receiver priority weights deciding who the quarterback will and
will not look at.

**We cannot test on the console from here.** A short list needs a live game,
chief among them which of two play-calling routines actually serves the CPU
offence.

**We do not have the other games.** Several community questions are really
"what changed between 2002, 2003, 2004 and 2005?" Those need the other discs.

> **Confidence: n/a** — this section is the list of things we are *not*
> claiming.

---

## How to read the numbers above

The findings we are most confident about are the ones where the answer is a
**structure**: the slider system, the blocking assignment pipeline, the
coverage break-off, the quarterback's pressure radar. Those were re-derived
independently and matched down to individual instructions.

Hold **specific percentages** more loosely than mechanisms. Where a number
here is precise, it survived a bit-for-bit recomputation; where it is vague,
that is deliberate rather than lazy.

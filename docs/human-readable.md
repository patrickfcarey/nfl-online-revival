# What we have actually found inside Madden 2004

This is the plain-English version. Everything here came from reading the
game's own program code — not from playing it and guessing, and not from
old forum wisdom. Where the code contradicts something the community has
believed for twenty years, the code wins, and we say so.

A few of these answers are uncomfortable. Several long-standing complaints
turn out to be real and to have a specific, findable cause. A few turn out
to be the opposite of what everyone assumed.

**One honest warning up front.** In August 2026 we ran eight independent
double-checks over everything we had written. Most of it survived. Some of
it did not, and a couple of our best-known claims were wrong. Those are
corrected below rather than quietly dropped — the section "Where we were
wrong" is not padding, it is the most useful part of this document if you
plan to rely on any of it.

---

## The one idea that explains most of the game

Almost nothing in this engine is about *how well* a player does something.
It is about **how often he is allowed to think.**

Nearly every rating we traced ends up controlling a countdown timer. A
player with high awareness does not make smarter decisions than a player
with low awareness — he makes the *same* decision, sooner and more often.
A low-rated player is running on stale information: he re-checks the
situation every eight frames instead of every frame, and in between he
keeps doing whatever he last decided to do.

This is why maxing out ratings in this game feels strange. You are not
buying better judgement. You are buying reaction speed.

It also explains a lot of the "the CPU cheats" feeling. On harder
difficulties the CPU's timers get shorter. It is not given better
information or better outcomes in most places — it is simply allowed to
react more often than you are.

---

## Sliders

### There are far more sliders than the menu shows

The game keeps a table of **131 settings**. The gameplay menu exposes a
small fraction of them. The rest are real, they are read by real code, and
several of them are things players have wanted control over for years.

### Every slider does the same thing, mathematically

There is exactly **one** function that applies a slider, and every one of
the eleven places that uses a slider calls it. The rule is always:

> **50 is dead centre and changes nothing at all.** Above 50 the underlying
> number is scaled up, below 50 it is scaled down, and the amount of scaling
> is fixed per slider.

We checked this to the last decimal place: at exactly 50, the value comes
out bit-for-bit unchanged. That is not an approximation — it is exact.

What differs between sliders is only *how hard* each one pulls. Some move
their number by half a percent per point, some by three quarters of a
percent. None of them is a switch, and none of them has a hidden step or
cliff. **There is no "magic slider value."**

### Sliders are weaker than people think, and some are inert

Several sliders cannot reach the outcome people expect:

* Some penalty sliders are **completely inert above 50** — the code sets
  their floor and their ceiling to the same value, so raising them does
  nothing whatsoever.
* One penalty (Personal Foul) is stored, has a slider position, and is
  **never read by anything.**
* Four penalty types exist in the game's own list and are **never generated
  at all**: Delay of Game, Encroachment, Unsportsmanlike Conduct, and
  Personal Foul.
* The Facemask penalty has a 15-yard branch that can never be reached — its
  chance is hard-set to zero, but the code that would call it is live. It is
  a genuine unfinished feature sitting in the shipped game.

### A slider that behaves backwards

The **Knockdowns** slider controls how often a defender swats a pass out of
the air. Its full range runs from about **13% to 87%**, centred on 50%.
Nothing about it is gated on how deep the pass is — a bomb is as swattable
as a five-yard out. Turning it up does not make defenders better at
defending; it makes deep passes get knocked down.

### Penalty sliders have a kink at 50

Penalties do not scale like everything else. Below 50 the frequency ramps
down smoothly toward zero. Above 50 it ramps toward a per-penalty ceiling.
The two halves meet at 50, and **only at exactly 50** do the two formulas
give precisely the same answer.

Setting a penalty slider to **0 turns it off completely** — that is a hard
off switch, not "very rare."

### One slider genuinely misbehaves

The False Start slider's effect is **squared**, not linear. Going from 50 to
100 makes false starts roughly **four times** as likely, not twice. The
pre-snap flinch that shares the same penalty is linear. So the two halves of
"false start" respond to the same slider at different rates.

---

## Do ratings have hidden thresholds?

**Mostly no — and this surprised us.**

We counted every place the game reads a player rating during gameplay:
**278 of them**. The overwhelming majority — around 87% — feed straight into
continuous arithmetic. A 71 is very slightly better than a 70, and that is
all. There is no secret tier system.

But there are real exceptions, and they matter:

* **Power pass-rush moves require Strength 66 or better.** Below that, the
  move is simply not available. This is a genuine hard gate.
* **Juke and spin require Agility 66 or better.** Same shape, same cliff.
* **Throw Power below 71 contributes literally nothing** to one calculation
  — it is clamped to zero.
* **Carrying below 50 is hard-zeroed** in one place.
* **Agility below 50 gets no bonus at all** in two places.
* **Strength is clamped between roughly 39 and 78** in the tackle
  calculation. A 340-pound nose tackle and a 250-pound end are treated as
  equally strong there, and anybody under about 39 is treated as if he were
  39.
* **Speed below 59** falls back to a fixed value in one movement
  calculation.

And there is a general banding effect: several systems chop ratings into
**16-point bands**. Inside a band, players are identical. A 88 break-tackle
and a 94 break-tackle are **the same number** to the tackle system.

### The 40 / 44 / 55 blocking mystery

Testers reported that pass-block ratings behaved as though something changed
at 40, 44 and 55. We found two separate mechanisms that could each produce a
step, and we now think we know which one it is.

The likely answer is a **banding effect in how long a blocker stays locked
onto a defender**. It produces three genuinely distinct behaviours at those
three values, and — importantly — it does so regardless of the player's
awareness, which matches testers seeing the same boundary on two completely
different players.

The competing explanation (an arithmetic overflow in a timer) would predict
that **44 and 55 behave identically**, and testers say they do not. So we
favour the banding explanation, while noting that both mechanisms are real
and could both be contributing.

---

## Blocking

### The single biggest finding: a defender in coverage cannot be blocked

This is the answer to "why doesn't my slot receiver ever seal the corner?"

The engine assigns blockers to defenders every frame. But before it does, it
builds a list of defenders who are *eligible to be blocked*. That list only
accepts defenders who are **chasing the ball carrier, rushing the passer, or
waiting pre-snap** — or who are being controlled by a human.

**A cornerback in man or zone coverage is in none of those states.** He is
invisible to the blocking system. No blocker of any position can be assigned
to him, and a second, independent check backs this up by scoring coverage
defenders below the threshold the system requires.

He becomes blockable only when he abandons coverage to chase the ball.

That is why the complaint is loudest about the slot: the slot defender is
the man a receiver most obviously *should* seal, and with him removed from
consideration the receiver's next-nearest option is a safety several yards
away. On screen that reads as the receiver wandering aimlessly.

### The fullback has a different problem

A fullback leading through the hole is excluded from the assignment system
**entirely**, for a completely separate reason: lead blockers are put in a
special mode, and the blocker list only accepts the two ordinary modes. He
is never considered for an assignment at all. His only guidance is a simple
"lean toward the general area" behaviour of his own.

So the fullback and the receiver fail for two unrelated reasons, and fixing
one would not fix the other.

### No play in the game says who to block

We checked this exhaustively from both directions. Play data can tell a
blocker to *pass protect*, to *run block*, to *lead through a gap*, or to
*follow a teammate* — but it can never name an opposing player. Every player
reference a play can contain is looked up on the blocker's **own team**.

"Block the man over number two" does not exist in this game. Targets are
chosen by the engine, live, every frame, purely on geometry and a threat
score.

### Receivers get no blocking logic of their own

The engine has custom pairing logic for halfbacks, tight ends, tackles,
guards and the centre. **Wide receivers get the generic fallback** — the same
code path used for anything unrecognised. And the halfback/fullback logic is
**pass-protection only**, so on a run play a fullback is scored exactly like
a receiver.

### Double teams do exist

The community has long said Madden 2004 has no double teams. **That is
wrong.** There is a full double-team system: a registry of who is helping
whom, distinct roles for the primary and the helper blocker, a scorer that
decides when help is warranted, a promotion when a second blocker joins, and
a clean peel-off when he leaves.

One detail is worth knowing: when two players block one defender, the effect
is **not** the sum of their blocking. The second blocker applies a *penalty*
to the defender rather than adding his own strength. That is why double
teams do not feel as decisive as they should.

### Pass blocking and run blocking are genuinely different systems

They are two separate behaviours with separate code, and the game converts
between them live — including mid-play, if a pass play turns into a run.
Which one a lineman is doing is decided by the play type plus a live flag,
and whether anybody is lined up over him.

The block sliders scale **three components of the blocking contest**, not
some single "push" value.

### A real bug in pass protection

There is an arithmetic overflow in how often a pass blocker re-evaluates.
Once a lineman's pass-block and awareness ratings **add up to more than
255**, a number wraps around and his re-evaluation gets *worse* instead of
better. In practical terms, an elite lineman with high awareness can be
slower to react than a slightly worse one. The equivalent run-blocking code
does the same calculation correctly, so this is a genuine mistake, not a
design choice.

---

## Pass rush

### Finesse and power are real, and richer than expected

Every pass rusher gets a hidden **three-axis profile** stamped on him when he
engages: a power score (blocking/tackling plus strength plus **weight**), a
finesse score (which adds awareness and agility), and an overall score.
Finesse moves test one axis, power moves test both.

Each of these is re-rolled with about **33% random jitter** every time a
rusher and blocker lock up, which is a large amount of noise on top of the
ratings.

### The move chosen has nothing to do with ratings

This one is a closed result: the code that picks *which* pass-rush move to
attempt reads **no ratings at all**. It is a flat random draw. Ratings decide
whether the move *works*, never which move gets tried.

There is one exception worth knowing: if the rusher already has a shot at the
ball carrier, the bull rush is removed from the pool entirely.

### Leverage matters more than anyone realised

The angle a rusher takes matters enormously — but not symmetrically. Coming
from a good angle is worth **four times** as much as coming from a bad one.
It is not a ±50% modifier; it is a factor of four. Bull rushes are exempt
from this entirely.

### The CPU is not given special animations

The complaint that the CPU gets pass-rush animations the human never sees is
**not** about a controller check. There is no such check. What is real is
that the CPU takes a different route into the same code — one that skips the
strength requirement when drawing a move. So the CPU can *attempt* moves a
human in the same situation would not be offered.

---

## Coverage and defensive backs

### Why maxing awareness gets your safety burned deep

This is the sdchargersfanboy report, and it checks out.

Every coverage defender periodically rolls to decide whether to **abandon his
assignment and chase the ball carrier**. The roll is, almost literally,
"random number under 255, is it less than my awareness?" — so a 255-awareness
defender abandons coverage **every single time he is asked**.

He is asked more often at higher difficulty and more often with higher
awareness, because both shorten his re-evaluation timer.

So a maxed-out safety is not playing better coverage. He is checking more
frequently whether to leave, and passing that check more often. **Maxing
awareness and tackling makes your deep safety worse at playing deep.**

The Knockdowns slider "fixes" this only in the sense that it lets him swat
the resulting bomb out of the air.

One important caveat we found on re-checking: in some situations a latch is
set that makes the defender break off **without rolling at all**. So the
behaviour is even less rating-dependent than the roll alone suggests.

### Zone defenders bunch up because they all watch the same point

Zone defenders slide based on where the ball carrier is, and each one applies
a simple multiplier to that position. There is **no term anywhere for staying
away from a teammate**. They are not aware of each other at all.

Worse, the field is split into thirds by the hash marks, and when the carrier
crosses a hash the multiplier switches abruptly. The result is a **1.5-yard
jump** in where several defenders want to be, all at the same instant.

### Linebackers can already jam — the premise was wrong

This was a specific community request: linebackers should be able to jam
receivers at the line, especially smaller slot receivers.

**They already can.** The jam routine is reachable from four places, and the
man-coverage code has an explicit linebacker branch — linebackers, middle and
outside — that jams with **80% probability**. A second path jams with no
position check at all.

The shared eligibility check looks at the phase of the play, whether the
receiver is downfield, whether somebody is already jamming him, roughly which
way the defender is facing, and how far apart they are. It contains **no
check on position, weight, height or size anywhere.**

So there is no capability to add. Whatever is being seen on the field is
about which path runs and when — not about linebackers being locked out.

Two related details: the decision to *attempt* a jam is a flat coin-flip with
**no rating input whatsoever**, and the jam *contest* is decided by
**strength and agility** — awareness is never consulted.

---

## The quarterback

### "Robo QB" is real, and it is not about pressure

The pinpoint-accurate throw is a single probability:

> **Perfect-throw chance = 50% + (Throw Accuracy ÷ 200)**

A 60-accuracy quarterback throws with **zero error 80% of the time**. An
80-accuracy quarterback, 90%. A 99, virtually always. When the roll succeeds
the error is not reduced — it is **set to nothing at all**.

And this fires identically against a blitz and against a soft zone. It is not
a panic bonus or a comeback mechanic. It is simply how the quarterback throws
all the time.

### But he folds to an ordinary four-man rush — here is why

The quarterback senses pressure through what amounts to a **radar**: eight
directional sectors, out to about eight and a half yards, with his awareness
determining how much of it he actually perceives.

A defender who is **currently blocked** has his threat reduced by more than
the maximum threat any defender can generate.

> **A blocked rusher is arithmetically invisible.** Not "less urgent" —
> invisible, at any distance beyond about four yards. Even a
> 100-awareness quarterback needs a blocked man inside two and a half yards
> before he registers at all.

So the quarterback's read of the pocket is not a gradual sense of pressure
building. It is a **switch that flips the instant a block is lost** — and by
then the rusher is already on top of him. He gets essentially no warning.

Two things compound it. An unpressured quarterback contributes **no movement
at all** — after his dropback animation he stands perfectly still. And no
throw is permitted for the first sixty frames of a play when a human is on
the other side.

There is **no sack-specific code anywhere in the game.** A sack is just a
tackle landing on a stationary target who never saw it coming.

### What pressure actually changes

We expected pressure to make the quarterback panic and skip his reads. It
does the opposite. He starts every play with reads *suppressed* for a few
frames, and enough threat **cancels that suppression** — so the same
evaluation simply runs sooner. Pressure makes him read earlier, not
differently. Accuracy is completely unaffected by it.

What pressure does uniquely: he scrambles, he throws it away (gated on
awareness — at 79 or above he always will), and very rarely he dumps off.

### Why he ignores your back in the flat

Each play carries a list of up to five receivers with priority weights. Two
of those weight values are **unreachable to a CPU quarterback** — he will
never look at a receiver carrying them.

That means the reason certain backs and tight ends get ignored is **a number
written into the play data**, not a flaw in the quarterback's logic. It is
editable in principle, once that data can be read.

### Scrambling is almost entirely about the defence's call

The scramble roll requires the quarterback to be flagged as a scrambling
type, requires CPU control, fires at most once every two and a half seconds
— and then checks **which defensive play was called**. Against four specific
calls he may scramble. **Against anything else he never scrambles at all.**

If he clears all that, his chance rises smoothly with speed, from about a
third at speed 50 to a bit over half at 99.

---

## Running backs

### What "vision" actually is

There is no vision *attribute*. What exists is the same timer idea as
everywhere else: a ball carrier re-plans his path on a countdown driven by the
average of his awareness, carrying and agility.

* A 50-rated back re-plans every **eight** frames.
* A 70-rated back, every four.
* An 88 or better, **every single frame.**

On the frames in between, he keeps running the direction he last chose. That
is the whole of "seeing the hole versus running blind" — an eight-to-one
difference in how stale his information is.

### Special moves

Move *selection* runs on its own separate timer, in which **awareness counts
double** compared to agility and break tackle.

Then a fixed priority list per running style is walked in order, and the first
move that fits the situation wins. Power backs try Sprint first; elusive backs
try Juke first. The move chain reads only four ratings — agility, awareness,
break tackle and strength. No speed, no carrying.

Rough ceilings: Juke 47%, Dive and Stiff Arm 38%, Spin 31%. **Hurdle has no
roll at all** — if the geometry is right and the defender is diving, it just
happens. And Sprint short-circuits everything at a flat rate by style, which
is 75% for power backs. That is why power backs mash turbo past every other
move they own.

### Can you improve vision without making everyone elite at moves?

**Through ratings, no** — vision and moves share awareness and agility, so
raising either lifts both.

**Through code, yes, completely.** The two systems share no instruction. They
are entirely separate. It takes two changes rather than one, and that is the
real answer to the question.

---

## Catching, fumbling and tackling

### The catch hands the ball over before it is secured

Possession transfers at the moment the catch roll succeeds — but the ball is
marked **unsecured** for roughly a third of a second. Lose it in that window
and it is scored an **incompletion**, not a fumble.

During that window the game substitutes the receiver's **Catching** rating
where it would normally use Carrying.

### Post-catch strips

A strip attempt weighs the carrier's catching-and-carrying against the
defender's tackling-and-strength. Realistic numbers land around **15%** for
an ordinary hit and **30%** for a big one. A maxed-out receiver drops under
4%. The slider works in reverse of what you might guess and is strong.

### Fumbles

There is **no fumble slider.** We looked; it does not exist.

The tackler's ratings **do not enter the ordinary fumble calculation at all**
— only the carrier's, and the force of the hit. And **Toughness has no
gameplay effect whatsoever**; it is read by exactly one rating-display
function and nothing else.

Base fumble rates run from roughly 0.17% to 1.6% depending on the hit — a
spread of about **nine times** between a glancing hit and a big one. A
perfectly-rated carrier reaches an exact zero.

There is one unexplained term that can add a very large amount to fumble
chance in specific circumstances. We know exactly where it is and what
triggers it; we do not yet know what those trigger values mean.

### Break Tackle does not do what it looks like

The tackle contest is a straight subtraction: the tackler's tackling, weight
and strength against the carrier's break tackle, weight and strength — plus a
random number that is **large relative to the ratings themselves**.

Two consequences:

* **One point of contest ≈ six points of rating.** Break Tackle 94 and Break
  Tackle 88 are literally the same number to this system.
* **Position never enters the calculation.** A cornerback and a defensive
  tackle tackle identically except for their weight — and weight is clamped,
  so the real difference between them is small.

Each broken tackle in a single play halves the chance of breaking the next
one.

---

## Special teams

### Punters never coffin-corner, and the logic is right there

The game contains a **complete, working coffin-corner solver** — it computes
flight time properly, accounts for wind, picks a sideline, and aims a yard
out of bounds.

It is gated behind four conditions stacked together, one of which is a **25%
coin flip** and another of which requires CPU control. So a human punter
essentially never reaches it, and the CPU reaches it rarely.

When the gates fail, the punt is three random numbers with **no reference to
field position at all**. The punter genuinely does not know he is at midfield.
The field-goal code, by contrast, *does* read field position.

### Punter accuracy is mostly a dead rating

Punter accuracy has a usable range of only about **70 to 100**. Every punter
rated 70 or below behaves identically. And accuracy affects only the *result*
— the aim itself reads no ratings whatsoever, so a 60-rated and a 95-rated
punter aim exactly the same way.

---

## CPU play calling

### The play pool is smaller than it looks

When the CPU picks a play, it asks for every play in its book tagged with the
current situation group — **one filter, and that is all**. No formation logic,
no personnel logic, no down-and-distance filtering beyond that single tag. The
candidates are weighted and a weighted random draw picks one.

There is a real landmine here: the candidate buffer holds 225 plays and the
fill loop **does not check the limit**. A sufficiently large custom playbook
would write past the end of it.

### The CPU does not avoid repeating itself

There is a system that tracks the last 48 plays and applies an
anti-repetition effect — but it is applied to the **opponent's** tendencies,
never to the caller's own. Nothing stops the CPU calling the same dive
fifteen times.

That tracker also feeds several defensive advantages: coverage defenders
break off sooner against a repeated play, blockers shed faster, and tacklers
get a bonus against a human carrier. Most of these are gated so they only
help the CPU.

### The "same play all game" mechanism is in doubt

We documented a mechanism that forces run and pass into a fixed ratio
regardless of how many plays are in each group — which would neatly explain
the CPU riding one dive all game.

On re-checking, we are **no longer confident it applies to offence**. The
evidence now points to it being the *defensive* weighting, with offence using
a much gentler adjustment. This needs a live test on hardware to settle, and
until then the mechanism should not be treated as the explanation.

---

## Where we were wrong

Eight independent re-checks went over everything above. Here is what did not
survive, because a document like this is worthless if it only reports its
wins.

**We said halfbacks might have no vision at all.** We had found that the byte
selecting a back's running style is never written anywhere, which would have
meant every halfback defaulted to a style with a **0%** chance of reading
blocks. It was a dramatic finding and it was **wrong**. The byte is written —
twice — by code that receives the player record as a hand-off from another
function. Our search could not follow a pointer across that boundary. The
byte is filled from roster data like everything around it, and the "halfbacks
are blind" theory is dead.

**We said a field in the pass-rush system was dead and free to reuse.** It is
read in four separate places, including a live contest. A proposed fix built
on that assumption would have corrupted three unrelated behaviours.

**We identified a 456-byte region of unused code as safe to write patches
into.** It is a live callback suite, registered at startup. A worked patch
example we wrote would have overwritten it. A second such region had the same
problem.

**All three of those mistakes have the same cause**, and it is worth stating
plainly: our search tools could not see a reference when the code built it in
two steps far apart, or handed it to another function. Everything the tools
reported as "never used" was suspect, and we did not know it. Those tools have
now been fixed, and they find all of the above correctly.

**Smaller corrections:** a blocking bonus we quoted as up to +94% is really up
to +47%. A fumble spread we called 12× is 9.3×. Weight advantages we printed
as +19 and +11 are really +9 and +1, because we forgot the stored weight has
160 subtracted from it. A table of CPU repetition percentages was computed on
a 0-to-100 scale when the game works internally on 0-to-255, making **every
number in it wrong**. And we claimed the CPU's memory of what worked against
you adds the *yards gained* to a play's weight, making a big play enormously
influential — it actually adds a small flat bonus, about forty times less.

---

## What we still cannot answer

**We cannot read the shipped play data.** The plays themselves live in a
compressed format we have not reversed yet. This blocks the most valuable
single thing on the list — the receiver priority weights that decide who the
quarterback will and will not look at.

**We cannot test on the console from here.** A short list of questions needs
a live game to settle, chief among them which of two play-calling routines
actually serves the CPU offence.

**We do not have the other games.** Several community questions are really
"what changed between 2002, 2003, 2004 and 2005?" Those need the other discs.

---

## How much of this should you trust?

Everything above was read out of the game's own code, and then read a second
time by a different pass that did not know what the first had concluded.
Where the two disagreed, we went and looked ourselves.

The parts we are most confident about are the ones where the answer is a
**structure** — the slider system, the blocking assignment pipeline, the
coverage break-off, the quarterback's pressure radar. Those were re-derived
independently and matched down to individual instructions.

The parts to hold loosely are **specific percentages**. Several of our tables
turned out to depend on assumptions the code does not actually make. Where a
number in this document is precise, it survived a bit-for-bit recomputation.
Where it is vague, that is deliberate.

And the general lesson, which cost us three findings: **be most suspicious of
the confident negatives.** "Nothing reads this," "nothing writes that,"
"this code is unused" — every one of those that failed, failed because we
could not see something rather than because it was not there.

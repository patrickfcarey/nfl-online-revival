# Open investigations — the community question ledger

Questions asked of this project by the community. **Wave 1** (2026-08-09)
is the original "Madden 2004 uplift" list; **wave 2** (2026-08-10) is a
second round of reports and a wish list, grouped by the system that would
have to be investigated. Each entry records the question as asked, what
this project already knows that bears on it, what materials it needs, and
the first investigative angle. Status values: **open**, **leads**,
**resolved**.

Seven of wave 1's nine are resolved; wave 2 adds nine more entries, most
of them 2004-native and several already half-answered by wave 1's work.
A triage list is at the end.

Reference base: the Madden 2004 (SLUS-20752) engine is now well-mapped —
`slider-behavior.md` (options/sliders/transforms), `sdchargersfanboy.md`
(coverage AI, break-off mechanism), `play-tendency-ai.md` (`ptrk`
anti-repetition tracker, Madden Cards). That map is the baseline and
search-signature source for every cross-title question below.

Titles on hand (rig, `~/Games/ps2/`): Madden 2001, **2004**, 06, 08
(+ a second copy and Ghidra project in `ps2_madden_recomp`), 09, 12,
12 Deluxe. **Not on hand: Madden 2002, 2003, 2005; NCAA 2005, 2006** —
entries needing them are blocked on materials.

---

## 1. "Concrete shoes" lateral reaction in Madden 2002 (not 2003; returns in NCAA 2006)

> Why do players in Madden 2002 have concrete-shoes reaction for lateral
> plays, but not in Madden 2003 — and can we retroactively fix it? The
> same problem returns in NCAA 2006.

**Status: open — blocked on materials (M2002, M2003, NCAA 2006 ISOs).**
What we know from 2004: defender responsiveness is governed by decision
cadences (`skillTerm + rand(0,(255−AWR)/32)` refills, `(AWR+TAK)/32`
timers) and by the locomotion command block (`player+0x1E8` speed scale,
desired bearing, and a ≤25.02° turn gate at `+0x1F5`). "Concrete shoes
on lateral plays" smells like the turn gate / turning-rate side, not the
cadence side. First angle: get the three ELFs, locate the same
locomotion command writer by idiom (the 2004 signatures — the 24-bit BAM
bearing math, `0x01000000`=360°, the 25° constant — are distinctive),
and diff the turn-rate constants and any lateral-speed penalty between
2002 and 2003. If it is a constant, the retro-fix is a pnach.

## 2. Runners over-run their blocks on pitch plays

> Why do runners over-run their blocks on pitch plays, and can we fix
> their vision/understanding of blocks? [both games and beyond]

**Status: RESOLVED — `pitch-play-runner.md`.** Hypothesis refuted: the AI
carrier (state 1, AIthink `0x001dfeb8`) DOES read blocks — it scans its
own team for a lead blocker, finds the nearest threat in a forward cone,
follows that defender's engagement link to his blocker, and steers to the
gap between them. The over-run is (a) a cone-limited field of view (75°
threat cone, 45° steer gate) and (b) a weak speed governor: base speed
1.0, and the only slowdown is gated behind a lead-blocker latch whose
routine **aborts unless the *carrier* is within 8 yards of the ball spot
laterally** — on a pitch he leaves that band at once, so the scan never
runs. Fix is code, not data (the carrier reads no play path once
carrying): recommended N1 (widen that 8→16-yard carrier gate,
`0x001df370`) + N5 (follow-speed pool word `0x005FEE40`).

## 3. Warping / sliding / leaping defense starting in Madden 2005 & NCAA 2005

> Why did the 2005 games start with extreme warping, sliding, and
> leaping on defense instead of respecting the physics of the 2002–2004
> era? Can we fix that?

**Status: open — blocked on materials (2005 ISOs).** The 2004 baseline
is the "good" physics: coverage steering writes a normalized locomotion
command and the locomotion layer applies ratings; the swat requires
facing gates, catchable height, and distance < 6.0 (`0x0019b338` chain)
— no teleportation anywhere in the walked code. First angle: find the
2005 equivalents of the swat gates and the locomotion clamp; warping
usually means either the position is written directly (bypassing the
locomotion layer) or the turn/speed clamps got removed. The 2004
constants give the diff targets. A fix, if it is constants or a removed
clamp, may be patchable; if it is animation-driven root motion, mark
infeasible early.

## 4. Real zone blocking in Madden/NCAA 2005

> Is it possible to have real zone blocking through existing animations
> and code in 2005, and/or can we cut/fix the garbage zone plays to play
> correctly?

**Status: open — blocked on materials (2005 ISOs), with a 2004 lead.**
Lead: in 2004, per-player AI assignments are **play-file data** — chains
of `{stateId|0x80, p1, p2, p3}` records at `blob + 40·idx + 63`
(`0x0024397c` installer), not ELF constants. If 2005 kept that scheme,
"fixing garbage zone plays" may be a play-data edit (re-author the
assignment records) rather than a code patch — no new animations needed.
First angle in 2005: find the installer by idiom, dump a zone-run play's
records, and see what the blockers are actually told to do.

## 5. Targeting of pulling / lead blockers

> What is with the targeting of pulling/lead blockers and can we fix
> that?

**Status: RESOLVED — `lead-blocker-targeting.md`.** Took three passes;
the first two were wrong in instructive ways (see `lessons-learned.md`).

**Taxonomy first:** engagement kind 4 is **contact** (promotion needs
distance < 2.1 yd), not "approaching". The approach is kind 2/3, which
the engagement manager never touches.

**Pre-contact, route primacy is already respected.** State 47 defaults to
the play-authored bearing, leads a defender by **2× his velocity**,
converts that into a bounded ~60° lean off its own facing, and snaps back
to the route on two clamps (one zeroing speed). Closer to the owner's
spec than anyone expected.

**On contact, route primacy is totally abandoned.** A bearing/speed/
facing triple is computed **once** at lock-in (`0x001F14D0` — a mutual
shove axis) and re-stamped into the blocker's locomotion **every frame**
for 15–30 frames (`0x001F2064/2068/2070`). State 47 keeps computing the
route and it is discarded. The axis is *frozen*, so the blocker drives
the lock-in line after the defender has left it. **That staleness plus
the 2× over-lead is a sufficient mechanism for the over-run the community
reports — with no current-position tracking anywhere in the code.**

**Selection is separately broken:** proximity-only pairing, no
already-engaged dedup, Awareness never consulted, though re-selection
already runs every ~15–30 frames.

**Owner's fix spec (acceptance criteria):** land the block via on-route
retargeting whose correctness is an **Awareness probability roll**; the
route is the primary steering objective; honest misses are acceptable;
faking the block via warp/snap/oversized-radius is forbidden; and
selection must not begin until a per-play minimum number of steps has
elapsed. Fixes are **code caves** (A: demote the *contact* override to a
lean — note the address moved to the per-frame stores; A2: recompute the
axis instead of freezing it; A3: dial back the 2× lead; B: AWR-gated
selection; C: on-route defender window; D: engaged-dedup). Caves are now
surveyed and proven (`code-caves.md`), so all of these are designable.

## 6. Zone defenders bunch up / hook defenders abandon the middle

> Why do zone defenders often bunch up instead of playing their zones?
> Two middle hook defenders will both spread out and abandon the middle,
> leaving it wide open. Can we fix that?

**Status: RESOLVED — `zone-bunching.md`.** The reference the landmark
slides with is not a per-defender receiver — it is a **single shared
object, the ball carrier** (`ball->carrier`, QB→catcher), so every zone
defender keys the same X and slides together. There is **no
teammate-separation term anywhere** in the zone-steering code (proven by
exhaustive field census). The coefficient is centrifugal on outside
thirds (0.75) with a 3× step at the hash and no hysteresis. Shared
reference + no separation = the bunching. Recommended fix: one word at
`0x001EE664` (kill the 0.75 centrifugal arm). Four zone states mapped to
roles (37 CB, 38 hook, 39 transition, 40 deep safety). Still open only:
authored per-assignment play-data values (needs an ISO/rig read).

## 7. Default-slider behavior uplift (the meta-goal)

> Get the game to fire the animations and behaviors we want — that we
> can only get from extreme slider settings — on default settings.
> E.g. defenders contesting the ball in the air without warping/sliding.

**Status: leads — lever catalog delivered, `default-uplift-tuning.md`.**
The verified patch-point catalog (every lever pinned to its instruction,
with encodings, safety analysis, an interaction map, and a draft pnach) is
done; what remains is tuning and playtesting, plus one runtime measurement
(the swat-window frame count) that decides gate-widening vs base-raising.

The whole transform
layer is mapped, so "extreme-slider behavior at default settings" is a
constant-patching exercise, not a mystery: every gameplay slider runs
`x' = x·(1 + S·0.02·(v−50))` with per-slider S in a float pool
(`0x005FDAB4`–`0x005FDAF8`), and the interesting base rates are
literals at the call sites (e.g. knockdown base chance 50 at
`0x0019bd7c`, the eligibility gates at `0x0019b338`, the break-off
weights). Moving the *base*, not the slider, changes default behavior
while keeping sliders meaningful around it. First concrete candidates:
raise the knockdown base (contest-the-ball) without touching AWR;
retune the coverage break-off constant so discipline survives higher
AWR (decouple "react fast" from "abandon assignment" — two different
instructions, patchable separately). Deliverable shape: a gameplay-tuning
pnach with each change documented against `slider-behavior.md`.

## 8. Maxed AWR/Tackling gets strong safeties burned deep; maxed Knockdowns "fixes" it

**Status: RESOLVED — `sdchargersfanboy.md`.** Both halves real: high
effective AWR maximizes coverage *abandonment* (break-off roll AWR/255,
evaluated every 2 frames at All-Madden); Knockdowns is the only slider
that puts a thrown ball on the ground (87% per eligible frame at max,
no depth gating). The perceived "AWR boost" is swats. Companion finding:
the CPU-only anti-repetition tracker (`play-tendency-ai.md`).

## 9. CPU-only animations in pass rushing and run defense, especially at DT

> Why does the CPU have access to animations that the user doesn't in
> pass rushing and run defense, especially at DT?

**Status: RESOLVED — `cpu-dt-animations.md`.** No CPU-exclusive animation
and no controller gate. Win vs lose on the block-shed contest
(`0x001a66f8`) selects disjoint animation-ID sets (win = shed/swim/rip/
club, lose = driven-back/pancake); the contest boosts only the shedder's
score, and only on the CPU side, via the skill-class modifier
(`0x00153498`, ×1.4 at All-Madden, human always class 1) and the `ptrk`
boost (`0x001a6aa0`, CPU-only). The AI auto-shed runs for the human's DT
too (the user-think slot never suppresses it), so the human is locked out
of nothing — he loses the roll: 44% shed vs the CPU's 59% at All-Madden
(72% with a repeated play). Fix: one word at `0x001a6a98` neutralizes the
`ptrk` boost; `0x001534b8` equalizes the class modifier. Confirms #9 is a
symptom of the `ptrk` tracker (`play-tendency-ai.md`), not a separate
system.

---

# Wave 2 — collected 2026-08-10

A second round of community reports and a wish list. Grouped by the
*system* that would have to be investigated, because that is how the code
is organised; the most-reported symptoms keep their own named entries
even where they belong to a larger group. Several already have anchors
from wave 1 — noted per entry.

## 10. QB decision-making (the "robo QB" system)

The single largest cluster. One system, several symptoms.

> "Robo QB" who always knew when everyone was open, to the pixel, and
> delivered perfect throws — versus not being able to execute a normal
> play. Madden QBs HATED losing to early pressure and would shred you,
> but would fold to a normal 4-man rush if a lineman got in. Send cover
> zero and get someone unblocked, you get cooked; play cover 2 man under
> and the QB eats a sack sandwich.

Sub-questions from the wish list, all of which resolve against the same
machinery:

* **10a. What is the QB's read/progression logic** — how does he decide
  where to throw on a given play? Is there a receiver priority order from
  the play data, a per-receiver openness score, or both?
* **10b. Why the pressure/coverage asymmetry** (the headline symptom):
  does pressure trigger a *different code path* — a panic/quick-throw
  branch that bypasses the read progression and its openness evaluation —
  while coverage-based decisions run the slower, weaker evaluation? That
  is the shape the report describes and it is directly checkable.
* **10c. Why do QBs rarely throw to a wide-open flat** (HB/FB/TE)? A
  receiver-eligibility or priority weighting that under-ranks backs and
  tight ends, or a route-depth term in the openness score.
* **10d. Why the lack of confidence against man coverage?** Does the
  evaluation recognise coverage type at all, or only per-receiver
  separation?
* **10e. Is there any code that "attacks coverages"** — i.e. any
  coverage-recognition input to the decision, as opposed to pure
  separation geometry?
* **10f. What makes a QB scramble?** The trigger, and what it is
  weighted by.

**Anchors we already have:** `0x001c53d8` is the throw-error / pass
trajectory site (the QB Accuracy slider consumer), and `0x001c5248`
builds a "QB quality" factor from effective THA + AWR — so the *throw
execution* half is partly mapped; the *decision* half is not. Attribute
order and the effective-ratings table are known. Expect the decision to
live in a state-machine think (the 93-state array) plus a per-play
receiver table, the same shape as the coverage and blocker systems.

**Status: RESOLVED — `qb-read.md` (the read) and `robo-qb.md` (pressure
and scrambling).** Headlines: the target is a weighted random draw over a
**play-file-authored** priority table where weights 0 and 1 are
unreachable to the CPU — so the ignored checkdown is *authored*, and
editable. Coverage recognition is real but **man-vs-zone per defender,
with no shell concept**. And the two halves of "robo QB" are unrelated:
the pixel-perfect throw is **global** (`p = 0.5 + THA/200`, zeroing the
error vector entirely), while folding to a four-man rush is because **a
blocked rusher is arithmetically invisible** — the −9.0 engaged discount
exceeds the 8.5 maximum raw threat, so the QB gets no warning frames and
stands at speed zero until the block breaks. There is no sack-specific
code at all.

## 11. Why does Madden 04 get QB scrambling right when earlier versions do not?

> Why has 04 done a much better job at getting QBs to run than past
> versions? Can we fix past versions to run more/better?

Kept separate from #10 because it is a **cross-title diff**, not a
system map: establish 04's scramble trigger (10f), then compare against
2002/2003. **Blocked on materials** (M2002/M2003 ISOs not on the rig);
the 04 half is investigable now and is a prerequisite anyway.

## 12. HB vision, and what governs special moves

> What gives them vision? Awareness, I assume. What is the threshold
> between seeing lanes and being blind, and what governs special-move
> usage? Is it possible to globally improve HB vision without making
> them all elite at special moves?

**Partially answered already** (`pitch-play-runner.md`): the carrier's
"vision" is state 1's steering — a 75° threat cone, a gap-midpoint target
derived from the defender→blocker engagement link, a 45° steer gate, and
a probabilistic drop on the steer. So vision is *cone geometry plus a
roll*, not an Awareness threshold — and the roll is the thing to look at
for a "blind vs sees lanes" dial.

**What remains:** (a) confirm whether AWR enters the carrier's steering
at all (in the coverage system it drives cadence, not quality — expect
the same); (b) locate the **special-move selection** (juke/spin/stiff-arm)
and what gates it; (c) the separability question — the wish is to improve
vision *without* making everyone elite at special moves, which is only
possible if the two read different inputs. Determine that explicitly.

**Status: RESOLVED — `hb-vision-and-moves.md`.** Vision is a **cadence
timer** (`(255 − mean(AWR,CAR,AGI)) >> 4`, so a 50-rated back re-plans
every 8 ticks and an 88 every tick), and the gap-steer roll itself is a
flat per-running-style constant (0 / 35 / 65%) that reads no rating.
**Halfbacks and fullbacks take their style from `player+0xB07`, a byte
with no writer anywhere in the ELF** — if it is zero at runtime, every HB
is style 0, the gap-steer probability is 0%, and the block-reading vision
never runs for a halfback at all. That needs a PINE read to settle and is
the highest-value runtime measurement outstanding. Special-move selection
is fully mapped (its own cadence, three 7-byte priority rows, seven
handlers reading only AGI/AWR/BTK/STR). **Separability: impossible via
ratings — they share AWR and AGI — but fully possible via code, since the
two systems share no instruction. Two patches, not one.**

## 13. Making rating differentials *felt*: break tackle vs tackle

> I want monsters like Alstott and the Bus to FEEL their 94+ strength and
> break-tackle ratings — especially against DBs with 60s — while still
> respecting the strength and tackle ratings of linemen and linebackers.
> Is there a way to tune break-tackle vs tackle that isn't just dropping
> the tackle slider, raising HB ability, and hoping the rest of the game
> survives?

This is a **contest-math** question, and the contest is already located:
`0x00186b04` scores a tackle attempt, applies a difficulty-class modifier
(`0x00153808`), the Tackling slider (`0x001447a8`), and the `ptrk` boost,
then compares the score against `RandInt(0,100)`.

The specific thing to determine: **how much of the score is the rating
differential versus flat terms.** The class modifier adds +25/+35/+40/+50
*raw points* to a 0–100 scale — if flat additions dominate, a 94-strength
back and a 70-strength back land in nearly the same band, which is
exactly the reported feel. If so, the fix is to re-weight the
differential rather than move sliders, and it is a code lever, not a
slider one.

Also in scope: whether the defender's position (DB vs LB vs DL) enters
the contest at all, since the wish is explicitly about *who* is
tackling.

**Status: RESOLVED — `tackle-contest.md`.** The hypothesis is confirmed:
of ~100 score points, a random seed contributes 24 and a flat difficulty
add 25, while the **entire 50→99 break-tackle range moves the score by
8**. Worse, quantisation means one point per ~6.3 rating points, so
**Alstott at 94 is arithmetically identical to an 88**, and the two
functions that look like "the break-tackle move" don't read break tackle
at all — they read strength and weight, both saturating at rating ~78.
**Position never enters the contest**; only weight does. And the
reporter's instinct about sliders is arithmetically correct — RB Ability
100 buys a monster +2.8 points and an average back +18.6, *compressing*
the differential. **Fix: four words** (double the resolution of both
ratings) plus a class retune, no sliders, closed blast radius.

## 14. Line play: pass blocking, run blocking, and the absence of double teams

> Pass blocking. Run blocking. Blocking. Seriously, we play 2K because of
> the blocking. I'm tired of watching line play look like a child
> crashing his action figures together. **There is no such thing as
> double teams.**

The biggest system after #10, and the one with the most existing
groundwork:

* **14a. The block cycle end to end.** `lead-blocker-targeting.md`
  mapped selection (proximity-only), the approach (state 47, authored
  route + bounded lean + 2× velocity lead), and contact (a frozen shove
  axis re-stamped every frame for 15–30 frames, discarding the route).
  The kind 5/6 processing pass is **still unlocated** — that is the last
  unmapped part of the cycle and it is where "action figures" likely
  lives.
* **14b. Double teams.** We know there is **no already-engaged dedup**,
  so two blockers *can* end up on one defender — but that is an accident,
  not an intentional double team. Determine whether the engine has any
  concept of a coordinated double team (a second blocker joining an
  existing engagement with combined leverage), or whether the mutual
  1-to-1 lock-in is the only model. If the latter, "no double teams" is
  an architectural fact, and adding them is a much larger project than a
  patch — worth knowing early.
* **14c. Pass vs run blocking as distinct behaviours** — the slider
  function already picks pass-block vs run-block by play state, and the
  contest reads PPBK vs PRBK by phase, but whether the *behaviour* differs
  (anchor-and-mirror vs drive-and-seal) is unknown.

**Status: RESOLVED — `block-cycle.md` and `pass-vs-run-blocking.md`.**

**14b: the community's flat claim is wrong — coordinated double teams
exist**, with their own engagement kinds (7, 8, 9 — our taxonomy was
incomplete), a per-frame pass, a purpose-built helper scorer with real
double-team geometry, a four-record registry, and **a working
peel-off-to-the-second-level path**. They are gated so tightly nobody
sees them: run block only, within 60 frames of the snap, and the helper
is *frozen* (speed zeroed) once attached. Effect is a debuff on the
defender, not a sum of blockers. **Not** representable beyond two men in
an animation: the dispatcher is hand-unrolled for exactly two.

**14a: there is no kind 5/6 pass** — those kinds are owned by AI state
32, where the animation's root motion owns both transforms.

**14c: pass and run blocking are separate systems** — two states, two
~700-instruction steering solvers, opposite-signed LOS constraints, and
eight phase-exclusive contest terms including a pass-only decay that
bleeds up to 95% of the blocker's score over three seconds.

**"Action figures" has five identified causes**, chief among them that a
block is a 14–30-frame rigid two-body translation along one frozen shared
axis, with **zero stores to either player's position** anywhere in the
block code.

## 15. Blocking assignments for non-linemen: FB and WR

> Full backs: blocking targeting. WRs: **[not] blocking the corners.**
> What's up with that, especially in the slot?

Distinct from #14 because the question is *assignment*, not *technique* —
these players are not being told to block the right man (or at all),
rather than blocking him badly. Directly adjacent to #5, which found that
target selection is proximity-only with no play-assigned defender: for a
slot WR whose job is to seal a corner, proximity-only selection would
plausibly pick the wrong man or never engage.

**Status: leads, 2004-native.** The #5 fix set (B: AWR-gated selection,
C: on-route window, D: dedup) may substantially address this too — worth
checking whether WR/FB run the same state 47 path or a different one.

## 16. Pass rush: finesse vs power moves, leverage and gap control

> "Pass rush. Finesse vs power moves. Leverage and gap control.
> Run-block block-shedding."

**Status: RESOLVED — `pass-rush.md`.** All four parts answered:

* **Finesse vs power is real** — and richer than expected. Beyond the
  move families (0–3 finesse, 4–5 power, 6 bull rush), there is a hidden
  **three-axis rating profile** stamped per engagement: a POWER axis
  (block/tackle + STR + **weight**) and a FINESSE axis (+ AWR + AGI),
  with finesse moves testing one, power moves testing both, and the bull
  rush testing power only. `cpu-dt-animations.md` had mislabelled this a
  "leverage test". Also identified: `player+0xAEC` is **weight**, the
  power currency. A third "overall" axis is computed and **never read** —
  a dead field and a free hook.
* **Leverage is real and dominant** — a 4× swing (1.0 → 0.25) on the
  blocker's score from the angle the rusher has won. Dwarfs every rating
  term. The bull rush is exempt.
* **Gap control does not exist.** A lineman gets a frozen (angle,
  distance) rush lane from the play data, anchored at the snap, with no
  gap identity, no line-alignment reference and no re-fit.
* **Run vs pass shedding is one system** with the blocker's rating
  swapped and one extra animation clip.

**Why it feels undifferentiated — two independent causes:** AI move
choice reads **no ratings at all** (uniform random plus a side remap;
census-proven), and the contest **saturates**, so once the multipliers
push the shedder's score well above the blocker's, rating changes stop
moving the outcome.

Fix candidates run from one-word (widen the STR-65 power gate, make the
bull rush respect leverage, make attempt rate depend on a rusher rating
instead of tackle) to a cave (rating-weighted move selection — the
headline ask).

## 17. Punter placement logic (the coffin corner)

> "I can put 20/20 punt power and accuracy and they never want to coffin
> corner me. They will often just drill touchbacks... they don't even try
> to drop the ball inside the 15 or 10 and get a bounce."

**Status: RESOLVED — `punt-logic.md`.** The hypothesis (that no
coffin-corner logic exists) is **refuted** — but the complaint is right.
A real ballistic coffin-corner solver exists at `0x001598c0`: it computes
flight time, compensates for wind, aims one yard outside the sideline,
and clamps the landing to the 1-yard line. It is gated behind four
conditions that must all pass — **CPU kicking side** (a human punter can
never reach it), kick type must be punt, ball beyond the punting team's
own 40, **and a 25% coin flip**. Everything else takes a default path of
three random numbers with **no field-position input at all** — the AI
punter swings as hard from the opponent's 35 as from his own 10, while
the field-goal arm ten instructions away *does* read field position.

Why the accuracy slider never helps: on the coffin path it is
**overwritten and does nothing**; on the default path there is no aim
point to sharpen. Punter ratings affect magnitude and noise only — **a
95-accuracy and a 60-accuracy punter choose the same aim point** — and
PKAC has a dead zone below rating 70 where every punter is identical
(logged for #19).

**Fixes are one-word each**, because the logic is already there: raise
the 25% to always (`0x0015a4bc`), aim for the 10 instead of the 1
(`0x00159a18`, 49.0 → 40.0), widen the field-position band
(`0x0015a4d4`), and optionally aim *inside* the sideline for a bounce
(the ±27.6667 pool words). A genuine feature, by contrast, would be
field-position-aware punt *power* — a code-cave project reusing the FG
solver's iterative pattern.

## 18. AI play calling: the algorithm, situational contradictions, and the small play pool

> End-of-half and end-of-game play calling is contradictory — the AI is
> down and wants to score, but calls a run, then burns a timeout to run
> again, not knowing whether it wants to milk the clock or score. In a
> Boise State sim, the AI goes to the same Strong-I FB weak dive
> predictably rather than choosing among the short-yardage plays in the
> book. **It generally feels like the AI selects from a much smaller list
> of plays than it has access to.**

Three related but separable questions:

* **18a. The selection algorithm itself.** Anchored: the CPU play caller
  is around `0x001459b4`, and it already consumes the `ptrk` play-history
  data (`play-tendency-ai.md`) — including the run/pass tendency mask and
  the recent-success factor. So we know *some* of its inputs.
* **18b. Situational logic** (clock/score awareness). The contradiction
  described — hurry up *and* run *and* burn a timeout — suggests either
  independent subsystems disagreeing (a clock manager and a play selector
  that do not share intent) or a missing two-minute mode. Determine
  whether a game-situation state exists at all.
* **18c. The effective play pool.** The strongest and most testable
  claim: does the selector consider the whole playbook or a filtered
  subset? A formation/personnel filter, a "situational" sub-list, or a
  scoring function with a steep favourite would each produce the reported
  repetitiveness. This one is measurable statically by reading the
  candidate-enumeration loop.

**Status: RESOLVED — `ai-play-calling.md`.** Both halves confirmed, with
different causes. **The pool is authored, not filtered**: the enumerator's
only predicate is the AI group, and the playbook table holds 175 rows
across *all* groups — under 18 plays per group in practice. **The
predictability is code**: a class renormalisation forces one play family
to own a fixed share of the roulette regardless of how many plays are in
it, so a lone run in a thin short-yardage group gets 80% of the draw.
**The AI applies no anti-repetition to itself** — every `ptrk` read is of
the *opponent*. **Situational state is complete** (timeouts, score,
quarter, clock, down, distance, LOS) but the policy is a **bytecode
script loaded from the disc**, with no shared intent variable — hence
hurry-up-and-run-and-timeout. Also: the CPU **never pinches or spreads**
its line (both weighted zero), and the deliberation clock reads only
skill level, never the game clock.

## 19. Do the PS2 games use rating THRESHOLDS? (cross-cutting)

> "I was doing the fake punt pass in NCAA 2004 on CPU-vs-CPU Varsity
> sliders. A backup safety playing LT with **40 pass block** would watch
> defenders run right by him. I subbed in a guy with **44** and that was
> enough to bump and fail — at least knock the rusher off his path. A TE
> with about **55** did more than bump: he got the rusher to a dead stop
> before breaking the block. Same consistent results, again and again.
> The rating must have SOME minimum thresholds. The animations and
> quality are impacted by the ratings, but what do they really do, and do
> the PS2 games use thresholds?"

The most generalisable question this project has been asked, and it comes
with a clean natural experiment: same play, repeated, only the protector's
rating changed, three distinct behaviours at 40 / 44 / 55.

**Thresholds demonstrably exist.** The shed contest gates pass-rush moves
4/5 behind effective Strength > 165.75 — and 165.75 = 65 × 2.55, i.e. a
rating of exactly **65** on the 0–100 scale. So the answer to "do they use
thresholds" is already yes; the work is enumerating them and deciding
whether the engine is mostly continuous with a few gates, or genuinely
banded.

**Method:** sweep for rating loads (`lh`/`lhu` in the `+0xB70..+0xB9A`
window) followed by a comparison, and convert every constant back to a
0–100 rating by dividing by 2.55 — round numbers are the signal.

**A subtlety worth watching:** a threshold can be created by a *clamp*
rather than a comparison. The contest clamps scores to zero
(`movz s3,zero,v1` / `movz s2,zero,v0`), so a rating low enough to drive a
term negative produces a player who cannot win — or perhaps cannot even
engage, which would explain the 40-rated protector making no contact at
all.

**Confirmed thresholds so far:** the power-move gate at **STR 65**
(`pass-rush.md`, three single-reader constants), and:

**First confirmed finding from another lane:** punter accuracy (PKAC)
has a **forgiveness dead-zone** whose useful band is rating **70–100** —
below 70, every punter behaves identically (`punt-logic.md`). That is a
threshold created by a clamp on a curve, not by an `if`, which is exactly
the pattern this entry warns about.

**Status: RESOLVED — `rating-thresholds.md`.** Yes, but selectively.
A closed census of **278 rating read sites** finds the engine ~87%
continuous — yet **the contact game is banded at 4 bits**, and the
community's 44 is a band boundary exactly. Confirmed hard gates and dead
zones include power pass-rush moves at **STR 65**, QB throw power below
**70** (every QB identical), carrying below **50** (term hard-zeroed),
agility below **50**, and punter accuracy below **70**. A clamp can
create a gate without a comparison, as predicted.

## 20. Press / jam: how is the win determined? (WR vs DB, TE vs LB)

> "How is win/loss determined for the press? I assume strength vs
> strength and awareness vs awareness. What I saw in M02 & M03 especially
> was TEs constantly getting jammed, and human WRs getting jammed easily
> and often — especially on All-Madden."

**Anchored:** zone state 37's enter gates a press on the defender being a
**CB** (`player+0xB04 == 16`) and calls `0x001a0f28` behind a 50% coin
flip with a float argument 0.9. Man coverage (state 22) sets press
alignment from |ΔX| < 3.5 / < 5.5. So the jam exists and has an entry
point; its *contest* is unmapped.

Specific things to settle: which attributes actually contest (confirm or
refute the STR/AWR assumption); whether a position or size term exists
(the TE complaint); whether the difficulty class enters (the All-Madden
complaint); and what winning/losing does — animation, speed penalty,
route delay, knock-off-path — and for how long.

**Owner's requirement (2026-08-10):** **linebackers need to be able to
jam at the line** — *it is not football if a linebacker cannot jam a much
smaller slot receiver.*

**Status: RESOLVED, and the premise was wrong — `press-and-routes.md`.**
Recorded here as a capability gap on 2026-08-10; investigation shows
**linebackers can already jam**, on three of the four jam paths, and one
of those arms is *explicitly* position-gated to linebackers. The shared
eligibility helper has **no position check**, and a closed field census
finds **zero position reads and no size field anywhere** in the jam. The
contest even favours an LB over a TE.

**The real gap is zone, not man.** An LB in man coverage jams at
assignment and every frame after; an LB in the underneath hook zone can
only jam a receiver **already inside his zone rectangle**, which a TE
releasing off the line is not yet. Targeted fix: pull that zone
rectangle's near edge back to the LOS (data, per zone kind, low risk).
Verify on the rig first — the prediction is that LB man-coverage jams
already happen today.

**The jam contest itself:** `P(win) = 65 + (50/255)·[(dSTR+dAGI)/2 −
(rSTR+rAGI)/2]` vs one d100. Strength confirmed, **awareness refuted —
the second term is agility**. Every realistic matchup lands in **57–74%**
because the base is a flat 65. One play-shell modifier adds a **flat
+50**, taking any matchup to a guaranteed win. Difficulty does not appear
in the jam code at all. "Constantly" is attempt *frequency* — the roll
retries every frame the geometry allows.

## 20b. Receivers held up by traffic while releasing (a separate issue)

> "It's possible that TEs get jammed in the sense that they get held up
> when trying to go on their route — like they're bumping into players
> that are trying to blitz."

**Explicitly a different issue from #20**, and tracked separately at the
owner's direction: one is a *missing capability* (linebackers cannot
jam), the other is an *unwanted engagement* (offensive players getting
stuck on contact they did not seek). They should not be conflated, and
they do not share a fix.

The hypothesis is well-founded because the identical mechanism is
**already confirmed on the offensive line**: the proximity pairing is
mutual with **no role exemption**, and the offense has **no shed**, so an
offensive player who brushes a defender is captured and held for the
15–30 frame engagement timer, re-armed on every re-contact
(`lead-blocker-targeting.md`, the hang-up section). A tight end releasing
inline runs straight through blitz traffic.

**Status: CONFIRMED — `press-and-routes.md`.** A route-running receiver
**can** be captured, there is **no exemption** for him, and he has **no
escape** — the shed move is unreachable from the route state's AI-think
*and* its USER-think. Worse, the route state's own code checks its
engagement kind and **abandons the route entirely** when engaged, handing
the receiver to the frozen shove axis. He is passive for 15–30 frames,
re-armed on every re-contact. On screen this is indistinguishable from a
jam through a completely different code path — which is exactly why
player reports cannot separate #20 from #20b. Fixes are code caves; the
honest one is a **capture exemption** that prevents an engagement which
should never have formed.

**Status: open, 2004-native, in progress (Lane W).**

## 21. Route running: what actually governs it?

> "How about route running? I assume this is an awareness battle + normal
> athleticism."

The receiver's route state is **31** (enter `0x001cabd8`, think
`0x001cb008`) — the same state that installs state 1 when he becomes the
carrier. The question is whether a route is simply authored waypoints
followed exactly, or whether there is a *quality* term modulated by
attributes, and whether the covering defender contests it at all.

Also in scope, because it feeds #10: **is there any explicit "separation"
or "openness" quantity computed for a receiver** — a number a QB read
could consult? If none exists, that constrains what #10 can possibly find.

**Status: open, 2004-native, in progress (Lane W).**

## 22. The catch process: strips, and whether CATCHING still matters after the catch

> "What about knocking the ball out after it has been caught but before
> it counts as a catch? Do strength, tackle, awareness or anything else
> deal with that? Does catch still matter once the WR has caught it, to
> also hang on to it during contact? I would assume so, but does it?"

**Anchored:** the ball-arrival resolver `0x00255250` dispatches on the
contesting player's animation (68 = swat → code 32, 69 = the catch
animation), and the catch resolver `0x00256030` is dominated by attr 5
(PCTH) + AWR/8 + attr1/16. What is unmapped is everything *after* the
catch resolves.

Specific questions: where does possession legally transfer
(`SetBallCarrier 0x001fff18`) relative to the catch animation — is there a
"process of the catch" window at all? Is there a post-catch strip or
jar-loose check, and what governs it? And precisely: **does attr 5
(catching) get read anywhere after the catch, or does attr 4 (carrying)
take over immediately?**

Bonus scope, because the community will ask: with **no fumble slider** in
this game (`slider-behavior.md`), what *does* govern fumbles?

**Status: RESOLVED — `catch-and-fumble.md`.** Both community assumptions
are correct and the engine is unusually sophisticated here. There is a
real **process of the catch**: possession transfers immediately but the
ball stays *unsecured* for ~21 ticks (41 diving), and a ball jarred loose
in that window is scored an **incompletion**, not a fumble. A dedicated
**post-catch strip** exists, resisted by catching (full weight) and
carrying (quarter), driven by tackle and strength, with the **big hit
worth 2–6× a standard wrap** — and the **WR Catching slider is the strip
slider**, inverted. And precisely: **catching is substituted for carrying
as the ball-security rating** while unsecured, then stops mattering
entirely. Bonus: the tackler's ratings are read **nowhere** in ordinary
fumbles; toughness has **no gameplay consumer** at all.


---

## Wave 2 triage

**Update 2026-08-10: nine lanes launched** covering #13, #14a/b, #14c,
#16, #17, #18, #19, #20/#21, #22. Remaining unstarted: #10 (the QB
system, which wants a dedicated multi-lane push), #12, #15, and the
material-blocked entries.

Original ordering, in rough value-per-hour terms:

1. **#13** rating differentials in the tackle contest — well-anchored, and
   the answer directly serves a stated wish.
2. **#17** punter intent — small, self-contained, likely a quick win.
3. **#16** pass-rush move families — mostly labelling tables we have.
4. **#18c** the effective play pool — one loop to read.
5. **#12** HB special-move gating — half-answered already.
6. **#14b** does the engine support double teams at all — a yes/no that
   scopes the whole blocking project.
7. **#10** the QB decision system — the biggest prize and the biggest
   effort; worth a dedicated multi-lane push rather than a single agent.

Blocked on materials: **#11** (needs M2002/M2003), and wave 1's #1/#3/#4
(need 2005-era ISOs).

---

## Cross-cutting notes

* **Code caves: surveyed and proven (2026-08-09, `code-caves.md`).** The
  blocker on nearly every designed fix is cleared: ~9.2 KB / 2,312
  instructions of zero-reference dead code exist, any site can reach any
  cave with a one-word `j`, and the pnach mechanics are demonstrated end
  to end with a fully-encoded worked example (the lead-blocker
  minimum-steps gate: 11 lines, one clobbered register). The budget is
  fixed with no growth path, so caves are pooled, not claimed per fix.
  Each cave still needs its "is-it-really-dead" breakpoint test on the
  rig before use — that test now gates all cave work.
* **The remaining enabler is play-file / ISO data reads**, for the *data*
  fixes (pull-path depth for #5's hang-up, per-play targeting delay) and
  to close the assignment-class → AI-state mapping.

* Items 1, 3, 4 need ISOs we don't have (M2002, M2003, M2005, NCAA
  2005/2006). Once on the rig, the extraction path is proven:
  sector-listing + `tools/lzh1.py` for UIS-era files, `recon/mipsdis.py`
  + `recon/fpudis.py` for the ELFs, and the 2004 idiom signatures for
  fast cross-title location of the same subsystems.
* Items 2, 5, 6, 9 are 2004-native and can start any time; 6 and 9
  already have candidate mechanisms identified.
* Item 7 is the umbrella: each resolved item feeds its tuning constants
  into the default-uplift pnach.

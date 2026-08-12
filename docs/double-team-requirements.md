# Double teams — requirements before any patch

Recorded 2026-08-11 from the operator. **Requirements only — nothing here is
designed or patched.** `block-cycle.md` holds the existing reverse engineering;
this document holds what the fix has to achieve and how it will be judged.

> "we need to actually fix double teams … make it so the blocking effect of the
> first guy is multiplied by at least 150% … none of this nonsense of just
> lowering strength … we need to actually move them backwards … we need to
> physically move them with the block … it needs to be EXACTLY like a real
> double team"

## Scope

Separate change from the pass-protection decay work and from
`defender-catch-requirements.md`. Different code path, own acceptance tests,
own patch. Rule 1.

## The operator has ruled out the obvious patch

`block-cycle.md` lists **DT-4 — strengthen the double-team debuff** (data word
`0x005FF138`, −0.13 → −0.30). That is a defender *nerf*, and it is explicitly
rejected: "none of this nonsense of just lowering strength." The requirement is
to make the **blockers stronger and the block physical**, not to make the
defender weaker. Any candidate that reaches the metric by subtracting from the
defender fails on intent even if the number moves.

## The architectural wall, stated up front

Two findings already in the repo constrain every option here, and one of them
looks fatal to R3 until read carefully:

* **"Zero stores to any player's position anywhere in the block code. Neither
  body is ever pushed; there is no positional coupling at all."**
  (`pass-vs-run-blocking.md`)
* But a block *is* **"a 14–30-frame rigid two-body translation along a frozen
  shared axis whose only magnitude is a rating ratio"**.

Both are true. The bodies do move — the block writes a **shared bearing and a
drive magnitude** (`staged_drive` +0x404, `staged_bearing` +0x40C, copied into
`desired_bearing` each frame) and ordinary locomotion carries both men along
it. So "physically move them with the block" does **not** require inventing a
position write; it requires changing the drive magnitude and its sign/direction.

**A patch that adds direct position stores is an anti-goal** — it is the warp
failure mode already banned in `lead-blocker-requirements.md`, and it would
break the one thing this system currently does correctly.

## Requirements

### R1 — The primary blocker's contest effect is multiplied by ≥1.5 when doubled

The operator's number. Applied to the **blocker**, never by subtracting from the
defender.

*Acceptance:* with a registered double team (role bytes `+0x437` = 0 primary,
1 helper), the primary's contest components are ≥1.5× their single-block value
for the same pairing on the same savestate. Measured per-rep, on the same
player, with the harness's episode segmentation — the composites are recomputed
at every lock-in, so a whole-play average sees nothing.

*Confound to control:* `pass_protection.py` established that these components
decay on the **global snap clock** (`worst_drop_late` 0.678 against
`worst_drop_early` 0.252). A 1.5× multiplier measured at frame 200 against a
single block measured at frame 40 would be comparing two points on the decay
ramp, not two blocking schemes. Compare at matched snap frames.

### R2 — The helper is not a statue

`block-cycle.md` item 5: **the double-team helper has his speed zeroed.** In a
real double team both linemen drive; one man standing still is the single
largest gap between this system and the thing it is imitating.

*Acceptance:* the helper's speed is non-zero for the duration of the rep, and
his displacement along the shared axis is within a small tolerance of the
primary's. Two men moving together, not one man and a prop.

#### MEASURED 2026-08-11 — the statue is real, and block-cycle.md attributes it wrongly

That item carries **no address**, so it was unproven. Read live from slot 8
(mid-play, ball in the air), all 22 players:

| player | `ai_state` | `speed_cmd` (+0x1E8) |
|---|---|---|
| LT, LG | 33 run blocking | **1.0000** |
| C, RG | 33 | 0.4600 |
| **RT** | **32 — scripted two-man block animation** | **0.0000** |

**The zeroing tracks `ai_state` 32, not the double-team role.** In the same
frame every player's `dt_role` reads **5** and `dt_record` reads **0** — no
double team is registered anywhere on the field — and the statue is present
regardless. So R2 is a property of the **two-man block animation** (state 32,
which `addresses.yaml` already records as owning engagement kinds 5 and 6),
and a fix aimed at the `dt_role` == 1 helper would miss it entirely.

**New fact: `dt_role` 5 means UNASSIGNED.** `block-cycle.md` gives the enum as
0 primary / 1 helper / 2 doubled defender / 3 peel-off and does not mention 5.
The engine tests for it explicitly — `lbu v1, 1079(a0)` then `bne v1, 5` at
`0x001f66dc` and `0x001f670c` — which reads as an empty-slot check. Any metric
that treats a non-zero `dt_role` as "in a double team" would count all 22
players.

**Also answers Q4 for the pass state:** no double team registers on slot 8,
which is what DT-1 (`0x001F6560`, double teams register on run block only)
predicts. A double-team baseline needs a run savestate, or DT-1 applied first.

#### The write, located — and it is not a zero

The state dispatch table is now enumerated (`state-dispatch-table.md`). State
32's row:

```
enter 001e7ee0   can_leave 001e8258   ai_think 001e8088
user_think 001e8318   exit 001b0528 (shared stub)   extra 0
```

Those sit at `0x001e8xxx`, **outside** the `0x001B0000-0x001C4000` range first
swept for the write — which is why six irrelevant candidates came back. Across
all four of state 32's functions there is exactly **one** store to `+0x1E8`:

```
001e81fc  bne v0, zero, 0x001e8230   ; guard: skip the whole block
001e8218  lwc1 f0, -26548(gp)        ; 0x005fef3c = 0.46
001e8224  swc1 f0, 488(s0)           ; speed_cmd = 0.46
```

**It writes 0.46, not zero**, and `0x005fef3c` has exactly one reader in the
image. So the earlier framing — "find the zero and remove it" — was wrong.
Nothing in state 32 zeroes the speed. The block that *grants* speed is behind a
guard at `0x001e81fc`, and when that guard fails the field is simply left at
whatever it held, which on the sampled frame was 0.

That relocates R2 entirely:

* The lever is **the guard at `0x001e81fc`**, not a zero constant.
* `0x005fef3c` (0.46) is a **sole-reader data word**, so it also tunes how fast
  the man moves once he is moving — the cheapest possible knob if the guard
  turns out to be right and only the magnitude is wrong.
* The live sample is consistent: C and RG, in state 33, read exactly **0.4600**
  — the same constant — while the state-32 man reads 0. Whatever the guard
  tests, it was failing for him and passing for them.

#### The guard traced — and it is probably not the statue either

`v0` at `0x001e81fc` is the return of **`0x001f7c98(player)`** (4 callers). That
function is a compound admission test which **returns 0 on every early exit**
(`s4` starts at zero and each bail returns it):

```
001f7cbc  jal 0x00154790          ; a play/mode code
001f7cc4  beq v0, s2(=4), ...     ; must be 4 ...
001f7cd8  bne v0, v1(=7), ->ret0  ; ... or 7
001f7ce0  jal 0x00260598          ; offense_team()
001f7ce8  bne s0, v0, ->ret0      ; must be ON OFFENCE
001f7cf8  bne v1, v0(=1), ->ret0  ; must be handle kind 1
001f7d0c  beq v0, s3(=158), ->ret0 ; a type code must not be 158
```

The guard skips the speed grant only when this is **non-zero**, so in the
default case it *permits* the grant. **That makes it an unlikely explanation for
a statue**, and the third framing of this problem to be wrong. Recorded anyway,
because "this is not it" is worth as much here as a hit.

**Better candidate, 22 instructions earlier and previously walked past:**

```
001e8184  lw v1, 12(s0)
001e8188  andi v0, v1, 0x0004
001e818c  beq v0, zero, 0x001e8234   ; bit 2 clear -> skip the whole block,
                                     ;   speed write included
```

A flag word at `player+0x0C`, bit 2. The same word is masked and rewritten at
`0x001e81a8`/`0x001e81b0` (clearing bit 2, `0xFFFFFFFB`) and again at
`0x001e81cc` (clearing bit 11, `0xFFFFF7FF`), so this is a per-frame latch the
state machine drives — the block is plausibly a **once-per-entry** setup that
runs on the frame the animation starts and never again. If so the man is not
being held at zero; he is being *granted* speed once and never re-granted.

#### PROBED 2026-08-11 — the one-shot trigger is confirmed, and speed decays

Sampled on slot 8, one player, every frame the poll loop could reach (182 of
455). The transition is visible in a single frame step:

```
snapf  state  speed     flags+0x0C   eng  bit2
 275     33   0.0000    0x00040000    1    0
 276     33   0.4600    0x00040000    1    0
 304     33   0.7700    0x00040000    2    0
 386     62   0.5000    0x00040000    0    0
 388     62   0.0000    0x00040000    0    0
 442     62   0.0000    0x00040004    0    1     <- trigger armed
 443     62   0.1908    0x00040000    0    0     <- consumed, speed granted
 444     62   0.1660 ... 447  0.1380              <- and then it decays
```

**Bit 2 of `player+0x0C` is a one-shot trigger.** It is set, the block runs, the
block clears it with the `0xFFFFFFFB` mask, and speed is non-zero on the very
next sample. The "granted once, never re-granted" reading is confirmed as a
mechanism, and the follow-on decay (0.1908 → 0.1380 over four frames) supplies
the missing half: **a man is not held at zero, he is granted speed once and
then decays to zero.** A statue is a man whose trigger has not fired recently.

Three honest limits on this:

* The transient was caught in **state 62**, not 32 — the mechanism is shared,
  not state-32-specific. This run never entered state 32 at all, so it does not
  confirm the same sequence there.
* **0.46 is not state-32-specific either.** Player 10 was in **state 33** and
  received exactly 0.4600 at frame 276. Whatever writes it is shared, so a
  patch to `0x005fef3c` would move run blocking too.
* An 11-player, 4-read-per-player loop sampled ~1 frame in 6 and **missed the
  window entirely** on the first attempt. A one-frame transient is not
  observable without cutting the per-frame read count; the useful run polls a
  single player.

`player+0x0C` still has no entry in `addresses.yaml`. It should get one — a
per-player flag word whose bit 2 is a one-shot "grant speed on entry" trigger —
before anything is patched against it.

**Confirmed on the way past:** state 32 does own engagement kinds 5 and 6, and
advances one to the other itself — `lw v1, 992(s0)` (+0x3E0), `bne v1, 5`,
`sw 6, 992(s0)` at `0x001e81dc`-`0x001e81ec`. That was `addresses.yaml`'s claim
and it holds.

### R3 — The doubled defender is driven backwards

*Acceptance:* the defender's position moves **away from the offensive backfield
along the shared axis** by a measurable distance over the rep — target ≥1.0 yd
on a won double team, against a baseline that should measure ~0. Measured from
`pos_x`/`pos_y`, which `pass_protection.py` now samples on **both** sides.

*Mechanism constraint:* achieved through the drive magnitude and bearing, not
through added position writes. See the wall above.

### R4 — Double teams register on pass protection, not only run blocking

`block-cycle.md`'s **DT-1** (`0x001F6560`, `bne` → `nop`) is filed there as
"highest value, lowest risk". It is a prerequisite for testing any of the above
on a pass savestate, and slot 7 is a pass state.

*Acceptance:* a double team registers with the same role bytes on a pass play.

### R5 — No regression on ordinary single blocks

*Acceptance:* on a savestate with no double team registered, every
`pass_protection.py` metric is unchanged within noise. This is the "must not
break" surface, and per rule 2 it is tested on its own savestate before any
combination.

## The operator's test case (slot 6)

A subject-matter expert prescribed, on the slot 6 misdirection run:

> the **left tackle and left guard double-team the right DT**, leaving the other
> DT unblocked for the **kick-out block from the guard**.

This is the concrete scenario to build toward, and it is a good stress test of
the framework because it needs three things the harness cannot currently
express:

1. **Assigning specific blockers to a specific defender.** There is no readable
   "assigned target" field — `engagement_link` (+0x3E4) is *contact*, not
   assignment, and the pool-membership word is still unlocated (SEAM REQUEST 6,
   `fb-wr-blocking.md`). So "did the LT and LG double the right DT" can today
   only be inferred from who they end up engaged with.
2. **Deliberately leaving a defender unblocked.** Every current metric treats an
   unblocked defender as a failure. Here it is the design.
3. **Naming players by role** (LT, LG, right DT). The harness identifies
   blockers behaviourally on purpose, because guessed position constants have
   produced wrong answers before. Position bytes 5–9 are LT/LG/C/RG/RT and were
   confirmed on the slot 7 dump, so this is available — but it is the first
   spec that would depend on it, and that dependency should be explicit.

**Which of these the framework should learn is itself a design question**, not a
detail: item 1 in particular may be better served by locating the assignment
word than by inferring it forever.

## Open questions

* **Q1. Where is the ≥1.5× applied?** The helper scorer is `0x001f4c40` and the
  role bytes are `+0x436`/`+0x437`, but the site where a double team's
  contribution is combined into the primary's contest score is not identified.
* **Q2. What zeroes the helper's speed** (R2), and is that write on the block
  path or the locomotion path?
* **Q3. Does the drive magnitude have a sign** that can push the defender
  backwards, or only a speed along a bearing chosen elsewhere? R3 depends on
  the answer.
* **Q4. Does slot 6 even produce a double team today?** Baseline first — R1's
  ratio needs a denominator.

## Anti-goals

* **No defender nerf.** Explicitly rejected by the operator.
* **No position warps.** No direct stores to a player's position from block
  code; drive the existing translation instead.
* **No metric-only wins.** A patch that moves the numbers while the animation
  still looks like two action figures has not met "EXACTLY like a real double
  team".

## CORRECTION 2026-08-11 — the operator's eyes beat the metrics

The slot 9 run reported `helper_speed` 0.435 against `primary_speed` 0.460 and
`defender_pushback` 3.2-4.3 yd, and I read that as "R2 and R3 are close to
satisfied already". **The operator, watching the screen, said he saw nothing of
the kind** — only "a right guard briefly touch someone and then go to the second
level", and asked to extend the duration.

He is right. Measured from the same file, per-player double-team duration:

| player | role | frames | window |
|---|---|---|---|
| RT (pos 9) | 0,1 | **17** | 2-36 |
| TE (pos 4) | 0,1 | **30** | 2-43 |
| **RG (pos 8)** | 0 | **13** | **27-43** |
| DE (pos 10) | 2 (doubled) | 17 | 2-36 |
| **LB (pos 13)** | 2 (doubled) | **13** | **27-43** |

**Every double team on this play is finished by frame 43 of 308** — a fifth to
half a second. The right guard pairs on a *linebacker* for 13 frames and
releases, which is precisely what the operator described.

**Why the metrics lied.** `helper_speed` and `defender_pushback` were computed
over the whole play. The double team occupies 13-30 of 308 frames, so both
numbers are dominated by what happens *after* it ends — pursuit drift and free
running. `defender_pushback` in particular measured a defender flowing to the
ball, not being driven. Both were flagged as needing episode-scoping and both
were quoted anyway before that was done.

**This is the same defect class as the pass-protection decay**, where a
whole-play statistic saw nothing because the composites reset at every lock-in.
The rule that keeps being relearned: **on this engine, any statistic not scoped
to an engagement episode is measuring the wrong thing.**

### R6 — A double team must persist (NEW, and now the primary requirement)

*Acceptance:* the pairing holds for a target duration under contact rather than
releasing on contact. Baseline is **13-30 frames**; the operator's ask is
explicitly to extend it.

This reorders the document. R1's 1.5x multiplier and R3's pushback are close to
irrelevant against a block that lasts a fifth of a second — a stronger block
that still releases at frame 43 changes nothing on screen. **Duration first.**

`block-cycle.md`'s "14-30-frame rigid two-body translation" is now identified as
describing this exact window. It was never a bug report; it is the design, and
the design is the problem.

## The real pushback: 15 inches, and the operator called it

Episode-scoped, measured only while `dt_role == 2`:

| defender | frames | dy | dx | distance |
|---|---|---|---|---|
| DE (pos 10) | 17 | **+0.410 yd** | -0.090 | **15.1 in** |
| LB (pos 13) | 13 | **-0.213 yd** | -1.184 | 43 in, but *forward* and lateral |

Against the whole-play figures I quoted: +0.873 and **+3.178** yd. The 3.178 was
almost entirely pursuit after the block ended.

The operator, watching, said "maybe a few inches" and "I don't see much at all".
Both readings were correct and both of my numbers were wrong, for the second
time on the same run. **Fifteen inches over seventeen frames is the true
baseline for R3**, and one of the two doubled men is not driven backwards at all.

### R6 duration candidate — `0x001f6b0c`

```
001f6b0c  2402001e  addiu v0, zero, 30
```

The only immediate 30 in the block-cycle region `0x001F4000-0x001F7000`, which
also holds five immediate 20s. `block-cycle.md` describes the block as a
"14-30-frame rigid two-body translation", and the measured durations were 13,
17 and 30 frames — the 30 lands exactly on the observed maximum.

**Candidate 10x test:** `2402001E` -> `2402012C` (30 -> 300), one word, data-free
and reversible. **Unverified**: it has not been confirmed to be the duration of
*this* window rather than some other 30-frame timer in the same file, and it
must be tested alone against slot 9 with `double_team.py` before being combined
with anything (rule 2). The acceptance signal is `dt_role == 2` persisting past
frame 43 and the episode-scoped pushback exceeding 15 inches.

## R6 patch attempt 1 — `0x001f6b0c` REFUTED

Deployed `patches/14F8B841.dt-duration-10x.pnach`, verified in memory, ran
`double_team.py` on slot 9. Durations came back **17 / 30 / 13 frames, ending
at 36 / 43 / 43** — identical to the unpatched baseline in every figure.
`0x001f6b0c` does not control the double-team hold.

Two things worth carrying:

**The patch applied as a BYTE write, not a word.** `patch=1,EE,001f6b0c,extended,2402012c`
put `0x2402002C` in memory, not `0x2402012C` — only the low byte took, so the
immediate became 44 rather than 300. That was still a valid test of the
address (44 > 30, so any real duration constant would have moved something)
but the `extended` type cannot be trusted to write 32 bits here. Verify the
patched word over PINE before every measurement; the pnach being *parsed*
("Found 1 cheats in ..." in the log) says nothing about what landed.

**The durations are not one constant.** 13, 17 and 30 frames on three blockers
in the same play, reproducible across runs. `block-cycle.md` describes the
block as a "**14-30**-frame" translation and its steering as "recomputed only
every **15-30** frames". A single immediate cannot produce three different
values — so the hold is **computed or randomised over a range**, and hunting
individual constants is the wrong shape of search. The five immediate 20s in
the region are unlikely to be it for the same reason.

**Next:** find where the 14-30 range is produced rather than where a 30 sits.
Likely a rand-scaled span (`lo + rand*(hi-lo)`) near the engagement-timer write
(`engage_timer` +0x42C). That is a static search for the *arithmetic*, not for
a literal.

## R6 REFRAMED — it is target re-selection, not duration (operator, 2026-08-11)

> "it was never related to duration but target priority right"

Almost certainly right, and it explains the refutation above better than the
refutation did. Three lines of evidence:

1. **Three different durations in one play** — 13, 17 and 30 frames on three
   blockers, reproducible across runs. A shared duration constant produces one
   number. Three blockers releasing at three different moments is three
   independent *decisions*.
2. **`dt_role` 3 is "peel-off"** (`block-cycle.md`). The engine has an explicit
   role for *leaving* a double team. A pure timer would not need one.
3. **`reselect_timer`** exists on the player struct, and
   `pass-vs-run-blocking.md`'s **P2** is already named "**soften the
   assignment-drop test** so a pass blocker cannot shed his man during the
   approach", at `0x001ca0a8` / `0x001ca0c8` / `0x001ca104`. That is the same
   mechanism described for a different symptom.

The operator's own description — "a right guard briefly touch someone and then
go to the second level" — is a blocker who **re-targeted a linebacker**, not one
whose clock expired. Climbing to the second level is what a combo blocker is
*supposed* to do; the defect is that he does it immediately instead of after
driving the down lineman.

**R6 restated:** the helper must not re-select a new target while the double
team is live and the doubled defender has not been displaced. The lever is the
assignment-drop / re-selection test, not a duration constant — which is why
patching a literal 30 changed nothing, and why the five immediate 20s would
also have changed nothing.

**Next static search:** what writes `dt_role` 3 (peel-off), and what feeds
`reselect_timer`. `0x001f6730` and `0x001f68e0` both `sb v0, 1079(a0)` — writes
to the role byte — and `0x001f672c` loads the immediate 3 directly before one
of them. That is the peel-off write, and its guard is the real target.

## R6 FINAL TARGET — delay `reselect_timer` on double teams

> "it seems like the double team stops being important to it super quickly, if
> ever … which is why even trying to adjust the duration would make no change …
> so we need to delay the reselect_timer on double teams … probably a second"

`addresses.yaml` already carries the decisive line, from
`lead-blocker-targeting.md`:

> **`reselect_timer`, +0x432, u16 — "Initialised to 30 − blockRating/16."**

That single note closes the loop on everything above:

* It is **computed per player**, not a constant. A 30-frame literal cannot
  produce 13, 17 and 30 on three blockers; `30 - blockRating/16` can. This is
  why patching `0x001f6b0c` changed nothing and why the five immediate 20s
  would not have either.
* It is a **re-selection** clock, not a hold duration — matching the operator's
  reading that the blocker re-targets rather than times out.
* **Better blockers re-select SOONER** (higher rating subtracts more). A
  99-rated lineman abandons his double team faster than a 60-rated one, which
  is precisely backwards from football and may be a defect in its own right,
  independent of double teams.

**The requirement:** while a double team is live (`dt_role` 0 or 1) and the
doubled defender has not been displaced, `reselect_timer` must be extended by
roughly **one second (60 frames)** — the operator's figure. The base 30 becomes
~90, or the double-team case takes its own branch.

**Anti-goal:** do not extend re-selection globally. Every blocker in the game
uses this timer, so a blanket change would freeze single blockers onto their men
and is the "must not break" surface in R5.

**Next step, static:** find the write to `+0x432` (u16, so `sh`) and the site
computing `30 - blockRating/16`. A sweep at `+0x430` returns nothing — the
offset is `0x432`, and getting that wrong wastes a pass. Then gate the extension
on `dt_role` being 0 or 1, which is readable at `+0x437` in the same struct.

## R6 final design — prioritise the double team, peel on outcome not on a clock

> "we need to prioritise the double team greatly and we need to make it so they
> peel off after x amount of time to reselect appropriately, and sometimes the
> answer is to just double team a big 3-4 nose center to the ground"

This is the whole requirement in one line: **the engine peels on a timer;
football peels on an outcome.** A combo blocker leaves when the down lineman is
*controlled* — and if he never is, he never leaves.

### R6a — Priority: the double team outranks re-selection

While `dt_role` is 0 or 1 and the pairing is live, the double team must dominate
target priority rather than being one candidate among many. The measured
behaviour is that it "stops being important super quickly, if ever" — the
blocker's re-selection does not appear to weight the active pairing at all.

*Acceptance:* the pairing survives at least ~60 frames (one second) rather than
the measured 13-30, unless R6b fires first.

### R6b — Peel on displacement, with a floor and a ceiling

Peel-off (`dt_role` 3, written near `0x001f672c`/`0x001f6730`) should be gated on
the doubled defender actually being **controlled** — displaced backwards past a
threshold, or neutralised — not on a clock running out.

*Acceptance:* the helper releases only after the defender is driven past a set
distance, subject to:
* a **floor** — never peel before ~30 frames, so a touch-and-go cannot count;
* a **ceiling** — peel by some maximum so a blocker is not welded on for the
  whole play when the second level is unblocked.

The floor and ceiling are the "x amount of time"; the displacement test is what
makes the peel *appropriate* rather than arbitrary.

### R6c — Sometimes you never peel: bury him

Against a defender the pair cannot displace — the archetypal case being a big
3-4 nose head-up on the centre, which is exactly what slot 9 presents — the
correct football answer is **not to peel at all**. Both men stay and drive him
into the ground. A rule that always releases at a ceiling gets this case wrong.

*Acceptance:* where the doubled man's rating or mass exceeds a threshold
relative to the pair, R6b's ceiling is suspended and the pairing holds to the
whistle. The doubled defender ends the play displaced by yards, not inches.

### Why this cannot be a single constant

Restating, because three attempts have now failed on it: the hold is
`reselect_timer` (+0x432) initialised to **`30 - blockRating/16`**, a per-player
computed value. R6a extends it, R6b replaces its *expiry* with a displacement
test, and R6c suspends the test entirely for a dominant defender. That is a
small state machine gated on `dt_role`, not a number to edit — which is why
patching a literal 30 moved nothing, and why the five immediate 20s would not
have either.

**Regression surface stays R5:** every blocker in the game shares this timer.
All three rules must be gated on `dt_role` being 0 or 1, or single-block line
play changes with them.

## Why this should generalise — and the two qualifiers

> "this seems like it should patch cleanly for all plays as its just the
> 'correct way' to do double teams in football"

Largely right, and it is a stronger position than the lead-blocker work. That
one needed the play file, because a pulling guard's route is authored per play.
This does not: **the engine already registers the pairing itself, with roles, on
every play where one exists.** The patch reads state the engine maintains and
changes a decision. Nothing play-specific has to be authored, so one change
covers every play that produces a double team.

Two qualifiers before "all plays" is taken literally:

**It does not reach pass plays yet.** DT-1 (`0x001F6560`) restricts registration
to run blocking, confirmed live on slot 8 — a pass down with zero registrations
on any of 22 players. The fix lands on run plays and silently does nothing on
pass protection until DT-1 is applied as well. A second patch, not a flaw.

**Zone and gap schemes may want different peel triggers.** In a gap/power
double the pair drives and the peel is on displacement, which is what R6b
specifies. In a zone combo the climb is usually triggered by the linebacker
*declaring*, not by how far the down lineman was moved. Same principle,
different trigger. The operator already flagged that schemes differ when he
required a lateral first step for zone in `lead-blocker-requirements.md` (R8).
R6b may need linebacker flow as a second peel condition — or displacement may
prove a good enough proxy for both. Untested either way.

**What actually decides whether it patches cleanly** is narrower than the
football logic: `reselect_timer` (+0x432) is shared by **every blocker in the
game**. Correct gating on `dt_role` makes the change invisible to single blocks
and universal to doubles; incorrect gating makes every lineman stickier. That is
the entire risk, and it is why R5's no-regression check runs on its own
savestate before anything is combined.

## R6z — Zone double teams (SPEC'D FOR LATER, unscoped from the current patch)

Explicitly a **future expansion**, kept out of the gap/power patch per rule 1 —
different trigger logic, and probably partly different code. It is written down
now so the current patch is built in a shape that can grow into it, rather than
one that has to be torn up.

### The football difference, stated precisely

| | gap/power double (R6a-c, current scope) | zone combo (this section) |
|---|---|---|
| purpose of the pairing | move THIS man off the spot | secure the down lineman WHILE reading a linebacker |
| peel trigger | displacement of the doubled man | the **linebacker declares** (commits to a gap / crosses the climb track) |
| who peels | the helper, by rule | whichever of the two the backer's flow takes away — it is decided live |
| if the backer never shows | drive to the whistle (R6c) | the combo simply never splits; both finish on the down man |
| first step | straight into contact | **lateral playside step first** (lead-blocker R8) |

Same principle — two men until the block is resolved — but the *resolving event*
differs: displacement there, linebacker declaration here.

### Requirements, numbered now so they do not drift

* **R6z1 — peel on declaration.** In a zone combo, the climb is triggered by the
  tracked second-level defender committing (crossing a gap threshold or the
  climb track), not by the down lineman's displacement and not by a clock.
* **R6z2 — either man climbs.** Which blocker releases is decided by the
  backer's flow at the moment of declaration: the man the backer flows AWAY
  from stays on the double. Not fixed at assignment time.
* **R6z3 — no declaration, no split.** If the backer sits or walls off, both
  blockers finish on the down lineman. R6c's "bury him" already covers this
  case; it must keep doing so under zone.
* **R6z4 — composes with lead-blocker R8.** The lateral first step precedes the
  combo. R8 is unscoped there for the same reason this is unscoped here; when
  either lands, the two must be tested together on a zone savestate.

### DESIGN CONSTRAINT ON THE CURRENT PATCH (this part is in scope NOW)

**The peel decision must be built as a single replaceable predicate** —
`should_peel(pairing) -> bool` in whatever form the cave takes — with the
displacement test as its first implementation, not as logic inlined into the
timer path. The zone expansion then swaps/extends one predicate (displacement
OR declaration, selected by scheme) instead of restructuring the patch. If the
gap/power patch hardwires displacement into the reselect flow, R6z starts over
from scratch; if it isolates the predicate, R6z is an addition. This constraint
costs nothing today and is the difference between the two futures.

### Gating unknowns, each blocking R6z and none blocking R6a-c

1. **Does the engine know a zone call from a gap call?** Same unknown that
   gates lead-blocker R8: the assignment-class byte may or may not encode
   scheme, and the play file is still unread (`play-data.md`). Without a scheme
   signal, R6z1 has nothing to key on.
2. **Is there a tracked second-level target?** R6z1 needs the combo to know
   WHICH linebacker it is reading. The dt record has four member slots
   (primary, helper, doubled defender, second-level per `block-cycle.md`) — if
   that fourth slot is real and populated, the hook exists; unverified.
3. **What does slot 9's play actually run?** The lead dive doubles may already
   be zone combos, or gap doubles — the play's scheme is not recorded anywhere.
   Baseline truth needs the operator to call an explicit inside zone and an
   explicit power from the same formation and compare dt registrations.
4. **A zone-run savestate does not exist yet.** Slot 9 is a lead dive. R6z
   acceptance needs its own state, recorded like the others in
   `experiments/states/`.

### Acceptance (when scoped)

On a zone-run savestate against a 3-4: combo forms playside (R8's lateral step
first), holds through the backer's read, splits ONLY on declaration with the
correct man climbing (R6z2), and never splits when the backer walls off (R6z3).
Regression surface: gap/power doubles from R6a-c must not change, verified on
slot 9 with the same spec that baselined them.

## The goal-line case — Power O, and why it validates the predicate design

> "how should we handle a power o play on the goalline? whats the football
> answer?"

**The football answer: on the goal line, the correct double team never peels.**

Power O from jumbo personnel (2-3 TEs) against a 6-man front: playside blocks
down, and the double is an ANGLE double — playside tackle + TE (or TE + wing)
on the 4i/5-technique, leverage by alignment. The fullback kicks the edge man
out, the crease is the body-width between the down-block and the kick-out, and
the puller wraps to meet the filling backer IN the hole, inside-out, first
wrong-coloured jersey. Nobody climbs; the backers are at two yards filling at
the snap and the play is over in ~1.5 seconds. The double's entire job is
vertical displacement and zero penetration — penetration at 1-yard depth kills
the play more surely than any unblocked defender.

(Refines the earlier "goal line = 0 spare blockers = useless" line: that was
correct for the HUNT, in base personnel, counting the interior five. Goal-line
doubles exist — they come from angles and extra TEs at the point of attack, not
from an uncovered interior lineman.)

### Two consequences for the design

**1. R6c must EMERGE at the goal line, not be special-cased.** A goal-line play
runs ~60-90 frames. Against R6b's ~30-frame floor plus the displacement
precondition, the peel conditions are never satisfied inside the play — so the
double naturally holds to the whistle. **This makes goal-line Power O the
discriminating acceptance test for the predicate design: if the patch needs a
goal-line special case to behave correctly, the predicate is wrong.**

**2. Declaration-triggered peel (R6z1) is hazardous here, recorded as a
constraint on R6z:** goal-line backers declare AT THE SNAP. A peel keyed on
declaration alone splits every goal-line double instantly — the exact defect
being fixed. Declaration may only ever fire as a trigger once the down lineman
is secured; it can never be the primary predicate.

### Measurement notes for a future goal-line savestate

* `carrier_yards`-style thresholds are meaningless at this scale; acceptance is
  crossing the plane and a penetration count of zero. Displacement is measured
  in feet.
* The plotters' field windows (Y 6..30) assume midfield; a goal-line state
  needs the window moved or the picture is empty.
* The puller's landmark logic (lead-blocker R1/R3) compresses naturally: "second
  level" collapses to "first wrong jersey inside-out in the hole". R3's
  prefer-second-level-else-nearest already produces that IF its range gate is
  right — worth checking on a goal-line state when one exists.
* No goal-line savestate exists. When one is recorded: jumbo personnel, Power O
  or equivalent, inside the 2, against a goal-line front — and the operator
  should expect the double to hold to the whistle as the PASS condition.

### Play-action Power O — identical at the snap, divergent after one beat

> "what about the play action version, should it be identically blocked?"

**At the snap, yes — by design.** The value of play action is that the backers
read the run picture: same down blocks, same angle-double look, and the guard
STILL PULLS, because linebackers key guards. The pull is the fake.

**After the first beat, no**, for one rule and one structure:

* **The drive is capped.** Ineligible man downfield: on a pass, a lineman
  cannot end up past ~1 yard. A double that buries the nose three yards deep
  has put two blockers three yards downfield — the goal on the run, a flag on
  the pass. The PA double fires out low, sells one beat, stalls, settles into
  protection.
* **Nobody climbs, ever** — no second level exists on a pass play, so the peel
  question vanishes rather than resolving differently.
* **The puller changes ends:** same first two steps (selling the key), then
  logs/seals the edge he pulled toward — a bonus protector, not a lead blocker.
  The FB runs his run track into traffic and blocks or leaks.

**Engine consequence — R6 is automatically PA-safe.** DT-1 (`0x001F6560`)
registers doubles on run blocking only; slot 8 confirmed a pass down registers
zero across 22 players. PA presumably classifies as a pass, so no doubles exist
on PA and a dt_role-gated patch has nothing to act on. The safe behaviour falls
out of the existing gate — no PA special case needed, and PA belongs on the R5
regression list only as a confirmation, not a risk.

**Rider on any future DT-1 lift:** if doubles are ever allowed to register in
pass protection, PA/pass doubles need the drive cap (~1 yard, then settle) or
the patch manufactures linemen driving defenders downfield on pass plays.
Whether this engine models ineligible-man-downfield at all is UNKNOWN — but the
visual is wrong regardless of whether a flag exists.

## DT-HOLD-90 RESULT (2026-08-11): zero effect, and the reason reframes R6 again

The five-times-reviewed one-word patch was applied (word verified in memory,
0x2403005A) and produced windows frame-identical to baseline: 2..36, 2..43,
27..43. T4 settled why: during the dt windows the doubled blockers' +0x432
reads 17 / 15 / 6 -- BASELINE-formula values, never the patched 76-89 (which
appear nowhere on them; their 100+ readings are post-window up-count bands).

**The registry doubles on this run play never use engagement kind 8.** The dt
registry (dt_role/dt_record) rides on ordinary kind-4 contact; kind 8 -- the
thing the patch extends -- was measured only in PASS protection (slot 7's
7<->8 flap). Two systems, and the patch extended the one slot 9 never touches.
Every static review lane was internally correct; the wrongly-shared premise
was that registry doubles are kind-8 engagements.

**The operator's screen observations are the new primary evidence:**

> "the right guard attempting the double block but he doesnt actually execute
> it once he is in position to touch him ... almost behaving like once he
> touches it he inherently thinks he should not be there anymore ... either
> that or theres no animation to do it and then it fails and then moves ahead"
> "[#]72 definitely lets the left guard take over -- i can see him pass over"

Touch -> abort -> pass the man off -> move ahead. The teardown at frames 36/43
is a DECISION at contact, made per-frame by the registry manage fn 0x001f6640
(docs/double-team-mechanism.md section 3 has its guard chain partially
traced), not a timer expiry anywhere.

**Next lever, stated precisely:** trace the manage fn's teardown/exchange
conditions -- what, at the moment of contact, tells the second man to
release/pass off. The +0x432 initialisers are the wrong tree for run doubles.
The kind-8 patch (still deployed, harmless per lanes 1/3) may yet matter for
PASS doubles, but that is a different requirement.

## DIAGNOSTIC RESULT (kind-4 init 30->90, 2026-08-11): timer family ELIMINATED, first positive yards

Pad path repaired (keepalive-then-restart enumeration procedure). 2/2 clean.

* T4 PROOF: RT held +0x432 = 77 during his dt window -- the patched formula's
  signature, impossible at baseline. The word executed through the recompiler.
* Windows STILL 2..36 / 2..43 / 27..43. A blocker carrying 77 ticks was torn
  down at the same frame as one carrying 17. **The registry teardown reads no
  timer this project has patched: kind-8, the +0x42C clock, and kind-4 are all
  eliminated.** The touch-abort is a non-timer decision in the manage fn
  0x001f6640. That trace is now the only live thread for R6.
* TE/RG read 15/6 in-window: stamped by writers never audited -- the mechanism
  doc lists 8 direct sh sites to +0x432 and only 2 have been examined.
* **carrier_yards -0.70 -> +0.49** -- first positive movement, causal (one
  word, deterministic engine). Longer ordinary contact holds alone turn the
  stuffed dive into a gain. The diagnostic stays a diagnostic (global R5
  violation), but it establishes that line-play hold time is a real lever on
  outcomes even without fixing the double team itself.

## P1 RESULT (2026-08-11): THE MARKET GUARD WORKS — first kind-8 on a run play

S0 22/22 words on a clean boot. Oracle, 3 iterations: TE's link STAYS on the
DE through frames 15-22+ (baseline flipped to the LB at exactly 17, 5/5);
one clean record 2..60 with roles held 59 consecutive frames (baseline max
7); KIND 8 on the RT from frame 23 -- 21 sightings, the first run-play attach
in the project's history. The engine's own machinery worked the moment the
market stopped re-shopping its participants.

Still true: carrier_yards -0.70 (outcome unmoved) and the window dies at 60 =
B0's 61-frame record cap, the next binding constraint exactly as the solution
staged it. Next levers: P4 (cap 61->361, predicted null alone, now
meaningful), then the on-skates drive (S1-S3) to make the held pair GO.
Deploy-cycle lessons banked: patch=0 cave bodies are wiped by load_state
(all-place-1 is the rule); the solution doc's cave failed reachability (live
memcpy selector at 0x00139DB0) and was relocated to cave #11.

## R7 — multiple simultaneous doubles (operator, post-P1)

> "also, the left guard needs to double team someone too"

On the slot 9 front the LG is uncovered; his textbook job is the C+LG double
on the NOSE -- a second record alongside TE+RT-on-DE. The registry has FOUR
record slots (T+4+20i), so capacity exists. Unknown: why the LG is never
elected -- market scoring (scorer fully mapped in dt-lanes/help-score.md:
88-dist base, 3.0-yd radius, x1.2/1.1/1.03/0.85 modifiers), play authoring,
or a limiter in the seek (0x001f64e0). Diagnose from the LG's series in the
P1 run data (/tmp/p1.jsonl on the rig) before touching the scorer. Levers if
it is scoring: the radius/threshold constants; if authoring: play-data.md
territory. Acceptance: two concurrent records on slot 9, LG+C on the NT,
neither stealing from the other (P1's theft guard already protects this).

### R7 DIAGNOSED (P1 data): a priority defect, not an availability defect

The LG is never idle -- he pairs with the LT on the LEFT END from f19 (kind
6, animation second slot) while the C singles the NT all play (kind 4,
f9-82+). His options at f1 were near-equidistant (LE 2.77 vs NT 2.80 yd);
the scorer's geometry modifiers picked the end, and once in kinds 6/4/5 he
is excluded from the helper market for the rest of the play. Two men on the
end the LT could handle alone; one man on the nose.

Fix direction: a PRIORITY term in the election -- e.g. weight the candidate
defender by mass/head-up alignment, or discount a defender already engaged
by an adequate single blocker. The scorer and its modifier table are fully
mapped (dt-lanes/help-score.md); this is a tuning target, not a hunt.
Acceptance: C+LG record on the NT while LT handles the LE single -- and the
TE+RT DE double unaffected.

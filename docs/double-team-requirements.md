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

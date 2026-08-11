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

# The block cycle, and the double-team system that exists

Investigated 2026-08-10 against `SLUS_207.52` (Madden NFL 2004), in
answer to open questions #14a and #14b.

> **CORRECTIONS from the 2026-08-12 double-team campaign** (details in
> `double-team-requirements.md`, `lessons-learned.md` Part 9, and the
> `dt-lanes/`, `anim-lanes/`, `drive-lanes/` directories). Three claims below
> are superseded: (1) **"the helper is a statue (speed zeroed)"** — the zeroing
> tracks `ai_state` 32, not the helper role, and is a *conditional grant* of
> 0.46 that lapses, not a zero write. (2) **The `dt_role` enum 0/1/2/3 is
> incomplete** — **5 = unassigned** (the empty-slot value the engine actually
> tests). (3) **"No positional coupling"** holds for the *engagement* system,
> but a convergence-warp (`0x00196FE0`) does move aligned pair participants,
> and the driven-back outcome is produced *natively* by winning the contest
> (N-1), not by any position write. The double team was fully fixed on
> 2026-08-12: it commits, holds, never releases, and drives the defender back
> proportional to combined weight+strength. This doc's *mechanism map* (the
> block cycle, DT-1..DT-4, the kind enum) remains sound; the three claims above
> are the ones the campaign overturned.

> "There is no such thing as double teams."

**That is wrong.** The engine has a designed, coordinated double-team
system with its own engagement kinds, its own per-frame pass, its own
scorer, a four-record registry, and a real peel-off-to-the-second-level
path. It is gated so tightly that players almost never see it work.

## The kind taxonomy was incomplete — 7, 8 and 9 exist

Engagement kind is a word at `player+0x3E0`. Three of the manager's ten
passes are about kinds our earlier docs never listed:

| kind | meaning |
|---|---|
| 0/1 | none / idle |
| 2/3 | assigned, approaching (3 = target is the ball carrier) |
| 4 | contact (distance < 2.1 yd) |
| 5/6 | inside a two-man scripted animation (5 = first frame, 6 = running) |
| **7** | **assigned as the second man on an already-engaged defender** |
| **8** | **attached second man — double team live** |
| **9** | reverse marker: "a blocker is coming for me" |

Also new: **`player+0x3F0` is a block-role enum** (1 = pass block, 2 = run
block, 3 = other). That is what selects PPBK vs PRBK, and what gates
double-team registration.

## How a double team forms

Multiplicity is not stored in the engagement record (88 bytes, four
handle words, **no second target, no list, no count**). It lives outside:

* two bytes on the player — `+0x436` (double-team record index) and
  `+0x437` (role: 0 primary, 1 helper, 2 doubled defender, 3 peel-off
  defender, 5 free);
* a **four-entry registry of 20-byte records** at `*(0x00601280) + 4 +
  20·k`: primary blocker, helper, doubled defender, **second-level
  defender**, active flag.

Two independent paths deliberately detect "my target is already engaged
→ join":

* **`0x001f4790` phase 4** — every frame, for each engaged offensive
  player, score every other eligible blocker and assign the best as kind
  7, but only if helping beats that blocker's own 1-on-1 assignment.
* **`0x001f7590`'s kind-7 arm** — promotes 7 → 8 only if the man being
  helped is genuinely in contact.

The helper scorer (`0x001f4c40`) is **textbook double-team geometry**:
base `88 − distance`, a hard 3.0-yard radius, and an angular window that
rejects a helper approaching within ~20° of the primary's engage line
(too stacked) or beyond ~135° (wrong side), with bonuses for the
defender being the helper's own man and for play direction.

**Peel-off to the second level is implemented** (`0x001f6d10` →
`0x001f64e0` / `0x001f6640` / `0x001f6940`): the registry latches a
second-level defender, and the release explicitly clears the helper's
engagement and re-targets him onto the linebacker. It even swaps who is
post man and who is drive man based on position.

## The mechanical effect: a debuff, not a sum

The shed contest is strictly two-player — no loop, no second blocker,
**no summation of blocker ratings**. Instead, the kind-8 helper
subtracts **−0.13** from the defender's engagement modifiers each time
his 16-frame lock lapses, and re-arms the primary's block countdown so
the primary's block never times out while help is attached.

## Why you never see it work

* Registration requires **run block** (`+0x3F0 == 2`) **and** within **60
  frames of the snap**.
* The kind-8 helper is **frozen** — the pass zeroes his staged speed and
  merely points his facing at the defender. When you do get a double
  team, the second man stands still touching the pile.

## #14a — the kind 5/6 pass does not exist (and that is the answer)

Closed-set negative across all ten manager passes: every one tests kind
4, kind 8, or kinds 2/3 — and `0x001ef820`'s kind jump table maps 5, 6
and 7 to a literal no-op.

**Kinds 5/6 are owned by AI state 32** (descriptor `0x00527538`, AIthink
`0x001e8088`). Players are pushed into it by the two-man animation
dispatcher — the same call that sets kinds 5/6. Per frame it advances the
shared animation, registers mutual no-collide, promotes 5 → 6 on the
animation's segment-end flag, and on exit stamps a fixed speed of 0.46.

**During kinds 5/6 nothing in the engagement system writes locomotion at
all: the animation's root motion owns both transforms.**

## "Action figures crashing together" — five code-level causes

1. **Contact is a binary distance test, not a collision.** Under 2.1
   yards flips the kind; the same frame both players' locomotion is
   overwritten from a single computed triple. There is no force, no
   impulse, no mass — the outcome is a dice roll expressed as an
   animation swap.
2. **Both bodies are driven from one frozen axis**, re-stamped every
   frame and recomputed only every 15–30 frames. Two rigid bodies on a
   shared line that changes in discrete quarter-second steps.
3. **Animations start at weight 1.0 with no blend** — every pose change
   is a hard cut, on both players simultaneously.
4. **Collision between the pair is switched off for the duration.** The
   bodies interpenetrate and are held together by the animation.
5. **The double-team helper is a statue** (speed zeroed).

## Fix candidates

Single-word, in place — this is tuning, not construction, because the
system already exists:

| # | what | site | change |
|---|---|---|---|
| **DT-1** | let double teams register on **pass** protection, not just run block | `0x001F6560` | `bne` → `nop`. **Highest value, lowest risk** |
| DT-2 | widen the 60-frame post-snap registration window | `0x001F651C` | 60 → 240 |
| DT-3 | enable helper assignment in play-type 2 | `0x001F4AE8` | branch → `nop` |
| DT-4 | strengthen the double-team debuff (data word, sole reader) | `0x005FF138` | −0.13 → −0.30 |
| DT-5 | apply the debuff more often | `0x001F21E8` | 16 → 8 frames |
| DT-6 | widen the helper engage radius | `0x001F4D58` | 3.0 → 4.0 |
| DT-7 | loosen the ~20° minimum split angle | `0x001F4E04` | smaller lo-half |
| DT-8 | more patience before a kind-7 helper gives up | `0x001F5C20` | 61 → 121 frames |

**Cave work** (`code-caves.md`): the biggest available "action figures"
fix is making the kind-8 helper actually drive instead of standing still
— replace the speed-zeroing store at `0x001F2164` with a jump to a cave
that stages a real shove off the primary's axis. Not a warp; it makes an
existing behaviour finish its job.

**Not tunable:** the participant cap. The interaction request has a
12-slot participant array, but the dispatcher is hand-unrolled for
exactly two, and all 16 call sites fill two slots. **A three-man pile-up
needs new code, not a table edit.** Growing the four-record registry is
also not a one-word change — the frames-since-snap counter this project
relies on sits immediately after it in memory.

## Corrections to earlier docs (all from this lane's evidence)

1. "The kind 5/6 processing pass was never located" → **there isn't
   one**; AI state 32 owns those kinds.
2. **"No already-engaged dedup" is wrong as a general claim.**
   `0x001f4790` phase 2 is an explicit same-target conflict resolver: when
   two blockers pick the same defender, the lower score is dropped and the
   pass re-runs until stable. The original statement holds only for the
   chooser path — so the previously proposed "add dedup" fix (D) must be
   re-scoped.
3. **"Awareness is never consulted anywhere in the block path" is
   wrong.** `0x001f0c40`, run at every lock-in, folds attribute 2 (AWR)
   into the leverage score and the pancake pool. AWR already affects
   block outcomes.
4. The kind taxonomy in three docs was incomplete (7/8/9 missing), and
   `player+0x3F0` is a block-role enum.
5. Minor: the kind-4 pass sets `player+0x1F4 = 5`, not 13.

## Hazard flags

Several conclusions rest on branch-likely delay slots — notably
`0x001ef8a4`, whose delay-slot load runs only for kinds 2/3/4, with
kinds 7/8 reloading later. In `0x001f4790` there is a delay-slot
argument trap: values stashed to the stack come from the *previous*
`jal`, not the one whose delay slot holds the store. Misreading it
inverts the entire double-team attribution. Four data jump tables were
hand-decoded and are patch surfaces in their own right.

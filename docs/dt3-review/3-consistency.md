# DT-3 fact-check, lane 3: does the causal story fit every measurement?

Recorded 2026-08-11. Static only — `extract/SLUS_207.52` via `recon.mipsdis`
(vaddr = file offset + 0xFF000, gp = 0x006056F0), plus the repo's dated result
sections. No rig, no network, no emulator, no commits. Every instruction quoted
below was read from the ELF this pass; nothing is carried over from another
document without re-derivation, and the three places where I am relying on
another document's *measurement* are labelled.

## VERDICT

**STORY-CONTRADICTED-BY-1.** Registry formation is not independent of the gated
block — it is strictly downstream of it, one manager slot later in the same
frame, via a kind-7 handoff. The story requires the block to be skipped on slot
9; the measured registrations require it to have run.

> The seek filter that leads to registration is
> `001f6568 lw v0, 992(a0); 001f656c addiu v0, v0, -7; 001f6570 sltiu v0, v0, 2`
> — **the candidate's engagement kind must be 7 or 8**. The only site in the
> image that can produce the *first* kind 7 is `001f4be4 jal 0x001f7398` with
> `001f4be8 addiu a2, zero, 7`, which sits **inside the block the type-2 gate
> skips**. Slot 9 formed three records; kind 8 was measured absent there; so the
> seek passed on kind 7; so `0x001f4be4` executed; so the branch at
> `0x001f4ae8` was **not taken** on slot 9.

Corollary: the deployed pnach is predicted **inert on slot 9**, and the same
argument makes it inert on slot 7. It is not harmful — see §5 — but a
frame-identical result must not be recorded as "DT-3 tested and refuted"; it is
the predicted outcome of a branch that was already falling through.

---

## 0. The pipeline, re-derived (call sites, not recollection)

Per-frame block manager `0x001f7298`, in order:

```
001f72d0  jal 0x001f5590    -> 001f55b8 jal 0x001f5510
                               001f55c8 jal 0x001f4790   <-- DT-3 gate lives here
                               001f55d0 jal 0x001f5158
001f72d8  jal 0x001f6d10    -> 001f6d18 jal 0x001f64e0   (seek/register)
                               001f6d20 jal 0x001f6640   (manage/peel/teardown)
                               001f6d2c j   0x001f6940   (drive/teardown)
```

Verified callers (full-image jal/j scan, both padded and unpadded entries):
`0x001f4790` ← `0x001f55c8` (sole); `0x001f6338` ← `0x001f657c` (sole);
`0x001f64e0` ← `0x001f6d18`; `0x001f65b8` ← `0x001f68f8` **and** `0x001f6b50`
(exactly two); `0x001f5158` ← `0x001f55d0`; `0x001f6940` ← `0x001f6d2c`.

**What the gated block actually is.** `0x001f4ae8 beq v0, v1(=2), 0x001f4c04`
jumps to the function epilogue (`0x001f4c04-0x001f4c38`, `jr ra` at
`0x001f4c34`), so value 2 is an *exclusion* — the tail of `0x001f4790` is
skipped. That tail, `0x001f4af0-0x001f4c00`, is one loop over the 11 offense
players (`0x001f4bf8 sltiu v0, s2, 11`):

```
001f4af0  addiu fp, zero, 3          ; comparison constants, not assignments
001f4af4  addiu s7, zero, 6
001f4af8  addiu s5, zero, 5
001f4b10  lw v0, 992(s1)             ; the candidate victim's kind
001f4b14/1c/24/2c/34                 ; keep kinds 4, 2, 3, 6, 5 -- else next man
001f4b40  jal 0x0013b798             ; resolve his +0x3E4 -> s3 (the defender)
001f4b6c  jal 0x001f4c40             ; helper scorer, over every other blocker
001f4be4  jal 0x001f7398             ; best helper ->
001f4be8  addiu a2, zero, 7          ;   KIND 7.  The only kind this block writes.
```

The patch comment's "whose first instructions load the two-man-animation
engagement kinds (3/6/5)" is a **misreading**: `0x001f4af0/4af4/4af8` load 3/6/5
into `fp`/`s7`/`s5` as the *victim-kind filter operands*. The block starts no
animation and writes no kind but 7.

---

## 1. Who formed the records with roles 0/1/2? — the contradiction

**Registration is upstream of nothing and downstream of the gate.** It is a
different function (`0x001f6338`) in a different manager slot (`0x001f72d8`),
so the skipped block does not itself do registration — that half of the story
survives. But entry to it is filtered on the gated block's *output*:

```
001f6550  lbu v0, 87(v1)     ; v1 = player+992 -> +0x437 dt_role must be 5
001f6560  bne v0, s2         ; +0x3F0 must be 2          (this is DT-1)
001f6568  lw v0, 992(a0)     ; engagement kind
001f656c  addiu v0, v0, -7
001f6570  sltiu v0, v0, 2    ; kind must be 7 or 8
001f657c  jal 0x001f6338     ; only then: register
001f6518  lw v0, 84(v1); 001f651c sltiu v0, v0, 60   ; and frame counter < 60
```

**Census of every producer of kind 7 or 8 in the image.** Method: all 12
non-stack stores to `+0x3E0` (`sw` offset 992, full-image), plus every `jal` to
the two kind-setters `0x001f7398`/`0x001f74c8` with its `a2` traced (66 sites),
plus the two computed writers (`0x001efa38 sw s2`, `0x001f5cf4 sw v0`) resolved
to their possible values.

| site | writes | precondition |
|---|---|---|
| **`0x001f4be4`** | **7** | **none — inside the gated block** |
| `0x001f6424` | 7 | seeker already kind 7 (`0x001f63fc lw v1, 0(s7)`; `0x001f6400 bnel v1, 7`) — inside register |
| `0x001f68b4` | 7 | an in-use record already exists (`0x001f6684 lbu v1, 16(s0)`) — inside manage |
| `0x001f5368` + partner | 7 | one of the pair already kind 7 (`0x001f5264 beq s1, 7`, `0x001f5288 beq s2, 7`) — a role *swap*, `0x001f5158` |
| `0x001efa38` via `0x001ef9b4` | 7 | jump table `0x00583340` arm for **current kind 8** only |
| `0x001efa38` via `0x001ef93c` | 8 | band test `0x001ef8ac/b4`: current kind already 7 or 8 |

Five of the six require a kind 7 (or 8) to already exist. **`0x001f4be4` is the
sole bootstrap**, and it is the site the gate skips.

**Against the record.** Slot 9 measured three registry records — roles 0/1/2,
windows 2..36 / 2..43 / 27..43 (`double-team-requirements.md`, CORRECTION and
DT-HOLD-90 sections) — and the DT-HOLD-90 result states kind 8 never appeared on
that run. Records require the seek to pass; the seek requires kind 7 or 8; kind 8
was absent; therefore **kind 7 was present on slot 9**, therefore the gated block
ran, therefore `0x001f4ae8`'s branch was not taken, therefore
`0x0015ada0() != 2` on slot 9.

**The one way to save the story, and how to close it.** A kind 7 surviving from
before the snap. The per-play reset `0x001f6ff0` clears `dt_record`/`dt_role` for
all 11 players unconditionally (`0x001f7088 sb zero, 1078(s0)`,
`0x001f7098 sb s4, 1079(s0)` with `0x001f7014 addiu s4, zero, 5`), but its
kind→1 writes (`0x001f7144`, `0x001f7170`) are gated on the player's AI-state
byte (`0x001f712c bne v0, s5`), so carryover is **not excluded statically**.
Cost to close: one pre-snap sample of `+0x3E0` across 22 players on slot 9.
Marked **unverified** until then; it is the only soft joint in §1.

---

## 2. Do 36 and 43 fall out of the story? No — and here is what does produce them

The `dt_role` windows end when a record is torn down. Teardown is `0x001f65b8`:
it clears the in-use byte (`0x001f65dc sb zero, 16(s2)`) and writes role **5** to
each of the four members (`0x001f6600 sb s3, 1079(v0)`, `s3 = 5`). It has **no
guards of its own** — the whole decision lives in its two callers.

**Caller A — `0x001f68f8`, in manage `0x001f6640`.** Unreachable unless the peel
slot rec+12 is already populated: `0x001f6744 beql a0, zero, 0x001f6904` sends an
empty-peel record straight to the next iteration. So a peel must be *detected*
first (a member's `+0x3E4` stopped pointing at the doubled defender —
`0x001f6728 sw v1, 12(s0)`). Then teardown fires if:

* an invariant breaks — primary, helper, defender or peel-man `+0x3E4` no longer
  points inside the record (`0x001f674c-0x001f67a8`, exits to
  `0x001f68c4`/`0x001f68c8`, both of which reach `0x001f68f8`);
* the **primary's kind** leaves 2..8 (`0x001f67b4 addiu v0, v0, -2`;
  `0x001f67b8 sltiu v1, v0, 7`; jump table `0x00583A80`, whose kind-5 and kind-6
  arms go to `0x001f67d8` and every other arm to `0x001f67f4`; the range failure
  goes to `0x001f67ec beq zero, zero, 0x001f68f8`);
* the **helper's kind** is < 2, ≥ 9, or **5 or 6** (`0x001f67f8-0x001f6820`:
  `sltiu a0, 2` → teardown, `sltiu a0, 9` fail → teardown, `sltiu a0, 7` →
  teardown).

**Caller B — `0x001f6b50`, in drive `0x001f6940`.** Gated on a break flag `s6`
(`0x001f6b48 beq s6, zero, 0x001f6cc8`; `s6 != 0` falls into the teardown), set
by geometry and defender state: two pair-separation tests against `0x002E38E2`
(`0x001f6ad4`, `0x001f6aec`), two float tests (`0x001f6a90`, `0x001f6ab8`), and
the defender's AI state being 2 or **30** (`0x001f6b00 lw v0, 764(s3)`;
`0x001f6b04 lbu a0, 0(v0)`; `0x001f6b08 beq a0, 2`; `0x001f6b0c addiu v0, zero,
30`) — confirming `review-2026-08-11.md`'s reading that R6 attempt 1 patched a
**state id**, not a duration.

Neither caller reads any timer, which is exactly what the timer graveyard
measured. **Neither is downstream of `0x001f4ae8`.** And the no-assignment story
predicts *zero* records on slot 9, so it cannot produce 36, 43, or any end frame
at all. On this item the story is not merely incomplete — it has no window to
explain. The predicates above are the live thread (mission brief item 2).

---

## 3. Ten players in state 32 — reconciled, but it costs the patch its rationale

**Not a contradiction.** State 32 owns kinds 5/6, and every kind-5/6 writer in
the image is outside `0x001f4790`: `0x001a66ac/bc`, `0x001b2810/20`,
`0x001ef300/10`, `0x001ef7e0/f0`, `0x001f007c/8c`, `0x001f0600/10`,
`0x001f0624/34`, `0x001f0b3c/4c`, `0x001f1944/54`, `0x001f7d88/98`, plus state
32's own 5→6 promotion at `0x001e81ec`. The gated block writes only kind 7. So
ten men in state 32 is fully compatible with the block being skipped, and item 3
is **not** the fatal one — item 1 is.

Two things it does kill:

1. **The patch's stated mechanism.** "The block's first instructions load the
   two-man-animation engagement kinds (3/6/5), so on a type-2 play the second man
   has nothing to execute" is false twice over: those constants are comparison
   operands, and the two-man animation is reached by a dispatcher this block
   never calls. Skipping `0x001f4790`'s tail cannot suppress a two-man animation.
2. **The direction of the inference.** Kinds 5/6 are a **teardown trigger**, not
   a reward: a helper at kind 5 or 6 routes manage straight to `0x001f68f8`
   (`0x001f6814-0x001f6820`). Ten players in state 32 on a play whose records die
   at 36/43 is a fact the *fix* must explain; the DT-3 story does not touch it.

(The ten-player figure is recorded in `docs/double-team-mission-brief.md:31` as
measured ground truth. I could not find it in `double-team-requirements.md` or
any result artifact in the repo — `double_team.py`'s `m_two_man_state_players`
exists, its output does not. Provenance **unverified**; the reconciliation above
holds for any count.)

---

## 4. The pass side — consistent, and it supports nothing

Under the story pass helper assignment runs (`type != 2`). Slot 7's 130+ frame
kind 7↔8 flap requires a kind 7, so `0x001f4be4` ran on a pass down too:
internally consistent. The registry's absence there is DT-1 — `0x001f6560
bne v0, s2` on `+0x3F0 == 2`, confirmed at the seek this pass, address as
documented. Slot 8's `dt_role` 5 on all 22 needs no double-team explanation at
all: 5 is the per-play reset value written by `0x001f7098`.

But this is *support for DT-1, not for DT-3*. Kind 7 appears on a pass play
(slot 7) and is required on a run play (slot 9) — i.e. on both savestates this
project has measured, the branch DT-3 removes was already falling through. The
pass evidence therefore gives the type-2 exclusion no observed instance.

**The play-type claim itself is unsourced.** The pnach cites "a property of the
CALLED PLAY -- addresses.yaml"; `addresses.yaml` has **no entry for
`0x0015ada0`** (zero grep hits repo-wide outside the pnach, the mission brief and
`review-2026-08-11.md`, which already flagged the ambiguity as D12). Statically,
`0x0015ada0` takes no argument and returns: when `0x00154790()` is 3, the global
word `*(0x00600c70)+152`; otherwise `0x0015aeb8()` of the first offense player it
classifies non-zero — a value in {0,1,2,3,4,5} derived from byte codes 0x36/0x37
at `player+0` and 0x2D at `player+4` (`0x0015af00-0x0015af50`). Value 2 requires
`(p[0] & 0x7F) == 0x36`. **What 2 means is unverified**; "play type 2 == run" is
established nowhere, and the manager itself only runs when `0x00154790()` ∈ {3,4}.

---

## 5. Pre-registered oracle for one slot-9 run under the DT-3 nop

Given §1, the honest prediction is a **null**. Registered before the run:

**If the story is right** (gate fires on slot 9, helper assignment currently
skipped), a nop must make appear what has never appeared: at least one blocker
reaching **kind 7** where none did, a *fourth* record or an earlier first
registration, and windows extending past 43. Because kind 7 is the seek's
precondition, the story also has to explain how the three existing records formed
without it — so the strongest form of the story predicts **more** registrations
than baseline, never the same three.

**Predicted (story false, branch already falling through):**
windows frame-identical at 2..36 / 2..43 / 27..43; `dt_registered`, `primaries`,
`helpers`, `doubled_defenders` unchanged; `carrier_yards` −0.70; no kind 8 on
slot 9; `two_man_state_players` unchanged.

**Refutes the null (supports the story):** any window ending past 43, any change
in record count or first-registration frame, or kind 8 appearing on slot 9.

**Two controls that settle it cheaper than a patched run, and should be read
first:**

1. Sample `+0x3E0` (kind) for all 22 players on the **baseline**. Kind 7 present
   anywhere before frame 43 ⇒ the gate is not firing ⇒ the patch is inert and
   DT-3 is closed. Kind 7 absent everywhere ⇒ the seek could not have passed and
   the `0x001f6568-70` reading is wrong — a different, larger fault, and a real
   finding either way. This same sample closes §1's carryover hole if taken
   pre-snap as well.
2. Read `0x0015ada0`'s return live (argument-free; call site `0x001f4adc`). If it
   is not 2 on slot 9, DT-3 is closed with one read.

**Safety note, so the null is not mistaken for a broken patch.** The nop is
benign: `0x001f4c04` is the epilogue, so removing the branch only removes an
early return, and the delay slot `0x001f4aec daddu s2, zero, zero` is the loop
counter initialiser, which executes identically on both paths. The risk is not
damage; it is booking an inert word as a tested hypothesis.

---

## Confidence

| item | verdict | confidence |
|---|---|---|
| 1 — registration downstream of the gate via kind 7 | **contradicts the story** | high — every guard and every kind-7/8 producer quoted from the ELF this pass; one labelled hole (pre-snap kind carryover) |
| 2 — 36/43 | story predicts no window at all; two teardown callers traced | high on the mechanism, unverified on *which* predicate fires at 36 vs 43 (needs live sampling of `+0x3E4` and the members' kinds) |
| 3 — ten in state 32 | reconciled; kills the patch's stated rationale, not the skip | high on the code, **unverified** on the ten-player datum's provenance |
| 4 — pass side | consistent with DT-1, gives DT-3 no support | high; `0x0015ada0`'s enum **unverified** |
| 5 — oracle | null predicted | n/a |

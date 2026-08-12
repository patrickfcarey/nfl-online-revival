# DT lane: why the pass-side 7↔8 pairing outlives the run-side one by 4x

Recorded 2026-08-11. Static only, against `extract/SLUS_207.52`
(vaddr = file offset + 0xFF000, gp = 0x006056F0), via `recon.mipsdis`. No rig,
no network, no emulator, no commits. Every instruction quoted below was read
from the ELF this pass; measured facts are cited to their result documents and
were **not** re-derived. Where a claim rests on inference it is marked
**UNVERIFIED** in place.

**The measured contrast this lane explains** (cited, not re-derived):

* Slot 7 (pass): the RG oscillated engagement kinds 7↔8 for **130+ consecutive
  frames**; reps up to 329 frames (`docs/dt-hold-90-review/4-pass-blast-radius.md`
  §4-§5). No registry record ever forms there (DT-1).
* Slot 9 (run): registry records form (roles 0/1/2, windows 2..36 / 2..43 /
  27..43), die at contact-time, and **kind 8 never appears at all**
  (`docs/double-team-mission-brief.md`, `docs/double-team-requirements.md`).

## VERDICT

```
KILLER-IS-SECOND-ORDER-GATE: the helper's 7->8 latch (Site A via 0x001f7590)
  requires THE PRIMARY, not the helper, to be in locked contact -- the doubled
  defender's own engagement partner must hold kind 4, or the primary+defender
  pair must be playing the same recognized engagement animation (yes-set of
  table 0x00583360). A run double spends its whole registry life (2..36/43) in
  the approach phase where the primary holds kind 2/3, so the latch is gate-
  locked shut; at contact the primary's kind 4 is immediately consumed by the
  contact-time cascade (the state-32 two-man capture 0x001f7c98 stamps kinds
  5/6 with animation 158, which is in the NO-set of 0x00583360, shutting the
  gate PERMANENTLY), and the registry dies at 36/43. A 61-tick kind-7 fuse in
  the tick (0x001f5c10-0x001f5c38: +0x432 up-counts, at 61 -> kind 1) bounds
  anything that survives anyway.

PROTECTOR-IS-THE-SAME-DEPENDENCY-SATISFIED, TWICE OVER: on pass the primary
  holds kind 4 continuously (pass sets are sustained contact), so the gate
  keeps re-opening; and because the registry never forms on pass (seek requires
  +0x3F0 == 2, verified below), the manage/drive teardown machinery has no
  object -- the pairing lives entirely inside Site A's kind machinery, which
  contains no teardown at all, only the 7<->8 exchange, and every flip restamps
  the very timers (+0x432) whose expiry could end it.
```

Confidence: every quoted instruction, high. That the primary-contact
precondition is the binding predicate on slot 9 (rather than one of the
secondary geometry gates in `0x001f7698`): **medium — one live kinds-series
read decides it** (§6). The registry-vs-kind architecture claim (registry is
bookkeeping + teardown authority over its own records only; the kind machinery
is what sustains pairs): high.

---

## 1. What the gate `0x001f7590` actually requires — the whole contrast in one function

Sole switch of both flip directions (7→8 when it returns non-zero, 8→7 when it
returns 0 — `4-pass-blast-radius.md` §4, call site `0x001ef880`). Full anatomy,
read this pass:

```
001f7598  0080882d  daddu s1, a0, zero          ; s1 = the caller (helper or primary)
001f75a4  262403e4  addiu a0, s1, 996
001f75ac  0c04ede6  jal 0x0013b798              ; s0 = resolve own +0x3E4 = the partner
001f75b0  0000902d  daddu s2, zero, zero        ; s2 := 0 (default return; daddu-zero idiom)
001f75b4  8e2303e0  lw v1, 992(s1)              ; own kind
001f75bc  2c620002  sltiu v0, v1, 2
001f75c0  1440002e  bne v0, zero, 0x001f767c    ; kind < 2  -> return 0
001f75c8  2c620005  sltiu v0, v1, 5
001f75cc  14400012  bne v0, zero, 0x001f7618    ; kind 2..4 -> straight to the distance test
001f75d0  26240190  addiu a0, s1, 400           ;   (delay, always: a0 = own +0x190)
001f75d4  2c620009  sltiu v0, v1, 9
001f75d8  10400027  beq v0, zero, 0x001f7678    ; kind >= 9 -> return 0
001f75dc  2c620007  sltiu v0, v1, 7
001f75e0  14400026  bne v0, zero, 0x001f767c    ; kind 5..6 -> return 0
```

**Kinds 7/8 — and only kinds 7/8 — get an extra precondition before any
geometry is looked at:**

```
001f75e8  0c04ede6  jal 0x0013b798
001f75ec  260403e4  addiu a0, s0, 996           ; resolve the DEFENDER's +0x3E4
001f75f0  0040202d  daddu a0, v0, zero          ;   = the defender's own engagement = THE PRIMARY
001f75f4  10800020  beq a0, zero, 0x001f7678    ; defender engaged to nobody -> return 0
001f75f8  24020004  addiu v0, zero, 4
001f75fc  8c8303e0  lw v1, 992(a0)              ; the PRIMARY's kind
001f7600  50620005  beql v1, v0, 0x001f7618     ; primary kind == 4 -> proceed  (likely:
001f7604  26240190  addiu a0, s1, 400           ;   delay executes only when taken)
001f7608  0c07c2ea  jal 0x001f0ba8              ; else: mutual-same-animation test (a0 =
001f760c  0200282d  daddu a1, s0, zero          ;   primary, a1 = defender)
001f7610  10400019  beq v0, zero, 0x001f7678    ; fails -> return 0
```

`0x001f0ba8` (read this pass): requires **mutual** engagement (`0x001f0bc4` /
`0x001f0bd4`: a0→+0x3E4 == a1 and a1→+0x3E4 == a0), both playing the **same
animation** (`0x001f0bf0 bnel v0, s0` on the two `0x003ad410(+0x304)` returns),
the shared id in **146..173** (`0x001f0bf8 addiu v1, v0, -146; 0x001f0bfc sltiu
v0, v1, 28`), dispatched through table `0x00583360`, whose yes-arm
(`0x001f0c20 addiu s1, zero, 1`) is reached only for ids
**{146-151, 168-170, 173}**; ids **{152-167, 171, 172} return 0**.

Then the geometry, shared by all kinds:

```
001f7618  0c12b7ca  jal 0x004adf28              ; distance(own +0x190, partner +0x190)
001f761c  26050190  addiu a1, s0, 400
001f7620  c7819ad0  lwc1 f1, -25904(gp)         ; 0x005ff1c0 = 2.1
001f7624  46010034  c.lt.s f0, f1
001f762c  4501000f  bc1t 0x001f766c             ; within 2.1 -> 0x001f7698(caller, partner)
001f7630  0220202d  daddu a0, s1, zero
001f7634  0c056b68  jal 0x0015ada0              ; NOT within 2.1: play classification
001f763c  24030003  addiu v1, zero, 3
001f7640  1443000e  bne v0, v1, 0x001f767c      ; class != 3 -> return 0
001f7648  0c07fe6e  jal 0x001ff9b8              ; class == 3 (the value the jam-eligibility
001f7654  0c081068  jal 0x002041a0              ;   helper requires; addresses.yaml reads
001f7658  0000282d  daddu a1, zero, zero        ;   it as "pass") ...
001f765c  24030006  addiu v1, zero, 6
001f7660  14430006  bne v0, v1, 0x001f767c      ; ... and 0x002041a0(0x001ff9b8(),0) == 6
001f766c  0c07dda6  jal 0x001f7698              ; -> the final decider anyway
001f7670  0200282d  daddu a1, s0, zero
001f7674  0040902d  daddu s2, v0, zero          ; s2 = its result
```

**Finding 1 (new): the 2.1-yd test has a pass-only rescue arm.** Beyond 2.1
yards the gate can still pass — but only when `0x0015ada0() == 3` (and a second
global test == 6). On any play class other than 3, separation beyond 2.1 is an
unconditional 0. A run pair that drifts apart is dead to the gate; a class-3
pair is not. (What class slot 9's play actually returns is the open item from
`dt3-review/1-gate-arms.md` §5.1 — value 2 vs others — so this arm's
contribution on slot 9 is **UNVERIFIED**, but it can only ever *widen* the pass
side's survival, never the run side's.)

**Finding 2 (the load-bearing one): for a kind-7 helper the gate is
second-order.** Before any distance or angle is evaluated, the *primary* must
be in locked contact (kind 4) or the primary+defender pair must be playing the
same yes-set animation. The helper's own state is irrelevant to this test. The
pass flap measured on slot 7 therefore *proves* the C (or whichever primary)
held kind 4 / a yes-set animation near-continuously for 130+ frames. On slot 9,
kind 8 never appearing means this precondition (or a later predicate, §2) was
never satisfied during the helper's kind-7 tenure.

## 2. The final decider `0x001f7698`, and whether it can fire at run distances

Called with (caller, partner) once the §1 preconditions pass. Read this pass;
the angle unit is BAM24 (0x01000000 = 360°, confirmed by the normalization
`0x001f7824 addu v0, s1, 0x00800000; and 0x00FFFFFF` = +180° mod 360°). Exits
in order:

```
001f76d4  jal 0x003ad410 (defender +0x304)      ; defender's current animation id
001f76e4  beq v1, 48 -> return 0                ; anim 48, 42 (if id < 49) or
001f76f8  beq v1, {42|99} -> return 0           ;   99 (if id >= 49) -> 0
001f7708  lw v0, 764(s2); lbu v1, 0(v0)         ; defender's AI state
001f772c  jump table 0x00583AC0 [state-10]      ; 83 entries, states 10..92
```

Table `0x00583AC0` (read as data): states **{10, 11, 12, 17, 49, 50, 58, 62,
73, 92} → return 0**; every other in-range state → continue. States < 10 (e.g.
**2, ball pursuit**) fall outside the table and continue. **State 30 (pass rush
/ engaged) continues.** So the defender-state screen does *not* separate run
from pass — 49/50/62/73/92 are "never a threat" states, and both sides' live
defenders pass it.

```
001f7738  blocker state 25/26 -> return 0       ; (authored blocking classes; 31/33 pass)
001f7754  defender +0xC & 0x0800 -> return 0    ; flag, unidentified
001f7764  jal 0x00200130(blocker) != 0 -> 0     ; unidentified test  (UNVERIFIED meaning)
001f7794  blocker +0xC & 0x4000 set: facing-vs-bearing-to-defender must be <= 135°
                                                ;   (0x00600000 BAM24) else return 0
001f77cc  s6 := (defender kind == 4 && own kind == 4)
001f77f4  angle tolerance s0 := s6 ? 65° (0x002E38E3) : 40° (0x001C71C7)
001f7814  blocker heading (+0x1A8 + 0x00188b60 adjust) vs bearing-to-defender:
          diff >= tolerance -> return 0         ; a kind-7 helper gets the 40° cone
001f7830  if defender +0x1F4 != 0: defender facing vs reverse bearing > 130°
          (0x005C71C6) -> return 0              ; defender must roughly face the blocker
001f785c  jal 0x004adf28; c.lt.s f0, [0x005ff1c4]=2.1; not within -> return 0
001f78b4/d8  jal 0x0013bad0(x, 11, out, ...)    ; body point 11 of each
001f78e0  bearing diff > 90° ? radius f20 = |cos(diff)|*0.4+0.85 : f20 = 0.85
001f7928  if 0x001f5db0(pair) != 0: f20 *= 1.2  ; (floats 0x005ff1c8..0x005ff1d4)
001f793c  distance(point11, point11) >= f20 -> return 0   ; CONTACT-RANGE requirement
001f795c  jal 0x001efb20(blocker, defender, angle) != 0 -> return 1
001f796c  jal 0x001f5db0(pair) != 0 -> return 1
001f7980  return (bearing diff > 85° (0x003C71C7))
```

`0x001efb20` (read this pass): blocker's position class `+0xB04` must be ≥ 4
and < 10 (or == 24), and his position within 3.0 / 4.0 of the reference point
from `0x00260208` — i.e. **an offensive lineman near the ball spot passes the
final OR outright**. `0x001f5db0`: same-animation pair in the 49..108 family →
1, or 146..173 per table `0x00583920` (not read; **UNVERIFIED** which ids).

**Answer to "does the flip fire at run distances":** the run double's
*distances* are fine — 0.5-1.5 yd is inside both 2.1 screens, and a helper
driving at the defender is inside the 40° cone. Nothing in the geometry is
run-hostile. What is run-hostile is **time**: every predicate has to hold on
the same frame as the §1 primary-contact precondition, and on a run that
precondition's satisfiable window is the contact instant itself — which is
exactly when the cascade of §4 destroys it. The geometry gates are the
*secondary* suspects if the live check (§6) shows primary kind 4 with helper
kind 7 coexisting for many frames and still no 8.

## 3. What each state driver does to the pairing — the thinks are innocent

State table rows (`docs/state-dispatch-table.md`): 31 pass pro, think
`0x001cb008`; 33 run block, think `0x001dc2d8`. Neither think calls Site A
(`0x001ef820`, sole caller `0x001f72e0` in the per-frame manager) or the helper
block (`0x001f4790`, sole caller `0x001f55c8`) — re-confirmed by full-image
jal scan this pass. What they *do* touch:

**State 33's think writes an engagement kind exactly once, gated on kind == 0:**

```
001dc4cc  8e620000  lw v0, 0(s3)                ; s3 = player+0x3E0
001dc4d0  5440000b  bnel v0, zero, 0x001dc500   ; kind != 0 -> skip everything below
001dc4e0  0220202d  daddu a0, s1, zero
001dc4e8  0c07dce6  jal 0x001f7398
001dc4ec  24060001  addiu a2, zero, 1           ; kind := 1 only from kind 0
```

On kind 4 it skips re-targeting entirely (`0x001dc594 beql v1, v0(4),
0x001dc638`); for every other kind (7 included) it resolves +0x3E4 and steers
toward that man (`0x001dc610 jal 0x001daf00` etc.) — **it steers the helper at
the doubled defender; it never re-decides him onto his own man**. The
task-3 hypothesis ("state 33's think re-decides the helper into kind 4 on his
own man every frame") is **REFUTED**. Its angle-breaker helper `0x001dbd90`
(sole caller `0x001dc628`, this pass) tears down pairs whose bearing diverges
past 35° (`0x001dbf7c-84`, 0x0018E38E BAM24) — **but only for kinds {2, 4, 9}
(`0x001dbf90-0x001dbfb8`); kinds 7/8 are exempt and exit untouched.**

**State 31's think is the same shape:** kind := 1 only from kind 0
(`0x001cb170-0x001cb198`), and its assignment-drop block is behind
`kind == 3` (`0x001ca084 bne v1, v0(3)` — `4-pass-blast-radius.md` §3, address
re-checked) so kinds 7/8 skip it. The 10%-per-frame shed on `0x00260988() != 0`
exists identically in both thinks (`0x001cb134-150` / `0x001dc48c-4b0`), gated
on kind 4, not 7/8.

**The enters do destroy foreign kinds — but only on state entry.** State 33's
enter keeps kinds {2,3,4} when `+0x3F0 == 2` and resets everything else
(7 included) to kind 1 (`0x001dc274-0x001dc2b8`); state 31's enter keeps only
kind 3 (`0x001cafc4-0x001cafe4`). Mid-play these fire only on state
*conversions* (33↔31 on play-character change, `0x001dc3f8-0x001dc454`; or
return from state 32), not per frame.

**The one shared call both thinks make is the capture service `0x001f7c98`**
(callers, this pass: `0x001b68ec` state 47, `0x001cb0c0` state 31, `0x001dc45c`
state 33, `0x001e81f4` state 32 — all four blocking states, identical
`bne v0, zero -> return` on its result). The asymmetry is *inside* it — §4.

## 4. The contact-time cascade: what consumes kind 4 on runs

`0x001f7c98`, read this pass — the only path in the image that starts the
scripted two-man block animation (state 32's kinds 5/6):

```
001f7cbc  jal 0x00154790
001f7cc4  10520006  beq v0, s2(=4), 0x001f7ce0  ; global mode must be 4 ...
001f7ccc  jal 0x00154790
001f7cd8  1443...   bne v0, 7 -> return 0       ; ... or 7
001f7ce0  jal 0x00260598; bne -> return 0       ; must be on the possessing side
001f7cf8  bne byte[player+0], 1 -> return 0     ; ("handle kind 1", requirements doc's
                                                ;   reading; UNVERIFIED here)
001f7d0c  beq 0x003ad410(+0x304), 158 -> ret 0  ; not already in animation 158
001f7d14  kind must be 4 (or 6)                 ; 0x001f7d18/0x001f7d20
001f7d28  s0 = resolve(+0x3E4)                  ; the defender
001f7d38  jal 0x001f1be0(player, defender)      ; eligibility -> 0 = blocked
001f7d70  jal 0x0018e648(request: anim id 158,  ; paired-animation start, 20° tolerance
          20° = 0x000E38E3, float 0x005ff1d8)   ;   (0x001f7d54-0x001f7d74)
001f7d88  jal 0x001f7398, a2=5                  ; success: kind 5 ...
001f7d98  jal 0x001f74c8, a2=5                  ; ... on BOTH (state 32 promotes 5->6 at
                                                ;   0x001e81ec)
```

`0x001f1be0` is a pure function of the **defender's AI state** via table
`0x005833D0` (88 entries, states 5..92, read as data): blocked for states
**{5, 10, 12, 15, 16, 17, 28, 49, 50, 57, 58, 62, 73, 92}**, allowed for
everything else — **state 30 allowed, state 2 out of range → allowed**. So
eligibility does not separate run from pass either.

**Why this is the killer's second half:** animation **158 is in the NO-set of
`0x00583360`** (§1: yes = {146-151, 168-170, 173}). The instant a primary in
kind-4 contact is captured, (a) his kind stops being 4, and (b) the pair's
shared animation is one the mutual-animation fallback rejects. **Both halves of
the helper's precondition are destroyed by the same event, permanently.** The
ten players measured in state 32 on slot 9 (`double-team-mission-brief.md:31`;
provenance flagged unverified by `dt3-review/3-consistency.md` §3) are this
capture firing five times at contact — the same window in which the registry
records die (36/43) and the only window in which the helper's latch could ever
have opened.

The registry tolerates a captured *primary* (manage's kind jump table routes
primary kinds 5/6 to `0x001f67d8`, not teardown) but tears down on a captured
*helper* (helper kind 5/6 → `0x001f68f8` — `dt3-review/3-consistency.md` §2).
Which record member got captured/re-pointed first on slot 9 is not knowable
statically.

**The mode gate is the pass side's likeliest shield — UNVERIFIED.** The
per-frame block manager runs at `0x00154790() ∈ {3,4}` (`0x001f72a0-0x001f72b4`);
the capture additionally requires **{4, 7}**. So there exists a mode (3) in
which all blocking runs but capture cannot. `0x00154790` (read this pass)
returns `[[0x00600ce4]]`, aliasing raw 14 → 3, null object → 7, and caches the
raw value at **`0x00600ce8`** (PINE-readable). The mode word has exactly one
`:= 3` writer (`0x0025316c`, followed immediately by block-system init
`0x001f8520` — snap-adjacent) and one `:= 4` writer (`0x00142284`, in phase-
handler `0x00141b60`, table `0x0057c8e8`). Context suggests 3 = live play with
the ball possessed and 4 = a later phase — if 4 arrives at the handoff on runs
but not while the QB holds on passes, the capture is open exactly on runs.
**The labels are inference; the cached word decides it live** (§6). What is
*not* inference: slot 7's 130-frame flap proves capture did not fire there
(a captured C would have shut the RG's gate), and slot 9's ten-in-state-32
(if the datum holds) proves it did fire there.

## 5. What sustains the pass pairing mechanically

1. **No registry record can form on pass.** Seek filter, re-derived this pass:

   ```
   001f6550  90620057  lbu v0, 87(v1)      ; +0x437 dt_role == 5
   001f655c  8c620010  lw v0, 16(v1)       ; +0x3F0 block mode
   001f6560  14520009  bne v0, s2(=2)      ; must be 2 = RUN BLOCK -> pass never registers
   001f6568  8c8203e0  lw v0, 992(a0)
   001f656c  2442fff9  addiu v0, v0, -7
   001f6570  2c420002  sltiu v0, v0, 2     ; kind 7 or 8
   001f657c  0c07d8ce  jal 0x001f6338      ; register
   ```

   No record → manage `0x001f6640` and drive `0x001f6940` iterate nothing →
   **the teardown fns `0x001f68f8`/`0x001f6b50` are unreachable for the pass
   pair.** The registry is not what sustains pairs anywhere — it is bookkeeping
   plus a teardown authority over its own records; on pass it simply has none.

2. **Kind 7 is a stable resting state under Site A.** Gate 0 → jump table
   `0x00583340[kind-2]` sends kind 7 to the keep arm `0x001efa1c`
   (`s2 := current kind`), and `0x001efa2c beq a1, s2, 0x001efaa4` skips the
   store entirely when the kind is unchanged — nothing is written at all.
   Kind 8 + gate 0 → `0x001ef9b0/0x001ef9b4: s2 := 7` with `s4` still zero from
   `0x001ef88c` (daddu-zero), so the demotion stores timer := 0. Kind 7 +
   gate ≠ 0 → `0x001ef93c: s2 := 8`, timer := init. Each flip in either
   direction restarts every relevant clock.

3. **The clocks that could end it are reset by the flap itself.** The tick
   (`0x001f5b60`), kind dispatch read this pass (`0x001f5bcc-0x001f5c08`):
   kind 4 → own-side down-count, underflow → kind 2 + partner 9
   (`0x001f5c78-0x001f5cb4`); kind 8 → own-side down-count, underflow → kind 1
   (`0x001f5c44-0x001f5c6c`); kind 3 → up-count with the 21-tick promote
   (`0x001f5be4`, `0x001f5cd0`); **and kind 7 has its own arm**:

   ```
   001f5bf8  10620005  beq v1, v0(=7), 0x001f5c10
   001f5c10  96030432  lhu v1, 1074(s0)
   001f5c14  24630001  addiu v1, v1, 1             ; +0x432 up-counts every frame
   001f5c20  2842003d  slti v0, v0, 61
   001f5c24  14400005  bne v0, zero, 0x001f5c3c    ; < 61 -> keep counting
   001f5c28  a6030432  sh v1, 1074(s0)
   001f5c34  0c07dce6  jal 0x001f7398
   001f5c38  24060001  addiu a2, zero, 1           ; 61 consecutive kind-7 ticks -> KIND 1
   ```

   **A kind-7 helper who never reaches 8 is stripped to kind 1 after 61
   ticks.** On pass the flap resets +0x432 constantly (every 7→8 stamps init,
   every 8→7 stamps 0, and any re-assert through the setter zeroes it at
   `0x001f73cc`), so the fuse never accumulates. On a gate-locked run helper it
   accumulates monotonically — the DT-HOLD-90 mid-window +0x432 readings of
   15/6 on the TE/RG (`docs/double-team-requirements.md`, DT-HOLD-90 section)
   are consistent with exactly this up-count. Any future patch that extends
   kind-7 tenure without delivering kind 8 must handle this fuse.

4. **State 31 never touches 7/8** (§3), and state 33's 35°-breaker — the only
   think-side pair-destroyer found — exempts 7/8 (§3). The run-side registry
   teardown reads no timer (`dt3-review/3-consistency.md` §2, timer graveyard)
   and the pass side has no registry. So on pass, once the flap starts, **there
   is no mechanism left in the image with both the reach and the authority to
   end it** — which is what 130+ frames of survival looks like.

## 6. What live/jsonl evidence would confirm this (inputs to the solution agent)

Ranked; the first two decide the verdict.

1. **Slot 9 baseline, per-frame, the record trio (primary RT, helper TE,
   defender):** `+0x3E0` kind, `+0x432`, AI state byte. Predictions if the
   verdict is right: primary's kind-4 tenure while the helper holds kind 7 is
   ≈ 0 frames (either never 4 pre-contact, or 4 for ≤ a think-tick before
   kinds 5/6 appear); helper's +0x432 ramps monotonically during kind-7 tenure;
   primary+defender show kinds 5/6 + state 32 at/near the teardown frames
   36/43. **Refutes:** primary kind 4 coexisting with helper kind 7 for many
   frames and still no 8 → the binding predicate is instead in `0x001f7698`'s
   geometry (§2), and the next sample must add positions/facings.
2. **Both slots, per-frame, one word: the cached mode at `0x00600ce8`.**
   Slot 7 prediction: never 4 (nor raw-14-mapped anything but 3) while the QB
   holds the ball. Slot 9 prediction: flips to 4 at/near the handoff, before
   the records die. Refutation of either half kills the mode-gate reading of
   §4 (the capture asymmetry then rests on `0x0018e648`'s pairing geometry —
   untraced, §7).
3. **Slot 9: the trio's animation ids** (`0x003ad410` of `+0x304`, or infer
   from state 32 + kinds): 158 on the captured pair confirms the no-set
   mechanism; any yes-set id (146-151/168-170/173) appearing while the helper
   holds 7 without an 8 following within 2.1 yd refutes §1's reading of the
   fallback.
4. **Slot 7 control:** confirm no offensive player enters state 32 / kinds 5/6
   during the flap window (predicted absent).

## 7. Could not establish

1. **The mode enum's meaning** (`0x00154790`): single writers found for := 3
   (`0x0025316c`) and := 4 (`0x00142284`), setter `0x001546e8`, raw word
   `[[0x00600ce4]]`, cached shadow `0x00600ce8`; the play-phase labels are
   inference from caller context (state 24 "ball in air" tests 4; QB-dropback /
   deliver-ball / ball-carrier thinks test 3). One live read closes it (§6.2).
2. **Whether the primary ever holds kind 4 during slot 9's record windows.**
   The kind-4 diagnostic proved kind 4 occurs on that play
   (`double-team-mission-brief.md`, timer graveyard), not that it occurs on the
   record's primary while the helper held 7. §6.1 closes it.
3. `0x0018e648` (paired-animation start): internals untraced; whether its 20°
   / `0x005ff1d8` requirements fail on pass geometry is unknown — this is the
   fallback explanation if §6.2 shows mode 4 on pass.
4. `0x00200130` and the defender flag `+0xC & 0x0800` in `0x001f7698`;
   `byte[player+0] == 1` in `0x001f7c98`; table `0x00583920`'s yes/no split
   for `0x001f5db0`. None is on the verdict's critical path.
5. **Which teardown predicate fires at 36/43 on slot 9** — owned by mission
   brief item 2, not this lane; §4 supplies the candidate ordering (capture →
   re-point → peel/invariant).
6. The ten-in-state-32 datum's provenance (already flagged by
   `dt3-review/3-consistency.md` §3); every use above is marked.
7. Whether `0x0015ada0() == 3` on slot 7 (assumed via addresses.yaml's "3 =
   pass" reading of the jam helper; the beyond-2.1 rescue arm's live relevance
   rests on it).

### Footnote for lane 2 (drive fn)

`dt3-review/3-consistency.md` §2 calls the drive fn's `0x002E38E2` comparisons
(`0x001f6ad4`, `0x001f6aec`) "pair-separation tests". The operands are
`0x00469fc8` of two `+0x1A8` values and the constant is **65° in BAM24**
(0x002E38E3 = 65.0°, the same constant family as `0x001f7698`'s 40°/65° cones)
— they are bearing-divergence tests, not distances. Not re-traced further here;
flagged so the 36/43 lane does not chase a distance that is an angle.

# DT lane: the help-score economics

Recorded 2026-08-11. Static only, against `extract/SLUS_207.52`
(vaddr = file offset + 0xFF000, gp = 0x006056F0) via `recon.mipsdis`. No rig,
no network, no emulator, no commits. Every instruction quoted below was read
from the ELF this pass; where another lane's *measurement* is used it is cited
as such. Caller scans were run against padded entries (fn±4) as well as the
real ones.

**Question under test:** the second man is assigned to help only while
help-score beats own-assignment-score (`001f4bcc c.lt.s f0, f20`). If that
comparison flips after contact, does the helper "re-decide himself out" —
explaining the touch-abort at frames 36/43?

## VERDICT: REFUTED

**The `c.lt.s` at `0x001f4bcc` cannot un-decide anyone.** Its false path is
write-free (§3.3, quoted): failing the comparison merely skips this frame's
*re-assertion* of kind 7. Nothing on that path — and nothing anywhere in
phase 4 — clears kind 7, clears `+0x3E4`, or touches the registry. The
flip-at-contact story as posed is therefore refuted as a *direct* mechanism.

**The underlying economic flip is real, but it acts one phase earlier.** The
same per-frame function re-runs the full 1-on-1 assignment market (phases 1-2)
over a candidate list that *includes* the already-assigned kind-7 helper, and
phase 2 is a writer: the frame the helper's own-assignment market rises above
the 1.0 floor, phase 2 re-engages him (kind 2/3, new `+0x3E4`) — and if his
best target is the doubled defender himself, it simultaneously kicks the
primary to kind 1 and resets the defender (§3.4a). Either write breaks a
registry invariant and manage tears the record down the same frame. Which of
the four traced stop-paths actually fires at 36 vs 43 is **UNPROVEN**
statically (§3.5, §7).

---

## 0. The per-frame market: function map and cadence (re-derived)

Caller chain, each link verified by full-image `jal` scan (padded entries
checked, all returned empty):

```
0x00164fc4  jal 0x001f7298            ; sole caller of the manager
0x001f7298: jal 0x00154790 x2         ; gate: result must be 3 or 4 (meaning UNVERIFIED)
  001f72c8  jal 0x001f5b60            ; slot 0
  001f72d0  jal 0x001f5590            ; slot 1 -- THE MARKET (this lane)
  001f72d8  jal 0x001f6d10            ; slot 2 -- registry seek/manage/drive (lane 3's teardown)
  001f72e0  jal 0x001ef820            ; slot 3 -- kind jump-table pass ...
```

Slot 1 (`0x001f5590`, sole caller `0x001f72d0`) each frame:

```
001f5590  addiu sp, sp, -2752
001f55a0  jal 0x001f2ff0              ; -> 8-byte ref point at 2688(sp), float at 2704(sp)
001f55b8  jal 0x001f5510              ; build BOTH lists (a0=sp+1344 blockers, a1=sp+0 defenders)
001f55c8  jal 0x001f4790              ; phases 1-4 (a0=blocker list, a1=defender list, a2=ref)
001f55d0  jal 0x001f5158              ; adjacent-pair swap pass (a0=blocker list)
```

`0x001f5510` zeroes both 1344-byte arrays (12 entries x 112) with
`0x0046dd90`, then `0x001f2ea0` fills the blocker list and `0x001f2cd8` the
defender list. **Both lists live on `0x001f5590`'s stack and are rebuilt from
zero every frame.** Nothing computed in them survives the frame; only the
players' own fields (`+0x3E0` kind, `+0x3E4` handle, `+0x432..` counters)
persist.

Entry layout (both lists, 112-byte stride): `+0` chosen-target ptr / claim
ptr, `+4..` an 11-slot 8-byte pair-score cache `{+4 float score, +8 valid
byte}` indexed by opponent id (`(word0>>16 & 0xFF)<<3`, see §1.2), `+92`
player ptr (NUL terminator), `+96` angle ref->player (int24), `+100` SCORE,
`+104` distance-from-ref, `+108` flag byte.

## 1. Operand f0: the candidate's own-assignment score, `100(s4)`

### 1.1 Who is in the blocker list — the filter that *includes* a kind-7 helper

`0x001f2ea0`, per offense player 0..10:

```
001f2efc  lw v1, 1008(s1)             ; +0x3F0 block role
001f2f00  sltiu v0, v1, 3
001f2f04  beq v0, zero, 0x001f2f94    ; role >= 3 -> excluded
001f2f0c  beql v1, zero, 0x001f2f98   ; role 0    -> excluded (must be 1=pass or 2=run)
001f2f14  lw v1, 992(s1)              ; +0x3E0 engagement kind
001f2f18  addiu v0, v1, -4
001f2f1c  sltiu v0, v0, 3             ; kind in {4,5,6} ?
001f2f20  bne v0, zero, 0x001f2f94    ; kind 4/5/6 -> excluded (contact & two-man anim)
001f2f28  beq v1, zero, 0x001f2f90    ; kind 0 -> excluded
```

So kinds **1, 2, 3, 7, 8, 9** pass. **An already-assigned kind-7 helper
re-enters the market every frame**; a kind-4 (contact) blocker drops out.
Entry seed: `+92` player, `+96` angle, `+104` distance-to-ref, and

```
001f2f74  bne v1, a0, 0x001f2f84      ; role != 1 ?
001f2f78  swc1 f0, 100(s0)            ; (delay, always) +100 := distance-from-ref  (seed only)
001f2f80/84  sb .., 108(s0)           ; +108 := (role != 1), i.e. run-block flag
```

then `jal 0x001f2270` (order pass over the list; internals not audited).

### 1.2 Phase 1 of `0x001f4790` overwrites +100 with the real own score

For each blocker entry `s0`, over each defender entry `s2`:

```
001f48c8  lwc1 f0, 100(s2)            ; defender entry score
001f48cc  c.lt.s f21, f0              ; f21 = 5.0
001f48d4  bc1fl 0x001f4950            ; <= 5.0 -> defender not claimable, skip  (see 1.3)
001f48dc  lw v0, 0(v1)                ; defender player word 0
001f48e0  and v0, v0, fp              ; & 0x00FF0000
001f48e4  srl s1, v0, 13              ; id*8 -> cache slot in the blocker entry
001f48ec  lbu v0, 8(s3)               ; cache valid?
001f48f8  beq zero, zero, 0x001f4934  ;   yes -> f1 = cached 4(s3)
001f4900  ld a2, 0(sp)                ;   no  -> call the pair scorer:
001f491c  jal 0x001f4290              ; a0=blocker entry, a1=defender entry, a2=ref8,
                                      ; a3=playdir8, t0..t3 = mode flags 52/60/68/64(sp)
001f492c  swc1 f1, 4(v0)              ; cache it
001f4934  c.lt.s f20, f1              ; f20 running max, initialised 1.0 (001f48b0-b4)
001f4944  mov.s f20, f1               ; new best
001f4948  daddu s4, s2, zero          ; best defender entry
...
001f4960/4968  swc1 f20, 100(s0)      ; +100 := max(1.0, best pair score)   <-- f0's WRITER
001f496c  sw s4, 0(s0)                ; +0   := best defender entry (or NULL if none > 1.0)
```

**`f0` at `0x001f4bcc` is this `+100`: the same-frame maximum of
`0x001f4290(blocker, defender)` over defenders whose entry `+100 > 5.0`,
floored at 1.0.** For a blocker with no profitable claimable defender it is
exactly **1.0** — the usual case for a live helper (see §3).

The pair scorer `0x001f4290` (sole caller `0x001f491c`):
* `defender +12 & 0x800` -> handled in refiners (below); helper `+0x430` bit 0
  near-ref exclusion box (6.0 X / 3.0 Y) -> 0.
* base = `88 − min(dist(blocker, defenderPredictedPos), 88)`
  (`001f4414 lui at, 0x42b0` ... `001f4460 sub.s f20, f1, f20`), predicted pos
  from `0x001eee10`.
* if dist < 2.1 (`0x005ff184`) and `0x001f7698(blocker, defender)` and blocker
  role == 2 and defender kind == 9 and `resolve(blocker+0x3E4) == defender`:
  **x1.2** (`0x005ff188`) — the "already my marked man" bonus. **A kind-7
  helper satisfies the resolve test against the doubled defender.**
* role dispatch: role 1 -> pass refiner `0x001f31d0`, role 2 -> run refiner
  `0x001f3a00` (result replaces the score; 0 -> return 0).
  The **run refiner** starts `lw v0, 12(defender); andi 0x0800; bne -> return 0`
  then dispatches on the blocker's position-class byte `+0xB04` through a
  24-arm jump table at `0x005838C0`. Arm internals **not audited** (out of
  budget); the only engagement read in the whole run refiner is one
  `resolve(blocker+0x3E4)` at `0x001f3f48` inside one arm. It reads **no
  engagement kind at all**.
* tail multipliers: already-my-target within 4.5yd & facing gates x1.1
  (`0x005ff18c`), x1.03 (`0x005ff190`); kind-3 blocker whose resolved target
  is this defender on a matching `0x0015ada0()` play class: x1.1
  (`0x005ff19c`) or x3.0, doubled if defender `+8 == 255`; **not**-my-target:
  x0.85 (`0x005ff1a0`).

### 1.3 Which defenders are claimable (> 5.0)

`0x001f2cd8` includes a defender iff his AI state byte `+0xBCC` routes to the
include arm of table `0x00583650` (states 18..91 **except 32, 49, 50, 62, 73,
89, 92**; 16, 17 and the seven listed are hard-excluded) AND (play class == 0
OR `+12 & 0x4000` OR action-state byte `*(+0x2FC)` in {2, 30, 51}). **A
defender inside the two-man animation state 32 is not in the list at all.**

`0x001f2b00` then seeds `+100` via classifier `0x001f2830`:

```
001f285c  lw v0, 992(v1)              ; defender's OWN +0x3E0 kind
001f2860  addiu v0, v0, -4
001f2864  sltiu v0, v0, 3
001f2868  bne v0, zero, 0x001f2ae4    ; kind 4/5/6 -> return 0
```

return 0 (also for `+12 & 0x800`, AI states {49,50,62,73,89,92}, or defender
Y more than 15.0 below the `0x002004e0` reference Y — `001f2ac4 lui at,
0xc170`; which sideline/downfield direction negative Y is: UNVERIFIED) ->
**`+100 := 5.0` exactly** — which fails
phase 1's strict `> 5.0` test: unclaimable. Return 1 -> `+100 := 80 −
distance-from-ref` with multipliers (x0.8 moving-away `0x005ff144`, x0.333
states 10/11 `0x005ff148`, x0.0833 / x0.1667 facing-away `0x005ff14c/150`,
+0.25·|dx| arm). Phase 2 also stamps a claimed defender's `+100 := 5.0`
(`001f4ac0 swc1 f0, 100(v0)`, f0 = 5.0) so re-runs cannot double-claim.

So: **kind-9 (marked) defenders are claimable; kind-4/5/6 (in contact / in
the two-man animation) and state-32 defenders are not.**

### 1.4 Phase 2 can zero f0's storage, and deletes claimants from the pool

Same-target conflicts (`001f49bc bne v1, v0` over adjacent claims): loser gets
`+100 := 0, +0 := 0` (`001f49dc/49e0` or `001f49f0/49f4`), re-run flag s1 = 1
re-executes phase 1 (cache-hot) until stable. Each surviving winner is
processed (§3.4a) and then **his entry is deleted from the list** —
`0x001f5418` (sole caller `0x001f4abc`) is a memmove compacting every
following 112-byte entry down over the winner. Phase 4 therefore scores only
the leftovers: blockers whose final own-market was <= 1.0.

## 2. Operand f20, and the scorer `0x001f4c40` in full

### 2.1 Phase 4 harness (victims and candidates)

```
001f4b00  jal 0x001655b0              ; victim = GetPlayer(ctx, 0..10) -- NOT the list
001f4b10  lw v0, 992(s1)              ; victim kind
001f4b14..4b34                        ; keep kinds {4,2,3,6,5} else next player
001f4b40  jal 0x0013b798              ; s3 = resolve(victim+0x3E4) = the defender.  NO NULL TEST
001f4b48  lui at, 0x3f80              ; f20 = 1.0 floor
001f4b6c  jal 0x001f4c40              ; a0 = candidate 92(s0), a1 = victim, a2 = s3 defender
001f4b74  mov.s f1, f0
001f4b78  c.lt.s f20, f1              ; beats running best?
001f4b80  bc1fl skip
001f4b88  lwc1 f0, 100(s0)            ; candidate's own score (this frame's phase-1 result)
001f4b8c  c.lt.s f0, f1               ; help must ALSO beat his own market -- per candidate
001f4b94  bc1fl skip
001f4b9c  mov.s f20, f1               ; f20 = best help score
001f4ba0  daddu s4, s0, zero          ; s4 = best candidate entry
```

`f20` at `0x001f4bcc` = the best `0x001f4c40` return over the leftover
candidates, floor 1.0, each accepted candidate having already beaten his own
`+100`. The final `001f4bc8 lwc1 f0, 100(s4); 001f4bcc c.lt.s f0, f20` re-test
of the best candidate is redundant with `0x001f4b8c` (same values); both exist.
A victim cannot be his own helper: scorer split-angle = 0 < 20° -> score 0.

### 2.2 The scorer, annotated (entry `0x001f4c40`, returns f0)

```
001f4c40  ; a0=s5 candidate helper, a1=s0 victim (man being helped), a2=s4 defender
001f4c6c  mtc1 zero, f20              ; score = 0
001f4c70  jal 0x00260190              ; global gate
001f4c78  beq v0, zero, 0x001f5024    ; == 0 -> return 0.0
001f4c80  lw v0, 12(s4)               ; *** FIRST USE OF a2 -- NO NULL TEST (see 4) ***
001f4c84  andi v0, v0, 0x0800
001f4c88  bne v0, zero, 0x001f5028    ; defender flag 0x800 -> return 0.0
001f4c90  jal 0x00260208              ; 8-byte ref point -> 32/36(sp)
001f4cc4  jal 0x00260688(0)           ; mode; if 0 and helper +0x430 bit0 set:
001f4ce4..4d20                        ;   defender within |X-ref|<6.0 and Y<refY+3.0 -> return 0.0
001f4d30  jal 0x004adda8              ; vec = defenderPos - helperPos     (+400/+404 = pos X/Y)
001f4d38  jal 0x004ad760              ; f3 = |vec| = DISTANCE helper<->defender
001f4d44/4d54  f0 = refY - 1.5
001f4d58  lui at, 0x4040              ; f4 = 3.0            <- DT-6's radius site, confirmed
001f4d60  c.lt.s f2, f0               ; defender > 1.5 behind refY ...
001f4d6c/4d70                         ; ... and helper deeper than defender ->
001f4d7c  lui at, 0x4090              ;   f4 = 4.5 (widened radius, unreported in block-cycle)
001f4d84  c.lt.s f3, f4
001f4d8c  bc1f 0x001f5020             ; distance >= radius -> return 0.0    * HARD RADIUS *
001f4d94  lui at, 0x42b0              ; 88.0
001f4dac  sub.s f20, f0, f20          ; *** BASE = 88 - min(dist, 88) ***
001f4da8  jal atan2(vec)              ; s0 = angle helper->defender (int24, full turn 0x1000000)
001f4dbc  jal 0x004adda8 / atan2      ; s1 = angle victim->defender (the primary's engage line)
001f4dd0  lw a1, 24(s2)               ; defender heading (+0x1A8)
001f4dd8  jal 0x00469fc8(s0, heading) ; angdiff
001f4de0  v1 = 0x005fffff
001f4de8  slt/bne -> 0x001f4e68       ; > 135 deg off the defender's heading -> score 0
001f4df4  jal 0x00469fc8(s0, s1)      ; SPLIT = angdiff(helper->def, victim->def)
001f4e00  v0 = 0x000e38e3             ; = 20.0 deg          <- DT-7's site, confirmed
001f4e08  slt/beq -> 0x001f4e68       ; split <= 20 deg -> score 0   * MIN SPLIT vs PRIMARY *
001f4e10  v0 = 0x00555554             ; = 120.0 deg (not ~135 as block-cycle had it)
001f4e1c  beq -> ok                   ; split > 120 deg allowed only if
001f4e24..4e2c                        ;   helperY < defenderY, else score 0
001f4e3c  a1 = (s0 + 0x800000) & 0xFFFFFF   ; reverse of approach direction
001f4e44  lw a0, 24(s3)               ; helper's own heading
001f4e48  jal angdiff / halve
001f4e58  jal 0x00469b00              ; trig table -> f0
001f4e64  mul.s f20, f20, f0          ; facing factor (orientation sin-vs-cos UNVERIFIED)
001f4e6c  lw v1, 0(s4); lw v0, 576(s5)
001f4e74  bne -> skip                 ; defender id == helper +0x240 (his assigned man):
001f4e7c  lwc1 f0, 0x005ff1a4         ;   x1.1 own-man bonus
001f4e84.. tail                       ; in-play (0x001f82b8) & side bit (0x001f86b8 & 1):
001f4f10  lwc1 f0, 0x005ff1a8         ;   x1.1 play-direction bonus when the helper is on
                                      ;   the play-side of the defender in X (0(s2) vs 0(s3))
001f4f44.. registry+90 in {1,2} arms  ; special-mode X/ref comparisons, x1.2 (0x005ff1ac)
001f5020  mov.s f0, f20; jr ra
```

### 2.3 Verdict on block-cycle.md's "textbook double-team geometry"

**VERIFIED in substance, with three corrections:** base `88 − distance`
confirmed; hard 3.0-yd radius confirmed (`0x001f4d58`) but with an unreported
4.5-yd deep-side widening; ~20° minimum split confirmed **against the
victim->defender line** (`0x001f4e04`); the "~135° wrong side" is actually two
different gates — >135° is measured against the **defender's heading**, and
the split limit is **120°** (waived when the helper is deeper); own-man and
play-direction bonuses confirmed, both x1.1; plus three unreported kill
switches (global `0x00260190`, defender flag `+12 & 0x800`, helper `+0x430`
bit-0 near-ref box) and a facing multiplier.

### 2.4 Does any scorer input change at contact?

The scorer reads **no engagement state**: not the victim's kind, not the
helper's kind, no `+0x3F0`, no dt bytes, no registry record. Its inputs are
positions, headings, `+0x240` assignment id, and two flags. Therefore:

* **Discrete flip available: defender `+12` bit `0x800` only.** Writer census
  incomplete: immediate-form search found one live writer, `0x0012baa8`
  (`lw 12(s0); or 0x00200800; sw 12(s0)`, generic entity module); computed-mask
  writers would be invisible to this search. Whether contact sets it:
  **UNVERIFIED**.
* **Continuous flip: the 20° split gate.** `dist -> ~0` at the touch *raises*
  the base toward 88, but the direction `atan2(defender − helper)` becomes
  noise-dominated as the vector length collapses, and a helper converging onto
  the same body as the primary drives the split angle toward 0 — one frame
  under 20° zeroes the score. Arithmetically live at exactly the touch moment;
  which frames it actually fires on: **UNVERIFIED** (needs live positions).
* Meanwhile f0's side can rise: a second-level defender entering the pair
  scorer's profitable range lifts the helper's own market above 1.0 (chain in
  §1.2-1.3). Also purely positional.

## 3. One-shot or per-frame? Per-frame — and what that actually implies

### 3.1 Cadence

Verified single-caller chain (§0): the whole market — list rebuild, phases
1-4 — runs **every frame** the manager runs (`0x00154790() in {3,4}`). Nothing
is latched between frames except the players' own engagement fields.

### 3.2 What keeps electing the same helper on frames 2..35

1. He re-enters the list every frame (kind 7 passes the §1.1 filter).
2. His own market stays at the 1.0 floor while every worthwhile defender is
   either claimed by another blocker (entry stamped 5.0), unclaimable
   (kind 4/5/6, state 32, `0x800`), or out of profitable range — so phase 2
   skips him (`+0` NULL) and the compactor leaves him in the pool.
3. In phase 4 his help score vs the same victim/defender (~`88−d` x facing
   x1.1s at d < 3.0) beats max(1.0, other leftovers) — re-elected, and
   `0x001f7398(helper, D, 7)` re-asserts kind 7 with the same handle.
   Side effect quoted (`0x001f73c4-73e0`): the re-assert **re-zeroes
   `+0x432` (sh), `+0x434`, `+0x435`, `+0x42E` every frame**, pinning the
   `+0x432` FSM of the `0x001f55e8` pass at 0 (its 61-frame kind-1 give-up
   band at `0x001f5c20` — DT-8's site — only starts counting once re-election
   stops. That pass's cadence/semantics: **UNVERIFIED**, out of lane).

### 3.3 What the comparison's false path does — nothing

```
001f4bcc  c.lt.s f0, f20
001f4bd4  bc1f 0x001f4bf4             ; own >= help ->
001f4bf4  andi s2, s2, 0x00ff         ;   loop bookkeeping only
001f4bf8  sltiu v0, s2, 11
001f4bfc  bne v0, zero, 0x001f4b00    ;   next victim
```

No store, no call. Same for an empty candidate result (`beql s4, zero`).
Kind 7, `+0x3E4`, and the registry are untouched. **A flip alone strands the
helper at a stale kind 7; it aborts nothing.**

### 3.4 The four un-assign / stop paths that exist (all writers traced)

**(a) Phase-2 re-target or capture — the economic sibling of the flip.**
The frame the helper's phase-1 best exceeds 1.0, phase 2 processes him:

```
001f4a24  jal 0x0013b798              ; who is engaged to his chosen defender?
001f4a34  beql s0, v0, 0x001f4ab0     ; already him -> harmless skip
001f4a44  jal 0x001f7428(s0)          ; else: clear the CURRENT engagee
001f4a54  jal 0x001f7398(s0, 0, 1)    ;   ... and idle him (kind 1)
001f4a78/4a90  jal 0x001f7398(winner, D, 3-or-2)   ; winner re-engaged
001f4aa4  jal 0x001f74c8(D, winner, 9)             ; defender remarked
```

If his best is a second-level defender: helper kind 7 -> 2/3, `+0x3E4` -> LB
("passes the man off, climbs"); the registry invariant `helper +0x3E4 inside
the record` fails in slot 2 the same frame -> teardown. If his best is the
doubled defender D himself (claimable only while D's kind is not 4/5/6 —
§1.3): the helper captures D, and the *primary* — the current engagee — is
kicked to kind 1; `0x001f7428(primary)` sees the mutual handles and resets D
too (`001f749c jal 0x001f7540`); primary kind 1 fails manage's kind-in-2..8
check -> teardown. Both variants produce the observed picture.

**(b) `0x001f5158` role swap** — rebuilds the list itself (`001f51bc jal
0x001f2ea0`; this also makes the `swc1 f20, 100(s4)` at `0x001f4bdc` dead
downstream, closing dt3-review lane 1 open item 4), then swaps kinds inside
adjacent same-target pairs (`001f5264/5288 beq kind, 7` arms, distance-based).
On run-role pairs it is gated `*(0x00601280)+84 < 20` (`001f5218-521c`):
**dead after registry frame 20 on slot 9 — not the 36/43 abort.**

**(c) The `+0x432` FSM give-up** (`0x001f5c20` band -> `0x001f7398(x, 0, 1)`)
— reachable only after (3.2)'s per-frame re-zeroing stops, i.e. ~61 frames
*after* the economics flip. Too slow to be 36/43 on its own.

**(d) Registry teardown predicates** (lane 3, re-used here as cited
evidence): member-handle invariants, primary kind outside 2..8, helper kind
<2 / >=9 / 5 / 6, drive-fn geometry/defender-state — the reactive layer that
(a) triggers.

### 3.5 What stops it at exactly 36/43 — not established statically

The two arithmetically live drivers are (i) a second-level defender entering
the helper's own-market range and (ii) D becoming claimable/unclaimable as
his kind and AI state change through contact and the state-32 animation
(during state 32 all three bodies are out of the market entirely — defender
excluded from the list, kind-5/6 blockers filtered). Both are functions of
live positions and kind trajectories this lane cannot read. **UNVERIFIED.**
One live run settles it (§7).

## 4. The null-a2 question (lane 1 item 2): YES, it dereferences unguarded

`0x001f4b40 jal 0x0013b798` -> `s3` -> `a2` with no null test (lane 1,
re-confirmed), and the scorer's **first action after one global gate is
`001f4c80 lw v0, 12(s4)`** — a2 + 0xC, no null check anywhere in the
function (later also `+400/+404/+24/+0`). `0x00260190() != 0` (normal in
play) => a null resolve faults at `0x001f4c80` reading address `0xC`. The
only standing protection is the victim kind filter {2..6} implying a live
`+0x3E4` (written by `0x001f7398` at `0x001f73dc`). Any patch that widens the
victim filter or lets kinds survive with a stale handle must add the guard.
Cheapest closure if ever needed: turn `0x001f4b58`'s empty-list test region
into a `beq s3, zero, 0x001f4bbc` — but that is solution-agent territory.

## 5. Smallest intervention (INPUT to the solution agent — not deployable)

The flip cannot be fixed at `0x001f4bcc` (its false path is already inert).
To keep help winning while a dt_role is live, the helper must be exempted
from the phase-1/2 market that re-decides him. Smallest found — **one word**:

```
0x001f2f1c:  2c420003  sltiu v0, v0, 3     ; excluded kinds {4,5,6}
      -->    2c420004  sltiu v0, v0, 4     ; excluded kinds {4,5,6,7}
```

A kind-7 helper then never re-enters the blocker list: no phase-1 score, no
phase-2 capture/re-target (path (a) closed), no `0x001f5158` swap exposure
(both builder call sites share this instruction), and no phase-4 re-assert —
so `+0x432` free-runs and the FSM's 61-frame band (DT-8's site) becomes his
designed give-up, which is *later* than every observed window end.
Consequences to test, not assume: (1) the helper is also never re-elected, so
kind 7 persists on the player until promotion (kind 8) or give-up — phase 4
was re-asserting, not sustaining, so nothing else is lost statically; (2) on
pass plays this stops the slot-7 7<->8 market churn too — R5 requires the
slot-7 regression state; (3) variant `2c420005` (exclude {4..8}) also shields
kind-8 helpers — a separate decision. Acceptance sketch: slot 9 windows
extend past 43 / helper `+0x3E4` stays on D through the touch; slots 6/7/8
frame-compare unchanged where dt is absent. Requirements first per CLAUDE.md
rule 3; this lane deliberately writes no pnach.

## 6. Corrections to earlier docs from this lane's evidence

1. block-cycle.md §scorer: split limit is 120° not ~135°; the 135° gate is
   vs the defender's heading; radius has a 4.5 deep-side case; kill switches
   `0x00260190` / `+12 & 0x800` / `+0x430` bit 0 unreported. Bonuses x1.1
   confirmed. DT-6/DT-7 site addresses confirmed as quoted there.
2. block-cycle.md DT-8 ("61 -> 121 frames of kind-7 patience"): the site is
   real, but the switch operand at `0x001f5bf0` is `lhu +0x432`, not the
   engagement kind — the counter is *reset every frame by the phase-4
   re-assert*, so DT-8 alone cannot lengthen a live double team's window
   while re-election continues. (FSM semantics otherwise unaudited.)
3. dt3-review lane 1 open item 4: closed — `0x001f5158` rebuilds the list
   before reading it; the phase-4 score write perturbs nothing downstream.
4. dt3-review lane 1 §1: "0x001f4c40, read-only as far as this block is
   concerned" — confirmed; and its two operands' full chains are now on
   record here.

## 7. Could not establish (each with the cheapest closing read)

1. **Which stop-path fires at 36 vs 43 on slot 9.** Close: per-frame sample
   of helper/primary/defender `+0x3E0`, `+0x3E4`-resolve, defender `+0xBCC`
   and `+12` bit `0x800`, frames 30..46. Signatures: helper `+0x3E4` flips to
   a new defender with kind 2/3 => path (a) re-target; primary kind -> 1 with
   helper kind -> 2 on D => path (a) capture; helper kind -> 1 => FSM (c);
   kinds 5/6 + defender state 32 boundary at 36/43 => animation-exit market
   re-entry.
2. **The 24 position-class arms of the run refiner `0x001f3a00`** (whether an
   engaged blocker's own-market is structurally zeroed, which would be what
   holds frames 2..35 stable) — one arm's `resolve(+0x3E4)` at `0x001f3f48`
   located; arms unread.
3. **Writers of defender `+12` bit `0x800`** beyond `0x0012baa8`
   (computed-mask writers invisible to the immediate census).
4. **`0x00469b00` sin-vs-cos orientation** of the facing multiplier.
5. **`0x00154790()` mode values** gating the whole manager (inherited open
   item), and the meaning of `0x0015ada0()`'s class 2 (lane 2's open item —
   untouched here).
6. **Defender-side kind trajectory at contact** (does D hold 9 or flip to
   4/5/6 while the primary is in contact) — decides whether the capture
   variant of (a) is even reachable pre-animation.

# DT-HOLD-90 hostile review — lane 2: Site B (`0x001f2108`, `addiu s4, zero, 30` -> 90)

Static only. ELF `extract/SLUS_207.52`, vaddr = file_offset + 0xFF000, gp = 0x006056F0.
All listings produced with `recon/mipsdis.py` against that image on this branch.

**VERDICT: PATCH-BROKEN-BECAUSE — the Site B word cannot do what the pnach says it does.**
`0x001f2230` is not a one-shot "hold for 30 ticks" stamp. It is a **periodic refresh on a
16-frame duty cycle**, self-imposed by the same function via `p2->+0x42C := 16`
(`0x001f21e4`/`0x001f21e8`), and the tick decrements that cooldown to zero
(`0x001f5d18-0x001f5d2c`). A value already refreshed every 16 frames with any stamp
above 16 **never underflows**, so at baseline the primary's kind-4 timer already holds
indefinitely for the entire realistic rating range. Raising 30 to 90 therefore adds no
hold. What it *does* add is a permanent, not transient, occupancy of the `>= 61` band read
at `0x001ca0e8`, whose false branch leads to a **forced disengage** (`kind := 1`) at
`0x001ca104`-`0x001ca114`. The pnach describes that exposure as "its first ~29 ticks";
under the refresh it is the whole life of the double team.

Sub-claims 1, 3, 4 and 5 of the pnach's Site B paragraph are **CONFIRMED**. The topology
claim (p3 = primary blocker, offense side) is **CONFIRMED**. The mechanism claim
("the PRIMARY's kind-4 contact timer ... down-counter with underflow exit", "holds
90 - rating/16 ticks against the baseline 15..30") is **REFUTED**.

---

## 1. True function boundaries and the s4 audit over the real extent — CLAIM HOLDS

Boundary, established by scanning `0x001f1c00`-`0x001f2600` for every `jr`/`jalr`/`j`
and every positive `addiu sp`:

```
001f20f0  03e00008  jr ra                       ; end of the PREVIOUS function
001f20f4  27bd0140  addiu sp, sp, 320
001f20f8  27bdff70  addiu sp, sp, -144          ; <-- fn entry, frame 144
...
001f2268  03e00008  jr ra                       ; the ONLY exit
001f226c  27bd0090  addiu sp, sp, 144           ; frame 144 balances
```

Real extent = `[0x001f20f8, 0x001f226c]`, 94 instructions. Inside it there is **no**
`jr` other than `0x001f2268`, **no** `jalr`, and **no** `j` — verified by the same scan,
so there are no computed or tail-call exits to audit. A whole-image scan for external
branches/jumps landing in `(0x001f20f8, 0x001f226c]` returned **empty**, so no path
enters mid-body with a foreign `s4`.

`s4` = GPR 20. Sweep of the real extent with `mipsdis.writes_gpr` (which recognises
R-type `rd`, so a zero-idiom `daddu s4, zero, zero` = `0x0000a02d` would be caught —
the only zero idiom present is `0x001f2110 0000982d daddu s3, zero, zero`, s3 not s4):

```
W 001f2100  ffb40050  sd s4, 80(sp)     ; prologue save of the CALLER's s4
W 001f2108  2414001e  addiu s4, zero, 30   <-- the patched word
R 001f222c  02821023  subu v0, s4, v0      <-- the only consumer
W 001f2250  dfb40050  ld s4, 80(sp)     ; epilogue restore
```

Nothing else. Note for anyone re-running this: a naive "rt == 20" reader-sweep also
flags `0x001f210c swc1 f20`, `0x001f2128 lwc1 f20`, `0x001f2264 lwc1 f20`. Those are
**f20 in the FPU register file, not s4** — different register file, no interaction.

Callee clobber check (s4 is callee-saved, but rule 4 says verify): every direct callee
reached between the set at `0x001f2108` and the use at `0x001f222c` —
`0x00260598`, `0x001655b0`, `0x0013b798`, `0x004adda8`, `0x00469e78`, `0x001f7a38`,
`0x001f79c0`, `0x001f7a28` — contains **zero** writes to GPR 20 and zero `sd s4` over
its own extent. (Deeper levels — `0x0013b798`'s `jr a0` dispatch bodies past their own
`jal`s, and `0x00469e78`'s callees — were not walked; marked UNPROVEN, ABI-assumed.)

**Sole caller confirmed:** whole-image `jal`/`j` scan for target `0x001f20f8` returns
exactly one hit, `0x001f733c  0c07c83e  jal 0x001f20f8`. (Scanning `0x001f20fc` returns
nothing, so the padded-entry trap recorded in the mechanism doc does not apply here.)

## 2. Can p3 be a DEFENDER? — NO for the registered double team; the hypothesised
## kill mechanism does not exist regardless

**(a) The resolver.** `0x0013b798` reads the handle word at `0(a0)`, takes byte0 as a
kind selector (`andi a0, v0, 0x00ff`, `sltiu v1, a0, 10`), and dispatches through a table
at `lui v0, 0x0058; addiu v0, v0, -18816` = **0x0057B680** (matches the brief). Case 0 is
`jal 0x001655b0` with `a0 = (handle>>8)&0xff` (team) and `a1 = (handle>>16)&0xff`
(player index) — so the handle carries an explicit team byte and nothing in the resolver
constrains it to one side. The question is therefore legitimate.

**(b) Who writes `+0x3E4`.** An exhaustive scan for instructions forming `a1 = X + 996`
(the destination argument of the handle store `0x0013b870`) yields exactly three sites:
`0x001f73d0` (in `setKind` `0x001f7398`), `0x001f74f4` (in `setPartnerKind` `0x001f74c8`),
`0x001f7548` (in the reset `0x001f7540`, writes a null handle). Both setters take
`(a0 = player, a1 = new partner, a2 = kind)` and write `player.kind` and
`player.+0x3E4 = handle(partner)` together.

**(c) The registration wires the topology explicitly.** In the DT register fn
(`0x001f6338`), with s0/s1 = the two blockers and s3 = the doubled defender:

```
001f641c  0200202d  daddu a0, s0, zero
001f6420  0260282d  daddu a1, s3, zero
001f6424  0c07dce6  jal 0x001f7398        ; blocker s0 -> kind 7, s0.+0x3E4 = defender
001f6428  24060007  addiu a2, zero, 7
001f6434  0220202d  daddu a0, s1, zero
001f6438  0260282d  daddu a1, s3, zero
001f643c  0c07dce6  jal 0x001f7398        ; blocker s1 -> kind 3, s1.+0x3E4 = defender
001f6440  24060003  addiu a2, zero, 3
001f6444  0220282d  daddu a1, s1, zero
001f6448  0260202d  daddu a0, s3, zero
001f644c  0c07dd32  jal 0x001f74c8        ; DEFENDER -> kind 9, defender.+0x3E4 = s1
001f6450  24060009  addiu a2, zero, 9
001f6454  0220102d  daddu v0, s1, zero    ; then s0/s1 swap: record+0 (PRIMARY) = old s1
001f6458  0200882d  daddu s1, s0, zero
```

So for a registered pair: helper.+0x3E4 = defender, and defender.+0x3E4 = **primary**.
`p2 = handle(helper+0x3E4)` = the defender; `p3 = handle(p2+0x3E4)` = the primary
blocker, offense side. **Topology claim CONFIRMED.** The `p3 == p_i` degenerate case
(ordinary 1-v-1 reciprocal engagement) is naturally excluded by the `kind == 4` gate,
because `p_i` was already gated to kind 8.

**(d) Kind 4 IS reachable on the defensive side** — the doc does not say this and it
matters. Kinds 4 and 8 are never passed to `setKind`/`setPartnerKind` (an exhaustive
sweep of all 64 call sites shows only kinds 1,2,3,5,6,7,9). They are written by two
direct `sw`s in the re-decision `0x001ef820`, which walks team `0x00260598()`:

```
001ef908  24120004  addiu s2, zero, 4     ; new own kind 4
001ef90c  24130004  addiu s3, zero, 4     ; new PARTNER kind 4
...
001efa38  ae1203e0  sw s2, 992(s0)        ; own kind  (s0 = team-T player)
...
001efab8  ae3303e0  sw s3, 992(s1)        ; PARTNER's kind  (s1 = handle(s0+0x3E4))
001efabc  a6200432  sh zero, 1074(s1)     ; and zero his timer
```

`s1` is the engaged partner — the defender. So defenders routinely carry kind 4, which is
why the tick's other-team branch for kind 4 is live code, not dead. That does not put a
defender in the p3 slot for a registered pair (see (c)), but it means the `kind == 4`
gate at `0x001f2204` is **not by itself** proof that p3 is an offense player. The proof is
(c), not the gate.

**(e) The hypothesised kill shot is not in the code.** The other-team path is:

```
001f5c44  1675001e  bne s3, s5, 0x001f5cc0   ; kind 8: team index != 0x00260598() -> up
001f5c7c  16750010  bne s3, s5, 0x001f5cc0   ; kind 4: same split
...
001f5cc0  96020432  lhu v0, 1074(s0)
001f5cc4  24420001  addiu v0, v0, 1
001f5cc8  10000013  beq zero, zero, 0x001f5d18   ; unconditional
001f5ccc  a6020432  sh v0, 1074(s0)              ; delay slot: store
```

Four instructions: load, +1, store, leave. **No comparison, no limit, no kind transition,
no clamp.** The brief's premise ("90 may be past a limit") describes a limit that does not
exist on this path. Even in the counterfactual where a defender were stamped with 90, the
consequence would be an unbounded u16 counter, not a state change. The 61 limit that does
exist belongs to the *kind-7* up-counter (`0x001f5c20 slti v0, v0, 61` ->
`setKind(self, 0, 1)`) and to the external reader at `0x001ca0e8` (see §7). The tick's
outer loop is confirmed two teams x 11 (`0x001f5d44 sltiu v1, s4, 11`,
`0x001f5d54 sltiu v0, s3, 2`), so both sides are ticked.

## 3. The `bne v1, 4` gate — the stamp is FULLY gated. CLAIM HOLDS.

```
001f21fc  8c8303e0  lw v1, 992(a0)              ; p3 kind
001f2200  24020004  addiu v0, zero, 4
001f2204  1462000b  bne v1, v0, 0x001f2234      ; not kind 4 -> 0x001f2234
001f2208  24050001  addiu a1, zero, 1           ; delay slot, dead on the taken path
...
001f2230  a4820432  sh v0, 1074(a0)             ; the stamp
001f2234  26620001  addiu v0, s3, 1             ; <-- branch target: AFTER the stamp
```

`0x001f2234` is the loop-increment tail, four bytes past the `sh`. On the skip path
execution does **not** reach `0x001f2230`; there is no fallthrough and no other entry
(§1's external-branch scan was empty). Note the delay slot is a non-likely `bne`, so
`addiu a1, zero, 1` executes on both paths — it is dead either way (a1 is next used only
as the comparand at `0x001f2210`, on the not-taken path).

## 4. `+0x42E` and the -0.13 debuff are unaffected by s4. CLAIM HOLDS.

`+0x42E`: `0x001f2214 a086042e sb a2, 1070(a0)` with `a2` set at `0x001f21f8
addiu a2, zero, 1` — a constant, no s4 involvement. It sits in the delay slot of the
non-likely `bne` at `0x001f2210`, so it fires on both `+0x3F0` sub-paths, but it is
*downstream of the same kind-4 gate as the stamp* — arm and stamp always happen together.
Whole-image sweep for offset 0x42E returns only two sites: `0x001efa5c` and this one.

Debuff: `f20` is loaded once at `0x001f2128 c7949a48 lwc1 f20, -26040(gp)` (= 0x005ff138)
and delivered by `mov.s f12, f20` at `0x001f21c4` / `0x001f21d4` into two calls of
`0x001f79c0` with `a0 = s2` (p2) and `a1 = 1` then `a1 = 0`. `0x001f79c0` adds `f12` to
`a0 + 1028 + 32` (a1==1) or `a0 + 1028 + 28` (a1==0) with a `max.s f0, f0, f1` clamp at
zero. Argument registers are a0/a1/f12; GPR 20 is never read or written by that callee
(§1). **No register reuse, no argument aliasing.**

## 5. Loop bound and kind test — CLAIM HOLDS, kind 7 CANNOT match.

```
001f2110  0000982d  daddu s3, zero, zero        ; index := 0
001f2138  0c05956c  jal 0x001655b0              ; loop head: player(team s5, index s3)
001f213c  02a0202d  daddu a0, s5, zero
001f2144  24030008  addiu v1, zero, 8
001f2148  8e0203e0  lw v0, 992(s0)              ; own kind
001f2150  14430038  bne v0, v1, 0x001f2234      ; EXACT equality with 8
...
001f2238  305300ff  andi s3, v0, 0x00ff
001f223c  2e63000b  sltiu v1, s3, 11            ; index 0..10 -> 11 players
001f2240  1460ffbd  bne v1, zero, 0x001f2138
```

It is a single `bne` against the literal 8, not a range test — kind 7 is excluded, so the
stamped set is exactly as claimed. The team is `s5 = 0x00260598()`
(`0x001f212c daddu s5, v0, zero`), the *same* accessor used by the re-decision
(`0x001ef854`) and by the tick's own-team test (`0x001f5b84`, compared at `0x001f5c44` /
`0x001f5c7c`) — so the walked team is by construction the down-counting team.

## 6. THE BREAK — Site B is a 16-frame refresh, not a one-shot hold

The stamp is not reached every frame. The function gates its entire tail on the
**defender's** `+0x42C`, and then sets that field itself:

```
001f2190  0240202d  daddu a0, s2, zero          ; a0 = p2 (the defender)
001f21b0  0c07de8e  jal 0x001f7a38              ; getter
001f21c0  1440001c  bne v0, zero, 0x001f2234    ; p2->+0x42C != 0 -> skip EVERYTHING
...                                             ;   (both debuffs, +0x42E, and the stamp)
001f21e0  0240202d  daddu a0, s2, zero
001f21e4  0c07de8a  jal 0x001f7a28              ; setter
001f21e8  24050010  addiu a1, zero, 16          ; p2->+0x42C := 16
```
```
001f7a28  00052e00  sll a1, a1, 24
001f7a2c  00052e03  sra a1, a1, 24
001f7a30  03e00008  jr ra
001f7a34  a485042c  sh a1, 1068(a0)             ; setter body
001f7a38  8482042c  lh v0, 1068(a0)             ; getter body
001f7a3c  03e00008  jr ra
001f7a40  0002102b  sltu v0, zero, v0           ; -> (value != 0)
```

and the per-frame tick decrements it for every player, clamped at zero
(`s2 = player + 1028`, so `40(s2)` = `+0x42C`):

```
001f5d18  96420028  lhu v0, 40(s2)
001f5d1c  2442ffff  addiu v0, v0, -1
001f5d20  00021c00  sll v1, v0, 16
001f5d24  1c600005  bgtz v1, 0x001f5d3c
001f5d28  a6420028  sh v0, 40(s2)               ; store the decrement
001f5d2c  a6400028  sh zero, 40(s2)             ; <= 0 -> clamp to 0 (no lockout)
```

The manager calls the tick before Site B in the same frame (`0x001f72c8` then
`0x001f733c`). So the primary's `+0x432` behaves as: **-1 every frame, re-stamped to
`base - rating/16` every 16-17 frames**, for as long as the helper stays kind 8.

Consequence, and this is the whole finding: a periodically refreshed down-counter
underflows only if the stamp value is **<= 16**. At `base = 30` the stamp is
`30 - rating/16`; for any block rating below ~208-224 that is >= 17, so the primary's
kind-4 timer **already never underflows while the helper holds**. The underflow exit the
pnach relies on —

```
001f5c84  96020432  lhu v0, 1074(s0)
001f5c88  2442ffff  addiu v0, v0, -1
001f5c8c  00021c00  sll v1, v0, 16
001f5c90  04610021  bgez v1, 0x001f5d18
001f5c94  a6020432  sh v0, 1074(s0)
001f5c98..001f5cb4                              ; self -> kind 2, partner -> kind 9
```

— is unreachable for the primary of a live double team at baseline. Raising 30 to 90
therefore buys **zero additional hold** on this half. The pairing dies when the *helper's*
kind-8 timer underflows (`0x001f5c4c-0x001f5c6c`, stamped only at lock-in by Site A) —
which is exactly what the pnach's own v1 post-mortem concluded, and is Site A's word, not
Site B's. Calling the two words "BOTH halves" of one hold is the error: only one half has
a timer that can expire.

## 7. The cost the pnach understates: permanent occupancy of the `>= 61` band

`0x001ca0e4` reads `+0x432` and, when the value is **not** below 61, falls into a forced
disengage:

```
001ca0e4  86a20432  lh v0, 1074(s5)
001ca0e8  2842003d  slti v0, v0, 61
001ca0ec  1440000b  bne v0, zero, 0x001ca11c    ; < 61 -> skip
001ca0f4  0c07dd64  jal 0x001f7590              ; else: state test on s5
001ca0f8  02a0202d  daddu a0, s5, zero
001ca0fc  14400007  bne v0, zero, 0x001ca11c    ; nonzero -> spared
001ca104  0c07dd0a  jal 0x001f7428              ; engagement TEARDOWN
001ca108  0000982d  daddu s3, zero, zero
001ca114  0c07dce6  jal 0x001f7398              ; and kind := 1
001ca118  24060001  addiu a2, zero, 1
```

Baseline: the primary's timer lives in `[stamp-16, stamp]` ⊂ `[7, 30]` — it never enters
this band. Patched: `[90-rating/16-17, 90-rating/16]` ⊂ roughly `[58, 90]` — it is at or
above 61 for effectively the entire double team, not "its first ~29 ticks" as the pnach
comment states. The transient framing in the pnach is a direct consequence of the same
one-shot misreading as §6. Whether the disengage actually fires depends on
`0x001f7590(primary)` (kind 4 routes to `0x001f7618`, a distance/state test that can
return either 0 or nonzero) and on whether `0x001c9e28` — its sole caller is
`0x001cb2ec` — runs on the primary at all. So this is a *hazard*, not a proven regression;
but it is the hazard the acceptance run must instrument, and the patch's stated exposure
window is wrong by an order of magnitude.

---

## Could not establish

* **The rating scale, hence the true stamp range.** The pnach's `15..30` (and `75..90`)
  requires `+0xB86`/`+0xB88` to reach ~240. The only bound I could derive statically is
  `0x001c9eec-0x001c9f08`, where `+0xB86 + 0xB74` is pushed through
  `sll 23; sra 24` — a sign-extended 8-bit result of the sum halved, implying each field
  is at most ~127, giving `23..30` baseline and `83..90` patched. **This is a bound, not a
  measurement.** It is load-bearing for §6: if ratings really can reach 224+, the very best
  blockers would stamp <= 16 and Site B's word would fix that narrow case (and only that
  case). Someone must read the live value of `+0xB86`/`+0xB88` for the slot-9 linemen.
* **Whether `0x001c9e28` (via `0x001cb2ec`) actually runs on a kind-4 primary blocker**,
  and what `0x001f7590` returns for one. Both gate §7 from hazard to regression. Not
  traced; the `0x001c9ee4 bgez v0, 0x001ca73c` guard on `lb v0, 38(s1)` was not resolved.
* **s4 preservation two levels deep** — `0x0013b798`'s dispatch bodies call out to
  `0x0016f668`, `0x00180688`, `0x00133e48`, `0x0013b1f0`, `0x00143a70`; `0x00469e78` has
  callees. Direct callees are clean (§1); deeper levels are ABI-assumed, not verified.
* **Whether a defender's `+0x3E4` can ever point at a teammate**, which is the only
  remaining route to a defender in the p3 slot. Ruled out for *registered* pairs by §2(c);
  not ruled out for an unregistered kind-8 blocker whose partner was wired by one of the
  other `setKind` call sites. Given §2(e) (no limit on the up path) this is low-stakes,
  but it is a genuine open negative and should not be written up as proven.
* **`p2`/`p3` null-safety.** Neither resolve result is null-checked before
  `lw v0, 992(a0)` / `sh v0, 1074(a0)`; `0x0013b798` returns 0 for an out-of-range kind
  byte. Pre-existing, unchanged by this patch, and not investigated.

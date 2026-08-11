# Lane 3 — closed-set consumer census of `+0x432` under DT-HOLD-90

Hostile review of `patches/14F8B841.dt-hold-90.pnach` (site A `0x001ef918`
`addiu v1, zero, 30 -> 90`; site B `0x001f2108` `addiu s4, zero, 30 -> 90`).
Question: does any consumer of `+0x432` have a threshold that 75–90 crosses and
15–30 never did?

Static only, on `extract/SLUS_207.52` (vaddr = file_offset + 0xFF000,
gp = `0x006056F0`), `recon.mipsdis`. Every instruction below was read out of the
ELF this pass. No emulator, no network, no commits.

---

## 0. Answer up front

**VERDICT: NO-NEW-THRESHOLDS.**

Not because no thresholds exist — three do (`slti 61` twice, `slti 21`,
`slti 11`) — but because **every one of them is gated on an engagement kind that
the patch structurally cannot stamp**. The two readers that *can* see 75–90
(`bgez` underflow on the kind-4/kind-8 down-counters, and the `!= 0` gate in
re-selection) compare against the same side at both values; only the duration
moves.

Two hazards previously written down are **withdrawn as unreachable**, and both
withdrawals rest on the same newly-derived fact (§3):

| hazard as previously stated | source | this pass |
|---|---|---|
| "a 75-90 timer flips [the `slti 61`] gate for its first ~29 ticks" | the pnach's own comment | **unreachable** — the block is `kind == 3`-gated (`0x001ca084`), and kind 3 is only ever entered through a timer-zeroing setter |
| "raising the initialiser to 90 makes the kind-7 branch expire on its first tick (90 ≥ 61)" | `docs/review-2026-08-11.md` D5 | **unreachable** — every entry into kind 7, *including the 8→7 flip inside the patched function itself*, stamps the timer to **0** |

One genuinely new finding, which matters more than either hazard: under the
measured 7↔8 oscillation the patch is **inert**, not harmful (§4).

---

## 1. The census is closed — 13 readers, no more

Four sweeps over every loadable word:

1. **Literal displacement 1074 (0x432), all 22 load/store opcodes** — 20 hits,
   all `lh`/`lhu`/`sh`, all on plausible player bases. 8 stores, **12 loads**.
2. **Indirect through a `+0x3E0`-derived base** (addiu-tracked, `daddu rD,rS,zero`
   copies followed, clobber-invalidated, jal kills caller-saved, 4096-byte
   window). Exactly 5 hits at `+0x52`: the 4 zeroing `sh`s the mechanism doc
   lists, plus **1 load** — `0x001efd08 lh v0, 82(s4)`. The matching indirect
   read the brief asked for is this one, and it is the only one.
3. **General base+K** (any `addiu rD,rS,K`, any K, effective offset in
   `+0x430..+0x434`): 34 hits, and every one outside the two sets above resolves
   to a *different* struct (base deltas 72/76/80/84/408/1056) or to `sp`.
4. **Wide-access overlap** — every `lw/ld/lq/lwl/ldl/sd/sq/...` whose byte range
   can cover 0x432/0x433 at any alignment: 34 hits, **all `sp`-relative register
   saves**. No aggregate load ever covers the field on a player struct.

No reader copies the value into another field (all 13 feed an arithmetic or a
compare in the same basic block), so there is no aliased consumer either.

Residual holes are in §7.

---

## 2. Who can hold 75–90 at all (the reachability lemma)

Both stamp sites iterate **the offense team only**:

```
001ef854  0c098166  jal 0x00260598      ; -> team-with-possession byte [0x00601f4c]+64
001ef85c  0040b82d  daddu s7, v0, zero
001ef860  02e0202d  daddu a0, s7, zero  ; site A's player fetch: a0 = that team
001ef868  0c05956c  jal 0x001655b0

001f2120  0c098166  jal 0x00260598      ; site B, same
001f212c  0040a82d  daddu s5, v0, zero
001f2138  0c05956c  jal 0x001655b0
001f213c  02a0202d  daddu a0, s5, zero
```

Site A writes only on a kind *change* (`0x001efa2c beq a1, s2`), and the patched
band (`0x001ef918`) is reached only for current kind ∈ {7,8} with new kind **8**
(`0x001ef93c addiu s2, zero, 8`); kinds 2–4 take the untouched `0x001ef8e8` copy.
Site B writes only onto a player whose kind is **4** (`0x001f2204 bne v1, v0`).

Every other route out of kinds 4/8 runs through `0x001f7398` / `0x001f74c8` /
`0x001f7428` / `0x001f7540`, and all four zero `+0x432` unconditionally
(`0x001f7398` even zeroes twice — once via its own `jal 0x001f7428`, once at
`0x001f73cc`). The only raw kind writes that skip the zeroing are `0x001efa38`
(paired with the stamp at `0x001efa34`), `0x001f5cf4` (kind 3 → 2) and
`0x001e81ec` (kind 5 → 6) — none of which can produce kind 3, 7, or a
carried-over stamp.

> **Lemma.** A `+0x432` value in 75–90 exists only on an **offense** player whose
> kind is **8** (site A) or **4** (site B), and it is destroyed the moment his
> kind changes by any route other than 8→8 / 4→4.

Because he is on the offense team, `0x001f5c44` / `0x001f5c7c` (`bne s3, s5`)
always send him down the **decrement** arm, never the other-team increment.

---

## 3. The table

`reach?` = can this reader ever observe a stamped 75–90, per the §2 lemma.

| reader | fn | comparison | 15–30 behaviour | 75–90 behaviour | reach? | verdict |
|---|---|---|---|---|---|---|
| `0x001ca0e4` `lh v0, 1074(s5)` | `0x001c9e28` blocker re-think (P2 assignment-drop) | `slti v0, v0, 61` → `bne` skips the drop check | timer<61 → drop check skipped | timer≥61 → drop check + possible teardown | **NO** — `kind==3` gate | **not flipped** |
| `0x001f5be8` `lh v0, 1074(s0)` | `0x001f5b60` tick, kind 3 | `slti v0, v0, 21` (`0x001f5cd0`) → ≥21 calls `0x001f82e8`, may force kind 2 | <21 for 21 frames | would be ≥21 at once | **NO** — kind 3 only | **not flipped** |
| `0x001f5c10` `lhu v1, 1074(s0)` | tick, kind 7 | `slti v0, v0, 61` (`0x001f5c20`) → ≥61 ⇒ kind 1 | up-counter from 0, converts at 61 | would convert on tick 1 | **NO** — kind 7 always enters at 0 | **not flipped** (D5 withdrawn) |
| `0x001f5c40` `lh v0, 1074(s0)` | tick, kind-7 tail | `slti v0, v0, 11` (`0x001f5d0c`) → ≥11 zeroes `+0x3EC` | 0..60 up-count crosses 11 at frame 11 | identical | **NO** — kind 7 enters at 0 | **not flipped** |
| `0x001f5c4c` `lhu v0, 1074(s0)` | tick, kind 8, own team | `addiu -1`, `sll 16`, `bgez` (`0x001f5c58`) → underflow ⇒ kind 1 | expires after 15–30 frames | expires after 75–90 frames | **YES** | same side, later — **intended effect** |
| `0x001f5c84` `lhu v0, 1074(s0)` | tick, kind 4, own team | `addiu -1`, `sll 16`, `bgez` (`0x001f5c90`) → underflow ⇒ self kind 2 + partner kind 9 | expires after 15–30 | expires after 75–90 | **YES** | same side, later — **intended effect** |
| `0x001f5cc0` `lhu v0, 1074(s0)` | tick, kinds 4/8, **other team** | **none** — `addiu +1`, `sh`, `beq zero,zero,0x001f5d18` | free-running frame count | n/a | **NO** — stamps are offense-only | **no threshold exists here** |
| `0x001f5cd8` `lhu v1` | tick, kind 3, <21 arm | feeds the `slti 11` tail | up-count | n/a | **NO** | not flipped |
| `0x001f5cec` / `0x001f5cf8` `lhu v1` | tick, kind 3, ≥21 arms | feeds the `slti 11` tail | up-count | n/a | **NO** | not flipped |
| `0x001f5bf0` / `0x001f5c0c` `lhu v1` | tick, default band (kinds 0,1,2,5,6,9+) | `slti v0, v0, 11` (`0x001f5d0c`) → ≥11 zeroes `+0x3EC` | crosses 11 at frame 11 | n/a — kinds 4/8 branch to `0x001f5d18` and never reach `0x001f5d0c` | **NO** | **not flipped** |
| `0x001efd08` `lh v0, 82(s4)` | `0x001efc00` re-selection | `bnel v0, zero, 0x001eff30` — **boolean `!= 0`** | nonzero ⇒ candidate skipped | nonzero ⇒ candidate skipped | **YES** | same side, longer window — **intended effect** |

Three readers can see 75–90. All three compare it the same way they compared
15–30. Zero flips.

---

## 4. The quoted flips (and why each fails to fire)

### 4a. `0x001ca0e8` — the gate the pnach itself flagged. Gated on kind 3.

```
001ca078  126001a3  beq s3, zero, 0x001ca708   ; no target -> bail
001ca07c  24020003  addiu v0, zero, 3
001ca080  8ea303e0  lw v1, 992(s5)             ; s5's engagement kind
001ca084  14620025  bne v1, v0, 0x001ca11c     ; kind != 3 -> SKIP the whole block
...
001ca0e4  86a20432  lh v0, 1074(s5)
001ca0e8  2842003d  slti v0, v0, 61
001ca0ec  1440000b  bne v0, zero, 0x001ca11c   ; <61 -> keep the engagement
001ca0f4  0c07dd64  jal 0x001f7590             ; >=61: is the engagement still valid?
001ca0fc  14400007  bne v0, zero, 0x001ca11c
001ca104  0c07dd0a  jal 0x001f7428             ; TEARDOWN
001ca108  0000982d  daddu s3, zero, zero       ; (delay) target lost
001ca114  0c07dce6  jal 0x001f7398             ; self -> kind 1
```

A full branch-target and data-word sweep of `0x001ca08c..0x001ca0e8` returns
**nothing** — the only way in is the fall-through past `0x001ca084`. So the
`slti 61` sees a kind-3 timer, and by §2 a kind-3 timer is a plain up-counter
starting at 0 (kind 3 is written only at `0x001b5784`, `0x001f4a78`, `0x001f59a0`,
`0x001f643c`, `0x001f6be4` — all `jal 0x001f7398`, all zeroing). Its path to 61
is 61 frames of kind 3 at both patch levels. **The pnach's own hazard note is
wrong**, and the required slot-7 pass-protection regression arm is justified for
other reasons, not this one.

Enclosing fn `0x001c9e28` (nop-padded at `0x001c9e24`; sole caller `0x001cb2ec`)
is the throttled blocker re-think of `pass-vs-run-blocking.md` P2 — `lh v0,
2950(s5)` at `0x001c9eec` is the `+0xB86` block rating driving the `sll 23; sra 24`
cadence. The drop test runs every 1–4 frames when the `+38` countdown goes
negative.

### 4b. `0x001f5c20` — D5's self-defeat. Kind 7 always enters at zero.

```
001f5c10  96030432  lhu v1, 1074(s0)
001f5c14  24630001  addiu v1, v1, 1
001f5c18  00031400  sll v0, v1, 16
001f5c1c  00021403  sra v0, v0, 16
001f5c20  2842003d  slti v0, v0, 61
001f5c24  14400005  bne v0, zero, 0x001f5c3c   ; <61 -> stay kind 7
001f5c28  a6030432  sh v1, 1074(s0)            ; (delay) store t+1 either way
001f5c34  0c07dce6  jal 0x001f7398             ; >=61 -> self kind 1
```

For this to fire on tick 1 the player must *arrive* in kind 7 holding ≥60. The
four `jal 0x001f7398 (a2=7)` sites (`0x001f4be4`, `0x001f6424`, `0x001f68b4`) zero
it. The fifth route is the 8→7 flip **inside the patched function**, and it
zeroes it too:

```
001ef888  10400031  beq v0, zero, 0x001ef950   ; 0x001f7590(player) == 0 -> jump-table path
001ef88c  0000a02d  daddu s4, zero, zero       ; (delay, ALWAYS) s4 := 0   <-- daddu-zero idiom
...
001ef97c  00800008  jr a0                      ; table at 0x00583340, kind 8 -> 0x001ef9b0
001ef9b0  1000001d  beq zero, zero, 0x001efa28
001ef9b4  24120007  addiu s2, zero, 7          ; (delay) new kind := 7
...
001efa2c  10b2001d  beq a1, s2, 0x001efaa4
001efa34  a6140432  sh s4, 1074(s0)            ; timer := s4 == 0
001efa38  ae1203e0  sw s2, 992(s0)             ; kind := 7
```

A register sweep of `0x001ef820..0x001efb10` finds `s4` written at exactly four
places: `0x001ef88c` (zero), `0x001ef8fc` and `0x001ef92c` (the two formula
copies, both on the `0x001f7590 != 0` side), and the epilogue `ld` at
`0x001efb00`. The jump-table path cannot reach the formula copies
(`0x001ef910`/`0x001ef940` both branch past `0x001ef950`, and `0x001ef950` has
exactly one predecessor). **8→7 stamps zero. D5's hazard cannot occur.**

---

## 5. The 7↔8 oscillation — the patch is inert there, at both values

Manager order is fixed: tick `0x001f72c8` runs **before** re-decision `0x001f72e0`.
`0x001f7590` (the flap driver, `0x001ef888`) is distance- and partner-kind-driven
(`0x004adf28` distance vs `[0x005ff1c0]`, plus `p3.kind == 4` / `0x001f0ba8`) —
it never reads `+0x432`, so the patch does not change the flap rate.

Per-frame timer sequence under a 1–2 frame flap (patched values in brackets):

| frame | kind at tick | tick does | re-decision does |
|---|---|---|---|
| n | 7 | 0 → 1, `slti 61` false | `0x001f7590` ok ⇒ kind 8, timer := **[75–90]** (was 15–30) |
| n+1 | 8 | 89 → 88 (was 29 → 28), `bgez` ok | `0x001f7590` fails ⇒ kind 7, timer := **0** |
| n+2 | 7 | 0 → 1 | ok ⇒ kind 8, timer := **[75–90]** |

Consequences:

* The kind-8 down-counter is re-stamped before it can descend more than 1–2, and
  the kind-7 up-counter is re-zeroed before it can climb past 2. **Neither timer
  exit is reachable while the flap runs — at 30 or at 90.** The 130+ frame
  oscillation on slot 7 is therefore not evidence for or against the patch.
* Nothing in the flip tears the engagement down: `0x001efa34/38` writes only the
  timer and the kind; `+0x3E4` is untouched because `0x001f7398` is not called.
* So what ended those engagements was **not** either timer. The exits that remain
  are the DT-manage teardowns (`0x001f6858`, `0x001f68a4`, `0x001f6bac`,
  `0x001f6c84`, plus the `0x001f5350` region), the state-machine teardowns
  (`0x001cb058`/`0x001cb0ac`/`0x001cb0f4`/`0x001cb160`/`0x001cb17c`/`0x001cb424`/
  `0x001cb470`/`0x001cb540`, `0x001dc*`), and the play-end reset `0x001f6ff0` —
  **which of those actually fired is not determinable statically and is flagged
  UNPROVEN**; it needs a live trace of `+0x3E0`/`+0x3E4` at the frame of release.
  Note `0x001ca104` is *not* among them: kind-3 gated.
* Corollary for the acceptance plan: `dt_last_hold_frame 43` will only move if the
  target player holds kind 8 for consecutive frames. If the slot-9 blocker flaps
  the way slot 7 did, the patch will read as a null result through no fault of the
  stamp.

## 6. 16-bit hygiene on every consumer

* **Sign extension.** Three readers are signed `lh` (`0x001ca0e4`, `0x001f5be8`,
  `0x001efd08`), ten are `lhu`. The distinction only bites above 0x7FFF. The field
  is written only with 15–30 / 75–90 / 0 / ±1 steps, so the signed/unsigned split
  is inert at both patch levels. The patch does not move any value nearer 0x8000.
* **`sll 16` / `sra 16`.** Used correctly at `0x001f5c18-1c` (kind 7) and
  `0x001f5d00-08` (default tail) to sign-extend before `slti`. The down-counters
  use `sll 16` + `bgez` without the `sra`, which is the correct idiom for "did the
  16-bit value go negative": at t=0, `0 - 1 = 0xFFFF`, `sll 16 = 0xFFFF0000`,
  `bgez` false ⇒ expiry. Verified at both `0x001f5c58` and `0x001f5c90`.
* **Wraparound at 65535.** Only the other-team increment (`0x001f5cc0`) is
  unbounded and it is untested and unstamped; at ≤~1800 frames per play it cannot
  reach 65535. No consumer wraps.
* **Wraparound at 0.** Handled by the two `bgez` sites; there is no `beq …, zero`
  underflow test anywhere that a 90 could skip past.
* **Negative stamp.** Site A masks (`0x001ef928 andi v1, v1, 0xffff`); site B does
  not, but `sh` truncates identically. `30 − rating/16` goes negative at an
  effective rating ≥ 496 and `90 − rating/16` at ≥ 1456 — so on the ratings scale
  the patch makes a negative (i.e. huge-u16, instant-expiry) stamp *strictly less*
  likely. `sll 16; sra 20` is an arithmetic shift, so a negative rating field would
  *raise* the stamp; unchanged in kind by the patch.
* Range check: with ratings 0..255, `sra 20` yields 0..15, so the stamp is exactly
  **75..90**, and the baseline exactly **15..30**, as the pnach claims.

## 7. Could not establish

1. **Whether `0x00260598`'s byte is possession or something else.** It is
   `[0x00601f4c]+64`, a 0/1 flag, and `0x002605b0` returns its complement. The
   whole offense-only argument in §2 rests on it, and on `0x001655b0(team, idx)`
   being a team-indexed player fetch. Both are consistent with every use seen, but
   neither is proven from a symbol. **If this flag flips mid-play** (turnover,
   fumble), a player stamped 75–90 would be re-classified onto the increment arm
   at `0x001f5cc0` while already ≥61 — harmless given no consumer tests a kind-4/8
   timer against 61, but not exercised here.
2. **What `0x001f82e8` returns** (the kind-3 ≥21 predicate) and what `+0x3EC`
   (`sw ..., 1004(s0)`, the threshold-11 payload) is used for downstream. Neither
   is on a reachable path for a stamped value, so it did not gate the verdict.
3. **Which exit ended the measured engagements** (§5) — statically undecidable.
4. **Computed-offset access.** A loop indexing the player struct with the offset in
   a register would evade all four sweeps. Same caveat `double-team-mechanism.md`
   §5.4 records; not ruled out.
5. **`jalr`/non-`ra` `jr` targets** inside the readers' functions were not
   individually resolved. Mitigated only by the fact that no `+0x432` access
   exists outside the 25 sites found, so an indirect call would have to reach one
   of the six known functions.
6. **Site B's live firing** (three-body chains) is still unconfirmed — inherited
   unproven from `double-team-mechanism.md` §5.7, not re-derived here. It affects
   how often 75–90 is stamped, not whether any threshold flips.
7. `docs/review-2026-08-11.md` D5's table conflates two gates: the **≥61** is
   kind 7 only and the **≥21** is kind 3 only; they are not a shared "kinds 3, 7"
   pair. Flagged here rather than fixed — that file is another lane's.

---

**VERDICT: NO-NEW-THRESHOLDS** — 13 readers, closed set; 3 can observe 75–90 and
all 3 compare it identically to 15–30; the 4 threshold comparisons in the image
are each gated on kind 3 or kind 7, and the patch cannot place a stamped value in
either kind. Confidence: high on the census and on the kind gates (both
branch-swept); medium on §7.1 (the possession flag), which is the single
assumption the offense-only lemma rests on.

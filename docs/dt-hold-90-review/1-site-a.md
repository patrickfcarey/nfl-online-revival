# Lane 1 — hostile review of Site A (`0x001ef918`, `addiu v1, zero, 30` → 90)

Static only. ELF `extract/SLUS_207.52`, vaddr = file_offset + 0xFF000
(checked: `offset_of(0x001ef918) = 0xf0918`, and `0x001ef918 - 0xFF000 = 0xf0918`).
`gp = 0x006056F0`. All listings below are `recon/mipsdis.py` output on that file,
re-derived this run — nothing quoted from a prior document.

**VERDICT: PATCH-SOUND.** The claim survives every attack in the brief. Two
statements *in the pnach comment* are wrong as written and are corrected below;
neither changes the outcome. One pre-existing effectiveness ceiling is named.

---

## 0. The patch words, re-derived

```
  001ef8e8 = 2403001e  addiu v1, zero, 30      <- kind 2-4 copy, NOT patched
  001ef918 = 2403001e  addiu v1, zero, 30      <- patched
  001f2108 = 2414001e  addiu s4, zero, 30      <- Site B (out of this lane)
  patched word 2403005a -> addiu v1, zero, 90
```

Both `addiu ..., zero, 30` sites in fn `0x001ef820` are accounted for: a full
v1-writer sweep of `0x001ef820`-`0x001efb1c` returns exactly `0x001ef8e8` and
`0x001ef918` for the immediate 30. There is no third copy.

---

## 1. The band split — attack: "does any path through 0x001ef918 lock in an
## up-counting kind?"  Answer: NO. The band is exactly {7,8} in, and 8 out.

The dispatch is a four-test unsigned ladder on `a1 = lw 992(s0)` (`+0x3E0`,
the *current* kind), read at `0x001ef890`:

```
001ef890  8e0503e0  lw a1, 992(s0)              ; current kind
001ef894  2ca20002  sltiu v0, a1, 2
001ef898  1440002b  bne v0, zero, 0x001ef948    ; kind 0,1  -> leave
001ef89c  00a0182d  daddu v1, a1, zero
001ef8a0  2ca20005  sltiu v0, a1, 5
001ef8a4  5440000c  bnel v0, zero, 0x001ef8d8   ; kind 2,3,4 -> UNPATCHED copy
001ef8a8  8e0303f0  lw v1, 1008(s0)
001ef8ac  2ca20009  sltiu v0, a1, 9
001ef8b0  10400025  beq v0, zero, 0x001ef948    ; kind >=9  -> leave
001ef8b4  2ca20007  sltiu v0, a1, 7
001ef8b8  14400059  bne v0, zero, 0x001efa20    ; kind 5,6  -> leave
001ef8bc  0060902d  daddu s2, v1, zero
001ef8c0  8e0303f0  lw v1, 1008(s0)             ; residue: kind 7 or 8 ONLY
001ef8c4  24020001  addiu v0, zero, 1
001ef8c8  54620013  bnel v1, v0, 0x001ef918     ;   +0x3F0 != 1 -> +0xB88
001ef8cc  96020b88  lhu v0, 2952(s0)
001ef8d0  10000011  beq zero, zero, 0x001ef918
001ef8d4  96020b86  lhu v0, 2950(s0)            ;   +0x3F0 == 1 -> +0xB86
```

Residue after the four tests is exactly `{7, 8}`. Both remaining edges land on
`0x001ef918`.

**Entry census for `0x001ef918`.** An image-wide branch/jump-target scan over
every loadable word finds exactly two edges into it — `0x001ef8c8` and
`0x001ef8d0`, both above. There is no fall-through, because `0x001ef910` is
unconditional:

```
001ef910  10000046  beq zero, zero, 0x001efa2c
001ef914  8e0503e0  lw a1, 992(s0)              ; delay slot
001ef918  2403001e  addiu v1, zero, 30
```

and an image-wide scan for data words equal to any address in the function
returns only the seven jump-table entries at `0x00583340`-`0x00583358`
(`001efa1c 001ef9b8 001ef984 001efa1c 001efa1c 001efa1c 001ef9b0`) — none of
which is `0x001ef918`.

**What the block sets the new kind to.** One value, unconditionally:

```
001ef918  2403001e  addiu v1, zero, 30
001ef91c  00021400  sll v0, v0, 16
001ef920  00021503  sra v0, v0, 20              ; (s16)rating >> 4
001ef924  00621823  subu v1, v1, v0             ; 30 - rating/16
001ef928  3063ffff  andi v1, v1, 0xffff
001ef92c  0060a02d  daddu s4, v1, zero
001ef930  8e3303e0  lw s3, 992(s1)              ; partner's own kind, read back
001ef934  0200202d  daddu a0, s0, zero
001ef938  0c07d764  jal 0x001f5d90
001ef93c  24120008  addiu s2, zero, 8           ; delay slot: NEW KIND := 8
001ef940  1000003a  beq zero, zero, 0x001efa2c
001ef944  8e0503e0  lw a1, 992(s0)
```

`addiu s2, zero, 8` sits in the delay slot of a `jal`, so it always executes.
No branch leaves the block. Therefore **the only kind ever locked in through the
patched word is 8** — and 8 is a down-counter (§4). The kill shot the brief was
hunting for does not exist.

The mirror block is equally tight; the unpatched copy sets kind 4 only:

```
001ef8e8  2403001e  addiu v1, zero, 30          ; NOT patched
001ef8fc  0060a02d  daddu s4, v1, zero
001ef908  24120004  addiu s2, zero, 4           ; new kind := 4
001ef90c  24130004  addiu s3, zero, 4
```

Entry to `0x001ef8e8` is likewise two edges only (`0x001ef8dc` taken-bnel, plus
fall-through from `0x001ef8e4`), both inside the kind-2-4 arm. **Kinds 3 and 4
keep 30 − rating/16.** Claim holds.

**The lock-in guard.** The stamp only lands when the kind actually changes:

```
001efa2c  10b2001d  beq a1, s2, 0x001efaa4      ; unchanged -> no write at all
001efa30  24030001  addiu v1, zero, 1
001efa34  a6140432  sh s4, 1074(s0)             ; +0x432 := s4
001efa38  ae1203e0  sw s2, 992(s0)              ; +0x3E0 := new kind
```

So through the patched word the only transition that writes is **7 → 8**
(8 → 8 is filtered by `beq a1, s2`). Exactly as claimed.

---

## 2. Where kind 7's timer is initialised — the claim's *conclusion* is right,
## its *wording* is wrong, and the wording is the dangerous part

Kind 7 is set at one place in this function, and it is on the **other** arm of
the very first branch:

```
001ef888  10400031  beq v0, zero, 0x001ef950    ; 0x001f7590(player) == 0
001ef88c  0000a02d  daddu s4, zero, zero        ; delay slot: s4 := 0  (ALWAYS)
...
001ef950  0c07d762  jal 0x001f5d88
001ef95c  24a3fffe  addiu v1, a1, -2
001ef960  2c620007  sltiu v0, v1, 7             ; kind 2..8 only
001ef970  27c33340  addiu v1, fp, 13120         ; table @ 0x00583340 (fp=0x0058_0000)
001ef97c  00800008  jr a0
...
001ef9b0  1000001d  beq zero, zero, 0x001efa28
001ef9b4  24120007  addiu s2, zero, 7           ; table idx 6 (kind 8) -> NEW KIND 7
```

Table decode, verified against the seven data words at `0x00583340`:
kind 2→`001efa1c`(no change), 3→`001ef9b8`, 4→`001ef984`(→2), 5,6,7→`001efa1c`
(no change), **8→`001ef9b0` (→7)**.

A full s4-writer sweep of the function returns exactly four sites:

```
  001ef88c  0000a02d  daddu s4, zero, zero      ; zero-idiom, invisible to addiu scans
  001ef8fc  0060a02d  daddu s4, v1, zero        ; kind 2-4 copy
  001ef92c  0060a02d  daddu s4, v1, zero        ; PATCHED copy
  001efb00  dfb40040  ld s4, 64(sp)             ; epilogue restore
```

`0x001ef8fc` and `0x001ef92c` both lie *after* the `beq` at `0x001ef888` on the
not-taken side, so they are unreachable once that branch is taken. **Every
kind-7 lock-in therefore stamps `+0x432 := 0`, and cannot ever stamp 90.**
D5's self-defeat hazard is structurally excluded, not merely unobserved.

> **Correction to the pnach comment.** It says *"Kind 7's up-counter (limit 61)
> is initialised elsewhere."* The **store** is not elsewhere — it is the same
> instruction, `0x001efa34 sh s4, 1074(s0)`. What differs is the **value
> source**: the zero-idiom at `0x001ef88c` rather than the patched
> `0x001ef918`. A future patcher who reads "elsewhere" and goes looking for a
> second `sh ..., 1074(...)` will find nothing and may conclude the note is
> wrong. Reword to: *"kind 7 reaches the same store carrying s4 = 0 from the
> zero-idiom at 0x001ef88c, on the `0x001f7590 == 0` arm."*

---

## 3. Other writers of kind 8 into `+0x3E0` — census, and one bounded ceiling

**(a) Complete store census, displacement 992.** Every `sb/sh/sw/sd` in the
image with immediate 992 was enumerated; discarding the 16 `sp`-relative
register saves leaves exactly eight:

| site | instruction | value written |
|---|---|---|
| `0x001e81ec` | `sw v0, 992(s0)` | **6** (`0x001e81e8 addiu v0, zero, 6`, delay slot of the guard `bne v1, 5`) |
| `0x001efa38` | `sw s2, 992(s0)` | Site A; s2 ∈ {1,2,4,**8**,7, current} |
| `0x001efab8` | `sw s3, 992(s1)` | partner write; s3 ∈ {0,4,9, partner's own kind} |
| `0x001f5cf4` | `sw v0, 992(s0)` | **2** (`0x001f5ce8 addiu v0, zero, 2`) |
| `0x001f73c4` | `sw s1, 992(s2)` | `a2` of setter `0x001f7398` |
| `0x001f7448` | `sw zero, 992(s1)` | 0 |
| `0x001f74fc` | `sw a2, 992(s1)` | `a2` of setter `0x001f74c8` |
| `0x001f7544` | `sw zero, 992(a0)` | 0 |

**(b) The zero-displacement blind spot is clean.** Every `addiu/daddiu rX, rY,
992` in the image (95 sites, `sp`-based excluded) was enumerated and each
scanned ±64 bytes for a store at displacement 0. The only hits are
`sw/sd ..., 0(sp)` register saves. No hidden `+0x3E0` writer through a biased
base.

**(c) The kind-setter helpers never carry 8.** All 45 callers of `0x001f7398`
and all 19 callers of `0x001f74c8` were enumerated with `find_jal_targets`, and
for each the last writer of `a2` before the call (delay slot included) was
resolved. The complete set of values passed is **{1, 2, 3, 5, 6, 7, 9}** plus
two register-carried cases (`0x001f5368 daddu a2, s1`, `0x001f539c daddu a2,
s2`). **Neither 4 nor 8 is ever passed as a literal.** And both helpers zero
the timer on the way through, so even a register-carried 8 could not deliver a
stale value:

```
001f73c4  ae5103e0  sw s1, 992(s2)    ; kind := a2      (s0 = s2+992)
001f73cc  a6000052  sh zero, 82(s0)   ; +0x3E0+0x52 = +0x432 := 0
...
001f74f0  a6000052  sh zero, 82(s0)   ; +0x432 := 0     (s0 = s1+992)
001f74fc  ae2603e0  sw a2, 992(s1)    ; kind := a2
```

**Conclusion for Q3: the only site in the image that writes kind 8 into
`+0x3E0` is `0x001efa38`, and it is the instruction immediately after the
patched-value store `0x001efa34`.** Every kind-8 lock-in therefore gets the
patched timer. There is no bypassing entry path.

**(d) The one bounded ceiling — `0x001efab8`, pre-existing, unchanged by the
patch.** The partner write-back can resurrect a kind 8 with a zero timer:

```
001efaa4  1220000d  beq s1, zero, 0x001efadc
001efaac  8e2203e0  lw v0, 992(s1)             ; partner's kind NOW
001efab0  1053000a  beq v0, s3, 0x001efadc     ; unchanged -> skip
001efab8  ae3303e0  sw s3, 992(s1)             ; else restore the stale kind
001efabc  a6200432  sh zero, 1074(s1)          ; and ZERO the partner's timer
```

If `s3` was read as 8 and something detached the partner in between, kind 8 is
written back with `+0x432 = 0`, which the down-counter breaks on its first tick
(§4). The only in-between call that can do that is the `s2 == 1` arm,
`0x001efa8c jal 0x001f7428` → `0x001f7540 sw zero, 992(a0)` /
`sh zero, 82(v0)`. **This path is unreachable from the 7→8 lock-in itself**:
with `s2 = 8`, `0x001efa40 beq s2, v1(=1)` and `0x001efa4c bne s2, v0(=4)` both
route straight to `0x001efaa4` with no intervening call, and
`0x001ef938 jal 0x001f5d90` is a stub (`0x001f5d90: jr ra / nop` — verified),
so `v0 == s3` always and no write occurs. The hazard is real but reachable only
while re-deciding a *different* (kind-3) player whose partner is a kind-8 man.
It exists identically at base 30. It caps how often the patch pays off; it
cannot make the patch worse than baseline.

---

## 4. Value flow `v1 → s4 → sh`, and the direction of the counter

**A read-sweep of the whole function (all 192 instructions, rs/rt decoded per
opcode class, zero-idioms included) finds `s4` read exactly twice:**

```
  001ef844  ffb40040  sd s4, 64(sp)     ; prologue save
  001efa34  a6140432  sh s4, 1074(s0)   ; THE store
```

No other consumer. Between `0x001ef92c daddu s4, v1, zero` and the store, the
only call is `jal 0x001f5d90` at `0x001ef938`, and that target is
`03e00008 / 00000000` — `jr ra; nop`, a stub that cannot clobber anything.
`v1` is likewise dead after `0x001ef92c`: the path
`0x001ef930 → 0x001ef944` contains no reader of v1, and its next writer is
`0x001efa30 addiu v1, zero, 1`. **The flow is single-producer,
single-consumer.** Q4 confirmed.

**Kind 8 is a down-counter; kind 7 is an up-counter with limit 61.** Re-derived
from the service routine — the dispatch value is `lw v1, 992(s0)` at
`0x001f5bcc`, i.e. the kind, not the timer:

```
001f5bcc  8e0303e0  lw v1, 992(s0)
001f5bd4  10620028  beq v1, v0(=4), 0x001f5c78     ; kind 4 -> down
001f5bdc  10400005  beq v0, zero, 0x001f5bf4       ; kind >=5 -> the 7/8 tests
001f5be4  5062003a  beql v1, v0(=3), 0x001f5cd0    ; kind 3 -> up, gate 21
001f5bf8  10620005  beq v1, v0(=7), 0x001f5c10     ; kind 7 -> UP
001f5c00  10620010  beq v1, v0(=8), 0x001f5c44     ; kind 8 -> DOWN

; kind 7 -- up, dies at >= 61
001f5c10  96030432  lhu v1, 1074(s0)
001f5c14  24630001  addiu v1, v1, 1
001f5c20  2842003d  slti v0, v0, 61
001f5c24  14400005  bne v0, zero, 0x001f5c3c      ; < 61 survives
001f5c34  0c07dce6  jal 0x001f7398                ; else DETACH (a2 = 1)

; kind 8 -- down, dies on underflow
001f5c44  1675001e  bne s3, s5, 0x001f5cc0        ; offence only (s5 = 0x00260598)
001f5c4c  96020432  lhu v0, 1074(s0)
001f5c50  2442ffff  addiu v0, v0, -1
001f5c54  00021c00  sll v1, v0, 16
001f5c58  0461002f  bgez v1, 0x001f5d18           ; still >= 0 -> survive
001f5c68  0c07dce6  jal 0x001f7398                ; else DETACH (a2 = 1)
```

90 on a kind-7 timer would give `91 >= 61` on the first tick — the D5 hazard,
exactly as described, and §2 shows it cannot arise. 90 on kind 8 gives ~91
service ticks before underflow. Claim holds.

---

## 5. 16-bit hygiene — the `andi`/`lhu`/`sh` triple is safe at 90, and *safer
## than at 30*

The brief's premise is inverted. `30 − rating/16` goes negative-as-u16 when
`rating > 480`; `90 − rating/16` needs `rating > 1440`. Raising the base
**widens** the safe band threefold. Three independent bounds on the field:

1. **Scale, from the writer.** The rebuild writes `+0xB88` from a byte:
   `0x00220354 lbu v1, 5(a0)` → `cvt.s.w` → `mul.s f0, f0, f1` → `cvt.w.s` →
   `0x00220370 sh v0, 2952(s0)`. `f1` is `lwc1 f1, -24844(gp)` = `0x005ff5e4` =
   `0x40233333` = **2.55**. Hard ceiling `255 × 2.55 = 650`, so
   `90 − 650/16 = 90 − 40 = 50` — still positive even at an impossible input.
2. **Live memory, measured this run.** Walking the descriptor
   (`[0x00600E48] = 0x00661b70` → `base = 0x00661b90, per_side = 11,
   total = 22`, stride `0x14C0`) through `extract/ee_inplay.bin`: all
   **462 halfwords of the `+0xB70` block are in 0..255** (max exactly 255,
   none ≥ 0x8000). Field-specific: `+0xB86` spans **38..229**, `+0xB88` spans
   **30..236** across the 22 players on the field.
3. **Resulting timer.** Measured worst case **76..89**; theoretical worst case
   over the full 0..255 scale **75..90**. Every value is a small positive
   `s16`, so `andi v1, v1, 0xffff` is a no-op, `sh` stores it verbatim, and the
   `lhu`/`sll 16`/`bgez` underflow test reads it back correctly. No wrap, no
   huge timer, no instant expiry.

Bonus, and it closes an open question in `docs/double-team-mechanism.md`
("which of +0xB86/+0xB88 is PRBK vs PPBK is not established"): the attribute
name table at `0x00520140` is `PACC PAGI PAWR PBTK PCAR PCTH PJMP PIMP PINJ
PKAC PKPR PPBK PRBK PSPD ...`, so index 11 = `+0xB70+22` = **+0xB86 = PPBK**
and index 12 = **+0xB88 = PRBK**. The selector `+0x3F0 == 1 → +0xB86` therefore
reads *pass* block when block_mode is 1 and *run* block otherwise — consistent
with `+0x3F0 == 2` being the run-block gate on DT registration.

---

## 6. Second correction to the pnach comment

> *"Watch the +0x432 reader at 0x001ca0e8 (`slti v0, v0, 61`): a 75-90 timer
> flips that gate for its first ~29 ticks, a path baseline timers never took."*

That reader is **unreachable for kind 8**. Thirty-six instructions earlier the
enclosing function (starts `0x001c9e24`, `s5 = a0`) gates on the kind:

```
001ca080  8ea303e0  lw v1, 992(s5)
001ca084  14620025  bne v1, v0(=3), 0x001ca11c   ; kind must be 3
...
001ca0e4  86a20432  lh v0, 1074(s5)
001ca0e8  2842003d  slti v0, v0, 61
```

Only a **kind-3** player reaches `0x001ca0e8`, and kind 3 is set exclusively
through the helpers (`a2 = 3` at `0x001b5784`, `0x001f4a78`, `0x001f59a0`,
`0x001f643c`, `0x001f6be4`), each of which zeroes `+0x432` first. Site A cannot
put 90 into a kind-3 player's timer. The warning is harmless but wrong, and
carrying it forward would waste a regression arm.

*(The slot-7 pass-protection regression arm is still justified on its own
terms — kind 8 does occur on pass plays, and `+0x3F0 == 1` selects PPBK there,
which the same patched formula scales. That part of the comment stands.)*

---

## 7. Full `+0x432` writer set (for the record)

Displacement-1074 scan (20 accesses, all `lh`/`lhu`/`sh`) plus the biased-base
form `sh zero, 82(base+992)`. The five other disp-82 halfword stores in the
image (`0x0015e8bc`, `0x0024c20c`, `0x0024c21c`, `0x0024c378`, `0x004871d0`)
were each checked in context and write a different struct (neighbouring offsets
52/54/62/78/80/84/92), not a player at `+992`.

| writer | value |
|---|---|
| `0x001efa34 sh s4, 1074(s0)` | Site A: `90 − rating/16` (kind 8) or `30 − rating/16` (kind 4) or **0** (kinds 1,2,7) |
| `0x001efabc sh zero, 1074(s1)` | 0, partner write-back |
| `0x001f2230 sh v0, 1074(a0)` | Site B (`30 − rating/16`, out of lane) |
| `0x001f5c28 / 5c5c / 5c94 / 5ccc / 5d04` | the service loop's own ±1 |
| `0x001f73cc / 744c / 74f0 / 7550 sh zero, 82(...)` | 0, on every kind-setter/detach |

---

## Could not establish (static analysis limits)

* **Semantics of the kind enum.** That 7 = "assigned as second man" and
  8 = "second man attached" is taken from `addresses.yaml`, not re-derived. The
  patch's *mechanical* correctness does not depend on it; its *usefulness*
  does.
* **How often the `0x001efab8` resurrection path (§3d) fires.** It needs a
  kind-3 re-decision whose partner is a live kind 8, on the same frame. Static
  analysis bounds the code path, not the rate. Only a run can.
* **Whether extending the kind-8 hold produces the intended on-screen effect.**
  Out of scope for static review; that is what the acceptance suite is for.
* **The `bne s3, s5` offence gate at `0x001f5c44`.** I read `s3` as the outer
  team-loop index and `s5` as `0x00260598`'s return (offense team) by
  construction within `0x001f5b60`; the meaning of `0x00260598`'s return value
  is carried from `docs/review-2026-08-11.md` row 6, **not re-derived here**.
* **Site B (`0x001f2108`) was not audited** — a different lane's claim. Its
  word was only read to confirm the pnach is internally consistent.
* **Cross-checks against a live emulator, PINE, or the rig: none.** No process
  was run. The one dynamic input used is the committed dump
  `extract/ee_inplay.bin`, read as a file.

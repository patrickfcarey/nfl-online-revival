# Double-team mechanism — static findings against SLUS_207.52

Recorded 2026-08-11. Static analysis only, on `extract/SLUS_207.52`
(vaddr = file_offset + 0xFF000, gp = 0x006056F0), using `recon.mipsdis`.
Every quoted instruction was read from the binary this pass; nothing below is
carried over from another document without re-derivation. Where a claim could
not be closed, it is in the final section, not silently assumed.

Answers the four questions posed after the R6 reframing in
`double-team-requirements.md`:

| # | question | answer in one line |
|---|---|---|
| 1 | where is `reselect_timer` (+0x432) written? | 8 direct `sh` sites + 4 indirect zeroing sites, all enumerated below; the two *initialisation* writes are `0x001efa34` and `0x001f2230` |
| 2 | is it really `30 − blockRating/16`? | **CONFIRMED**, at two independent sites (`0x001ef8e8`/`0x001ef918` feeding `0x001efa34`, and `0x001f2224` feeding `0x001f2230`); "/16" is `sll 16; sra 20` |
| 3 | which site writes `dt_role` 3, and what guards it? | `0x001f6730` writes 3; `0x001f68e0` writes **5**, not 3. The 3 is stamped on the blocker's **new** engagement partner *after* he has already left — peel-off is detected, never commanded |
| 4 | does re-selection consult the double team? | **No.** Census-grade negative: the re-selection closures (81 + 109 + 16 functions) contain zero reads of +0x436/+0x437. The only dt_role read on any scoring path is a single ×0.75 in one scorer (`0x001f421c`), whose consumption by the actual target choice is unproven |

The operator's observation — "it seems like the double team stops being
important to it super quickly, if ever" — is not a tuning problem. It is the
architecture: **the engine never decides to peel; it notices afterwards that it
peeled**, and the machinery that does the peeling (timer-driven re-selection)
never reads the double-team state at all.

---

## 0. The subsystem map (context for everything below)

**The double-team record table** lives at `0x00601280`, reached everywhere as
`lw rX, -17520(gp)`. Layout, derived from the registration and manage code:

| offset | meaning | evidence |
|---|---|---|
| +0x00..+0x53 | 4 records × 20 bytes, starting at +4: record base = `table + 4 + 20*i` | `0x001f6670-80`: `addiu v0, zero, 20; mult v0, s5, v0; addiu v0, v0, 4; addu s0, a0, v0` |
| record +0 | handle: PRIMARY blocker (role 0) | `0x001f646c` `sw v0, 0(s2)` after the role swap |
| record +4 | handle: HELPER blocker (role 1) | `0x001f6474` `sw v1, 4(s2)` |
| record +8 | handle: doubled DEFENDER (role 2) | `0x001f6480` `sw v0, 8(s2)` |
| record +12 | handle: PEEL-OFF target (role 3), empty until a blocker leaves | cleared at registration `0x001f647c` (`jal 0x0013b870` with a0=0, a1=rec+12); written at `0x001f66f4`/`0x001f6728` |
| record +16 | in-use byte | set to 1 at `0x001f63cc` `sb s5, 16(s2)`; tested at `0x001f6684`/`0x001f63c0` |
| table +84 | **per-play frame counter** | incremented once per frame at `0x001f5b9c` `sw v0, 84(v1)`; zeroed per play at `0x001f7034` `sw zero, 84(v1)` (only two writers, both verified against the gp-loaded base) |

**The per-frame block manager** is `0x001f7298` (callers: `jal` from
`0x00164fc4`, `j` from `0x0021c87c`), gated on `0x00154790()` returning 3 or 4.
Its call order fixes which pass sees whose writes:

```
001f72c8  jal 0x001f5b60   ; engagement-timer tick (the +0x432 inc/dec cluster)
001f72d0  jal 0x001f5590   ; pair scoring: -> 0x001f5510, 0x001f4790 (driver), 0x001f5158
001f72d8  jal 0x001f6d10   ; DOUBLE TEAMS: -> 0x001f64e0 (seek/register),
                           ;                 0x001f6640 (manage/peel detect),
                           ;               j 0x001f6940 (per-record drive)
001f72e0  jal 0x001ef820   ; re-decision stamp   (writes +0x432 @ 001efa34)
001f72e8  jal 0x001ef338
001f72f0  jal 0x001f0ba0
001f72f8  jal 0x001efc00   ; re-selection        (reads  +0x432 @ 001efd08)
...
001f733c  jal 0x001f20f8   ; kind-8 partner stamp (writes +0x432 @ 001f2230)
001f7344  jal 0x001f1c20
001f7350  j   0x001f5e80
```

**Tooling trap recorded for the next pass:** `find_jal_targets` on the
function *starts* returned NONE for the DT functions because each is preceded
by a `nop` pad — the real entries are +4 (`0x001f6338`, `0x001f64e0`,
`0x001f6640`). Verified callers: `0x001f6338` ← `0x001f657c`; `0x001f64e0` ←
`0x001f6d18`; `0x001f6640` ← `0x001f6d20`; `0x001f6940` ← `j` at `0x001f6d2c`.
A caller scan against the padded address concludes "dead code" and is wrong.

---

## 1. Every write to `reselect_timer` (+0x432, u16)

Census method: (a) exhaustive scan of all loadable words for `sh` (op 0x29)
with literal 0x432; (b) linear base+K scan (addiu-tracked bases, clobber
invalidation, 4096-byte window); (c) `find_field_refs` for the cross-call
form. The +0x430 sweep that returned nothing is explained: the field really is
at 0x432, and there are no 4-aligned accesses covering it.

### The 12 writes

| vaddr | instruction | function | what it writes |
|---|---|---|---|
| `0x001efa34` | `sh s4, 1074(s0)` | `0x001ef820` re-decision | **INIT: 30 − rating/16** on kind lock-in (see §2) |
| `0x001efabc` | `sh zero, 1074(s1)` | `0x001ef820` | zero the *partner's* timer when his kind is rewritten (`0x001efab8 sw s3, 992(s1)`) |
| `0x001f2230` | `sh v0, 1074(a0)` | `0x001f20f8` kind-8 stamp | **INIT: 30 − rating/16** (see §2) |
| `0x001f5c28` | `sh v1, 1074(s0)` | `0x001f5b60` tick | kind 7: increment (count-up) |
| `0x001f5c5c` | `sh v0, 1074(s0)` | `0x001f5b60` | kind 8: decrement |
| `0x001f5c94` | `sh v0, 1074(s0)` | `0x001f5b60` | kind 4: decrement |
| `0x001f5ccc` | `sh v0, 1074(s0)` | `0x001f5b60` | kinds 4/8 other-team path: increment |
| `0x001f5d04` | `sh v1, 1074(s0)` | `0x001f5b60` | kind 3 / default: increment |
| `0x001f73cc` | `sh zero, 82(s0)` (s0 = player+992) | `0x001f7398` kind-setter | zero on every kind change |
| `0x001f744c` | `sh zero, 82(s2)` (s2 = player+992) | `0x001f7428` teardown | zero on engagement teardown |
| `0x001f74f0` | `sh zero, 82(s0)` | `0x001f74c8` partner-kind setter | zero |
| `0x001f7550` | `sh zero, 82(v0)` (v0 = player+992) | `0x001f7540` | zero (reset helper, reached from `0x001f749c` and the `0x001f5350` region) |

Readers found by the same census: the tick cluster's own `lh`/`lhu`s,
`0x001efd08` `lh v0, 82(s4)` in re-selection `0x001efc00` (s4 = player+992,
gate: timer must be 0 for the rest of the function to run —
`0x001efd0c bnel v0, zero, 0x001eff30`), and `0x001ca0e4`
`lh v0, 1074(s5)` in the P2 assignment-drop test:

```
001ca0e4  86a20432  lh v0, 1074(s5)
001ca0e8  2842003d  slti v0, v0, 61
001ca0ec  1440000b  bne v0, zero, 0x001ca11c    ; timer < 61 -> skip the drop check
```

### Expiry behaviour (from the tick cluster, fn `0x001f5b60`)

Kind 4 (blocking engagement), own-team path — decrement, and on underflow drop
to **kind 2 with the partner reversed to kind 9**, exactly as
`lead-blocker-targeting.md` claimed:

```
001f5c84  96020432  lhu v0, 1074(s0)
001f5c88  2442ffff  addiu v0, v0, -1
001f5c8c  00021c00  sll v1, v0, 16
001f5c90  04610021  bgez v1, 0x001f5d18
001f5c94  a6020432  sh v0, 1074(s0)             ; store the decrement (delay slot)
001f5c98  0200202d  daddu a0, s0, zero
001f5c9c  0220282d  daddu a1, s1, zero
001f5ca0  0c07dce6  jal 0x001f7398              ; self  -> kind 2
001f5ca4  24060002  addiu a2, zero, 2
001f5ca8  0220202d  daddu a0, s1, zero
001f5cac  0200282d  daddu a1, s0, zero
001f5cb0  0c07dd32  jal 0x001f74c8              ; partner -> kind 9
001f5cb4  24060009  addiu a2, zero, 9
```

Kind 8 decrements to kind 1 on underflow (`0x001f5c4c-0x001f5c6c`); kind 7
counts *up* and converts to kind 1 at 61 (`0x001f5c10-0x001f5c38`).

---

## 2. The `30 − blockRating/16` formula — CONFIRMED at two sites

`addresses.yaml` / `lead-blocker-targeting.md` claim
`reselect_timer = 30 − blockRating/16`. **The formula is real.** Both
initialisation writes compute exactly it.

### Site A — re-decision fn `0x001ef820` (manager slot `0x001f72e0`)

The base 30 is an immediate; the division is an arithmetic shift right by 4 of
the sign-extended 16-bit rating; the rating field is selected by the player's
+0x3F0 word (1 → +0xB86, anything else → +0xB88). Two copies exist, one per
kind band (current kind 2–4 → new kind 4; current kind 7–8 → new kind 8):

```
001ef8c0  8e0303f0  lw v1, 1008(s0)             ; +0x3F0
001ef8c4  24020001  addiu v0, zero, 1
001ef8c8  54620013  bnel v1, v0, 0x001ef918     ; != 1 -> use +0xB88 (delay slot below)
001ef8cc  96020b88  lhu v0, 2952(s0)            ;   +0xB88
001ef8d0  10000011  beq zero, zero, 0x001ef918
001ef8d4  96020b86  lhu v0, 2950(s0)            ; == 1 -> +0xB86
...
001ef918  2403001e  addiu v1, zero, 30
001ef91c  00021400  sll v0, v0, 16
001ef920  00021503  sra v0, v0, 20              ; rating >> 4  (the "/16")
001ef924  00621823  subu v1, v1, v0             ; 30 - rating/16
001ef928  3063ffff  andi v1, v1, 0xffff
001ef92c  0060a02d  daddu s4, v1, zero
```

(the kind-2–4 copy is the identical sequence at `0x001ef8e8-0x001ef8fc`).
The value lands only when the newly decided kind differs from the current one
— i.e. at lock-in:

```
001efa2c  10b2001d  beq a1, s2, 0x001efaa4      ; kind unchanged -> no write
001efa30  24030001  addiu v1, zero, 1
001efa34  a6140432  sh s4, 1074(s0)             ; reselect_timer := 30 - rating/16
001efa38  ae1203e0  sw s2, 992(s0)              ; kind := new kind
001efa3c  a2000434  sb zero, 1076(s0)
001efa44  a2000435  sb zero, 1077(s0)
```

`s4` is otherwise only ever `daddu s4, zero, zero` (0x001ef88c — the zero
idiom, invisible to an `addiu`-only scan) or one of the two formula results;
the epilogue restore at `0x001efb00` is the only other writer. Verified by a
register-write sweep over the whole function.

### Site B — kind-8 partner stamp `0x001f20f8` (manager slot `0x001f733c`)

`s4` is loaded with 30 once in the prologue (`0x001f2108
addiu s4, zero, 30`; only other write is the epilogue `ld` — verified), and
the function walks all 11 offense players. For each player whose own kind is 8
it resolves `p2 = handle(+0x3E4)`, then `p3 = handle(p2+0x3E4)`, and if p3's
kind is 4, stamps **p3**:

```
001f21ec  0c04ede6  jal 0x0013b798              ; resolve p2->+0x3E4  -> a0 = p3
001f21f0  264403e4  addiu a0, s2, 996
001f21f4  0040202d  daddu a0, v0, zero
001f21f8  24060001  addiu a2, zero, 1
001f21fc  8c8303e0  lw v1, 992(a0)              ; p3 kind
001f2200  24020004  addiu v0, zero, 4
001f2204  1462000b  bne v1, v0, 0x001f2234      ; must be kind 4
001f2208  24050001  addiu a1, zero, 1
001f220c  8c8203f0  lw v0, 1008(a0)             ; p3 +0x3F0
001f2210  14450003  bne v0, a1, 0x001f2220
001f2214  a086042e  sb a2, 1070(a0)             ; +0x42E := 1 (delay slot, both paths)
001f2218  10000002  beq zero, zero, 0x001f2224
001f221c  94820b86  lhu v0, 2950(a0)            ; +0x3F0==1 -> +0xB86
001f2220  94820b88  lhu v0, 2952(a0)            ; else     -> +0xB88
001f2224  00021400  sll v0, v0, 16
001f2228  00021503  sra v0, v0, 20              ; /16
001f222c  02821023  subu v0, s4, v0             ; 30 - rating/16
001f2230  a4820432  sh v0, 1074(a0)             ; p3->reselect_timer
```

The same function is where the one-shot `+0x42E` is armed and where the
−0.13 contest debuff constant is loaded (`0x001f2128
lwc1 f20, -26040(gp)` = `0x005ff138` = −0.13, its sole reader — both
re-verified) and applied to the partner via two `0x001f79c0` calls.

### Consequences, now instruction-backed

* The timer is **computed per player**: three different blockers can hold
  13/17/30 simultaneously. The hunt for a literal was structurally doomed.
* **Better blockers re-select sooner** — a higher rating subtracts more. The
  quirk flagged in the requirements doc is real and lives in these two `subu`s.
* One caveat on naming: at the roster-load site both rating fields are filled
  from the *same* source halfword (`0x0021bc38 lhu v1, 0(s4)` →
  `sh v1, 2950(s0)`; `0x0021bc40 lhu v0, 0(s4)` → `sh v0, 2952(s0)`), though
  other writers exist (`0x00220370`, `0x00221d4c` for +0xB88). Which of
  +0xB86/+0xB88 is PRBK vs PPBK is not established here; only the selection by
  +0x3F0 is.

---

## 3. The peel-off write: `0x001f6730` writes 3 — and it is a post-hoc label

Both candidate sites live in the DT **manage** function `0x001f6640`
(manager → `0x001f6d10` → `jal 0x001f6640` at `0x001f6d20`), which loops over
the 4 records each frame. Verdict:

* **`0x001f6730` writes 3** — `0x001f672c addiu v0, zero, 3` immediately
  precedes it, and a branch-target scan over the whole image shows the only
  way into `0x001f672c` is the `bnel` at `0x001f66f0`; nothing jumps into the
  middle of the sequence.
* **`0x001f68e0` does NOT write 3.** Its value is set at `0x001f68d8
  addiu v0, zero, 5` — it writes **5 (UNASSIGNED)**. It is the cleanup that
  un-labels a peel-off target when the record's invariants break.

### The guard, traced to source

The role-3 write runs only when ALL of the following hold (quoted in order):

```
001f66b8  0c04ede6  jal 0x0013b798              ; resolve record+12
001f66bc  0280202d  daddu a0, s4, zero          ;   (s4 = record+12, set @001f6698)
001f66c0  1440001d  bne v0, zero, 0x001f6738    ; G1: peel slot must be EMPTY
...
001f66f8  0c04ede6  jal 0x0013b798              ; resolve helper's +0x3E4
001f66fc  264403e4  addiu a0, s2, 996
001f6700  0040202d  daddu a0, v0, zero
001f6704  1080000c  beq a0, zero, 0x001f6738    ; G2: blocker must be ENGAGED to someone
001f6708  24020005  addiu v0, zero, 5
001f670c  90830437  lbu v1, 1079(a0)            ; that someone's dt_role
001f6710  14620009  bne v1, v0, 0x001f6738      ; G3: new partner's dt_role == 5 (unassigned)
001f6714  00000000  nop
001f6718  8c830000  lw v1, 0(a0)                ; new partner's handle
001f671c  8e020008  lw v0, 8(s0)                ; record's DEFENDER handle
001f6720  10620005  beq v1, v0, 0x001f6738      ; G4: new partner != the doubled defender
001f6724  00000000  nop
001f6728  ae03000c  sw v1, 12(s0)               ; record+12 := new partner
001f672c  24020003  addiu v0, zero, 3
001f6730  a0820437  sb v0, 1079(a0)             ; new partner's dt_role := 3
001f6734  a0950436  sb s5, 1078(a0)             ; new partner's dt_record := slot index
```

(the `0x001f66c8-0x001f66f4` block is the identical test for the *primary*,
record+0; its taken-`bnel` delay slot `sw v1, 12(s0)` at `0x001f66f4` feeds
the same `0x001f672c` tail.)

Read what that means: **the store target `a0` is not the peeling blocker — it
is the man the blocker is engaged to *now*.** The guard's engine-level source
is the player's `+0x3E4` engagement-partner handle, and the only writers of
that handle are the ordinary kind-setters (`0x001f7398` stores the partner
handle via `jal 0x0013b870` at `0x001f73dc` with a1 = player+0x3E4). So the
causal chain of a peel is:

1. `reselect_timer` expires (§1) or the re-decision fn changes the kind —
   both call `0x001f7398`/`0x001f74c8`, re-pointing `+0x3E4` away from the
   doubled defender. **Neither reads a single double-team byte.**
2. On a later frame, DT-manage notices the blocker's `+0x3E4` no longer
   matches record+8, and stamps role 3 on the *new* man (G1–G4 above).

There is no site anywhere that decides "peel off now" as a function of the
double team. `dt_role` 3 is bookkeeping after the fact. (What role 3 is *used*
for downstream: the manage fn's invariant checks at `0x001f674c-0x001f6798`
and the `0x001f68c4` cleanup; the seek filter's role==5 test keeps a role-3
man from being double-teamed himself while so marked.)

### The complete writer set for the role bytes (from the census)

| vaddr | write | value | function |
|---|---|---|---|
| `0x001f6484/88/8c` | `sb s4, 1078(...)` | slot idx → dt_record of primary/helper/defender | register `0x001f6338` |
| `0x001f6490` | `sb zero, 1079(s0)` | **0 primary** (the seeker, after the role swap at `0x001f6454-5c`) | register |
| `0x001f6494` | `sb s5, 1079(s1)` | **1 helper** (the defender's prior engagee) | register |
| `0x001f649c` | `sb s6, 1079(s3)` | **2 doubled defender** | register |
| `0x001f6600` | `sb s3, 1079(v0)` | **5** to all four record members | teardown `0x001f65b8` (callers `0x001f68f8`, `0x001f6b50`) |
| `0x001f6730` | `sb v0, 1079(a0)` | **3 peel-off**, on the new partner | manage `0x001f6640` |
| `0x001f6734` | `sb s5, 1078(a0)` | slot idx → new partner's dt_record | manage |
| `0x001f68e0` | `sb v0, 1079(a0)` | **5** — un-label a stale peel target | manage |
| `0x001f7088` | `sb zero, 1078(s0)` | 0 → dt_record, all 11 players | per-play reset `0x001f6ff0` |
| `0x001f7098` | `sb s4, 1079(s0)` | **5** → dt_role, all 11 players (`s4` set at `0x001f7014 addiu s4, zero, 5`) | per-play reset |

The per-play reset (9 callers, all play-start initialisers) proves the enum
from the initialisation side: every player starts each play with
`dt_role = 5`, `dt_record = 0` — exactly the live slot-8 observation. It also
means `dt_record == 0` is ambiguous (slot 0 vs never-assigned); the record's
+16 in-use byte is the ground truth.

Registration context worth carrying (all quoted from `0x001f6338` /
`0x001f64e0`): the seek filter requires the candidate's `dt_role == 5`
(`0x001f6550-54`), **`+0x3F0 == 2`** (`0x001f655c-60` — this is DT-1's
run-block-only gate, address confirmed), own kind ∈ {7,8}
(`0x001f6568-74`), and **table frame counter < 60** (`0x001f6518-20`) — double
teams can only *form* in the first ~second of a play. Registration further
requires geometry A→D←B (the defender's `+0x3E4` must point at a *different*
blocker), seeker kind == 7, other-blocker kind == 2, and two byte tests at
`+0xB04`: seeker == 4, other ∈ {5,9} (`0x001f63d4-0x001f63f4`). `+0xB04` is
**not** the position byte: role 0 is only ever written at `0x001f6490`, yet
the slot-9 run measured an RG (pos 8) holding role 0, which a position
interpretation would forbid. What +0xB04 is remains open.

---

## 4. Is the double team consulted during re-selection? No — census-grade

### The census of +0x436/+0x437 accesses

Four independent sweeps over every loadable word:

1. **Literal offsets, all load/store opcodes** (including lq/sq/ldl/ldr byte
   ranges): every player-plausible hit lies in `0x001f4210-0x001f7098` — the
   19 sites in §3's table plus three `lbu` readers (`0x001f4210`,
   `0x001f6388`, `0x001f6394`, `0x001f66dc`, `0x001f670c`). The remaining
   matches are `sd/ld rX, 1072(sp)` register saves, sp-based unaligned-store
   idioms (`0x004e6144 sdl a2, 1079(sp)`), and non-player bases — dismissed
   below.
2. **base+K indirect scan** (addiu-tracked, clobber-invalidated, 4096-byte
   window): one additional site, `0x001f6550 lbu v0, 87(v1)` with
   `v1 = player+992` — the seek filter's role==5 test.
3. **`find_field_refs` cross-call form**: extra hits at `0x003a86e4/f8/fc`,
   `0x003ab434` (base+1006, then float access at +72 → 1078) are impossible
   on a player record — `lwc1/swc1` at +0x436 is a misaligned word access and
   faults on the EE — so those bases are not player structs. `0x003d08f8`
   (base+976, `sdl` +39) does not reach the field. Residual risk stated in §5.
4. **Indirect-call closure**: the six functions containing every genuine
   access (`0x001f3a00`, `0x001f6338`, `0x001f64e0`, `0x001f65b8`,
   `0x001f6640`, `0x001f6ff0`) are reachable **only** by direct jal/j: none of
   the six entry addresses appears anywhere in the image as a data word, and
   no `lui/addiu` or `lui/ori` pair materialises any of them
   (`find_address_refs`, full-image). No function pointer to them exists, so
   no `jalr` can reach them.

### The re-selection paths, walked

BFS closure over the direct call graph (function bounds from `jr ra`
boundaries), from each of the three manager slots that own the timer:

| entry | role | functions in closure | +0x436/+0x437 accesses |
|---|---|---|---|
| `0x001efc00` (@`0x001f72f8`) | re-selection: gated on `reselect_timer == 0` (`0x001efd08`), rolls `0x002f9428` randoms, re-stamps kinds via `0x001f7398`/`0x001f74c8` | 81 | **none** |
| `0x001ef820` (@`0x001f72e0`) | re-decision stamp: computes the §2 formula, writes kinds and the timer | 109 | **none** |
| `0x001f20f8` (@`0x001f733c`) | kind-8 partner stamp | 16 | **none** |

Consistency check that the closure is real: it *does* include the kind-setters
that zero +0x432 (`0x001f7398` at call `0x001f007c`, etc.) — the +0x432 and
+0x436/7 site sets are disjoint exactly as the census says.

So when the timer expires and the helper picks a new man, **nothing in that
path knows the double team exists**. The pairing's only defences are the
timer itself (initialised DT-blind, §2) and the after-the-fact relabelling
(§3).

### The one DT-aware read in scoring — and its limit

The single `dt_role` read outside the DT bookkeeping functions is in scorer
`0x001f3a00` (reached per frame via `0x001f5590` → `0x001f4790` driver →
`0x001f4290` → `jal` at `0x001f4518`, selected when the pair's `+0x3F0` is 2):

```
001f4208  8e22005c  lw v0, 92(s1)               ; s1 = first scorer argument; +0x5C -> player
001f420c  24040001  addiu a0, zero, 1
001f4210  90430437  lbu v1, 1079(v0)            ; that player's dt_role
001f4214  14640004  bne v1, a0, 0x001f4228      ; == 1 (HELPER)?
001f4218  8cc20054  lw v0, 84(a2)               ;   (delay: table frame counter)
001f421c  3c013f40  lui at, 0x3f40              ; 0.75f
001f4220  44810000  mtc1 at, f0
001f4224  4600a502  mul.s f20, f20, f0          ; score *= 0.75
```

A 25% score haircut when the a0-side player holds role 1. Two honest limits:
which side of the pair the a0-entry is (blocker vs candidate) depends on list
construction in `0x001f5510` that was not traced, and **no path was
established from this score matrix to the target actually chosen by
`0x001efc00`**. So the strongest supportable statement is: *at most* one ×0.75
nudge in one scorer is DT-aware, and the code that performs re-targeting
reads none of it. The operator's "stops being important super quickly, if
ever" is the correct description of the instruction stream.

(Same scorer, for the record: `score -= 6.0 × distance` while the play clock
at table+84 is < 60 — `0x001f4228-0x001f4248` — the same 60-frame window that
bounds DT formation.)

---

## 5. What I could not establish

Unproven, and stated so a future pass does not inherit them as facts:

1. **Whether the `0x001f5590` score matrix feeds `0x001efc00`'s choice.** The
   matrix (with its ×0.75) is computed earlier in the same manager pass, but
   no consumer link was traced. If it does not feed it, the 0.75 is fully
   inert for re-selection; if it does, the DT's entire influence on targeting
   is that single 25% haircut. Either way the closures' negative stands.
2. **Which scorer argument is the blocker** in `0x001f3a00` (a0 vs a1). The
   ×0.75 fires on the a0-side entry's player; the list construction in
   `0x001f5510` was not walked, so "helper scoring alternatives is penalised"
   vs "candidates who are helpers score lower" is undecided.
3. **`jalr` targets inside the closures** (10 `jalr` + up to 8 non-ra `jr`
   sites, e.g. `0x001afba4`, `0x001b00f0`, `0x00468fc8`) were not individually
   resolved. Mitigated, not eliminated, by census item 4: no pointer to any
   dt-byte-accessing function exists anywhere in the image, so those indirect
   calls cannot reach the dt bytes; what they do reach was not enumerated.
4. **Computed-offset access**: a loop that walks the player struct with the
   offset in a register would evade every sweep above. No such pattern is
   known in this engine's player code, but it was not ruled out.
5. **`+0xB04`'s meaning** (registration wants seeker==4, partner∈{5,9};
   demonstrably not the position byte).
6. **Which of +0xB86/+0xB88 is PRBK vs PPBK**, and the meaning of
   `+0x3F0 == 1` vs `== 2` (run vs pass block). Only the selection logic is
   verified; the labels come from `block-cycle.md` and were not re-derived.
7. **Site B's pairing topology**: `0x001f20f8` stamps the timer on
   partner-of-partner of a kind-8 player when that man is kind 4. For a
   strictly mutual engagement that resolves back to the kind-8 player himself
   (kind 8 ≠ 4 → no write), so the write appears to serve three-body chains;
   live confirmation of when it actually fires was not done (static only).
8. **The `0x001f6940` per-record drive stage** (third DT pass) was only
   skimmed — it resolves all four record handles per frame and does
   clock-gated float work; its writes were not enumerated.
9. **Mode gate values**: `0x00154790()` ∈ {3,4} gates the whole manager, and
   {4,7} appears in the state-32 guard; the meaning of these mode codes is
   unverified here.

Everything in §§1–4 outside this list was read directly from the ELF this
session, with the stated scans as the method of record.

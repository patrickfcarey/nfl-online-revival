# The double-team registry's management path, annotated end to end

Recorded 2026-08-11. Static lane, medium effort, tightly scoped. Source:
`extract/SLUS_207.52` (vaddr = file offset + 0xFF000, gp = 0x006056F0), read
with `recon.mipsdis` this session. **Every quoted instruction below was dumped
from the ELF this pass**; both jump tables and the anim whitelist were read as
data words; branch-likely delay-slot semantics were applied everywhere (marked
`; likely` — the delay slot runs only when taken). No rig, no network, no
emulator, no commits. Claims that could not be closed are in §9, not assumed.

Scope: the per-frame wrapper `0x001f6d10` → seek `0x001f64e0` → registration
`0x001f6338` → manage `0x001f6640` → drive `0x001f6940`, the teardown fn
`0x001f65b8` and both of its callers, the record structure, the 60-frame
window, and a census of every other reader of the table.

**Corrections to prior documents established here** (each derived below):

| prior claim | correction |
|---|---|
| `3-consistency.md`: caller B tests "the defender's AI state 2 or 30" | it is the **peel-off man's** (record+12) state, not the doubled defender's (`0x001f6b00 lw v0, 764(s3)`, s3 = resolve(rec+12)) |
| `3-consistency.md`: invariant failures exit to `0x001f68c4/68c8`, "both of which reach `0x001f68f8`" | they reach teardown **only if the peel man is engaged to one of the two blockers**; otherwise the record survives with the peel label cleared (`0x001f68e0`) |
| `3-consistency.md`: "two pair-separation tests against 0x002E38E2" | they are **angle-alignment** tests: teardown when the angular difference is **≤ ~65°** (`beql v0, zero` on `slt s0, result`), not when a distance exceeds a bound |
| `double-team-mechanism.md` §3: registration "further requires … seeker kind == 7, other-blocker kind == 2, and two byte tests at +0xB04" | those four tests gate only the **role-swap branch**; on failure the record is **still created** (both paths fall into the fill at `0x001f6464`) |
| `double-team-mechanism.md` §0: "the record table lives at 0x00601280" | `0x00601280` is a **pointer slot** (.sdata, gp-17520). The table is a 92-byte heap block, tag `'blck'`, allocated once by `0x001f6fa0` |
| `double-team-mechanism.md` §5 item 8: drive stage "does clock-gated float work; its writes were not enumerated" | fully enumerated below: a hard **frame-61 lifetime cap**, one **dead** float-compare block, five break predicates, and a teardown tail that converts the double into a single block and formally hands the helper to the peel man |

---

## 0. Call order and allocation

Per-frame manager `0x001f7298` (mode `0x00154790()` ∈ {3,4}) slot `0x001f72d8`
calls the wrapper; order fixes what each stage sees:

```
  001f6d10  27bdfff0  addiu sp, sp, -16
  001f6d14  ffbf0000  sd ra, 0(sp)
  001f6d18  0c07d938  jal 0x001f64e0        ; 1) seek + register
  001f6d20  0c07d990  jal 0x001f6640        ; 2) manage: peel-detect, invariants, teardown A
  001f6d2c  0807da50  j   0x001f6940        ; 3) drive: break tests, teardown B, handoff
```

A record registered in (1), or a peel detected in (2), is visible to (3) **the
same frame**. A record torn down in (2) is invisible to (3) (in-use already 0).

The table is allocated once, 92 bytes, allocation tag `0x626c636b` = `'blck'`,
and the pointer stored to `0x00601280` by the allocator callee:

```
  001f6fa0  27bdffe0  addiu sp, sp, -32
  001f6fa4  3c08626c  lui t0, 0x626c
  001f6fac  0000202d  daddu a0, zero, zero
  001f6fb4  2785bb90  addiu a1, gp, -17520  ; a1 = &slot 0x00601280
  001f6fb8  2406005c  addiu a2, zero, 92    ; SIZE = 92 (0x5C)
  001f6fbc  0000382d  daddu a3, zero, zero
  001f6fc0  0c0e75b2  jal 0x0039d6c8        ; allocate + store pointer
  001f6fc4  3508636b  ori t0, t0, 0x636b    ; t0 = 'blck'
```

This is the **only** site in the image that takes the slot's address; there is
no `lui/addiu` materialisation of `0x00601280` anywhere (checked with
`find_address_refs` — empty), so every access is `lw rX, -17520(gp)`.

---

## 1. The structure (table and record), derived from every accessor

`T = *(0x00601280)`, 92 bytes:

| offset | size | meaning | writers | readers |
|---|---|---|---|---|
| T+0 | word | **handle: designated next-lead** — the man the pair-driver will stamp kind 3 (instead of 2) on his next re-engagement, then consume | via `0x001f7b20(player-or-0)` (a `j 0x0013b870` with `a1 = T` in the delay slot). Callers: `0x001b6070` (designate, fn `0x001b5f20`, state-32 chooser region), `0x001f4a80` (consume/clear, in `0x001f4790`'s tail), `0x001f7030` (clear, per-play reset) | `0x001f4a68` (`0x001f4790` tail: `== victim's +0x5C handle` → kind 3 + clear, else kind 2) |
| T+4+20·i, i=0..3 | 4 × 20 B | the four records (below) | registration / manage / teardown / reset-memset **only** | manage / drive **only** |
| T+84 | word | per-play frame counter | `+1` each manager frame at `0x001f5b9c` (tick `0x001f5b60`, unconditional); `:=0` at `0x001f7034` (reset) | seek `<60`; drive `<61`; predicate `0x001f7b28` `<60`; getter `0x001f7360` (17 jal callers); ~14 direct reads in the scoring/kind cluster (thresholds 60, 46, 46, 31, 30, 180, 180, 20, 4, ==0; §7) |
| T+88 | byte | "inside the pair-scoring phase" flag | `:=1` at `0x001f5628` (entry of scoring phase `0x001f55e8`), `:=0` at `0x001f5b54` (its epilogue) | `0x001f2dd8`, `0x001f2e5c` (fns `0x001f2cd8`/`0x001f2830`, called from inside the phase) |
| T+89 | byte | once-per-play latch for T+90 recompute | `:=1` at `0x001f71b0` (play-start pass), `:=0` at `0x001f62a0` (fn `0x001f5f58`, when its ±metric fires) and `0x001f71d4` | `0x001f62bc` (gates the T+90 write) |
| T+90 | byte | push/flow direction ∈ {0,1,2} | `0x001f62f8` (1 if metric>0 else 2), `0x001f6f6c/78/7c` (fn `0x001f6d38`: 1/2 by float compare, 0 on gate fail), `0x001f702c` (0, reset) | `0x001f4f4c/0x001f4fb0` (helper scorer `0x001f4c40`), `0x001f5784-90` (scoring geometry select) |
| T+91 | byte | no accessor found — pad | — | — |

Record `R = T + 4 + 20·slot`:

| offset | size | meaning | written at (life cycle) |
|---|---|---|---|
| R+0 | word | handle, **PRIMARY** blocker (role 0) | `0x001f646c` (birth) |
| R+4 | word | handle, **HELPER** blocker (role 1) | `0x001f6474` (birth) |
| R+8 | word | handle, doubled **DEFENDER** (role 2) | `0x001f6480` (birth) |
| R+12 | word | handle, **PEEL-OFF TARGET** (role 3); 0 until a peel is noticed | `:=0` at birth (`0x001f647c`), `:=new man` at `0x001f66f4`/`0x001f6728` (manage), `:=0` again at `0x001f68e4` (stale-peel un-label) and in teardown |
| R+16 | byte | in-use (1/0) | `:=1` at `0x001f63cc` (birth, before the swap tests), `:=0` at `0x001f65dc` (teardown) |
| R+17..19 | — | no accessor found — pad | — |

All four member fields hold **handles** (packed type/index words), resolved to
player pointers by `0x0013b798` at every use and written by `0x0013b870`
(`*(a1) := a0 ? *(a0+0) : 0`).

Player-side fields the path touches: `+0x3E0` kind, `+0x3E4` partner handle,
`+0x3E8` first-partner handle (set only when zero), `+0x432` reselect timer,
`+0x436` dt_record (slot idx), `+0x437` dt_role (0/1/2/3, 5 = unassigned),
`+0x2FC` → AI-state block (state byte at +0), `+0x304` → anim context
(current anim id via `0x003ad410`), `+0x190/+0x194` position floats,
`+0x1A8/+0x1B0` angle words on a 0x01000000 = full-circle scale (proven by
`0x00469fc8`, §5), `+0xC` flags word (bit 0x4000 tested), `+0xB04` byte
(meaning still open).

**Per-play reset `0x001f6ff0` clears everything.** Head, quoted:

```
  001f6ff0  8f84bb90  lw a0, -17520(gp)     ; T
  001f7004  24060050  addiu a2, zero, 80
  001f700c  24840004  addiu a0, a0, 4
  001f701c  0c12cfa2  jal 0x004b3e88        ; memset(T+4, 0, 80): ALL FOUR RECORDS
  001f7024  8f83bb90  lw v1, -17520(gp)
  001f7028  0000202d  daddu a0, zero, zero
  001f702c  a060005a  sb zero, 90(v1)       ; T+90 := 0
  001f7030  0c07dec8  jal 0x001f7b20        ; T+0 := null handle
  001f7034  ac600054  sw zero, 84(v1)       ; counter := 0 (delay slot)
  ...per player (11):
  001f706c  260403e0  addiu a0, s0, 992
  001f7070  0c12cfa2  jal 0x004b3e88        ; memset(player+0x3E0, 0, 88)
  001f7074  24060058  addiu a2, zero, 88    ;   (kind, partner, timer — all of it)
  001f7088  a2000436  sb zero, 1078(s0)     ; dt_record := 0
  001f7098  a2140437  sb s4, 1079(s0)       ; dt_role := 5
```

So **no record, no peel label, and no designation survives a play boundary**
— this closes the cross-play-staleness question the drive fn's frame cap
would otherwise raise.

---

## 2. Birth, for contrast: seek `0x001f64e0` and registration `0x001f6338`

### 2.1 The seek — who is even considered

```
  001f64e0  27bdffb0  addiu sp, sp, -80
  001f64f4  0c07e0ae  jal 0x001f82b8        ; GATE 1: context classifier
  001f64fc  10400027  beq v0, zero, 0x001f659c   ; == 0 -> no registration at all
  001f6504  0c07e1ae  jal 0x001f86b8        ; GATE 2: byte *(ptr@0x006012C8)+4
  001f650c  2c420006  sltiu v0, v0, 6
  001f6510  10400021  beq v0, zero, 0x001f6598   ; >= 6 -> out
  001f6514  8f83bb90  lw v1, -17520(gp)     ; T (delay slot)
  001f6518  8c620054  lw v0, 84(v1)         ; frame counter
  001f651c  2c42003c  sltiu v0, v0, 60
  001f6520  1040001e  beq v0, zero, 0x001f659c   ; GATE 3 (DT-2): counter >= 60 ->
  001f6524  dfbf0040  ld ra, 64(sp)         ;   FULL FUNCTION EXIT. Doubles only FORM
                                            ;   in the play's first 60 manager frames.
  001f6528  0c098166  jal 0x00260598        ; offense team id (byte+64 of ball obj)
  001f652c  0000802d  daddu s0, zero, zero  ; s0 = player index 0
  001f6534  24130005  addiu s3, zero, 5
  001f6538  24120002  addiu s2, zero, 2
loop (11 offense players):
  001f6540  0c05956c  jal 0x001655b0        ; player s0 of team s1
  001f6548  0040202d  daddu a0, v0, zero    ; a0 = candidate
  001f654c  248303e4  addiu v1, a0, 992
  001f6550  90620057  lbu v0, 87(v1)        ; +0x437 dt_role
  001f6554  1453000c  bne v0, s3, 0x001f6588     ; != 5 (already in a record) -> next
  001f655c  8c620010  lw v0, 16(v1)         ; +0x3F0
  001f6560  14520009  bne v0, s2, 0x001f6588     ; != 2 -> next   (DT-1's run-block gate)
  001f6568  8c8203e0  lw v0, 992(a0)        ; kind
  001f656c  2442fff9  addiu v0, v0, -7
  001f6570  2c420002  sltiu v0, v0, 2
  001f6574  10400004  beq v0, zero, 0x001f6588   ; kind not in {7,8} -> next
  001f657c  0c07d8ce  jal 0x001f6338        ; REGISTER (sole caller of 0x001f6338)
  001f6588  305000ff  andi s0, v0, 0x00ff   ; next player; NO early exit on success —
  001f658c  2e03000b  sltiu v1, s0, 11      ;   several records can form in one frame
```

### 2.2 Registration — what a record looks like at birth

`a0 = the seeker` (the kind-7/8, role-5, +0x3F0==2 candidate).

```
  001f6338  27bdff70  addiu sp, sp, -144
  001f6340  0080882d  daddu s1, a0, zero    ; s1 = seeker
  001f634c  262403e4  addiu a0, s1, 996
  001f6354  263703e0  addiu s7, s1, 992     ; s7 = &seeker.kind
  001f6368  0c04ede6  jal 0x0013b798        ; resolve seeker->+0x3E4
  001f6370  0040982d  daddu s3, v0, zero    ; s3 = THE DEFENDER he is attached to
  001f6374  0c04ede6  jal 0x0013b798        ; resolve defender->+0x3E4
  001f6378  266403e4  addiu a0, s3, 996
  001f637c  0040802d  daddu s0, v0, zero    ; s0 = the defender's engagee (other blocker)
  001f6380  1200004b  beq s0, zero, 0x001f64b0   ; REG-1: defender engaged to nobody -> out
  001f6384  24020005  addiu v0, zero, 5
  001f6388  92630437  lbu v1, 1079(s3)
  001f638c  14620049  bne v1, v0, 0x001f64b4     ; REG-2: defender's dt_role != 5 -> out
  001f6394  92020437  lbu v0, 1079(s0)
  001f6398  54430047  bnel v0, v1, 0x001f64b8    ; REG-3: other blocker's dt_role != 5 -> out
  001f63a0  8f84bb90  lw a0, -17520(gp)     ; T
  001f63a4  0000a02d  daddu s4, zero, zero  ; s4 = slot index
  001f63b0  24050014  addiu a1, zero, 20
find-free-slot:
  001f63b4  02851018  mult v0, s4, a1
  001f63b8  24420004  addiu v0, v0, 4
  001f63bc  00829021  addu s2, a0, v0       ; s2 = R = T + 4 + 20*s4
  001f63c0  92430010  lbu v1, 16(s2)
  001f63c4  14600036  bne v1, zero, 0x001f64a0   ; in use -> next slot (REG-4: all 4 busy -> out)
  001f63cc  a2550010  sb s5, 16(s2)         ; CLAIM: in-use := 1  (before any further test)
```

The next four tests choose **who becomes primary** — they do NOT gate
registration. Failure of any of them branches (`bnel … ; likely`, delay
`lw v0, 0(s0)`) straight to the fill at `0x001f6464`:

```
  001f63d0  24030004  addiu v1, zero, 4
  001f63d4  92220b04  lbu v0, 2820(s1)      ; SWAP-1: seeker +0xB04 == 4 ?
  001f63d8  54430022  bnel v0, v1, 0x001f6464    ; no -> fill (no swap)
  001f63e0  92030b04  lbu v1, 2820(s0)      ; SWAP-2: other +0xB04 in {5,9} ?
  001f63e8  10620003  beq v1, v0, 0x001f63f8     ; (v0=5)
  001f63f0  5462001c  bnel v1, v0, 0x001f6464    ; (v0=9) no -> fill
  001f63f8  8ee30000  lw v1, 0(s7)          ; SWAP-3: seeker kind == 7 exactly ?
  001f63fc  24020007  addiu v0, zero, 7
  001f6400  54620018  bnel v1, v0, 0x001f6464    ; no (i.e. kind 8) -> fill
  001f6408  8e0203e0  lw v0, 992(s0)        ; SWAP-4: other blocker kind == 2 ?
  001f640c  54560015  bnel v0, s6, 0x001f6464    ; no -> fill
the swap (all four hold): the seeker TAKES OVER the block —
  001f6414  0c07dd0a  jal 0x001f7428        ; disengage other blocker
  001f6424  0c07dce6  jal 0x001f7398        ; other blocker := kind 7 on the defender
  001f6428  24060007  addiu a2, zero, 7
  001f642c  0c07dd0a  jal 0x001f7428        ; disengage seeker
  001f643c  0c07dce6  jal 0x001f7398        ; seeker := kind 3 on the defender
  001f6440  24060003  addiu a2, zero, 3
  001f644c  0c07dd32  jal 0x001f74c8        ; defender := kind 9, partner := seeker
  001f6450  24060009  addiu a2, zero, 9
  001f6454  0220102d  daddu v0, s1, zero    ; swap s0 <-> s1: the seeker will be filed
  001f6458  0200882d  daddu s1, s0, zero    ;   as PRIMARY, the old man as HELPER
  001f645c  0040802d  daddu s0, v0, zero
  001f6460  8e020000  lw v0, 0(s0)
fill (both paths):
  001f6464  2645000c  addiu a1, s2, 12
  001f6468  0000202d  daddu a0, zero, zero
  001f646c  ae420000  sw v0, 0(s2)          ; R+0  := primary   (swap: seeker; else: other man)
  001f6470  8e230000  lw v1, 0(s1)
  001f6474  ae430004  sw v1, 4(s2)          ; R+4  := helper    (swap: other man; else: seeker)
  001f6478  8e620000  lw v0, 0(s3)
  001f647c  0c04ee1c  jal 0x0013b870        ; R+12 := 0 (a0=0)
  001f6480  ae420008  sw v0, 8(s2)          ; R+8  := defender  (delay slot)
  001f6484  a2140436  sb s4, 1078(s0)       ; all three: dt_record := slot
  001f6488  a2340436  sb s4, 1078(s1)
  001f648c  a2740436  sb s4, 1078(s3)
  001f6490  a2000437  sb zero, 1079(s0)     ; primary  dt_role := 0
  001f6494  a2350437  sb s5, 1079(s1)       ; helper   dt_role := 1
  001f649c  a2760437  sb s6, 1079(s3)       ; defender dt_role := 2 (delay slot)
```

**A record at birth**: in-use=1, three member handles, empty peel slot, three
players stamped record+role. On the swap path the engine also rewrites kinds
(arriving man 3-on-defender as primary, old man 7 as helper, defender
9-with-primary); on the no-swap path **kinds are untouched** — the registry
records the pair exactly as the ordinary engagement engine had it.

Structural note (static): nothing checks `s0 != s1`. If the defender's
`+0x3E4` pointed back at the seeker himself, a degenerate record
(primary == helper == seeker, final role 1) would form. On slot-9 geometry the
defender points at the *first* blocker while the seeker is the kind-7 second
man, so the measured records are non-degenerate — but the guard's absence is
worth knowing. UNVERIFIED that the degenerate case ever occurs live.

---

## 3. Manage `0x001f6640` — peel detection, invariants, teardown caller A

Loop registers: s5 = slot 0..3, s0 = R, s6 = 0x00580000 (jump-table base),
s4 = &R+12. Resolved members: s1 = primary, s2 = helper, s3 = defender.

### 3.1 Skip and resolve

```
  001f6670  24020014  addiu v0, zero, 20
  001f6678  02a21018  mult v0, s5, v0
  001f667c  24420004  addiu v0, v0, 4
  001f6680  00828021  addu s0, a0, v0       ; R
  001f6684  92030010  lbu v1, 16(s0)
  001f6688  1060009f  beq v1, zero, 0x001f6908   ; not in use -> next slot
  001f6690  0200202d  daddu a0, s0, zero
  001f6694  0c04ede6  jal 0x0013b798        ; s1 = resolve(R+0)  PRIMARY
  001f6698  2614000c  addiu s4, s0, 12
  001f66a0  0c04ede6  jal 0x0013b798        ; s2 = resolve(R+4)  HELPER
  001f66ac  0c04ede6  jal 0x0013b798        ; s3 = resolve(R+8)  DEFENDER
  001f66b8  0c04ede6  jal 0x0013b798        ; resolve(R+12) PEEL
  001f66c0  1440001d  bne v0, zero, 0x001f6738   ; peel already known -> invariants
```

### 3.2 Peel detection (only while R+12 is empty) — the role-3 stamp

Primary first; the helper is only examined if the primary check "passes"
(i.e. he is still on the defender, or unengaged, or his new man is filtered):

```
  001f66c8  0c04ede6  jal 0x0013b798        ; a0 := resolve(primary->+0x3E4)
  001f66d4  10800008  beq a0, zero, 0x001f66f8   ; P-G2: not engaged -> check helper
  001f66d8  24020005  addiu v0, zero, 5
  001f66dc  90830437  lbu v1, 1079(a0)
  001f66e0  14620005  bne v1, v0, 0x001f66f8     ; P-G3: new man's dt_role != 5 -> check helper
  001f66e8  8c830000  lw v1, 0(a0)          ; new man's handle
  001f66ec  8e020008  lw v0, 8(s0)          ; defender handle
  001f66f0  5462000e  bnel v1, v0, 0x001f672c    ; P-G4: != defender -> PEEL!
  001f66f4  ae03000c  sw v1, 12(s0)         ;   (delay, taken only) R+12 := new man
  001f66f8  0c04ede6  jal 0x0013b798        ; a0 := resolve(helper->+0x3E4)
  001f66fc  264403e4  addiu a0, s2, 996
  001f6704  1080000c  beq a0, zero, 0x001f6738   ; H-G2
  001f6708  24020005  addiu v0, zero, 5
  001f670c  90830437  lbu v1, 1079(a0)
  001f6710  14620009  bne v1, v0, 0x001f6738     ; H-G3
  001f6718  8c830000  lw v1, 0(a0)
  001f671c  8e020008  lw v0, 8(s0)
  001f6720  10620005  beq v1, v0, 0x001f6738     ; H-G4: still the defender -> no peel
  001f6728  ae03000c  sw v1, 12(s0)         ; R+12 := helper's new man
stamp (shared tail; a0 = the NEW MAN, not the peeling blocker):
  001f672c  24020003  addiu v0, zero, 3
  001f6730  a0820437  sb v0, 1079(a0)       ; new man's dt_role := 3
  001f6734  a0950436  sb s5, 1078(a0)       ; new man's dt_record := slot
```

The primary has detection priority: if his peel fires, the helper's check is
skipped that frame (branch lands past it). The stamp then **falls straight
into 3.3** — a fresh peel is invariant-checked, and can kill the record, in
the same call.

### 3.3 The gate that shields peel-less records

```
  001f6738  0c04ede6  jal 0x0013b798        ; re-resolve R+12 -> a0 (fresh or old peel man)
  001f6744  5080006f  beql a0, zero, 0x001f6904  ; R+12 EMPTY -> NEXT RECORD.
```

**Everything below — pointer invariants, kind gates, maintenance, teardown
caller A — is unreachable while the peel slot is empty.** Manage cannot kill
a record that has never had a peel noticed. For such records, only the drive
fn's B0/B1/B3/B4 (§5) can end the window.

### 3.4 The four pointer invariants (evaluation order)

`a1` starts as the defender handle (`0x001f6750 lw a1, 8(s0)`), switches to
the primary handle at `0x001f6784`.

| # | test | pass route | fail route |
|---|---|---|---|
| I1 | primary→+0x3E4 == defender? (`0x001f6754 beql`) else == R+12? (`0x001f6760 bnel`) | on to I2 | neither → `0x001f68c4` with a1 = primary handle |
| I2 | helper→+0x3E4 == defender? (`0x001f676c`) else == R+12? (`0x001f6778`) | on to I3 | neither → `0x001f68c4` |
| I3 | defender→+0x3E4 == primary? (`0x001f6788`) else == helper? (`0x001f6794 bne`) | on to I4 | neither → `0x001f68c8` (peel man's partner already loaded by the always-run delay `0x001f6798 lw v1, 996(a0)`) |
| I4 | peel-man→+0x3E4 == primary? (`0x001f679c`) else == helper? (`0x001f67a8`) | on to kind gates | neither → `0x001f68c8` |

The consolidated exit asks **one question — is the peel man engaged to one of
my blockers?**

```
  001f68c4  8c8303e4  lw v1, 996(a0)        ; peel man's partner (entry for I1/I2 failures)
  001f68c8  5065000b  beql v1, a1, 0x001f68f8    ; == primary -> TEARDOWN
  001f68d0  8e020004  lw v0, 4(s0)
  001f68d4  10620007  beq v1, v0, 0x001f68f4     ; == helper  -> TEARDOWN
  001f68d8  24020005  addiu v0, zero, 5
  001f68dc  0280282d  daddu a1, s4, zero
  001f68e0  a0820437  sb v0, 1079(a0)       ; neither: peel man's dt_role := 5
  001f68e4  0c04ee1c  jal 0x0013b870        ;   R+12 := 0  (un-label, a0=0)
  001f68ec  10000005  beq zero, zero, 0x001f6904 ;   RECORD SURVIVES -> next
```

So: an invariant break kills the record **iff the peel man is still attached
to a blocker** (the block genuinely moved to the new man). If the peel link
evaporated (he is engaged elsewhere or to nobody), the peel label is dropped
and the record lives to detect a fresh peel next frame. By construction the
I4-failure route always takes the un-label branch — a peel man who leaves both
blockers merely loses his role 3.

### 3.5 Primary-kind gate and the anim whitelist

```
  001f67b4  2442fffe  addiu v0, v0, -2      ; v0 = primary kind - 2
  001f67b8  2c430007  sltiu v1, v0, 7
  001f67bc  1060000b  beq v1, zero, 0x001f67ec   ; kind not in 2..8 -> TEARDOWN
  001f67c4  26c33a80  addiu v1, s6, 14976   ; jump table 0x00583A80
  001f67d0  00800008  jr a0
```

Table `0x00583A80` (read as data): kinds 2,3,4,7,8 → `0x001f67f4` (straight to
the helper gate); kinds **5,6** → `0x001f67d8`:

```
  001f67d8  0220202d  daddu a0, s1, zero
  001f67dc  0c07c2ea  jal 0x001f0ba8        ; predicate(primary, defender)
  001f67e4  54400004  bnel v0, zero, 0x001f67f8  ; nonzero -> record survives to helper gate
  001f67e8  8e4403e0  lw a0, 992(s2)        ;   (delay: helper kind)
  001f67ec  10000042  beq zero, zero, 0x001f68f8 ; zero -> TEARDOWN
```

`0x001f0ba8(primary, defender)` returns 1 only when **all** of:
primary→+0x3E4 == defender's handle, defender→+0x3E4 == primary's handle
(mutual), both players' current anim id (via `0x003ad410` on `+0x304`) is the
**same**, and that id is in [146,173] with jump table `0x00583360` sending it
to the return-1 arm. The whitelist, read from the table:

> **anims 146–151, 168–170, 173 → record survives; 152–167, 171, 172 → teardown.**

So a primary in a two-man-animation kind keeps his record only while the pair
is mutually locked in the *same, whitelisted* two-man animation. (Which
animations those ids denote is not established statically — §9.)

### 3.6 The helper-kind gate — teardown caller A's last word

```
  001f67f4  8e4403e0  lw a0, 992(s2)        ; helper kind
  001f67f8  2c820002  sltiu v0, a0, 2
  001f67fc  5440003e  bnel v0, zero, 0x001f68f8  ; kind < 2 (0/1, disengaged) -> TEARDOWN
  001f6804  2c820005  sltiu v0, a0, 5
  001f6808  54400007  bnel v0, zero, 0x001f6828  ; kind 2..4 -> survive (maintenance)
  001f6810  2c820009  sltiu v0, a0, 9
  001f6814  1040fff5  beq v0, zero, 0x001f67ec   ; kind >= 9 -> TEARDOWN
  001f6818  2c820007  sltiu v0, a0, 7
  001f681c  54400036  bnel v0, zero, 0x001f68f8  ; kind 5 or 6 -> TEARDOWN
  001f6824  8e2303e0  lw v1, 992(s1)        ; kind 7/8 -> survive (delay: primary kind)
```

**Helper kind: {2,3,4,7,8} live; {<2, 5, 6, ≥9} die.** The 5/6 trigger — the
touch-abort prime suspect — is real, but remember 3.3: it can only fire on a
record whose R+12 is already populated.

### 3.7 Survivor maintenance (not teardown — the re-anchoring writes)

Reached only by surviving records (peel populated, invariants OK, kinds OK):

* Primary kind 5/6 → left alone (`0x001f6828-30` skips ahead).
* Primary kind 7/8, **or** kind 2..4 with his partner == the peel target
  (I1 allowed that): `0x001f6858-78` disengages him and re-stamps
  **kind 2 on the defender, defender kind 9 partner primary** — the primary is
  pulled back onto the doubled man.
* Helper kind 2..4 → `0x001f68a4-b8`: disengage, then **kind 7 on the
  defender** (`0x001f68b4` with `a2=7` — the census's "kind-7 write requiring
  an existing record"). A helper who converted to an ordinary block on the
  defender is pushed back into assist kind.
* Helper kind 7/8 whose partner == the peel target (`0x001f6894-9c`): same
  re-stamp to kind 7 **on the defender** — pulled back off the new man.

Then next record. Note the asymmetry: manage actively *defends* the pairing
against kind drift (2..4, 7..8), but treats 5/6 on the helper as fatal and
5/6 on the primary as fatal-unless-whitelisted-anim.

### 3.8 Teardown caller A

```
  001f68f8  0c07d96e  jal 0x001f65b8        ; a0 = R (set in each route's delay slot)
```

No conversion, no handoff: when manage kills a record it only calls the
teardown fn. (Contrast drive, §5.4.)

---

## 4. Teardown `0x001f65b8` — what dying actually changes

```
  001f65b8  27bdffb0  addiu sp, sp, -80
  001f65c4  0080902d  daddu s2, a0, zero    ; R
  001f65cc  24130005  addiu s3, zero, 5
  001f65d4  0000882d  daddu s1, zero, zero
  001f65dc  a2400010  sb zero, 16(s2)       ; in-use := 0
loop s1 = 0..3 (fields R+0, R+4, R+8, R+12):
  001f65e0  00118080  sll s0, s1, 2
  001f65e8  02508021  addu s0, s2, s0
  001f65ec  0c04ede6  jal 0x0013b798        ; resolve member handle
  001f65f4  0200282d  daddu a1, s0, zero
  001f65f8  10400002  beq v0, zero, 0x001f6604
  001f65fc  0000202d  daddu a0, zero, zero
  001f6600  a0530437  sb s3, 1079(v0)       ; member's dt_role := 5  (peel man included)
  001f6604  0c04ee1c  jal 0x0013b870        ; member handle slot := 0
  001f6614  2e230004  sltiu v1, s1, 4
  001f6618  1460fff3  bne v1, zero, 0x001f65e8
```

It clears: in-use, all four handles, and the four members' `dt_role` (→5).
It does **not** touch: `dt_record` (stale slot index remains — `dt_record`
is meaningless without checking R+16), any kind, any `+0x3E4` engagement, any
timer. **The physical block continues exactly as the ordinary engine had it;
only the bookkeeping dies.** No guards of its own; exactly two callers
(`0x001f68f8`, `0x001f6b50` — re-verified by full-image jal scan in this
session's census work).

A same-frame subtlety that matters for the slot-9 measurements: a peel man
stamped role 3 in 3.2 whose record dies in the same manage call is reset to
role 5 before the frame ends — **a role 3 need never be observable between
frames**, so "no role-3 was measured" does not rule the peel path out.

---

## 5. Drive `0x001f6940` — break tests, teardown caller B, the handoff

Loop registers differ from manage: s7 = slot, s5 = R, **s1 = primary,
s4 = helper, s2 = defender, s3 = peel man** (resolve order `0x001f69c0-69f4`).
Prologue fetches `0x00260208()` — a packed pair of floats from the object at
`*(0x00601F4C)` (+12, +16), landed at sp+0 / sp+4; from their pairing with
player `+0x190/+0x194` below these are the **ball/LOS reference coordinates**
on the two ground axes (identity of the object: §9). `s6` is the break flag,
0 at each record's start (`0x001f69c8`).

### 5.1 A genuinely dead block: the crossed-the-reference tests

```
  001f69f0  0c07e0ae  jal 0x001f82b8        ; gate as in seek
  001f69f8  1040000e  beq v0, zero, 0x001f6a34
  001f6a00  0c07e1ae  jal 0x001f86b8
  001f6a08  30420001  andi v0, v0, 0x0001   ; direction bit
  001f6a0c  14400009  bne v0, zero, 0x001f6a34
  001f6a10  c7a00000  lwc1 f0, 0(sp)        ; ball[0]
  001f6a14  3c013f00  lui at, 0x3f00        ; 0.5f
  001f6a1c  c6820190  lwc1 f2, 400(s4)      ; helper +0x190
  001f6a20  46010001  sub.s f0, f0, f1
  001f6a24  46001034  c.lt.s f2, f0         ; helper past ball[0]-0.5 (this direction)?
  001f6a2c  45010010  bc1t 0x001f6a70       ; true -> skip the mirror test
  001f6a34  0c07e0ae  jal 0x001f82b8        ; (mirror direction)
  001f6a44  0c07e1ae  jal 0x001f86b8
  001f6a50  10400006  beq v0, zero, 0x001f6a6c
  001f6a60  c6820190  lwc1 f2, 400(s4)
  001f6a64  46010000  add.s f0, f0, f1
  001f6a68  46020034  c.lt.s f0, f2         ; ball[0]+0.5 < helper ?  ...
  001f6a6c  8f84bb90  lw a0, -17520(gp)
  001f6a70  8c820054  lw v0, 84(a0)         ;  ...and the flag is NEVER read:
```

The `c.lt.s` at `0x001f6a68` sets the FPU condition flag, and the next
consumer of that flag is the `bc1tl` at `0x001f6a90` — whose flag is freshly
set by the compare at `0x001f6a88`. A full-image branch-target scan confirms
nothing jumps between `0x001f6a6c` and `0x001f6a88` except the three internal
skips quoted above. **Both "helper crossed the reference by half a unit"
comparisons are computed and discarded — the shipped game has no working
LOS-crossing break test.** (Presumably a lost `bc1t` in the original source;
the mirrored first compare only short-circuits the second.)

### 5.2 The hard lifetime cap — B0

```
  001f6a70  8c820054  lw v0, 84(a0)         ; frame counter
  001f6a74  2c42003d  sltiu v0, v0, 61
  001f6a78  50400033  beql v0, zero, 0x001f6b48  ; counter >= 61 ->
  001f6a7c  24160001  addiu s6, zero, 1     ;   s6 := 1 -> TEARDOWN
```

**Every record still alive at manager-frame 61 is destroyed, unconditionally.**
Paired with the seek's `< 60` gate, the whole registry exists only in the
play's first second. (The slot-9 windows 2..36 / 2..43 / 27..43 never reached
it; any fix that extends windows will hit this cap at 61.)

### 5.3 Break predicates B1–B5 (first hit wins; each jumps to `0x001f6b48`)

```
B1 001f6a80  c6810194  lwc1 f1, 404(s4)     ; helper +0x194
   001f6a84  c6400194  lwc1 f0, 404(s2)     ; defender +0x194
   001f6a88  46010034  c.lt.s f0, f1
   001f6a90  4503002d  bc1tl 0x001f6b48     ; defender+0x194 < helper+0x194 -> TEARDOWN
   001f6a94  24160001  addiu s6, zero, 1
B2 001f6a98  12600009  beq s3, zero, 0x001f6ac0   ; no peel man -> skip B2
   001f6a9c  c7a10004  lwc1 f1, 4(sp)       ; ball[1]
   001f6aa0  c6600194  lwc1 f0, 404(s3)     ; peel man +0x194
   001f6aa4  3c014000  lui at, 0x4000       ; 2.0f
   001f6aac  46010001  sub.s f0, f0, f1
   001f6ab0  46020034  c.lt.s f0, f2
   001f6ab8  45030023  bc1tl 0x001f6b48     ; peel+0x194 - ball[1] < 2.0 -> TEARDOWN
   001f6abc  24160001  addiu s6, zero, 1    ;   ("the man you'd peel to is within two
                                            ;    units of the ball axis" - he came downhill)
B3 001f6ac0  8e8401a8  lw a0, 424(s4)       ; helper +0x1A8 (angle)
   001f6ac8  8e4501a8  lw a1, 424(s2)       ; defender +0x1A8
   001f6acc  0c11a7f2  jal 0x00469fc8       ; angular difference (see below)
   001f6ad0  361038e2  ori s0, s0, 0x38e2   ; s0 = 0x002E38E2
   001f6ad4  0202102a  slt v0, s0, v0
   001f6ad8  5040001b  beql v0, zero, 0x001f6b48  ; diff <= 0x002E38E2 (~65.01 deg) ->
   001f6adc  24160001  addiu s6, zero, 1    ;   TEARDOWN.  ALIGNMENT kills, not separation.
B4 001f6ae0  8e2401a8  lw a0, 424(s1)       ; primary vs defender, same test
   001f6aec  0202102a  slt v0, s0, v0
   001f6af0  50400015  beql v0, zero, 0x001f6b48  ; diff <= ~65 deg -> TEARDOWN
B5 001f6af8  12600013  beq s3, zero, 0x001f6b48   ; no peel man -> done (s6 still 0)
   001f6b00  8e6202fc  lw v0, 764(s3)       ; PEEL MAN's AI-state block
   001f6b04  90440000  lbu a0, 0(v0)        ;   (NOT the defender's - prior doc corrected)
   001f6b08  10830003  beq a0, v1, 0x001f6b18     ; state == 2  -> TEARDOWN
   001f6b0c  2402001e  addiu v0, zero, 30
   001f6b10  54820003  bnel a0, v0, 0x001f6b20    ; state == 30 -> TEARDOWN
   001f6b14  8e62000c  lw v0, 12(s3)        ;   (delay: peel man's +0xC flags)
   001f6b18  1000000b  beq zero, zero, 0x001f6b48
   001f6b1c  24160001  addiu s6, zero, 1
   001f6b20  30424000  andi v0, v0, 0x4000  ; else: flags bit 0x4000 set?
   001f6b24  10400008  beq v0, zero, 0x001f6b48   ; clear -> done
   001f6b2c  8e6401b0  lw a0, 432(s3)       ; peel man +0x1B0 (angle)
   001f6b30  0c11a7f2  jal 0x00469fc8
   001f6b34  3c0500c0  lui a1, 0x00c0       ; vs constant 0x00C00000 = 270 deg
   001f6b3c  3463aaa9  ori v1, v1, 0xaaa9   ; 0x002AAAA9 (~60.00 deg)
   001f6b40  0062182a  slt v1, v1, v0
   001f6b44  38760001  xori s6, v1, 0x0001  ; s6 := (diff <= 60 deg)  -> TEARDOWN if within
```

`0x00469fc8` is, quoted in full:

```
  00469fc8  lui v1, 0x00ff / ori v1, ffff   ; mask 0x00FFFFFF
  00469fcc  subu a0, a0, a1
  00469fd8  and a0, a0, v1                  ; d = (a-b) mod 0x01000000
  00469fd4  lui v0, 0x0100
  00469fdc  subu v0, v0, a0                 ; 0x01000000 - d
  00469fe0  slt v1, v0, a0
  00469fe8  movz v0, a0, v1  / jr ra        ; return min(d, 0x01000000-d)
```

— an absolute **angular difference on a 24-bit circle** (0x01000000 = 360°).
Hence: 0x002E38E2 = 65.01°, 0x002AAAA9 = 60.00°, 0x00C00000 = 270°. The
"units" question from the brief is answered: **B3/B4/B5's constants are
angles, and B2's 2.0 is a float distance along the +0x194 axis relative to
the ball reference** (axis orientation: §9). Whether `+0x1A8` is facing or
course is not establishable statically; either way B3/B4 fire when blocker
and defender **align** within 65° — the natural readings are "defender turned
to run / pair collapsed facing the same way" (facing) or "no longer moving as
an engaged pair… would fire on shed, not on drive" (course). A pancake
animation that rotates the pair could also align them — i.e. statically it is
possible that **the engine kills the double team at the exact moment it
succeeds**; live sample decides (§9).

### 5.4 Teardown caller B and the conversion/handoff tail

```
  001f6b48  12c0005f  beq s6, zero, 0x001f6cc8   ; no break -> NEXT RECORD (drive does
  001f6b4c  8f84bb90  lw a0, -17520(gp)          ;   NOTHING to a healthy record)
  001f6b50  0c07d96e  jal 0x001f65b8        ; TEARDOWN(R)
  001f6b54  02a0202d  daddu a0, s5, zero
```

Then, unlike caller A, drive **re-shapes the engagements** (players still
resolved; only the bookkeeping is gone):

```
  001f6b58  8e2503e0  lw a1, 992(s1)        ; primary kind
  001f6b5c/6b68                              ; kind 5/6 -> skip his re-anchor entirely
  001f6b70  8e4203e0  lw v0, 992(s2)        ; DEFENDER kind, dispatch via 0x00583AA0:
                                            ;   kinds 3,4,9 -> 0x001f6b98
                                            ;   kinds 5,6   -> 0x001f6c1c (skip re-anchor)
                                            ;   kinds 7,8   -> 0x001f6bc8
                                            ;   else        -> 0x001f6bc8
  001f6b98  0c04ede6  jal 0x0013b798        ; s0 := resolve(defender->+0x3E4)
  001f6ba4  52110008  beql s0, s1, 0x001f6bc8    ; if it is not the primary
  001f6bac  0c07dd0a  jal 0x001f7428        ;   (e.g. the helper): disengage him
  001f6bbc  0c07dce6  jal 0x001f7398        ;   and set him kind 1, no partner
  001f6bc0  24060001  addiu a2, zero, 1
re-anchor the primary as a SINGLE block:
  001f6bc8  24020003  addiu v0, zero, 3
  001f6bcc  14a20009  bne a1, v0, 0x001f6bf4
  001f6bd4/6be4                              ; primary kind == 3: re-stamp kind 3 on defender
  001f6bf4/6c04                              ; else:              re-stamp kind 2 on defender
  001f6c14  0c07dd32  jal 0x001f74c8        ; defender := kind 9, partner := primary
the helper handoff:
  001f6c1c  8e8303e0  lw v1, 992(s4)        ; helper kind
  001f6c20/6c2c                              ; kind 5/6 -> NEXT RECORD (leave his anim alone)
  001f6c34  12600024  beq s3, zero, 0x001f6cc8   ; no peel man -> NEXT RECORD
  001f6c3c  8e6303e0  lw v1, 992(s3)        ; peel man's kind
  001f6c44-6c68                              ; kind 5/6 -> NEXT RECORD;
                                            ; kind 4 or 9 (already engaged) -> 0x001f6c70;
                                            ; else (0,1,2,3,7,8) -> 0x001f6c9c
  001f6c70  0c04ede6  jal 0x0013b798        ; s0 := resolve(defender->+0x3E4)
  001f6c7c  12140007  beq s0, s4, 0x001f6c9c     ; if not the helper:
  001f6c84  0c07dd0a  jal 0x001f7428        ;   disengage him, kind 1, no partner.
  001f6c94  0c07dce6  jal 0x001f7398        ;   NB: after 0x001f6c14 this is THE PRIMARY
  001f6c98  24060001  addiu a2, zero, 1     ;   just re-anchored - see note below
  001f6c9c  0c07dd0a  jal 0x001f7428        ; disengage the HELPER
  001f6cac  0c07dce6  jal 0x001f7398        ; helper := kind 2 ON THE PEEL MAN
  001f6cb0  24060002  addiu a2, zero, 2
  001f6cbc  0c07dd32  jal 0x001f74c8        ; peel man := kind 9, partner := helper
```

This tail is the literal implementation of the operator's "passes the man
off, climbs": primary keeps the (formerly doubled) defender as an ordinary
single block, and the helper is formally engaged onto the peel-off target.

**Oddity, verified in the instruction stream** (`0x001f6c70-6c98`): on the
peel-kind-4/9 path the code resolves the *defender's* partner — which the tail
itself just set to the primary at `0x001f6c14` (`0x001f74c8` writes
`a0→+0x3E4 := handle(a1)`, quoted in §6) — and, finding it is not the helper,
**frees the primary it just re-anchored** (`0x001f7428` on a mutual pair also
runs `0x001f7540` on the defender: kind := 0, partner := 0). Net result on
that path: primary kind 1, defender kind 0, helper on the peel man. Either
the intended source resolved the *peel man's* partner (an off-by-one-register
bug in the original game), or the intent is "let the primary re-acquire
organically next frame". Statically undecidable; flagged for live
confirmation before anything is built on it.

---

## 6. What the three engagement primitives actually write (read this session)

| fn | writes |
|---|---|
| `0x001f7398(p, q, k)` | calls `0x001f7428(p)` first (full disengage), then p.kind := k, p.+0x432 := 0, +0x434/435 := 0, +0x42E := 0, p.+0x3E4 := handle(q) (0 if q==0), and p.+0x3E8 := handle(q) **only if currently 0** |
| `0x001f74c8(p, q, k)` | **no disengage**: p.+0x42E := 0, p.+0x432 := 0, p.kind := k, p.+0x3E4 := handle(q), +0x3E8 as above. Does not touch +0x434/435 |
| `0x001f7428(p)` | p.kind := 0, timers/flags zeroed, resolve old partner; if mutual (partner's +0x3E4 == handle(p)): `0x001f7540(partner)` — partner kind := 0, partner.+0x3E4 := 0, timers zeroed; then p.+0x3E4 := 0. Also the pairwise `0x002136d0` unlink calls both ways |

---

## 7. The 60-frame window (DT-2) and the counter's audience

Three-gate pattern, twice in code:

* Seek `0x001f64e0`: `0x001f82b8() != 0` AND `0x001f86b8() < 6` AND
  `T+84 < 60` (`0x001f6518-20`, the DT-2 site) — else **no formation**, whole
  seek exits.
* Predicate `0x001f7b28` — the same three gates packaged as a boolean:

```
  001f7b28  ... s0 = 0
  001f7b34  jal 0x001f82b8   ; == 0 -> return 0
  001f7b44  jal 0x001f86b8   ; >= 6 -> return 0
  001f7b54  lw v0, -17520(gp) / lw v1, 84(v0)
  001f7b5c  2c70003c  sltiu s0, v1, 60      ; return (counter < 60)
```

  Sole caller `0x001b5e98` (scorer fn `0x001b5648`, the state-32/two-man
  chooser region): while the window is open and the candidate is beyond a
  reference on `+0x194`, his score is multiplied by `*(0x005fe7a0)` = **⅓**.
  So the window doesn't just bound registration — it biases the two-man
  system's target choice for the same first second.

* The cap: drive kills everything at `T+84 >= 61` (§5.2). Window to *form*:
  frames 0–59. Window to *exist*: frames 0–60.

Other counter consumers (census; thresholds as read at each site):
`0x001eef18` (<60), `0x001f02ec`/`0x001f0888` (<46), `0x001f34d0`/`0x001f4100`
(<31), `0x001f3518` (<30), `0x001f32b8`/`0x001f3650` (<180), `0x001f5214`
(<20), `0x001f3d38` (<4), `0x001f4014` (==0), `0x001f387c`, `0x001f3f28/f54`
(feed float math), plus getter `0x001f7360` (returns T+84) with 17 jal callers:
`0x146030, 0x19455c, 0x1c921c, 0x1c93b4, 0x1c9e88, 0x1caacc, 0x1cbad8,
0x1db20c, 0x1db2c0, 0x1db7b8, 0x1e148c, 0x1efdf8, 0x1efe7c, 0x1f0704,
0x1f2c78, 0x241bfc, 0x242524` (`0x001efdf8/0x001efe7c` are inside re-selection
`0x001efc00`). The counter is a widely consumed play clock; the **records** are
not (next section).

## 8. Census: who else touches the registry

Method: all 74 gp-relative accesses to the pointer slot (the only access form
— no lui/addiu pair exists), each classified by the offset(s) used off the
loaded pointer, with callee-argument passes followed. Result:

* **The four records (T+4..T+83) are private.** Every access to them lies in
  registration `0x001f6338`, manage `0x001f6640`, drive `0x001f6940`, teardown
  `0x001f65b8`, or the reset memset (`0x001f701c`). No other function reads or
  writes a record field. (The scorer `0x001f3a00` receives T in `a2` but reads
  only T+84; the one dt-aware read on a scoring path remains the ×0.75
  `dt_role` test at `0x001f4210-24`, on the player struct, not the table.)
* T+84 has the audience listed in §7.
* T+0 connects three systems: written by the state-32 chooser region
  (`0x001b6070`, fn `0x001b5f20`, gated on `0x001f86b8()&1` and a ball-relative
  `+0x190` compare), consumed-and-cleared by the pair-driver tail
  (`0x001f4a5c-0x001f4a88`: victim whose `+0x5C` handle matches gets **kind 3**
  instead of kind 2, then `0x001f7b20(0)`), cleared per play by reset.
* T+88/T+89/T+90 belong to the scoring phase (`0x001f55e8`, helper scorer
  `0x001f4c40`, direction fns `0x001f5f58`/`0x001f6d38`) — none is read
  anywhere in the teardown path.

## 9. Why a record dies — the decision tree

Per manager frame (mode 3/4), in execution order. **First hit ends the
record**; everything below it that frame is moot.

```
MANAGE 0x001f6640 (runs first; skips records not in use)
│
├─ R+12 empty?
│   ├─ peel scan: primary (priority), then helper - +0x3E4 points at an
│   │   engaged, role-5 man who is not the defender?
│   │   └─ yes -> R+12 := him, his role := 3, dt_record := slot
│   │             (fall through - a fresh peel is judged THIS call)
│   └─ still empty -> record UNTOUCHABLE by manage this frame -> drive
│
├─ [R+12 populated] pointer invariants, in order:
│   I1 primary+0x3E4 ∉ {defender, peel}  ┐
│   I2 helper +0x3E4 ∉ {defender, peel}  ├─> peel man engaged to primary
│   I3 defender+0x3E4 ∉ {primary,helper} ┘    or helper?  yes -> DIE (A)
│   I4 peel-man+0x3E4 ∉ {primary,helper} ──>  no  -> un-label peel
│                                              (role 5, R+12:=0), SURVIVE
├─ primary kind ∉ 2..8                     -> DIE (A)
├─ primary kind 5/6 AND NOT (mutual with defender AND same anim AND
│    anim ∈ {146-151, 168-170, 173})       -> DIE (A)
├─ helper kind < 2                         -> DIE (A)
├─ helper kind 5 or 6   [touch suspect]    -> DIE (A)
├─ helper kind ≥ 9                         -> DIE (A)
└─ survive -> maintenance re-stamps (§3.7), NOT teardown

DRIVE 0x001f6940 (same frame, after manage; break flag s6)
│
├─ B0  T+84 ≥ 61                            -> DIE (B)   [hard cap]
├─ B1  defender+0x194 < helper+0x194        -> DIE (B)
├─ B2  peel man exists AND
│        peel+0x194 − ball[1] < 2.0         -> DIE (B)   [he came downhill]
├─ B3  angdiff(helper+0x1A8, defender+0x1A8) ≤ 65°  -> DIE (B)  [alignment]
├─ B4  angdiff(primary+0x1A8, defender+0x1A8) ≤ 65° -> DIE (B)
├─ B5  peel man exists AND
│        (his AI state ∈ {2, 30}            -> DIE (B)
│         else (his +0xC & 0x4000) AND
│              angdiff(his +0x1B0, 270°) ≤ 60° -> DIE (B))
└─ none -> drive does nothing at all to this record
```

Death consequences differ by caller — a live discriminator:
**A (manage)**: registry cleared, engagements left exactly as they were.
**B (drive)**: registry cleared, then primary re-anchored kind 2/3 on the
defender (defender kind 9), and if a peel man exists (kinds permitting) the
helper is engaged kind 2 onto him — the formal pass-off/climb (§5.4).

**On the 36/43 touch-abort question.** Manage cannot be the killer unless a
peel was detected first (3.3). Since a peel-less record shows drive as its
only executioner, and B2/B5 also need a peel man, the static candidate sets
are exactly:

* no peel event before death → **B1, B3 or B4** (B0 excluded at 36/43);
* peel event (possibly the same frame — the stamp precedes the invariant
  chain) → helper-kind-5/6, invariant-break-with-attached-peel, primary-5/6
  anim-whitelist failure, then drive's B2/B5 for later frames.

The measured absence of role 3 on slot 9 does **not** exclude the second set:
a same-frame stamp+teardown restores role 5 before any between-frame sample
(§4). One savestate read decides it: at the death frame, R+12 and s6's inputs
(helper kind; defender/helper/primary +0x194 and +0x1A8; peel man's state).

## 10. Could not establish (do not inherit as fact)

1. **Which predicate fires at 36 vs 43 on slot 9** — static analysis narrows
   to the two candidate sets above; needs the live sample.
2. **`+0x1A8` / `+0x1B0` semantics** (facing vs course vs something else).
   Both are angles on the 0x01000000 circle — that much is proven by
   `0x00469fc8` — but which body vector each is, and therefore whether B3/B4
   mean "shed" or "pancake", is open. Likewise the field-axis orientation
   (which of `+0x190/+0x194` is downfield, and what 270° points at).
3. **`0x00260208`'s object** (`*(0x00601F4C)`, floats at +12/+16) — read as
   the ball/LOS reference by usage pattern only.
4. **`0x001f82b8`'s meaning** (classifier over team-object byte +20 via
   `0x00248360`/`0x00243f08`) and **`0x001f86b8`'s byte** (`*(0x006012C8)+4`,
   `< 6` required, bit 0 = a direction). Gate shapes verified; enums not.
5. **AI states 2 and 30** (B5) and the **anim ids 146–173** — which behaviors
   they denote. State 32 = two-man animation is carried from the mission
   brief, not re-derived here.
6. **The `0x001f6c70` oddity** (§5.4): original-game bug vs intent.
7. **`+0xB04`** (swap tests: seeker 4, other ∈ {5,9}) — still unidentified;
   prior doc proved it is not the position byte.
8. **Peel-man flags bit `+0xC & 0x4000`** — meaning unknown.
9. **The degenerate self-record** (§2.2) — statically possible, never
   observed; whether the engine's handle graph can actually produce it live
   is unverified.
10. The **±0.5 dead block** (§5.1) is dead in *this* binary; whether other
    builds (NTSC revisions/PAL) share the lost branch was not checked.

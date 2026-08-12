# N-1 — fold the helper into the contest (cave listing + pnach)

Authored 2026-08-12, static/offline, against `extract/SLUS_207.52`
(Madden NFL 2004, SLUS-20752, CRC 14F8B841). Every claim below was
re-checked against the ELF this pass with `recon/mipsdis.py`,
`recon/fpudis.py` and `recon/cave_census.py`; nothing is quoted from
memory of prior sessions.

**What it does.** At the sole contest lock-in call (`jal 0x001f0c40` at
`0x001F153C`, inside `0x001F14D0`), after the engine stamps all six
contest composites for the pair, add the attached role-1 helper's
weight/strength terms into the PRIMARY blocker's three composites
(+0x414/+0x418/+0x41C). The engine's own contest then scores two
blockers against the doubled defender instead of one. If the defender is
not a live role-2 double-team target, or the helper cannot be resolved,
or the helper is not in engagement kind 7/8, the cave is byte-for-byte
behaviour-identical to stock.

---

## 1. Site verification (re-derived from the ELF this pass)

```
001f1538  0200202d  daddu a0, s0, zero     ; a0 = blocker  (unchanged by patch)
001f153c  0c07c310  jal 0x001f0c40         ; <- THE SITE (becomes jal cave)
001f1540  0280282d  daddu a1, s4, zero     ; delay slot   (unchanged by patch)
001f1544  c6440014  lwc1 f4, 20(s2)        ; host reads A comp2 fresh after return
001f1548  c6630014  lwc1 f3, 20(s3)        ; host reads B comp2 fresh after return
```

* Word at `0x001F153C` confirmed `0x0C07C310` in the ELF. (Not
  re-checked in `double_team_slot9.p2s` this pass — offline session —
  but the mission brief records it identical there.)
* **The `jal` is the sole entry to the site**: an image-wide scan of
  every branch form (beq/bne/blez/bgtz, -likely, REGIMM, bc1x) and every
  `j`/`jal` found ZERO instructions targeting `0x001F153C` or
  `0x001F1540`. Redirecting the `jal` therefore captures every execution
  of the call and nothing else.
* Host prologue (`0x001F14D0`, quoted from ELF): frame 192; saves
  s0@48, s1@64, s2@80, s3@96, s4@112, s5@128, s6@144, ra@160, f21@184,
  f20@176, a2→0(sp). **s7 is NOT saved — the cave never touches s7.**
* Live registers at the site (host body re-read): s0 = blocker base,
  s4 = defender base, s1 = pair-axis value (read at `0x001F1564`),
  s2 = blocker+0x404, s3 = defender+0x404, s5 = blocker+0x190,
  s6 = blocker+0x3E0, gp/sp/fp, 0(sp) = saved a2. The cave reads s0,
  s2, s4, gp, sp and writes none of them.
* FPU after the call: f4/f3 (comp2s) and f1/f2 (comp1s) are loaded
  FRESH at `0x001F1544/48/60/68`; scanned the whole host body
  (`0x001F1544..0x001F1714` = `jr ra`) — **zero reads of f12–f19
  after the call** (f12/f13 were consumed as args to the
  `jal 0x00469e78` before the site). f20/f21 are defined before use in
  every arm. So f0–f21 are all dead across the site; the cave uses
  f1–f11 only.
* a2 at the site: stock sets a2 last at `0x001F14EC` for an earlier
  call; by the site it is callee-clobbered garbage, and the host reloads
  its saved copy from 0(sp) when needed. The cave leaves a2 untouched
  before the displaced call, so `0x001f0c40` receives exactly the stock
  register file (a0=s0, a1=s4, a2=whatever stock had).

## 2. The displaced callee and the resolver (ABI audit)

`0x001f0c40` (contest stamp) prologue quoted from ELF: frame 208, saves
fp@160, s7@144, s6@128, s5@112, s4@96, s3@80, s2@64, s1@48, s0@32,
ra@176, f20@192. All s-registers the cave depends on (s0/s2/s4) are
callee-saved here. It confirms every field offset the fold uses:
s5 = A (a0), `s0 = A+0xB70` (ratings, `addiu s0,s5,2928`),
`s1 = A+0x404` (comps, 1028), weight read `lwc1 f1, 2796(s5)` = +0xAEC,
comp stores `swc1 …,16/20/24(s1)` = +0x414/418/41C — **the comps are
f32** (stored and re-read exclusively via swc1/lwc1).

`0x0013B798` (handle resolver) full body read (`0x0013B798–0x0013B868`):
frame 16, `sd ra,0(sp)` … `ld ra,0(sp); jr ra; addiu sp,sp,16`.
**It touches NO s-registers anywhere in its body** (only v0/v1/a0–a3,
ra, sp). Takes a0 = **address of** a handle word (`lw v0,0(a0)` at
`0x0013B7A4`), dispatches on kind byte (<10, table `0x0057B680` —
materialisation `lui 0x58; addiu -0x4980` verified), kind-1 arm at
`0x0013B7E0` calls GetPlayer `0x001655B0`, result funnels through a2 to
`v0 = player base, or 0` (a2 initialised to zero at `0x0013B79C`, so
kind ≥ 10 also returns 0). Engine precedent for calling it on registry
record slots: the record-clear walk at `0x001F65E8-0x001F6618` resolves
each of the 4 handle words of a record at stride 4 — confirming handles
sit at record +0x00/+0x04/+0x08/+0x0C and the resolver takes their
address.

## 3. Registry lookup facts (re-verified)

* `gp` is the constant `0x006056F0` image-wide; `-17520(gp)` =
  `[0x00601280]` = play manager (mipsdis GP_BASE annotation confirms).
* Record = mgr + 4 + 20k, k = `lbu defender+0x436`; helper handle
  ALWAYS at record+0x04 (creation stores `0x001F6460–0x001F649C`
  re-read: primary→+0, helper→+4, defender→+8; roles 0/1/2 stamped at
  +0x437 in the same run of stores as k at +0x436).
* **The role-2 gate is sound as the validity gate** — full image census
  of +0x437 (`sb`/`lbu`/`lb` with simm 1079, 12 hits total, all listed
  and read this pass): creation stamps 0/1/2 (`0x001F6490/94/9C`); both
  teardown writers store **5** (`0x001F7098`, s4 set to 5 at
  `0x001F7014`; `0x001F68E0`, v0=5); the record-clear walk `0x001F6600`
  stores s3 (the same clear path that zeroes the active byte first);
  the only other writer `0x001F6730` stores role **3** and re-stamps
  +0x436 in its delay-slot neighbour `0x001F6734` — so role 2 with a
  stale index cannot arise. Readers: `0x001F4210`, `0x001F6388/94`,
  `0x001F66DC/0x001F670C` (compare against 5).
* Engagement kind lives at player+0x3E0, stored/read as a 32-bit word
  (`sw s2,992(s0)` at the kind stamp `0x001EFA38`; `lw a0,16(s6)` reads
  +0x3F0 off the same block). **Gate accepts {7,8} per the N-1 spec**:
  kind 7 = assigned/running-in, kind 8 = attached. This is deliberately
  wider than mass-law §4.3's touch-only gate (kind 8), because run-play
  registry doubles hold kind 7 throughout and never enter kind 8
  (`double-team-requirements.md` lines 761, 871) — a kind-8-only N-1
  would never fire on the exact play it exists for. R5's contract is the
  three-part gate as specified: role==2, resolve≠0, kind∈{7,8}.

## 4. Attribute indices — AGI RESOLVED (was flagged unverified)

The 21-entry name table at `0x00520140` read from the file: packed
4-char tags, in order:

```
PACC(0) PAGI(1) PAWR(2) PBTK(3) PCAR(4) PCTH(5) PJMP(6) PIMP(7) PINJ(8)
PKAC(9) PKPR(10) PPBK(11) PRBK(12) PSPD(13) PSTA(14) PSTR(15) PTAK(16)
PTHA(17) PTHP(18) PTGH(19) PKRT(20)
```

Cross-checked against the contest stamp's own reads (effective ratings
base +0xB70, u16, offset = 2·index):

* `lh 30(s0)` in the comp1 path = index 15 = **PSTR** ✓ (doc: STR)
* `lh 22/24(s0)` = 11/12 = PPBK/PRBK ✓ (pass/run block)
* Defender comp2 = "AGI + STR + AWR" reads `lh 2(s3)`, `lh 30(s3)`,
  `lh 4(s3)` (`0x001F0EAC–0x001F0EBC`, quoted) — with 30=STR and
  4=AWR(2), **offset 2 = index 1 = PAGI is AGI**. The blocker comp2
  path (`0x001F0D0C–0x001F0D54`) independently agrees: `lhu 4` (AWR)
  and `lhu 30` (STR) each halved by `sra 1` (with sign-fix), plus
  `lh 2` (AGI) added whole — matching the documented formula
  BLK + AWR/2 + STR/2 + AGI term-for-term.

**Indices used: STR = 15 (+0xB8E), AGI = 1 (+0xB72), AWR = 2 (+0xB74),
weight = +0xAEC (f32 real pounds).** All four fold terms retained.
The cave's `srl t8,t8,1` for STR/2 equals the engine's sign-fixed
`sra 1` for every possible input because effective ratings are u16
0..255 (never negative).

## 5. The fold law (as specified, with two stated properties)

```
comp1 (+0x414) += W_h + STR_h
comp2 (+0x418) += AGI_h + STR_h/2
comp3 (+0x41C) += W_h + STR_h + AWR_h + AGI_h
```

Applied to the PRIMARY blocker's comps only (host s2 = blocker+0x404;
comps at 16/20/24(s2)). Two properties of running the fold AFTER the
displaced call, stated so nobody rediscovers them as bugs: the helper's
terms receive **no ×[1.00,1.33) jitter** and **no per-position-class
scale** (both applied inside `0x001f0c40` before the cave's addition).
The helper contributes his raw terms; the engine's precedent that one
point of effective STR equals one pound (comp1 adds them directly) is
preserved.

## 6. Design of the cave's frame and ordering

* **Order**: displaced call first (requirement — the fold adds to comps
  that call stamps), then gates, then resolve, then fold. All the
  cave's own FPU work happens after both nested calls, so no FPU value
  ever crosses a call. No t-register value crosses a call either (t2 is
  consumed by the `addiu a0,t2,8` in the resolver's delay slot, which
  executes before the callee body).
* **Frame**: `addiu sp,sp,-16` (quadword alignment preserved — EE ABI),
  `sd ra,0(sp)` … `ld ra,0(sp); jr ra; addiu sp,sp,16` — the exact
  idiom of `0x0013B798` itself. Only ra needs saving: everything else
  the cave uses is either caller-saved (dead at the site, per §1) or
  read-only s-registers preserved by both callees (§2). The host's
  0(sp) (saved a2) sits in the HOST frame at old-sp+0; the cave's
  0(sp) is 16 bytes below it — no overlap.
* **Arguments**: a0/a1 are re-materialised (`daddu a0,s0,zero` /
  `daddu a1,s4,zero`) rather than trusted from the redirected site, so
  the displaced call's inputs are correct by construction, not by
  delay-slot survival.
* **Gate failure of any kind exits through the shared epilogue** —
  identical machine state to stock (ra restored, sp popped; the only
  residue is the caller-saved scratch the real callee would have
  clobbered anyway, plus canary A).
* **Insurance gates** (2 loads + 1 branch each, beyond the spec's
  three): record.defender handle == [s4+0] guards a stale-k record
  (statically impossible per §3's lifecycle census, kept as
  belt-and-braces — it can only fail toward stock); record.primary
  handle == [s0+0] guards the case where the lock-in fires for the
  HELPER-vs-defender pair of the same record — without it the cave
  would fold the helper's stats into the helper's own comps. Both can
  only suppress the fold, never corrupt it; the "must not change"
  oracle set depends on them.
* **mtc1 → cvt.s.w back-to-back** is the compiler's own idiom here
  (`0x001F0CB8/BC`); EE COP1 interlocks it.

## 7. Full annotated listing (66 words, 264 bytes, cave #4)

Every word below was hand-assembled and round-tripped through
`recon/mipsdis.py` (FPU words through `recon/fpudis.py`); the
disassembler's output matched the intended mnemonic for all 66 words.
The four FPU encoder patterns were additionally verified byte-identical
against compiler-emitted words in the ELF (mtc1 `0x44820000`, cvt.s.w
`0x46800020`, add.s `0x46010000`, swc1 `0xE6200010` at
`0x001F0CB8–0x001F0CCC`).

```
; entry: redirected jal from 0x001F153C. a0=blocker, a1=defender already
; set by stock code, but re-materialised below anyway. s0=blocker,
; s4=defender, s2=blocker+0x404, gp=0x006056F0.

004F4AA0  27BDFFF0  addiu sp, sp, -16       ; own 16-byte frame (quadword-aligned)
004F4AA4  FFBF0000  sd    ra, 0(sp)         ; save return address (two nested jals follow)
004F4AA8  0200202D  daddu a0, s0, zero      ; a0 = blocker base (stock 0x001F1538 value)
004F4AAC  0C07C310  jal   0x001f0c40        ; DISPLACED CALL FIRST -- stamps all six comps
004F4AB0  0280282D  daddu a1, s4, zero      ; (ds) a1 = defender base (stock delay-slot value)
; ---- entry canary (unconditional: proves the redirect is live) ----
004F4AB4  3C080051  lui   t0, 0x0051
004F4AB8  24090001  addiu t1, zero, 1
004F4ABC  AD09497C  sw    t1, 0x497C(t0)    ; CANARY A: [0x0051497C] = 1  (cave entered)
; ---- gate 1: defender dt_role == 2 (doubles as k validity, §3) ----
004F4AC0  92880437  lbu   t0, 0x437(s4)     ; defender dt_role
004F4AC4  24010002  addiu at, zero, 2
004F4AC8  15010034  bne   t0, at, exit      ; not a doubled defender -> stock
004F4ACC  92890436  lbu   t1, 0x436(s4)     ; (ds) k = record index
; ---- record = [gp-17520] + 4 + 20k  (t2 holds mgr+20k; fields at t2+4..) ----
004F4AD0  8F8ABB90  lw    t2, -17520(gp)    ; play manager = [0x00601280]
004F4AD4  00095880  sll   t3, t1, 2         ; k*4
004F4AD8  00096100  sll   t4, t1, 4         ; k*16
004F4ADC  016C5821  addu  t3, t3, t4        ; k*20
004F4AE0  014B5021  addu  t2, t2, t3        ; t2 = mgr + 20k
; ---- gate 2: record active ----
004F4AE4  914D0014  lbu   t5, 0x14(t2)      ; record+0x10 active byte
004F4AE8  11A0002C  beq   t5, zero, exit    ; dead record -> stock
004F4AEC  8E8E0000  lw    t6, 0(s4)         ; (ds) defender self-handle (harmless if taken)
; ---- insurance A: record.defender == this defender ----
004F4AF0  8D4F000C  lw    t7, 0x0C(t2)      ; record+0x08 defender handle
004F4AF4  15CF0029  bne   t6, t7, exit      ; stale k -> stock
004F4AF8  8E0E0000  lw    t6, 0(s0)         ; (ds) blocker self-handle
; ---- insurance B: record.primary == this blocker (never helper-into-helper) ----
004F4AFC  8D4F0004  lw    t7, 0x04(t2)      ; record+0x00 primary handle
004F4B00  15CF0026  bne   t6, t7, exit      ; s0 is not this record's primary -> stock
004F4B04  00000000  nop                     ; (ds)
; ---- resolve helper handle -> v0 = player base or 0 ----
004F4B08  0C04EDE6  jal   0x0013b798        ; engine's own resolver (null/kind-validated)
004F4B0C  25440008  addiu a0, t2, 8         ; (ds) a0 = &record.helper (record+0x04)
004F4B10  10400022  beq   v0, zero, exit    ; helper gone -> stock
004F4B14  00000000  nop                     ; (ds)
; ---- gate 3: helper engagement kind in {7,8} ----
004F4B18  8C5803E0  lw    t8, 0x3E0(v0)     ; helper engagement kind (32-bit, §3)
004F4B1C  2718FFF9  addiu t8, t8, -7
004F4B20  2F180002  sltiu t8, t8, 2         ; 1 iff kind in {7,8}
004F4B24  1300001D  beq   t8, zero, exit    ; helper not assigned/attached -> stock
004F4B28  00000000  nop                     ; (ds)
; ---- THE FOLD (all-FPU work after both calls; f1-f11 dead at site, §1) ----
004F4B2C  C4410AEC  lwc1  f1, 0xAEC(v0)     ; W_h   helper weight, f32 real pounds
004F4B30  94580B8E  lhu   t8, 0xB8E(v0)     ; STR_h ratings[15] PSTR, u16 0..255
004F4B34  94590B72  lhu   t9, 0xB72(v0)     ; AGI_h ratings[1]  PAGI      (§4)
004F4B38  94480B74  lhu   t0, 0xB74(v0)     ; AWR_h ratings[2]  PAWR
004F4B3C  44981000  mtc1  t8, f2
004F4B40  468010A0  cvt.s.w f2, f2          ; f2 = STR_h
004F4B44  0018C042  srl   t8, t8, 1         ; STR_h/2 (== engine's sra 1: u16 input)
004F4B48  44983800  mtc1  t8, f7
004F4B4C  468039E0  cvt.s.w f7, f7          ; f7 = STR_h/2
004F4B50  44991800  mtc1  t9, f3
004F4B54  468018E0  cvt.s.w f3, f3          ; f3 = AGI_h
004F4B58  44882000  mtc1  t0, f4
004F4B5C  46802120  cvt.s.w f4, f4          ; f4 = AWR_h
004F4B60  46020940  add.s f5, f1, f2        ; f5 = W_h + STR_h
004F4B64  C6460010  lwc1  f6, 16(s2)        ; blocker comp1 (+0x414, f32)
004F4B68  46053180  add.s f6, f6, f5
004F4B6C  E6460010  swc1  f6, 16(s2)        ; comp1 += W_h + STR_h
004F4B70  46071A00  add.s f8, f3, f7        ; f8 = AGI_h + STR_h/2
004F4B74  C6490014  lwc1  f9, 20(s2)        ; blocker comp2 (+0x418)
004F4B78  46084A40  add.s f9, f9, f8
004F4B7C  E6490014  swc1  f9, 20(s2)        ; comp2 += AGI_h + STR_h/2
004F4B80  46042A80  add.s f10, f5, f4       ; W_h + STR_h + AWR_h
004F4B84  46035280  add.s f10, f10, f3      ; ... + AGI_h
004F4B88  C64B0018  lwc1  f11, 24(s2)       ; blocker comp3 (+0x41C)
004F4B8C  460A5AC0  add.s f11, f11, f10
004F4B90  E64B0018  swc1  f11, 24(s2)       ; comp3 += W_h + STR_h + AWR_h + AGI_h
; ---- fold canary ----
004F4B94  3C090051  lui   t1, 0x0051
004F4B98  AD224978  sw    v0, 0x4978(t1)    ; CANARY B: [0x00514978] = helper base (fold ran)
; ---- exit (shared by all gate failures and the fold path) ----
004F4B9C  DFBF0000  ld    ra, 0(sp)         ; "exit:"
004F4BA0  03E00008  jr    ra                ; back to 0x001F1544
004F4BA4  27BD0010  addiu sp, sp, 16        ; (ds) pop frame
```

### Branch-target verification (computed from the final layout)

exit = `0x004F4B9C`. offset = (target − (branch_addr + 4)) / 4:

| branch | addr | encoded offset | branch_addr+4 + 4·offset |
|---|---|---|---|
| `bne t0,at`  | 004F4AC8 | 0x0034 (52) | 004F4ACC + 0x0D0 = **004F4B9C** ✓ |
| `beq t5,zero`| 004F4AE8 | 0x002C (44) | 004F4AEC + 0x0B0 = **004F4B9C** ✓ |
| `bne t6,t7`  | 004F4AF4 | 0x0029 (41) | 004F4AF8 + 0x0A4 = **004F4B9C** ✓ |
| `bne t6,t7`  | 004F4B00 | 0x0026 (38) | 004F4B04 + 0x098 = **004F4B9C** ✓ |
| `beq v0,zero`| 004F4B10 | 0x0022 (34) | 004F4B14 + 0x088 = **004F4B9C** ✓ |
| `beq t8,zero`| 004F4B24 | 0x001D (29) | 004F4B28 + 0x074 = **004F4B9C** ✓ |

The disassembler independently printed `0x004f4b9c` as the target for
all six (see §7 assembly log). Jump encodings: site
`jal 0x004F4AA0` = `0x0C000000 | (0x004F4AA0>>2)` = `0x0C13D2A8`;
`jal 0x001f0c40` = `0x0C07C310` (byte-identical to the stock site
word); `jal 0x0013b798` = `0x0C04EDE6` (matches the engine's own
encoding at `0x001F65EC`).

### Register / FPU / stack audit

* **Written by the cave**: at, v0*, v1*, a0, a1, a2*, a3*, t0–t9,
  hi/lo*, f1–f11 (* = via callees; the same callee-clobber set stock
  already has). All dead at the site (§1); host redefines everything it
  reads after the call (`0x001F1544–0x001F15A8` re-read: v0/v1/a0/f0–f4
  all freshly defined before use).
* **Read, never written**: s0, s2, s4, gp, fp(no), sp (±16 balanced).
* **Never touched**: s1, s3, s5, s6, **s7**, fp, k0/k1, f0, f12–f31.
* **Stack**: −16/+16 balanced on every path (single shared epilogue);
  ra saved before any jal, restored on every path; both callees verified
  to preserve s-regs and pop their frames (§2).
* Delay-slot loads on taken branches (`lbu 0x436(s4)`, `lw 0(s4)`,
  `lw 0(s0)`) read valid player fields — harmless either way.

## 8. Cave census (re-run this pass) and canaries

`python3 -m recon.cave_census extract/SLUS_207.52 0x004F4AA0:608
0x00447888:600 0x0044BEB0:584` — all three candidates: **DEAD on all
five axes** (jal / j / branch / formed / word) and cannot be entered by
fall-through. **Chosen: cave #4 `0x004F4AA0`** (608 B; 264 B used,
**344 B left free at `0x004F4BA8`**) — largest of the three and, unlike
#5/#6, not adjacent to the claimed cave #7 band. No existing pnach in
`patches/` writes to caves #4/#5/#6 (grepped). Census caveat unchanged
from `code-caves.md`: a clean static census is necessary, not
sufficient — the runtime execute-breakpoint liveness test still gates
first use of this cave.

**Canaries** (both words confirmed `0x00000000` in the ELF, inside
cave #11's linker padding, owned by no object; not written by any
existing pnach — P9's frame counter uses the adjacent `0x00514974`):

* `[0x0051497C]` = 1 — the cave executed at all (stock-unreachable
  word; if it stays 0 the site patch never fired).
* `[0x00514978]` = helper player base — the full gate chain passed and
  the fold ran (doubles as a pointer you can inspect live).

## 9. Ready-to-paste pnach lines

```
// N-1 -- helper fold at the contest lock-in. Site 0x001F153C jal ->
// cave #4 0x004F4AA0 (censused DEAD 2026-08-12). Delay slot unchanged.
// Gates: dt_role==2, record active, record.defender==s4, record.primary==s0,
// resolve!=0, helper kind in {7,8}. Any failure = stock behaviour.
// Canary A [0x0051497C]=1 cave ran; canary B [0x00514978]=helper base fold ran.
patch=1,EE,001F153C,word,0C13D2A8
patch=1,EE,004F4AA0,word,27BDFFF0
patch=1,EE,004F4AA4,word,FFBF0000
patch=1,EE,004F4AA8,word,0200202D
patch=1,EE,004F4AAC,word,0C07C310
patch=1,EE,004F4AB0,word,0280282D
patch=1,EE,004F4AB4,word,3C080051
patch=1,EE,004F4AB8,word,24090001
patch=1,EE,004F4ABC,word,AD09497C
patch=1,EE,004F4AC0,word,92880437
patch=1,EE,004F4AC4,word,24010002
patch=1,EE,004F4AC8,word,15010034
patch=1,EE,004F4ACC,word,92890436
patch=1,EE,004F4AD0,word,8F8ABB90
patch=1,EE,004F4AD4,word,00095880
patch=1,EE,004F4AD8,word,00096100
patch=1,EE,004F4ADC,word,016C5821
patch=1,EE,004F4AE0,word,014B5021
patch=1,EE,004F4AE4,word,914D0014
patch=1,EE,004F4AE8,word,11A0002C
patch=1,EE,004F4AEC,word,8E8E0000
patch=1,EE,004F4AF0,word,8D4F000C
patch=1,EE,004F4AF4,word,15CF0029
patch=1,EE,004F4AF8,word,8E0E0000
patch=1,EE,004F4AFC,word,8D4F0004
patch=1,EE,004F4B00,word,15CF0026
patch=1,EE,004F4B04,word,00000000
patch=1,EE,004F4B08,word,0C04EDE6
patch=1,EE,004F4B0C,word,25440008
patch=1,EE,004F4B10,word,10400022
patch=1,EE,004F4B14,word,00000000
patch=1,EE,004F4B18,word,8C5803E0
patch=1,EE,004F4B1C,word,2718FFF9
patch=1,EE,004F4B20,word,2F180002
patch=1,EE,004F4B24,word,1300001D
patch=1,EE,004F4B28,word,00000000
patch=1,EE,004F4B2C,word,C4410AEC
patch=1,EE,004F4B30,word,94580B8E
patch=1,EE,004F4B34,word,94590B72
patch=1,EE,004F4B38,word,94480B74
patch=1,EE,004F4B3C,word,44981000
patch=1,EE,004F4B40,word,468010A0
patch=1,EE,004F4B44,word,0018C042
patch=1,EE,004F4B48,word,44983800
patch=1,EE,004F4B4C,word,468039E0
patch=1,EE,004F4B50,word,44991800
patch=1,EE,004F4B54,word,468018E0
patch=1,EE,004F4B58,word,44882000
patch=1,EE,004F4B5C,word,46802120
patch=1,EE,004F4B60,word,46020940
patch=1,EE,004F4B64,word,C6460010
patch=1,EE,004F4B68,word,46053180
patch=1,EE,004F4B6C,word,E6460010
patch=1,EE,004F4B70,word,46071A00
patch=1,EE,004F4B74,word,C6490014
patch=1,EE,004F4B78,word,46084A40
patch=1,EE,004F4B7C,word,E6490014
patch=1,EE,004F4B80,word,46042A80
patch=1,EE,004F4B84,word,46035280
patch=1,EE,004F4B88,word,C64B0018
patch=1,EE,004F4B8C,word,460A5AC0
patch=1,EE,004F4B90,word,E64B0018
patch=1,EE,004F4B94,word,3C090051
patch=1,EE,004F4B98,word,AD224978
patch=1,EE,004F4B9C,word,DFBF0000
patch=1,EE,004F4BA0,word,03E00008
patch=1,EE,004F4BA4,word,27BD0010
```

## 10. Oracle (pre-registered acceptance)

**Must change** (slot 9, the measured double): at the frame-23 shed
contest the doubled defender must now LOSE — the pair resolves into the
driven-back clip family (ids 120–131) instead of the shed→handoff.
Canary A = 1 by the first lock-in of any play; canary B = a valid
player base (0x00xxxxxxx, helper's) after the double's lock-in.
Mechanically: at lock-in the primary's +0x414/418/41C must each exceed
their stock-formula value by exactly W_h+STR_h / AGI_h+STR_h/2 /
W_h+STR_h+AWR_h+AGI_h (readable live; no jitter on the added terms).

**Must NOT change**: slot 6 (lead blocker play) and slot 7 (pass
protection) resolve identically to stock — on slot 7 records never form
(role byte never 2 → gate 1 fails every lock-in); any engaged pair
without a live registry record behaves byte-identically to stock;
defender-side comps (+0x414/418/41C off s3) untouched everywhere;
canary B stays 0 on every play with no qualifying double. Full
regression per project rule 2: this patch alone on its own savestate,
then integration with the standing patch set plus
`tests/test_madden_lab_*.py`.

## 11. What I could not establish (static session limits)

1. **Runtime liveness of cave #4** — censused DEAD on all five static
   axes, but the census cannot rule out a computed `jalr` through a
   never-materialised pointer or runtime `.text` writes. The
   execute-breakpoint test (code-caves.md liveness test 1) is still
   required before first use.
2. **Live-record content** — no owned savestate carries an active
   registry record (slot 9 is pre-snap), so record layout is verified
   at its creation site and structurally in memory, not against a live
   populated record.
3. **The site word in slot 9 memory** — recorded as verified by the
   mission brief; I could not re-check the savestate's memory image
   offline this pass (ELF word re-verified).
4. **Whether a helper-vs-defender lock-in actually occurs** for kind-7/8
   helpers (which would have caused helper-into-helper folding).
   Insurance gate B makes the answer irrelevant to correctness, but the
   question itself stays open.
5. **Magnitude sufficiency** — that W_h+STR_h (etc.) is ENOUGH to flip
   the frame-23 contest for the measured pair is the acceptance test's
   job; the fold law is implemented exactly as specified, not tuned.

# The motion cave — driving a blocked defender backwards

Designed 2026-08-12, static/offline. Sources: `extract/SLUS_207.52` via
`recon.mipsdis`/`recon.fpudis`; `experiments/states/ball_in_air_slot8.p2s`
(the one owned MID-PLAY image, live kind-6 pair) and
`experiments/states/double_team_slot9.p2s` (pre-snap) via
`tools/statereader.py`. Every instruction quoted below was re-read from the
image this pass; every live value was re-read from a savestate this pass
(rule 4). Every cave word below was hand-assembled and round-tripped through
`recon.fpudis` this pass. **UNVERIFIED** marks the residue. Nothing in this
document is deployed by this document.

**One sentence:** the double team already plays the driving clip (161) with a
zeroed motion block; this cave relocates the root-motion applier's addition
into the one code path proven to run every frame for an attached double —
the kind-8 helper servicer — and writes the mass-law displacement onto all
three bodies there, gated so that everything that is not a dominant live
double is byte-for-byte untouched.

---

## 1. The hook decision — and why it is NEITHER of the two offered sites

The mission offered two hooks: the converter's output (post-`0x0018F9E0`) or
the applier (`0x0018F980`'s addition). Both were fully derived this pass, and
**both had to be rejected on live evidence: for pair-block clips, neither
function executes.** The applier's *arithmetic* is adopted; its *call site*
is not. Derivation follows.

### 1.1 Where per-frame motion lives after conversion (the load-bearing read)

The converter's stores, read from the image (`0x0018F9E0`, s7 = a1
throughout — the prologue at 0x0018FA04 is `daddu s7, a1, zero`):

```
0018fb30  bne  v0, zero, 0x0018fb44
0018fb34  sw   a2, 8(s7)            ; block+0x08 = per-frame dheading (int)
0018fb40  sw   v0, 8(s7)            ; (wrap arm: negated)
0018fb94  div.s f3, f3, f5          ; f5 = spec+0x20 duration
0018fbb4  swc1 f3, 0(s7)            ; block+0x00 = per-frame dx (f32)
0018fbc4  div.s f1, f1, f0
0018fbc8  swc1 f1, 4(s7)            ; block+0x04 = per-frame dz (f32)
0018fbd4  cvt.w.s f1, f0
0018fbd8  swc1 f1, 12(s7)           ; block+0x0C = frames (int countdown)
```

**Answer: after conversion the per-frame motion lives in the block passed as
a1 — four words {dx f32, dz f32, dheading BAM24-int, frames int} at +0x00/
+0x04/+0x08/+0x0C.** That block is anim-slot+0x10: the accessor `0x003AD3D0`
walks the four 0x64-byte slots at animbase and returns `slot+0x10`
(`jr ra; addiu v0, a0, 16` at 0x003AD3F4). One correction to
5-clip-semantics on the way: **`0x003AD3D0`'s a1 is the CLIP ID, not a slot
index** — it matches `lhu v0, 4(a0)` (the slot's id field) against a1, so
the burst service is bound to a clip id, not a slot number.

The applier consumes exactly those fields (`0x0018F980`, complete):

```
0018f980  lw   v0, 12(a1)           ; frames
0018f98c  addiu v0, v0, -1
0018f990  blez v0, 0x0018f9d8       ; exhausted -> no motion
0018f994  sw   v0, 12(a1)           ; (ds, always) frames--
0018f998  lwc1 f1, 0(a1)            ; dx
0018f9a0  lwc1 f0, 400(a2)          ; player x  (+0x190)
0018f9ac  add.s f0, f0, f1
0018f9bc  swc1 f0, 400(a2)          ; x += dx        <- THE addition
0018f9a8  lw   v0, 24(a3)           ; heading (+0x1A8), += dheading, BAM24-masked
0018f9c4  sw   v0, 24(a3)
0018f9c8  lwc1 f0, 4(a3)            ; player z (+0x194), += dz
0018f9d4  swc1 f0, 4(a3)
```

Root motion on this engine is **accumulate-in-place on +0x190/+0x194** —
position is persistent state, nothing re-derives it. That is the mechanism
this cave reuses.

### 1.2 Both functions have exactly one caller, inside one phase-dispatched host

`find_jal_targets`: converter — only `0x0018FCC4`; applier — only
`0x0018FCE8`. No `j` tail-calls, no lui/addiu materialisation of either
address anywhere in the image. Both sites sit in the host `0x0018FBE8`
(prologue at 0x0018FBE8; the mission's "region 0x0018FCB0" is its interior),
which dispatches on t1 (its 6th register argument):

```
0018fc38  beql s0, v0, 0x0018fcfc   ; phase 1 -> clear flag 0x200
0018fc40  beq  s0, zero, 0x0018fc60 ; phase 0 -> setup
0018fc48  beq  s0, v0, 0x0018fcd4   ; phase 2 -> per-frame apply
0018fc50  beq  s0, v0, 0x0018fcf8   ; phase 3 -> clear flag 0x200
; phase 0 (setup): arm slot bit, launch, convert
0018fc68  jal 0x003ad568            ; OR 1 into slot flags (+0x08 of the slot)
0018fca0  jal 0x003a8930            ; launch; cookie -> block+0x10
0018fcb0  lw  a2, 8(s0)             ; spec ptr from the matched row
0018fcc4  jal 0x0018f9e0            ; CONVERTER(player, block, spec)
0018fcc8  sw  v0, 12(s1)            ; (ds) player flags |= 0x200
; phase 2 (tick):
0018fcd4  jal 0x003ad410            ; currently-active clip id
0018fcdc  bne v0, s2, 0x0018fd10    ; not our clip -> skip
0018fce8  jal 0x0018f980            ; APPLIER(player, block)
```

The host is **opcode 38 of a 50-entry message-handler table** — the
registration function at `0x00180E78` fills table `0x00522F10` with a
default handler via `0x0013C398(table, 50)`, then stores five specific
handlers; `sw a1, 152(v1)` at 0x00180EFC installs `0x0018FBE8` at +0x98
(= entry 38). The table belongs to a named engine object (`"PROPC"`, record
at data 0x00600FE0: name, count 0x32, table pointer 0x00522F10). Delivery is
through the runtime message bus (dispatch pointer [0x00609134]); the sender
of opcode 38 was not statically traced (§10) — and did not need to be,
because of what the live pair's memory shows.

### 1.3 The live disproof: no burst runs for pair clips

`ball_in_air_slot8.p2s`, the LIVE kind-6 pair (OL 0:9 ↔ DE 1:3, both
playing clip 147 status 3), motion block area slot+0x10 read this pass:

| player | +0x10 (dx) | +0x14 (dz) | +0x18 | +0x1C (frames) | +0x20 (cookie) |
|---|---|---|---|---|---|
| blocker 0:9 | 0.0 | 0.0 | 0 | 0 | 0 |
| defender 1:3 | 0.654 | **4.5146** | 0x40C3317C (=6.099f) | 0x3E33... (**float bits**) | 0x3E1DBF14 (=0.154f) |

Three decisive facts:

1. **If the applier were ticking the defender's block he would fly off the
   field** — dz 4.51 yd *per frame* — and the frames word would be a
   decrementing integer going negative. It is float bits. The applier has
   never touched this block.
2. **If phase 0 had run for the pair clip**, block+0x10 would hold the
   launcher cookie (`sw v0, 16(s4)` at 0x0018FCA8) and +0x0C an integer
   duration. The blocker's block is virgin zeros, the defender's is stale
   float garbage from an earlier tenant of the slot: the +0x10 area of a
   pair-clip slot is simply **other data** until the burst service claims
   it. (The "motion block reads zeroed" observation in 5-clip-semantics was
   the blocker's; the defender's junk makes the stronger point.)
3. Flag 0x200 is set on both pair members — but also on DE 1:0, who is
   kind 0 in open field playing shed-finish 99. The bit is **stale from
   earlier solo/segment bursts** (g12/g15 carry type-9 specs; pair families
   g15–g19 ship none — 5-clip-semantics §3) and proves nothing about the
   pair clip. This corrects the "a burst DID run at attach" reading.

**Conclusion: opcode-38 phase-0/phase-2 messages are not sent for
pair-family clips.** A hook at the converter's output or inside the applier
inherits an execution premise that is measurably false — the exact
silent-null failure of risk register #2. The defect ("driving-class
animation, no motion") is upstream of both offered sites: *nothing ever
starts a burst for the pair*, so there is nothing to scale.

### 1.4 The chosen hook: the applier's addition, relocated to a proven per-frame site

What the patch needs is a site that (a) provably runs every frame during
the pair window, (b) has the pair and the helper in registers, (c) is
scoped to doubles by construction. That site exists and is already mapped:
**the kind-8 helper servicer `0x001F20F8`** ("Site B" in the C4 rung). Its
caller is the per-frame engagement maintenance driver `0x001F7298`
(single jal caller 0x00164FC4, the gameplay tick):

```
001f7324  jal 0x001f00d8     ; lock-in establishment (parity arm A)
001f7334  jal 0x001f06a0     ; lock-in establishment (parity arm B)
001f733c  jal 0x001f20f8     ; kind-8 helper servicer   <- the host
001f7344  jal 0x001f1c20     ; per-frame engagement sweep
```

`0x001F20F8` loops players 0..10 of one side and, for each whose
+0x3E0 == 8 (delay-slot-guarded fall-through at 0x001F2150 — the site-region
census in §6.3 proves no other entry path), resolves the defender and
freezes the helper:

```
001f2148  lw   v0, 992(s0)          ; kind
001f2150  bne  v0, v1, 0x001f2234   ; != 8 -> next player
001f2158  jal  0x0013b798           ; resolve helper's link -> s2 = DEFENDER
001f2164  sw   zero, 36(s1)         ; helper staged_drive := 0   <- THE SITE WORD
001f21a8  swc1 f0, 488(s0)          ; helper speed_cmd := 0 ("Site B" freeze)
```

Execution every frame while attached is proven twice over: statically (the
driver above runs from the per-frame tick, unconditionally reaching
0x001F733C on both parity arms) and **live** — the freeze at this site is
what visibly pins the RT while attached (the measured 1.75-yd trail *is*
this code acting every frame). During the A2 capture window the helper
holds kind 7/8/2 while TE/DE sit in 5/6; every kind-8 frame enters this
body. The gate this gives for free is the law's own touch gate: **on the
slot-9 double, drive without the helper's mass is D ≈ 0 anyway (TE alone
vs the DE: R = 0.825), so "drive only while the helper is attached" is not
an approximation of the law — it is the law.**

The cave therefore performs the applier's addition (same fields, same
add-in-place semantics, +0x1A8 heading deliberately untouched) on the
defender, the primary, and the helper, once per attached frame. Nothing
fights it: during kinds 5/6 the engagement system writes no locomotion for
the pair (block-cycle.md, re-confirmed by P6/P7), the pair's clip has no
motion to add (that is the defect), and the helper's own locomotion is
zeroed by the very site we hook.

---

## 2. The gate — R5 byte-identical by construction

Applied in cave order, cheapest rejection first. All fields re-verified
this pass (offsets against addresses.yaml; role stamps against the manage
fn disassembly `001f6490/94/9c`: primary := 0, helper := 1, defender := 2,
unassigned = 5; slot-9 pre-snap reads 5 on all 22).

| # | test | field | rejects |
|---|---|---|---|
| 0 | helper kind == 8 | +0x3E0 (host's own guard at 0x001F2150) | everyone not an attached helper — the touch gate |
| 1 | defender kind ∈ {5,6} | s2+0x3E0 | pairs not captured into a pair animation (incl. every pass-pocket frame: capture is mode-gated off while the QB holds) |
| 2 | defender dt_role == 2 | s2+0x437 | every defender not currently the doubled man of a live DT record (pass sets doubly excluded: the registry never forms on pass — DT-1) |
| 3 | helper dt_role == 1 | s0+0x437 | kind-8 players that are not the record's helper (stale-role protection) |
| 4 | sides differ | s0+0x01 xor s2+0x01 == 1 | any same-side pairing artifact |
| 5 | link handle kind == 1 | s2+0x3E4 low byte | null/non-player links during transitions |
| 6 | D > 0 | computed | every non-dominant pair: **no store executes at all** |

**Byte-identical proof.** On any gate failure the cave's memory effect is
exactly the displaced stock store (`sw zero, 36(s1)` — the Site B freeze,
preserved verbatim) plus pure reads; no RNG is consumed, no timer touched,
no field written. On gate success with D ≤ 0 likewise. Therefore every
single block, every pass set, every near-equal or defence-winning pair, and
every frame outside a live record is byte-for-byte the stock world — not
statistically similar, identical. The continuous-D preference the mission
asked for is implemented as a hard skip: band-0 pairs are untouched by
construction, not by a tuned threshold. (The one theoretical IEEE edge —
position exactly -0.0 — cannot arise because no store happens when D ≤ 0.)

Role-2 defenders exist only while a record lives (roles stamped at record
creation, 5 otherwise), so the gate's lifetime is the record's. That makes
the companion word in §7 load-bearing for the oracle window.

## 3. Direction — the pair's own axis, and why not the bearing fields

What is available at the hook, with live values from the slot-8 pair:

| field | blocker 0:9 | defender 1:3 | meaning |
|---|---|---|---|
| +0x40C staged_bearing | 0x5BBF8E | **0x5BBF8E (identical)** | the pair's shared axis, stamped to BOTH at lock-in (`sw v0, 8(s2)` / `sw v0, 8(s3)` at 001f159c/15a8 = the primary's heading when he won) |
| +0x410 staged_facing | 0x5BBF8E | 0xDBBF8E (= bearing + 0x800000) | per-man facing along the axis; **the members differ by exactly 180°** |
| +0x1A8 heading | 0xF9F08D | 0x732E28 | live anim heading, wanders during the clip |

So the engine does stage the primary's earned bearing, shared and readable
off either member — but consuming it requires BAM24 → unit vector, i.e. the
engine's rotate helper `0x004ada50` whose axis convention this pass did not
pin (§10). The design instead derives the same direction from positions:

    axis = (defender+0x190 − primary+0x190, defender+0x194 − primary+0x194),
    normalized;  both bodies (and the helper) translate along +axis.

* **It is the primary's established bearing, geometrically:** the lock-in
  stamped his heading while driving into this defender; primary→defender is
  that same direction as built by the engagement itself, and it tracks the
  pile if the pair rotates (a seal block drives along the seal, which is
  the football-correct reading of S2).
* **Into the loser's backfield half-plane:** the blocker stands between his
  own backfield and the defender for as long as a block is live; the ray
  primary→defender therefore always crosses into the defensive half-plane.
  Slot-8 check: axis (+1.228, −0.397) with offence at higher z — the −z
  component points into the defence's half. ✓
* **Signing for both members:** there is nothing to sign. Both bodies get
  the SAME world vector (a rigid translation, exactly like the applier's
  burst); the 180° facing difference is a *facing* fact and facings are
  deliberately not consumed. No per-member flip exists to get wrong.
* Degeneracy is impossible for a live pair (bodies hold ~1.3 yd apart;
  measured) and guarded anyway: |axis|² < 0.0625 (0.25 yd) skips the frame.
* Feedback-stable: translating both endpoints by the same vector leaves the
  axis bit-identical; norm is constant; the drive cannot spiral.

Fallback if the operator sees sideways drives (§9 F4): swap the axis block
for the staged +0x40C bearing through `0x004ada50` — after its convention
is pinned with one rig probe.

## 4. The helper — carried, not chased

A2's constraint: the kind-8 RT is not a participant role of the paired clip
and never enters kinds 5/6; at baseline he trails 1.75 yd because this very
site zeroes his speed every attached frame while the pile he leans on holds
still. Once the pile translates, a frozen helper is left behind, falls out
of the 2.1-yd attach gate, detaches — and the law loses its helper term
(C4's diagnosis, confirmed by this pass's read of the freeze stores).

**Design: the cave adds the identical {vx, vz} to the helper's
+0x190/+0x194 in the same frame — the pile moves as one rigid body of
three.** The Site B freeze is *kept* (displaced store runs first): with the
pile's translation supplied directly, speed_cmd = 0 is again correct
station-keeping — the freeze and the carry are complementary, not rivals.
His own locomotion cannot fight the carry (it is the thing being zeroed),
and because he is carried, attach uptime and the D window become
self-reinforcing rather than self-defeating. Cost if he ever overruns the
pile visually: halve only his share (§9 F5, two-word change). This makes
rung C4 unnecessary rather than contradicted: its acceptance (attach uptime
> 80% while the pile translates) becomes one of this patch's watch metrics.

## 5. Magnitude — the mass law, computed in-cave, per frame

    M(p) = weight(+0xAEC, f32 pounds) + STR_eff(+0xB8E, u16 0..255)   [k = 1]
    R    = (M(primary) + M(helper)) / M(defender)        ; helper by presence
    D    = clamp( ±margin + R² − 1,  0,  3.0 )
    step = D / 64  yd per frame, along the §3 axis

* margin = the engine's own staged +0x404 (identical on both members —
  live-verified 0.5633/0.5633 on the slot-8 pair mid-kind-6, so capture
  does NOT zero the staged block). Its owner is re-derived exactly as the
  lock-in derived it: comp2 compare (+0x418); if the defence won the rep
  the margin counts *against* the drive. If the comps read 0 during a
  run-play capture (UNVERIFIED, §10) the term degrades to ±0 and the mass
  term alone drives — the double still lands at D ≈ 2.42.
* Slot-9 numbers, re-read from the state this pass (all five verified
  exact): TE 250+180, RT 302+231, DE 295+226 → **R = 1.8484, R²−1 =
  2.4165, D ≈ 2.42–2.72, step 0.0378–0.0424 yd/f** — 1.40–1.57 yd over the
  37-frame clip window. C alone on the NT: R = 1.023 → R²−1 = 0.047, and
  with no attached helper the cave never even runs for him.
* K = 1/64 (0x3C800000, lui-exact) is the calibration knob: cap D=3 gives
  0.047 yd/f ≈ 1.7 in/frame — visible skates, no warp. Range card:
  aggressive K = 3/128 (0x3CC00000), conservative K = 1/128 (0x3C000000);
  one word each.
* Interaction note (rule 1): if rung C2 (the confluence cave) ever deploys,
  +0x404 becomes D itself and this cave would double-count the mass term
  (bounded by the 3.0 cap). The integration edit is pre-specified: replace
  this cave's law section with a direct `lwc1 f10, 0x404(s2)` + clamp.
  Until then this cave is self-sufficient and testable alone.

---

## 6. The cave

### 6.1 Site: one word

```
patch=1,EE,001F2164,word,0C110C9C    // jal 0x00443270   (was AE200024: sw zero, 36(s1))
```

Stock word re-verified in the ELF and in the slot-9 savestate this pass
(both AE200024). The delay slot 0x001F2168 (`lw v0, 492(s0)`) is untouched
and runs before the cave. Mechanics: jal → ra = 0x001F216C; cave executes
the displaced store first and returns past the replaced word.

**Register audit at the site** (from the host's own body, 0x001F2148–
0x001F2234): live-in and preserved — s0 (helper), s1 (helper+0x3E0), s2
(defender), s3 (loop index), s4, s5, f20 (loaded once at 0x001F2128, used
at 0x001F21C4), gp/sp/fp. **v0 is live across the hook** (the delay slot's
load is consumed by `sw v0, 44(s1)` at 0x001F217C): the cave restores it by
re-executing the same load in its exit path — legal because nothing in the
cave writes helper+0x1EC. Dead and clobberable: at, v1, a0–a3, t0–t9,
hi/lo (no mult/div nor mfhi/mflo before the loop's next call), ra (next
redefined by the jal at 0x001F2178), and every FPU register except f20
(f12/f13 are re-loaded fresh at 0x001F2180/88 before their next use;
condition flag re-set by later compares). The §6.3 census proves nothing
jumps into 0x001F2148–0x001F2190 from outside, so the kind-8 fall-through
is the only entry and the audit holds on every path.

**FPU audit in the cave:** uses f0–f6, f10, f12–f14 (all dead at site),
never touches f20; every `c.lt.s` is separated from its `bc1x` by one
non-flag instruction (EE compare-to-branch hazard); `sqrt.s` uses the EE
encoding (operand in ft — the fpudis-documented trap); operand order of the
variable-shift/3-op-mult traps does not arise (`mult rd,rs,rt` is
GetPlayer's own idiom at 0x001655D0, copied).

### 6.2 The listing — 116 of 120 words, every word round-tripped

```
00443270 AE200024  sw zero, 0x24(s1)      ; displaced Site-B word: helper staged_drive := 0 (stock behaviour preserved)
00443274 8E4303E0  lw v1, 0x3E0(s2)       ; defender engagement kind
00443278 2461FFFB  addiu at, v1, -5
0044327C 2C210002  sltiu at, at, 2        ; 1 iff kind in {5,6} (captured pair)
00443280 1020006C  beq at, zero, exit     ; not captured -> nothing
00443284 92410437  lbu at, 0x437(s2)      ; (ds) defender dt_role
00443288 24030002  addiu v1, zero, 2
0044328C 14230069  bne at, v1, exit       ; defender not the doubled man (role 2) -> nothing
00443290 92010437  lbu at, 0x437(s0)      ; (ds) helper dt_role
00443294 24030001  addiu v1, zero, 1
00443298 14230066  bne at, v1, exit       ; we are not the record's helper (role 1) -> nothing
0044329C 92410001  lbu at, 1(s2)          ; (ds) defender side byte
004432A0 92030001  lbu v1, 1(s0)          ; helper side byte
004432A4 00611826  xor v1, v1, at
004432A8 10600062  beq v1, zero, exit     ; same side -> never drive
004432AC 8E4803E4  lw t0, 0x3E4(s2)       ; (ds) defender's engagement_link handle -> primary
004432B0 310900FF  andi t1, t0, 0xFF      ; handle kind byte
004432B4 24010001  addiu at, zero, 1
004432B8 1521005E  bne t1, at, exit       ; not a kind-1 player handle -> nothing
004432BC 00084A02  srl t1, t0, 8          ; (ds)
004432C0 312900FF  andi t1, t1, 0xFF      ; primary side
004432C4 00084402  srl t0, t0, 16
004432C8 310800FF  andi t0, t0, 0xFF      ; primary index
004432CC 8F8AB758  lw t2, -18600(gp)      ; [0x00600E48] player array descriptor
004432D0 11400058  beq t2, zero, exit     ; no world -> nothing
004432D4 95410008  lhu at, 8(t2)          ; (ds) per_side
004432D8 8D4B0000  lw t3, 0(t2)           ; player array base
004432DC 01214818  mult t1, t1, at        ; side * per_side (3-operand; GetPlayer's own idiom 0x001655D0)
004432E0 01284821  addu t1, t1, t0
004432E4 240114C0  addiu at, zero, 0x14C0 ; player stride
004432E8 01214818  mult t1, t1, at
004432EC 01695821  addu t3, t3, t1        ; t3 = PRIMARY player base
004432F0 C56C0AEC  lwc1 f12, 0xAEC(t3)    ; primary weight (real pounds)
004432F4 95610B8E  lhu at, 0xB8E(t3)      ; primary STR_eff (0..255)
004432F8 44810000  mtc1 at, f0
004432FC 46800020  cvt.s.w f0, f0
00443300 46006300  add.s f12, f12, f0     ; M_p
00443304 C60D0AEC  lwc1 f13, 0xAEC(s0)    ; helper weight
00443308 96010B8E  lhu at, 0xB8E(s0)
0044330C 44810000  mtc1 at, f0
00443310 46800020  cvt.s.w f0, f0
00443314 46006B40  add.s f13, f13, f0     ; M_h
00443318 C64E0AEC  lwc1 f14, 0xAEC(s2)    ; defender weight
0044331C 96410B8E  lhu at, 0xB8E(s2)
00443320 44810000  mtc1 at, f0
00443324 46800020  cvt.s.w f0, f0
00443328 46007380  add.s f14, f14, f0     ; M_d
0044332C 460D6300  add.s f12, f12, f13    ; M_p + M_h (helper counts: he IS attached here — S4-D)
00443330 460E6283  div.s f10, f12, f14    ; R
00443334 3C013F80  lui at, 0x3F80
00443338 44810800  mtc1 at, f1            ; 1.0
0044333C 460A5282  mul.s f10, f10, f10    ; R^2
00443340 46015281  sub.s f10, f10, f1     ; R^2 - 1
00443344 C5620418  lwc1 f2, 0x418(t3)     ; primary comp2 (who-drives-whom)
00443348 C6430418  lwc1 f3, 0x418(s2)     ; defender comp2
0044334C C6440404  lwc1 f4, 0x404(s2)     ; staged margin (same word on both members; live-verified)
00443350 46021834  c.lt.s f3, f2          ; defender comp2 < primary comp2 ?
00443354 44800800  mtc1 zero, f1          ; (hazard slot) f1 = 0.0 for the clamp test
00443358 45010004  bc1t margin_add
0044335C 00000000  nop
00443360 46045281  sub.s f10, f10, f4     ; defence won the rep: his margin counts against
00443364 10000002  beq zero, zero, clamp
00443368 00000000  nop
0044336C 46045280  add.s f10, f10, f4     ; margin_add: offence won: D = margin + R^2 - 1
00443370 460A0834  c.lt.s f1, f10         ; clamp: 0.0 < D ?
00443374 3C014040  lui at, 0x4040         ; (hazard slot) 3.0 bits
00443378 4500002E  bc1f exit              ; D <= 0: planted pair -- NOTHING is written
0044337C 44810800  mtc1 at, f1            ; (ds) 3.0
00443380 460152A9  min.s f10, f10, f1     ; cap: D <= 3.0
00443384 3C013C80  lui at, 0x3C80
00443388 44810800  mtc1 at, f1            ; K = 0.015625 yd/frame per unit D
0044338C 46015282  mul.s f10, f10, f1     ; step (yd/frame)
00443390 C6400190  lwc1 f0, 0x190(s2)
00443394 C5610190  lwc1 f1, 0x190(t3)
00443398 46010001  sub.s f0, f0, f1       ; ax = defender.x - primary.x
0044339C C6420194  lwc1 f2, 0x194(s2)
004433A0 C5630194  lwc1 f3, 0x194(t3)
004433A4 46031081  sub.s f2, f2, f3       ; az = defender.z - primary.z
004433A8 46000102  mul.s f4, f0, f0
004433AC 46021142  mul.s f5, f2, f2
004433B0 46052100  add.s f4, f4, f5       ; n2 = |axis|^2
004433B4 3C013D80  lui at, 0x3D80
004433B8 44810800  mtc1 at, f1            ; 0.0625 = (0.25 yd)^2
004433BC 46012034  c.lt.s f4, f1          ; bodies impossibly close?
004433C0 46040104  sqrt.s f4, f4          ; (hazard slot) norm — EE form: operand in ft
004433C4 4501001B  bc1t exit              ; degenerate axis -> nothing
004433C8 00000000  nop
004433CC 46045283  div.s f10, f10, f4     ; scale = step / norm
004433D0 460A0002  mul.s f0, f0, f10      ; vx
004433D4 460A1082  mul.s f2, f2, f10      ; vz
004433D8 C6460190  lwc1 f6, 0x190(s2)     ; ---- the applier's addition, relocated ----
004433DC 46003180  add.s f6, f6, f0
004433E0 E6460190  swc1 f6, 0x190(s2)     ; defender x
004433E4 C6460194  lwc1 f6, 0x194(s2)
004433E8 46023180  add.s f6, f6, f2
004433EC E6460194  swc1 f6, 0x194(s2)     ; defender z
004433F0 C5660190  lwc1 f6, 0x190(t3)
004433F4 46003180  add.s f6, f6, f0
004433F8 E5660190  swc1 f6, 0x190(t3)     ; primary x
004433FC C5660194  lwc1 f6, 0x194(t3)
00443400 46023180  add.s f6, f6, f2
00443404 E5660194  swc1 f6, 0x194(t3)     ; primary z
00443408 C6060190  lwc1 f6, 0x190(s0)
0044340C 46003180  add.s f6, f6, f0
00443410 E6060190  swc1 f6, 0x190(s0)     ; helper x (the carry, §4)
00443414 C6060194  lwc1 f6, 0x194(s0)
00443418 46023180  add.s f6, f6, f2
0044341C E6060194  swc1 f6, 0x194(s0)     ; helper z
00443420 3C010051  lui at, 0x0051
00443424 34214974  ori at, at, 0x4974     ; canary word: cave #11 free word 0x00514974
00443428 8C230000  lw v1, 0(at)
0044342C 24630001  addiu v1, v1, 1
00443430 AC230000  sw v1, 0(at)           ; driven-frame counter (stock-unreachable)
00443434 8E0201EC  lw v0, 0x1EC(s0)       ; exit: restore v0 = helper desired_bearing (site ds value)
00443438 03E00008  jr ra
0044343C 00000000  nop
```

Deploy every line `patch=1` (standing rule: load_state wipes `patch=0`
bodies). 0x00443440–0x0044344C (4 words) remain free. The canary word
0x00514974 gets **no patch line at all** — it is ELF-zero, state-zero, and
only the cave writes it; cave #11's free count drops from 3 words to 2
(0x00514978/7C).

### 6.3 Cave census — re-run from scratch this pass (rule: three "dead" caves went live)

Cave **#7, 0x00443270–0x0044344F** (480 B / 120 words), full-image sweep:

| test | result |
|---|---|
| `j`/`jal` targets into the range | **0** |
| branches into the range from outside (all forms: beq..bgtz, branch-likely, REGIMM incl. -likely/-al, COP1 bc1x) | **0** |
| lui 0x0044 + addiu/ori pairs completing to any address in-range (register-tracked, file-wide) | **0** |
| 32-bit data words equal to any in-range address, anywhere in the file | **0** |
| fall-through entry | impossible — preceding fragment ends `jr ra` + delay slot at 0x00443268/6C |
| ELF content | the dead byte-swap leaves (first word 0x00041202), exactly as surveyed |
| slot-9 savestate content | **all 120 words byte-identical to the ELF** — no diagnostic residue (the P6/P7 lines proved to live elsewhere; the state predates P1/P4 and is fully stock: 0x001F4A30 = 8E82005C, 0x001F6A74 = 2C42003D) |
| canary 0x00514974 | 0 in ELF, 0 in state; inside PT_LOAD; in the padding region owned by no object |

Site-region census: **zero** external jumps or branches target
0x001F2148–0x001F2190; 0x001F2164 is reachable only through the kind-8
fall-through, so the register audit's premises hold on every execution.

Residual (cannot be settled statically, same as every cave): runtime
overwrite of .text and computed-jalr reachability. **Runtime liveness test
1 gates first use** — #7 has never hosted executing code: execute-
breakpoints at 0x00443270 / 0x004432F0 / 0x00443390 through boot → menus →
a quarter → replay → save/load, must never trip, before the first measured
run. Fallback if #7 ever fails test 1: cave #2 (0x0044C1C0, 640 B,
lane-2-censused) — re-census it before use.

---

## 7. Companion arm (separate patch, own oracle): let the record outlive the capture

The gate lives exactly as long as the DT record (role bytes revert to 5 at
teardown), and the baseline record dies at ~64 — ~21 frames into the
37-frame 161 window — because **161 is NO-set** in the manage table:
`0x0058339C = 001F0C24` (re-verified in ELF and state this pass; yes-arm =
001F0C20; this is B1's edit retargeted to the clip A2 proved the double
actually plays — B1's own 158 word 0x00583390 stays stock so the C/NT
single keeps stock behaviour).

```
patch=1,EE,0058339C,word,001F0C20    // admit 161 to the yes-set  (was 001F0C24)
```

Deployment shape per rule 2: **arm B = this word alone** (oracle:
`dt_longest_hold` > 64 with kind-8 sightings during 5/6 persisting; motion
metrics pre-registered unchanged), **arm A = the cave alone** (partial
window, see oracle), **then A+B** (the full R3 oracle). The cave is
measurable alone; the companion buys the window the ≥ 1.0-yd acceptance
needs.

## 8. The oracle — pre-registered numbers

Baseline to beat (P1+P4 world, measured): defender_pushback +0.41 yd over
17 frames; DE dy −0.51 (penetrating); pair/helper gap at record end 1.75
yd; carrier_yards −0.70; record window 2..64.

Predicted mechanics on slot 9 (verified personnel): R = 1.8484, D = 2.42 +
margin ∈ [0, 0.3] → step 0.0378–0.0424 yd/frame while (helper kind-8 ∧
defender 5/6 ∧ D > 0).

**Execution canary (stock-unreachable):** [0x00514974] — ELF-zero,
state-zero, written by nothing in the image (pointer/branch census §6.3) —
must be > 0 after one slot-9 rep, and its per-play delta must equal the
harness's independent count of (helper k8 ∧ defender 5/6) frames. A
mismatch is itself diagnostic (gate 6 rejecting = D ≤ 0 frames).

| arm | MOVES (gates) | MUST NOT MOVE |
|---|---|---|
| A: cave alone | canary > 0; defender_pushback > +0.41 (predicted +0.5..+0.9 over the ~15–21 drivable frames); DE dy rises from −0.51 toward ≥ 0; helper gap at record end < 1.75 (the carry) | C/NT pair byte-identical; slots 6/7/8 frame-compare clean; every no-record single identical; determinism 3× frozen-seed green |
| B: yes-set word alone | dt_longest_hold > 64 (target ≥ 90); record end no longer coincides with capture+21 | pushback/carrier pre-registered UNCHANGED (161 still ships no motion — a null here is B working, not failing) |
| A+B | **defender_pushback ≥ +1.0 yd (R3; predicted 1.4–1.6 over ≥ 37 frames); carrier_yards > −0.70 and positive-trending; helper attach uptime > 80% of the record while the pile moves** | same as A, plus: no warps (step ≤ 1.7 in/frame by the D-cap — operator's eye is the instrument); no post-whistle drift (kind gate closes at teardown) |

Operator acceptance (the actual finish line): **#93 driven backwards, feet
churning (161 already animates the drive — that was the whole diagnosis),
TE and RT riding him as one pile, C/NT planted, nothing warps.**

S0 before every measured run: 0x001F2164 = 0C110C9C, 116 cave words as
listed, 0x0058339C = 001F0C20 (arm B), and PINE read-back of 0x00514974
before/after.

## 9. Failure modes and the bisect — one change per rung

| # | observation | reading | the single next change |
|---|---|---|---|
| F1 | canary = 0, S0 green | a gate never passes | the harness already samples kinds/roles per frame — name the failing gate from data; then NOP exactly that gate's branch (one word) and re-run; suspect order: helper role ≠ 1 (stale enum), handle kind ≠ 1 |
| F2 | canary > 0, pushback ≈ baseline | something re-derives +0x190 during 5/6 (the one census not run, §10) | one diagnostic run at K = 0.25 (word 0x3C80 → 0x3E80): teleport ⇒ adds partially eaten, raise K; still zero ⇒ position is re-derived — hunt the 5/6-active +0x190 writer with find_field_refs before any redesign |
| F3 | drives, but < 1.0 yd on A+B | window short: record still dying (re-check B in memory; if verified and dying, the manage-arm reading is wrong — D1 adjudication next) or attach uptime low | if window-bound: K one notch up, 1/64 → 3/128 (0x3CC0), one word; if attach-bound: the carry is under-holding him — investigate before touching K |
| F4 | moves laterally, dy flat while total displacement is right | pair axis rotated (seal geometry) | swap the axis block to staged_bearing +0x40C via 0x004ada50 — AFTER pinning its axis convention with one rig probe (§10); the 24 spare bytes hold the call |
| F5 | helper orbits or overruns the pile | full carry too strong for his geometry | scale only his two adds by 0.5 (mul.s f0/f2 copies into f7 before his block — 2 of the 4 free words) |
| F6 | visual horror at max D | cap too high | 0x4040 (3.0) → 0x4000 (2.0), one word |
| F7 | ANY no-record pair moves | impossible by construction unless role bytes are stale | probe +0x437 lifecycle around teardown; quarantine by reverting the site word (one line = complete stock restore) |
| F8 | determinism wobble | the two known wobbles both hid under drive scaling | quarantine the iteration, replay per standing rule 9 before believing any number |

Revert order: site word first (system returns to stock instantly — the
cave becomes unreachable dead code again), then arm B's word, then S0 to
confirm both stock values.

## 10. What I could not establish

1. **Who sends opcode-38 messages, and when, for spec-carrying clips.** The
   PROPC table's callers go through the runtime dispatch bus ([0x00609134],
   154 senders); the phase-0/2 sender for g3/g12 bursts was not named. Not
   load-bearing for this design (the chosen hook does not use the bus), but
   it is the missing piece of the burst lifecycle — and the reason the
   stock converter/applier sites had to be rejected on live evidence rather
   than on a decoded sender condition.
2. **An exhaustive census of +0x190/+0x194 writers active during kinds
   5/6.** The design rests on: the applier's own accumulate-in-place
   semantics, block-cycle.md's "root motion owns both transforms" plus
   P6/P7's proven-dead locomotion, and the live pair holding drifted
   positions frame over frame. Strong, but not the full writer census —
   F2 is the designed catch if a hidden re-deriver exists.
3. **comp2/+0x404 population during a RUN-play capture.** Live proof of
   population mid-kind-6 comes from a pass-play state (0.5633 on both
   members). If run captures zero them, the margin term contributes ±0 and
   the mass term alone drives — the design degrades gracefully, but the
   exact D loses its margin component. One A2-style probe read closes it.
4. **The rotate helper 0x004ada50's axis convention** (which component is
   forward at angle 0, rotation sense). Deliberately routed around via the
   position axis; must be pinned before F4's fallback is used.
5. **0x00260598's side semantics** — the servicer iterates
   `lbu [ [0x00601F4C]+0x40 ]`'s side only (presumed possession side).
   The gate makes this moot for correctness; it would only matter for a
   hypothetical defensive kind-8, which the DT lifecycle does not produce.
6. **dt_role enum completeness beyond {0,1,2,5}** and the exact frame the
   role bytes revert at teardown (a stale-role tail would extend the drive
   by at most a frame or two, bounded by the kind-5/6 gate).
7. **Provenance of the float garbage in pair-clip slots' +0x10 area**
   (harmless to this design; it is what disproved the burst-delivery
   premise, so naming its writer would complete §1.3's story).
8. **Frame-pacing cost of 117 patch=1 lines** (site + 116 body): the
   recompiler re-dirties the cave page every vsync. The standing rule
   (patch=1 everywhere) is followed; if pacing suffers on the rig, that
   rule — not this design — is the thing to revisit.

## P8 RESULT (2026-08-12): cave executes, gate starves it — 5 driven frames per play

S0 all words verified, canary zero pre-run. After 3 iterations:

    canary        15   (= 5 driven frames per play; cave DID execute)
    DE dy       -0.57   (baseline -0.51 -- still penetrating)
    DE dist      0.61   (baseline 0.60)
    TE-DE gap    1.01   (baseline 1.75 -- REAL IMPROVEMENT, pair stays with him)
    carrier     -0.70   (unchanged)
    window     2..57    (baseline 2..64 -- SHORTER despite the companion word)

**Diagnosis, and A2's series already contains it:** the gate requires the
helper in kind 8 AND the defender in kinds 5/6 simultaneously. The RT holds
kind 8 from f23 to f43; the DE does not enter 5/6 until f43. The overlap is
~5 frames -- by the time the defender is captured, the helper has dropped to
kind 7. At 0.038-0.042 yd/frame that is ~0.2 yd of drive: below the noise.

The law, the arithmetic and the cave are all sound. The SIMULTANEITY
REQUIREMENT is what starves them.

Two secondary findings:
* The gap closing 1.75 -> 1.01 yd is the drive working in miniature -- five
  frames of it measurably pulled the pair back onto him. That is the first
  positive displacement signal of the project.
* The window SHORTENED (64 -> 57) even with 161 admitted to the yes-set, so
  the companion word did not extend the record and something else ends it at
  ~57-64. The frame-64 ender is still unidentified (facings remain unsampled).

### Next change, single and precise

Widen the drive gate from "helper kind == 8" to "helper dt_role == 1" (the
record already proves the pairing) or to kinds {7,8}. Predicted canary jump
from 5 to ~35-40 frames per play, which at the measured step is 1.3-1.7 yd --
straight into R3's >= 1.0 target. One word in the cave's gate; everything else
stays. Bisect is trivial: revert the gate word alone.

## P8b (2026-08-12): widening the defender gate changed NOTHING — canary still 15

Gate widened from defender kind {5,6} to {4,5,6} (0x00443278 -> 2461FFFC,
0x0044327C -> 2C210003), applied live and verified, canary reset to 0.
Three iterations: **canary 15 again**, identical. Operator confirms visually:
"he still isnt being driven back."

**So the defender's kind was never the limiter, and my diagnosis of P8 was
wrong.** The ~5-frames-per-play starvation is upstream of that gate. Ruled in
as the remaining candidates, none tested:

* Site B (0x001F20F8) may not run per-frame per helper the way the design
  assumed -- 5 hits/play looks much more like ONCE PER LOCK-IN than like
  "every attached frame". The design's central premise ("proven to run every
  attached frame") is now in doubt and should be re-derived, not re-argued.
* One of the surviving gates rejects most frames: defender dt_role == 2,
  helper dt_role == 1, sides differ, handle kind 1, or D > 0.

**The cheapest way to find out is instrumentation, not reasoning:** give the
cave a second canary incremented at ENTRY (before any gate) and a third after
the dt_role pair. Comparing the three counts names the rejecting gate in one
run. Two spare words exist at 0x00514978/7C (cave #11's remainder, ELF-zero).

That is the next step, and it is a measurement rather than a patch -- which is
the discipline that has worked all session and which I skipped here by
inferring the limiter from A2's series instead of measuring it.

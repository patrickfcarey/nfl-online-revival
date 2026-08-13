# `ptrk` internals — static RE for the AI-coach rewire

Static investigation (2026-08-13) of the play-tendency tracker `ptrk`, in
support of `ai-coach-playcalling-requirements.md`. Binary: `extract/SLUS_207.52`
(SLUS-20752, CRC 0x14F8B841). vaddr = file_offset + 0xFF000, gp = 0x006056F0.
Every claim below is pinned to an address + quoted disassembly. Rule 4 applies:
these are re-derived from the image, not carried from prior docs.

Base pointer: `ptrk` struct at `*(gp−14396)` = `*0x00601eb4` (null in the ELF;
allocated at ctor). **Closed-set fact:** a whole-image scan for `lw/sw …(gp)`
with immediate `0xC7C4` returns **exactly 24 hits, all inside 0x0024CAFC–
0x0024E45C.** Nothing outside this module dereferences the struct pointer, so the
record fields can only be touched by functions in this module (the getters hand
out the two header floats, never a record pointer).

## Struct layout (re-derived)

Total **1556 bytes** — ctor allocates and zero-fills 1556
(`0024D8AC addiu a2,zero,1556` → alloc `0x0039d6c8`;
`0024D8D8 addiu a2,zero,1556` → memset `0x004b3e88`):

```
+0x000  f32  repetition factor f      (header)   written by recompute 0024D9F8
+0x004  f32  success factor s         (header)   written by recompute 0024DA08
+0x008  ..   (8 more header bytes; recompute writes a1+8 / a1+12 via 0024DC68)
+0x010  side-0 ring: 48 × 16-byte records  (768 B)
+0x310  side-1 ring: 48 × 16-byte records  (768 B)   (= +0x010 + 768)
+0x610  u16 count[0], u16 count[1]        (offset 1552)
= 1556
```

Record `ring[side][i]` address = `base + 16 + side*768 + i*16`
(`0024DA30 sll s2,a0,1` side*2 for counts; `0024DA38 mult a0,768`;
`0024DA50 addiu a0,a0,16`; `0024DA58 sll v0,a1,4` i*16).

### 16-byte record schema (all four "undecoded" slots decoded)

| off | type | meaning (re-derived) | setter | reader(s) |
|---|---|---|---|---|
| 0 | u32 | **own play id** (the offense's call) | AddPlay `0024DA20` (`AE110000 sw s1,0(s0)`) | RepFac, success, matchup-scan, hist-agg |
| 4 | u32 | **opponent play id** (the *defensive* call that snap) — a matchup secondary key | SetField4 `0024DAE8` (`AC450014 sw a1,20(v0)`) | matchup-scan `0024CF60` |
| 8 | u32 | **direction / zone bitmask** (from ball-trajectory floats) | field8fn `0024DB28` (`AE020008 sw v0,8(s0)`, value from helper `0024D5F8`) | hist-agg `0024CB7C`, serializer `0024DFA0` |
| 12 | u8 | small code = `lbu 2(v0)` of a lookup (`0013B798`) | `0024DB08` (`A045001C sb a1,28(v0)`) | serializer `0024DF94` |
| 13 | i8 | **yards gained** (`cvt.w.s` of a float delta) | `0024DB80` (`A043001D sb v1,29(v0)`) | success `0024CE80`, matchup-scan `0024CFA0` |
| 14 | u8 | **outcome category** (values 1..5 observed) | `0024DBA8` (`A045001E`, write-once guarded) | success `0024CE74` (==5), matchup-scan `0024CF70` |
| 15 | u8 | play-type code (1 or 2) | `0024DBD8` (`A045001F sb a1,31(v0)`) | field8fn `0024DB64`, serializer `0024E25C/0024E35C` |

(AddPlay writes byte 12 = 0xFF as a default marker: `0024DAA8 A203000C sb v1,12(s0)`
with `v1=-1`; the real per-byte setters overwrite as needed.)

Note: `docs/play-tendency-ai.md`'s "record = {playId, ~0x4eda, ~4, flags}" is
confirmed — 0x4eda was an **opponent play id** in field 4, and the "flags word"
is really four independent bytes (12/13/14/15). **Correction to that doc's
implication that fields 4/8/12 are undecoded: they are fully wired.**

---

## Q1 — Are the undecoded fields free to repurpose?

**Finding: NO field is unread. The closed-set census (24 pointer refs, whole
module disassembled) finds a live reader for every slot.** But the readers of
fields 4/8/13/14/15 are *precisely the legacy consumers the feature plans to
retire*, so "free" depends on that retirement, not on absence of readers.

Who writes each field: **all setters are called from a single play-recorder,
`0x00148900`** (verified by `jal` xref):

```
00148978 jal 0024DA20  AddPlay(side=s3, @0 = ownPlay)      ; a1 = [getPlaySt(s3)+4]
00148990 jal 0024DAE8  SetField4(side=s3, @4 = oppPlay)    ; a1 = [getPlaySt(s3^1)+4]  <- OTHER side
001489A8 jal 0024DBD8  set@15 = 2       (gateB 0x001F82E8 true)
001489B8 jal 0024DBD8  set@15 = 1       (gateB false)
001489E0 jal 0024DB28  set@8  = dir bitmask (helper 0x0024D5F8 over ball floats)
00148B1C jal 0024DBA8  set@14 = 4
00148D0C jal 0024DBA8  set@14 = 1   ; then 00148D2C set@13 = yards (sub.s f0-f12)
00148E0C jal 0024DBA8  set@14 = 3
00148EB4 jal 0024DBA8  set@14 = 2
00148F64 jal 0024DBA8  set@14 = 5   ; the "normal completed play" success counts
00148E40 / 00148F30 jal 0024DB08  set@12 = lbu 2(lookupC 0x0013B798)
```

`SetField4` proves **field 4 = the opposing play call** (`00148984 jal getPlaySt`
with `a0 = s3^1`, then `8C450004 lw a1,4(v0)` = that side's play id).

Who reads them (the only consumers, all in-module):

* **field 4** — the **matchup-average scan `0x0024CF08`** matches a record only when
  BOTH `@0 == ownPlay` AND `@4 == oppPlay`:
  ```
  0024CF54  8C620000  lw v0,0(v1)     ; @0 playId
  0024CF58  14C20016  bne a2,v0,skip
  0024CF60  8C620004  lw v0,4(v1)     ; @4 opponent play
  0024CF64  14A20013  bne a1,v0,skip
  0024CF70  9063000E  lbu v1,14(v1)   ; @14 category  (==1 → −5.0; else use yards)
  0024CFA0  8043000D  lb  v1,13(v0)   ; @13 yards → accumulate
  0024CFE0  46000843  div.s f1,f1,f0  ; average over matches
  ```
  This is the **"yards-scaled matchup memory"** that `ai-play-calling.md` marks
  for replacement.
* **field 8** — the **histogram aggregator `0x0024CAE0`** (12+ float buckets f4..f13)
  reads `@8` (`0024CB7C 8C640008 lw a0,8(v1)`), `@14` (`0024CB88 lbu 14`), `@15`
  (`0024CB74 lbu 15`). Called from `0x24d250` / `0x24e430`.
* **fields 13 & 14** — the **success factor `0x0024CE20`** (see Q4).
* **fields 8/12/15** — the **save serializer** (`0024DF94 lbu 12`, `0024DFA0 lw 8`,
  `0024E25C/0024E35C lbu 15`) — copy-through only.

**Verdict:** the three "undecoded" slots are **not free** in the strict
no-reader sense, but their *only* readers are the matchup-average scan
(`0x0024CF08`) and the histogram aggregator (`0x0024CAE0`) — the two legacy
selection inputs the campaign already intends to replace, plus pure save
copy-through. **They become free the moment those consumers are retired.** More
usefully: **field 8 already encodes inside/outside/left/right direction, field 13
already stores yards, field 14 already stores a 5-way outcome class** — a large
part of Layer-1's "track run direction and outcome" is *already recorded*; the
genuinely-missing dimensions are an explicit run/pass boolean, the **pass-target
player id** (nowhere stored), and 1st-down/TD flags (may fit a new `@14` value).

---

## Q2 — Resize feasibility (48 → 200/side)

**Finding: the "48" is baked into ~30 immediates across allocation, caps, per-side
stride, count offset, the band divisor, and TWO 4-entry weight tables with no
bounds clamp. Every site below must move together.** Enumerated:

**A. Allocation / clear (1556 → ~6416)**
```
0024D8AC  addiu a2,zero,1556   ; ctor alloc (0x0039d6c8)
0024D8D8  addiu a2,zero,1556   ; ctor memset (0x004b3e88)
0024E454  addiu a2,zero,1556   ; reset/clear path 0x0024E3F8
```

**B. Per-side ring byte-stride 768 (= 48*16)** — every setter/scan does
`side*768`. Sites (`addiu …,768`):
```
0024CAF0 0024CE24 0024CF0C 0024DA2C 0024DAEC 0024DB0C 0024DB2C
0024DB80 0024DBDC 0024DC6C 0024E0F4 0024E1FC 0024E304   (+ the setter fns)
```
→ 200/side = **3200**.

**C. Count-array offset 1552 (= 16 + 2*768)** — baked in the `lhu/sh` immediate:
```
0024DABC  addiu v1,v1,1552     ; AddPlay count update
0024E10C  lhu a3,1552(a0)      ; RepetitionFactor
0024CE40  lhu a2,1552(a0)      ; success
0024CF28  lhu t1,1552(a0)      ; matchup-scan
0024CB10  lhu v1,1552(v0)      ; hist-agg
```
→ counts move to **16 + 2*3200 = 6416**.

**D. The cap (AddPlay `0x0024DA20`)** — shift-down ring insert:
```
0024DA4C  addiu a1,zero,47     ; shift start index (copy rec[46]→rec[47] … rec[0]→rec[1])
0024DAAC  addiu a1,zero,48     ; movz cap value
0024DAD4  slti a0,v0,49        ; count+1 < 49 ?
0024DAD8  movz v0,a1,a0        ; else clamp to 48   (movz — flagged)
```
→ 199 / 200 / 201. Note the insert is an **O(n) full array memmove** every play
(`ldl/ldr/sdl/sdr` loop `0024DA68–0024DA88`); at 200 it copies 199 records/play —
functionally fine, ~4× cost, negligible.

**E. Band divisor 12 + the weight tables — the real hazard.**
RepetitionFactor and success both compute `band = i / 12` and index a **4-entry**
table with **no clamp**:
```
0024E124  addiu a2,zero,12     ; RepFac divisor
0024E148  divu a0,a2           ; i/12
0024E160  addu v0,v0,t2        ; t2 = 0x00540FE0 ; lwc1 table[band]
0024CE64  addiu a0,zero,12     ; success divisor (table 0x00540FF0)
```
Tables (verified, each exactly 4 floats):
```
0x00540FE0 repetition: 1/24, 1/48, 1/96, 1/192   (0.041667 0.020833 0.010417 0.005208)
0x00540FF0 success   : 1/16, 1/96, 1/192, ~1/372 (0.062500 0.010417 0.005208 0.002687)
```
At i∈[0,47], `i/12 ∈ {0,1,2,3}` — in range. **At 200, `i/12` reaches 16 →
reads 13 words past each 4-entry table into adjacent constant pool = garbage
weights and cross-table corruption.** This is the site that *cannot* be a
mechanical constant-bump: either extend both tables to ≥17 bands, or (cleaner,
per Layer 0) **reformulate the band lookup as a decay curve** — replace the
`divu/table` with `weight = expf(-k*i)` or a fixed-point equivalent, sidestepping
the table entirely and keeping the top-recency band hot for fast cheese
detection.

**F. Header/record-start +16** — `addiu …,16` to skip the header (e.g. `0024DA50`,
`0024E110`). Header stays 16 B, so these are unchanged **provided counts are
appended after both rings** (they are). Only the count offset (item C) moves.

**G. Save format** — see Q3; the persisted section grows and old saves need
migration.

**Invasiveness bottom line:** items A–D and F are mechanical constant edits
(one value each, ~30 sites) and low-risk. **Item E (the weight tables) is the
only place needing a design change, not a constant bump.** Item G (save
migration) is the genuinely risky part and is orthogonal to the in-RAM resize —
consistent with Layer 0's advice to *prove the logic at stock 48 first, then do
the resize + save migration as its own gated change.*

---

## Q3 — Save/load format

**Finding: `ptrk` is (de)serialized as a single named GBIN section `'STPG'` via
the generic format-string engine — NOT hand-serialized field-by-field in the
ELF.** The site `0x0024E458` is a thin stub that tail-jumps to the serializer:
```
0024E458  lui a0,0x5354        ; 'ST'
0024E45C  lw  a1,-14396(gp)    ; ptrk base
0024E460  j   0x003f2630       ; tail-call
0024E464  ori a0,a0,0x5047     ; a0 = 0x53545047 = 'STPG'
```
`0x003f2630` calls the GBIN command-string interpreter `0x004cb8c8` with a schema
string:
```
003F2658  jal 0x004cb8c8       ; a1 = fmt "to \x85 from 'IAES'\n" (0x005e91e8)
                               ; a2 = 'STPG', a3 = ptrk base
```
The ctor's load path uses the command string at `0x00586C70`:
`"select 'STPG' into \x89 from 'NIBG'\n"` (`'NIBG'` = `'GBIN'` reversed). So the
whole 1556-byte block is transferred as one tagged section; the escape codes
(`\x85`,`\x89`) are the engine's block/pointer specifiers. **There is no per-record
field walk in the ELF** — the block is opaque to the save layer.

**Consequence for resize:** the GBIN section is self-describing (it carries its
own length), so a *larger* struct simply writes a larger `'STPG'` section. But a
raw-blob load of an **old 1556-byte save** into a **6416-byte struct** places
side-1's ring and the count words at the *old* offsets (0x310 / 0x610), which no
longer match the new layout (0xC90 / 0x1910). **A resize therefore REQUIRES save
versioning/migration** — either a version byte in the section that triggers a
remap of the old side-1 ring + counts, or accept a one-time reset of the tracker
on first load of a pre-resize franchise. (`Hypothesis`, high-confidence: the
engine memcpy's by the stored section length; a live save/load diff or reading
`0x004cb8c8` would confirm the exact copy semantics, but the layout-relocation
conclusion holds regardless of copy detail.)

---

## Q4 — Tendency signals at record time

**Correction to the framing:** the signals are **not** captured at the recompute
`0x0024D9C0` — that function only calls RepFac + success and stores the two
header floats:
```
0024D9E4  jal 0x0024e0f0   ; RepetitionFactor(side, a1=play id)
0024D9F4  jal 0x0024ce20   ; success factor
0024D9F8  swc1 f0,0(v0)    ; store f  @+0
0024DA08  swc1 f0,4(a1)    ; store s  @+4
```
The signals are captured at the **play-recorder `0x00148900`** (the function that
calls AddPlay + all the setters, per Q1). That recorder already has, at play-end:

* **(a) run vs pass** — `Hypothesis`: encoded via the play-type gate
  `gateB 0x001F82E8` (called 3×), whose boolean drives `set@15 = 1 vs 2`
  (`001489A8/001489B8`) and branch selection. Also derivable from play id via the
  playbook classifier `0x0024CA98` (a 38-entry jump table mapping play id →
  category, returning small codes). A live read of `@15` on a known run vs known
  pass, or naming `0x001F82E8`, settles which is the clean run/pass bit.
* **(b) run direction inside/outside** — **already stored in `@8`.** field8fn
  `0x0024DB28` calls helper `0x0024D5F8`, which compares two ball-position float
  pairs (start vs end, `geomA 0x001F86C8` / `geomB 0x00146790`) and builds a
  **direction/zone bitmask** (bits 0x002/0x004/0x008/0x080/0x100/0x200/0x010 chosen
  by `c.lt.s`/`abs.s` on the deltas — left/right × short/deep × inside/outside):
  ```
  0024D61C  sub.s f0,f4,f2   ; Δx
  0024D620  abs.s f3,f0
  0024D624  c.lt.s f1,f3     ; |Δx| vs 4.5  → 0024D640 ori bits {8,512,2,128,256,4}
  0024D694  sub.s f3,f0,f1   ; Δy → 0024D6A8 ori v1,0x0010
  ```
* **(c) pass target player** — **NOT stored.** No record field holds a receiver
  index; the recorder captures play ids, geometry, yards, and outcome class, but
  never a target player id. This is the one Layer-1 dimension that needs a **new**
  field (or repurposing `@12`/`@15`) — it cannot be read out of the current ring.
* **(d) outcome 1st-down / TD** — `@14` is a **5-way outcome class** (values 1–5
  set at `00148B1C/00148D0C/00148E0C/00148EB4/00148F64`), `@13` is **yards**. The
  success factor already keys on `@14 == 5` ("normal completed play") and
  `@13 > 2`. Whether any existing `@14` value already means "1st down" or "TD" is
  unproven from statics (the branch predicates call game-state helpers
  `0x0013B798`/`0x001F8488`); `Hypothesis`: 1st-down/TD would be a **new `@14`
  value or a spare bit in `@12`**, both readable at this recorder since the full
  play result is in scope here.

**Summary:** run-direction (b) and outcome/yards (d-partial) are already
recorded and reusable; run/pass (a) is present as a code needing confirmation;
pass-target (c) is genuinely absent and needs a new field.

---

## Q5 — De-cheese consumer sites (spot-check)

**Finding: the 9 CPU-advantage consumers all still read the repetition factor
through getter `0x0024E188`, exactly as `play-tendency-ai.md` describes.** A `jal`
xref of the getter returns **exactly the 9 documented sites** and no others:
`0x147674, 0x186cc8, 0x1a6aa0, 0x1be924, 0x1ea364, 0x1ec748, 0x1ed99c,
0x1eeaec, 0x1f1250`. The success getter `0x0024E1C0` is read from 6 sites
(`0x1bdce0, 0x1be640, 0x1d29c4, 0x1d2d80, 0x1ed318, 0x1ed560`). Four spot-checked:

* **coverage break-off `0x001EEAEC`** — gated on controller 255
  (`001EEAE0 addiu v1,zero,255; 001EEAE4 bne v0,v1,skip`), then:
  ```
  001EEAEC jal 0x0024e188      ; f
  001EEAF4 mtc1 s0,f1; cvt.s.w ; s0 = AWR
  001EEAFC mul.s f1,f1,f0      ; AWR·f
  001EEB08 addu v0,s0,v0       ; AWR + AWR·f
  ```
* **Break-Block `0x001A6AA0`** — `(s3/2)·f` added to s3
  (`001A6AA8 srl+add+sra = s3/2; 001A6ABC mul.s; 001A6AC8 addu s3,s3,v0`).
* **Tackle `0x00186CC8`** — `(s3/3)·f` (`001886CDC div s3,3; 001886CF4 mul.s`).
* **event-rate `0x00147674`** — `2·f` clamped to 1.0 then used as a denominator
  reduction (`0014767C add.s f2,f0,f0; 00147694 min.s f2,f2,1.0`).

All four apply the factor as documented. **The getters are the single choke
point** — the de-cheese step (Layer 3) can neuter all 9 rating consumers by
making `0x0024E188` return 0 (or gating it), without touching the 15 call sites,
while leaving the *history recording* and the *play-selection* reads intact. That
is the cleanest de-cheese seam.

---

## Bottom line

* **Q1:** No slot is unread; the "undecoded" fields are opponent-play (`@4`),
  direction bitmask (`@8`), yards (`@13`), 5-way outcome class (`@14`), type code
  (`@15`) — read only by the matchup-scan `0x0024CF08` and histogram
  `0x0024CAE0` (both legacy selection inputs slated for replacement) plus save
  copy-through. Free once those are retired; and `@8`/`@13`/`@14` already carry
  much of what Layer 1 wants.
* **Q2:** ~30 baked immediates (alloc 1556, stride 768, count-offset 1552, caps
  47/48/49) are mechanical bumps; **the one non-mechanical site is the band
  divisor 12 + the two unclamped 4-entry weight tables** (0x00540FE0/0x00540FF0),
  which overrun at 200 and should be reformulated as a decay curve.
* **Q3:** Saved as one opaque GBIN section `'STPG'` via the format-string engine
  (`0x0024E458`→`0x003f2630`→`0x004cb8c8`), not field-by-field; **resize needs
  save versioning/migration** because side-1 ring + counts relocate.
* **Q4:** Signals live at the **recorder `0x00148900`**, not the recompute:
  direction (b) and yards/outcome (d) already recorded; run/pass (a) present as a
  code (`@15`/gate `0x001F82E8`) needing confirmation; **pass-target (c) is not
  stored — needs a new field.**
* **Q5:** All 9 rating consumers still read `0x0024E188`; the getter is the
  single de-cheese choke point.

**How invasive is 48→200 really?** The **in-RAM resize is moderate and largely
mechanical** — one design decision (the decay-curve weight reformulation) and a
batch of constant edits, all in one 6 KB module, no external code touches the
struct. **The expensive/risky half is the persistence**: growing the `'STPG'`
section relocates the second ring and forces a save-format version + migration
path for existing franchises. Phase them: prove the tracking/calling logic at
stock 48 (no save risk), then land the resize + migration as a separate gated
change — exactly the Layer-0 sequencing.

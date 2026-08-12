# The mass-drive law — formula, real numbers, and where it plugs in

Static lane 2 for the Route C work (`docs/on-skates-requirements.md`, "ROUTE C
CONFIRMED"). Investigated 2026-08-11 against `extract/SLUS_207.52`
(vaddr = offset + 0xFF000, gp = 0x006056F0) with `recon/mipsdis.py`, and
against `experiments/states/double_team_slot9.p2s` read offline with
`tools/statereader.py`. Every instruction quoted below was re-read from the
image this pass; every roster number below was read from the savestate this
pass. Anything not re-derived is marked UNVERIFIED.

The operator's specification (verbatim intent, from the requirements doc):
drive proportional to the attackers' combined `weight + k*STR` against the
defender's; the helper counted only while touching; "basically overpowering"
for a real double; the same model applied when big guys block smaller ones.
One continuous formula, no special cases — stalemates stay planted and R5
protection comes from the shape of the curve.

Context that shapes everything here: **Route C is confirmed** — animation
root motion owns both bodies during a block. speed_cmd (P6) and velocity (P7)
are proven dead levers for moving an engaged defender. So this law is a
**selector input** (which clip family the pair gets) first, and a drive
magnitude second. Lane 1 (`docs/anim-lanes/1-dispatcher.md`, sibling, not yet
present as of this writing) owns the dispatcher; this document owns the
formula, its calibration from real memory, and the hook that computes it.

---

## 1. The existing contest arithmetic, quoted from the image

### 1.1 The composites (`0x001f0c40`, sole caller: the lock-in `0x001f14d0`)

Register frame, from the prologue (`0x001f0c40–0x001f0c9c`): s5 = A (blocker,
a0), s6 = B (defender, a1), s1 = A+0x404, s2 = B+0x404, s0 = A+0xB70
(ratings), s3 = B+0xB70, s7 = (block_mode == 1), i.e. pass:

```
001f0c88  lw v0, 1008(s5)           ; A block_mode (+0x3F0)
001f0c8c  xori v0, v0, 0x0001
001f0c90  sltiu s7, v0, 1           ; s7 = 1 iff pass block
001f0c94  beq s7, zero, 0x001f0ca4
001f0ca0  lh s4, 22(s0)             ; pass: s4 = PPBK  (ratings[11])
001f0ca4  lh s4, 24(s0)             ; run:  s4 = PRBK  (ratings[12])
```

**Blocker comp1 (+0x414, POWER)** — the whole computation:

```
001f0ca8  lh v0, 30(s0)             ; PSTR (ratings[15])
001f0cac  lwc1 f1, 2796(s5)         ; weight (+0xAEC, f32, REAL POUNDS)
001f0cb0  addu v0, s4, v0           ; BLK + STR
001f0cb4  lwc1 f12, -26132(gp)      ; 0x005ff0dc = 0.33
001f0cb8  mtc1 v0, f0
001f0cbc  cvt.s.w f0, f0
001f0cc0  add.s f0, f0, f1          ; + weight
001f0cc4  mul.s f12, f0, f12        ; value * 0.33
001f0cc8  jal 0x0039a9a8            ; float->int
001f0ccc  swc1 f0, 16(s1)           ; store base value to +0x414
001f0cd4  jal 0x002f9428            ; RandInt(0, value*0.33)
...
001f0d10  add.s f0, f0, f1          ; comp1 += jitter
001f0d28  swc1 f0, 16(s1)
```

The same pattern repeats for all six composites. Verified formulas (effective
ratings are u16, 0..255 = trunc(rating×2.55); weight is f32 real pounds):

| slot | blocker (verified sites) | defender (verified sites) |
|---|---|---|
| +0x414 comp1 POWER | `(PPBK\|PRBK) + STR + WGT` (0x001f0ca8–0cc0) | `TAK + STR + WGT` (0x001f0e38–0e54) |
| +0x418 comp2 FINESSE | `(PPBK\|PRBK) + AWR/2 + STR/2 + AGI` (0x001f0d0c–0d54) | `AGI + STR + AWR` (0x001f0eac–0ebc) |
| +0x41C comp3 late | `(PPBK\|PRBK) + AWR + AGI + STR + WGT` (0x001f0db4–0dd8) | `TAK + AWR + AGI + STR + WGT` (0x001f0f1c–0f44) |

Each of the six then gets `+= RandInt(0, 0.33 × value)` — all six jitter
scales read 0.33 (data words 0x005ff0dc–0x005ff0f0, read from the file this
pass). So each composite is its base value ×[1.00, 1.33) per lock-in, both
sides rolled independently.

After the six composites: a per-position-class scale (`0x001ef0c8`, jump
table on player+0xB06 at 0x005832A0 — applied to BOTH men, 0x001f0e30 and
0x001f0f9c), then the phase modifiers documented in
`pass-vs-run-blocking.md` (run-only home ×1.1 — 0x005ff0f4/f8 both read 1.10;
pass-only pocket-collapse decay, rusher gain, etc.). The sliders scale the
blocker's three components (0.5/0.5/0.35 strengths, slot 1 pass / slot 4 run).

**The engine's own idiom is the mass law's precedent:** comp1 already adds
effective STR (0..255) *directly to real pounds* — one point of effective STR
counts exactly one pound. Weight's census (drive-machinery Q4, re-confirmed
at the four contest sites 0x001f0cac / 0dc4 / 0e40 / 0f30 this pass): **17
readers image-wide, every one contest-side; weight has ZERO motion uses.**
The only mass-aware motion code in the image is the collision layer's
`1/(weight × 335.4)` at 0x00213038 — dead for engaged pairs (mutual
no-collide), but proof that per-player inverse mass is an in-engine pattern.

### 1.2 What the composites become: the lock-in margin chain (`0x001f14d0`)

Register frame, from the prologue (verified 0x001f14d0–0x001f1524): **s0 = A
raw base, s4 = B raw base**, s2 = A+0x404, s3 = B+0x404, s5 = A+0x190,
s6 = A+0x3E0; ra saved at 160(sp), f20 at 176(sp), f21 at 184(sp) — all
restored in the epilogue (0x001f16ec–0x001f1718). A liveness scan of the body
(every writes_gpr between 0x001f1528 and 0x001f16ec) found **no write to s0
or s4** — the raw player bases are live at the confluence.

```
001f153c  jal 0x001f0c40            ; fill all six composites
001f1544  lwc1 f4, 20(s2)           ; A comp2
001f1548  lwc1 f3, 20(s3)           ; B comp2
001f154c  c.lt.s f3, f4             ; comp2 decides WHO DRIVES WHOM
...A-wins arm:
001f156c  sub.s f0, f4, f3
001f1580  div.s f21, f0, f4         ; f21 = (A2-B2)/A2
001f1590  sub.s f0, f1, f2          ; comp1 margin (f1/f2 = A/B comp1)
001f15a0  div.s f20, f0, f1         ; f20 = (A1-B1)/A1  = the DRIVE
001f15a4  bne a0, v1, 0x001f16dc    ; a0 = A block_mode; !=1 -> confluence
...pass-only LOS freeze (S5, preserved by this design):
001f15cc  lui at, 0x3fc0            ; 1.5 yd
001f15dc  c.lt.s f0, f1
001f15e4  bc1fl 0x001f16e0          ; not inside 1.5: store raw f20, skip
001f15ec  mtc1 zero, f20            ; inside: DRIVE = 0
001f15f0  beq zero, zero, 0x001f16e0
...split-decision arms multiply both margins by 0.5 (0x001f1614/18, mirrored
   0x001f16d0-d8), defender-wins arm computes the mirror margins...
THE CONFLUENCE (identical stores into BOTH men):
001f16dc  swc1 f20, 0(s2)           ; A staged_drive (+0x404) = comp1 margin
001f16e0  swc1 f20, 0(s3)           ; B staged_drive = same
001f16e4  swc1 f21, 4(s2)           ; A +0x408 = comp2 margin (no known reader)
001f16e8  swc1 f21, 4(s3)           ; B +0x408 = same
```

Branch census into the confluence (every branch in the function, this pass):
targets 0x001f16dc ← 0x001f15a4, 0x001f1618, 0x001f165c (+ fall-through from
16d8); **target 0x001f16e0 ← 0x001f15e4 / 0x001f15f0, the pass-freeze path
only**, entering with f20 already zeroed and A's store already done at
0x001f15e8/0x001f15f4. This matters for the hook (§5): a call placed at
0x001f16dc is never reached by the pass-freeze path, so S5's containment
survives by construction.

What the margin is then used for: the per-frame sweep `0x001f1c20` re-stamps
it into live locomotion **of both men** every frame the kind-4 engagement
holds (re-verified: `swc1 f0, 488(s1)` at 0x001f2068, `swc1 f0, 488(s2)` at
0x001f2084, f0 loaded from staged +0x404). Under Route C that copy moves the
blocker's gait, not the defender's body — which is exactly why this law lands
in the *selector*, with the +0x404 write as its carrier.

---

## 2. Real values, read from `double_team_slot9.p2s` this pass

Player array walked from the descriptor at [0x00600E48] (base 0x00661B90,
stride 5312). The state is pre-snap: engagement fields 0, contest comps 0.00,
DT registry empty — so **comp values could not be read populated** (see §7);
weights and effective ratings are populated and are the law's actual inputs.

The slot 9 double team is TE+RT on the right DE; the C takes the NT head-up
(`double-team-requirements.md`: "TE+RT-on-DE", "LG+C on the NT" as the
future second record). Relevant personnel (weight +0xAEC f32; effective
ratings +0xB70, STR = index 15, u16 0..255):

| player | weight (lb) | STR | RBK | TAK | M = W + k·STR (k=1) |
|---|---|---|---|---|---|
| TE (0,5) | 250.0 | 180 | 159 | – | 430 |
| RT (0,10) | 302.0 | 231 | 218 | – | 533 |
| C (0,8) | 299.0 | 226 | 216 | – | 525 |
| RG (0,9) | 322.0 | 231 | 224 | – | 553 |
| LT (0,6) | 305.0 | 229 | 216 | – | 534 |
| FB (0,2) | 248.0 | 206 | 133 | – | 454 |
| WR (0,4) | 212.0 | 154 | 126 | – | 366 |
| DE-right (1,3) | 295.0 | 226 | – | 224 | 521 |
| NT (1,2) | 307.0 | 206 | – | 200 | 513 |
| DE-left (1,1) | 280.0 | 198 | – | 208 | 478 |
| MLB (1,5) | 245.0 | 203 | – | 224 | 448 |
| CB (1,10) | 200.0 | 144 | – | 175 | 344 |

---

## 3. The law

### 3.1 Formula

For a locked pair (A = comp2 winner's side, B = loser), in engine units:

    M(p) = weight(p) + k · STR_eff(p)          W: +0xAEC f32 pounds
                                               STR_eff: +0xB8E u16 (0..255)
    R    = ( M(winner) + M(helper) · [touching] ) / M(loser)
    D    = clamp( margin + R² − 1,  0,  3.0 )

where `margin` is the engine's own staged normalized comp1 margin (f20, the
thing the confluence stores today — skill, jitter, sliders, split-halving and
the S5 freeze all already folded in), and `[touching]` = the helper is
attached (engagement kind 8; §4.3). One expression, no cases:

* **R ≈ 1 (stalemate): D ≈ margin ≈ today's value.** Nothing changes for
  ordinary play — R5 protection is the `R²−1 ≈ 0` region of the curve.
* **R > 1 (mass advantage): D grows as R².** A real double lands at
  R ≈ 1.85 → +2.4, "basically overpowering", regardless of the jitter roll.
* **R < 1 (small man wins the rep anyway): R²−1 goes negative** and eats his
  margin — a WR who beats a DE on ratings still cannot drive him. Mass
  damping (S3) is the same term with the sign the other way.
* **The helper joins the sum only while attached** — S4-D's touch gate — and
  the defender side is the same formula with no helper (a winning defender
  driving a small blocker back is the "big guys block smaller" clause,
  symmetric by construction).

### 3.2 k — fixed at 1.0 in effective units, and why

k = 1 effective point per pound **is the engine's own convention**: comp1
adds STR_eff straight to pounds (0x001f0cb0–0cc0, §1.1). On the 0–100 rating
scale that is 2.55 lb per STR point — a 99-STR player carries +252 lb of
muscle-equivalent, i.e. STR and body weight are roughly equal partners, which
is also how the blocking power axis (`STR + AGI + trunc(weight) + PBK|RBK`,
0x001efd14) treats them.

Measured sensitivity kills k as a tuning knob: across every real matchup in
§3.4, moving k from 0.5 to 2.0 moves R by **less than ±2.5%** (weight and
STR are strongly correlated in real personnel; the ratio barely notices the
mix). The curve knees are where the tuning budget belongs. k stays a
one-instruction change (scale the STR term) if a range card ever wants it.

### 3.3 The curve — why R², why the deadband is not needed

* `R²` is one `mul.s`. It is what turns 1.85 into 2.4 ("basically
  overpowering") while leaving 1.02 at 0.05 (planted): the tier separation
  the operator asked for comes from the exponent, not from special cases.
  With p = 1 the real double scores D ≈ 0.86 — inside single-block range,
  failing the "overpowering" requirement; p = 2 is the smallest integer
  exponent that separates the tiers. (p = 1 kept as the conservative card.)
* No hard deadband: near-equal pairs are protected by *two* multiplied
  facts — their margin is small AND their `R²−1` is ≈ 0. Jitter analysis:
  the 0.33 jitter can push a near-equal pair's margin to at most ≈ 0.25 +
  base; for every slot 9 trench single the worst-case roll stays under 0.31,
  below the drive knee. The double's R² term (+2.42) dwarfs the jitter band
  entirely: **stalemates are planted on every roll, the double overpowers on
  every roll, and only genuine mismatches (LT vs the weak DE at +0.25 base)
  oscillate across the knee rep-to-rep** — per-rep variance exactly where
  football has it.
* Clamp to [0, 3.0]: floor because a negative drive is a nonsense selector
  input (the loser's arm handles the other direction); ceiling because
  staged_drive feeds speed_cmd while the pair translates (§5.4) and 3.0
  preserves the ordering between "double vs 350-lb NT" (1.65) and "double vs
  240-lb DE" (2.4) that a 2.0 cap would erase.

### 3.4 The numbers (slot 9 personnel + archetypes, computed, k = 1)

`margin` column = pre-jitter comp1 margin from §1.1's verified formula
(RBK|TAK + STR + WGT), primary only — the engine never sums a double's
comps, which is precisely the gap the R term fills. Bands per §4.2 knees
(drive ≥ 0.40, overpower ≥ 1.5).

| attackers → defender | R | R²−1 | margin | **D** | band | reading |
|---|---|---|---|---|---|---|
| **RT+TE → DE-right** | **1.848** | +2.416 | 0.008 | **2.42** | **2 skates** | the real double: overpowering on every roll |
| C → NT | 1.023 | +0.047 | 0.038 | 0.09 | 0 planted | the head-up stalemate stays a stalemate |
| RT → DE-right | 1.023 | +0.047 | 0.008 | 0.06 | 0 planted | same DE, no helper: planted — S4 is the formula's shape |
| TE → DE-right | 0.825 | −0.319 | (loses) | 0.00 | 0 | TE alone is underpowered; DE's own arm: D ≈ 0.68 → band 1, **the DE walks him back** |
| LT → DE-left | 1.117 | +0.248 | 0.085 | 0.33 | 0/1 edge | genuine mismatch: drives only on hot rolls |
| RG → NT | 1.078 | +0.162 | 0.082 | 0.24 | 0 planted | |
| FB → MLB | 1.013 | +0.027 | ~0 | 0.03 | 0 planted | iso root: back runs off it |
| WR → CB | 1.064 | +0.132 | ~0 | 0.13 | 0 planted | perimeter stalk stays a stalk |
| WR → DE-right | 0.702 | −0.507 | (loses) | 0.00 | 0 | and the DE's arm drives the WR: big-on-small, free |
| 300lb/90 OL → 240lb/80 DE | 1.191 | +0.420 | 0.113 | 0.53 | 1 drive | a won 60-lb mismatch grinds forward |
| 300 OL → 350lb/95 NT | 0.894 | −0.202 | – | 0.00 | 0 | nobody singles the mountain (S3) |
| 300 OL + 250 TE → 350 NT | 1.617 | +1.613 | ~0 | 1.61 | 2 skates | a true double moves even him — slower than the DE (1.61 < 2.42), S3 inside S4 |
| 300 OL + 250 TE → 240 DE | 2.155 | +3.646 | 0.113 | 3.00 (cap) | 2 skates | |
| 300 OL → 190lb nickel | 1.669 | +1.785 | 0.376 | 2.16 | 2 skates | the guard pancaking a nickel — operator's own example |
| 190 WR → 240 DE | 0.743 | −0.448 | – | 0.00 | 0 | |
| 190 WR + 190 WR → 240 DE | 1.486 | +1.210 | ~0 | 1.21 | 1 drive | two receivers ride a DE, don't pancake him |

Every row lands where football says it should, from one formula.

---

## 4. Where it plugs in — what the dispatcher needs

### 4.1 The carrier: D lands in +0x404, stamped by the lock-in hook

The single point where the drive value reaches both men is the confluence
(0x001f16dc–16e8). The patch (§5) replaces the raw margin stores with
D-stores: **player+0x404 (staged_drive) holds D, on both members, from the
first lock-in of every pair and at every re-lock.** That gives lane 1's
dispatcher a per-pair scalar at a known offset on either man, computed before
any kind-5/6 conversion can happen (no kind-4 pair exists without a lock-in —
the establishment fns 0x001f00d8/0x001f06a0 call it), with no new field
claimed and no unproven-dead scratch space trusted (+0x408 stays virgin; its
no-reader status is an unproven negative under rule 4).

### 4.2 Boolean, magnitude, or index — cost and verdict

| shape | producer cost | consumer cost (in the dispatcher's cave) | loses |
|---|---|---|---|
| boolean (offence dominates?) | 2 compares | 1 branch | the overpower tier, all future tuning becomes re-patch |
| **magnitude (D, f32 at +0x404)** | **0 extra — §5 stores it anyway** | **lwc1 + c.lt.s + bc1x ≈ 5 words per knee; 2 knees ≈ 9–10 words** | nothing |
| index (band 0/1/2 in a byte) | 2 compares + a store | 3–4 words (indexes a clip table directly) | needs a scratch byte, and **no player byte is proven free** |

**Verdict: magnitude, thresholded at the consumer.** The index is cheapest to
consume, but parking it requires claiming a field whose deadness is exactly
the class of negative this project has been burned by; the ~5-word saving is
not worth it. The dispatcher reads D from either member's +0x404 and cuts:

    D < 0.40          -> band 0: neutral clip (today's 158-family look)
    0.40 <= D < 1.5   -> band 1: drive clip   (feet churning, steady ground loss)
    D >= 1.5          -> band 2: overpower    (skates / pancake family)

Knees justified by §3.4: 0.40 clears every slot 9 trench single at max
jitter (worst 0.31) while catching real mismatches (0.47–0.53); 1.5 splits
"two men or a huge single" (1.6–3.0) from "modest advantage" (0.5–1.2).
Range cards: aggressive {0.25, 0.9}, conservative {0.6, 2.0}; and p = 1 with
knees {0.2, 0.7} as the low-drama fallback. If lane 1 finds the pair's clip
choice already keyed off staged +0x404/+0x408 or the 0x002cfc00 args, the
selection patch may collapse into thresholds inside code that already reads
this value — check that first.

### 4.3 "Helper counted only while touching" is free — a per-frame re-lock
### already fires while a helper is attached (doc correction included)

The touch gate: defender.dt_role (+0x437) == 2, registry record
(table [gp−17520] + 4 + 20·dt_record, helper handle at +4) resolves to a
player whose engagement (+0x3E0) == 8. Kind 7 (assigned, running in) does not
count; kind 8 (attached) does — exactly S4-D's gate.

Freshness is the engine's own doing. In the kind-8 per-frame fn `0x001f20f8`
(helper → link → partner s2 = the defender → link → a0 = the primary), the
re-lock latch is set on the primary **in the delay slot of a plain bne**:

```
001f21fc  lw v1, 992(a0)            ; primary's kind
001f2204  bne v1, v0, 0x001f2234    ; not 4 -> skip everything
001f220c  lw v0, 1008(a0)           ; primary's block_mode
001f2210  bne v0, a1, 0x001f2220    ; plain bne: selects PBK vs RBK ONLY
001f2214  sb a2, 1070(a0)           ; DELAY SLOT: +0x42E latch := 1 — RUNS
001f2218  beq zero, zero, 0x001f2224;             ON BOTH PATHS
001f221c  lhu v0, 2950(a0)          ; pass: PBK   (taken-path slot)
001f2220  lhu v0, 2952(a0)          ; run:  RBK
```

`0x14450003` is plain `bne`, not branch-likely — its delay slot executes
regardless. **So the latch is set for run pairs too; drive-machinery.md's
"pass-pro pairs re-lock every frame; run pairs never do" is wrong** — the
mode test only chooses which rating feeds the timer re-init. Consequence for
this law: while a helper is attached, the pair re-runs lock-in (and therefore
the §5 cave) **every frame** — R includes the helper within one frame of
attachment and drops him at the rep after detachment. No staleness patch
needed. (Residual gate: the undecoded predicate `0x001f7a38(partner)` at
0x001f21c0 can skip this block — §7.)

### 4.4 Leaf-ABI fallback (only if lane 1 finds +0x404 unusable at choice time)

If kind-5/6 conversion turns out to zero the staged block (unverified, §7),
the cave exports a second entry: a0 = winner, a1 = loser, returns f0 =
`R²−1` (pure mass term — by clip-choice time the skill contest is already
expressed in who won) and v0 = its band. Same body minus the margin add;
~8 extra words. Not built until lane 1 asks.

---

## 5. The patch

### 5.1 Site: one word

```
0x001F16DC:  0C110C9C    jal 0x00443270        (was: E6540000 swc1 f20, 0(s2))
```

The delay slot 0x001F16E0 (`swc1 f20, 0(s3)`) is unchanged: it stores the raw
margin to B before the cave runs, and the cave overwrites both copies with D.
ra is dead at this point (restored from 160(sp) in the epilogue at
0x001f16ec), so the jal clobbers nothing. **The pass-freeze path
(0x001f15e4/0x001f15f0) enters at 0x001F16E0, past the jal — a frozen pass
set keeps drive = 0 and never reaches the law: S5 preserved by construction,
zero words spent.** All other arms (A-wins, defender-wins, both splits) pass
through 0x001F16DC and get the law.

### 5.2 Cave: #7 at 0x00443270 — re-censused this pass

Full census re-run against the ELF (j/jal targets, all branch forms incl.
REGIMM and COP1, cross-function lui/addiu+ori pairing, and the file-wide
32-bit pointer-word scan): **cave #7 (0x00443270, 480 B / 120 words): zero
external references. Clean.** Its prior use was the P6/P7 diagnostic pnach
lines only — runtime patches, ELF content untouched; those diagnostics are
retired. Also re-verified: cave #11 (0x00514920) has exactly 3 free words
(0x00514974/78/7C, zero-filled; 21 of 24 words held by the deployed P1
market-guard) — too small for this. Cave #2 (0x0044C1C0, 640 B) also
re-censused clean, held as the fallback.

**Survey correction found on the way: cave #3 (0x0045F598) is NOT dead.**
Two real address materializations reach inside it: `addiu t0, s6, -2664` at
0x00460178 → 0x0045F598 and `addiu t1, s7, -2624` at 0x00460180 →
0x0045F5C0 (a 76-byte copy loop's buffers). `code-caves.md`'s table entry
for #3 must be struck — the original survey's lui-pairing window missed a
cross-function pair, the same failure class as the 0x00139AA0/#1 burn.

### 5.3 The routine (~55 of 120 words; register proof in §1.2)

Live-in: s0 = A, s4 = B (raw bases, proven unclobbered), s2/s3 = A/B+0x404,
f20 = staged margin (post-split, post-freeze), gp valid. Must preserve: f21
(stored by the site at 0x001F16E4/E8 after return), all s-regs, sp. Free:
at, v0, v1, a0–a3, t0–t9, f0–f19 minus f21 — the fn is one instruction from
its epilogue and f20/f21 are stack-restored there anyway.

```
; -- masses: M = weight + STR_eff  (k = 1 is the addu itself)
lwc1  f4, 0x14(s2)          ; A comp2 |  who is driving whom is re-derived
lwc1  f5, 0x14(s3)          ; B comp2 |  exactly as the lock-in did at 0x001f154c
lwc1  f6, 0xAEC(s0)         ; A weight
lhu   v0, 0xB8E(s0)         ; A STR_eff (ratings index 15)
mtc1  v0, f7
cvt.s.w f7, f7
add.s f6, f6, f7            ; M_A
lwc1  f8, 0xAEC(s4)
lhu   v0, 0xB8E(s4)
mtc1  v0, f9
cvt.s.w f9, f9
add.s f8, f8, f9            ; M_B
; -- helper, only while touching (S4-D gate)
lbu   v0, 0x437(s4)         ; B dt_role
addiu v1, zero, 2
bne   v0, v1, no_help       ; not a doubled defender
lbu   v0, 0x436(s4)         ;   (slot) B dt_record
sltiu v1, v0, 4
beq   v1, zero, no_help     ; index sanity: registry has 4 records
sll   v1, v0, 2             ;   (slot) idx*4
sll   v0, v0, 4             ; idx*16
addu  v0, v0, v1            ; idx*20
lw    at, -17520(gp)        ; DT table 0x00601280 (P1-cave precedent: registry
addu  at, at, v0            ;   words deref directly as player bases)
lw    at, 8(at)             ; helper = [table + 4 + 20*idx + 4]
beq   at, zero, no_help
nop
beq   at, s0, no_help       ; registry echoing the primary: count once
nop
lw    v0, 0x3E0(at)         ; helper engagement kind
addiu v1, zero, 8
bne   v0, v1, no_help       ; touching == attached (kind 8); kind 7 shadows add nothing
nop
lwc1  f10, 0xAEC(at)
lhu   v0, 0xB8E(at)
mtc1  v0, f11
cvt.s.w f11, f11
add.s f10, f10, f11
add.s f6, f6, f10           ; M_A += M_helper
no_help:
; -- R with the comp2 winner on top (defender side has no helper term)
c.lt.s f5, f4               ; B2 < A2 ?
nop                         ; COP1 compare hazard slot (engine's own idiom)
bc1t  a_wins
nop
div.s f12, f8, f6           ; defender drives: R = M_B / (M_A + M_H)
beq   zero, zero, have_r
nop
a_wins:
div.s f12, f6, f8           ; R = (M_A + M_H) / M_B
have_r:
mul.s f12, f12, f12         ; R^2
lui   at, 0x3F80
mtc1  at, f13               ; 1.0
sub.s f12, f12, f13
add.s f20, f20, f12         ; D = margin + R^2 - 1
mtc1  zero, f13
max.s f20, f20, f13         ; clamp low: 0
lui   at, 0x4040
mtc1  at, f13               ; 3.0
min.s f20, f20, f13         ; clamp high
swc1  f20, 0(s2)            ; A staged_drive = D
jr    ra
swc1  f20, 0(s3)            ;   (slot) B staged_drive = D
```

No stack, no nested calls (the P1 precedent of direct registry deref avoids
needing 0x0013b798, which would cost an ra save), no data pool — all four
constants are lui-materialized. Site line at `patch=1`, cave body at
`patch=0` per code-caves pnach mechanics. Every word must round-trip through
`recon/mipsdis.py` before deploy (standing rule; SLLV-operand and
branch-likely traps).

### 5.4 What deploying this ALONE does (rule 2 statement)

D replaces the margin in +0x404, so until lane 1's selector consumes it, the
only live consumer is the sweep's speed_cmd re-stamp — a value the engaged
defender provably ignores (P6). Expected observable effects, to be measured
per-patch before any integration:

* Band-0 pairs (every ordinary slot 9 rep): |D − margin| = |R²−1| ≤ 0.16 —
  within the jitter the value already carries. Baseline behaviour must be
  statistically unchanged; that is the acceptance's must-not-break arm.
* The won double: the pair's shared speed_cmd rises to ~2.4 while kind-4
  frames run (locomotion's own caps still bound applied speed). The pair may
  visibly translate together somewhat faster on those frames — the P5 data
  says symmetric same-value speed moves the pair as a unit. Not the goal,
  not obviously wrong, must be measured on slot 9 (carrier_yards + DE dy).
* One-frame leak window at teardown: the last stamped D stays in speed_cmd
  until the owner state's next think re-grants (arrival steering rewrites
  every tick). Bounded by locomotion caps; watch for post-rep lurches.
* Pass sets: the freeze path never reaches the cave (§5.1); slot 7 must be
  bit-identical on frozen reps.

Acceptance for THIS patch (episode-scoped): slot 9, post-snap probe of
+0x404 on the TE/RT/DE triple reads ≈ 2.4 (vs ≤ 0.3 baseline) while the
helper is attached and falls back when he detaches; C/NT reads within
baseline jitter; slot 7 pass-freeze reps identical; determinism suite green.
The MOTION acceptance (defender displaced ≥ 1.0 yd on the won double, feet
moving, no warps) belongs to the integrated law + lane 1 selector, not to
this patch alone.

---

## 6. Corrections to existing docs produced by this lane

1. **drive-machinery.md Q3(b)**: "pass-pro pairs with a live DT helper
   re-lock every frame; run pairs never do" — wrong. The latch store
   0x001f2214 is in a plain-bne delay slot and executes for both block
   modes; the mode test picks PBK vs RBK for the timer only (§4.3).
2. **code-caves.md cave #3 (0x0045F598)**: referenced (0x00460178/80 addiu
   pairs into it) — not a cave. Strike it (§5.2).
3. **pass-rush.md "+0x41C is never read"**: already corrected elsewhere
   (addresses.yaml note); re-confirmed live here — comp3 is read and
   stored back by the pass collapse (0x001f1070) and drives the rusher-gain
   term at 0x001f1050–0x001f1068.

## 7. What I could not establish

* **The contest comps populated on slot 9** — the savestate is pre-snap;
  +0x414/418/41C read 0.00 for all 22 players. The §3.4 margin column is
  computed from the verified formula on read ratings, pre-jitter, without
  the per-class scale (0x001ef0c8's per-class constants at 0x005832A0 were
  not enumerated — they cancel in R, and shift margin only). A post-snap
  savestate or live probe would close it.
* **Whether kind-5/6 conversion preserves the staged block (+0x404)** — the
  teardown fns zero timers/latches; whether the capture-to-158 path zeroes
  staged_drive is unchecked. Gates §4.1's "dispatcher reads +0x404"
  contract; the §4.4 leaf ABI is the designed fallback. Lane 1 checkpoint.
* **The predicate 0x001f7a38(partner)** that can skip the per-frame re-lock
  block (0x001f21c0) — undecoded. Slot 9's measured re-lock cadence
  (records re-scoring, windows 2..43) suggests it passes in live doubles;
  UNVERIFIED.
* **dt_role byte semantics beyond {0,1,2,3}** — slot 9 pre-snap shows 5 on
  all players (presumably "none"); the cave gates on == 2 exactly, so only
  that value is load-bearing, but the enum is not closed.
* **Whether any code reads +0x404 as a float in a range-sensitive way
  besides the sweep** — the known consumers are the sweep copies and the two
  anim-arm copies (0x001f1df0/0x001f1f68 regions); a full image-wide census
  of +0x404 readers with biased bases was not re-run this pass. Before
  deploy, run the same find_field_refs sweep that closed +0x432.
* **Live proof that registry words deref as player bases** — relied on the
  deployed P1 cave (same pattern, ran on the rig without fault) rather than
  a fresh derivation of the handle format.
* The sibling dispatcher doc (`1-dispatcher.md`) had not appeared by the end
  of this lane; the §4 contract (D at +0x404, knees {0.40, 1.5}, leaf-ABI
  fallback) is this lane's half of the interface and may need one round of
  reconciliation when lane 1 lands.

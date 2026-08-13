# Defensive fatigue — static binary investigation (F1–F6)

Static reverse-engineering pass on Madden NFL 2004 (PS2, SLUS-20752,
CRC 0x14F8B841). Binary: `extract/SLUS_207.52`. vaddr = file_offset + 0xFF000,
gp = 0x006056F0. **Static only** — no rig, no emulator, no patches, no commits.
All disassembly quoted below was produced by `recon.mipsdis` / `recon.fpudis`
against the ELF image.

Every load-bearing claim is pinned to an address with quoted disassembly and
labelled **Finding** (verified in the image) or **Hypothesis** (inference).

## Bottom line up front

There is a complete, self-contained **fatigue subsystem** in the image, whose
own registration fourcc is **"fatg"** (`0x66617467`, quoted below). It is a
per-player runtime float model with a drain path, a per-frame recovery path,
a substitution trigger, and a performance penalty that scales the effective
ratings. STA (the rating) governs both drain and recovery rate. The single
thing static analysis **cannot** settle is the cadence of the hard `memset`
reset (see F3) — that needs one live read.

---

## The subsystem map (addresses)

Manager descriptor pointer: **`[0x00600cc0]`** (gp-relative `-18992(gp)`).
Its fields, from the code that reads them:
- `+0x0` : table base pointer (a 2200-byte / `0x898` buffer, allocated once)
- `+0x4` : entry count (u16; `lhu 4(v0)`)
- `+0x6` : "enabled" byte (`lbu 6(...)` gates every entry point)
- also `0x00600cc4` and `0x00600cc8`: two callback/pointer slots (setters at
  `0x0014fc90` / `0x0014fc98`; `0x00600cc8` is invoked as the **sub-out
  callback**, see F3/tick).

Per-player **fatigue entry = 20 bytes** (stride confirmed `addiu t1, zero, 20`
in the lookup, and `mult ... ,20` in the builder). Layout, from the readers and
the builder:
| off | type | meaning |
|---|---|---|
| +0x0 | f32 | **fatigue A** — primary accumulator; drives the sub threshold and the rating multiply |
| +0x4 | f32 | **fatigue B** — secondary accumulator (drain ×3); drives the rating subtract |
| +0x8 | f32 | **fatigue C** — tertiary; only touched by exertion "type 10"; linear |
| +0xC | s16 | **key = player id** (== `player+0xAF2`); table is sorted on this, binary-searched |
| +0xE | u16 | **STA rating snapshot** (built to 255, refreshed to live STA on drain) |
| +0x10 | u8 | side/team flag (0/1) |
| +0x11 | u8 | flags; **bit0 = "tired/subbed" latch** set when threshold crossed |
| +0x12 | u8 | set from `0x0014efc0(id)` |

Lookup: **`0x0014f670`** — binary search over `base[+0]`, count `[+4]`, stride
20, key at entry `+0xC`; returns entry pointer or 0.
Convenience wrapper **`0x0014f650`**: `lh a0, 2802(a0)` (= `player+0xAF2`) then
`jal 0x0014f670`. So every access is keyed by **`player+0xAF2`**, *not* a fixed
player-struct offset.

```
0014F670  8F82B5D0  lw v0, -18992(gp)   ; gp-relative 0x00600cc0
0014F684  24090014  addiu t1, zero, 20        ; entry stride = 20
0014F688  94470004  lhu a3, 4(v0)             ; count
0014F69C  84C3000C  lh v1, 12(a2)             ; key at entry+0xC
...
0014F650  0C053D9C  jal 0x0014f670
0014F654  84840AF2  lh a0, 2802(a0)           ; key = player+0xAF2
```

Registration (proves the subsystem's identity):
```
0014FBCC  3C086661  lui t0, 0x6661
0014FBE8  0C0E75B2  jal 0x0039d6c8            ; register manager @0x00600cc0, name in t0
0014FBEC  35087467  ori t0, t0, 0x7467        ; t0 = 0x66617467 = "fatg"
0014FBF4  3C050015  lui a1, 0x0015
0014FC04  24A5F838  addiu a1, a1, -1992       ; a1 = 0x0014f838  (reset/init callback)
0014FC10  0C0E75E8  jal 0x0039d7a0            ; store lifecycle callbacks into descriptor
```
`0x0039d7a0` stores `a1→desc+16, a2→desc+20, a3→desc+24, t0→desc+28`; a second
call `0x0039d7b8` stores three more. Six lifecycle callbacks are registered,
`0x0014f838` (the reset) among them.

---

## F1 — Is there a per-player runtime energy/fatigue field? **YES (Finding).**

**Finding.** Three per-player runtime floats live in the "fatg" side table
described above: entry `+0x0`, `+0x4`, `+0x8`. They are distinct from the STA
*rating* at `player+0xB70+2·14 = +0xB8C`; the rating is only *read* (to set the
drain/recovery rate) and *snapshotted* into entry `+0xE`. The field is **not**
at a fixed player-struct offset — it is reached via the key `player+0xAF2`
through the binary search `0x0014f670`.

Direction of the field is **fatigue**, not energy: the value starts at 0
(memset), the drain path *adds* to it (below), and the sub trigger fires when it
climbs *past* a threshold. Higher value = more tired.

Getters that hand the value to the rest of the engine:
- `0x0014fcb0` → returns fatigue A (entry+0), or 0 if no entry.
- `0x0014fd20` → returns fatigue B (entry+4).
- `0x0014fcd8` → returns `min(fatigue C, 1.0)` if C>0 (entry+8).
- `0x0014f180` → returns `min(fatigue C + 0.1, 1.0)` (const `0x005fdc1c` = 0.1).

---

## F2 — What consumes it, and by how much? **Finding.**

**Finding.** The drain function is **`0x0014FE20`**. Signature `(a0=player,
a1=exertion "type")`. It:
1. reads the STA rating `lhu v1, 2956(s0)` (`+0xB8C`),
2. builds a STA-inverse base rate `f21 = 0.5·(1 − STA/255)` (const `0x005fdc30`
   = 1/255),
3. multiplies it by an **action-intensity factor** looked up by `type`
   (via `0x0014f010`, table at `0x0051d5a0`) and by an engine time factor,
4. **adds** the result to fatigue A (entry+0), the ×3 result to fatigue B
   (entry+4), and — only when `type == 10` — to fatigue C (entry+8), each
   `min`-clamped to 1.0,
5. refreshes the STA snapshot: `sh v1, 14(s1)` (entry+0xE).

```
0014FE7C  96030B8C  lhu v1, 2956(s0)          ; STA rating (+0xB8C, idx 14)
0014FE84  C7838540  lwc1 f3, -31424(gp)       ; 0x005fdc30 = 1/255
0014FEB0  46030002  mul.s f0, f0, f3          ; STA/255
0014FEB4  46000841  sub.s f1, f1, f0          ; 1 - STA/255
0014FEBC  46020D42  mul.s f21, f1, f2         ; * 0.5   -> base drain rate
0014FEAC  A623000E  sh v1, 14(s1)             ; snapshot STA into entry+0xE
...
0014FF54  C6200000  lwc1 f0, 0(s1)            ; fatigue A
0014FF64  46150000  add.s f0, f0, f21         ; += drain
0014FF6C  46160029  min.s f0, f0, f22         ; clamp to 1.0
0014FF74  E6200000  swc1 f0, 0(s1)            ; store back
0014FF68  4601AD42  mul.s f21, f21, f1        ; f1 = 3.0  -> fatigue B rate ×3
0014FF94  E6200004  swc1 f0, 4(s1)            ; fatigue B += (×3), clamp
0014FFA4  E6200008  swc1 f0, 8(s1)            ; fatigue C += (type 10 only)
```

Intensity table `0x0051d5a0` (indexed by `type`, f32 per-action drain
magnitudes):

| type | value | | type | value |
|---|---|---|---|---|
| 0,1,2,4 | 0.025 | | 8 | **0.06** |
| 3 | 0.04 | | 9 | 0.02 |
| 5 | 0.012 | | 10 | 0.0 (uses the C path) |
| 6 | 0.018 | | 11 | 0.0001 |
| 7 | 0.02 | | 12 | 0.03 |
| | | | 13 | 0.10 |
| | | | 14 | **0.35** |
| | | | 15 | **0.45** |

**Finding.** Drain is called from **16 gameplay sites** (jal → `0x0014FE20`):
`0x00165214, 0x001A66C8, 0x001A7464, 0x001A8664, 0x001A94A8, 0x001AC7DC,
0x001AF5D8, 0x001B23BC, 0x001B282C, 0x001B29C4, 0x001C6978, 0x001E4F70,
0x001E8418, 0x001E8DEC, 0x001F1814, 0x001F1930`. These sit in the AI/animation
states (the 0x1A–0x1F band this project has been mapping). The `type` at these
sites is mostly **data-driven** — e.g. `lw a1, 776(s0)` (= a per-player
`+0x308` action field) at `0x001A7444` and `0x001B29A8`; small literals
elsewhere (`a1=5` at `0x001AC7DC`, `a1=1` at `0x001B23BC`, `a1=2` at
`0x001E4F70`). So the drain magnitude tracks the *action* the player is
performing, via the intensity table.

**By how much (Finding, structural):** per drain call,
`ΔfatigueA ≈ 0.5·(1 − STA/255) · intensity[type] · (time factor)`, clamped to
≤1.0; fatigueB gets ×3 that. Exact per-frame magnitude in fatigue units needs
a live read (the time factor comes from `0x0013ecc0`, not fully traced —
Hypothesis: a frame-dt / counter).

---

## F3 — Does it recover, and is there a per-play reset? **THE CRUX.**

**Finding — gradual recovery EXISTS and is per-frame.** Two leaf functions
decay the accumulators, driven by the recovery pass `0x00150168`:

- **`0x0014F518`** recovers fatigue A and B (multiplicative decay toward ~0):
  ```
  0014F518  8482000E  lh v0, 14(a0)             ; STA snapshot (entry+0xE)
  0014F51C  C7848534  lwc1 f4, -31436(gp)       ; 0x005fdc24 = 1/255
  0014F544  46000841  sub.s f1, f1, f0          ; 1 - STA/255
  0014F548  46020842  mul.s f1, f1, f2          ; * 0.5           (= same STA rate as drain)
  0014F54C  46016002  mul.s f0, f12, f1         ; step * rate     (f12 = recovery step)
  0014F550  46006301  sub.s f12, f12, f0        ; step * (1-rate)
  0014F554  46036042  mul.s f1, f12, f3         ; * fatigueA
  0014F558  460118C1  sub.s f3, f3, f1          ; fatigueA -= ...  (multiplicative)
  0014F568  E4830000  swc1 f3, 0(a0)            ; store fatigueA
  0014F57C  E4800000  swc1 f0, 0(a0)            ; floor at 0x005fdc28 = 1e-7
  0014F5B4  E4800004  swc1 f0, 4(a0)            ; fatigueB decays (×3 factor, min 1.0)
  ```
  Note the recovery rate reuses `0.5·(1 − STA/255)` off the STA **snapshot**:
  higher STA → larger effective decay term → **faster recovery**.
- **`0x0014F5D8`** recovers fatigue C linearly: `f1 = C − f12`, floored at 0.

Recovery driver **`0x00150168`** loops the whole table (count at manager+4),
computes per-team recovery steps from **sliders** (`0x0015ea70` with slider ids
2/3/4) and a per-team constant table (`0x0052d5d0`), then per entry calls
`0x0014f518` (step in f21) and `0x0014f5d8` (step in f25):
```
001502F8  0C053D46  jal 0x0014f518            ; recover A/B
001502FC  4600AB06  mov.s f12, f21
00150304  0C053D76  jal 0x0014f5d8            ; recover C
00150308  4600CB06  mov.s f12, f25
```
Recovery is **gated**: at `0x001502DC`, when the team selector `s2==2` it calls
`0x0014fb20(entry.id)` and, if that returns non-zero, **skips** recovery for
that entry (jumps past all three) — Hypothesis: on-field / actively-engaged
players don't recover, benched/resting ones do (the classic "rest to recover"
rule).

The recovery driver `0x00150168` is called from **five** game-loop sites:
`0x00161C4C, 0x0017DB80, 0x0017DBA0, 0x0025A1BC, 0x00260664` — i.e. it runs
continuously during play, not once per play.

**The hard reset (Finding).** The only code that *zeroes* the accumulators is
the table (re)builder **`0x0014f6d8`**, which `memset`s the whole 2200-byte
buffer (`jal 0x004b3e88(buf, 0, 2200)` — confirmed a byte-fill routine) and
then repopulates keys/flags/STA-defaults. It does **not** selectively clear the
floats; the memset does. `0x0014f6d8` is reached only through the "fatg" **reset
callback `0x0014f838`** (registered at `0x0014fc04`), which the subsystem
framework invokes at some lifecycle event.

**Closed-set negative (Finding).** The entry pointer is produced *only* by
`0x0014f650` / `0x0014f670`. I enumerated every caller of both. The only writers
of entry `+0/+4/+8` in the entire subsystem are: the **drain** (`0x0014FE20`,
adds), the **recovery** leaves (`0x0014F518`/`0x0014F5D8`, subtract), and the
**memset** in the builder (`0x0014f6d8`, zeroes). Nothing else writes them. In
particular, **no per-frame path resets the accumulators to zero** — recovery is
gradual decay, not a snap-back.

**What static analysis cannot settle (the crux gap):** the cadence of the
`0x0014f838` reset callback. It is registered as the subsystem's init/reset
hook and its builder allocates the buffer once (guarded by a null check at
`0x0014f724`), which *reads like* a per-activation (game/half) init rather than
a per-play one — **Hypothesis: it fires at match/half start, not per snap.**
But I could not trace the framework's callback dispatch to proof. 

**Verdict on F3:** fatigue is a **drain/recover equilibrium updated every
frame**, with recovery gated so resting players recover faster than exerting
ones. Because recovery is gradual (not a per-play reset) and drain is
continuous, **accumulation across plays within a game is the expected behaviour
of the code as written** — provided the `0x0014f838` memset reset does not fire
per play. One live read (watch an entry's `+0` across a snap boundary, and
across a play boundary) settles both the equilibrium magnitude and the reset
cadence definitively.

---

## F4 — Run vs pass differential today? **Finding: per-action, not a flag.**

**Finding.** There is **no** single run-vs-pass switch in the drain path. The
asymmetry the engine already has is **per-action**, via the `type` argument and
the intensity table `0x0051d5a0` (F2): taking on a block, pursuit, tackling,
sprinting each carry a different `type` and therefore a different drain
magnitude (0.012 … 0.45). Several drain sites read the type from a per-player
action field (`lw a1, 776(s0)` = `+0x308`), so the drain tracks whatever the
player is doing frame to frame.

Whether a *run play* drains a front-seven defender more than a *pass play* is
therefore **emergent** (more/attritional block engagements and pursuit on runs
sum to more drain), **not** a coded run/pass differential. The "play turned
into a run" global bit (state 72 → 31/33 decider, per `fb-wr-blocking.md`) is
**not** read anywhere in the fatigue subsystem — verified: no reference to it in
`0x0014Fxxx`/`0x0015xxxx`. So to *inject* a deliberate run>pass asymmetry we
would add a new read of that bit at the defender drain sites; the amplification
lever already exists (bump `type` or the intensity-table value for
block/pursuit actions on run plays).

---

## F5 — Where does fatigue degrade performance? **Finding.**

**Finding.** Fatigue is applied to the **effective-ratings table** (`+0xB70`)
every tick, degrading *all* attributes:

- **`0x0014f1b8`** multiplies ratings by `(1 − fatigueA·0.3)` (const
  `0x005fdc20` = 0.3) and writes back into `+0xB70`:
  ```
  0014F1B8  C4A20000  lwc1 f2, 0(a1)            ; fatigueA (a1 = entry)
  0014F1C0  C7808530  lwc1 f0, -31440(gp)       ; 0x005fdc20 = 0.3
  0014F1C4  84820B70  lh v0, 2928(a0)           ; rating[0]  (+0xB70)
  0014F1C8  46001082  mul.s f2, f2, f0          ; fatigueA * 0.3
  0014F1DC  46020841  sub.s f1, f1, f2          ; 1 - fatigueA*0.3
  0014F1E0  46010002  mul.s f0, f0, f1          ; rating * (1 - fatigueA*0.3)
  0014F1EC  A4820B70  sh v0, 2928(a0)           ; write back effective rating
  ```
  (it then walks the rest of the ratings block, offsets +2…+36).
- **`0x0014f360`** subtracts `round(fatigueB·12.75)` from the effective ratings
  (const `0x414c0000` = 12.75), clamped ≥ 0:
  ```
  0014F39C  ...        mtc1 at,f0 ; at=0x414c0000 = 12.75
  0014F3AC  46006002  mul.s f0, f12, f0         ; fatigueB * 12.75  (f12 = fatigueB)
  0014F3B0  46000064  cvt.w.s f1, f0
  0014F3C0  00822023  subu a0, a0, v0           ; rating -= fatigueB*12.75
  0014F3C8  0003200A  movz a0, zero, v1         ; clamp >= 0  (conditional move — noted)
  0014F3CC  A4E40B70  sh a0, 2928(a3)           ; write back
  ```

Both run **per player every tick** from the per-team update `0x0014FFD0`
(`jal 0x0014f1b8` at `0x0015006C`, `jal 0x0014f360` at `0x00150078`). So low
energy ⇒ lower effective ratings ⇒ worse everything the ratings feed (pursuit
speed, tackle, shed, etc.). This is the performance hook the requirement's
"claim 4" needs, and it is **global and already wired**.

**Also (Finding): the substitution trigger.** The per-team tick `0x0014FFD0`
compares fatigue A against a slider-derived threshold and, on crossing, latches
the "tired" bit and fires the sub-out callback `[0x00600cc8]`:
```
0015009C  4600A036  c.le.s f20, f0            ; threshold <= fatigueA ?
001500AC  92230011  lbu v1, 17(s1)            ; entry+0x11 flags
001500BC  34620001  ori v0, v1, 0x0001        ; set bit0 "tired"
001500C0  A2220011  sb v0, 17(s1)
001500C4  8F82B5D8  lw v0, -18984(gp)         ; 0x00600cc8 = sub-out callback
001500D0  0040F809  jalr v0                    ; fire, a0 = entry.id (+0xC)
```
`f20` threshold = `(100 − slider3)·0.01` (read at `0x00150034`). This is the
auto-sub decision, and it reads the same fatigue field — so any change to drain
must respect the regression note in the requirements (don't wreck auto-subs).

---

## F6 — STA rating index, and does it govern the rate? **Finding: YES.**

**Finding.** From the 21-entry fourcc attribute-name table at `0x00520140`
(quoted in `n1-cave.md`): **`PSTA` = index 14**, so the STA rating lives at
`player+0xB70 + 2·14 = +0xB8C` (u16, effective 0..255).

**Finding.** STA governs **both** drain and recovery rate, through the identical
term `0.5·(1 − STA/255)`:
- drain (`0x0014FE20`): base rate `= 0.5·(1 − STA/255)` — **low STA drains
  faster** (quoted in F2).
- recovery (`0x0014F518`): decay term built from `0.5·(1 − STA/255)` off the STA
  snapshot at entry+0xE — **high STA recovers faster** (quoted in F3).

So a high-STA defender both tires slower and recovers faster; STA is the single
per-player resistance knob, exactly as the calibration plan hoped. (Note: only
**one** direct read of `+0xB8C` exists in the whole image — the drain function;
`find_immediate_all(0xB8C)` returns exactly `0014FE7C  lhu v1, 2956(s0)`. The
recovery path reads the *snapshot* at entry+0xE instead, not `+0xB8C` directly.)

---

## Searched and not found (closed-set negatives)

- **No per-frame reset of the accumulators.** The only writers of entry
  `+0/+4/+8` are drain (add), recovery (subtract), and the builder's `memset`
  (zero). Verified by enumerating every caller of the only two functions that
  produce an entry pointer (`0x0014f650`, `0x0014f670`).
- **No run/pass flag in the fatigue path.** The "play turned into a run" global
  is not referenced anywhere in `0x0014Fxxx`/`0x0015xxxx`. Differential today is
  per-action only.
- **STA `+0xB8C` is read in exactly one place** in the entire image (the drain
  function). No other consumer reads the STA rating directly.
- **The field is not at a fixed player-struct offset** — searches for a drained
  float at a constant player offset would (and did) come up empty; the value is
  in the side table keyed by `player+0xAF2`.

## What a live read would settle (single most valuable measurement)

1. **Reset cadence (the crux).** Watch one entry's `+0x0` across a snap and
   across a play boundary. If it snaps to 0 at every snap → the `0x0014f838`
   memset fires per play and cross-play accumulation is defeated as-is
   (feature would require changing *when* the reset fires). If it declines
   monotonically across plays → accumulation is already the behaviour and the
   feature is a *tuning* job on drain vs recovery.
2. **Equilibrium magnitude.** Read `+0x0` for a front-seven defender fresh vs
   after ~10 run plays vs ~10 pass plays — quantifies the existing asymmetry and
   the drain/recover balance, which sets the calibration curve (claim 3).
3. Confirm the recovery gate (`0x0014fb20`) semantics — whether on-field players
   recover during live play or only between.

## Feasibility verdict

**Feasible, and the hooks are unusually clean** — with one gating unknown.

The subsystem is real, named ("fatg"), and already does everything the feature
needs *except* a coded run>pass differential: a per-player fatigue float (F1),
an action-scaled drain keyed off STA (F2/F6), a gradual STA-scaled recovery
(F3), a global performance penalty that scales every effective rating (F5), and
an auto-sub trigger. Injecting "run drains the front seven more than pass" is a
small, well-localised change: read the known run/pass bit at the defender drain
sites and bump the `type`/intensity, gated to defenders on run plays (satisfies
the blast-radius rule — offense, subs, and fresh-player performance are
untouched because they go through the same field with unchanged constants).

**The whole feature still hinges on F3's one open question: does the
`0x0014f838` memset reset fire per play or per game?** The static evidence
(single guarded allocation, registered as the subsystem init hook, gradual
per-frame recovery rather than a per-play snap-back) points to **per-activation,
not per-play** — i.e. accumulation across a game is the code's natural behaviour
— but this is a **Hypothesis** until a live read confirms it. Recommend that
single measurement before any requirement is promoted from provisional.

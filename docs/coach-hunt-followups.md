# Coach-hunt follow-ups — mid-play coverage entry (Q1) and the PS2 de-cheese switch (Q2)

Static investigation, 2026-08-14, against `extract/SLUS_207.52` (Madden NFL 2004,
SLUS-20752). Static only — no rig, no emulator, nothing patched.
`vaddr = file_offset + 0xFF000`; single `PT_LOAD`, so every address quoted here is
file-backed and re-derivable. `gp = 0x006056F0`.

Answers the two items left open by `docs/coach-hunt-defense.md` §B3.7 and by
`docs/ai-coach-playcalling-requirements.md` §2.6:

* **Q1** — do the coverage states' `enter` handlers tolerate mid-play entry?
  **Verdict table in §1.7.**
* **Q2** — is there a free de-cheese switch on PS2 (the Xbox `ptrk` kill switch)?
  **Verdict in §2.6.**

Prefix key: **Finding** = verified against this binary, evidence quoted;
**Hypothesis** = inference, not nailed; **Correction** = a prior doc statement was
wrong.

---

## 1. Q1 — mid-play entry into the coverage states

### 1.0 The one object that decides the answer: `playArt`

Everything in Q1 turns on a single sub-object and a single counter, so it goes
first.

**Finding: `0x001F86D8` returns `*(0x006012C8) + 8`.** It is the *only* accessor
for that sub-object, and it has 48 `jal` callers, 44 of them inside the
defensive-coverage module (`0x001A0F6C` … `0x001EDC54`), plus three outside it
(`0x00233C64`, `0x00234D64`, `0x002439B4`).

```
001F86D8  8F82BBD8  lw v0, -17448(gp)   ; = *(0x006012C8)
001F86DC  03E00008  jr ra
001F86E0  24420008  addiu v0, v0, 8     ; playArt = *(0x006012C8) + 8
```

The parent object is created 468 bytes wide with a four-char registry tag
(`0x7069`/`0x6E66` → `'pinf'` when read little-endian) and is the same object
that carries the coach struct at `+72` (`0x001F86E8`, §B1.1 of
`coach-hunt-defense.md`):

```
001F8204  3C087069  lui t0, 0x7069
001F821C  240601D4  addiu a2, zero, 468
001F8224  0C0E75B2  jal 0x0039d6c8      ; registry create
001F8228  35086E66  ori t0, t0, 0x6e66
```

**Finding — the `playArt` layout, derived from its writers and readers:**

| offset | width | meaning | evidence |
|---|---|---|---|
| `+0` | `i16` | **freshness countdown**, armed to **30** | armed `001BEB70`; tested `001BDDD4`, `001BE150`, `001A0FC4`, `001A0FEC` |
| `+2` | `i16` | second countdown, armed to **150** | armed `001BEB78`; decremented `001BF5E0`; tested `001EB470` |
| `+4` | `u8` | "authored assignments are in force" flag | `001BDDBC lbu v0, 4(s2)` |
| `+6` | `u8` | a flag read by the state-chain installer | `002439BC lbu v1, 6(v0)`; cleared at `001BFDFC` |
| `+8 … +12` | `u8[5]` | **the 5 tracked offensive skill players, authored order** — man designator 1..5 indexes here | filled `001BEBAC`; read `001BDE78 lbu a1, 7(v1)` |
| `+16 … +20` | `u8[5]` | **the same 5, sorted left→right by live X** — zone/press byte `ctx+2` indexes here | filled `001BEC54`; read `001ED654`, `001E98EC`, `001ECB28` (all `lbu …, 15(base)`) |
| `+44 … +54` | `u8[11]` | **the authored man-coverage designator per formation slot**, `0xFF` = none | `001BDDE4 lbu s0, 44(v0)` |

The two receiver lists are built by `0x001BEB28`, which also arms both counters:

```
001BEB54  0C07E1B6  jal 0x001f86d8
001BEB5C  24430010  addiu v1, v0, 16
001BEB60  2404001E  addiu a0, zero, 30
001BEB64  AFA30000  sw v1, 0(sp)          ; sp+0 = playArt+16 (the sorted list)
001BEB68  24570008  addiu s7, v0, 8       ; s7   = playArt+8  (the authored list)
001BEB6C  24030096  addiu v1, zero, 150
001BEB70  A4440000  sh a0, 0(v0)          ; playArt[0] = 30
001BEB74  0C098166  jal 0x00260598
001BEB78  A4430002  sh v1, 2(v0)          ; playArt[2] = 150
  ; copy 5 player indices from coachObj+233 into playArt+8..12
001BEB90  00521021  addu v0, v0, s2
001BEB98  904500E9  lbu a1, 233(v0)
001BEBAC  A0650000  sb a1, 0(v1)
  ; bubble-sort those 5 by LIVE X (+0x190), left to right
001BEBF0  C6010190  lwc1 f1, 400(s0)
001BEBF4  C4400190  lwc1 f0, 400(v0)
001BEBF8  46000834  c.lt.s f1, f0
001BEC14  A2630000  sb v1, 0(s3)          ; swap
001BEC18  A2220000  sb v0, 0(s1)
  ; copy the sorted 5 into playArt+16..20
001BEC54  A0650000  sb a1, 0(v1)
```

`coachObj+233` is the coach's five-player formation cache from `0x00145188`
(§B1.2 of `coach-hunt-defense.md`: `addiu s7, a0, 233 ; 5 player indices`).

**Finding — `0x001BEB28` is pre-snap-only. Closed set: three call sites, no `j`.**
*Scope: whole-image opcode scan for `jal`/`j` to `0x001BEB28`.*

| call site | enclosing | phase |
|---|---|---|
| `0x00145D84` | `0x00145D68`, whose sole caller is `0x0017B3B0` inside `0x0017B2B8` = the **update column of the pre-snap state row `0x0057C878`** | pre-snap, per frame |
| `0x00217D94` | update column of state row `0x0057C8FC` (`0x0057C900` = `0x00217D78`) | per frame |
| `0x0025319C` | `0x002530B0`, the play-start function (§B3.3) | play start |

**Finding — the counters are decremented by two *different* per-frame update
hooks, and the decrement is one-shot (it stops at −1).**

```
; 0x001BEE48 -- playArt[0]
001BEE7C  0C07E1B6  jal 0x001f86d8
001BEE88  AFA20048  sw v0, 72(sp)            ; sp+72 = playArt
001BEEBC  94430000  lhu v1, 0(v0)
001BEEC0  84420000  lh v0, 0(v0)
001BEEC4  0440012B  bltz v0, 0x001bf374      ; already negative -> never again
001BEEC8  8FA40048  lw a0, 72(sp)
001BEECC  2462FFFF  addiu v0, v1, -1
001BEED0  00021C00  sll v1, v0, 16
001BEED4  0461012A  bgez v1, 0x001bf380      ; plain bgez -> the delay slot ALWAYS runs
001BEED8  A4620000  sh v0, 0(a0)             ; store the decrement unconditionally
```
```
; 0x001BF598 -- playArt[2], identical shape
001BF5BC  0C07E1B6  jal 0x001f86d8
001BF5C8  86020002  lh v0, 2(s0)
001BF5CC  04400025  bltz v0, 0x001bf664
001BF5D4  2462FFFF  addiu v0, v1, -1
001BF5DC  04610021  bgez v1, 0x001bf664
001BF5E0  A6020002  sh v0, 2(s0)
```

Callers, closed sets: `0x001BEE48` ← {`0x001484DC`, `0x00217F44`};
`0x001BF598` ← {`0x001484E4`, `0x00217F4C`}. `0x00217F44`/`0x00217F4C` sit in the
update column of state row `0x0057C908` (`0x0057C90C` = `0x00217EF0`) — **the row
immediately after the arming row `0x0057C8FC`** in the 12-byte
`{enter, update, exit}` game-state table that also holds the pre-snap row
`0x0057C878`:

```
0057C8F0  002178E8 / 00217B30 / 00217AD0
0057C8FC  00217BB0 / 00217D78 / 00217D50   ; update ARMS playArt (calls 0x001BEB28)
0057C908  00217E00 / 00217EF0 / 00217E88   ; update DECREMENTS playArt
```

**Finding: `playArt[0]` is re-armed to 30 on every frame of the pre-snap phase and
counts down, once per in-play tick, to −1 and stops. It is a "the authored
pre-snap assignment data is still fresh" gauge, and the engine's own coverage code
branches on it three separate ways.** **Hypothesis:** the timebase is one
decrement per rendered frame, so the window is ≈30 frames (half a second at 60 Hz)
after the snap; the tick rate is not provable statically.

Corroborating the semantics, from `0x001A0F28` (the press/jam attempt, §1.3):

```
001A0FC4  86020000  lh v0, 0(s0)          ; s0 = playArt
001A0FC8  28420019  slti v0, v0, 25
001A0FCC  10400009  beq v0, zero, 0x001a0ff4   ; >= 25  -> the JAM window (first 5 ticks)
001A0FD4  0C056B68  jal 0x0015ada0
001A0FE0  1443000B  bne v0, v1, 0x001a1010     ; v1 = 3
001A0FE8  86020000  lh v0, 0(s0)
001A0FEC  04410008  bgez v0, 0x001a1010        ; < 0 -> the LATE window, mode 3 only
```

### 1.1 State 22 (man coverage) — `enter` = `0x001BDD88`

**Finding: 22's `enter` has an explicit "play-art is stale" fallback, and the two
gates on it are the `playArt+4` flag and the `playArt[0]` freshness counter.**

```
001BDDAC  0C07E1B6  jal 0x001f86d8
001BDDB8  0040902D  daddu s2, v0, zero      ; s2 = playArt
001BDDBC  92420004  lbu v0, 4(s2)
001BDDC0  5040000C  beql v0, zero, 0x001bddf4   ; BRANCH-LIKELY
001BDDC4  8E2202FC  lw v0, 764(s1)          ;   delay slot runs ONLY when taken: v0 = live ctx
001BDDC8  0C07E1B6  jal 0x001f86d8
001BDDD0  84430000  lh v1, 0(v0)            ; the freshness counter
001BDDD4  04620007  bltzl v1, 0x001bddf4    ; BRANCH-LIKELY: stale -> use the live ctx
001BDDD8  8E2202FC  lw v0, 764(s1)          ;   delay slot, taken-only
  ; --- fresh: take the AUTHORED designator for this formation slot ---
001BDDDC  92220002  lbu v0, 2(s1)           ; player+2 = the formation slot index
001BDDE0  02421021  addu v0, s2, v0
001BDDE4  9050002C  lbu s0, 44(v0)          ; playArt[44 + slot]
001BDDE8  3A0300FF  xori v1, s0, 0x00ff
001BDDEC  10000002  beq zero, zero, 0x001bddf8
001BDDF0  0003800A  movz s0, zero, v1       ; MOVZ (delay slot, always runs): 0xFF -> 0
  ; --- stale: take the LIVE designator that was pushed with the state ---
001BDDF4  90500001  lbu s0, 1(v0)           ; ctx+1
```

and the chosen designator is written straight back into the live context:

```
001BDE84  A0700001  sb s0, 1(v1)            ; v1 = player[+0x2FC]; ctx+1 = s0
```

Everything after that is live geometry: the designator indexes the *authored*
receiver list (`001BDE78 lbu a1, 7(v1)` → `playArt[7 + designator]`), the receiver
object comes from `0x001655B0`, and the comparisons are live positions
(`001BDEC0 lwc1 f1, 400(s2)` vs `001BDEC4 lwc1 f0, 400(s1)`, thresholds 3.5 and
5.5). The one authored-data read is a *class byte*, not a coordinate:
`0x00242848(defBlk, recv+1, recv+2, 0)` → `001BDEA0 lbu v1, 2(v0)`, tested `== 3`.

Two designator sentinels divert the state entirely — both push a different state
through `0x001AFB50` and return 1:

```
001BDDF8  240200FE  addiu v0, zero, 254
001BDDFC  1602000B  bne s0, v0, 0x001bde2c
001BDE08  24020055  addiu v0, zero, 85       ; 254 -> state 85, param 48
001BDE2C  1602000B  bne s0, v0, 0x001bde5c   ; v0 = 253
001BDE30  24020002  addiu v0, zero, 2        ; 253 -> state 2 (ball pursuit)
001BDE4C  0C06BED4  jal 0x001afb50
```

Tail: the generic per-player motion quad, plus a second freshness test:

```
001BE144  0C07E1B6  jal 0x001f86d8
001BE14C  84430000  lh v1, 0(v0)
001BE150  04630005  bgezl v1, 0x001be168     ; BRANCH-LIKELY
001BE158  2402001E  addiu v0, zero, 30       ; stale -> block+26 = 0, block+20 = 30
001BE174  8E2201A8  lw v0, 424(s1)
001BE178  A23001F4  sb s0, 500(s1)
001BE17C  AE2201EC  sw v0, 492(s1)
001BE180  AE2001E8  sw zero, 488(s1)
001BE184  AE2201F0  sw v0, 496(s1)
```

**Verdict for 22 — mid-play safe, with one behavioural caveat that the design must
respect.** Nothing it reads is invalid mid-play. But **inside the ≈30-tick freshness
window, `enter` overwrites the man-target byte you pushed with the authored one
for that formation slot.** After the window it uses your byte. Two consequences:

* a rotation into man **at or right after the snap** cannot choose its own matchup
  — it gets the authored one;
* a rotation into man **later in the play** keeps whatever `ctx+1` you push.

That is exactly the behaviour the engine's own post-snap state-22 entry needs.
Re-derived here rather than taken from the prior doc:

```
0019EEB8  0C0BE50A  jal 0x002f9428          ; rand(0, 100)
0019EEBC  24050064  addiu a1, zero, 100
0019EEC0  0050102B  sltu v0, v0, s0         ; a probability gate
0019EEC4  10400011  beq v0, zero, 0x0019ef0c
0019EEC8  24020016  addiu v0, zero, 22      ; <== state 22, man coverage
0019EECC  AFA00000  sw zero, 0(sp)
0019EED0  A3A20000  sb v0, 0(sp)
0019EED4  0C07013C  jal 0x001c04f0          ; the man-target designator, COMPUTED
0019EED8  0220202D  daddu a0, s1, zero
0019EEE8  A3A20001  sb v0, 1(sp)            ; ctx+1 = that designator
0019EEF0  A3A00002  sb zero, 2(sp)
0019EEF4  03A0302D  daddu a2, sp, zero
0019EEF8  0C06C028  jal 0x001b00a0          ; SET state 22 now
0019EEFC  A3A00003  sb zero, 3(sp)
```

**Finding: there are TWO shipped mid-play coverage-state pushes, not one.**
`0x001BE708` pushes state **40** (§B3.4), and `0x0019EEF8` — inside `0x0019EDF8`,
reached from state 2's `ai_think` (ball pursuit) — pushes state **22** with a
computed man target, behind a random gate. Writing that designator would be
pointless if `enter` always clobbered it, which is precisely what the stale path
at `0x001BDDD4` exists to prevent.

### 1.2 State 40 (deep safety zone) — `enter` = `0x001EC430` — the reference case

```
001EC430  27BDFFD0  addiu sp, sp, -48
001EC440  0080882D  daddu s1, a0, zero
001EC448  26300150  addiu s0, s1, 336         ; the per-state block at player+0x150
001EC44C  AE000004  sw zero, 4(s0)
001EC450  AE220150  sw v0, 336(s1)            ; v0 = 0x00C00000
001EC454  A6000012  sh zero, 18(s0)
001EC458  A6000010  sh zero, 16(s0)
001EC45C  AE00000C  sw zero, 12(s0)
001EC460  A2000018  sb zero, 24(s0)
001EC46C  AE000008  sw zero, 8(s0)
001EC470  0C07E0AE  jal 0x001f82b8            ; the AUTHORED play-type predicate
001EC474  A200001B  sb zero, 27(s0)
001EC478  10400009  beq v0, zero, 0x001ec4a0
001EC480  240300FF  addiu v1, zero, 255
001EC484  A2020017  sb v0, 23(s0)
001EC488  86240B74  lh a0, 2932(s1)           ; a player RATING (player+0xB74)
001EC48C  00641823  subu v1, v1, a0
001EC490  00031883  sra v1, v1, 2
001EC494  2463005A  addiu v1, v1, 90          ; depth = ((255 - rating) >> 2) + 90
001EC498  10000003  beq zero, zero, 0x001ec4a8
001EC49C  A6030014  sh v1, 20(s0)
001EC4A0  A6000014  sh zero, 20(s0)
001EC4A4  A2000017  sb zero, 23(s0)
```

`0x001F82B8` is a pure read of the offence's authored play-type code, i.e. a
constant of the play, not a phase-dependent value:

```
001F82B8  jal 0x00260598 -> jal 0x00248360 -> jal 0x00243f08
00243F08  8C830014  lw v1, 20(a0)             ; playbook block +20 = the play-type code
00243F14  2C620026  sltiu v0, v1, 38          ; classify
```
(its sibling `0x001F82E8` = `IsRun` reaches `0x00243F58`, `code < 7 && code != 0`.)

**Verdict for 40 — mid-play safe, and *proven*.** `enter` reads exactly two things:
an authored play-type code and a player rating. **No position, no LOS, no authored
alignment, no `playArt`, no context byte.** This is the state
`0x001BE708` already pushes mid-play (§B3.4). Its deep landmark is not resolved at
`enter` at all — it is re-resolved *per tick* from the live `ctx+1`:

```
001EC2B8  8E2202FC  lw v0, 764(s1)
001EC2BC  90440001  lbu a0, 1(v0)             ; ctx+1 = the landmark id
001EC2C0  2C830009  sltiu v1, a0, 9           ; bound -> 9 entries
001EC2C8  3C020058  lui v0, 0x0058
001EC2D0  24423270  addiu v0, v0, 12912       ; table base 0x00583270, stride 4
001EC2D8  8C640000  lw a0, 0(v1)
001EC2DC  00800008  jr a0
```
(hand-decoded jump table `0x00583270`, stride 4, **9 entries**, count proven by the
`sltiu … 9`; sole caller `0x001EAEBC`.)

### 1.3 State 37 (CB outside zone: flat / deep outside) — `enter` = `0x001ED5F8`

```
001ED610  8E42000C  lw v0, 12(s2)
001ED614  30424000  andi v0, v0, 0x4000
001ED618  14400022  bne v0, zero, 0x001ed6a4   ; flagged -> straight to block init
001ED61C  26510150  addiu s1, s2, 336
001ED620  92430B04  lbu v1, 2820(s2)
001ED624  24020010  addiu v0, zero, 16
001ED628  1462001F  bne v1, v0, 0x001ed6a8     ; not a CB -> straight to block init
001ED62C  24100001  addiu s0, zero, 1          ; (plain bne: delay slot always runs)
  ; --- CB only: pick a press target out of the SNAP-SORTED receiver list ---
001ED630  0C07E1B6  jal 0x001f86d8
001ED638  0C098166  jal 0x00260598
001ED63C  0040802D  daddu s0, v0, zero         ; s0 = playArt (captured in the delay slot)
001ED640  8E4302FC  lw v1, 764(s2)
001ED648  90620002  lbu v0, 2(v1)              ; ctx+2 = a receiver SLOT index
001ED64C  00501021  addu v0, v0, s0
001ED650  0C05956C  jal 0x001655b0
001ED654  9045000F  lbu a1, 15(v0)             ; playArt[15 + ctx2]
001ED65C  0040802D  daddu s0, v0, zero
001ED65C  ...
001ED65C  0C0BE4EC  jal 0x002f93b0             ; rand float, < 0.5 -> a2 = 1 else 0
001ED680  C78C9920  lwc1 f12, -26336(gp)       ; 0.9
001ED688  0C0683CA  jal 0x001a0f28             ; try to press/jam
001ED694  14430004  bne v0, v1, 0x001ed6a8     ; v1 = 1
001ED69C  1000001A  beq zero, zero, 0x001ed708 ; pressed -> return 1, block NOT initialised
  ; --- everyone else: initialise the per-state block ---
001ED6A8  A6200000  sh zero, 0(s1)
001ED6BC  A2300010  sb s0, 16(s1)
001ED6C0  0C07E0AE  jal 0x001f82b8             ; authored play type
001ED6E0  C64001AC  lwc1 f0, 428(s2)           ; player+0x1AC (live)
001ED6E8  C7819924  lwc1 f1, -26332(gp)        ; 5.642202
001ED6F0  8E4301B0  lw v1, 432(s2)             ; player+0x1B0 (live)
001ED6F4  46010002  mul.s f0, f0, f1
001ED6F8  A24401F4  sb a0, 500(s2)
001ED6FC  AE4301EC  sw v1, 492(s2)
001ED700  AE4301F0  sw v1, 496(s2)
001ED704  E64001E8  swc1 f0, 488(s2)
```

`0x001A0F28` builds a state message `{41, receiverIndex, flag}` and pushes it:

```
001A101C  24030029  addiu v1, zero, 41         ; state 41 = the press/jam state
001A1020  92480002  lbu t0, 2(s2)              ; the receiver's index
001A104C  A3A30000  sb v1, 0(sp)
001A1054  A3A80001  sb t0, 1(sp)
001A1058  0C06BED4  jal 0x001afb50
```

**Verdict for 37 — mid-play safe, and the one pre-snap-shaped branch it has
self-disables.** The press attempt is gated on `playArt[0] >= 25` (the first ≈5
ticks after the snap) or, post-window, on `0x0015ADA0() == 3` (§1.6). Enter it
late and a CB simply initialises his zone block from the authored play type and
his own live kinematics. Enter it *at* the snap and a CB may be diverted into
state 41 instead of playing the zone — which is the shipped behaviour, but it
means a rotation timed at the snap can lose the corner to a jam.

### 1.4 State 39 (intermediate / transition drop) — `enter` = `0x001EA020`

```
001EA020  27BDFFD0  addiu sp, sp, -48
001EA034  26300150  addiu s0, s1, 336
001EA038  A6200150  sh zero, 336(s1)
001EA03C  A6000002  sh zero, 2(s0)
001EA040  A2000014  sb zero, 20(s0)
001EA044  AE000004  sw zero, 4(s0)
001EA048  AE000008  sw zero, 8(s0)
001EA04C  AE00000C  sw zero, 12(s0)
001EA050  A2000015  sb zero, 21(s0)
001EA054  0C07E0AE  jal 0x001f82b8            ; authored play type
001EA058  A2000016  sb zero, 22(s0)
001EA05C  10400005  beq v0, zero, 0x001ea074
001EA068  A6020012  sh v0, 18(s0)
001EA070  A2030010  sb v1, 16(s0)
001EA074  A6000012  sh zero, 18(s0)
001EA078  A2000010  sb zero, 16(s0)
001EA07C  C62001AC  lwc1 f0, 428(s1)           ; player+0x1AC (live)
001EA084  C7819894  lwc1 f1, -26476(gp)        ; 5.642202
001EA08C  8E2301B0  lw v1, 432(s1)             ; player+0x1B0 (live)
001EA090  46010002  mul.s f0, f0, f1
001EA094  A22401F4  sb a0, 500(s1)
001EA098  AE2301EC  sw v1, 492(s1)
001EA09C  AE2301F0  sw v1, 496(s1)
001EA0A0  E62001E8  swc1 f0, 488(s1)
```

**Verdict for 39 — mid-play safe, cleanest of the five.** `enter` never touches the
context, never touches `playArt`, never reads a position, never reads an authored
alignment. Its dependency set is a strict subset of 40's plus the generic motion
quad. Its zone *is* resolved per tick, from the live `ctx+2`, by the shared helper
`0x001E9830` (§1.5) — which means a mid-play push of 39 with your own zone byte is
honoured immediately.

### 1.5 State 38 (underneath hook / curl) — `enter` = `0x001EE6B8` — the exception

**Finding: 38 is the only coverage state whose `enter` computes a field landmark,
and it anchors that landmark to the defender's AUTHORED ALIGNMENT SPOT.**

```
001EE6E0  A6200150  sh zero, 336(s1)
001EE700  0C04EE1C  jal 0x0013b870              ; block+16 = 0
001EE708  8E2202FC  lw v0, 764(s1)
001EE710  3C0140A0  lui at, 0x40a0
001EE714  44816000  mtc1 at, f12                ; magnitude 5.0
001EE718  90450002  lbu a1, 2(v0)               ; ctx+2 = a 7-bit ANGLE
001EE71C  0C12B710  jal 0x004adc40              ; out[0..1] = 5.0 * (sin, cos)(angle)
001EE720  00052C40  sll a1, a1, 17
001EE724  0C09816C  jal 0x002605b0              ; DEFENCE side
001EE72C  0C0920CE  jal 0x00248338              ; -> defence playbook block +60
001EE734  0040802D  daddu s0, v0, zero
001EE738  92250002  lbu a1, 2(s1)               ; player+2 = the formation slot
001EE73C  0000302D  daddu a2, zero, zero
001EE740  0C090A12  jal 0x00242848              ; -> the AUTHORED ALIGNMENT record
001EE748  92040017  lbu a0, 23(s0)              ; the defence's flip byte
001EE74C  24450018  addiu a1, v0, 24
001EE750  24430010  addiu v1, v0, 16
001EE754  C7A10000  lwc1 f1, 0(sp)              ; the 5.0-at-angle offset
001EE758  38840001  xori a0, a0, 0x0001
001EE760  00A4180A  movz v1, a1, a0             ; MOVZ: record+16 or record+24
001EE764  C4600000  lwc1 f0, 0(v1)              ; the AUTHORED X
001EE76C  46000840  add.s f1, f1, f0            ; landmark = authored X + 5.0*dir
001EE770  92030017  lbu v1, 23(s0)
001EE778  00A3100A  movz v0, a1, v1             ; MOVZ again
001EE77C  C4420004  lwc1 f2, 4(v0)              ; the AUTHORED Y
001EE780  E6410018  swc1 f1, 24(s2)
001EE784  E6410014  swc1 f1, 20(s2)
001EE78C  C6200194  lwc1 f0, 404(s1)            ; the defender's LIVE Y
001EE794  E640001C  swc1 f0, 28(s2)
```

`0x004ADC40` is "vector from magnitude and angle" (`0x00469BA0` sin/cos, then
`mul.s` by `f12` into `out[0]`/`out[1]`), so `f12 = 5.0` is a 5-unit offset.
`0x00242848` is the authored per-player alignment record — the same resolver
`0x00145188` uses.

**Finding: the alignment record's `+0`/`+4` floats are ball/LOS-RELATIVE offsets,
not absolute field coordinates.** `0x00145188` proves it by converting: on the
"player has not moved" path it *adds* the ball X to the record, while on the "live
position" path it stores `player+0x190` straight through.

```
00145248  C4600190  lwc1 f0, 400(v1)      ; LIVE path: absolute X ...
0014524C  10000013  beq zero, zero, 0x0014529c
00145250  E7A00000  swc1 f0, 0(sp)        ;   ... stored as-is
00145258  0C090A12  jal 0x00242848        ; AUTHORED path
00145274  00A4180A  movz v1, a1, a0       ; MOVZ: record+16 or record+24
00145278  C4600000  lwc1 f0, 0(v1)        ; the authored X ...
00145280  46140000  add.s f0, f0, f20     ;   ... + the ball X  <== the conversion
00145294  E7A00000  swc1 f0, 0(sp)
```
and `f20` is the situation object's `+12`:
```
001451E8  0C098082  jal 0x00260208
00145208  C7B40010  lwc1 f20, 16(sp)
00260208  8F86C85C  lw a2, -14244(gp)     ; *(0x00601F4C), the situation object
00260220  9CC5000C  lwu a1, 12(a2)        ; low  word -> f20
00260224  9CC30010  lwu v1, 16(a2)        ; high word
```

So state 38's `enter` stores a **LOS-relative** X into block `+20`/`+24` and an
**absolute** live Y into block `+28` (`001EE78C lwc1 f0, 404(s1)`); the LOS term
for the X must be re-applied by the consumer. Either way the X anchor is the
defender's authored spot, which is the point for Q1.

**Verdict for 38 — enterable mid-play, but its landmark is LOS-anchored, not
entry-anchored.** Nothing goes invalid: the authored record is play data, constant
for the whole snap. The consequence is semantic, and it cuts both ways:
* the lateral landmark is where the defender *lined up* ± 5 units — correct for a
  hook/curl that is supposed to be LOS-relative, wrong if you wanted "hook where
  you are now";
* the depth field takes the defender's **live Y at the instant of entry**
  (`001EE78C`), so a safety who has already dropped 15 yards and then rotates into
  38 gets a 15-yard-deep "underneath" zone.

That second half is the only genuine mid-play hazard found in the five handlers.
It makes 38 the wrong target for a late *rotate-down* and a fine target for a
rotate-from-depth-you-already-have.

### 1.6 Two functions the handlers lean on, named

* **`0x001F82B8` / `0x001F82E8`** — authored play-type predicates
  (`playbookBlock+20`). Constants of the play; valid in every phase. Used by 37,
  39 and 40's `enter`.
* **`0x0015ADA0`** — a situation/phase getter. In game mode 14 it returns
  `[*(0x00600C70) + 152]`; otherwise `0x0015AEB8(0x0015B418())`. The mode
  translation itself is a conditional move:
  ```
  00154790  8F82B5F4  lw v0, -18956(gp)     ; *(0x00600CE4)
  0015479C  8C420000  lw v0, 0(v0)
  001547A8  38A3000E  xori v1, a1, 0x000e
  001547B0  0083280A  movz a1, a0, v1       ; MOVZ: mode == 14 -> 3
  001547B4  24050007  addiu a1, zero, 7     ; null object -> 7
  ```
  **Finding: its value is available in-play** — state 38's `ai_think` queries it at
  `0x001EE814` and tests `== 3`, and `ai_think` only runs after the snap. So it is
  not a pre-snap-only value, whatever it names.
* **player `+0x1E8 / +0x1EC / +0x1F0 / +0x1F4`** — the generic per-player motion
  quad. **Correction to `coach-hunt-defense.md` §B3.7:** this is *not* "drop
  geometry derived at enter". It is written by essentially every AI state's
  `enter` in the image (hundreds of sites across `.text`; state 22 writes the same
  quad at `0x001BE174`-`0x001BE184` from a different source field), and the
  constant at both `gp-26332` and `gp-26476` is the same float **5.642202**.

### 1.7 VERDICT — the mid-play-entry table

| state | `enter` | what `enter` computes | pre-snap-only inputs? | mid-play entry |
|---|---|---|---|---|
| **40** deep safety zone | `0x001EC430` | block init; if pass-type, depth `((255−rating)>>2)+90` from `player+0xB74` | **none** — no position, no LOS, no alignment, no `playArt`, no ctx | **SAFE — and proven.** `0x001BE708` ships this exact push. Landmark re-resolved per tick from live `ctx+1` (`0x001EC2BC`, table `0x00583270`) |
| **39** intermediate drop | `0x001EA020` | block init from authored play type; motion quad from `player+0x1AC/0x1B0` | **none** — never reads ctx, `playArt`, position or alignment | **SAFE.** Zone resolved per tick from live `ctx+2` (`0x001E98E0`) |
| **37** CB outside zone | `0x001ED5F8` | same block init as 39; **plus**, for CBs only, a press attempt against `playArt[15+ctx2]` | the press branch reads the snap-frozen sorted list and gates on `playArt[0]` | **SAFE — the pre-snap branch self-disables.** Post-window the press is skipped (unless `0x0015ADA0()==3`). Entering *at* the snap can divert a CB into state 41 |
| **22** man coverage | `0x001BDD88` | designator (authored or live), then live-position comparisons against the target | reads `playArt+4` and `playArt[0]`, **but has an explicit stale path** | **SAFE — and proven.** `0x0019EEF8` ships a mid-play push of 22 with a computed target. Caveat: inside the ≈30-tick window `enter` **overwrites your pushed man target** with `playArt[44+slot]`; after it, your byte sticks |
| **38** hook / curl | `0x001EE6B8` | zone landmark = **authored alignment spot** + 5.0 at angle `ctx+2`; depth field = **live Y at entry** | the authored alignment record (a play constant, so never *invalid*) | **ENTERABLE, semantically riskiest.** Lateral landmark is LOS-anchored; depth is wherever the defender happens to be at entry |

**One cross-cutting Finding that de-risks the whole feature:** indexing the
snap-frozen receiver list *after* the snap is the engine's own normal behaviour,
not an abuse. State 40 — the state proven to work mid-play — does it every tick:

```
001ECAF4  0C07E1B6  jal 0x001f86d8
001ECB00  0040802D  daddu s0, v0, zero
001ECB14  8E2302FC  lw v1, 764(s1)
001ECB1C  90620002  lbu v0, 2(v1)          ; ctx+2
001ECB20  00501021  addu v0, v0, s0
001ECB24  0C05956C  jal 0x001655b0
001ECB28  9045000F  lbu a1, 15(v0)         ; playArt[15 + ctx2]
```

so does 39's per-tick helper `0x001E9830` (`0x001E98E0` / `0x001E98EC`).

**Correction to `coach-hunt-defense.md` §B3.1a.** The pair
`001ED648 lbu v0, 2(v1)` / `001ED654 lbu a1, 15(v0)` cited there as evidence that
"`ctx+2` = zone id 0..5 for states 37/39" is in state 37's **`enter`**, and it is
the CB **press-target** lookup. The correct reading of `ctx+2` is
**a receiver-slot index into `playArt+16..20`, the snap-sorted left-to-right list**
— in 37's `enter`, in 39's per-tick helper `0x001E9830`, and in 40's `ai_think`.
The exception is state 38, which uses `ctx+2` as a 7-bit angle (`0x001EE718`,
`sll a1, a1, 17`). "Zone" in this engine is mostly *"the Nth receiver from the
left as of the last pre-snap frame"*, not a field polygon.

### 1.8 What the disguise feature can therefore rotate between

Ranked by structural safety, and framed as the §2.6 shapes:

1. **2-high → 1-high, and any deep-shell re-map: `40 → 40` with a different
   `ctx+1` landmark byte.** The safest possible rotation in the engine — the
   target state has *zero* pre-snap-only inputs, the landmark is re-resolved every
   tick from the byte you write, and `0x001BE708` already performs this exact
   push. **This is the rotation the design should build on first.**
2. **Man → deep zone (`22 → 40`).** Shipped verbatim at `0x001BE708` for FS/SS.
3. **Anything → 39, and corner-level rotations into 37.** Structurally free; 37 at
   the snap risks the jam diversion.
4. **Anything → 22 (show zone, play man).** Also shipped (`0x0019EEF8`), so the
   mechanism is proven — but choosing the matchup works **only outside the ≈30-tick
   freshness window**. At the snap you inherit the authored designator for that
   slot, which is still a legal disguise (the *shown* alignment differs from the
   *played* man), just not a chosen one.
5. **Anything → 38 (rotate down into a hook).** Last resort: the drop depth
   becomes wherever the defender already is.

Two constraints the feature inherits, both re-confirmed here:

* **Rotate by pushing the assignment triple, never by re-applying alignment.**
  §B3.7's third open item stands: `0x00242E98` is pre-snap-shaped.
* **A "zone" is a receiver slot.** A rotation that moves a defender's `ctx+2`
  moves him onto a *receiver ordinal from the last pre-snap frame*, so the shown
  and played shells are separable exactly as far as receiver ordinals allow.

### 1.9 What Q1 leaves open

* **The tick rate of the `playArt[0]` countdown.** 30 *what* — frames, AI ticks,
  physics steps? Statically undecidable. *A live read settles it: watch
  `[[0x006012C8]+8]` as a signed halfword across a snap.*
* **Where states 37 and 38 get their per-tick zone target**, given that neither
  `enter` nor the first 240 instructions of either `ai_think` reads the context
  directly (both read `player+0x152` instead). 39 and 40 are resolved; 37 and 38
  are not. Does not change the Q1 verdict — the question is what `enter` depends
  on — but it is the next thing to pin before writing the rotation.
* **`playArt+4`'s setter.** Its *reset* is located — `0x001BFD90` fills **two**
  11-slot byte arrays with the 0xFF "none" sentinel and then clears the three
  flags:
  ```
  001BFDB0  2486002C  addiu a2, a0, 44        ; playArt+44 : the man designators
  001BFDB4  24850018  addiu a1, a0, 24        ; playArt+24 : a SECOND 11-slot table
  001BFDC4  A0670000  sb a3, 0(v1)            ; 0xFF x 11
  001BFDEC  A0660000  sb a2, 0(v1)            ; 0xFF x 11
  001BFDFC  A0800006  sb zero, 6(a0)
  001BFE04  A0800004  sb zero, 4(a0)
  001BFE0C  A0800005  sb zero, 5(a0)
  ```
  but the code that *populates* `+4` and `+44…54` was not found. *Scope: all 48
  `jal 0x001F86D8` sites, each forward-tracked 90 instructions with register-copy
  propagation through `daddu`/`addiu`; six stores found, all listed above plus
  `0x001BB070` (`sb a0, 24(v0)`).* **Hypothesis:** the populate path is
  `0x00233C64` / `0x00234D64` (the play-art reader family named in §B3.1a), which
  hand the pointer to a callee. The freshness counter alone explains every branch
  observed in the five `enter` handlers, so this does not change the Q1 verdict —
  but a second gate on the authored-designator override has not been ruled out.
* **`playArt+24…34`** — a second 11-slot per-formation-slot byte table, sentinel
  0xFF, purpose unidentified.

---

## 2. Q2 — is there a free de-cheese switch on PS2?

### 2.1 The two getters, and their only guard

Both `ptrk` getters were re-derived from this image (not carried over):

```
; 0x0024E188 -- RepetitionFactor getter
0024E188  27BDFFF0  addiu sp, sp, -16
0024E18C  FFBF0000  sd ra, 0(sp)
0024E190  0C05CA58  jal 0x00172960          ; <== the only guard
0024E194  00000000  nop
0024E198  10400004  beq v0, zero, 0x0024e1ac  ; PLAIN beq: the delay slot always runs
0024E19C  DFBF0000  ld ra, 0(sp)
0024E1A0  44800000  mtc1 zero, f0           ; guard true -> return 0.0f
0024E1A4  10000003  beq zero, zero, 0x0024e1b4
0024E1A8  00000000  nop
0024E1AC  8F82C7C4  lw v0, -14396(gp)       ; = *(0x00601EB4), the ptrk header
0024E1B0  C4400000  lwc1 f0, 0(v0)          ; header +0x00 = f
0024E1B4  03E00008  jr ra
0024E1B8  27BD0010  addiu sp, sp, 16
```
```
; 0x0024E1C0 -- success-factor getter: identical, only the field offset differs
0024E1C8  0C05CA58  jal 0x00172960
0024E1D0  10400004  beq v0, zero, 0x0024e1e4
0024E1D4  DFBF0000  ld ra, 0(sp)
0024E1D8  44800000  mtc1 zero, f0
0024E1E4  8F82C7C4  lw v0, -14396(gp)
0024E1E8  C4400004  lwc1 f0, 4(v0)          ; header +0x04
```

**Finding: exactly ONE early-return-0.0 path per getter, and it is the same call in
both. No second guard, no `movn`/`movz`, no jump table, no null check on the ptrk
pointer** — `0x00172960` returning nonzero is also what stops `lwc1 f0, 0(v0)`
dereferencing a null header before the tracker exists.

**Finding — no consumer bypasses the getters.** *Scope: whole-image scan for
gp-relative accesses to `gp−14396` (`0x00601EB4`) — 25 hits, every one inside
`0x0024CAFC`-`0x0024E45C`, the `ptrk` module itself.* Consumer counts re-derived:
9 callers of `0x0024E188`, 6 of `0x0024E1C0`, 15 total, matching
`docs/play-tendency-ai.md`.

### 2.2 What `0x00172960` reads — the same flag as the Xbox one

```
00172960  8F83B810  lw v1, -18416(gp)   ; *(0x00600F00)
00172964  10600003  beq v1, zero, 0x00172974
00172968  0000102D  daddu v0, zero, zero
0017296C  9062017E  lbu v0, 382(v1)     ; +382 = 0x17E
00172970  0002102B  sltu v0, zero, v0
00172974  03E00008  jr ra
```

i.e. `IsPractice() { p = *(void**)0x00600F00; return p ? p->byte[0x17E] != 0 : 0; }`

**Finding — object identity, from the constructor `0x001720A0`:**

```
001720A4  3C087072  lui t0, 0x7072
001720B4  2785B810  addiu a1, gp, -18416    ; the slot at 0x00600F00
001720BC  24060184  addiu a2, zero, 388     ; sizeof == 388
001720CC  35086163  ori t0, t0, 0x6163      ; t0 = 0x70726163 = 'prac'
001720E0  0C0E75B2  jal 0x0039d6c8          ; registry create
```

**Finding: `docs/play-tendency-ai.md`'s "`'prac'` object + offset 382" is confirmed
by disassembly** — fourcc `'prac'`, size 388, flag at `+0x17E`.

**Finding: the PS2 practice check and the Xbox "kill switch" are the SAME flag.**
`docs/xbox-hook-map.md` records the Xbox getters testing `byte [[0x00532B48]+0x17E]`
after a null check on the pointer; PS2 tests `byte [[0x00600F00]+0x17E]` after a
null check on the pointer. Same offset, same null-then-test shape — the Xbox
compiler inlined this exact `IsPractice()` where the PS2 build kept the `jal`.
**Correction to `docs/xbox-hook-map.md`:** the hypothesis there that
`0x00532B48` is "a settings object" and `+0x17E` "a difficulty or classic/cheat-off
option" is **refuted**. It is the `'prac'` object and the practice-mode flag.
There is no second, distinct guard on PS2.

### 2.3 Is the flag settable outside practice mode? No.

**Finding — three writers, closed set, all at construction:**

| addr | instruction | reached by |
|---|---|---|
| `0x0039CFEC` | `jal 0x004b3e88` = `memset(obj, 0, 388)` | generic registry-object allocation (`0x0039CF70`) — zeroes the struct before the ctor |
| `0x0017211C` | `sb zero, 382(s2)` | `'prac'` ctor, **branch-likely delay slot**, taken only when `mode != 13` |
| `0x00172124` | `sb v0, 382(s2)` (`v0 = 1`) | `'prac'` ctor, `mode ∈ {3, 10, 13}` |

```
001720F4  0C05C348  jal 0x00170d20          ; GetMode
001720F8  0040902D  daddu s2, v0, zero      ; delay slot: captures the PREVIOUS call's
                                            ;   return -> s2 = the 'prac' object
001720FC  0040182D  daddu v1, v0, zero      ; v1 = the mode id
00172100  24020003  addiu v0, zero, 3
00172104  10620006  beq v1, v0, 0x00172120
00172108  AE430178  sw v1, 376(s2)          ; plain beq -> always runs: prac+0x178 = mode id
0017210C  2402000A  addiu v0, zero, 10
00172110  10620003  beq v1, v0, 0x00172120
00172114  2402000D  addiu v0, zero, 13
00172118  54620003  bnel v1, v0, 0x00172128 ; BRANCH-LIKELY
0017211C  A240017E  sb zero, 382(s2)        ;   delay slot: runs ONLY when mode != 13
00172120  24020001  addiu v0, zero, 1
00172124  A242017E  sb v0, 382(s2)          ; flag = 1
```

so `flag = 1 iff modeId ∈ {3, 10, 13}`, and the mode id is not a runtime variable —
it is a **DB read**:

```
00170D2C  24A5E960  addiu a1, a1, -5792     ; 0x0057E960
00170D38  0C132E32  jal 0x004cb8c8          ; the query engine
0057E960  "select 'PYTM' into \x82 from 'NIOM'\n"
```

The ctor has one caller (`0x001026B0` inside `0x00102550`), reached once from
`0x0010314C` inside `0x00102F88` — the function whose string pool holds
`'GameLoopEnterStart'`, `'game.qkl'`, `'gamedata.dat'`. **The flag is latched once
per game-loop entry, before the pointer is even published.**

**The negative, with its scopes** (each re-derived this session):

1. *Every store opcode in the image whose byte range covers offset 382*, any width
   (`sb/sh/sw/sd/sq/swl/swr/sdl/sdr/swc1/sdc1`), 1,338,509 words → **10 hits**; two
   are the ctor pair, the other eight resolve to different structs (`0x00168D00`,
   `0x0023B8D8`, `0x0023C2F8`, `0x004447B4`, `0x00473148`, `0x00477288`,
   `0x00487688`, `0x004BAC44` — the last is `sq s7, 368(k1)`, an exception-context
   save).
2. *base+K form* — every store whose immediate `D` plus a preceding (≤24 insn)
   `addiu base, X, 382−D` sums to 382 → **1 hit**, `0x001CF188 sh zero, 46(s0)`
   with `0x001CF174 addiu s0, s1, 336`; that struct has a field at +764, so it is
   not the 388-byte `'prac'`.
3. *Pointer reachability* — all gp-relative accesses to `gp−18416` → **74 hits, all
   inside `0x001712C0`-`0x00173868`**, all `lw` except the ctor's
   `addiu a1, gp, -18416`. **No `sw` to the slot anywhere in the image.**
4. *Pointer escape* — taint scan of `0x00171000`-`0x00174000`: 9 sites pass a
   prac-derived value in `a0..t0`; all six distinct callees resolved
   (`0x001F82E8`, `0x00248C70`, `0x00248D00`, `0x00260598`, `0x00154790`,
   `0x0025FF58`) and none takes the object. **The pointer never leaves the module.**
5. *DB route* — every `'NIOM'` update string: `PYTM` ×4, plus `IANM`/`ASCM`/`ASPM`;
   **no `update 'NIOM' set \x8c` dynamic-field form exists**, and there are no
   standalone `'PYTM'`/`'NIOM'` identifier strings a generic setter could name.
   (Named for honesty: `0x002AA7F8` emits a fully generic
   `update \x8c set \x8c = \x82` with caller-supplied table and field; its one call
   site `0x002AA888` is not on any PYTM path found.)

**Finding: the flag is written only at `'prac'` construction, only from the DB mode
id, and only for `mode ∈ {3, 10, 13}`. No option, difficulty setting, menu, or
debug flag reaches it, and a runtime poke would not survive the next
`GameLoopEnterStart`.** **Hypothesis:** 3/10/13 are Practice / Mini Camp /
Situation — supported (not proven) by mode 10 alone triggering the 38-iteration
drill-table setup at `0x001721BC`-`0x00172210`, by seven `mode == 10` checks in
`0x00177684` (the audible/shift string pool), and by 3/10/13 always travelling
together across the 61 `GetModeId` call sites.

### 2.4 Flipping it would not be free anyway

**Finding: `0x00172960` has 169 call sites in 133 distinct functions**, spanning
`0x0010330C` to `0x00315558`; the sibling mode getter `0x00172980`
(`lw v0, 376(v1)`) has 61 more. Spot-checked consumers, each of the form
*practice ⇒ skip this behaviour entirely*:

```
0014FE54  jal 0x00172960 / 0014FE5C bne v0,zero  ; 0x0014FE20 = the fatigue drain fn
001A4CA8  jal 0x00172960 / 001A4CB0 bne v0,zero  ; 0x001A4C50 = penalty generator
001837C0  jal 0x00172960 / 001837C8 bne v0,zero  ; 0x001837B0 = sideline / out-of-bounds
00170274  jal 0x00172960 / 0017027C beql v0,zero ; 0x00170178 = down / turnover announcer
0022FFE8  jal 0x00172960 / 0022FFF0 bne v0,zero  ; 0x0022FD54, string pool 'Goalpost'
0024E404  jal 0x00172960 / 0024E40C beq v0,zero  ; the ptrk franchise save/load path
```

Setting the flag in a normal game would disable fatigue drain, penalties,
out-of-bounds handling, the announcer, goalpost handling and franchise stat-ring
persistence, and would leave the engine **internally inconsistent**: `prac+0x178`
would still report Exhibition/Franchise, and 61 sites read *that* instead.

### 2.5 The patch that actually is cheap

Because the guard is a **plain `beq`** (delay slot always executes) and the 0.0 path
is already inside each function, neutering a getter is **one word → `nop`**:

```
0x0024E198:  10400004  ->  00000000     ; repetition getter
0x0024E1D0:  10400004  ->  00000000     ; success getter
```

Traced: with the branch nopped, control falls `ld ra, 0(sp)` → `mtc1 zero, f0` →
`beq zero, zero, <epilogue>` → `jr ra` / `addiu sp, sp, 16`. Returns 0.0f with `ra`
and `sp` correct, on every call, in every mode. **8 bytes, two words, no cave, no
branch fix-ups, no relocation**, and all 15 consumers are covered because no
consumer touches `gp−14396` directly.

### 2.6 VERDICT — Q2

**Finding: NO. There is no free de-cheese switch on PS2, and there was never one on
Xbox either.** The "shipped kill switch" is the practice-mode flag:
PS2 `'prac' +0x17E` **is** Xbox `[[0x00532B48]+0x17E]` — same object, same offset,
same idiom. Its writer set is closed to three construction-time sites driven by
`NIOM.PYTM`; nothing outside practice can set it; and 169 + 61 other branches ride
on it, so flipping it is a mode change, not a de-cheese.

**Phase 4 therefore stays a 2-getter patch — but at 2 words, not a cave.** That is
cheaper than the plan assumed and cheaper than the Xbox equivalent, and it is the
whole of the fix: the getters are the only door onto the tracker.

**Consequence for the requirements doc.** §2.6/Phase 4 should record that
"drop the cheese" is 8 bytes of pnach, and that the anti-repetition ring itself
(`0x0024E0F0`, the weights at `0x00540FE0`) survives intact underneath — so a later
coach-brain can still *read* the ring for legitimate, symmetric tendency purposes
while the CPU-only combat boosts stay zeroed.

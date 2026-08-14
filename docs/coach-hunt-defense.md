# Defensive-adjustment static hunt — ledger items B1 / B2 / B3

Static investigation, 2026-08-14, against `extract/SLUS_207.52` (Madden NFL 2004,
SLUS-20752, CRC 0x14F8B841). Static only — no rig, no emulator, nothing patched.
`vaddr = file_offset + 0xFF000`; single `PT_LOAD` (vaddr `0x00100000`, offset
`0x1000`, filesz `0x509579`, memsz `0x559FDC`), so every address quoted here is
file-backed and re-derivable. `gp = 0x006056F0`.

Prefix key: **Finding** = verified against this binary, evidence quoted;
**Hypothesis** = inference, not nailed; **Correction** = a prior doc statement was
wrong.

Answers the §7 ledger items:
* **B1** — the defensive pre-snap-adjust / audible-response trigger.
* **B2** — defensive personnel packages (base / nickel / dime).
* **B3** — the coverage-rotation primitive. **Verdict in §B3.6.**

---

## 0. The map, in one picture

Two independent machines produce the CPU's defensive pre-snap behaviour. They
never touch each other's data:

```
                     THE ALIGNMENT MACHINE                    THE ASSIGNMENT MACHINE
                     (what the front LOOKS like)              (what each defender DOES)

  read   0x00145188  formation-strength L/R count
           │  (edge)
           ▼
  arm    0x001450C0  flip the D formation + arm a 20-39 tick timer
           │
           ▼
  pick   0x00145940  5-option roulette per slot (LB, DL)
           │
           ▼
  apply  0x00145018 -> 0x002437E0  token channel 0x006143F0[0..3]
           │                        {Norm, LB_P/L/R/S, DL_P/L/R/S, Tigh, Loos}
           ▼
         0x002430E0 -> 0x002436A0 -> 0x00242848 -> 0x00242E98
           │            per-player ALIGNMENT variant record
           ▼            playbookBlk+60 +140 +variant*440 +idx*40
         moves the defender, plays a stance anim
                                                              0x00243B10 -> 0x00243980
                                                                per-player STATE CHAIN
                                                                playbook_base[side] +5120 +63 +idx*40
                                                                -> 0x001B00A0 (set) + 0x001AFF48 (queue)
                                                                = AI states 22 / 37..40 / ...
                                                              installed ONCE, at play setup
```

Everything the CPU defense does pre-snap lives in the left column. The right
column — the coverage assignment itself — is never re-run after play setup.
That asymmetry is the whole of the B3 answer.

A third machine, **personnel**, is authored in the playbook DB and has exactly one
runtime variable — `block+0x24`, the active package id — which no ELF code ever
computes (§B2).

Summary of the three answers:

| | today | the lever | what it costs |
|---|---|---|---|
| **B1** front adjust | fires off a per-frame formation-strength read; re-rolls a 5-option roulette on a random 20-39 tick timer; can never pinch | `0x00145188` (the read) → `0x001450C0` (arm) → `0x0051D450` (weights) | replace one boolean function; the §2.4 checks reuse the whole cadence |
| **B2** personnel | a complete authored package layer that nothing ever selects | `block+0x24`, or command `0x80000022` | one write, **if** the shipped playbooks contain `SPKF`/`SPKG` rows |
| **B3** coverage | locked at the snap; no shell rotation; but a man→deep-zone safety bail-out already runs post-snap | `0x001AFB50` / `0x001B00A0` + the 3-byte assignment triple | a new call site, not a new mechanism |

---

## B1 — the defensive pre-snap-adjust trigger

### B1.1 The chain, end to end (Finding)

The pre-snap game state is row `0x0057C878` of the state-triple table in rodata:

```
0057C878  0017AD00     ; enter
0057C87C  0017B2B8     ; update  (per frame)
0057C880  0017B498     ; exit
```

`enter` and `update` each call into the defensive-adjust module:

```
0017AFD4  0C051712  jal 0x00145c48        ; enter  -> initial formation read
0017B3B0  0C05175A  jal 0x00145d68        ; update -> per-frame formation read
```

`0x00145D68` is the per-frame heart. Its prologue (mind the `jal` delay slots —
each `daddu sX, v0, zero` sits in a delay slot and therefore captures the
*previous* call's return value):

```
00145D7C  0C07E1BA  jal 0x001f86e8            ; -> the "coach" object
00145D80  00000000  nop
00145D84  0C06FACA  jal 0x001beb28
00145D88  0040802D  daddu s0, v0, zero        ; s0 = 0x001F86E8's return = coachObj
00145D94  0C09816C  jal 0x002605b0
00145D9C  0040902D  daddu s2, v0, zero        ; s2 = DEFENSE side
00145DA4  0C0920CE  jal 0x00248338            ; s2 -> defence playbook block +60
00145DAC  8C43001C  lw v1, 28(v0)
00145DB0  30630001  andi v1, v1, 0x0001
00145DB4  5060001F  beql v1, zero, 0x00145e34   ; likely
00145DD4  0C05AD64  jal 0x0016b590            ; who controls the defence?
00145DD8  0240202D  daddu a0, s2, zero
00145DDC  240300FF  addiu v1, zero, 255
00145DE0  54430014  bnel v0, v1, 0x00145e34     ; likely -- 0xFF = NO human => CPU defence
00145DE8  0C051462  jal 0x00145188            ; <== THE READ
00145DEC  0200202D  daddu a0, s0, zero
00145DF0  50400008  beql v0, zero, 0x00145e14   ; likely
00145DF8  92020100  lbu v0, 256(s0)           ; the CACHED previous answer
00145DFC  1440000B  bne v0, zero, 0x00145e2c
00145E04  0C051430  jal 0x001450c0            ; <== EDGE 0->1 : re-arm
00145E08  A2020100  sb v0, 256(s0)
...
00145E18  14620004  bne v1, v0, 0x00145e2c
00145E20  0C051430  jal 0x001450c0            ; <== EDGE 1->0 : re-arm
00145E24  A2000100  sb zero, 256(s0)
...
00145E60  08051650  j 0x00145940              ; tail-call the picker
00145E64  27BD0040  addiu sp, sp, 64
```

**`0x00145940` has no `jal` caller at all** — it is reached only by this tail
`j` at `0x00145E60` (raw scan of the whole image for `0x0C051650`/`0x08051650`
found exactly one hit, the `j`). *(Correction: `ai-coach-investigation-playcaller.md`
lists `0x00145940` as an entry point without naming its caller; the caller is
`0x00145D68`, itself a pre-snap-state update hook.)*

The coach object is a fixed sub-object, not a search result:

```
001F86E8  8F82BBD8  lw v0, -17448(gp)   ; = *(0x006012C8)
001F86EC  03E00008  jr ra
001F86F0  24420048  addiu v0, v0, 72    ; coachObj = *(0x006012C8) + 72
```

### B1.2 The trigger is a FORMATION READ, not the audible event (Finding)

`0x00145188` is the read. It answers one question — *which side of the ball is
the offence's strength?* — and returns a boolean:

```
00145194  249E0104  addiu fp, a0, 260         ; fp = coachObj+260, a 5-slot float cache
0014519C  249700E9  addiu s7, a0, 233         ; s7 = coachObj+233, 5 player indices
001451CC  0C098166  jal 0x00260598            ; s3 = OFFENCE side
001451E0  0C0920CE  jal 0x00248338            ; s2 = OFFENCE playbook block +60
001451E8  0C098082  jal 0x00260208            ; the ball / LOS position
00145208  C7B40010  lwc1 f20, 16(sp)          ; f20 = ball X (float)
  ; loop s1 = 0..4 over the five tracked players
00145218  0260202D  daddu a0, s3, zero
0014521C  0C05956C  jal 0x001655b0            ; v1 = player object (side, idx)
00145220  92050000  lbu a1, 0(s0)
0014522C  8C62000C  lw v0, 12(v1)
00145230  00441024  and v0, v0, a0            ; a0 = 0x00040000 ("has moved")
00145234  54400005  bnel v0, zero, 0x0014524c   ; likely
00145238  C4600190  lwc1 f0, 400(v1)          ;   -> use the LIVE position (+0x190)
0014523C  8C620000  lw v0, 0(v1)
00145240  56C20004  bnel s6, v0, 0x00145254     ; likely
00145248  C4600190  lwc1 f0, 400(v1)          ;   -> live position again
  ; otherwise: the AUTHORED alignment of the offence's CURRENT play
00145258  0C090A12  jal 0x00242848            ; resolve the per-player record
00145260  92440017  lbu a0, 23(s2)            ; the offence's flip byte
00145270  38840001  xori a0, a0, 0x0001
00145274  00A4180A  movz v1, a1, a0           ; MOVZ: record+16 or record+24
00145278  C4600000  lwc1 f0, 0(v1)            ; the authored X
  ; classify against the ball X with a 0.5 dead zone
001452A8  46140801  sub.s f0, f1, f20
001452AC  46000005  abs.s f0, f0
001452B0  4600A834  c.lt.s f21, f0            ; f21 = 0.5 (lui at,0x3F00)
001452B8  45000009  bc1f 0x001452e0
001452BC  E4410000  swc1 f1, 0(v0)            ; NOT branch-likely: always caches
001452C0  4601A036  c.le.s f20, f1
001452C8  45000003  bc1f 0x001452d8
001452CC  26820001  addiu v0, s4, 1           ; count on one side
001452D8  26A20001  addiu v0, s5, 1           ; count on the other
  ; return
001452F4  0295102B  sltu v0, s4, s5
00145300  38420001  xori v0, v0, 0x0001       ; return (s4 >= s5)
```

`0x00260208` reads the situation object (`*0x00601F4C`) fields +12/+16 and packs
them into a 64-bit; the low word taken as a float is the reference X.

**Finding: the trigger is a per-frame re-read of the offensive formation's
strength side. There is no audible hook anywhere in the chain.** No code in the
module reads an "audible happened" event, and nothing in the audible executor
(`0x001785F0`, §B1.4) pokes the defence.

**Hypothesis (why the operator sees a counter-adjust on EVERY audible):** the read
takes each un-moved player's X from `0x00242848(offencePlaybookBlk+60, idx, 0)` —
the authored alignment of the offence's **currently selected play**. An audible
overwrites that play record wholesale (`0x00249068`, §B1.4), so the read's inputs
change the instant the play id changes, before any player physically moves. That
is a zero-latency read of just-selected play data — legitimate-looking in shape,
omniscient in timing. *A live read settles it: break on `0x00145188`, audible
within a formation, and watch whether `[coachObj+256]` flips before the offensive
players move.*

### B1.3 The cadence — this is the "willy-nilly shuffle" (Finding)

`0x001450C0` is what an edge triggers. It does three things:

```
001450F8  92230100  lbu v1, 256(s1)           ; cached strength
001450FC  90A40017  lbu a0, 23(a1)            ; the DEFENCE's flip byte
00145100  2C820001  sltiu v0, a0, 1
00145104  10430005  beq v0, v1, 0x0014511c
0014510C  A0A20017  sb v0, 23(a1)             ; (1) FLIP the defensive formation
00145114  0C090D46  jal 0x00243518            ;     and re-apply it
00145118  0000282D  daddu a1, zero, zero
0014511C  0C05AD64  jal 0x0016b590
00145124  240300FF  addiu v1, zero, 255
00145128  14430011  bne v0, v1, 0x00145170    ; CPU-defence only
00145138  0C051406  jal 0x00145018            ; (2) re-apply both shift tokens
0014513C  0220202D  daddu a0, s1, zero        ;     (loop s0 = 0,1)
00145150  24020001  addiu v0, zero, 1
00145158  A2220011  sb v0, 17(s1)             ; (3) pending = 1
0014515C  0C0BE50A  jal 0x002f9428            ;     rand(0,20)
00145160  24050014  addiu a1, zero, 20
00145164  24420014  addiu v0, v0, 20
00145168  AE22000C  sw v0, 12(s1)             ;     countdown = 20 + rand -> 20..39 ticks
```

And `0x00145940` fires only when the timer expires:

```
0014595C  92420011  lbu v0, 17(s2)            ; pending?
00145960  1040004C  beq v0, zero, 0x00145a94
0014597C  8E42000C  lw v0, 12(s2)
00145980  2442FFFF  addiu v0, v0, -1
00145984  0062182A  slt v1, v1, v0            ; v1 = -1
00145988  0003100A  movz v0, zero, v1         ; MOVZ: clamp to 0 when it went negative
0014598C  14400040  bne v0, zero, 0x00145a90  ; still counting -> bail
00145990  AE42000C  sw v0, 12(s2)             ; (delay slot: always stores)
...
00145A8C  A2400011  sb zero, 17(s2)           ; pending = 0
```

So: read flips → arm → 20-39 frames later → re-roll the front. Exactly the
"shuffle with no thought behind it" the Architect observed, with the randomness
now pinned to `0x002F9428` at `0x0014515C`.

Two more CPU-defence-only gates guard the whole thing:
`0x002537C8` (checked at `0x00145994`) and `0x00253888` (at `0x001459A4`); both
start by calling `0x00260598` and flipping the side, i.e. both are asking about
the defence.

### B1.4 What it CAN change — and what it cannot (Finding)

**It changes the token channel and the formation flip. Nothing else.**

The token channel is a 4-slot array of C-string pointers at **`0x006143F0`**:

```
00243770  3C020061  lui v0, 0x0061            ; reset-all-to-"Norm"
00243778  24631E18  addiu v1, v1, 7704        ; v1 = 0x00601E18 = "Norm"
0024377C  244443F0  addiu a0, v0, 17392       ; a0 = 0x006143F0
00243780  AC83000C  sw v1, 12(a0)             ; [3]
00243784  AC4343F0  sw v1, 17392(v0)          ; [0]
00243788  AC830004  sw v1, 4(a0)              ; [1]
0024378C  03E00008  jr ra
00243790  AC830008  sw v1, 8(a0)              ; [2]
```

| slot | meaning | setter | who calls the setter |
|---|---|---|---|
| `[0]` | D-line shift | `0x002437E0(0, tok)` | CPU `0x001450A8`; human "Line Shift" via `0x00178F80` |
| `[1]` | coverage depth | `0x00243798(tok)` | **only** `0x00178DD8 / 0x00178DF0 / 0x00178E3C / 0x00178E68`, all inside `0x00178D60` = the human's *Coverage Audible* |
| `[2]` | LB shift | `0x002437E0(2, tok)` | CPU `0x001450A8`; human "LB Shift" via `0x00178F80` |
| `[3]` | (reset only) | — | — |

The token vocabularies are 8-byte-stride `.data` tables. The CPU's copy:

```
00600C28 "Norm"   00600C30 "LB_P"  00600C38 "LB_L"  00600C40 "LB_R"  00600C48 "LB_S"
00600C28 "Norm"   00600C50 "DL_P"  00600C58 "DL_L"  00600C60 "DL_R"  00600C68 "DL_S"
```
reached through two 5-word pointer arrays at `0x0057BFD0` (slot 0 = LB) and
`0x0057BFE8` (slot 1 = DL). The human's identical vocabulary lives at
`0x00600F88`/`0x00600F90…` and `0x00600FB0…`, reached through `0x0057F250` /
`0x0057F268` inside `0x00178F80`. The coverage vocabulary is
`0x00600F78 "Tigh"`, `0x00600F80 "Loos"`, `0x00600F88 "Norm"` — **press depth, not
a coverage shell.**

**Finding: the CPU and the human's D-pad converge on the same channel and the
same applier.** Both call `0x002437E0`, which stores the token and tail-calls
`0x002430E0` → `0x002436A0`:

```
002437E0  308400FF  andi a0, a0, 0x00ff
002437E8  3C020061  lui v0, 0x0061
002437EC  244243F0  addiu v0, v0, 17392       ; 0x006143F0
002437F0  00042080  sll a0, a0, 2
002437FC  00822021  addu a0, a0, v0
00243800  0C09816C  jal 0x002605b0            ; defence side
00243804  AC850000  sw a1, 0(a0)              ; token[idx] = a1
0024380C  0C0920CE  jal 0x00248338            ; -> playbook block +60
00243824  08090C38  j 0x002430e0
```

`0x002436A0` then re-resolves every defender's ALIGNMENT record and applies it:

```
002436E0  0C090A12  jal 0x00242848            ; resolve the record for this token set
002436E4  94700092  lhu s0, 146(v1)           ; the record currently in force
002436F0  96220006  lhu v0, 6(s1)
002436F4  10500008  beq v0, s0, 0x00243718    ; unchanged -> skip
00243710  0C090BA6  jal 0x00242e98            ; apply record -> player
00243714  0220282D  daddu a1, s1, zero
```

and `0x00242E98` moves the player and plays a stance animation — it does **not**
touch the assignment:

```
00242EC8  8E2502FC  lw a1, 764(s1)            ; the player's AI state block (+0x2FC)
00242ECC  90A20000  lbu v0, 0(a1)
00242ED0  10430078  beq v0, v1, 0x002430b4    ; v1 = 42 (defensive pre-snap) -> skip
00242F24  3A620001  xori v0, s3, 0x0001
00242F2C  00C2280B  movn a1, a2, v0           ; MOVN: record+16 or record+24 (alignment)
00242F4C  26250190  addiu a1, s1, 400         ; vs the player's live position
00242F98  0C06BFD2  jal 0x001aff48            ; queue state 7 (walk to the spot)
00242FDC  0C06BFD2  jal 0x001aff48            ; queue state 6 (variant)
0024307C  92420009  lbu v0, 9(s2)             ; record+9/+10 = a STANCE ANIM id
0024309C  0C06BFD2  jal 0x001aff48            ; queue state 9 (play that stance)
```

State 9's `enter` (`0x001E4170`) confirms the param is an animation, not an
assignment — it feeds `0x003ACDF8(player+772, +776, +780, …, s3)`.

**What the CPU cannot change:** the play call. The per-side pre-snap command word
`[*(0x00600F64) + 260 + side*28]` — the queue behind the human's audible / hot
route / shift buttons — has **no AI writer**. A full scan of the image for any
load/store at offset `0x104` (181 hits) resolves to these writers, all inside the
human input dispatcher `0x00177688` or its executors:

```
00177904 (code 1, Audible)   001779FC (code 1)   00177B4C (code 2)
00177C7C (code 6, LB Shift)  00177D78 (code 5, Line Shift)  00177E74 (code 4, Coverage Audible)
00178978 / 00178B0C / 00178D10 / 00178F04 / 00179188 (code 7, executors marking done)
0017A0E8 (code 9)  0017A3F0 / 0017A5C8 (restore from +264)  00177074 (copy +264 -> +260)
```
(`0x0017DB9C` writes +260 of a *different* global, `*(0x00600FEC)` — excluded.)

The human's dispatcher `0x00177688` decodes a 125-entry jump table at
**`0x0057EE40`** (`lui v0,0x0058 / addiu v0,v0,-4544`; `addiu` sign-extends, so
the base is `0x0057EE40`, not `0x0058EE40`), index = `cmdId - 7`. The defensive
group is commands 83-89, gated by `addiu v0, s5, -83 / sltiu v0, v0, 7` at
`0x00177760`. Command 87 = LB Shift, 88 = Line Shift, 85 = Coverage Audible;
their HUD strings are `0x0057EE00 "LB Shift"`, `0x0057EE10 "Line Shift"`,
`0x0057EE20 "Coverage Audible"`. Each defensive command gates on
`bnel s0, s1` (the acting player's side must equal the defence side).

At the snap, `0x0017B668` (called from `0x002BC39C`, the snap/host transition)
replays whatever was queued, via a 9-entry table at `0x0057F3F0`:

| code | executor | meaning |
|---|---|---|
| 1 | `0x001785F0(obj, 98)` | play audible |
| 2 | `0x001789D0(obj, 106)` | — |
| 3 | `0x00178B60(obj, 106)` | — |
| 4 | `0x00178D60(obj, 118)` | coverage depth (Tigh/Loos/Norm) |
| 5 | `0x00178F80(obj, 118, 0)` | line shift |
| 6 | `0x00178F80(obj, 118, 2)` | LB shift |
| 8 | `0x00179BB0(side, obj, 131)` | — |

The audible executor `0x001785F0` maps command ids 98-105 through an 8-entry
table at `0x0057F040` to audible slots 0-4 (plus a flip at slot code 104), and
the actual play swap is a bulk copy inside the playbook block:

```
00249068  3402AFBC  ori v0, zero, 0xafbc      ; playbook stride
00249074  8F834080  lw v1, 16512(gp)          ; playbook base = *(0x00609770)
00249084  24642BF4  addiu a0, v1, 11252       ; source: the audible's record
00249080  24650024  addiu a1, v1, 36          ; dest:   the CURRENT play record
```

### B1.5 The option set and the weights — Correction to the requirements doc

The 5 options per slot are **0 = Norm, 1 = Pinch, 2 = Left, 3 = Right,
4 = Spread**, read straight off the token vocabulary above.

**Correction (address).** The starting-weight table is at **`0x0051D450`**, not
`0x0052D450`. `lui v0,0x0052 / addiu s1,v0,-11184` at `0x00145A34` sign-extends
to `0x00520000 - 11184 = 0x0051D450`. The immediately preceding rodata string is
the source filename:

```
0051D440  43 55 53 54 4F 4D 41 49 2E 43 00 ...   "CUSTOMAI.C"
0051D450  F4 01 00 00 C8 00 C8 00 64 00 | F4 01 00 00 FA 00 FA 00 00 00
```

Two 10-byte rows of five halfwords:

| slot | Norm | Pinch | Left | Right | Spread |
|---|---|---|---|---|---|
| 0 (LB) | 500 | **0** | 200 | 200 | 100 |
| 1 (DL) | 500 | **0** | 250 | 250 | **0** |

**Correction (behaviour).** The requirements doc (§2.7) says the picker "uses
FIXED weights that NEVER pinch or spread (the 0.0 weights)" and
`ai-coach-investigation-playcaller.md` reports only three calls with multipliers
1.0/0.5/0.0. Both are partial. `0x00145508` makes **27** calls to the accumulator
`0x001454A8`; tracking `a1` across the whole body gives the option indices:

```
index 0 (Norm)   x 9      index 2 (Left)  x 7
index 1 (Pinch)  x 0      index 3 (Right) x 9      index 4 (Spread) x 2
```

**Finding: the CPU can never pinch.** Pinch starts at weight 0 in both rows and
is not one of the 27 add sites, so its weight is 0 at every roulette. Norm keeps
a 500 floor and also receives additions. Spread is reachable for the LBs (base
100) and, for the D-line, only through the two index-4 add sites (`0x001455C0`,
`0x00145720`). The additions themselves are **read-driven**, not fixed: their
multipliers come from the `ptrk` block the caller filled (`lwc1 f12, 16(s1)`,
`12(s1)`, and differences of those fields at `0x001456F4`-`0x001458F0`), plus a
hysteresis bonus for the currently-held option:

```
00145608  00131080  sll v0, s3, 2             ; s3 = slot
0014560C  02821021  addu v0, s4, v0           ; s4 = coachObj
00145610  8C430004  lw v1, 4(v0)              ; the slot's CURRENT pick
00145614  54720030  bnel v1, s2, 0x001456d8   ; likely
```

The roulette itself (`0x001453E0`) sums the five halfwords, draws
`0x002F9428(0)`, walks the cumulative sum, and on a change writes the pick and
applies it:

```
00145484  AC670000  sw a3, 0(v1)              ; coachObj + 4 + slot*4 = the pick
00145488  0C051406  jal 0x00145018            ; apply
```

### B1.6 The ceiling on §2.7 — the shift is only as good as the authored data

`0x00145018` (and the human's `0x00178F80`) only *names* a token. The alignment
that results is looked up, not synthesised:

```
00242AD0 ... (token list) -> a variant index
00242B4C  26D1002A  addiu s1, s6, 42          ; per-playbook token table: name at +36+k*8,
00242B50  26D00024  addiu s0, s6, 36          ;   u16 id at +42+k*8
00242B64  0C12D4FC  jal 0x004b53f0            ; strcmp(token, name)
00242B90  2AE2000B  slti v0, s7, 11           ; search up to 11 VARIANTS
00242BA4  94430092  lhu v1, 146(v0)           ; each variant record's id
```
```
00242884  00832818  mult a1, a0, v1           ; v1 = 440 = 11 players x 40 bytes
00242888  02221018  mult v0, s1, v0           ; v0 = idx * 40
  ; record = playbookBlk60 + 140 + variant*440 + idx*40
```
and the default (no matching variant) is
```
00242988  24020028  addiu v0, zero, 40
00242998  2442008C  addiu v0, v0, 140
0024299C  02021021  addu v0, s0, v0           ; playbookBlk60 + 140 + idx*40
```

**Finding: a shift token that the current play has no authored variant for is a
silent no-op.** Un-zeroing the Pinch weight (§2.7's proposed fix) therefore only
produces a pinch on plays whose data contains a `DL_P`/`LB_P` variant. *What a
live read (or a disc-data pass, ledger C1) settles: how many of the 11 variant
slots real defensive plays actually populate.*

### B1.7 The module also cheats on play type (corroborates §2.6)

The module's once-per-play init is `0x00145BC8` (callers `0x00160980` and
`0x0017AF74`, the latter inside the pre-snap state `enter`), latched by a byte at
`0x00600C20`. It rolls a 75% coin into `coachObj+291`, then calls the coach
struct reset `0x00145338`, which contains:

```
00145384  0C09816C  jal 0x002605b0
0014538C  0C05AD64  jal 0x0016b590            ; CPU defence only (== 0xFF)
001453A0  0C07E0BA  jal 0x001f82e8            ; <== IsRun: the AUTHORED play type
001453A4  24100032  addiu s0, zero, 50
001453A8  2403004B  addiu v1, zero, 75
001453B0  0062800B  movn s0, v1, v0           ; MOVN: run -> 75, pass -> 50
001453B4  0C0BE50A  jal 0x002f9428
001453B8  24050064  addiu a1, zero, 100
001453BC  0050102B  sltu v0, v0, s0
001453C8  A2220010  sb v0, 16(s1)             ; coachObj+16 = the rolled flag
```

**Finding: the pre-snap-adjust module reads `IsRun` (`0x001F82E8`, the authored
play type) directly, at play setup, to bias a 75%/50% coin.** This is a second,
independent instance of the §2.6 PA-omniscience defect, in a module the design
already plans to hook. Whatever `coachObj+16` gates, its input is hidden state.

### B1.8 B1 answers

* **What fires it:** a per-frame re-read of the offence's formation strength
  (`0x00145188`), edge-detected against `[coachObj+256]`. **Not** the audible
  event — there is no audible hook in the module (searched: the whole
  `0x00176000`-`0x0017F800` pre-snap module plus `0x001450C0`/`0x00145188`/
  `0x00145940`/`0x00145C48`/`0x00145D68`/`0x00145E68`; no read of an audible flag,
  no call from `0x001785F0`).
* **Why it looks like an audible mirror:** the read's inputs include the authored
  alignment of the offence's *currently selected* play, which an audible
  overwrites instantly (Hypothesis, §B1.2).
* **What it can change:** the DL shift token, the LB shift token, and the
  defensive formation flip (`playbookBlk60+23` + `0x00243518`). Alignment only.
* **What it cannot change:** the play call, the coverage shell, the coverage depth
  (`Tigh`/`Loos`) — that slot has no AI writer at all.
* **Is `0x00145940` the whole story?** No: it is the *picker*. The trigger is
  `0x00145188`, the arming/flip is `0x001450C0`, and the apply is
  `0x00145018 → 0x002437E0 → 0x002430E0 → 0x002436A0 → 0x00242E98`. There is no
  separate audible-response path.
* **The §2.4 hypothesis — CONFIRMED, and cheaper than hoped.** The formation
  checks do not need a new mechanism. `0x00145188` already loops five offensive
  players and already returns the boolean that drives the entire adjust cadence;
  a personnel read (count `+0xB04 == 3`) is a drop-in replacement or extension of
  that function. The front-commitment fix (§2.7) is a change to the weight table
  at `0x0051D450` plus the read-driven adds in `0x00145508` — subject to §B1.6's
  authored-variant ceiling. **The coverage half of the check is the part that
  needs new wiring**: the CPU has never once written token slot `[1]`, and even
  that slot only carries press depth, not a shell.

---

## B2 — defensive personnel packages

### B2.1 The position enum, calibrated (Finding)

Hand-decoded jump table at **`0x0057EC90`** (`lui v0,0x0058 / addiu v0,v0,-4976`
→ `0x0057EC90`; 21 entries, bound `sltiu v0, v1, 21` at `0x0017485C`, index = the
position code):

```
0 QB   1 HB   2 FB   3 WR   4 TE   5 LT   6 LG   7 C    8 RG   9 RT
10 LE  11 RE  12 DT  13 LOLB 14 MLB 15 ROLB 16 CB 17 FS 18 SS  19 K  20 P
```
Grouping is visible in the table itself: 10/11/12 → `0x001748D4` (D-line),
13/14/15 → `0x00174914` (linebackers), 16 → `0x00174948`, 17/18 → `0x00174970`
(safeties), 19/20 → `0x0017497C`. **31 is the "no position" sentinel.** This is
the same enum as `player+0xB04`, both sides.

*(This calibration is what makes §B3.4's jump table `0x00582570` legible: index
`player[+0xB04] - 13` = {LOLB, MLB, ROLB, CB, FS, SS}, and the man→deep-zone
bail-out fires for 17/18 = FS and SS. Exactly the safety rotation §2.6 wants.)*

### B2.2 Personnel is AUTHORED, in two layers (Finding)

**Layer 1 — the play's set rows.** The play loader `0x002BD550` runs the generic
query engine `0x004C7C78` against a 10-entry × 16-byte field-descriptor template
copied from `0x00597DA8`, whose tags are:

```
00597DAC 'PBSTSETP'  00597DBC 'PBSTDPos'  00597DCC 'PBSTEPos'  00597DDC 'PBSTSGT_'
00597DEC 'PBSTtabo'  00597DFC 'PBSTposo'  00597E0C 'PBSTartx'  00597E1C 'PBSTarty'
00597E2C 'PBSTflas'  00597E3C ff ff ff ff (terminator; 10*16 = 160 = the copy length)
```

The row is written into the slot record, 11 slots × 40 bytes at `ctx + 140`:

```
002BD7A4  93A50090  lbu a1, 144(sp)     ; PBST.tabo
002BD7C4  A0470084  sb a3, 132(v0)      ; +0x84 EPos (overridable)
002BD7C0  A0480085  sb t0, 133(v0)      ; +0x85 DPos (overridable)
002BD7CC  A0530086  sb s3, 134(v0)      ; +0x86 EPos (original)
002BD7C8  A0540087  sb s4, 135(v0)      ; +0x87 DPos (original)
002BD7D4  A045008C  sb a1, 140(v0)      ; +0x8C = the slot's POSITION
002BD7E0  244201B8  addiu v0, v0, 440   ; 11 x 40
```

The accessor is `0x00242D68(ctx, slot, &pos, &depth)`:
`+0x8C` = position, `+0x8D` = depth+1, stride 40.

**Layer 2 — an authored PACKAGE layer. This is the substitution mechanism.**
`0x0024AC70`-`0x0024B0C0` is a complete personnel-package subsystem driven by SQL
(tags stored 4-byte-reversed; unreversed in brackets):

| string addr | query | used at |
|---|---|---|
| `0x00586760` | `select '_FPS' from 'FKPS' where 'LTES' = …` → `SPF_` from `SPKF` where `SETL` | `0x0024ACB8` |
| `0x005867D8` | `select count(*) … from 'FKPS' where 'LTES' = …` | `0x0024AD54` |
| `0x00586818` | `select 'eman' … from 'FKPS' where '_FPS' = …` — **packages have names** | `0x0024ADE4` |
| `0x00586858` | `select count(*) … from 'GKPS' where '_FPS' = … and 'osop' = …` → `SPKG` by package+slot | `0x0024AEF4` |
| `0x005868A8` | `select 'soPE' …, 'soPD' … from 'PTES' where 'LTES' = … and 'osop' = …` | `0x0024AFA0` |
| `0x00597AF0` | `select 'soPD' …, 'soPE' … from 'GKPS' where '_FPS' = … and 'osop' = …` | `0x002BD070` |

Schema: **SETL** (set) → **SETP** (one row per slot: `poso`, `DPos`, `EPos`) →
**SPKF** (the packages available for that set: `SPF_` id + `eman` name) →
**SPKG** (per-package, per-slot `DPos`/`EPos` overrides).

Four playbook DBs are open at once, chosen by a conditional move:

```
0024AC24  3C033254  lui v1, 0x3254   \ ori 0x4250 -> 'PBT2'   (offence, team 2)
0024AC2C  3C023154  lui v0, 0x3154   / ori 0x4250 -> 'PBT1'
0024AC30  3C033244  lui v1, 0x3244   \ ori 0x4250 -> 'PBD2'   (DEFENCE, team 2)
0024AC34  3C023144  lui v0, 0x3144   / ori 0x4250 -> 'PBD1'
0024AC40  0045180A  movz v1, v0, a1   ; MOVZ: a1 picks the team
```

### B2.3 The one runtime variable: the active package id (Finding)

**`block + 0x24`** (where `block = *(0x00609770) + side*0xAFBC`) holds the active
package id, `-1` = base. Exactly three writers exist in the image (verified by
scanning all 83 `0xAFBC` sites for ±36 accesses):

```
0024AE40  AC820024  sw v0, 36(a0)   ; = the Nth package of this set (0x0024AC70)
0024AE54  AC640024  sw a0, 36(v1)   ; = -1
0024AE90  AC460024  sw a2, 36(v0)   ; = -1
```

and it is consumed at exactly one place that matters — the per-slot assignment
loop `0x00162618(side)`, called from play setup (`0x0021703C`):

```
001626A8  0C090B5A  jal 0x00242d68     ; sp+80 = the authored 'tabo' position
001626B8  0C092BA6  jal 0x0024ae98     ; count(*) SPKG where SPF_=<pkg> and poso=<slot>
001626C0  10400007  beq v0, zero, 0x001626e0
001626D0  0C092BCC  jal 0x0024af30     ; SETP/SPKG EPos for this slot
001626DC  A2020B04  sb v0, 2820(s0)    ;  <== PACKAGE path : player+0xB04 = EPos
001626E0  93A20050  lbu v0, 80(sp)
001626E4  A2020B04  sb v0, 2820(s0)    ;  <== BASE path    : player+0xB04 = tabo
001626EC  2A62000B  slti v0, s3, 11    ; 11 slots
```

`0x0024AF30` short-circuits to the sentinel 31 when no package is active:

```
0024AF4C  2406001F  addiu a2, zero, 31
0024AF68  A7A60000  sh a2, 0(sp)
0024AF74  8C620024  lw v0, 36(v1)      ; block+0x24
0024AF78  10440015  beq v0, a0, 0x0024afd0   ; a0 = -1 -> return the sentinel
```

**Finding: nothing in the ELF ever COMPUTES a package index.** The pending index
lives at `0x0061A020 + 24 + 2*side` and every writer stores `-1`
(`0x00278A9C`, `0x00278AAC`, `0x00278AC8`, `0x00278AD0`, `0x00278F0C`,
`0x00278F14`, `0x002790B0`, `0x00279108`) except the cycle handler, which only
increments and wraps:

```
00278DDC  0C092B44  jal 0x0024ad10     ; how many packages does this set have?
00278E18  0043200A  movz a0, v0, v1    ; MOVZ: past the last -> -1 (back to base)
```

reached only from the pre-snap command dispatcher `0x002796A0`:

| command id | handler | effect |
|---|---|---|
| `0x8000001F` | `0x00279EA0` → `0x00278E30` | cycle to the next personnel package |
| `0x80000022` | `0x00279EB0` → `0x00278F30` | set the package explicitly |
| `0x80000031` | `0x00279EC8` → `0x00278EC0` | clear the package (back to base) |

### B2.4 B2 answers

* **(a) Fixed by the play, or a substitution mechanism?** **Both, and the
  substitution mechanism exists and is complete.** The base 11 is authored in the
  play's `SETP` rows (`PBST.tabo` per `poso`); on top of that sits an authored
  **package** layer (`SPKF` / `SPKG`) that overrides individual slots' positions.
  The only runtime degree of freedom is *which package index is selected* — one
  word at `block + 0x24`.
* **(b) Could it be driven from a pre-snap WR count?** **Yes, and the wiring is
  already there; only the decision function is missing.** Three hooks, least to
  most invasive:
  1. write the pending index at `0x0061A03C`/`0x0061A03E`, or `block + 0x24`
     directly, pre-snap (`-1` = base);
  2. emit command `0x80000022` (set package) into `0x002796A0` for the defensive
     side — semantically the cleanest;
  3. cave inside `0x00162618`'s per-slot loop, right at the
     `sb v0, 2820(s0)` that writes the position.
  The WR count itself is free and is the same shape as the existing per-slot
  loops: `for i in 0..10 { p = 0x001655B0(offSide, i); if p[+0xB04] == 3 n++ }`.
* **Is a WR count used today? Searched and not found.** *Scope: all 232
  `find_field_refs(0xB04)` sites across the whole image, each disassembled ±16
  instructions and matched for a WR test (`addiu rX,zero,3` / `sltiu rX,rX,3` /
  `addiu rX,rX,-3` / `xori …,3` / `slti …,4`) combined with an accumulating
  `addiu rX,rX,1`.* Six candidate hits, none of them a count: `0x001719C4` /
  `0x00171B8C` (`pos-10 <u 3` → LE/RE/DT), `0x00171A7C` / `0x00171C2C`
  (`pos-13 <u 3` → LOLB/MLB/ROLB), `0x0018EA0C` (offensive line), `0x002D4724`
  (a commentary bucket).

### B2.5 The load-bearing unknown for B2

**Do the shipped defensive playbooks actually contain `SPKF` / `SPKG` rows?**
`docs/play-data.md` reports the create-a-playbook TDB template as having zero
rows and shipped play content as `DMF`, yet this code queries live DBs named
`PBD1` / `PBD2` at runtime, so a populated playbook DB *is* built at load.
*A live read settles it: observe `0x0024AD10(setId)` (package count) and
`0x0024AD80` (package name) for a defensive set, or dump `PBD1`.*
**If the count is 0 for every defensive set, the package layer is dead data and a
WR-driven nickel needs new authored rows, not just a poke.** Note the corroborating
static signal: **every hard-coded defensive lineup in the ELF is a 4-3** — the
practice/mini-camp drill tables (`0x00176820` → `0x00522D90`, 12-byte records;
lists at `0x00521ED0`, `0x00521F38`, `0x00521FA0`, `0x00522008`) contain no nickel
and no dime.

Secondary open items, all live reads: confirm `s6 == block+4` in `0x002BD554` so
that `lw a1, 32(s6)` really is `block+0x24`; whether `PBST` is per-play or
per-set (the `where` clause is printf-composed at runtime, invisible statically —
this decides whether an edit lands on one play or on a whole formation); and
whether the disc script ever emits `0x8000001F` / `0x80000022` (trap
`0x00278E30` / `0x00278F30` during CPU defence).

### B2.6 Two corrections to the starting leads

1. **The `Cover 4 / Dime / Robber / Safe / 3-4 / Cover 2 / Nickel / Under 4 /
   4-3` table at `0x00600F00` is not personnel logic.** It has no `lui`/`addiu`
   reference because it is reached through a pointer table at **`0x00520A10`** —
   108 words = 36 rows × 3 columns of `(formationName, playName)` pairs, e.g.
   `0x00520A10 → ('4-3','Under 4')`, `0x00520A20 → ('Nickel','Cover 2')`,
   `0x00520BD0 → ('Dime','Double Wide')`; play names at
   `0x0057E890`-`0x0057E950`. Sole reference `0x0017337C`, indexed by a hash of
   team-stat getters. **Hypothesis: a "suggested defensive call" hint display.**
   It does establish that Nickel / Dime / 3-4 / 4-3 are *formation* names paired
   with play names.
2. **There is no per-team default-formation struct at `0x006016A0`.** The raw
   dump of `0x00601698`-`0x00601758` shows linker-packed literals with no regular
   stride (`'Chiefs'`, `'Nickel'`, `0x42`, `'Rams'`, `'Bengals'`, `'4-3'`, …).
   **Hypothesis: leftover debug/HUD test strings.** A team's base front is not
   stored there.

---

## B3 — the coverage-rotation primitive

### B3.1 The state machine, precisely (Finding)

Player AI is a table-driven state machine. The table is `0x00527238` (stride 24,
115 rows, columns `enter / can_leave / ai_think / user_think / exit / extra`;
`docs/state-dispatch-table.md`). It is registered as a data word:

```
006012D4  00527238        ; the only 4-byte-aligned occurrence in the image
```

Per player, `[player+0x2FC]` is an array of 4-byte entries. **Entry 0 is the
CURRENT state** — `{stateId, param, flags, aux}` — and entries 1..N-1 are a
**queue**. Two primitives operate on it:

```
; 0x001AFF48 -- APPEND to the queue (finds the first entry whose byte0 == 0)
001AFF48  90A20000  lbu v0, 0(a1)
001AFFBC  88C20003  lwl v0, 3(a2)
001AFFC4  A8620003  swl v0, 3(v1)             ; copy the 4-byte message into the slot
```
```
; 0x001B00A0 -- SET the current state now
001B00D0  92030000  lbu v1, 0(s0)             ; the current state id
001B00DC  00641818  mult v1, v1, a0           ; a0 = 24  -> table row
001B00E0  8C450000  lw a1, 0(v0)
001B00E4  8CA20004  lw v0, 4(a1)              ; the table base
001B00EC  8C620004  lw v0, 4(v1)              ; column 1 = can_leave
001B00F0  0040F809  jalr v0
001B00FC  14430010  bne v0, v1, 0x001b0140    ; v1 = 1: refuses unless can_leave says 1
001B0104  8A620003  lwl v0, 3(s3)
001B010C  AA020003  swl v0, 3(s0)             ; overwrite entry 0
001B0138  0806BDB8  j 0x001af6e0              ; run exit/enter
```

**Finding: every coverage state can be left at any instant.** States 22, 37, 38,
39 and 40 all use `0x001B0520` as their `can_leave`, which is:

```
001B0520  03E00008  jr ra
001B0524  24020001  addiu v0, zero, 1         ; always 1
```

(For contrast, `0x001B0518` and `0x001B0528` are the two always-0 stubs.)

### B3.1a The assignment record — three authored bytes (Finding)

The 4-byte entry copied into `[player+0x2FC]` is not just a state id. Its three
live bytes are, together, the defender's entire coverage assignment, and they are
written **atomically** by the one unaligned copy pair inside the setter family
(`swl/swr` at `0x001AFC38`, `0x001AFD68`, `0x001AFEB4`, `0x001AFFC4`,
`0x001B010C`, `0x001B01DC`, `0x001B0298` — a complete `swl` scan of `.text`,
527 sites, isolates exactly these seven):

| byte | meaning | evidence |
|---|---|---|
| +0 bits 0-6 | AI state id | `00243A8C andi v0, v0, 0x007f` |
| +0 bit 7 | "another record follows" (chain) | `00243A7C srl s2, v0, 7` |
| +1 | **man-coverage target designator**, 1..5, 0 = none | state 22 `enter`: `001BDE84 sb s0, 1(v1)` from `001BDDE4 lbu s0, 44(v0)` (play-art table +0x2C + formation slot); mirror `001BEB14 subu v0, v0, v1` = `6 - x` |
| +1 | **deep-zone landmark id**, 0..8 (state 40 uses +1, not +2) | `001EC2BC lbu a0, 1(v0)` / `001EC2C0 sltiu v1, a0, 9`, jump table `0x00583270`, 9 entries |
| +2 | **zone id**, 0..5 (states 37, 39) or a 7-bit **angle** (state 38) | 37: `001ED648 lbu v0, 2(v1)` → `001ED654 lbu a1, 15(v0)`; 38: `001EE718 lbu a1, 2(v0)` / `001EE720 sll a1, a1, 17` |

**Finding: byte 0 of the context is never written through a directly loaded
`+0x2FC` pointer anywhere in `.text`** (scope: all 985 `lw rX,0x2FC(rY)` sites,
forward-walked to redefinition — 330 loads of +0, zero stores). It changes only
through the setter family, which means every state change is also, necessarily, a
rewrite of the man-target and zone bytes.

The `extra` column of the dispatch table is the **assignment mirror** — the
per-state left/right transform, invoked by `0x001B0530`:

```
001B0548  90C50000  lbu a1, 0(a2)            ; a2 = the authored RECORD (not the live ctx)
001B0554  00A22818  mult a1, a1, v0          ; * 24
001B0560  8CA20014  lw v0, 20(a1)            ; column 5 = "extra"
001B056C  0040F809  jalr v0
001B0570  00C0202D  daddu a0, a2, zero
```
```
22/extra 0x001BEB08 : ctx+1 = 6 - ctx+1               (0 preserved)
37/extra 0x001EDBC0 : ctx+1 9<->10 ; ctx+2 = 6 - ctx+2
38/extra 0x001EED18 : ctx+2 = -angle (7-bit) ; ctx+1 = 6 - ctx+1
39/extra 0x001EA518 : ctx+1 11<->12 ; ctx+2 = 6 - ctx+2
40/extra 0x001EC980 : ctx+1 <2 -> 1-x ; 2..4 -> 6-x ; else 13-x
```

**This is the closest thing in the engine to a shell transform — and both of its
call sites (`0x00243A9C` in the play-art installer, `0x00234BA0` in a play-art
reader) operate on a stack copy of an authored record before it is applied, never
on a live context.** Install-time only.

### B3.2 Where a coverage assignment comes from (Finding)

`0x00243980` is the **only** state-set site in the whole image whose state id is a
*variable read from data*:

```
00243A44  0C090F26  jal 0x00243c98            ; -> the player's state CHAIN
00243A60  92020000  lbu v0, 0(s0)             ; entry byte0
00243A68  8A030003  lwl v1, 3(s0)             ; copy the 4-byte entry to sp
00243A7C  000291C2  srl s2, v0, 7             ; bit 7 = "another entry follows"
00243A84  26100004  addiu s0, s0, 4
00243A8C  3042007F  andi v0, v0, 0x007f        ; <== the state id, from DATA
00243A90  A3A20000  sb v0, 0(sp)
00243AAC  16A00005  bne s5, zero, 0x00243ac4
00243AB4  0C06C028  jal 0x001b00a0            ; first entry -> SET now
00243AD0  0C06BFD2  jal 0x001aff48            ; later entries -> QUEUE
```

and the chain's address is a flat per-player table:

```
00243C98  30C2FFFF  andi v0, a2, 0xffff
00243C9C  24030028  addiu v1, zero, 40
00243CA0  00431018  mult v0, v0, v1
00243CA4  2442003F  addiu v0, v0, 63
00243CAC  00821021  addu v0, a0, v0           ; base + idx*40 + 63
```
```
00248360  3403AFBC  ori v1, zero, 0xafbc      ; the base argument
0024836C  8F844080  lw a0, 16512(gp)          ; playbook base = *(0x00609770)
00248378  24421400  addiu v0, v0, 5120        ; playbook_base[side] + 5120
```

**Finding: the assignment table (`playbook_base[side] + 5120 + 63 + idx*40`) and
the alignment-variant records (`playbookBlk+60 + 140 + variant*440 + idx*40`) are
two different structures.** The shift/coverage tokens re-resolve the *alignment*
records only (§B1.4); they never index the assignment table. **Alignment and
assignment are already decoupled in the data model.**

### B3.3 When assignments are installed — a closed set (Finding)

* `0x00243980` has exactly **one** `jal` caller: `0x00243BEC`, inside `0x00243B10`.
* `0x00243B10` has exactly **five** `jal` callers:

| call site | enclosing fn | reached from | phase |
|---|---|---|---|
| `0x0021E69C` | `0x0021E650` | `0x0021F5A0` | play start |
| `0x00220914` | `0x002208B0` | `0x00220DD4` (next to `jal 0x00145E68`, `jal 0x001F8520`) | play setup |
| `0x002222C4` | `0x00222258` | `0x002226CC` (next to `jal 0x00145E68`, `jal 0x001F8520`) | play setup |
| `0x00253200` | `0x002530B0` | `0x0017990C` in the pre-snap module | pre-snap |
| `0x00253228` | `0x002530B0` | *and* tail-`j` from the input dispatcher's cmd 83 (`0x00177810`) | pre-snap |

Each of the five sits immediately beside a play-init call (`0x001F8520`) or
inside the pre-snap module. **Scope of the search: `find_jal_targets` over the
whole image for `0x00243980` and `0x00243B10`; plus a raw `j`/`jal` opcode scan
for both targets, which added nothing.** There is no post-snap caller.

There is a **second** install path, and it matters for the design: `0x002530B0`
(the play-start function, single caller `0x0017990C`) also runs

```
00253238  jal 0x00156830     ; offence: 6 slots x 40 B from [gp-18920] = 0x00600D08
00253240  jal 0x0018f688     ; defence: 11 slots x 40 B from [gp-17816] = 0x00601158
```

`0x0018F688` walks 11 slots and calls `0x0018F4B8(player, slot*40 + base)`, which
applies record[0] via `0x001B00A0` (`0x0018F504`) and records[1..] via
`0x001AFF48` (`0x0018F520`); `0x0018F748` then wipes the block
(`memset(base+440, -1, 22)` + `memset(base, 0, 440)`). **That is the human's
pre-snap defensive hot-route / individual-assignment buffer — a second, already
wired channel that writes the same three authored bytes, consumed once at the
snap and cleared.** It is the natural place for a coach-brain to inject per-player
assignments without touching the play data.

### B3.4 What a coverage state can become at runtime — a closed set (Finding)

Census method: enumerate every call site of the two state primitives
(`0x001B00A0`: 100 sites; `0x001B0170`, its sibling: 11 sites; `0x001AFF48`:
41 sites), then for each site resolve the message buffer's byte 0 back to the
instruction that wrote it. 60 of 111 set-sites and 27 of 41 queue-sites resolve
to an immediate; the rest were read individually.

From inside a coverage state, only two transitions exist:

```
; identical shape in all five coverage ai_think handlers
001BE20C  24030006  addiu v1, zero, 6
001BE210  5443000F  bnel v0, v1, ...            ; likely
001BE218  0C098166  jal 0x00260598              ; the side WITH possession
001BE220  5602000B  bnel s0, v0, ...            ; likely -- only if MY side now has the ball
001BE22C  24020021  addiu v0, zero, 33          ; state 33 = run blocking
001BE240  0C06C028  jal 0x001b00a0
```
| from | to | site | meaning |
|---|---|---|---|
| 22 / 37 / 38 / 39 / 40 | **33** run blocking | `0x001BE240`, `0x001ED7AC`, `0x001EE860`, `0x001EA144`, `0x001EC550` | after a turnover, the defender becomes a blocker |
| 22 / 37 / 38 / 39 / 40 | **24** play the ball | `0x001BEA00`, `0x001EDA38`, `0x001EEB94`, `0x001EA400`, `0x001EC8B4` | the ball is in the air |
| 22 | **2** ball pursuit, **85** cadence jitter | `0x001BE684` / `0x001BDE4C`, `0x001BE30C` / `0x001BE604` | |
| 40 | **2** ball pursuit | `0x001EC8B4` (the 24-or-2 pair) | |
| 41 | **41** (itself) | `0x001A0D18` | re-sets its own byte3 from `0x002F93B0` (a random flavour) |
| **22** | **40 deep safety zone** | **`0x001BE708`** | **the one genuine in-play coverage change — see below** |

#### The one post-snap coverage→coverage transition (Finding)

Inside state 22's `ai_think` (`0x001BE1B0`) there is a per-position dispatch:

```
001BE594  92220B04  lbu v0, 2820(s1)          ; player+0xB04 = position
001BE598  2444FFF3  addiu a0, v0, -13
001BE59C  2C830006  sltiu v1, a0, 6
001BE5A0  10600067  beq v1, zero, 0x001be740
001BE5AC  24422570  addiu v0, v0, 9584        ; +0x2570 POSITIVE -> table 0x00582570
001BE5B4  8C640000  lw a0, 0(v1)
001BE5B8  00800008  jr a0
```

Hand-decoded jump table **`0x00582570`**, stride 4, **6 entries**, index =
`player[+0xB04] - 13`, bound-checked by the `sltiu`:
`13→0x001BE614  14→0x001BE5C0  15→0x001BE614  16→0x001BE694  17→0x001BE6A8  18→0x001BE6A8`.

Positions 17 and 18 only:

```
001BE6A8  0220202D  daddu a0, s1, zero
001BE6AC  0C06F142  jal 0x001bc508            ; is there still a man worth covering?
001BE6B0  24050001  addiu a1, zero, 1
001BE6B4  54400018  bnel v0, zero, 0x001be718   ; LIKELY: yes -> stay in man
001BE6B8  0040902D  daddu s2, v0, zero
001BE6BC  0C07013C  jal 0x001c04f0
001BE6C4  8E2302FC  lw v1, 764(s1)
001BE6C8  24040028  addiu a0, zero, 40        ; <== state 40, deep safety zone
001BE6CC  44800800  mtc1 zero, f1
001BE6D0  A0620001  sb v0, 1(v1)              ; rewrite the LIVE ctx man-target in place
001BE6D8  C6200190  lwc1 f0, 400(s1)
001BE6DC  46000836  c.le.s f1, f0             ; which half of the field
001BE6E4  45000004  bc1f 0x001be6f8
001BE6E8  A3A40000  sb a0, 0(sp)              ; plain bc1f -> the delay slot ALWAYS runs
001BE6EC  24020001  addiu v0, zero, 1
001BE6F4  A3A20001  sb v0, 1(sp)              ; landmark 1  (Y >= 0)
001BE6F8  A3A00001  sb zero, 1(sp)            ; landmark 0  (Y <  0)
001BE708  0C06BED4  jal 0x001afb50            ; push state 40
```

**Correction to my own §B3.4 draft: a coverage→coverage transition DOES exist.**
Positions 17 and 18 are, by the §B2.1 enum, **FS and SS** — so what this code
does is: *a safety in man coverage who finds no man worth covering converts,
mid-play, to a deep zone on his half of the field.* It is a man-to-zone bail-out,
not a disguise. But it is the engine's own proof that entering a coverage state
after the snap is mechanically sound and behaviourally sane — **and it happens to
be on exactly the two positions §2.6's late safety rotation targets.**

The only other post-snap entry into a coverage state is `0x0019EEF8` inside
`0x0019EDF8` (reached from `0x0019F008` = state 2's `ai_think`, ball pursuit),
which sets state **22** with the target byte from `0x001C04F0(player)`.

**Man defenders also re-target continuously.** Eight in-play sites under state
22's `ai_think` write the live ctx+1: `0x001BE478`, `0x001BE5EC`, `0x001BE674`,
`0x001BE6D0`, `0x001BE738`, plus `0x001BC6D0` / `0x001BC6E4` (fn `0x001BC508`,
callers all in 22/ai_think) and `0x001B96C0` / `0x001B96CC` (fn `0x001B9430`,
reached only via `0x001B9720` ← `0x001BE800`). All write `0x001C04F0`'s
designator. **A man hand-off primitive is live every AI tick.**

**Zone defenders never re-derive their zone.** States 37/38/39/40 contain zero
writes to any ctx byte; every `lw a1, 764(sX)` inside their `ai_think`s
(`0x001EC548`, `0x001EC824`, `0x001EC8A4`, `0x001EA13C`, `0x001EA3B8`,
`0x001EA3EC`, `0x001ED7A4`, `0x001ED9F0`, `0x001EDA24`, `0x001EE858`,
`0x001EEB3C`, `0x001EEB80`) is a setter argument, never a field access.

The remaining literal-38 sites, `0x00220888` and `0x00222230`, are play-setup
code that picks a defender by `player+0xB04` ∈ {17,18} and hands him a hook zone
with param 32; both enclosing functions clear the whole state stack first
(`0x00220998 jal 0x001af830`) and then run `0x00243B10`. Pre-snap.

### B3.5 What the tokens can and cannot do to coverage (Finding)

The one coverage-shaped lever the engine exposes pre-snap is token slot `[1]`,
and its whole vocabulary is:

```
00178DD0  3C040060 / 24840F78  ->  0x00600F78 "Tigh"   (s0 = 2)
00178DE8  3C040060 / 24840F88  ->  0x00600F88 "Norm"   (s0 = 0)
00178E34  3C040060 / 24840F80  ->  0x00600F80 "Loos"   (s0 = 1)
```
(jump table at `0x0057F220`, 11 entries, index = `a1 - 118`, inside `0x00178D60`.)

**Finding: "Coverage Audible" is press depth — tight / loose / normal. It is not
a shell change, and no CPU code path writes it.**

### B3.6 VERDICT — does a coverage-rotation primitive exist?

**Finding — NO SHELL-LEVEL rotation or disguise exists. But the answer is not the
feared "it would need a whole new mechanism": every piece is already present and
one of them already runs post-snap.**

**What does NOT exist** (three closed-set results, scopes stated):

1. **No coverage SHELL is ever re-chosen after the snap.** The whole shell — the
   state id plus the man-target and zone bytes — is three authored bytes of a
   4-byte play-art record (`0x00243C98`: `base + slot*40 + 63`), copied verbatim
   by `0x00243980`, reachable only through `0x00243B10`, whose complete caller
   set (5 sites, §B3.3) is play-setup and pre-snap. *Scope: whole-image `jal`/`j`
   scan for both addresses.*
2. **No defender ever enters state 37, 38 or 39 post-snap.** *Scope: complete
   `.text` (`0x00100000`-`0x0050A9FC`, 1,059,199 instructions) scan for every
   materialisation of 22/37/38/39/40 (842 sites); only 4 reach a request byte 0,
   and the two `38` sites are both play-setup. Cross-checked against the complete
   152-site census of `0x001B00A0` / `0x001B0170` / `0x001AFF48` with byte 0
   resolved per site.*
3. **The pre-snap lever is press depth, not shell.** Token slot `[1]`'s entire
   vocabulary is Tigh/Loos/Norm, and no CPU path ever writes it (§B3.5).

**What DOES exist — and this is the load-bearing half of the verdict:**

* **A post-snap coverage-state change already ships.** `0x001BE708`: a safety
  (position 17/18) in man coverage with no man worth covering converts, mid-play,
  to state 40 with a side-chosen deep landmark (§B3.4). *The behaviour the
  disguise feature needs — enter a coverage state after the snap and have the
  defender play it correctly — is demonstrated by the engine itself.*
* **A man hand-off primitive runs every AI tick** — 8 in-play writers of the live
  ctx man-target under state 22's `ai_think` (§B3.4).
* **Every coverage state's `can_leave` is the always-1 stub `0x001B0520`**
  (§B3.1) — nothing has to be fought to pull a defender out.
* **The state id is already data-driven** (`andi v0, v0, 0x007F` at `0x00243A8C`)
  and the record format already supports multi-stage chains via bit 7. A
  two-stage assignment is the engine's own idiom.
* **Alignment and assignment live in different tables** (§B3.2), so "show one
  look, play another" needs no data-model change.
* **A second, already-wired install channel exists**: the human's pre-snap
  defensive adjustment block (`0x0018F688` / `0x0018F4B8`, 11 slots × 40 B at
  `*(0x00601158)`, applied at the snap and wiped), which writes the same three
  bytes without touching play data (§B3.3).

**Implication for §2.6 (disguise / late safety rotation): FEASIBLE, and it is a
new call site rather than a new mechanism.** The cheapest shape: choose shell A's
alignment pre-snap through the existing token path, then at or just after the
snap push shell B's `{state, target/landmark, zone}` triple onto the rotating
defenders with `0x001AFB50` / `0x001B00A0` — exactly what `0x001BE708` already
does for one case. The remaining risk is behavioural, not structural.

**Ceiling this sets on the feature:** the *disguise* is limited to what a
per-defender triple can express. There is no "call a different coverage" object —
only per-player state + target/zone bytes — so a rotation must be composed
defender-by-defender, and the gate `0x001B0530` + the `extra` column (the only
shell-level transform in the binary) is install-time only and would have to be
re-invoked deliberately for a mirrored rotation.

### B3.7 What remains unproven for B3

* **Whether a coverage state's `enter` behaves sanely when entered mid-play** for
  states other than 40. `0x001BE708` proves 40 does. The other four `enter`
  handlers (`0x001BDD88`, `0x001ED5F8`, `0x001EE6B8`, `0x001EA020`) have not been
  audited for pre-snap assumptions. Note that 37 and 39 derive their drop
  geometry at `enter` (`0x001ED6E0 lwc1 f0, 428(s2)` scaled by `gp-26332`;
  `0x001EA084` via `gp-26476`) and 40 derives depth from a rating
  (`0x001EC488 lh a0, 2932(s1)` → `((255 - rating) >> 2) + 90`), so entering them
  late will recompute depth from the *current* position, not the LOS — which is
  probably what a late rotation wants, but is worth confirming. *Static question;
  no rig needed.*
* **The authored value set.** The state ids actually stored at
  `base + slot*40 + 63` live in the disc play data, not the ELF. *A live read
  (dump that table for a known Cover 2 and a known Cover 3) enumerates them and
  confirms 22/37/38/39/40 appear with the expected target/zone bytes.*
* **Whether the alignment apply path is safe post-snap.** `0x00242E98` early-outs
  when the player is already in state 42 (`beq v0, v1`, `v1 = 42`, at
  `0x00242ED0`) and queues "walk to the spot" states 6/7 — both pre-snap-shaped.
  A late rotation should push the assignment triple directly and leave
  `0x00242E98` alone.

---

## Corrections to prior documents

1. **`docs/ai-coach-investigation-playcaller.md` Q1** — "iterates a 10-byte
   descriptor table at `0x0052D450`". The table is at **`0x0051D450`**
   (`addiu` sign-extension); the string `CUSTOMAI.C` sits at `0x0051D440`,
   immediately above it.
2. **Same doc, and `ai-coach-playcalling-requirements.md` §2.7** — "option
   indices 2/3/4 with float multipliers 1.0 / 0.5 / 0.0" / "FIXED weights that
   NEVER pinch or spread (the 0.0 weights)". Those are the first three of **27**
   accumulator calls. The correct statement: options are
   {0 Norm, 1 Pinch, 2 Left, 3 Right, 4 Spread}; base weights are
   LB {500, 0, 200, 200, 100} and DL {500, 0, 250, 250, 0}; the 27 adds hit
   indices 0/2/3/4 only (9/7/9/2 times) and are `ptrk`-driven, not fixed.
   **Pinch is the option that can never be selected**; DL Spread starts at 0 but
   is reachable through two add sites. The weights are therefore *partly*
   read-driven already — the defect is narrower and more specific than "fixed".
3. **`docs/ai-coach-playcalling-requirements.md` §2.4** — the mechanism
   hypothesis ("the check likely rides the engine's EXISTING defensive-audible
   path") is confirmed for the FRONT and refuted for COVERAGE: the front path is
   shared with the human's D-pad, but there is no defensive-audible path for the
   coverage shell at all, and the trigger was never the audible event.

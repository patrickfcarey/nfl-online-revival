# Coach hunt — B4 (gap fits) and B5 (pass target)

Static RE, 2026-08-14, against `extract/SLUS_207.52` (SLUS-20752, CRC
`0x14F8B841`). vaddr = file_offset + `0xFF000`, gp = `0x006056F0`. Answers
ledger items **B4** and **B5** of `ai-coach-playcalling-requirements.md` §7,
plus a re-verification of the `ptrk` recorder's `@8/@12/@13/@14/@15` stores
(§4.1) so the Phase-2 schema builds on re-derived ground.

Rule 4 applies throughout: every load-bearing claim is pinned to an address
with quoted disassembly, re-derived from the image in this session. Prior docs
are cited only where they independently corroborate.

**Hazard flags used below:** `movn`/`movz` (conditional moves — easy to
mis-read as unconditional), branch-likely (`bnel`/`beql`/`bc1tl`/`bc1fl` —
the delay slot executes **only when taken**), and hand-decoded jump tables.
`recon.mipsdis` prints SLLV/SRLV/SRAV with rs/rt swapped
(`docs/pass-vs-run-blocking.md`); no such instruction is load-bearing here.

---

## 0. Two corrections, up front

### 0.1 The recorder's entry is `0x001488D8`, not `0x00148900`

**Finding.** `0x00148900` is mid-prologue and has **zero `jal` callers**
(whole-image `jal` scan). The real entry is `0x001488D8`, with **exactly one
caller**, `0x0014A11C`:

```
001488D8  27BDFF20  addiu sp, sp, -224      <- real entry
001488DC  FFB20060  sd s2, 96(sp)
001488E4  0080902D  daddu s2, a0, zero      ; a0 = out-summary struct
001488EC  00A0882D  daddu s1, a1, zero      ; a1 = 28-byte play-result struct
...
00148900  0000A02D  daddu s4, zero, zero    <- the published address
...
00148FA0  DFBF00C0  ld ra, 192(sp)
00148FC4  27BD00E0  addiu sp, sp, 224
```

Same hazard class as the two mid-prologue addresses caught in
`docs/catch-and-fumble.md`. Searching for callers of `0x00148900` returns zero
and would have looked like "the recorder is never called".

### 0.2 `0x001F82E8` is **IsPass**, not IsRun

**Finding.** `0x001F82E8` tail-calls `0x00243F58` on the current play record:

```
001F82E8  27BDFFF0  addiu sp, sp, -16
001F82F0  0C098166  jal 0x00260598          ; current side
001F82F8  0C0920D8  jal 0x00248360          ; -> play record for that side
001F8300  0C090FD6  jal 0x00243f58
```
```
00243F58  8C840014  lw a0, 20(a0)           ; playType = play[0x14]
00243F5C  2C830007  sltiu v1, a0, 7
00243F60  10600002  beq v1, zero, 0x00243f6c
00243F64  0000102D  daddu v0, zero, zero
00243F68  0004102B  sltu v0, zero, a0
```
→ true iff `1 <= playType <= 6`.

Its sibling `0x001F82B8` (103 callers, including two inside the state-2
pursuit chain at `0x0019B860`/`0x0019B88C`) tail-calls `0x00243F08`, true for
`playType ∈ [11,18] ∪ {37, 41}`.

Three independent lines say **[1,6] = pass**:

1. The `@8` direction encoder branches on `@15`, and the branch taken when
   `@15 == 2` (the value the recorder writes when `0x001F82E8` is true) is the
   one that buckets **downfield depth at 7.0 and 15.0 yards** — short /
   intermediate / deep. That is a pass concept (§3.2 below).
2. `0x00148EF4`–`0x00148F20`: on an authored `0x001F82E8`-true play where **no
   throw event occurred**, the recorder *re-writes* `@15` to 1. "Authored pass,
   nobody threw" → record it as a run (a scramble). Coherent only if
   `0x001F82E8` = IsPass.
3. `docs/pass-rush.md` reached the same split independently
   ("pass = playType ∈ [1,6], run = [11,18] ∪ {37,41}") from the block-shed
   animation tables.

So: **`@15 == 2` ⇒ PASS, `@15 == 1` ⇒ RUN.** `ai-coach-playcalling-requirements.md`
§4.1/§4.3 and `ai-coach-investigation-ptrk.md` name `0x001F82E8` "IsRun"; the
*address* is right, the *sense is inverted*. Anything built on "IsRun true →
@15=2" would have recorded every pass as a run.

---

## 1. B4 — gap assignments / run fits

### Verdict

**Finding: run fits are EMERGENT, not modelled.** The engine has no
per-defender gap/lane/responsibility for run defense. There is exactly one
authored-play-derived directional assignment on the run-pursuit path — a
binary **edge-contain flag for the two defensive-end position slots** — and it
is derived from the ball-carrier's *authored* heading, not from the offensive
line's alignment, and is not a gap.

§2.7's "know your run fits" is therefore **build-from-scratch**, not a tunable.

### 1.1 What an authored per-defender assignment actually is (re-derived)

The play file gives each player a chain of **4-byte steps**
`{id|0x80, p1, p2, p3}` at `*(player + 0x2FC)`. The chain is searched by step
id with `0x001B02C8`:

```
001B02C8  38E2FFFF  xori v0, a3, 0xffff
001B02D0  0002380A  movz a3, zero, v0        ; *** movz: a3=0xFFFF means "start at 0"
001B02D4  00071080  sll v0, a3, 2            ; 4-byte stride
001B02D8  00451021  addu v0, v0, a1
001B02DC  90430000  lbu v1, 0(v0)            ; step id
001B02E0  306A007F  andi t2, v1, 0x007f      ; id & 0x7F
001B02E4  15400003  bne t2, zero, 0x001b02f4
001B02EC  03E00008  jr ra
001B02F0  3402FFFF  ori v0, zero, 0xffff     ; not found
```

A whole-image `jal` census of `0x001B02C8` returns **105 call sites**; the step
ids they look up are exactly:
`1, 3, 4, 6, 9, 11, 12, 15, 16, 17, 18, 19, 20, 21, 22, 26, 27, 30, 31, 33, 40,
41, 47, 52, 61, 73, 87, 90, 91, 92` (three sites compute the id in a register).
**None of these is a gap.** They are step/phase identities (route legs, block
targets, pitch phases, coverage phases).

For comparison, the one place a defender *does* get an authored geometric
assignment — state 30's enter — is a rush lane, re-derived here:

```
001CB5BC  8C8502FC  lw a1, 764(a0)           ; the step record
001CB5C4  90A20003  lbu v0, 3(a1)            ; p3
001CB5C8  54400006  bnel v0, zero, 0x001cb5e4 ; *** branch-likely
001CB5CC  90A20001  lbu v0, 1(a1)            ;   (delay, taken-only) p1
001CB5FC  46010002  mul.s f0, f0, f1         ; p1 * 0.125  (at=0x3E000000)
001CB600  E6000008  swc1 f0, 8(s0)           ; -> state[+8]  = distance
001CB604  90A20002  lbu v0, 2(a1)            ; p2
001CB608  00021440  sll v0, v0, 17           ; -> BAM bearing
001CB610  AE02000C  sw v0, 12(s0)            ; -> state[+12] = bearing
```

That is (angle, distance) frozen at the snap — corroborates
`docs/pass-rush.md`'s "gap control does not exist" for the rush, now
re-derived. **Three bytes of authored parameter, whose meaning is fixed per
state, cannot carry a gap scheme.**

### 1.2 The ball-pursuit state takes NO authored parameters at all

**Finding (closed set).** State 2 is "ball pursuit" (`docs/state-dispatch-table.md`,
row 2: enter `0x0019EF30`, can_leave `0x0019FCC0`, ai_think `0x0019F008`,
user_think `0x0014EB18`, exit `0x0019FCC8`).

> Note: the brief's "state-2 pursuit think fn `0x0019B338`" is a *callee*, not
> the think. `0x0019B338` has exactly one caller, `0x0019FC10`, inside the real
> think `0x0019F008`.

The enter is a **complete 54-instruction function** (`0x0019EF30`–`0x0019F004`).
An exhaustive load/store census of it yields these bases and offsets, and
nothing else:

```
a2 (= self+336, the state block): +4 +8 +12 +16 +17 +18 +19 +20 +21 +22
s0 (= self):                      +336 +424 +428 +2932 +2960
gp:                               -29296 -29292   (two float constants)
```

**`+0x2FC` (764) does not appear once.** The enter reads only the defender's
own facing (`+0x1A8`=424), a float at `+0x1AC`=428, and two ratings
(`+0xB74`=2932 AWR, `+0xB90`=2960), and turns them into a reaction-delay timer:

```
0019EF90  86020B74  lh v0, 2932(s0)          ; AWR
0019EF94  86030B90  lh v1, 2960(s0)
0019EF98  00621821  addu v1, v1, v0
0019EF9C  0103402A  slt t0, t0, v1           ; t0 = -1
0019EFA0  2462001F  addiu v0, v1, 31
0019EFA4  0068100B  movn v0, v1, t0          ; *** movn (round-toward-zero idiom)
0019EFA8  00021143  sra v0, v0, 5
0019EFAC  01224823  subu t1, t1, v0          ; t1 = 21 - (AWR+r16)/32
0019EFB0  A0C90011  sb t1, 17(a2)            ; state[+17] = reaction frames
```

No gap, no landmark, no responsibility, no reference to any other player.

### 1.3 The one run-fit-like mechanism: DE edge contain (and it cheats)

**Finding.** The state-2 enter's only callee that touches authored data is
`0x0019ECA8` (exactly one caller: `0x0019EFDC`). It:

```
0019ECC4  92420B04  lbu v0, 2820(s2)         ; SELF position byte (+0xB04)
0019ECC8  2442FFF6  addiu v0, v0, -10
0019ECCC  2C420002  sltiu v0, v0, 2          ; gate: position in {10, 11}
0019ECD0  10400042  beq v0, zero, 0x0019eddc
...
0019ED18  0C090FEA  jal 0x00243fa8           ; the AUTHORED ball carrier
0019ED2C  92020B04  lbu v0, 2820(s0)
0019ED30  2442FFFF  addiu v0, v0, -1
0019ED34  2C420002  sltiu v0, v0, 2          ; carrier must be position 1 or 2 (HB/FB)
0019ED40  8E0502FC  lw a1, 764(s0)           ; the CARRIER's step chain
0019ED44  24060013  addiu a2, zero, 19       ; find step id 19
0019ED48  0C06C0B2  jal 0x001b02c8
0019ED60  8E0502FC  lw a1, 764(s0)
0019ED68  2406005B  addiu a2, zero, 91       ; else step id 91
0019ED6C  0C06C0B2  jal 0x001b02c8
0019ED80  8E0202FC  lw v0, 764(s0)
0019ED84  00031880  sll v1, v1, 2
0019ED90  00621821  addu v1, v1, v0          ; -> that step record
0019ED98  90620002  lbu v0, 2(v1)            ; p2 = the carrier's authored heading
0019ED9C  3C050080  lui a1, 0x0080           ; 0x00800000 = 180 deg
0019EDA0  00021440  sll v0, v0, 17           ; p2 -> BAM (0x01000000 = 360 deg)
0019EDA4  00461024  and v0, v0, a2           ; & 0x00FFFFFF
0019EDA8  00822023  subu a0, a0, v0          ; a0 = 0x00400000 (90 deg) - heading
0019EDAC  00862024  and a0, a0, a2
0019EDB0  00A4282A  slt a1, a1, a0           ; > 180 deg ?
0019EDB4  10A00003  beq a1, zero, 0x0019edc4
0019EDB8  2402000B  addiu v0, zero, 11       ;   yes -> expect position 11
0019EDC8  2402000A  addiu v0, zero, 10       ;   no  -> expect position 10
0019EDCC  14620004  bne v1, v0, 0x0019ede0   ; is that ME?
0019EDD8  A2620016  sb v0, 22(s3)            ; state[+22] = 1   (the contain flag)
```

So: *"if I am the defensive end on the side the authored runner's authored
heading points to, set a flag."* The flag is read **exactly once** in the
think:

```
0019F078  92620016  lbu v0, 22(s3)
0019F07C  10400008  beq v0, zero, 0x0019f0a0   ; 0 -> ordinary pursuit
0019F084  0C067B7E  jal 0x0019edf8             ; contain routine
```

And `0x0019EDF8` (one caller) turns out to be **option/pitch containment**, not
gap fitting:

```
0019EE1C  0C090FEA  jal 0x00243fa8
0019EE20  A2000016  sb zero, 22(s0)          ; one-shot: clear the flag
0019EE5C  8E030014  lw v1, 20(s0)            ; play TYPE
0019EE64  2C620023  sltiu v0, v1, 35
0019EE6C  2C620021  sltiu v0, v1, 33         ; threshold 2 for types {31,33,34}
0019EE8C  86430B74  lh v1, 2932(s2)          ; self AWR
0019EE9C  00641818  mult v1, v1, a0          ; a0 = 75
0019EEA8  0062001A  div v1, v0               ; v0 = 255  -> AWR*75/255  (max ~29)
0019EEB8  0C0BE50A  jal 0x002f9428           ; RandInt(0, 100)
0019EEBC  24050064  addiu a1, zero, 100
0019EEC0  0050102B  sltu v0, v0, s0          ; roll < AWR*75/255 ?
0019EEC8  24020016  addiu v0, zero, 22       ; push step id 22 onto MY OWN chain
0019EEF8  0C06C028  jal 0x001b00a0
```

Properties worth stating plainly:

* **Binary and slot-based** — LE vs RE by position byte, nothing else.
* **Omniscient** — the trigger is `0x00243FA8` (the play file's *designated*
  carrier) and that carrier's *authored* heading byte. The DE does not read the
  backfield; it reads the play call. Same anti-omniscience violation §2.6
  catalogues for PA.
* **Probabilistic on AWR only** — max ~29% at AWR 99, and gated to play types
  {31,33,34} (option/pitch family, per the threshold split).
* **Not a gap** — no A/B/C identity, no reference to any offensive lineman, no
  re-fit.

### 1.4 Census scope (what the negative covers)

The negative is closed over these sets, all re-derived this session:

1. **State-2 enter** — complete function, 54 instructions, exhaustive
   load/store census: zero `+0x2FC` accesses (§1.2).
2. **State-2 think + depth-3 call closure** — 349 functions (281 in the
   gameplay range `< 0x00300000`). Census of `+0x2FC` accesses: 103 sites, all
   of them either the ai-state byte `[0]`, a step id, or `p1` used as an
   *identity* (e.g. `0x0019B58C` compares another player's step id against 53
   and 55; `0x0019BB64` compares another player's `p1`). No site converts a
   p-byte into a lateral landmark on this path.
3. **Field-offset census of the think body** (`0x0019F008`–`0x0019FCC0`): the
   only *other-player* base is a single pointer (`fp`) read at `+400/+404`
   (position) and `+440/+444/+448`. **No pair of players, no line iteration, no
   midpoint.** A gap fit requires referencing two adjacent blockers; nothing on
   this path does.
4. **The 115-row state dispatch table** (`0x00527238`, stride 24) — a linear
   scan of every enter for `lw ?,764(..)` followed by `lbu ?,{0,1,2,3}`. State 2
   appears in **neither** list (it consumes no authored bytes); state 42
   (defensive pre-snap, `0x001A5250`) likewise reads none. *Caveat, stated
   honestly:* this scan is a **lower bound** — an enter that hands `+0x2FC` to a
   callee (form 3 of the `find_field_refs` hazard) would be missed. It is not
   the basis of the verdict; §1.2's complete-function census is.
5. **Authored step-id vocabulary** — all 105 `0x001B02C8` call sites, ids
   enumerated in §1.1. None is a gap.
6. **String/tag census** — the image contains no `FIT`, `TECH` or `LANE`
   substring at all; all 73 `GAP` hits are inside DB query strings with
   reversed fourccs (`'IGAP'`, `'EGAP'`, `'GAPS'`, `'GAPC'` = `PAGI`, `PAGE`,
   `SPAG`, `CPAG` — stat/DB columns, e.g. `select 'EGAP' into ... from 'YALP'`).
   Independently corroborates `docs/pass-rush.md`'s "no fourcc anywhere encodes
   a gap or technique".

### 1.5 Consequence for §2.7

* "Ensure/measure gap integrity" has **nothing to tune**. A gap-fit layer is a
  new subsystem: assign each box defender a lateral landmark derived from the
  offensive line's live alignment, and hold it until the carrier commits.
* The **inputs exist and are cheap**: position byte `+0xB04` identifies the OL
  (5..9 under the standard enum that makes 16=CB, 17/18=FS/SS — consistent with
  `docs/zone-bunching.md`'s state-37/40 gates), live positions are `+0x190/+0x194`,
  and `0x001655B0(team, idx)` resolves any player in O(1). What is missing is
  *code that reads two of them at once*.
* The DE contain flag (`state+22`, written by `0x0019ECA8`, consumed at
  `0x0019F078`) is a **ready-made insertion point** for an edge-setting
  requirement — it is already a per-side, per-position, one-shot boolean.
  Re-deriving it from *visible* cues (formation strength, back alignment)
  instead of the authored heading would also retire one omniscience site.
* **Hypothesis (untested):** positions 10 and 11 are LE and RE. Grounded on the
  16/17/18 = CB/FS/SS anchors plus the standard enum ordering; a one-line live
  read settles it.

---

## 2. B5 — the pass-target signal

### Verdict

**Finding: the pass target is already in the ring record.** `ptrk` field `@12`
holds the intended receiver's player index on every throw of kinds {0,1,6,7}.
It is written from the play's own event log by code the recorder already runs.
No new plumbing is needed to *capture* the target — only to stop it being
overwritten and to give it a dedicated slot.

`ai-coach-investigation-ptrk.md`'s "the pass-target player id (nowhere stored)"
is **wrong**; `@12` is it.

### 2.1 The recorder walks a per-play EVENT LOG

**Finding.** The recorder's whole body is a loop over a global event list:

```
00148944  0C057968  jal 0x0015e5a0           ; event count
0014894C  0040B02D  daddu s6, v0, zero
001489F8  0C057986  jal 0x0015e618           ; event[s5]
00148A00  0040802D  daddu s0, v0, zero
00148A04  96020014  lhu v0, 20(s0)           ; event type
00148A08  2444FFFE  addiu a0, v0, -2
00148A0C  2C83003A  sltiu v1, a0, 58         ; types 2..59
00148A18  00041880  sll v1, a0, 2
00148A1C  2442C080  addiu v0, v0, -16256     ; table at 0x0057C080
00148A28  00800008  jr a0                    ; *** hand-decoded jump table
```

The list lives at `*(gp-18624)` = `*0x00600E30`, created with **capacity 50,
element size 28**:

```
0015E480  27BDFFE0  addiu sp, sp, -32
0015E484  24020032  addiu v0, zero, 50       ; capacity
0015E488  2403001C  addiu v1, zero, 28       ; element size
0015E4A0  0C0E774A  jal 0x0039dd28
0015E4AC  AF82B740  sw v0, -18624(gp)
```
Corroborated independently by `BeginEvent`'s `memset(slot, 0, 28)`
(`0015E5E4 addiu a2,zero,28`) and `CommitEvent`'s `sltiu v0, count, 50`
(`0015E4FC`).

Event record layout (as used):

| off | meaning |
|---|---|
| 0 | u32 tagged handle of the actor (`{kind, teamByte, idxByte}`) |
| 4 | event-specific u32 |
| 8 | event-specific u32 — **on the throw event, the target player POINTER** |
| 12 | f32 X |
| 16 | f32 Z (downfield) |
| 20 | u16 event type |
| 22 | u16 event-specific |
| 24 | u32 timestamp (`0015E530 sw v0, 24(s0)` from `0x0013ECA0(1)`) |

Producers: `BeginEvent 0x0015E5C8` (48 callers) / `CommitEvent 0x0015E4E8`
(46 callers) / a `{type,&floats}` convenience wrapper `0x0015E558` (3 callers).
A scan of all 48 `BeginEvent` sites for the type constant written to `+20`
gives the producer→type map used below.

### 2.2 Event type 5 is the throw, and it carries the receiver

**Finding.** Two producers emit type 5, both inside the "deliver the ball"
executor family. Producer A is `0x001C7988` (called from state-15 think
`0x001C6D9C` and state-16 think `0x001E91A8`):

```
001C79D0  0C084FD8  jal 0x00213f60           ; -> s5 = the throw-intent block
001C7A04  92A30015  lbu v1, 21(s5)           ; throwKind, 0..8
001C7A0C  10400024  beq v0, zero, 0x001c7aa0
001C7A18  00031880  sll v1, v1, 2            ; *** jump table 0x005827A0
001C7A2C  92240001  lbu a0, 1(s1)            ; the QB object's OWN team byte
001C7A30  0C05956C  jal 0x001655b0           ; GetPlayer(team, idx)
001C7A34  92A50014  lbu a1, 20(s5)           ; *** intent[+20] = TARGET INDEX
001C7A44  0040F02D  daddu fp, v0, zero       ; fp = the target player object
...
001C824C  0C057972  jal 0x0015e5c8           ; BeginEvent
001C825C  0C04EE1C  jal 0x0013b870           ; event@0 = the QB's handle
001C8264  C6200190  lwc1 f0, 400(s1)         ; event@12/@16 = QB position
001C8274  92A20015  lbu v0, 21(s5)
001C8278  AE020004  sw v0, 4(s0)             ; event@4 = throwKind
001C8294  00621821  addu v1, v1, v0          ; *** jump table 0x00582800
001C82A4  10000002  beq zero, zero, 0x001c82b0
001C82A8  AE1E0008  sw fp, 8(s0)             ; *** event@8 = TARGET POINTER
001C82AC  AE000008  sw zero, 8(s0)           ;     (kinds 2,3,4,5,8: none)
001C82B8  24020005  addiu v0, zero, 5
001C82BC  A6020014  sh v0, 20(s0)            ; event type = 5
001C82CC  0C05793A  jal 0x0015e4e8           ; CommitEvent
```

Jump table `0x00582800` decoded by hand: kinds **0, 1, 6, 7 → store the target
pointer**; kinds 2, 3, 4, 5, 8 → store zero.

Producer B is `0x001C8330` (callers `0x001E9200`, `0x0025755C`) — structurally
identical, table `0x00582830`, **same kind split**:

```
001C83A0  92620015  lbu v0, 21(s3)
001C83D0  92240001  lbu a0, 1(s1)
001C83D4  0C05956C  jal 0x001655b0
001C83D8  92650014  lbu a1, 20(s3)           ; intent[+20] again
001C83E4  AE140008  sw s4, 8(s0)             ; event@8 = target
001C83E8  AE000008  sw zero, 8(s0)
001C83EC  24020005  addiu v0, zero, 5
```

Two independent producers agreeing is the strongest form this evidence takes.

**Hypothesis:** kinds {0,1,6,7} are the *throwing* kinds and {2,3,4,5,8} are
handoff / pitch / spike / throwaway — state 15 is named "deliver the ball
(pass, handoff, pitch)" in `docs/state-dispatch-table.md`, and only a thrown
ball has a downfield target. Not proven; a live read of `intent[+21]` on a
known play settles it.

### 2.3 The throw-intent block

**Finding.** `0x00213F60` is the accessor (30 callers, incl. `0x00148538` in
the recorder's *own* module, plus `0x00257A04`/`0x00257B10` in the catch
module). It resolves through:

```
00213DD8  8F82BC78  lw v0, -17288(gp)        ; *0x00601368
00213DDC  03E00008  jr ra
00213DE0  0004100B  movn v0, zero, a0        ; *** movn: a0!=0 -> NULL
```
The gp slot `0x00601368` has exactly **two** references in the whole image
(`0x00213D8C` writes it at construction, `0x00213DD8` reads it) — a closed set.

Fields established by use:

| off | meaning | evidence |
|---|---|---|
| +0x00 | flag byte (bits 0/1, mask 0xF2) | `00213F88`–`00213FA4` |
| +0x04 / +0x08 / +0x0C | f32 throw params | `001C7A74`, `001C7ABC`, `001C7AE4` |
| +0x10.. | a vector passed as `a2` | `001C7A48 addiu s0, s5, 16` |
| +0x12 | i16 | `001C7A7C lh a3, 18(s5)` |
| **+0x14 (20)** | **u8 target receiver index** | `001C7A34`, `001C83D8` |
| **+0x15 (21)** | **u8 throw kind (0..8)** | `001C7A04`, `001C83A0`, table gates |
| +0x16 (22) | u8, cleared after the throw | `001C82F0 sb zero, 22(s5)` |

A per-side reset zeroes +20/+21/+22/+23 (`0017B0B4`–`0017B0C0`) on a 28-byte
record at `*(gp-18316) + 260 + i*28`. **Hypothesis:** that record *is* this
block (identical field layout and size); a live read of both pointers settles
it. Either way, `0x00213F60` is the sanctioned accessor.

### 2.4 A player object's first word IS its handle — so `+2` is its index

**Finding.** `0x0013B870` copies an object's word `0` into an event slot:

```
0013B870  10800004  beq a0, zero, 0x0013b884
0013B878  8C820000  lw v0, 0(a0)             ; the object's OWN word 0
0013B880  ACA20000  sw v0, 0(a1)             ; = its handle
0013B888  ACA00000  sw zero, 0(a1)
```

`0x0013B798` decodes such a handle (`kind = byte0`, jump table `0x0057B680`,
9 entries hand-decoded); kind 1 resolves through `0x001655B0`:

```
001655B0  8F86B758  lw a2, -18600(gp)        ; roster array
001655C4  94C20008  lhu v0, 8(a2)            ; per-team count
001655C8  240314C0  addiu v1, zero, 5312     ; *** 5312-byte stride = a PLAYER
001655D0  00E23018  mult a2, a3, v0          ; a3 = teamByte
001655D4  00C51021  addu v0, a2, a1          ; a1 = idxByte
001655D8  00431018  mult v0, v0, v1
```

Decisive corroboration that byte1=team, byte2=index — an object re-resolving
*itself* from its own header bytes:

```
0015C78C  92040001  lbu a0, 1(s0)            ; team
0015C790  0C05956C  jal 0x001655b0
0015C794  92050002  lbu a1, 2(s0)            ; index
```
(`0x001C7A2C` uses the same `lbu 1(QB)` for the team when resolving the
target.) A whole-image scan for `lbu ?,1(rs)` followed within 3 instructions by
`lbu ?,2(rs)` on the same base returns 139 sites; the pattern is universal.

**Therefore `lbu 2(playerObject)` = that player's roster index within his team.**

### 2.5 The recorder already stores it into `@12`

**Finding.** The event-5 handler:

```
00148C88  8E030000  lw v1, 0(s0)             ; the QB handle
00148C8C  24020004  addiu v0, zero, 4
00148C90  AE420004  sw v0, 4(s2)             ; summary phase = 4
00148C94  24170001  addiu s7, zero, 1        ; *** "a throw happened" latch
00148C98  128000BC  beq s4, zero, 0x00148f8c
00148C9C  AFA30030  sw v1, 48(sp)
00148CA0  8E020008  lw v0, 8(s0)             ; the TARGET pointer
00148CA4  504000BA  beql v0, zero, 0x00148f90 ; *** branch-likely
00148CAC  10000064  beq zero, zero, 0x00148e40
00148CB0  90450002  lbu a1, 2(v0)            ; *** target's roster index
00148E40  0C0936C2  jal 0x0024db08           ; -> set@12
```

and the setter is a plain unconditional store into record[0]:

```
0024DB08  308400FF  andi a0, a0, 0x00ff
0024DB0C  24030300  addiu v1, zero, 768
0024DB14  8F82C7C4  lw v0, -14396(gp)
0024DB1C  03E00008  jr ra
0024DB20  A045001C  sb a1, 28(v0)            ; base + side*768 + 28 = rec[0].@12
```

So on a pass, `@12` = the intended receiver's roster index. Default is `0xFF`
(AddPlay writes `sb -1, 12(s0)`), which doubles as "no target".

**The one real defect: `@12` is last-writer-wins.** The recorder also writes
`@12` from `0x0013B798(event)` — the event's *actor* — on event types 4, 6 and
8 (handler `0x00148E1C`/`0x00148D7C` → `0x00148E34`), and on the run path
(`0x00148F24`, reachable only when `s7 == 0`, i.e. no throw). A type-4/6/8
event occurring after the throw overwrites the receiver with whoever that
event's actor is. **Hypothesis:** events 4/8 are tackle/contact-class markers
(producers `0x001D4858`, `0x001ADB78` in the tackle/block module, `0x00203940`)
— which on a completed pass fire *after* the throw and would clobber the
target with the tackler or the carrier. This is exactly why the Phase-2 schema
needs its own slot rather than reusing `@12`.

### 2.6 What is live at the recorder's call site

**Finding.** The one caller:

```
0014A0F0  27BDFFB0  addiu sp, sp, -80
0014A0F4  A380B584  sb zero, -19068(gp)
0014A0FC  0080802D  daddu s0, a0, zero
0014A108  0C052216  jal 0x00148858
0014A110  0C052202  jal 0x00148808           ; init the stack summary
0014A114  03A0202D  daddu a0, sp, zero
0014A11C  0C052236  jal 0x001488d8           ; a0 = &summary, a1 = s0
0014A120  0200282D  daddu a1, s0, zero
0014A128  0C0523F4  jal 0x00148fd0           ; second pass over the same pair
```

In scope at the `jal`, and therefore available to a cave hooked there or inside
the recorder:

1. **The full event log** — `*0x00600E30`, up to 50 × 28 B, already populated
   for the just-finished play, and the recorder is already iterating it. The
   **type-5 event with the target pointer at `+8` is sitting in it.** This is
   the cheapest source: zero new globals, zero lifetime risk.
2. **`0x00213F60()`** — the throw-intent block, `+20` = target index, `+21` =
   kind. Reachable (the accessor is already called from this module at
   `0x00148538`). *Risk:* `+22` is cleared right after the throw
   (`001C82F0`), and a per-side reset zeroes +20/+21 — so whether `+20`
   survives to play end is **unverified**. Prefer source 1.
3. **`s3` = the recording side**, already computed at `0x0014891C`
   (`0x00260208`), and `s2`/`s1` = the two output structs.
4. **The ring itself** — `*0x00601EB4`; every setter targets record[0].

### 2.7 Recommended Phase-2 wiring (design note, not a patch)

Add a `@target` byte to the record and set it from the event-5 handler at
`0x00148CB0`, in parallel with the existing `@12` store, using a new setter
cloned from `0x0024DB08` with a different offset. That gives a target field
that (a) costs one instruction pair, (b) cannot be clobbered by later events,
and (c) needs no new state whatsoever. Keep `@12` as-is until the legacy
consumers are retired.

Also record the **throw kind** (`event@4`) — it is free at the same site and
separates real targets from handoff/pitch/spike, which `@15` alone cannot.

---

## 3. Recorder re-verification (the §4.1 bonus)

Re-derived from the stores, not from the prior summary.

### 3.1 The ring is newest-first, and every setter writes record[0]

**Finding.** `AddPlay 0x0024DA20` shifts all 48 records up by one before
writing:

```
0024DA4C  2405002F  addiu a1, zero, 47
0024DA58  00051100  sll v0, a1, 4            ; i*16
0024DA68  6843FFF7  ldl v1, -9(v0)           ; copy record[i-1] -> record[i]
0024DA78  B0430007  sdl v1, 7(v0)
0024DA88  14A0FFF3  bne a1, zero, 0x0024da58
0024DA98  0C12CFA2  jal 0x004b3e88           ; memset(record[0], 0, 16)
0024DAA4  AE110000  sw s1, 0(s0)             ; @0 = own play id
0024DAA8  A203000C  sb v1, 12(s0)            ; @12 = 0xFF
```
Every per-field setter computes `base + side*768 + {28,29,30,31}` — i.e.
`ring[side][0].@{12,13,14,15}`. Confirms record 0 = the current play.

### 3.2 `@8` — direction, and its encoding depends on `@15`

**Finding.** `set@8 0x0024DB28` passes the *already-written* `@15` as the mode
selector:

```
0024DB4C  24840010  addiu a0, a0, 16
0024DB54  02048021  addu s0, s0, a0          ; s0 = &ring[side][0]
0024DB5C  27A60010  addiu a2, sp, 16
0024DB60  0C09357E  jal 0x0024d5f8
0024DB64  9204000F  lbu a0, 15(s0)           ; *** a0 = @15 (1 = run, 2 = pass)
0024DB68  AE020008  sw v0, 8(s0)             ; @8 = result
```

`0x0024D5F8(mode, losPair, &spotPair, fieldByte)`:

```
0024D61C  46022001  sub.s f0, f4, f2         ; dx = spotX - LOS.x
0024D620  460000C5  abs.s f3, f0
0024D624  46030834  c.lt.s f1, f3            ; 4.5 (at = 0x40900000)
0024D640  24020002  addiu v0, zero, 2
0024D644  1482000D  bne a0, v0, 0x0024d67c
0024D648  24030008  addiu v1, zero, 8        ;  run,  dx > +4.5  -> 8
0024D650  24030200  addiu v1, zero, 512      ;  pass, dx > +4.5  -> 512
0024D658  24030002  addiu v1, zero, 2        ;  run,  dx < -4.5  -> 2
0024D660  24030080  addiu v1, zero, 128      ;  pass, dx < -4.5  -> 128
0024D66C  24030004  addiu v1, zero, 4        ;  run,  |dx| <= 4.5 -> 4
0024D674  24030100  addiu v1, zero, 256      ;  pass, |dx| <= 4.5 -> 256
0024D67C  54820013  bnel a0, v0, 0x0024d6cc  ; *** branch-likely: mode != 2
0024D680  46001045  abs.s f1, f2             ;     (delay, taken-only)
; --- mode == 2 (PASS) only: depth buckets ---
0024D694  460100C1  sub.s f3, f0, f1         ; dy = spot.y - LOS.y
0024D698  46021834  c.lt.s f3, f2            ; 7.0  (at = 0x40E00000)
0024D6A8  34630010  ori v1, v1, 0x0010       ;   short
0024D6B4  46001834  c.lt.s f3, f0            ; 15.0 (at = 0x41700000)
0024D6C0  34630040  ori v1, v1, 0x0040       ;   deep
0024D6C8  34630020  ori v1, v1, 0x0020       ;   intermediate
; --- mode != 2 (RUN) only: field-side flags ---
0024D71C  3C021000  lui v0, 0x1000
0024D720  3C022000  lui v0, 0x2000           ; hash/wide-side flags
0024D73C  3C024000  lui v0, 0x4000           ; gated on the field-direction byte
```

So, corrected and sharpened:

* **Pass (`@15==2`):** `{128 left | 256 middle | 512 right}` **OR**
  `{0x10 short (<7 yd) | 0x20 intermediate (7–15) | 0x40 deep (≥15)}`.
  Depth is **relative to the LOS**, measured to the event's spot.
* **Run (`@15==1`):** `{2 left | 4 middle | 8 right}` **OR** the
  `0x10000000/0x20000000/0x40000000` absolute-field-side flags. **No depth
  buckets on runs** — the doc's "inside/outside, L/R, short/deep" is right for
  passes and over-stated for runs.
* The "left/right" threshold is a **4.5-unit** lateral deadband about the LOS
  spot; `0x00146790` supplies the field-direction byte as `lbu 256(ball+72)`
  (ball object at `*0x006012C8`).

Call sites: `0x001489E0` (initial, run plays only), `0x00148D00` (event 22),
`0x00148DB4` (event 6), `0x00148E00` (event 20), `0x00148F58` (event 25).
Last writer wins.

### 3.3 `@12` — the play's key player (see §2.5)

`lbu 2(obj)` = a roster index. Three writer paths: the throw target
(`0x00148CB0`), the event actor for types 4/6/8 (`0x00148E3C`), and the
carrier on a no-throw play (`0x00148F2C`). Unconditional store; last writer
wins.

### 3.4 `@13` — signed yards

```
0024DB80  24030300  addiu v1, zero, 768
0024DB90  46006024  cvt.w.s f0, f12          ; truncate the float
0024DB98  00441021  addu v0, v0, a0
0024DBA0  A043001D  sb v1, 29(v0)            ; @13
```
Called once, from `0x00148D2C`, with
`f12 = playResult[+12] - LOS.y` (`00148D30 sub.s f12, f0, f12`, where
`20(sp)` is the snap LOS captured at `0x00148934`). **Signed byte — anything
beyond ±127 yards wraps** (not reachable in practice, but the field is `i8`).

### 3.5 `@14` — outcome class, write-ONCE

```
0024DBA8  24020300  addiu v0, zero, 768
0024DBBC  9062001E  lbu v0, 30(v1)
0024DBC0  14400002  bne v0, zero, 0x0024dbcc ; *** already set -> ignore
0024DBC8  A065001E  sb a1, 30(v1)
```
**First writer wins.** The five values and the event types that produce them
(**Finding** — straight from the handlers):

| `@14` | set at | reached from event type |
|---|---|---|
| 4 | `0x00148B1C` | 29 (via the `0x00148A60` handler tail, gated on IsPass + a spot compare) |
| 1 | `0x00148D0C` | 22 |
| 3 | `0x00148E0C` | 20 |
| 2 | `0x00148EB4` | 18 and 58 (shared handler `0x00148E84`) |
| 5 | `0x00148F64` | 25, only when `s7 == 0` (no throw) **and** IsPass |

Producers, for the A3 live read: evt 22 ← `0x00256510`, `0x00258964`,
`0x002588FC`; evt 25 ← `0x00253C20`; evt 29 ← `0x002540D4`, `0x002575D4`;
evt 18 ← `0x002578B0`; evt 58 ← `0x002577E8`; evt 2 ← `0x00258108`,
`0x00260634`. **Hypothesis** (not proven statically): 22 = incompletion /
turnover (it is the value the matchup scan penalises at −5.0), 25 = normal
play end. A3 should enumerate against observed outcomes; the handler→value map
above is the ground truth it maps onto.

Note `@14` carries **no 1st-down or TD bit** — with write-once semantics and
all five slots taken, T-track's first-down/TD counters need either a new field
or a 6th/7th value plus a relaxation of the write-once guard.

### 3.6 `@15` — run/pass, and the scramble re-classification

```
0024DBD8  308400FF  andi a0, a0, 0x00ff
0024DBEC  03E00008  jr ra
0024DBF0  A045001F  sb a1, 31(v0)            ; unconditional
```
Written at three sites:

```
00148998  0C07E0BA  jal 0x001f82e8           ; IsPass  (see 0.2)
001489A0  10400005  beq v0, zero, 0x001489b8
001489A8  0C0936F6  jal 0x0024dbd8
001489AC  24050002  addiu a1, zero, 2        ; pass -> @15 = 2
001489B8  0C0936F6  jal 0x0024dbd8
001489BC  24050001  addiu a1, zero, 1        ; run  -> @15 = 1, then compute @8
...
00148EF4  56E00027  bnel s7, zero, 0x00148f94 ; *** branch-likely: a throw happened
00148EFC  0C07E0BA  jal 0x001f82e8
00148F04  10400022  beq v0, zero, 0x00148f90
00148F1C  0C0936F6  jal 0x0024dbd8
00148F20  24050001  addiu a1, zero, 1        ; authored pass, no throw -> RUN
```

So `@15` already distinguishes a **scramble** from a called run. That is a real
signal for the coach (a QB-run tendency), and it is free.

---

## 4. What is still open

| # | question | how it closes |
|---|---|---|
| B5-a | Do event types 4/6/8 actually fire after a completed pass (clobbering `@12`)? | A3-adjacent live read, or: identify the type-6 producer (not found in the 48-site `BeginEvent` scan — it is one of the register-typed sites `0x00140A14`, `0x0015AC1C`, `0x001C5A88`, `0x002584FC`) |
| B5-b | Throw-kind → football meaning (which of 0..8 are real throws) | live read of `intent[+21]` on a known pass / handoff / pitch |
| B5-c | Does `intent[+20]` survive to play end? | live read at the recorder's call site (irrelevant if the event-log source is used) |
| B4-a | Are positions 10/11 really LE/RE? | one live read of `+0xB04` across a defensive lineup |
| B4-b | Event→outcome map for `@14` (A3) | unchanged; §3.5 gives the handler→value ground truth to map against |
| — | The `@8` run-mode flags `0x10000000/0x20000000/0x40000000` (hash / wide-side semantics) | decoded structurally at `0x0024D6CC`–`0x0024D75C`; the football meaning wants one live read |

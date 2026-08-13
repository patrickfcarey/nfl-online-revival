# AI-coach inputs: situation array, formation read, PA-bite, own-personnel

Static investigation, 2026-08-13, against `extract/SLUS_207.52` (Madden NFL
2004, PS2, SLUS-20752, CRC 0x14F8B841). `vaddr = file_off + 0xFF000`,
`gp = 0x006056F0`. Serves `ai-coach-playcalling-requirements.md` — the INPUTS
to the coach brain. No rig, no patch, no commit.

Discipline: every claim is pinned to an address + quoted disassembly.
**Finding** = proven from the binary; **Hypothesis** = inference. Closed-set
negatives are called out as evidence. Branch-likely / `movz`/`movn` / the
mipsdis SLLV swap bug flagged where they touch a cited span.

---

## Q1 — the game-situation state object (the "41-slot array")

### Finding: it is a single global object at `*(gp−14244)` = `*0x00601f4c`

There is one game-state object, held through a single global pointer. Every
accessor in the state library loads it the same way:

```
00260598  8F83C85C  lw v1, -14244(gp)   ; v1 = *0x00601f4c  (gamestate ptr)
0026059C  10600002  beq v1, zero, 0x002605a8
002605A0  0000102D  daddu v0, zero, zero
002605A4  90620040  lbu v0, 64(v1)       ; return [gamestate + 0x40] = possession side
002605A8  03E00008  jr ra
```

`0x00260598` is `GetPossessionSide()`. This is the same field
`fact-check-2026-08.md` §5 called `[gamestate+64]` and identified as
possession — **confirmed** here, and the pointer it hangs off is `gp−14244`
(0x00601f4c). The whole accessor library lives in the band
**`0x0025FF00`–`0x00260E30`** (~40+ small getter/setter/predicate leaves, all
keyed on `gp−14244`). The "41-slot variable array" of `ai-play-calling.md` is
this object plus its accessor family; there is **no separate flat 41-word
array and no table of accessor pointers** (closed search: no run of ≥6 words
pointing into the accessor band exists anywhere in the image).

### Finding: it is read from the play-calling / AI band

The possession field routes the play-weighting dispatcher. `0x0024DBF8` takes
`(side, playPtr, playId)`, asks `GetPossessionSide`, and tail-jumps to the
offense weighter `0x0024D070` when the requested side **is** the possessor,
else the defense/class-renorm weighter `0x0024D1C8`:

```
0024DC14  jal 0x00260598              ; possession side -> v0
0024DC18  andi s0, a0, 0x00ff         ; s0 = requested side
0024DC1C  bne s0, v0, 0x0024dc44      ; side != possessor -> D1C8 (defense)
...
0024DC3C  j 0x0024d070                ; side == possessor -> D070 (offense)
...
0024DC5C  j 0x0024d1c8
```

(This also resolves `fact-check-2026-08.md` §5: `[gs+64]` really is
possession, so the class-renormalisation weighter `0x0024D1C8` is on the
**non-possessor / defense** arm. Not this investigation's question, but
recorded because it fell out of the same trace.)

Beyond the dispatcher, the raw getters have callers throughout the pre-snap AI
band — e.g. the score getter `0x002609C0` and the `+0x38` getter `0x00260190`
are both called from `0x001483F0`, `0x001488B0`, `0x00148FF4`, `0x00149A40`,
`0x00149E78`… (the `0x00145`–`0x0014E` play/shift-calling module) and from the
play-object band `0x00243230`/`0x002435C4`. **The situational state is
reachable from the coach context.**

### Finding: field census (offset, width, proven semantics)

Full census of every `gp−14244`-based access in the library
(`0x0025FF00`–`0x00260E30`):

| offset | width | proven / inferred meaning | evidence |
|---|---|---|---|
| `+0x00` | u32 | **quarter / period counter** | `0x0025FF9C` end-of-period: `lw [0]`, `+1`, `sltiu v1,v0,4`, else force 2 (wrap to OT), then flips possession |
| `+0x04..+0x30` | 12× f32 | **field-geometry (LOS / landmarks / direction)** | `0x00260000` negates all twelve on a possession change (`neg.s` block) — field-flip |
| `+0x34` | u32 | per-play field (unlabeled) | get `0x002601A0` / set `0x002601F8`; 22 callers incl. UI `0x00140xxx` |
| `+0x38` | u32 | **game clock (Hypothesis, strong)** | get `0x00260190`; set `0x002601C8` via timer `0x00172E30`; read by ~230 call sites across every subsystem |
| `+0x3C` | u32 | **24-bit flag bitfield** | set/clear-bit leaves `0x002606A0`/`0x002606C8` (`sllv 1,idx`), 0..23 clear loop `0x002606E8`; bits 18 & 22 special-cased |
| `+0x40` | u8 | **possession side** | `0x00260598` getter; setter `0x002605D0`; drives the weighting dispatch above |
| `+0x41/42/43` | u8×3 | possession-sequence bytes (`+0x42` = previous `+0x41`) | `0x00260774`: `+0x42 = +0x41`; `0x00260790`: `+0x90 = +0x40` |
| `+0x44 / +0x46` | u16×2 | **per-team SCORE → score differential** | getter `0x002609C0` selects by side (`side==1 → +0x46`, else `+0x44`); setter `0x002609E0` same, and sets dirty flag `+0x48` |
| `+0x48` | u32/u8 | score-dirty flag | set to 1 in `0x002609E0`; cleared `0x00260A18`; byte-read `0x00260A28` |
| `+0x4C / +0x50` | u32×2 | per-play fields (unlabeled) | snapshotted with the block below |
| `+0x74` | 32-byte blob | embedded per-team/drive struct (unlabeled) | addr-of getter `0x00260A38` (`addiu v0,v0,116`); struct-copy setter `0x00260A48` (`ldl/ldr`…`sdl/sdr`) |
| `+0x8C..+0x9C` | mixed | possession-save + 2× f32 | `0x00260A78`, `0x00260DC8`, floats at `+0x98/+0x9C` |
| `+0xC0..+0xDC` | copy | **per-play SNAPSHOT of `+0x34..+0x50`** | see below |
| `+0x118/+0x11C` | u32/u8 | additional saved fields | `0x00260C3C`, `0x00260DD0` |

`+0x44`/`+0x46` being a **per-side u16 pair, indexed by the side argument and
written directly (not accumulated)** is the load-bearing evidence they are the
two teams' scores; the label "score" itself is inference, but the shape is
proven:

```
002609C0  andi a0, a0, 0x00ff
002609C8  bne  a0, v0, 0x002609d8   ; v0==1 ?
002609D4  lhu  v0, 70(v1)           ; side 1 -> [+0x46]
002609DC  lhu  v0, 68(v1)           ; side 0 -> [+0x44]
```

### Finding: "populated each play" is a snapshot/restore pair

`0x00260B50` copies the live block `+0x34..+0x50` (+ possession bytes
`+0x40..+0x43`, scores `+0x44/+0x46`) into a parallel saved block at
`+0xC0..+0xDC`:

```
00260B54  lbu v1, 64(v0)     00260B5C  sb  v1, 204(v0)   ; +0x40 -> +0xCC
00260B58  lw  a2, 52(v0)     00260B68  sw  a2, 192(v0)   ; +0x34 -> +0xC0
00260B60  lw  a0, 56(v0)     00260B6C  sw  a0, 196(v0)   ; +0x38 -> +0xC4
00260B64  lw  a1, 60(v0)     00260B70  sw  a1, 200(v0)   ; +0x3C -> +0xC8
00260B9C  lhu v1, 68(v0)     00260BB0  sh  v1, 208(v0)   ; +0x44 -> +0xD0 (score0)
00260BA0  lhu a0, 70(v0)     00260BB4  sh  a0, 210(v0)   ; +0x46 -> +0xD2 (score1)
00260BA4  lw  a1, 72(v0) ... 00260BB8  sw  a1, 212(v0)   ; +0x48/4C/50 -> +0xD4/D8/DC
```

`0x00260CD8` is the exact inverse (restore). This save/restore pair is the
per-play population mechanism the requirements doc's "populated every play"
refers to — the situation is captured at the play boundary. Additional
boolean situational **predicates** (return 0/1 over these fields) sit at
`0x00260490`, `0x00260518`, `0x00260558`… — the query API a policy layer
would call.

### Gaps (what a live read settles) — honest per rule 4

The doc's claimed contents that I could **not** statically bind to a specific
offset: **down, distance, timeouts (both teams), and "current play ids."**
They are almost certainly among the unlabeled slots (`+0x34`, `+0x4C`,
`+0x50`, the 32-byte `+0x74` blob, `+0x118/+0x11C`) or reached through the
boolean predicates, but no disassembled leaf proves the label. **A single
live read of `*0x00601f4c` during a play** (dump 0x120 bytes, compare against
the on-screen down/distance/timeouts/clock/score) assigns every remaining
label definitively. Score (`+0x44/+0x46`), possession (`+0x40`), quarter
(`+0x00`), field geometry (`+0x04..+0x30`) and the clock candidate (`+0x38`)
do not need it.

Toolchain note: the `+0x3C` bit accessors use `sllv` (`0x002606B4`,
`0x002606CC`) — read operand order with the known mipsdis SLLV/SRLV/SRAV
`rs`/`rt` swap in mind if patching them.

---

## Q2 — pre-snap formation / personnel read

### Finding: the readable signal is the per-player position byte `player+0xB04`

There is **no packaged formation-id or RB/WR-count field in the ELF.** The
engine reads personnel one player at a time from the u8 position enum at
`player+0xB04` (0xB04 = 2820), populated for all 22 players before the snap
(enum QB=0, HB=1, FB=2, WR=3, TE=4, OL 5–9, DL 10–12, LB 13–15, CB=16, FS=17,
SS=18; confirmed in `addresses.yaml` / `extract/ee_inplay.bin`).

The pre-snap defensive-shift picker reads it exactly as a role classifier via
`slti` buckets — verified:

```
0014E070  lbu  v1, 2820(s0)     ; this player's position (+0xB04)
0014E074  slti v0, v1, 10       ; <10  => offense
0014E078  bne  v0, zero, 0x0014e1ac
```

(further buckets at `0x0014E080` `slti ...,13` = DL 10–12, `0x0014E08C`
`slti ...,19` = LB/DB.) The coverage assignment path reads the same byte
(`0x001BC754  lbu v1, 2820(s0)` → `bne v1,16` special-cases CB).

### Finding (negative, closed): no tally, no formation-id in ELF memory

A full census of `+0xB04` loads (~200 sites) shows **every one dereferences a
single player object** — no loop anywhere accumulates an offense HB/FB/WR
count. So the "empty vs heavy" read is a **derivation, not a field read**:
iterate the offense's 11 players (`GetPlayer(side,idx)`), count `+0xB04 ∈
{1,2}` (backfield) vs `== 3` (WR). Positions are set the instant the huddle
breaks, so the empty check is available pre-snap. Player x/y at
`+0x190/+0x194` (confirmed populated) lets you split "in the backfield" from
"split wide" the same way.

Formation/personnel **ids** live in the on-disc play content (`FORM → SETL` /
`DMF`, per `play-data.md`), read by the alignment SQL but not surfaced as a
static engine field. The play-object accessors expose only the authored
**play-type/class** byte (`0x0015AEE0` → class getter `0x00243C98`), not a
formation id.

**Hypothesis (needs live read):** a transient formation/personnel-package id
may exist in the alignment object the SQL builds; not among any static
reader's offsets. A live dump of the play/alignment struct pre-snap settles
it. For the "empty check," the `+0xB04` count is sufficient and proven.

---

## Q3 — PA-bite / defensive run-pass diagnosis

### Finding: the diagnosis primitive is authored play-type, not a live read

A defender's run/pass decision comes from a global leaf, `IsRun()` at
`0x001f82e8`, which routes context → possession side → play object and tests
the play-type field `playobj+0x14`:

```
001F82E8  addiu sp, sp, -16
001F82F0  jal 0x00260598          ; possession side (SAME gamestate accessor as Q1)
001F82F8  jal 0x00248360          ; -> play object for that side
001F8300  jal 0x00243f58          ; classify play type

00243F58  lw   a0, 20(a0)         ; play type = playobj + 0x14
00243F5C  sltiu v1, a0, 7
00243F68  sltu v0, zero, a0       ; return (0 < type < 7) == "is a run"
```

A sibling `0x001f82b8` classifies the pass/other category (type ∈
{11..18,37,41}) via `0x00243f08`. Both key on the **authored** play type.

### Finding: the consumer sites (where a defender bites toward the LOS)

- **Man coverage (state 22) think** — `0x001BE2D0  jal 0x001f82e8`;
  `0x001BE2D8  beq v0,zero,0x001be31c` (not a run ⇒ stay in coverage). On a
  run read it builds a state-change to **state 85 (run support)** and the
  defender abandons coverage. A second read `0x001BE4D4  jal 0x001f82e8`
  (⚠ **branch-likely** `bnel` at `0x001BE4DC`) controls releasing from
  engagement (`+0x3E0`) to pursue.
- **Zone-family coverage** — helper `0x001EC9B8` gated at
  `0x001EC9CC  jal 0x001f82e8` (`beq v0,zero,→exit`), then a geometric
  "runner crossed me" test (`self+0x190` vs carrier `+0x190`) — but **only
  after** `IsRun` is already true.

### Key implication (Finding) and the gap

Because every defender keys on the **authored** play type, a play-action
**pass** returns `IsRun == 0` at all these sites — so as shipped, **defenders
cannot be fooled by a fake at all**; there is no live OL-pass-set / run-block
"run key" being read. The multi-variable bite model must be **introduced** by
replacing the binary `IsRun` gate at `0x001BE2D0`, `0x001BE4D4`, `0x001EC9CC`
with a probabilistic diagnosis = f(run-success from `ptrk`, down/distance,
field position, score/time, formation) — all inputs Q1 + Layer-1 supply.

**Hypothesis (gap):** the run-reaction branch at `0x001BE2D0` carries **no
AWR/discipline term** — the bite is all-or-nothing on play type. No
rating-scaled discipline scalar was found on the immediate branch (closed on
the branch, not a full-function census); such a scalar would have to be added.

---

## Q4 — own-personnel / matchup evaluation

### Finding (a): the `+0xB70` ratings block is reached from the play-calling band

Census of `lh/lhu` with displacement in `0xB70..0xB96` (the 21× u16 ratings):
AWR (idx2, `+0xB74`) = 76 sites, STR (idx15, `+0xB8E`) = 13, etc. A concrete
read **inside the play/shift-calling module** (`0x00145AB0`, called by
`jal` from `0x00147A38`):

```
00145AEC  lh   v0, 2932(s0)       ; +0xB74 = AWR (idx2) of player s0
00145AF0  lwc1 f2, -31716(gp)     ; scale const @0x005FDB0C
00145AF8  cvt.s.w f1, f1
00145AFC  mul.s f1, f1, f2        ; AWR * k -> threshold gate
```

So the ratings block is reachable and read from the coach context. Whether
`s0` is the CPU's own player or the opponent depends on the arg the
`0x00147A38` caller passes; the load idiom (`lh …, 0xB70+2*idx`) is identical
for own-player and matchup reads, so **both are mechanically available** — the
"evaluate my own nickel corner's rating" read is a supported operation, not a
new capability.

### Finding (b): coverage reads the defender's position + AWR; the assignment lives in the state-chain record

Man-coverage helper `0x001bc738` (from the state-22 think) reads the
defender's position byte and a coverage-assignment param out of the
state-chain object `*(player+0x2FC)`:

```
001BC754  lbu  v1, 2820(s0)       ; +0xB04 position of defender s0
001BC758  bne  v1, v0, 0x001bc7b8 ; v0 = 16 (CB) special-case
001BC778  lw   v1, 764(s0)        ; +0x2FC = state-chain object
001BC77C  lbu  a0, 1(v1)          ; chain record byte1 (assignment param p1)
```

The break-off roll reads the defender's own AWR (matches `sdchargersfanboy.md`):

```
001BE914  lhu  s0, 2932(s1)       ; +0xB74 AWR of defender s1
001BE924  jal  0x0024e188         ; ptrk getter -> AWR + AWR*f
001BE968  bnel v0, zero, ...      ; ⚠ branch-likely
```

### Finding (negative) + Hypothesis: the CB-vs-WR comparison does not exist yet

All the pieces are individually reachable from coverage/AI code — defender
position `+0xB04`, defender AWR `+0xB74`, and (once the assigned receiver is
resolved to a player object via the `GetPlayer` idiom) that receiver's
`+0xB70` ratings via the identical `lh …,0xB70+2*idx`. **But no site loads the
covered WR's rating and compares it to the covering defender's rating** —
coverage think reads only *its own* AWR (`0x001be914`), never the receiver's
(`qb-read.md` independently: "no receiver rating enters coverage"). So
CB-vs-WR matchup scoring is a **build target on already-reachable inputs**, not
existing code — exactly the "forced to a subpar nickel package" feature.

**Hypothesis (needs live read):** the covered-receiver identity is carried in
the state-chain record `*(player+0x2FC)` params `p1..p3`, but which param byte
holds the covered WR's index is unresolved statically — the one param read I
pinned (`0x001bc77c`, byte1) is compared against the defender's **own** lineup
number (`0x001c04f0` returns self), i.e. a self-check, not a clean receiver
index. A live read of `+0x2FC` on a man corner during a play settles which
byte is the assignment target.

---

## Cross-cutting note

The Q1 gamestate accessor `0x00260598` (possession) is called directly by the
Q3 diagnosis leaf `0x001f82e8` and by the Q1 weighting dispatcher
`0x0024DBF8`. The situation object at `*0x00601f4c`, the play-object type
field `playobj+0x14`, the position byte `player+0xB04`, and the ratings block
`player+0xB70` are the four readable primitives the coach objective function
consumes — all confirmed reachable; the missing pieces (down/distance/timeout
labels, formation-package id, covered-WR field) are each a single live read
away, and none blocks the design.

# The situational-policy script — disc asset #69, decoded

Ledger item **C2** (`ai-coach-playcalling-requirements.md` §7): *format, decompile,
re-author.* Done statically, 2026-08-14. The bytecode format is fully reversed
from the PS2 ELF, the shipped script disassembles at **99.2% coverage with zero
undecodable statements**, the 4th-down go/no-go policy is located and readable,
and the file turns out to be **stored uncompressed on the disc** — which makes
re-authoring a same-size byte overwrite on an existing pipeline, not a research
project.

Tooling: `tools/vmscript.py` (disassembler), `tests/test_vmscript.py` (16 tests,
hand-assembled fixtures plus an asset-backed integration arm that skips when
`extract/` is empty).

---

## 1. Getting the asset

**Finding — asset #69 is member 69 of `GAMEDATA.DAT`, stored with codec 0
(raw), not compressed.**

```
             file bytes    member-69 file offset   length   sha256
PS2   GAMEDATA.DAT  2,179,776   0x001fccc0          28,301   82b087e3ab1feffa…
Xbox  GAMEDATA.DAT  2,261,760   0x001fd500          28,301   82b087e3ab1feffa…
```

Extracted with `tools/lzh1.read_terf`, saved to `extract/asset69_ps2.bin` and
`extract/asset69_xbox.bin` (gitignored). The two are byte-identical, confirming
`docs/xbox-data-layer.md`: one decoder, one re-authored script, both platforms.

The container's `COMP` table gives member 69 `ctype = 0, usize = 28301` and its
`DIR1` entry gives `csize = 28301` — same number, no compression. Nine of the
76 members are codec 0, so **the engine reads stored members as shipped**; this
is not an exotic path. Members are 32-byte aligned: member 69 ends at
`0x0020360d` and member 70 begins at `0x00203640`, leaving **51 bytes of slack**
after the script.

*Load path (unchanged from the earlier investigation, re-checked):*
`0x00247db4 jal 0x0047f480 (a1=69, a2=1)` → init `0x0024c750` → VM globals
`0x00618fd0` → exec `0x0024c7c8`.

---

## 2. The virtual machine

Everything in this section was read out of `SLUS_207.52`; the three jump tables
were dumped programmatically through `recon.mipsdis.Elf32.word`, not by hand.

| piece | address |
|---|---|
| interpreter | `0x0024bfc0` |
| opcode jump table (8 entries) | `0x00586ac0` |
| operand reader | `0x0024bea0` |
| comparator table (7 entries, #7 NULL) | `0x00586aa0` |
| native command handler | `0x0024bb50`, table `0x00586a20` (13) |
| exit dispatcher, primary | `0x0024c468`, table `0x00586ae0` (5) |
| exit dispatcher, secondary | `0x0024c628`, table `0x00586b00` (5) |
| header relocator | `0x0024bf10` (byte-swap + add base, 10 words) |

**Correction to the earlier investigation:** the opcode table has exactly
**8** entries. The words that follow it at `0x00586ae0` are the *exit
dispatcher's* 5-entry table, not opcodes 8–9; the interpreter's own bound is
`sltiu v0, a0, 8` at `0x0024c018`.

### 2.1 Header

Ten big-endian `u32` file offsets at byte 0; `0x0024c750` hands them to
`0x0024bf10`, which byte-swaps ten words in place and adds the load address.
Init also stores `script+0` and `script+20` as two table bases, so:

* **entries 0–4** (`0x0038 0x5016 0x5018 0x52e7 0x598a`) are reached through the
  primary exec `0x0024c7c8`;
* **entries 5–9** (`0x5e86 0x675b 0x675c 0x697c 0x6aaa`) through the secondary
  `0x0024c930`.

`exec(out_buf, class)` runs `header[class]`, dispatches one native command from
the result, and copies the first 64 bytes of the VM context into `out_buf`
(`0x0024c84c`). The ten entry points partition the file exactly — every
statement reachable from an entry is reachable from no other.

### 2.2 Statements

A statement begins with one byte: `op = b >> 4`, `n = b & 0x0f`. **Every branch
offset is a signed big-endian 16-bit value relative to the opcode byte** (the
interpreter adds it to opcode+1 and subtracts one at use — `addiu v0, v1, -1` at
`0x0024c070` and `0x0024c2a8`).

| op | name | encoding | effect |
|---|---|---|---|
| 0 | `RET` | — | pop a continuation pushed by op2 (`0x0024c040`) |
| 1 | `IF n` | `s16 else` + n×(value, cmp, value) | all n must hold; else jump (`0x0024c0b0`) |
| 2 | `SWITCH n` | value, `s16 join`, `s16 default`, then a case chain | `0x0024c150` |
| 3 | `END` | — | `ctx[86] = n`, exit code 4 (`0x0024c2b4`) |
| 4 | `END b` | 1 byte | `ctx[86] = b`, exit code 1 (`0x0024c2c0`) |
| 5 | `END w` | 2 bytes, **little-endian** | `ctx[86] = w`, exit code 2 (`0x0024c2d4`) |
| 6 | `CASE` | value, `s16 next` | a case label executed by fall-through: skips itself (`0x0024c300`) |
| 7 | `SETPLAY w` | 2 bytes, little-endian | `ctx[84] = w`, continue (`0x0024c344`) |

op6 is the elegant part: a `SWITCH`'s case list is a chain of op6 labels, and
running off the end of one case's body lands on the next label, which skips its
own operands and runs the following body — C's fall-through, in one opcode.

### 2.3 Values (`0x0024bea0`)

* `0x80–0xbf` → **variable reference**, index `b & 0x7f`, read as a *signed
  halfword* from `ctx + 2*index`.
* anything else → **two-byte big-endian immediate**, sign-extended from bit 13
  (`andi v0, a1, 0x2000`) — a 14-bit signed range.

**In case-label position only**, bytes `0x40–0x7f` and `0xc0–0xff` are
intercepted before the reader (`0x0024c200`, `0x0024c30c`) and mean *ask the
engine to pick a play*: the handler is called with command 11 (`0x0024bc8c`),
which calls the selector **`0x00249498`** with `flag = b & 0x3f` and flips the
side when `(b & 0xc0) == 0xc0` (`xori v0, s0, 1`). The case matches iff the pick
succeeded (`movn a2, s1, v0`, `0x0024c23c`). This is the script's only inline
call into the engine.

Comparators, by table index: `0 1 ==`, `2 <`, `3 >`, `4 <=`, `5 >=`, `6 !=`.

### 2.4 Exits and the native commands

The interpreter never emits clock/huddle commands inline. It **terminates with a
code** (1, 2, 3 or 4) which it writes to `ctx+82`, leaving the payload in
`ctx+86`; `exec` then calls the dispatcher for its class:

| class | script | what the result becomes |
|---|---|---|
| 0 | E0 / E5 | exit 1 → cmd8, exit 2 → cmd9 (both = set play/group), 254 → cmd5, 255 → cmd12 |
| 1 | E1 / E6 | cmd10(`ctx[40]`) — E1 and E6 are one-statement stubs |
| 2 | E2 / E7 | cmd8/cmd9 with bit 15 set (`ori a2, a2, 0x8000` = *specific play*) |
| 3 | E3 / E8 | 255→cmd1, 254→cmd2, 253→cmd3, 252→cmd4 (clock / huddle / timeout) |
| 4 | E4 / E9 | 255→cmd6, 254→cmd7 — a **yes/no answer** written back to `ctx+86` |

cmd8 → `0x00249400`, cmd9 → `0x002493a8`, cmd11 → `0x00249498` (the §4.2 seam),
cmd2/3/5/12 → `0x00163148` / `0x00163310` / `0x0017b638`.

### 2.5 The variable table = the situation snapshot

`ctx` is `[0x00609770] + 0x15f7c` (the only place in the ELF that computes it,
`0x00247dc8`) — i.e. immediately after the two 0xafbc-byte team playbook blocks.
It is filled by **cmd0**, which `exec` issues before every run (`0x0024c7f4`):
`0x0024b9e8` → the common snapshot **`0x0024b570`**, plus a per-class filler
(`0x0024b4e0` for classes 0/2, `0x0024b230` for class 4).

The load-bearing slots, with the instruction that fills them:

| var | ctx | source | meaning |
|---|---|---|---|
| V2 | +4 | `sit+0x46 − sit+0x44` (`0x002609c0`) | **score difference** |
| V3 | +6 | `sit+0x38` remapped 0..4 (`0x0024b6e0` switch) | **down** (1,2,3; 4 = 4th/kicking; 0 = PAT) |
| V4 | +8 | `sit+0x08` (float) − ball spot | **yards to go** |
| V5 | +10 | `50.0 −` ball spot | **yards to the opponent's goal** |
| V6 | +12 | `0x0013eb70()` = clock period | **quarter** (1–5) |
| V7 | +14 | `0x0013eca0(1)` | seconds left in the quarter |
| V8 | +16 | + one quarter length on Q1/Q3 | **seconds left in the half** |
| V9 | +18 | + 3/2/1 quarter lengths on Q1/Q2/Q3 | **seconds left in the game** |
| V10,V11 | +20,+22 | `0x002f9428(0,100)` | two random rolls, 0–99 |
| V15 | +30 | `0x0013ed00(1)` | clock running |
| V19 | +38 | `0x0015fa48(0)` (class 4 only) | which yes/no **question** is being asked |
| V22,V23 | +44,+46 | `*(u16*)(0x00248360(side)+4)` | the op2 subject for the play-select switches |
| V35–V38 | +70…+76 | `0x0014b9a8()` f32@0x14×100, s8@0x45, f32@0x24×100; `0x0017b868()` | **coach profile** — the 4th-down aggression gate |
| V41,V42,V43 | +82,+84,+86 | VM-internal | switch subject / exit code, pending play, result |

`sit` is the game-situation object at `*0x00601f4c` — the same object §4.3
describes, so the script reads the situation through the engine's own accessors.

Two facts worth carrying forward:

* **`V6` is the quarter, not the down**, and `V8`/`V9` are derived by the
  builder adding whole quarter lengths (`0x0013ecc0`) — the script gets
  time-left-in-half and time-left-in-game for free, already computed.
* **`V29`/`V30` are hardcoded to 50** (`addiu v0, zero, 50` … `sh v0, 58(s1)` /
  `sh v0, 60(s1)` at `0x0024b994`–`0x0024b9a4`); no other writer in the snapshot
  chain (`0x0024b230`, `0x0024b4e0`, `0x0024b570`, `0x0024b9e8`, `0x0024ba98`)
  touches either slot. The script compares `V29` **36 times** — `> 75` ×16,
  `> 65` ×13, `> 64` ×2, `> 60` ×2, `< 50` ×2, `> 50` ×1 — and with the value
  pinned at 50 **not one of them can ever be true**; `V30` is never read at all.
  *Hypothesis:* an
  aggression/difficulty slider that was authored into the script and never wired
  to a source. It is a ready-made knob: point those two slots at a real value
  and a dozen already-authored rules wake up.

---

## 3. What the script actually contains

`python3 tools/vmscript.py extract/asset69_ps2.bin` disassembles the whole file:
**5,537 statements, 28,068 of 28,301 bytes covered (99.2%), zero undecodable**.
The 176 uncovered bytes are single `0x00` pad bytes between case chains (plus
the 0x38-byte header). Statement mix: 1,967 `IF`, 1,560 `END b`, 1,056
`SETPLAY`, 378 `CASE`, 331 `END w`, 136 `SWITCH`, 108 `RET`.

| entry | offset | statements | role |
|---|---|---|---|
| E0 | 0x0038 | 4,071 | **the offensive play-group policy** — 2-point chart, then quarter × down × field position × score |
| E1 | 0x5016 | 1 | stub (`END 2`) |
| E2 | 0x5018 | 112 | specific-play / group-select script (14 `SELECT_PLAY` labels) |
| E3 | 0x52e7 | 245 | **clock management** — huddle, hurry-up, timeout (results 252–255) |
| E4 | 0x598a | 210 | **yes/no questions**, switched on V19 (13 questions) |
| E5 | 0x5e86 | 551 | defensive/secondary play-group policy (140 `SELECT_PLAY` labels — the busiest) |
| E6 | 0x675b | 1 | stub |
| E7 | 0x675c | 137 | secondary specific-play script (33 `SELECT_PLAY` labels) |
| E8 | 0x697c | 24 | secondary clock script |
| E9 | 0x6aaa | 185 | secondary yes/no questions |

**Finding — the `END` value in E0 is an AI play-group id (`AIGR`).** Across all
64 playbook TDBs in `GAMEDATA.DAT`, the `PBAI.AIGR` column takes 32 distinct
values: **0–24 and 32–38**. E0's `END` values are `{0,1,3,4,5,6,7,8,9,10,11,12,
13,14,15,16,18,21,22,34,35,36,37,38}` plus the sentinel 254 — every one inside
that domain, **none in the 25–31 hole `AIGR` also skips**. E5/E7 use 0–24 only.

---

## 4. The 4th-down rule — found, and readable

**Finding — the 4th-down go/no-go policy is authored here, as 13 `down == 4`
case bodies totalling 9,003 bytes, 31.8% of the file.** Twelve are in E0 (one
per quarter × game-state branch), one in E5.

The simplest, Q1 (`SWITCH V6(quarter)` case 1 → `SWITCH V3(down)` case 4 at
`0x0628`), decoded with the raw bytes beside it:

```
0628  60000400d6                CASE 4 (next -> 06fe)
062d  1400158503001e85020028…   IF to_goal > 30 AND to_goal < 40 AND togo < 3
                                  AND V29 > 65               -> END 1
0642  11000985050026            IF to_goal >= 38             -> END 9    (punt)
064b  16003f8e0300e18e02013b…   IF wind? in (225,315) AND coach_A > 40
                                  AND coach_D > 89 AND coach_C > 40 AND coach_B < 33
0666  11000985020021              IF to_goal < 33            -> END 0    (field goal)
066f  11000984020003              IF togo < 3                -> END 34   (go for it)
0678  11000984020007              IF togo < 7                -> END 35
0681  11000984030006              IF togo > 6                -> END 9    (punt)
```

The 4th quarter (`0x22b3`, 989 bytes) is the interesting one — this is the
"competent head coach" behaviour the campaign is after, and it is score-aware:

```
22b8  IF to_goal > 55                                   (own end of the field)
22bf    IF score_diff <= -33 AND to_goal < 70           -> 9    punt
22cc    IF score_diff <= -17 AND to_goal < 70
22d7      IF togo < 3 -> 34 ;  IF togo < 6 -> 35 ;  else 9
22eb  IF score_diff < -8 AND to_goal <= 55 AND to_goal > 37
2303      IF togo < 3 -> 34 ; togo < 6 -> 35 ; togo < 15 -> 36 ; else 9
2320  IF score_diff < 0  AND to_goal <= 55 AND to_goal > 37
232f      IF togo < 3 -> 34 ; togo < 6 -> 35 ; else 9
2343  IF score_diff == 0 AND to_goal <= 55 AND to_goal > 37
2352      IF togo < 2 -> 34 ;                    else 9
235d  IF score_diff > 0  AND to_goal <= 55 AND to_goal > 37   -> 9   (always punt)
236e  IF score_diff <= -12 AND to_goal <= 37
2379      IF togo < 3 -> 34 ; togo < 6 -> 35 ; togo <= 15 -> 36 ; else …
```

Read plainly: **in a tied 4th quarter between the opponent's 38 and 55, the CPU
goes for it only on 4th-and-1 (`togo < 2`); if it is ahead at all it always
punts; trailing, the threshold opens to `togo < 3`, then `< 6`, then `< 15` as
the deficit grows.** The operator's "go for it on 4th-and-1 like a competent NFL
head coach" is one immediate away — `2352: 84 02 00 02` (`togo < 2`) is a
two-byte constant in a fixed-size statement.

**Finding — 4th-down aggression is gated on the coach profile.** The
`coach_A > 40 AND coach_D > 89 AND coach_C > 40 AND coach_B < 33` guard appears
throughout the down-4 bodies; those four variables come from the coach object
(`0x0014b9a8`, fields `f32@0x14`, `f32@0x24`, `s8@0x45`) and `0x0017b868`. So
the shipped policy already scales with coach personality — a hook the coach
brain can reuse rather than replace.

**The engine never asks "should I go for it?" as a yes/no question.** The E4/E9
question scripts (13 questions, switched on `V19`) answer with 255/254 and are
about timeouts, spikes and clock decisions; the 4th-down choice is expressed as
*which play group to request*, with punt (9) and field goal (0) as ordinary
groups. That is exactly the shape the coach brain wants.

### Two more decoded samples

**The 2-point conversion chart** (E0's first block, `0x0038`), which is a pure
score-and-clock table:

```
0048  IF quarter == 4 AND score_diff < 0   AND game_secs < 120  -> END 16
0059  IF quarter == 4 AND score_diff <= -9 AND game_secs < 240  -> END 16
006a  IF quarter == 4 AND score_diff <= -17 AND game_secs < 360 -> END 16
007b  END 15
```

**The group-select switch** (E2, `0x51ef`) — the only place the script talks to
the selector seam, and it reads as an ordered preference list with fallback:

```
51ef  249600a5003a   SWITCH V22 (4 cases, join -> 5294, default -> 5229)
51f5  604c0005         CASE SELECT_PLAY(group=<subject>, side=US, flag=0x0c)
51fa  60400005         CASE SELECT_PLAY(group=<subject>, side=US, flag=0x00)
51ff  60490017         CASE SELECT_PLAY(group=<subject>, side=US, flag=0x09)
5216  604d0013         CASE SELECT_PLAY(group=<subject>, side=US, flag=0x0d)
5229  2697006a006a   SWITCH V23 (6 cases …)   ; the same, for the other side
```

**The clock script** (E3, `0x52e7`):

```
52e7  IF clock_running == 1                                   else -> 5989
52ee  IF half_secs < 2 AND V0 > 0
52f9    IF state_is_2 AND quarter == 4 AND score_diff > 0      -> END 253 (cmd3)
530b    IF state_is_2                                          -> END 255 (cmd1)
5315  IF half_secs < 3 AND state_is_2                          -> END 252 (cmd4)
```

---

## 5. Verdict — is this a practical patch surface?

**Yes. It is the cheapest re-authoring surface in the project.** Three reasons:

1. **The bytes are not compressed.** Member 69 is codec 0. Editing the policy is
   a raw overwrite of a known byte range at a known offset inside
   `GAMEDATA.DAT` — no LZH1 compressor, no container rewrite, no re-pack.
2. **Same-size edits are free, and most useful edits are same-size.** Immediates
   are fixed-width (2 bytes), comparators are 1 byte, `END` payloads are 1 or 2
   bytes, and branch offsets are relative — so retuning a threshold
   (`togo < 2` → `togo < 3`), flipping a comparator, or changing which group a
   rule returns touches 1–2 bytes and moves nothing. `docs/pnach-to-iso-pipeline.md`
   **P1 is already built and verified**, and `tools/patch_iso_roster.py` already
   does same-size in-place patching of a *data file* in the ISO — which is
   exactly this shape, not the ELF shape.
3. **Structural rewrites are a modest tool, not a research project.** The format
   is fully decoded and the disassembler already recovers the complete statement
   graph; an emitter that renumbers relative offsets is the mirror image of
   `tools/vmscript.decode`. Growth beyond the **51 bytes of member slack** means
   shifting later members and rewriting `DIR1`/`COMP`, then a file-size change on
   the disc — the P2 path. Budget that only if the policy is rewritten wholesale;
   a re-tune does not need it.

**Recommended first patch (cheap, measurable, self-contained):** the Q4 tied-game
4th-down threshold at `0x2352` and its twelve siblings — one immediate each,
same size, no offset changes. Acceptance is directly observable (4th-and-1 calls
from the CPU) and it is independent of every ELF change in flight.

**Scope note (rule 1).** This is disc data on a different code path from the
seam hook (§4.2). Re-authoring the script and retargeting `0x00249498` are
separate patches with separate acceptance tests, and the script edit does not
require the ELF-expansion workstream. The script is also the *source* of the
group requests the seam receives, so a coach brain that takes the seam must
decide whether it still honours the script's group choice or overrides it —
that is a design decision (§7 D-list), not an investigation.

---

## 6. What is decoded, and what is not

**Solid:** the opcode set, operand encoding, branch semantics, the case-chain
grammar, the header and its ten entry points, the exit-code → native-command
dispatch, the situation-variable table's provenance, and the full statement
graph of the shipped script.

**Inferred, not proven (Hypothesis):**

* **The `END` value is an `AIGR` group id.** The evidence is a domain match
  (including the 25–31 hole) plus the punt/FG/go-for-it reading of specific
  values; it is not a live read. Ledger **A2** (break in `0x002bff68`) confirms
  it in one breakpoint.
* **V5's polarity** ("yards to the opponent's goal") is inferred from
  `to_goal <= 4 → goal-line group` and `to_goal >= 95 → backed-up group`. Same
  live read settles it.
* **V14** is only ever tested against 225 and 315 (228 times) and is scaled by
  `2.146e-5`; "wind direction in degrees" fits, but nothing proves it.
* **V0, V1, V13, V16–V18, V20–V28** have verified sources and unverified
  meanings. V24–V28 are class-4 question operands from `0x0024b230`.
* The 13 E4/E9 **question ids** (`V19` from `0x0015fa48`) are not enumerated.

**Correction owed to `ai-coach-playcalling-requirements.md`:**

* §4.3 lists the situation object's **clock at `+0x38`**. `0x00260190` reads
  `sit+0x38` as a `u32` that the snapshot builder switches over with **7** cases
  and remaps to the script's `down` (1–4, 0 = PAT). The clock does not come from
  the situation object at all — it comes from the clock module at `*0x00600bd8`
  (`0x0013eb70` period, `0x0013eca0` value, `0x0013ecc0` length). Worth
  re-checking `+0x38` when ledger **A1** runs.
* §4.2 labels the seam `0x00249498` as `a1 = flag, a2 = group id`. In
  `0x002bff68` the query template puts **`a1` into the `AIGR` clause**
  (`sw a1, 64(sp)`, struct tagged `AIGRPBAI`) and **`a2` into the `PBPL` clause**
  (`sw a2, 104(sp)`, struct tagged `PBPLPBAI`). The script's own values agree:
  the case-label byte it passes as `a1` is 0x00–0x16, inside `AIGR`'s domain,
  while `a2` comes from a runtime playbook halfword. So **the group arrives in
  `a1`** — the labels look swapped. Flagged rather than rewritten: which struct
  is the where-clause and which is the projection needs `0x004c7e38` read or the
  A2 breakpoint.

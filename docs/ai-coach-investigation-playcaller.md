# AI coach investigation — the CPU play-caller chain and the replacement seam

Static investigation, 2026-08-13, against `extract/SLUS_207.52` (Madden NFL
2004, SLUS-20752, CRC 0x14F8B841). Static only — no rig, no emulator. Every
load-bearing claim is pinned to an address + quoted disassembly (rule 4).
`vaddr = file_offset + 0xFF000`, `gp = 0x006056F0`.

Prefix key: **Finding** = verified against the binary here; **Hypothesis** =
inference not yet nailed; **Correction** = a prior doc statement was wrong.

---

## Q1 — Which function actually SELECTS the CPU's offensive play? Resolve `0x001459B4`.

**Finding (resolves the doc conflict in favour of `ai-play-calling.md`):**
`0x001459B4` is **not** a function entry and it is **not** the offensive play
caller. It is a branch target *inside* the function that begins at
**`0x00145940`**, and that function is the **pre-snap DEFENSIVE line/LB shift
picker**. `play-tendency-ai.md`'s "the CPU play-caller (`0x001459b4`+) also
reads the raw history to choose its own plays" is a **Correction**: those
`ptrk` reads belong to the defensive-shift routine, and they bias the
pre-snap *alignment*, not the offensive *play id*.

### Evidence

**No `jal` targets `0x001459B4`** — it is reached only by the internal branch
`bne v0, zero, 0x001459b4` at `0x0014599C`. The enclosing function's prologue:

```
00145940  27BDFF80  addiu sp, sp, -128        ; function entry
00145944  FFB20050  sd s2, 80(sp)
0014594C  0080902D  daddu s2, a0, zero        ; s2 = the object (a0)
0014595C  92420011  lbu v0, 17(s2)            ; a "dirty/pending" flag at +17
00145960  1040004C  beq v0, zero, 0x00145a94  ; nothing to do -> return
00145968  0C05CA60  jal 0x00172980            ; read game-mode word
00145974  10430046  beq v0, v1, 0x00145a90    ; v1 == 10 -> skip
```

**The two-side loop** (`s3` = 0,1) that iterates a 10-byte descriptor table at
`0x0052D450` and calls the per-side workers:

```
00145A34  3C020052  lui v0, 0x0052
00145A38  2451D450  addiu s1, v0, -11184      ; s1 = 0x0052D450 (descriptor table)
00145A40  6A220007  ldl v0, 7(s1)             ; load 8-byte record ...
00145A48  86230008  lh v1, 8(s1)              ; ... + halfword at +8  (stride 10)
00145A64  0C051542  jal 0x00145508            ; per-side shift worker (branch A)
00145A68  2631000A  addiu s1, s1, 10          ; advance one 10-byte record
00145A78  0C0514F8  jal 0x001453e0            ; per-side shift worker (branch B)
00145A7C  26730001  addiu s3, s3, 1
00145A80  2E620002  sltiu v0, s3, 2           ; loop for 2 sides
00145A8C  A2400011  sb zero, 17(s2)           ; clear the pending flag
```
*(Flag: `movz v0, zero, v1` at `0x00145988` is a conditional move; the loop
back-edges at `0x00145A84` and `0x00145984` use plain `bne`, no branch-likely.)*

**It reads `ptrk` (opponent history) to weight the shift** — the calls into the
tracker module, in sequence:

```
001459C8  0C09371A  jal 0x0024dc68            ; ptrk accessor
001459DC  0C09383C  jal 0x0024e0f0            ; RepetitionFactor(side,playId)
001459F0  0C0938C0  jal 0x0024e300            ; ptrk read
00145A00  0C09387E  jal 0x0024e1f8            ; ptrk read
00145A1C  0C093808  jal 0x0024e020            ; ptrk read
```

**The workers set halfword shift weights, not a play id.** `0x00145508` calls
the weight-accumulator `0x001454a8` three times with option indices 2, 3, 4 and
float multipliers 1.0 / 0.5 / 0.0:

```
00145594  24050002  addiu a1, zero, 2         ; option index 2, weight f12=1.0
0014559C  0C05152A  jal 0x001454a8
001455AC  24050003  addiu a1, zero, 3         ; option index 3
001455C4  24050004  addiu a1, zero, 4         ; option index 4
```

`0x001454a8` is a **halfword** weighted accumulator over a tiny option array —
`slot[index]` at `a0 + index*2`, scaled by `f12`, folded into a running total
slot `a2`:

```
001454AC  00052840  sll a1, a1, 1             ; index*2  (16-bit slots)
001454B4  94A70000  lhu a3, 0(a1)             ; old weight
001454C0  460C0002  mul.s f0, f0, f12         ; * multiplier
001454E8  A4A20000  sh v0, 0(a1)             ; store back (halfword)
001454FC  03E00008  jr ra
00145500  A4620000  sh v0, 0(v1)             ; running total (halfword)
```

This is exactly the "shift weight table" `ai-play-calling.md` F4 describes
(pinch/spread weighted **zero** ⇒ the CPU never pinches/spreads): a small
halfword option table, **not** the ~225-entry float pool of the offensive
roulette. **Conclusion: `0x00145940` chooses the defensive front/coverage shift;
it does not enumerate plays or emit a play id.**

**Corollary Finding — the offensive play-caller does NOT read `ptrk` statically.**
Callers of the `ptrk` factor producers were enumerated:
* `RepetitionFactor 0x0024e0f0` ← `0x001459DC` (the shift fn) + `0x0024D25C`,
  `0x0024D9E4` (inside `ptrk` itself). Nothing else.
* reads `0x0024e300 / 0x0024e1f8 / 0x0024e020` ← **only** `0x00145940` (the shift
  fn) and the `ptrk` recompute.
* getters `0x0024e188 / 0x0024e1c0` ← only the rating-cheese consumers already
  catalogued in `play-tendency-ai.md` (coverage break-off, tackle, break-block,
  ball-contest, event-rate at `0x00147674`) — none of which enumerates a play
  pool.

So whatever "matchup memory" the offensive weighting uses (`ai-play-calling.md`),
**it is not the `ptrk` getters** — that structure must be found separately (it is
likely the 41-slot state's "current play ids" + a per-defensive-call yardage
memory). This is an open item, flagged for the runtime read.

---

## Q2 — Map the selection chain end to end

The chain has two clearly separable halves. The **DB/enumeration half** is fully
mapped statically (below). The **runtime weighting + roulette half** is a
separate investigation thread (see the parallel results integrated under Q4);
statically it is *not* reachable from the `ptrk` axis and is not an IABP
re-query per play.

### The playbook-AI DB and the `PBAI`/`AIGR` enumerator (Finding)

The AI play table is `PBAI`, stored in the DB engine with its 4-byte tag
**reversed** as `'IABP'`; the group column `AIGR` is stored as `'RGIA'`; the
play-id column is `'LPBP'`; the per-play weight/tendency column is `'tcrp'`.
Confirmed from the query-format strings:

```
0059A110  "use %s select count(*) into %s from 'IABP' where 'RGIA' = %s"
0059A150  "use %s declare %s cursor for select * from 'IABP' where 'RGIA' = %s"
0059A198  "fetch from %s 'LPBP' into %s"
005997C8  "use %s update 'IABP' set 'tcrp' = %s where 'RGIA' = %s and 'LPBP' = %s"
0059A280  "use %s declare %s cursor for select * from 'IABP'"   (unfiltered)
0059A2B8  "fetch from %s 'LPBP' into %s"
00599450  "fetch from %s 'LPBP' into %s and 'TSBP' into %s"
```

**These strings are referenced ONLY inside the playbook-AI DB module
(`0x002C0000`–`0x002C5000`)** (verified by hi/lo `lui`+`addiu` reference scan).
The single-group query pair (`0x0059A110` count / `0x0059A150` cursor) is
referenced **only** at `0x002C48A0` / `0x002C491C`, inside the function at
`0x002C4828`. That function loops over the **whole group-id list** and rewrites
each play's `tcrp` — it is a **playbook-load preprocessing pass**, not a per-play
enumerator:

```
002C4828  27BDFF30  addiu sp, sp, -208        ; fn entry
002C4864  0C0A4CAA  jal 0x002932a8            ; get current playbook DB handle
002C488C  24150011  addiu s5, zero, 17
002C4894  0062A80B  movn s5, v1, v0           ; s5 = 17 or 21 group-list length (movn!)
002C48C4  0C132E32  jal 0x004cb8c8            ; SQL exec: count(*) where RGIA=grp
002C4930  0C132E32  jal 0x004cb8c8            ; SQL exec: cursor where RGIA=grp
002C4970  0C0B083A  jal 0x002c20e8            ; -> UPDATE IABP set tcrp=...  (0x5997C8)
```

The two group-id lists it iterates are byte arrays in rodata:

```
005458E8  03 0D 01 22 05 11 04 07 06 23 16 14 0A 08 15 02 20 21 24 25 26   (21 groups)
00545900  05 16 01 06 12 13 07                                              (7 groups)
```

Sibling preprocessing passes: `0x002C4C60` (unfiltered `select * from IABP`,
fills a u16 stack buffer at `sp+16`, then rewrites `tcrp`); `0x002C1AD0` /
`0x002C1BB8` / `0x002C1C00` (fetch `LPBP`+`TSBP`, count consecutive equal
`TSBP`, update). The DB-query primitive throughout is
**`0x004cb8c8`** (variadic `printf`-into-SQL executor, DB context at
`0x00650FA8`); the current-playbook handle getter is **`0x002932a8`**
(→`0x002b0848`, returns loaded/not-loaded).

**Externally, this whole module is called only from the DB/franchise-management
region** (`0x0029xxxx`–`0x002Axxxx`, `0x002Fxxxx`) — never from the in-play AI
region (`0x0014xxxx`–`0x0020xxxx`) (verified by cross-module `jal` scan). So the
runtime caller does **not** re-run the `PBAI`/`AIGR` SQL each play; the per-group
play list + `tcrp` weights are **materialised at playbook-load** and the runtime
selector reads that in-memory structure.

**Consequence for the mission:** `ai-play-calling.md`'s "candidate enumerator
builds a three-table SQL join at runtime … one predicate `PBAI.AIGR ==
<group>`" is more precisely a **load-time** build; at runtime the pool is a
resident array keyed by group. The single-predicate/`AIGR` filter and the
`tcrp` weight it establishes are correct; the "each play" timing is load-time,
not snap-time. *(A runtime re-query using a variable table/column name
(`from %s where %s = %s`) cannot be excluded by string search alone — see
open items.)*

### The actual runtime selection path (Finding — this is the spine)

The offensive play is **not** selected in the in-play AI region (`0x0014`–`0x0020`)
at all. It is driven by the **situational bytecode VM** (Q5), whose "select play
from group" command lands in one small native function. The full runtime chain:

```
disc VM script  (picks the GROUP + situational policy; Q5)
  │  emits command 11 with the group operand in VM-context +82
  ▼
VM command handler  0x0024BB50  → cmd11 body 0x0024BC8C
  0024BC98  lh   a2, 82(s2)      ; a2 = group id (from context)
  0024BCAC  jal  0x00249498      ; <-- "AI select play from group"   [THE SEAM]
  0024BCB0  daddu s4, v0, zero   ; s4 = status
  ▼
0x00249498  (group→play stub, 8 instrs)
  00249498  andi a0, a0, 0xFF        ; a0 = side/team (0/1)
  0024949C  ori  v0, 0xAFBC          ; playbook-block stride
  002494A0  mult a0, a0, v0
  002494A8  lw   v1, 16512(gp)       ; playbook base @ gp[16512] = 0x00609770
  002494B0  addu v1, v1, a0          ; &playbook_block[side]
  002494B4  jal  0x002bff68          ; the DB-query selector (tail position)
  002494B8  lw   a0, 12(v1)          ; a0 = block+12 = this group's play-list handle
  ▼
0x002bff68  (build the IABP/RGIA/LPBP query, run it, set the play, return status)
  ; assembles the column/table tokens inline:
  002C0014  lui t1,0x4941 / ori 0x4250 ... → 'IABP','RGIA'   (I A B P / R G I A)
  002C0038  lui t4,0x4941 / ori 0x4250 ... → 'IABP','LPBP'   (I A B P / L P B P)
  002C0088  jal 0x004c7e38          ; the SQL prepare/step engine
  002C0090  xori v0, v0, 0x17
  002C0098  sltu v0, zero, v0       ; return = (status != 0x17)
```

So the "candidate enumerator (`PBAI.AIGR == group`)" of `ai-play-calling.md` is
**`0x002bff68`** at runtime: it selects `'LPBP'` from `'IABP'` where `'RGIA'` =
group, through the generic query engine **`0x004c7e38`** (prepare `0x004d6140`,
step `0x004d6430`/`0x004d6718`). The chosen play is set into the team playbook
block; the return is a success/fail status (`0x0024BC8C` propagates it, it does
**not** write context+84 — that write, `sh v0,84(s2)` at `0x0024BBA0`, belongs to
the *set-specific-play* commands cmd8/9, native `0x0024b100`).

**Where the weighting / roulette / matchup term live (Hypothesis — needs a live
read).** Neither the float-weight/RNG scan of the AI region nor the DB-string
scan found a separate 225-slot two-family renormalisation roulette in ELF code:
* `0x3F4CCCCD` (the 0.8 fallback ratio) has 129 rodata hits but **none** loaded
  via `lui`+`lwc1` into the `0x140000`–`0x220000` AI region (searched, not found);
* the only float roulette in `0x00145xxx` is the **5-option** halfword
  shift roulette `0x001453e0` + RNG `0x002f9428` — that is the *defensive shift*
  (Q1), not the play pool.

The remaining conclusion is that the tendency weighting (`tcrp`), the
class/family split, and the weighted-random pick are applied **inside the query
path** — either as bind parameters/opcodes handed to `0x004c7e38` (note the
integer opcodes 9, 11, 6, 3 and 0x10003 marshalled at `0x002BFFFC`–`0x002C0084`)
or in a post-query walk over the result set. Pinning the exact renorm/roulette
instructions is the one piece that a single live read (break in `0x002bff68`,
watch the result set + the set play) settles definitively.

---

## Q3 — The candidate-pool data structure

**Finding (record columns, from the DB schema):** each `PBAI`/`'IABP'` row the
enumerator cares about exposes at least:
* `'LPBP'` — the **play id** (fetched as the pool element),
* `'RGIA'` — the **AI group** (the sole filter predicate),
* `'tcrp'` — a **per-play weight / tendency count** (written by the load-time
  normaliser at `0x002C4828`; this is the weight the roulette later reads),
* `'TSBP'` — a secondary tendency/situation key (fetched alongside `LPBP` at
  `0x00599450`; used by the consecutive-run counter in `0x002C1AD0`).

**Finding (buffer shape as seen in the preprocessing pass `0x002C4C60`):** play
ids are gathered as **`u16` (halfword) elements into a contiguous stack buffer**
(`s2 = sp+16`, stride 2):

```
002C4C64  27BDFED0  addiu sp, sp, -304        ; 304-byte frame
002C4CCC  27B20010  addiu s2, sp, 16          ; buffer base
002C4CD0  00113840  sll a3, s1, 1            ; index*2  -> u16 slots
002C4CE0  0C132E32  jal 0x004cb8c8            ; fetch LPBP into buffer[i]
002C4CF4  0050880A  movz s1, v0, s0           ; advance count while fetch ok (movz!)
```

**Hypothesis (the "225-slot / no bound check" buffer):** `ai-play-calling.md`'s
225-slot roulette buffer is the **result set** of the runtime query in
`0x002bff68` (`select 'LPBP' from 'IABP' where 'RGIA' = group`), materialised by
the query engine `0x004c7e38` into the large stack frame of `0x002bff68`
(`addiu sp, sp, -208`) and/or the engine's own buffers. The "no bound check /
overflows the stack frame past 225 rows" landmine therefore lives on the
`0x002bff68` / `0x004c7e38` result path, not in the `0x002C` load-time module.
The load-time evidence says the per-play weight is the `tcrp` column; the runtime
result rows are `{playId ('LPBP'), weight ('tcrp')}`. *(Flagged: the 225 bound
and the exact row width are asserted by the prior doc; a live read of the
`0x002bff68` result set confirms them.)*

---

## Q4 — THE SEAM (the pool → selection boundary; the hook for a new selector)

**Finding — the single cleanest hook is `0x00249498`, the "AI select play from
group" native.** Its entire job is *group in → play set*, and it is called from
**exactly two sites, both VM command handlers** (`0x0024BCAC` = cmd11 of the
primary handler `0x0024BB50`; `0x0024BE58` = the same command in the secondary
handler `0x0024BD68`). Nothing else in the ELF calls it. Replacing it — or
retargeting those two `jal`s — bypasses the stock DB-query weighting/roulette
(`0x002bff68` → `0x004c7e38`) entirely.

**Live registers / pointers at the seam** (verified from the cmd11 body
`0x0024BC8C` and the stub `0x00249498`):

| reg | meaning at `jal 0x00249498` (`0x0024BCAC`) |
|---|---|
| `a0` = `s0` | **side / team index** (0 or 1) — indexes the per-team playbook block, stride **`0xAFBC`**, base `*(gp[16512])` = `*0x00609770` |
| `a1` = `s1` (masked, `~0xC0`) | a request-flag word (bit 0x40/0x80 semantics; a bit is folded into `s0` at `0x0024BC9C`) |
| `a2` = `lh 82(s2)` | **AI group id** (written into VM-context +82 by the op2 group-select opcode) |
| `s2` | **VM context object** (game-state view; +82 group, +84 current play id, +86 special-play slot) |
| return `v0` | status (`!= 0x17`); the *chosen play* is written into the team playbook block by `0x002bff68`, not returned |

**Recommended hook for the coach-brain cave:** retarget the two `jal 0x00249498`
sites (or overwrite the 8-instruction stub at `0x00249498`) to a new selector
that receives `{side a0, flag a1, group a2}`, evaluates the objective function
(situation from `s2`/the 41-slot state + opponent tendencies + own roster), and
**sets the team's current play** the same way `0x002bff68` does (write the chosen
`'LPBP'` id into the playbook block), returning a non-`0x17` status. Because
`0x00249498` is the choke point through which *all* scripted offensive selection
flows, one cave here supersedes the enumerator + `tcrp` weighting + roulette in a
single, independently testable boundary — exactly the replacement the campaign
wants. The group is still chosen by the disc script upstream (Q5); the *play
within the group* becomes the cave's decision.

*(If a later live read finds a non-scripted default selection path that does not
route through the VM, it would call `0x00249498` too — the two-caller closure
above says any such path is not in the static image.)*

---

## Q5 — The situational disc-script boundary (the bytecode VM)

**Finding — a single generic bytecode VM in the `0x0024Bxxx`–`0x0024Cxxx` module,
configured once with a disc-loaded script + a native command handler.**

**VM interpreter = `0x0024BFC0`.** Byte stream; **opcode = high nibble**, operand
= low nibble; bound-checked `< 8`; dispatched through an 8-entry jump table at
**`0x00586AC0`**:

```
0024C008  lbu   v1, 0(v0)        ; fetch bytecode byte
0024C014  srl   a0, v1, 4        ; opcode = high nibble
0024C018  sltiu v0, a0, 8        ; bound < 8
0024C020  andi  s3, v1, 0x000f   ; operand = low nibble
0024C02C  addiu v0, v0, 27328    ; table base 0x00586AC0
0024C034  lw    a0, 0(v1)
0024C038  jr    a0               ; dispatch
```
Handler op1 (`0x0024C0B0`) is the comparison opcode; **op2 (`0x0024C150`) is the
command-emit opcode** (calls the registered native handler via `jalr 52(sp)`).
*(Flags: `movz s7,v0,s6` @`0x0024C048/84`, `movn a2,s1,v0` @`0x0024C238`, several
`beql`/`bnel` branch-likely in the loop.)*

**Comparison-operator table = `0x00586AA0`** — 7 leaf comparators indexed by a
byte and `jalr`'d in op1: `0x0024BF70` EQ, `0x0024BF80` LT, `0x0024BF88` GT,
`0x0024BF90` LE, `0x0024BFA0` GE, `0x0024BFB0` NE, entry 7 = null.

**Native command handler (the ELF-patchable boundary) = `0x0024BB50`**, a 13-entry
command table at **`0x00586A20`**, `(a0 = command 0–12, a1 = context, a2 =
operand)`:
* **cmd11 body `0x0024BC8C` → `0x00249498` = "AI select play from group"** (the
  Q4 seam). The op2 opcode stores the group operand to context +82
  (`sh s1, 82(s4)`); cmd11 reads it (`lh a2, 82(s2)`) and calls the selector.
* **cmd8/cmd9 `0x0024BBEC`/`0x0024BC08` → `0x0024b100` = "set specific play"**
  (writes a 15-bit play id + 0x8000 flag; `sh v0, 84(s2)`).
* cmd6/7 write context +86 (255/254); cmd2/3/5/12 poke clock/huddle via
  `0x00163148` / `0x00163310` / `0x0017b638`.

**Script resource = disc asset id 69 (`0x45`), category 1.** Descriptor built at
`0x00247DB4`–`0x00247DF4`: `jal 0x0047f480 (a1=69, a2=1)` (asset loader; path
string `0x0056E628`, `0x0049bdf8`). Init **`0x0024C750`** (sole caller
`0x00247DF0`) copies `{script@+0, ctx@+4, handler@+8 = 0x0024BB50,
handler2@+12 = 0x0024BD68}` into the BSS VM-context global **`0x00618FD0`**.

**Script exec entry = `0x0024C7C8`** (primary; uses the +8 handler), with a
secondary `0x0024C930` (+12 handler). `0x0024C7C8` has ~12 callers — the
situational trigger points (`0x00160784, 0x00160EF4, 0x00161A40, 0x0016327C,
0x00173160, 0x00177400, 0x00259E84, 0x00278858, 0x002A0AF0, 0x002E9FCC,
0x002EA02C`).

**ELF-patchable vs authored data:** the situational *policy* (clock, 4th-down
go/no-go, 2-minute drill, which group to run) is **authored bytecode in disc
resource #69 — not in the ELF.** The ELF-patchable seam is the command handler
`0x0024BB50`, specifically **cmd11 → `0x00249498`** (select-from-group; the Q4
hook) and cmd8/9 → `0x0024b100` (set-specific-play). Patching those natives (or
the exec entry `0x0024C7C8`) lets ELF code override what the authored script
selects without touching disc data — but changing the *policy* (e.g. new
4th-down aggressiveness) means editing the disc script, which is outside the ELF.

---

## What is firmly resolved vs what needs a live read

* **Q1 — RESOLVED (static):** `0x00145940` (which contains the branch target
  `0x001459B4`) = pre-snap defensive line/LB **shift** picker (5-option halfword
  roulette `0x001453e0` + RNG `0x002f9428`). It reads `ptrk` for the *opponent*
  to bias the shift. The offensive play caller is a different chain (Q2/Q4) and
  does **not** read the `ptrk` getters.
* **Q2 — RESOLVED (static):** the runtime spine is
  `VM cmd11 (0x0024BC8C)` → **`0x00249498`** (group→play stub) → **`0x002bff68`**
  (build+run `select 'LPBP' from 'IABP' where 'RGIA' = group`) → query engine
  **`0x004c7e38`**. The `tcrp` weight column is written at playbook load by the
  `0x002C0000`–`0x002C5000` DB module (normaliser `0x002C4828`, group lists
  `0x005458E8`/`0x00545900`, primitive `0x004cb8c8`).
* **Q3 — schema RESOLVED** (`'LPBP'` play id, `'RGIA'` group, `'tcrp'` weight,
  `'TSBP'` tendency key); the **225-slot buffer is the `0x002bff68`/`0x004c7e38`
  query result set** (Hypothesis for the exact width/bound — a live read of that
  frame confirms it).
* **Q4 — RESOLVED (static):** the seam is **`0x00249498`**, called only from
  `0x0024BCAC` and `0x0024BE58` (both VM handlers). Live regs: `a0`=side(0/1),
  `a1`=flag, `a2`=group, `s2`=VM context; play is *set* (not returned).
* **Q5 — RESOLVED (static):** VM interpreter `0x0024BFC0` (opcode = high nibble,
  jump table `0x00586AC0`), comparator table `0x00586AA0`, native handler
  `0x0024BB50` (cmd table `0x00586A20`), script = disc asset #69, exec entry
  `0x0024C7C8`. Situational policy is authored disc data, not ELF.

**The one thing a live read settles:** where inside `0x002bff68` → `0x004c7e38`
the `tcrp` weighting, the two-family class renorm (fallback `0x3F4CCCCD` = 0.8),
and the weighted-random pick actually execute — none of those instructions were
found in the ELF AI region by either the float/RNG scan or the DB-string scan, so
they live on the query path. Break in `0x002bff68`, watch the result set and the
play written into the team playbook block: that confirms the buffer bound and
pins the renorm/roulette. **Recommended seam hook for the new selector:
`0x00249498`** (retarget the two `jal`s or overwrite the stub).

# Anim lane 1: the two-man block animation dispatcher, end to end

Recorded 2026-08-11. Static lane, maximum effort, against `extract/SLUS_207.52`
(vaddr = file offset + 0xFF000, gp = 0x006056F0) via `recon.mipsdis`, plus one
offline savestate walk (`tools/statereader.py` over
`experiments/states/double_team_slot9.p2s`). No rig, no network, no emulator,
no commits. Every quoted instruction was dumped from the ELF this pass; every
data table was read as words/halfwords from the file; the live-memory claims
are from the slot-9 savestate. Claims not closed are marked **UNVERIFIED** in
place and collected in §9.

Mission context: Route C is confirmed by elimination
(`docs/on-skates-requirements.md`) — during a block the animation's root
motion owns both bodies, so the fix is **animation selection**. This lane maps
who selects, from what vocabulary, on which inputs, and where to patch.

## VERDICT (the one-paragraph version)

The engine already contains a complete margin-driven outcome selector for
blocks — three of them, in fact: two pre-contact converters that roll against
a ratings+weight "pancake pool" (`+0x41C`) to start winning/losing clips
(ids 149/146, 168/173), and a per-segment outcome dispatcher for held kind-4
pairs (`0x001f1980`) that picks the next clip from a 6×5 grid of continuation
tables keyed on *who is winning* (`+0x414`/`+0x418` pools) and *drive
magnitude* (`+0x404`) — blocker-winning cells cycle the drive clips
{50, 53, 54, 58}. The reason none of this produces "on skates" on the play
that matters: at the handoff (mode 4/7) the **capture service `0x001f7c98`**
converts every contacted pair into AI state 32 with a **hardcoded clip id 158**
(`addiu s3, zero, 158` at `0x001f7d08`), and state 32's segment-end path calls
the capture service again — so every subsequent segment is 158 forever. The
margin machinery is orphaned at exactly the moment the block is won. One word
(158 → a yes-set driving id) or one small cave (margin-conditional id at the
request store `0x001f7d5c`) reconnects it; because the driving candidates
{149, 150} are **in** the yes-set {146-151, 168-170, 173}, the same change
also fixes the record-dies-at-capture problem from
`docs/dt-lanes/run-pass-contrast.md`.

---

## 1. The call chain, end to end

### 1.1 Per-frame block manager `0x001f7298` (runs at mode `0x00154790()` ∈ {3,4})

Quoted this pass; slots in execution order:

```
001f72c8  jal 0x001f5b60      ; tick (timers, T+84 play counter)
001f72d0  jal 0x001f5590      ; market wrapper (chooser 0x001f4790 etc.)
001f72d8  jal 0x001f6d10      ; double-team registry (seek/manage/drive)
001f72e0  jal 0x001ef820      ; Site A kind machine
001f72e8  jal 0x001ef338      ; id-161 engage starter (ball-bearing variant)
001f72f0  jal 0x001f0ba0      ; per-frame pass at the yes-set predicate's site
001f72f8  jal 0x001efc00      ; two-man starter, ids 79/25
001f7300  jal 0x0039db50      ; frame counter ->
001f7308  andi v1, v0, 1      ;   parity switch:
001f7324  jal 0x001f00d8      ;   EVEN frames: pre-contact converter A (146-151)
001f7334  jal 0x001f06a0      ;   ODD frames:  pre-contact converter B (168-173)
001f733c  jal 0x001f20f8      ; Site B (kind-8 mechanics)
001f7344  jal 0x001f1c20      ; kind-4 SEGMENT CYCLE (every frame)
001f7350  j   0x001f5e80      ; scoring phase (tail call)
```

### 1.2 The capture path (the one that owns the observed play)

All four blocking-state thinks call the capture service every think tick
(callers re-verified: `0x001b68ec` state 47, `0x001cb0c0` state 31,
`0x001dc45c` state 33, `0x001e81f4` state 32):

```
0x001f7c98 capture service
  001f7cbc/ccc  mode 0x00154790() must be 4 or 7
  001f7ce0      0x00260598(): possessing side only
  001f7cf8      byte[player+0] == 1
  001f7d00  8e240304  lw a0, 772(s1)        ; +0x304 anim context
  001f7d04  0c0eb504  jal 0x003ad410        ; current interaction id
  001f7d08  2413009e  addiu s3, zero, 158   ; <<< THE SELECTION. Hardcoded.
  001f7d0c  10530025  beq v0, s3, ...       ; already playing 158 -> return 0
  001f7d14      kind must be 4 (or 6)
  001f7d28      s0 = resolve(+0x3E4)        ; the defender
  001f7d38      jal 0x001f1be0(p, def)      ; defender-state eligibility (table 0x005833D0)
  001f7d48  0c063a44  jal 0x0018e910        ; init 72-byte request on the stack
  001f7d50  c7809ae8  lwc1 f0, -25880(gp)   ; [0x005ff1d8] = 1.4  (loosest in the image)
  001f7d54/58   v0 = 0x000E38E3             ; 20 deg (BAM24)
  001f7d5c  a7b30040  sh s3, 64(sp)         ; request+0x40 := 158
  001f7d60  e7a00038  swc1 f0, 56(sp)       ; request+0x38 := 1.4 (slot position tolerance)
  001f7d68  afa20034  sw v0, 52(sp)         ; request+0x34 := 20 deg
  001f7d6c/74   request+0 := blocker, +4 := defender
  001f7d70  0c063992  jal 0x0018e648        ; DISPATCH
  001f7d88/98   on success: kind 5 on both (0x001f7398 / 0x001f74c8, a2=5)
```

### 1.3 The dispatcher core (shared by every starter in the image)

```
0x0018e648(request)
  0018e66c  jal 0x00240050(2,0,participant0,0)   ; nonzero -> widen req+0x34 and
  0018e690/94   ... *1.5                          ;   req+0x38 by 1.5x  [UNVERIFIED
                                                  ;   meaning of 0x00240050; reads as
                                                  ;   "user-controlled/assist"]
  0018e6a4  jal 0x0018d3c0(req, 1)               ; participant validation: fn ptr
                                                  ;   [0x00601140+4] over req+0 and the
                                                  ;   12-slot array req+4..+0x2C
  0018e6b8  jal 0x0018de38(req, out, &out_id)    ; RESOLVE -> record + chosen id
  0018e6c4  lhu a2, 48(sp)                        ; the resolved id (halfword)
  0018e6d0  jal 0x0018e378(req, record, id)      ; START
```

`0x0018de38` gates: distance(p0,p1) < req+0x3C (default 4.0); angdiff(defender
`+0x1A8`, bearing p0→p1) < req+0x30 (default 60°) — else defender must be
engaged; then `0x0018d998` does the real matching: it looks the id up in the
**player's animation-set group** (`[player+0x308]+2` = group id; slot-9: all
22 players are group 1), enumerates variants, checks per-variant filter bytes
(req+0x42 = variant kind byte, req+0x43 = variant index, 255 = any), resolves
each variant's **participant-slot table** (20-byte records) and matches the
participants against it with position tolerance req+0x38 and the angles in
req+0x30/+0x34.

`0x0018e378` then writes `player+0x3DC/0x3DD` := pairdef record hw0 and
`player+0x3DE/0x3DF` := id (both men), and **pushes both players into AI
state 32** via a state message {byte0=32, id_hi, id_lo, partner byte}
(`0x001afb50`, or `0x001b0170` if already in 32).

### 1.4 State 32: enter, advance, and the 158 loop

Descriptor `0x00527538`: enter `0x001e7ee0`, think `0x001e8088`, exit
`0x001e8318`.

Enter (`0x001e7ee0`, quoted): reads the id back out of the state message and
starts the clip at **weight 1.0** — `0x003acdf8(ctx+0x304, ctx+0x308,
ctx+0x30C, id, f12=1.0)` — stores the partner handle at `+0x150`, and derives
a segment length into `+0x154` (scan of master id list `0x00526A10` through
`0x003a8cd0`, then the 124-byte per-anim table at `[player+0x30C]`, entry
`+0x4C`, queried by `0x003ac570/0x003ac368` with tag 0xc004).

Think (`0x001e8088`, quoted): registers **mutual no-collide** (`0x00213628`
both directions) while both are in state 32 and point at each other; on the
segment-end flag (`player+0xC` bit 2):

```
001e81dc/ec   kind 5 -> kind 6
001e81f4  0c07df26  jal 0x001f7c98    ; RE-CAPTURE: the next segment is chosen by
                                      ;   the same hardcoded-158 service
001e8204/28   on failure: exit path — speed_cmd +0x1E8 := 0.46 [0x005fef3c],
              +0x1EC/+0x1F0 := +0x1A8, +0x1F4 := 1
```

**This is the "staircase of 14-30-frame frozen steps": every step is 158,
re-requested at segment end, until a re-request fails and the pair exits at
0.46 speed.** No margin, no outcome, no drive ever enters this loop.

---

## 2. The selectors that DO choose by margin (and who orphans them)

### 2.1 The contest pools: `+0x414` / `+0x418` / `+0x41C`, written by `0x001f0c40`

`0x001f0c40(blocker, defender)` stamps three floats on each man's engagement
block (s1 = blocker+0x404; ratings at `+0xB70+2*attr`, weight at `+0xAEC`;
BLK = PPBK (attr 11, `+0xB86`) if `+0x3F0`==1 else PRBK (attr 12, `+0xB88`)):

```
+0x414 := (BLK + STR)            + weight + rand(k1 * base)     ; "comp1"
+0x418 := (BLK + AWR/2 + STR/2 + attr1)   + rand(k2 * base)     ; "comp2"
+0x41C := (BLK + AWR + attr1 + STR) + weight + rand(k3 * base)  ; the PANCAKE POOL
```

(quoted at `0x001f0ca0-0x001f0e34`; attr 2 = AWR per the fourcc order, attr 15
= STR; attr1's fourcc not re-checked here — **UNVERIFIED label**, likely AGI.
k1..k3 = `[0x005ff0dc]`, `[0x005ff0e0]`, `[0x005ff0e4]`.) The defender gets the
mirror treatment with STR + attr16 (TAK) + weight. Then `0x001ef0c8(p, pools)`
post-processes — not dumped, so any slider/difficulty folding lives there or
in the un-dumped tail: **whether sliders scale these pools is UNVERIFIED in
this lane** (lane 0's note calls +0x41C slider-scaled; not contradicted, just
not re-derived here).

Weight is therefore ALREADY in the engine's own who-wins-the-block numbers —
the operator's weight+STR law needs only to re-shape these pools or their
consumers, not a new pathway.

### 2.2 Even-frame pre-contact converter `0x001f00d8` (kinds 2/3 → clip, ids 146-151)

Gates per offensive player (all quoted): kind ∈ {2,3}; partner resolved;
either man moving (`+0x1AC` speed > 0.026 `[0x005ff0b0]`); defender-state
eligibility `0x001f1be0`; defender's desired-bearing `+0x1EC` within 45°
(pass) / 90° (run) of the bearing — or his speed_cmd < 0.46 `[0x005ff0b4]`;
"aligned" flag := angdiff(defender `+0x1EC`, bearing) ≤ 15°; blocker facing
within 120° of the reverse bearing; dist² < 6.25 (2.5 yd). Then it **builds a
preference chain of ids from the yes-set data list at `0x00526B80`** (layout
§4.3) and tries them in order through the dispatcher (req+0x34 = 20°,
req+0x38 = 0.4):

| priority | id | condition (play counter ≥ 46 for the two rolls) |
|---|---|---|
| 1a | **149** | blocker `+0x41C` > defender's, blocker moving (>0.0354): margin% = (B−D)/B×100 (user: flat 50); `rand(150) < margin%` (roll at `0x001f0368-74`) |
| 1b | **146** | defender `+0x41C` > blocker's, defender moving, defender facing >120° from reverse bearing; margin% mirrored; play-class 3 (pass) halves it ≫4; roll at `0x001f0414-20` |
| 2a | **150** | blocker `+0x414` > defender's AND blocker `+0x404` (staged drive) > 0.16 `[0x005ff0c0]` AND not pass block |
| 2b | **151** | (same margin arm) blocker faster (`+0x1AC`) |
| 2c | **148** | (same margin arm) aligned flag |
| 3a | **147** | defender `+0x414` ≥ blocker's AND defender `+0x404` > 0.16 `[0x005ff0c4]` AND aligned |
| 3b | **151** | (defender arm) defender faster |
| 3c | **148** | (defender arm) aligned |

On success: ids **{149, 146} start kinds 5/5** (fresh two-man animation);
every other id starts kinds **6/6** (`0x001f05f8-0x001f0638`).

### 2.3 Odd-frame pre-contact converter `0x001f06a0` (kinds 2/3 → clip, ids 168-173)

Same shape, play counter ≥ 31, dist² < 12.25 (3.5 yd), speed gate
`+0x1AC × [0x005ff0cc] ≥ [0x005ff0d0]`; fp := angdiff of the two men's
facings (`+0x1A8`):

| priority | id | condition (counter ≥ 46 for the rolls) |
|---|---|---|
| 1a | **168** | blocker `+0x41C` > defender's: margin = int(B−D)>>1 (user: 50); roll `rand(150)` at `0x001f08d8`; then fp > 85° — or fp ≤ 85° AND ball-carrier within 15.0 AND `0x002f93b0(...) < [0x005ff0d4]` (sub-case partially **UNVERIFIED**) |
| 1b | **173** | defender `+0x41C` > blocker's: margin = int(D−B)>>1 (class-3 ≫5); fp > 85° required; roll at `0x001f099c` |
| 2a | **169** | blocker `+0x414` ≥ defender's (or user) |
| 2b | **170** | aligned flag |
| 3 | **169** | defender inside the ball x±6.0 / behind-reference band — pushed with its angle tolerance doubled at try time (`0x001f0adc-0x001f0afc`) |

All successes stamp kinds **5/5**.

**Together the two converters' vocabulary is exactly the yes-set
{146-151, 168-170, 173} of table `0x00583360`.** The yes-set is not an
arbitrary whitelist — it is "the clips the pre-contact margin converters can
legally start", which is why the attach gate and the registry manage arm
tolerate precisely those ids.

### 2.4 ADJUDICATION for lane 2 (mass-law): push vs test, and whether the selector reads +0x41C

Lane 2 reports `0x001e7d54`'s `168` is a current-animation TEST. **Correct —
and it is a different site from this lane's selector finding.** Both, quoted:

The test (state-32 region, fn `0x001e7cf8`, sole caller `0x001e7e70` inside
the think's per-tick helper `0x001e7df8`; possessing side only):

```
001e7d24  jal 0x003ad410            ; current interaction id
001e7d2c  24030038  addiu v1, zero, 56    ; == 56 ?  ------+
001e7d40  24030095  addiu v1, zero, 149   ; == 149 ?      | any -> rand(100) < 76
001e7d54  240300a8  addiu v1, zero, 168   ; == 168 ?  ----+   -> 0x0015e650(38) < 30
001e7d78  jal 0x0015e650 / a0=38          ;   -> return 1
```

— a *predicate*, "is the pair playing 56/149/168", feeding an event-38 cap
check (see §4 item 2a: this is new evidence, not a refutation).

The selector push (fn `0x001f06a0`, and the mirror sites in `0x001f00d8`) is
a **load of the id word from the data list `0x00526B80` and a store into the
local candidate chain** that the request walker then tries:

```
001f094c  8c830024  lw v1, 36(a0)     ; v1 := [0x00526B80+0x24] = 168
001f095c  ac430000  sw v1, 0(v0)      ; chain[s4] := 168      <- a STORE, not a compare
...walker:
001f0b24  94430000  lhu v1, 0(v0)     ; next candidate id from the chain
001f0b2c  a7a30040  sh v1, 64(sp)     ; request+0x40 := id
001f0b28  jal 0x0018e648              ; try it
```

**The selector IS margin-driven, and it reads `+0x41C` on BOTH men** — the
load-bearing instructions:

```
even 0x001f00d8:  001f0304  c642003c  lwc1 f2, 60(s2)   ; blocker+0x41C
                  001f0308  c463003c  lwc1 f3, 60(v1)   ; defender+0x41C
                  001f0348/54/5c     margin% = (f2-f3)/f2 * 100 -> rand(150) roll
odd  0x001f06a0:  001f08a4  c460003c  lwc1 f0, 60(v1)   ; blocker+0x41C
                  001f08a8  c48c003c  lwc1 f12, 60(a0)  ; defender+0x41C
                  001f08c8/d0        margin = int(f0-f12)>>1 -> rand(150) roll
```

**Trap confirmed for lane 2's verdict (b):** every margin here is A−B over
the two men's own copies. If a cave stores its drive value D identically to
both men (as `+0x404` is stored by the confluence) and the selector's reads
are redirected there, **the margin is identically zero and the rolls never
fire**. A redirected read must take ONE side's copy, or better: inject into
the per-man stamp `0x001f0c40` (sole caller `0x001f153c`, inside
`0x001f14d0`, which is called by BOTH converters and the kind-4 cycle at
`0x001f02e4`/`0x001f0880`/`0x001f1cc4`) — i.e. the pools are **re-stamped
with fresh 0.33-jitter on every selection attempt**, so a formula change
there reaches every roll with per-man values and no identical-copy problem.

### 2.4b The kind-4 segment cycle `0x001f1c20` + outcome dispatcher `0x001f1980`

Runs every manager frame for pairs in **raw kind 4** whose one-shot byte
`+0x42E` is set (consumed at `0x001f1cb8`; armed by the engagement setters —
zeroed by them too; exact arming writer **UNVERIFIED**). Order:

1. `0x001f1720(blocker, defender)`: request id **150** while play counter <
   60, else **56** (`0x001f17f0-0x001f1804`), tolerance 20°/0.4
   `[0x005ff124]`; on success `0x0014fe20(defender, 6)` and a
   block-rating-scaled timer `0x0025e628(blocker, 1.0 − BLK×[1/255])`.
2. else `0x001f1980(blocker, defender)` returns a **continuation table**, and
   the walker at `0x001f1d04` tries that table's 12-byte entries
   {hw id, float tol, word angle} — skipping the CURRENT id (kept at
   `+0x3FC`, with its {angle, tol} at `+0x3F4/+0x3F8`) — through
   `0x0018e768`; on success both men's `+0x3F4..+0x3FF` segment records are
   updated and mutual no-collide is re-registered. **No kind write: the pair
   stays kind 4 while playing these segments.**
3. else `0x001f1eb0` re-requests the current id (a refresh), eligibility
   permitting.

`0x001f1980` is the per-segment outcome dispatcher (fully quoted this pass):

* facing class a3: angdiff(facings) > 145° → 0 (opposed); else signed
  difference picks 1 or 2 (turned left / turned right);
* s4 = pass block (`+0x3F0`==1) ? 1 : 0; drive threshold f20 =
  `[0x005ff130]` (pass) / `[0x005ff134]` (run);
* column, in priority order:
  * blocker `+0x414` > defender's (or user): **col 1** if blocker `+0x404` >
    f20 (users always col 1); else **col 0**;
  * defender side: defender `+0x404` > f20: **col 2** if defender `+0x418` >
    blocker's, else col 3/col 4 split by which side the defender's staged
    bearing `+0x40C` lies relative to his facing;
  * defender `+0x404` ≤ f20: col 3/col 4 (same split) if defender `+0x418` >
    blocker's, else **col 0**;
* table = word at `0x00526F90 + a3*20 + s4*60 + col*4`.

The grid and every table it points to, decoded from the file:

```
                 col0 STALEMATE   col1 BLOCKER-WIN   col2 DEFENDER-WIN   col3          col4
run  opposed     {49}             {50}               {51}                {60,49}       {59,49}
run  turned-L    {52,49}          {53,50}            {106,51}            {60,49}       {106,59,52,55,49}
run  turned-R    {55,49}          {54,50}            {107,51}            {107,60,49}   {59,49}
pass opposed     {57,49}          {58,49}            {108}               {60,49}       {59,49}
pass turned-L    {52,57,49}       {53,106,58,49}     {106,108}           {60,49}       {106,59,52,55,49}
pass turned-R    {55,57,49}       {54,107,58,49}     {107,108}           {107,60,49}   {59,49}
```

(tables at `0x00526BA8..0x00526F88`, 12-byte entries, 0xFFFF-terminated;
tolerances 0.4-0.5, angles 25°-55°.)

**Why the observed play never shows any of this driving:** the capture gate
requires mode ∈ {4,7} — on a run that arrives at the handoff
(run-pass-contrast §4, mode reading UNVERIFIED there and unchanged here). The
moment it opens, the state thinks capture the kind-4 pair into 158/state 32,
the pair stops being kind 4, and passes 2.2-2.4 never see it again. The
margin-driven cycle gets, at most, the pre-handoff frames 18..36 measured on
slot 9.

---

## 3. The clip vocabulary (id space, tables, and index derivation)

### 3.1 Id space and registry

Interaction ids are halfwords, requested via request+0x40. Resolution is
data-driven, per player animation-set group:

* `[player+0x308]+2` = the player's set-group id (slot-9 savestate: **1** for
  all 22 players).
* Global set registry: count at `[0x00608098]` (slot-9: 81), 28-byte
  descriptors at `[0x006080B0]` — {+0xC set object, +0x12 flag(1=live),
  +0x14 group id, 0x8000/0xFFFF = empty}. Group-1 sets in the state:
  {1, 2, 3, 12-19}.
* Set object `+0x8` → directory {word count; 16-byte entries: hw id at +0,
  hw at +2, variant list ptr at +8, pairdef ptr at +12}. Lookup by linear
  scan (`0x003a9328`).
* Variant list: {hw count at +0; 8-byte records at +4: hw pairdef-id (+4·8k),
  word data-ptr (+8·8k)} — built into the scratch global `0x00627418` by
  `0x003ad530` → `0x003aa630` for consumers. Each id in the savestate carries
  a param record + **two mirrored variants** (left/right pairdef ids, e.g.
  149 = a1c2/a1c3).
* Pairdef (participant-slot table, resolved by `0x003aa790` → `0x003a9488` =
  dir entry +12): {hw count at +4; 20-byte slot records at +8: flag byte at
  +2 (0 = required — counted by `0x0018d5f8`), role byte at +16 (matched by
  the `0x0018d548` scanner)}.

Savestate walk result (all of these ids resolve in slot-9 memory, each with
2 mirrored variants): 25, 49, 50, 51, 56, 57, 58, 66, 79, 106, 107, 108, 120,
124, 131, 146-151, 158, 161, 168, 169, 170, 173. Sets: 49-108 family in set
15, 118-131 in set 17, 146-151/168-173 in set 18, 158/161 in set 19, 25/79 in
set 1.

### 3.2 The master list and the family map

`0x00526A10` (4-byte stride {hw id, 0}, zero-terminated, 49 entries — scanned
by state-32's enter): 108, 64, 55, 61, 49, 56, 50, 66, 59, 65, 107, 106, 63,
62, 57, 52, 51, 60, 58, 54, 53, 25, 79, 150, 149, 148, 151, 147, 146, 123,
120, 129, 126, 121, 119, 125, 118, 131, 130, 127, 128, 124, 122, 161, 158,
169, 170, 168, 173. This is the engine's own census of every block-relevant
interaction id.

| family | ids | started by | kinds | role |
|---|---|---|---|---|
| kind-4 cycle segments | 49-60, 106-108 (+56, 66) | `0x001f1720` / grid tables / `0x001f1eb0` | stays 4 | per-segment hold/drive/lose loop (§2.4b). 49 = universal stalemate; **50/53/54 = run blocker-winning; 58 = pass blocker-winning**; 51/106/107/108 = defender-winning (106/107 = corner turned L/R, 108 = pocket collapse); 52/55/57 = angled/pass stalemates; 59/60 = col3/col4 transitions |
| engage starters | 61, 130, 147 (fn near `0x001f1880`); 150/56 (`0x001f1720`); 161 (two fns, 45°-sector variant bytes {4,7,10,14,16,17,18} via jump tables `0x00583300`/`0x00583320`); 79/25 (`0x001efc00`); 66 (`0x001b27d0` region) | various | 5/5, 6/6, 5/6 | contact-moment animations |
| pre-contact converter set = **the yes-set** | 146-151, 168-170, 173 | `0x001f00d8` / `0x001f06a0` | 5/5 ({149,146} and all odd-frame) else 6/6 | margin-selected engage-in-motion clips (§2.2/2.3) |
| capture/hold | **158** | `0x001f7c98` only | 5→6 | the neutral two-man hold loop; hardcoded |
| shed-contest outcomes | win {62,63,64,65,118,119,122,123,126,127,130}; lose {120,121,124,125,128,129,131} | shed starter (fn at `0x001a65f0`-ish walking 0xFFFF-terminated chains; `0x001a7070` picks the chain: base `0x00526668` run / `0x00526710` pass, stride 24 per move type 0-6, win chain at +0, lose at +12) | 5 and 6 (one each) | defender-shed vs **defender-driven-back/pancaked** (labels per `docs/cpu-dt-animations.md`, bases re-verified here) |
| tackle families | 157, 160-165, 343, 345-362 | `0x00187xxx` sites, tables at `0x00526800`/`0x005268E0` | — | out of block scope; listed to bound the space |

### 3.3 Root-motion representation

Located but not decoded: each variant's data pointer (variant record +8) and
the per-anim 124-byte entries at `[player+0x30C]` (entry+0x4C → clip object,
queried with tag **0xc004** by `0x003ac570`/`0x003ac368`/`0x003ac338` — the
enter uses this to derive the segment length at `+0x154`). The displacement
curves live in those clip objects (loaded assets; no name strings exist for
interaction ids anywhere in the ELF — checked). **Which ids carry backward
root translation is therefore UNVERIFIED at the data level**; the drive
labels below rest on selector semantics, which are strong but indirect.

---

## 4. WHICH IDS DRIVE — candidates, with evidence

Ranked by evidence strength:

1. **50 (run, opposed), 53/54 (run, turned L/R), 58 (pass)** — the grid's
   blocker-winning columns (§2.4b). Selected precisely when blocker `+0x414` >
   defender's AND staged drive `+0x404` > threshold. The engine's own label
   for these cells is "blocker winning with drive present"; they cycle for as
   long as the pair holds kind 4 and the margin holds — this is *sustained*
   drive by construction, i.e. the exact "on skates" shape. Mirror lose ids:
   51/106/107/108.
2. **149** (even converter) and **168** (odd converter) — started on a
   `rand(150) < margin%` roll against the **pancake pool** `+0x41C`
   (BLK+AWR+attr1+STR+weight). 149/146 are the only ids the even converter
   starts as kinds 5/5 (a fresh scripted two-man take-over), consistent with
   a decisive winning clip (pancake / de-cleat). Mirrors: **146** (defender
   beats blocker; pass-damped ≫4) and **173**.
   * **2a. Independent corroboration (found via lane 2's flag):** state 32's
     own per-tick helper (`0x001e7cf8`, §2.4) tests `current clip ∈
     {56, 149, 168}` for possessing-side players and, on a 76% roll capped by
     an event-count check (`0x0015e650(38) < 30`), triggers the emission of
     an **event record type 38 stamped with the player's x/y**
     (`0x001e7da0`: `sh 38, 20(s0)` + position floats into a record from
     `0x0015e5c8`). The engine singles out exactly {56, 149, 168} — the two
     blocker-margin clips plus the late contact starter — as the clips worth
     recording a located event for. A "Pancake" stat label exists in the ELF;
     event 38 = the pancake/knockdown marker is the natural reading
     (**UNVERIFIED** — the `0x0015e4e8` event system was not traced to the
     stat).
3. **Shed-lose set {120, 121, 124, 125, 128, 129, 131}** — "driven back /
   pancaked" per `docs/cpu-dt-animations.md` (its table bases and the win/lose
   chain structure re-verified from the file this pass, including chains the
   doc missed: pass-side win chains {118,62}, {119,63}, {126,64}, {127,65}).
   These are per-move-type outcome clips of the shed contest, defender losing
   → blocker driving/flattening him.
4. **150** — pushed when blocker staged-drive `+0x404` > 0.16 on run block,
   and the id `0x001f1720` requests at contact in the first 60 frames.
   Reads as "engage with drive".
5. **147** — defender-drive mirror of 150 (defender `+0x404` > 0.16).

The operator's 256x pancake observation is consistent with (2) or (3): both
are margin/score-gated and 256x buffs blew the margins out.

## 5. Where selection can be patched (ranked options, exact addresses)

**P-A. One word: retarget the capture id.** `0x001f7d08`:
`2413009E` (`addiu s3, zero, 158`) → `24130096` (150) or `24130095` (149).
s3 feeds both the already-playing check (`0x001f7d0c`) and the request store
(`0x001f7d5c`), so the change is self-consistent (re-request loop then cycles
the new id exactly as it cycled 158). Because 149/150 are in the yes-set:
`0x001f0ba8` passes during the whole animation → the 7→8 attach gate's
fallback arm opens and manage's primary-5/6 whitelist arm passes — **§8 of
run-pass-contrast's "shut PERMANENTLY" becomes "open for the duration"; one
word addresses drive and persistence together.** Risks: (i) 158's variants
may be the most permissive (the capture's 1.4 slot tolerance suggests the
data was authored for it); if the new id fails to match, capture returns 0
and the pair stays kind 4 — which keeps the margin-driven §2.4b cycle running
(likely an improvement, but the visual is UNVERIFIED); (ii) blast radius is
every capture on every play (pass sets included when mode permits) — no
margin gate. Use as the diagnostic probe: it is the cheapest way to see a
driving clip on demand.

**P-B. RECOMMENDED: margin-conditional capture id (small cave).** Hook
`0x001f7d5c` (`a7b30040 sh s3, 64(sp)`) → `j cave`. Register state at the
hook (from the quoted listing): s1 = blocker, s0 = defender, s3 = 158,
**v0 holds 0x000E38E3 and f0 holds 1.4 — both still needed by the fall-through
stores at `0x001f7d60/68`, so the cave must not clobber v0 or f0**. Free: at,
v1, a1, a2, a3, t0-t9, f1-f3. Cave body (~13 words):

```
lwc1 f1, 0x41C(s1)        ; blocker pancake pool
lwc1 f2, 0x41C(s0)        ; defender pancake pool
lui  at, 0x3FA0           ; 1.25f  (dominance factor; tuning)
mtc1 at, f3
mul.s f2, f2, f3
c.lt.s f2, f1             ; blocker pool > 1.25 * defender pool ?
bc1f keep
nop
addiu s3, zero, 149       ; dominant -> the pancake-pool clip (or 150)
keep:
sh   s3, 64(sp)           ; displaced store
j    0x001f7d60
nop
```

Dominant pairs capture into a driving clip; even pairs keep 158 — R5
protection by construction, same margin variable the engine itself rolls on.
A doubled defender is the dominant case once R6 keeps the helper attached
(and S4-D's helper-in-contact test can later scale the factor). Cave: **#11
is full** (see census below) — use **cave #7 `0x00443270`** (480 B, no pnach
touches it, ELF content is the original dead byte-swap family) after its own
runtime liveness test (code-caves.md test 1), or the free tail of #11
(3 words at `0x00514974..0x0051497C`) is too small for this.

**P-C. Data word: admit 158 to the yes-set** — `0x00583390`:
`001F0C24` → `001F0C20` (from `docs/double-team-solution.md`, not re-verified
at the byte level this pass — **UNVERIFIED offsets**). Fixes persistence
only; no motion change. Complementary to P-B; subsumed by P-A/P-B if the new
id is yes-set.

**P-D. Pre-contact roll tuning (one word each, no cave):** the four
`addiu a1, zero, 150` rand ranges at `0x001f036c`, `0x001f0418`,
`0x001f08dc`, `0x001f09a0` (the even/odd converters' 149/146/168/173 rolls).
Lowering 150 → 75 makes a 50% pool margin a certainty instead of a coin
flip. Margin-gated by construction; affects only kinds-2/3 conversions with
play counter ≥ 46.

**P-E. Outcome-grid data edits (pointer swaps at `0x00526F90+`):** e.g. run
opposed col0 `0x00526BA8`({49}) → `0x00526BE8`({50}) would make every held
run block cycle the drive segment — too blunt alone (R5), but the grid is
the natural tuning surface once P-B reconnects the capture to the margin,
and cell pointers are single-word data patches.

**Cave census (re-derived from `patches/*.pnach` this pass):** cave #11
`0x00514920` is fully claimed by `14F8B841.dt-market-guard-p1.pnach`
(21 words, `0x00514920-0x00514970`; the three skates/velocity diagnostic
pnaches claim the same words — mutually exclusive arms, only one loadable at
a time); 3 words free at `0x00514974-0x0051497C`. Cave #7 `0x00443270`:
unclaimed by any pnach; ELF holds the original dead code (119/120 nonzero
words). **Cave #1 `0x00139A68` must NOT be used**: the P1 pnach's own header
records that the region is materialised into fp/s6/s7 by a jalr-reachable
selector at `0x00139DB0` — the code-caves.md "zero-reference" claim for it is
superseded.

## 6. Does the yes-set/158 issue share a fix with drive? — YES

Established mechanically in §5 P-A/P-B: the record dies at capture because
158 is NO-set (`run-pass-contrast` §4); the drive dies at capture because 158
is neutral and self-perpetuating (§1.4). Any capture-id change to a yes-set
driving clip (149/150) addresses both with the same word(s): the primary's
5/6 whitelist arm (`0x001f67d8` → `0x001f0ba8`) passes, the helper's attach
gate fallback (`0x001f7590` → `0x001f0ba8`) passes, and the clip being cycled
is a winning one. The remaining separate work is only the market guard (P1,
already deployed) and the S4-D force model as a selector input.

## 7. Call-chain diagram (one page)

```
mode 3/4 manager 0x001f7298 (per frame)
 ├─ 0x001f5b60 tick ─ 0x001f5590 market ─ 0x001f6d10 registry ─ 0x001ef820 SiteA
 ├─ 0x001ef338 (161 ball-bearing) ─ 0x001f0ba0 ─ 0x001efc00 (79/25)
 ├─ parity: EVEN 0x001f00d8 ──┐ kinds 2/3, ≤2.5yd, counter≥46
 │           ODD  0x001f06a0 ─┤ kinds 2/3, ≤3.5yd, counter≥31
 │      chain from 0x00526B80 list, margin rolls on +0x41C ──> 0x0018e910/0x0018e648
 ├─ 0x001f20f8 SiteB
 └─ 0x001f1c20 kind-4 cycle: 0x001f1720 (150/56) → 0x001f1980 GRID 0x00526F90
        (+0x414/+0x418/+0x404 → col; facing → row) → tables {49..108} → 0x0018e768
                                                     current segment @ +0x3F4/F8/FC

state thinks 31/33/47/32 (per think tick, mode 4/7)
 └─ 0x001f7c98 capture: id := 158 (0x001f7d08) → req{+0x40=158,+0x34=20°,+0x38=1.4}
     → 0x0018e648 → 0x0018d3c0 → 0x0018de38 → 0x0018d998
         └ [p+0x308]+2 group → sets [0x006080B0] → dir[+8] → variants → pairdef slots
     → 0x0018e378: p+0x3DC..DF, push state 32 (msg {32, id})
         → enter 0x001e7ee0: 0x003acdf8(ctx304,ctx308,ctx30C,id,1.0), +0x154 len
         → think 0x001e8088: no-collide; on +0xC bit2: kind 5→6, jal 0x001f7c98 (158 again)
                             on fail: exit @ 0.46 [0x005fef3c]

shed contest (defender think): 0x001a66f8 → 0x001a7070(win,move) → chain @0x00526668/0x00526710
 └─ 0x001a65f0-ish walker → 0x0018e6f0 per id → kinds 5/6   (win 62-130 / LOSE 120-131 = driven/pancaked)
```

## 8. Constants and fields referenced (verified values)

`[0x005fef3c]`=0.46 exit speed; `[0x005ff1d8]`=1.4 capture slot tolerance;
`[0x005ff0a0/a8]`=0.8 (161 starters); `[0x005ff0ac]`=0.4 (79/25);
`[0x005ff0b0]`=0.026, `[0x005ff0b4]`=0.46, `[0x005ff0b8/bc]`=0.0354,
`[0x005ff0c0/c4]`=0.16, `[0x005ff0c8/d8]`=0.4 (converters);
`[0x005ff0cc]`=5.642 with `[0x005ff0d0]`=0.46 (odd-converter speed gate:
`+0x1AC` > 0.0815); `[0x005ff0d4]`=0.01 (the 168 near-parallel sub-case is a
1% random gate — rare by design); `[0x005ff0dc/e0/e4/e8]`=0.33 (the pools'
rand-jitter scales, i.e. each pool is base + rand(0..0.33*base));
`[0x005ff130]`=0.06 / `[0x005ff134]`=0.175 (the grid's `+0x404` drive
thresholds, pass / run);
`[0x005ff124/12c]`=0.4, `[0x005ff128]`=1/255 (contact starters). Fields:
`+0x1A8` facing, `+0x1AC` live speed, `+0x1E8` speed_cmd, `+0x304/308/30C`
anim contexts, `+0x3DC-0x3DF` current pairdef/id, `+0x3E0` kind, `+0x3F0`
block role, `+0x3F4/F8/FC` kind-4 segment {angle,tol,id}, `+0x404` staged
drive, `+0x414/418/41C` contest pools, `+0x42E` cycle one-shot, `+0x150/154`
state-32 partner/segment-length, `+0xAEC` weight, `+0xB70` ratings base.

## 9. What I could not establish (do not inherit as fact)

1. **Clip content.** Root motion is in the variant data objects / the
   `[+0x30C]` 124-byte entries' `+0x4C` clips (tag-0xc004 queries); not
   decoded. All drive/pancake labels are selector-semantics inferences. No
   interaction-id name strings exist in the ELF (searched). The one direct
   visual datum remains the operator's 256x pancake.
2. Whether sliders/difficulty scale the `+0x414/418/41C` pools
   (`0x001ef0c8` and the `0x001f0c40` tail un-dumped). Lane-0's "+0x41C is
   slider-scaled comp3" is neither confirmed nor contradicted here.
4. `0x00240050(2,0,p,0)` meaning ("user-controlled" reading; it also gates
   the 1.5x tolerance widening in `0x0018e648`).
5. The `0x001f1880`-region starter (147→130 / 61) — its function start and
   caller were not pinned (`find_jal` on `0x001f1880` returns nothing; entry
   is elsewhere).
6. `0x001efc00`'s 79-vs-25 gate byte (`s2+0x51`) meaning; the `0x001b27d0`
   (id 66) and `0x001ad1a8` functions' contexts (state-47 / special-teams
   suspected).
7. Which of the shed starter's (s1,s2) is the shedder (kind 5 vs 6 mapping);
   the win/lose → driven-back semantics are carried from
   `docs/cpu-dt-animations.md`, not re-derived from the clips.
8. Who arms the `+0x42E` one-shot (and hence how often the kind-4 cycle
   re-picks). The pool-stamp frequency IS now settled (§2.4: sole caller
   chain `0x001f0c40` ← `0x001f153c` in `0x001f14d0` ← both converters + the
   kind-4 cycle; re-stamped per selection attempt) — but note for P-B: the
   CAPTURE path does not call `0x001f14d0`, so at the P-B hook the `+0x41C`
   values are whatever the last converter/cycle attempt stamped (with its
   0.33 jitter). Acceptable for a ratio test; a cave that wants fresh,
   jitter-free numbers should compute its own from ratings + weight.
8b. Event 38's consumer (`0x0015e4e8` system) — pancake stat/commentary
   linkage is the natural reading, untraced. `0x001e7da0` has no jal callers
   (reached inside `0x001e7df8`'s body or via jalr) — its reachability from
   the {56,149,168} predicate's true-arm was not instruction-traced.
9. The variant param record (values 6/0xC/0x10/0x00010002…) and the exact
   role-byte matching between the 161 sector bytes {4,7,10,14,16,17,18} and
   pairdef slot records.
10. P-C's exact table word (`0x00583390`) — carried from
    double-team-solution.md, not re-read this pass.
11. Whether every pairing (OL/TE/FB vs DL/LB/DB) has matching 149/150
    variants — the savestate proves the ids resolve for group 1, not that
    every role combination matches a pairdef slot pair. One live P-A probe
    decides it.

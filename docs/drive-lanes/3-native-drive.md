# Drive lane 3: the engine's own "drive a man backwards" path, end to end

Recorded 2026-08-12. Static lane, maximum effort, against `extract/SLUS_207.52`
(vaddr = offset + 0xFF000, gp = 0x006056F0) via `recon.mipsdis`, and against
`extract/ee_inplay.bin` (registry walk). No rig, no network, no emulator, no
commits. Every instruction and data word quoted below was re-read from the
image or the dump **this run** unless marked *(carried)* — carried claims cite
their source doc. **UNVERIFIED** marks inference.

Mission: the operator has seen a pancake live, and defenders knocked back on
big hits. Find the engine's own "one player drives another backwards" path and
specify how a winning double team (slot 9: TE+RT vs DE, R = 1.848, D = 2.42
per `anim-lanes/2-mass-law.md`) enters it.

---

## 0. Verdict up front

The premise survives at the **selection** level and fails at the **motion**
level, and the two must not be conflated:

1. **The engine natively SELECTS driven-back outcomes** — the shed-contest
   lose chains (120-131, "driven back / pancaked"), the 6×5 grid's
   blocker-winning drive cells ({50,53,54,58}), and 161's straight-back
   classes. All of it is decoded below, with entry conditions quoted. The
   pancake the operator saw is this machinery firing.
2. **No code in this build moves a block-pair member's logical position
   during any of those clips.** Three independent closures this run (§1.3):
   the outcome clips ship no root-motion spec; the animation streams cannot
   trigger the burst applier; and the image-wide +0x190 store census leaves
   no writer alive during kinds 5/6. The pancake is a *skeletal* fact — the
   man falls where he stands.
3. The one mechanism the engine built to translate a body during an
   interaction — the type-9 burst (`0x0018f9e0` convert → `0x0018f980`
   apply) — is real, works for kick/punt and solo-move clips, and **is never
   fed for any pair family**. The kind-4 speed stamps move only free
   locomotion (P6: a 256× one-sided speed_cmd did not move an engaged
   defender — carried, `on-skates-requirements.md`). Big-hit knockback lives
   in the **tackle module**, which has its own position writers (§1.4) on a
   code path blocks cannot reach.

So "make the double team enter the native drive" decomposes cleanly:
**native selection** (make the engine's own margin machinery declare the
double the winner — patchable at one call site, §4 N-1), **native window**
(keep the dominant pair where that machinery runs, §4 N-2), **native finish**
(start the engine's own pancake chain for the dominated man, §4 N-3), and
**displacement**, which must be supplied — for which this lane pins a
one-data-word per-frame host that no previous pass had (§4 N-4).

---

## 1. The pancake path, end to end (all quoted this run)

### 1.1 Who initiates: the DEFENDER, from his own think — never the blocker

The lose-set clips are reached only through the defender's shed attempt
failing. Three entry funnels, all converging on `TryShedMove 0x001a7130`
(callers re-derived: `find_jal` → 0x0016c3c4/0x0016c484/0x0016c544/0x0016c998
= DL user-think; 0x0019ba6c/0x0019be44 in fn 0x0019b338 ← state-2 ai_think
0x0019f008; 0x001cb9e8 in state-30 ai_think 0x001cb630):

**Run plays — state 2 "ball pursuit" think** (fn `0x0019b338`, s3 = player,
s7 = player+0x150, engagement saved at sp+100):

```
0019b404  lbu v0, 17(s7)            ; +0x161 attempt-interval byte, --
0019b410  bgtz -> 0x0019bb28        ; not yet
0019b418/44c  reset: +0x161 := 21 - (AWR+TAK)/32     (AWR +0xB74, TAK +0xB90)
0019b45c-494  [target+0xC] & 0x8000 or 0x00148138(): interval HALVED
0019b828  lw a0, 100(sp)            ; the engagement block (+0x3E0)
0019b830  lw v1, 0(a0)
0019b834  bne v1, 4 -> skip         ; KIND MUST BE EXACTLY 4
0019b83c  jal 0x001f7a38(player)    ; the SHED LOCK (§3.1)
0019b844  bne v0, zero -> return    ; locked -> no attempt at all
0019b9f0-ba64  move choice: geometry picks side, STR vs 165.75 [0x005fe3c4/c8]
               gates power; s0 in {0,1,4,5} (or 8 = auto)
0019ba6c  jal 0x001a7130(player, move)
0019ba7c/88  on failed attempt: +0x161 += 10        ; retry penalty
```

**Pass plays — state 30 "pass rush / engaged" think** (fn `0x001cb630`,
s1 = player, s5 = player+0x3E0, s2 = player+0x150):

```
001cb96c  lw v0, 20(s2)             ; +0x164 shed timer, --
001cb974  bgtz -> return
001cb97c-9b0  reset: +0x164 := 31 - (AWR+TAK)/32
001cb9b4  sltiu v0, v0, 25          ; rand(100) < 25 -- flat 25% per window
001cb9c0  lw v1, 0(s5)
001cb9c4  bne v1, 4 -> return       ; KIND MUST BE 4
001cb9d4  lw v1, 16(s5)             ; +0x3F0 block mode
001cb9dc  bnel v1, 1 -> return      ; pass block only (this funnel)
001cb9e8  jal 0x001a7130(player, 8) ; move 8 = "choose for me"
```

The human DL user-think (0x0016c1c0/0x0016c7e8 sites) is button-driven and
not gated by these timers — the only funnel that could shed out of kind 6.

**Answer to the mission's reachability question: the driven-back ids 118-131
are reachable from ANY engaged block, run or pass** — the run table
`0x00526668` exists precisely for run blocks, state 2 is the run-defense
think, and the A2 measurement (§5) shows 121 and 129 firing on the slot-9
run play. They are **defender-initiated only**: no blocker-side code requests
them, and a doubled defender is barred (§3).

### 1.2 The contest and the outcome (TryShedMove `0x001a7130`, quoted)

```
001a7168  jal 0x0013b798(+0x3E4)    ; resolve link -> s2 = the blocker
001a7178  jal 0x001a6fa8(def, blk)  ; eligibility precheck
001a71ac-1c4  move 8 -> jal 0x001a6c98 (the no-ratings chooser)
001a71c8-218  human w/ flag 0xC&0x4000: STR > [0x005fe578]=165.75 and
              rand<50 -> upgrade move 0->4 / 1->5
001a7220-22c  kind must be 4 OR 6   (the 6 arm is reachable only by the
                                     human funnel; both AI funnels gate on 4)
001a7238  jal 0x001f7a38(defender)  ; SHED LOCK: +0x42C != 0 -> return 0
001a7258  jal 0x001a66f8(def, blk, def+0x404, blk+0x404, move)
                                    ; BreakBlockContest -> win (1 = shed)
001a7268  jal 0x001a7070(win, move) ; chain select (tables below)
001a7278  jal 0x001a6618(blk, def, chain)   ; START the outcome pair anim
001a7284  started -> return 1       ; pair now owned by state 32
; -- only if NO clip could start (all chain ids failed to match):
001a728c-7314  tug-of-war accumulators +-0.075 [0x005fe57c/80] (x2 human)
              via 0x001f79c0 -- slots +0x420/424/428. NOT motion.
001a7318-4c   locks: both men +0x42C := 30 (15 if human power arm)
001a7358      blocker +0x42E := 1   ; arms the kind-4 grid re-pick
```

`BreakBlockContest 0x001a66f8` itself is carried (adversarially verified in
`cpu-dt-animations.md` / `pass-rush.md`; its sole caller and both table
bases re-verified this run): blocker score = (PPBK|PRBK) + STR/3 + move
terms, ×(1.0…0.25) leverage by angle-to-carrier (bull rush exempt); shedder
score = (STR + move terms) ×4 × two CPU-side difficulty scalers × ptrk;
**win iff RandInt(0, blockScore) < RandInt(0, shedScore)**.

Chain tables, re-read as data this run (24 B/move: win chain +0, lose +12,
u32 ids, 0xFFFF-terminated; fallback `0x00526704` = {131}):

```
RUN  0x00526668  win:  62  63 122 123 126 127 130   (move 0..6)
                 lose:120 121 124 125 128 129 131
PASS 0x00526710  win: [118,62] [119,63] 122 123 [126,64] [127,65] 130
                 lose: same seven as run
```

Lose = the **driven-back / pancake family** *(carried label,
`cpu-dt-animations.md`)*; win = shed/swim/rip/club, except move-6 win 130 =
the bull-rusher driving the BLOCKER back.

### 1.2b The outcome starter `0x001a6618` — what the engine does to the loser

```
001a6650  jal 0x0018e910(sp)        ; same 72-byte request ctor as the capture
001a6660/68  request+0 := blocker, +4 := defender
001a6670-98  walk the chain: request+0x40 := id; jal 0x0018e6f0; next on fail
001a66ac  jal 0x001f7398(blk, def, 5)   ; BLOCKER  kind := 5
001a66bc  jal 0x001f74c8(def, blk, 6)   ; DEFENDER kind := 6
001a66c8  jal 0x0014fe20(def, 9)        ; defender gait/speed reprofile
```

`0x0018e6f0` (quoted) = fixed tolerances +0x34 := 20° (0x000E38E3), +0x38 :=
[0x005fe16c] = **0.8**, then the standard resolve (`0x0018d3c0` →
`0x0018de38`) and launch (`0x0018e378` → both men pushed into **AI state
32**). This settles lane 1's open item 7: **kind 5 = blocker, kind 6 =
defender(shedder), for win and lose alike** — the same convention A2
measured on the 161 pair (TE k5 / DE k6).

So a pancake is: defender rolls the contest, loses, and the pair plays a
two-man lose clip under state 32 — same machinery as every other pair anim.
What the engine "does differently" to the loser is **clip choice plus a gait
reprofile**. Nothing else.

### 1.3 How the loser's body moves — the negative, closed three ways this run

**(a) The lose clips carry no root-motion spec.** Registry re-walked in
`ee_inplay.bin` (count [0x00608098]=81, table [0x006080B0]=0x00F848F0): g17
(member 6, gd 0x00CD09D0) serves 118-131; every id in the lose and win sets
carries a param record plus exactly **two mirrored variants whose typed
records are type 0x0B (class record), size 5, class 0** — e.g. 121 →
tags 0xA15E/0xA15F, 129 → 0xA18C/0xA18D, 131 → 0xA198/0xA199, all
(type=11, size=5, class=0). **No type-9 (root-motion spec {vector,
duration, heading}) exists anywhere in g17** — re-verifying
`5-clip-semantics` §3 for exactly the ids that matter.

**(b) The animation streams cannot trigger motion.** The stream walker's
event call `0x003a8398` forwards `event & 0xFFFF` to the runtime callback
[0x0060807c] = `0x0012bdf0` (read from the in-play dump). That callback maps
events 0xC000-0xC01C through jump table `0x0057A020` — and **all 29 entries
resolve to three stubs (0x0012be30/38/40) that call `0x003ac550` one, two,
or three times (popping the event's payload words) and return 1.** No bus
message, no burst, no position math. A clip's packed stream can carry
events, and this build throws every one of them away.

**(c) The +0x190 position-store census** (find_field_refs, stores, biased
bases included, whole image; stack-save noise discarded). The complete set
of gameplay writers that can touch a standing player's x during a play:

| writer | va | runs during a pair clip (kinds 5/6)? |
|---|---|---|
| burst applier `0x0018f980` | 0x0018f9bc | **no** — opcode-38 messages are not sent for pair clips (carried live disproof, `motion-block-cave.md` §1.3: live 147 pair, virgin/garbage motion blocks) |
| attach glide `0x0018c7c0` | 0x0018cc30 | start-of-clip only, walks players onto an authored anchor; **null for the whole shed family** (all g17 alignments null — carried, `3-clip-inventory.md` §3d); killable by [0x0060111d]=0 |
| collision separator | 0x00212fc4-0x002135a8 | **no** — mutual no-collide registered for the pair (state-32 think; also re-registered by the kind-4 segment walker) |
| locomotion integrator family | 0x00216998 region | moot: P6 proved 256× speed_cmd does not move an engaged defender *(carried)* |
| tackle-module placement | 0x001fe720/804/914/9ec (callee of `0x001869ec`) | different family — see (d). UNVERIFIED beyond caller identity |
| spot/teleport/setup | 0x0012c-0x00143 band etc. | dead ball only |

Nothing else in the image stores a player's +0x190. **During a shed-outcome
clip, the loser's logical position has no writer.** The clip animates the
fall — the body goes prone where contact happened; that is what a 256×
pancake looks like from the couch, and it is consistent with every live
read this project owns (147 pair holding at 1.3 yd; A2's 161×37 with zero
drive; P8's canary arithmetic).

**(d) Where real knockback DOES live.** The one gameplay system with its own
position writers on a beaten man is the **tackle module**: stores reached
via `0x001869ec` (the function directly preceding TackleScore `0x00186b08`)
land at +0x190-relative offsets on both participants — the tackle-placement
analog of the glide. That is the natural home of "defenders knocked back on
big hits". It is not reachable from block code (blast-radius rule: separate
path). Identification is caller-level only — **UNVERIFIED** at the
instruction-semantics level.

### 1.4 The engine's own pancake bookkeeping (corroboration)

State 32's per-tick helper `0x001e7cf8` *(carried, lane 1 §4.2a; predicate
re-confirmed at 0x001e7d2c-54 in a prior pass)* tests the possessing-side
member's current clip ∈ {56, 149, 168} and on a 76% roll emits located
event 38, capped at 30 per game — the pancake-stat marker. The engine's own
recorded "pancake" is therefore the **blocker-side decisive-win clips**
(149 = the +0x41C pancake-pool roll, 168 = its odd-frame sibling, 56 = the
late contact starter), while the defender-side fall vocabulary is the shed
lose set. Both are selection events, not displacement events.

---

## 2. The grid and what "blocker wins decisively" actually does

Re-read as data this run — `0x00526F90`, word = table pointer, indexed
`[facing*20 + pass*60 + col*4]`; tables are 12-byte entries {u16 id, f32
tol, u32 angle}, 0xFFFF-terminated:

```
              col0 stale   col1 BLOCKER-WIN  col2 DEF-WIN  col3          col4
run  opposed  {49}         {50}              {51}          {60,49}       {59,49}
run  turnedL  {52,49}      {53,50}           {106,51}      {60,49}       {106,59,52,55,49}
run  turnedR  {55,49}      {54,50}           {107,51}      {107,60,49}   {59,49}
pass opposed  {57,49}      {58,49}           {108}         {60,49}       {59,49}
pass turnedL  {52,57,49}   {53,106,58,49}    {106,108}     {60,49}       {106,59,52,55,49}
pass turnedR  {55,57,49}   {54,107,58,49}    {107,108}     {107,60,49}   {59,49}
```

Column logic *(carried, lane 1 §2.4b, consistent with everything read
here)*: col 1 requires blocker `+0x414` > defender's AND blocker `+0x404` >
[0x005ff134]=**0.175** run / [0x005ff130]=**0.06** pass (both re-read).
Rows are facing classes. The dispatcher `0x001f1980` only picks a
**continuation clip table**; the walker (`0x0018e768`) starts segments with
**no kind change** — the pair stays kind 4.

**What the engine does differently in blocker-win cells — quoted:** the
per-frame sweep stamps, at `0x001f2054-0x001f2088` (s3/s6 = the two men's
+0x3E0 blocks, s1/s2 the player bases):

```
001f2064  sw v0, 492(s1)    ; +0x1EC desired bearing := [+0x40C] staged axis
001f2068  swc1 f0, 488(s1)  ; +0x1E8 speed_cmd      := [+0x404] staged drive
001f2074  sb v1, 500(s2)    ; +0x1F4 profile
001f2080  sw v0, 492(s2)    ; same two fields on the OTHER man
001f2084  swc1 f0, 488(s2)  ;   (identical shared values)
```

— i.e. in a won cell both men are *commanded* to walk the shared axis at the
margin's speed, and the drive clips {50,53,54,58} are the look of it. **This
is the engine's intended sustained drive — and P5/P6 proved the command
moves only the blocker's free locomotion; an engaged defender does not
consume it at any magnitude** *(carried, on-skates-requirements: symmetric
256× left the DE at baseline; asymmetric 256× likewise)*. The native "drive"
is therefore selection + blocker gait; the defender yields only what the
(unidentified) residual writer of the measured +0.41 yd/17 f baseline gives
up — see §6.3.

Corrected en passant: lane 2 read the sweep's source as "+0x404 via s3" —
true, but the full stamp set is +0x404→speed, **+0x40C→bearing,
+0x410→profile**, s3 being the +0x3E0 base. Same site, wider meaning.

---

## 3. Entry conditions — and exactly why the doubled DE never gets there

### 3.1 The shed lock `+0x42C`: the whole story, quoted

The predicate every shed funnel consults (`0x001f7a38`, three
instructions):

```
001f7a38  lh v0, 1068(a0)      ; u16 player+0x42C
001f7a3c  jr ra
001f7a40  sltu v0, zero, v0    ; return (+0x42C != 0)
```

Callers (complete): `0x0019b83c` (state-2 shed gate), `0x001a7238`
(TryShedMove), `0x001f21b0` (kind-8 servicer). Setter `0x001f7a28`
(`sh a1, 0x42C(a0)`, sign-extended byte); callers (complete):
`0x001a7320/2c/3c/48` (TryShedMove's post-contest locks, 15/30) and
**`0x001f21e4` — the kind-8 helper servicer.** Countdown: the per-pair tick
(fn `0x001f5b60` region) decrements it every frame and floors at zero:

```
001f5d1c  addiu v0, v0, -1     ; (+0x42C via 40(s2), s2 = +0x404 base)
001f5d24  bgtz -> store
001f5d28  sh v0, 40(s2)
001f5d2c  sh zero, 40(s2)      ; expired -> 0x001f5d98 handler
```

### 3.2 The kind-8 servicer's block — the double team locks its own victim

`0x001f20f8`, per serviced frame for helper s0 (kind 8) with defender s2:

```
001f21b0  jal 0x001f7a38(defender)   ; lock still running?
001f21c0  bne v0, zero -> next player     ; yes -> nothing
001f21c8  jal 0x001f79c0(def, 1, f20)    ; the -0.13 debuff        (lapse)
001f21d8  jal 0x001f79c0(def, 0, f20)
001f21e4  jal 0x001f7a28(defender)   ; *** +0x42C := 16 -- RE-ARM ***
001f21e8  addiu a1, zero, 16
001f21ec  jal 0x0013b798(def+0x3E4)  ; resolve the PRIMARY
001f21fc-2204  primary kind must be 4
001f2214  sb a2, 1070(a0)            ; primary +0x42E := 1 (grid re-pick arm)
001f221c/20  PBK|RBK >> 4
001f2230  sh v0, 1074(a0)            ; primary +0x432 := base - rating/16
                                     ;   (hold countdown re-init)
```

**This answers lane 1's open "who arms +0x42E" (this block plus
TryShedMove's no-anim fallback at 0x001a7358) and block-cycle.md's
"re-arms the primary's block countdown" (+0x432 at 0x001f2230). And it is
the mission's smoking gun: the helper that should be overpowering the
defender is what re-arms the defender's shed lock to 16 frames at every
lapse — so a doubled defender's +0x42C is nonzero essentially always, the
gate at 0x001a7240/0x0019b844 rejects him, the contest never rolls, and the
lose chain — the engine's entire driven-back/pancake vocabulary — is
unreachable for exactly the defender a double team dominates.**

### 3.3 The other two exits from kind 4, for completeness

* **Site A's 161 conversion** — sole builder call `0x001efad0` (inside the
  per-player loop of the kind machine `0x001ef820`); the builder
  `0x001ef130` re-verified: requires bearing-vs-facing divergence > 67°,
  45°-sector ∈ [90,270], then request {+0x40 := **161** (0x001ef204/214),
  +0x34 := 20°, +0x38 := [0x005ff0a0], class byte per sector table
  0x00583300} → kinds 6/6. Geometry-only — no margin, no ratings *(trigger
  conditions carried from `3-clip-inventory.md` §3c, id/class stores
  re-read this run)*. On slot 9 this — not the capture — is what took TE/DE
  into a pair clip at f43 (the handoff flips the DE's desired bearing).
* **The capture service** `0x001f7c98` — mode ∈ {4,7}, possessing side,
  hardcoded id 158 (`addiu s3, zero, 158` at 0x001f7d08 — carried, verified
  live 2026-08-11), defender-state eligibility `0x001f1be0` re-read this
  run: jump table `0x005833D0` keyed on state−5 (88 entries → return 0/1);
  **states 0-4 fall outside the table and return 1** (unsigned wrap), so a
  state-2 pursuit defender is capturable.

Both convert the pair to kinds 5/6 / state 32 — where the defender's think
no longer runs shed logic at all (state 32's think is the segment loop;
kind gate 4 fails in every AI funnel anyway). **Kinds 5/6 are a shed-free,
drive-free hold: selection machinery parked, no mover running (§1.3).**

### 3.4 So the entry condition for the native pancake, stated plainly

For a blocking pair to enter a driving/pancake outcome the engine requires
ALL of: defender kind == 4 (not 5/6); defender +0x42C == 0; the defender's
own attempt timer/roll to come up; and then the contest itself to resolve
against the shedder. A dominant double team fails the second condition by
construction (§3.2) and, from the handoff on, the first (§3.3). Nothing in
any of these tests reads weight, the helper, or any notion of dominance —
**mass never enters the trigger.** That is the entire gap between "the
engine can play a pancake" and "a winning double team gets one".

---

## 4. Patches — ranked, with exact addresses

Per rule 2, each is separable and individually measurable. The composite
target: slot 9's TE+RT (M=963) vs DE (M=521), D = 2.42.

**N-1 — THE KEYSTONE: inject the attached helper into the contest comps
(one jal retarget + cave).** Site: the sole lock-in call `0x001F153C`
(`jal 0x001f0c40` inside `0x001f14d0`; s0 = blocker base, s4 = defender
base, both live across the call — carried liveness proof, lane 2 §1.2).
Retarget to a cave that (i) calls `0x001f0c40`, (ii) if defender
`+0x437 == 2` and the registry's role-1 helper ([gp-17520] = [0x00601280],
record [def+0x436], helper at +4+20k+4) is attached (helper +0x3E0 ∈
{7,8}), adds the helper's terms into the BLOCKER's freshly stamped comps:
`+0x414 += W_h + STR_h`, `+0x418 += AGI_h + STR_h/2`, `+0x41C += W_h +
STR_h + AWR_h + AGI_h` (ratings at +0xB70+2i: AWR i2, AGI **UNVERIFIED
label** i3-area — reuse lane 2's attr1 caveat; weight +0xAEC). ~55 words;
host cave #2 `0x0044C1C0` (lane-2-censused clean; #7 is claimed by the
deployed P8/P9 pnach lines). Effects, every one through a consumer quoted
in this repo: comp2 compare `0x001f154c` flips → **the blocker becomes the
comp2 winner, so the staged axis +0x40C becomes the PRIMARY's earned
heading** (today the DE wins it — measured: at f39 the TE was playing 106,
a col-2 defender-win cell clip, which requires defender +0x418 > blocker's
and defender drive staged — so the axis points the wrong way; this alone is
disqualifying for any drive until fixed); comp1 margin ≈
(1340−745)/1340 ≈ **+0.44** lands in +0x404 → clears the 0.175 run drive
threshold → **grid col 1, drive clips {50,53,54}**; the even/odd
converters' pancake-pool rolls (149/168) see a ~44-margin; the shed
contest's finesse/power advantage axes favor the block. R5: no helper → no
change, byte-identical; pass sets: registry never forms (DT-1), no change.
Oracle: +0x404 on TE/DE ≈ 0.44 (baseline ≈ 0, defender-signed); grid
segment ids {50,53,54} observed on the pair; C/NT and slots 6/7 unchanged.

**N-2 — the window: dominant pairs stay kind 4.** Two jal shims, both
gated on defender `+0x437 == 2` (6-word caves): (a) capture eligibility
`0x001F7D38` (`jal 0x001f1be0`, a1 = defender) → cave: role-2 → return 0,
else fall into 0x001f1be0; (b) Site-A 161 builder call `0x001EFAD0`
(`jal 0x001ef130`) → cave: if either participant is role-2 → return 0,
else fall into 0x001ef130 (a0/a1→request+4/+0 mapping — **verify the
defender slot on first deploy**). Result: the doubled pair never enters
kinds 5/6; the kind-4 cycle, sweep stamps, +0x42E re-picks and (with N-3)
the shed machinery keep running for the whole play instead of 20 frames.
Oracle: TE/DE kinds stay 4 (no 161×37), record window past 64, grid
segments all play. Risk: visual — the pair holds segment clips instead of
the capture's lock-up; bounded by the fact that C/NT spend 107 frames in
exactly such loops today.

**N-3 — the native finish: fire the engine's own pancake for the dominated
man.** Once N-1 makes dominance legible in the comps, force the lose chain
instead of merely permitting it: a cave (natural host: extend N-1's, which
already holds both men and the helper) that, when comp1 margin ≥ knee
(0.30) and defender kind == 4 and a per-record one-shot is clear, calls
`0x001a7070(win=0, move=6)` then `0x001a6618(primary, defender, chain)` —
the identical calls TryShedMove makes on a lost bull rush, starting id 131
(fallback-proof: 131 IS the fallback chain), kinds 5/6, gait reprofile,
state 32. Entirely engine machinery; the request walker does the geometry
(20°/0.8 tolerances). Displacement: none (§1.3) — this is the LOOK of the
pancake, honestly labeled. Alternative trigger site if the one-shot
bookkeeping is unwelcome in N-1's cave: the servicer's lapse block
(0x001f21c8-0x001f21e8 runs exactly once per 16-frame lapse — a natural
rate limiter; all registers audited in `motion-block-cave.md` §6.1).

**N-4 — displacement, at last on a counted per-frame host: one data word.**
The state-32 ai_think pointer is data: **word `0x00527540` = 0x001E8088**
(descriptor read this run: enter/can_leave/think/exit =
0x001e7ee0/0x001e8258/0x001e8088/0x001e8318). Repoint it to a cave that
(i) increments an entry canary, (ii) for players with `+0x437 == 2` (or
whose partner is role 2) applies the P9-proven position add (+0x190/+0x194,
both members + helper carry, D/64 per frame along primary→defender — the
audited body already exists in `motion-block-cave.md` §6.2), then (iii)
jumps `0x001e8088`. The think runs once per frame per captured player —
the A2 series (121×59, 158×107, 161×37 continuous) is per-frame evidence —
**but P9's lesson stands: read the canary before believing it** (both
prior hosts promised per-frame and delivered 5/play). This is not native
motion — it is the missing motion, delivered while the pair is inside the
native clips that N-1/N-3 select. Revert = restore one word.

**N-5 — diagnostics and tuning surfaces** (not the fix): (a) one-word
retarget `0x001EF204` `addiu v1, zero, 161` (240300A1) → 131 (24030083)
plus neutralizing the class store (request+0x42 := 255) makes every Site-A
conversion a bull-lose visual — instant on-demand pancakes for camera
verification of §1.3's "falls in place" prediction; (b) grid cell pointer
swaps at `0x00526F90+` (e.g. run-opposed col0 0x00526BA8 → 0x00526BE8)
force drive-clip looks without margin — blunt, R5-hostile, diagnostic
only; (c) the shed-lock re-arm value at `0x001F21E8` (16) is a one-word
lever over how often ANY doubled defender may contest — raising it is the
cheap "doubles suppress sheds harder" tuning; **do not lower it to force
pancakes** — a free defender may WIN the contest and the win chain releases
him from the double.

Deployment order per rule 2: N-1 alone (margins/axis/grid oracle) → N-2
alone (window oracle) → N-1+N-2 (+0.44 drive command, whatever the residual
mover yields — the honest kind-4 displacement measurement this project
still lacks) → +N-4 (the yards) → +N-3 (the finish). Slots 6/7/8 frozen-
compare and C/NT byte-identity are the must-not-move arms throughout.

---

## 5. Cross-check against the A2 measurement (mission item 4)

Measured (carried, `4-synthesis.md` A2): TE/DE **161×37** (k5/k6, from
f43); RG/#14 161×38; **C/NT 121×59 → 158×107**; **LG/#11 151×33 → 129×59 →
158×89**; FB/#14 148×34 → 63×26. Every line is now mechanism-named:

* **C/NT**: NT single-blocked → +0x42C free between his own attempts →
  state-2 funnel rolled, contest LOST → run lose chain move 1 → **121**
  (kinds 5/6, 59 f) → at segment end / eligibility, the mode-4 capture took
  the pair at hardcoded **158** and its think re-captured 158 forever
  (107 f). The lose clip fired and the NT fell — with no logical drive.
* **LG/#11**: converter clip 151 (yes-set, kinds 6/6) first, then the same
  story: contest lost at move 5 → **129**, then capture 158.
* **FB/#14**: 148 then **63** — the defender WON his move-1 contest (win
  chain 63) and shed the FB. Both directions of the contest live on one
  play.
* **TE/DE**: the helper re-armed the DE's +0x42C every lapse (§3.2) → not
  one contest roll → no lose clip possible; at the f43 handoff geometry the
  Site-A builder converted the pair to **161** (37 f) — the drive-class
  family, played with zero displacement (§1.3). The record died at ~64
  regardless (161 is NO-set in the manage table — carried).
* And the pass-shed asymmetry explains the funnels: state 30 (pass) rolls
  at flat 25% per ~24-frame window; state 2 (run) uses the shorter 21-base
  interval with failure penalty — matching how briskly the run-play
  contests fired here.

The contrast the mission called "the key" is therefore fully mechanical:
**single-blocked defenders reach the outcome vocabulary through their own
failed sheds; the doubled defender is shed-locked by his own doublers and
geometry-captured into a motionless drive clip.** No dominance term exists
anywhere in the path — which is precisely what N-1/N-2/N-3 add.

---

## 6. What I could not establish (do not inherit as fact)

1. **The writer of the residual kind-4 displacement** (baseline
   defender_pushback +0.41 yd/17 f, A2 world). Candidates, none proven:
   collision separator (if pair no-collide lapses between segments), the
   attach glide at 106/158/161 starts (non-null alignments), or blocker
   locomotion dragging through the contact geometry. The §4 order measures
   it (N-1+N-2 with N-4 off) before N-4 obscures it.
2. **Opcode-38's sender for the clips that DO burst** (g3/g12). Eliminated:
   stream events (this run, §1.3b). Still unfound; not load-bearing here.
3. **Whether any pair-family packed stream encodes root translation** the
   engine simply never decodes. The §1.3 closures make it irrelevant to
   behavior, but per-clip visual displacement magnitudes remain unreadable
   statically (packed 84-byte rows, unchanged from lane 3 §5).
4. **The tackle module's knockback writers** beyond caller identity
   (0x001869ec → 0x001fe7xx stores) — flagged as the "big hits" home,
   semantics untraced (separate blast radius).
5. **State-32 think cadence as a count** — per-frame by construction and
   by the A2 clip series, but the canary (N-4 step i) is the proof this
   project's history demands.
6. **The a0/a1 → request-slot mapping in 0x001ef130** (which participant is
   the defender) — one deploy-time read; N-2b's cave tests both bytes
   until then.
7. **AGI's ratings index** for N-1's comp2/comp3 terms (lane 2's "attr1,
   likely AGI" caveat inherited — verify the fourcc before the cave
   assembles those two adds).
8. **State-30's [s5+0x10]==1 restriction** — whether a run-mode-flagged
   pass rusher (carrier past LOS flips modes) loses his funnel entirely or
   re-enters via state 2; peripheral to the double-team question.

## P11 RESULT (2026-08-12): the shed-lock WAS the suppressor — and the defender wins the contest

One word (0x001F21E8: addiu a1,zero,16 -> 0). 3/3 identical:

    window   2..64 -> 2..78     DE travel  0.60 -> 1.79 yd (3x)
    DE dy   -0.51 -> -1.41      gap        1.75 -> 1.35
    carrier -0.70 -> +0.77      clips 5/6: 161x37 -> {147x41, 161x45}

**THESIS CONFIRMED:** the helper's shed-lock re-arm was suppressing the
contest. One word unlocked it and everything moved -- longer pairing, a new
clip (147, engage-and-hold), triple the defender travel.

**But the outcome is inverted.** dy -1.41 is DEEPER penetration: given a
contest, the doubled defender WINS it. No driven-back ids (120-131) appear at
all, so the lose chains are still not being reached -- he is not losing, he is
beating two blockers. This is the risk the pnach flagged, and it is the
cleanest possible evidence for what N-1 exists to fix: **nothing in the
contest knows a second blocker is there.**

So the picture is now complete and consistent:
* the native driven-back vocabulary is real and reachable only by losing a
  shed contest;
* the double team was never entering that contest (shed-lock);
* unlocked, it enters and LOSES -- because the contest scores one blocker
  against one defender and the helper contributes nothing.

**N-1 is now the whole fix and its prediction is sharp:** fold the attached
role-1 helper's weight+STR into the primary's comps at the lock-in call
(0x001F153C), and the same contest that just ran should resolve the other way
-- into ids 120-131. Keep P11 deployed: without it the contest never happens,
so N-1 alone would have nothing to flip. **P11 + N-1 is the pair that should
finish this.**

Operator watch item: with P11 alone, #93 should look MORE dominant, not less.

### Operator's reading of P11, which names the remaining defect exactly

> "it looks like he sheds the first RT block and then the TE picks him up
> which is its own win in a way but then we need to make sure it still can
> push him back"

The clip sequence corroborates it precisely: 147 x41 (engage-and-hold) then
161 x45 -- a shed followed by a re-engagement, i.e. a HANDOFF. That behaviour
did not exist before P11 and is football-correct on its own.

And it states the remaining defect better than this document did: **each
blocker fights him one-on-one.** He beats the RT alone, so the TE takes over
alone. The pair exists in the RECORD but not in the ARITHMETIC -- no contest
input anywhere knows a second man is on him. That is exactly and only what
N-1 changes.

Prediction to carry into N-1: with the helper's weight+STR folded into the
primary's comps, the shed he currently wins against one man he should LOSE
against two -- which routes into the lose chains (120-131) instead of into a
handoff. The handoff behaviour is worth preserving as the correct outcome when
he DOES win, so N-1 should shift the odds, not remove the path.

## N-1 build attempt (2026-08-12): BLOCKED on the helper lookup, cave #2 rejected

Two findings before a word was written:

**Cave #2 (0x0044C1C0) is NOT safe** -- a branch from 0x0044C404 lands inside
it. It was listed safe in code-caves.md AND censused clean by an earlier agent
whose pass omitted the branch-target axis. That is the FOURTH region this
session documented as safe and found live (#1 poisoned, #3 lui/addiu-live, #2
branch-in, plus the motion cave's original recommendation). Verified-clean
alternatives, all four axes, this pass: **#4 0x004F4AA0, #5 0x00447888,
#6 0x0044BEB0**.

**The helper lookup is unverified and expensive.** N-1 must resolve, from the
DEFENDER, the record's role-1 helper in order to add his mass into the
primary's comps. The design asserts manager [0x00601280] -> record index at
def+0x436 -> helper handle at record+4+20k+4. But the stride is 20 (not
shift-friendly) and the value is likely a handle needing 0x0013b798 -- roughly
20 extra cave words and a second nested call, on a path never checked against
a live record.

Site itself is confirmed: 0x001F153C = `jal 0x001f0c40`, identical in the ELF
and in slot 9's memory, with s0 = blocker base and s4 = defender base live
across it.

**Status: N-1 designed, site verified, cave region chosen, BLOCKED on the
helper-resolution path.** A lane has been redirected onto exactly that
question (docs/drive-lanes/2-position-authority.md, section "Helper lookup for
N-1"), including whether a cheaper route exists -- a cached helper base or an
engine function returning "the other blocker on this man" would beat twenty
words of pointer arithmetic. Building on the unverified path would have been
the same mistake as writing into cave #2.

## REGRESSION ARMS under P1+P4+P11 (boot 1, 2026-08-12)

**Slot 6 (lead-blocker run, R5 control): CLEAN.** lead_blockers_seen 1,
first_mode3 21, engaged_frames 92, carrier -0.724 -- all at or within rounding
of the pre-patch-era baselines. 3/3 deterministic.

**Slot 7 (pass): STRUCTURALLY MOVED -- pre-registered flag tripped.**

    qb_dropback       7.167 -> 8.753     block_episodes  12 -> 25
    longest_episode   329  -> 218        worst_drop_late 0.678 -> 0.559  <- the flag
    carrier_yards     -0.725 UNCHANGED   blockers_engaged 7 UNCHANGED
    max_snap_frame    467 (play still never ends; QB still never sacked)

Reading: P11's shed-lock unlock reaches pass pro through the measured kind-8
flap exactly as predicted. Defenders on pass blocks can now shed, so reps end
and re-form (25 episodes vs 12, longest 218 vs 329) and the QB drops deeper --
but no blocker is lost, the QB is still never sacked, and the play outcome is
unchanged. The pocket became DYNAMIC rather than static.

The pre-registered letter says worst_drop_late moving = fail flag, so it is
raised, not rationalised. The football question is the operator's: baseline
pass pro was arguably broken-good (a QB standing 8 seconds untouched behind 7
men). More shedding may be a fix. Options if he rules it a regression: gate
P11's zero to run plays (a small cave on the servicer testing the play class),
or accept and re-baseline slot 7's range card.

### OPERATOR RULING on the slot-7 change (2026-08-12): ACCEPTED, with two tunes logged

> "they are shedding a bit too quickly but overall it does seem fun to see
> them actually try to shed blocks aggressively, they all seem to hit swim
> moves"

P11's pass-side effect is ruled an improvement in direction, magnitude to
tune. Slot 7's range card re-baselines to the dynamic-pocket values. Two
follow-ups logged, neither actioned now (one variable at a time; N-1 is the
queued change):

* **T1 -- shed cadence.** P11 sets the lock to 0 = instant re-contest. The
  word takes any immediate (addiu a1, zero, N), so a small N (4-8 frames)
  slows the shed rhythm without restoring the statue. One-word tune, own
  test, after N-1.
* **T2 -- move variety.** "They all seem to hit swim moves": the shed-move
  selection appears to collapse to one move. The win/lose chains
  (0x00526668/0x00526710) and the move pools are mapped; why the roll always
  lands on swim is an open thread (cpu-dt-animations.md territory -- possibly
  the same finesse-vs-power selection the docs cover).

## N-1 RESULT (2026-08-12): THE DRIVE IS REAL — DE_dy flips -1.41 -> +2.09

Boot-2 S0 full pass. Canaries: entry=1, fold=0x0066EB10 -- the RT's base, so
the helper lookup resolved and the fold executed. (Canary counts are per-play:
load_state restores the cave-#11 region, wiping them each iteration.)

    DE_dy     -1.41 -> +2.09    DRIVEN BACK past R3's >= 1.0 target
    window    2..78 -> 2..92/98
    DE clips  {147,161} handoff -> {56} -- the blocker-WINNING grid family
    carrier   -0.70 / +0.65 / -0.70 (mixed; see notes)

One fold per play sufficed, exactly as the latching-patch analysis predicted.
The engine chose a different outcome family and its own machinery moved the
man: no position writes, no warps, mass and strength deciding it.

Honest notes: (1) iteration 1 diverged (window 98, carrier +0.65) -- third
determinism wobble on record, all under patch sets; (2) the pair's gap grew to
1.92 -- they drive him from distance via the grid clips rather than staying
welded, worth eyes; (3) carrier_yards is NOT yet improved in 2 of 3 -- the
dive still gets stuffed by the rest of the defence; driving the end does not
by itself score the play. The REQUIREMENT (two men drive the doubled man
backward, proportional to mass, via the engine) is MET on the numbers.
AWAITING THE OPERATOR'S EYES -- the instrument of record.

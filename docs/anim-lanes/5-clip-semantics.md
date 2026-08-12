# Anim lane 5: clip semantics — naming the animation ids

Recorded 2026-08-11. Static lane, maximum effort. Sources: `extract/SLUS_207.52`
(vaddr = offset + 0xFF000, gp = 0x006056F0) via `recon.mipsdis`;
`extract/ee_inplay.bin` and `extract/ee_mainmenu.bin` (32 MB EE images,
vaddr == offset); `extract/GAMEDATA.DAT`, `extract/PLADATA.DAT`,
`extract/DB_TEAMS.DAT`, `extract/TEMPLATE.DAT`; savestates in
`experiments/states/` via `tools/statereader.py`. No rig, no network, no
emulator, no commits. **UNVERIFIED** marks inference; everything else was read
from the binary, a dump, or a savestate this pass.

Headline results:

1. **+0x3DE is the registry GROUP NUMBER of the current interaction anim, not
   a clip id and not an outcome class.** The live slot-9 sample decodes
   completely under this reading, and it adjudicates the lane 1 / lane 3
   dispute: on a real run play every observed pair played converter clips
   (g18), then shed-contest outcomes (g17), then capture/segment clips
   (g19/g15) — both lanes' machinery runs, in that order (§4).
2. **No name table exists for gameplay animation ids** — but one exists for
   sideline/celebration animations, which is what the fourcc pattern hit (§1).
3. The registry's variant records are **typed**; type 9 is a static
   **root-motion spec {vector, vector2, duration, final heading}** whose
   layout matches consumer `0x0018f9e0` field-for-field. In ctx 1 only the
   kick/punt family (g3) and one solo family (g12) ship type-9 specs. **Every
   two-man block family (g15-g19) ships class-tag records only — there is no
   static per-clip displacement to rank for blocks** (§3). The class bytes of
   161's 24 variants were read from data and match lane 3's code-derived
   class set exactly.
4. A large new id→meaning table from a mid-play savestate read (§0, §2).

## 0. New ground truth mined this pass (slot 8 = ball-in-air, MID-PLAY)

`experiments/states/ball_in_air_slot8.p2s` is the one owned image taken
mid-play. (`ee_inplay.bin` is PRE-SNAP — the "MID-PLAY" label in this lane's
tasking is wrong; all 22 players read kind 0 in stance clips there. Lane 3's
description was correct.) Slot 8, read via statereader (player base
0x00661B90, stride 0x14C0, anim slots via `[p+0x304]`):

| player | pos | kind | slot-scan clip (st=3) | +0x3DC | +0x3DE |
|---|---|---|---|---|---|
| 0:0 QB | (0.95,10.65) | 0 | **69** (just threw) | 0102 | ffff |
| 0:1 HB | (1.62,8.83) | 1 | 74 | 0102 | 0404 |
| 0:2/3/5 WR/TE | downfield | 0 | 74 (153 st=0 queued) | xx02 | ff01 |
| 0:4 WR | (13.22,28.78) | 0 | 74 (**153 st=1** fading) | 0502 | ffff |
| 0:6 LT | (-8.33,11.69) | 1 | 74 (**168 st=1** fading) | 00ab | **0012** |
| 0:7 OL | (-0.76,15.69) | 2 | 74 (161 st=0 in k3) | 0003 | 0013 |
| 0:8 OL | (4.84,13.89) | 2 | 74 (151 st=0) | 0096 | 000f |
| **0:9 OL** | (2.54,6.68) | **6** | **147 st=3 LIVE PAIR** | 000b | **0012** |
| 0:10 OL | (2.64,10.63) | 0 | **61** | 00b6 | 000f |
| 1:0 DE | (-6.73,11.01) | 0 | **99** (168 st=1 fading) | 0302 | ff03 |
| 1:2 DT | (3.91,12.50) | 0 | 74 (151 st=0) | 0097 | 000f |
| **1:3 DE** | (3.77,6.28) | **6** | **147 st=3 LIVE PAIR** | 000a | **0012** |
| 1:4 LB | (-9.44,27.49) | 0 | **33** (zone drop) | 0102 | 0104 |
| 1:7 CB | (-12.31,21.49) | 0 | **160** | 0103 | ff02 |
| 1:8/1:9 S | deep | 9 | 74 (157 st=0 queued) | 0302 | ff01 |

## 1. Name table: NONE for gameplay ids; the celebration exception

Searched: the ELF, both EE dumps (including all heap above the ELF image,
where the loaded animdata members live), GAMEDATA.DAT, PLADATA.DAT,
DB_TEAMS.DAT, TEMPLATE.DAT, UIS_*.DAT, and an ASCII sweep over every resident
animation group-data block (701 ≥6-char printable runs in the group blocks —
all packed-stream noise, zero real words).

* **A real animation name table DOES exist — for sideline/celebration
  clips.** String pool at ELF file 0x0047b0c0..0x0047c1xx (vaddr 0x0057a0c0+):
  "Packers def mini-celebration", "Walk dejected", "Jog slap helmet", coach
  reactions, TD dances, etc. Referenced by a **107-record table of
  {u32 name_ptr, u32 0, u32 anim_id} at file 0x0041c9ac (vaddr 0x0051b9ac)**,
  ids 43-374. This is what the fourcc/name-table pattern from
  slider-behavior.md finds when pointed at animations. But it is a DIFFERENT
  id namespace from the on-field one — proven by collision: the table maps
  66 → "Slap hit helmet" while ctx-1's 66 is a block interaction in the
  engine's own master list 0x00526A10 (47/56 collide the same way). Per-ctx
  resolution (§2) is how both coexist, so **this table names nothing an
  on-field ctx-1 player plays**.
  Duplicate names with different ids and vice versa (e.g. three "Slap hit
  helmet" rows) — it maps trigger situations to clips, not a 1:1 registry.
* The on-field clip data (ANIMDATA.DAT members) contains **no strings at
  all** — verified against every resident group block in ee_inplay.bin.
  ANIMDATA.DAT itself is not in extract/ (disc path "/DATA/ANIMDATA.DAT",
  seen at 0x006cdf80 in-play; loader handle 0x00600B30).
* PLADATA/GAMEDATA/DB_TEAMS/TEMPLATE hits are all player surnames, play
  names ("HB Sprint", "Weak Blitz"), minicamp drill names ("BlockingHurdle",
  "SledStiffArm"), and credits. UIS files: nothing.

**Verdict: the id→meaning question cannot be closed by strings for ctx-1
clips; it is closed (as far as it is) by §0's live reads plus §2's registry
walk.** Lane 3's "no name table" claim was right for gameplay ids, but it
missed that the pattern DOES exist in the celebration vocabulary.

## 2. The registry, decoded against known ids

Walked in ee_inplay.bin (count [0x00608098]=81, table [0x006080B0]=0x00F848F0,
28-byte rows {+0 member#, +0xC group data, +0x12 type, +0x14 ctx}). One
correction to lane 3: **in the 16-byte index entries the id is at +0x00**
(u16), not +0x04 — `93 00 08 00 | ptrA | ptrB | ptrC` = id 147, hw 8, three
pointers. Layout (all re-derived from `0x003a9368`/`0x0018d488` this pass):

```
entry+0x00 u16 id          entry+0x04 ptr A: per-id section list
entry+0x02 u16 tag         entry+0x08 ptr B: variant list
                           entry+0x0C ptr C: (B+4 for most g1 singles)
variant list B: u16 count @+0; 8-byte items {u16 tag @+4+8k, ptr rec @+8+8k}
  tag bit15 set -> tag&0x7FFF is a component id resolved via the same index
component record: u16 participant count @+4; 20-byte rows @+8+20p:
  {u16 role-seq# @+0, u8 flag @+2 (0=required), BAM24 @+4, float relx @+8,
   float relz @+0xC, u8 role @+0x10}   (accessors 0x0018d488/0x0018d548)
variant/param records (also table at gd+0x28: {u32 count; {u32 key, ptr}}):
  TYPED: u32 hdr {u16 type, u16 size}:
  type 0x0B (12 B)  = class record: u32 class @+4  (the byte the selector
                      0x0018d998 filters on req+0x42)
  type 0x09 (60 B)  = ROOT-MOTION SPEC (§3)
  type 0x0C (24 B)  = throw-family param (g13); type 0x0A (4 B) = flag (g2)
```

Where the known ids live (ctx-1 groups, live registry):

| group | member | top-level ids | family (evidence) |
|---|---|---|---|
| g1 | 0 | 21-48, 68-92, 98-117, 152-155, 166-167, 171-172 | base set: stances, solo moves, route/shed singles |
| g2 | 8 | 74, 157, 159, 160 | locomotion + DB/pursuit-tackle |
| g3 | 9 | 140-145 | kick/punt (has type-9 root motion, §3) |
| g12 | 1 | 40, 67, 84, 103, 156 | solo scripted moves (type-9 carriers) |
| g13 | 2 | 69 | QB throw (entire member for one id) |
| g14 | 3 | 42-44, 97, 99, 105, 165, 174 | shed/disengage finishes (99 live on a DE leaving a block) |
| g15 | 4 | 49-66, 106-108 | kind-4 grid segment family |
| g16 | 5 | 93-96, 164 | sack/QB-hit family (UNVERIFIED label) |
| g17 | 6 | 118-131 | shed-contest outcome pairs |
| g18 | 10 | 146-151, 168-170, 173 | yes-set converter pairs |
| g19 | 11 | 158, 161 | capture hold + directional drive |

Same numeric ids recur in other ctx groups (ctx 2 = g4 member 34, ctx 3 = g0
member 41 with the celebration vocabulary, etc.) — **an id only means
something per ctx**; all 22 on-field players are ctx 1.

### id→meaning table (as established this pass; source in parentheses)

| id | meaning | source |
|---|---|---|
| 21 | NT four-point/heavy stance | live slot-9 + ee_inplay (NT only) |
| 28 | LB two-point ready stance | ee_inplay (all 3 LB) |
| 33 | coverage backpedal/zone drop | slot 8 (LB alone mid-zone) |
| 61 | engage starter, blocker side | slot 8 (lone OL, kind 0) + lane 1 map |
| 69 | QB throw | slot 8 (QB at release; g13 sole id) |
| 74 | upright locomotion base (stand/run) | slot 8 (every open-field mover) + ee_inplay CBs |
| 85 | two-point stance (WR/HB/S) | ee_inplay |
| 86 | three-point stance (OL/TE/FB/DL) | ee_inplay |
| 91 | QB under-center stance | ee_inplay |
| 99 | shed/disengage finish | slot 8 (DE leaving faded 168 block; g14) |
| 144 | punt/kick approach (0.79 yd, 128 frames type-9 spec) | g3 data + slot roster |
| 147 | two-man engage-and-hold block (no root motion; §3) | slot 8 LIVE pair, kind 6 |
| 153 | route/release move (solo, state-16 family) | slot 8 (st=1 on route WRs; g1) |
| 157 | pursuit/tackle prep | slot 8 (queued on both deep safeties; g2) |
| 160 | DB break/close-on-ball | slot 8 (CB driving on throw; g2) |
| 168 | odd-converter block clip, fades to 74/99 on shed | slot 8 (LT+DE both st=1) |
| 158/161, 146-151, 168-170, 173, 118-131, 49-66, 106-108 | per lanes 1/3 (selector semantics; unchanged) | prior lanes |

What distinguishes the stance records in g1: nothing labels them — same
3-variant shape, different role-seq#s and one trailing float (0.2 for id 86
vs 0.8 for 85/91, meaning UNVERIFIED). The registry carries **no per-id
semantic marker of any kind**; meaning comes only from who plays it and
which code requests it.

## 3. Root motion: the static spec exists — but not for block pairs

**The type-9 record is the spec `0x0018f9e0` consumes**, field-for-field:

```
+0x00 u32 {type=9, size=0x38}   +0x14 vector2 (3 float)
+0x04 u32 role-seq#             +0x20 float duration (frames)
+0x08 vector1 (3 float)         +0x38 final-heading term (BAM24)
```

`0x0018f9e0(player, block, spec)` rotates vector1 by the player's heading,
adds to position, divides by duration, and writes per-frame deltas into the
**motion block at anim-slot+0x10** ({dx, dz, dheading, frames} at
+0x10/+0x14/+0x18/+0x1C of the 0x64-byte slot; `0x003ad3d0` returns
slot+0x10 — its `jr ra` delay slot is `addiu v0, a0, 16`, which lane 3's
read missed). Applier `0x0018f980` then steps +0x190/+0x194/+0x1A8 while
`[block+0xC]` counts down. Player flag +0xC bit 0x200 = burst owns him.

**The ranking the mission asked for, as far as static data allows:**

| ids (ctx 1) | static displacement | duration | verdict |
|---|---|---|---|
| g12 solos, tag-0 variants (84 is the sole tag-0-only id; 103/156 also carry it; role seqs 56-59) | **1.65 yd** | 30 f | largest static spec in ctx 1 |
| 140/141/143/145 (kick family) | 0.90-1.26 yd | 68-156 f | kick/punt approach steps |
| 144 | 0.79 yd | 128 f | punt approach |
| 40, 67 (g12) | 0.00 yd | 32 f | in-place solos |
| **158, 161, 146-151, 168-170, 173, 118-131, 49-66, 106-108 (ALL pair-block families)** | **no type-9 record exists** | — | class-tag records only |

**Consequence, stated plainly: per-variant displacement for the two-man
block vocabulary is NOT expressible as a static spec lookup — g15-g19's
variant tables carry only class bytes.** Pair root motion must therefore
come from the packed per-role sequence streams (the 84-byte-row sections at
gd+0x10, still undecoded — lane 3 §5's wall, now with the reason visible:
the spec framework exists and the block families simply don't use it) or
arrive as zero. Empirical support: the LIVE 147 pair (kind 6) has motion
block zeroed, frames=0, both bodies holding at 1.3 yd — an engage-and-hold
clip with no scripted drive. The flag bit 0x200 is set on both, so a burst
DID run at attach and completed. UNVERIFIED: whether any g18/g19 variant's
stream carries net root translation (the §4 probe in lane 3 remains the way
to measure it live).

**Adjudication of lane 1 vs lane 3 (the mission's dispute):** the live
+0x3DE sequences (§4) show, on one run play: g18 converter clips first
(lane 1's 146-151/168-173 margin rolls DID fire), g17 shed outcomes second
(118-131 — cpu-dt-animations' drive/pancake vocabulary DID get selected),
then g19 capture (158, per the hardcode) or g15 grid segments (lane 1's
49-66). Lane 3's 161 cannot be confirmed or excluded from a group stamp
alone (§4 caveat) but nothing requires it; **the observed driving-family
traffic is lane 1's picture, with lane 3's class table confirmed at the
data level** (161's 24 variants: keys 0xA22C-0xA243 → classes exactly
{18,16,14,17,16,10,14,7,17,16,10,7,17,10,7,18,10,16,7,14,18,10,16,14}).
Both lanes over-claimed exclusivity; neither is wrong about its own path.

## 4. +0x3DE: VERDICT — it is the registry GROUP NUMBER of the current pair/solo interaction animation

Re-derived from the launcher chain, then confirmed against both live samples:

* `0x0018d998` (matcher) returns its chosen variant via
  `sh a1, 0(out_id)` at 0x0018DDA4, where a1 = **descriptor entry +4 = the
  GROUP NUMBER** the variant came from. The descriptor entries built into
  scratch 0x00627418 by `0x003a9368` are 8-byte {**+4 u16 group**, +6 u16
  variant tag, +8 ptr record} — filled from `sh t3(group), 4(...)`. (Lane 3
  labeled +4 "tag"; it is the group.)
* Every interaction launcher stamps both men:
  `+0x3DC/+0x3DD := matched participant-row hw0` (the role/slot word — live
  147 pair: 11 vs 10, one per mirror role) and `+0x3DE := group & 0xFF`,
  `+0x3DF := group >> 8` (always 0 for real groups). Stamp sites read this
  pass: `0x0018e378` (pair→AI state 32), `0x0018df10` (solo→state 16, which
  is how route moves like 153 stamp ff01-era values), `0x0018e0a0`, and
  `0x0018e768` (the kind-4 segment try — no state push).
* The requested clip ID travels separately (state message {32, id_hi, id_lo,
  partner} built from request+0x40); +0x3DE never held it.

Decoding the mission's live slot-9 sample: TE↔DE **19 = g19** (capture 158
segments); C↔NT **17→19 = shed-contest outcome (118-131), then capture**;
LG↔#11 **18,17,19 = converter clip (146-151/168-173), shed outcome,
capture**; FB↔#14 **18,15 = converter clip, then kind-4 grid segment
(49-66/106-108)**. Slot 8 confirms independently: the live 147 pair reads
18 = g18 ✓ (147 ∈ g18), a player with 168 fading reads 18 ✓, players whose
last segment was 61 (g15) read 15 ✓, one with 161/158 residue reads 19 ✓.

* The "class" hypothesis is REFUTED: the overlap with {7,10,14,16,17,18}
  was a coincidence of small integers. The "group-local index" hypothesis is
  REFUTED likewise. **A +0x3DE of 19 does NOT distinguish 158 from 161** —
  to name the exact clip during a block, the pointer-chased slot scan
  (`u16[[p+0x304]+0x64k+4]`, status +6 == 3) is the only authoritative read,
  exactly as 4-synthesis.md's probe already does.
* Outside kinds 5/6 the four bytes are multi-writer scratch: dozens of AI
  sites store them (census run this pass; heaviest cluster in the
  shed-contest region 0x001A78xx-0x001A7Cxx), and +0x3DF := 0xFF appears to
  be an invalidation marker leaving a stale group low byte behind
  (ff01/ff02/ff03 on idle players — matching each man's LAST interaction
  family: WRs after a g1 route move, DBs after g2 pursuit). UNVERIFIED:
  per-site semantics of those non-launcher writers.

## 5. Could not establish

1. **Per-variant root displacement for the pair-block families** — no static
   spec exists (§3); the packed per-role streams (gd+0x10 sections) remain
   undecoded. The halfword stream format IS now partially decoded (data
   words advance time; 0x8001 = end + total-frame latch; 0xCxxx = event
   tags via `0x003a8398`, 0xC004 = the segment-length event lane 1 found;
   iterator = {stream, time@+8, dt@+0xC, pos@+0x18, frames@+0x1C}, walker
   `0x003abfa0`) — but the pose/root channels were not reached.
2. Which participant row feeds which spec pointer at runtime
   ([iobj+p*8+8]); the interaction object could not be located in the
   slot-8 heap (callback table 0x00522F10 is static, +0x98 = 0x0018FBE8;
   the object itself is transient).
3. Why the live 147 pair's +0x3DC reads 10/11 while 147's component rows
   (8630/8631) carry role-seq 0x56/0x57 — the concrete matched row came
   from a descriptor path not reproduced statically (the variant-list walk
   at the component level, my parse of which overruns into non-list
   records).
4. Meaning of the per-id trailing float (0.2 vs 0.8) on g1 stances; the A
   section lists (entry+4); the g13 type-0x0C throw params; slot fields
   +0x24/+0x28 (stream pointers for locomotion, cookie-like for pairs).
5. HB/LB +0x3DE values 0x0404/0x0104 (slot 8) — not group stamps; from the
   non-launcher writer population (item 4 of §4). UNVERIFIED which site.
6. ee_mainmenu.bin was searched for names only; its registry (menu ctx
   groups) was not walked.

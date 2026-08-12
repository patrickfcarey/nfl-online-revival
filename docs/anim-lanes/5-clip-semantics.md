# Anim lane 5: clip semantics — naming the animation ids

Recorded 2026-08-11. Static lane, maximum effort. Sources: `extract/SLUS_207.52`
(vaddr = offset + 0xFF000, gp = 0x006056F0) via `recon.mipsdis`;
`extract/ee_inplay.bin` and `extract/ee_mainmenu.bin` (32 MB EE images,
vaddr == offset); `extract/GAMEDATA.DAT`, `extract/PLADATA.DAT`,
`extract/DB_TEAMS.DAT`; savestates in `experiments/states/` via
`tools/statereader.py`. No rig, no network, no emulator, no commits.
**UNVERIFIED** marks inference; everything else was read this pass.

Ground truth being decoded (live, slot 9 pre-snap, run play, all 22 in
stance; from 4-synthesis.md §PROBE):

    current clip u16[[p+0x304]+0x64k+4]: QB 91 | HB 85 | FB 86 | WR 85,85
        | TE 86 | OL 86 x5 | NT 21 | DE 86
    +0x3DE during kinds 5/6: TE<->DE 19 | C<->NT 17->19 | LG<->#11 18,17,19
        | FB<->#14 18,15

## Status

INCOMPLETE — being written incrementally. Sections below fill in as
established.

## 0. New ground truth mined this pass (slot 8 = ball-in-air, MID-PLAY)

`experiments/states/ball_in_air_slot8.p2s` is the one owned image taken
mid-play (ee_inplay.bin is PRE-SNAP — the "MID-PLAY" label in the tasking is
wrong; every player reads kind 0 in a stance clip). Slot 8, read via
statereader (player base 0x00661B90, stride 0x14C0):

| player | pos | kind | slot-scan clip (st=3) | +0x3DC u16 | +0x3DE u16 |
|---|---|---|---|---|---|
| 0:0 QB | (0.95,10.65) | 0 | **69** (just threw) | 0102 | ffff |
| 0:1 HB | (1.62,8.83) | 1 | 74 | 0102 | 0404 |
| 0:2/3/5 WR/TE | downfield | 0 | 74 (k2 holds 153 st=0) | 0x02 | ff01 |
| 0:4 WR | (13.22,28.78) | 0 | 74 (153 st=1 fading) | 0502 | ffff |
| 0:6 LT | (-8.33,11.69) | 1 | 74 (**168 st=1** fading) | 00ab | 0012 |
| 0:7 OL | (-0.76,15.69) | 2 | 74 (k3 holds 161 st=0) | 0003 | 0013 |
| 0:8 OL | (4.84,13.89) | 2 | 74 (151 st=0) | 0096 | 000f |
| **0:9 OL** | (2.54,6.68) | **6** | **147 st=3 LIVE PAIR** | 000b | **0012** |
| 0:10 OL | (2.64,10.63) | 0 | **61** | 00b6 | 000f |
| 1:0 DE | (-6.73,11.01) | 0 | **99** (168 st=1 fading) | 0302 | ff03 |
| 1:2 DT | (3.91,12.50) | 0 | 74 (151 st=0) | 0097 | 000f |
| **1:3 DE** | (3.77,6.28) | **6** | **147 st=3 LIVE PAIR** | 000a | **0012** |
| 1:4 LB | (-9.44,27.49) | 0 | **33** (zone drop) | 0102 | 0104 |
| 1:7 CB | (-12.31,21.49) | 0 | **160** | 0103 | ff02 |
| 1:8/1:9 S | deep, kind 9 | 9 | 74 (157 st=0 queued) | 0302 | ff01 |

Immediate semantic yields (all VERIFIED as "this id was playing while the
snapshot shows the situation"):

* **74 = the upright locomotion/base clip** — every player in open-field
  motion plays it (WRs mid-route, safeties in pursuit, QB dropback HB); CBs
  standing pre-snap in ee_inplay also hold it. The universal mover.
* **69 = QB throw** (QB one frame family after release; group g13/member 2
  exists solely for id 69).
* **147 plays LIVE on an engaged OL/DE pair in kind 6** — first live
  confirmation that a yes-set id is a real two-man block clip. Both men read
  +0x3DE = 0x12 = 18 while the slot scan reads 147.
* **33 = coverage backpedal/zone drop** (LB alone in the middle third).
* **160 = DB break/close-on-ball family** (CB driving on the throw;
  157 queued on both deep safeties, g2 = {74,157,159,160}).
* 153 (st=1 on route-runners) = a route/release move that just finished;
  168 st=1 on LT and his DE = the odd-converter clip fading out on both.
* 61 active on a lone OL (lane 1's "engage starter" id) — consistent.
* Stances (ee_inplay, all 22, kind 0): **91 QB under center, 85 two-point
  (WR/HB/S), 86 three-point (OL/TE/FB/DE/DT), 28 LB two-point ready, 21 NT
  (g1 carries it; the NT's low four-point variant), 74 upright (CB)**.

## 1. Name table search

(pending)

## 2. Registry decode against known ids (86 OL stance, 21 NT, 91 QB)

(pending)

## 3. Root-motion magnitudes and the displacement ranking

(pending)

## 4. What +0x3DE means

(pending)

## 5. Could not establish

(pending)

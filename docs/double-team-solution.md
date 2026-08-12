# Double teams: diagnosis and fix design

Recorded 2026-08-11 by the escalation agent. Static analysis against
`extract/SLUS_207.52` (vaddr = file offset + 0xFF000, gp = 0x006056F0) via
`recon.mipsdis`, plus mining of the two result files that landed mid-run:
`extract/slot9_baseline_dt3.jsonl` (3 iterations, byte-identical) and
`extract/slot9_kind4diag_dtk4c.jsonl` (2 iterations, byte-identical). No rig,
no network, no emulator, no commits. Every load-bearing instruction below was
read from the ELF this pass; every measured number below was computed from the
jsonl this pass. Claims not verified this run are marked UNVERIFIED.

## The diagnosis in three sentences

The double team never dies of a timer, a duration, or a teardown rule of its
own: every registry record on slot 9 is killed by the **per-frame 1-on-1
assignment market** (`0x001f4790` phases 1-3, manager slot `0x001f72d0`),
which re-shops record members every frame with zero double-team awareness and
re-points them — the registry manage/drive functions then merely *complete*
the death their own invariants demand. The second man can never attach
(kind 7 never becomes kind 8) because the attach gate `0x001f7590` requires
the primary to be in **raw contact (kind 4)** at the same moment the helper is
within 2.1 yd — and the market re-shuffles the participants faster than that
coincidence can occur, on this play, every time. The repair machinery that
would drag strays back (`0x001f6858`/`0x001f68a4`), the outcome-based peel
triggers in the drive pass, and the kind-8 mechanical package (defender debuff
−0.13 per 16 frames + the primary's hold re-armed at `0x001f2230`) all already
exist, are reachable, and work — the fix is to stop the market from re-shopping
the members of a live record, one guard at the market's commit point.

---

## 1. Q1 — the measured kind lifecycle on slot 9 (from the jsonl)

Entities: TE = `player:0:5` (pos 4), RG = `player:0:9` (pos 8),
RT = `player:0:10` (pos 9), DE = `player:1:3` (pos 10), LB = `player:1:6`
(pos 13), NT = `player:1:5` (pos 14, at y = 19.01), and `player:1:0`
(pos 14 label per sample, second interior DL). `engagement_link` decodes as
`(index<<16)|(side<<8)|1`. All five runs (3 baseline + 2 kind4diag iterations)
are frame-identical in kinds, links, and roles except where noted.

**The published windows 2..36 / 2..43 / 27..43 were unions.** The registry
actually forms **four short records**, each torn down within samples of an
assignment change:

| record | members (role) | alive (sample frames) | death (between samples) |
|---|---|---|---|
| R1 | TE(0) + RT(1) on DE(2) | 2..16 | 16→17 |
| R2 | RG(0) + TE(1) on LB(2) | 27..33 | 33→34 |
| R3 | RT(0) + TE(1) on DE(2) | 35..36 | 36→37 |
| R4 | RG(0) + TE(1) on LB(2) | 38..43 | 43→44 |

Kind timelines (baseline it0; identical in all iterations):

* TE: `2/DE` 2..26 → `7/LB` 27..32 → `7/DE` 33..36 → `2/NT` 37 → `7/LB`
  38..46 → churns (`2` on rotating targets) → 0 after 157.
* RG: kind **2 for 156 straight frames** (2..157), target churning:
  LB(2..16) → DE(17) → NT(18..26) → LB(27..33) → NT(34..36) → LB(37..43) →
  p1:0(44..49) → LB(50+). The RG never contacts anyone all play.
* RT: `7/DE` 2..17 → `4/DE` 18..36 (mutual with DE) → `5/DE` 37..77 (state 32
  animation) → 4 78..79 → 6/5 through 199. A stable 1-on-1 for ~180 frames.
* DE: `9/TE` 2..16 → `9/RG` 17 → `4/RT` 18..36 → animation kinds 5/6 with RT.
* LB: kind 9 flapping with each blocker (re)assignment; ai_state 2 (pursuit)
  from 27 to 197. NT (p1:5): ai_state 38 until 36, **2 from exactly frame 37**
  (both arms).

**Kind 8 appears nowhere in either arm.** `two_man_state_players` = 10 = five
engaged blocker+defender PAIRS in state 32 — the "two-man animation" is one
blocker plus one defender (interaction ids 146..173; the RT-DE pair plays 158).
The mission brief's UNRECONCILED item is resolved: ten men in state 32 is the
ordinary block-animation population, not double-team evidence.

## 2. Q2 — what kills each record, at instruction level

### 2.1 The market (chooser) — the initiator of every death

`0x001f4790` (sole caller `0x001f55c8`, per frame in manager slot
`0x001f72d0`, which runs **before** the DT pass `0x001f72d8`) rebuilds the
blocker/defender lists from zero every frame (`0x001f5510` → `0x001f2ea0` /
`0x001f2cd8`), re-scores every blocker against every defender (phase 1,
`0x001f48c8-0x001f497c`, rand(75)-loaded composites), resolves same-target
conflicts (phase 2), and **commits re-assignments** (phase 3):

```
001f4a20  8c44005c  lw a0, 92(v0)        ; chosen target player
001f4a24  0c04ede6  jal 0x0013b798       ; resolve target+0x3E4
001f4a2c  0040802d  daddu s0, v0, zero   ; s0 = target's CURRENT man
001f4a30  8e82005c  lw v0, 92(s4)        ; this blocker
001f4a34  5202001e  beql s0, v0, 0x001f4ab0  ; already his -> refresh only
001f4a3c  52000008  beql s0, zero, 0x001f4a60
001f4a44  0c07dd0a  jal 0x001f7428       ; THEFT: tear the target's man off
001f4a54  ...       SetEngagement(old man, NULL, 1)   ; release him to idle
001f4a90  0c07dce6  jal 0x001f7398 ; a2=2 ; SetEngagement(blocker, target, 2)
001f4aa4  0c07dd32  jal 0x001f74c8 ; a2=9 ; SetPartner(target, blocker, 9)
```

`0x001f7398` always calls `0x001f7428` first, which zeroes the old partner's
kind/link via `0x001f7540` when the old partner pointed back — this is what
produced every observed LB/NT kind-9→0 flap. Census (all 66 `jal` sites of
both setters, a2 traced): the chooser's `a2=2` at `0x001f4a90` is the **only
per-frame market re-targeter** in the image; the other kind-2 writers are
tick-expiry (`0x001f5ca0`) and the DT repair/handoff paths themselves.

The census-grade fact from `double-team-mechanism.md` §4 stands re-confirmed:
none of this reads a single double-team byte. The one ×0.75 dt-aware haircut
sits in scorer `0x001f3a00` and cannot prevent a re-commit.

### 2.2 The registry's own logic — gate, cleanup, repair

Manage `0x001f6640` (per record): **if rec+12 (peel slot) is empty, it does
nothing at all** — `001f6744 beql a0, zero, 0x001f6904` skips invariants,
kind checks, teardown, everything. The peel slot is stamped by peel-detect
(`0x001f66c8-0x001f6734`) only when a *member's* +0x3E4 has left the record
defender and the new man has dt_role 5. Then four invariants run
(`0x001f674c-0x001f67a8`: primary on defender-or-peel, helper on
defender-or-peel, defender on a member, peel man on a member), a primary-kind
jump table (`0x00583A80`: kinds 2,3,4,7,8 → helper checks at `0x001f67f4`;
kinds 5,6 → `0x001f67d8` = predicate `0x001f0ba8`, zero → teardown), helper
kind checks (<2, ≥9, or 5/6 → teardown), and then **repair**: a strayed
primary is torn off his new man and re-pointed at the defender
(`0x001f6858-0x001f687c`, kinds 2/9), a strayed helper is re-pointed at the
defender as kind 7 (`0x001f68a4-0x001f68b8` — the sole in-manage kind-7
writer). Cleanup (`0x001f68c4-0x001f68f4`): if the peel man reciprocates a
member (`beql v1, a1` / `beq v1, v0`) → full teardown `0x001f65b8`; otherwise
un-label (role 5, slot cleared) and the record survives.

Teardown `0x001f65b8` itself: in-use := 0, all four handles zeroed, role 5 to
each member. **It does not touch kinds or +0x3E4** — record death never ends
an engagement.

### 2.3 The four deaths, adjudicated

* **R1, 16→17.** Frame-17 chooser swaps TE→LB and RG→DE (links in the data;
  DE's reverse marker flips 9/TE → 9/RG). Frame-17 manage: peel-detect stamps
  LB (TE's new man, role 5) into rec+12; invariants: TE on peel ✓, RT on
  defender ✓, **defender DE points at RG — not a member** → cleanup; peel man
  LB's +0x3E4 = TE = rec+0 → `beql` at `0x001f68c8` → teardown. RT, still
  kind 7 inside the 2.1-yd radius (d = 1.46), is orphaned; he contact-locks
  DE as an ordinary single block at 18 (mutual 4/4) and keeps him ~180 frames.
* **R2, 33→34** and **R4, 43→44.** The chooser re-targets the primary RG
  (LB→NT at 34, LB→p1:0 at 44); his SetEngagement teardown zeroes the LB's
  reverse marker (LB kind 9→0 in the data at exactly those frames);
  peel-detect stamps RG's new man; invariant 3 fails (LB points at nobody);
  cleanup finds the new man reciprocating RG (9-back) → teardown. At 33 the
  helper-side flap (TE `7/LB`→`7/DE`, re-pointed by the helper-assign block)
  had already stamped-and-survived once: DE's partner was RT, no member, so
  cleanup **un-labeled and the record lived** — measured proof the cleanup's
  survive path works.
* **R3, 36→37 — the only record whose primary was ever in contact.**
  Initiator in both arms: the frame-37 chooser captures TE (kind 7→2, link
  DE→NT; NT's 9/TE marker appears in the data at 37). Completer differs by
  arm and is the strongest cross-arm result in the dataset:
  * **baseline**: RT+DE entered animation 158 (kinds 5/5, state 32) between
    36 and 37. Manage: invariants pass (traced), primary kind 5 → arm
    `0x001f67d8` → `0x001f0ba8(RT, DE)`: mutual ✓, same interaction ✓, but
    **animation 158 is in the table's NO-set** (`0x00583360`: yes = states
    {146-151, 168-170, 173}, read this pass; 158 → `0x001f0c24` = return 0)
    → teardown at `0x001f67ec`.
  * **kind4diag**: RT still raw kind 4 (animation delayed to 73 by the
    90-tick hold). Manage invariants pass → helper TE kind 2 → **repair
    fires** (`0x001f68a4`: TE re-pointed 7/DE) with rec+12 = NT still
    stamped. Drive `0x001f6940` then runs the break chain:
    * B0 (play counter ≥ 61 at `0x001f6a70-78`): counter is the **play**
      clock at table+84 (single writer `0x001f5b9c`, once per frame,
      play-scoped — not per-record). At the four deaths it read ≈17/34/37/44,
      all < 61 → **B0 never fired on slot 9**, including for the record
      formed at 27 (would have needed the death at play-frame 61, observed
      34/44).
    * B1 (`defender.pos_y < helper.pos_y`, `0x001f6a80-90`): DE.y 16.67 vs
      TE.y 16.27 at 37 → false (computed from the data).
    * B2 (peel man within 2.0 of the ball line, `0x001f6a98-6ab8`; the line
      is `*(0x00601f4c)+16` = **15.0 = the LOS**, read from ee_inplay.bin):
      NT.y 19.01 − 15.0 = 4.01 → false.
    * B3/B4 (facing-difference ≤ 65.01° helper↔defender / primary↔defender,
      `0x00469fc8` = min(d, 2²⁴−d) on +0x1A8): **not computable offline**
      (facings unsampled); engaged-opposed pairs sit near 180°, so presumed
      false — UNVERIFIED.
    * **B5 (peel man's ai_state ∈ {2, 30}, `0x001f6b00-6b18`): the NT's
      state flips 38 → 2 at exactly frame 37 in both arms.** Given B0-B2
      false by arithmetic, B5 is the arithmetically live killer.
    The drive teardown's exit then re-anchored the primary (RT 4→2→re-locked
    4 within the same/next frame, invisible at 1-frame sampling) and handed
    TE to the NT as kind 2 — the sampled `TE 2/NT` at 37 in both arms.

So the "36/43 contact-time decision" was three deaths by manage-cleanup after
market re-targeting, plus one death (R3) whose completer is either the
158-NO-set arm or the phantom-peel B5 — in both cases *initiated* by the
market capturing the helper. The timer graveyard extends: the kind-7
up-count-to-61 (`0x001f5c10-38`) never fired either (longest kind-7 stretch
16 ticks), and DT-HOLD-90's T4 readings of +0x432 = 17/15/6 "in-window" were
**tick up-counts** (kinds 7 and 2 count up from the last SetEngagement zero;
`0x001f73cc`), not formula stamps — the formula stamps only at kind-4/8
lock-ins, which these players never had.

## 3. Q3 — why kind 7 never becomes kind 8 on the run

The attach is Site A's 7/8 band (`0x001ef918` copy → s2 := 8 at
`0x001ef93c`) behind `0x001f7590`, which for kinds 7/8 requires **all** of:

1. the defender's engaged man exists and holds **kind 4** (raw contact) — or
   shares a yes-set animation with the defender (`0x001f75fc-0x001f7610`,
   `0x001f0ba8`); the standard block animation **158 is NO-set**, so once the
   primary's pair is captured into state 32 the gate is shut for good
   (`docs/dt-lanes/run-pass-contrast.md`, confirmed here by the table read);
2. dist(helper, defender) < 2.1 (`0x005ff1c0`, read: 2.0999999) — with a
   play-class-3 bypass (`0x0015ada0()==3` && sub-mode 6, `0x001f7630-64`),
   i.e. a pass-only shortcut;
3. the geometric admission `0x001f7698`: approach-facing within 40° (65° if
   both already kind 4), defender bearing within 130° of the reverse
   approach, a second distance gate (`0x005ff1c4`), then near-ball-box
   (`0x001efb20`) OR facing-difference > 85°.

Measured against the data: RT held kind 7 **inside the 2.1 radius from frame
~10** (d = 2.00 → 1.46) but his primary TE never reached contact (kind 2 for
his whole window) — condition 1 never true. The only frames when a kind-7
helper coexisted with a kind-4 raw-contact primary within 2.1 yd are TE-on-DE
at 34..36 (d = 0.98/0.95/0.91, RT-DE mutual 4/4): five runs sampled those
frames and **no 8 was ever seen**, so either admission (3) rejected —
which branch is UNVERIFIABLE offline (facings unsampled) — or the attach was
made and destroyed sub-frame (considered unlikely: the same sampler catches
8s on slot 7). Then the market captured TE at 37 and the window closed.

The pass side needs no separate mechanism: pass sets hold the primary in raw
kind 4 continuously (capture to state 32 is mode-gated off while the QB
holds — `docs/dt-lanes/run-pass-contrast.md`, UNVERIFIED here), so condition
1 is always true, the helper flaps 7↔8 on the kind-8 tick expiry
(`0x001f5c44-6c`: decrement → SetEngagement(helper, NULL, 1) → re-assigned 7
next frame), and the registry never exists to be torn down (seek requires
+0x3F0 == 2 at `0x001f6560`). Kind 8's mechanical package (Site B
`0x001f20f8`) is: helper staged_drive := 0 (`0x001f2164`) and speed := 0
(`0x001f21a8`) faced at the defender, defender −0.13 twice per 16-frame lock
(`0x001f21c8/21d8`, re-armed at `0x001f21e4`), and **the primary's kind-4
countdown re-stamped to full** (`0x001f2230`) — "the block never times out
while help is attached". The is-two-man-animation question resolves: state 32
is not required for a sustained double; it is the *single-block* contact
animation, its id (158) currently *blocks* the attach, and the double team's
designed sustain is the 7↔8 kind cycle plus the registry's protection.

One more structural fact that shapes the fix: **an intact record does
nothing per frame** — manage no-ops (peel slot empty) and drive only runs
break tests. The registry is a peel scheduler and repair authority, not a
drive source; the visible double team lives entirely in the kind system.

## 4. Q4 — the fix

### The design principle

Every death traces to the DT-blind market re-shopping a live record member
(or stealing his defender). The engine already contains the counter-forces
(repair, un-label survive path, outcome-based peels, attach machinery).
Therefore: **guard the market's two commit points on `dt_role`, and touch
nothing else.** dt_role is 5 for every player outside a live record (per-play
reset `0x001f7098`; registration writes 0/1/2 at `0x001f6490-9c`; teardown
restores 5 at `0x001f6600`) — so the guards are structurally inert on every
snap that never registers a double (all pass plays via DT-1, all singles):
R5 by construction, not by tuning.

### P1 — the market commit guard (RECOMMENDED, the one patch)

Hook: `0x001F4A30` (`8e82005c lw v0, 92(s4)`) → `j 0x00139AA0`
(`0804E6A8`). Cave (cave #1 pool, clear of the worked example's
0x00139A68..8C; all words round-tripped through `recon.mipsdis` this pass):

```
patch=1,EE,001F4A30,word,0804E6A8   // j 0x00139AA0   (was lw v0, 92(s4))

patch=0,EE,00139AA0,word,8E82005C   // lw    v0, 92(s4)      ; displaced: this blocker
patch=0,EE,00139AA4,word,90410437   // lbu   at, 1079(v0)    ; his dt_role
patch=0,EE,00139AA8,word,2C210002   // sltiu at, at, 2       ; role 0/1?
patch=0,EE,00139AAC,word,10200006   // beq   at, zero, +6    ; not a member -> theft check
patch=0,EE,00139AB0,word,00000000   // nop
patch=0,EE,00139AB4,word,8C4303E0   // lw    v1, 992(v0)     ; member: his kind
patch=0,EE,00139AB8,word,2463FFFE   // addiu v1, v1, -2
patch=0,EE,00139ABC,word,2C630007   // sltiu v1, v1, 7       ; kind 2..8 = live engagement
patch=0,EE,00139AC0,word,14600009   // bne   v1, zero, skip  ; live member -> don't re-shop
patch=0,EE,00139AC4,word,00000000   // nop
patch=0,EE,00139AC8,word,12000005   // beq   s0, zero, resume ; target unengaged -> no theft
patch=0,EE,00139ACC,word,00000000   // nop
patch=0,EE,00139AD0,word,92010437   // lbu   at, 1079(s0)    ; target's current man's role
patch=0,EE,00139AD4,word,2C210002   // sltiu at, at, 2
patch=0,EE,00139AD8,word,14200003   // bne   at, zero, skip  ; stealing from a member -> no
patch=0,EE,00139ADC,word,00000000   // nop
patch=0,EE,00139AE0,word,0807D28D   // resume: j 0x001F4A34  ; original flow, v0/s0 correct
patch=0,EE,00139AE4,word,00000000   // nop
patch=0,EE,00139AE8,word,8E820000   // skip:   lw v0, 0(s4)  ; chosen-target entry
patch=0,EE,00139AEC,word,0807D2AC   // j 0x001F4AB0          ; engine's own satisfied path
patch=0,EE,00139AF0,word,00000000   // nop
```

The skip path is the engine's existing "already engaged to the chosen
target" path (`0x001f4ab0`: score-memo 5.0 into the target entry +
`0x001f5418` list-compaction) — no novel state is invented. The kind∈2..8
condition deliberately lets a released (kind-1) member be re-acquired by the
market, because manage's repair (`0x001f68a4`, proven reachable) then drags
him back to the defender as kind 7 — the fix uses the engine's own healing.
Register safety: `at`/`v1` are dead at the hook (next writes precede reads:
`v1` at `0x001f4a60`); `v0` is re-established for both continuations; the
`beql` delay slots at `0x001f4a34/4a38` behave exactly as stock.

Why P1 alone covers all four deaths: R1's initiator is a member (TE role 0)
re-commit — guarded; R2/R4's initiator is RG (role 0) re-commits — guarded;
R3's initiator is TE (role 1, kind 7) captured by the chooser — guarded;
R1's DE-marker theft by RG is the theft leg (DE's man TE has role 0) —
guarded. The helper-block flap (TE `7/LB`→`7/DE` at 33) is *not* guarded by
P1, but the data itself proves that flap alone is survivable (the un-label
path saved R2 at 33); P2 exists to stop it anyway.

### P2 — the helper-block commit guard (companion)

Hook: `0x001F4BC8` (`c6800064 lwc1 f0, 100(s4)`) → `j 0x00139B00`
(`0804E6C0`).

```
patch=1,EE,001F4BC8,word,0804E6C0   // j 0x00139B00   (was lwc1 f0, 100(s4))

patch=0,EE,00139B00,word,8E81005C   // lw    at, 92(s4)      ; best helper candidate
patch=0,EE,00139B04,word,90210437   // lbu   at, 1079(at)    ; his dt_role
patch=0,EE,00139B08,word,2C210002   // sltiu at, at, 2
patch=0,EE,00139B0C,word,14200004   // bne   at, zero, skip  ; live member -> leave him be
patch=0,EE,00139B10,word,00000000   // nop
patch=0,EE,00139B14,word,C6800064   // lwc1  f0, 100(s4)     ; displaced
patch=0,EE,00139B18,word,0807D2F3   // j 0x001F4BCC          ; resume at the c.lt.s
patch=0,EE,00139B1C,word,00000000   // nop
patch=0,EE,00139B20,word,0807D2FD   // skip: j 0x001F4BF4    ; engine's "not better" path
patch=0,EE,00139B24,word,00000000   // nop
```

Consequence accepted and documented: when the best-scoring candidate for some
victim is already a record member, that victim gets no helper that frame
(the selection is not re-run). On slot 9 this is football-correct — there is
no free third blocker.

### P3 — disable the state-{2,30} phantom-peel trigger (staged)

`0x001F6B1C`: `24160001 addiu s6, zero, 1` → `00000000 nop`. The drive pass
no longer tears a record down merely because the man stamped in rec+12 is in
ai_state 2 or 30; the motion trigger (flags bit 14 + heading test,
`0x001f6b20-44`) and the LOS-fill trigger (B2) remain. This implements the
requirements' own ruling ("declaration may only ever fire as a trigger once
the down lineman is secured; it can never be the primary predicate").
**Predicted null alone on clean slot 9** — baseline R3's completer is the
manage 158-arm, not B5 — its value is protecting the fixed world's transient
repair-cycle stamps. Deploy with its own S0, expect no standalone movement.

### P4 — raise the 61-frame record ceiling (staged, R6c enabler)

`0x001F6A74`: `2C42003D sltiu v0, v0, 61` → `2C420169 sltiu v0, v0, 361`.
B0 alone never fired at baseline (all records dead by 44), so this is also a
predicted null alone; combined with P1+P2 it is what lets a dominant-nose
double hold past one second (R6c) while B1/B2/motion/65° peels stay in
charge — peel on outcome, not on a clock. The seek's 60-frame *formation*
window (`0x001f651c`) is deliberately untouched.

### Alternatives evaluated and not chosen

* **`0x001f2f1c` sltiu widening (help-score lane)** — real lever, wrong
  coverage: it removes kind-7/8 men from the market's *blocker list*, which
  addresses only R3's class of death. R1, R2 and R4 are **primary-side**
  re-commits (kinds 2/3 — inside any kind-based filter), so the word cannot
  save three of the four records. It is also kind-scoped rather than
  registry-scoped: it changes every kind-7 player on every play, including
  slot 7's pass helpers (R5 exposure P1 does not have). Keep as a fallback
  if the cave route stalls.
* **Adjudication the help-score lane asked for**: the jsonl says *market
  re-shop*, 4 of 4 deaths, both arms — the death signature is always a
  member's `engagement_link` changing to a new man (with kind 2 staying 2,
  or 7→2), never kinds 5/6 appearing on the helper (the helper's kind at
  death is 7 or 2 in every case; the one 5/6 co-occurrence, baseline R3, has
  a kind4diag twin with no 5/6 until frame 73 that dies at the same frame).
  The `c.lt.s` flip theory stays refuted (its false path is write-free —
  confirmed: no store between `0x001f4bcc` and the loop tail on that path).
* **Yes-set +158 data edit** (`0x00583390`: `001F0C24` → `001F0C20`, from
  run-pass-contrast): opens the attach and the manage kind-5/6 arm during
  the standard contact animation. Stops none of the four deaths (all occur
  before/independent of the animation), so not the primary patch — but it is
  the natural **phase 2** once P1/P2 hold records together, because it turns
  "helper attached while the primary animates" from unsatisfiable into
  legal, extending the attach cycle deep into the play. Blast radius: the
  only consumers of the table are `0x001f0ba8`'s two callers (manage 5/6 arm
  and the attach gate) — both record/attach-scoped; inert on pass (pass
  primaries hold raw kind 4; capture is mode-gated off). UNVERIFIED live.
* **Force 7→8 / widen 2.1 / relax `0x001f7698`** — rejected: attaches from
  arbitrary geometry produce the Site-B freeze wherever the helper happens
  to stand (a statue at 3 yd), and none of it stops the market from
  re-shopping the primary. The admission constants (`0x005ff1c0/c4`, the
  40°/65°/130°/85° set) are sole-purpose data words and remain available as
  tuning knobs if the fixed world shows the attach failing persistently.
* **Retune the help-score `c.lt.s`** — refuted as a death mechanism (above);
  as hysteresis it would only slow the helper flap P2 removes outright.

### Predicted lifecycle under P1(+P2), pre-registered

R1 {TE primary, RT helper, DE} survives from frame 2: TE contact-locks DE at
~16-18 (his 2.1 crossing; d(TE,DE) = 1.95 at 17); RT — already in position
(d = 1.46-1.9, kind 7) — passes the attach gate's condition 1 the same frame
and attaches (kind 8) if `0x001f7698` admits him; Site B freezes RT on the
pile, debuffs the DE every 16 frames, and re-arms TE's hold; the kind-8
timer (30 − rating/16 ≈ 13-17) cycles 8→1→(market re-acquire, allowed by
P1's kind gate)→repair 7→8. When TE+DE are captured into animation 158, the
attach gate shuts (NO-set) and the helper cycle ends at its next expiry —
the record then dies through the reciprocation test as RT legitimately
re-targets: a clean, football-shaped end (~frame 50-60) unless the +158 edit
(phase 2) extends it. R2/R4 never form (TE is never free), leaving the RG
honestly single-assigned. **The failure mode this play actually needs fixed
— second man touches, aborts, climbs — cannot recur, because the abort was
the market's write and the market is now closed to record members.**

## 5. Q5 — acceptance and regression plan

**Hygiene first**: remove `14F8B841.dt3-helper-assign.pnach` (lanes 1/2/3
predicted inert; this run *measured* it inert — the five jsonl iterations
were taken with it deployed and reproduce the pre-patch windows exactly) and
`14F8B841.dt-diag-kind4.pnach` (R5-violating diagnostic). Take one clean
slot-9 baseline to re-anchor.

**Cave liveness (per code-caves.md test 1, gates all cave work)**:
execute-breakpoints on `0x00139AA0` and `0x00139B00` regions, unpatched,
through boot → menus → a quarter → save/load: must never trip.

**S0 word read-backs before every arm** (PINE): `0x001F4A30 = 0804E6A8`,
`0x00139AA0 = 8E82005C`, `0x00139AF0 = 00000000`; `0x001F4BC8 = 0804E6C0`,
`0x00139B00 = 8E81005C`; `0x001F6B1C = 00000000`; `0x001F6A74 = 2C420169` —
each only in its own arm. pnach `word` type (proven 32-bit); cave bodies may
be `patch=0`, site words `patch=1`.

**Arm 1 — P1 alone, slot 9, 3 iterations** (`double_team.py`):

* T4-style execution proof: TE's `engagement_link` at frames 17-26 stays DE
  (baseline flips to LB at exactly 17, all five runs). This single number is
  the patch-executed signal; if it flips, the cave did not run — check S0.
  Optional live-wire probe, code-caves style: set `0x00139AA8` to
  `2C210008` (protect roles < 8 = everyone) → all blocker re-targeting
  stops game-wide, visually unmistakable; revert.
* Pre-registered PASS: at least one record's roles 0/1/2 persist ≥ 60
  consecutive frames (baseline max 7 continuous); zero `engagement_link`
  changes on role-0/1 members while their record is in use (baseline: 6);
  registration count drops to 1-2 (baseline `dt_registered` = 5).
* Attach oracle (Q3's open end): **kind 8 sampled on RT** (`player:0:10`)
  at any frame — baseline has zero 8s anywhere. If records persist but no 8
  appears within 20 frames of the primary's contact-lock, `0x001f7698`'s
  admission is the confirmed blocker → escalate to the +158/admission-knob
  phase with a facing-sampling spec (+0x1A8/+0x1EC need adding to the
  harness fields).
* Direction watches (not gates): `carrier_yards` > −0.70; episode-scoped DE
  pushback ≥ 15.1 in; `two_man_state_players` ≈ 10.
* MUST-NOT-MOVE: slot 6 misdirection metrics within noise; slot 7 pass arm —
  the RG's 7↔8 flap present with baseline cadence (P1 reads dt_role, which
  is 5 for all 22 on pass; structurally inert, verified by measurement);
  slot 8: zero registrations (DT-1 untouched).

**Arm 2 — P2 alone**: TE's kind-7 link at 27..36 stays LB (baseline flips
to DE at 33). Windows may still die at 34/44 (primary churn is P1's job) —
partial by design, isolating P2's lever. Then **arm 3 — P1+P2** against the
full oracle; then P3 (S0 + predicted null alone), then P4 (S0 + predicted
null alone), then the combined stack, each on its own savestate run per
CLAUDE.md rule 2.

**Operator's eyes** (primary instrument): watch the RT/TE pair on the DE —
expected: both stay on the down lineman past the first second, the second
man plants at the pile instead of climbing at the touch; the RG will still
wander (unfixed by design, his own requirement). Failure smells: any lineman
frozen mid-field doing nothing for seconds (over-broad guard — check the
kind gate), or a defender standing unengaged while two blockers shadow him
(theft guard too strong).

## 6. What I could not establish (offline limits)

1. **Which `0x001f7698` branch rejected TE at 34-36** — facings (+0x1A8,
   +0x1EC) are not in the sample set; the admission chain is enumerated but
   the failing predicate is unknown. Same limit applies to pre-proving RT's
   attach under P1 — hence the kind-8 oracle rather than a claim.
2. **B3/B4 (65°) at the death frames** — same missing fields; B5 is
   arithmetically live and exact in timing, but B3/B4 false is presumption.
3. **Sub-frame kind-8 existence** — the sampler reads once per frame at an
   unknown intra-frame point; a one-pass-lifetime 8 could hide. Judged
   unlikely (the same sampler sees slot 7's 8s), not excluded.
4. **The intra-frame pass order** chooser → DT → re-decision → animation
   starter is proven from `0x001f7298`'s call sequence (re-read this pass);
   that the *sampler* lands between fixed passes is assumption.
5. **Whether a defender-side manager pass churns defender engagements** —
   every observed defender kind flip was explained by offense-side setter
   calls, but a defense pass was not excluded.
6. **`0x001f7c98` capture timing** (animation entry at 37 baseline / 73
   kind4diag — why those frames) — not traced; does not gate the fix.
7. **Mode words**: `0x00154790` ∈ {3,4} manager gate and the {4,7} capture
   gate meanings, and `0x00600ce8` as their shadow — carried from
   run-pass-contrast, UNVERIFIED here.
8. **+0xB04 semantics** (registration's swap wants seeker 4, other ∈ {5,9};
   `0x001efb20` treats 4..9/24 as near-ball classes) — still unlabeled.
9. **ai_state labels** 2/30/38/39 (pursuit/anchored/line-idle guesses) —
   consistent with behavior, unverified against the dispatch table.
10. **Lane-1 leftover closed**: scorer `0x001f4c40` dereferences its a2 at
    `0x001f4c80` (`lw v0, 12(s4)`) guarded only by `0x00260190()` — a null
    defender faults. Neither P1 nor P2 alters list composition or creates a
    null-resolve path into it (P2 skips only the commit, after scoring).

## Appendix — corrections this pass makes to earlier docs

* `double-team-mechanism.md` §1/§2: the re-selection gate at `0x001efd08`
  reads the **target's** +0x432 (s4 = partner+992), and `0x001efc00` is not
  a re-targeter at all — it is the two-man-animation starter (interaction
  ids 79/25 at `0x001f0054/68`, kinds 5/5 on success). The market
  re-targeter is `0x001f4790` phase 3. T4's in-window +0x432 values were
  tick up-counts, not formula stamps.
* `double-team-mechanism.md` §3: registration does **not** require the other
  blocker's kind == 2 — that test (`0x001f640c`) only selects the
  swap-and-reassign variant (with +0xB04 4/5/9); R3 formed with a kind-4
  primary.
* dt3-review lane 3 §2: the drive fn's state-{2,30} test is on the **peel
  man** (rec+12, s3), not the defender (rec+8, s2); and `0x001f6b0c`'s 30 is
  that state id (re-confirmed).
* The "windows 2..36 / 2..43 / 27..43" phrasing everywhere: unions of four
  records; per-record maxima are 15/7/2/6 frames.
* Mission brief's UNRECONCILED ten-in-state-32: five blocker+defender
  animation pairs; resolved, no tension with any DT reading.

# Mission brief: make double teams work (for a max-effort agent)

Written 2026-08-11 as the pre-armed escalation. Fire condition: any DT-3
fact-check lane (docs/dt3-review/) returns less than strongly positive.

## Mission

Figure out how to make run-play double teams actually function -- two blockers
sustaining a block on one defender, driving him, peeling only when it is
football-correct (docs/double-team-requirements.md R6a/b/c, R6z). Deliver: (1)
the verified diagnosis of why they do not, (2) a designed fix (pnach word(s)
or code cave), (3) its acceptance plan with pre-registered predictions.

## Ground truth -- proven by experiment, do not re-derive

* Slot 9 (I-Form lead dive vs 3-4 nose) registers dt records: roles 0/1/2,
  windows 2..36 / 2..43 / 27..43, torn down at contact-time. Operator's
  observation, repeatedly confirmed: the second man touches, aborts, passes
  the man off, climbs. carrier_yards -0.70 every unpatched run.
* THE TIMER GRAVEYARD -- all eliminated BY EXPERIMENT, each verified
  executing first: kind-8 init (0x001ef918, T4-proven executing, zero
  effect); the +0x42C 16-clock (only writer is kind-8-gated Site B); kind-4
  init (0x001ef8e8 -> 90; a blocker held a 77-tick timer and was torn down at
  the same frame as baseline). The registry teardown reads NO timer.
* The kind-4 diagnostic DID move the outcome: carrier_yards -0.70 -> +0.49,
  causal. Ordinary contact hold time is a real lever; it is not the double
  team fix (R5-violating, global).
* Registry (dt_record/dt_role) and engagement kinds (+0x3E0) are TWO systems.
  Run doubles: registry forms, kind 8 never appears. Pass (slot 7): kinds
  7<->8 flap 130+ frames, registry never forms (DT-1). They never connect.
* UNRECONCILED: ten players entered state 32 (two-man animation, owns kinds
  5/6) on slot 9 -- while the DT-3 story says helper assignment is skipped
  there. Resolve this; it may break the whole DT-3 reading.

## Live threads, in priority order

1. The dt3-review verdicts (read them first -- they may already answer 2-3).
2. The registry manage fn 0x001f6640: the contact-time decision that tears
   records down at 36/43. Its guard chain is partially traced in
   docs/double-team-mechanism.md section 3. What test fires at contact?
3. The play-type gate 0x001f4ae8 (beq v0,v1(=2) on 0x0015ada0's return) and
   what the skipped block truly does. Patch deployed as nop, UNTESTED.
4. Six unaudited writers of +0x432 (mechanism doc lists 8 direct sh sites;
   only 0x001efa34 / 0x001f2230 examined). The TE/RG in-window stamps of
   15/6 came from one of them.
5. The registration path 0x001f6424/643c/644c: what forms records, and
   whether it is upstream or downstream of the type gate.

## Constraints (operator's, non-negotiable)

No defender nerfs. No position warps. R5: single blocks unchanged (patches
gated on dt state, or proven inert elsewhere). Requirements before patches
(CLAUDE.md 3); each patch tested alone on its own savestate (rule 2); every
claim re-derived against the binary (rule 4) -- five patches this project
shipped were each "obviously right" and wrong.

## Verification protocol that exists and works

Savestates 6/7/8/9 in experiments/states/ (9 = the double-team play). Harness:
experiments/double_team.py (reselect_timer sampled = T4 execution-proof
channel). S0: read patched words over PINE before believing any run. Pad trap:
if snaps do not fire after an emulator restart, keepalive-then-restart
(memory/rig-emulator-operational.md). pnach: word type = 32-bit proven;
EnableCheats via per-game gamesettings ini; cheats load at VM boot. The
operator's eyes are the primary instrument -- they have out-diagnosed the
tooling five times today; give them specific things to watch.

## Tooling traps (each cost a pass today)

find_address_refs misses gp-relative; addiu-only scans miss daddu-zero;
fn entries may sit +4 past a nop pad (caller scans return false negatives);
191-page whole-play metrics lie (episode-scope everything); PINE writes do
not reach code (pnach only); 0x001f6b0c's 30 is a state id, not a duration.

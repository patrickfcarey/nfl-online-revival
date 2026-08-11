# Documentation map

This project has two tracks. They share a repo, a rig, and a
disassembler, but they answer different questions.

**Track 1 — Online revival**: reconstruct the dead EA/GameSpy services so
these titles can be played online again.
**Track 2 — Gameplay reverse engineering**: understand and retune the
game's own AI and tuning systems (sliders, coverage, blocking).

## Start here

**Not a programmer?** Read `human-readable.md` and stop there. It covers
every gameplay finding in plain English — what the sliders really do, why
the slot receiver never blocks the corner, why the quarterback folds to a
four-man rush — and it is honest about the several things we got wrong.

New to the repo? Read in this order:

1. `../README.md` — what the project is and where it stands.
2. `ea-protocol.md` — the wire format (track 1's foundation).
3. `method.md` — how to investigate this system, and a catalogue of every
   conclusion that turned out to be wrong.
4. `lessons-learned.md` — the gameplay-RE companion to `method.md`: tool
   gaps, MIPS traps, and what this engine's architecture actually looks
   like. **Read before starting any new gameplay investigation.**

## Track 1 — Online revival

| doc | what it covers |
|---|---|
| `ea-protocol.md` | the wire format: framing, status tags, message vocabulary |
| `roster-delivery.md` | how a roster reaches a console, and how to build one |
| `roster-checksum.md` | the CSUM derivation in detail |
| `lobby-and-matchmaking.md` | rooms, chat, quickmatch, the peer link |
| `backend-data-model.md` | the server's persistence model |
| `hardening.md` | making the backend survive contact with real clients |
| `emulator-capture.md` | rig-side runbook: DNS redirect, plaintext vs NIC, tcpdump |
| `protocol-notes.md` | the running log the above were distilled from (oldest first; earlier sections are superseded) |
| `method.md` | investigative method + the wrong-conclusion catalogue |

## Track 2 — Gameplay reverse engineering

**Systems** (how the engine works):

| doc | what it covers |
|---|---|
| `slider-behavior.md` | the options/slider system end to end: storage, the universal transform, penalty ramps, the UI binding layer |
| `play-tendency-ai.md` | the `ptrk` anti-repetition tracker (the real "CPU cheat"), and Madden Cards |
| `lessons-learned.md` | tooling gaps, MIPS traps, method, and engine architecture lessons |

**Answered questions** (community reports, diagnosed):

| doc | question |
|---|---|
| `sdchargersfanboy.md` | maxed Awareness/Tackling gets safeties burned deep; Knockdowns "fixes" it |
| `zone-bunching.md` | zone defenders bunch up and abandon the middle (#6) |
| `pitch-play-runner.md` | runners over-run their blocks on pitch plays (#2) |
| `lead-blocker-targeting.md` | pulling/lead blockers target wrong, and get hung up on the line (#5) |
| `cpu-dt-animations.md` | the CPU appears to have pass-rush animations the human never gets (#9) |
| `punt-logic.md` | punters never coffin-corner (#17) — the logic exists and is gated off |
| `pass-rush.md` | finesse vs power, leverage, and the absence of gap control (#16) |
| `block-cycle.md` | the double-team system that exists, and why line play looks like action figures (#14a/b) |
| `pass-vs-run-blocking.md` | pass pro and run blocking are separate systems; what the block sliders really scale (#14c) |
| `tackle-contest.md` | why break tackle doesn't feel like break tackle, and a four-word fix (#13) |
| `rating-thresholds.md` | does the engine use rating thresholds? (#19) |
| `catch-and-fumble.md` | the process of the catch, post-catch strips, and what governs fumbles (#22) |
| `press-and-routes.md` | the jam contest, whether LBs can press, and what governs route running (#20/#20b/#21) |
| `ai-play-calling.md` | the small play pool, the steep favourite, and the missing plan (#18) |
| `fb-wr-blocking.md` | why the FB and the slot WR block nobody — a corner in coverage is not a legal target (#15) |
| `fact-check-2026-08.md` | **read before patching** — eight verification passes over every doc here; what survived and what did not |
| `lead-blocker-requirements.md` | the agreed requirements + acceptance tests for the lead-blocker fix, before any patch |
| `field-overlay-tool.md` | design: paint proven world points (landmarks, routes) onto a game screenshot for visual review |
| `human-readable.md` | every gameplay finding in plain English, for readers who do not read disassembly |

**Forward work**:

| doc | what it covers |
|---|---|
| `open-investigations.md` | **the ledger** — all nine community questions, status, and first attack angle for each |
| `default-uplift-tuning.md` | verified patch-point catalog + draft pnach for getting extreme-slider behaviour at default settings (#7) |
| `play-data.md` | where play data actually lives — two formats, and which one the engine queries |
| `hb-vision-and-moves.md` | HB vision, special-move selection, and why halfbacks may have no vision at all (#12) |
| `robo-qb.md` | why the QB shreds a blitz but folds to a four-man rush (#10 pressure half) |
| `qb-read.md` | how the QB chooses a target (#10 read half) |
| `tooling-gaps.md` | what tooling the modernization work is actually blocked on, scored by findings unblocked |
| `code-caves.md` | the free-space survey: where injected code can live, the pnach mechanics, a proven worked example, and the rig verification plan |
| `slider-threshold-hunt.md` | the original hunt brief (historical; results superseded by `slider-behavior.md`) |

## Conventions used throughout

* Addresses are **virtual**; `file_offset = vaddr − 0xFF000` for
  `extract/SLUS_207.52`. `gp = 0x006056f0`.
* Every load-bearing claim is pinned to an address with quoted
  disassembly. Claims resting on a conditional move (`movn`/`movz`), a
  branch-likely delay slot, or a hand-decoded REGIMM branch say so
  explicitly — those are the three things that have produced wrong
  answers here most often.
* "Searched and not found" sections are deliberate: a closed-set census
  (all callers, all references, including raw data-word scans) is
  evidence, and it stops the next person re-walking a dead end.

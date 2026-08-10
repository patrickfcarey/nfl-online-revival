# Documentation map

This project has two tracks. They share a repo, a rig, and a
disassembler, but they answer different questions.

**Track 1 — Online revival**: reconstruct the dead EA/GameSpy services so
these titles can be played online again.
**Track 2 — Gameplay reverse engineering**: understand and retune the
game's own AI and tuning systems (sliders, coverage, blocking).

## Start here

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

**Forward work**:

| doc | what it covers |
|---|---|
| `open-investigations.md` | **the ledger** — all nine community questions, status, and first attack angle for each |
| `default-uplift-tuning.md` | verified patch-point catalog + draft pnach for getting extreme-slider behaviour at default settings (#7) |
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

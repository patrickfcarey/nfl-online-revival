# Reachability before deploy — the savestate-evaluation protocol

Born 2026-08-11. The DT-3 patch cost a full deploy-and-restart cycle before a
fact-check agent proved its branch could never be taken on any savestate this
project owns — by decompressing the .p2s files and evaluating the engine's own
play classifier against their memory. No rig, no emulator, decisive. This
protocol makes that check mandatory and `tools/statereader.py` makes it a
one-liner.

## The rule

**No patch is deployed until its gate conditions have been evaluated against
the target savestate's memory image.** A reachability FAIL kills the deploy
(the patch cannot fire on that state); a PASS is necessary but not sufficient
(states are pre-snap instants — post-snap rewrites are invisible here).

## The tool

    python3 tools/statereader.py <state.p2s> word 0xADDR [0xADDR...]
    python3 tools/statereader.py <state.p2s> classify-play
    python3 tools/statereader.py <state.p2s> roster

`SavestateReader.read(addr, n)` matches the live Emu's signature, so the whole
typed harness layer (`World`, `Player`, addresses.yaml) runs offline against a
state. A .p2s is a ZIP with zstd (method 93) members that zipfile refuses;
the reader parses the local header itself and shells to the system `zstd`.

## What it settled on day one

| state | play class (engine's own classifier) | code words |
|---|---|---|
| slot 6 lead dive | 0 | stock |
| slot 7 pass | 0 | stock |
| slot 9 double-team dive | **0 — new fact, measured not inferred** | stock |

DT-3's `beq v0, v1(=2)` therefore falls through on every state we own; the
nop was unreachable everywhere it was tested, including the acceptance state
lane 2 never checked.

## Uses beyond gates

* **Pre-deploy word check**: confirm the original word at a patch address in
  the state that will be loaded — catches savestate/ELF drift.
* **Anchor derivation**: load-confirm geometry (QB/FB spots) read from the
  state's own bytes instead of typed in from a live probe.
* **Post-mortem**: when a run does something odd, evaluate the suspect
  predicate against the exact state that was loaded, not a lookalike.

## Limits

Pre-snap instants only; anything the engine rewrites after the snap (the play
class bytes are flagged unverified post-snap — dt3-review lane 2) needs a live
read. And a savestate saved WITH cheats active bakes the patched words in;
`word` on a suspicious state distinguishes that immediately.

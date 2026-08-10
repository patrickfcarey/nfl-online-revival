# Lessons learned: reverse-engineering this engine

Accumulated across the gameplay reverse-engineering campaign (2026-08-08
to 2026-08-09, ~15 agent investigations plus three adversarial
verification passes). `method.md` carries the equivalent for the protocol
work and its catalogue of wrong conclusions; this is the gameplay-side
companion. Read it before starting a new investigation — most of these
were paid for with hours.

## Part 1 — Tooling: what our disassembler does not tell you

These gaps produced real errors, repeatedly. **Six separate lanes each
rebuilt the same enhanced disassembler in scratch** before this was
written down; fold it into `recon/mipsdis.py` and stop paying the tax.

| gap | consequence |
|---|---|
| `find_immediate` filters opcodes 0x08–0x0E — it cannot see `lui`, **nor any load or store** | Any "exhaustive sweep" for a constant or a struct offset done with it is **unsound**. One earlier sweep reached the right conclusion only by luck. Sweep `elf.words()` with your own opcode decoding instead. |
| REGIMM branches (opcode 0x01: rt 0=`bltz`, 1=`bgez`, 2=`bltzl`, 3=`bgezl`) print as `.word` | Load-bearing gates read as nothing. The knockdown clamp, the coverage cadence gate, and the 110-word default-image copy loop all hinge on hand-decoded REGIMM. |
| MMI (opcode 0x1C, e.g. `div1`/`mflo1` — the EE's second divider) prints as `.word` | Silently loses whole arithmetic terms (a `/115` vanished from one formula). |
| R5900 `mult` is **3-operand** (writes `rd`) | Reading the 2-operand form hides difficulty-class multipliers and struct-stride math entirely. |
| `lui` + negative `addiu`/`ori` sign extension | Hand-arithmetic gets the wrong address (`0x0057C838` misread as `0x0058C838`, twice in different lanes). Use `find_address_refs`, or be very careful. |

## Part 2 — Reading MIPS in this binary: the recurring traps

1. **`movn`/`movz` are conditional moves.** Read as plain moves they
   invert entire chains. This has caused wrong answers in at least five
   investigations (the DNAS poller, the playbook error path, the
   knockdown clamp, the false-start 1-in-N, the human/AI dispatch flag).
2. **Branch-likely delay slots execute only when taken.** Some
   conclusions survive a misread only by luck (the Holding ×0.4 damp);
   others invert completely (a coverage slot assignment made *in* a
   likely delay slot).
3. **A delay-slot literal may belong to a different instruction than the
   one you think.** A claimed `SetOption(15, 2)` site was really
   `SetOption(0, 2)` — the `15` was the delay slot of the *previous*
   `jal`. Always read the instruction the slot belongs to.
4. **A documented function address may be mid-prologue.** `SetTarget` was
   cited for a day as `0x001f73a0`; the real entry (and the `jal` target
   of all ~45 callers) is `0x001f7398`, eight bytes earlier. Verify entry
   points with `find_jal_targets`, not by eye.
5. **Strings reached through pointer tables produce no `lui`/`addiu`
   hit.** If `find_address_refs` on a string finds nothing, hunt for a
   table of pointers over it and find refs to the *table*. (Penalty
   names, attribute names, playbook errors — all table-indexed.)
6. **A constant is not a threshold until you have seen who compares it.**
   Report the comparison instruction, not the immediate.
7. **Not all logic is in the ELF.** UI flow lives in UI Studio bytecode
   (`uis_*.dat`); per-player AI assignments live in play-file data. If a
   search for "where is this decided" comes up empty, ask whether it is
   decided in data.

## Part 3 — Method: what actually worked

* **Closed-set censuses beat searches.** "I didn't find it" is worthless;
  "there are exactly 19 references to this pointer and all 19 are in this
  module" is proof. Enumerate *all* callers (`find_jal_targets`), *all*
  address materialisations, *and* scan for the address as a raw data word
  (vtables/jump tables) before claiming no indirect use. Every strong
  negative in these docs was produced this way.
* **The field census.** To decide "does this code read X?", enumerate
  every load/store of every player-object field in the function and
  resolve each base register to a role (self / target / teammate / ball).
  This settled "do zone defenders avoid each other" (no) and "do runners
  read blocks" (yes) — opposite answers from the same technique.
* **Adversarial verification pays for itself.** Every doc that went
  through a "try to refute this" pass came back with real errors —
  including one where the *source* of a claimed fact was a table the same
  doc called a red herring. Single-agent findings should be treated as
  provisional until re-derived.
* **Cross-lane correction is normal and healthy.** Lanes routinely
  corrected each other (the RNG "doesn't exist" → it's split across
  `lui`/`ori`; "19 penalty sliders" → 10 penalty + 9 gameplay; "gameplay
  sliders are words 17–39" → those are create-a-player ratings). Give
  each lane the others' findings *as claims to check*, not as gospel.
* **Compute, don't estimate.** Several published numbers were wrong by a
  rounding mode (the knockdown span is 13–87%, not 12–87%, because the EE
  rounds toward zero). Recompute in float32 with the actual pool
  constants.

## Part 4 — What we learned about this engine (transferable)

Findings that are *architecture*, likely to hold across the 2002–2005
Madden/NCAA family and worth checking first in any sibling title:

* **Ratings are decisiveness, not intelligence.** Awareness feeds only
  cadence timers and commitment probabilities — it never improves the
  *quality* of a decision. There is no route prediction, no play-action
  recognition, no "smarter" positioning. Maxing it produces a hair-trigger
  defender, not a smart one. (`sdchargersfanboy.md`)
* **Settings exist in three mirrors**: a front-end struct, the TDB
  database row, and an engine-side byte array. Find the one the engine
  actually reads before drawing conclusions — two of three are decoys.
  (`slider-behavior.md`)
* **Effective ratings are the 0–100 rating × 2.55 on a 0–255 scale**,
  stored at `player+0xB70+2·attr`, order given by the fourcc list at
  `0x00520140`. Skill class and sliders both rewrite this table before
  play; gameplay code reads only the result.
* **The AI is a 93-state machine** (array `0x00527238`, 24-byte
  descriptors: enter / canLeave / AI-think / USER-think / exit). The
  dispatcher runs USER-think first and falls through to AI-think unless it
  returns 1 — which is why human-controlled players still get AI
  behaviours. Assignments are selected by **play-file data**, not ELF
  constants.
* **A recurring anti-pattern: "steer at a reference, no separation, no
  lead."** Zone defenders slide toward one shared reference with no
  teammate repulsion; ball-carriers and blockers aim at a target's
  *current* position with no lead term. The same shape produces zone
  bunching, runner over-run, and blocker over-run. Expect it anywhere the
  engine steers.
* **Agency is asymmetric.** Defenders have a shed contest; the offense has
  no equivalent — a captured blocker is passive until a timer the
  *defender* set expires. Look for missing counterparts, not just
  present mechanisms.
* **Hidden CPU advantage exists but is bounded and knowable** — here an
  anti-repetition play tracker (`ptrk`), not a score-based rubber band.
  Chase the pointer, not the folklore. (`play-tendency-ai.md`)
* **Compressed asset containers are reversible from the ELF.** The game
  must decompress its own files, so the decompressor is in the binary;
  `tools/lzh1.py` came from following that logic and now opens every UIS
  container on the disc.

## Part 5 — Working with agent fleets on this codebase

* **Session limits and stream stalls kill agents mid-run.** Resuming from
  transcript works and loses almost nothing — always try that before
  relaunching from scratch.
* **Agents write to the repo even when told not to.** One left a
  truncated file behind after an interruption. Check `git status` before
  committing a batch.
* **Scratch tooling evaporates.** Anything an agent builds in the
  scratchpad (the enhanced disassembler, the LZH1 decompressor, the FPU
  decoder) is lost unless promoted into the repo. Promote it in the same
  session.
* **Give lanes each other's corrections mid-flight.** Several
  investigations were saved from dead ends by forwarding a sibling lane's
  finding while they were still running.

## Standing debts

1. **Fold the enhanced disassembler into `recon/mipsdis.py`** — REGIMM,
   MMI, R5900 3-operand `mult`, gp-relative annotation, and a
   load/store-aware immediate sweep. Requested by six lanes.
2. **Free-space survey** for code caves — the enabling step for nearly
   every designed gameplay fix (in progress).
3. **Play-file / ISO data reads** — the enabling step for the *data*
   fixes (pull-path depth, per-play targeting delay) and for closing the
   assignment-class → AI-state mapping.

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
* **Steering failures look alike on screen and are not alike in code.**
  Three complaints that all present as "over-running the target" have
  three different causes: zone defenders slide toward one *shared*
  reference with no teammate repulsion; the ball carrier aims at a
  target's *current* position with no lead; the lead blocker *does* lead
  (2× velocity, which over-leads a cutting defender) during the approach
  and then, on contact, stops tracking entirely and drives a **frozen**
  axis for 15–30 frames. An earlier version of this file lumped all
  three together as one anti-pattern — a tidy generalisation that was
  wrong in two of the three cases. Diagnose each steering complaint from
  its own code.
* **"Overwritten every frame" is a real architectural pattern here.**
  The engagement manager runs *after* the per-player AI loop, so its
  locomotion writes win. An AI state can be computing perfectly good
  steering that is then discarded — which means "I found the code that
  steers this player" is not the same as "I found what moves him".
  Establish write *order*, not just write *sites*.
* **Agency is asymmetric — but check the exact boundary.** A captured
  blocker is passive until a timer the *defender* set expires. The first
  version of this lesson said "the offense has no shed contest at all";
  verification found the ball *carrier* can reach it (via state 30) —
  it is specifically the offensive line and pull roles that cannot. Look
  for missing counterparts, and then pin exactly who lacks them.
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

## Part 6 — Liveness analysis: a `jal` search is not a liveness test

The free-space survey (`code-caves.md`) found that **all four functions
this project had recorded as "zero callers" are in fact referenced** —
and each escaped detection a different way:

* reached by a tail-call **`j`** (not `jal`) — two of the four;
* the address is **mid-function**, not an entry point;
* the address appears as a **function-pointer word in `.data`**.

To claim something is dead you need all of: no `jal`, no `j`, no branch
of any form from outside, no `lui`+`addiu`/`ori` pair materialising an
interior address, **and no 32-bit word anywhere in the file equal to any
address in it**. The last test is the one that catches vtables and jump
tables, and it is the one everybody forgets.

The same discipline applies in reverse: fragment code at `jr ra`+delay
slot boundaries so a candidate cannot be entered by fall-through.

## Part 7 — What the second verification round added

A full adversarial re-check of the four gameplay-AI docs (2026-08-09)
produced six P0 corrections. The patterns worth internalising:

* **A base register is not self-evident.** The worst error was reading
  `s3 = s1 + 992` as "the target" when it is *self's own engagement
  record*. Everything downstream — "the blocker drives at the defender's
  current position, overriding the route" — followed from that one
  misattribution. **Resolve every base register explicitly before
  narrating what a store means.**
* **State a conclusion only from the instructions that produce it.** The
  "no route landmark, no lead" census was reported against stores whose
  values were staged elsewhere; the census could not have derived it. The
  finding may still be true, but it was not evidenced.
* **Quantitative tables are the most fragile artefact.** One shed-odds
  table was unreproducible and was missing two whole multipliers. Either
  derive numbers from a model you can re-run, or publish the mechanism
  qualitatively.
* **Check idioms for hidden gates.** Two "per-frame" behaviours turned
  out to be gated behind a reaction countdown; the docs described them as
  running every frame. If a block sits after a timer decrement, assume a
  gate until proven otherwise.
* **A precondition can look like a filter.** The pitch-runner's 8-yard
  window reads like a test on teammates; it is a test on the *carrier*
  that aborts the scan entirely. Read guard clauses before loop bodies.
* Doc claims should carry their hazards: several conclusions rested on
  unflagged branch-likely / REGIMM / `movz` instructions even in docs
  whose own convention is to flag them.

## Part 8 — Three passes to one answer: an anatomy

The lead-blocker question (`lead-blocker-targeting.md`) took three
investigations, and the sequence is worth keeping because each pass
failed differently:

1. **Pass 1 produced a tidy, wrong story.** It read `s3 = s1 + 992` as
   "the target" when it is *self's own engagement record*, and from that
   single misattribution derived a whole narrative — "the blocker
   abandons his route to drive at the defender's current position".
   Everything downstream was internally consistent and externally false.
2. **Pass 2 (adversarial) killed the claim but could not replace it.** It
   correctly showed the stores were a mutual lock-in, not a chase — and
   correctly reported that the *cause* was now unknown, because the
   values were staged by code nobody had walked. This is the right
   outcome for a verification pass: it is allowed to leave a hole.
3. **Pass 3 walked the missing writer and produced the real mechanism** —
   which vindicated the *observed behaviour* from pass 1 while relocating
   it entirely (contact, not approach; frozen axis, not tracking; and a
   2× lead that pass 1 had claimed was absent).

Lessons that generalise:

* **A retraction is not an answer.** After pass 2 the doc said "we don't
  know", which was honest but left a hole where the fix spec needed
  detail. Budget a third pass to *close* what verification opens.
* **Do not patch a document whose spine was removed — rewrite it.** The
  interim version asserted the override in its summary, retracted it in
  the middle, and re-listed it as proven at the end. It was unreadable,
  and the reader could not tell what was known. If the load-bearing claim
  changes, restructure around known-vs-open.
* **Behavioural reports from players are evidence about behaviour, not
  about code.** The community independently described the over-run, which
  raised the prior that a real mechanism existed and made pass 3 worth
  funding — but it could not have confirmed the specific instructions,
  and the instructions were wrong. Use such reports to prioritise
  searches and to sanity-check conclusions; never to promote a code claim
  that failed verification.
* **Terminology can carry a wrong model.** "Kind 4 = approaching" was an
  early guess that hardened into vocabulary across three documents; kind
  4 is *contact*. Once a label is wrong, every sentence using it is
  subtly wrong too.

## Standing debts

1. **Fold the enhanced disassembler into `recon/mipsdis.py`** — REGIMM,
   MMI, R5900 3-operand `mult`, gp-relative annotation, and a
   load/store-aware immediate sweep. Requested by six lanes.
2. ~~Free-space survey~~ — **done**, see `code-caves.md`. Caves exist
   (~9.2 KB of dead code, all reachable by a one-word `j`), the pnach
   mechanics are proven with a worked example, and the runtime
   verification plan is written. Every cave still needs its
   "is-it-really-dead" breakpoint test on the rig before use.
3. **Play-file / ISO data reads** — the enabling step for the *data*
   fixes (pull-path depth, per-play targeting delay) and for closing the
   assignment-class → AI-state mapping.

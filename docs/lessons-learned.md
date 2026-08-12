# Lessons learned: reverse-engineering this engine

Accumulated across the gameplay reverse-engineering campaign (2026-08-08
to 2026-08-09, ~15 agent investigations plus three adversarial
verification passes). `method.md` carries the equivalent for the protocol
work and its catalogue of wrong conclusions; this is the gameplay-side
companion. Read it before starting a new investigation — most of these
were paid for with hours.

## Part 1 — Tooling: what our disassembler does not tell you

These gaps produced real errors, repeatedly. **Eight separate lanes each
rebuilt the same enhanced disassembler in scratch** before it was finally
folded into `recon/mipsdis.py`. **Do not build a ninth** — use the module,
and if it is missing something, add it there.

| gap | consequence | status |
|---|---|---|
| `find_immediate` filters opcodes 0x08–0x0E — it cannot see `lui`, **nor any load or store** | Any "exhaustive sweep" for a constant or a struct offset done with it is **unsound**. One earlier sweep reached the right conclusion only by luck. | **fixed** — `find_immediate_all` sweeps every immediate-carrying opcode including loads and stores. `find_immediate` keeps its narrow filter (comparisons only) because older notes cite its output. |
| REGIMM branches (opcode 0x01: rt 0=`bltz`, 1=`bgez`, 2=`bltzl`, 3=`bgezl`) print as `.word` | Load-bearing gates read as nothing. The knockdown clamp, the coverage cadence gate, and the 110-word default-image copy loop all hinge on hand-decoded REGIMM. | **fixed** — decoded, including the `-al` forms; the likely ones are marked `; likely`. |
| MMI (opcode 0x1C, e.g. `div1`/`mflo1` — the EE's second divider) prints as `.word` | Silently loses whole arithmetic terms (a `/115` vanished from one formula). | **fixed** — the pipeline-1 integer set (`mult1`/`div1`/`mfhi1`/`mflo1`/`madd`…). The SIMD sub-tables MMI0–MMI3 still print as `.word`. |
| `sllv`/`srlv`/`srav` printed `rd, rs, rt` — the operands the wrong way round | A variable shift read backwards: "shift the counter by the score". Made a live function look like unreachable dead code. | **fixed** — the six variable shifts are `rd, rt, rs`, and a mutation test pins it. |
| COP1 (FPU) prints as `.word` unless you remember `recon/fpudis.py` | Float maths is everywhere in the gameplay code and it was easy to forget the second module mid-read. | **fixed** — `mipsdis.disassemble` delegates the three FPU opcodes to `fpudis`. |
| R5900 `mult` is **3-operand** (writes `rd`) | Reading the 2-operand form hides difficulty-class multipliers and struct-stride math entirely. | **fixed** (earlier) — `rd` is shown whenever it is non-zero. |
| `lui` + negative `addiu`/`ori` sign extension | Hand-arithmetic gets the wrong address (`0x0057C838` misread as `0x0058C838`, twice in different lanes). Use `find_address_refs`, or be very careful. | unchanged — this one is arithmetic, not tooling. |

One addition worth knowing about: loads, stores and address-forming adds
through `gp` now carry the address they resolve to as a comment
(`lw v1, -19164(gp)   ; gp-relative 0x00600c14`), using `GP_BASE =
0x006056f0`. Pass `gp=None` to `disassemble`/`dump` for a different
executable rather than trusting a stale annotation.

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

1. ~~**Fold the enhanced disassembler into `recon/mipsdis.py`**~~ —
   **done**. REGIMM, MMI, the `sllv` operand order, COP1 (delegated to
   `recon/fpudis.py`), gp-relative annotation and `find_immediate_all`
   all live in the module now, with tests in `tests/test_mipsdis.py` and
   a mutation entry for the shift-operand bug. Requested by eight lanes;
   there is no longer any reason to build a private one.
2. ~~Free-space survey~~ — **done**, see `code-caves.md`. Caves exist
   (~9.2 KB of dead code, all reachable by a one-word `j`), the pnach
   mechanics are proven with a worked example, and the runtime
   verification plan is written. Every cave still needs its
   "is-it-really-dead" breakpoint test on the rig before use.
3. **Play-file / ISO data reads** — the enabling step for the *data*
   fixes (pull-path depth, per-play targeting delay) and for closing the
   assignment-class → AI-state mapping.


---

## Scope-test every new requirement against the blast radius (2026-08-11)

**The rule.** When a new requirement arrives mid-design, do not fold it into
the change in flight. First ask **which code path the requirement's behaviour
actually lives in**. If that is not the path the current change modifies, it
is a *separate work item*: capture it with its acceptance test, mark it
explicitly unscoped, and record why. Topical similarity is not scope.

**The case that produced it.** While designing a state-47 (lead-blocker) patch,
a requirement arrived: *"in zone blocking schemes they need to first take a big
step to the left or right before even attempting to block."* It is a real,
correct football requirement. It was still **out of scope**, because most
zone-scheme linemen are not lead blockers — they run the ordinary run-block
state (33), not state 47 — so the behaviour lives on a code path the patch does
not touch. Folding it in would have widened the change across a second path and
risked exactly the cross-scheme breakage the requirements doc exists to prevent.
It was recorded as R8, unscoped, with the reason.

**Why it is easy to get wrong.** The requirement was *about blocking* and the
change was *about blocking*, so they feel like the same job. **Blast radius is
about code paths, not topics.** The pull to accept a related-sounding
requirement is strongest exactly when the requester is the domain expert and
the requirement is obviously true — neither of which says anything about where
the code lives.

**The repeatable test — three questions, every time:**

1. Which state / function / code path does this requirement's behaviour live in?
2. Is that the same path the current change modifies?
3. If no → capture it as its own item with its own acceptance test, mark it
   unscoped, state the reason, and carry on. If unknown → it is an open
   question that **gates** the design, not an assumption.

**Its counterpart.** This is the design-time twin of the standing test rule
(each patch verified individually before integration). One keeps unrelated
behaviour out of a change; the other proves the change did not reach further
than intended. A project needs both: scope discipline before the code, and
isolation testing after it.

## Part 9 — The block system, and the patching lessons the double-team campaign earned (2026-08-12)

The double team went from a quarter-second touch-and-abort to a
mass-proportional pancake over one campaign (four shipped words: P1, P4, P11,
N-1). The *procedure* lessons are in `tracing-method.md`; the *architecture*
and the *deploy discipline* live here because a future investigation of this
engine needs them before it starts.

### 9.1 The block pipeline, end to end (transferable)

A block is not one system; it is a pipeline, and a defect can live at any
stage. Naming the stages is what let the campaign attack the right one:

1. **Assignment market** (`0x001f4790`, per-frame). Re-shops every blocker's
   target every frame with no awareness of double-team records. It was
   *stealing the participants* — the touch-abort. Fixed by P1 (a commit guard:
   don't re-shop a live record member). See `double-team-solution.md`.
2. **Registry** (`dt_record`/`dt_role` at +0x436/+0x437; block `[0x00601280]`,
   records at T+4+20k). Bookkeeping only — it does NOT sustain a pairing; the
   engagement *kinds* do. `dt_role` **5 = unassigned** (not in the published
   0/1/2/3 enum). The role-1 helper is always at record+0x04, stored as a
   HANDLE (resolve via `0x0013B798`), and `+0x436` has **zero readers
   image-wide** — there is no "find my helper" function.
3. **Contest** (`0x001f0c40`, at lock-in). Stamps three comps at
   +0x414/+0x418/+0x41C from ratings and weight. **It scores ONE blocker vs
   ONE defender** — the reason a doubled man wins his shed. N-1 folds the
   helper's weight+STR in here so the contest finally knows two men are on him.
4. **Outcome grid** (`0x00526F90`, 6×5, keyed on the comp pools + drive).
   Selects a *cell* by margin size: stalemate, shed, drive, pancake. **Cell,
   not scalar** — this is why T3 (outcome variety) is a fold-magnitude tune,
   not new code.
5. **Animation** (state 32, clip via the group registry; root motion
   `0x0018F9E0` → `0x0018F980` onto +0x190/+0x194/+0x1A8). Owns both bodies'
   transforms during kinds 5/6.

**The load-bearing architectural fact:** during a pair clip, nothing moves the
*loser's* logical position — the pancake animation is **skeletal, visual
only**. Real "drive him back" is not a position write; it is winning a native
contest so the engine selects a driving outcome cell. Four patches (P8–P10)
died writing positions before this was established by a single field-writer
census. (One qualifier: a convergence-warp at `0x00196FE0` *does* write
+0x190/+0x194 for aligned participants — the "stale garbage" earlier work saw.
"Nothing writes position" is true of the *engagement* system, not the whole
image.)

### 9.2 Latching vs accumulating patches — the distinction that saves a patch

A patch that **accumulates** an effect per frame (a position nudge, a timer
tick) needs the host to run at a known high frequency; five firings buys
inches. A patch that **latches** a decision (a contest outcome, a target
choice) needs to fire *once at the right moment* — the outcome governs the rep
until the next lock-in. N-1 fires only 2–8×/play and works, because it latches.
Counting firing frequency without asking which kind of patch it is will reject
a good latching patch for the wrong reason. Record this next to
`tracing-method.md`'s "count frequency in situ" rule — the count is necessary,
but its *interpretation* depends on the patch class.

### 9.3 The canary: a hand-rolled execution counter, and its one gotcha

To prove a cave executed (and how often), append 3–4 words that increment a
stock-zero word in unused padding (cave #11 spare words). Read it after the
run. It settled three "does this host even run?" questions that no static
derivation could. **Gotcha:** `load_state` restores EE memory including the
canary region, so a multi-iteration trial WIPES it each load — the reading is
per-play, not cumulative. (A per-frame execution counter belongs in the
harness; it was hand-rolled twice before anyone said so.)

### 9.4 Deploy discipline (operational, and each cost a cycle)

* **Five-axis cave census, by the deployer, every time.** Four regions
  documented safe were live this session (#1 poisoned via a distant
  `lui`-formed base, #3 live, #2 a branch landing inside from `0x0044C404`,
  plus a recommended region). A prior agent's clean census does NOT transfer —
  the axis it omitted is the one that bites. `recon/cave_census.py` runs all
  five (data-word pointers, branches/jumps in, jal targets, lui/addiu
  materialisations, ELF-vs-savestate identity). Census in BOTH the ELF and the
  target savestate's memory.
* **pnach encoding.** `patch=1,EE,addr,word,VALUE` is a proven 32-bit write in
  this fork. The `extended` type encodes width in the address's LEADING DIGIT
  (0=byte, 1=half, 2=word) — a bare `001f6b0c,extended` silently byte-wrote and
  cost a cycle. Use `word`.
* **`patch=1` for cave bodies, not `patch=0`.** `load_state` restores the cave
  region, so a boot-time (place 0) cave body is wiped by the first savestate
  load and the trial measures unpatched code. Every line `patch=1`.
* **Verify the patched words over PINE before believing a run (S0).** "The
  cheat file parsed" ("Found N cheats" in the log) is not "the words are in
  memory." Read them back.
* **EnableCheats reverts on emulator exit.** PCSX2 rewrites its global ini on
  quit; editing it mid-session is undone. Use a per-game override
  (`gamesettings/SLUS-20752_14F8B841.ini`, `[EmuCore] EnableCheats = true`).
* **Two-boot regression discipline.** When a patch's blast radius reaches a
  second play type (P11 reached pass pro via the kind-8 flap), run the
  regression arm under the CURRENT set before adding the next patch, so any
  movement attributes to the right change.

### 9.5 The operator is the instrument of record

Across this campaign the operator's screen observations corrected the
instruments **seven times** — the statue, the pushback magnitude, timer-vs-
priority, the touch-abort, a symmetric 256× test that cancelled, an over-claim
on a 7-inch nudge, and reading P11's result as a shed-then-handoff. Every time
the eyes were right and the metric was being read too generously. `obs:` from
the console outranks the harness; when they disagree, the harness has a bug.
This is `operator-observations-are-evidence` earning its rule, quantified.

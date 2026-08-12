# Tracing a behavioural defect to a patchable site

Written 2026-08-12 from the double-team campaign: ~18 hours, roughly 40
commits, one working diagnosis, and along the way **fourteen refuted theories,
four code caves documented safe that were not, five patches that could never
have fired, and three execution-frequency claims that were wrong by direct
count.** It worked. It should have taken a fraction of the time.

This document is about **how the findings were made**, not what they were. It
extends two existing documents and deliberately does not repeat them:

* `method.md` — the protocol-track tooling and its catalogue of wrong
  conclusions. Still current.
* `lessons-learned.md` — how to read this binary (the `movn` trap, branch-likely
  delay slots, base-register attribution, the `jal`-is-not-liveness rule).
  Still current, and Part 6 is the ancestor of §1's cave incidents.

**What is new here**, and is not in either: the ordering (§2) — several checks
this project already owned were used far too late; the execution-frequency
invariant (§3.1), which is new this session and cost the most; the field-writer
census as a *precondition of patch design* rather than a follow-up (§2 step 3);
the gate-occurrence census over traces already on disk (§4); and the
agent-orchestration rules in §5, where the dominant failure was not a bad lane
but a **shared premise across lanes**.

---

## 1. The dead-end catalogue

Every wrong turn, what supported it, what killed it, and — the column that
matters — the cheapest check that would have caught it first. "Cost" is
wall-clock from the commit stream where the commits bracket the episode.

| # | What was believed | What supported it | What refuted it | **Cheapest check that would have caught it first** | Cost |
|---|---|---|---|---|---|
| **1** | `0x001f6b0c`'s immediate 30 is the double-team hold duration | the only immediate 30 in the block-cycle region; `block-cycle.md` calls the block a "14–30-frame translation"; measured windows 13/17/30 | deployed 10×; windows frame-identical (17/30/13) | **Read the comparison, not the immediate.** Two instructions around it (`lbu a0, 0(v0)` = current AI state id; `bnel a0, v0`) show 30 is a **state id**. ~30 s. `lessons-learned.md` Part 2 rule 6 already said this and it was not applied. Free second check: 13/17/30 on one play cannot come from one constant — arithmetic on data in hand | ~1 h |
| **2** | DT-HOLD-90: extending the kind-8 hold makes run doubles persist | five review lanes, all internally correct | deployed; windows frame-identical; `+0x432` read baseline values throughout. **"Registry doubles on this run play never use engagement kind 8"** | **Count kind-8 frames in the baseline trace already on disk.** Zero. `gatecensus --gate 'k8: self.engagement == 8'` → 0, flagged *never sampled*. Seconds (now a test) | ~2 h + 5 lanes |
| **3** | DT-3: nopping the play-type-2 gate unblocks helper assignment | the surviving mechanism after every timer was eliminated | three lanes, 12 min after deploy. Lane 2 evaluated the engine's own classifier against savestate memory → returns **0**, not 2, on every state owned | **Grep the project's own recorded measurements.** Any sighting of kind 7 or `dt_role` 0/1/2 proves the branch fell through — and slot 9's role table was already ground truth in the mission brief. Seconds, no tooling. (This incident invented `statereader.py`) | 1 deploy + restart |
| **4** | P5: 256× drive proves the lever — the dive broke for **+7.07 yd** | the largest outcome swing yet, 10/10 identical | **the operator**: "is it possible we also buffed it for the defense?" The sweep's two stores write the *same* magnitude to both members (83.72 ↔ 83.72) | **Check the lever is differential before believing an outcome.** One look at the two store sites' base registers, or one line over data already captured: per-pair max `speed_cmd` by side. Seconds | ~10 min + a withdrawn conclusion |
| **5** | The +7.07 was blocking working | it was the first positive carrier number | operator: "those were very weird results" — the arms invert. Both sides boosted = +7.07; **offence-only = −0.70** | **Monotonicity.** If helping only your own side helps *less* than helping both, the mechanism is not what you think. Free | included above |
| **6** | `speed_cmd` (P6) and velocity (P7) are the drive lever | the drive chain was real and mapped | clean pre-registered negatives — 256× one-sided moved nobody; 8× velocity byte-identical | **The field-writer census on `+0x190/+0x194` during kinds 5/6** — see #12. `block-cycle.md` had already said root motion owns both transforms, and the operator called it before any of them ran | ~25 min |
| **7** | `+0x3DE` is the pair animation **id** — so sampling it adjudicates lanes 1 vs 3 | held by lane 1, lane 3, *and* the synthesis | it reads 15/17/18/19 — neither lane's vocabulary. Lane 5 derived the producer: `sh a1, 0(out_id)` where a1 = descriptor **+4 = the group number** | **Derive the producer of a field before sampling it.** Five instructions, static, no probe, no rig. The measure-first instinct was right; "measure" was executed as *sample the offset both lanes wrote down* instead of *derive what it holds, then sample* | ~40 min |
| **8** | B1: adding anim 158 to the yes-set saves the record through the capture | lane 1's trace — the capture hardcodes 158 | A2, the first exact-clip read during a live block: the double team plays **161** for 37 frames. 158 is the *single*-block capture | **The pointer-chased anim-id read** — the probe both lanes independently designed hours earlier, run against the wrong word. B1 "would have changed single blocks and left the double untouched, and its oracle could never have fired" | ~3 h of lane time |
| **9** | Cave #1 `0x00139A68` is dead; a worked patch example was built on it | the survey's zero-reference test | four interior addresses materialised at `0x00139E2C..44` off a `lui`-formed base **124 bytes back**, registered as callbacks. Ten of eleven patch lines sat on live code | **The five-axis range census.** 2 s for the whole image, any number of caves (§4, built) | a rewritten doc |
| **10** | Cave #3 `0x0045F598` is dead | same survey | same shape — pairs at `0x00460178/80`. Struck *after* the pairing window was already widened, because a per-address search was never re-run word by word | same census — it needs **no target guessed in advance**, which is why the per-address fix did not prevent this one | ~30 min |
| **11** | Cave #2 `0x0044C1C0` is dead and usable | listed safe; **censused clean by an earlier agent whose pass omitted the branch axis** | its tail at `0x0044C404` branches back to `0x0044C228`, inside the first 55 words anyone would write | **Census on every axis, by the person about to write there.** Prior censuses do not transfer. Note the subtlety this session sharpened: cave #2 *is* unreferenced externally — the killer is that it is one **function**, not padding, so a partial overwrite corrupts it | caught at the desk |
| **12** | **Position-writing drives a blocked body** (P8 → P9 → P10, four patches) | root motion owns the transforms; the pair's clip ships no motion; so write position directly | P10 + lane 3, closed three independent ways: group 17 ships no type-9 displacement spec; all 29 stream-event handlers are stubs; **the image-wide `+0x190` census finds no live writer during kinds 5/6**. The cave was the sole writer, pushing alone. The pancake is *skeletal* — visual only | **The field-writer census, run before the first cave was written.** It was *named as missing* in `motion-block-cave.md` §10 item 2, routed around, and three patches shipped anyway with a failure-mode entry as "the designed catch" | ~4 h, 4 patches |
| **13** | P8's five driven frames are starved by the **defender-kind gate** | A2's series showed kind-8 and kinds 5/6 overlap only ~5 frames | P8b widened exactly that gate and redeployed: **canary still 5**. Then, from data already on disk: gates pass on 35 frames, cave ran on 5 | **The gate census with leave-one-out**, over a trace already captured: cutting the defender-kind gate buys nothing. Seconds (§4, built). "The cheapest way to find out is instrumentation, not reasoning" — written *after* paying for it | 1 deploy + 3 iterations |
| **14** | P9: two hosts both fire ~5/play, so these sites are **lock-in driven** and a third host will not help | two independent hosts, same count | lane 1: the P9 hook sat inside `0x001F1C20`'s **kind-4-only filter** (`bne` at `0x001F1C98`). Five frames was the blocker's kind-4 residency — "an unseen gate, not a mystery" | **Enumerate every branch between the host's entry and your hook site.** The host's enclosing conditions are part of your gate chain. Minutes of disassembly | ~30 min |
| **15** | "P9 PROVES THE LEVER" | DE travel 0.60 → 1.22, carrier −0.70 → +0.82, 3/3 identical | **the operator**: "are you sure? I dont see anything." 5 frames × 0.045 yd = **0.2 yd**, seven inches; dy still −0.56, still net penetrating | **Multiply before publishing.** frames × step, in the unit the observer uses. Seconds. This was the fifth over-claim he corrected the same day | a retracted headline |
| **16** | Whole-play metrics describe the double team | `defender_pushback` 3.178 yd, `helper_speed` ≈ `primary_speed` | episode-scoped: **+0.410 yd** over 17 frames. The 3.178 was a defender flowing to the ball after the block ended | **Episode-scope by construction.** The double team occupies 13–30 of 308 frames; any whole-play mean is dominated by what happens after it. Three metrics shipped wrong; the later review found five more | 2 wrong readings on one run |

Two patterns across the table worth naming:

* **Nine of sixteen were killed by evidence the project already had** — a trace
  on disk (#2, #13), a savestate in the tree (#3), a doc it had written itself
  (#6), its own recorded ground truth (#3), or two adjacent instructions (#1,
  #7, #14). The binding constraint was not evidence. It was *asking*.
* **The operator refuted seven of them**, from the screen, against the
  instruments — #4, #5, #15, the metrics-lied correction behind #16, the P8b
  "he still isnt being driven back", the P11 handoff reading, and the R6
  reframe. Seven for seven.

---

## 2. The ordered method

The order is the finding. Almost every step below already existed somewhere in
this project; the cost came from running them in the wrong sequence — expensive
deploy cycles before free static checks. Kill-rate is *how many of §1's sixteen
this step catches*.

### Phase A — before you believe the defect exists (minutes)

**A1. Episode-scope every metric, then re-read the baseline.** Any statistic
over a whole play is presumed wrong until shown otherwise. Cost: minutes.
Kill-rate: #16, and it reframed the entire requirement (duration, not force).

**A2. Get one screen observation from the operator and write it verbatim.**
Not a metric. His words named the mechanism three times before the instruments
did — "once he touches it he inherently thinks he should not be there anymore",
"he sheds the first RT block and then the TE picks him up".

### Phase B — before you design anything (minutes to an hour, all static)

**B1. Derive the producer of every field you intend to read.** Never sample an
offset whose writer you have not read. Cost: ~5 instructions. Kill-rate: #7.

**B2. Census who else writes the field you intend to move, and when.** This is
the highest-value single step in the document and it was run last. Enumerate
every store to the target field image-wide (`find_field_refs`, which needs
cross-function base tracking — a callee writing through a handed pointer reads
as no writer at all), then ask which of those writers are live in the state you
intend to act in. Cost: one pass. Kill-rate: **#12 and #6 — five patches and
roughly six hours.**

> The rule this earns: **a census you have named as missing and routed around
> is a blocking unknown, not a residue.** `motion-block-cave.md` §10 listed the
> `+0x190` writer census as not-run, designed a failure mode to catch it, and
> shipped three patches. That census later closed the whole approach.

**B3. Count how often the state you are gating on actually occurs**, in a trace
already on disk. Cost: seconds with `gatecensus`. Kill-rate: #2, #13.

### Phase C — before you choose a hook (minutes, static)

**C1. Walk callers from your site up to the frame tick, on every axis.** A
`jal` search is not a caller list: state-32's `ai_think` has **no `jal` caller
at all** — it is reached only by a function-pointer word in the dispatch table.
`cave_census --callers` answers this. Cost: seconds.

**C2. Enumerate every branch between the host's entry and your hook site.** The
host's own enclosing conditions are part of your gate chain, and an unseen one
looks exactly like a mysterious cadence. Kill-rate: #14.

**C3. Write down the predicted execution count before deploying.** This is the
oracle. A derivation is a hypothesis (see §3.1).

### Phase D — before you deploy (seconds, static)

**D1. Evaluate the gate chain against the savestate that will be loaded**
(`statereader.py`, `docs/state-reachability.md`). A FAIL is decisive; a PASS is
necessary, not sufficient — states are pre-snap instants. Kill-rate: #3.

**D2. Gate census with leave-one-out.** Which gate binds, and what widening
each one would buy. Kill-rate: #13 — P8b's entire deploy cycle.

**D3. Census the cave on all five axes, yourself, plus internal crossings into
your write window.** Prior censuses do not transfer. Kill-rate: #9, #10, #11.

**D4. Round-trip every word through the disassembler; confirm the original word
in both the ELF and the savestate.** The `extended` pnach type wrote **one
byte**, not four, and the parse log said nothing. And record a read-back row
per iteration: today a result file cannot distinguish a patched run from an
unpatched one — the arm label is unverified free text.

### Phase E — after deploying one arm (the measurement)

**E1. Read the canary first, before any behavioural metric.** Predicted vs
measured is the host's duty cycle. A large gap means the hook is in the wrong
function and **no gate edit will help**.

**E2. Multiply into the observer's units before claiming anything.** frames ×
step = displacement; compare against what an eye can see at 60 fps. Kill-rate:
#15.

**E3. A null result is only informative if the patch could have fired.** Three
patches produced frame-identical nulls that proved nothing about their
hypotheses. "The risk is not damage; it is booking an inert word as a tested
hypothesis."

**E4. The operator's eyes close the rung.** Not the metric.

---

## 3. The invariants

Stated as testable rules, each with the incident that earned it. I checked each
candidate against the session rather than accepting it; two needed strengthening
and one needed narrowing.

### 3.1 A claim about execution frequency must be counted in situ. A derivation is necessary but not sufficient.

**Verified, and stronger than proposed — three refutations, escalating.**

1. **Site B.** "Proven to run every attached frame — statically *and* live (the
   measured 1.75-yd freeze)." Direct count: gates passed on 35 frames, cave ran
   on 5. **Roughly one frame in seven.**
2. **The per-frame sweep.** Also ~5/play. Read as "these sites are lock-in
   driven" — itself wrong; the hook sat inside a kind-4-only filter.
3. **State-32's `ai_think`.** Derived per-frame through a gate-free dispatcher
   with the whole chain quoted from the main loop down, every link single-caller
   or table-verified. Predicted ~110. **Measured 25.**

The third is the one that sets the rule's strength: a *correct, fully derived*
call chain still over-predicted by 4×. Argument establishes that a site *can*
run; only a counter establishes that it *does*. Every design that asserted a
frequency in prose was wrong.

### 3.2 A closed-set negative needs a **five**-axis census, run by the person relying on it.

**Verified, and the axis count is wrong in the older docs.** `lessons-learned.md`
Part 6 gives four (no `jal`, no `j`, no branch, no pointer word) plus the
`lui`+`addiu` pair. This session added the axis that actually did the damage:

* **`addiu rD, rBase, simm` off a base a `lui` established far away.** The pair
  never completes to the target, so a search *for the target* finds nothing.
  Caves #1 and #3 both died here. One base served four callbacks in #1.

And #11 added a sixth, structural test that is not a reference at all: the
region must be **padding, not a function**. Cave #2 has zero external references
and is still unusable, because its tail branches into the words you would
overwrite.

Corollary, paid for four times in one session: **prior censuses do not
transfer.** Cave #2 had been "censused clean by an earlier agent whose pass
omitted the branch-target axis."

### 3.3 Any statistic on an episodic event must be episode-scoped.

**Verified, no exceptions found.** `defender_pushback` 3.178 → 0.410 yd once
scoped to `dt_role == 2`; the double team occupies 13–30 of 308 frames.
Three metrics shipped wrong before the rule; a later review found five more of
the same class. The general form: **if the event is a fraction of the window,
the mean measures the remainder.**

### 3.4 The operator's screen observation outranks the instrument — but not the disassembly.

**Verified seven for seven, and it needs the second clause.** He caught a
symmetric 256× test, inverted arms, a whole-play metric, two over-claims, the
R6 reframe, and read P11's result as a handoff before the clip data confirmed
it. Not once was he wrong about *behaviour*.

The narrowing is real and is already in `lessons-learned.md` Part 8: his
*theories* about code do not inherit that authority — the split-angle theory
was refuted in positional form even though his conclusion stood by another
route. **Observations outrank instruments; theories do not.**

### 3.5 No patch is designed against a belief whose supporting measurement does not exist on disk.

This is the testable form of "measure before patching", which is too vague to
enforce. Nine of the sixteen dead ends were refutable from evidence already in
the repository. The sharpened corollary from #7: **a measurement of an underived
field is not a measurement.**

### 3.6 A differential claim requires a differential test.

P5 scaled a value the engine writes identically to *both* members of the pair,
so a 256× amplifier cancelled exactly and the conclusion drawn from it had to be
withdrawn. Before believing a lever, confirm it can produce an asymmetry.

---

## 4. Tooling gaps, ranked by time saved — and what was built

Ranked by hours this session would have saved, from the catalogue.

| rank | tool | would have prevented | est. saved | status |
|---|---|---|---|---|
| 1 | **State-scoped field-writer census** — every writer of a field, restricted to those reachable in a given AI state | #12, #6 — the entire position-writing branch, four patches | **~6 h** | **specified below, not built** — the static half is buildable, the state-scoping needs the dispatch-table reachability walk |
| 2 | **Gate census with leave-one-out and host verdict** | #2, #13, and it names #14's limiter | ~3 h | **BUILT** — `tools/gatecensus.py` |
| 3 | **Five-axis cave census over a range, plus write-window crossings** | #9, #10, #11 | ~2 h + a rewritten doc | **BUILT** — `recon/cave_census.py` |
| 4 | **Caller-chain walker to the frame tick, all axes** | #14, and the P10 host hunt | ~2 h | **BUILT** — `cave_census --callers` (the walk is a loop over it) |
| 5 | **Reachability-before-deploy** | #3 | 1 deploy cycle | **EXISTS** — `tools/statereader.py`, invented mid-session, paid off immediately |
| 6 | **Patch verify + arm read-back rows** — assert the current word, emit the pnach, record a read-back per iteration | the `extended` byte-write; unverifiable arm labels | recurring | gap (`tooling-gaps.md` Gap 3) |
| 7 | **Episode-scoping helper** | #16 | recurring | partly in the analyze layer |

### Built: `tools/gatecensus.py`

Counts what a patch's gate chain *would* pass, over a trace already on disk.

```
python3 tools/gatecensus.py extract/slot9_baseline_dt3.jsonl --iteration 0 \
    --gate 'helper_role:   self.dt_role == 1' \
    --gate 'defender_role: link.dt_role == 2' \
    --gate 'sides_differ:  self.side != link.side' \
    --gate 'defender_kind: link.engagement in 5,6' \
    --canary 5
```

Three outputs, each answering a question that cost a deploy cycle:

* **the ladder** — survivors after each gate, in cave order. Names the binding
  gate with no inference.
* **leave-one-out** — what removing each single gate would buy. P8b's null is
  visible here for free.
* **host verdict** — predicted gate-passing frames against the measured canary.
  A large gap prints `HOST-BOUND` and says so explicitly: the hook is in the
  wrong function and no gate edit can fix it.

`link` resolves through the engagement handle exactly as the cave does
(`kind | side<<8 | index<<16`), and a gate over a field the spec never sampled
is flagged rather than silently counted as zero — four probes in a row wanted
fields the spec did not sample.

Run against the committed baseline it reports, in seconds, that engagement
kind 8 **never occurs** on this run play — the fact DT-HOLD-90 was designed
and deployed without.

### Built: `recon/cave_census.py`

Inverts the search. Instead of asking "is this address referenced?" once per
address, it propagates register values forward across the image once and records
every address the code *forms*, alongside jump, branch and pointer-word targets.
One pass answers any number of candidate ranges, so re-censusing before each use
costs seconds — which is the actual reason regions stopped being re-checked.

```
python3 -m recon.cave_census extract/SLUS_207.52 --write=55 0x0044C1C0:640
python3 -m recon.cave_census extract/SLUS_207.52 --callers 0x001E8088
```

It reproduces all four cave incidents and confirms the three verified
alternatives (#4, #5, #6). `tests/test_cave_census.py` pins each one, so a
regression cannot quietly re-authorise a poisoned region.

**Honest limits.** Both tools are static or trace-based. A clean census is
necessary, not sufficient — computed `jalr` through a never-materialised
pointer, addresses arriving from data files, and runtime `.text` overwrites all
evade it, so `code-caves.md`'s runtime execute-breakpoint test still gates first
use. A gate census describes the world that was *recorded*; a patch that changes
behaviour changes the counts. Use both to kill hopeless work cheaply, not to
bless promising work.

### Specified, not built: the state-scoped field-writer census

The highest-value tool and the one I could not test offline. Shape:

1. every store to field `+X` image-wide, with cross-function base tracking
   (`find_field_refs` already does the hard part);
2. the set of functions reachable from a given AI state's dispatch row
   (`enter`/`can_leave`/`ai_think`/`user_think`), transitively — `ImageIndex`
   already holds the call graph;
3. intersect, and report *who else writes this field while that state is live*.

Against `+0x190` during kinds 5/6 this returns the empty set, which is the
finding that closed four patches' worth of work. It needs a reachability walk
whose termination and soundness I cannot verify without live traces, so it is
specified rather than shipped.

---

## 5. Agent-orchestration lessons

This session ran many parallel lanes — five reviewing one patch, three on
DT-3, four on the registry, five on animation, three on the drive.

### What worked

* **A shared lane contract, stated in every file.** Static only; every
  instruction re-read *this pass*; cite a sibling's measurement rather than
  re-deriving it; mark `UNVERIFIED` in place; and a closing **"Could not
  establish — do not inherit as fact"** section. Every lane shipped that last
  section, and it is the reason adjudication was possible at all.
* **One lane auditing another's claim.** The DT-3 review is the model: two
  lanes reached the answer through control flow, one through the data plane,
  sharing no address but the patched word. That is a genuine double-blind.
* **Pre-registered oracles with an explicit "Refutes:" clause**, so a lane's
  result was informative in both directions.
* **A pre-armed escalation with a fire condition** ("any DT-3 lane returns less
  than strongly positive"), so the response did not need to be designed under
  pressure.
* **Cross-pollinating mid-run** — forwarding a sibling's reframe to lanes still
  running saved several from dead ends.

### What failed

* **A shared wrong premise across five lanes.** DT-HOLD-90 was reviewed by five
  lanes; "every static review lane was internally correct; the wrongly-shared
  premise was that registry doubles are kind-8 engagements." Five internally
  correct lanes produced zero detection, because they were partitioned by
  **area** and all inherited one assumption. It happened again in the animation
  round: every lane was briefed "the fix is animation selection", and A2
  dissolved the premise for all of them at once.
* **Findings saved for the end were lost.** Three lanes were killed mid-run by a
  session limit; their findings survived only because someone transcribed the
  progress notifications verbatim, `UNVERIFIED`.
* **An unverified rescued fragment became load-bearing.** That transcript's
  "the selector pushes id 168" was consumed as an input by two other lanes'
  designs while still marked unverified.
* **Lanes citing a sibling doc that was being corrected in parallel.** One
  lane's evidence item cited a document another lane corrected in three places;
  they landed one minute apart.
* **Duplicated corrections.** Two lanes independently proved the same constant
  was an angle, not a distance.
* **"Only one can be right" as a framing.** When two lanes disagreed about the
  driving animation, the synthesis framed it as a contradiction. Both were
  right about different code paths that run in sequence — a resolution the same
  document had hypothesised in its own text and then not acted on. The identical
  shape had already occurred once between two other lanes and been resolved for
  free by quoting both cited addresses side by side.
* **Scratch contention and duplicated tooling** — lanes overwriting each other's
  files; eight lanes independently rebuilding the same disassembler.

### The rules

1. **Give each lane a premise to attack, not an area to cover.** At least one
   lane's assignment must be *"prove this patch cannot fire"* and another's
   *"find the measurement that would refute the shared premise"*. Area
   partitioning produced five correct lanes and one wrong answer.
2. **Write findings incrementally, to a per-lane file in a per-lane directory.**
   Never hold results for a final report; interruption is routine.
3. **When two lanes disagree, read the other lane's cited address before
   re-arguing.** Twice this session the disagreement was two code paths, and
   both times quoting both sites resolved it immediately.
4. **Every inherited fact carries its source and its grade.** An `UNVERIFIED`
   fragment may not become load-bearing without a lane tasked to re-derive it.
5. **Cite the commit, not the document**, when depending on a sibling — siblings
   land minutes apart and get corrected.
6. **Every lane ends with "could not establish", and every open item carries its
   cheapest closing procedure.** This was already the practice and it is the
   single most reusable thing in the corpus.
7. **Promote scratch tooling in the same session, and fix the shared tool rather
   than working around it** — a tool defect costs the product of its lifetime
   and the number of agents who hit it.

---

## 6. What generalises, and what is only this engine

**Generalises to any binary reverse-engineering campaign:**
execution frequency by counter, never by argument (§3.1); the closed-set census
and the rule that prior censuses do not transfer (§3.2); episode-scoping
(§3.3); a null is uninformative unless the patch could fire; derive a field's
producer before sampling it; differential claims need differential tests; the
observer-outranks-the-instrument rule and its narrowing; and every
orchestration rule in §5.

**Specific to this engine and toolchain:** the exact axis set and opcode
encodings (MIPS/R5900 `lui`/`addiu`/`ori`, REGIMM, COP1 branch forms);
`patch=1` vs `patch=0` and the `extended` byte-write trap; the `.p2s`
zstd container; the engagement-handle layout `kind | side<<8 | index<<16`; the
`dt_role`/engagement-kind enums; BAM24 angles; and the 93-state dispatch table
that makes `--callers` find a data word instead of a `jal`.

**The one-sentence version.** Almost everything expensive in this session was
bought by asserting something that a free, already-possible check would have
refuted — how often a site runs, whether a region is dead, whether a state ever
occurs, who else writes a field. The method is not more analysis. It is
**running the cheap checks before the expensive ones**, and the ordering in §2
is the whole of it.

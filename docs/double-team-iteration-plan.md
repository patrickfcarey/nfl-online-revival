# The double-team iteration ladder — from "holds and loses" to "two men bury him"

Recorded 2026-08-11, static/offline. This is the ordered, testable path from
the world P1+P4 built (the pair commits and never releases, but never wins)
to the thing the operator asked for: two men who stay attached, drive the
defender backwards in proportion to combined weight+STR, peel on outcome not
on a clock, sometimes never peel, with a second double on the nose and the
same law covering big-on-small singles.

Sources: `double-team-requirements.md` (R1-R7, R6a/b/c, R6z),
`on-skates-requirements.md` (S1-S5, S4-D, the unified mass law, P5/P6/P7
results, Route C), `double-team-solution.md` (P1-P4, the market diagnosis),
`anim-lanes/1..4` (dispatcher, mass law, clip inventory, synthesis + live
probe), `dt-lanes/*` (drive machinery, help-score, run/pass contrast),
`seed-testing-plan.md` (S0-S4 stages, range cards), `state-reachability.md`,
`code-caves.md`. Every patch-site stock word cited below was re-read from
`extract/SLUS_207.52` this pass (rule 4), including the full yes-set table
`0x00583360..0x005833CC` — entry 158 at `0x00583390` confirmed `001F0C24`
(no-arm), yes-arm `001F0C20`, yes-set exactly {146-151, 168-170, 173},
and **161 is currently NO-set** (`0x0058339C = 001F0C24`), which matters at
rung C3. Nothing in this document is deployed by this document.

---

## 0. Where the ladder starts (measured, 2026-08-11)

Deployed and confirmed executing: **P1** (market commit guard, hook
`0x001F4A30` -> cave #11 `0x00514920`) and **P4** (record cap 61->361,
`0x001F6A74 = 2C420169`), one pnach (`14F8B841.dt-market-guard-p1.pnach`).
Result, eyes and data agreeing 4/4:

* The pair commits and holds: one record 2..64 (~1.07 s), zero re-shops,
  roles intact, first kind-8 attach ever seen on a run play. Operator:
  "the tight end does not release, which is awesome."
* Still wrong, in causal order:
  1. **The record dies at ~64 because the capture poisons it**: TE+DE are
     captured into pair animation 158 (kinds 5/6), 158 is NO-set in
     `0x00583360`, the manage 5/6 arm (`0x001f67d8` -> `0x001f0ba8`)
     returns 0, teardown. Confirmed the active killer.
  2. **Nobody is driven**: DE dy -0.51 (he gains ground), episode pushback
     never above the 15-inch era, `carrier_yards` -0.70 unchanged. All
     three force levers are dead by measurement (speed_cmd P6, velocity
     P7, collision excluded by mutual no-collide): **Route C stands —
     animation root motion owns both bodies; driving = selecting a
     different clip.**
  3. **Contact is lost**: TE real contact 20/62 frames; the record's last
     22 frames are contactless shadowing, gap 1.75 yd at record end.
     Downstream of 2 — a defender driven backwards is a defender you are
     touching.
  4. **Only one double forms**: the LG pairs with the LT on the left end
     from f19 while the C singles the NT — a priority defect in the helper
     election, not availability (R7, diagnosed).
* Determinism wobble on record: 2 of ~25 iterations diverged, both under
  heavy drive scaling. Not yet explained.

## 1. The end state (what "working like I've wanted" means)

From R6a/b/c, R6z, R7, S1-S5, S4-D and the unified mass law, in one list —
this is the acceptance that ends the project segment (§10 makes it
operational):

1. A double team forms fast, stays attached under contact, and **drives
   the doubled man backwards** ≥ 1.0 yd on a won rep (R3), feet moving, no
   warps, no defender nerfs.
2. Drive magnitude follows **one law for singles and doubles**:
   `D = clamp(margin + R² − 1, 0, 3)`, `R = Σ_attackers(weight + STR_eff)
   / defender(weight + STR_eff)`, helper counted only while touching
   (S4-D); direction = the primary's earned bearing.
3. The pair **peels on outcome**: displacement past a threshold with a
   floor (~30 frames) and a ceiling — and versus a dominant nose the
   ceiling is suspended and they bury him to the whistle (R6b/R6c).
   Goal-line Power O must get this behaviour with **no special case**.
4. **Two concurrent doubles** where football wants them: C+LG on the NT
   while TE+RT handle the DE and the LT singles the end (R7).
5. Near-equal singles are visually and statistically stock (R5);
   dominant singles grind forward (the same law's band 1); pass sets stay
   contained (S5) — and, when R4 is taken, pass doubles register without
   downfield rides.
6. Zone combos (R6z) peel on linebacker declaration via a swapped
   predicate, never as the primary trigger — later, entry-gated (§9).

## 2. Standing rules — inherited by every rung, stated once

1. **One patch at a time** (CLAUDE.md rule 2). Every rung is its own pnach
   arm, measured alone on its own savestate runs before any combination;
   combinations re-run the S2 matrix (each single, then the pair, then the
   stack) plus `tests/test_madden_lab_*.py`.
2. **Measure before patching.** Every phase opens with a probe rung; no
   patch is designed against an unprobed belief. Three poisoned caves and
   five refuted theories in one day were all built-before-measured.
3. **S0 before belief** (`seed-testing-plan.md`): PINE word read-back of
   every patched word before every measured run. The pnach parsing log is
   not evidence; the `extended` byte-write incident is why.
4. **Reachability before deploy** (`state-reachability.md`):
   `tools/statereader.py` confirms the stock word at every patch address
   in the savestate that will be loaded, and evaluates gate conditions
   offline where possible.
5. **Cave discipline** (`code-caves.md`): all pnach lines `patch=1`
   (load_state wipes `patch=0` bodies — banked lesson); every word
   round-trips through `recon.mipsdis`; every cave passes runtime
   liveness test 1 (execute-breakpoints through boot -> menus -> a
   quarter -> save/load) before first use. Cave budget: **#7
   `0x00443270`** (480 B, re-censused clean) is this ladder's workhorse;
   **#2 `0x0044C1C0`** (640 B, censused clean) is the fallback; #11 is
   full (3 words free); #1 and #3 are poisoned — never use.
6. **R5 by construction, not by tuning**: every new behaviour is gated on
   state only a live double (or a genuinely dominant pairing under the
   law) possesses — dt_role 0/1/2, kind 8, or D past a knee. Near-equal
   pairs must sit in the flat part of every curve.
7. **No defender nerfs, no position warps, no metric-only wins.** The
   operator's eyes are the primary instrument and close every rung.
8. **Episode-scoped metrics only** — any whole-play statistic is presumed
   wrong until shown otherwise (three have shipped wrong already).
9. **Determinism protocol**: minimum 3 frozen-seed iterations per arm;
   any divergent iteration is flagged, replayed, and quarantined from
   the oracle (the two known wobbles both hid under drive scaling).
10. **Revert = remove the arm, S0-confirm stock words, re-run the current
    baseline to confirm return.** Failed integration bisects by
    re-running the S2 matrix singles.

## 3. The ladder at a glance

| rung | kind | serves | one-line oracle |
|---|---|---|---|
| A1 | tools | everything | anim-id accessor + FIELDS reproduce known values offline against `ee_inplay.bin` |
| A2 | probe | baseline W1 | current-anim id reads 158 on the captured pair while `+0x3DE` reads class vocab — lanes 1/3 reconciled |
| B1 | patch (1 data word) | R6a | record survives past 64; kind 8 persists through the capture; slots 6/7/8 unmoved |
| C1a/b/c | diagnostic arms | Route C | requested clip id plays; per-clip defender displacement measured; arms reverted |
| C2 | patch (cave) | mass law producer | `+0x404` reads ≈2.4 on the attached triple, ≈margin everywhere else; slot 7 bit-identical |
| C3 | patch (cave) | S1/S2/S4/S4-D, R3 | dominant pair captures into the crowned clip; defender_pushback ≥ +1.0 yd; C/NT keeps 158 |
| C4 | conditional patch | S4-D touch gate | helper stays attached while the pile moves; attach uptime > 80% of record |
| C5 | tuning cards | S3 calibration | knees/exponent/rolls swept per range cards; no card tail violated |
| D1 | probe | R6b/R6c | the ender that fires is displacement-shaped (B1-geometry) or absent vs the nose |
| D2 | conditional (1 word) | R6b | phantom-peel B5 removed; nothing else moves |
| D3 | conditional cave | R6b/c/z shape | `should_peel` predicate replaces expiry; goal-line holds with no special case |
| E1 | static+data probe | R7 | the LG's election loss is named (scorer term vs refiner arm) |
| E2 | patch | R7 | two concurrent records: C+LG on NT, TE+RT on DE, LT singles the LE |
| F1 | patch (1 word) | R4/S5 | doubles register on slot 7; no OL past ~1.5 yd of LOS on pass sets |
| F2 | entry-gated series | R6z | zone combo splits only on declaration; slot 9 regression untouched |

Rungs are strictly ordered within phases; phases B->C->D are strictly
ordered; E requires C; F1 requires C+D stable; F2 has its own entry gate
(§9). A1/A2 precede everything.

**Why this order.** Today's proven lesson generalises: a rung's result is
only interpretable if the layers beneath it are already true. A drive
patch before the record survives contact buys nothing (measured); a peel
predicate before drive exists has no displacement to read; a second double
of a non-driving pair proves nothing; pass registration before S5 is
verified manufactures downfield rides. Each phase turns exactly one
unknown into a fact the next phase stands on.

---

## 4. Phase A — instruments first (no game-code patches)

### A1 — the anim-id accessor and the missing fields

* **Requirement served:** every animation rung below. Three probes in a
  row wanted fields the spec does not sample; the clip vocabulary cannot
  be adjudicated without reading the current clip.
* **Work (tools, not patches):**
  1. A `World`/`Player` accessor for the **current animation id**:
     pointer-chase `animptr = [player+0x304]`, four slots of 0x64 bytes,
     `status = u16[slot+6]` (3 = active), `id = u16[slot+4]`, engine
     getter semantics = lowest active slot (`0x003ad410`). Validated
     offline: against `extract/ee_inplay.bin` it must reproduce the known
     pre-snap stances (QB 91, OL 86, NT 21); against
     `experiments/states/double_team_slot9.p2s` via `statereader` the
     same. This is the decisive instrument of §5/§6.
  2. Add to `experiments/double_team.py` FIELDS: `facing` (+0x1A8),
     `vel_x/vel_y` (+0x1B8..), `staged_drive` (+0x404), and the
     per-frame current-anim id via the new accessor (budgeted: if the
     added reads push the sampler below ~1 frame in 2 on 22 players,
     split into a paired-players-only sub-spec — the sampling-loss
     lesson).
  3. Confirm B3/B4 measurability: the 65° facing tests read +0x1A8; with
     facing sampled, the drive-pass break chain becomes fully
     adjudicable for the first time.
* **Acceptance:** offline validations above; `tests/test_madden_lab_*.py`
  green; no rig time needed.
* **On failure:** the accessor is wrong, not the world — fix against the
  dump before any live run.

### A2 — re-baseline the fixed world, and settle the vocabulary

* **Requirement served:** a reference world ("W1" = stock + P1 + P4) for
  every later delta; the lane 1 vs lane 3 dispute (§5).
* **Probe:** slot 9, 3+ frozen-seed iterations, full new FIELDS. Also
  one-pass regression captures of slots 6/7/8 on the same build. Hygiene
  first: only the P1+P4 arm enabled on the rig; S0 read-back of
  `0x001F4A30 = 08145248`, `0x00514920 = 8E82005C`, `0x001F6A74 =
  2C420169`; statereader confirms the slot-9 state itself carries stock
  words (no baked cheats).
* **Pre-registered readings:**
  * During TE/DE kinds 5/6, the **current-anim accessor reads 158**
    while `+0x3DE` reads the small class vocabulary (15/17/18/19) — the
    prediction that reconciles lane 1, lane 3, and the live probe (§5).
    If it does NOT read 158, lane 1's capture story is wrong and C1
    re-plans before any patch.
  * Which manage/drive ender kills the record at ~64, now with facings
    sampled (B3/B4 become computable); the capture-kill reading is
    confirmed or corrected.
  * The kind-4 pre-capture frames: which segment ids the pair cycles
    (grid cells), as the baseline for C2's secondary oracle.
* **Operator:** no change expected on screen; this is the instrument run.
* **On failure (wobble):** if determinism diverges at stock+P1+P4, stop —
  the wobble is not drive-scaling-induced and must be characterised
  before any oracle below is trusted.

## 5. Adjudication 1 — lane 1 vs lane 3, resolved by measurement, not argument

The disagreement: lane 1 says the driving family is 149/150 (+ grid cells
50/53/54/58) and the mechanism is the hardcoded capture 158 at
`0x001f7d08`; lane 3 says the directional drive family is **161 classes
17/18** dispatched by geometry at `0x001ef130`. Both are selector-semantics
inferences; **neither lane established per-clip root displacement**, and
the one live probe showed `+0x3DE` speaks a third vocabulary entirely.

The resolution is empirical and staged:

1. **A2 names the words.** The pointer-chased current-anim id is the
   authoritative "what is playing" instrument; `+0x3DE` is demoted to a
   class/participant word (authoritative only as a pair-match indicator
   during kinds 5/6). If A2 reads 158 during the capture, both lanes were
   right about different words and the argument dissolves.
2. **C1 measures the clips.** One diagnostic arm per candidate id flips
   the capture request and reads displacement off the bodies. Whichever
   id moves the defender backwards, with acceptable visuals, is crowned —
   by data, not by which lane wrote the better document.
3. The crowned id's **family membership decides the companion edits**:
   149/150 are yes-set already (B1's edit covers 158-band pairs only);
   161 is NO-set at `0x0058339C` and would need its own yes-arm word
   flipped (or the record dies during the drive clip — the exact defect
   B1 fixes for 158).
4. **Anim lane 5 (`anim-lanes/5-clip-semantics.md`, in flight)** is
   mining id semantics from the owned memory images — it has already
   live-confirmed a yes-set id (147) playing on a real engaged kind-6
   pair with `+0x3DE = 18`, supporting the two-vocabulary reading. Its
   output refines C1's candidate list and A2's predictions; it does not
   replace the live displacement measurement, which no static read can
   supply.

## 6. Phase B — survive the capture (R6a completed)

### B1 — admit 158 to the yes-set (one data word)

* **Requirement served:** R6a — the pairing survives; specifically the
  confirmed frame-64 killer (capture anim 158 in the NO-set kills the
  record from the inside, and shuts the helper's attach gate for good).
* **Patch:** `patch=1,EE,00583390,word,001F0C20` (was `001F0C24`;
  both words re-read from the ELF this pass). Data edit, no code moved.
* **Reachability / pre-deploy:** statereader confirms `0x00583390 =
  001F0C24` in the slot-9 state. Consumer census (from the solution doc,
  re-affirmed by the run/pass lane): the table is dispatched only inside
  `0x001f0ba8`, whose two callers are the registry manage 5/6 arm
  (`0x001f67d8`) and the attach gate fallback (`0x001f7590`) — both
  record/attach-scoped. `0x001f5db0` uses the OTHER table
  (`0x00583920`), which already includes 158 — unchanged. Pass plays:
  the registry never forms (DT-1) and capture is mode-gated off while
  the QB holds, so the edit is structurally inert on slot 7/8.
* **Pre-registered oracle:**
  * MOVES: `dt_longest_hold` > 64 (target ≥ 90; P4's cap now allows
    ~361); kind-8 sightings on the helper **during** primary kinds 5/6
    (impossible at stock — this is the execution canary); record end no
    longer coincides with the capture frame.
  * MUST NOT MOVE: slot 7 flap cadence and pass metrics; slot 6/8
    frame-compare; `carrier_yards` and `defender_pushback` are
    **pre-registered as expected-unchanged** (-0.70 / ≤ baseline) — 158
    is still the neutral clip; a null here is NOT a failure of B1.
  * WATCH (not gates): what ends the record now — with facings sampled,
    name the ender (candidates: B1-geometry `defender.y < helper.y`,
    B2 LOS-fill, B3/B4 65°, B5 peel-man state {2,30}, separation).
    This observation seeds Phase D.
* **Operator acceptance:** the pair stays glued through the animation
  well past one second; still no push (told in advance, so the
  no-push does not read as failure).
* **On failure:** if the record still dies ≈64 with the word verified in
  memory, the manage-arm reading is wrong — revert, re-trace
  `0x001f67d8`'s live inputs with the A1 fields before re-planning.
  Revert = restore `001F0C24`.

## 7. Phase C — the drive war (Route C executed)

### C1 — name the driving clip (diagnostic arms, each deployed alone, each reverted)

* **Requirement served:** S1/S2 mechanism selection; closes the gap
  neither anim lane could (per-clip root displacement magnitudes are not
  statically readable — lane 3 §5).
* **Arms** (run on W1+B1; S0 word read-back each; 3 iterations each;
  **reverted after measurement** — while active these violate R5 on
  every run capture by design, exactly like P5/P6/P7):
  * **C1a:** `0x001F7D08: 2413009E -> 24130095` (capture id 158 -> 149,
    the pancake-pool clip; yes-set, so B1-independent survival).
  * **C1b:** `... -> 24130096` (150, the drive-engage clip; yes-set).
  * **C1c** (only if a/b show no backward motion): a small cave arm in
    #7 that builds the capture request with **id 161 and class byte
    17/18 in request+0x42** (the class is how 161 carries direction;
    plain P-A on 161 would leave class 255 = any variant, an
    uninterpretable roll). ~8 words + the displaced `sh`. Companion
    word for the arm only: `0x0058339C -> 001F0C20` so the record can
    survive the clip.
* **Pre-registered oracle per arm:**
  * Execution canary: current-anim id == the requested id during kinds
    5/6 (A1 accessor). No canary, no conclusions — check S0/dispatch
    failure (a failed request leaves the pair kind 4: also
    informative, pre-registered as "clip does not match this pairing",
    lane 1 risk (i)).
  * MOVES (measured, per-clip): episode-scoped defender displacement
    while the clip plays (`defender_pushback` sign convention: positive
    = driven toward the defence's backfield); pair translation vector;
    whether contact/gap closes.
  * The operator files a look verdict per arm: churn / pancake /
    slide / statue; any warp or interpenetration horror = the clip is
    disqualified regardless of numbers.
* **Decision rule:** crown the id (and class) with the best backward
  displacement and an acceptable look. If **no** candidate moves a body
  (all root motions ≈ 0), Route C's clip-selection premise fails for
  these families — fall back to **F-A: the root-motion magnitude cave**
  (scale `motion_block{+0,+4}` after the converter `0x0018f9e0`
  returns, margin-gated; lane 3 §5), which turns any clip into a
  magnitude lever. That fallback then slots in as C3's mechanism with
  the same oracle.
* **On failure/completion:** every arm reverted; W1+B1 re-confirmed by
  one baseline iteration before C2.

### C2 — the mass-law producer (the confluence cave)

* **Requirement served:** S3/S4/S4-D force model — the unified law
  computed where the engine computes its own margin.
* **Patch:** hook `0x001F16DC` (`E6540000 swc1 f20,0(s2)`, verified) ->
  `jal` cave #7 `0x00443270`. Body per `anim-lanes/2-mass-law.md` §5.3
  (~55 words): `M = weight(+0xAEC) + STR_eff(+0xB8E)` both men; helper
  added **only** while `dt_role==2` on the defender and the registry
  helper holds kind 8 (S4-D touch gate); comp2 picks the winner side;
  `D = clamp(margin + R² − 1, 0, 3)` stored to **both** men's +0x404.
  The delay slot at `0x001F16E0` stays stock; the pass-freeze path
  enters at `0x001F16E0` past the hook, so **S5 containment survives by
  construction, zero words spent**.
* **Reachability / pre-deploy:** cave #7 runtime liveness test 1 (it has
  never actually hosted executing code — the P6/P7 lines lived in #11's
  words; do not inherit "it ran clean"). Full `+0x404` reader census
  with biased-base tracking (the same sweep that closed +0x432) —
  lane 2 flagged this as its own unclosed item; it gates deploy.
  `mipsdis` round-trip of all words. Statereader: `0x001F16DC =
  E6540000` in the state.
* **Pre-registered oracle (deployed ALONE on W1+B1):**
  * Execution canary: `staged_drive` (+0x404, now in FIELDS) reads
    ≈ 2.4 on the TE/RT/DE triple while the helper is attached —
    unreachable by the stock formula (margins are ≤ 1.0 by
    construction) — and falls back within one re-lock of detach.
  * MUST NOT MOVE: C/NT and every near-equal pair within jitter of
    stock margin (|D − margin| ≤ 0.16 in band 0); slot 7 frozen-rep
    pass sets bit-identical; slot 6/8 frame-compare; determinism green.
  * ADJUDICATED (two docs mildly disagree; the run decides): lane 2
    says the only live consumer is the sweep's speed_cmd re-stamp;
    lane 1 §2.4b says the kind-4 grid reads +0x404 against its drive
    thresholds for column choice. Pre-register BOTH readings: dominant
    pair's pre-capture kind-4 segments may shift toward the
    blocker-winning cells {50,53,54} (allowed, desirable, recorded);
    `carrier_yards`/pushback drift is a watch, not a gate.
* **Operator acceptance:** nothing visibly different on ordinary plays;
  the doubled pair may close/flow slightly faster pre-capture.
* **On failure:** canary absent with S0 green -> the hook never ran
  (check reachability of the A-wins arm on this play) or a register
  assumption broke — revert (`E6540000` back), re-derive prologue
  liveness before retry. R5 violation in band 0 -> the curve is wrong,
  not the site; move to the conservative card (p=1, knees {0.2,0.7})
  and re-run.

### C3 — the margin-conditional capture (the consumer; the rung that moves #93)

* **Requirement served:** S1 sustained drive, S2 backward direction (the
  clip's root motion, along the primary's earned bearing per S4-D), S4
  compounding, R3's ≥ 1.0 yd — the ladder's payoff rung.
* **Patch:** hook `0x001F7D5C` (`A7B30040 sh s3,64(sp)`, verified) ->
  `j` cave (second region of #7, or #2 if crowded). Cave: load D from
  **ONE man's** +0x404 (see §8 — the identical-copies trap), compare
  against the overpower knee (1.5): if D ≥ knee, `s3 := crowned id`
  (+ class store to request+0x42 if the crown is 161); optionally a
  band-1 mid clip at the drive knee (0.40) once the overpower band is
  proven; else keep 158. Preserve `v0` (0x000E38E3) and `f0` (1.4) —
  both live for the fall-through stores; displaced `sh s3,64(sp)` in
  the cave; resume `0x001F7D60`. ~12-18 words.
* **Reachability / pre-deploy:** statereader word checks; confirm the
  crowned id's yes-set membership (else its companion word rides in the
  SAME arm and is listed in S0); mipsdis round-trip; note the capture
  fires only in modes {4,7} on the possessing side — run plays at/after
  the handoff, by construction.
* **Deployment shape (rule 2):** C3 is meaningful only on W1+B1+C2, so
  its acceptance is an S2 matrix by design: B1 alone (done), C2 alone
  (done), B1+C2, then B1+C2+C3 — each with its own S0 and 3 iterations,
  then slots 6/7/8.
* **Pre-registered oracle:**
  * Execution canary: current-anim id == crowned id on the TE/DE pair
    during kinds 5/6 (and 158 stays on C/NT — the negative canary).
  * MOVES: episode-scoped `defender_pushback` on the doubled DE ≥ +1.0
    yd (R3 acceptance; baseline era 15 in / current dy −0.51);
    TE-DE gap at record end < 1.0 yd (the 1.75-yd trail closes —
    contact retention is predicted to fall out of the defender no
    longer advancing); `carrier_yards` > −0.70 and positive-trending.
  * MUST NOT: C/NT stalemate stock (158, no displacement change);
    near-equal singles capture 158; slot 6/7/8 unchanged; no warps
    (operator + position-delta spike check); determinism green.
  * WATCH: clip flapping at segment ends near the knee (jitter in D can
    re-roll each re-capture — §8's staleness note); helper attach
    uptime while the pile translates (feeds C4).
* **Operator acceptance:** **"#93 moves backwards"** — driven, feet
  churning, pair attached, C/NT unmoved, nothing warps. This is the
  rung where his original complaint is answered or the design is wrong.
* **On failure:**
  * Canary green, no displacement -> the crowned clip's root motion was
    misjudged at capture geometry — return to C1 with the next
    candidate or invoke F-A (root-motion magnitude cave).
  * Flapping -> switch the cave to the **leaf recompute variant**
    (`R²−1` computed fresh from weights+STR in-cave, jitter-free and
    margin-free — lane 2 §4.4's ABI, ~8 extra words) so capture-time
    selection is deterministic per pairing.
  * R5 violation (a stock single drives) -> knee too low or the law's
    band 0 leaks — conservative card, re-run C2's must-not-move first.
  * Revert order: C3 word first (system falls back to B1+C2 world),
    then bisect per §2.10.

### C4 — helper rides the pile (conditional; deploy only if C3's watch trips)

* **Requirement served:** S4-D's touch gate in a *moving* world: Site B
  freezes an attached helper (staged_drive := 0 at `0x001F2164`
  `AE200024`, speed := 0 at `0x001F21A8` `E60001E8`, both verified) —
  correct for a static pile, wrong once the pile translates backwards:
  the frozen helper is left outside the 2.1-yd gate, detaches, and D
  loses its helper term (attach uptime collapses).
* **Patch (shape, if needed):** small cave at Site B replacing the two
  zero-stores for kind-8 helpers with "match the pair" (copy the
  primary's staged drive/bearing, or a fixed follow fraction). One
  mechanism, dt_role/kind-8-gated — invisible outside live doubles.
* **Oracle:** helper attach uptime > 80% of the record while the pair
  translates; defender_pushback does not regress; helper never
  overruns the pile (no orbiting). MUST NOT: slot 7 kind-8 flap
  behaviour (pass helpers ARE in kind 8 — this is the one rung whose
  regression surface includes the pass flap even before F1; its slot-7
  arm is mandatory).
* **Operator:** the second man stays leaned on the pile as it moves —
  the picture he described as missing.
* **On failure:** revert; the attach cycle (8->1->market->repair 7->8,
  legal under P1's kind gate) is the accepted fallback behaviour.

### C5 — calibration under cards (tuning rungs, data words only)

Knees {0.40, 1.5} and exponent p=2 first; range cards per
`seed-testing-plan.md` part 3: aggressive {0.25, 0.9}, conservative
{0.6, 2.0}, p=1 fallback. Optional richness knobs, each its own arm with
its own card, none before the main path holds: the converter roll ranges
(`0x001f036c/0418/08dc/09a0`, 150 -> 75 makes a 50% pool margin
near-certain pre-capture), grid cell pointer swaps (P-E), 161-class
sub-variant choice. Every card derived from a measured sweep — frozen-seed
3x until the seed-control project (part 1 of the seed plan) lands, then
S1-depth sweeps (20-30 seeds).

## 8. Adjudication 2 — where the mass law enters, and the margin==0 trap

Lane 1's finding: every selector margin is `A−B` over the two men's own
copies of +0x41C, and the confluence stores D **identically to both men**
— so redirecting a selector's two reads at a shared field yields
`D − D = 0` and the rolls silently never fire. A null that looks exactly
like refutation. The ladder's answer, in three commitments:

1. **The law enters as a producer at the confluence (C2)**, symmetric to
   both men on purpose — the per-frame sweep stamps each man's speed_cmd
   from his own copy, and asymmetric copies would split the pair's
   shared translation (lane 2 §4.0, considered and rejected).
2. **The consumer (C3) is a NEW threshold compare on ONE copy** — no
   A−B anywhere. D already *is* the pair-level margin; it feeds a
   `c.lt.s` against a constant knee. The stock converters and grid keep
   reading +0x41C/+0x414 untouched, so the engine's own pre-contact
   margin logic is never redirected at all.
3. **Two designed fallbacks, pre-committed:** (a) if D is stale or
   zeroed at capture time (kind-5/6 conversion's effect on +0x404 is
   lane 2's open item), or if jitter flaps the choice, the cave computes
   `R²−1` fresh from weights+STR (leaf ABI, jitter-free); (b) only if
   the capture hook itself proves wrong does injection move upstream to
   the per-man stamp `0x001f0c40` — with lane 2's documented
   contamination list (shed rebalance, pass-collapse rewrite) attached,
   as last resort, never silently.

## 9. Phase D — peel on outcome, not on a clock (R6b/R6c)

The engine's own break chain may already BE the outcome peel once drive
exists: B1-geometry (`defender.pos_y < helper.pos_y`) is
displacement-shaped, B2 is LOS-fill, and P4 moved the clock (B0) to ~6 s.
So Phase D is adjudicate-first, build-only-if-wrong:

### D1 — peel adjudication (probe)

On the C-stack world, per scenario, name the ender (facings now
sampled, so B3/B4 are computable for the first time):

* TE+RT drive the DE ≥ 1 yd -> **predict** B1-geometry fires (defender
  driven behind the helper) = outcome peel = PASS; helper climbs after
  the drive — football-correct.
* C+NT stalemate (or later C+LG on the nose) -> **predict** no ender
  before the whistle = R6c bury, emergent from P4's ceiling.
* Floor check: no ender may fire < ~30 frames on a live record.

### D2 — remove the phantom peel (conditional, one word)

`0x001F6B1C: 24160001 -> 00000000` (verified stock; P3 from the solution
doc — the drive pass stops tearing a record down because the stamped
peel man sits in ai_state {2,30}). Deploy only if D1 shows B5 firing (the
NT flips to state 2 exactly at contact-adjacent frames — the known
hazard). Predicted null on scenarios where B5 never armed. Oracle: the
D1 scenario that died of B5 now survives; nothing else moves.

### D3 — the `should_peel` predicate cave (conditional; the R6z-ready shape)

Built only if D1 shows the surviving enders are NOT outcome-shaped in
practice (e.g. facing-noise 65° teardowns mid-drive). One cave in the
drive pass replacing the offending tests with a **single replaceable
predicate**: `peel iff (displacement ≥ X AND t ≥ floor 30) OR t ≥
ceiling`, ceiling suspended when the pair's R ≥ overpower knee (R6c by
rule). The requirements' design constraint is binding here: displacement
inlined into the reselect flow is forbidden — the predicate must be one
swappable unit or R6z starts over. **Goal-line Power O is the
discriminating acceptance: if the predicate needs a goal-line special
case, the predicate is wrong** (a goal-line savestate must be recorded —
jumbo, inside the 2, expected PASS = double holds to the whistle).

## 10. Phase E — R7, the second double (entry: Phase C landed)

### E1 — name the LG's election loss (static + data probe)

Diagnosed so far: a priority defect — LG+LT double the LEFT END from f19
(near-equidistant options at f1: LE 2.77 vs NT 2.80 yd) while the C
singles the NT; capacity exists (4 record slots). Two candidate homes,
distinguishable without patching: (a) scorer geometry — the mapped
modifier chain (x1.2 marked-man `0x005ff188`, x1.1 own-man/play-side,
x0.85 not-my-target) simply prefers the end; (b) structural — the run
refiner `0x001f3a00`'s 24 position-class arms (unread — the standing gap
in `dt-lanes/help-score.md` §7.2) zero or cap the LG->NT pairing. Work:
read the refiner arms for the guard/centre classes; re-derive the LG's
f1-f19 scores from the P1 run data. Output: the lever, named.

### E2 — the priority patch

* **Shape (decided by E1):** either a scorer term — discount a defender
  already engaged by an adequate single blocker / weight a head-up
  heavy defender (the operator's mass-priority reading) — as a data
  retune or a ~10-word cave in the pair scorer; or, if structural, the
  refiner arm's constant. One lever, dt-blind code touched as narrowly
  as P1 touched the market.
* **Oracle:** two **concurrent** records on slot 9 — C+LG on the NT and
  TE+RT on the DE — neither stealing (P1's theft guard already
  protects); LT handles the LE single; both doubles then drive under
  the C-stack law (slot-9 masses put both pairs deep in the overpower
  band; lane 2's archetype table predicts a true 350-lb mountain drives
  visibly slower at D ≈ 1.6 vs 2.4 — S3 visible inside S4). MUST NOT:
  slot 6 kick-out scenario (LT+LG double the right DT, other DT left
  for the guard kick-out — the operator's prescribed play) still
  achievable; slot 7 pass assignment metrics unchanged.
* **Operator:** "the left guard needs to double team someone too" — he
  sees the LG fold onto the nose with the C.
* **On failure:** revert the term; the E1 diagnosis was wrong — re-probe
  before a second lever (never stack two election changes unmeasured).

## 11. Phase F — pass registration and zone (entry-gated expansions)

### F1 — DT-1: doubles register on pass (R4, with the S5 rider)

`0x001F6560: 14520009 -> 00000000` (verified stock). Entry conditions:
C-stack + D adjudication stable on runs; S5 verified intact (the 1.5-yd
pass freeze at `0x001f15cc` zeroes drive at the LOS — C2 never reaches
the freeze path by construction; capture is mode-gated off while the QB
holds, so the drive clip cannot fire in the pocket — verify both by
measurement on slot 7 before enabling). Oracle: records form on slot 7
with roles 0/1/2; P1's guard now protects them (flap stabilises toward
sustained 8 with Site B's designed freeze); **no OL body past ~1.5 yd
beyond the LOS on a pass set** (the ineligible-man visual is the fail);
sack timing / pressure metrics within their cards. PA plays inherit
safety from the same gates — confirm, don't assume, on one PA capture.

### F2 — R6z zone (entry conditions, then rungs)

Entry gate — all four before any zone rung (rule 1 keeps them out of
everything above):

1. A scheme signal exists (play-file work, `play-data.md`) — without it
   R6z1 has nothing to key on.
2. The record's fourth member slot (second-level target) verified real
   and populated.
3. A zone-run savestate recorded in `experiments/states/`.
4. Slot 9's own scheme classified (its doubles may already be zone
   combos — baseline truth first).

Then: zone baseline probe -> extend D3's predicate (`displacement OR
declaration`, selected by scheme; declaration **never** the primary
predicate — the goal-line rule is a standing constraint) -> R6z1-4
acceptance on the zone state with slot 9 re-run as the regression arm.
If D3 was never built (engine enders proved outcome-shaped), F2 begins
by building the predicate cave in D3's specified shape — that option was
kept open on purpose.

## 12. Definition of done

Staged per `seed-testing-plan.md`, and the operator closes every stage:

1. **Every rung**: S0 word verification + 3 frozen-seed iterations +
   its own MUST-NOT savestate arm, before entering any combination.
2. **Every phase boundary**: S2 matrix (singles, pairs, stack) across
   slots 9/6/7/8 + `tests/test_madden_lab_*.py` green.
3. **Range cards** exist and hold for: `carrier_yards`,
   `defender_pushback` (episode), `dt_longest_hold`, attach uptime,
   TE-DE gap at record end, pass-set max OL depth, sack timing. Cards
   derived from measured sweeps; frozen-seed until seed control (part 1
   of the seed plan) passes its own gate, then S3 depth (300-1000
   seeds) on the gated metrics, every out-of-range seed replayed.
4. **The operator's sign-off list** — the actual finish line:
   * Slot 9: double forms early, holds under contact, **#93 driven
     ≥ 1 yd**, feet churning, no warps; peel only after displacement or
     never against the nose; **C+LG double the NT concurrently**; C/NT
     head-up single (pre-E2) reads as a planted stalemate.
   * Slot 6: the prescribed LT+LG double / kick-out picture achievable;
     misdirection metrics within cards.
   * Slot 7/8: pass world unchanged until F1, then contained per F1.
   * Goal-line state: the double holds to the whistle with zero
     special-case code.
   * Big-on-small single (any state): the guard grinds the nickel —
     the unified law's clause, visible.
   * S4 soak: operator plays real games against the stack; his
     observations file as evidence with the same standing as the
     harness (they have out-ranked it repeatedly).
5. **Docs settle**: addresses.yaml gains every new field/site touched;
   the lanes' UNVERIFIED items either closed or explicitly carried.

## 13. Risk register (ranked)

1. **No reachable clip actually drives.** All drive labels are selector
   semantics; root-motion magnitudes were never statically readable. If
   C1 finds every candidate's displacement ≈ 0, Route C's
   clip-*selection* form fails. Mitigation: pre-committed fallback F-A
   (root-motion magnitude cave post-`0x0018f9e0`) turns any clip into a
   magnitude lever; the operator's 256x pancake proves large-motion
   pair clips exist somewhere in the data.
2. **Silent-null integrations.** The margin==0 trap (§8), D staleness at
   capture, a cave that never executes — each produces a null
   indistinguishable from refutation. Mitigation: every cave rung
   carries an execution canary whose value is unreachable by stock code
   (D≈2.4, clip id, kind-8-during-5/6), and S0 word read-back is
   mandatory before any run is believed.
3. **R5 erosion at scale.** The capture hook fires on every run-play
   capture; +0x404 has partially-censused consumers; C4's surface
   includes the pass flap; a subtle regression may hide until S2/S3
   sweeps — and seed control does not exist yet, so tails are currently
   invisible. Mitigation: band-0 flatness as a hard gate at C2/C3, the
   per-phase S2 matrix, cards before claims, and the seed-plan part 1
   as a scheduled prerequisite of S3 depth — plus the two known
   determinism wobbles quarantined and re-tested at every drive rung.

Secondary, tracked: cave #7 has never hosted executing code (liveness
test 1 gates it); 161's NO-set membership if crowned (companion word
rides the same arm); the helper-freeze/moving-pile interaction (C4's
trigger); B3/B4 were presumption until A1 lands facings.

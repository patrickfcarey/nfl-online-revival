# AI coach play-calling — leverage `ptrk`, drop the rating cheese

New campaign, 2026-08-13. Make the CPU call plays like a real coach — reading the
human's tendencies and countering them — instead of the current model (a small
authored pool, a steep favourite, and a hidden rating-boost "cheat"). Separate
code path from blocking/fatigue (the play-calling + `ptrk` systems). Scope only;
no patch until the hooks are confirmed (rules 1, 3, 4).

## Vision (operator, 2026-08-13)

> Use the existing 48-play database so the AI calls better plays, and remove the
> nonsense they have now for scaling their ratings. Anti-cheese measure FIRST:
> they must figure out pass vs run, inside vs outside, and whether you're
> targeting a specific RB or WR — in a fairly simple way. The 48-play database
> will need expanding; we need to track successful 1st downs and touchdowns, so
> the computer can be as smart as a real coach. This database is huge for us — we
> can seriously leverage it.

## The objective, the quality bar, and deliberate error (operator, 2026-08-13)

> The CPU needs to figure out the best defensive alignment for the opponent given
> game situation + its knowledge of tendencies. It can always be slightly wrong —
> build in some error, but not much. It has to be like a competent NFL head coach.
> That also means it'll go for 4th-and-1 and stuff.

**The unifying objective function:** `best response = f(game situation, opponent
tendencies, OWN personnel & matchups)` — every layer below is an input to, or a
consumer of, this one decision. Defensive alignment, play call, PA-bite,
formation check: all the same "read the situation + the scouting report + my own
roster, pick the best answer I can actually execute."

**Own-personnel constraint (operator):** the answer is bounded by the CPU's own
roster — maybe they're *forced into a subpar package because they don't have a
good nickel corner*. The theoretically-optimal call isn't always available; a
team without the horses accepts a mismatch or checks to a lesser package. This is
**readable**: player ratings live in the `+0xB70` block (we already use STR idx15,
STA idx14, AGI, AWR), so the CPU can evaluate its own DB quality and the
matchup (its CB rating vs the human's targeted WR) directly. Makes the coach
realistic — good rosters execute the plan, bad ones compromise.

**Quality bar: a competent NFL head coach.** Not psychic, not dumb. Smart,
situationally aware, aggressive when the math says so.

**Deliberate error (REQUIREMENT, not a bug):** the model must be **mostly right
but occasionally wrong — a small, TUNABLE error term.** A perfect defense is
unbeatable and unfun; a real coach guesses wrong sometimes. After computing the
best response, apply a small probability of a sub-optimal pick (or noise on the
weighting). "Some error, but not much" — one knob, low value. *(Note: the current
engine's `ptrk` already injects rating noise; this replaces cheat-noise with
honest decision-noise.)*

**Game management — the coach acts on 4th down, clock, score (both sides of the
ball).** "Go for it on 4th-and-1" is an OFFENSIVE game-management decision, and it
belongs to the same competent-coach objective. **CONSTRAINT (see below): the
situational POLICY (4th-down go/no-go, 2-min drill, clock) lives in the DISC
BYTECODE SCRIPT, not the ELF** — the 41-slot state array feeds it, but the rules
are authored data. That surface is real but separate from the ELF patching the
rest of this feature uses.

## THE KEY INSIGHT — the "48-play database" is `ptrk`

The 48-play tracker the operator means is **`ptrk`** (`play-tendency-ai.md`,
fully mapped): registry object, per-game, **two 48×16 record rings (one per
side) + counts**, franchise-persistent (`GBIN`/`STPG`). It already:
- records every play call, both sides (16-byte records in EE memory);
- computes a **recency-weighted repetition factor** `f` (table `0x00540FE0`) and
  a **recent-success factor** (currently "> 2 yд gains", table `0x00540FF0`);
- is **read by the CPU play-caller to choose its own plays**.

**The "rating-scaling nonsense" is the SAME object mis-wired:** `ptrk` today
feeds **9 one-sided CPU-advantage consumers** that inflate CPU *ratings* when the
human repeats (AWR+AWR·f break-off across all 5 coverage states; Break-Block
`+ (s/2)·f`; Tackle `+ (s/3)·f`, explicitly anti-human; ball-contest `×(1+0.25f)`;
event-rate). Evidence + addresses: `play-tendency-ai.md` "Who consumes it".

So the feature is a **rewire, not a build**: same tracker, expanded schema, feeding
play *selection* instead of rating boosts.

## What `ptrk` already gives us (the leverage inventory)

| already exists | where |
|---|---|
| per-side 48-play ring, franchise-persistent | ctor `0x0024D890`; save/load `0x0024E458` |
| per-play recompute (the write hook) | `0x0024D9C0` |
| recency-weight tables (repetition / success) | `0x00540FE0` / `0x00540FF0` |
| **undecoded record fields** (room for new dimensions) | record = `{playId, u32 ?, u32 ?, u32 flags}` |
| CPU play-caller reads history to choose | see conflict note below |

## Architectural decision — the play-caller is a NEW system, not a patch (operator, 2026-08-13)

> We also have to fix how the CPU calls plays, and make sure it's a totally
> different system.

The existing selection is **not worth patching** — its problems are structural
(`ai-play-calling.md`): the two-pass **class renormalization** (steep favourite —
one play can own 80% of the roulette), the **yards-scaled matchup memory** (a lone
15-yд memory ≈ 16×), and the `ptrk` **rating cheese**. **DECISION: REPLACE the
weighting + roulette with a new decision system** — a cave implementing the
objective function (situation + tendencies + own personnel/matchup → best
response, with small error). This **supersedes the earlier "inject bias into the
existing weighting" framing** (Layer 2 below): we bypass the broken weighting, not
tune it.

**Reuse vs replace:**
- **REUSE:** the candidate **enumerator** (builds the legal play pool from
  `PBAI.AIGR`) — it just lists legal plays; the 41-slot game-state array; `ptrk`
  (extended) as the tendency store; the `+0xB70` ratings block for own personnel.
- **REPLACE:** the class renormalization, the yards-scaled matchup term, the
  roulette, and the 9 rating-cheese consumers. The new evaluator reads the
  enumerated pool + all inputs and **selects**.
- **SEAM TO FIND (investigation):** the exact hand-off where the enumerated pool
  goes into weighting — that is where the new selector cuts in and the old
  weighting/roulette is bypassed.

Cleaner than patching: a fresh evaluator can't inherit the steep favourite or the
cheat, and it is independently testable — feed it a situation + a tendency profile
+ a roster, check its pick. Layers 1–5 become the INPUTS and CONSUMERS around this
new brain; Layer 2's "smarter calling" IS this new system.

## Layer 0 — resize the ring (48 → 200/side) + robust persistence (operator, 2026-08-13)

> Expand it from 48 plays / both sides to 200. Make sure it's always safely
> emptied or whatever as necessary; ideally it stays as full as can be, even if we
> persist it on the save file.

**Current structure** (`play-tendency-ai.md`): ctor `0x0024D890`, **1556 bytes**
= 16-byte header + **two 48×16 record rings** (one per side) + counts. Already
franchise-persisted (`GBIN`/`STPG`, `0x0024E458`).

**Target:** 200 records/side → 2×(200×16) = 6400 B + header ≈ **6416 B**.

**Sites this touches (all must move together):**
- **Allocation size** in the ctor (1556 → ~6416).
- **The 48 cap** — the AddPlay count cap (`movz` at `0x0024DA20` / `0x0024DAD8`).
- **Stride / index math** wherever 48 or the per-side ring size is baked in.
- **The recency-weight table `0x00540FE0`** — it's 48 entries in 4 bands
  (1/24, 1/48, 1/96, 1/192). At 200 it must extend (bigger table) or be
  **reformulated as a decay curve** — cleaner, and lets the top band still drive
  the fast-cheese reaction (Layer 2).
- **Save/load format** (`0x0024E458`) — the persisted blob grows; **existing
  franchise saves need versioning/migration** or they break.

**Safety ("safely emptied"):** bounds on every ring op, and the clear/`memset`
sized to the new buffer. "Empty only when necessary" = wipe on a genuinely new
franchise, **persist otherwise** (operator: stay as full as possible) — the
save-file persistence already exists to build on.

**Why this matters — cross-game scouting (operator):** the persisted, expanded
profile is what lets *a team later in the season scout you* — a Week 15 opponent
loads your accumulated tendencies and game-plans against them, exactly like a
real coordinator studying film. The foundation already exists: `ptrk`'s
franchise save/load (`0x0024E458`) means "your play-calling reputation follows
you between games" (`play-tendency-ai.md`). The resize + robust persistence turns
that from a shallow last-48 memory into a season-long scouting report. **Design
question to settle:** is the persisted profile keyed to the human/team so ANY
later CPU opponent reads it (a true scouting report), or is it per-matchup?
Determines the save schema.

**Honest risk flag:** this is the **most invasive piece of the whole feature** —
resizing a *persisted* struct touches allocation, caps, the weight model, AND the
save format at once. **Phase-able:** build and prove the tendency/calling logic at
the stock 48 first (cheaper, no save-format risk), then do the resize as its own
gated change with save migration. Don't front-load the riskiest part.

## The feature, in four layers

1. **Track more (expand the ring schema).** Encode into the free record fields,
   per play: **run/pass**, **inside/outside**, **target player** (which RB/WR the
   pass/carry went to), and outcome **1st down / TD** (extend the success factor
   beyond "> 2 yд"). "Fairly simple" = a few recency-weighted counters over the
   ring, exactly the machinery that already computes `f`.
2. **Call smarter (anti-cheese) — THIS IS THE NEW SELECTOR** (see Architectural
   decision above). The coach-brain cave reads the enumerated legal pool +
   tendencies + situation + own roster and **selects**, *replacing* the class-
   renorm weighting + roulette (not tuning them). Counter the human: run-heavy →
   prefer run defenses; outside-run tendency → contain/force; feeding WR#1 → roll
   coverage to him. The old weighting/roulette is bypassed.
3. **Drop the cheese.** Neuter the 9 CPU-advantage rating consumers so adaptation
   comes from *calling*, not fake ratings. **ORDERING: this is LAST** — remove the
   crutch only once smarter calling exists, or the CPU gets dumber (operator's
   "anti-cheese first").
4. **Expand the pool.** Widen the authored play pool the AI can call from
   (`ai-play-calling.md` F5: a playbook-DB edit) **with the landmine** — the
   row-fetch loop has NO bound check; >225 rows in one group overwrites the stack.
   Raise the buffer first.

## Immediate reads — formation recognition & fast cheese detection (operator, 2026-08-13)

> The AI needs to immediately identify things — e.g. first play of a down, EMPTY
> formation, trying 5 verts → that's an "empty check". And it needs to immediately
> figure out if someone is a cheese player who only does the same things.

Two capabilities that are about **reacting fast**, not just accumulating a
season's worth of data:

- **Pre-snap formation recognition ("checks").** Distinct from the rolling
  tendency (Layers 1–2) — this reads the offense's **formation/personnel THIS
  snap** and checks the defense accordingly: empty backfield → expect
  pass/verts → check to a pass-safe shell; heavy/goal-line personnel → expect
  run. A real defense's pre-snap "check to the empty look". Needs: where the
  offensive formation/personnel is readable pre-snap (an investigation item;
  the engine clearly knows it — it aligns the players).
- **Fast cheese detection.** A one-trick human must be countered within a **few
  snaps**, not after 48. Good news: `ptrk`'s recency table already weights the
  **last 12 plays** heaviest (1/24 each vs 1/192 for plays 37–48), so the
  machinery to react fast exists — the anti-cheese calling (Layer 2) should lean
  on the top-recency band so a spammer is flagged and countered almost
  immediately. "Immediately figure out a cheese player" = threshold the
  short-window repetition, not the full ring.

These fold into Layer 2 (smarter calling): the CPU's per-snap decision reads
(a) this-snap formation, (b) short-window tendency, (c) the multi-variable
situation — and adjusts. The 41-slot game-state array + `ptrk` supply (b) and (c)
today; (a) formation-personnel is the one read to locate.

## Self-scheme — the CPU builds off its own success & keeps a coach's "look" (operator, 2026-08-13)

> The CPU needs to track its OWN plays so it can build off the successful ones —
> maintain the same "look" more or less, like a real coach — and try something
> else if what they try doesn't work.

Today the selection is **blind to its own history**: `ai-play-calling.md` found
every `ptrk` read in the offense weighting is for the **opponent**; "no term
reduces a play's weight because the AI just called it." The CPU neither builds off
its own success nor abandons its own failures.

**But the infrastructure already exists: the ring has TWO sides.** The CPU's own
calls + outcomes are already recorded on its side (and Layer 1's outcome
expansion — success / 1st down / TD — applies to it). The new selector just needs
to read its OWN side, **symmetric to the anti-cheese read of the opponent's
side**:
- **Build off success:** up-weight the CPU's own recently-successful plays/concepts.
- **Abandon failure:** down-weight its own recently-failed plays — "try something else".
- **Keep a look (coach identity):** a scheme-inertia term — lean toward the CPU's
  established concepts rather than calling randomly, so its offense reads as one
  coherent philosophy that builds on what's working.

So the **coach brain reads BOTH ring sides**: the opponent's side to counter
tendencies (anti-cheese), its own side to build a consistent, self-improving game
plan. Same ring, both directions — the self-referential half the current engine
entirely lacks. Note: this uses the exact infrastructure the ptrk investigation is
already mapping (both-side ring + outcome signals), so it changes the DESIGN, not
the investigation targets.

## Requirements + acceptance tests

- **T-track:** after the human runs N times / feeds one receiver M times, the new
  ring counters reflect run/pass, inside/outside, and per-target concentration
  (read the ring live). 1st-down and TD counters increment on those outcomes.
- **T-call:** a human who spams one tendency (e.g. outside runs, or targeting
  WR#1) sees a **measurable shift** in CPU play/coverage selection over the game
  toward the counter — quantified from CPU call logs, not vibes.
- **T-self:** the CPU up-weights its own recently-successful concepts (builds off
  them, keeps a "look") and down-weights its own recently-failed ones (tries
  something else) — measured as a shift in CPU self-call distribution as a
  function of its own play outcomes, holding the opponent constant.
- **T-check:** presenting an empty formation makes the CPU check to a pass-safe
  shell (and a heavy/goal-line look makes it check run-strong) THIS snap —
  measured as a change in the CPU's pre-snap call/shift vs a neutral formation.
- **T-fast:** a human repeating one play/tendency is countered within a **few
  snaps** (short-recency band), not only after a full ring — measure the CPU
  counter-shift as a function of repeat count and confirm it moves by ~3–5 reps.
- **T-decheese:** repeating a play no longer inflates CPU ratings — the 9
  consumers read 0 boost (the `AWR+AWR·f` and contest terms are neutralised).
- **T-regress:** human-vs-human (online) is unaffected — `ptrk` never fires with
  no CPU side, so the revival server stays clean. Single-player difficulty still
  feels fair (the point: adaptation via calling, not cheating).

## Constraints & unknowns (gate the design)

- **Situational coaching is a DISC SCRIPT, not ELF** (`ai-play-calling.md` F6):
  clock/down-distance policy runs in a bytecode VM loaded from disc. "Smart as a
  coach" for *situational* calls (2-min drill, 4th-and-short) is partly OUTSIDE
  the ELF — the ELF supplies the state (41-slot array: score, clock, down,
  distance, timeouts…) but the policy is authored data. Scope the ELF part;
  the situational script is a separate, harder surface.
- **DOC CONFLICT on the play-caller (verify — rule 4):** `play-tendency-ai.md`
  says `0x001459B4+` reads history to choose plays; `ai-play-calling.md` corrects
  that `0x001459B4` is the pre-snap **defensive line/LB shift** picker, and the
  real caller is the enumerator chain. Resolve which function actually selects
  the CPU play before hooking the weighting.
- **Ring free fields unconfirmed:** the two undecoded u32s + flags must be
  verified free to repurpose (no other reader) before encoding new dimensions.
- **The run/pass, direction, and target signals** must be readable at the
  per-play recompute (`0x0024D9C0`) — where does play type / ball destination
  live at play-end? (Fatigue work found a "turned into a run" bit; targets need
  finding.)
- **Pool-expansion landmine:** the 225-row candidate buffer has no bound check.

## Layer 5 — Play-action BITE (the defense reacting like a coach) — operator, 2026-08-13

> It also needs to know about play-action threats — the defense must
> appropriately BITE on play-action based on a multi-variable model: game
> situation (goal line, 4th-and-1), formation, score, time in the game, and how
> well they're running the ball — all kinds of things.

This is the DEFENSIVE-reaction half of "smart as a coach", and it draws on the
SAME expanded tendency model (Layers 1–2). "How well they're running the ball" is
exactly the run-specific **success factor** Layer 1 adds to `ptrk`. Bite harder
when the run has been working; stay disciplined when it hasn't.

**The multi-variable bite model — and the good news: the inputs already exist.**
`ai-play-calling.md` established a **complete 41-slot game-situation array**,
populated every play in ELF-readable memory: **score differential, quarter, time
left, down, distance, LOS (→ goal-line/field position), timeouts, current play
ids, who is CPU-controlled.** So a bite model keyed on:
- **run success** (ptrk, run-specific) — the dominant term,
- **down & distance** (4th-and-1 / short yardage → expect run → bite),
- **field position** (goal line → expect run → bite),
- **score & time** (protect-a-lead late vs trailing),
- **formation** (heavy/goal-line personnel → expect run),
- **run/pass tendency** (ptrk),
…reads inputs that are **all already available** (except formation-personnel,
which needs locating). Only the POLICY (how to weight them) is new.

**Mechanism to find (investigation):** where a defender's run/pass *diagnosis* /
play-fake reaction is computed — the moment the fake exploits — and make its
bite/discipline value a function of the model above. This is the DEFENSIVE
counterpart to the earlier PA **pass-protection** thread
(`block-dominance`/`double-team-requirements.md` PA notes) — offense sells the
fake, defense decides whether to buy it. Both are "PA awareness"; keep them as
separate patch surfaces (rule 1) but design them as one coherent feature.

**Acceptance (T-bite):** on a fixed PA play, defender bite (LB/S step toward the
LOS on the fake) increases with run success + short-yardage/goal-line situations,
and decreases when the run hasn't worked — measurable from defender first-step
displacement, holding the play constant and varying only the situation/tendency.

## Blast radius

Play-calling AI + `ptrk` + the rating-cheat consumers. Does NOT touch blocking or
fatigue. Online (h-v-h) is inert by construction. The de-cheese step is the only
one that changes single-player *difficulty feel* — gate and measure it.

## Investigation results

### ptrk internals — DONE, spot-checked 2026-08-13 (full: `ai-coach-investigation-ptrk.md`)

Three findings that materially shrink the work:

1. **De-cheese is surgical — TWO getters.** `0x0024E188` (repetition) has exactly
   the **9 documented cheat consumers** as callers (verified: coverage ×5,
   break-block, tackle, ball-contest, event-rate); `0x0024E1C0` (success) feeds
   the defensive-AI branches. **Neuter the two getters → all rating cheese zeroes,
   recording + selection untouched.** Still do it LAST per ordering, but it's a
   2-site change, not a hunt.
2. **Layer 1 is MOSTLY ALREADY RECORDED.** The recorder `0x00148900` (not the
   recompute) already writes per play: `@4` opponent play id, **`@8` direction/zone
   bitmask (inside/outside, L/R, short/deep)**, **`@13` yards**, **`@14` 5-way
   outcome class**, **`@15` run/pass** (via the `0x001F82E8` gate — verified: it
   stores 1 or 2). Only **pass-target player is missing** → one new field. The
   existing fields' only readers are the two LEGACY selection inputs we're
   replacing, so they free up for the new selector automatically.
3. **Resize (48→200) is mechanical + one hazard.** ~30 baked immediates (alloc
   1556 ×3, stride 768 ×~13, count-offset 1552 ×5, caps 47/48/49). **Hazard:** the
   two 4-entry, UNCLAMPED weight tables (`0x00540FE0`/`0x00540FF0`) — `i/12` hits
   band 16 at 200 records and reads 13 words OOB. Must extend to ≥17 bands or
   **reformulate as a decay curve** (which we want anyway for the fast-cheese top
   band). Persistence: saved as one opaque `'STPG'` GBIN section; resize relocates
   side-1 ring + counts → **save versioning/migration required**. Phase per Layer 0.

Struct is self-contained (all refs in one 6 KB module `0x0024CAFC–0x0024E45C`) —
nothing external dereferences it, which makes the resize safer.

### Situational + PA-bite inputs — DONE, spot-checked 2026-08-13 (full: `ai-coach-investigation-inputs.md`)

1. **Situation state — CONFIRMED, but it's an OBJECT, not a flat array
   (correction).** Single global at `*0x00601F4C`, ~40-accessor library
   (`0x0025FF00`–`0x00260E30`). Supersedes the "41-slot array" framing used
   above/in `ai-play-calling.md`. Proven fields: quarter `+0x00`, possession
   `+0x40`, per-team score `+0x44/+0x46`, clock `+0x38`, flags `+0x3C`. Reachable
   from the coach (possession drives weighting dispatcher `0x0024DBF8`; score/clock
   getters called from the `0x00148/0x00149` play-calling module). **GAP: down,
   distance, timeouts, current-play-ids are in unlabeled slots — need ONE live
   read to bind** (dump 0x120 B at `*0x00601F4C` mid-play, compare to HUD). These
   are load-bearing for the situational model, so this read is a prerequisite.
2. **Formation recognition — DIY from position bytes.** No pre-built formation id
   and no RB/WR count field in the ELF (closed census). Signal = player position
   byte **`+0xB04`** (enum QB0/HB1/FB2/WR3/TE4…), pre-snap. Empty check = iterate
   the offense's 11 and count `+0xB04∈{1,2}` (RB/FB) vs `==3` (WR). Tractable, just
   a small loop we write.
3. **PA-bite — BIG REFRAME: the CPU defense is OMNISCIENT about run/pass.**
   `IsRun 0x001F82E8` reads the **authored** play-type (possession → play object
   `0x00243F58`, verified), not a visual signal. Defenders gate on it
   (man-cover think `0x001BE2D0`: `IsRun` true → abandon coverage to state 85).
   **So a play-action pass returns IsRun=0 and the fake fools the CPU NOT AT ALL
   today** — explaining why PA feels useless vs the CPU. Layer 5 is therefore not
   "tune a bite value" — it's **introduce foolability**: replace the omniscient
   `IsRun` gate at the defensive-read sites with a probabilistic diagnosis driven
   by the multi-variable model (run success, down/distance, field pos, score/time,
   formation). Note `IsRun` is the SAME primitive the ptrk recorder uses for `@15`
   — a shared choke point.
4. **Own-personnel/matchup — inputs reachable, scoring absent.** `+0xB70` ratings
   ARE read from the play-calling band (AWR at 76 sites); coverage reads defender
   position `+0xB04` and AWR `+0xB74`; assignment is in the state-chain record
   `*(player+0x2FC)`. **Negative finding:** nothing compares the covered WR's
   rating vs the covering defender's — CB-vs-WR matchup scoring is a **build
   target on already-reachable inputs** (supports "forced to a subpar package").

### Play-caller & the SEAM — DONE, spot-checked 2026-08-13 (full: `ai-coach-investigation-playcaller.md`)

1. **THE SEAM — `0x00249498` ("AI select play from group"), verified.** Indexes
   the per-team playbook block (stride `0xAFBC`, base `[0x00609770]`) and runs the
   selection query. **Exactly two callers** (verified): `0x0024BCAC` and
   `0x0024BE58`, both VM command handlers. Live regs at the call: `a0`=side(0/1),
   `a1`=flag, `a2`=group id, `s2`=VM context. **The hook: retarget those two
   `jal 0x00249498` to the coach-brain cave** — one clean cut-in point for the
   entire new selector.
2. **Play-caller conflict RESOLVED.** `0x001459B4` is inside `0x00145940` = the
   pre-snap **defensive line/LB shift** picker (confirms `ai-play-calling.md`,
   corrects `play-tendency-ai.md`). It reads `ptrk` to bias *alignment*, not to
   pick a play. Closed: no offensive play-caller among any `ptrk`-getter caller.
3. **The selection spine:** VM cmd11 (`0x0024BC8C`) → seam `0x00249498` →
   `0x002BFF68` (assembles+runs `select LPBP from IABP where RGIA=group`) → query
   engine `0x004C7E38`. Columns: `LPBP` play id, `RGIA` group, `tcrp` weight,
   `TSBP` tendency key. The **weighting (tcrp + the 0.8 class-renorm + weighted-
   random) runs on the QUERY path, not in ELF AI code** — so taking over at the
   seam *replaces* it wholesale (we decide, we don't run the query's weighting).
   The 225-slot no-bound-check buffer is that query's result set.
4. **VM / disc-script boundary:** interpreter `0x0024BFC0`, command handler
   `0x0024BB50` (13-entry table): cmd11→seam (select-from-group), cmd8/9→
   `0x0024B100` (set-specific-play), others clock/huddle. **Script = disc asset
   #69** (loader `0x0047F480`, exec `0x0024C7C8`). Situational policy (clock,
   4th-down, which group) is authored disc data; **the ELF-patchable boundary is
   the command handler** — so we can override *which play* at the seam, and the
   4th-down/clock *policy* is the separate disc-asset surface.

**One live read closes the remainder:** break in `0x002BFF68` to watch the
weighting/renorm/pick execute + confirm the result-set row width and the 225 bound.

## Build target & portability (operator, 2026-08-13)

> The goal is always to be able to build the modified ISO for online play
> eventually, and hopefully port it to the Xbox version for a friend.

Two consequences that settle the space/approach question:

1. **A modified ISO is the deliverable ANYWAY (Track 1 online play).** So the
   ELF-expansion path is not a detour or a burden — it IS the destination. The
   `~9.2 KB` cave budget stops being a ceiling: code caves become a **dev-time
   convenience** for fast emulator iteration (pnach, no rebuild), while the real
   coach-brain ships in a **relinked, properly-sized ELF** in the rebuilt ISO.
   Build in caves to iterate; land in the expanded ELF to ship.
2. **The Xbox version is a DIFFERENT ARCHITECTURE (x86), not just different
   addresses.** Our PS2 MIPS work — every address, every cave, every disassembly
   — does NOT transfer. Only the **design** transfers: the objective function, the
   tendency data schema (the `ptrk` fields), the coach-brain algorithm, the
   de-cheese approach, the hook *semantics*. **Consequence for how we build:**
   keep the coach-brain a **clean, self-contained module** with a
   **platform-agnostic design spec** (algorithm + data model + "what each hook
   does" in words, not just MIPS addresses), so a future Xbox effort re-derives
   the equivalent structures on the x86 binary instead of starting from raw
   gameplay observations. A coherent module ports; scattered cave hacks do not.
   This reinforces the "new system, not a patch" decision.

## Status

New, scoped. Foundation (`ptrk`) mapped AND its internals investigated (above).
Play-caller/seam and situational/PA-bite investigations still running. Next: a focused investigation to
(a) resolve the play-caller conflict + find the weighting hook, (b) confirm the
ring's free fields, (c) locate the run/pass + direction + target signals at the
recompute, (d) list the exact de-cheese sites (mostly already in
`play-tendency-ai.md`). No patch until those land. Recommend starting with the
tracking + calling layers (build the smarts) and doing de-cheese LAST.

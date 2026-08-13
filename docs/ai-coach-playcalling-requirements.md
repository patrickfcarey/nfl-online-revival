# AI coach — a competent, fallible NFL head coach

Campaign opened 2026-08-13. Replace the CPU's play-calling and its hidden
rating-cheat with a genuine coaching brain: reads the human, reads both rosters,
reads the situation, and calls like a real coordinator — mostly right, sometimes
wrong, never psychic. Separate code path from blocking/fatigue. Scope + verified
hooks; no patch until the remaining live-reads land (rules 1, 3, 4).

The design below was architected by the operator across one session; the three
engineering investigations (§4) are done and spot-checked against the binary.

---

## 1. The objective

**One decision drives everything** — defensive alignment, play call, PA-bite,
formation check are all the same act:

> **best response = f( game situation, opponent tendencies, opponent roster, own roster )**

"Read the situation + the scouting report + both rosters, pick the best answer I
can actually execute."

- **Quality bar:** a competent NFL head coach. Not psychic, not dumb; situationally
  aware, aggressive when the math says so (goes for it on 4th-and-1).
- **Deliberate error (a REQUIREMENT):** mostly right, occasionally wrong — a small,
  tunable error term (one low-value knob). A perfect defense is unbeatable and
  unfun. This replaces the engine's cheat-noise with honest decision-noise.
- **Difficulty scales the COACHING, not the ratings** (the honest replacement for
  the cheat): higher difficulty reads tendencies faster, has a smaller error term,
  counters harder. Pro = a green coordinator; All-Madden = Belichick.

---

## 2. Coaching philosophy (the design principles)

### 2.1 Roster-driven identity — build around your stars
At game load, analyze the own roster's ratings and let the best players drive the
plan. A team with Randy Moss features him; a team with Emmitt Smith uses him.
Two moves: **(1) feature your best players; (2) scheme around your weaknesses to
keep featuring them** — Emmitt behind a bad OL gets tosses/screens/quick-hitters,
not slow dives. Use the star is non-negotiable; *how* adapts.

**Offensive archetypes** — the roster profile picks a style + a down/distance
philosophy:

| roster profile | archetype | behavior |
|---|---|---|
| good OL + good RB (+ beatable run D) | **ball-control / run-first** (the DEFAULT when ratings align) | run 1st & 2nd down, manufacture easy 3rd-and-short; ideally 1st downs on 2nd down |
| bad OL | **shotgun air-raid / spread** | can't run or protect → shotgun, quick game |
| elite WR | vertical / featured-receiver | deep shots + coverage-beaters to him |

The ball-control default is conditional: only when personnel aligns AND the run D
is beatable (expert run-stuffers negate it — matchup-aware), and the coach
abandons it if the run stalls (§2.3 self-scheme). Every archetype must still pass
competently. (Archetypes need the PLAYS to exist — ties to §6 pool expansion.)

### 2.2 Opponent roster analysis — attack weakness, avoid strength
The SAME roster-analysis pass, pointed at the opponent (nearly free). Offense:
attack the weak CB (isolate a WR on him), run at a weak run D, TE/RB on a weak
coverage LB; AVOID the great CB (throw away from Deion). Defense: bracket the
elite WR, spy the mobile QB. Apply on a CLEAR mismatch, not forced.

### 2.3 Read both sides of the ring — counter the human, build off yourself
`ptrk` (§4.1) has two ring sides. The coach reads **both**:
- **Opponent's side → anti-cheese.** Counter their tendencies (run-heavy → run
  defenses; outside-run → contain; feeding WR#1 → roll coverage to him).
- **Own side → self-scheme.** Build off the CPU's own recently-successful concepts
  (keep a coherent "look"/identity), abandon its own failures ("try something
  else"). The engine today reads only the opponent side — this self-referential
  half is entirely new.

### 2.4 Immediate reads — formation & fast cheese
- **Pre-snap formation check:** read the offense's formation THIS snap (empty
  backfield → pass-safe shell; heavy → run-strong). An "empty check."
- **Fast cheese detection:** a one-trick human is countered in a **few snaps**, not
  a whole game — lean on the short-recency band (the ring already weights the last
  ~12 plays heaviest).

### 2.5 Memory scoping — this game vs prior games
The resize (§6) makes the ring span 3+ games, so current- and prior-game plays
MIX. Don't purge — **separate + weight**: a LIVE signal (this game → immediate
adaptation) and a PRIOR signal (previous games → the scouting report at kickoff).
Prior leads at kickoff, the live read takes over as the game develops (Bayesian in
spirit). Mechanism: a **game/session id per record** (never attribute a prior play
to the live read) + a **cross-game decay** (recent games weigh more). This is the
same decay-curve the resize needs anyway. Enables the **cross-game scouting
report** — a Week-15 team studies your season.

### 2.6 The defense reads VISIBLE cues, never HIDDEN state (the anti-omniscience axiom)
The single sharpest test for the defensive AI. Today it cheats by knowing things it
shouldn't:
- **PA omniscience (§4.3):** it reads the *authored* play type, so play-action
  fools it not at all. FIX: introduce foolability — a probabilistic run/pass
  *diagnosis* driven by the multi-variable **PA-bite** model (run success,
  down/distance, field position, score/time, formation). Bite harder when the run
  has been working; stay disciplined when it hasn't.
- **Audible-mirroring:** it counter-audibles EVERY time the human audibles (it
  "knows" you audibled). FIX: don't deterministically switch — sometimes stay;
  adjust only to VISIBLE motion/formation, not the audible event. (Reacting to
  *motion* is legitimate; reacting to the *call change* is not.)
- **Disguise & late safety-rotation:** show one look, rotate to another late —
  gated on secondary quality (great DBs disguise; a weak secondary plays honest).
  "Hide your looks behind your best players." (Highest-uncertainty piece — depends
  on whether a coverage-rotation primitive exists; see §7.)

Offense builds identity *around* the stars; defense hides looks *behind* them —
"let your best players define you."

---

## 3. Architecture — a NEW module, not a patch

The existing selection is not worth patching — structural problems (`ai-play-calling.md`):
the class renormalization (steep favourite — one play can own 80% of the
roulette), the yards-scaled matchup memory (~16×), and the rating cheese. **DECISION:
REPLACE it with a new self-contained coach-brain module** implementing §1's
objective function.

- **REUSE:** the candidate enumerator (the legal play pool), the situation object,
  `ptrk` (extended) as the tendency store, the `+0xB70` ratings for both rosters.
- **REPLACE:** the class-renorm weighting, the matchup term, the roulette, the 9
  rating-cheese consumers.
- **Portable by construction:** the module is documented as a platform-agnostic
  spec (algorithm + data schema + hook semantics), because the eventual Xbox port
  is a different architecture (x86) where only the *design* transfers, not the MIPS
  addresses.
- **Ships in an expanded ELF / rebuilt ISO** — which is the Track-1 online-play
  deliverable ANYWAY, so it's the destination, not a detour. Code caves (~9.2 KB)
  are the dev-time iteration convenience; the module lands in relinked ELF space.

---

## 4. Engineering foundation (investigations DONE, spot-checked 2026-08-13)

Full detail: `ai-coach-investigation-{ptrk,inputs,playcaller}.md`.

### 4.1 `ptrk` — the tendency store, and the de-cheese choke point
- **Structure:** ctor `0x0024D890`, 1556 B = 16-B header + two 48×16 rings (per
  side) + counts. Self-contained (all refs in `0x0024CAFC–0x0024E45C`).
  Franchise-persisted as one `'STPG'` GBIN section (`0x0024E458`).
- **Record already holds most of Layer-1's data** (recorder `0x00148900`): `@4`
  opponent play id, `@8` direction/zone (inside/outside, L/R, short/deep), `@13`
  yards, `@14` 5-way outcome, `@15` run/pass (via `IsRun 0x001F82E8`, verified).
  **Only pass-target player is missing → one new field** (+ a game/session id for
  §2.5). Existing fields' only readers are the legacy selection we're replacing.
- **De-cheese = TWO getters (verified).** `0x0024E188` (repetition) has exactly the
  9 cheat consumers as callers (coverage ×5, break-block, tackle, ball-contest,
  event-rate); `0x0024E1C0` (success) feeds the defensive branches. Neuter both →
  all cheese zeroes, recording + selection untouched.
- **Recency model:** weight tables `0x00540FE0`/`0x00540FF0` (4 bands, unclamped).

### 4.2 The SEAM — one clean cut-in for the whole selector (verified)
- **`0x00249498`** ("AI select play from group") — indexes the per-team playbook
  (stride `0xAFBC`, base `[0x00609770]`), runs the selection query. **Exactly two
  callers:** `0x0024BCAC`, `0x0024BE58` (VM handlers). Regs: `a0`=side, `a1`=flag,
  `a2`=group id, `s2`=VM context. **HOOK: retarget those two `jal`s → the
  coach-brain.**
- **Spine:** VM cmd11 `0x0024BC8C` → seam → `0x002BFF68` (`select LPBP from IABP
  where RGIA=group`) → query engine `0x004C7E38`. The weighting/renorm/roulette
  runs on the QUERY path — taking the seam replaces it wholesale.
- **Play-caller conflict RESOLVED:** `0x001459B4` (in `0x00145940`) is the pre-snap
  **defensive shift** picker, not the play-caller.
- **VM / disc boundary:** command handler `0x0024BB50`; cmd8/9 = set-specific-play
  `0x0024B100`; **script = disc asset #69** (`0x0047F480` / `0x0024C7C8`). The
  situational POLICY (4th-down, clock, group choice) is authored disc data — a
  separate surface; the ELF boundary is the command handler.

### 4.3 Situational + defensive inputs
- **Situation = an OBJECT at `*0x00601F4C`** (~40 accessors `0x0025FF00–0x00260E30`),
  NOT a flat array. Known: quarter `+0x00`, possession `+0x40`, score `+0x44/+0x46`,
  clock `+0x38`, flags `+0x3C`. Reachable from the coach (`0x0024DBF8`; getters in
  the `0x00148/9` module). **GAP: down/distance/timeouts unbound — one live read.**
- **Formation = position byte `+0xB04`** (QB0/HB1/FB2/WR3/TE4). No formation-id;
  empty check = count `+0xB04∈{1,2}` vs `==3` over the 11.
- **PA omniscience:** `IsRun 0x001F82E8` reads the authored play type
  (`0x00243F58`); defenders gate on it (`0x001BE2D0`). The fake fools the CPU not
  at all today — §2.6.
- **Ratings reachable both rosters:** `+0xB70` block (STR `+0xB8E`, STA `+0xB8C`,
  AWR `+0xB74`), position `+0xB04`, assignment `*(player+0x2FC)`. CB-vs-WR matchup
  scoring is absent — a build target on reachable inputs. Roster-analysis home: the
  DB load module `0x002C0000–0x002C5000`.

---

## 5. Build plan (ordered — cheapest/safest first, riskiest last)

**Phase 0 — Live-read session (rig).** Close the last gaps: bind down/distance/
timeouts in `*0x00601F4C` (dump 0x120 B, compare HUD); confirm the query-path
weighting + the 225-buffer row width (break in `0x002BFF68`); confirm the run/pass
gate `@15` and the `+0x2FC` covered-receiver byte; scope whether a coverage-rotation
primitive exists (gates §2.6 disguise). Cheap; gates the design.

**Phase 1 — The coach-brain module + cut in at the seam.** Build the objective
function (§1) reading the located inputs; retarget the two callers of `0x00249498`.
Includes the roster-analysis precompute (§2.1/2.2), both-side tendency reads
(§2.3), formation/fast-cheese (§2.4), and the error term. Prove at stock ring size.

**Phase 2 — Extend `ptrk` schema.** Add the pass-target field + the game/session id
(§2.5); read the already-recorded dimensions (`@8/@13/@14/@15`).

**Phase 3 — Defensive visible-cue reads (§2.6).** Replace the `IsRun` omniscience
with the PA-bite diagnosis; gate the audible-mirror; disguise + late rotation IF
Phase 0 finds a rotation primitive.

**Phase 4 — Drop the cheese (LAST).** Neuter the two getters (§4.1) — only after
the calling is smart, or the CPU gets dumber.

**Phase 5 — Resize + persistence.** 48 → 200 (or a full season ~1200/side; cost is
~32 B/record, trivial — memory is not the constraint, the save migration is, so
size once). ~30 mechanical constants + reformulate the weight tables as a decay
curve (also serves §2.5) + save-format versioning/migration. Most invasive; phased
alone.

**Ongoing — Expand the play pool (§6).** Widen the authored pool so the archetypes
have plays; raise the 225-row buffer first (no bound check).

---

## 6. Acceptance tests

| test | passes when |
|---|---|
| T-track | after N runs / M targets, the ring counters reflect run/pass, in/out, per-target; 1st-down & TD counters increment |
| T-call | a human spamming one tendency sees a measurable CPU counter-shift (from call logs) |
| T-self | the CPU up-weights its own successful concepts, down-weights failures (self-call distribution shifts with its outcomes, opponent held constant) |
| T-check | an empty formation → CPU checks pass-safe; heavy → run-strong, THIS snap vs neutral |
| T-fast | a repeated tendency is countered within ~3–5 reps, not a full ring |
| T-roster | elite RB → run-featuring plan; elite WR → that receiver primary; worse OL → same star, quicker-developing usage |
| T-archetype | good OL+RB runs first on early downs vs a beatable run D, shifts to pass when it stalls / run D is elite; bad OL plays shotgun |
| T-opponent | vs one weak CB the CPU targets that matchup more; vs an elite CB it throws away; vs a weak run D its run tendency rises |
| T-memory | at kickoff reflects prior-game tendencies; by mid-game tracks THIS game even when it differs; stale prior data isn't mis-countering past ~Q1 |
| T-bite | on a fixed PA play, defender bite rises with run success + short-yardage/goal-line, falls when the run hasn't worked |
| T-audible | an offensive audible does NOT deterministically flip the defense; it adjusts only to visible motion/formation |
| T-disguise | the pre-snap shell doesn't reliably predict the post-snap coverage; disguise frequency scales with secondary quality |
| T-decheese | repeating a play no longer inflates CPU ratings (the 9 consumers read 0 boost) |
| T-regress | human-vs-human (online) unaffected — `ptrk` never fires with no CPU side; single-player still feels fair |

---

## 7. Open questions (to resolve before/within the build)

- **Down/distance/timeouts binding** — the Phase-0 live read.
- **Coverage-rotation primitive** — does it exist? Sets the ceiling on §2.6
  disguise / late-switch (the highest-uncertainty piece). Phase-0 scope.
- **Save-key design** — is the persisted profile per-human (a true cross-team
  scouting report) or per-matchup? Determines the save schema (§2.5).
- **Situational policy surface** — the 4th-down/clock rules live in disc asset #69,
  not the ELF; game-management "go for it on 4th-and-1" partly lives there.
- **225-buffer row width** — Phase-0 live read (gates the pool expansion, §5).

---

## 8. Constraints & blast radius

- **Blast radius:** play-calling AI + `ptrk` + the rating-cheat consumers +
  (§2.6) defensive run-diagnosis/coverage. Does NOT touch blocking or fatigue.
- **Online (h-v-h) is inert by construction** — `ptrk` never fires with no CPU
  side, so the revival server stays clean.
- **De-cheese is the only step that changes single-player difficulty feel** — gate
  and measure it; it comes last.
- **Situational policy is partly disc-authored, not ELF** — a separate surface.

## 9. Status

Design consolidated (this doc). Three engineering investigations DONE and
spot-checked (§4). Remaining before build: the **Phase-0 live-read session**. The
ordered build plan is §5; nothing is patched until Phase 0 lands.

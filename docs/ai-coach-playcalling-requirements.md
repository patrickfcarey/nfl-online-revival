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
  *Placement note:* prefer erring at the **read** (slightly misjudge the tendency)
  over the **pick** (choosing a known-worse play) — a coach errs by misreading, not
  by deliberate bad calls. Both knobs exist; tune in Phase 1e.
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
competently. (Archetypes need the PLAYS to exist — ties to the §5 pool expansion.)

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
- **Pre-snap formation/PERSONNEL check.** Read the offense's personnel THIS snap
  (count WR/RB/TE via `+0xB04`) and check the defense accordingly — both PERSONNEL
  and COVERAGE:
  - empty backfield → pass-safe shell ("empty check"); heavy/goal-line → run-strong.
  - **4 WR → recognize spread → (a) SUB to nickel/dime, (b) CHECK to a pass shell:
    Cover 4 vs deep/vertical threats, Cover 2 man if the corners can travel.** The
    coverage CHOICE within the shell is a coach-brain call (their tendency + my
    corners §2.1 + their WRs §2.2), not a fixed reflex.
  - Free input (WR count via `+0xB04`); the two build gaps are the OUTPUTS —
    driving a defensive personnel SUB (nickel/dime) and a coverage CHECK from the
    count. Whether the engine exposes pre-snap D-personnel substitution is a
    formation-check investigation item.
  - **These reads need NO history — they work on the FIRST play of the game**
    (read the field, not the ring), unlike the accumulated tendency reads. The CPU
    is a coverage threat from the opening snap, cold, with an empty ring (quickplay
    or a new franchise). Tendency reads accumulate; formation reads are instant.
  - **Mechanism (Hypothesis):** the check likely rides the engine's EXISTING
    defensive-audible path — the same machinery behind today's reflexive
    counter-audible (§2.6), whose existence the operator's observation proves.
    Repurposed: triggered by the formation/personnel READ instead of the audible
    EVENT. One hunt covers both (§7 B1).
- **Fast cheese detection:** a one-trick human is countered in a **few snaps**, not
  a whole game — lean on the short-recency band (the ring already weights the last
  ~12 plays heaviest).

### 2.5 Memory scoping — this game vs prior games
The resize (§5 Phase 5) makes the ring span 3+ games, so current- and prior-game plays
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

### 2.7 Defensive structure — the coverage call sets the run/pass balance
The coach's coverage choice has structural consequences the defense must execute
correctly — it's the run-strong end of the same dial the 4-WR read (§2.4) is the
pass-strong end of:
- **Single-high (Cover 1 / Cover 3) → strong safety DOWN → 8-man box → run-strong.**
  Against a run threat / short-yardage / heavy personnel, roll to single-high and
  bring the SS into the box.
- **Two-high (Cover 2 / Cover 4) → both safeties back → 7-man box → pass-strong.**
  Against spread / 4-WR, stay two-high (§2.4).
- **RUN FITS (gap integrity):** every box defender must FIT his assigned gap or runs
  break — the defense has to KNOW its run fits, not just rush bodies. A competent
  run D (the "beatable run D" gate in §2.1/archetypes) is one that fits its gaps;
  blown fits = the big runs a human exploits.
- **FRONT COMMITMENT — the current defect (operator, evidence).** The CPU today
  shifts its line/LBs willy-nilly — "ABSOLUTELY no thought behind it." Matches the
  binary: the pre-snap shift picker `0x00145940` (Agent A) uses FIXED weights that
  NEVER pinch or spread (the 0.0 weights), so the front can't commit to a side or a
  gap; it just shuffles. **FIX: drive the front from the READ** — commit the line
  shift + LB slide to the side the offense favors running to (**directional
  tendency is ALREADY recorded in the ring's `@8` inside/outside + L/R field**) +
  formation strength, with proper gap fits, and enable the zeroed pinch/spread so it
  can actually load a gap. The front-seven counterpart to the safety-shell decision
  above — same read, same `0x00145940` hook, read-driven instead of fixed. Willy-
  nilly loading (no commitment, no fits) is exactly how a human runs where the front
  isn't; commit to the favored side AND fit the gaps and the run has nowhere to go.
- **The commitment is a DELIBERATE TRADE-OFF (operator).** Loading one side *spends*
  strength on the other — it's a calculated bet on the read, not free. Three
  consequences, all good: (1) it's a real decision with a downside; (2) a smart human
  can read the load and counter to the vacated side — **honest counterplay** (win by
  out-thinking, not mashing); (3) if the human keeps exploiting it the coach adjusts
  (§2.3 self-scheme). Commit → counter → re-read → adjust: a chess match, not a
  shuffle.

Feasibility/investigation: the SS-down alignment is likely AUTHORED into the Cover
1/3 plays (the coach-brain's job is CHOOSING the shell from the run/pass read +
situation + personnel). Whether the engine models per-defender GAP assignments — so
we can ensure/measure gap integrity — is an investigation item, same class as the
coverage-rotation unknown (§7).

### 2.8 Base by default — deviate only with a reason (the antidote to willy-nilly)
The DEFAULT defensive behavior is a **sound base defense** — proper alignment, base
coverage, gaps fit. The CPU does NOT shift/blitz/disguise willy-nilly (the §2.7
defect); it **sits in base UNTIL the coach-brain produces a justified REASON to
leave it.** The defensive decision is two-stage: (1) is there a reason to deviate?
(2) if yes, which deviation. No reason → base (calm, sound, not random).

Every defensive deviation scoped here is a "reason to leave base," each from a
specific read:
- **front commitment** (§2.7) ← a directional run tendency,
- **coverage check** (§2.4) ← 4-WR → two-high; run threat → single-high SS-down,
- **blitz** ← an obvious passing down, a needed stop, a QB who holds the ball, a
  weak-OL / great-rusher edge, the score/clock (a blitz is just a defensive play
  selection — the seam covers it; no new mechanism),
- **disguise / late rotation** (§2.6) ← a good QB + the DB personnel to sell it,
- **PA-bite adjustment** (§2.6) ← the multi-variable model.

This both fixes the willy-nilly defect AND makes every deviation MEANINGFUL and
HONEST: leaving base is a TELL — the coach saw something, and a good human can read
it, unlike a random shuffle that teaches nothing. The coach "deems it so";
deviations are decisions, not noise.

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

**Phase 0 — Live-read session (rig).** One session closes ledger items **A1–A7**
(§7): bind the situation slots; watch the query path run (row width, the 225
bound); enumerate `@14`'s outcome classes; confirm `@15` + the covered-receiver
byte; settle the defensive call timing/seam question; plus two cross-campaign
freebies that share the session (fatigue F3; the PA block-mode read). Cheap; gates
the design. The B-list static hunts (§7) can run any time, rig-free.

**Phase 1 — Take the seam, grow the brain (incremental; each step lands alone
with its own toggle + acceptance arm — rule 2, the double-team discipline).**
- **1a — NULL-BRAIN diagnostic.** Retarget the two seam callers to a cave that
  re-implements the trivial choice (pick from the enumerated pool, uniform or
  mimic-stock). ZERO intelligence — proves the hook, the pool read, and the
  return contract, nothing else. The C1-full pattern: blunt lever-proof first.
  Acceptance: full games play normally, the CPU calls legal plays from the right
  groups, no crash across quarters.
- **1b — Situation baseline.** Read the situation object; sane down/distance
  behavior (no deep bombs on 4th-and-inches).
- **1c — Tendency counter (anti-cheese).** Opponent ring side + the fast-cheese
  short window (§2.3/2.4).
- **1d — Roster identity.** The game-load roster analysis: archetypes, opponent
  strengths/weaknesses, self-scheme reads (§2.1/2.2/2.3).
- **1e — Error term + difficulty scaling** (§1).
Prove everything at stock ring size throughout.

**Phase 2 — Extend `ptrk` schema.** Add the pass-target field + the game/session id
(§2.5); read the already-recorded dimensions (`@8/@13/@14/@15`); and if A3 shows
`@14` does not already encode 1st-down/TD, extend the recorder's outcome field
(T-track requires it).

**Phase 3 — Defensive visible-cue reads (§2.6).** Replace the `IsRun` omniscience
with the PA-bite diagnosis; gate the audible-mirror; disguise + late rotation IF
Phase 0 finds a rotation primitive.

**Phase 4 — Drop the cheese (LAST).** Neuter the two getters (§4.1) — only after
the calling is smart, or the CPU gets dumber.

**Phase 5 — Resize + persistence.** 48 → 200 (or a full season ~1200/side). **The
ring is HEAP-allocated** (ctor `0x0024D890` mallocs 1556 B via `0x0039D6C8`;
pointer at `0x00601EB4`), so the resize costs **ZERO ELF space** — the size
constants (`1556`→`6416`, ~30 others) are edited in place; the array grows only in
heap RAM (~4.75 KB @200, ~77 KB @season — trivial vs 32 MB). So memory is NOT the
constraint; the save migration is (size once). ~30 mechanical constants +
reformulate the weight tables as a decay curve (also serves §2.5) + save-format
versioning/migration. Invasive in the SAVE format, but free in the ELF — it does
not compete for the tight code budget the coach-brain needs.

**Workstream W — ELF-expansion toolchain (parallel, start early).** §3 ships the
brain in a relinked ELF inside a rebuilt ISO, but no phase above builds that
pipeline — it is its own workstream (§7 B6): expand/relink `SLUS_207.52` (new
PT_LOAD segment vs grown `.text`), rebuild the ISO, verify under the emulator.
Track 1 (online play) needs the rebuilt-ISO path anyway; the coach-brain is simply
its first large tenant. Caves carry dev iteration until it lands. **Scoped in
`pnach-to-iso-pipeline.md`** — P1 (same-size bake) is unblocked now and audited
102/102 against the live patch set; P2 is the grow path with the segment-placement
question (B6). The Xbox port rides the same philosophy: `xbox-madden-2004-plan.md`.

**Ongoing — Expand the play pool.** **Custom playbooks must hold ≥200 plays**
(template is ~175 today, ~18/group) so the archetypes (§2.1) and situational calling
have real depth. A playbook-DB capacity change — investigate the per-team playbook
block size (`0xAFBC`, Agent A), the PBAI row cap, and where create-a-playbook limits
to ~175; the caps may be DISC-format rather than ELF, so attack with the existing
disc tooling (`play-data.md`, `tools/madden_tdb.py`, `lzh1`) — §7 C1. The 225-slot
candidate buffer has NO bound check (`ai-play-calling.md` F5; the bound is real —
`224` immediates verified at `0x2BD2EC`/`0x2C1E20`). **225 caps one situational
GROUP, not the playbook**: 200+ plays spread across groups never near it, but raise
the buffer before any single group could exceed it.

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
| T-pool | a custom playbook holds ≥200 plays and the AI selects from the full set — no truncation, no buffer overflow |
| T-decheese | repeating a play no longer inflates CPU ratings (the 9 consumers read 0 boost) |
| T-regress | human-vs-human (online) unaffected — `ptrk` never fires with no CPU side; single-player still feels fair |

---

## 7. Open questions — the ledger

Every unanswered question, grouped by how it gets answered. Consolidated in the
2026-08-13 review pass; items previously scattered inline (§2.4, §2.6, §2.7) now
live here canonically.

### A — Live reads (the Phase-0 rig session; one session covers all)

| # | question | gates |
|---|---|---|
| A1 | Bind down/distance/timeouts/current-play-id slots in the situation object — dump 0x120 B at `*0x00601F4C`, compare to the HUD | the situational model (§1), PA-bite (§2.6), shell choice (§2.7) |
| A2 | Watch the query path execute (break in `0x002BFF68`): the weighting/renorm/pick live, the result-set row width, the 225 bound | the seam replacement (§4.2), pool expansion |
| A3 | Enumerate `@14`'s five outcome classes against observed play outcomes — does it already encode 1st down / TD? | Layer-1 outcome tracking (T-track), Phase-2 schema |
| A4 | Confirm `@15` run/pass writes live, and which `+0x2FC` param byte is the covered-receiver index | tendency reads, matchup scoring (§2.2) |
| A5 | Defensive call timing: what does the D know at pick time, and does the defensive play flow through the same seam (`a0`=side suggests yes — unverified for defense) | §2.4 checks, Phase-1 defensive scope |
| A6 | *(cross-campaign)* fatigue F3: does a defender's `fatg` accumulator persist across snaps/plays? | the defense-fatigue campaign — same session, free |
| A7 | *(cross-campaign)* PA pass-pro: linemen's block-mode (`+0x3F0`) on a play-action play — run-block (2) or pass-pro (1)? | the block-dominance PA thread — same session, free |

### B — Static hunts (no rig; can run any time)

| # | question | gates |
|---|---|---|
| B1 | The D-audible trigger: what code fires the CPU's counter-audible, and what can it change (play? alignment? both)? Hypothesis (§2.4): the formation checks REPURPOSE this path — fire it off the personnel read, not the audible event | §2.4 checks, §2.6 audible fix |
| B2 | Defensive personnel packages: where base/nickel/dime selection lives; can a sub be driven from a personnel count? | §2.4 (4-WR → nickel/dime) |
| B3 | Coverage-rotation primitive: can a defense show shell A and play shell B (post-snap assignment rotation)? **Highest-uncertainty item — sets the ceiling on §2.6** | disguise + late safety rotation |
| B4 | Gap assignments: does the engine model per-defender run-fit/gap responsibility, or is the "fit" emergent pursuit? | §2.7 run fits |
| B5 | Pass-target signal: where "which receiver was targeted" is readable at play end, for the recorder's new field | Phase-2 schema |
| B6 | ELF-expansion toolchain: grow/relink `SLUS_207.52` (new PT_LOAD vs grown `.text`), ISO rebuild, emulator compatibility | shipping (§3, Workstream W) — Track 1 needs it anyway |

### C — Disc / data-format investigations

| # | question | gates |
|---|---|---|
| C1 | Playbook capacity: the TOTAL play cap, the PER-FORMATION cap (the I-form depth question), and where the ~175 template limit lives (ELF constant vs disc DB format). Tooling exists: `play-data.md`, `tools/madden_tdb.py`, `lzh1` | ≥200-play custom playbooks (T-pool), archetype depth |
| C2 | Disc asset #69 — the situational-policy bytecode: format, decompile, re-author | 4th-down/clock game management (§1) |

### D — Design decisions (the Architect's, not investigations)

| # | decision | notes |
|---|---|---|
| D1 | Final ring size: 200 (ratified) vs full-season ~1200/side | cost trivial either way (~40 B/record, zero ELF); the save migration happens ONCE — size for the horizon wanted |
| D2 | Save-key: per-matchup vs per-human profile (any later team scouts you) | franchise-STPG reuse vs a new memory-card file; determines the schema (§2.5) |
| D3 | Situational tendency bucketing — *proposed, NOT yet ratified*: track tendencies per down/distance bucket ("his 3rd-and-2 call"), not just global | schema impact in Phase 2; likely the biggest realism jump per byte |
| D4 | Fatigue-aware calling — *proposed, NOT yet ratified*: the coach reads `fatg` (run at a gassed defense; rest/sub its own tired stars) | cross-campaign wiring with defense-fatigue |
| D5 | Identity vs exploitability: how consistent may the CPU's "look" (§2.3) be before a human learns and cheeses it | the error term + disguise (§2.6) partially cover; explicit knob? |

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

Design consolidated, then **reviewed (2026-08-13 second pass)**: the build plan
made incremental (null-brain seam takeover first — rule 2), the ELF-toolchain
workstream added, dead cross-references fixed, and every open question
consolidated into the §7 ledger (A live / B static / C disc / D decisions —
including the D-items awaiting the Architect's ruling). Three engineering
investigations DONE and spot-checked (§4). Remaining before build: the **Phase-0
live-read session** (ledger A1–A7); the B/C hunts can run any time, rig-free.
Nothing is patched until Phase 0 lands.

# Seed manipulation and staged statistical testing — the plan

Recorded 2026-08-11 from the operator. Plan only: **nothing here is built, and
the gate in part 1 is not passed.** This document exists because the testing
this project is heading toward — patched game AI judged against distributions,
not single replays — is qualitatively harder than anything the harness does
today, and the operator's direction is to take that seriously now rather than
discover it at release time.

> "write a plan to manipulate the seeds in the future so that we can test upper
> and lower bounds for extreme regression … integration testing for this will
> be complex … we will have to have established ranges that we expect things to
> fall under … variable amounts of them depending on what stage of release we
> are … more repetitions for testing the final product than normal … likely
> take hours or days … upper bounds lower bounds standard deviations and
> extreme spreads"

## Why seeds, restated in one paragraph

A savestate freezes every RNG seed (they live in EE memory behind
`rng_context`, `0x006046BC`), which is why a reload replays bit-identically —
ideal for causation ("did my patch change THIS play"), useless for
distributions ("what does this play DO now"). Writing a different seed after
each load turns one savestate into N genuinely different worlds with formation,
play call, adaptation (`ptrk` ring — also frozen memory) and fatigue all held
constant. That is controlled variance: the only thing that differs between
iterations is the dice. No other instrument this project has gives that.

## Part 1 — the gate: decode before one byte is written

**Nothing writes a seed until the format is decoded.** A garbage seed
"producing variance" by breaking the RNG would generate exactly the kind of
confident wrong numbers this project keeps having to retract. In order:

1. **Decode the element format.** `[rng_context+0x14]` is an array of
   per-channel seed words, advanced by `0x00468fa8`, channel index masked to 8
   bits. Statically derive the generator (LCG/LFSR/whatever `0x00468fa8` is),
   the element width, and how many channels are actually allocated.
2. **Map channel consumers.** 205 call sites pass a channel argument. Census
   which channels feed gameplay (block lock-ins, decay draws, tackle rolls)
   versus presentation (audio, crowd, menus). Perturbing a presentation channel
   and concluding "the play is seed-insensitive" is the obvious false negative.
3. **Prove control end to end.** Acceptance for the whole part: on one
   savestate, (a) same written seed twice → bit-identical plays (the harness's
   existing tick-aligned agreement is the instrument); (b) two different seeds
   → divergent plays; (c) seed restored → original play reproduced exactly.
   Until (a)-(c) pass, no experiment may cite seed-swept results.
4. **Harness surface.** `Trial` gains an optional `seed_policy`: `frozen`
   (today's behaviour, the default), `sweep(list)` (explicit seeds, recorded
   per iteration in the result rows), `derived(i)` (deterministic function of
   iteration index). A result file must state which policy produced it —
   a distribution whose seeds are unrecorded cannot be reproduced or audited.

## Part 2 — what seed sweeps are FOR: bounding, not just averaging

The operator's framing is the right one: the purpose is **upper and lower
bounds for extreme regression**, not prettier means. A patch that improves the
median dive while occasionally producing a 99-yard fumble return has failed,
and only the tails can say so.

Per metric under test, a sweep of N seeds yields and records:

* **min / max** — the observed extremes, each with its seed, so any extreme
  is REPLAYABLE: rerun that seed, watch that exact play at the console.
  The operator's eyes have out-performed summary statistics all day; the
  seed-to-replay link is what lets them be aimed at the worst case.
* **mean and standard deviation** — with the caveat the reviewer would insist
  on: game outcomes are not normal, so σ is a spread gauge, not a probability
  claim.
* **percentile spread** — p5/p25/p50/p75/p95 ("extreme spreads"); more honest
  than σ for lumpy football distributions (a dive's yardage is multi-modal:
  stuffs, modest gains, breakaways).
* **out-of-range count** — how many of N landed outside the established range
  (below), which is the number the release gates actually read.

Two-sided always. "Extreme regression" includes too-good: a lead dive that
averages 12 yards after the R6 patch is as broken as one that loses ground,
and a pass rush that never arrives is as wrong as one that arrives instantly.

## Part 3 — established ranges: every gated metric gets a card

Before any staged testing can exist, each metric that gates a release needs a
**range card**, recorded next to the spec that measures it:

```
metric:            carrier_yards (double_team.py, slot 9)
baseline:          -0.70 every seed (pre-patch, frozen-seed era)
expected range:    [-2.0, +9.0]     <- pass band after patch
extreme band:      [-5.0, +25.0]    <- outside this, HALT: individual replay
                                       review of that seed, no exceptions
derived from:      N=?? sweep on build ??, date, result file
tails allowed:     p5 >= -2.0, p95 <= +12.0, out-of-range <= 2% of N
```

Rules for the cards:

* **Ranges are derived from measured sweeps, never invented.** The first sweep
  on an unpatched build writes the baseline card; patch acceptance is stated as
  a movement of that card, not as raw thresholds someone liked.
* Cards are versioned with the patch set. A range card whose provenance line
  is stale is invalid — the same discipline as the address map's `source:`.
* A metric without a card cannot gate anything. It can still be recorded.

## Part 4 — staged testing: variable depth by release stage

The operator's requirement: the amount of testing scales with what is being
released. Stages, with budgets stated in seeds-per-metric (wall-clock at slot
9's measured ~12 s/iteration):

| stage | when | seeds per metric | wall clock (one spec) | gates |
|---|---|---|---|---|
| **S0 smoke** | every patch edit, before anything else | frozen seed ×3 | ~40 s | bit-identical replay still holds; the patched word reads back over PINE |
| **S1 patch acceptance** | a patch candidate claims its requirement | 20-30 | 4-6 min | its own metric moves as claimed; its `must not break` savestate unchanged |
| **S2 integration** | two or more patches combined | 50-100 per affected spec | 10-20 min each | every range card in the affected area holds; no interaction extreme |
| **S3 release candidate** | before anything is called done | 300-1000 per gated metric, every spec, both arms | **hours** | full card compliance incl. tails; every out-of-range seed individually replayed and explained |
| **S4 soak** | final product | 1000+ per gated metric, multi-savestate, operator sessions interleaved | **days** | as S3, plus the operator plays real games against it and files observations — the instrument that has caught every wrong number so far goes last and counts most |

Notes the stages depend on:

* **S0 exists because of the pnach byte-write incident**: a patch that parsed
  but applied wrong (30 → 44, not 300) would sail through statistics measuring
  the wrong build. Every stage re-verifies the patched words over PINE first.
* **S2 is where complexity actually lives, and it is taken seriously here**:
  patches that each pass alone can interact (rule 2 exists for this). The
  known coupling hazards are already on record — `reselect_timer` runs DOWN on
  kinds 4/8 but UP to a 61 cap on kinds 3/7, so a duration patch interacts
  with anything touching engagement kinds; the contest composites decay on the
  global snap clock, so a protection patch interacts with anything that
  lengthens plays; 26 state handlers are shared, most sharply 47/72. S2's
  matrix is per-pair first (A, B, A+B), full set last.
* **S3/S4 runtimes are real and planned for, not discovered**: 1000 seeds ×
  12 s ≈ 3.3 hours per metric per spec per arm. Multiple specs, both arms,
  soak repetitions — days, exactly as the operator says. The runner's existing
  crash-resume behaviour (append-only JSONL) and `nohup` discipline are what
  make multi-day runs survivable; disk budgeting comes from the specs' own
  declared MB_PER_ITERATION through the menu, which already exists.
* Deterministic-arm testing (frozen seed, N=1 decisive) remains the FIRST
  check at every stage — it is cheap and conclusive for causation. The sweeps
  answer the question determinism cannot: what the change does to the
  distribution.

## Part 5 — honesty constraints, so the statistics stay trustworthy

* **Seeds are recorded in every result row** (the policy and the value). An
  extreme that cannot be replayed is an anecdote, not a finding.
* **The harness's known blind spots apply to sweeps too**: episode-scoping
  (three whole-play metrics have already shipped wrong), the ~1-in-6 sampling
  loss under multi-player polling, and PINE tearing. A tail observation made
  of one torn frame is noise; the existing 2-frame persistence rules carry
  over unchanged.
* **Ranges move only with evidence.** Widening a band to make a stage pass is
  the statistical version of quoting the whole-play pushback: it makes the
  number agree with the wish. Any band change cites the sweep that justified
  it.
* **The operator closes the loop.** S4 includes live play sessions precisely
  because summary statistics have been wrong three times today and his eyes
  were right three times. The pipeline exists to aim that instrument, not to
  replace it.

## Current status

* `rng_context` is mapped (`addresses.yaml`) and READ-ONLY. Part 1 not started.
* No range cards exist yet. The first candidates, with baselines already
  measured under frozen seeds: `carrier_yards` (-0.70), `dt_longest_hold`
  (13-30 by 43 — pending the contiguity re-derivation the review requires),
  `defender_pushback` (+0.410), `worst_drop_early`/`late` (0.252/0.678).
* The review's metric corrections (contiguity, `KIND_COMMITTED`, carrier-yards
  on dropbacks) gate the first real card: **cards inherit metric bugs**, so the
  metrics get fixed first.

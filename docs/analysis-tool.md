# Analysis tool — reading a result file without writing a script

Design, 2026-08-11. A set of commands that answer the questions currently
answered by throwaway Python written fresh for every run. The harness
(`tools/madden_lab/`) already produces defensible numbers; nothing yet makes
them *explorable*, so every diagnosis so far has been a hand-written loop over
a 1.5 GB file, discarded afterwards, and re-derived the next day.

## Sizes, because they decide the whole design

A 200-iteration run is roughly **4 million rows and 1.5 GB** of JSONL, one row
per `(iteration, frame, entity, field)`. Measured on synthetic rows of the real
shape: **316 bytes a row**, and `json.loads` over all of it costs **25 seconds**
on the dev box. That single number rules out three obvious designs — load it
into a dict, load it into sqlite, load it into anything — and it also says that
a tool which parses the whole file for every question is a tool nobody will use
interactively. The two consequences run through everything below:

* **Stream, and filter before parsing.** A substring test on the raw line
  before `json.loads` costs nothing and skips 99.9% of the parses. Measured on
  the same file: one entity + one field goes from 25 s to **4 s** (5.7x);
  metric rows only go to **1.7 s** (15x). The rows are written with
  `separators=(",", ":")`, so `"entity":"player:0:9"` is an exact substring of
  every matching line and of no other. It over-matches (`player:0:1` is a
  substring of `player:0:10`) and that is safe — the survivors are parsed and
  checked properly. It never under-matches, which is the only direction that
  would silently lose rows, and that is a **proof rather than a hope**: JSON
  escapes a quote inside a string value as `\"`, so the literal bytes
  `"kind":"sample"` cannot occur anywhere but a real key/value pair. The one
  case where the needle would be wrong — a value JSON *would* escape, a name
  with a quote or a non-ASCII character in it — is detected by round-tripping
  `json.dumps` and falls back to parsing every line.
* **Cut once, then work small.** `slice` exists so the expensive pass happens
  once. A 400 KB slice of one player answers thirty questions in a second each.

## The six analyses this replaces

Every one of these has been done by hand at least once. The right-hand column
is the commitment this design makes.

| # | done by hand | becomes |
|---|---|---|
| 1 | per-metric summary across iterations — `carrier_yards` was `[1.1, 0.3, 0.9, 0.9]` | `summarize` |
| 2 | tick-aligned cross-iteration comparison and disagreement classification | `agree` (the offline half of `verify-determinism`) |
| 3 | per-entity field census — `player:0:9`'s `engagement` was `{0: 232, 2: 86}`, so contact never happened | `timeline` |
| 4 | episode extraction — "ticks 90-163, engaged with defence idx 15" | `timeline` |
| 5 | per-frame `xyz` for one entity across iterations, then plot | `slice` feeding `tools/plot_routes.py` |
| 6 | baseline vs patched | **already `compare.py`.** Not duplicated. |

3 and 4 are one command because they are one pass over the same filtered rows:
a census is the sum of the episodes, grouped by value. Splitting them would
mean reading the file twice to print two halves of the same answer.

## What it reuses, and what is genuinely new

Reuse is most of the value here. The parts that already exist are the parts
that were hard to get right, and re-implementing any of them would silently
lose the corrections they encode.

| existing | used for | change needed |
|---|---|---|
| `results.read_rows` | streaming, and tolerating the truncated last line of a run still in progress | split out the line iterator so a prefilter can sit between reading and parsing (**R1**) |
| `results.load_run` / `RunFile` | the run header, metric rows, iteration digests | prefilter, so `summarize` and `compare` stop paying 25 s to read 2400 metric rows (**R2**) |
| `runner._tick_aligned_agreement` and its phase / sub-tick classifier | `agree` | split the "build a tick index" half from the "classify disagreements" half, so both a live `Samples` and a JSONL file can feed it (**R3**) |
| `runner._is_continuous` / `_as_axes` | deciding whether a field has episodes or a track | moves with R3 |
| `compare.median` / `quantile` / `mean` / `stdev` | the spread in `summarize` | none — import them |
| `results.RunFile.metric_values` / `metric_missing` | `summarize`'s None handling | none |
| `world.Handle` | decoding an `engagement_link` word into `player:1:15` | none, but import it lazily: `world` needs PyYAML and analysis must run where the address map does not |
| `tools/plot_routes.py` | pixels | feed it a slice instead of pointing it at a run; and fix its hardcoded LOS (**R4**) |
| `compare.py` | verdicts | none. Out of scope by design — see "what it does not do" |

Genuinely new, in descending order of how much thought they need:

* **Episode extraction** — run-length encoding a field on the tick axis, with a
  minimum-length guard so a torn read does not manufacture a one-frame episode.
  Nothing in the repo does this.
* **The value census** per `(entity, field)`, which is the same pass.
* **Entity discovery** — "which entities ever held `block_mode == 3`" — so the
  interesting player does not have to be known before the command is typed.
  Today `plot_routes.py` hardcodes `player:0:9`, and nobody remembers why.
* **The row filter vocabulary and its prefilter**, shared by all four commands.
* **The slice writer**, which is trivial once the filter exists.

## The commands

Four, flat, alongside `compare` and `answer` in `python -m tools.madden_lab`.
Flat rather than nested under an `analyze` verb because these get typed at a
console next to a booting console, and `analyze episodes` is two words where
one will do.

### `summarize` — did the run work, and what did it measure

```
python -m tools.madden_lab summarize RUN.jsonl [--metric NAME] [--json]
```

One pass over metric, iteration, run and event rows; sample rows are skipped by
the prefilter without ever being parsed.

```
run 8f3c1d2e4b6a  spec lead_blocker_gate_a (digest 4a1f9c22)  arm baseline  git 30c24db
state SLUS-20752 (14F8B841).06.p2s  [pre-snap, scratch]
3 iteration(s) complete, 1 in progress; 4,537 sample rows

! all 3 completed iterations produced an identical sample stream. They
  are replays, not independent samples: n_effective is 1, and any spread
  below is the spread of one play measured once.
! iteration(s) 3 have metric rows but no iteration row, so the run is
  still writing or was killed. Their plays may be short; --complete-only
  excludes them.

metric                            n  none    median  IQR                     min      max
carrier_yards                     4     0     0.900  [  0.750,   0.950]    0.300    1.100
                               values: 1.100  0.300  0.900  0.900
lead_blocker_block_depth          3     0     0.550  [  0.550,   0.550]    0.550    0.550
first_mode3_frame                 0     3        --  never produced a value
                               none on iterations 0, 1, 2
```

(Real output, from the fixture the tests build.)

Four decisions in that block, each from something that has already gone wrong:

* **Median and IQR, not mean and SD.** `[1.1, 0.3, 0.9, 0.9]` has a mean of
  0.80 and a median of 0.90, and the whole difference is one noisy iteration —
  the same 0.3 that was read as a patch effect for a day in "Patch mechanism"
  (`lab-design.md`). A robust centre would not have been fooled.
* **The raw values are printed whenever n is 12 or fewer.** A summary statistic
  of four numbers is a worse representation of them than the four numbers. The
  summary row is for scanning; the values row is what actually gets read.
* **`none` is a column, never a filtered-out row.** A metric that legitimately
  did not happen (no lead blocker in this formation) and a metric that is
  broken look identical in a mean, and are told apart by this count. `--metric`
  on a metric that only ever returned None lists the iterations it failed on,
  because *when* it failed is usually the answer.
* **The determinism warning is at the top, not the bottom.** Read from the
  iteration digests via `RunFile.iteration_digests` — the same signal
  `compare.py` uses to refuse statistics. On this engine identical iterations
  are the *expected* result, and a summary that quietly reports the spread of
  four copies of one play invites a confidence nothing supports.

`--complete-only` uses the `iteration` rows as the completion marker: the
runner writes one at the end of each iteration, so an iteration with metric
rows but no iteration row is the one currently running. Without it, inspecting
a run mid-flight silently mixes a half-length play into the distribution.

### `timeline` — what did this field do, and when

The one to build first. It answers 3 and 4 above, and it is the command with no
existing implementation anywhere in the repo to lean on.

```
python -m tools.madden_lab timeline RUN.jsonl --field engagement
        [--entity player:0:9] [--value 3] [--iteration 0]
        [--with engagement_link] [--min-frames 2] [--json]
```

With `--entity`, one entity. Without it, every entity that ever held `--value`
— which is the discovery step that today happens by poking around in the
interpreter.

```
engagement   player:0:9   216 samples

  census (raw samples per value, all iterations -- unlike the episodes below,
  not torn-read guarded)
    0                              85   39.4%
    2                              75   34.7%
    4                              56   25.9%

  episodes, iteration 0, min 2 samples
    tick     4-89     0                   86 samples
    tick    90-163    2                   74 samples  engagement_link player:1:15
    tick   164-219    4                   56 samples  engagement_link player:1:15
    1 episode(s) shorter than 2 samples folded between equal neighbours (torn read)
```

The census counts 85 zeros and the first episode covers 86 samples, and that
gap is the point rather than an inconsistency: the census is raw, the episode
absorbed the torn sample at tick 50. Both numbers are printed, and the census
header says which is which — an unlabelled disagreement between two totals in
the same block is how a reader stops trusting either.

Design details that carry weight:

* **The axis is `tick`, always.** A row carries three time-like fields:
  `frame` (offset from the iteration's base tick), `sample` (the poll-loop
  ordinal) and `tick` (the game's own clock). `lab-design.md` records what
  ordering by `sample` cost — a bit-for-bit reproducible engine reported
  DIVERGENT — so ordering by anything but `tick` is not offered, not even as a
  flag.
* **`span` next to `samples`.** They differ exactly when the poll loop dropped
  frames, and an episode where they differ is one whose boundaries are soft.
  The runner already draws this distinction (`span` vs `frames` on
  `IterationResult`); the analysis has to preserve it or it manufactures a
  contiguity that was never observed.
* **`--min-frames 2`, defaulting to 2.** This is
  `Samples.first_frame_where(run=2)` generalised: PINE reads are not
  synchronised with emulation, so a single sample can carry a value the game
  never held, and an episode boundary is precisely where that lie is most
  expensive. An episode below the threshold whose two neighbours agree is
  folded into them and counted; one between two *different* values is kept and
  marked, because folding it would invent a transition that did not happen.
* **Discrete fields get episodes; continuous fields get a track.** Decided per
  `(entity, field)` by `align.is_continuous` over the values actually seen — if
  any value is a float or a vector of floats, the whole field is continuous.
  Run-length encoding `xyz` would emit one episode per sample, which is not an
  analysis, it is the file again. A continuous field reports count, first,
  last, min and max per axis, and points at `slice` and the plotter.
* **`--with FIELD`** shows a second field's value at the same tick beside each
  episode, which is how "engaged with defence idx 15" gets answered without a
  second command. Handle words are decoded through `world.Handle` when the
  decode succeeds and printed raw when it does not; the raw word is always in
  the `--json` output, because a decode is an interpretation and the record
  should keep what was actually read.

### `agree` — the same tick, across iterations

```
python -m tools.madden_lab agree RUN.jsonl [--iteration 0,1,2] [--field xyz]
                                          [--repeats N]
```

This is `verify-determinism` with the rig removed. Same classifier, same
constants, same output — `FieldAgreement.describe()` — but reading a file, so
the determinism question can be re-asked months later, on a run recorded by
someone else, without booting the game.

```
aligned on the game clock: 180 common ticks, 0 uncertified samples excluded
  ai_state             540 compared  100.00% identical
  engagement           540 compared   99.63% identical  (2 phase)
  xyz                  540 compared   80.04% identical  (141 phase, 1440 sub-tick (max 0.152))
DETERMINISTIC (discrete exact; continuous within 0.152 read noise)
```

Two hard edges:

* **Only certified samples are compared**, and the count of excluded ones is
  printed. An uncertified sample may straddle two game frames; comparing it
  proves nothing in either direction. If a file has *no* certified samples it
  predates the clock-bracketed batch, its `tick` is really a sample ordinal,
  and `agree` refuses rather than aligning on a fiction — that fiction is
  exactly the first determinism run's mistake.
* **Iterations are capped, default 3.** The tick index is the one structure
  here that must be resident: `iterations x ticks x keys` values, about 40 MB
  at 200 x 300 x 130. Three repeats is what the question needs. Asking for more
  is allowed and is told what it will cost.

### `slice` — cut the file down, keep the schema

```
python -m tools.madden_lab slice RUN.jsonl --out small.jsonl
        [--entity E] [--field F] [--kind sample] [--iteration N]
        [--tick-from T] [--tick-to T] [--certified-only] [--where FIELD=VALUE]
```

The output is the same schema, so **every tool that reads a run still reads the
slice**: `plot_routes.py`, `compare.py`, `load_run`, and the analyzer itself.
That is the whole reason `results.py` chose one self-describing row per line,
and this command is the first thing to cash it in. `.gz` works because
`results._open_write` already handles it.

* The `run` header row and all `iteration` rows are **always kept**. They are a
  few hundred bytes and they are the provenance; a slice without them is a file
  that cannot be defended later, which the result format exists to prevent.
* `--where block_mode=3` selects *entities* by a value they ever held, and
  therefore costs **two passes** — pass one to find out who, pass two to cut
  their rows. Stated in the output, because the alternative is buffering 1.5 GB.
* Feeding `plot_routes.py` is the headline use: cut `player:0:9`'s `xyz` plus
  `engagement` plus `game.los` into 400 KB, then iterate on the picture without
  re-reading the run each time.

## The data model

No class for the row. A row is the dict `read_rows` yields, and the tool's
types are the three reductions of it:

```python
Selection      # which rows: kinds, iterations, entities, fields, tick range,
               # certified-only -- plus the substring needles it can prefilter on
Episode        # (iteration, entity, field, value, first_tick, last_tick,
               #  samples, span, folded, partners)
FieldSeries    # per (entity, field): census {value: count}, episodes per
               # iteration, or a continuous track summary
```

and, shared with the runner via R3:

```python
TickIndex = Dict[int, Dict[Tuple[str, str], Any]]   # tick -> (entity, field) -> value
```

Values arrive from JSON, not from the world, and that has two consequences
worth stating rather than discovering: `xyz` is a **list**, so it is unhashable
and must be tupled before it can key a census; and a field whose values happen
to be integral may come back as `int` where the live path had `float`, so
continuity is decided over the values observed rather than value by value.

## One pass or two

| command | passes | why |
|---|---|---|
| `summarize` | 1 | metric, iteration, run and event rows only; samples never parsed |
| `timeline --entity` | 1 | one entity, one field, prefiltered |
| `timeline` (discovery) | 1 | episodes for every entity accumulate in bounded memory — 22 entities x 6 fields of accumulator — and `--value` is applied at print time |
| `agree` | 1 | but resident: the tick index for the capped iteration set |
| `slice` | 1 | filter and write |
| `slice --where` | **2** | which entities qualify is only known at the end of pass one |
| exact quantiles over *sample* rows | **2**, so not offered | see below |

Quantiles over metric rows are exact and cheap — there are a few hundred of
them. Quantiles over sample rows would need every value resident or a second
pass, so `timeline` reports count, min, max and first/last for a continuous
field and does not pretend to a median. A one-pass approximate quantile is a
thing that exists; a number whose accuracy depends on arrival order is not a
number this project should print.

## Refactors this needs

Each of these exists so the tool can *use* code rather than copy it. None is
speculative — every one is load-bearing for a command above.

**R1 — split the line iterator out of `results.read_rows`.** Today it reads,
parses and yields in one loop, holding the last line back so a truncated final
line of a killed run is forgiven while a bad line mid-file still raises. That
tolerance is exactly right and must survive. Extract it as `read_lines(path)`
yielding `(line_no, text, is_last)`, make `_parse_row` public, and rebuild
`read_rows` on top. The analyzer then puts its prefilter between the two.
Without this, every command either loses truncation tolerance or pays 25 s.

**R2 — let `read_rows` take the kinds it wants, and stop `load_run` parsing
what it discards.** `load_run`'s docstring is already honest that sample rows
are 99.9% of the file and that it keeps none of them — but it still parsed all
4 million to count them. As built: `read_rows(path, kinds=(...))` prefilters,
and `load_run` counts sample lines by substring instead of parsing them (and
picks up the uncertified count for free from the same scan, since
`"certified":false` is another sound needle). `summarize` needs it; `compare`
gets an order of magnitude faster without a line changing, which matters
because `compare` is run against real 1.5 GB files today.

**R3 — split `_tick_aligned_agreement` at its own seam, into `align.py`.**
This is the important one. The function does two separable things: it builds
`per_iter: List[Dict[tick, Dict[(entity, field), value]]]` from in-memory
`Samples`, then it compares those dicts and classifies every disagreement as
identical / phase / sub-tick / real. Only the first half knows about
`IterationResult`, and only the first half is the part the analyzer cannot use.
Proposed:

```python
# tools/madden_lab/align.py -- pure, stdlib, no emulator, no world
PHASE_WINDOW, SUBTICK_CEIL, is_continuous, as_axes, FieldAgreement
def tick_index(frames) -> Tuple[TickIndex, int]
def agreement(per_iter, uncertified=0) -> Tuple[List[FieldAgreement], int, int]
def verdict(fields) -> str
```

`verdict` was not in the first draft of this list and had to join it: the
DETERMINISTIC / phase-noise / DIVERGENT ladder lived as a property on
`DeterminismReport`, and `agree` reaching the same conclusion from the same
evidence would otherwise have meant a second copy of the ladder as well as of
the classifier. `DeterminismReport.verdict` now decides only the one case a
file cannot speak to — whether the live streams were byte-identical — and
delegates the rest.

`runner._tick_aligned_agreement` becomes four lines over those, keeping its
name and signature so its tests do not move; `analyze` builds a `TickIndex` out
of JSONL rows and calls the same `agreement`. The alternative — a second copy
of the phase window, the sub-tick ceiling and the both-directions rule — is the
worst outcome available here, because the two copies would disagree the first
time either is tuned, and the classifier is the piece of this harness that took
four separate corrections to get right.

A new file rather than leaving it in `runner.py`: the executor importing the
analyzer reads backwards, and `runner.py` is already 1559 lines about running
trials, not about judging them.

**R4 — `tools/plot_routes.py` has a wrong LOS, and should be fed rather than
pointed at a run.** It hardcodes `LOS = 14.17`. The line of scrimmage the
engine itself holds for this savestate is **15.000** — that is the whole
finding behind `lead_blocker._los`, which records that deriving the LOS from
the centre's body position reads 14.219 against the engine's 15.000, a
systematic 0.78 yd bias that contaminated every absolute yardage reported. So
the commit-depth caption every route plot has printed is roughly **0.8 yd too
deep**, in the same direction and for the same reason as the bug already fixed
in the metric. The fix is to read `game.los` out of the stream it is already
being handed, falling back to the constant only when the file predates that
field and labelling the fallback as biased — exactly the shape `_los` already
uses. The slice makes it cheap: `game.los` rows are in the same file.

**Not a refactor, but worth naming:** `compare.py`'s "small statistics, stdlib
only" block is generic and now has a second consumer. Importing
`median`/`quantile` from a module named for verdicts is a smell, not a bug, and
splitting it into `stats.py` is churn with no behaviour behind it. Left alone
deliberately; revisit if a third consumer appears.

## What it deliberately does not do

* **No verdicts.** No p-values, no effect sizes, no "the patch works". That is
  `compare.py`, which spends 80 lines of docstring making a bare difference of
  means structurally impossible to print, and every one of those guards would
  be absent from a statistic bolted onto an exploration tool. `analyze` is for
  forming a hypothesis; `compare` is for testing one. If `summarize` ever grows
  a significance test, delete it.
* **No plotting.** `plot_routes.py` owns pixels. Keeping PIL out of the import
  graph is what lets every command here run on a box with no image library —
  and the boundary is honest, because a slice is a better interface between the
  two than a shared drawing function would be.
* **No index, no cache, no sqlite sidecar.** A cache that can go stale is a new
  way to be confidently wrong, in a project whose entire failure history is
  stale data being trusted. `slice` is strictly better: it produces a real file
  in the real schema that every other tool already reads, and it cannot
  disagree with its source because it *is* a subset of its source.
* **No recomputing metrics from samples.** Metrics are reduced by the runner at
  iteration end and written as rows, deliberately, so that `compare` never has
  to re-import a spec that may have been edited since — the failure mode that
  makes an old result indefensible. A command that recomputed `carrier_yards`
  from the sample rows using today's spec would reintroduce exactly that drift
  while looking like a convenience. Explore fields freely; never replace a
  metric row.
* **No cross-run joins.** One file per invocation. Two files is `compare`.
  (Concatenation still works — `cat a.jsonl b.jsonl` is a valid run file and
  every row carries its own provenance — but the tool will not pretend two arms
  are one experiment.)
* **No repair.** A malformed line anywhere but the last still raises, with its
  line number. Silently skipping it would turn corruption into a quiet change
  in the numbers.
* **No emulator, no writes, no game.** Every command reads one file. That is
  also the test criterion: the suite runs with no rig, like the rest of
  `tests/test_madden_lab_*.py`.

## Priority

If only one thing gets built: **`timeline`**. Analyses 3 and 4 are the two that
actually produced findings — `{0: 232, 2: 86}` proved contact never occurred,
and the episode list answered who the blocker addressed and when — and it is
the only command with nothing existing to lean on. `summarize` is an hour of
glue over `RunFile` and `compare`'s statistics, `agree` is R3 plus an adapter,
and `slice` is the row filter plus a writer.

Build order, which is not the same as value order: **R1, `slice`, `timeline`,
`summarize`, R3, `agree`.** `slice` first because every later command is then
developed against a 400 KB file instead of a 1.5 GB one, and because it is the
smallest thing that removes the biggest cost.

## What building it changed, and one defect it exposed

Four corrections the prototype forced, recorded here because a design that is
never checked against its implementation stops being a description of anything:

1. **The prefilter is sound, not heuristic.** The design justified it as "safe
   because we parse the survivors anyway". The stronger argument is above: JSON
   escaping makes the needle impossible to find inside a value, so the filter
   cannot under-match at all, and the only unsafe case — a value JSON would
   escape — is detectable and is refused. That upgrades a performance hack into
   a guarantee, and it is why `load_run` is now allowed to *count* sample rows
   by substring rather than parsing them.
2. **`verdict` had to move with the classifier** (R3 above). Splitting the
   comparison without the verdict ladder would have left `agree` to reach the
   same conclusion by its own arithmetic.
3. **The census and the episodes disagree by exactly the folded samples**, and
   that has to be labelled in the output or it reads as a bug in one of them.
4. **The episode axis needs a rewind guard.** `WorldFrameClock` returns
   backward clock movement as itself rather than smoothing it — deliberately,
   because smoothing it is how a savestate landing mid-iteration went unseen —
   so a tick going backwards inside one iteration is a real thing an analysis
   will meet. It closes the episode and is counted, rather than producing an
   episode that appears to run backwards.

**A defect found while moving the code, and deliberately not fixed here.**
`FieldAgreement` is decorated `@dataclass` but declares no annotated fields, so
the generated `__eq__` compares two empty tuples and the generated `__repr__`
prints `FieldAgreement()`. Every instance therefore compares equal to every
other regardless of field name or counts, and `repr()` of one shows nothing:

```python
>>> a, b = FieldAgreement("xyz"), FieldAgreement("engagement")
>>> a.agree = 500
>>> a == b
True
```

Nothing today depends on either behaviour — the hand-written `__init__` wins
over the generated one, which is why the class works at all — so this is
latent. The fix is to drop the decorator and write a `__repr__`, and it is a
behaviour change to a type that sits inside a public report, on a code path
(determinism reporting) that is not the one this change is modifying. Per the
repo's first rule it gets captured here with its own reasoning rather than
smuggled in.

## Status

Design, plus a prototype of all four commands and R1/R2/R3 in the tree
(`tools/madden_lab/analyze.py`, `tools/madden_lab/align.py`,
`tests/test_madden_lab_analyze.py`). R4 is written up above and **not** done —
it changes a number that has already been quoted, so it wants its own change.

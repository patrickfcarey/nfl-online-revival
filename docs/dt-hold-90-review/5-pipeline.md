# DT-HOLD-90 v2 — hostile review, lane 5: the deployment and measurement chain

**Question.** Assume the patch is logically right. Can this pipeline still produce
a wrong verdict?

**Method.** Static/offline only — no rig, no network, no emulator, no commits.
Target words re-read from `extract/ee_inplay.bin` (a 32 MB EE dump, file offset =
EE address), harness read from source, pnach dialect derived from the two
measurements this repo actually contains. Nothing below is taken on a document's
word; where a claim rests on knowledge outside the repo it is marked so.

---

## VERDICT

**PIPELINE-RISKS** — the patch can be logically correct and still be recorded as
refuted, or recorded as confirmed when it never executed. Nine risks, ranked:

| # | risk | severity | cheapest fix |
|---|---|---|---|
| P1 | **No read-back exists.** S0's "the patched word reads back over PINE" is a sentence in `seed-testing-plan.md` with no implementation. `doctor`'s `ANCHORS` is a fixed 2-tuple that does not contain either target word; no CLI command reads an arbitrary address. | critical | add both words to `ANCHORS`, or a `Trial.expect` list checked after the load-confirm and written as a result row |
| P2 | **The arm label is unverified free text.** `--arm` defaults to `"baseline"`; the result file records `writes=[]` (the patch is out-of-band), so nothing in a result file distinguishes a patched run from an unpatched one. A mislabelled arm is undetectable forever. | critical | same fix as P1 — make the read-back a row, so the arm is evidence-backed |
| P3 | **`patches/` is not the deployed artifact.** `tools/deploy_lab.sh` copies `tools/madden_lab/*.py|*.yaml` only. No script, stamp or checksum ties the repo's pnach files to the rig's cheats directory. Withdrawing a line in the repo does nothing to the rig. | critical | P1's read-back also closes this; plus copy pnach in `deploy_lab.sh` |
| P4 | **`extended` with a leading `2` is unverified in this dialect** (see §1). The repo has measured leading-`0`=8-bit and `word`=32-bit. It has never measured leading-`2`. | high | switch both lines to `word` — the only encoding this repo has *measured* to write 32 bits |
| P5 | **Recompiler reachability is unresolved** (see §2), and the dt44 null result does not discriminate the hypotheses. | high | T4, then T2 |
| P6 | **`dt_last_hold_frame` is not a hold oracle** (see §3): it is a one-sided max over 22 players of the last sample carrying a non-5 role, guarded only by a 2-sample run. Any 2-sample tear at frame 300 sets it to 300. | high | replace with a contiguity-segmented span metric; require the mover to be a blocker |
| P7 | **`max_frames=360` is a ceiling the baseline probably already hits** (see §3). `play_length` 308 is a *sample count*, not frames; the spec's own capture rates are 49–76 %. | high | settle from the recorded `span`/`reason` rows; do not raise the ceiling before T5 |
| P8 | **The snap fires ~5 wall-clock seconds after the load, not at frame 4** (see §4) — and `input_lateness` reports 0, so the health report calls it clean. | medium | record the stall; it is benign for P-content, hostile to determinism claims |
| P9 | **`require_reset=False` disables the stale-world defence on every iteration**, not just iteration 0 (see §4). | medium | set `require_reset=True`; on this trial it costs nothing |

The single change that retires P1, P2 and P3 together is one read-back row per
iteration. It is ~10 lines and it is the highest-value edit in this lane.

---

## 1. pnach syntax — what is actually established

### What the repo has measured

**Leading-`0` + `extended` = 8-bit write at the masked address. Measured.**
v1 deployed `patch=1,EE,001f6b0c,extended,2402012c`. Memory afterwards read
`0x2402002C` (`double-team-requirements.md:393`). Reconstructed: the word at
`0x001F6B0C` is `0x2402001E` (confirmed in the dump), little-endian bytes
`1E 00 02 24`; writing `data & 0xFF = 0x2C` at offset +0 gives `2C 00 02 24` =
`0x2402002C`. Exact match, no other width reproduces it. This is a genuine
first-principles derivation from an in-repo measurement.

**`word` = 32-bit at a plain address. Measured, and it is a code patch.**
`patches/14F8B841.pnach:48` — `patch=1,EE,00305258,word,10000003` — changes
`0x10400003` to `0x10000003`. The *only* differing byte is at offset **+2**
(`0x40` → `0x00`); bytes +0, +1, +3 are identical between the two values. A byte
or halfword write therefore could not produce this change at all. And
`protocol-notes.md:212` records that it **works** ("Madden NFL 2004 no longer
touches DNAS at all", with a before/after packet table). So in this fork `word`
demonstrably writes ≥ 24 bits at a plain address, on `.text`, with a runtime
effect.

### What the repo has NOT established

Nothing in this repo measures **leading-`2`**. The pnach's own line —
*"pnach `extended` width lives in the address's leading digit; 2 = 32-bit"* — is
the conventional PS2 raw-code table (`0`=8, `1`=16, `2`=32, `3`=inc/dec,
`4`=slide, `5`=copy, `6`=pointer, `7`=logic, `D`/`E`=conditional). That
convention is almost certainly right and it is **outside this repo**: no PCSX2
source tree is present anywhere on this machine, and `emu.py`'s PINE line
citations were taken from a checkout **on the rig**, which is off-limits here.
**SAYING THIS LOUDLY: leading-2 semantics are UNVERIFIED offline.**

### Corruption table for these two specific lines

Both target words were re-read from the dump and both match the patch comment:

```
0x001EF918  1e 00 03 24  = 0x2403001E   addiu v1, zero, 30      (Site A)
0x001F2108  1e 00 14 24  = 0x2414001E   addiu s4, zero, 30      (Site B)
```

The intended new values are `0x2403005A` / `0x2414005A`. **The delta is confined
to the low byte** (`0x1E` → `0x5A`). Consequences:

| parse | Site A result | correct? | caught by a full-word read-back? |
|---|---|---|---|
| 32-bit (intended) | `0x2403005A` | yes | n/a |
| 16-bit (`data & 0xFFFF` = `0x005A` over halfword `0x001E`) | `0x2403005A` | **yes, by luck** | — |
| 8-bit (`data & 0xFF` = `0x5A` over byte `0x1E`) | `0x2403005A` | **yes, by luck** | — |
| increment op (`mem += data`) | `0x48060078` | no — garbage opcode | **yes** |
| address not masked (write to `0x201EF918`, unmapped) | `0x2403001E` unchanged | no — silent no-op | **yes** |

So v2 is accidentally robust exactly where v1 was fatal: v1's intended value
`0x012C` did not fit in a byte, so the byte write silently produced 44; v2's
`0x5A` does fit, so every constant-write width converges on the intended word.
**This is luck, not design, and it must not be filed as evidence that leading-2
is 32-bit** — a read-back that passes here proves the memory content, not the
dialect. The next patch whose delta touches a high byte gets no such protection.

**Recommendation: encode both lines as `word` with plain addresses**
(`patch=1,EE,001ef918,word,2403005a`, `patch=1,EE,001f2108,word,2414005a`). Width
is then named by the type instead of hidden in the address, the failure mode of an
unknown type is a skipped patch (loud, no write) rather than a partial write, and
it is the one encoding this repo has measured end to end on `.text`.

### Is the S0 read-back a full-word check?

**There is no S0 read-back.** `tools/madden_lab/__main__.py:139-148` defines two
anchors (`0x0019BD7C`, `0x0051FDC8`); neither is a patched word, and there is no
`read`/`expect` subcommand. `Emu.read(addr, 4)` *can* return a full 32-bit word —
the primitive exists, the check does not. Any read-back done today is an operator
eyeballing a hex dump, which is precisely how v1's 44 survived long enough to be
written up as a refutation.

---

## 2. Recompiler reachability

### The repo contains both halves of the contradiction

* `docs/lab-design.md:293-295` — *"**PCSX2's EE recompiler caches compiled blocks,
  and a PINE write to code RAM does not invalidate the cached block**, so
  execution keeps running the old code. The write is real; the CPU never re-reads
  it."* Backed by a real experiment: two different code writes gave byte-identical
  metrics, and a clean baseline re-run gave the same numbers.
* `docs/code-caves.md:110-115,146` — *"`patch=1` is `PPT_CONTINUOUSLY` —
  re-applied **every vsync** … `patch=1` dirties the cave and site pages every
  frame, **forcing recompilation**"* and *"PCSX2's recompiler invalidates on
  write, so emulation is fine."*

These reconcile only if the *path* matters: PINE writes go through the raw vtlb
RAM accessor (`emu.py` cites `vtlb.cpp:326-352` for a silently-dropped write),
while the pnach applier goes through the full `memWrite*` path that trips page
protection and clears the block. That is a coherent mechanism and it is
**asserted in this repo, not verified in this pass** — the PCSX2 source is on the
rig, which is out of scope. Treat P5 as open.

### The dt44 null result discriminates nothing — and there is a third hypothesis

The brief offers two hypotheses (wrong address; cached block). The repo's own
listing supplies a third and more mundane one:

```
001f6b00  lw    v0, 764(s3)
001f6b04  lbu   a0, 0(v0)              ; the defender's current AI state id
001f6b08  beq   a0, v1, 0x001f6b18     ; v1 = 2 (ball pursuit) -> QUALIFIES, branch taken
001f6b0c  addiu v0, zero, 30           ; the patched word
001f6b10  bnel  a0, v0, 0x001f6b20     ; 30 = pass rush / engaged
```

If the doubled defender on slot 9 is in state 2, **the `beq` is taken and the
patched instruction is never reached** — 30→44 changes nothing, with no
recompiler and no wrong address required. The dt44 run is therefore consistent
with *all three* hypotheses and refutes none of them. It is not evidence that the
pipeline delivers patched code to execution, and it should stop being cited as if
it were a clean test of anything but the address's *semantics*.

### What partial evidence there is

The DNAS `word` patch is a `.text` patch that provably takes effect
(`protocol-notes.md:212`). That establishes that pnach code patches reach
execution in this fork. It does **not** establish invalidation of an
already-compiled *hot* block mid-play, which is the DT-HOLD-90 case: DNAS runs
during boot, plausibly compiled after the patch was already resident.

**If "cached block never re-executed" cannot be excluded, then both the
`0x001f6b0c` refutation and any DT-HOLD-90 result are uninterpretable.** See T2
and T4.

---

## 3. The acceptance instrument

### `dt_last_hold_frame` (baseline 43) is not a sound oracle for "the hold extended"

It is `max(last_frame)` over `_holds`, which is built from `_role_frames` — a
**set** of all sample indices where `dt_role ∈ {0,1,2}`, with no contiguity test
(review D1). Three ways it moves without any hold getting longer:

1. **A second, later pairing.** Partly mitigated: DT-2 gates registration to the
   first 60 post-snap frames — re-derived here from the dump,
   `0x001F651C = 0x2C42003C` = `sltiu v0, v0, 60`. So a *registry* entry cannot
   open after frame 60. **Unverified**: that role bytes 0/1/2 are written only
   under that gate. A "nothing else writes this" negative is exactly `CLAUDE.md`
   rule 4's forbidden class and was not censused in this pass.
2. **A 2-sample tear on any of 22 players.** `_assigned` needs `run=2` and
   `_holds` needs `len(frames) >= 2`; review D20 already records that a two-frame
   tear clears both. A torn non-5 role byte at frame 300 sets the metric to 300 —
   a one-sided max with no upper guard, on a `doc`-grade byte with no domain check
   (review D13 — the test is `!= 5`, so 4, 6, 0xFF or a stale 0 all register).
3. **The population includes role 2.** `_holds` iterates every assigned entity,
   so the *defender's* last frame can be the max. Review D3 shows a doubled
   defender re-registering late is a live phenomenon.

It is an **end-time-of-last-dt-event** metric. It is monotone in the number and
lateness of pairings, not in their length. Extending a hold to ~90 ticks *should*
move it, so it is not useless — but it cannot distinguish that from three of the
above, and its acceptance target ("past ~100") is stated in the units of a
defective instrument.

### `carrier_yards` (-0.70) confounds

* **One-sided latch, no persistence guard.** `deepest = max(ys)` over
  `game.carrier_y`. Every other "did this happen" test in the file requires two
  consecutive samples; this one does not. A single torn read inflates it
  permanently. The stated acceptance — *"-0.70 → anything else"* — is therefore
  satisfied by one bad frame on a metric the docs say is deterministic across
  every iteration of every run.
* **Subject change.** Review D7: `carrier_y` follows `ball.carrier or
  ball.last_carrier`, and pre-snap `+0xB4` is null. Early samples can be a
  different body from later ones, and `max` is taken across the change. On a run
  play this is mild; it is not zero, and frames 0–3 are in the window.
* **Ceiling-bounded, see below** — it measures the deepest point within the first
  360 post-snap frames, not the play's outcome.
* Does the patch touch it? Only through blocking, which is the intended channel.
  The confound is instrumental, not causal.

### `max_frames=360` and the stop condition

`PlayEnded(patience=8)` is **dead code** (review D20): the runner only samples
after the clock has been observed to move, sampled `frames_since_snap` is strictly
increasing, so `current <= self._last` never holds. Iterations end on `max_frames`
or on `clock_rewound`. The declared `until_name="whistle"` never fires.

`play_length` is `float(len(samples))` — a **sample count** labelled "frames". The
baseline "308-frame play" is 308 *samples*. The loop breaks at
`frame + 1 >= 360`, so the span is at most 359; with the spec's own measured
capture rates of 49–76 % inside the hold windows, 308 samples implies a span at or
very near the 360 ceiling. **The baseline is therefore probably already truncated
at the ceiling, not ended by the play.** That is testable at zero cost: the
recorded `iteration` rows carry `span` and `reason` — grep the baseline JSONL for
`"reason"`. Do that before anything else in this section is acted on.

What a truncated iteration reads:

* `status` stays **`"ok"`** (only the `reason` string changes), so metrics are
  computed and pooled. Nothing in `compare.py` or `analyze.py` filters or flags
  truncation; a run where one arm truncates more often than the other is compared
  silently.
* `carrier_yards`: a max over a shortened window → **biased low**. This is
  directional against the patch — the success case (a back who breaks through and
  is still running at frame 359) is exactly the case the ceiling clips. It also
  makes a *post-frame-359 catastrophe* — the seed plan's "extreme regression",
  e.g. a fumble return — structurally invisible.
* `max_snap_frame`, the declared control, reads 359 and cannot distinguish
  truncation from a play that genuinely ended there.
* `dt_*`: unaffected; a 90-tick hold starting inside the 60-frame gate ends well
  before 359.

**Do not simply raise `max_frames`.** If the whistle *parks* the counter rather
than rewinding it, a longer ceiling turns iterations into `StallError` →
`status="stalled"` → **`metrics` is never populated** (`runner.py:981`), silently
discarding whole iterations. Settle it with T5 first.

### Proposed acceptance, designed around the defects

| id | gate | pass | fail |
|---|---|---|---|
| **A0** | *pipeline*: both words read back over PINE as exactly `0x2403005A` and `0x2414005A`, all 32 bits, after the load-confirm and before the snap, recorded as a row every iteration | exact match | anything else → iteration invalid, run aborts |
| **A1** | *execution proof*: sample `reselect_timer` (`+0x432`, u16 — code-grade since the review) on all 22 players. At least one offensive lineman holds **≥ 61** for ≥ 2 consecutive samples inside the first 60 post-snap frames | ≥ 61 seen | never > 30 → the patched immediate did not execute, regardless of memory content |
| **A2** | *behaviour, D1-proof*: new metric `dt_hold_max_run` — segment `_role_frames` into contiguous runs, split on any role change and any gap > 2 ticks, measure each run as `last - first + 1`, take the max over runs whose role ∈ {0,1}. Baseline from the project's own fixture is **15**, not 30 | ≥ 45 on ≥ 2 of 3 frozen-seed iterations | < 30 |
| **A3** | *outcome, two-sided*: `carrier_yards` with a 2-sample persistence guard on `carrier_y` | movement counts only if \|Δ\| ≥ 0.5 yd from −0.70 | HALT and replay the seed if < −5.0 or > +12.0 |
| **A4** | *truncation control*: `reason` and `span` reported beside every metric | identical `reason` distribution across arms | mixed → arms not comparable |
| **A5** | *regression*: slot 7 `pass_protection.py`, **with A1's `+0x432` sampling added** — the patch's own comment concedes Site A fires on pass plays | unchanged within its own card | any movement is a finding, not a freebie |

A1 is the important one. It is the only proposed gate that is **mechanistic**:
`+0x432` is initialised to `30 − blockRating/16` ∈ [15,30] on the baseline and to
`90 − blockRating/16` ∈ [75,90] under the patch, so reading ≥ 61 on any blocker is
a direct observation of the patched constant having executed. It separates "the
write landed" (A0) from "the code ran" (A1) from "it changed the game" (A2/A3) —
three questions the current instrument collapses into one number that cannot
answer any of them. It costs one extra field in `FIELDS`.

`dt_last_hold_frame` should be **demoted to a reported diagnostic**, not an
acceptance gate, until D1's segmentation lands.

---

## 4. State interaction: the load / patch / snap window

### There is no unpatched window worth worrying about — but not for the stated reason

The real timing is not "load → confirm → snap at frame 4". Traced through the
runner:

1. `load_and_confirm` → `_wait_until(loaded_state_is_pre_snap, 15 s)`.
2. `resync()`, `base_counter = 0`, loop starts. Frame 0 is sampled.
3. `clock.tick()` blocks. The clock is `WorldFrameClock(world)` with the
   **default `timeout_s=5.0`** (`runner.py:601` — no caller overrides it), and
   `frames_since_snap` is parked at 0 for the whole pre-snap window. So `tick()`
   spins for a full **5 wall-clock seconds**, raises `StallError`, and the runner
   catches it and calls `player.force_earliest()` — *the snap is fired by fiat,
   five seconds after the load.*

Corroboration: the spec declares `SECONDS_PER_ITERATION = 12.0`; 5 s of stall plus
~6 s of play (360 frames at 60 fps) plus overhead is exactly that.

Consequences:

* **Memory-content risk is nil.** `place=1` re-applies at every vsync, so the two
  words are re-written ~300 times before the first post-snap frame, and the double
  team registers at frame 2. No iteration can run with unpatched *memory*.
* **Recompiler risk is unchanged** — it is not a timing problem, and §2 stands.
* **`input_lateness` reports 0.** `force_earliest` calls `apply(4)`, so
  `late = 4 - 4 = 0`. The health report's "scripted input landed N frames late"
  warning is structurally incapable of seeing this. The instrument that exists to
  catch exactly this says clean.
* **A new determinism hazard.** The pre-snap emulated window is
  `5 s × actual emulator fps`, i.e. wall-clock dependent and unrecorded. If the
  rig runs at 100 % for one iteration and 85 % for the next, the two plays begin
  from different pre-snap states. Mitigating evidence: the v1 pnach records
  "3 runs, identical" and `carrier_yards` "-0.70 on every iteration of every run",
  so metric-level reproducibility survives it in practice. Digest-level evidence
  is unavailable offline — result files are gitignored (`*.jsonl`).

### Can iteration 0 differ, and is it detectable?

Detectable, yes — the runner writes a per-iteration `digest` over the whole sample
stream, in every run, precisely so this question stays answerable. But **nothing
compares iteration 0 against the rest**: `run()` prints only "N distinct sample
streams", and `compare` treats identical digests as `n_effective = 1`. The signal
is recorded and never read.

**Yes, S0 should require N ≥ 3 with iteration 0 discarded by default.** Under a
frozen seed, iterations 1..N should be byte-identical; `digest(0) ≠ digest(1) =
digest(2)` is a clean, already-recorded first-iteration anomaly, and discarding
iteration 0 costs 12 seconds. Where it matters most is not memory content (see
above) but recompiler warm-up — the one mechanism that *would* single out the
first iteration after cheats are enabled.

### A defect found while tracing this

`double_team.py`'s `LoadConfirm(..., require_reset=False, ...)`. In
`_confirm_edge` the first branch is
`if not vacuous or not getattr(confirm, "require_reset", True):` — with
`require_reset=False` that condition is **always true**, so the clock-edge
requirement is disabled on *every* iteration, not just the vacuous case. The
defence that exists because a run once photographed the outgoing world is switched
off for this trial. On this spec the predicate (snap counter 0 **and** QB/FB on
I-Form spots within 0.35 yd) is very unlikely to be vacuously true after a
completed iteration, so the practical exposure is low — but the guard is off, and
turning it on costs nothing: when the predicate is already false pre-load, both
branches behave identically. **Set `require_reset=True`.**

---

## 5. The withdrawn v1 file, and re-introduction paths

**In the repo: clean.** `patches/14F8B841.dt-duration-10x.pnach` has no live
`patch=` line. The only occurrence is line 40, prefixed `// WITHDRAWN: `. It
cannot be parsed as a patch by any plausible parser, for two independent reasons:
a `//`-comment strip empties the line, and a naive `split('=')` yields the key
`"// WITHDRAWN: patch"`, which is not `"patch"`. The `comment=` line also carries
"WITHDRAWN … Do not deploy."

Census of every `patch=` line in `patches/` (live, i.e. not `//`-prefixed):

| file | live lines |
|---|---|
| `14F8B841.pnach` | 1 — `patch=1,EE,00305258,word,10000003` (DNAS bypass, unrelated region) |
| `14F8B841.dt-hold-90.pnach` | 2 — the patch under review |
| `14F8B841.dt-duration-10x.pnach` | **0** |
| `lead-blocker-candidates.pnach` | **0** (all commented) |
| `42F9D5AF.pnach` | 0 for this CRC (different game) |

`0x001f6b0c` appears nowhere as a live patch line, in `patches/` or anywhere else
in the tree. Address-leading-digit histogram over all live lines: every one is a
leading-`0` plain address with type `word`. **The two `extended` lines under
review are the only leading-`2` addresses in the entire repository** — which is
the same thing as saying the encoding has never been exercised here (§1).

**On the rig: cannot be confirmed, and the repo has no lever on it.** No script
copies `patches/*.pnach` anywhere; `deploy_lab.sh` handles the harness only. A
stale `14F8B841.dt-duration-10x.pnach` with its live v1 line, or a hand-edited
copy, may still sit in the cheats directory. Two aggravating factors:

* The multi-file naming (`14F8B841.<name>.pnach`) implies the build loads every
  `<CRC>*.pnach` in the cheats directory and merges them, so a stale file is
  additive and silent, and `14F8B841.pnach`'s DNAS line is live at the same time.
  *(That PCSX2 loads `<CRC>*.pnach` rather than only `<CRC>.pnach` is inferred
  from the naming convention in use here; it is not verified in this pass.)*
* Which cheats are *enabled* is per-game emulator config on the rig, invisible
  from the repo. The requirements doc already notes that the log's
  "Found 1 cheats in …" says nothing about what landed — but it does give a free
  arity check: with both files present the count should be **≥ 3**, not 1.

Only A0's read-back closes this. A read-back of `0x001F6B0C` (expect
`0x2402001E`) should ride along with it, so a resurrected v1 announces itself.

---

## Discriminating tests (designed here, to be run later on the rig)

Observe the H-2 live-session check before any of these.

**T1 — dialect probe. Settles §1 permanently, ~1 minute, touches nothing.**
A throwaway pnach with a single line writing a sentinel with four distinct nonzero
bytes to an unused scratch word (`.bss`, verified unreferenced):
`patch=1,EE,2XXXXXXX,extended,DEADBEEF`. Read all four bytes back over PINE.
`DEADBEEF` → leading-2 is 32-bit. `000000EF` → 8-bit. `0000BEEF` → 16-bit.
Unchanged → the address was not masked and the write went nowhere. This is the
only test that can establish the dialect, because the DT-HOLD-90 words converge on
the same result under every width (§1) and therefore carry no information about it.

**T2 — run the patch backwards. Discriminates "instrument cannot see it" from
"code did not execute", and is immune to every metric defect found in §3.**
Deploy `30 → 2` instead of `30 → 90` (`24030002` / `24140002`), one iteration per
arm. A *shorter* hold cannot be manufactured by a contiguity bug, a torn role byte
or a late second pairing — all of those inflate. If holds collapse toward 1–2
frames, the pipeline delivers patched code to execution and the instrument can see
duration changes; if nothing moves, no positive result from `30 → 90` would ever
have meant anything. Run this **before** the real acceptance run.

**T3 — recompiler / first-iteration discriminator.** With cheats enabled from
boot, run N=5 on slot 9 and compare the recorded per-iteration digests:
`digest(0)` against `digest(1..4)`. Then, in a second session, boot with cheats
**off**, run one iteration, enable cheats without rebooting, and run three more —
if the first post-enable iteration differs from the two after it, a stale compiled
block was executing. Costs ~2 minutes; uses only data the runner already writes.

**T4 — direct execution proof (also acceptance gate A1).** Add `reselect_timer`
(`+0x432`) to `FIELDS` and read it on all 22 players. Baseline must top out at 30;
patched must show ≥ 61 on at least one blocker for ≥ 2 consecutive samples. This
is the cheapest possible answer to "did the patched immediate execute", it is
independent of every duration metric, and it doubles as an empirical check on
review D5's self-defeat hazard (a kind-7 player showing 75–90 and then breaking
immediately is D5, visible).

**T5 — end-of-play probe, gates any change to `max_frames`.** One iteration with
`max_frames=900`. Record the terminating `reason`. `clock_rewound` → the counter
re-arms and a higher ceiling is safe. `stalled` → the counter parks at the whistle
and a higher ceiling **discards the iteration's metrics entirely**
(`runner.py:981`), so the ceiling must stay and the truncation must be handled
another way.

**T0 — free, do it first, no rig needed.** Grep the recorded baseline JSONL for
`"reason"` and `"span"` in the `iteration` rows. It settles P7 (is the baseline
already truncated?) in ten seconds from data that already exists.

---

## Could not establish

* **Leading-`2` `extended` semantics in this fork.** No PCSX2 source tree exists
  anywhere on this machine; the only citations in the harness were taken from a
  checkout on the rig, which is out of scope for this pass. The convention is
  near-certain and it is not verified here. T1 settles it.
* **Whether the pnach applier invalidates the EE recompiler's cached blocks.**
  Asserted twice in the repo (`lab-design.md`, `code-caves.md`) with a plausible
  mechanism (raw vtlb accessor for PINE vs the full `memWrite*` path for patches),
  never verified. The DNAS patch proves pnach code patches *reach execution*; it
  does not prove invalidation of an already-hot block.
* **Whether a savestate load flushes the recompiler.** Not determinable offline.
  Note the benign asymmetry: if it does not flush and the block was compiled from
  *patched* memory, the patch persists across the load anyway. The hostile case
  needs a block compiled before the first pnach application that then survives
  every subsequent per-vsync write.
* **Whether `dt_role` values 0/1/2 are written only under DT-2's 60-frame gate.**
  The gate itself is re-confirmed from the dump (`sltiu v0, v0, 60`). A full
  census of writes to `+0x437` was not run in this pass, and a "nothing else
  writes this" negative is exactly the class `CLAUDE.md` rule 4 forbids taking on
  trust. Until it is run, `dt_last_hold_frame > 60` is *probably* a persisting
  pairing rather than a new one — probably, not provably.
* **Whether the baseline iteration terminated on `max_frames` or `clock_rewound`.**
  The arithmetic points at the ceiling; result files are gitignored, so it could
  not be read. T0.
* **What is actually in the rig's cheats directory, and which entries are
  enabled.** Structurally unknowable from the repo; only A0's read-back closes it.
* **Whether the 5-second pre-snap stall perturbs the play.** Metric-level
  reproducibility is documented ("3 runs, identical"); digest-level evidence is in
  gitignored result files.

---

*Lane 5 of a five-lane hostile review, 2026-08-11. Static only: no rig, no
network, no emulator, no commits. This file is the only one written.*

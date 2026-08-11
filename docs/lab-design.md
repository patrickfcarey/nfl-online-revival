# `madden_lab` — the automation harness

Design contract, 2026-08-10. Written before the code so four builders can
work in parallel against fixed seams.

**The job:** run a gameplay experiment in Madden 2004 under PCSX2 hundreds
of times unattended — snap to whistle — and produce numbers you can compare
between a baseline and a patched build. Everything this project has claimed
about blocking, coverage and the pass rush is currently a static derivation.
This is how those claims start being measured.

## Why a savestate is the unit of work

A savestate is a complete 32 MB image of console RAM **and** a `Screenshot.png`
of the frame it was taken on. That gives the harness three things at once:

* **A reset button.** Load the same state and the trial starts from an
  identical world every time.
* **Offline observation.** Every address in `docs/` can be read out of the
  file with no emulator running at all.
* **Eyes.** The embedded screenshot lets the automation see what it did.
  Claude cannot watch the screen; it can read a PNG.

Verified already: the state file is a ZIP, `eeMemory.bin` is zstd (method 93)
and maps 1:1 onto our virtual addresses — the live image matched the ELF's
loadable segment to 99.86%, and `0x0019BD7C` read `24050032` exactly as the
static analysis says.

## Layers, and who owns what

Each layer talks only to the one below it through the named seam.

### 1. `emu.py` — emulator control
Wraps PINE (`tools/pine.py` already does reads/writes). Adds the rest of the
protocol: status, pause, resume, save state, load state, title/CRC.

```python
class Emu:
    def read(addr, size=4) -> int          # exists
    def write(addr, value, size=4) -> None # exists
    def read_bytes(addr, count) -> bytes   # exists
    def status() -> Status                 # running | paused
    def pause() / resume() -> None
    def save_state(slot: int) -> None
    def load_state(slot: int) -> None
    def game_crc() -> int                  # must equal 0x14F8B841
```

`load_state` is the reset primitive the whole harness turns on. If this
build's PINE lacks a command, say so loudly rather than emulating it.

### 2. `pad.py` — input
The harness has to snap the ball. Two candidate mechanisms; pick one on
evidence and record why in the module docstring.

* **A virtual gamepad via `uinput`.** PCSX2 binds it like real hardware.
  Robust, survives emulator restarts, needs a one-time binding.
* **Poking the pad buffer in EE memory.** More deterministic in principle,
  but the emulator writes that buffer through SIO2 emulation every frame, so
  a poke can be overwritten. Prove it holds before choosing it.

```python
class Pad:
    def press(button, frames=2) -> None
    def hold(button) -> None ; def release(button) -> None
    def sequence(script) -> None    # [(frame_offset, button, duration), ...]
```

Input must be expressible **relative to a frame**, because "snap, wait 12
frames, throw" is the shape of every experiment.

### 3. `world.py` + `addresses.yaml` — observation
The typed view of the game. **The address map is data, not code** — this
doubles as the machine-readable address index the project has wanted, and it
must cite the doc each address came from so a wrong number is traceable.

```yaml
player:
  stride: 0x...            # confirm from the live array
  fields:
    position:      {off: 0xB04, type: u8,  source: fb-wr-blocking.md}
    running_style: {off: 0xB07, type: u8,  source: hb-vision-and-moves.md}
    engagement:    {off: 0x3E0, type: u32, source: block-cycle.md}
    block_mode:    {off: 0x3F0, type: u32, source: fb-wr-blocking.md}
    state_chain:   {off: 0x2FC, type: ptr, source: fb-wr-blocking.md}
    ratings_base:  {off: 0xB70, type: u16[21], source: slider-behavior.md}
```

```python
class World:
    def players() -> list[Player]     # both sides
    def ball() -> Ball                # state, carrier, position
    def phase() -> int                # pre-snap / live / dead
    def frames_since_snap() -> int
    def options() -> dict[str, int]   # the 131-entry table, by fourcc
```

**Known-good anchors** (already read out of a live image): option table
`0x0051FFD8` u16[131] with fourcc names at `0x0051FDC8`, index 0 = `OQLN`,
7 and 8 both `OFMO`. The player-array pointer at `0x00600E48` is **null at
the main menu** — it only populates in a game, which is the first thing to
confirm against an in-play state.

### 4. `trial.py` / `runner.py` — experiments
A trial is declarative so it can be version-controlled and diffed:

```python
@dataclass
class Trial:
    state: str                 # savestate to load
    setup: list[Write]         # slider values, rating overrides, patch words
    script: list[InputEvent]   # what the operator's hands would do
    sample: SampleSpec         # which fields, which frames
    stop: StopCondition        # play over, or N frames
```

The runner loads the state, applies setup, runs the script, samples every
frame into tidy rows, and stops on the condition. Output is one row per
(iteration, frame, entity) — long format, because every analysis wants it.

**Determinism is a research question, not an assumption.** The same state
plus the same inputs may still diverge, because the engine draws from an RNG
whose state lives in memory. Find out. If it diverges, either seed the RNG
before each trial or treat iterations as samples from a distribution and say
so — both are fine, silently assuming the first is not.

### 5. `compare.py` — regression
Two result sets in, a verdict out: per-metric deltas with enough statistics
to distinguish a real change from noise. Given trials are random draws,
report an effect size and a spread, never a bare difference of means.

## The CLI

```
python -m tools.madden_lab doctor          # PINE up? right game? addresses sane?
python -m tools.madden_lab snapshot        # dump the world right now
python -m tools.madden_lab record --frames 300 --out run.jsonl
python -m tools.madden_lab trial --spec experiments/lead_blocker.py -n 200
python -m tools.madden_lab compare baseline.jsonl patched.jsonl
python -m tools.madden_lab shot --out frame.png   # screenshot via savestate
```

`doctor` matters more than it looks: every wrong answer this project has
produced came from an address that had drifted or was never right. It should
refuse to run when the CRC is not `0x14F8B841`.

## The operator

Some things cannot be read from memory — whether a block *looked* right,
whether the receiver visibly ran the wrong route. For those the harness
prints an explicit `OPERATOR:` prompt describing what to watch for and what
to report back. Treat operator time as the scarcest resource in the system:
never ask a human for something a memory read could answer, batch the asks,
and always say which trial number the question refers to.

## Non-negotiables

* **Never write to game memory outside a trial's declared `setup`.** An
  undeclared poke makes every result in the run unreproducible.
* **Assert the CRC before every run.** Wrong build, wrong addresses, silent
  nonsense.
* **Record the provenance of every row** — savestate, spec, iteration, git
  revision — or the numbers cannot be defended later.
* **Read-only by default.** `--write` must be explicit.

---

## First measurement: is a trial reproducible? (2026-08-11)

Three loads of one mid-play savestate, no input sent, 180 frames each.

`verify-determinism` reported **DIVERGENT**, and that verdict is wrong — or
rather, it answers a different question than the one asked. Its first
difference was `frames_since_snap 74 != 179` at sample 0: the *sampling start
point* varied, because the load confirmation only waits for the counter to be
non-zero and the play is already several frames along by then. Comparing
sample-ordinal to sample-ordinal therefore compares different moments of the
play.

Re-aligned on the game's own clock, the picture inverts:

| field | agree | differ | identical |
|---|---|---|---|
| position | 4488 | 0 | 100% |
| block_mode | 4483 | 5 | 99.89% |
| ai_state | 4479 | 9 | 99.80% |
| engagement | 4479 | 9 | 99.80% |
| **xyz** | 3563 | **925** | **79.4%** |

Largest positional difference: **0.266 units**.

**The signature is read skew, not engine nondeterminism.** Layer 1 established
that PINE reads take no lock against the EE thread, so a snapshot is not
atomic: the clock is read, then the players, and the game advances in between.
Positions change every frame and so expose that skew; discrete state changes
rarely and so mostly agrees, with the ~0.2% that disagree being exactly what a
±1-frame sampling error looks like at a transition.

So the honest conclusions are:

* **Discrete behaviour is reproducible to ~99.8%.** Experiments whose metrics
  are engagement kinds, block modes or AI states need a handful of iterations,
  not hundreds. Given the operator's observation below, the residual 0.2% is
  measurement error too, so even that is a floor rather than a limit.
* **Positions carry ~0.27 units of measurement noise** that is ours, not the
  game's. Use them in aggregate — distances, medians, whether a gap opened —
  never as a frame-by-frame equality.
* **Determinism is confirmed — by the operator, not by the instrument.**
  Watching the same three loads, the operator reported: *"it played out mostly
  the same for me, the running back takes a few steps then does the same spin
  move and is tackled the same spot."* That is far stronger evidence than the
  field comparison above. A spin move is a discrete AI decision drawn from a
  probability roll, and the tackle spot is hundreds of frames of accumulated
  physics and decisions; neither repeats by chance. The savestate restores the
  RNG state, and the play replays.

  So the engine is reproducible and **every difference the harness measured is
  its own**. The instrument still cannot prove this on its own — that needs an
  atomic snapshot, which needs a pause PINE does not have — which is precisely
  why the operator channel exists. This is the first finding it has produced,
  and it corrected the tool.

Two harness defects follow, both worth fixing before the numbers are trusted:

1. `verify-determinism` must align on the game clock before comparing, and
   should report per-field agreement rather than one all-or-nothing verdict.
   As written it will call every real experiment divergent.
2. `LoadConfirm` should wait for a *specific* clock value, not merely
   non-zero, so every iteration starts at the same point in the play.

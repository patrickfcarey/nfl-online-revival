"""The iteration loop: load, poke, press, sample, stop, reduce, repeat.

## The seams this layer needs

Everything below is expressed as a `Protocol`, and the runner is constructed
with instances of them. That is not ceremony -- it is what lets the tests in
`tests/test_madden_lab_runner.py` exercise this entire file on a laptop with no
emulator, no PINE socket and no PS2 game, and it is what stops layers 1-3 from
having to exist before layer 4 can be finished.

Where the design contract in `docs/lab-design.md` did not pin a call down, the
narrowest thing that works is declared here and flagged `SEAM REQUEST` in the
docstring, so the four builders can reconcile rather than discover.

## Determinism

Every iteration is digested -- a stable hash over its whole sample stream --
and the digest goes in the `iteration` row of *every* run, not just of a
`verify-determinism` run. It costs nothing and it means the question "were
these iterations independent draws or 200 copies of the same play?" is always
answerable from the result file alone, months later, by anyone.

Both worlds are supported and neither is assumed:

* **If trials are reproducible**, `verify_determinism` proves it, and
  `compare` sees identical digests, refuses to treat n=200 as 200 samples, and
  says so. A deterministic run has n_effective = 1 no matter how long it ran,
  and a harness that reports p < 0.001 off it is lying with confidence.
* **If they diverge**, the digests differ, `verify_determinism` reports the
  exact (frame, entity, field) where the two runs first parted company -- which
  is a genuinely useful bug-hunting output in its own right -- and the
  iterations are treated as samples from a distribution.

A trial may declare `rng_seed` writes to try to force the first case. The three
generators behind `0x0056E358` (`slider-behavior.md`) mean there is no single
seed word, so this is left to the spec rather than guessed at here.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:  # pragma: no cover - typing shim for 3.7
    from typing import Protocol
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore

try:
    from . import EXPECTED_CRC
except ImportError:  # pragma: no cover - the package constant is shared with
    # layers 1-3, who also assert it; a local fallback keeps layer 4 runnable
    # if the shared module is mid-edit.
    EXPECTED_CRC = 0x14F8B841

from .align import (PHASE_WINDOW, SUBTICK_CEIL, FieldAgreement,  # noqa: F401
                    TickIndex, agreement, as_axes, is_continuous, tick_index,
                    verdict)
from .results import (KIND_ASK, KIND_EVENT, KIND_ITERATION, KIND_METRIC,
                      KIND_RUN, KIND_SAMPLE, Provenance, git_revision,
                      new_run_id)
from .trial import Frame, OperatorAsk, Samples, Trial, entity_id, _read_field


# --------------------------------------------------------------------------
# Seams
# --------------------------------------------------------------------------


class EmuLike(Protocol):
    """Layer 1. Contract methods, plus what the contract did not name.

    SEAM REQUEST 1 -- `load_state` takes a slot int in the design contract, but
    a `Trial` names its savestate as a string, because a slot number is not
    provenance: "slot 3" in August is not "slot 3" in October. The runner calls
    `load_state(state)` with whatever the trial declared and expects layer 1 to
    accept either an int slot or a path.

    SEAM REQUEST 2 -- `screenshot(path)` is optional. The design contract notes
    that a savestate embeds `Screenshot.png`, which is how the harness gets
    eyes; if layer 1 exposes it, the runner attaches a still to every operator
    prompt, which converts an interruption into something reviewable later.
    Absent, prompts are text-only and nothing breaks.

    `wait_until(predicate, timeout)` is layer 1's confirmation seam and the
    runner's answer to the fire-and-forget load. It is used, never a sleep;
    the runner synthesises a polling equivalent only if layer 1 does not
    provide it, and records that it had to.

    `writable` reflects layer 1's read-only-by-default construction. The runner
    checks it in preflight rather than discovering it on the first setup write,
    four hours into a run.
    """

    def read(self, addr: int, size: int = 4) -> int: ...
    def write(self, addr: int, value: int, size: int = 4) -> None: ...
    def load_state(self, state: Any) -> None: ...
    def game_crc(self) -> int: ...


class PadLike(Protocol):
    """Layer 2, reduced to the two calls that cannot block.

    SEAM REQUEST 3 -- the runner drives the schedule itself and needs only
    `hold` and `release`. It deliberately does *not* use `press(button,
    frames)` or `sequence(script)`: both imply the pad owns a clock and blocks
    for a duration, and a blocked pad is a frame the runner did not sample.
    `hold`/`release` are instantaneous by construction, so the runner can keep
    a single authoritative frame clock. `release_all` is a convenience; the
    runner tracks held buttons and can synthesise it.
    """

    def hold(self, button: str) -> None: ...
    def release(self, button: str) -> None: ...


class WorldLike(Protocol):
    """Layer 3, plus the one accessor the contract is missing.

    SEAM REQUEST 4 -- `frame_counter()`: a free-running frame count that ticks
    before the snap. The contract gives `frames_since_snap()`, which is reset
    at play init (`0x001f7034`, see `lead-blocker-targeting.md`) and therefore
    is not a clock during the pre-snap window -- which is exactly the window in
    which the input script has to press the snap button. The runner needs a
    monotonic tick to advance on; anything monotonic will do, including a
    global frame or vsync counter. Without it, `WorldFrameClock` falls back to
    `frames_since_snap` and the pre-snap portion of every script is untimed.

    SEAM REQUEST 5 -- entity fields are read by name off whatever
    `players()` returns. `trial._read_field` accepts an attribute, a mapping,
    or a `.fields` dict, so layer 3 can pick; what it must guarantee is a
    stable `index` per player, because rows are joined on it across
    iterations.

    SEAM REQUEST 8 -- **met**: `Player.snapshot(fields)`, one batched read per
    entity per frame. Sampling 22 players x 4 fields one field at a time would
    be 88 PINE round trips a frame, which at even 0.1 ms each exceeds a frame's
    budget -- the harness would sample every third frame while believing it
    sampled every one. It also *narrows* the tearing window: reads are not
    synchronised with emulation, so a read spread over 88 round trips can span
    several frames of game time and produce a row describing a world that never
    existed. Batching shrinks that window; nothing available can close it.

    `phase()` is in the design contract and **does not exist**: layer 3
    established that no play-phase word is in the game (`0x0015ADA0` returns a
    property of the called play, not of where the play has got to) and its
    `phase()` raises `NotReadable` by design. Nothing here samples it; use
    `frames_since_snap()` for the clock and `ai_state` for what a player is
    doing.
    """

    def players(self) -> Sequence[Any]: ...
    def ball(self) -> Any: ...
    def frames_since_snap(self) -> int: ...


class ClockLike(Protocol):
    """The frame clock. `tick` blocks until the frame count moves, and returns
    how many frames it moved by -- which is 1 on a healthy run and more when
    the harness fell behind the emulator."""

    def now(self) -> int: ...
    def tick(self) -> int: ...


class PreflightError(RuntimeError):
    """Refusing to run. Always a wrong-build or undeclared-write problem."""


class StallError(RuntimeError):
    """The frame counter stopped moving. Paused, crashed, or in a dialog."""


class LoadUnconfirmed(RuntimeError):
    """The savestate did not visibly load. Every row after this would be stale."""


# --------------------------------------------------------------------------
# Clocks
# --------------------------------------------------------------------------


class WorldFrameClock:
    """Polls the game's own frame counter. The only clock we can trust.

    Wall-clock sleeping is not an option: PCSX2 does not run at a fixed
    frame rate under load, and a harness that samples every 16.6 ms produces
    rows labelled with frame numbers it invented. Polling a counter in EE
    memory costs one PINE round trip and is right by construction.

    `dropped` counts frames the poll loop missed. It is reported per iteration
    rather than hidden, because "we sampled every frame" is a claim the results
    depend on and this is the only evidence for or against it.
    """

    def __init__(self, world: WorldLike, poll_s: float = 0.0005,
                 timeout_s: float = 5.0, sleep=time.sleep, monotonic=time.monotonic):
        self._world = world
        self._poll_s = poll_s
        self._timeout_s = timeout_s
        self._sleep = sleep
        self._monotonic = monotonic
        self._read = self._pick_counter(world)
        self._last = self._read() or 0
        self.dropped = 0
        self.rewinds: List[Tuple[int, int]] = []
        self.degraded = not callable(getattr(world, "frame_counter", None))

    @staticmethod
    def _pick_counter(world: WorldLike):
        counter = getattr(world, "frame_counter", None)
        if callable(counter):
            return counter
        # SEAM REQUEST 4 unmet: fall back and accept that the pre-snap window
        # is untimed, rather than refuse to run at all.
        return world.frames_since_snap

    def now(self) -> int:
        return self._last

    def resync(self) -> int:
        """Re-read the counter now, discarding the cached value.

        The cache is set at construction and advanced by `tick()`, so right
        after a savestate load it still holds the *outgoing* world's reading.
        An iteration that takes its frame origin from that cache numbers the
        whole run against a world that no longer exists.
        """
        self._last = self._read() or 0
        return self._last

    def tick(self) -> int:
        """Block until the counter moves; return the (signed) delta.

        A negative delta is reported, not smoothed over. An earlier version
        treated any backward movement as `+1` on the theory that a reset
        counter is "not a rewind" -- and that smoothing is exactly what hid a
        savestate load landing mid-iteration: the clock jumped 179 -> 74, the
        mask turned it into 180, and every frame number after it was fiction.
        A rewind means the world was replaced (a load landed) or the play
        ended (the counter re-arms at 0). Both are events the caller must see;
        neither is noise. `rewinds` keeps the evidence.
        """
        deadline = self._monotonic() + self._timeout_s
        while True:
            current = self._read()
            if current is None:
                # Layer 3 returns None for frames_since_snap when there is no
                # play. Not an error and not a tick -- just nothing yet.
                current = self._last
            if current != self._last:
                delta = current - self._last
                if delta > 1:
                    self.dropped += delta - 1
                if delta < 0:
                    self.rewinds.append((self._last, current))
                self._last = current
                return delta
            if self._monotonic() >= deadline:
                raise StallError(
                    "frame counter stuck at %d for %.1fs" % (self._last, self._timeout_s))
            self._sleep(self._poll_s)


class ManualClock:
    """A clock the caller advances. Tests, and offline replay."""

    def __init__(self, start: int = 0):
        self._frame = start
        self.dropped = 0
        self.rewinds: List[Tuple[int, int]] = []
        self.degraded = False

    def now(self) -> int:
        return self._frame

    def tick(self) -> int:
        self._frame += 1
        return 1


# --------------------------------------------------------------------------
# Input scheduling
# --------------------------------------------------------------------------


class ScriptPlayer:
    """Turns a list of `InputEvent` into hold/release calls on frame boundaries.

    The runner owns this rather than the pad because the runner owns the clock.
    Overlapping presses of the same button are coalesced by extending the
    release frame -- two events pressing X at frames 10 and 11 for 2 frames
    each is one hold and one release at frame 13, not a release at 12 that the
    game may or may not see as a second press.

    **Catch-up is mandatory, not a nicety.** There is no frame-advance in the
    PINE protocol, so the runner polls a counter the emulator moves on its own
    schedule and will sometimes observe frame 10 and then frame 13. An
    implementation keyed on exact frame equality would silently never press the
    snap button. Everything due at or before the observed frame fires, and the
    lateness is reported so a run where the input landed four frames late is
    distinguishable in the result file from one where it did not.
    """

    def __init__(self, events: Sequence[Any], pad: PadLike):
        self._pending: List[Any] = sorted(events, key=lambda e: e.frame)
        self._pad = pad
        self._release_at: Dict[str, int] = {}
        self.max_lateness = 0

    def apply(self, frame: int) -> List[str]:
        """Do everything due at or before `frame`. Returns a log, for the rows."""
        log: List[str] = []
        for button, when in sorted(self._release_at.items()):
            if when <= frame:
                self._pad.release(button)
                log.append("release:%s" % button)
        self._release_at = {b: w for b, w in self._release_at.items() if w > frame}

        while self._pending and self._pending[0].frame <= frame:
            event = self._pending.pop(0)
            late = frame - event.frame
            self.max_lateness = max(self.max_lateness, late)
            if event.button not in self._release_at:
                self._pad.hold(event.button)
                log.append("hold:%s%s" % (event.button, "+%d" % late if late else ""))
            self._release_at[event.button] = max(
                self._release_at.get(event.button, 0), frame + event.duration)
        return log

    def release_all(self) -> None:
        for button in sorted(self._release_at):
            self._pad.release(button)
        self._release_at.clear()

    def force_earliest(self) -> List[str]:
        """Fire the next pending event now, ignoring its scheduled frame.

        SEAM REQUEST 4, resolved by admitting what the clock cannot do. The
        only frame counter this harness has resets at play init and is parked
        at zero for the whole pre-snap window -- which is exactly when the
        snap must be pressed. A script that waits for frame 4 of a clock that
        starts at the snap is waiting for an event only the script itself can
        cause. So when the clock stalls with input still pending, the runner
        fires the earliest event by fiat: the press is what starts the clock.
        Its scheduled frame is honoured for the *release* arithmetic, so a
        3-frame press still releases at frame `event.frame + 3` once the
        counter is moving.
        """
        if not self._pending:
            return []
        return self.apply(self._pending[0].frame)

    @property
    def unfired(self) -> int:
        """Events the play ended before reaching. Non-zero is a spec problem."""
        return len(self._pending)


# --------------------------------------------------------------------------
# The operator queue
# --------------------------------------------------------------------------


@dataclass
class QueuedAsk:
    ask: OperatorAsk
    iteration: int
    still: str = ""


class OperatorQueue:
    """Batches questions for a human and prints them as one block.

    Three modes. `batch` (the default) prints nothing until the run ends, so an
    overnight run of 200 iterations is one interruption rather than 200.
    `live` prints after each flagged iteration, for the case where the operator
    is sitting there anyway. `none` records the ask rows and prints nothing at
    all, which is what you want when the run is unattended and the questions
    will be answered from the saved stills tomorrow.

    Arms are named by an opaque label in the prompt -- "arm A", never
    "baseline" or "patched". An operator who knows which build they are
    watching is not a measuring instrument, and blocking is exactly the kind of
    judgment that bends to expectation. The true arm is still in the row's
    provenance; it is just not on the operator's screen.
    """

    MODES = ("batch", "live", "none")

    def __init__(self, mode: str = "batch", printer=print, blind_label: str = "A",
                 run_id: str = "", result_path: str = ""):
        if mode not in self.MODES:
            raise ValueError("ask mode must be one of %s" % (", ".join(self.MODES),))
        self.mode = mode
        self._print = printer
        self.blind_label = blind_label
        self.run_id = run_id
        self.result_path = result_path
        self.queued: List[QueuedAsk] = []
        self._counts: Dict[str, int] = {}
        self._flushed = 0

    def consider(self, trial: Trial, iteration: int, still: str = "") -> List[QueuedAsk]:
        """Queue whichever asks are due for this iteration."""
        raised: List[QueuedAsk] = []
        for ask in trial.asks:
            asked = self._counts.get(ask.id, 0)
            if ask.due(iteration, asked):
                self._counts[ask.id] = asked + 1
                item = QueuedAsk(ask=ask, iteration=iteration, still=still)
                self.queued.append(item)
                raised.append(item)
        if self.mode == "live" and raised:
            self._emit(raised)
            self._flushed = len(self.queued)
        return raised

    def flush(self) -> int:
        """Print everything not yet printed. Returns how many were printed."""
        outstanding = self.queued[self._flushed:]
        if outstanding and self.mode != "none":
            self._emit(outstanding)
        self._flushed = len(self.queued)
        return len(outstanding)

    def _emit(self, items: Sequence[QueuedAsk]) -> None:
        self._print("")
        self._print("=" * 72)
        self._print("OPERATOR: %d question(s) for run %s, arm %s"
                    % (len(items), self.run_id or "?", self.blind_label))
        self._print("=" * 72)
        for item in items:
            self._print("")
            self._print("  iteration %d   [%s]" % (item.iteration, item.ask.id))
            self._print("  WATCH:  %s" % item.ask.watch_for)
            if item.still:
                self._print("  STILL:  %s" % item.still)
            self._print("  ASK:    %s" % item.ask.question)
            self._print("  ANSWER: %s" % " / ".join(item.ask.choices))
            self._print("     python -m tools.madden_lab answer --run %s \\"
                        % (self.result_path or "<run.jsonl>"))
            self._print("            --ask %s --iteration %d --value <%s>"
                        % (item.ask.id, item.iteration, "|".join(item.ask.choices)))
        self._print("")
        self._print("  (answers append to the same file as the automatic rows)")
        self._print("=" * 72)


# --------------------------------------------------------------------------
# Results of a run
# --------------------------------------------------------------------------


@dataclass
class IterationResult:
    index: int
    frames: int
    digest: str
    status: str
    reason: str
    dropped: int
    wall_ms: float
    metrics: Dict[str, Optional[float]] = field(default_factory=dict)
    samples: Optional[Samples] = None
    #: How the savestate load was proven: declared | fingerprint |
    #: unverified-first | unverified-unchanged | skipped.
    confirmed: str = "skipped"
    #: Game frames the iteration covered, as opposed to samples taken.
    span: int = 0
    input_lateness: int = 0
    unfired_input: int = 0


@dataclass
class RunSummary:
    run_id: str
    trial: str
    spec_digest: str
    arm: str
    iterations: List[IterationResult]
    asks_raised: int
    rows_written: int
    result_path: str

    @property
    def ok(self) -> List[IterationResult]:
        return [it for it in self.iterations if it.status == "ok"]

    @property
    def digests(self) -> List[str]:
        return [it.digest for it in self.ok]

    @property
    def deterministic(self) -> Optional[bool]:
        """True/False if there is enough evidence, None if there is not."""
        digests = self.digests
        if len(digests) < 2:
            return None
        return len(set(digests)) == 1


@dataclass
class Divergence:
    iteration_a: int
    iteration_b: int
    frame: Optional[int]
    entity: str
    field_name: str
    value_a: Any
    value_b: Any

    def describe(self) -> str:
        if self.frame is None:
            return "iterations %d and %d differ in length" % (
                self.iteration_a, self.iteration_b)
        return "frame %d  %s.%s  %r != %r" % (
            self.frame, self.entity, self.field_name, self.value_a, self.value_b)


@dataclass
class DeterminismReport:
    trial: str
    repeats: int
    digests: List[str]
    identical: bool
    first_divergence: Optional[Divergence]
    seeded: bool
    fields: List[FieldAgreement] = field(default_factory=list)
    common_ticks: int = 0
    uncertified: int = 0

    @property
    def verdict(self) -> str:
        """The three-way answer the ordinal comparison could not give.

        Delegated to `align.verdict` so the offline `agree` command reaches
        the same conclusion from the same evidence; only the "no fields to
        align on" case is decided here, because only a live run knows whether
        the streams were byte-identical.
        """
        if not self.fields:
            return "REPRODUCIBLE" if self.identical else "UNDECIDABLE"
        return verdict(self.fields)

    def describe(self) -> str:
        lines = ["trial %s, %d repeats%s" % (
            self.trial, self.repeats, ", RNG seeded" if self.seeded else "")]
        lines.append(self.verdict)
        if self.fields:
            lines.append("aligned on the game clock: %d common ticks, "
                         "%d uncertified samples excluded"
                         % (self.common_ticks, self.uncertified))
            for f in self.fields:
                lines.append(f.describe())
            real = [e for f in self.fields for e in f.examples]
            if real:
                lines.append("REAL divergences (engine, not instrument):")
                lines.extend("  " + e for e in real[:10])
                lines.append("  -> iterations are samples from a distribution; "
                             "use `compare` and mind the effect size.")
            elif any(f.phase or f.subtick for f in self.fields):
                lines.append("  Every disagreement is explained by the "
                             "instrument -- adjacent-tick phase or sub-tick "
                             "read noise -- not the engine. Treat iterations "
                             "as replays; positions belong in aggregates.")
            else:
                lines.append("  Iterations are replays, NOT independent samples; "
                             "n_effective is 1 however many you run.")
        elif self.identical:
            lines.append("  all %d sample streams are byte-identical." % self.repeats)
            lines.append("  Iterations are therefore NOT independent samples; "
                         "n_effective is 1 however many you run.")
        else:
            lines.append("  no certified common ticks to align on, so phase "
                         "noise cannot be told from real divergence here.")
            if self.first_divergence:
                lines.append("  first ordinal difference: "
                             + self.first_divergence.describe())
            lines.append("  Treat iterations as samples from a distribution; "
                         "use `compare` and mind the effect size.")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------


class Runner:
    """Executes trials. Owns the clock, the schedule and the result stream."""

    def __init__(self, emu: EmuLike, world: WorldLike, pad: Optional[PadLike] = None,
                 clock: Optional[ClockLike] = None, allow_writes: bool = False,
                 printer=print, still_dir: str = ""):
        self.emu = emu
        self.world = world
        self.pad = pad
        self.clock = clock or WorldFrameClock(world)
        self.allow_writes = allow_writes
        self._print = printer
        self.still_dir = still_dir

    # -- preflight ---------------------------------------------------------

    def preflight(self, trial: Optional[Trial] = None) -> List[str]:
        """Refuse early and loudly. Returns advisory notes; raises on refusal.

        The CRC check is the one that has actually saved this project: every
        wrong answer it has produced came from an address that had drifted or
        was never right, and a wrong build produces plausible numbers rather
        than an error.
        """
        notes: List[str] = []
        crc = self.emu.game_crc()
        if crc != EXPECTED_CRC:
            raise PreflightError(
                "wrong game: CRC 0x%08X, expected 0x%08X (Madden NFL 2004, "
                "SLUS_207.52). Every address in docs/ is wrong for this build."
                % (crc, EXPECTED_CRC))
        if trial is not None and trial.all_writes and not self.allow_writes:
            raise PreflightError(
                "%d declared write(s) but --write was not given. Read-only is "
                "the default:\n  %s" % (
                    len(trial.all_writes),
                    "\n  ".join(w.describe() for w in trial.all_writes)))
        players = list(self.world.players())
        if not players:
            raise PreflightError(
                "world.players() is empty. The player-array pointer at "
                "0x00600E48 is null at the main menu -- the savestate must be "
                "taken in a live play, not in a menu.")
        if len(players) != 22:
            notes.append("world.players() returned %d, not 22" % len(players))
        if getattr(self.clock, "degraded", False):
            notes.append(
                "no world.frame_counter(): clocking off frames_since_snap, so "
                "input scheduled before the snap is untimed (SEAM REQUEST 4)")

        # Layer 1 is read-only unless constructed writable=True. Discovering
        # that on the first setup write is discovering it four hours in.
        if trial is not None and trial.all_writes and self.allow_writes:
            if getattr(self.emu, "writable", True) is False:
                raise PreflightError(
                    "--write was given but the emulator was constructed "
                    "read-only. Layer 1 defaults to read-only by design; the "
                    "runner has to opt in at connect time, not at write time.")
        if trial is not None and trial.state and trial.load_confirm is None:
            notes.append(
                "no LoadConfirm: PINE's load_state is fire-and-forget and "
                "cannot report failure, so this run falls back to a world "
                "fingerprint that cannot verify the first iteration and cannot "
                "detect a load that restores an identical world. Declare one.")
        if players and not callable(getattr(players[0], "snapshot", None)):
            notes.append(
                "players expose no snapshot(fields): every field is a separate "
                "PINE round trip, so a frame's read spans real time and tears "
                "(SEAM REQUEST 8)")
        return notes

    # -- one iteration -----------------------------------------------------

    def load_and_confirm(self, trial: Trial, sink=None, iteration: int = 0) -> str:
        """Reset the world, and prove it. Returns how the proof was obtained.

        `load_state` returns as soon as PINE has *queued* the load on another
        thread, so for some unknown number of milliseconds afterwards every
        read still describes the previous trial. Worse, a load that failed --
        empty slot, corrupt file -- is reported on the emulator's OSD and never
        on the socket, so it is indistinguishable from success at this end.

        A run that samples through that window produces clean, plausible,
        entirely stale numbers, and nothing in the result file can reveal it
        later. So this blocks until the loaded world is positively identified,
        and raises if it never is. No sleep: a sleep turns a detectable failure
        into an undetectable one, which is the wrong direction.
        """
        if not trial.state or trial.state.startswith("<"):
            return "skipped"  # live-observation trials never reset the world

        confirm = trial.load_confirm
        # Everything that describes the OUTGOING world must be read before the
        # load is issued. The first version of the edge watcher took its
        # baseline afterwards, which makes a fast-landing load invisible --
        # the watcher wakes up inside the new world and sees a calm clock.
        vacuous = False
        baseline_tick = None
        if confirm is not None:
            try:
                vacuous = bool(confirm.satisfied(self.emu))
            except Exception:
                pass
            read_tick = getattr(self.world, "frames_since_snap", None)
            if callable(read_tick):
                baseline_tick = read_tick()

        self.emu.load_state(self._resolve_state(trial))

        if confirm is not None:
            return self._confirm_edge(trial, confirm, vacuous, baseline_tick,
                                      sink=sink, iteration=iteration)

        # No LoadConfirm at all: say so, and claim nothing. An earlier version
        # compared world fingerprints here and reported "fingerprint" when
        # they differed -- but a fingerprint of a *running* world changes
        # every frame all by itself, so the comparison confirmed any live
        # world, loaded or stale. A confirmation that a running world can
        # forge by existing is not a confirmation; the honest label is that
        # this iteration's world was never verified.
        return "unverified-first" if iteration == 0 else "unverified"

    def _confirm_edge(self, trial: Trial, confirm, vacuous: bool,
                      baseline_tick: Optional[int], sink=None,
                      iteration: int = 0) -> str:
        """Accept the predicate only on evidence the world changed hands.

        The failure this exists for, observed rather than imagined: the first
        determinism run confirmed with `counter > 0` while the *previous*
        iteration's play was still running. The predicate was true before the
        load was even issued, confirmation was instant, and the first samples
        photographed the outgoing world -- tick 179, the prior iteration's
        final frame -- until the load landed and the clock jumped back to 74.
        The analyzer then compared those stale samples against real ones and
        called a bitwise-reproducible engine divergent.

        The contract that follows:

        * A predicate that was **false before the load** proves itself -- its
          own false -> true transition is the handover. Nothing extra.
        * A predicate that was **already true** proves nothing alone. On
          iteration 0 it is accepted anyway, with a warning row in the result
          stream -- nothing ran before iteration 0, so the stale world it
          could be describing is the one the operator just set up, which is
          the world the trial wants. On every later iteration -- exactly
          where the observed failure lived, because a finished iteration
          leaves its world satisfying most predicates -- it is believed only
          after the game clock shows a **per-poll discontinuity**: a backward
          jump, or a forward leap no single poll gap can produce. On this
          transport polls outpace game ticks several-fold, so a live stale
          world moves the clock by at most a tick or two per poll; only a
          load landing moves it far, in either direction, between two reads.

        A stillness arm ("the counter held still, so this must be a freshly
        loaded static state") was considered and rejected: stillness measured
        in polls is a statement about the transport's cadence, not the
        world's, and the same count that proves a parked pre-snap state on a
        fast socket is produced by two adjacent reads of a *running* play on
        a slow one. A trial whose loaded state cannot produce a clock edge --
        a pre-snap state re-loaded over an ended play, both parked at 0 --
        must confirm on something the reset visibly changes instead: a
        window predicate (`LoadConfirm(addr=..., lo=..., hi=...)`) on a value
        the outgoing world has run past, or a `check` on formation geometry.
        The refusal message says so, because the alternative -- accepting --
        is how a run samples a world that no longer exists and files it as
        data.
        """
        if not vacuous or not getattr(confirm, "require_reset", True):
            ok = self._wait_until(confirm.satisfied, confirm.timeout_s)
            if not ok:
                raise LoadUnconfirmed(
                    "savestate %r did not load: %s never became true within "
                    "%.1fs. PINE reports load failures on the emulator's "
                    "screen only, so check there -- an empty slot looks "
                    "exactly like this."
                    % (trial.state, confirm.describe(), confirm.timeout_s))
            return "declared"

        if iteration == 0:
            if sink is not None:
                sink.write(KIND_EVENT, iteration=iteration,
                           event="load-confirm predicate was already true "
                                 "before the load was issued; accepted on "
                                 "iteration 0 only, where the world it might "
                                 "be describing is the operator's setup. "
                                 "Later iterations will demand a clock edge.")
            ok = self._wait_until(confirm.satisfied, confirm.timeout_s)
            if not ok:
                raise LoadUnconfirmed(
                    "savestate %r did not load: %s never became true within %.1fs."
                    % (trial.state, confirm.describe(), confirm.timeout_s))
            return "declared-vacuous-first"

        read_tick = getattr(self.world, "frames_since_snap", None)
        if not callable(read_tick):
            raise LoadUnconfirmed(
                "savestate %r cannot be confirmed: the predicate (%s) was "
                "already true in the outgoing world and there is no game "
                "clock to witness the handover. Use a predicate the loaded "
                "state makes true and the outgoing world makes false -- a "
                "window (LoadConfirm lo=/hi=) usually is one."
                % (trial.state, confirm.describe()))

        state = {"last": baseline_tick, "edge": False}

        def proven(emu) -> bool:
            tick = read_tick()
            last = state["last"]
            if tick is not None and last is not None:
                delta = tick - last
                if delta < 0 or delta > 8:
                    state["edge"] = True
            if tick is not None:
                state["last"] = tick
            if not state["edge"]:
                return False
            try:
                return bool(confirm.satisfied(emu))
            except Exception:
                return False  # a predicate probing a half-loaded world may raise

        ok = self._wait_until(proven, confirm.timeout_s)
        if not ok:
            raise LoadUnconfirmed(
                "savestate %r did not load: %s%s within %.1fs. The predicate "
                "was already true before the load, so only a clock edge could "
                "prove the handover; if this state legitimately cannot move "
                "the clock (pre-snap over pre-snap), confirm on something the "
                "reset visibly changes -- a window (LoadConfirm lo=/hi=) or a "
                "geometry check."
                % (trial.state, confirm.describe(),
                   "" if state["edge"] else
                   " (no clock discontinuity was ever seen)",
                   confirm.timeout_s))
        return "declared-edge"

    @staticmethod
    def _resolve_state(trial: Trial) -> Any:
        """Turn the trial's savestate identity into what layer 1 will accept.

        Layer 1's `load_state` takes a PCSX2 slot as a u8 and nothing else --
        it has no path form. `Trial.state` is the filename, because that is the
        provenance the result file needs; `Trial.state_slot` is the slot the
        operator has that file loaded into. Neither alone is sufficient: a slot
        number in a result file from August means nothing in October, and a
        path cannot be handed to the emulator.
        """
        if trial.state_slot is not None:
            return trial.state_slot
        text = trial.state.strip()
        if text.isdigit():
            return int(text)
        if text.lower().startswith("slot:") and text[5:].strip().isdigit():
            return int(text[5:].strip())
        raise PreflightError(
            "trial %r names savestate %r but no slot. Layer 1's load_state "
            "takes a PCSX2 slot (0-255), not a path (SEAM REQUEST 1): set "
            "state_slot= to the slot that file is loaded into."
            % (trial.name, trial.state))

    def _wait_until(self, predicate, timeout_s: float) -> bool:
        """Prefer layer 1's own wait; poll only if it has none.

        Layer 1's `wait_until` passes the `Emu` to the predicate and raises on
        timeout rather than returning False, so both are normalised here. A
        `LoadConfirm` predicate is written to take the emulator, which is the
        only thing it could usefully be given.
        """
        waiter = getattr(self.emu, "wait_until", None)
        if callable(waiter):
            try:
                waiter(predicate, timeout_s)
                return True
            except TypeError:
                pass  # a different signature; fall through to polling
            except Exception:
                return False  # EmuTimeout, or the predicate itself failing
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if predicate(self.emu):
                return True
            time.sleep(0.002)
        return predicate(self.emu)

    def run_iteration(self, trial: Trial, index: int, sink=None,
                      keep_samples: bool = False) -> IterationResult:
        started = time.time()
        dropped_before = getattr(self.clock, "dropped", 0)

        frames: List[Frame] = []
        digest = hashlib.sha256()
        status, reason = "ok", ""
        confirmed = "skipped"
        player: Optional[ScriptPlayer] = None
        base_counter = self.clock.now()
        frame = 0
        samples_taken = 0
        last_sampled: Optional[int] = None
        deadline = time.time() + trial.stop.timeout_s

        try:
            confirmed = self.load_and_confirm(trial, sink=sink, iteration=index)
            self._apply_writes(trial)
            # The frame origin is taken *after* the confirmed load, so frame 0
            # is the first frame of the reset world rather than the last frame
            # of the previous one. `resync` matters: the clock caches its last
            # reading, and that cache still holds the outgoing world's value --
            # the exact stale base that once made frame numbers negative.
            resync = getattr(self.clock, "resync", None)
            if callable(resync):
                resync()
            base_counter = self.clock.now()
            if trial.script and self.pad:
                player = ScriptPlayer(trial.script, self.pad)
            # A stop condition that needs history -- "the snap counter stopped
            # advancing" -- keeps state, and the Trial is a single object
            # reused for every iteration. Without this, iteration 1 inherits
            # iteration 0's history and stops on its first frame.
            reset = getattr(trial.stop.until, "reset", None)
            if callable(reset):
                reset()

            while True:
                frame = self.clock.now() - base_counter
                if player is not None:
                    for entry in player.apply(frame):
                        if sink is not None:
                            sink.write(KIND_EVENT, iteration=index, frame=frame,
                                       event=entry)
                if trial.sample.due(frame, last_sampled):
                    snapshot = self._sample(trial, frame)
                    if snapshot.tick is not None and snapshot.certified:
                        # The batch's own clock reading outranks the poll
                        # loop's: it was taken atomically with the values.
                        snapshot.index = snapshot.tick - base_counter
                        frame = snapshot.index
                    last_sampled = frame
                    samples_taken += 1
                    frames.append(snapshot)
                    self._digest_frame(digest, snapshot)
                    if sink is not None:
                        self._emit_samples(sink, index, snapshot, samples_taken - 1)
                    stop_reason = trial.stop.reached(snapshot)
                    if stop_reason:
                        reason = stop_reason
                        break
                if frame + 1 >= trial.stop.max_frames:
                    reason = "max_frames"
                    break
                if time.time() > deadline:
                    status, reason = "timeout", "wall clock %.0fs" % trial.stop.timeout_s
                    break
                try:
                    moved = self.clock.tick()
                except StallError:
                    # A parked clock plus pending input is not a stall: it is
                    # the pre-snap window, and the pending press is the thing
                    # that will start the clock (SEAM REQUEST 4). Fire it and
                    # go back to waiting. A parked clock with nothing left to
                    # send is a real stall -- paused, crashed, or a dialog --
                    # and propagates as before.
                    if player is not None and player.unfired:
                        for entry in player.force_earliest():
                            if sink is not None:
                                sink.write(KIND_EVENT, iteration=index,
                                           frame=frame,
                                           event=entry + " (forced: clock "
                                           "parked pre-snap)")
                        continue
                    raise
                if moved < 0:
                    # The counter rewound mid-run: the play ended (re-armed at
                    # 0) or a state landed over us. Either way the world this
                    # iteration was measuring no longer exists; continuing
                    # would append another world's rows to it.
                    reason = "clock_rewound (play ended, or a state loaded over the run)"
                    break
        except LoadUnconfirmed as exc:
            status, reason = "load_unconfirmed", str(exc)
        except StallError as exc:
            status, reason = "stalled", str(exc)
        except Exception as exc:  # keep the run alive; one bad iteration is data
            status, reason = "error", "%s: %s" % (type(exc).__name__, exc)
        finally:
            if player is not None:
                player.release_all()

        samples = Samples(frames)
        metrics: Dict[str, Optional[float]] = {}
        if status == "ok":
            for metric in trial.metrics:
                try:
                    metrics[metric.name] = _as_float(metric.reduce(samples))
                except Exception as exc:
                    metrics[metric.name] = None
                    if sink is not None:
                        sink.write(KIND_EVENT, iteration=index, event="metric_error",
                                   metric=metric.name, detail="%s: %s"
                                   % (type(exc).__name__, exc))

        result = IterationResult(
            index=index,
            frames=len(frames),
            digest=digest.hexdigest()[:24],
            status=status,
            reason=reason,
            dropped=getattr(self.clock, "dropped", 0) - dropped_before,
            wall_ms=(time.time() - started) * 1000.0,
            metrics=metrics,
            samples=samples if keep_samples else None,
            confirmed=confirmed,
            span=frame,
            input_lateness=player.max_lateness if player else 0,
            unfired_input=player.unfired if player else 0,
        )
        if sink is not None:
            for name, value in metrics.items():
                sink.write(KIND_METRIC, iteration=index, metric=name, value=value)
            # `span` is the game frames the play covered; `frames` is how many
            # of them were actually sampled. They differ whenever the poll loop
            # fell behind, and the gap is the honest measure of how much of the
            # play this row set did not see.
            sink.write(KIND_ITERATION, iteration=index, digest=result.digest,
                       frames=result.frames, span=result.span, status=status,
                       reason=reason, dropped=result.dropped,
                       load_confirmed=confirmed,
                       input_lateness=result.input_lateness,
                       unfired_input=result.unfired_input,
                       wall_ms=round(result.wall_ms, 1))
            sink.flush()
        return result

    def _apply_writes(self, trial: Trial) -> None:
        """The only path to game memory. Seeds first, then setup.

        Layer 1's `Emu` refuses writes unless it was constructed
        `writable=True`, which makes "never write outside a trial's declared
        setup" structural rather than conventional. This runner is the one
        component that legitimately opts in, it does so only for the writes the
        trial declared, and the opt-in is carried explicitly from the command
        line -- no code path here can enable it on its own.
        """
        if not trial.all_writes:
            return
        if not self.allow_writes:
            raise PreflightError("refusing undeclared-write run without --write")
        for write in trial.all_writes:
            self.emu.write(write.addr, write.value, write.size)

    #: How many times a torn sample is re-read before being emitted as torn.
    #: One retry usually suffices -- the batch is microseconds and a tick is
    #: 16.7 ms -- but a rig struggling to hold frame rate can tear repeatedly,
    #: and looping forever would turn a slow emulator into a hung run.
    CERTIFY_RETRIES = 3

    def _sample(self, trial: Trial, frame_index: int) -> Frame:
        certified = self._sample_certified(trial, frame_index)
        if certified is not None:
            return certified
        values: Dict[Tuple[str, str], Any] = {}
        for selector in trial.sample.entities:
            for ordinal, entity in enumerate(self._entities(selector.kind)):
                fields = self._read_entity(entity, selector.fields)
                if not selector.matches(entity, fields):
                    continue
                key = entity_id(selector.kind, entity, ordinal)
                for name in selector.fields:
                    values[(key, name)] = fields.get(name)
        return Frame(frame_index, values)

    def _sample_certified(self, trial: Trial, frame_index: int) -> Optional[Frame]:
        """One clock-bracketed batch for the whole frame, or None to fall back.

        The per-entity path below reads each player in its own round trip, so
        a "frame" is really ~23 photographs taken over several milliseconds of
        game time. Against a deterministic engine that smear *is* the noise:
        the first determinism run measured positions 79% reproducible and
        every disagreement was bitwise-equal to an adjacent tick -- pure
        sampling phase. One batch, bracketed by the game clock, replaces the
        smear with a certificate: tick unchanged across the batch means the
        sample lies within one game frame, provably.

        Falls back (returns None) when the world cannot batch -- fake worlds
        in tests, or a selector this path does not cover (`ball` resolves
        through a per-frame pointer chase that a fixed spec list cannot
        express). A torn sample -- clock moved mid-batch -- is retried a few
        times, then emitted marked `certified=False` so the analyzer can
        exclude rather than silently compare it.
        """
        world = self.world
        if not callable(getattr(world, "certified_batch", None)):
            return None
        player_selectors = [s for s in trial.sample.entities if s.kind == "player"]
        game_selectors = [s for s in trial.sample.entities if s.kind == "game"]
        if len(player_selectors) + len(game_selectors) != len(trial.sample.entities):
            return None                       # a ball selector needs the old path

        players = list(self._entities("player"))
        parts: List[Tuple[Any, Tuple[str, ...]]] = []
        keys: List[Tuple[Any, Any, str]] = []  # (selector, entity, key)
        for selector in player_selectors:
            for ordinal, entity in enumerate(players):
                # Side/index filters are attribute-only and free; a position
                # filter would need the fields first, so it takes the fallback.
                if selector.positions:
                    return None
                if not selector.matches(entity):
                    continue
                parts.append((entity, tuple(selector.fields)))
                keys.append((selector, entity,
                             entity_id("player", entity, ordinal)))
        if not parts:
            return None

        tick = after = None
        decoded: List[Dict[str, Any]] = []
        for _attempt in range(self.CERTIFY_RETRIES):
            tick, after, decoded = world.certified_batch(parts)
            if tick is not None and tick == after:
                break
        if tick is None:
            return None                       # no play manager; nothing to certify
        is_certified = tick == after

        values: Dict[Tuple[str, str], Any] = {}
        for (selector, _entity, key), fields in zip(keys, decoded):
            for name in selector.fields:
                values[(key, name)] = fields.get(name)
        for selector in game_selectors:
            key = entity_id("game", world, 0)
            for name in selector.fields:
                if name == "frames_since_snap":
                    # The certified tick IS this reading; a separate read
                    # would reintroduce the very skew the batch removes.
                    values[(key, name)] = tick
                else:
                    values[(key, name)] = _read_field(world, name)
        return Frame(frame_index, values, tick=tick, certified=is_certified)

    @staticmethod
    def _read_entity(entity: Any, fields: Sequence[str]) -> Dict[str, Any]:
        """One batched read per entity where layer 3 offers one.

        Layer 3's `Player.snapshot(fields)` pulls every named field in a single
        batched PINE request; going field by field would be one round trip
        each, so a 22-player frame would be ~88 requests. That is not merely
        slow -- at ~0.1 ms a trip it exceeds a frame's budget, so the harness
        would sample every third frame while believing it sampled every one,
        and the "snapshot" would be smeared across several frames of game time.
        Batching does not close the tearing window (reads are not synchronised
        with emulation) but it is the only thing that narrows it.

        A field the address map does not define reads as None rather than
        raising: a run four hours in should not die because a spec named a
        field layer 3 has not mapped yet.
        """
        snapshot = getattr(entity, "snapshot", None)
        if callable(snapshot):
            try:
                batch = snapshot(list(fields))
            except TypeError:
                batch = None  # World.snapshot() takes no field list
            except Exception:
                batch = None  # an unmapped field in the batch; fall back
            if isinstance(batch, dict):
                return {name: batch.get(name, _read_field(entity, name))
                        for name in fields}
        return {name: _read_field(entity, name) for name in fields}

    def _entities(self, kind: str) -> Sequence[Any]:
        if kind == "player":
            return list(self.world.players() or ())
        if kind == "ball":
            ball = self.world.ball()
            # Null between plays, and null at the main menu. An absent ball is
            # a missing reading, not an exception.
            return [ball] if ball is not None else []
        if kind == "game":
            return [self.world]
        raise ValueError("unknown entity kind %r" % kind)

    @staticmethod
    def _digest_frame(digest, frame: Frame) -> None:
        for (entity, name) in sorted(frame.values):
            digest.update(("%d|%s|%s|%r\n" % (
                frame.index, entity, name, frame.values[(entity, name)])).encode("utf-8"))

    @staticmethod
    def _emit_samples(sink, iteration: int, frame: Frame, ordinal: int) -> None:
        # `frame` is the game's own frame counter offset from the load, not a
        # loop index. The distinction is the difference between a real
        # measurement and a fiction: with no frame-advance in the protocol the
        # loop runs at whatever rate PINE round trips allow, and numbering rows
        # 0,1,2,... would label a sample taken three frames later as the next
        # frame. `sample` keeps the loop ordinal so the two can be compared.
        for (entity, name) in sorted(frame.values):
            sink.write(KIND_SAMPLE, iteration=iteration, frame=frame.index,
                       sample=ordinal, entity=entity, field=name,
                       value=frame.values[(entity, name)],
                       tick=frame.tick, certified=frame.certified)

    # -- a whole run -------------------------------------------------------

    def run(self, trial: Trial, iterations: int, sink, arm: str = "baseline",
            ask_mode: str = "batch", blind_label: str = "A",
            progress_every: int = 25) -> RunSummary:
        notes = self.preflight(trial)
        run_id = getattr(getattr(sink, "provenance", None), "run_id", "") or new_run_id()
        queue = OperatorQueue(mode=ask_mode, printer=self._print,
                              blind_label=blind_label, run_id=run_id,
                              result_path=getattr(sink, "path", ""))

        sink.write(KIND_RUN, iteration=None, trial=trial.name,
                   question=trial.question, iterations_requested=iterations,
                   max_frames=trial.stop.max_frames,
                   writes=[w.describe() for w in trial.all_writes],
                   script=["f%d %s x%d" % (e.frame, e.button, e.duration)
                           for e in trial.script],
                   metrics=[m.name for m in trial.metrics],
                   asks=[a.id for a in trial.asks],
                   cannot_conclude=list(trial.cannot_conclude),
                   load_confirm=(trial.load_confirm.describe()
                                 if trial.load_confirm else None),
                   notes=notes, started_at=time.time())
        for note in notes:
            self._print("note: %s" % note)

        results: List[IterationResult] = []
        consecutive_bad_loads = 0
        for index in range(iterations):
            result = self.run_iteration(trial, index, sink=sink)
            results.append(result)

            # A run that cannot reset is not producing 200 samples of anything;
            # it is producing one stale world 200 times, and every row after
            # the first failure is worthless. Stop rather than fill a file.
            if result.status == "load_unconfirmed":
                consecutive_bad_loads += 1
                if consecutive_bad_loads >= 3:
                    sink.write(KIND_EVENT, iteration=index, event="run_aborted",
                               detail="three consecutive unconfirmed savestate loads")
                    self._print(
                        "\nABORTED after %d iterations: three savestate loads in a "
                        "row could not be confirmed.\n  %s\n  Check the emulator's "
                        "on-screen display -- PINE never reports a load failure."
                        % (index + 1, result.reason))
                    break
            else:
                consecutive_bad_loads = 0
            still = self._capture_still(run_id, index) if trial.asks else ""
            if result.status == "ok":
                for item in queue.consider(trial, index, still=still):
                    sink.write(KIND_ASK, iteration=index, ask=item.ask.id,
                               question=item.ask.question,
                               watch_for=item.ask.watch_for,
                               why_not_memory=item.ask.why_not_memory,
                               choices=list(item.ask.choices), still=item.still)
            if progress_every and (index + 1) % progress_every == 0:
                self._print("  %d/%d iterations (%d ok)"
                            % (index + 1, iterations,
                               sum(1 for r in results if r.status == "ok")))
        queue.flush()
        sink.flush()

        summary = RunSummary(run_id=run_id, trial=trial.name,
                             spec_digest=trial.digest(), arm=arm,
                             iterations=results, asks_raised=len(queue.queued),
                             rows_written=getattr(sink, "rows_written", 0),
                             result_path=getattr(sink, "path", ""))
        self._report_health(summary)
        self._report_determinism(summary)
        return summary

    def _report_health(self, summary: RunSummary) -> None:
        """Say what the run did not see. Nobody reads this out of the file.

        Three numbers decide whether the metrics above are worth anything, and
        all three are invisible in a table of means: how many resets were
        actually confirmed, how much of each play went unsampled, and whether
        the input landed on the frame the spec asked for.
        """
        ok = summary.ok
        if not ok:
            return
        unverified = [r for r in ok if r.confirmed.startswith("unverified")]
        if unverified:
            self._print(
                "warning: %d/%d iterations could not confirm the savestate load. "
                "Those rows may describe the previous iteration's world."
                % (len(unverified), len(ok)))
        dropped = sum(r.dropped for r in ok)
        span = sum(r.span for r in ok)
        sampled = sum(r.frames for r in ok)
        if span:
            self._print("coverage: %d samples over %d game frames (%.0f%%), "
                        "%d frames the poll loop never saw."
                        % (sampled, span, 100.0 * sampled / span, dropped))
        late = max(r.input_lateness for r in ok)
        if late:
            self._print("warning: scripted input landed up to %d frames late; "
                        "the snap did not happen when the spec says." % late)
        unfired = sum(r.unfired_input for r in ok)
        if unfired:
            self._print("warning: %d scripted input event(s) never fired -- the "
                        "play ended before their frame." % unfired)

    def _capture_still(self, run_id: str, index: int) -> str:
        """Best-effort screenshot for an operator prompt (SEAM REQUEST 2)."""
        shot = getattr(self.emu, "screenshot", None)
        if not callable(shot) or not self.still_dir:
            return ""
        path = "%s/%s-%04d.png" % (self.still_dir.rstrip("/"), run_id, index)
        try:
            shot(path)
        except Exception:
            return ""
        return path

    def _report_determinism(self, summary: RunSummary) -> None:
        verdict = summary.deterministic
        if verdict is None:
            return
        if verdict:
            self._print(
                "determinism: all %d sample streams identical -- these are NOT "
                "%d independent samples (n_effective = 1)."
                % (len(summary.ok), len(summary.ok)))
        else:
            distinct = len(set(summary.digests))
            self._print("determinism: %d distinct sample streams in %d iterations."
                        % (distinct, len(summary.ok)))

    # -- the determinism experiment ---------------------------------------

    def verify_determinism(self, trial: Trial, repeats: int = 3,
                           sink=None) -> DeterminismReport:
        """Run the same trial N times and find out whether it repeats.

        This is the question the whole harness rests on, so it gets a command
        of its own rather than being inferred from a normal run. Three repeats
        is the default because one proves nothing and two cannot distinguish
        "reproducible" from "coincidence on a coarse metric" -- the digest is
        over the whole stream, so agreement of three is strong evidence.
        """
        if repeats < 2:
            raise ValueError("determinism needs at least 2 repeats")
        self.preflight(trial)
        results = [self.run_iteration(trial, index, sink=sink, keep_samples=True)
                   for index in range(repeats)]
        usable = [r for r in results if r.status == "ok"]
        if len(usable) < 2:
            raise RuntimeError(
                "only %d of %d repeats completed; cannot judge determinism (%s)"
                % (len(usable), repeats,
                   "; ".join(r.reason for r in results if r.status != "ok")))
        digests = [r.digest for r in usable]
        identical = len(set(digests)) == 1
        divergence = None
        if not identical:
            divergence = _first_divergence(usable[0], usable[1])
        fields, common, uncertified = _tick_aligned_agreement(usable)
        return DeterminismReport(trial=trial.name, repeats=len(usable),
                                 digests=digests, identical=identical,
                                 first_divergence=divergence,
                                 seeded=bool(trial.rng_seed),
                                 fields=fields, common_ticks=common,
                                 uncertified=uncertified)


#: `_is_continuous` and `_as_axes` moved to `align.py` with the classifier they
#: serve, and are re-exported above under their public names. These aliases are
#: kept because the names appear in the harness's own history and in review
#: notes, and a NameError is a worse answer than an indirection.
_is_continuous = is_continuous
_as_axes = as_axes


def _tick_aligned_agreement(results: Sequence[IterationResult]
                            ) -> Tuple[List[FieldAgreement], int, int]:
    """Compare iterations on the game's clock, not on sample ordinals.

    The comparison itself lives in `align.agreement`, because the analysis
    tool has to run it against a JSONL file recorded elsewhere and cannot
    build the `IterationResult` list this signature takes. All that is left
    here is the adaptation: in-memory `Samples` to the tick index both callers
    speak. See `align.py` for what the classification is defending against.
    """
    per_iter: List[TickIndex] = []
    uncertified = 0
    for result in results:
        ticks, torn = tick_index(result.samples or [])
        per_iter.append(ticks)
        uncertified += torn
    return agreement(per_iter, uncertified)


def _first_divergence(a: IterationResult, b: IterationResult) -> Optional[Divergence]:
    """Where two sample streams first part company.

    Reported as (frame, entity, field) because that is directly actionable: a
    divergence that starts at frame 0 is a load-state or setup problem, one
    that starts at the snap is the RNG, and one that starts on a single player
    forty frames in is a contest roll you can go and find.
    """
    frames_a = list(a.samples or [])
    frames_b = list(b.samples or [])
    for frame_a, frame_b in zip(frames_a, frames_b):
        keys = sorted(set(frame_a.values) | set(frame_b.values))
        for key in keys:
            value_a = frame_a.values.get(key)
            value_b = frame_b.values.get(key)
            if value_a != value_b:
                return Divergence(a.index, b.index, frame_a.index, key[0], key[1],
                                  value_a, value_b)
    if len(frames_a) != len(frames_b):
        return Divergence(a.index, b.index, None, "", "", len(frames_a), len(frames_b))
    return None


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    return float(value)


def make_provenance(trial: Trial, arm: str, run_id: str = "",
                    repo: Optional[str] = None) -> Provenance:
    return Provenance(run_id=run_id or new_run_id(), spec=trial.name,
                      spec_digest=trial.digest(), state=trial.state,
                      git_rev=git_revision(repo), arm=arm)

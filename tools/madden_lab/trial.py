"""The declarative half of an experiment: what to load, poke, press, sample.

A `Trial` is data, not a script. That is the whole point -- it can be
version-controlled, diffed, and printed into the result file, so a number
produced in August can be defended in October by reading the spec that made it
rather than by trusting a memory of what the code did that day.

Two rules from the design contract are enforced here rather than left to
discipline:

* **No undeclared writes.** A `Write` is the only way to touch game memory, it
  carries a mandatory `why`, and the runner refuses a trial with a non-empty
  `setup` unless writes were explicitly enabled at the command line. An
  undeclared poke makes every row in the run unreproducible, and there is no
  way to detect one after the fact.
* **No lazy operator questions.** An `OperatorAsk` carries a mandatory
  `why_not_memory`. Operator time is the scarcest resource in the system, so
  the type system asks you to justify spending it before you can spend it. If
  the honest answer is "I could read it but I did not want to write the
  accessor", the field is impossible to fill in and the ask should not exist.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# Setup: the only legal way to write to the game
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Write:
    """One declared poke, applied after the savestate loads and before input.

    `why` is not decoration. When a run months old shows an odd distribution,
    the first question is always "what were we writing?", and a bare address is
    not an answer. Prefer a doc citation: "fb-wr-blocking.md C1: put coverage
    defenders back in the assignment pool".
    """

    addr: int
    value: int
    why: str
    size: int = 4

    def __post_init__(self) -> None:
        if self.size not in (1, 2, 4, 8):
            raise ValueError("write size must be 1, 2, 4 or 8, got %r" % (self.size,))
        if not self.why.strip():
            raise ValueError("Write at 0x%08X needs a `why`" % self.addr)
        if self.addr <= 0:
            raise ValueError("Write needs a positive EE address, got %r" % (self.addr,))

    def describe(self) -> str:
        return "0x%08X <- 0x%X (%d-byte): %s" % (
            self.addr, self.value, self.size, self.why)


# --------------------------------------------------------------------------
# Input: frame-relative, because every experiment is shaped that way
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class InputEvent:
    """Press `button` for `duration` frames, starting at `frame`.

    `frame` counts from the first frame the runner samples, not from the snap.
    The snap has not happened yet when a trial starts -- pressing the button
    that causes it is usually event zero -- so a snap-relative origin would be
    undefined exactly when the script needs it. Alignment to the snap is
    recovered downstream from the sampled `frames_since_snap` field.
    """

    frame: int
    button: str
    duration: int = 2

    def __post_init__(self) -> None:
        if self.frame < 0:
            raise ValueError("InputEvent frame must be >= 0, got %d" % self.frame)
        if self.duration < 1:
            raise ValueError("InputEvent duration must be >= 1, got %d" % self.duration)


# --------------------------------------------------------------------------
# Observation: which entities, which fields, which frames
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EntitySelector:
    """Which entities to read, and which of their fields.

    `kind` is a `world` concept: "player", "ball" or "game". The filters are
    all optional and conjunctive; leaving them empty selects every entity of
    that kind, which for players is 22 rows per field per frame and is usually
    a mistake -- see the size arithmetic in `results.py`.
    """

    kind: str
    fields: Tuple[str, ...]
    side: Optional[int] = None
    positions: Tuple[int, ...] = ()
    indices: Tuple[int, ...] = ()
    label: str = ""

    def __post_init__(self) -> None:
        if not self.fields:
            raise ValueError("EntitySelector(%r) selects no fields" % self.kind)

    def matches(self, entity: Any, fields: Optional[Dict[str, Any]] = None) -> bool:
        """Filter one entity. Absent attributes never match.

        `fields` is the batch already read for this entity, if any. Preferring
        it keeps filtering free: `Player.position` is a property that costs a
        PINE round trip, so filtering 22 players on position would otherwise
        add 22 requests per frame purely to discard rows.
        """
        fields = fields or {}

        def value(name: str) -> Any:
            return fields[name] if name in fields else _read_field(entity, name)

        if self.side is not None and value("side") != self.side:
            return False
        if self.positions and value("position") not in self.positions:
            return False
        if self.indices and value("index") not in self.indices:
            return False
        return True


@dataclass(frozen=True)
class SampleSpec:
    """What to record, and how often.

    `every` decimates frames. A 200-iteration run of a 6-second play sampling
    22 players x 6 fields is about three million rows; `every=2` halves that at
    the cost of being unable to see a one-frame transition. Decide deliberately
    -- block mode changes are multi-frame, engagement kind transitions are not.
    """

    entities: Tuple[EntitySelector, ...]
    every: int = 1
    start_frame: int = 0

    def __post_init__(self) -> None:
        if self.every < 1:
            raise ValueError("SampleSpec.every must be >= 1, got %d" % self.every)
        if not self.entities:
            raise ValueError("SampleSpec selects no entities")

    def due(self, frame: int, last_sampled: Optional[int]) -> bool:
        """Should this game frame be sampled, given the last one that was?

        Phrased as a cursor rather than `frame % every == 0` because there is
        no frame-advance in the PINE protocol: the harness polls a counter that
        the emulator moves on its own schedule, so the frame numbers it sees
        have gaps. Modulo silently skips a whole sampling period every time the
        poll loop falls behind by exactly the wrong amount; a cursor degrades
        to "as soon as possible after it was due", which is the honest
        behaviour and is visible in the recorded frame numbers.
        """
        if frame < self.start_frame:
            return False
        if last_sampled is None:
            return True
        return frame >= last_sampled + self.every


# --------------------------------------------------------------------------
# Confirming the reset actually happened
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadConfirm:
    """Proof that the savestate finished loading. Not optional in practice.

    Layer 1 reports that PINE's `MsgSaveState`/`MsgLoadState` hand the work to
    another thread with `block=false` and reply OK immediately: **OK means
    queued, not loaded**, and a failed load -- empty slot, corrupt file -- is
    reported on the emulator's on-screen display and never on the socket.

    That produces the worst failure mode this harness has: a run that executes
    entirely against the *previous* trial's world, samples cleanly, reduces to
    plausible metrics, and is wrong. There is no way to detect it afterwards
    from the result file, because a stale world is a perfectly valid world.

    So every trial declares how to recognise its own loaded state, and the
    runner blocks on it. `addr`/`expected` covers the ordinary case -- pick a
    word the loaded state provably differs on, not a word that merely *should*
    be right, and remember that reads of unmapped pages return 0 rather than
    erroring, so 0 is never proof of anything. `check` covers the rest.

    A fixed sleep is not an acceptable substitute and is deliberately not
    offered: it converts a detectable failure into an undetectable one.

    `require_reset` exists because a predicate can be **vacuously true in the
    outgoing world**. The first determinism run confirmed with `counter > 0`
    while the previous iteration's play was still running: the confirm passed
    instantly, the first samples photographed the stale world (tick 179 -- the
    prior iteration's final frame -- then a backward jump to 74 as the load
    landed), and the tool concluded the engine diverged. With `require_reset`
    the runner additionally waits until the game clock shows a discontinuity
    -- a backward jump, or a forward leap far larger than the poll interval
    allows -- before it will believe the predicate. A level can lie about a
    load; an edge cannot. Set it False only for a state whose clock genuinely
    cannot jump on load, and say why in the trial.
    """

    addr: int = 0
    expected: Optional[int] = None
    size: int = 4
    check: Optional[Callable[[Any], bool]] = None
    description: str = ""
    timeout_s: float = 10.0
    require_reset: bool = True
    lo: Optional[int] = None
    hi: Optional[int] = None

    def __post_init__(self) -> None:
        window = self.lo is not None or self.hi is not None
        if window and (self.lo is None or self.hi is None or self.addr <= 0):
            raise ValueError("a window LoadConfirm needs addr=, lo= and hi=")
        if window and not 0 < self.lo <= self.hi:
            raise ValueError(
                "LoadConfirm window [%r, %r] must satisfy 0 < lo <= hi: a "
                "window admitting 0 confirms on an unmapped read, and an "
                "inverted one admits nothing." % (self.lo, self.hi))
        if window:
            return
        if self.check is None and (self.addr <= 0 or self.expected is None):
            raise ValueError(
                "LoadConfirm needs either check=, addr=+expected=, or "
                "addr=+lo=+hi=")
        if self.expected == 0 and self.check is None:
            raise ValueError(
                "LoadConfirm expecting 0 at 0x%08X proves nothing: unmapped "
                "reads return 0. Anchor on a known-nonzero word." % self.addr)

    def satisfied(self, emu: Any) -> bool:
        if self.lo is not None:
            return self.lo <= emu.read(self.addr, self.size) <= self.hi
        if self.check is not None:
            return bool(self.check(emu))
        return emu.read(self.addr, self.size) == self.expected

    def describe(self) -> str:
        if self.description:
            return self.description
        if self.lo is not None:
            return "%d <= [0x%08X] <= %d" % (self.lo, self.addr, self.hi)
        return "0x%08X == 0x%X" % (self.addr, self.expected or 0)


# --------------------------------------------------------------------------
# Stopping: never hang, and say why you stopped
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StopCondition:
    """When the iteration ends.

    `max_frames` is mandatory and is a hard ceiling, not a fallback. An
    unattended 200-iteration run that hangs on iteration 3 because a play never
    reached the dead-ball phase costs a night; the ceiling turns that into a
    truncated iteration with a recorded reason.

    `timeout_s` is the wall-clock twin, and catches the different failure where
    the frame counter itself stops advancing -- a paused, crashed or
    save-state-dialogued emulator.
    """

    max_frames: int
    until: Optional[Callable[["Frame"], bool]] = None
    until_name: str = ""
    timeout_s: float = 120.0

    def __post_init__(self) -> None:
        if self.max_frames < 1:
            raise ValueError("StopCondition.max_frames must be >= 1")

    def reached(self, frame: "Frame") -> Optional[str]:
        """Return a reason string if the iteration should stop, else None."""
        if frame.index + 1 >= self.max_frames:
            return "max_frames"
        if self.until is not None and self.until(frame):
            return self.until_name or "until"
        return None


# --------------------------------------------------------------------------
# What a stop predicate and a metric get to look at
# --------------------------------------------------------------------------


class Frame:
    """One sampled frame, keyed (entity, field). Handed to stop predicates.

    `index` is the game tick offset from the iteration's base tick; `tick` is
    the absolute counter value the sample was read at. They are separate
    because cross-iteration comparison must join on the *game's* clock: the
    first determinism run compared sample-ordinal to sample-ordinal, which
    compares different moments of the play, and called a bitwise-reproducible
    engine DIVERGENT.

    `certified` records that the game clock read the same value immediately
    before and after the batch that produced these values -- the whole sample
    provably lies within one game tick. An uncertified sample may mix two
    adjacent frames (PINE cannot pause the EE) and is excluded from
    determinism verdicts rather than silently compared.
    """

    __slots__ = ("index", "values", "tick", "certified")

    def __init__(self, index: int, values: Dict[Tuple[str, str], Any],
                 tick: Optional[int] = None, certified: bool = False):
        self.index = index
        self.values = values
        self.tick = index if tick is None else tick
        self.certified = certified

    def get(self, entity: str, field_name: str, default: Any = None) -> Any:
        return self.values.get((entity, field_name), default)

    def entities(self, prefix: str = "") -> List[str]:
        seen = []
        for entity, _field in self.values:
            if entity.startswith(prefix) and entity not in seen:
                seen.append(entity)
        return seen

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Frame(index=%d, %d values)" % (self.index, len(self.values))


class Samples:
    """Every frame of one iteration, in the shape metric functions want.

    Metrics are computed by the runner at the end of each iteration and written
    as `metric` rows. That is a deliberate choice over reducing at analysis
    time: `compare` then needs only the metric rows and never has to re-import
    a spec that may have been edited since, which is exactly the failure mode
    that makes an old result indefensible.
    """

    def __init__(self, frames: Sequence[Frame]):
        self.frames = list(frames)

    def __len__(self) -> int:
        return len(self.frames)

    def __iter__(self):
        return iter(self.frames)

    def entities(self, prefix: str = "") -> List[str]:
        seen: List[str] = []
        for frame in self.frames:
            for entity in frame.entities(prefix):
                if entity not in seen:
                    seen.append(entity)
        return seen

    def series(self, entity: str, field_name: str) -> List[Tuple[int, Any]]:
        """[(frame_index, value)] for one field, skipping frames it is absent."""
        out = []
        for frame in self.frames:
            key = (entity, field_name)
            if key in frame.values:
                out.append((frame.index, frame.values[key]))
        return out

    def values(self, entity: str, field_name: str) -> List[Any]:
        return [value for _frame, value in self.series(entity, field_name)]

    def first_frame_where(
        self, entity: str, field_name: str, predicate: Callable[[Any], bool],
        run: int = 2
    ) -> Optional[int]:
        """First frame the predicate holds for `run` consecutive samples.

        `run` defaults to 2, not 1, and that default is load-bearing. Layer 1
        reports that PINE reads are not synchronised with emulation, so a
        multi-field snapshot can tear mid-update: a single frame can carry a
        value the game never actually held. A metric keyed on one frame turns
        that read noise into a measurement -- and worse, into a *first*
        measurement, which is the kind that gets quoted. Requiring the value to
        survive to the next sample costs one frame of resolution and removes
        the entire class of error. Pass run=1 only when the event genuinely
        cannot persist for two samples, and say so where you pass it.
        """
        series = self.series(entity, field_name)
        streak = 0
        start: Optional[int] = None
        for index, value in series:
            if predicate(value):
                if streak == 0:
                    start = index
                streak += 1
                if streak >= run:
                    return start
            else:
                streak = 0
                start = None
        return None

    def count_frames_where(
        self, entity: str, field_name: str, predicate: Callable[[Any], bool]
    ) -> int:
        """Total frames matching. Aggregates are torn-read tolerant by nature:
        one spurious frame in three hundred moves a count by one."""
        return sum(1 for _f, value in self.series(entity, field_name) if predicate(value))

    def holds_for(
        self, entity: str, field_name: str, predicate: Callable[[Any], bool],
        run: int = 2
    ) -> bool:
        """Did the predicate ever hold for `run` consecutive samples?

        The torn-read-safe form of `any(...)`. Use it wherever a metric asks
        "did this ever happen", because `any()` over a few thousand samples
        will eventually say yes to a value that was never real.
        """
        return self.first_frame_where(entity, field_name, predicate, run=run) is not None


@dataclass(frozen=True)
class Metric:
    """A scalar summary of one iteration.

    `reduce` returns None when the iteration could not produce the measurement
    -- the play ended early, the entity never appeared. None is recorded as a
    null metric row and excluded from the statistics, with the count reported,
    because silently dropping it turns a systematic failure into a quiet
    reduction in n.

    `higher_is` is "better", "worse" or "" and exists only so `compare` can
    phrase a direction; it never affects the arithmetic.
    """

    name: str
    reduce: Callable[[Samples], Optional[float]]
    unit: str = ""
    description: str = ""
    higher_is: str = ""


# --------------------------------------------------------------------------
# The operator
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OperatorAsk:
    """A question only a human watching the screen can answer.

    Batched by default. The runner does not stop to ask; it queues the prompts
    and flushes them, so a 200-iteration run is not 200 interruptions. The
    answers come back through `python -m tools.madden_lab answer` and land as
    rows in the same JSONL file as the automatic measurements, with the same
    provenance, so a downstream analysis cannot tell -- and does not need to
    care -- which measurements had a human in the loop.

    `choices` is mandatory and should be short. A free-text answer is
    expensive to give and impossible to aggregate; "yes / no / unclear" is a
    variable. "unclear" is always appended if absent, because forcing a binary
    answer out of an ambiguous frame manufactures data.

    `why_not_memory` must explain why a memory read cannot settle this. The
    good cases are genuinely outside the machine: authorial intent that the
    engine does not encode, or whether an animation reads as a warp.
    """

    id: str
    question: str
    watch_for: str
    why_not_memory: str
    choices: Tuple[str, ...] = ("yes", "no", "unclear")
    #: Ask on iterations where `iteration % every == 0`, capped at `limit`.
    every: int = 25
    limit: int = 8

    def __post_init__(self) -> None:
        for name in ("id", "question", "watch_for", "why_not_memory"):
            if not str(getattr(self, name)).strip():
                raise ValueError("OperatorAsk.%s must not be empty" % name)
        if self.every < 1:
            raise ValueError("OperatorAsk.every must be >= 1")
        if "unclear" not in self.choices:
            object.__setattr__(self, "choices", tuple(self.choices) + ("unclear",))

    def due(self, iteration: int, already_asked: int) -> bool:
        return already_asked < self.limit and iteration % self.every == 0


# --------------------------------------------------------------------------
# The trial itself
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Trial:
    """One experiment, start to finish.

    `question` and `cannot_conclude` are prose fields written into the run
    header. The second one is the important one: an experiment that has not
    written down what it will *not* be able to say is an experiment whose
    results will be over-read. Every trial in this repo has one, because every
    trial here samples a single savestate and therefore a single play, a single
    formation and a single set of personnel.
    """

    name: str
    #: Human-meaningful identity of the savestate -- the filename. This is
    #: provenance, and it is what goes in every row.
    state: str
    question: str
    sample: SampleSpec
    stop: StopCondition
    #: The PCSX2 slot that savestate is loaded into. Separate from `state`
    #: because layer 1's `load_state` takes a u8 slot, not a path (SEAM
    #: REQUEST 1), and "slot 3" is not provenance: it means nothing in two
    #: months' time. Recording both is the only way to have a reset primitive
    #: that works *and* a result file that can be defended.
    state_slot: Optional[int] = None
    #: How to know the savestate finished loading. Omitting it is permitted so
    #: that live-observation trials work, but the runner degrades to a much
    #: weaker check and says so loudly -- see `LoadConfirm`.
    load_confirm: Optional[LoadConfirm] = None
    setup: Tuple[Write, ...] = ()
    script: Tuple[InputEvent, ...] = ()
    metrics: Tuple[Metric, ...] = ()
    asks: Tuple[OperatorAsk, ...] = ()
    cannot_conclude: Tuple[str, ...] = ()
    #: Writes that seed the RNG, kept separate so `verify-determinism` can
    #: report whether a divergence was measured with or without them.
    rng_seed: Tuple[Write, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Trial needs a name")
        if not self.state.strip():
            raise ValueError("Trial needs a savestate")
        seen_metrics = set()
        for metric in self.metrics:
            if metric.name in seen_metrics:
                raise ValueError("duplicate metric name %r" % metric.name)
            seen_metrics.add(metric.name)
        seen_asks = set()
        for ask in self.asks:
            if ask.id in seen_asks:
                raise ValueError("duplicate ask id %r" % ask.id)
            seen_asks.add(ask.id)

    @property
    def all_writes(self) -> Tuple[Write, ...]:
        """Everything this trial is allowed to poke, seeds included."""
        return tuple(self.rng_seed) + tuple(self.setup)

    def digest(self) -> str:
        """Stable hash of the executable content of the spec.

        Prose changes do not move it; an address, a value, a button or a frame
        does. That is what makes "the same trial" a checkable claim across two
        result files rather than a shared filename.
        """
        parts: List[str] = [self.name, self.state, str(self.state_slot)]
        for write in self.all_writes:
            parts.append("W:%08X:%X:%d" % (write.addr, write.value, write.size))
        for event in self.script:
            parts.append("I:%d:%s:%d" % (event.frame, event.button, event.duration))
        parts.append("S:%d:%d" % (self.sample.every, self.sample.start_frame))
        for selector in self.sample.entities:
            parts.append("E:%s:%s:%s:%s:%s" % (
                selector.kind, ",".join(selector.fields), selector.side,
                selector.positions, selector.indices))
        parts.append("T:%d:%s" % (self.stop.max_frames, self.stop.until_name))
        if self.load_confirm is not None:
            parts.append("L:%s" % self.load_confirm.describe())
        parts.extend("M:" + metric.name for metric in self.metrics)
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _read_field(entity: Any, name: str) -> Any:
    """Read a named field off a `world` entity, whatever shape layer 3 chose.

    The design contract fixes `World.players()` but not whether a Player is an
    attribute object, a mapping, or a wrapper with a `.fields` dict. Rather
    than bet on one and break when layer 3 lands, accept all three. Absent is
    None, never an exception, so a spec naming a field that this address map
    does not define degrades to a missing measurement instead of aborting a
    run at hour four.
    """
    if hasattr(entity, name):
        value = getattr(entity, name)
        # World exposes phase() and frames_since_snap() as methods while a
        # Player exposes plain values; calling zero-arg callables makes one
        # spec syntax cover both. Anything that raises is a missing reading,
        # not a crash -- a run four hours in should not die on one field.
        if callable(value):
            try:
                return value()
            except Exception:
                return None
        return value
    # Layer 3's per-field accessor. A Player carries no attribute per mapped
    # field -- `block_mode` lives in addresses.yaml, not in the class -- so
    # without this every field would read as None on the unbatched path.
    reader = getattr(entity, "field", None)
    if callable(reader):
        try:
            return reader(name)
        except Exception:
            return None  # unmapped field: a missing reading, not a crash
    fields = getattr(entity, "fields", None)
    if isinstance(fields, dict) and name in fields:
        return fields[name]
    try:
        return entity[name]
    except Exception:
        return None


def entity_id(kind: str, entity: Any, ordinal: int) -> str:
    """Stable row key for an entity: "player:0:7", "ball", "game".

    Stability across iterations is the whole requirement -- rows are joined on
    it. Prefer the world's own identity; fall back to enumeration order only if
    layer 3 exposes none, and note that the fallback is only stable if the
    player array is.

    **The side is part of the key, not decoration.** Layer 3 numbers players
    per side (`divmod(slot, per_side)`), so offence #5 and defence #5 both have
    index 5 and a key of "player:5" would silently merge two different men into
    one time series -- with the offensive and defensive rows for the same frame
    overwriting each other. The format matches the engine's own handle
    notation, side:index, so a handle word read out of memory can be turned
    into a row key by inspection.
    """
    if kind in ("ball", "game"):
        return kind
    index = _read_field(entity, "index")
    if index is None:
        return "%s:%s" % (kind, ordinal)
    side = _read_field(entity, "side")
    if side is None:
        return "%s:%s" % (kind, index)
    return "%s:%s:%s" % (kind, side, index)

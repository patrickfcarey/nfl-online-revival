"""Joining iterations on the game's clock, and classifying what disagrees.

Extracted from `runner.py`, unchanged in behaviour, because two callers need it
and only one of them can build the runner's input. `verify_determinism` has
`Samples` objects in memory; the analysis tool has a JSONL file recorded weeks
ago on a machine that is not here. A classifier that can only be reached
through the first of those is a classifier the second has to reimplement -- and
a second copy of `PHASE_WINDOW`, `SUBTICK_CEIL` and the both-directions rule
would disagree with the first the day either is tuned.

The seam is the tick index: `tick -> (entity, field) -> value` for one
iteration. Whoever can produce that can ask `agreement()` the question, and
nothing below that line knows where the values came from.

Why this exists at all, in one paragraph: the harness's first determinism run
compared sample #N to sample #N, which compares different moments of a football
play whenever the sampling start wobbles, and reported a bit-for-bit
reproducible engine as DIVERGENT. Aligned on the game's own clock, every one of
those disagreements turned out to be bitwise-equal to the other run one or two
ticks away -- sampling phase, an artifact of reading memory without being able
to pause the EE. `docs/lab-design.md` records the full correction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

#: One iteration, joined on the game's own clock. The key is `(entity, field)`
#: because that is the granularity a row is written at, and comparing anything
#: coarser would hide which field moved.
TickIndex = Dict[int, Dict[Tuple[str, str], Any]]

#: How far, in ticks, a disagreeing value may sit from a bitwise match in the
#: other run and still be called sampling phase. Two, because the poll loop
#: can drop a tick and land the equivalent read two frames along. Anything
#: further has no instrument explanation and is charged to the engine.
PHASE_WINDOW = 2

#: The largest positional difference sub-tick read phase can plausibly
#: produce, in field units. Measured, not guessed: across 540 certified ticks
#: of a live no-input replay, every positional disagreement fell in one of
#: three signatures of the same cause -- on the other run's inter-tick
#: movement segment (a smooth mover read mid-stride), inside its local wobble
#: envelope, or in a disjoint narrow band of an engaged lineman's contact
#: oscillation (each run's reads phase-locked to its own part of the wobble)
#: -- and the worst of them measured 0.13. Read phase is bounded by roughly
#: one frame of movement; a genuine behavioural divergence is not bounded at
#: all, because it compounds frame over frame into yards and flips discrete
#: fields on its way. 0.5 splits the regimes with margin on both sides, and
#: the discrete fields' strict verdict stands guard over the gap: 180 ticks
#: of this noise never moved one of them.
SUBTICK_CEIL = 0.5


def is_continuous(value: Any) -> bool:
    """Does this value live on a line, so that "close" is a defence?

    A float or a vector of floats gets the sub-tick allowance below; anything
    else must match bitwise. `bool` is deliberately not continuous -- it is an
    int in Python and a flag in the game.

    Values arriving from JSON rather than from `world.py` are the reason this
    is asked of a *value* and not of a field name: `xyz` comes back as a list,
    not a tuple, and a coordinate that happened to be integral comes back as
    an int. Callers that classify a whole field should ask this of every value
    they saw and treat the field as continuous if any of them says yes.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, float):
        return True
    return (isinstance(value, (tuple, list)) and bool(value)
            and all(isinstance(v, float) for v in value))


def as_axes(value: Any) -> Tuple[float, ...]:
    return tuple(value) if isinstance(value, (tuple, list)) else (float(value),)


@dataclass
class FieldAgreement:
    """Per-field verdict of a tick-aligned determinism comparison."""

    def __init__(self, field: str) -> None:
        self.field = field
        self.agree = 0
        self.phase = 0                 # bitwise-equal to an adjacent tick
        self.subtick = 0               # continuous: explained by read phase
        self.real = 0                  # explained by nothing
        self.max_delta = 0.0           # continuous only: worst |difference|
        self.examples: List[str] = []

    @property
    def compared(self) -> int:
        return self.agree + self.phase + self.subtick + self.real

    def describe(self) -> str:
        total = self.compared
        pct = 100.0 * self.agree / total if total else 0.0
        line = "  %-18s %6d compared  %6.2f%% identical" % (self.field, total, pct)
        extras = []
        if self.phase:
            extras.append("%d phase" % self.phase)
        if self.subtick:
            extras.append("%d sub-tick (max %.3f)" % (self.subtick, self.max_delta))
        if self.real:
            extras.append("%d REAL" % self.real)
        if extras:
            line += "  (" + ", ".join(extras) + ")"
        return line


def verdict(fields: Sequence[FieldAgreement]) -> str:
    """The three-way answer the ordinal comparison could not give.

    The first run of `verify-determinism` compared sample #N against sample
    #N, which compares different moments of a football play whenever the
    sampling start wobbles, and called a bitwise-reproducible engine
    DIVERGENT. Aligned on the game's own clock the same data was 100%
    identical on discrete fields with every disagreement bitwise-equal to an
    adjacent tick -- sampling phase, an artifact of reading without a pause.
    So phase noise gets its own verdict rather than being allowed to
    masquerade as engine behaviour.
    """
    if any(f.real for f in fields):
        return "DIVERGENT"
    subtick = max((f.max_delta for f in fields if f.subtick), default=0.0)
    if subtick:
        return ("DETERMINISTIC (discrete exact; continuous within "
                "%.3f read noise)" % subtick)
    if any(f.phase for f in fields):
        return "DETERMINISTIC (phase noise only)"
    return "DETERMINISTIC"


def tick_index(frames: Iterable[Any]) -> Tuple[TickIndex, int]:
    """`(index, uncertified)` for one iteration's in-memory frames.

    Uncertified samples -- the game clock moved between the two reads that
    bracket the batch -- are counted and excluded. A torn sample may mix two
    adjacent game frames, so it proves nothing in either direction and must
    not be allowed to decide a determinism verdict.
    """
    ticks: TickIndex = {}
    uncertified = 0
    for frame in frames:
        if frame.tick is None:
            continue
        if not frame.certified:
            uncertified += 1
            continue
        ticks[frame.tick] = frame.values     # last certified read wins
    return ticks, uncertified


def agreement(per_iter: Sequence[Mapping[int, Mapping[Tuple[str, str], Any]]],
              uncertified: int = 0) -> Tuple[List[FieldAgreement], int, int]:
    """Compare tick indexes to each other and classify every disagreement.

    This function is the forensics that acquitted the engine, promoted into
    the tool. The first determinism run compared sample #0 to sample #0 --
    tick 74 of one play against tick 179 of another -- and reported DIVERGENT.
    Joined on the tick instead, all 23 discrete disagreements in that capture
    were bitwise-equal to the other run one or two ticks earlier (transitions
    caught on opposite sides of a read), and 779 of 925 position disagreements
    were bitwise-equal at an adjacent tick. A verdict that cannot tell that
    pattern from real divergence calls every experiment on this transport
    divergent, forever.

    Every index is compared against the first, not pairwise: with three
    repeats the pairwise version triples the work to answer the same question,
    and "does anything differ from the reference replay" is the question.
    """
    if not per_iter or not all(per_iter):
        return [], 0, uncertified

    common = set(per_iter[0])
    for ticks in per_iter[1:]:
        common &= set(ticks)
    fields: Dict[str, FieldAgreement] = {}

    def neighbours(ticks: Mapping[int, Mapping[Tuple[str, str], Any]],
                   tick: int, key: Tuple[str, str]):
        for delta in range(-PHASE_WINDOW, PHASE_WINDOW + 1):
            if delta == 0:
                continue
            row = ticks.get(tick + delta)
            if row is not None and key in row:
                yield row[key]

    baseline = per_iter[0]
    for tick in sorted(common):
        row_a = baseline[tick]
        for other in per_iter[1:]:
            row_b = other[tick]
            for key in row_a.keys() & row_b.keys():
                name = key[1]
                agg = fields.setdefault(name, FieldAgreement(name))
                va, vb = row_a[key], row_b[key]
                if va == vb:
                    agg.agree += 1
                elif (any(vb == n for n in neighbours(baseline, tick, key))
                      and any(va == n for n in neighbours(other, tick, key))):
                    # Both directions must be explained by a shift. One-way
                    # matching is not enough: a genuinely wrong value in run B
                    # would still count as phase whenever run A's value merely
                    # persists into B's neighbourhood -- which, for a discrete
                    # field that rarely changes, is always.
                    agg.phase += 1
                elif is_continuous(va) and is_continuous(vb):
                    delta = max(abs(p - q) for p, q in
                                zip(as_axes(va), as_axes(vb)))
                    if delta <= SUBTICK_CEIL:
                        agg.subtick += 1
                        agg.max_delta = max(agg.max_delta, delta)
                    else:
                        agg.real += 1
                        if len(agg.examples) < 4:
                            agg.examples.append(
                                "tick %d %s.%s: %r != %r (delta %.3f exceeds "
                                "sub-tick read noise)"
                                % (tick, key[0], name, va, vb, delta))
                else:
                    agg.real += 1
                    if len(agg.examples) < 4:
                        agg.examples.append(
                            "tick %d %s.%s: %r != %r (no bitwise match within "
                            "%d ticks either side)"
                            % (tick, key[0], name, va, vb, PHASE_WINDOW))
    return ([fields[k] for k in sorted(fields)], len(common), uncertified)

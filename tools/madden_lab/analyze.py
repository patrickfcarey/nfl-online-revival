"""Exploring a result file: what happened, to whom, and when.

`compare.py` answers "did the patch change anything", with the statistics and
the refusals that question needs. This module answers the questions that come
*before* that one, which until now have been answered by throwaway Python
written fresh for each run and deleted afterwards:

* what did this run measure, and did any of it fail (`summarize`);
* what did one field do over a play, and between which ticks (`timeline`);
* do the iterations replay each other, judged from the file rather than from
  the rig (`agree`);
* cut this 1.5 GB run down to the rows one question needs (`slice`).

Three constraints shape everything here, and `docs/analysis-tool.md` argues
them at length:

**Streaming.** A 200-iteration run is ~4 million rows and ~1.5 GB, and
`json.loads` over all of it is 25 seconds. Nothing here holds a file in memory;
every command runs in one pass over `results.read_lines`, rejecting lines on a
substring before paying for the parse. The one resident structure is `agree`'s
tick index, which is why its iteration count is capped.

**The clock, not the ordinal.** Rows carry three time-like fields -- `frame`,
`sample` and `tick`. Ordering by `sample` compares different moments of a
football play, which is how a bit-for-bit reproducible engine was once reported
DIVERGENT (`docs/lab-design.md`). Everything here orders and joins on `tick`,
and there is deliberately no flag to do otherwise.

**A partial file is the normal case.** Runs take an hour and get read while
they are still being written, so the truncated final line is expected and
forgiven, and an iteration with metric rows but no `iteration` row is the one
currently running rather than a short play.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field as dc_field
from typing import (Any, Dict, Iterable, Iterator, List, Optional, Sequence,
                    Tuple)

from .align import (FieldAgreement, TickIndex, agreement, as_axes,
                    is_continuous, verdict)
from .compare import median, quantile
from .results import (KIND_ANSWER, KIND_ASK, KIND_EVENT, KIND_ITERATION,
                      KIND_METRIC, KIND_RUN, KIND_SAMPLE, kind_needle,
                      load_run, open_write, parse_row, read_lines)

#: Mirrors `__main__`; defined here so the analysis commands never import the
#: entry point that imports them.
EXIT_OK = 0
EXIT_USAGE = 2

#: Rows a slice keeps whatever else was asked for: they are the provenance, and
#: a result file without them is one nobody can defend six weeks later.
PROVENANCE_KINDS = (KIND_RUN, KIND_ITERATION, KIND_METRIC, KIND_EVENT,
                    KIND_ASK, KIND_ANSWER)

#: Above this many distinct values, a census has stopped being a census. Hit
#: only by a field that is really continuous and was not detected as such --
#: an int that happens to move every frame -- and the cap turns that from four
#: million dictionary entries into one honest line of output.
MAX_DISTINCT = 64

#: Same guard for episodes. A field that changes every tick has no episodes;
#: it has a track, and run-length encoding it just prints the file back.
MAX_EPISODES = 4096

#: Iterations `agree` will hold resident before it complains. The tick index is
#: `iterations x ticks x keys` values -- about 40 MB at 200 x 300 x 130 -- and
#: the determinism question needs three replays, not two hundred.
DEFAULT_REPEATS = 3


# --------------------------------------------------------------------------
# Selecting rows, cheaply
# --------------------------------------------------------------------------


def _token_needle(key: str, value: Any) -> Optional[str]:
    """`"key":value` as it appears in a row, or None if it cannot be trusted.

    Returns None whenever JSON would escape the value, because the escaped
    form is not the substring we would be searching for and the filter would
    silently drop every matching row. Over-matching is fine (`"iteration":3`
    is a substring of `"iteration":30`) since survivors are parsed and checked;
    under-matching is data loss, so it is refused rather than risked.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return '"%s":%d' % (key, value)
    if isinstance(value, str):
        encoded = json.dumps(value)
        if encoded != '"%s"' % value:
            return None
        return '"%s":%s' % (key, encoded)
    return None


@dataclass(frozen=True)
class Selection:
    """Which rows a command wants, and what it can prove about them cheaply.

    `needles()` returns groups of substrings: a line must contain at least one
    from every group to be worth parsing. Empty criteria contribute no group,
    so an unfiltered selection reads everything, which is the correct and slow
    answer rather than a wrong fast one.
    """

    kinds: Tuple[str, ...] = ()
    iterations: Tuple[int, ...] = ()
    entities: Tuple[str, ...] = ()
    fields: Tuple[str, ...] = ()
    tick_from: Optional[int] = None
    tick_to: Optional[int] = None
    certified_only: bool = False

    def needles(self) -> Tuple[Tuple[str, ...], ...]:
        groups: List[Tuple[str, ...]] = []
        for key, values in (("kind", self.kinds), ("iteration", self.iterations),
                            ("entity", self.entities), ("field", self.fields)):
            if not values:
                continue
            found = [_token_needle(key, value) for value in values]
            if all(needle is not None for needle in found):
                groups.append(tuple(needle for needle in found if needle))
        return tuple(groups)

    def matches(self, row: Dict[str, Any]) -> bool:
        if self.kinds and row.get("kind") not in self.kinds:
            return False
        if self.iterations and row.get("iteration") not in self.iterations:
            return False
        if self.entities and row.get("entity") not in self.entities:
            return False
        if self.fields and row.get("field") not in self.fields:
            return False
        if self.certified_only and not row.get("certified"):
            return False
        if self.tick_from is not None or self.tick_to is not None:
            tick = row.get("tick")
            if not isinstance(tick, int):
                return False
            if self.tick_from is not None and tick < self.tick_from:
                return False
            if self.tick_to is not None and tick > self.tick_to:
                return False
        return True


def _passes(text: str, groups: Sequence[Sequence[str]]) -> bool:
    return all(any(needle in text for needle in group) for group in groups)


def stream(path: str, selection: Selection) -> Iterator[Dict[str, Any]]:
    """Rows matching `selection`, in file order, one at a time.

    The truncated final line of a run still being written ends the stream
    quietly; a malformed line anywhere else raises with its line number,
    because silently skipping corruption changes the numbers without saying so.
    """
    groups = selection.needles()
    for line_no, text, is_last in read_lines(path):
        if groups and not _passes(text, groups):
            continue
        try:
            row = parse_row(text, line_no, path)
        except ValueError:
            if is_last:
                return
            raise
        if selection.matches(row):
            yield row


def read_header(path: str) -> Dict[str, Any]:
    """The `run` row, or {}. Reads one line, not the file.

    The runner writes it first, so this is free. It is worth having even when
    a command does not otherwise need it: printing the spec digest and the
    savestate above an analysis is what stops a number being quoted against
    the wrong experiment.
    """
    for line_no, text, _is_last in read_lines(path):
        try:
            row = parse_row(text, line_no, path)
        except ValueError:
            return {}
        return row if row.get("kind") == KIND_RUN else {}
    return {}


# --------------------------------------------------------------------------
# Regrouping the row stream into frames
# --------------------------------------------------------------------------


@dataclass
class SampledFrame:
    """One `(iteration, tick)` worth of rows, keyed the way a `Frame` is.

    Regrouped rather than read row by row because two questions need a whole
    frame at once: what a second field held at the same instant (`--with`),
    and the tick index `align.agreement` compares. The runner emits all of a
    frame's rows together, so this costs one frame of memory and no sorting.
    """

    iteration: Optional[int]
    tick: Optional[int]
    certified: bool
    values: Dict[Tuple[str, str], Any]
    run_id: Optional[str] = None


def frames(rows: Iterable[Dict[str, Any]]) -> Iterator[SampledFrame]:
    key: Optional[Tuple[Any, Any]] = None
    values: Dict[Tuple[str, str], Any] = {}
    certified = True
    run_id: Optional[str] = None
    for row in rows:
        here = (row.get("iteration"), row.get("tick"))
        if key is not None and here != key:
            yield SampledFrame(key[0], key[1], certified, values, run_id)
            values, certified = {}, True
        key = here
        run_id = row.get("run_id")
        values[(str(row.get("entity")), str(row.get("field")))] = row.get("value")
        certified = certified and bool(row.get("certified"))
    if key is not None:
        yield SampledFrame(key[0], key[1], certified, values, run_id)


def _hashable(value: Any) -> Any:
    """JSON gives lists; a census key must be hashable. Tuples round-trip."""
    if isinstance(value, list):
        return tuple(_hashable(item) for item in value)
    return value


# --------------------------------------------------------------------------
# Episodes and censuses
# --------------------------------------------------------------------------


@dataclass
class Episode:
    """A maximal run of one value on the tick axis, for one entity and field.

    `span` and `samples` differ exactly when the poll loop dropped frames
    inside the episode, and an episode where they differ is one whose
    boundaries are soft. Reporting only the tick range would manufacture a
    contiguity that was never observed -- the same distinction the runner
    draws between an iteration's `span` and its `frames`.
    """

    iteration: Optional[int]
    entity: str
    field: str
    value: Any
    first_tick: int
    last_tick: int
    samples: int
    folded: int = 0
    partners: Tuple[Any, ...] = ()

    @property
    def span(self) -> int:
        return self.last_tick - self.first_tick + 1


def fold_short(episodes: List[Episode], min_samples: int) -> Tuple[List[Episode], int]:
    """Absorb episodes too short to believe, when their neighbours agree.

    PINE reads are not synchronised with emulation, so one sample can carry a
    value the game never held -- and an episode boundary is where that lie is
    most expensive, because it invents a transition. This is
    `Samples.first_frame_where(run=2)` generalised: A-B-A with a one-sample B
    is a torn read and becomes A. A-B-C is kept whatever B's length, because
    folding it would have to choose between two transitions that both
    happened, and losing a real one is worse than reporting a brief one.
    """
    out: List[Episode] = []
    folded = 0
    index = 0
    while index < len(episodes):
        current = episodes[index]
        following = episodes[index + 1] if index + 1 < len(episodes) else None
        if (current.samples < min_samples and out and following is not None
                and out[-1].value == following.value):
            previous = out[-1]
            previous.last_tick = following.last_tick
            previous.samples += current.samples + following.samples
            previous.folded += 1 + current.folded + following.folded
            previous.partners = tuple(dict.fromkeys(
                previous.partners + current.partners + following.partners))
            folded += 1
            index += 2
            continue
        out.append(current)
        index += 1
    return out, folded


class FieldSeries:
    """Everything one `(entity, field)` did, accumulated in one pass.

    Discrete fields get a census and episodes; continuous ones get a track and
    neither, because run-length encoding `xyz` emits one episode per sample --
    that is not an analysis, it is the file again. Continuity is decided over
    the values actually seen rather than from the field's name, and is
    promoted rather than assumed: the first float turns the series continuous
    and throws the episodes away.
    """

    def __init__(self, entity: str, field: str) -> None:
        self.entity = entity
        self.field = field
        self.samples = 0
        self.census: Dict[Any, int] = {}
        self.census_overflow = 0
        self.continuous = False
        self.episodes: List[Episode] = []
        self.folded = 0
        self.rewinds = 0
        self.too_many_episodes = False
        self.first: Any = None
        self.last: Any = None
        self.lo: Optional[List[float]] = None
        self.hi: Optional[List[float]] = None
        self._open: Optional[Episode] = None
        self._key: Optional[Any] = None

    # -- accumulation ------------------------------------------------------

    def add(self, iteration: Optional[int], tick: int, value: Any,
            partner: Any = None) -> None:
        self.samples += 1
        if self.first is None:
            self.first = value
        self.last = value
        if not self.continuous and is_continuous(value):
            self._become_continuous()
        if self.continuous:
            self._track(value)
            return
        self._count(value)
        self._episode(iteration, tick, value, partner)

    def _become_continuous(self) -> None:
        self.continuous = True
        self.episodes = []
        self.census = {}
        self._open = None

    def _track(self, value: Any) -> None:
        axes = list(as_axes(value)) if is_continuous(value) else None
        if axes is None:
            return
        if self.lo is None or len(axes) != len(self.lo):
            self.lo, self.hi = list(axes), list(axes)
            return
        for index, axis in enumerate(axes):
            self.lo[index] = min(self.lo[index], axis)
            self.hi[index] = max(self.hi[index], axis)

    def _count(self, value: Any) -> None:
        key = _hashable(value)
        if key in self.census:
            self.census[key] += 1
        elif len(self.census) < MAX_DISTINCT:
            self.census[key] = 1
        else:
            self.census_overflow += 1

    def _episode(self, iteration: Optional[int], tick: int, value: Any,
                 partner: Any) -> None:
        key = _hashable(value)
        open_episode = self._open
        if open_episode is not None:
            rewound = tick < open_episode.last_tick
            if rewound:
                # The clock going backwards inside one iteration is a
                # savestate landing mid-play, not a value changing. Kept as
                # itself rather than smoothed, because smoothing it is how the
                # harness once photographed the previous iteration's world.
                self.rewinds += 1
            if (not rewound and iteration == open_episode.iteration
                    and key == self._key):
                open_episode.last_tick = tick
                open_episode.samples += 1
                if partner is not None and partner not in open_episode.partners:
                    open_episode.partners = open_episode.partners + (partner,)
                return
            self._close()
        if len(self.episodes) >= MAX_EPISODES:
            self.too_many_episodes = True
            self._open, self._key = None, None
            return
        self._open = Episode(iteration=iteration, entity=self.entity,
                             field=self.field, value=value, first_tick=tick,
                             last_tick=tick, samples=1,
                             partners=() if partner is None else (partner,))
        self._key = key

    def _close(self) -> None:
        if self._open is not None:
            self.episodes.append(self._open)
        self._open, self._key = None, None

    def finish(self, min_samples: int) -> None:
        self._close()
        if self.continuous or min_samples <= 1:
            return
        folded_all: List[Episode] = []
        total = 0
        for iteration in _ordered({ep.iteration for ep in self.episodes}):
            group = [ep for ep in self.episodes if ep.iteration == iteration]
            kept, folded = fold_short(group, min_samples)
            folded_all.extend(kept)
            total += folded
        self.episodes, self.folded = folded_all, total

    # -- reading out -------------------------------------------------------

    def held(self, value: Any, min_samples: int = 1) -> bool:
        """Did this series ever hold `value` for `min_samples` in a row?"""
        if self.continuous:
            return False
        key = _hashable(value)
        return any(_hashable(ep.value) == key and ep.samples >= min_samples
                   for ep in self.episodes)

    def census_rows(self) -> List[Tuple[Any, int]]:
        return sorted(self.census.items(), key=lambda pair: (-pair[1], repr(pair[0])))


def _ordered(values: Iterable[Any]) -> List[Any]:
    """Sort, tolerating the None an iteration-less row contributes."""
    return sorted(values, key=lambda v: (v is None, v))


# --------------------------------------------------------------------------
# timeline
# --------------------------------------------------------------------------


@dataclass
class TimelineReport:
    path: str
    header: Dict[str, Any]
    field: str
    with_field: Optional[str]
    min_samples: int
    series: List[FieldSeries]
    skipped: List[str]
    uncertified: int
    warnings: List[str] = dc_field(default_factory=list)


def timeline(path: str, field: str, entity: Optional[str] = None,
             value: Any = None, iterations: Sequence[int] = (),
             with_field: Optional[str] = None, min_samples: int = 2,
             certified_only: bool = False) -> TimelineReport:
    """What one field did, per entity, as a census and a list of episodes.

    One pass. Without `entity` this is also the discovery step -- "which
    players ever held block mode 3" -- which is why `value` filters at the end
    rather than at the row: an entity that never held it still has to be
    counted before it can be reported as never having held it.

    `min_samples` guards the episode boundaries against torn reads and
    defaults to 2 for the reason `Samples.first_frame_where` does. Pass 1 only
    when the event genuinely cannot survive two samples.
    """
    wanted = (field,) if not with_field else (field, with_field)
    selection = Selection(kinds=(KIND_SAMPLE,),
                          iterations=tuple(iterations),
                          entities=(entity,) if entity else (),
                          fields=wanted,
                          certified_only=certified_only)
    series: Dict[str, FieldSeries] = {}
    uncertified = 0
    run_ids = set()
    for frame in frames(stream(path, selection)):
        if frame.run_id is not None:
            run_ids.add(frame.run_id)
        if not frame.certified:
            uncertified += 1
        if not isinstance(frame.tick, int):
            continue
        for (row_entity, row_field), row_value in frame.values.items():
            if row_field != field:
                continue
            found = series.get(row_entity)
            if found is None:
                found = series[row_entity] = FieldSeries(row_entity, field)
            partner = None
            if with_field:
                partner = frame.values.get((row_entity, with_field))
            found.add(frame.iteration, frame.tick, row_value, partner)
    for found in series.values():
        found.finish(min_samples)

    kept, skipped = [], []
    for name in sorted(series):
        found = series[name]
        if value is not None and not found.held(value, min_samples):
            skipped.append(name)
            continue
        kept.append(found)

    warnings: List[str] = []
    if uncertified:
        warnings.append(
            "%d frame(s) could not be certified: the game clock moved between "
            "the two reads bracketing the batch, so those samples may mix two "
            "ticks. Episode boundaries drawn from them are soft; "
            "--certified-only excludes them." % uncertified)
    if value is not None and not kept:
        warnings.append(
            "no entity held %s=%r for %d consecutive samples. That is a "
            "measurement, not an error -- but check the field name against "
            "addresses.yaml before believing it."
            % (field, value, min_samples))
    if len(run_ids) > 1:
        warnings.append("this file holds %d runs; iterations from different "
                        "runs share numbers and have been merged" % len(run_ids))
    return TimelineReport(path=path, header=read_header(path), field=field,
                          with_field=with_field, min_samples=min_samples,
                          series=kept, skipped=skipped, uncertified=uncertified,
                          warnings=warnings)


def _decode_partner(value: Any) -> Optional[str]:
    """A handle word as `player:side:index`, or None if it is not one.

    Imported lazily: `world` needs PyYAML to read the address map, and every
    command in this module must keep working on a machine that has neither.
    The undecoded word is always what the JSON output carries -- a decode is
    an interpretation, and the record should keep what was read.
    """
    if not isinstance(value, int) or not value:
        return None
    try:
        from .world import Handle
    except Exception:
        return None
    handle = Handle(value)
    return "player:%d:%d" % (handle.side, handle.index) if handle.is_player else None


def format_timeline(report: TimelineReport) -> str:
    lines: List[str] = [_header_line(report.path, report.header), ""]
    for warning in report.warnings:
        lines.append("! " + _wrap(warning, "  "))
    if report.warnings:
        lines.append("")
    if not report.series:
        lines.append("no samples of %r in this file." % report.field)
        return "\n".join(lines)

    for found in report.series:
        lines.append("=" * 72)
        lines.append("%s   %s   %d samples" % (report.field, found.entity,
                                               found.samples))
        if found.continuous:
            lines.append("  continuous field: no episodes. Use `slice` and plot it.")
            lines.append("  first %s" % _short(found.first))
            lines.append("  last  %s" % _short(found.last))
            if found.lo is not None and found.hi is not None:
                for axis, (low, high) in enumerate(zip(found.lo, found.hi)):
                    lines.append("  axis %d  %.4f .. %.4f" % (axis, low, high))
            continue

        lines.append("")
        lines.append("  census (raw samples per value, all iterations -- "
                     "unlike the episodes below, not torn-read guarded)")
        for value, count in found.census_rows():
            share = 100.0 * count / found.samples if found.samples else 0.0
            lines.append("    %-24s %8d  %5.1f%%" % (_short(value), count, share))
        if found.census_overflow:
            lines.append("    %-24s %8d  (more than %d distinct values seen)"
                         % ("<other>", found.census_overflow, MAX_DISTINCT))

        for iteration in _ordered({ep.iteration for ep in found.episodes}):
            group = [ep for ep in found.episodes if ep.iteration == iteration]
            lines.append("")
            lines.append("  episodes, iteration %s, min %d samples"
                         % (iteration, report.min_samples))
            for episode in group:
                line = ("    tick %5d-%-5d  %-16s %5d samples"
                        % (episode.first_tick, episode.last_tick,
                           _short(episode.value), episode.samples))
                if episode.span != episode.samples:
                    line += "  span %d (frames dropped)" % episode.span
                if report.with_field and episode.partners:
                    line += "  %s %s" % (report.with_field,
                                         _partners(episode.partners))
                lines.append(line)
        if found.folded:
            lines.append("    %d episode(s) shorter than %d samples folded "
                         "between equal neighbours (torn read)"
                         % (found.folded, report.min_samples))
        if found.rewinds:
            lines.append("    %d clock rewind(s) inside an iteration -- a "
                         "savestate landing mid-play" % found.rewinds)
        if found.too_many_episodes:
            lines.append("    episodes truncated at %d: this field changes "
                         "almost every tick and has no episodes to report"
                         % MAX_EPISODES)
    if report.skipped:
        lines.append("=" * 72)
        lines.append("%d other entit%s never held the requested value: %s"
                     % (len(report.skipped),
                        "y" if len(report.skipped) == 1 else "ies",
                        ", ".join(report.skipped[:12])))
    return "\n".join(lines)


def _partners(values: Sequence[Any]) -> str:
    shown = []
    for value in values[:4]:
        decoded = _decode_partner(value)
        shown.append(decoded if decoded else _short(value))
    if len(values) > 4:
        shown.append("+%d more" % (len(values) - 4))
    return ", ".join(shown)


def timeline_as_dict(report: TimelineReport) -> Dict[str, Any]:
    return {
        "path": report.path,
        "field": report.field,
        "with": report.with_field,
        "min_samples": report.min_samples,
        "uncertified_frames": report.uncertified,
        "warnings": report.warnings,
        "skipped_entities": report.skipped,
        "series": [
            {
                "entity": found.entity,
                "samples": found.samples,
                "continuous": found.continuous,
                "census": [{"value": list(value) if isinstance(value, tuple)
                            else value, "count": count}
                           for value, count in found.census_rows()],
                "census_overflow": found.census_overflow,
                "rewinds": found.rewinds,
                "folded": found.folded,
                "first": found.first, "last": found.last,
                "lo": found.lo, "hi": found.hi,
                "episodes": [
                    {"iteration": ep.iteration, "value": ep.value,
                     "first_tick": ep.first_tick, "last_tick": ep.last_tick,
                     "samples": ep.samples, "span": ep.span,
                     "folded": ep.folded, "partners": list(ep.partners)}
                    for ep in found.episodes
                ],
            }
            for found in report.series
        ],
    }


# --------------------------------------------------------------------------
# summarize
# --------------------------------------------------------------------------


@dataclass
class MetricSummary:
    name: str
    values: List[float]
    missing: int
    missing_iterations: List[int]

    @property
    def n(self) -> int:
        return len(self.values)


@dataclass
class SummaryReport:
    path: str
    header: Dict[str, Any]
    complete: int
    in_progress: List[int]
    samples: int
    uncertified: int
    degenerate: bool
    metrics: List[MetricSummary]
    events: List[Dict[str, Any]]
    warnings: List[str] = dc_field(default_factory=list)


#: Below this many iterations the raw values are printed as well as the
#: summary. A median of four numbers is a worse representation of them than
#: the four numbers, and the run that produced `[1.1, 0.3, 0.9, 0.9]` is
#: exactly the case: the 0.3 was one noisy iteration and a mean read it as an
#: effect for a day.
SHOW_VALUES_UPTO = 12


def summarize(path: str, complete_only: bool = False) -> SummaryReport:
    """Per-metric centre and spread across iterations, plus the run's health.

    One pass, and the sample rows are never parsed -- they are counted by
    substring, which is the difference between two seconds and twenty-five on
    a real run.

    Robust statistics, not a mean and an SD, and `None` is a column rather
    than a filtered-out row: a metric that legitimately did not happen and a
    metric that is broken are indistinguishable in a mean and are told apart
    by that count.
    """
    run = load_run(path)
    complete = {row.get("iteration") for row in run.iterations}
    seen = {row.get("iteration") for row in run.metrics}
    in_progress = _ordered(seen - complete)

    metrics: List[MetricSummary] = []
    for name in run.metric_names():
        rows = [row for row in run.metrics if row.get("metric") == name]
        if complete_only:
            rows = [row for row in rows if row.get("iteration") in complete]
        rows.sort(key=lambda row: (row.get("iteration") is None,
                                   row.get("iteration")))
        values = [float(row["value"]) for row in rows
                  if isinstance(row.get("value"), (int, float))
                  and not isinstance(row.get("value"), bool)]
        missing = [row.get("iteration") for row in rows
                   if row.get("value") is None]
        metrics.append(MetricSummary(name=name, values=values,
                                     missing=len(missing),
                                     missing_iterations=_ordered(missing)))

    digests = run.iteration_digests()
    degenerate = len(digests) >= 2 and len(set(digests)) == 1
    warnings: List[str] = []
    if degenerate:
        warnings.append(
            "all %d completed iterations produced an identical sample stream. "
            "They are replays, not independent samples: n_effective is 1, and "
            "any spread below is the spread of one play measured once."
            % len(digests))
    if in_progress and not complete_only:
        warnings.append(
            "iteration(s) %s have metric rows but no iteration row, so the run "
            "is still writing or was killed. Their plays may be short; "
            "--complete-only excludes them."
            % ", ".join(str(index) for index in in_progress))
    if run.uncertified:
        warnings.append(
            "%d of %d sample rows are uncertified: the game clock moved during "
            "the batch that produced them." % (run.uncertified, run.samples))
    return SummaryReport(path=path, header=run.header, complete=len(complete),
                         in_progress=in_progress, samples=run.samples,
                         uncertified=run.uncertified, degenerate=degenerate,
                         metrics=metrics, events=run.events, warnings=warnings)


def format_summary(report: SummaryReport, only: Optional[str] = None) -> str:
    lines = [_header_line(report.path, report.header)]
    lines.append("%d iteration(s) complete, %d in progress; %s sample rows"
                 % (report.complete, len(report.in_progress),
                    "{:,}".format(report.samples)))
    lines.append("")
    for warning in report.warnings:
        lines.append("! " + _wrap(warning, "  "))
    for row in report.events:
        lines.append("! event %s: %s" % (row.get("event"), row.get("detail")))
    if report.warnings or report.events:
        lines.append("")

    lines.append("%-30s %4s %5s %9s  %-18s %8s %8s"
                 % ("metric", "n", "none", "median", "IQR", "min", "max"))
    for metric in report.metrics:
        if only and metric.name != only:
            continue
        if not metric.values:
            lines.append("%-30s %4d %5d %9s  never produced a value"
                         % (metric.name, 0, metric.missing, "--"))
            if metric.missing_iterations:
                lines.append("%s none on iterations %s"
                             % (" " * 30,
                                _brief(metric.missing_iterations)))
            continue
        low, high = quantile(metric.values, .25), quantile(metric.values, .75)
        lines.append("%-30s %4d %5d %9.3f  [%7.3f, %7.3f] %8.3f %8.3f"
                     % (metric.name, metric.n, metric.missing,
                        median(metric.values), low, high,
                        min(metric.values), max(metric.values)))
        if metric.n <= SHOW_VALUES_UPTO:
            lines.append("%s values: %s"
                         % (" " * 30,
                            "  ".join("%.3f" % value for value in metric.values)))
        if metric.missing_iterations:
            lines.append("%s none on iterations %s"
                         % (" " * 30, _brief(metric.missing_iterations)))
    if only and not any(metric.name == only for metric in report.metrics):
        lines.append("no metric named %r. This file has: %s"
                     % (only, ", ".join(m.name for m in report.metrics)))
    return "\n".join(lines)


def summary_as_dict(report: SummaryReport) -> Dict[str, Any]:
    return {
        "path": report.path,
        "run_id": report.header.get("run_id"),
        "spec": report.header.get("spec"),
        "spec_digest": report.header.get("spec_digest"),
        "arm": report.header.get("arm"),
        "state": report.header.get("state"),
        "git_rev": report.header.get("git_rev"),
        "complete": report.complete,
        "in_progress": report.in_progress,
        "samples": report.samples,
        "uncertified": report.uncertified,
        "deterministic": report.degenerate,
        "warnings": report.warnings,
        "metrics": [
            {"name": m.name, "n": m.n, "missing": m.missing,
             "missing_iterations": m.missing_iterations,
             "values": m.values,
             "median": median(m.values) if m.values else None,
             "iqr": [quantile(m.values, .25), quantile(m.values, .75)]
                    if m.values else None,
             "min": min(m.values) if m.values else None,
             "max": max(m.values) if m.values else None}
            for m in report.metrics
        ],
    }


# --------------------------------------------------------------------------
# agree
# --------------------------------------------------------------------------


@dataclass
class AgreeReport:
    path: str
    header: Dict[str, Any]
    iterations: List[int]
    fields: List[FieldAgreement]
    common_ticks: int
    uncertified: int
    warnings: List[str] = dc_field(default_factory=list)

    @property
    def verdict(self) -> str:
        if not self.fields:
            return "UNDECIDABLE"
        return verdict(self.fields)


def agree(path: str, iterations: Sequence[int] = (),
          fields: Sequence[str] = (), repeats: int = DEFAULT_REPEATS
          ) -> AgreeReport:
    """`verify-determinism` with the rig removed: the same classifier, on a file.

    Only certified samples are compared and the excluded count is reported. An
    uncertified sample may straddle two game frames, so comparing it proves
    nothing in either direction; and a file with no certified samples at all
    predates the clock-bracketed batch, its `tick` is really a sample ordinal,
    and aligning on it would compare different moments of the play -- the
    original mistake this whole path exists to prevent. That case refuses
    rather than producing a verdict.

    Iterations are capped because the tick index is the one structure here
    that has to be resident.
    """
    selection = Selection(kinds=(KIND_SAMPLE,), iterations=tuple(iterations),
                          fields=tuple(fields))
    per_iter: Dict[int, TickIndex] = {}
    order: List[int] = []
    uncertified = 0
    truncated = False
    for frame in frames(stream(path, selection)):
        index = frame.iteration
        if not isinstance(index, int) or not isinstance(frame.tick, int):
            continue
        if index not in per_iter:
            if len(order) >= repeats:
                # Iterations are written in order, so everything after this is
                # another iteration we are not going to look at.
                truncated = True
                break
            per_iter[index] = {}
            order.append(index)
        if not frame.certified:
            uncertified += 1
            continue
        per_iter[index][frame.tick] = frame.values

    warnings: List[str] = []
    if truncated:
        warnings.append(
            "stopped after %d iterations. Three replays answer the determinism "
            "question; more is allowed with --repeats and costs memory "
            "proportional to iterations x ticks x fields." % repeats)
    if uncertified and not any(per_iter.values()):
        return AgreeReport(path=path, header=read_header(path), iterations=order,
                           fields=[], common_ticks=0, uncertified=uncertified,
                           warnings=warnings + [
                               "every sample in this file is uncertified, so "
                               "its `tick` is a sample ordinal rather than the "
                               "game clock. Aligning on it would compare "
                               "different moments of the play; refusing."])
    if len(order) < 2:
        return AgreeReport(path=path, header=read_header(path), iterations=order,
                           fields=[], common_ticks=0, uncertified=uncertified,
                           warnings=warnings + [
                               "found %d iteration(s) with certified samples; "
                               "at least 2 are needed to compare." % len(order)])
    found, common, _ = agreement([per_iter[index] for index in order], uncertified)
    if not common:
        warnings.append(
            "the iterations share no certified tick, so there is nothing to "
            "compare. Either the sampling windows do not overlap, or the load "
            "confirmation is landing them at different points of the play.")
    return AgreeReport(path=path, header=read_header(path), iterations=order,
                       fields=found, common_ticks=common,
                       uncertified=uncertified, warnings=warnings)


def format_agree(report: AgreeReport) -> str:
    lines = [_header_line(report.path, report.header), ""]
    lines.append("iterations %s aligned on the game clock: %d common ticks, "
                 "%d uncertified samples excluded"
                 % (", ".join(str(i) for i in report.iterations),
                    report.common_ticks, report.uncertified))
    for found in report.fields:
        lines.append(found.describe())
    lines.append(report.verdict)
    real = [example for found in report.fields for example in found.examples]
    if real:
        lines.append("REAL divergences (engine, not instrument):")
        lines.extend("  " + example for example in real[:10])
    for warning in report.warnings:
        lines.append("! " + _wrap(warning, "  "))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# slice
# --------------------------------------------------------------------------


@dataclass
class SliceReport:
    source: str
    out: str
    kept: int
    provenance_kept: int
    scanned: int
    passes: int
    entities: Tuple[str, ...] = ()


def entities_holding(path: str, field: str, value: Any,
                     min_samples: int = 2) -> List[str]:
    """Entities that held `field == value` for `min_samples` consecutively.

    The consecutive requirement is not fussiness. With 22 players over a few
    hundred frames, `any(v == 3)` will eventually find a torn 3 somewhere, and
    a selector built on it silently widens to include a player who never held
    the value -- which is how the count of lead blockers would have gained a
    second one from read noise.
    """
    streak: Dict[str, int] = {}
    found: List[str] = []
    selection = Selection(kinds=(KIND_SAMPLE,), fields=(field,))
    for row in stream(path, selection):
        entity = str(row.get("entity"))
        if row.get("value") == value:
            streak[entity] = streak.get(entity, 0) + 1
            if streak[entity] >= min_samples and entity not in found:
                found.append(entity)
        else:
            streak[entity] = 0
    return sorted(found)


def slice_run(path: str, out: str, selection: Selection,
              where: Optional[Tuple[str, Any]] = None,
              min_samples: int = 2) -> SliceReport:
    """Write the rows matching `selection` to `out`, in the same schema.

    Same schema is the whole point: `plot_routes.py`, `compare`, `load_run`
    and every command in this module read the slice exactly as they read the
    run, so the expensive pass over 1.5 GB happens once and everything after
    it works on a file small enough to iterate against. `.gz` works because
    `results.open_write` handles it.

    Provenance rows are kept whatever the filter says. They are a few hundred
    bytes and they are the only reason a derived file can be defended later.

    `where` selects entities by a value they ever held, and therefore needs a
    first pass to find out who -- reported, because the alternative is
    buffering the file.
    """
    passes = 1
    entities = selection.entities
    if where is not None:
        passes = 2
        found = entities_holding(path, where[0], where[1], min_samples)
        entities = tuple(found)
        selection = Selection(
            kinds=selection.kinds, iterations=selection.iterations,
            entities=entities, fields=selection.fields,
            tick_from=selection.tick_from, tick_to=selection.tick_to,
            certified_only=selection.certified_only)

    keep = tuple(kind_needle(kind) for kind in PROVENANCE_KINDS)
    groups = selection.needles()
    kept = provenance = scanned = 0
    with open_write(out) as handle:
        for line_no, text, is_last in read_lines(path):
            scanned += 1
            if any(needle in text for needle in keep):
                handle.write(text + "\n")
                provenance += 1
                continue
            if groups and not _passes(text, groups):
                continue
            try:
                row = parse_row(text, line_no, path)
            except ValueError:
                if is_last:
                    break
                raise
            if selection.matches(row):
                # The original text, not a re-encoding: a slice that differs
                # from its source byte for byte is a slice whose digest cannot
                # be checked against the run it came from.
                handle.write(text + "\n")
                kept += 1
    return SliceReport(source=path, out=out, kept=kept,
                       provenance_kept=provenance, scanned=scanned,
                       passes=passes, entities=entities)


# --------------------------------------------------------------------------
# Shared rendering
# --------------------------------------------------------------------------


def _header_line(path: str, header: Dict[str, Any]) -> str:
    if not header:
        return "%s  (no run header -- provenance unknown)" % path
    return ("run %s  spec %s (digest %s)  arm %s  git %s\nstate %s"
            % (header.get("run_id"), header.get("spec"),
               header.get("spec_digest"), header.get("arm"),
               header.get("git_rev"), header.get("state")))


def _short(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_short(item) for item in value) + "]"
    if isinstance(value, float):
        return "%.4f" % value
    return repr(value)


def _brief(values: Sequence[Any], limit: int = 12) -> str:
    shown = ", ".join(str(value) for value in values[:limit])
    return shown + (" (+%d more)" % (len(values) - limit)
                    if len(values) > limit else "")


def _wrap(text: str, indent: str, width: int = 70) -> str:
    words = text.split()
    lines, current = [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = (current + " " + word) if current else word
    if current:
        lines.append(current)
    return ("\n" + indent).join(lines)


def literal(text: str) -> Any:
    """A command-line value as the row would hold it: 3, 3.5, or a string."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def _int_list(text: str) -> Tuple[int, ...]:
    try:
        return tuple(int(part) for part in text.split(",") if part.strip())
    except ValueError:
        raise ValueError("expected a comma-separated list of iteration "
                         "numbers, got %r" % text)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_summarize(args: Any) -> int:
    report = summarize(args.run, complete_only=args.complete_only)
    if args.json:
        print(json.dumps(summary_as_dict(report), indent=2, default=str))
    else:
        print(format_summary(report, only=args.metric))
    return EXIT_OK


def cmd_timeline(args: Any) -> int:
    report = timeline(args.run, field=args.field, entity=args.entity,
                      value=literal(args.value) if args.value is not None else None,
                      iterations=_int_list(args.iterations) if args.iterations else (),
                      with_field=getattr(args, "with_field", None),
                      min_samples=args.min_frames,
                      certified_only=args.certified_only)
    if args.json:
        print(json.dumps(timeline_as_dict(report), indent=2, default=str))
    else:
        print(format_timeline(report))
    return EXIT_OK


def cmd_agree(args: Any) -> int:
    report = agree(args.run,
                   iterations=_int_list(args.iterations) if args.iterations else (),
                   fields=tuple(args.field or ()), repeats=args.repeats)
    print(format_agree(report))
    return EXIT_OK


def cmd_slice(args: Any) -> int:
    where = None
    if args.where:
        if "=" not in args.where:
            print("error: --where wants FIELD=VALUE, got %r" % args.where)
            return EXIT_USAGE
        name, _, raw = args.where.partition("=")
        where = (name, literal(raw))
    selection = Selection(
        kinds=tuple(args.kind or ()) or (KIND_SAMPLE,),
        iterations=_int_list(args.iteration) if args.iteration else (),
        entities=tuple(args.entity or ()),
        fields=tuple(args.field or ()),
        tick_from=args.tick_from, tick_to=args.tick_to,
        certified_only=args.certified_only)
    report = slice_run(args.run, args.out, selection, where=where,
                       min_samples=args.min_frames)
    print("%s: %d sample rows + %d provenance rows from %d lines (%d pass%s)"
          % (report.out, report.kept, report.provenance_kept, report.scanned,
             report.passes, "" if report.passes == 1 else "es"))
    if where is not None:
        print("  --where selected: %s"
              % (", ".join(report.entities) if report.entities else "nobody"))
    return EXIT_OK


def add_parsers(subs: Any) -> None:
    """Register the analysis commands on `__main__`'s subparser table.

    Registered from here rather than written out in `__main__` so the four
    commands and the code behind them stay in one file; `__main__` is the
    dispatch table and the layer-1-to-3 bring-up, and it is already long
    enough to hide something in.
    """
    summary = subs.add_parser(
        "summarize", help="per-metric centre and spread across iterations")
    summary.add_argument("run")
    summary.add_argument("--metric", help="show only this metric")
    summary.add_argument("--complete-only", action="store_true",
                         dest="complete_only",
                         help="ignore iterations with no iteration row (still running)")
    summary.add_argument("--json", action="store_true")
    summary.set_defaults(func=cmd_summarize, pad=False)

    time_line = subs.add_parser(
        "timeline", help="what one field did, per entity, as census + episodes")
    time_line.add_argument("run")
    time_line.add_argument("--field", required=True)
    time_line.add_argument("--entity", help="e.g. player:0:9; omit to scan all")
    time_line.add_argument("--value", help="report only entities that held this")
    time_line.add_argument("--iteration", dest="iterations",
                           help="comma-separated iteration numbers")
    time_line.add_argument("--with", dest="with_field", metavar="FIELD",
                           help="also show this field's value on each episode")
    time_line.add_argument("--min-frames", type=int, default=2, dest="min_frames",
                           help="episodes shorter than this are torn reads (default 2)")
    time_line.add_argument("--certified-only", action="store_true",
                           dest="certified_only")
    time_line.add_argument("--json", action="store_true")
    time_line.set_defaults(func=cmd_timeline, pad=False)

    agreement_cmd = subs.add_parser(
        "agree", help="tick-aligned cross-iteration comparison, from the file")
    agreement_cmd.add_argument("run")
    agreement_cmd.add_argument("--iteration", dest="iterations",
                               help="comma-separated iteration numbers")
    agreement_cmd.add_argument("--field", action="append",
                               help="restrict to this field (repeatable)")
    agreement_cmd.add_argument("--repeats", type=int, default=DEFAULT_REPEATS,
                               help="iterations to hold resident (default 3)")
    agreement_cmd.set_defaults(func=cmd_agree, pad=False)

    cut = subs.add_parser("slice", help="cut a run down, keeping the schema")
    cut.add_argument("run")
    cut.add_argument("--out", required=True, help=".jsonl or .jsonl.gz")
    cut.add_argument("--entity", action="append")
    cut.add_argument("--field", action="append")
    cut.add_argument("--kind", action="append", help="default: sample")
    cut.add_argument("--iteration", help="comma-separated iteration numbers")
    cut.add_argument("--tick-from", type=int, dest="tick_from")
    cut.add_argument("--tick-to", type=int, dest="tick_to")
    cut.add_argument("--certified-only", action="store_true", dest="certified_only")
    cut.add_argument("--where", metavar="FIELD=VALUE",
                     help="select entities that ever held this value (2 passes)")
    cut.add_argument("--min-frames", type=int, default=2, dest="min_frames")
    cut.set_defaults(func=cmd_slice, pad=False)

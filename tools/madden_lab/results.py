"""The result stream: one JSON object per line, one observation per row.

## Why JSONL and not something better

* **Append-only survives a crash.** A 200-iteration run is hours. Everything
  written before the emulator died is still valid and still parseable, with no
  index to rebuild and no footer to repair. Parquet's footer, and any format
  with one, loses the whole file.
* **Heterogeneous rows share one stream.** The brief requires that an
  operator's answer land in the same result stream as the automatic
  measurements. A `sample` row, a `metric` row, an `ask` and an `answer` have
  nothing in common but their provenance, and a self-describing row per line is
  the only format where that costs nothing. A CSV would need the union of every
  column; a second file would need a join that some future script forgets.
* **No dependency.** This repo is stdlib-only, and a format that needs pandas
  installed to read is a format that is unreadable on the rig.
* **Concatenation is the merge operation.** `cat a.jsonl b.jsonl` is a valid
  file, which is why every row carries its own provenance rather than
  inheriting it from a header.

The cost is size and untyped numbers. Size arithmetic, because it bites: 200
iterations x 120 frames x 22 players x 6 fields is 3.2M rows, roughly 700 MB.
Three levers, in the order to reach for them: select fewer entities
(`EntitySelector.indices`), decimate frames (`SampleSpec.every`), and write to
a path ending `.gz`, which this module transparently gzips for about a 12x
saving. Note that `*.jsonl` is gitignored in this repo -- results are
artifacts, not commits, and the provenance fields are what make them
reproducible instead.

## Long format

One row per (iteration, frame, entity, **field**) rather than per
(iteration, frame, entity) with a bag of fields. The extra key buys the
property that matters over months: two runs that sampled different field sets
concatenate and compare without reshaping. A wide row's schema is a function of
the spec that produced it, so the day you add a field you lose the ability to
stack the new run on the old one.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterator, List, Optional

KIND_RUN = "run"
KIND_ITERATION = "iteration"
KIND_SAMPLE = "sample"
KIND_METRIC = "metric"
KIND_ASK = "ask"
KIND_ANSWER = "answer"
KIND_EVENT = "event"

ALL_KINDS = (
    KIND_RUN, KIND_ITERATION, KIND_SAMPLE, KIND_METRIC,
    KIND_ASK, KIND_ANSWER, KIND_EVENT,
)


@dataclass(frozen=True)
class Provenance:
    """Stamped onto every row. Without it a number cannot be defended later.

    `spec_digest` is the part people forget: a spec *name* says nothing about
    whether the addresses or the input script were the same on both runs, and
    "we compared baseline to patched" is worthless if the patched run also
    quietly changed the snap timing.
    """

    run_id: str
    spec: str
    spec_digest: str
    state: str
    git_rev: str
    arm: str

    def as_row(self) -> Dict[str, Any]:
        return asdict(self)


def git_revision(repo: Optional[str] = None) -> str:
    """`<sha>` or `<sha>-dirty`, or "unknown" off a checkout.

    A dirty tree is recorded rather than refused. Refusing would be purer and
    would also mean nobody could take a measurement while mid-edit, which is
    when most measurements are actually taken; "unknown" and "-dirty" are
    honest labels that an analysis can filter on.
    """
    repo = repo or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        sha = subprocess.check_output(
            ["git", "-C", repo, "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        # The measuring machine is usually a deployed copy, not a checkout --
        # the emulator lives elsewhere and the harness is copied to it. Without
        # this fallback every row taken on that machine says "unknown", which
        # is precisely the provenance the runner exists to preserve. `deploy`
        # writes REVISION next to this file; trust it, but mark it as stamped
        # so it is never mistaken for a revision read from a live checkout.
        stamped = os.path.join(os.path.dirname(os.path.abspath(__file__)), "REVISION")
        try:
            with open(stamped, "r") as handle:
                value = handle.read().strip()
            return value or "unknown"
        except OSError:
            return "unknown"
    try:
        dirty = subprocess.check_output(
            ["git", "-C", repo, "status", "--porcelain"],
            stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        dirty = ""
    return sha + ("-dirty" if dirty else "")


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def _open_write(path: str, append: bool = False):
    mode = "at" if append else "wt"
    if path.endswith(".gz"):
        return gzip.open(path, mode, encoding="utf-8")
    return io.open(path, mode, encoding="utf-8", newline="\n")


def _open_read(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return io.open(path, "rt", encoding="utf-8")


class JsonlSink:
    """Writes rows. Flushes on iteration boundaries, not on every row.

    Per-row flushing costs an order of magnitude on a three-million-row run;
    per-iteration flushing bounds the loss from a hard kill to one iteration,
    which is already discarded as incomplete.
    """

    def __init__(self, path: str, provenance: Provenance, append: bool = False):
        self.path = path
        self.provenance = provenance
        self._fh = _open_write(path, append=append)
        self._base = provenance.as_row()
        self.rows_written = 0

    def write(self, kind: str, iteration: Optional[int] = None, **fields: Any) -> None:
        row = dict(self._base)
        row["kind"] = kind
        row["iteration"] = iteration
        row.update(fields)
        self._fh.write(json.dumps(row, separators=(",", ":"), default=_jsonable) + "\n")
        self.rows_written += 1

    def flush(self) -> None:
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.flush()
        finally:
            self._fh.close()

    def __enter__(self) -> "JsonlSink":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


class MemorySink:
    """The same interface, into a list. Used by the tests and by `snapshot`."""

    def __init__(self, provenance: Optional[Provenance] = None):
        self.provenance = provenance
        self.rows: List[Dict[str, Any]] = []
        self.path = "<memory>"
        self.rows_written = 0

    def write(self, kind: str, iteration: Optional[int] = None, **fields: Any) -> None:
        row = self.provenance.as_row() if self.provenance else {}
        row["kind"] = kind
        row["iteration"] = iteration
        row.update(fields)
        self.rows.append(row)
        self.rows_written += 1

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self) -> "MemorySink":
        return self

    def __exit__(self, *_exc) -> None:
        pass

    def of_kind(self, kind: str) -> List[Dict[str, Any]]:
        return [row for row in self.rows if row.get("kind") == kind]


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


def read_rows(path: str) -> Iterator[Dict[str, Any]]:
    """Stream rows. A malformed line is reported with its number, not swallowed.

    Truncation is expected -- a killed run leaves a partial last line -- so a
    bad *final* line is tolerated silently while a bad line anywhere else
    raises. Anything else either hides real corruption or makes every
    interrupted run unreadable.
    """
    with _open_read(path) as handle:
        pending: Optional[str] = None
        pending_no = 0
        for line_no, line in enumerate(handle, 1):
            if pending is not None:
                yield _parse_row(pending, pending_no, path)
            stripped = line.strip()
            if not stripped:
                pending = None
                continue
            pending, pending_no = stripped, line_no
        if pending is not None:
            try:
                yield _parse_row(pending, pending_no, path)
            except ValueError:
                pass  # truncated tail of a killed run


def _parse_row(line: str, line_no: int, path: str) -> Dict[str, Any]:
    try:
        row = json.loads(line)
    except ValueError as exc:
        raise ValueError("%s:%d is not JSON: %s" % (path, line_no, exc))
    if not isinstance(row, dict):
        raise ValueError("%s:%d is not a row object" % (path, line_no))
    return row


@dataclass
class RunFile:
    """A parsed result file, grouped by row kind."""

    path: str
    header: Dict[str, Any]
    iterations: List[Dict[str, Any]]
    metrics: List[Dict[str, Any]]
    asks: List[Dict[str, Any]]
    answers: List[Dict[str, Any]]
    samples: int
    events: List[Dict[str, Any]]

    @property
    def arm(self) -> str:
        return str(self.header.get("arm") or "?")

    @property
    def spec_digest(self) -> str:
        return str(self.header.get("spec_digest") or "?")

    def metric_values(self, name: str) -> List[float]:
        """Non-null values for one metric, in iteration order."""
        pairs = [(row.get("iteration"), row.get("value"))
                 for row in self.metrics if row.get("metric") == name]
        pairs = [(i, v) for i, v in pairs if v is not None and isinstance(v, (int, float))]
        pairs.sort(key=lambda pair: (pair[0] is None, pair[0]))
        return [float(value) for _i, value in pairs]

    def metric_missing(self, name: str) -> int:
        return sum(1 for row in self.metrics
                   if row.get("metric") == name and row.get("value") is None)

    def metric_names(self) -> List[str]:
        seen: List[str] = []
        for row in self.metrics:
            name = row.get("metric")
            if name and name not in seen:
                seen.append(str(name))
        return seen

    def iteration_digests(self) -> List[str]:
        return [str(row.get("digest")) for row in self.iterations
                if row.get("digest") and row.get("status") == "ok"]


def load_run(path: str) -> RunFile:
    """Read a whole result file into memory, keeping only what analysis needs.

    Sample rows are counted, not kept: on a real run they are 99.9% of the file
    and gigabytes of them, and every question `compare` asks is answered by the
    metric rows the runner already reduced. A tool that needs the raw frames
    should stream `read_rows` itself.
    """
    header: Dict[str, Any] = {}
    iterations: List[Dict[str, Any]] = []
    metrics: List[Dict[str, Any]] = []
    asks: List[Dict[str, Any]] = []
    answers: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    samples = 0
    for row in read_rows(path):
        kind = row.get("kind")
        if kind == KIND_RUN and not header:
            header = row
        elif kind == KIND_ITERATION:
            iterations.append(row)
        elif kind == KIND_METRIC:
            metrics.append(row)
        elif kind == KIND_ASK:
            asks.append(row)
        elif kind == KIND_ANSWER:
            answers.append(row)
        elif kind == KIND_EVENT:
            events.append(row)
        elif kind == KIND_SAMPLE:
            samples += 1
    if not header:
        # Concatenated or hand-edited files may have lost the header. Recover
        # what provenance we can from the first row rather than refusing.
        for row in read_rows(path):
            header = {key: row.get(key) for key in
                      ("run_id", "spec", "spec_digest", "state", "git_rev", "arm")}
            break
    return RunFile(path=path, header=header, iterations=iterations, metrics=metrics,
                   asks=asks, answers=answers, samples=samples, events=events)


def append_answer(path: str, ask_id: str, iteration: int, value: str,
                  operator: str = "", note: str = "") -> Dict[str, Any]:
    """Append an operator's answer to the run file it belongs to.

    Provenance is copied from the ask row rather than regenerated, so the
    answer carries the git revision the *measurement* was taken at, not the one
    the human happened to answer at. Those differ whenever anyone commits
    between running and watching, which is most of the time.
    """
    run = load_run(path)
    match = None
    for row in run.asks:
        if row.get("ask") == ask_id and row.get("iteration") == iteration:
            match = row
            break
    if match is None:
        raise KeyError("%s has no ask %r for iteration %s" % (path, ask_id, iteration))
    choices = match.get("choices") or []
    if choices and value not in choices:
        raise ValueError("answer %r is not one of %s" % (value, ", ".join(choices)))
    answer = {key: match.get(key) for key in
              ("run_id", "spec", "spec_digest", "state", "git_rev", "arm")}
    answer.update({
        "kind": KIND_ANSWER,
        "iteration": iteration,
        "ask": ask_id,
        "value": value,
        "operator": operator,
        "note": note,
        "answered_at": time.time(),
    })
    with _open_write(path, append=True) as handle:
        handle.write(json.dumps(answer, separators=(",", ":"), default=_jsonable) + "\n")
    return answer


def pending_asks(path: str) -> List[Dict[str, Any]]:
    """Asks with no answer yet, in the order they were raised."""
    run = load_run(path)
    answered = {(row.get("ask"), row.get("iteration")) for row in run.answers}
    return [row for row in run.asks
            if (row.get("ask"), row.get("iteration")) not in answered]

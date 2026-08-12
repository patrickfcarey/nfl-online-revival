"""How many frames would this patch's gate chain actually pass? Count first.

    python3 tools/gatecensus.py extract/slot9_baseline_dt3.jsonl \\
        --gate 'helper_kind8: self.engagement == 8' \\
        --gate 'defender_kind: link.engagement in 5,6' \\
        --gate 'defender_role: link.dt_role == 2' \\
        --gate 'helper_role:   self.dt_role == 1' \\
        --gate 'sides_differ:  self.side != link.side' \\
        --canary 5

Why this exists -- the P8/P8b incident, in full, because it is the most
expensive avoidable hour of the campaign (``docs/motion-block-cave.md``):

* **P8** deployed a 116-word cave behind a seven-test gate chain. Its
  execution canary read 5 driven frames per play. The diagnosis written from
  the trace *by eye* was "the defender's kind gate starves it".
* **P8b** widened exactly that gate ({5,6} -> {4,5,6}), redeployed, re-ran
  three iterations. **Canary still 5.** "So the defender's kind was never the
  limiter, and my diagnosis of P8 was wrong."
* The answer then came "from data already on disk -- no patch, no rig time":
  counting each gate's passing frames gave helper-kind-8 35, the role pair 56,
  all four together 35 -- **against a measured canary of 5**. The gates were
  never the limiter. The *host function* runs about one frame in seven, and
  the design's central premise ("proven to run every attached frame") was
  false. No gate widening could ever have fixed it.

Every number in that closing analysis was computable before P8 was ever
built, from a baseline trace the project already had. This module makes it
one command, and it answers three questions that the session answered with
two deploy cycles:

**1. Which gate is binding?**  The ladder evaluates the chain in order and
reports the survivors after each test. The gate where the count collapses is
the limiter -- no inference, no eye.

**2. What would widening a gate buy?**  The leave-one-out column re-counts
with each gate removed. P8b's null result is visible here for free: dropping
the defender-kind gate leaves the conjunction unchanged.

**3. Are the gates the problem at all, or is the host?**  Give it the measured
canary with ``--canary``. Predicted-passing-frames versus actually-executed
frames is the host's duty cycle. A large gap means the hook is in the wrong
function, and *no* gate edit will help -- the P8b lesson, stated as a number.

The trace format is the harness's own long-form JSONL (one record per
iteration/frame/entity/field), so any run already on disk is an input. A
predicted count is necessary, not sufficient: a savestate-derived or
baseline-derived count says what the gate *would* pass in the world that was
recorded, and a patch that changes behaviour changes the counts too. Use it
to kill hopeless patches cheaply, not to bless promising ones.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

#: An engagement_link word is ``kind | side<<8 | index<<16``; kind 1 is a
#: player handle. The cave decodes it with exactly this masking
#: (``andi 0xFF`` / ``srl 8`` / ``srl 16``), so this mirrors the engine.
HANDLE_KIND_PLAYER = 1

PlayerKey = Tuple[int, int]                      # (side, index)


def decode_link(word: int) -> Optional[PlayerKey]:
    """``(side, index)`` for a player handle, else ``None``.

    ``None`` covers both a null link and a non-player handle -- the two cases
    the cave's ``handle kind == 1`` test rejects.
    """
    if not isinstance(word, int) or word <= 0:
        return None
    if (word & 0xFF) != HANDLE_KIND_PLAYER:
        return None
    return ((word >> 8) & 0xFF, (word >> 16) & 0xFF)


class Frame:
    """One (iteration, frame) instant: every entity's sampled fields."""

    def __init__(self, iteration: int, frame: int) -> None:
        self.iteration = iteration
        self.frame = frame
        self.players: Dict[PlayerKey, Dict[str, object]] = {}
        self.game: Dict[str, object] = {}

    def field(self, key: PlayerKey, name: str) -> object:
        """A player's field, with ``side``/``index`` synthesised from the key.

        The trace keys players by entity name, so side and index are not
        sampled fields -- but the gate chain tests them (the cave's
        "sides differ" test), so they are exposed here.
        """
        if name == "side":
            return key[0]
        if name == "index":
            return key[1]
        rec = self.players.get(key)
        return None if rec is None else rec.get(name)


class Trace:
    """A harness JSONL run, indexed by (iteration, frame)."""

    def __init__(self, frames: Sequence[Frame], meta: Optional[dict] = None):
        self.frames = list(frames)
        self.meta = meta or {}

    @property
    def iterations(self) -> List[int]:
        return sorted({f.iteration for f in self.frames})

    @classmethod
    def from_jsonl(cls, path: str, iteration: Optional[int] = None) -> "Trace":
        index: Dict[Tuple[int, int], Frame] = {}
        meta: dict = {}
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("kind") == "run":
                    meta = rec
                    continue
                if rec.get("kind") != "sample":
                    continue
                it = rec.get("iteration")
                if it is None or (iteration is not None and it != iteration):
                    continue
                key = (it, rec.get("frame", 0))
                frame = index.get(key)
                if frame is None:
                    frame = index[key] = Frame(*key)
                entity = rec.get("entity", "")
                if entity.startswith("player:"):
                    _, side, idx = entity.split(":")
                    frame.players.setdefault(
                        (int(side), int(idx)), {})[rec["field"]] = rec["value"]
                else:
                    frame.game[rec["field"]] = rec["value"]
        return cls([index[k] for k in sorted(index)], meta)


class Gate(NamedTuple):
    """One test in a patch's gate chain, in the order the cave applies it."""

    name: str
    subject: str            #: 'self' or 'link'
    field: str
    op: str                 #: '==' '!=' 'in' 'notin' '<' '>' '<=' '>='
    value: object           #: a literal, a set, or ('self'|'link', field)

    @classmethod
    def parse(cls, spec: str) -> "Gate":
        """``'name: self.field == value'``; value may be ``link.field``.

        ``in``/``notin`` take a comma-separated list (``in 5,6``).
        """
        if ":" not in spec:
            raise ValueError("gate needs 'name: subject.field op value': %r"
                             % spec)
        name, body = spec.split(":", 1)
        parts = body.split()
        if len(parts) < 3:
            raise ValueError("gate body needs 'subject.field op value': %r"
                             % body)
        lhs, op, rhs = parts[0], parts[1], " ".join(parts[2:])
        if "." not in lhs:
            raise ValueError("subject must be self.<field> or link.<field>")
        subject, field = lhs.split(".", 1)
        if subject not in ("self", "link"):
            raise ValueError("subject must be 'self' or 'link', got %r"
                             % subject)
        if op not in ("==", "!=", "in", "notin", "<", ">", "<=", ">="):
            raise ValueError("unknown operator %r" % op)
        value: object
        if op in ("in", "notin"):
            value = frozenset(_literal(v) for v in rhs.split(","))
        elif rhs.startswith(("self.", "link.")):
            sub, fld = rhs.split(".", 1)
            value = (sub, fld)
        else:
            value = _literal(rhs)
        return cls(name.strip(), subject, field, op, value)

    def evaluate(self, frame: Frame, subject: PlayerKey,
                 link_field: str) -> bool:
        """Does this test pass for *subject* in *frame*?

        A field that was never sampled, or a link that does not resolve to a
        player, is a **rejection** -- the cave's own handle-kind test rejects
        exactly those. Unsampled fields are reported separately by the census
        so an all-rejecting gate is never mistaken for a real limiter.
        """
        who = self._resolve(frame, subject, link_field, self.subject)
        if who is None:
            return False
        left = frame.field(who, self.field)
        if left is None:
            return False
        if isinstance(self.value, tuple):
            other = self._resolve(frame, subject, link_field, self.value[0])
            if other is None:
                return False
            right = frame.field(other, self.value[1])
            if right is None:
                return False
        else:
            right = self.value
        return _apply(self.op, left, right)

    @staticmethod
    def _resolve(frame: Frame, subject: PlayerKey, link_field: str,
                 which: str) -> Optional[PlayerKey]:
        if which == "self":
            return subject
        word = frame.field(subject, link_field)
        return decode_link(word) if isinstance(word, int) else None


def _literal(text: str) -> object:
    text = text.strip()
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    return text


def _apply(op: str, left: object, right: object) -> bool:
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if op == "in":
        return left in right                      # type: ignore[operator]
    if op == "notin":
        return left not in right                  # type: ignore[operator]
    try:
        if op == "<":
            return left < right                   # type: ignore[operator]
        if op == ">":
            return left > right                   # type: ignore[operator]
        if op == "<=":
            return left <= right                  # type: ignore[operator]
        return left >= right                      # type: ignore[operator]
    except TypeError:
        return False


class Census(NamedTuple):
    """What a gate chain would have passed, over a recorded world."""

    candidates: int                     #: (frame, subject) pairs examined
    per_gate: Dict[str, int]            #: each gate alone
    ladder: List[Tuple[str, int]]       #: survivors after each gate, in order
    leave_one_out: Dict[str, int]       #: survivors with that gate removed
    unsampled: Dict[str, bool]          #: gate references a field never seen
    frames: int                         #: distinct frames in the trace

    @property
    def conjunction(self) -> int:
        """Frames-times-subjects the whole chain would pass."""
        return self.ladder[-1][1] if self.ladder else self.candidates

    @property
    def binding_gate(self) -> Optional[str]:
        """The gate that actually stops the chain.

        When nothing survives, that is the first gate to reach zero -- not the
        gate with the biggest drop, which is usually just the first one and
        tells you nothing. When the chain does pass, it is the largest single
        loss along the ladder.
        """
        if not self.ladder:
            return None
        if self.conjunction == 0:
            for name, survivors in self.ladder:
                if survivors == 0:
                    return name
        best, drop = None, -1
        prev = self.candidates
        for name, survivors in self.ladder:
            if prev - survivors > drop:
                best, drop = name, prev - survivors
            prev = survivors
        return best

    @property
    def best_widening(self) -> Optional[Tuple[str, int]]:
        """The gate whose removal buys the most, and what it would buy.

        ``None`` when no single gate is worth widening -- the case P8b paid a
        deploy cycle to learn.
        """
        if not self.leave_one_out:
            return None
        name = max(self.leave_one_out, key=lambda n: self.leave_one_out[n])
        gain = self.leave_one_out[name]
        return (name, gain) if gain > self.conjunction else None

    def report(self) -> str:
        lines = ["%d frames, %d (frame, subject) candidates"
                 % (self.frames, self.candidates),
                 "",
                 "  %-18s %8s %8s %8s" % ("gate", "alone", "ladder", "if cut"),
                 "  " + "-" * 46]
        for name, survivors in self.ladder:
            flag = "  <- never sampled" if self.unsampled.get(name) else ""
            lines.append("  %-18s %8d %8d %8d%s"
                         % (name, self.per_gate[name], survivors,
                            self.leave_one_out[name], flag))
        lines.append("")
        lines.append("  chain passes on %d of %d candidates"
                     % (self.conjunction, self.candidates))
        if self.binding_gate:
            lines.append("  binding gate: %s" % self.binding_gate)
        widen = self.best_widening
        if widen:
            lines.append("  widening %s would buy %d (from %d)"
                         % (widen[0], widen[1], self.conjunction))
        else:
            lines.append("  no single gate is worth widening -- removing any "
                         "one of them changes nothing")
        if self.conjunction:
            inert = [n for n, v in self.leave_one_out.items()
                     if v == self.conjunction]
            if inert:
                lines.append("  inert gates (cutting them changes nothing): %s"
                             % ", ".join(sorted(inert)))
        return "\n".join(lines)


def census(trace: Trace, gates: Sequence[Gate],
           subjects: Optional[Iterable[PlayerKey]] = None,
           link_field: str = "engagement_link") -> Census:
    """Evaluate *gates* against every (frame, subject) pair in *trace*."""
    per_gate = {g.name: 0 for g in gates}
    ladder_counts = [0] * len(gates)
    loo_counts = {g.name: 0 for g in gates}
    seen_field = {g.name: False for g in gates}
    candidates = 0

    for frame in trace.frames:
        keys = list(subjects) if subjects is not None else sorted(frame.players)
        for key in keys:
            if key not in frame.players:
                continue
            candidates += 1
            results = []
            for gate in gates:
                ok = gate.evaluate(frame, key, link_field)
                results.append(ok)
                if ok:
                    per_gate[gate.name] += 1
                    seen_field[gate.name] = True
            # short-circuit ladder: survivors after each prefix
            for i in range(len(gates)):
                if all(results[: i + 1]):
                    ladder_counts[i] += 1
                else:
                    break
            # leave-one-out: what removing each single gate would buy
            for i, gate in enumerate(gates):
                if all(r for j, r in enumerate(results) if j != i):
                    loo_counts[gate.name] += 1

    return Census(candidates=candidates,
                  per_gate=per_gate,
                  ladder=[(g.name, ladder_counts[i])
                          for i, g in enumerate(gates)],
                  leave_one_out=loo_counts,
                  unsampled={g.name: not seen_field[g.name] for g in gates},
                  frames=len(trace.frames))


class HostVerdict(NamedTuple):
    """Did the hook's host function actually run when the gates allowed it?"""

    predicted: int
    measured: int
    verdict: str
    detail: str

    def report(self) -> str:
        return ("  predicted %d gate-passing frames, canary measured %d\n"
                "  VERDICT: %s\n  %s"
                % (self.predicted, self.measured, self.verdict, self.detail))


def host_verdict(predicted: int, measured: int,
                 tolerance: float = 0.9) -> HostVerdict:
    """Compare a gate-census prediction against a measured execution canary.

    This is the P8b test, done arithmetically. If the canary is far below the
    predicted count, the *host* is not ticking on every frame the gates allow
    and no amount of gate widening will change the outcome.
    """
    if predicted == 0:
        return HostVerdict(
            predicted, measured, "GATE-BOUND",
            "The chain passes on no recorded frame. The patch cannot fire in "
            "this world; fix the gate or the savestate before deploying.")
    ratio = measured / predicted
    if ratio >= tolerance:
        return HostVerdict(
            predicted, measured, "HOST-TICKS",
            "The host runs on essentially every gate-passing frame, so the "
            "gate chain is the only lever. Widening a gate will move the "
            "count.")
    one_in = predicted / measured if measured else float("inf")
    return HostVerdict(
        predicted, measured, "HOST-BOUND",
        "The host executes roughly 1 frame in %s of those the gates allow. "
        "The hook is in the wrong function -- no gate edit can fix this "
        "(the P8b null). Count a candidate host before re-hooking."
        % ("%.1f" % one_in if measured else "infinity"))


def main(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Count the frames a patch's gate chain would pass.")
    ap.add_argument("trace", help="harness JSONL run")
    ap.add_argument("--gate", action="append", default=[], metavar="SPEC",
                    help="'name: self.field op value', repeatable, in cave "
                         "order")
    ap.add_argument("--canary", type=int, default=None,
                    help="measured execution-canary count, to test the host")
    ap.add_argument("--iteration", type=int, default=None,
                    help="restrict to one iteration (default: all)")
    ap.add_argument("--link-field", default="engagement_link",
                    help="field holding the handle that 'link' resolves")
    args = ap.parse_args(list(argv))

    if not args.gate:
        ap.error("at least one --gate is required")
    gates = [Gate.parse(s) for s in args.gate]
    trace = Trace.from_jsonl(args.trace, iteration=args.iteration)
    result = census(trace, gates, link_field=args.link_field)
    print(result.report())
    if args.canary is not None:
        print()
        print(host_verdict(result.conjunction, args.canary).report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

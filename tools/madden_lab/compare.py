"""Two result sets in, a verdict out -- with the honesty attached.

## Why this file is deliberately awkward to get a headline out of

A comparison that cannot distinguish a real behavioural change from noise is
worse than no comparison, because it will be believed. So nothing here reports
a bare difference of means, and three specific ways of being wrong are made
structurally impossible:

1. **"The patched build averages 3.2, the baseline 2.9, so it works."** A
   difference of means with no spread is not a result. Every metric here
   reports an effect size (Hedges' g and Cliff's delta), a bootstrap interval
   on the difference, and a permutation p-value.

2. **"No significant difference, so the patch does nothing."** Absence of
   evidence gets reported as what it actually is: an upper bound. Every
   non-significant metric states the *minimum detectable effect* this run had
   the power to find, and the n that would have been needed for the effect
   actually observed. "We could only have detected a change of 0.8 yards or
   more" is a finding; "no difference" is not.

3. **"n = 200, so this is precise."** If the trials were deterministic, the
   200 iterations are 200 copies of one play and n_effective is 1. This module
   reads the per-iteration digests the runner writes and refuses to run
   statistics on a degenerate arm, reporting `deterministic` instead. This is
   the failure mode most likely to produce a confident wrong answer here,
   because the arithmetic works perfectly on identical numbers -- zero variance
   makes any difference infinitely significant.

## The methods, and why these ones

Everything is stdlib. No numpy, no scipy: this has to run on the rig.

* **Permutation test** for the p-value, not a t-test. Metrics here are counts,
  frame indices and 0/1 indicators -- bounded, discrete, often bimodal. The
  t-test's normality assumption is wrong for most of them, and the permutation
  test needs only exchangeability under the null. Exact when the number of
  splits is small enough to enumerate, sampled (seeded, so the report is
  reproducible) otherwise.
* **Hedges' g** rather than Cohen's d: the small-sample correction matters at
  the n these runs actually reach.
* **Cliff's delta** alongside it, because g is a ratio to a standard deviation
  that means very little for a Bernoulli metric like "was he ever assigned a
  target". Cliff's delta is just "how often does one arm beat the other", is
  defined for any ordinal metric, and is unmoved by the shape of the
  distribution.
* **Percentile bootstrap** for intervals. Not BCa -- the acceleration term is
  more machinery than the honesty it buys at this n, and the bias is stated
  below rather than corrected.
* **Holm** across metrics. A spec with twelve metrics gets a false positive
  about half the time at alpha 0.05, and the whole point of the run is to be
  able to believe the one that lit up.

## Stated limitations

* **The savestate is the population.** Every iteration starts from one saved
  frame: one play, one formation, one set of personnel, one field position. A
  result generalises to *that situation*, and nothing here can tell you it
  holds on any other. Running the same trial from several savestates and
  finding the same effect is the only cure, and is a matter for the spec, not
  for this module.
* **Iterations are assumed exchangeable within an arm.** If the emulator
  drifts over a long run, they are not. `drift()` regresses each metric on
  iteration index (Spearman) and is reported unprompted when it is large,
  because a drifting arm makes the permutation p-value meaningless.
* **Arms are compared between files, not interleaved.** Baseline and patched
  are usually run at different times on the same machine, so any time-varying
  confounder is fully aliased onto the treatment. Interleaving arms within one
  session would fix it; the runner supports it (two runs, `--append`), nothing
  enforces it.
* **Percentile bootstrap intervals under-cover for skewed metrics at small n**,
  typically by a few percent. Treat a CI that just excludes zero as suggestive.
* **Operator answers are not blinded end-to-end.** The prompts use opaque arm
  labels, but the same person usually runs both arms and knows which is which.
* **Missing measurements are reported, not imputed.** A metric that returned
  None on 40% of iterations is a broken metric, and the count is printed next
  to the estimate so it cannot be read past.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import random
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .results import RunFile, load_run

#: Two-sided alpha and target power for every "how many would I need" answer.
ALPHA = 0.05
POWER = 0.80
#: Below this, no statistics are attempted -- the verdict is `underpowered`.
MIN_N = 5
#: Enumerate permutations exactly below this many splits, sample above it.
EXACT_PERMUTATION_LIMIT = 20000
PERMUTATION_SAMPLES = 20000
BOOTSTRAP_SAMPLES = 5000

VERDICT_CHANGE = "change"
VERDICT_NO_EVIDENCE = "no-evidence"
VERDICT_UNDERPOWERED = "underpowered"
VERDICT_DETERMINISTIC = "deterministic"
VERDICT_MISSING = "missing"


# --------------------------------------------------------------------------
# Small statistics, stdlib only
# --------------------------------------------------------------------------


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def variance(xs: Sequence[float]) -> float:
    """Sample variance (n-1). Zero for n < 2 rather than an exception."""
    if len(xs) < 2:
        return 0.0
    mu = mean(xs)
    return sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)


def stdev(xs: Sequence[float]) -> float:
    return math.sqrt(variance(xs))


def median(xs: Sequence[float]) -> float:
    if not xs:
        return float("nan")
    ordered = sorted(xs)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def quantile(xs: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile. Used for IQR and bootstrap intervals."""
    if not xs:
        return float("nan")
    ordered = sorted(xs)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    low = int(math.floor(pos))
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


def pooled_sd(a: Sequence[float], b: Sequence[float]) -> float:
    na, nb = len(a), len(b)
    if na + nb < 3:
        return 0.0
    num = (na - 1) * variance(a) + (nb - 1) * variance(b)
    return math.sqrt(num / (na + nb - 2))


def hedges_g(a: Sequence[float], b: Sequence[float]) -> float:
    """Standardised mean difference, small-sample corrected.

    Returns 0.0 rather than infinity when the pooled SD is zero: identical
    constant arms are not an infinitely large effect, they are a case for the
    `deterministic` verdict, which is decided elsewhere.
    """
    sd = pooled_sd(a, b)
    if sd == 0:
        return 0.0
    d = (mean(b) - mean(a)) / sd
    dof = len(a) + len(b) - 2
    if dof <= 2:
        return d
    correction = 1.0 - (3.0 / (4.0 * dof - 1.0))  # Hedges' J
    return d * correction


def cliffs_delta(a: Sequence[float], b: Sequence[float]) -> float:
    """P(b > a) - P(b < a), in [-1, 1]. Non-parametric, shape-free."""
    if not a or not b:
        return float("nan")
    greater = lesser = 0
    for x in b:
        for y in a:
            if x > y:
                greater += 1
            elif x < y:
                lesser += 1
    return (greater - lesser) / float(len(a) * len(b))


def cliffs_label(delta: float) -> str:
    """Romano's thresholds. A convention, not a law -- reported as a hint."""
    if delta != delta:  # NaN
        return "?"
    size = abs(delta)
    if size < 0.147:
        return "negligible"
    if size < 0.330:
        return "small"
    if size < 0.474:
        return "medium"
    return "large"


def permutation_p(a: Sequence[float], b: Sequence[float], seed: int = 0) -> Tuple[float, bool]:
    """Two-sided p for the difference of means. Returns (p, was_exact).

    The seed is derived from the data by the caller, so the same two files
    always produce the same p. A report whose number moves when you re-run it
    is a report nobody can quote.
    """
    na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        return float("nan"), False
    pool = list(a) + list(b)
    observed = abs(mean(b) - mean(a))
    total = sum(pool)

    try:
        splits = math.comb(na + nb, na)
    except AttributeError:  # pragma: no cover - Python < 3.8
        splits = float("inf")

    if splits <= EXACT_PERMUTATION_LIMIT:
        extreme = 0
        for combo in itertools.combinations(range(na + nb), na):
            left = sum(pool[i] for i in combo)
            diff = abs((total - left) / nb - left / na)
            if diff >= observed - 1e-12:
                extreme += 1
        return extreme / float(splits), True

    rng = random.Random(seed)
    extreme = 0
    shuffled = list(pool)
    for _ in range(PERMUTATION_SAMPLES):
        rng.shuffle(shuffled)
        left = sum(shuffled[:na])
        diff = abs((total - left) / nb - left / na)
        if diff >= observed - 1e-12:
            extreme += 1
    # +1 in both terms: a sampled p is never allowed to be exactly zero,
    # because "p = 0" from 20000 draws is a statement the data cannot support.
    return (extreme + 1) / float(PERMUTATION_SAMPLES + 1), False


def bootstrap_ci(a: Sequence[float], b: Sequence[float], seed: int = 0,
                 samples: int = BOOTSTRAP_SAMPLES,
                 level: float = 0.95) -> Tuple[float, float]:
    """Percentile CI for mean(b) - mean(a)."""
    if not a or not b:
        return (float("nan"), float("nan"))
    rng = random.Random(seed ^ 0x5EED)
    na, nb = len(a), len(b)
    diffs = []
    for _ in range(samples):
        ra = sum(a[rng.randrange(na)] for _ in range(na)) / na
        rb = sum(b[rng.randrange(nb)] for _ in range(nb)) / nb
        diffs.append(rb - ra)
    tail = (1.0 - level) / 2.0
    return (quantile(diffs, tail), quantile(diffs, 1.0 - tail))


def _z(p: float) -> float:
    return NormalDist().inv_cdf(p)


def minimum_detectable_effect(sd: float, na: int, nb: int,
                              alpha: float = ALPHA, power: float = POWER) -> float:
    """Smallest true difference this run had `power` odds of detecting.

    Normal approximation, which is optimistic for the small n and discrete
    metrics common here -- so read it as a floor on the floor. Its job is to
    stop "no significant difference" being read as "no difference", and it does
    that job even when it is 20% off.
    """
    if sd <= 0 or na < 2 or nb < 2:
        return float("nan")
    return (_z(1 - alpha / 2.0) + _z(power)) * sd * math.sqrt(1.0 / na + 1.0 / nb)


def n_required(sd: float, effect: float, alpha: float = ALPHA,
               power: float = POWER) -> Optional[int]:
    """Iterations *per arm* needed to detect `effect` at `power`."""
    if sd <= 0 or not effect or effect != effect:
        return None
    z_sum = _z(1 - alpha / 2.0) + _z(power)
    return int(math.ceil(2.0 * (z_sum ** 2) * (sd ** 2) / (effect ** 2)))


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Rank correlation, for the drift check. Ties get average ranks."""
    if len(xs) != len(ys) or len(xs) < 3:
        return float("nan")
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = mean(rx), mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")


def _ranks(xs: Sequence[float]) -> List[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        average = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def holm(pvalues: Sequence[float]) -> List[float]:
    """Holm-Bonferroni step-down. Preserves input order; NaNs pass through."""
    indexed = [(p, i) for i, p in enumerate(pvalues) if p == p]
    indexed.sort()
    adjusted = [float("nan")] * len(pvalues)
    m = len(indexed)
    running = 0.0
    for rank, (p, index) in enumerate(indexed):
        value = min(1.0, (m - rank) * p)
        running = max(running, value)  # enforce monotonicity
        adjusted[index] = running
    return adjusted


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------


@dataclass
class MetricComparison:
    name: str
    n_a: int
    n_b: int
    missing_a: int
    missing_b: int
    mean_a: float
    mean_b: float
    sd_a: float
    sd_b: float
    median_a: float
    median_b: float
    iqr_a: Tuple[float, float]
    iqr_b: Tuple[float, float]
    diff: float
    diff_ci: Tuple[float, float]
    g: float
    g_ci: Tuple[float, float]
    delta: float
    delta_label: str
    p: float
    p_exact: bool
    p_holm: float
    mde: float
    n_needed: Optional[int]
    drift_a: float
    drift_b: float
    verdict: str
    notes: List[str] = field(default_factory=list)


@dataclass
class ComparisonReport:
    baseline: str
    patched: str
    arm_a: str
    arm_b: str
    spec_a: str
    spec_b: str
    metrics: List[MetricComparison]
    warnings: List[str]

    def changed(self) -> List[MetricComparison]:
        return [m for m in self.metrics if m.verdict == VERDICT_CHANGE]


def compare_files(baseline_path: str, patched_path: str,
                  min_n: int = MIN_N) -> ComparisonReport:
    return compare_runs(load_run(baseline_path), load_run(patched_path), min_n=min_n)


def compare_runs(a: RunFile, b: RunFile, min_n: int = MIN_N) -> ComparisonReport:
    """Compare every metric present in either file.

    Preconditions that are checked and warned about rather than enforced: the
    two runs should share a spec digest, and neither arm should be
    deterministic. Both are stated at the top of the report because both
    invalidate everything under them, and neither is visible in the numbers.
    """
    warnings: List[str] = []
    if a.spec_digest != b.spec_digest:
        warnings.append(
            "SPEC MISMATCH: %s vs %s. The two runs did not execute the same "
            "trial -- different addresses, inputs or sampling. Any difference "
            "below may be the spec, not the build."
            % (a.spec_digest, b.spec_digest))
    if a.header.get("state") != b.header.get("state"):
        warnings.append("different savestates: %r vs %r"
                        % (a.header.get("state"), b.header.get("state")))

    det_a = _degenerate(a)
    det_b = _degenerate(b)
    for run, degenerate in ((a, det_a), (b, det_b)):
        if degenerate:
            warnings.append(
                "%s: all %d iterations produced an identical sample stream. "
                "These are not independent samples -- n_effective is 1. No "
                "p-value below that arm is meaningful."
                % (run.path, len(run.iteration_digests())))
    if not warnings:
        warnings.append(
            "iterations diverge within each arm, so they are treated as draws "
            "from a distribution. All results are conditional on this one "
            "savestate: they describe this play, not the game.")

    names: List[str] = []
    for run in (a, b):
        for name in run.metric_names():
            if name not in names:
                names.append(name)

    raw: List[MetricComparison] = []
    for name in names:
        raw.append(_compare_metric(name, a, b, det_a or det_b, min_n))
    adjusted = holm([m.p for m in raw])
    for comparison, p_adj in zip(raw, adjusted):
        comparison.p_holm = p_adj
        comparison.verdict = _verdict(comparison, det_a or det_b, min_n)

    return ComparisonReport(baseline=a.path, patched=b.path, arm_a=a.arm, arm_b=b.arm,
                            spec_a=a.spec_digest, spec_b=b.spec_digest,
                            metrics=raw, warnings=warnings)


def _degenerate(run: RunFile) -> bool:
    digests = run.iteration_digests()
    return len(digests) >= 2 and len(set(digests)) == 1


def _compare_metric(name: str, a: RunFile, b: RunFile, degenerate: bool,
                    min_n: int) -> MetricComparison:
    xs = a.metric_values(name)
    ys = b.metric_values(name)
    notes: List[str] = []

    missing_a, missing_b = a.metric_missing(name), b.metric_missing(name)
    for label, missing, total in (("baseline", missing_a, missing_a + len(xs)),
                                  ("patched", missing_b, missing_b + len(ys))):
        if total and missing / float(total) > 0.1:
            notes.append("%s: %d/%d iterations produced no value (%.0f%%)"
                         % (label, missing, total, 100.0 * missing / total))

    if not xs or not ys:
        return MetricComparison(
            name=name, n_a=len(xs), n_b=len(ys), missing_a=missing_a,
            missing_b=missing_b, mean_a=mean(xs), mean_b=mean(ys),
            sd_a=stdev(xs), sd_b=stdev(ys), median_a=median(xs), median_b=median(ys),
            iqr_a=(quantile(xs, .25), quantile(xs, .75)),
            iqr_b=(quantile(ys, .25), quantile(ys, .75)),
            diff=float("nan"), diff_ci=(float("nan"), float("nan")),
            g=float("nan"), g_ci=(float("nan"), float("nan")),
            delta=float("nan"), delta_label="?", p=float("nan"), p_exact=False,
            p_holm=float("nan"), mde=float("nan"), n_needed=None,
            drift_a=float("nan"), drift_b=float("nan"),
            verdict=VERDICT_MISSING,
            notes=notes + ["metric absent from one arm"])

    if degenerate and (variance(xs) > 0 or variance(ys) > 0):
        # The digests say every iteration produced an identical sample stream,
        # yet the metric derived from those streams moves. One of the two is
        # wrong, and it matters which: either the metric reads something the
        # sample spec never recorded (so it is not reproducible and not
        # provenanced), or the digest is not covering what it claims to. Either
        # way the run cannot be interpreted until it is resolved.
        notes.append(
            "CONTRADICTION: iteration digests are identical but this metric "
            "varies (sd %.3f / %.3f). The metric is reading something outside "
            "the recorded sample stream, or the digest is incomplete."
            % (stdev(xs), stdev(ys)))

    seed = _seed_for(name, xs, ys)
    enough = len(xs) >= min_n and len(ys) >= min_n

    diff = mean(ys) - mean(xs)
    if enough and not degenerate:
        p, exact = permutation_p(xs, ys, seed=seed)
        ci = bootstrap_ci(xs, ys, seed=seed)
        g_ci = _bootstrap_g_ci(xs, ys, seed=seed)
    else:
        p, exact, ci, g_ci = float("nan"), False, (float("nan"),) * 2, (float("nan"),) * 2

    sd = pooled_sd(xs, ys)
    drift_a = spearman(list(range(len(xs))), xs)
    drift_b = spearman(list(range(len(ys))), ys)
    for label, drift in (("baseline", drift_a), ("patched", drift_b)):
        if drift == drift and abs(drift) > 0.5:
            notes.append(
                "%s drifts with iteration index (Spearman %.2f) -- iterations "
                "are not exchangeable, so the p-value is not trustworthy"
                % (label, drift))

    return MetricComparison(
        name=name, n_a=len(xs), n_b=len(ys), missing_a=missing_a, missing_b=missing_b,
        mean_a=mean(xs), mean_b=mean(ys), sd_a=stdev(xs), sd_b=stdev(ys),
        median_a=median(xs), median_b=median(ys),
        iqr_a=(quantile(xs, .25), quantile(xs, .75)),
        iqr_b=(quantile(ys, .25), quantile(ys, .75)),
        diff=diff, diff_ci=tuple(ci), g=hedges_g(xs, ys), g_ci=tuple(g_ci),
        delta=cliffs_delta(xs, ys), delta_label=cliffs_label(cliffs_delta(xs, ys)),
        p=p, p_exact=exact, p_holm=float("nan"),
        mde=minimum_detectable_effect(sd, len(xs), len(ys)),
        n_needed=n_required(sd, diff), drift_a=drift_a, drift_b=drift_b,
        verdict=VERDICT_NO_EVIDENCE, notes=notes)


def _bootstrap_g_ci(xs: Sequence[float], ys: Sequence[float], seed: int,
                    samples: int = 1000) -> Tuple[float, float]:
    rng = random.Random(seed ^ 0x9E3779B9)
    na, nb = len(xs), len(ys)
    values = []
    for _ in range(samples):
        ra = [xs[rng.randrange(na)] for _ in range(na)]
        rb = [ys[rng.randrange(nb)] for _ in range(nb)]
        values.append(hedges_g(ra, rb))
    return (quantile(values, 0.025), quantile(values, 0.975))


def _seed_for(name: str, xs: Sequence[float], ys: Sequence[float]) -> int:
    """Data-derived seed: the same inputs always give the same report.

    Not `hash()` -- Python randomises string hashing per process, so a report
    would change between runs on identical data, which is exactly the property
    a quotable number must not have.
    """
    payload = "%s|%r|%r" % (name, list(xs), list(ys))
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "little")


def _verdict(m: MetricComparison, degenerate: bool, min_n: int) -> str:
    if m.verdict == VERDICT_MISSING:
        return VERDICT_MISSING
    if degenerate:
        return VERDICT_DETERMINISTIC
    if m.n_a < min_n or m.n_b < min_n:
        return VERDICT_UNDERPOWERED
    if m.p_holm == m.p_holm and m.p_holm < ALPHA:
        return VERDICT_CHANGE
    return VERDICT_NO_EVIDENCE


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def format_report(report: ComparisonReport) -> str:
    """Human-readable, and shaped so the caveats cannot be skipped past."""
    lines: List[str] = []
    lines.append("baseline  %s   (arm %s, spec %s)"
                 % (report.baseline, report.arm_a, report.spec_a))
    lines.append("patched   %s   (arm %s, spec %s)"
                 % (report.patched, report.arm_b, report.spec_b))
    lines.append("")
    for warning in report.warnings:
        lines.append("! " + _wrap(warning, "  "))
    lines.append("")

    for m in report.metrics:
        lines.append("-" * 72)
        lines.append("%s   [%s]" % (m.name, m.verdict))
        if m.verdict == VERDICT_MISSING:
            lines.append("  n = %d / %d; %s" % (m.n_a, m.n_b, "; ".join(m.notes)))
            continue
        lines.append("  baseline  n=%-4d mean %8.3f  sd %7.3f  median %8.3f  IQR [%.3f, %.3f]"
                     % (m.n_a, m.mean_a, m.sd_a, m.median_a, m.iqr_a[0], m.iqr_a[1]))
        lines.append("  patched   n=%-4d mean %8.3f  sd %7.3f  median %8.3f  IQR [%.3f, %.3f]"
                     % (m.n_b, m.mean_b, m.sd_b, m.median_b, m.iqr_b[0], m.iqr_b[1]))

        if m.verdict == VERDICT_DETERMINISTIC:
            lines.append("  Every iteration replayed an identical sample stream.")
            lines.append("  The arms %s. n_effective = 1; no p-value is possible."
                         % ("differ" if m.diff else "are identical"))
            lines.append("  To get statistics, find a source of variation (do not seed the")
            lines.append("  RNG) or vary the savestate.")
            for note in m.notes:
                lines.append("  ! " + _wrap(note, "    "))
            continue
        if m.verdict == VERDICT_UNDERPOWERED:
            lines.append("  Fewer than %d usable iterations per arm. No statistics attempted."
                         % MIN_N)
            continue

        lines.append("  difference  %+.3f   95%% CI [%+.3f, %+.3f]"
                     % (m.diff, m.diff_ci[0], m.diff_ci[1]))
        lines.append("  Hedges' g   %+.3f   95%% CI [%+.3f, %+.3f]"
                     % (m.g, m.g_ci[0], m.g_ci[1]))
        lines.append("  Cliff's d   %+.3f   (%s)" % (m.delta, m.delta_label))
        lines.append("  p           %.4f %s -> %.4f after Holm across %d metrics"
                     % (m.p, "(exact)" if m.p_exact else "(sampled)",
                        m.p_holm, len(report.metrics)))
        if m.verdict == VERDICT_NO_EVIDENCE:
            lines.append("  NO EVIDENCE OF A CHANGE -- which is not evidence of no change.")
            if m.mde == m.mde:
                lines.append("  This run could only have detected a difference of %.3f or"
                             % m.mde)
                lines.append("  more (80% power, alpha 0.05). Anything smaller would look")
                lines.append("  exactly like this.")
            if m.n_needed:
                lines.append("  To detect the %+.3f actually observed would need ~%d "
                             "iterations per arm." % (m.diff, m.n_needed))
        else:
            lines.append("  A change of %+.3f survives correction. Judge whether %s of a"
                         % (m.diff, _magnitude(m)))
            lines.append("  %s matters before calling it a fix."
                         % ("standard deviation" if m.sd_a else "unit"))
        for note in m.notes:
            lines.append("  ! " + _wrap(note, "    "))
    lines.append("-" * 72)
    changed = report.changed()
    lines.append("%d of %d metrics changed after multiplicity correction."
                 % (len(changed), len(report.metrics)))
    return "\n".join(lines)


def _magnitude(m: MetricComparison) -> str:
    if m.g != m.g:
        return "that size"
    return "%.2f" % abs(m.g)


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


def report_as_dict(report: ComparisonReport) -> Dict[str, Any]:
    """For downstream tools. Mirrors the text report exactly."""
    return {
        "baseline": report.baseline,
        "patched": report.patched,
        "arm_a": report.arm_a,
        "arm_b": report.arm_b,
        "spec_a": report.spec_a,
        "spec_b": report.spec_b,
        "warnings": report.warnings,
        "metrics": [
            {
                "name": m.name, "verdict": m.verdict,
                "n_a": m.n_a, "n_b": m.n_b,
                "missing_a": m.missing_a, "missing_b": m.missing_b,
                "mean_a": m.mean_a, "mean_b": m.mean_b,
                "sd_a": m.sd_a, "sd_b": m.sd_b,
                "median_a": m.median_a, "median_b": m.median_b,
                "diff": m.diff, "diff_ci": list(m.diff_ci),
                "hedges_g": m.g, "g_ci": list(m.g_ci),
                "cliffs_delta": m.delta, "cliffs_label": m.delta_label,
                "p": m.p, "p_exact": m.p_exact, "p_holm": m.p_holm,
                "mde_80": m.mde, "n_needed": m.n_needed,
                "drift_a": m.drift_a, "drift_b": m.drift_b,
                "notes": m.notes,
            }
            for m in report.metrics
        ],
    }

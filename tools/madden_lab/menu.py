"""An interactive front end: pick an experiment, answer three questions, run.

    python3 -m tools.madden_lab.menu

The non-interactive CLI (`python -m tools.madden_lab trial --spec ...`) stays
the real interface -- it is what a script or a cron job uses, and every prompt
here just assembles a call to it. This exists because the operator runs these
at a console next to a booting console, and remembering a spec path, an
iteration count and an output convention at 1am is how a run gets pointed at
the wrong savestate.

Design rules it follows:
* **Discover, never hardcode.** The experiment list is whatever is in
  `experiments/`, so a new spec appears without editing this file.
* **Every prompt has a default that is safe to accept.** Enter-through must
  produce a sensible run.
* **Show what it will do before it does it**, because the expensive mistake
  here is a two-hour run against the wrong spec.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
EXPERIMENTS = REPO / "experiments"
#: Big enough to see a distribution, small enough to finish inside an hour.
DEFAULT_ITERATIONS = 20

#: Fallbacks for a spec that has not declared its cost yet. Deliberately
#: pessimistic: an unmeasured experiment should over-reserve time and disk
#: rather than surprise an operator an hour in.
DEFAULT_SECONDS_PER_ITERATION = 15.0
DEFAULT_MB_PER_ITERATION = (11.0, 20.0)


def spec_cost(path):
    """`(seconds, (low_mb, high_mb))` a spec declares, or the fallbacks.

    The spec owns these numbers because only it knows its play length and
    sample spec; a constant in this file could not be right for two specs at
    once. Reading them costs an import, which is why the values are plain
    module constants rather than anything that needs `build()` to run.

    A spec that fails to import is not fatal here -- the menu still offers it
    and the real CLI will report the import error properly.
    """
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location("_cost_probe", str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception:
        return DEFAULT_SECONDS_PER_ITERATION, DEFAULT_MB_PER_ITERATION
    seconds = getattr(module, "SECONDS_PER_ITERATION", DEFAULT_SECONDS_PER_ITERATION)
    megabytes = getattr(module, "MB_PER_ITERATION", DEFAULT_MB_PER_ITERATION)
    if isinstance(megabytes, (int, float)):      # a spec may declare a point
        megabytes = (megabytes, megabytes * 1.3)
    return float(seconds), (float(megabytes[0]), float(megabytes[1]))


def size_estimate(iterations: int, per_iteration):
    low, high = per_iteration
    return iterations * low, iterations * high


def duration_estimate(iterations: int, seconds: float) -> str:
    total = iterations * seconds
    if total < 90:
        return "%d s" % round(total)
    if total < 5400:
        return "%d min" % round(total / 60)
    return "%.1f h" % (total / 3600.0)


def disk_report(target: str):
    """`(free_mb, total_mb)` for the filesystem that will hold `target`.

    Walks up to the nearest existing parent, because the operator will type a
    path in a directory that does not exist yet and a crash here would be a
    silly way to lose a run.
    """
    path = pathlib.Path(target).expanduser().resolve()
    probe = path.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(str(probe))
    except OSError:
        return None, None
    return usage.free // (1024 * 1024), usage.total // (1024 * 1024)


def discover():
    """Every runnable spec in experiments/, with its one-line summary."""
    out = []
    for path in sorted(EXPERIMENTS.glob("*.py")):
        if path.name.startswith("_"):
            continue
        summary = ""
        try:
            head = path.read_text(errors="replace").lstrip()
            if head.startswith(('"""', "'''")):
                body = head[3:]
                summary = body.split("\n", 1)[0].strip() or \
                    body.split("\n")[1].strip()
        except OSError:
            pass
        out.append((path, summary))
    return out


def _human(mb) -> str:
    return "%.1f GB" % (mb / 1024.0) if mb >= 1024 else "%d MB" % mb


def ask(prompt: str, default: str) -> str:
    try:
        reply = input("%s [%s]: " % (prompt, default)).strip()
    except EOFError:
        return default
    return reply or default


def main(argv=None) -> int:
    specs = discover()
    if not specs:
        print("no experiments found in %s" % EXPERIMENTS, file=sys.stderr)
        return 4

    print("\n  Madden lab -- experiments in %s\n" % EXPERIMENTS)
    for i, (path, summary) in enumerate(specs, 1):
        print("   %d) %-22s %s" % (i, path.stem, summary[:52]))
    print()

    choice = ask("which experiment (number)", "1")
    try:
        spec = specs[int(choice) - 1][0]
    except (ValueError, IndexError):
        print("not a listed choice: %r" % choice, file=sys.stderr)
        return 2

    seconds, per_iter = spec_cost(spec)

    iterations = ask("how many iterations", str(DEFAULT_ITERATIONS))
    try:
        n = int(iterations)
        if n < 1:
            raise ValueError
    except ValueError:
        print("iterations must be a positive integer: %r" % iterations,
              file=sys.stderr)
        return 2

    # Default output is named for the spec and stamped, so two runs of the
    # same experiment never silently overwrite each other -- results that
    # collide are results nobody can compare afterwards.
    default_out = "/tmp/%s-%s-n%d.jsonl" % (
        spec.stem, time.strftime("%Y%m%d-%H%M"), n)
    out = ask("save output to", default_out)
    write = ask("allow memory writes (only for patched arms) y/N", "n")
    allow_writes = write.lower().startswith("y")

    cmd = [sys.executable, "-u", "-m", "tools.madden_lab", "trial",
           "--spec", str(spec), "-n", str(n), "--out", out]
    if allow_writes:
        cmd.append("--write")

    # ~7 s per play plus load and confirm; deliberately rough, and it is the
    # number that tells the operator whether to wait or walk away.
    print("\n  spec       : %s" % spec)
    print("  iterations : %d  (roughly %s at %.1f s/iteration)"
          % (n, duration_estimate(n, seconds), seconds))
    free_mb, total_mb = disk_report(out)
    low, high = size_estimate(n, per_iter)
    worst = high
    print("  output     : %s" % out)
    if free_mb is None:
        print("  disk       : could not read free space for that path")
    else:
        print("  est. size  : %s to %s  (as declared by the spec)"
              % (_human(low), _human(high)))
        print("  disk       : %s free of %s"
              % (_human(free_mb), _human(total_mb)))
        if worst > free_mb:
            print("  !! NOT ENOUGH SPACE -- the run would fill the disk and die "
                  "partway. Choose another path or fewer iterations.")
        elif worst > free_mb * 0.8:
            print("  !  that is most of the free space; consider another path.")
    print("  writes     : %s" % ("ENABLED" if allow_writes else "read-only"))
    print("  command    : %s\n" % " ".join(cmd))
    if not ask("run it? y/N", "y").lower().startswith("y"):
        print("nothing run.")
        return 0

    print("  progress is flushed per iteration; watch it here or count rows:")
    print("    grep -c '\"kind\":\"iteration\"' %s\n" % out)
    return subprocess.call(cmd, cwd=str(REPO))


if __name__ == "__main__":
    sys.exit(main())

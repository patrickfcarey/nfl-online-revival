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

#: Measured on the lead-blocker spec: 1,614,405,759 bytes over 134 completed
#: iterations = **11.49 MB per iteration** (4.31 M rows at ~374 B, one row per
#: entity-field-frame). Quoted as a range because play length varies with the
#: play -- a stuffed run ends sooner than one that breaks -- and that is the
#: only real source of spread once the sample spec is fixed.
#:
#: The +/-15% band is deliberately wider than the observed variation so the
#: high end is a number you can trust not to be exceeded, which is the whole
#: point of showing it before an hour-long run.
MB_PER_ITERATION = 11.5
ESTIMATE_BAND = 0.15


def size_estimate(iterations: int):
    """`(low_mb, high_mb)` this run will occupy. Calibrated, not guessed.

    A single figure would imply a precision the measurement does not have; a
    range whose top end is honest is what lets the operator decide.
    """
    mid = iterations * MB_PER_ITERATION
    return mid * (1 - ESTIMATE_BAND), mid * (1 + ESTIMATE_BAND)


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
    print("  iterations : %d  (roughly %d min)" % (n, max(1, n * 12 // 60)))
    free_mb, total_mb = disk_report(out)
    low, projected = size_estimate(n)
    print("  output     : %s" % out)
    if free_mb is None:
        print("  disk       : could not read free space for that path")
    else:
        print("  est. size  : %s to %s  (%.1f MB/iteration, measured over 134 runs)"
              % (_human(low), _human(projected), MB_PER_ITERATION))
        print("  disk       : %s free of %s"
              % (_human(free_mb), _human(total_mb)))
        if projected > free_mb:
            print("  !! NOT ENOUGH SPACE -- the run would fill the disk and die "
                  "partway. Choose another path or fewer iterations.")
        elif projected > free_mb * 0.8:
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

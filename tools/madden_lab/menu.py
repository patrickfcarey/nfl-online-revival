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
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
EXPERIMENTS = REPO / "experiments"
#: Big enough to see a distribution, small enough to finish inside an hour.
DEFAULT_ITERATIONS = 20


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
    print("  output     : %s" % out)
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

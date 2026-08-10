"""`python -m tools.madden_lab <command>`.

Argument problems are messages and exit codes, never tracebacks: these commands
are run at a console next to a booting console, and a stack trace there costs a
session.

Layers 1-3 are imported *inside* each command, not at module scope. That is
what lets `--help`, argument validation, `compare` and `answer` work on a
machine with no PINE socket, no emulator and no game -- and it is what lets the
tests drive the whole dispatch table offline. A missing lower layer is reported
as the one line that says which layer is missing, rather than an ImportError
traceback naming a module the operator has never heard of.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, List, Optional, Tuple

from . import EXPECTED_CRC
from .results import JsonlSink, MemorySink, append_answer, pending_asks
from .runner import PreflightError, Runner, make_provenance

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_UNAVAILABLE = 4


# --------------------------------------------------------------------------
# Spec loading
# --------------------------------------------------------------------------


def load_spec(path: str):
    """Import an experiment file by path and pull the `Trial` out of it.

    By path rather than by module name because experiments live in
    `experiments/`, which is not a package and should not become one -- a spec
    is a document that happens to be executable, and making it importable
    invites people to import from it.

    The file must define `TRIAL`, or `build()` returning one.
    """
    import importlib.util

    if not os.path.exists(path):
        raise FileNotFoundError("no such spec: %s" % path)
    spec = importlib.util.spec_from_file_location("_madden_lab_spec", path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load %s as Python" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_madden_lab_spec"] = module
    spec.loader.exec_module(module)
    trial = getattr(module, "TRIAL", None)
    if trial is None:
        builder = getattr(module, "build", None)
        if callable(builder):
            trial = builder()
    if trial is None:
        raise AttributeError("%s defines neither TRIAL nor build()" % path)
    return trial


def connect(args: argparse.Namespace) -> Tuple[Any, Any, Any]:
    """Bring up layers 1-3. The only place this package touches them.

    Every import failure here means a sibling layer is not finished, which is a
    different problem from a broken emulator and gets a different message.
    """
    try:
        from . import emu as emu_module
    except ImportError as exc:
        raise SystemExit(_missing("emu.py (layer 1, emulator control)", exc))
    try:
        from . import world as world_module
    except ImportError as exc:
        raise SystemExit(_missing("world.py (layer 3, observation)", exc))

    # Layer 1 is read-only unless asked otherwise, which is what makes "never
    # write outside a trial's declared setup" structural. The opt-in happens
    # exactly here, from the explicit --write flag, and nowhere else.
    writable = bool(getattr(args, "write", False))
    emu = _construct(emu_module, getattr(args, "socket", None), writable)
    if hasattr(emu, "connect"):
        try:
            emu.connect()
        except Exception as exc:
            # No emulator is the single most common way to run these commands
            # wrongly, and layer 1's message already says exactly what to check.
            # Printing it under a traceback buries it.
            raise SystemExit("error: %s" % exc)
    # Layer 3 reads through a Reader, not an Emu -- the same World runs against
    # a live socket or an offline savestate dump, which is why the address map
    # is testable with no emulator at all.
    reader = world_module.EmuReader(emu) if hasattr(world_module, "EmuReader") else emu
    world = world_module.World(reader)

    pad = None
    if getattr(args, "pad", True):
        try:
            from . import pad as pad_module
            pad = pad_module.Pad() if hasattr(pad_module, "Pad") else pad_module.connect()
        except ImportError:
            pad = None  # sampling-only commands do not need input
    return emu, world, pad


def _construct(emu_module, socket_path: Optional[str], writable: bool):
    """Build layer 1's Emu, tolerating a constructor signature still in flux."""
    factory = getattr(emu_module, "Emu", None) or getattr(emu_module, "connect")
    for kwargs in ({"path": socket_path, "writable": writable},
                   {"writable": writable},
                   {}):
        try:
            if socket_path and "path" not in kwargs:
                continue
            return factory(**kwargs)
        except TypeError:
            continue
    return factory()


def _missing(what: str, exc: Exception) -> str:
    return ("error: %s is not available yet (%s).\n"
            "       This command needs it; `compare`, `answer` and `asks` do not."
            % (what, exc))


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


#: Anchors `doctor` checks, each a *known-nonzero* value from a verified read.
#: Nonzero matters: PINE returns 0 for unmapped pages rather than erroring, so
#: a check that passes on 0 cannot tell a correct address from a dead one, and
#: this project's whole failure history is addresses that drifted.
ANCHORS = (
    # docs/lab-design.md: read out of a live image, matched to the ELF at 99.86%.
    ("0x0019BD7C", 0x0019BD7C, 4, 0x24050032, "slider-behavior.md knockdown site"),
    # Option-table fourcc index 0 == "OQLN", little-endian in memory.
    ("0x0051FDC8", 0x0051FDC8, 4, None, "option fourcc[0], expected OQLN"),
)


def cmd_doctor(args: argparse.Namespace) -> int:
    """Refuse to run when anything upstream is wrong. The cheapest command here.

    Every wrong answer this project has produced came from an address that had
    drifted or was never right, so this checks the build identity first and
    stops there if it fails.

    It cannot, however, distinguish a drifted address from a genuinely zero
    word -- reads of unmapped pages return 0 rather than erroring -- so every
    anchor below is a value known to be nonzero, and a zero read is reported as
    a failure rather than as a value.
    """
    emu, world, _pad = connect(args)
    checks: List[Tuple[str, bool, str]] = []

    require = getattr(emu, "require_crc", None)
    try:
        if callable(require):
            require()
            crc = EXPECTED_CRC
            checks.append(("game CRC", True, "0x%08X via require_crc()" % EXPECTED_CRC))
        else:
            crc = emu.game_crc()
            checks.append(("game CRC", crc == EXPECTED_CRC,
                           "0x%08X (expected 0x%08X)" % (crc, EXPECTED_CRC)))
    except Exception as exc:
        checks.append(("game CRC", False, "%s: %s" % (type(exc).__name__, exc)))
        crc = None

    if crc == EXPECTED_CRC:
        for label, addr, size, expected, why in ANCHORS:
            try:
                value = emu.read(addr, size)
            except Exception as exc:
                checks.append((label, False, "%s: %s" % (type(exc).__name__, exc)))
                continue
            if value == 0:
                checks.append((label, False,
                               "read 0 -- unmapped pages read as 0, so this is "
                               "either a dead address or a drifted one (%s)" % why))
            elif expected is not None and value != expected:
                checks.append((label, False, "0x%08X, expected 0x%08X (%s)"
                               % (value, expected, why)))
            else:
                checks.append((label, True, "0x%08X (%s)" % (value, why)))

    if crc == EXPECTED_CRC:
        try:
            players = list(world.players())
            checks.append(("player array", bool(players),
                           "%d players (0x00600E48 is null at the main menu)"
                           % len(players)))
        except Exception as exc:
            checks.append(("player array", False, "%s: %s" % (type(exc).__name__, exc)))
        # Not `phase`: layer 3 established there is no such word, and its
        # phase() raises by design. Checking it would report a permanent FAIL.
        for label, call in (("frames_since_snap", getattr(world, "frames_since_snap", None)),
                            ("frame_counter", getattr(world, "frame_counter", None))):
            if not callable(call):
                checks.append((label, label == "frame_counter",
                               "absent" + (" (SEAM REQUEST 4; pre-snap input untimed)"
                                           if label == "frame_counter" else "")))
                continue
            try:
                checks.append((label, True, repr(call())))
            except Exception as exc:
                checks.append((label, False, "%s: %s" % (type(exc).__name__, exc)))

        # Capabilities the runner degrades over. Absent is not fatal, but a
        # run that had to degrade should have said so before it started, not
        # in a footnote afterwards.
        checks.append(("emu.wait_until", callable(getattr(emu, "wait_until", None)),
                       "load confirmation; without it the runner polls itself"))
        checks.append(("world.begin_frame", callable(getattr(world, "begin_frame", None)),
                       "batched snapshot; without it a frame is ~88 round trips "
                       "and can tear (SEAM REQUEST 8)"))
        checks.append(("emu writable", True,
                       "yes -- setup writes permitted" if getattr(emu, "writable", False)
                       else "no (read-only; pass --write on `trial` to opt in)"))

    width = max(len(name) for name, _ok, _detail in checks)
    failed = 0
    for name, ok, detail in checks:
        if not ok:
            failed += 1
        print("%-4s %-*s  %s" % ("ok" if ok else "FAIL", width, name, detail))
    if crc is not None and crc != EXPECTED_CRC:
        print("\nrefusing: wrong build. Every address in docs/ is for SLUS_207.52.")
        return EXIT_REFUSED
    return EXIT_OK if not failed else EXIT_REFUSED


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Dump the world once, as rows. Same schema as a run, so it diffs."""
    from .trial import EntitySelector, SampleSpec, StopCondition, Trial

    emu, world, _pad = connect(args)
    fields = tuple(args.fields.split(",")) if args.fields else ("position", "block_mode",
                                                               "engagement")
    trial = Trial(name="snapshot", state="<live>", question="what is the world right now?",
                  # Not `phase`: layer 3 established there is no play-phase
                  # word in the game, and its phase() raises by design.
                  sample=SampleSpec(entities=(EntitySelector("player", fields),
                                              EntitySelector("game",
                                                             ("frames_since_snap",)))),
                  stop=StopCondition(max_frames=1))
    runner = Runner(emu, world, pad=None, clock=_manual_clock())
    sink = MemorySink(make_provenance(trial, arm="snapshot"))
    frame = runner._sample(trial, 0)
    for (entity, name) in sorted(frame.values):
        sink.write("sample", iteration=0, frame=0, entity=entity, field=name,
                   value=frame.values[(entity, name)])
    for row in sink.rows:
        print(json.dumps(row, separators=(",", ":"), default=str))
    return EXIT_OK


def cmd_record(args: argparse.Namespace) -> int:
    """Sample the live game for N frames with no state load and no input.

    Deliberately does not load a savestate: this is the command for watching
    what a human is doing at the pad, which is how you find out what a field
    means before writing a trial that assumes it.
    """
    from .trial import EntitySelector, SampleSpec, StopCondition, Trial

    emu, world, _pad = connect(args)
    fields = tuple(args.fields.split(",")) if args.fields else ("position", "block_mode",
                                                               "engagement")
    # state="<live>" is the runner's signal to skip the savestate load
    # entirely, so this observes whatever is on screen right now.
    trial = Trial(name="record", state="<live>",
                  question="what does the live game do for %d frames?" % args.frames,
                  sample=SampleSpec(entities=(EntitySelector("player", fields),),
                                    every=args.every),
                  stop=StopCondition(max_frames=args.frames, timeout_s=args.timeout))
    runner = Runner(emu, world, pad=None)
    provenance = make_provenance(trial, arm="record")
    with JsonlSink(args.out, provenance) as sink:
        sink.write("run", trial=trial.name, question=trial.question,
                   iterations_requested=1, max_frames=args.frames)
        result = runner.run_iteration(trial, 0, sink=sink)
    print("%s: %d frames, %d rows, status %s %s"
          % (args.out, result.frames, sink.rows_written, result.status, result.reason))
    return EXIT_OK if result.status == "ok" else EXIT_UNAVAILABLE


def cmd_trial(args: argparse.Namespace) -> int:
    trial = load_spec(args.spec)
    emu, world, pad = connect(args)
    runner = Runner(emu, world, pad=pad, allow_writes=args.write,
                    still_dir=args.stills or "")
    provenance = make_provenance(trial, arm=args.arm)
    try:
        with JsonlSink(args.out, provenance, append=args.append) as sink:
            summary = runner.run(trial, args.iterations, sink, arm=args.arm,
                                 ask_mode=args.ask, blind_label=args.blind)
    except PreflightError as exc:
        print("refusing: %s" % exc, file=sys.stderr)
        return EXIT_REFUSED
    ok = len(summary.ok)
    print("\n%s: %d/%d iterations ok, %d rows -> %s"
          % (trial.name, ok, args.iterations, summary.rows_written, args.out))
    if trial.cannot_conclude:
        print("\nThis experiment cannot conclude:")
        for line in trial.cannot_conclude:
            print("  - %s" % line)
    return EXIT_OK if ok else EXIT_UNAVAILABLE


def cmd_verify_determinism(args: argparse.Namespace) -> int:
    """The question the whole harness rests on. Answer it before trusting a run."""
    trial = load_spec(args.spec)
    emu, world, pad = connect(args)
    runner = Runner(emu, world, pad=pad, allow_writes=args.write)
    sink = None
    if args.out:
        sink = JsonlSink(args.out, make_provenance(trial, arm="determinism"))
    try:
        report = runner.verify_determinism(trial, repeats=args.repeats, sink=sink)
    except PreflightError as exc:
        print("refusing: %s" % exc, file=sys.stderr)
        return EXIT_REFUSED
    finally:
        if sink is not None:
            sink.close()
    print(report.describe())
    return EXIT_OK


def cmd_compare(args: argparse.Namespace) -> int:
    """Needs no emulator: it reads two files."""
    from .compare import compare_files, format_report, report_as_dict

    report = compare_files(args.baseline, args.patched, min_n=args.min_n)
    if args.json:
        print(json.dumps(report_as_dict(report), indent=2, default=str))
    else:
        print(format_report(report))
    return EXIT_OK


def cmd_shot(args: argparse.Namespace) -> int:
    """Screenshot via savestate -- the harness's only pair of eyes."""
    emu, _world, _pad = connect(args)
    shot = getattr(emu, "screenshot", None)
    if not callable(shot):
        print("error: layer 1 exposes no screenshot(). A savestate embeds\n"
              "       Screenshot.png; emu.py should unzip it (SEAM REQUEST 2).",
              file=sys.stderr)
        return EXIT_UNAVAILABLE
    shot(args.out)
    print(args.out)
    return EXIT_OK


def cmd_asks(args: argparse.Namespace) -> int:
    """List the questions a run raised that nobody has answered yet."""
    outstanding = pending_asks(args.run)
    if not outstanding:
        print("no unanswered operator questions in %s" % args.run)
        return EXIT_OK
    print("%d unanswered question(s) in %s\n" % (len(outstanding), args.run))
    for row in outstanding:
        print("  iteration %-5s [%s]" % (row.get("iteration"), row.get("ask")))
        print("    WATCH:  %s" % row.get("watch_for"))
        if row.get("still"):
            print("    STILL:  %s" % row.get("still"))
        print("    ASK:    %s" % row.get("question"))
        print("    ANSWER: %s" % " / ".join(row.get("choices") or []))
        print("")
    return EXIT_OK


def cmd_answer(args: argparse.Namespace) -> int:
    """Record an operator's answer into the same file as the measurements."""
    try:
        answer = append_answer(args.run, args.ask, args.iteration, args.value,
                               operator=args.operator or "", note=args.note or "")
    except (KeyError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    print("recorded %s=%s for iteration %d in %s"
          % (answer["ask"], answer["value"], args.iteration, args.run))
    remaining = len(pending_asks(args.run))
    print("%d question(s) still unanswered" % remaining)
    return EXIT_OK


def _manual_clock():
    from .runner import ManualClock
    return ManualClock()


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------


def _positive(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("%r is not a number" % text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more, got %d" % value)
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.madden_lab",
        description="Run gameplay experiments in Madden 2004 under PCSX2.")
    parser.add_argument("--socket", help="PINE socket path (default: PCSX2's own)")
    subs = parser.add_subparsers(dest="command")

    doctor = subs.add_parser("doctor", help="PINE up? right game? addresses sane?")
    doctor.set_defaults(func=cmd_doctor, pad=False)

    snapshot = subs.add_parser("snapshot", help="dump the world right now")
    snapshot.add_argument("--fields", help="comma-separated player fields")
    snapshot.set_defaults(func=cmd_snapshot, pad=False)

    record = subs.add_parser("record", help="sample the live game, no reset, no input")
    record.add_argument("--frames", type=_positive, default=300)
    record.add_argument("--every", type=_positive, default=1)
    record.add_argument("--timeout", type=float, default=120.0)
    record.add_argument("--fields", help="comma-separated player fields")
    record.add_argument("--out", default="run.jsonl", help=".jsonl or .jsonl.gz")
    record.set_defaults(func=cmd_record, pad=False)

    trial = subs.add_parser("trial", help="run an experiment N times")
    trial.add_argument("--spec", required=True, help="path to an experiments/*.py")
    trial.add_argument("-n", "--iterations", type=_positive, default=20)
    trial.add_argument("--out", default="run.jsonl", help=".jsonl or .jsonl.gz")
    trial.add_argument("--arm", default="baseline",
                       help="which build this is; recorded in every row")
    trial.add_argument("--blind", default="A",
                       help="opaque label shown to the operator instead of --arm")
    trial.add_argument("--ask", choices=("batch", "live", "none"), default="batch",
                       help="when to print OPERATOR prompts (default: at the end)")
    trial.add_argument("--stills", help="directory for screenshots attached to prompts")
    trial.add_argument("--append", action="store_true",
                       help="append to --out, for interleaving arms in one session")
    trial.add_argument("--write", action="store_true",
                       help="permit the trial's declared setup writes (default: read-only)")
    trial.set_defaults(func=cmd_trial, pad=True)

    determinism = subs.add_parser(
        "verify-determinism",
        help="run one trial N times and report whether the streams are identical")
    determinism.add_argument("--spec", required=True)
    determinism.add_argument("--repeats", type=_positive, default=3)
    determinism.add_argument("--out", help="also write the sample rows here")
    determinism.add_argument("--write", action="store_true")
    determinism.set_defaults(func=cmd_verify_determinism, pad=True)

    compare = subs.add_parser("compare", help="two result sets in, a verdict out")
    compare.add_argument("baseline")
    compare.add_argument("patched")
    compare.add_argument("--min-n", type=_positive, default=5, dest="min_n")
    compare.add_argument("--json", action="store_true")
    compare.set_defaults(func=cmd_compare, pad=False)

    shot = subs.add_parser("shot", help="screenshot via savestate")
    shot.add_argument("--out", default="frame.png")
    shot.set_defaults(func=cmd_shot, pad=False)

    asks = subs.add_parser("asks", help="list unanswered operator questions")
    asks.add_argument("--run", required=True)
    asks.set_defaults(func=cmd_asks, pad=False)

    answer = subs.add_parser("answer", help="record an operator's answer")
    answer.add_argument("--run", required=True)
    answer.add_argument("--ask", required=True)
    answer.add_argument("--iteration", type=int, required=True)
    answer.add_argument("--value", required=True)
    answer.add_argument("--operator", help="who watched; recorded in the row")
    answer.add_argument("--note", help="free text, for the thing the choices missed")
    answer.set_defaults(func=cmd_answer, pad=False)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    try:
        return args.func(args)
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            return EXIT_UNAVAILABLE
        return int(exc.code or 0)
    except (FileNotFoundError, AttributeError, ImportError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("\ninterrupted; rows written so far are still valid", file=sys.stderr)
        return EXIT_UNAVAILABLE


if __name__ == "__main__":
    sys.exit(main())

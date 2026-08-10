"""Mutation testing: check that the tests would notice if the code changed.

A passing suite proves the code does what the tests say. It does not prove the
tests say anything. This project has already shipped two of those: a rate-limit
guard that could never fire, and a peak gauge wired to the wrong scope -- both
fully "covered", both wrong, because coverage records that a line ran and not
that anything depended on the result.

Mutation testing closes that. Change the source in a way that must alter
behaviour, run the tests, and see whether they fail. A mutant that survives is
a behaviour nothing checks.

TWO MODES, and they answer different questions.

**Regressions** (``--regressions``) replay the specific defects this project
has actually shipped -- BEQL at 0x13, the roster keyed off the scraped ``tgId``,
the peak gauge summed across connections. Each must be killed. This is fast,
deterministic, and it is the mode worth running often: it is a regression suite
for the *tests*, pinning the ones that were written to catch a real bug so they
cannot quietly stop catching it.

**Generated** (``--file``) rewrites the module's syntax tree -- comparison
operators, boolean connectives, integer constants, returned booleans -- and
reports whatever survives. Slower, noisier, and the survivors need reading
rather than fixing: some are equivalent mutants, where the change genuinely
cannot be observed.

SAFETY. This edits files in place. Every path restores the original in a
``finally`` *and* through ``atexit``, and the tool refuses to start if the
target has uncommitted changes -- a crash mid-run would otherwise take edits
with it. Nothing here should ever be pointed at a working tree you have not
committed.

    python3 tools/mutate.py --regressions
    python3 tools/mutate.py --file backend/limits.py
    python3 tools/mutate.py --file backend/limits.py --limit 20
"""

from __future__ import annotations

import argparse
import ast
import atexit
import copy
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent

#: Which test modules exercise which source file. Running the whole suite for
#: every mutant would take hours; these run in seconds and are what actually
#: covers the file. A source file missing from here falls back to everything,
#: which is correct but slow -- add an entry rather than wait.
TEST_MODULES: Dict[str, List[str]] = {
    "backend/limits.py": ["tests.test_limits", "tests.test_buddy_limits"],
    "backend/metrics.py": ["tests.test_metrics"],
    "backend/protocol.py": ["tests.test_protocol_edges", "tests.test_backend"],
    "backend/hub.py": ["tests.test_limits", "tests.test_backend_internals"],
    "backend/service.py": ["tests.test_limits", "tests.test_backend_internals",
                           "tests.test_fake_console"],
    "backend/buddy.py": ["tests.test_buddy_limits",
                         "tests.test_backend_internals"],
    "backend/rosterfile.py": ["tests.test_roster_delivery"],
    "backend/lobby.py": ["tests.test_backend"],
    "backend/handlers.py": ["tests.test_backend", "tests.test_roster_delivery",
                            "tests.test_matchmaking"],
    "backend/matchmaking.py": ["tests.test_matchmaking"],
    "backend/store.py": ["tests.test_backend"],
    "backend/__main__.py": ["tests.test_cli"],
    "tools/madden_tdb.py": ["tests.test_roster_checksum",
                            "tests.test_build_year_roster"],
    "tools/roster_checksum.py": ["tests.test_roster_checksum",
                                 "tests.test_tool_clis"],
    "tools/mark_roster.py": ["tests.test_tool_clis",
                             "tests.test_build_year_roster"],
    "tools/build_roster.py": ["tests.test_build_roster", "tests.test_tool_clis"],
    "tools/build_year_roster.py": ["tests.test_build_year_roster"],
    "tools/patch_iso_roster.py": ["tests.test_patch_iso_roster"],
    "tools/pine.py": ["tests.test_pine", "tests.test_tool_clis"],
    "tools/read_roster_checksum.py": ["tests.test_read_roster_checksum"],
    "tools/fake_console.py": ["tests.test_fake_console"],
    "recon/mipsdis.py": ["tests.test_mipsdis"],
    "recon/dnsd.py": ["tests.test_recon_cli"],
    "recon/sinkd.py": ["tests.test_recon_tools", "tests.test_recon_servers"],
    "recon/easerver.py": ["tests.test_recon_tools", "tests.test_recon_servers"],
    "recon/tlssink.py": ["tests.test_recon_tools", "tests.test_recon_servers"],
    "recon/__main__.py": ["tests.test_recon_cli"],
}


class Regression(NamedTuple):
    """A defect this project actually shipped, as a textual substitution."""

    name: str
    path: str
    old: str
    new: str
    why: str


#: Each of these was a real bug, and each has a test written to catch it. If a
#: mutant here survives, that test has stopped doing its job -- which is a
#: different and worse failure than the bug coming back.
REGRESSIONS: List[Regression] = [
    Regression(
        "beql-off-by-one", "recon/mipsdis.py",
        '_LIKELY = {0x14: "beql", 0x15: "bnel", 0x16: "blezl", 0x17: "bgtzl"}',
        '_LIKELY = {0x13: "beql", 0x14: "bnel", 0x15: "blezl", 0x16: "bgtzl"}',
        "BEQL at 0x13 silently inverts every branch-likely condition printed, "
        "and those readings go into the documentation as facts."),
    Regression(
        "variable-shift-operand-order", "recon/mipsdis.py",
        '            return "%s %s, %s, %s" % (name, _REGS[rd], _REGS[rt], _REGS[rs])',
        '            return "%s %s, %s, %s" % (name, _REGS[rd], _REGS[rs], _REGS[rt])',
        "sllv/srlv/srav are rd, rt, rs -- the reverse of every other SPECIAL "
        "form. Printed the generic way round, a shift of a value by a counter "
        "reads as a shift of the counter by the value, and a live function "
        "reads as dead code."),
    Regression(
        "roster-team-from-file", "tools/build_year_roster.py",
        "            team_id = resolve_team(abbreviation, team_ids)",
        "            team_id = team['tgId']",
        "The scraped tgId disagrees with the game for ids 30/31/32. The "
        "twenty-nine that agree make a wrong build look correct."),
    Regression(
        "nickname-claim-arbitration", "tools/build_year_roster.py",
        "            and not (claimed and key in claimed)]",
        "            ]",
        "Without it a nickname takes a record another player owns outright: "
        "Green Bay's Tony Brown wearing Anthony Brown's ratings. Seven such "
        "pairs of different men across eighteen years."),
    Regression(
        "weight-offset", "tools/build_year_roster.py",
        '_set(record, table, "PWGT", max(0, player["weight"] - 160))',
        '_set(record, table, "PWGT", player["weight"])',
        "PWGT is pounds minus 160; an 8-bit field cannot hold a lineman "
        "otherwise, so dropping the offset wraps every heavy player."),
    Regression(
        "peak-across-connections", "backend/limits.py",
        "            seen = state.peak.record()",
        "            seen = state.peak.record() + 0; seen = max(seen, self._peak + 1)",
        "The calibration gauge must measure one connection. Summed across "
        "them it is inflated by however many clients are online, and the rate "
        "limit derived from it is that many times too loose."),
    Regression(
        "burst-validation", "backend/limits.py",
        "        if rate > 0 and burst <= 0:",
        "        if False:",
        "A positive rate with a zero burst refuses every message while "
        "reading as lenient. Validation must happen at construction, because "
        "buckets are built per connection."),
    Regression(
        "eager-rate-validation", "backend/limits.py",
        "        TokenBucket(rate, burst)\n        TokenBucket(ip_rate, ip_burst)",
        "        pass",
        "Without the eager check an invalid pair starts the server cleanly "
        "and then kills every client as it arrives."),
    Regression(
        "send-timeout-zero", "backend/service.py",
        "        if send_timeout <= 0:",
        "        if False:",
        "settimeout(0) is non-blocking, not untimed: recv raises "
        "BlockingIOError and every connection dies on its first quiet moment."),
    Regression(
        "threaded-roster-server", "backend/rosterfile.py",
        "self._httpd = http.server.ThreadingHTTPServer((bind, port), handler)",
        "self._httpd = http.server.HTTPServer((bind, port), handler)",
        "One stalled request blocks every roster download, and the install "
        "path wipes the league database before it validates."),
    Regression(
        "replay-server-reply", "recon/easerver.py",
        "                outgoing = _replies_for(message, replies, host, redirect_port)",
        "                outgoing = _reply_for(message, replies, host, redirect_port)",
        "_reply_for does not exist; the replay server died with NameError on "
        "the first message any client sent it."),
    Regression(
        "metrics-public-bind", "backend/metrics.py",
        "        if not allow_public and not is_loopback(bind):",
        "        if False:",
        "The metrics page has no authentication and would enumerate player "
        "addresses to anyone who asked."),
    Regression(
        "iso-size-check", "tools/patch_iso_roster.py",
        "    if len(roster) != inner_size:",
        "    if False:",
        "A roster of the wrong length moves every file after it, and the "
        "image still mounts."),
    Regression(
        "news-status-tag", "backend/handlers.py",
        "    return NEWS_REPLY_BASE[:3] + str(kind)",
        "    return NEWS_REPLY_BASE",
        "The category rides in the STATUS word and the client gates on it "
        "being 'new0' + NAME. A constant tag means every reply but one is "
        "silently discarded."),
]


class Mutant(NamedTuple):
    index: int
    line: int
    description: str


# ---------------------------------------------------------------------------
# generated mutations
# ---------------------------------------------------------------------------

_COMPARE_SWAPS = {
    ast.Lt: ast.LtE, ast.LtE: ast.Lt,
    ast.Gt: ast.GtE, ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
    ast.In: ast.NotIn, ast.NotIn: ast.In,
}


class _Collector(ast.NodeVisitor):
    """Find every place a mutation could be applied, in a stable order."""

    def __init__(self) -> None:
        self.sites: List[Tuple[str, int, str]] = []

    def visit_Compare(self, node):
        for op in node.ops:
            swap = _COMPARE_SWAPS.get(type(op))
            if swap is not None:
                self.sites.append(("compare", getattr(node, "lineno", 0),
                                   "%s -> %s" % (type(op).__name__,
                                                 swap.__name__)))
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        name = type(node.op).__name__
        self.sites.append(("boolop", getattr(node, "lineno", 0),
                           "%s -> %s" % (name,
                                         "Or" if name == "And" else "And")))
        self.generic_visit(node)

    def visit_UnaryOp(self, node):
        if isinstance(node.op, ast.Not):
            self.sites.append(("not", getattr(node, "lineno", 0),
                               "drop `not`"))
        self.generic_visit(node)

    def visit_Constant(self, node):
        if isinstance(node.value, bool):
            self.sites.append(("bool", getattr(node, "lineno", 0),
                               "%s -> %s" % (node.value, not node.value)))
        elif isinstance(node.value, int):
            self.sites.append(("int", getattr(node, "lineno", 0),
                               "%d -> %d" % (node.value, node.value + 1)))
        self.generic_visit(node)


class _Applier(ast.NodeTransformer):
    """Apply exactly the Nth mutation site, leaving every other alone."""

    def __init__(self, target: int) -> None:
        self.target = target
        self.seen = 0

    def _take(self) -> bool:
        hit = self.seen == self.target
        self.seen += 1
        return hit

    def visit_Compare(self, node):
        self.generic_visit(node)
        ops = list(node.ops)
        for index, op in enumerate(ops):
            swap = _COMPARE_SWAPS.get(type(op))
            if swap is None:
                continue
            if self._take():
                ops[index] = swap()
                node.ops = ops
        return node

    def visit_BoolOp(self, node):
        self.generic_visit(node)
        if self._take():
            node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        return node

    def visit_UnaryOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.Not) and self._take():
            return node.operand
        return node

    def visit_Constant(self, node):
        if isinstance(node.value, bool):
            if self._take():
                return ast.copy_location(ast.Constant(value=not node.value),
                                         node)
        elif isinstance(node.value, int):
            if self._take():
                return ast.copy_location(ast.Constant(value=node.value + 1),
                                         node)
        return node


def find_mutants(source: str) -> List[Mutant]:
    """Every mutation this source admits, numbered stably."""
    collector = _Collector()
    collector.visit(ast.parse(source))
    return [Mutant(index, line, "%s: %s" % (kind, text))
            for index, (kind, line, text) in enumerate(collector.sites)]


def apply_mutant(source: str, index: int) -> str:
    """The source with mutation *index* applied."""
    tree = _Applier(index).visit(copy.deepcopy(ast.parse(source)))
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------

def write_source(path: Path, text: str) -> None:
    """Write a source file and drop its cached bytecode.

    **The cache invalidation is not optional, and leaving it out poisons the
    repository.** Python decides a ``.pyc`` is current by comparing the
    source's ``(mtime, size)``. Every mutation here replaces text with text of
    the same length, and the original is restored within the same second, so
    both halves of that check still match and the next import gets the
    *mutant's* bytecode from cache while the source on disk is correct.

    This is not theoretical. It left this repository reporting three failures
    in `recon/mipsdis.py` against a file that was byte-for-byte identical to
    HEAD, with a clean `git status` -- the tests were running code that existed
    nowhere except `__pycache__`.
    """
    path.write_text(text)
    try:
        os.unlink(importlib.util.cache_from_source(str(path)))
    except OSError:
        pass
    importlib.invalidate_caches()


class Result(NamedTuple):
    name: str
    killed: bool
    detail: str


def _tests_for(relative: str) -> List[str]:
    modules = TEST_MODULES.get(relative)
    if modules:
        return modules
    return []


def _run_tests(modules: List[str], timeout: float) -> Tuple[bool, str]:
    """True when the tests PASS. A crash counts as a failure, which is a kill."""
    command = [sys.executable, "-m", "unittest"]
    command += modules if modules else ["discover", "-s", "tests"]
    try:
        finished = subprocess.run(command, cwd=str(ROOT), timeout=timeout,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT)
    except subprocess.TimeoutExpired:
        # A mutant that hangs the suite is detected, not survived: the tests
        # noticed, they just noticed by never finishing.
        return False, "timed out after %.0fs" % timeout
    tail = finished.stdout.decode("utf-8", "replace").strip().splitlines()
    return finished.returncode == 0, tail[-1] if tail else ""


class _Restorer:
    """Put the file back, in a finally and again at exit."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.original = path.read_bytes()
        self._done = False
        atexit.register(self.restore)

    def restore(self) -> None:
        if self._done:
            return
        try:
            self.path.write_bytes(self.original)
            try:
                os.unlink(importlib.util.cache_from_source(str(self.path)))
            except OSError:
                pass
            importlib.invalidate_caches()
            # Verify rather than assume. A restore that silently did nothing
            # leaves a mutant in the working tree, and the next thing to read
            # it has no way to tell.
            if self.path.read_bytes() != self.original:
                print("RESTORE VERIFICATION FAILED for %s -- the working tree "
                      "still differs from what was read. Check `git diff`."
                      % self.path, file=sys.stderr)
        except OSError as exc:                    # pragma: no cover
            print("CANNOT RESTORE %s: %s" % (self.path, exc), file=sys.stderr)
        self._done = True


def _is_dirty(relative: str) -> bool:
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", relative],
                             cwd=str(ROOT), stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
    except OSError:                               # pragma: no cover - no git
        return False
    return bool(out.stdout.strip())


def run_regressions(only: Optional[str] = None,
                    timeout: float = 900.0) -> List[Result]:
    """Replay the defects this project has shipped. Every one must be killed."""
    results: List[Result] = []
    for case in REGRESSIONS:
        if only and only not in case.name:
            continue
        path = ROOT / case.path
        source = path.read_text()
        if case.old not in source:
            results.append(Result(
                case.name, False,
                "STALE: the text this mutates is no longer in %s -- the code "
                "moved and this entry needs updating" % case.path))
            continue
        restorer = _Restorer(path)
        try:
            write_source(path, source.replace(case.old, case.new, 1))
            passed, tail = _run_tests(_tests_for(case.path), timeout)
        finally:
            restorer.restore()
        results.append(Result(case.name, not passed,
                              "tests still passed (%s)" % tail if passed
                              else "caught"))
    return results


def run_generated(relative: str, limit: Optional[int] = None,
                  timeout: float = 900.0) -> List[Result]:
    path = ROOT / relative
    source = path.read_text()
    mutants = find_mutants(source)
    if limit:
        mutants = mutants[:limit]
    modules = _tests_for(relative)
    results: List[Result] = []
    restorer = _Restorer(path)
    try:
        for mutant in mutants:
            try:
                mutated = apply_mutant(source, mutant.index)
            except (SyntaxError, ValueError, RecursionError) as exc:
                results.append(Result("line %d %s" % (mutant.line,
                                                      mutant.description),
                                      True, "unparseable (%s)" % exc))
                continue
            write_source(path, mutated)
            passed, tail = _run_tests(modules, timeout)
            results.append(Result(
                "line %d %s" % (mutant.line, mutant.description),
                not passed, "SURVIVED (%s)" % tail if passed else "caught"))
            print("  %-46s %s" % (results[-1].name,
                                  "killed" if results[-1].killed
                                  else "SURVIVED"), flush=True)
    finally:
        restorer.restore()
    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that the tests would notice if the code changed.")
    parser.add_argument("--regressions", action="store_true",
                        help="replay the defects this project has shipped; "
                             "every one must be caught")
    parser.add_argument("--file", help="generate mutations for one source file")
    parser.add_argument("--limit", type=int,
                        help="stop after this many generated mutants")
    parser.add_argument("--only", help="run regressions whose name contains this")
    parser.add_argument("--timeout", type=float, default=900.0,
                        help="seconds one test run may take (default %(default)s)")
    parser.add_argument("--list", action="store_true",
                        help="list mutation sites without running anything")
    args = parser.parse_args(argv)

    if not args.regressions and not args.file:
        parser.error("give --regressions or --file")

    if args.file and args.list:
        for mutant in find_mutants((ROOT / args.file).read_text()):
            print("%4d  line %-5d %s"
                  % (mutant.index, mutant.line, mutant.description))
        return 0

    targets = [case.path for case in REGRESSIONS] if args.regressions else []
    if args.file:
        targets.append(args.file)
    dirty = sorted({t for t in targets if _is_dirty(t)})
    if dirty:
        print("error: these files have uncommitted changes, and this tool "
              "edits files in place:\n  %s\nCommit or stash first."
              % "\n  ".join(dirty), file=sys.stderr)
        return 2

    results: List[Result] = []
    if args.regressions:
        print("replaying %d shipped defect(s)..."
              % len([c for c in REGRESSIONS
                     if not args.only or args.only in c.name]), flush=True)
        results += run_regressions(args.only, args.timeout)
        for result in results:
            print("  %-28s %s" % (result.name,
                                  "killed" if result.killed
                                  else "SURVIVED -- " + result.detail))
    if args.file:
        print("mutating %s..." % args.file, flush=True)
        results += run_generated(args.file, args.limit, args.timeout)

    survivors = [r for r in results if not r.killed]
    print()
    print("%d mutant(s): %d killed, %d survived"
          % (len(results), len(results) - len(survivors), len(survivors)))
    if survivors:
        print()
        print("SURVIVORS -- behaviour nothing checks:")
        for result in survivors:
            print("  %s\n    %s" % (result.name, result.detail))
        print()
        print("Some may be equivalent mutants, where the change genuinely "
              "cannot be observed. Read before fixing.")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())

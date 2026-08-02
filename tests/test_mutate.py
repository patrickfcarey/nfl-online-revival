"""The mutation engine. Fast, and it never runs a mutation.

Actually mutating and re-running the suite takes minutes and belongs in
`python3 tools/mutate.py`, not in a suite that has to stay quick. What is here
is everything that can go wrong without executing anything: mutation sites
found, applied in isolation, files restored, and -- the one that earns its
place -- the regression catalogue checked against the source it claims to
mutate.

That last check exists because the catalogue went stale within a day of being
written. `news-status-tag` named a line that had been reworded, so the entry
reported SURVIVED for a defect it had never actually applied: a false alarm
that looks identical to a real test gap. A catalogue of exact substitutions
rots silently every time the code it quotes is touched.
"""

from __future__ import annotations

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import mutate  # noqa: E402

SAMPLE = '''
def check(value, limit):
    if value < limit and limit > 0:
        return True
    if not value:
        return False
    return value == limit
'''


class FindingSites(unittest.TestCase):
    def test_it_finds_every_kind(self):
        kinds = {m.description.split(":")[0]
                 for m in mutate.find_mutants(SAMPLE)}
        self.assertEqual(kinds, {"compare", "boolop", "not", "bool", "int"})

    def test_sites_are_numbered_from_zero_without_gaps(self):
        mutants = mutate.find_mutants(SAMPLE)
        self.assertEqual([m.index for m in mutants], list(range(len(mutants))))

    def test_the_order_is_stable(self):
        # A run has to be reproducible, or a surviving mutant cannot be looked
        # up again by its number.
        first = [m.description for m in mutate.find_mutants(SAMPLE)]
        second = [m.description for m in mutate.find_mutants(SAMPLE)]
        self.assertEqual(first, second)

    def test_line_numbers_are_reported(self):
        for mutant in mutate.find_mutants(SAMPLE):
            self.assertGreater(mutant.line, 0)

    def test_source_with_nothing_to_mutate(self):
        self.assertEqual(mutate.find_mutants("def f():\n    pass\n"), [])


class ApplyingSites(unittest.TestCase):
    def test_a_mutant_is_valid_python(self):
        for mutant in mutate.find_mutants(SAMPLE):
            mutated = mutate.apply_mutant(SAMPLE, mutant.index)
            ast.parse(mutated)              # must not raise

    def test_a_mutant_actually_differs(self):
        # A mutation that changes nothing would be scored as survived and read
        # as a test gap.
        original = ast.dump(ast.parse(SAMPLE))
        for mutant in mutate.find_mutants(SAMPLE):
            mutated = ast.dump(ast.parse(mutate.apply_mutant(SAMPLE,
                                                             mutant.index)))
            self.assertNotEqual(mutated, original, mutant.description)

    def test_each_mutant_is_distinct(self):
        seen = {ast.dump(ast.parse(mutate.apply_mutant(SAMPLE, m.index)))
                for m in mutate.find_mutants(SAMPLE)}
        self.assertEqual(len(seen), len(mutate.find_mutants(SAMPLE)))

    def test_only_one_site_changes_at_a_time(self):
        """Otherwise a survivor cannot be attributed to anything.

        Counting the operators: exactly one comparison should differ between
        the original and a compare-mutant.
        """
        source = "def f(a, b):\n    return a < b or a > b\n"
        mutants = mutate.find_mutants(source)
        compare = [m for m in mutants if m.description.startswith("compare")][0]
        mutated = mutate.apply_mutant(source, compare.index)
        original_ops = [type(o).__name__ for n in ast.walk(ast.parse(source))
                        if isinstance(n, ast.Compare) for o in n.ops]
        new_ops = [type(o).__name__ for n in ast.walk(ast.parse(mutated))
                   if isinstance(n, ast.Compare) for o in n.ops]
        differing = sum(1 for a, b in zip(original_ops, new_ops) if a != b)
        self.assertEqual(differing, 1)

    def test_comparison_swaps_are_the_documented_ones(self):
        self.assertEqual(mutate._COMPARE_SWAPS[ast.Lt], ast.LtE)
        self.assertEqual(mutate._COMPARE_SWAPS[ast.Eq], ast.NotEq)
        self.assertEqual(mutate._COMPARE_SWAPS[ast.Is], ast.IsNot)

    def test_dropping_a_not_inverts_the_branch(self):
        source = "def f(a):\n    return not a\n"
        mutant = [m for m in mutate.find_mutants(source)
                  if m.description.startswith("not")][0]
        self.assertNotIn("not", mutate.apply_mutant(source, mutant.index))


class Restoring(unittest.TestCase):
    """This tool edits files in place; losing one would be unforgivable."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".py")
        os.write(handle, b"original contents\n")
        os.close(handle)
        self.addCleanup(lambda: os.path.exists(self.path)
                        and os.unlink(self.path))

    def test_it_puts_the_file_back(self):
        restorer = mutate._Restorer(Path(self.path))
        Path(self.path).write_text("mutated")
        restorer.restore()
        self.assertEqual(Path(self.path).read_text(), "original contents\n")

    def test_writing_drops_the_cached_bytecode(self):
        """The hazard that poisoned this repository for an hour.

        Python decides a .pyc is current from the source's (mtime, size). Every
        mutation replaces text with text of the same length and is restored
        within the same second, so both halves still match and the next import
        gets the mutant's bytecode while the source on disk is correct. It left
        three tests failing against a file identical to HEAD, with a clean
        `git status`.
        """
        import py_compile
        source = Path(self.path).with_suffix(".py")
        source.write_text("VALUE = 1\n")
        cached = Path(py_compile.compile(str(source), doraise=True))
        self.assertTrue(cached.exists())
        mutate.write_source(source, "VALUE = 2\n")
        self.assertFalse(cached.exists(),
                         "the mutant's bytecode survived the write")

    def test_restoring_drops_the_cached_bytecode_too(self):
        import py_compile
        source = Path(self.path).with_suffix(".py")
        source.write_text("VALUE = 1\n")
        restorer = mutate._Restorer(source)
        mutate.write_source(source, "VALUE = 2\n")
        cached = Path(py_compile.compile(str(source), doraise=True))
        self.assertTrue(cached.exists())
        restorer.restore()
        self.assertFalse(cached.exists(),
                         "a restored file must not leave the mutant cached")
        self.assertEqual(source.read_text(), "VALUE = 1\n")

    def test_restoring_twice_is_harmless(self):
        restorer = mutate._Restorer(Path(self.path))
        Path(self.path).write_text("mutated")
        restorer.restore()
        Path(self.path).write_text("changed again after restore")
        restorer.restore()          # latched: must not clobber
        self.assertEqual(Path(self.path).read_text(),
                         "changed again after restore")


class TestSelection(unittest.TestCase):
    def test_known_sources_map_to_test_modules(self):
        self.assertIn("tests.test_limits",
                      mutate._tests_for("backend/limits.py"))

    def test_an_unmapped_source_falls_back_to_everything(self):
        # Correct but slow, which is why the map exists.
        self.assertEqual(mutate._tests_for("backend/nonesuch.py"), [])

    def test_every_mapped_test_module_exists(self):
        for source, modules in mutate.TEST_MODULES.items():
            self.assertTrue((mutate.ROOT / source).exists(),
                            "%s is mapped but does not exist" % source)
            for module in modules:
                path = mutate.ROOT / (module.replace(".", "/") + ".py")
                self.assertTrue(path.exists(),
                                "%s maps to %s, which does not exist"
                                % (source, module))


class RegressionCatalogue(unittest.TestCase):
    """The entries must still match the source they claim to mutate."""

    def test_every_entry_targets_a_file_that_exists(self):
        for case in mutate.REGRESSIONS:
            self.assertTrue((mutate.ROOT / case.path).exists(), case.name)

    def test_every_entry_still_matches_its_source(self):
        """The staleness guard, and the reason this class exists.

        An entry whose quoted text has been reworded applies nothing and
        reports SURVIVED -- a false alarm indistinguishable from a real test
        gap. This catches it in a tenth of a second instead of after a
        fifteen-minute mutation run.
        """
        stale = []
        for case in mutate.REGRESSIONS:
            source = (mutate.ROOT / case.path).read_text()
            if case.old not in source:
                stale.append("%s: %r is no longer in %s"
                             % (case.name, case.old[:60], case.path))
        self.assertEqual(stale, [], "\n".join(stale))

    def test_every_entry_actually_changes_the_source(self):
        for case in mutate.REGRESSIONS:
            source = (mutate.ROOT / case.path).read_text()
            self.assertNotEqual(source.replace(case.old, case.new, 1), source,
                                "%s mutates to identical text" % case.name)

    def test_every_mutation_leaves_parseable_python(self):
        # A syntax error is caught by every test at once, which would score as
        # a kill for the wrong reason.
        for case in mutate.REGRESSIONS:
            source = (mutate.ROOT / case.path).read_text()
            try:
                ast.parse(source.replace(case.old, case.new, 1))
            except SyntaxError as exc:
                self.fail("%s produces unparseable source: %s"
                          % (case.name, exc))

    def test_every_entry_says_why_it_matters(self):
        # The name alone does not tell a future reader what breaks.
        for case in mutate.REGRESSIONS:
            self.assertGreater(len(case.why), 40, case.name)

    def test_names_are_unique(self):
        names = [case.name for case in mutate.REGRESSIONS]
        self.assertEqual(len(names), len(set(names)))

    def test_the_catalogue_covers_both_halves_of_the_project(self):
        paths = {case.path.split("/")[0] for case in mutate.REGRESSIONS}
        self.assertIn("backend", paths)
        self.assertIn("tools", paths)
        self.assertIn("recon", paths)


class Cli(unittest.TestCase):
    def test_neither_mode_is_an_error(self):
        with self.assertRaises(SystemExit):
            mutate.main([])

    def test_listing_sites_runs_nothing(self):
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = mutate.main(["--file", "backend/limits.py", "--list"])
        self.assertEqual(code, 0)
        self.assertIn("compare", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()

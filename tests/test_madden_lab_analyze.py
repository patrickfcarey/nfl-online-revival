"""The analysis commands, exercised against files rather than an emulator.

Every test here builds a result file and reads it back, because that is the
whole contract: these commands run on a laptop, months later, against a run
someone else recorded. Nothing below imports layer 1, 2 or 3.

Three properties get most of the attention, because all three are ways of
being confidently wrong rather than ways of crashing:

* **the substring prefilter must be semantically invisible.** It exists to skip
  99.9% of a 1.5 GB file without parsing it, and a filter that drops one row it
  should have kept changes a number without saying so. Tested by asserting the
  filtered and unfiltered streams agree.
* **an episode boundary must survive a torn read.** PINE reads are not
  synchronised with emulation, so one sample can carry a value the game never
  held; an analysis keyed on a single frame turns that into a transition that
  never happened.
* **a file being written right now must read.** Runs take an hour and get
  inspected while running, so the truncated final line is the normal case, not
  the exception.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.madden_lab import __main__ as cli  # noqa: E402
from tools.madden_lab import align  # noqa: E402
from tools.madden_lab import analyze  # noqa: E402
from tools.madden_lab import results as res  # noqa: E402

BASE = {"run_id": "8f3c1d2e4b6a", "spec": "lead_blocker_gate_a",
        "spec_digest": "4a1f9c22", "state": "SLUS-20752 (14F8B841).06.p2s",
        "git_rev": "30c24db", "arm": "baseline"}


def row(**fields):
    merged = dict(BASE)
    merged.update(fields)
    return json.dumps(merged, separators=(",", ":"))


def sample(iteration, tick, entity, field, value, certified=True):
    return row(kind="sample", iteration=iteration, frame=tick, sample=tick,
               entity=entity, field=field, value=value, tick=tick,
               certified=certified)


def write(directory, name, lines, tail=""):
    path = os.path.join(directory, name)
    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
        if tail:
            handle.write(tail)          # deliberately unterminated
    return path


def build_run(directory, name="run.jsonl", iterations=3, torn_tick=50,
              uncertified_tick=100, tail=""):
    """A run with the shape the lead-blocker experiment produces.

    `player:0:9` is in block mode 3 and walks 0 -> 2 -> 4 through the
    engagement kinds; `player:0:5` is an ordinary pool blocker. One sample at
    `torn_tick` reports the wrong engagement, which is what a torn read looks
    like, and one whole frame is uncertified.
    """
    lines = [row(kind="run", iteration=None, trial="lead_blocker_gate_a",
                 question="does the FB enter the assignment system?")]
    link = 1 | (1 << 8) | (15 << 16)          # handle: player, side 1, index 15
    for index in range(iterations):
        for tick in range(4, 220):
            engagement = 0 if tick < 90 else (2 if tick < 164 else 4)
            if tick == torn_tick:
                engagement = 2
            certified = tick != uncertified_tick
            for entity, field, value in (
                    ("player:0:9", "engagement", engagement),
                    ("player:0:9", "engagement_link", 0 if not engagement else link),
                    ("player:0:9", "block_mode", 3 if tick > 6 else 0),
                    ("player:0:9", "xyz", [round(-2.0 + tick * 0.05, 4),
                                           round(10.0 + tick * 0.03, 4), 0.0]),
                    ("player:0:5", "block_mode", 1),
                    ("player:0:5", "engagement", 4 if tick > 20 else 0),
                    ("game", "los", 15.0)):
                lines.append(sample(index, tick, entity, field, value, certified))
        for metric, value in (("carrier_yards", [1.1, 0.3, 0.9][index % 3]),
                              ("lead_blockers_seen", 1.0),
                              ("first_mode3_frame", None)):
            lines.append(row(kind="metric", iteration=index, metric=metric,
                             value=value))
        lines.append(row(kind="iteration", iteration=index, digest="same",
                         frames=216, span=216, status="ok", reason="whistle"))
    return write(directory, name, lines, tail=tail)


@contextlib.contextmanager
def captured():
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        yield buffer


# --------------------------------------------------------------------------
# The reader seam (R1/R2)
# --------------------------------------------------------------------------


class ReaderSeamTests(unittest.TestCase):

    def test_truncated_last_line_is_forgiven_and_a_bad_middle_line_is_not(self):
        with tempfile.TemporaryDirectory() as directory:
            good = write(directory, "good.jsonl",
                         [row(kind="run", iteration=None)],
                         tail='{"kind":"sample","iter')
            self.assertEqual(1, len(list(res.read_rows(good))))
            bad = write(directory, "bad.jsonl",
                        ['{"kind":"run"', row(kind="metric", iteration=0)])
            with self.assertRaises(ValueError):
                list(res.read_rows(bad))

    def test_kind_filtering_is_invisible_apart_from_speed(self):
        """The prefilter may only ever reject rows the caller would reject."""
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=2)
            everything = [r for r in res.read_rows(path)
                          if r.get("kind") == "metric"]
            filtered = list(res.read_rows(path, kinds=("metric",)))
            self.assertEqual(everything, filtered)
            self.assertTrue(everything)

    def test_kind_needle_cannot_appear_inside_a_value(self):
        """JSON escaping is what makes the substring test sound, not luck."""
        text = row(kind="event", iteration=0, event="odd",
                   detail='a value containing "kind":"sample" verbatim')
        self.assertNotIn(res.kind_needle("sample"), text)
        self.assertIn(res.kind_needle("event"), text)

    def test_load_run_counts_samples_and_uncertified_without_parsing_them(self):
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=1)
            run = res.load_run(path)
            self.assertEqual(216 * 7, run.samples)
            self.assertEqual(7, run.uncertified)      # one whole frame
            self.assertEqual(3, len(run.metrics))


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


class SelectionTests(unittest.TestCase):

    def test_a_value_json_would_escape_produces_no_needle(self):
        """Refusing to prefilter is slow; prefiltering on the wrong bytes is
        silent data loss, so the ambiguous case must fall back to parsing."""
        self.assertIsNone(analyze._token_needle("entity", 'quote"inside'))
        self.assertIsNone(analyze._token_needle("entity", "café"))
        self.assertEqual('"entity":"player:0:9"',
                         analyze._token_needle("entity", "player:0:9"))
        self.assertEqual('"iteration":3', analyze._token_needle("iteration", 3))

    def test_needles_are_dropped_for_a_whole_group_if_any_member_is_unsafe(self):
        selection = analyze.Selection(entities=("player:0:9", 'bad"one'))
        self.assertEqual((), selection.needles())

    def test_an_entity_with_an_awkward_name_still_streams(self):
        with tempfile.TemporaryDirectory() as directory:
            odd = 'player:0:"9"'
            path = write(directory, "odd.jsonl",
                         [sample(0, 4, odd, "engagement", 2)])
            rows = list(analyze.stream(path, analyze.Selection(entities=(odd,))))
            self.assertEqual(1, len(rows))

    def test_stream_stops_quietly_on_a_run_still_being_written(self):
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=1,
                             tail='{"kind":"sample","iteration":1,"fra')
            rows = list(analyze.stream(path, analyze.Selection(kinds=("sample",))))
            self.assertEqual(216 * 7, len(rows))


# --------------------------------------------------------------------------
# Episodes
# --------------------------------------------------------------------------


class EpisodeTests(unittest.TestCase):

    def series(self, values, min_samples=2, start=0):
        found = analyze.FieldSeries("player:0:9", "engagement")
        for offset, value in enumerate(values):
            found.add(0, start + offset, value)
        found.finish(min_samples)
        return found

    def test_a_constant_run_is_one_episode(self):
        found = self.series([0] * 5)
        self.assertEqual(1, len(found.episodes))
        self.assertEqual((0, 4, 5), (found.episodes[0].first_tick,
                                     found.episodes[0].last_tick,
                                     found.episodes[0].samples))

    def test_a_one_sample_flicker_between_equal_neighbours_is_folded(self):
        found = self.series([0, 0, 0, 2, 0, 0, 0])
        self.assertEqual(1, len(found.episodes))
        self.assertEqual(7, found.episodes[0].samples)
        self.assertEqual(1, found.folded)

    def test_a_short_episode_between_different_values_is_kept(self):
        """Folding A-B-C would have to delete one of two real transitions."""
        found = self.series([0, 0, 2, 4, 4])
        self.assertEqual([0, 2, 4], [ep.value for ep in found.episodes])
        self.assertEqual(0, found.folded)

    def test_min_frames_one_keeps_every_flicker(self):
        found = self.series([0, 0, 2, 0, 0], min_samples=1)
        self.assertEqual(3, len(found.episodes))

    def test_dropped_frames_show_as_span_wider_than_samples(self):
        found = analyze.FieldSeries("player:0:9", "engagement")
        for tick in (10, 11, 14, 15):          # the loop missed 12 and 13
            found.add(0, tick, 2)
        found.finish(1)
        episode = found.episodes[0]
        self.assertEqual(6, episode.span)
        self.assertEqual(4, episode.samples)

    def test_a_clock_rewind_closes_the_episode_rather_than_extending_it(self):
        found = analyze.FieldSeries("player:0:9", "engagement")
        for tick in (100, 101, 102, 40, 41):    # a savestate landing mid-play
            found.add(0, tick, 2)
        found.finish(1)
        self.assertEqual(2, len(found.episodes))
        self.assertEqual(1, found.rewinds)

    def test_an_iteration_boundary_closes_the_episode(self):
        found = analyze.FieldSeries("player:0:9", "engagement")
        found.add(0, 10, 2)
        found.add(0, 11, 2)
        found.add(1, 10, 2)
        found.finish(1)
        self.assertEqual([0, 1], [ep.iteration for ep in found.episodes])

    def test_a_float_field_becomes_continuous_and_drops_its_episodes(self):
        found = analyze.FieldSeries("player:0:9", "xyz")
        for tick in range(20):
            found.add(0, tick, [float(tick), 1.5, 0.0])
        found.finish(2)
        self.assertTrue(found.continuous)
        self.assertEqual([], found.episodes)
        self.assertEqual([0.0, 1.5, 0.0], found.lo)
        self.assertEqual([19.0, 1.5, 0.0], found.hi)

    def test_a_list_value_can_key_the_census(self):
        """JSON hands back lists, which are unhashable; a census needs tuples."""
        found = analyze.FieldSeries("player:0:9", "pair")
        found.add(0, 1, [1, 2])
        found.add(0, 2, [1, 2])
        found.finish(1)
        self.assertEqual([((1, 2), 2)], found.census_rows())


# --------------------------------------------------------------------------
# timeline
# --------------------------------------------------------------------------


class TimelineTests(unittest.TestCase):

    def test_the_census_and_episodes_answer_the_hand_written_question(self):
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=1)
            report = analyze.timeline(path, field="engagement",
                                      entity="player:0:9",
                                      with_field="engagement_link")
            self.assertEqual(1, len(report.series))
            found = report.series[0]
            self.assertEqual({0: 85, 2: 75, 4: 56}, dict(found.census_rows()))
            self.assertEqual([0, 2, 4], [ep.value for ep in found.episodes])
            self.assertEqual(1, found.folded)     # the torn sample at tick 50
            engaged = found.episodes[1]
            self.assertEqual((90, 163), (engaged.first_tick, engaged.last_tick))

    def test_the_partner_field_decodes_to_the_man_he_is_engaged_with(self):
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=1)
            report = analyze.timeline(path, field="engagement",
                                      entity="player:0:9",
                                      with_field="engagement_link")
            text = analyze.format_timeline(report)
            self.assertIn("player:1:15", text)

    def test_discovery_finds_the_entities_that_held_a_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=1)
            report = analyze.timeline(path, field="block_mode", value=3)
            self.assertEqual(["player:0:9"],
                             [found.entity for found in report.series])
            self.assertEqual(["player:0:5"], report.skipped)

    def test_a_single_torn_sample_does_not_manufacture_a_second_holder(self):
        """`any(v == 3)` over a few thousand samples eventually says yes to a
        value that was never real, which would refute the single-writer
        reading of 0x001b6780 with read noise."""
        with tempfile.TemporaryDirectory() as directory:
            lines = [sample(0, tick, "player:0:5", "block_mode",
                            3 if tick == 12 else 1) for tick in range(4, 40)]
            path = write(directory, "torn.jsonl", lines)
            self.assertEqual([], analyze.timeline(path, field="block_mode",
                                                  value=3).series)
            self.assertEqual(["player:0:5"],
                             [found.entity for found in
                              analyze.timeline(path, field="block_mode", value=3,
                                               min_samples=1).series])

    def test_a_continuous_field_reports_a_track_and_no_episodes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=1)
            report = analyze.timeline(path, field="xyz", entity="player:0:9")
            found = report.series[0]
            self.assertTrue(found.continuous)
            self.assertEqual([], found.episodes)
            self.assertIn("continuous field", analyze.format_timeline(report))

    def test_certified_only_excludes_the_torn_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=1)
            loose = analyze.timeline(path, field="engagement", entity="player:0:9")
            strict = analyze.timeline(path, field="engagement",
                                      entity="player:0:9", certified_only=True)
            self.assertEqual(loose.series[0].samples - 1,
                             strict.series[0].samples)
            self.assertEqual(0, strict.uncertified)

    def test_json_output_keeps_the_raw_handle_word(self):
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=1)
            report = analyze.timeline(path, field="engagement",
                                      entity="player:0:9",
                                      with_field="engagement_link")
            payload = analyze.timeline_as_dict(report)
            partners = payload["series"][0]["episodes"][1]["partners"]
            self.assertIn(1 | (1 << 8) | (15 << 16), partners)


# --------------------------------------------------------------------------
# summarize
# --------------------------------------------------------------------------


class SummarizeTests(unittest.TestCase):

    def test_none_is_counted_not_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=3)
            report = analyze.summarize(path)
            by_name = {m.name: m for m in report.metrics}
            self.assertEqual(0, by_name["first_mode3_frame"].n)
            self.assertEqual(3, by_name["first_mode3_frame"].missing)
            self.assertIn("never produced a value",
                          analyze.format_summary(report))

    def test_the_centre_is_a_median_so_one_noisy_iteration_cannot_move_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=3)
            report = analyze.summarize(path)
            carrier = {m.name: m for m in report.metrics}["carrier_yards"]
            self.assertEqual([1.1, 0.3, 0.9], carrier.values)
            text = analyze.format_summary(report, only="carrier_yards")
            self.assertIn("0.900", text)             # median, not the 0.767 mean
            self.assertIn("values: 1.100  0.300  0.900", text)

    def test_an_iteration_still_running_is_named_not_averaged_in(self):
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=2)
            with io.open(path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(row(kind="metric", iteration=2,
                                 metric="carrier_yards", value=0.1) + "\n")
            report = analyze.summarize(path)
            self.assertEqual([2], report.in_progress)
            self.assertIn("still writing", " ".join(report.warnings))
            complete = analyze.summarize(path, complete_only=True)
            carrier = {m.name: m for m in complete.metrics}["carrier_yards"]
            self.assertNotIn(0.1, carrier.values)

    def test_identical_iterations_are_reported_before_any_spread_is(self):
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=3)
            report = analyze.summarize(path)
            self.assertTrue(report.degenerate)
            text = analyze.format_summary(report)
            self.assertLess(text.index("n_effective is 1"), text.index("metric"))


# --------------------------------------------------------------------------
# agree
# --------------------------------------------------------------------------


class AgreeTests(unittest.TestCase):

    def test_identical_replays_are_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=3)
            report = analyze.agree(path)
            self.assertEqual([0, 1, 2], report.iterations)
            self.assertEqual("DETERMINISTIC", report.verdict)
            self.assertTrue(all(f.real == 0 for f in report.fields))

    def test_a_real_divergence_is_reported_as_real(self):
        with tempfile.TemporaryDirectory() as directory:
            lines = []
            for iteration in range(2):
                for tick in range(10, 30):
                    value = 2 if (iteration == 1 and tick > 20) else 0
                    lines.append(sample(iteration, tick, "player:0:9",
                                        "engagement", value))
            path = write(directory, "diverge.jsonl", lines)
            report = analyze.agree(path)
            self.assertEqual("DIVERGENT", report.verdict)
            self.assertIn("REAL", analyze.format_agree(report))

    def test_an_adjacent_tick_transition_is_phase_not_divergence(self):
        """The classification that acquitted the engine, reached from a file."""
        with tempfile.TemporaryDirectory() as directory:
            lines = []
            for iteration in range(2):
                switch = 20 + iteration          # one tick apart
                for tick in range(10, 30):
                    lines.append(sample(iteration, tick, "player:0:9",
                                        "engagement", 0 if tick < switch else 2))
            path = write(directory, "phase.jsonl", lines)
            report = analyze.agree(path)
            self.assertEqual("DETERMINISTIC (phase noise only)", report.verdict)

    def test_a_file_with_no_certified_samples_refuses_rather_than_aligning(self):
        """Its `tick` is a sample ordinal, and aligning on that is the original
        mistake: it compares different moments of the play."""
        with tempfile.TemporaryDirectory() as directory:
            lines = [sample(i, t, "player:0:9", "engagement", 0, certified=False)
                     for i in range(2) for t in range(10, 20)]
            path = write(directory, "old.jsonl", lines)
            report = analyze.agree(path)
            self.assertEqual([], report.fields)
            self.assertIn("sample ordinal", " ".join(report.warnings))

    def test_iterations_beyond_the_cap_are_not_held_resident(self):
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=3)
            report = analyze.agree(path, repeats=2)
            self.assertEqual([0, 1], report.iterations)
            self.assertIn("stopped after 2 iterations", " ".join(report.warnings))

    def test_the_classifier_is_the_runner_s_and_not_a_second_copy(self):
        self.assertIs(analyze.agreement, align.agreement)
        from tools.madden_lab import runner as runner_mod
        self.assertIs(runner_mod.agreement, align.agreement)
        self.assertIs(runner_mod.FieldAgreement, align.FieldAgreement)


# --------------------------------------------------------------------------
# slice
# --------------------------------------------------------------------------


class SliceTests(unittest.TestCase):

    def test_the_slice_is_a_result_file_every_other_tool_still_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=2)
            out = os.path.join(directory, "guard.jsonl")
            report = analyze.slice_run(
                path, out, analyze.Selection(kinds=("sample",),
                                             entities=("player:0:9",),
                                             fields=("xyz",)))
            self.assertEqual(2 * 216, report.kept)
            run = res.load_run(out)
            self.assertEqual("4a1f9c22", run.spec_digest)
            self.assertEqual(2, len(run.iterations))
            self.assertTrue(run.metric_values("carrier_yards"))

    def test_rows_are_copied_byte_for_byte(self):
        """A slice that re-encodes cannot be checked against its source."""
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=1)
            out = os.path.join(directory, "cut.jsonl")
            analyze.slice_run(path, out,
                              analyze.Selection(kinds=("sample",),
                                                fields=("los",)))
            with io.open(path, encoding="utf-8") as handle:
                source = {line for line in handle if '"field":"los"' in line}
            with io.open(out, encoding="utf-8") as handle:
                cut = {line for line in handle if '"field":"los"' in line}
            self.assertEqual(source, cut)

    def test_where_selects_entities_and_says_it_took_two_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=1)
            out = os.path.join(directory, "mode3.jsonl")
            report = analyze.slice_run(path, out,
                                       analyze.Selection(kinds=("sample",)),
                                       where=("block_mode", 3))
            self.assertEqual(2, report.passes)
            self.assertEqual(("player:0:9",), report.entities)
            entities = {r["entity"] for r in
                        res.read_rows(out, kinds=("sample",))}
            self.assertEqual({"player:0:9"}, entities)

    def test_where_will_not_select_on_a_single_torn_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            lines = [sample(0, tick, "player:0:5", "block_mode",
                            3 if tick == 12 else 1) for tick in range(4, 40)]
            path = write(directory, "torn.jsonl", lines)
            self.assertEqual([], analyze.entities_holding(path, "block_mode", 3))
            self.assertEqual(["player:0:5"],
                             analyze.entities_holding(path, "block_mode", 3,
                                                      min_samples=1))

    def test_a_gzip_slice_is_gzip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=1)
            out = os.path.join(directory, "cut.jsonl.gz")
            analyze.slice_run(path, out,
                              analyze.Selection(kinds=("sample",),
                                                fields=("los",)))
            with io.open(out, "rb") as handle:
                self.assertEqual(b"\x1f\x8b", handle.read(2))
            self.assertEqual(216, sum(1 for _ in
                                      res.read_rows(out, kinds=("sample",))))


# --------------------------------------------------------------------------
# The command line
# --------------------------------------------------------------------------


class CommandLineTests(unittest.TestCase):

    def test_every_analysis_command_runs_with_no_emulator(self):
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=3,
                             tail='{"kind":"sample","iteration":3,"fra')
            out = os.path.join(directory, "cut.jsonl")
            for argv in (["summarize", path],
                         ["summarize", path, "--json"],
                         ["summarize", path, "--metric", "carrier_yards"],
                         ["timeline", path, "--field", "engagement",
                          "--entity", "player:0:9", "--with", "engagement_link"],
                         ["timeline", path, "--field", "block_mode",
                          "--value", "3", "--json"],
                         ["agree", path],
                         ["slice", path, "--out", out, "--entity", "player:0:9",
                          "--field", "xyz"]):
                with captured() as buffer:
                    self.assertEqual(0, cli.main(argv), argv)
                self.assertTrue(buffer.getvalue().strip(), argv)

    def test_a_malformed_where_is_a_message_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = build_run(directory, iterations=1)
            out = os.path.join(directory, "cut.jsonl")
            with captured() as buffer:
                code = cli.main(["slice", path, "--out", out, "--where",
                                 "block_mode"])
            self.assertEqual(cli.EXIT_USAGE, code)
            self.assertIn("FIELD=VALUE", buffer.getvalue())

    def test_the_value_argument_is_read_as_the_row_holds_it(self):
        self.assertEqual(3, analyze.literal("3"))
        self.assertEqual(3.5, analyze.literal("3.5"))
        self.assertEqual("idle", analyze.literal("idle"))


if __name__ == "__main__":
    unittest.main()

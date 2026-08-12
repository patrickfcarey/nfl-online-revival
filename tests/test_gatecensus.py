"""Offline tests for the gate census.

The synthetic cases pin the arithmetic; the trace-backed case pins the finding
that the committed baseline run already contained -- engagement kind 8 never
occurs on slot 9, which is the fact DT-HOLD-90 was deployed without knowing.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.gatecensus import (Gate, Trace, census, decode_link, host_verdict)

TRACE = os.path.join(os.path.dirname(__file__), "..", "extract",
                     "slot9_baseline_dt3.jsonl")


def write_trace(frames):
    """`frames` is [{(side, idx): {field: value}}], one dict per frame."""
    fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    with fh:
        fh.write(json.dumps({"kind": "run", "spec": "synthetic"}) + "\n")
        for frame_no, players in enumerate(frames):
            for (side, idx), fields in players.items():
                for field, value in fields.items():
                    fh.write(json.dumps({
                        "kind": "sample", "iteration": 0, "frame": frame_no,
                        "entity": "player:%d:%d" % (side, idx),
                        "field": field, "value": value}) + "\n")
    return fh.name


def link_word(side, index, kind=1):
    return kind | (side << 8) | (index << 16)


class LinkDecodeTest(unittest.TestCase):
    def test_handle_layout_matches_the_cave(self):
        # The cave decodes with andi 0xFF / srl 8 / srl 16; 0x070001 is the
        # handle for side 0, index 7.
        self.assertEqual(decode_link(0x00070001), (0, 7))
        self.assertEqual(decode_link(link_word(1, 3)), (1, 3))

    def test_null_and_non_player_handles_reject(self):
        self.assertIsNone(decode_link(0))
        self.assertIsNone(decode_link(link_word(1, 3, kind=2)))
        self.assertIsNone(decode_link(-1))


class GateParseTest(unittest.TestCase):
    def test_parses_the_forms_the_cave_uses(self):
        g = Gate.parse("defender_kind: link.engagement in 5,6")
        self.assertEqual((g.name, g.subject, g.field, g.op), (
            "defender_kind", "link", "engagement", "in"))
        self.assertEqual(g.value, frozenset({5, 6}))
        self.assertEqual(Gate.parse("r: self.dt_role == 1").value, 1)
        self.assertEqual(Gate.parse("s: self.side != link.side").value,
                         ("link", "side"))

    def test_rejects_malformed_specs(self):
        for bad in ("no colon here", "n: self.field", "n: bogus.f == 1",
                    "n: self.f ~= 1"):
            with self.assertRaises(ValueError, msg=bad):
                Gate.parse(bad)


class CensusTest(unittest.TestCase):
    """A helper attached for 10 frames; his defender captured for only 3."""

    def setUp(self):
        frames = []
        for f in range(10):
            helper = {"dt_role": 1, "engagement": 8,
                      "engagement_link": link_word(1, 3)}
            # The defender only enters kinds 5/6 for the last three frames --
            # the simultaneity starvation that produced P8's five frames.
            defender = {"dt_role": 2, "engagement": 5 if f >= 7 else 2}
            frames.append({(0, 9): helper, (1, 3): defender})
        self.path = write_trace(frames)
        self.trace = Trace.from_jsonl(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def _census(self, *specs):
        return census(self.trace, [Gate.parse(s) for s in specs],
                      subjects=[(0, 9)])

    def test_ladder_names_the_binding_gate(self):
        c = self._census("helper_role:   self.dt_role == 1",
                         "helper_kind:   self.engagement == 8",
                         "defender_kind: link.engagement in 5,6")
        self.assertEqual(c.candidates, 10)
        self.assertEqual(c.ladder, [("helper_role", 10),
                                    ("helper_kind", 10),
                                    ("defender_kind", 3)])
        self.assertEqual(c.conjunction, 3)
        self.assertEqual(c.binding_gate, "defender_kind")

    def test_leave_one_out_prices_a_widening_before_it_is_deployed(self):
        # This is the P8b question asked offline: cutting the defender-kind
        # gate takes 3 -> 10; cutting either role gate buys nothing.
        c = self._census("helper_role:   self.dt_role == 1",
                         "helper_kind:   self.engagement == 8",
                         "defender_kind: link.engagement in 5,6")
        self.assertEqual(c.leave_one_out["defender_kind"], 10)
        self.assertEqual(c.leave_one_out["helper_role"], 3)
        self.assertEqual(c.best_widening, ("defender_kind", 10))

    def test_no_gate_worth_widening_is_reported_as_such(self):
        c = self._census("helper_role: self.dt_role == 1",
                         "helper_kind: self.engagement == 8")
        self.assertEqual(c.conjunction, 10)
        self.assertIsNone(c.best_widening)
        self.assertIn("no single gate is worth widening", c.report())

    def test_cross_subject_comparison(self):
        c = self._census("sides_differ: self.side != link.side")
        self.assertEqual(c.conjunction, 10)
        c = self._census("same_side: self.side == link.side")
        self.assertEqual(c.conjunction, 0)

    def test_unresolvable_link_rejects(self):
        # A null handle is the cave's `handle kind == 1` test failing.
        frames = [{(0, 9): {"dt_role": 1, "engagement_link": 0}}]
        path = write_trace(frames)
        try:
            c = census(Trace.from_jsonl(path),
                       [Gate.parse("d: link.dt_role == 2")])
            self.assertEqual(c.conjunction, 0)
        finally:
            os.unlink(path)

    def test_a_field_never_sampled_is_flagged_not_silently_zero(self):
        # Three probes in a row wanted fields the spec did not sample; a
        # gate over an absent field must not read as a real limiter.
        c = self._census("facing: self.facing == 3")
        self.assertTrue(c.unsampled["facing"])
        self.assertIn("never sampled", c.report())


class HostVerdictTest(unittest.TestCase):
    def test_the_p8b_arithmetic(self):
        # motion-block-cave.md: gates passed on 35 frames, canary read 5.
        # "roughly 1 frame in 7" -- and no gate widening can fix it.
        v = host_verdict(35, 5)
        self.assertEqual(v.verdict, "HOST-BOUND")
        self.assertIn("1 frame in 7.0", v.detail)

    def test_a_host_that_ticks_is_reported_as_gate_bound_work(self):
        v = host_verdict(35, 34)
        self.assertEqual(v.verdict, "HOST-TICKS")

    def test_a_chain_that_passes_nowhere(self):
        v = host_verdict(0, 0)
        self.assertEqual(v.verdict, "GATE-BOUND")


@unittest.skipUnless(os.path.exists(TRACE), "committed baseline trace missing")
class BaselineTraceTest(unittest.TestCase):
    """What the committed run already knew, before three patches were built."""

    @classmethod
    def setUpClass(cls):
        cls.trace = Trace.from_jsonl(TRACE, iteration=0)

    def test_kind_8_never_occurs_on_this_run_play(self):
        # "The registry doubles on this run play never use engagement kind 8"
        # -- the DT-HOLD-90 null, computable from this file before the patch
        # was designed. Any gate chain requiring kind 8 passes zero frames.
        c = census(self.trace, [Gate.parse("k8: self.engagement == 8")])
        self.assertEqual(c.conjunction, 0)
        self.assertTrue(c.unsampled["k8"])

    def test_the_registry_roles_do_occur(self):
        # The contrast that makes the kind-8 result meaningful: dt_role 1 and
        # 2 are both present, so the double team exists -- it just is not a
        # kind-8 engagement. Two systems, and the patch extended the wrong one.
        c = census(self.trace, [Gate.parse("helper: self.dt_role == 1")])
        self.assertGreater(c.conjunction, 0)
        c = census(self.trace, [Gate.parse("doubled: self.dt_role == 2")])
        self.assertGreater(c.conjunction, 0)

    def test_frames_and_players_parse(self):
        self.assertEqual(len(self.trace.frames), 308)
        self.assertEqual(len(self.trace.frames[100].players), 22)


if __name__ == "__main__":
    unittest.main()

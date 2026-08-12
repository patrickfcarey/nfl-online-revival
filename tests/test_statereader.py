"""Offline tests for the savestate reader against the committed states."""
import os
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.statereader import SavestateReader, classify_play

STATE = os.path.join(os.path.dirname(__file__), "..",
                     "experiments", "states", "double_team_slot9.p2s")


@unittest.skipUnless(os.path.exists(STATE), "committed savestate missing")
class SavestateReaderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = SavestateReader(STATE)

    def test_full_ee_image(self):
        self.assertEqual(len(self.r.mem), 32 * 1024 * 1024)

    def test_known_code_word(self):
        # Site A's kind-7/8 band init, stock in every committed state --
        # these states predate all patches, and a state saved with cheats
        # active would bake the patch in. This is the drift detector.
        self.assertEqual(self.r.read(0x001EF918, 4), 0x2403001E)

    def test_play_class_is_zero(self):
        # Lane 2 proved slots 6/7 classify 0; this pins slot 9 (measured
        # 2026-08-11) so the DT-3 unreachability finding stays regression-
        # tested even if the classifier map is edited.
        self.assertEqual(classify_play(self.r, 0), 0)
        self.assertEqual(classify_play(self.r, 1), 0)

    def test_los_via_typed_layer(self):
        from tools.madden_lab.world import World
        self.assertAlmostEqual(World(self.r).los(), 15.0, places=3)


if __name__ == "__main__":
    unittest.main()

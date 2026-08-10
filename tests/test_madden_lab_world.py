"""Tests for the observation layer.

Two kinds of test here, deliberately.

The synthetic ones build a fake EE memory from scratch and check that
`world.py` walks it the way the engine does -- the descriptor indirection, the
5312-byte stride, the handle format. Those run anywhere and they fail for the
right reason, because the fixture is built from the layout rather than from a
dump that happens to agree with it.

The rest read the real 32 MB images in `extract/` and are skipped when they are
absent. They are the ones that would catch the map drifting away from the game,
which a synthetic fixture cannot do by construction. Where a claim in `docs/`
was checked against real memory and failed, the test asserts the *finding* --
so if someone later "fixes" the map back to what the documentation says, this
fails and points at the evidence.
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.madden_lab import world as W

REPO = Path(__file__).resolve().parent.parent
MENU_DUMP = REPO / "extract" / "ee_mainmenu.bin"
INPLAY_DUMP = REPO / "extract" / "ee_inplay.bin"


class FakeMemory:
    """A sparse EE image. Unwritten addresses read 0, exactly like PINE.

    That last property is the point: a PINE read of an address nothing has
    mapped returns 0 rather than failing, so a drifted address is
    indistinguishable from a zero field. The fake reproduces that so the
    `verify()` tests are testing the real hazard.
    """

    def __init__(self) -> None:
        self.mem = bytearray(W.EE_SIZE)

    def put(self, addr: int, data: bytes) -> None:
        self.mem[addr:addr + len(data)] = data

    def put_u8(self, addr: int, v: int) -> None:
        self.put(addr, struct.pack("<B", v))

    def put_u16(self, addr: int, v: int) -> None:
        self.put(addr, struct.pack("<H", v))

    def put_u32(self, addr: int, v: int) -> None:
        self.put(addr, struct.pack("<I", v))

    def put_f32(self, addr: int, v: float) -> None:
        self.put(addr, struct.pack("<f", v))

    def read(self, address: int, size: int = 4) -> int:
        return int.from_bytes(self.mem[address:address + size], "little")

    def read_bytes(self, address: int, count: int) -> bytes:
        return bytes(self.mem[address:address + count])


class NoBatchReader:
    """A reader with no `read_many`, to exercise the fallback path."""

    def __init__(self, inner: FakeMemory) -> None:
        self.inner = inner
        self.calls = 0

    def read(self, address: int, size: int = 4) -> int:
        self.calls += 1
        return self.inner.read(address, size)

    def read_bytes(self, address: int, count: int) -> bytes:
        return self.inner.read_bytes(address, count)


def build_world(players=(), per_side=0):
    """A fake world with a player array laid out the way GetPlayer expects."""
    mem = FakeMemory()
    amap = W.AddressMap.load()
    if players:
        desc, base = 0x00700000, 0x00710000
        stride = amap.stride("player")
        mem.put_u32(0x00600E48, desc)
        mem.put_u32(desc + 0x00, base)
        mem.put_u16(desc + 0x08, per_side)
        mem.put_u16(desc + 0x0A, len(players))
        for slot, spec in enumerate(players):
            p = base + slot * stride
            side, index = divmod(slot, per_side)
            mem.put_u8(p + 0x00, 1)          # handle kind: player
            mem.put_u8(p + 0x01, side)
            mem.put_u8(p + 0x02, index)
            mem.put_u8(p + 0xB04, spec["position"])
            mem.put_f32(p + 0xAEC, spec.get("weight", 200.0))
            mem.put_f32(p + 0x190, spec.get("x", 0.0))
            mem.put_f32(p + 0x194, spec.get("y", 0.0))
            for i, r in enumerate(spec.get("ratings", [])):
                mem.put_u16(p + 0xB70 + 2 * i, r)
    return W.World(mem, amap), mem


# ---------------------------------------------------------------------------
# The map itself
# ---------------------------------------------------------------------------

class TestAddressMap(unittest.TestCase):

    def setUp(self) -> None:
        self.amap = W.AddressMap.load()

    def test_every_field_is_expressible(self):
        """A type the decoder cannot handle is a map bug, not a runtime bug."""
        for sname in self.amap.structs:
            for fname, field in self.amap.fields(sname).items():
                if field.type == "struct":
                    continue   # nested arrays carry their own struct_name
                with self.subTest(struct=sname, field=fname):
                    self.assertIn(field.type, W._SCALARS)
                    self.assertGreaterEqual(field.offset, 0)

    def test_every_entry_cites_a_source(self):
        """The map's whole justification is that a wrong number is traceable."""
        for section in ("globals", "pointers"):
            for name, spec in getattr(self.amap, section).items():
                with self.subTest(entry=name):
                    self.assertIn("source", spec, "%s has no source" % name)
                    self.assertIn("confidence", spec)
                    self.assertIn(spec["confidence"],
                                  {"dump", "code", "doc", "refuted"})

    def test_enum_lookup_and_ranges(self):
        self.assertEqual(self.amap.enum_name("position", 0), "QB")
        self.assertEqual(self.amap.enum_name("position", 16), "CB")
        # 5..9 is a range entry, not a value entry.
        self.assertEqual(self.amap.enum_name("position", 7), "OL")
        self.assertEqual(self.amap.enum_name("position", 11), "DL")
        self.assertEqual(self.amap.enum_name("position", 14), "LB")

    def test_unknown_enum_value_is_none_not_an_error(self):
        """Most of these enums are partial and saying so is the honest answer."""
        self.assertIsNone(self.amap.enum_name("ai_state", 113))

    def test_player_stride_is_the_value_read_out_of_getplayer(self):
        self.assertEqual(self.amap.stride("player"), 0x14C0)
        self.assertEqual(self.amap.stride("player"), 5312)

    def test_phase_is_marked_refuted(self):
        """The map must not present the play phase as readable."""
        phase = self.amap.derived["phase"]
        self.assertEqual(phase["confidence"], "refuted")
        self.assertFalse(phase["readable"])

    def test_weight_is_a_float_in_pounds(self):
        """Guards the finding against a well-meaning revert to the doc's claim."""
        f = self.amap.field("player", "weight")
        self.assertEqual(f.type, "f32")
        self.assertIn("POUNDS", f.note)

    def test_missing_things_raise_maperror(self):
        with self.assertRaises(W.MapError):
            self.amap.field("player", "nonesuch")
        with self.assertRaises(W.MapError):
            self.amap.stride("gamestate")


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

class TestDecoding(unittest.TestCase):

    def test_scalars(self):
        self.assertEqual(W._decode("u16", 0x1234), 0x1234)
        self.assertEqual(W._decode("s16", 0xFFFF), -1)
        self.assertEqual(W._decode("s8", 0xFF), -1)
        self.assertAlmostEqual(W._decode("f32", 0x42FE0000), 127.0)

    def test_fourcc_is_stored_readable_not_byte_swapped(self):
        raw = int.from_bytes(b"OQLN", "little")
        self.assertEqual(W._decode("fourcc", raw), "OQLN")

    def test_handle_layout(self):
        h = W.Handle(0x00050301)
        self.assertEqual(h.kind, 1)
        self.assertEqual(h.side, 3)
        self.assertEqual(h.index, 5)
        self.assertTrue(h.is_player)

    def test_non_player_handle(self):
        self.assertFalse(W.Handle(0x00000002).is_player)
        self.assertFalse(W.Handle(0).is_player)


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

class TestReaders(unittest.TestCase):

    def test_read_many_falls_back_when_unbatched(self):
        mem = FakeMemory()
        mem.put_u32(0x1000, 0xAABBCCDD)
        mem.put_u16(0x1004, 0x1234)
        reader = NoBatchReader(mem)
        got = W.read_many(reader, [(0x1000, 4), (0x1004, 2)])
        self.assertEqual(got, [0xAABBCCDD, 0x1234])
        self.assertEqual(reader.calls, 2)

    def test_dumpreader_rejects_a_short_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as fh:
            fh.write(b"\0" * 1024)
            name = fh.name
        with self.assertRaises(W.WorldError):
            W.DumpReader(name)

    def test_emureader_exposes_only_reads(self):
        """The adapter is a wall against writes, so it must not forward one."""
        self.assertFalse(hasattr(W.EmuReader, "write"))
        self.assertFalse(hasattr(W.EmuReader, "load_state"))


# ---------------------------------------------------------------------------
# Walking a synthetic world
# ---------------------------------------------------------------------------

class TestSyntheticWorld(unittest.TestCase):

    def test_empty_world_is_not_an_error(self):
        """Null pointers are the normal reading at a menu."""
        w, _ = build_world()
        self.assertFalse(w.in_play)
        self.assertEqual(w.players(), [])
        self.assertIsNone(w.ball())
        self.assertIsNone(w.frames_since_snap())
        self.assertEqual(w.double_teams(), [])

    def test_phase_refuses_rather_than_guessing(self):
        w, _ = build_world()
        with self.assertRaises(W.NotReadable):
            w.phase()

    def test_getplayer_arithmetic(self):
        """base + (side * per_side + index) * stride, from 0x001655B0."""
        roster = [{"position": p} for p in (0, 1, 3, 16, 17, 18)]
        w, _ = build_world(roster, per_side=3)
        stride = w.map.stride("player")
        base = 0x00710000
        self.assertEqual(w.player(0, 0).base, base)
        self.assertEqual(w.player(0, 2).base, base + 2 * stride)
        # side 1 starts one full team in, not one record in.
        self.assertEqual(w.player(1, 0).base, base + 3 * stride)
        self.assertEqual(w.player(1, 2).base, base + 5 * stride)

    def test_players_are_enumerated_with_the_right_handles(self):
        roster = [{"position": p} for p in (0, 1, 16, 17)]
        w, _ = build_world(roster, per_side=2)
        got = [(p.side, p.index) for p in w.players()]
        self.assertEqual(got, [(0, 0), (0, 1), (1, 0), (1, 1)])
        for p in w.players():
            self.assertEqual(p.handle.side, p.side)
            self.assertEqual(p.handle.index, p.index)
            self.assertTrue(p.handle.is_player)

    def test_ratings_are_keyed_by_the_engines_own_attribute_order(self):
        ratings = list(range(21))
        w, _ = build_world([{"position": 0, "ratings": ratings}], per_side=1)
        r = w.players()[0].ratings()
        self.assertEqual(len(r), 21)
        self.assertEqual(r["AGI"], 1)
        self.assertEqual(r["AWR"], 2)
        self.assertEqual(r["SPD"], 13)
        self.assertEqual(r["STR"], 15)
        self.assertEqual(r["TGH"], 19)

    def test_rating_by_name_and_index_agree(self):
        w, _ = build_world([{"position": 0, "ratings": list(range(21))}], per_side=1)
        p = w.players()[0]
        self.assertEqual(p.rating("SPD"), p.rating(13))
        with self.assertRaises(W.MapError):
            p.rating("NOPE")
        with self.assertRaises(W.MapError):
            p.rating(21)

    def test_roster_report_counts_by_side(self):
        roster = [{"position": p} for p in (0, 1, 5, 16, 17, 13)]
        w, _ = build_world(roster, per_side=3)
        rep = w.roster_report()
        self.assertEqual(rep["total"], 6)
        self.assertEqual(rep["sides"]["offence"], {"QB": 1, "HB": 1, "OL": 1})
        self.assertEqual(rep["sides"]["defence"], {"CB": 1, "FS": 1, "LB": 1})

    def test_snapshot_is_one_row_per_player(self):
        roster = [{"position": p, "weight": 300.0} for p in (5, 6)]
        w, _ = build_world(roster, per_side=2)
        snap = w.snapshot()
        self.assertEqual(len(snap["players"]), 2)
        self.assertEqual(snap["players"][0]["weight"], 300.0)
        self.assertIn("torn", snap)

    def test_verify_rejects_a_world_of_zeros(self):
        """The failure this whole method exists for.

        A PINE read of a dead address returns 0, so an address map that has
        drifted produces a complete, plausible, entirely wrong world. Verify
        must refuse it.
        """
        w, _ = build_world()
        with self.assertRaises(W.VerifyFailed) as ctx:
            w.verify()
        self.assertTrue(ctx.exception.failures)


# ---------------------------------------------------------------------------
# The real images
# ---------------------------------------------------------------------------

@unittest.skipUnless(MENU_DUMP.exists(), "extract/ee_mainmenu.bin not present")
class TestMenuDump(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.world = W.open_dump(MENU_DUMP)

    def test_verify_passes(self):
        self.world.verify()

    def test_option_table_is_131_entries_with_the_shipped_duplicate(self):
        rows = self.world.option_rows()
        self.assertEqual(len(rows), 131)
        self.assertEqual(rows[0][1], "OQLN")
        # 7 and 8 really are both OFMO. Not a reader bug.
        self.assertEqual(rows[7][1], "OFMO")
        self.assertEqual(rows[8][1], "OFMO")
        self.assertEqual(rows[62][1], "EPPN")
        # ...so the dict form necessarily loses one of them.
        self.assertEqual(len(self.world.options()), 130)

    def test_gameplay_sliders_all_read_the_midpoint(self):
        rows = self.world.option_rows()
        for i in range(16, 56):
            self.assertEqual(rows[i][2], 50, "option %d" % i)

    def test_attribute_names_come_from_memory(self):
        names = self.world.attribute_names()
        self.assertEqual(len(names), 21)
        self.assertEqual(names[1], "PAGI")
        self.assertEqual(names[13], "PSPD")
        self.assertEqual(names[20], "PKRT")

    def test_penalty_names(self):
        names = self.world.penalty_names()
        self.assertEqual(len(names), 18)
        self.assertEqual(names[0], "CLIPPING")
        self.assertEqual(names[9], "PASS INTERFERENCE")
        self.assertEqual(names[17], "ILLEGAL PROCEDURE")

    def test_ai_state_table_column_order(self):
        """State 47 is lead blocking, and docs give its enter and think."""
        row = self.world.state_row(47)
        self.assertEqual(row[0], 0x001B66A8)   # enter
        self.assertEqual(row[2], 0x001B6870)   # ai_think
        self.assertEqual(row[4], 0x001B0528)   # the shared no-op exit stub

    def test_state_table_is_longer_than_the_documented_93(self):
        """docs/ say 93 states. Rows 93..114 hold real code pointers."""
        self.assertEqual(int(self.world.map.globals["ai_states"]["count"]), 115)
        for state in (94, 100, 114):
            row = self.world.state_row(state)
            self.assertNotEqual(row[0], 0)
            self.assertEqual(row[4], 0x001B0528)

    def test_nothing_is_in_play_at_a_menu(self):
        self.assertFalse(self.world.in_play)
        self.assertEqual(self.world.players(), [])


@unittest.skipUnless(INPLAY_DUMP.exists(), "extract/ee_inplay.bin not present")
class TestInPlayDump(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.world = W.open_dump(INPLAY_DUMP)

    def test_verify_passes(self):
        self.world.verify()

    def test_twenty_two_players_in_a_legal_formation(self):
        """The test that actually discriminates a right stride from a wrong one.

        A wrong stride still yields records; it cannot yield eleven a side in a
        legal offence against a legal 4-3.
        """
        rep = self.world.roster_report()
        self.assertEqual(rep["total"], 22)
        self.assertEqual(rep["sides"]["offence"],
                         {"QB": 1, "HB": 1, "WR": 3, "TE": 1, "OL": 5})
        self.assertEqual(rep["sides"]["defence"],
                         {"DL": 4, "LB": 3, "CB": 2, "FS": 1, "SS": 1})

    def test_array_descriptor(self):
        base, per_side, total = self.world._array()
        self.assertEqual(per_side, 11)
        self.assertEqual(total, 22)
        self.assertNotEqual(base, self.world.deref("player_array"))

    def test_weight_is_real_pounds_not_pounds_minus_160(self):
        """Settles a correction that docs/fact-check-2026-08.md got backwards.

        Offensive linemen weigh 299-322. Under pounds-minus-160 they would be
        459-482, which no human being is.
        """
        by_pos = {}
        for p in self.world.players():
            by_pos.setdefault(p.position_name, []).append(p.field("weight"))
        for w in by_pos["OL"]:
            self.assertTrue(280.0 <= w <= 340.0, "lineman weighs %r" % w)
        for w in by_pos["CB"]:
            self.assertTrue(170.0 <= w <= 210.0, "corner weighs %r" % w)
        self.assertEqual(by_pos["QB"], [226.0])

    def test_height_is_a_float_in_inches(self):
        for p in self.world.players():
            self.assertTrue(66.0 <= p.field("height") <= 82.0)

    def test_bcc_is_the_current_state_id(self):
        """fb-wr-blocking.md called this inference. It holds for all 22."""
        for p in self.world.players():
            chain = p.field("state_chain")
            self.assertNotEqual(chain, 0)
            self.assertEqual(p.field("ai_state"), self.world.reader.read(chain, 1))

    def test_exactly_one_player_is_human_controlled(self):
        ctrl = [p.field("controller") for p in self.world.players()]
        self.assertEqual(sum(1 for c in ctrl if c != 255), 1)
        self.assertEqual(self.world.players()[0].field("controller"), 0)

    def test_geometry_separates_the_two_sides(self):
        offence = [p.xyz[1] for p in self.world.players() if p.side == 0]
        defence = [p.xyz[1] for p in self.world.players() if p.side == 1]
        self.assertLess(max(offence), min(defence))

    def test_offensive_line_is_evenly_spaced_about_the_centre(self):
        xs = sorted(p.xyz[0] for p in self.world.players()
                    if p.position_name == "OL")
        gaps = [b - a for a, b in zip(xs, xs[1:])]
        for g in gaps:
            self.assertTrue(1.4 < g < 2.0, "lineman gap %r" % g)

    def test_ratings_are_the_0_to_255_effective_scale(self):
        for p in self.world.players():
            for name, v in p.ratings().items():
                self.assertTrue(0 <= v <= 255, "%s %s = %r" % (p, name, v))

    def test_frame_counter_is_zero_before_the_snap(self):
        self.assertEqual(self.world.frames_since_snap(), 0)

    def test_ball_carrier_is_null_before_the_snap(self):
        ball = self.world.ball()
        self.assertIsNotNone(ball)
        self.assertIsNone(ball.carrier)
        # ...but the sticky field still resolves to a real player.
        self.assertIsNotNone(ball.last_carrier)

    def test_phase_still_refuses(self):
        """Even with a live game in memory there is no phase word to read."""
        with self.assertRaises(W.NotReadable):
            self.world.phase()

    def test_screen_id_is_not_0x0e_during_gameplay(self):
        """The evidence that killed the [[0x00600C70]+0x98] phase hypothesis."""
        screen = self.world.deref("screen_object")
        self.assertNotEqual(screen, 0)
        self.assertNotEqual(self.world.reader.read(screen, 4), 0x0E)

    def test_pre_snap_states_are_observable(self):
        """What `phase()` cannot give you, the AI state can."""
        states = {p.side: set() for p in self.world.players()}
        for p in self.world.players():
            states[p.side].add(p.field("ai_state"))
        self.assertEqual(states[1], {42})
        self.assertEqual(states[0], {69, 70})


if __name__ == "__main__":
    unittest.main()

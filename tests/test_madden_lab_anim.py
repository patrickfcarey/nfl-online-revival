"""Tests for the current-animation-id accessor -- the +0x304 pointer chase.

Same two kinds as test_madden_lab_world.py, for the same reasons. The
synthetic tests build the slot array from the layout and check the machinery
walks it the way the engine does -- the status-3 scan, the refusal to follow
a junk pointer, the two-batch cost ceiling, the stale-chase verify. The rest
read the committed savestate and the in-play dump and pin the accessor to the
values the live probe of 2026-08-11 measured (docs/anim-lanes/4-synthesis.md),
so the map cannot drift away from the game without a test noticing.

The batching assertions are not style: SEAM REQUEST 8's arithmetic is that
per-field reads make a 22-player frame ~88 round trips and blow the frame
budget. An indirect field is the one construct that could quietly reintroduce
that -- a naive chase is 22 extra trips -- so the "exactly two batches" tests
are the acceptance test for the design's cost claim.
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.madden_lab import world as W

REPO = Path(__file__).resolve().parent.parent
SLOT9_STATE = REPO / "experiments" / "states" / "double_team_slot9.p2s"
INPLAY_DUMP = REPO / "extract" / "ee_inplay.bin"

#: Layout constants under test, straight from addresses.yaml's anim_id entry.
ANIM_PTR_OFF = 0x304
SLOT_STRIDE = 0x64
SLOTS = 4
PLAYING = 3


class FakeMemory:
    """A sparse EE image; unwritten addresses read 0, exactly like PINE."""

    def __init__(self) -> None:
        self.mem = bytearray(W.EE_SIZE)

    def put_u8(self, addr: int, v: int) -> None:
        self.mem[addr:addr + 1] = struct.pack("<B", v)

    def put_u16(self, addr: int, v: int) -> None:
        self.mem[addr:addr + 2] = struct.pack("<H", v)

    def put_u32(self, addr: int, v: int) -> None:
        self.mem[addr:addr + 4] = struct.pack("<I", v)

    def read(self, address: int, size: int = 4) -> int:
        return int.from_bytes(self.mem[address:address + size], "little")

    def read_bytes(self, address: int, count: int) -> bytes:
        return bytes(self.mem[address:address + count])


class BatchCountingReader:
    """Counts round trips: each read_many is one, each bare read is one."""

    def __init__(self, inner: FakeMemory) -> None:
        self.inner = inner
        self.batches = 0
        self.single_reads = 0

    def read(self, address: int, size: int = 4) -> int:
        self.single_reads += 1
        return self.inner.read(address, size)

    def read_bytes(self, address: int, count: int) -> bytes:
        return self.inner.read_bytes(address, count)

    def read_many(self, specs):
        self.batches += 1
        return [self.inner.read(a, s) for a, s in specs]

    def reset(self) -> None:
        self.batches = 0
        self.single_reads = 0


class PointerSwappingReader(BatchCountingReader):
    """Moves the anim object between the two phases of a chase.

    After serving the first batch (phase A, where the pointer is resolved)
    it rewrites the pointer word, so phase B's verify read sees a different
    value than the addresses were built from -- the exact hazard the in-batch
    re-read exists to catch.
    """

    def __init__(self, inner: FakeMemory, ptr_addr: int, new_value: int) -> None:
        super().__init__(inner)
        self._ptr_addr = ptr_addr
        self._new_value = new_value

    def read_many(self, specs):
        out = super().read_many(specs)
        if self.batches == 1:
            self.inner.put_u32(self._ptr_addr, self._new_value)
        return out


DESC, PLAYERS_BASE, PLAY_MGR, ANIM_BASE = (
    0x00700000, 0x00710000, 0x00730000, 0x00740000)


def build_world(players, per_side, frames_since_snap=None, reader_cls=None):
    """A fake world with players and, per player, an anim slot array.

    Each player spec may carry:
      position     -- byte at +0xB04
      anim         -- list of (clip_id, status) pairs, laid into slots 0..n
      anim_ptr     -- overrides the +0x304 word (None lays a real array)
    """
    mem = FakeMemory()
    amap = W.AddressMap.load()
    stride = amap.stride("player")
    mem.put_u32(0x00600E48, DESC)
    mem.put_u32(DESC + 0x00, PLAYERS_BASE)
    mem.put_u16(DESC + 0x08, per_side)
    mem.put_u16(DESC + 0x0A, len(players))
    if frames_since_snap is not None:
        mem.put_u32(0x00601280, PLAY_MGR)
        mem.put_u32(PLAY_MGR + 0x54, frames_since_snap)
    for slot, spec in enumerate(players):
        p = PLAYERS_BASE + slot * stride
        side, index = divmod(slot, per_side)
        mem.put_u8(p + 0x00, 1)
        mem.put_u8(p + 0x01, side)
        mem.put_u8(p + 0x02, index)
        mem.put_u8(p + 0xB04, spec.get("position", 0))
        if "anim_ptr" in spec:
            mem.put_u32(p + ANIM_PTR_OFF, spec["anim_ptr"])
        else:
            arr = ANIM_BASE + slot * 0x1000
            mem.put_u32(p + ANIM_PTR_OFF, arr)
            for k, (clip, status) in enumerate(spec.get("anim", [])):
                mem.put_u16(arr + k * SLOT_STRIDE + 4, clip)
                mem.put_u16(arr + k * SLOT_STRIDE + 6, status)
    reader = reader_cls(mem) if reader_cls else mem
    return W.World(reader, amap), mem, reader


# ---------------------------------------------------------------------------
# The map entry and the Field machinery
# ---------------------------------------------------------------------------

class TestIndirectFieldSpec(unittest.TestCase):

    def setUp(self) -> None:
        self.amap = W.AddressMap.load()
        self.f = self.amap.field("player", "anim_id")

    def test_anim_id_parses_as_indirect_u16(self):
        self.assertTrue(self.f.is_indirect)
        self.assertEqual(self.f.type, "u16")
        self.assertEqual(self.f.width, 2)
        self.assertEqual(self.f.offset, ANIM_PTR_OFF)
        self.assertEqual(self.f.indirect["stride"], SLOT_STRIDE)
        self.assertEqual(self.f.indirect["slots"], SLOTS)
        self.assertEqual(self.f.indirect["status_equals"], PLAYING)

    def test_flat_specs_refuse_an_indirect_field(self):
        """The guard that keeps an unconverted call path from reading the
        pointer bytes and presenting them as a clip id."""
        with self.assertRaises(W.MapError):
            self.f.specs(0x1000)

    def test_indirect_specs_start_with_the_verify_read(self):
        specs = self.f.indirect_specs(0x00500000, 0x00660000)
        self.assertEqual(specs[0], (0x00500000 + ANIM_PTR_OFF, 4))
        self.assertEqual(len(specs), 1 + 2 * SLOTS)
        # Slot 2's id and status land where the layout says.
        self.assertIn((0x00660000 + 2 * SLOT_STRIDE + 4, 2), specs)
        self.assertIn((0x00660000 + 2 * SLOT_STRIDE + 6, 2), specs)

    def test_unfollowable_pointer_has_no_specs_and_decodes_none(self):
        for bad in (0, 0x50, 0x000FFFFF, W.EE_SIZE, 0x7FFFFFFF):
            self.assertEqual(self.f.indirect_specs(0x00500000, bad), [])
            self.assertIsNone(self.f.decode_indirect(bad, []))

    def test_malformed_indirect_specs_raise_maperror(self):
        with self.assertRaises(W.MapError):
            W.Field("bad", {"offset": 0, "type": "u16",
                            "indirect": {"stride": 0x64, "slots": 4}})
        with self.assertRaises(W.MapError):
            W.Field("bad", {"offset": 0, "type": "u16", "count": 2,
                            "indirect": {"stride": 0x64, "slots": 4,
                                         "value_offset": 4, "status_offset": 6,
                                         "status_equals": 3}})
        with self.assertRaises(W.MapError):  # value word overruns the record
            W.Field("bad", {"offset": 0, "type": "u16",
                            "indirect": {"stride": 8, "slots": 4,
                                         "value_offset": 7, "status_offset": 2,
                                         "status_equals": 3}})


# ---------------------------------------------------------------------------
# The scan itself, on synthetic slots
# ---------------------------------------------------------------------------

class TestSlotScan(unittest.TestCase):

    def one_player(self, **spec):
        w, _mem, _r = build_world([dict(spec)], per_side=1)
        return w.players()[0]

    def test_status_3_slot_wins_wherever_it_is(self):
        """The rule is the status, not the slot number."""
        p = self.one_player(anim=[(111, 0), (112, 0), (222, PLAYING), (113, 0)])
        self.assertEqual(p.anim_id(), 222)

    def test_slot_zero_playing_reads_slot_zero(self):
        p = self.one_player(anim=[(91, PLAYING), (91, 0), (91, 0), (77, 0)])
        self.assertEqual(p.anim_id(), 91)

    def test_first_playing_slot_wins_on_a_tie(self):
        p = self.one_player(anim=[(5, 0), (10, PLAYING), (11, PLAYING), (0, 0)])
        self.assertEqual(p.anim_id(), 10)

    def test_no_playing_slot_is_none_not_a_guess(self):
        """Mid-transition every slot can be idle; None is the true reading."""
        p = self.one_player(anim=[(111, 0), (112, 0), (113, 0), (114, 0)])
        self.assertIsNone(p.anim_id())

    def test_null_and_junk_pointers_read_none(self):
        for bad in (0, 0x40, 0x7FFFFFFF):
            p = self.one_player(anim_ptr=bad)
            self.assertIsNone(p.anim_id())

    def test_status_beyond_the_scanned_slots_is_ignored(self):
        """The bound errs short on purpose: data past the real array is other
        object members, and a stray 3 there must not become a clip."""
        w, mem, _r = build_world([{"anim": [(1, 0), (2, 0), (3, 0), (4, 0)]}],
                                 per_side=1)
        arr = ANIM_BASE  # player slot 0's array
        mem.put_u16(arr + SLOTS * SLOT_STRIDE + 4, 999)
        mem.put_u16(arr + SLOTS * SLOT_STRIDE + 6, PLAYING)
        self.assertIsNone(w.players()[0].anim_id())

    def test_snapshot_and_field_agree(self):
        p = self.one_player(anim=[(86, PLAYING)])
        self.assertEqual(p.snapshot(("anim_id",))["anim_id"], 86)
        self.assertEqual(p.field("anim_id"), 86)

    def test_full_snapshot_carries_anim_id_without_raising(self):
        """World.snapshot() pulls every mapped field; the indirect one must
        ride along rather than break the default path."""
        p = self.one_player(anim=[(85, PLAYING)])
        self.assertEqual(p.snapshot()["anim_id"], 85)


# ---------------------------------------------------------------------------
# Cost: the batching property the design promises
# ---------------------------------------------------------------------------

class TestBatchingCost(unittest.TestCase):

    ROSTER = [{"position": 0, "anim": [(91, PLAYING)]},
              {"position": 1, "anim": [(85, PLAYING)]},
              {"position": 16, "anim": [(74, PLAYING)]},
              {"position": 17, "anim": [(85, PLAYING)]}]

    def test_snapshot_without_indirect_fields_is_one_batch(self):
        w, _mem, r = build_world(self.ROSTER, per_side=2,
                                 reader_cls=BatchCountingReader)
        p = w.players()[0]
        r.reset()
        p.snapshot(("position", "pos_x", "pos_y"))
        self.assertEqual((r.batches, r.single_reads), (1, 0))

    def test_snapshot_with_anim_id_is_exactly_two_batches(self):
        w, _mem, r = build_world(self.ROSTER, per_side=2,
                                 reader_cls=BatchCountingReader)
        p = w.players()[0]
        r.reset()
        snap = p.snapshot(("position", "anim_id", "pos_x"))
        self.assertEqual(snap["anim_id"], 91)
        self.assertEqual((r.batches, r.single_reads), (2, 0))

    def test_certified_batch_without_indirect_is_one_batch(self):
        w, _mem, r = build_world(self.ROSTER, per_side=2, frames_since_snap=7,
                                 reader_cls=BatchCountingReader)
        parts = [(p, ("position", "pos_x")) for p in w.players()]
        r.reset()
        tick, after, _decoded = w.certified_batch(parts)
        self.assertEqual((tick, after), (7, 7))
        # one deref of the play-manager pointer, then ONE batch
        self.assertEqual(r.batches, 1)

    def test_certified_batch_with_anim_id_is_exactly_two_batches(self):
        """The design's cost claim, as an acceptance test: a whole-frame
        sample including the chase is 2 round trips, not 1 + one per player."""
        w, _mem, r = build_world(self.ROSTER, per_side=2, frames_since_snap=7,
                                 reader_cls=BatchCountingReader)
        parts = [(p, ("position", "anim_id", "xyz")) for p in w.players()]
        r.reset()
        tick, after, decoded = w.certified_batch(parts)
        self.assertEqual((tick, after), (7, 7))
        self.assertEqual(r.batches, 2)
        self.assertEqual([d["anim_id"] for d in decoded], [91, 85, 74, 85])
        for d in decoded:
            self.assertIn("xyz", d)

    def test_certified_batch_brackets_span_both_phases(self):
        """tick_before is read in phase A and tick_after in phase B, so a
        clock that moves between the phases de-certifies the sample."""

        class TickingReader(BatchCountingReader):
            def read_many(self, specs):
                out = super().read_many(specs)
                # advance the counter after every served batch
                self.inner.put_u32(PLAY_MGR + 0x54,
                                   self.inner.read(PLAY_MGR + 0x54, 4) + 1)
                return out

        w, _mem, r = build_world(self.ROSTER, per_side=2, frames_since_snap=7,
                                 reader_cls=TickingReader)
        parts = [(p, ("anim_id",)) for p in w.players()]
        tick, after, _decoded = w.certified_batch(parts)
        self.assertEqual(tick, 7)
        self.assertEqual(after, 8)   # moved mid-sample: visibly torn


# ---------------------------------------------------------------------------
# The stale-chase verify
# ---------------------------------------------------------------------------

class TestStaleChase(unittest.TestCase):

    def test_pointer_moving_between_phases_reads_none_not_garbage(self):
        stride = W.AddressMap.load().stride("player")
        ptr_addr = PLAYERS_BASE + 0 * stride + ANIM_PTR_OFF
        new_arr = 0x00750000

        def make(reader_inner):
            return PointerSwappingReader(reader_inner, ptr_addr, new_arr)

        w, mem, _r = build_world(
            [{"position": 0, "anim": [(91, PLAYING)]},
             {"position": 1, "anim": [(85, PLAYING)]}],
            per_side=2, frames_since_snap=3, reader_cls=make)
        # The replacement object is fully valid and playing clip 99, so only
        # the verify -- not the plausibility check -- can catch the swap.
        mem.put_u16(new_arr + 4, 99)
        mem.put_u16(new_arr + 6, PLAYING)

        parts = [(p, ("position", "anim_id")) for p in w.players()]
        _tick, _after, decoded = w.certified_batch(parts)
        self.assertIsNone(decoded[0]["anim_id"])      # stale chase: refused
        self.assertEqual(decoded[0]["position"], 0)   # flat fields unharmed
        self.assertEqual(decoded[1]["anim_id"], 85)   # the unswapped player


# ---------------------------------------------------------------------------
# The committed savestate: the end-to-end proof with no rig
# ---------------------------------------------------------------------------

#: The live probe's readings (docs/anim-lanes/4-synthesis.md, 2026-08-11),
#: re-derived offline from the committed state before being pinned here:
#: QB 91 | HB 85 | FB 86 | WR 85/85 | TE 86 | OL 86 x5 | NT 21 | DE 86,
#: linebackers and secondary 85.
EXPECTED_SLOT9 = {
    (0, 0): 91,                                        # QB
    (0, 1): 85,                                        # HB
    (0, 2): 86,                                        # FB
    (0, 3): 85, (0, 4): 85,                            # WRs
    (0, 5): 86,                                        # TE
    (0, 6): 86, (0, 7): 86, (0, 8): 86, (0, 9): 86, (0, 10): 86,  # OL
    (1, 0): 85,                                        # OLB
    (1, 1): 86, (1, 2): 21, (1, 3): 86,                # 3-4 front: DE NT DE
    (1, 4): 85, (1, 5): 85, (1, 6): 85,                # LBs
    (1, 7): 85, (1, 8): 85, (1, 9): 85, (1, 10): 85,   # secondary
}


@unittest.skipUnless(SLOT9_STATE.exists(), "committed savestate missing")
class TestSlot9State(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from tools.statereader import SavestateReader
        cls.world = W.World(SavestateReader(str(SLOT9_STATE)))

    def test_accessor_reads_the_live_probes_values(self):
        got = {(p.side, p.index): p.anim_id() for p in self.world.players()}
        self.assertEqual(got, EXPECTED_SLOT9)

    def test_certified_batch_reads_the_same_values(self):
        """The exact path the runner samples through, offline end to end."""
        parts = [(p, ("anim_id",)) for p in self.world.players()]
        tick, after, decoded = self.world.certified_batch(parts)
        self.assertEqual((tick, after), (0, 0))        # pre-snap, certified
        got = {(p.side, p.index): d["anim_id"]
               for (p, _f), d in zip(parts, decoded)}
        self.assertEqual(got, EXPECTED_SLOT9)

    def test_anim_id_works_where_pair_anim_is_not_authoritative(self):
        """The reason the field exists: pre-snap every engagement is 0, so
        pair_anim is outside its kinds-5/6 window (and three defenders hold
        residual values here) -- while anim_id names a playing clip on all 22."""
        for p in self.world.players():
            snap = p.snapshot(("engagement", "anim_id"))
            self.assertEqual(snap["engagement"], 0)
            self.assertIsNotNone(snap["anim_id"])


@unittest.skipUnless(INPLAY_DUMP.exists(), "extract/ee_inplay.bin not present")
class TestInPlayDump(unittest.TestCase):
    """A second real image, different personnel, same accessor."""

    @classmethod
    def setUpClass(cls):
        cls.world = W.open_dump(INPLAY_DUMP)

    def test_every_player_is_playing_a_clip(self):
        got = {(p.side, p.index): p.anim_id() for p in self.world.players()}
        self.assertEqual(len(got), 22)
        self.assertNotIn(None, got.values())

    def test_position_correlated_ids(self):
        got = {(p.side, p.index): p.anim_id() for p in self.world.players()}
        self.assertEqual(got[(0, 0)], 91)                    # QB again
        for lb in ((1, 4), (1, 5), (1, 6)):
            self.assertEqual(got[lb], 28)                    # 4-3 linebackers
        for cb in ((1, 7), (1, 10)):
            self.assertEqual(got[cb], 74)                    # corners


if __name__ == "__main__":
    unittest.main()

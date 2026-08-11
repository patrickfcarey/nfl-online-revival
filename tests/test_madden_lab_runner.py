"""Layers 4 and 5, exercised with no emulator, no PINE socket and no PS2 game.

Everything the runner touches below the seam is a fake defined in this file.
That is not a convenience -- it is the acceptance criterion for the design. If
these tests needed a rig, nobody could change the harness without one, and the
harness would rot at exactly the rate the rig is unavailable.

The fakes are also where the seams get pinned down. `FakeEmu` implements the
fire-and-forget `load_state` layer 1 reported, including the window in which
reads still return the *previous* world, so the load-confirmation logic is
tested against the failure it exists for rather than against an idealised
emulator that resets instantly.
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

from tools.madden_lab import EXPECTED_CRC  # noqa: E402
from tools.madden_lab import compare as cmp  # noqa: E402
from tools.madden_lab import results as res  # noqa: E402
from tools.madden_lab import runner as runner_mod  # noqa: E402
from tools.madden_lab.runner import (DeterminismReport, IterationResult,  # noqa: E402
                                     OperatorQueue, PreflightError,
                                     Runner, ScriptPlayer, StallError,
                                     WorldFrameClock, make_provenance)
from tools.madden_lab.trial import (EntitySelector, Frame, InputEvent,  # noqa: E402
                                    LoadConfirm, Metric, OperatorAsk,
                                    SampleSpec, Samples, StopCondition, Trial,
                                    Write)


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakePlayer:
    def __init__(self, index, side, position, block_mode=0, engagement=0,
                 ai_state=0, engagement_link=0):
        self.index = index
        self.side = side
        self.position = position
        self.block_mode = block_mode
        self.engagement = engagement
        self.ai_state = ai_state
        self.engagement_link = engagement_link


class FakeWorld:
    """A scripted world. `advance()` is the only thing that changes it.

    The script is a list of per-frame mutations so a test can say "player 5
    enters block mode 3 at frame 10" without the fake growing a game engine.
    """

    def __init__(self, players=None, script=None, counter=0):
        self._players = ([FakePlayer(0, 0, 1), FakePlayer(1, 0, 2),
                          FakePlayer(2, 1, 20)] if players is None else list(players))
        self._script = script or {}
        self._counter = counter
        self._snap = 0
        # Frames since the savestate loaded. The snap gate hangs off this, not
        # off the global counter, so an iteration replays identically however
        # many iterations preceded it -- otherwise the fake would be
        # non-deterministic and the determinism tests would be testing the fake.
        self._since_load = 0
        self.begin_frame_calls = 0

    def players(self):
        return list(self._players)

    def ball(self):
        return {"index": 0, "carrier": 1}

    def phase(self):
        return 1 if self._snap else 0

    def frames_since_snap(self):
        return self._snap

    def frame_counter(self):
        return self._counter

    def advance(self, frames=1):
        for _ in range(frames):
            self._counter += 1
            self._since_load += 1
            if self._snap or self._since_load > 4:
                self._snap += 1
            for index, field, value in self._script.get(self._snap, ()):
                setattr(self._players[index], field, value)

    def reset(self):
        self._snap = 0
        self._since_load = 0
        for player in self._players:
            player.block_mode = 0
            player.engagement = 0
            player.ai_state = 0
            player.engagement_link = 0


class BatchedPlayer(FakePlayer):
    """Layer 3's real batching seam: one round trip for every named field."""

    def __init__(self, *args, **kwargs):
        FakePlayer.__init__(self, *args, **kwargs)
        self.snapshot_calls = 0

    def snapshot(self, fields=None):
        self.snapshot_calls += 1
        names = list(fields) if fields else ["block_mode", "engagement"]
        return {name: getattr(self, name, None) for name in names}


class FakeEmu:
    """Fire-and-forget load, exactly as layer 1 described it.

    `load_lag` frames of staleness after `load_state` returns; `load_fails`
    makes the load never happen while still returning OK on the socket, which
    is the case that silently ruins a run.
    """

    def __init__(self, world, crc=EXPECTED_CRC, writable=False, load_lag=0,
                 load_fails=False, has_wait_until=True):
        self.world = world
        self.crc = crc
        self.writable = writable
        self.load_lag = load_lag
        self.load_fails = load_fails
        self.writes = []
        self.loads = []
        self.memory = {0x00601280: 0x00700000, 0x00700054: 0}
        self._pending = 0
        if not has_wait_until:
            del self.__class__.wait_until  # pragma: no cover

    def game_crc(self):
        return self.crc

    def read(self, addr, size=4):
        self._settle()
        return self.memory.get(addr, 0)

    def write(self, addr, value, size=4):
        if not self.writable:
            raise PermissionError("emulator is read-only")
        self.writes.append((addr, value, size))
        self.memory[addr] = value

    def load_state(self, state):
        self.loads.append(state)
        if self.load_fails:
            return  # OK on the socket, nothing happens in the emulator
        self._pending = self.load_lag
        if self.load_lag == 0:
            self._do_load()

    def _settle(self):
        if self._pending:
            self._pending -= 1
            if self._pending == 0:
                self._do_load()

    def _do_load(self):
        self.world.reset()
        self.memory[0x00700054] = 0

    def wait_until(self, predicate, timeout=10.0, interval=0.0):
        """Layer 1's signature exactly: the predicate takes the Emu, and a
        timeout raises rather than returning False. Getting this wrong in the
        fake is how a suite passes against an emulator that does not exist.

        The world advances one frame per poll, because that is what makes the
        fake honest: a real emulator keeps running while the harness waits,
        and the stale-world confirmation bug was only visible at all because
        the outgoing play kept advancing under the poll loop. A frozen fake
        would certify a confirmation strategy that reality rejects.
        """
        for _ in range(200):
            if predicate(self):
                return 0.0
            self.world.advance(1)
            self._settle()  # a pending load lands because time passes, not
            self.memory[0x00700054] = self.world.frames_since_snap()
        raise TimeoutError("condition still false")  # only because reads happen


class FakePad:
    def __init__(self):
        self.log = []

    def hold(self, button):
        self.log.append(("hold", button))

    def release(self, button):
        self.log.append(("release", button))


class DrivenClock:
    """Advances a FakeWorld, so the world and the clock cannot disagree."""

    def __init__(self, world, step=1):
        self.world = world
        self.step = step
        self.dropped = 0
        self.degraded = False

    def now(self):
        return self.world.frame_counter()

    def tick(self):
        self.world.advance(self.step)
        if self.step > 1:
            self.dropped += self.step - 1
        return self.step


def simple_trial(**overrides):
    kwargs = dict(
        name="t", state="s.p2s", state_slot=3, question="q?",
        # require_reset=False is a deliberate opt-out, not a default: this
        # fake's pointer predicate is true in every world, and its short plays
        # park the snap counter at 0, so a handover is genuinely invisible --
        # the exact situation the runner's docstring tells a real spec to
        # solve with a window predicate. The edge contract has its own tests
        # (StaleWorld below); the forty tests using this helper are about
        # everything else.
        load_confirm=LoadConfirm(addr=0x00601280, expected=0x00700000,
                                 description="player array pointer is populated",
                                 require_reset=False),
        sample=SampleSpec(entities=(
            EntitySelector("player", ("block_mode", "engagement")),
            EntitySelector("game", ("frames_since_snap",)))),
        stop=StopCondition(max_frames=12),
        metrics=(Metric("mode3", lambda s: float(sum(
            s.count_frames_where(e, "block_mode", lambda v: v == 3)
            for e in s.entities("player:")))),),
    )
    kwargs.update(overrides)
    return Trial(**kwargs)


def silent(*_args, **_kwargs):
    pass


# --------------------------------------------------------------------------
# Spec validation
# --------------------------------------------------------------------------


class SpecRules(unittest.TestCase):
    def test_a_write_must_justify_itself(self):
        with self.assertRaises(ValueError):
            Write(0x100, 1, "")
        self.assertIn("C3", Write(0x1F2F00, 4, "C3: admit mode 3").describe())

    def test_an_operator_ask_must_justify_spending_a_human(self):
        with self.assertRaises(ValueError):
            OperatorAsk(id="a", question="q", watch_for="w", why_not_memory="")

    def test_unclear_is_always_an_allowed_answer(self):
        ask = OperatorAsk(id="a", question="q", watch_for="w",
                          why_not_memory="not in RAM", choices=("yes", "no"))
        self.assertIn("unclear", ask.choices)

    def test_load_confirm_refuses_to_anchor_on_zero(self):
        # Unmapped pages read as 0, so "expect 0" confirms nothing.
        with self.assertRaises(ValueError):
            LoadConfirm(addr=0x600000, expected=0)
        with self.assertRaises(ValueError):
            LoadConfirm()

    def test_digest_tracks_executable_content_not_prose(self):
        a = simple_trial()
        b = simple_trial(question="a completely different question")
        self.assertEqual(a.digest(), b.digest())
        c = simple_trial(script=(InputEvent(4, "cross"),))
        self.assertNotEqual(a.digest(), c.digest())

    def test_sampling_is_a_cursor_so_a_missed_poll_does_not_skip_a_period(self):
        spec = SampleSpec(entities=(EntitySelector("player", ("x",)),), every=3)
        self.assertTrue(spec.due(0, None))
        self.assertFalse(spec.due(2, 0))
        self.assertTrue(spec.due(3, 0))
        # The poll loop fell behind and frame 6 never appeared; 7 still samples.
        self.assertTrue(spec.due(7, 3))


# --------------------------------------------------------------------------
# Torn reads
# --------------------------------------------------------------------------


class TornReads(unittest.TestCase):
    """PINE reads are not synchronised with emulation, so a single frame can
    report a value the game never held. The helpers must not turn that into a
    measurement."""

    def _samples(self, values):
        from tools.madden_lab.trial import Frame
        return Samples([Frame(i, {("player:0", "block_mode"): v})
                        for i, v in enumerate(values)])

    def test_a_one_frame_spike_is_not_an_event(self):
        samples = self._samples([0, 0, 3, 0, 0, 0])
        self.assertIsNone(samples.first_frame_where(
            "player:0", "block_mode", lambda v: v == 3))
        self.assertFalse(samples.holds_for("player:0", "block_mode", lambda v: v == 3))

    def test_a_persistent_value_is_an_event_and_reports_its_start(self):
        samples = self._samples([0, 0, 3, 3, 3, 0])
        self.assertEqual(2, samples.first_frame_where(
            "player:0", "block_mode", lambda v: v == 3))

    def test_counts_are_aggregate_and_tolerate_the_spike(self):
        self.assertEqual(1, self._samples([0, 3, 0]).count_frames_where(
            "player:0", "block_mode", lambda v: v == 3))


# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------


class Preflight(unittest.TestCase):
    def test_the_wrong_build_is_refused(self):
        world = FakeWorld()
        runner = Runner(FakeEmu(world, crc=0xDEADBEEF), world, printer=silent)
        with self.assertRaises(PreflightError) as caught:
            runner.preflight()
        self.assertIn("0x14F8B841", str(caught.exception))

    def test_an_empty_player_array_is_refused_with_the_menu_explanation(self):
        world = FakeWorld(players=[])
        runner = Runner(FakeEmu(world), world, printer=silent)
        with self.assertRaises(PreflightError) as caught:
            runner.preflight()
        self.assertIn("0x00600E48", str(caught.exception))

    def test_declared_writes_need_an_explicit_opt_in(self):
        world = FakeWorld()
        trial = simple_trial(setup=(Write(0x1F2F00, 4, "C3"),))
        runner = Runner(FakeEmu(world), world, printer=silent)
        with self.assertRaises(PreflightError) as caught:
            runner.preflight(trial)
        self.assertIn("--write", str(caught.exception))

    def test_opting_in_against_a_read_only_emulator_is_refused_up_front(self):
        world = FakeWorld()
        trial = simple_trial(setup=(Write(0x1F2F00, 4, "C3"),))
        runner = Runner(FakeEmu(world, writable=False), world,
                        allow_writes=True, printer=silent)
        with self.assertRaises(PreflightError) as caught:
            runner.preflight(trial)
        self.assertIn("read-only", str(caught.exception))

    def test_a_missing_load_confirm_is_a_loud_note(self):
        world = FakeWorld()
        runner = Runner(FakeEmu(world), world, printer=silent)
        notes = runner.preflight(simple_trial(load_confirm=None))
        self.assertTrue(any("LoadConfirm" in note for note in notes))


# --------------------------------------------------------------------------
# The fire-and-forget load
# --------------------------------------------------------------------------


class ConfirmedReset(unittest.TestCase):
    CONFIRM = LoadConfirm(addr=0x00601280, expected=0x00700000,
                          description="player array pointer is populated")

    def test_a_silently_failed_load_is_caught_not_sampled(self):
        world = FakeWorld()
        emu = FakeEmu(world, load_fails=True)
        emu.memory[0x00601280] = 0  # the load never repopulated it
        runner = Runner(emu, world, clock=DrivenClock(world), printer=silent)
        trial = simple_trial(load_confirm=self.CONFIRM)
        result = runner.run_iteration(trial, 0)
        self.assertEqual("load_unconfirmed", result.status)
        self.assertIn("screen only", result.reason)
        self.assertEqual(0, result.frames)

    def test_a_slow_load_is_waited_for_not_slept_through(self):
        world = FakeWorld()
        emu = FakeEmu(world, load_lag=5)
        runner = Runner(emu, world, clock=DrivenClock(world), printer=silent)
        result = runner.run_iteration(simple_trial(load_confirm=self.CONFIRM), 0)
        self.assertEqual("ok", result.status)
        # The label records that this predicate was already true before the
        # load was issued -- accepted on iteration 0, but marked, because a
        # vacuous confirmation carries less proof than a false -> true one and
        # the result file should say which kind this run got.
        self.assertEqual("declared-vacuous-first", result.confirmed)

    def test_without_a_load_confirm_the_first_iteration_is_marked_unverified(self):
        world = FakeWorld()
        runner = Runner(FakeEmu(world), world, clock=DrivenClock(world),
                        printer=silent)
        result = runner.run_iteration(simple_trial(load_confirm=None), 0)
        self.assertTrue(result.confirmed.startswith("unverified"))

    def test_three_bad_loads_in_a_row_abort_the_run(self):
        world = FakeWorld()
        emu = FakeEmu(world, load_fails=True)
        emu.memory[0x00601280] = 0
        printed = []
        runner = Runner(emu, world, clock=DrivenClock(world), printer=printed.append)
        sink = res.MemorySink(make_provenance(simple_trial(), arm="a"))
        summary = runner.run(simple_trial(load_confirm=self.CONFIRM), 50, sink,
                             ask_mode="none", progress_every=0)
        self.assertEqual(3, len(summary.iterations))
        self.assertTrue(any("ABORTED" in line for line in printed))

    def test_a_live_trial_never_loads_at_all(self):
        world = FakeWorld()
        emu = FakeEmu(world)
        runner = Runner(emu, world, clock=DrivenClock(world), printer=silent)
        runner.run_iteration(simple_trial(state="<live>"), 0)
        self.assertEqual([], emu.loads)


# --------------------------------------------------------------------------
# Frame numbering and input
# --------------------------------------------------------------------------


class FrameNumbering(unittest.TestCase):
    def test_rows_carry_the_game_frame_not_the_loop_index(self):
        world = FakeWorld()
        # The poll loop only sees every third frame.
        runner = Runner(FakeEmu(world), world, clock=DrivenClock(world, step=3),
                        printer=silent)
        sink = res.MemorySink()
        runner.run_iteration(simple_trial(stop=StopCondition(max_frames=13)),
                             0, sink=sink)
        frames = sorted({row["frame"] for row in sink.of_kind("sample")})
        self.assertEqual([0, 3, 6, 9, 12], frames)
        # The loop ordinal is kept alongside, so the gap is visible.
        self.assertEqual([0, 1, 2, 3, 4],
                         sorted({row["sample"] for row in sink.of_kind("sample")}))

    def test_dropped_frames_are_reported_not_hidden(self):
        world = FakeWorld()
        runner = Runner(FakeEmu(world), world, clock=DrivenClock(world, step=3),
                        printer=silent)
        sink = res.MemorySink()
        result = runner.run_iteration(simple_trial(), 0, sink=sink)
        self.assertGreater(result.dropped, 0)
        self.assertGreater(result.span, result.frames)


class ProductionClock(unittest.TestCase):
    """`WorldFrameClock` is what runs on the rig, so it gets tested here rather
    than only being stood in for by `DrivenClock`."""

    class Counter:
        def __init__(self, values, has_counter=True):
            self.values = list(values)
            self.calls = 0
            if not has_counter:
                self.frame_counter = None

        def _next(self):
            value = self.values[min(self.calls, len(self.values) - 1)]
            self.calls += 1
            return value

        def frame_counter(self):  # noqa: F811 - replaced when has_counter=False
            return self._next()

        def frames_since_snap(self):
            return self._next()

        def players(self):
            return []

    def test_a_normal_tick_is_one_frame(self):
        world = self.Counter([10, 10, 11])
        clock = WorldFrameClock(world, sleep=lambda _s: None)
        self.assertEqual(1, clock.tick())
        self.assertEqual(0, clock.dropped)

    def test_frames_the_poll_loop_missed_are_counted(self):
        world = self.Counter([10, 14])
        clock = WorldFrameClock(world, sleep=lambda _s: None)
        self.assertEqual(4, clock.tick())
        self.assertEqual(3, clock.dropped)

    def test_a_frozen_counter_raises_rather_than_hanging_forever(self):
        world = self.Counter([7])
        times = iter([0.0, 0.5, 1.0, 99.0, 99.0])
        clock = WorldFrameClock(world, timeout_s=1.0, sleep=lambda _s: None,
                                monotonic=lambda: next(times))
        with self.assertRaises(StallError):
            clock.tick()

    def test_a_rewind_is_reported_not_masked(self):
        """This test used to assert the opposite, and the opposite was a bug.

        The old contract smoothed any backward movement into `+1` on the
        theory that a resetting counter is "the next frame". That smoothing
        is exactly what hid a savestate load landing mid-iteration: the clock
        jumped 179 -> 74, the mask turned it into 180, and every frame number
        after it described a world that no longer existed. A rewind is the
        single most important event this clock can witness -- it means the
        world was replaced -- so it is returned as itself and kept as
        evidence.
        """
        world = self.Counter([300, 0])
        clock = WorldFrameClock(world, sleep=lambda _s: None)
        self.assertEqual(-300, clock.tick())
        self.assertEqual(0, clock.now())
        self.assertEqual([(300, 0)], clock.rewinds)

    def test_no_frame_counter_degrades_loudly(self):
        world = self.Counter([0], has_counter=False)
        clock = WorldFrameClock(world, sleep=lambda _s: None)
        self.assertTrue(clock.degraded)


class Input(unittest.TestCase):
    def test_a_skipped_frame_still_fires_the_snap_and_reports_the_lateness(self):
        pad = FakePad()
        player = ScriptPlayer([InputEvent(4, "cross", 3)], pad)
        player.apply(0)
        self.assertEqual([], pad.log)
        player.apply(7)  # frames 4, 5 and 6 were never observed
        self.assertEqual([("hold", "cross")], pad.log)
        self.assertEqual(3, player.max_lateness)

    def test_overlapping_presses_coalesce_into_one_hold(self):
        pad = FakePad()
        player = ScriptPlayer([InputEvent(0, "cross", 2), InputEvent(1, "cross", 2)],
                              pad)
        player.apply(0)
        player.apply(1)
        player.apply(2)
        player.apply(3)
        self.assertEqual([("hold", "cross"), ("release", "cross")], pad.log)

    def test_unfired_events_are_counted(self):
        pad = FakePad()
        player = ScriptPlayer([InputEvent(500, "square")], pad)
        player.apply(10)
        self.assertEqual(1, player.unfired)

    def test_buttons_are_released_when_the_iteration_ends(self):
        world = FakeWorld()
        pad = FakePad()
        runner = Runner(FakeEmu(world), world, pad=pad, clock=DrivenClock(world),
                        printer=silent)
        runner.run_iteration(simple_trial(script=(InputEvent(2, "cross", 999),)), 0)
        self.assertIn(("release", "cross"), pad.log)


# --------------------------------------------------------------------------
# Sampling, stopping and metrics
# --------------------------------------------------------------------------


class Sampling(unittest.TestCase):
    def test_batching_is_used_when_layer_three_offers_it(self):
        # Without this the runner issues one PINE round trip per field per
        # player -- ~88 a frame, which exceeds a frame's budget and smears the
        # "snapshot" across several frames of game time.
        players = [BatchedPlayer(0, 0, 1), BatchedPlayer(1, 0, 2)]
        world = FakeWorld(players=players)
        runner = Runner(FakeEmu(world), world, clock=DrivenClock(world),
                        printer=silent)
        result = runner.run_iteration(simple_trial(), 0)
        self.assertEqual(result.frames, players[0].snapshot_calls)

    def test_an_unbatched_layer_three_still_works_and_is_warned_about(self):
        world = FakeWorld()
        runner = Runner(FakeEmu(world), world, clock=DrivenClock(world),
                        printer=silent)
        self.assertEqual("ok", runner.run_iteration(simple_trial(), 0).status)
        self.assertTrue(any("tear" in note for note in runner.preflight()))

    def test_a_stop_predicate_ends_the_play_before_the_ceiling(self):
        world = FakeWorld()
        trial = simple_trial(
            stop=StopCondition(max_frames=500,
                               until=lambda f: (f.get("game", "frames_since_snap") or 0) >= 6,
                               until_name="whistle"))
        runner = Runner(FakeEmu(world), world, clock=DrivenClock(world),
                        printer=silent)
        result = runner.run_iteration(trial, 0)
        self.assertEqual("whistle", result.reason)
        self.assertLess(result.span, 500)

    def test_a_stateful_stop_condition_is_reset_between_iterations(self):
        # The Trial is one object reused for every iteration, so a predicate
        # with memory would carry iteration 0's history into iteration 1 and
        # stop it on its first frame.
        class AfterThree:
            def __init__(self):
                self.seen = 0

            def reset(self):
                self.seen = 0

            def __call__(self, _frame):
                self.seen += 1
                return self.seen > 3

        world = FakeWorld()
        trial = simple_trial(stop=StopCondition(max_frames=50, until=AfterThree(),
                                                until_name="after3"))
        runner = Runner(FakeEmu(world), world, clock=DrivenClock(world),
                        printer=silent)
        first = runner.run_iteration(trial, 0)
        second = runner.run_iteration(trial, 1)
        self.assertEqual(first.frames, second.frames)
        self.assertEqual("after3", second.reason)

    def test_players_on_opposite_sides_do_not_collide_into_one_series(self):
        # Layer 3 numbers players per side, so both sides have an index 5.
        world = FakeWorld(players=[FakePlayer(5, 0, 1, block_mode=3),
                                   FakePlayer(5, 1, 20, block_mode=1)])
        runner = Runner(FakeEmu(world), world, clock=DrivenClock(world),
                        printer=silent)
        sink = res.MemorySink()
        runner.run_iteration(simple_trial(), 0, sink=sink)
        entities = {row["entity"] for row in sink.of_kind("sample")}
        self.assertEqual({"player:0:5", "player:1:5", "game"}, entities)

    def test_max_frames_is_a_ceiling_a_bad_predicate_cannot_escape(self):
        world = FakeWorld()
        trial = simple_trial(stop=StopCondition(max_frames=9,
                                                until=lambda f: False))
        runner = Runner(FakeEmu(world), world, clock=DrivenClock(world),
                        printer=silent)
        self.assertEqual("max_frames", runner.run_iteration(trial, 0).reason)

    def test_a_stalled_frame_counter_ends_the_iteration_rather_than_hanging(self):
        world = FakeWorld()

        class DeadClock:
            dropped = 0
            degraded = False

            def now(self):
                return 0

            def tick(self):
                raise StallError("frame counter stuck at 0 for 5.0s")

        runner = Runner(FakeEmu(world), world, clock=DeadClock(), printer=silent)
        result = runner.run_iteration(simple_trial(), 0)
        self.assertEqual("stalled", result.status)

    def test_a_metric_that_raises_costs_one_measurement_not_the_run(self):
        world = FakeWorld()

        def explode(_samples):
            raise ZeroDivisionError("no players")

        trial = simple_trial(metrics=(Metric("boom", explode),
                                      Metric("fine", lambda s: 1.0)))
        runner = Runner(FakeEmu(world), world, clock=DrivenClock(world),
                        printer=silent)
        sink = res.MemorySink()
        result = runner.run_iteration(trial, 0, sink=sink)
        self.assertEqual("ok", result.status)
        self.assertIsNone(result.metrics["boom"])
        self.assertEqual(1.0, result.metrics["fine"])

    def test_a_missing_field_is_a_missing_reading_not_a_crash(self):
        world = FakeWorld()
        trial = simple_trial(sample=SampleSpec(entities=(
            EntitySelector("player", ("block_mode", "no_such_field")),)))
        runner = Runner(FakeEmu(world), world, clock=DrivenClock(world),
                        printer=silent)
        sink = res.MemorySink()
        runner.run_iteration(trial, 0, sink=sink)
        missing = [row for row in sink.of_kind("sample")
                   if row["field"] == "no_such_field"]
        self.assertTrue(missing)
        self.assertTrue(all(row["value"] is None for row in missing))


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


class Determinism(unittest.TestCase):
    def test_a_reproducible_trial_is_proven_reproducible(self):
        world = FakeWorld(script={3: [(0, "block_mode", 3)]})
        runner = Runner(FakeEmu(world), world, clock=DrivenClock(world),
                        printer=silent)
        report = runner.verify_determinism(simple_trial(), repeats=3)
        self.assertTrue(report.identical)
        self.assertIn("n_effective is 1", report.describe())

    def test_a_divergent_trial_reports_where_it_first_diverged(self):
        world = FakeWorld()
        state = {"n": 0}
        original = world.advance

        def wobble(frames=1):
            original(frames)
            # One player picks up a different mode on the second repeat only.
            if state["n"] == 1 and world.frames_since_snap() == 4:
                world.players()[1].block_mode = 7

        world.advance = wobble
        emu = FakeEmu(world)
        original_load = emu.load_state

        def counting_load(name):
            original_load(name)
            state["n"] += 1

        emu.load_state = counting_load
        # state["n"] is incremented after the load, so repeat index 1 is the
        # second one -- exactly the one the wobble targets.
        runner = Runner(emu, world, clock=DrivenClock(world), printer=silent)
        report = runner.verify_determinism(simple_trial(), repeats=3)
        self.assertFalse(report.identical)
        self.assertIsNotNone(report.first_divergence)
        self.assertIn("block_mode", report.first_divergence.describe())
        self.assertIn("distribution", report.describe())

    def test_every_run_records_a_digest_so_the_question_is_always_answerable(self):
        world = FakeWorld()
        runner = Runner(FakeEmu(world), world, clock=DrivenClock(world),
                        printer=silent)
        sink = res.MemorySink(make_provenance(simple_trial(), arm="baseline"))
        summary = runner.run(simple_trial(), 3, sink, ask_mode="none",
                             progress_every=0)
        digests = [row["digest"] for row in sink.of_kind("iteration")]
        self.assertEqual(3, len(digests))
        self.assertTrue(summary.deterministic)


# --------------------------------------------------------------------------
# The operator
# --------------------------------------------------------------------------


ASK = OperatorAsk(id="whiff", question="Did he miss a man he should have hit?",
                  watch_for="the lead blocker from snap to tackle",
                  why_not_memory="no authored block target exists in the game",
                  every=2, limit=3)


class Operator(unittest.TestCase):
    def test_questions_are_batched_not_asked_every_iteration(self):
        printed = []
        queue = OperatorQueue(mode="batch", printer=printed.append, run_id="r")
        trial = simple_trial(asks=(ASK,))
        for index in range(6):
            queue.consider(trial, index)
        self.assertEqual([], printed)  # nothing interrupted the run
        self.assertEqual(3, queue.flush())  # limit=3 respected
        self.assertTrue(printed)

    def test_the_limit_caps_how_much_human_time_a_run_can_spend(self):
        queue = OperatorQueue(mode="none", printer=silent)
        trial = simple_trial(asks=(ASK,))
        for index in range(100):
            queue.consider(trial, index)
        self.assertEqual(3, len(queue.queued))

    def test_the_prompt_says_what_to_watch_which_iteration_and_how_to_answer(self):
        printed = []
        queue = OperatorQueue(mode="live", printer=printed.append, run_id="r1",
                              result_path="out.jsonl", blind_label="B")
        queue.consider(simple_trial(asks=(ASK,)), 0)
        text = "\n".join(printed)
        self.assertIn("OPERATOR:", text)
        self.assertIn("iteration 0", text)
        self.assertIn("WATCH:", text)
        self.assertIn("--ask whiff --iteration 0", text)
        self.assertIn("out.jsonl", text)

    def test_the_operator_is_blinded_to_which_build_they_are_watching(self):
        printed = []
        queue = OperatorQueue(mode="live", printer=printed.append,
                              blind_label="A", run_id="r")
        queue.consider(simple_trial(asks=(ASK,)), 0)
        text = "\n".join(printed)
        self.assertIn("arm A", text)
        self.assertNotIn("patched", text)
        self.assertNotIn("baseline", text)

    def test_an_answer_lands_in_the_same_stream_with_the_runs_provenance(self):
        world = FakeWorld()
        runner = Runner(FakeEmu(world), world, clock=DrivenClock(world),
                        printer=silent)
        trial = simple_trial(asks=(ASK,))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "run.jsonl")
            provenance = make_provenance(trial, arm="patched")
            with res.JsonlSink(path, provenance) as sink:
                runner.run(trial, 3, sink, arm="patched", ask_mode="none",
                           progress_every=0)
            self.assertEqual(2, len(res.pending_asks(path)))
            answer = res.append_answer(path, "whiff", 0, "yes", operator="pc")
            self.assertEqual("patched", answer["arm"])
            self.assertEqual(provenance.git_rev, answer["git_rev"])
            self.assertEqual(1, len(res.pending_asks(path)))
            kinds = [row["kind"] for row in res.read_rows(path)]
            self.assertIn("answer", kinds)
            self.assertIn("sample", kinds)

    def test_an_answer_outside_the_offered_choices_is_refused(self):
        world = FakeWorld()
        runner = Runner(FakeEmu(world), world, clock=DrivenClock(world),
                        printer=silent)
        trial = simple_trial(asks=(ASK,))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "run.jsonl")
            with res.JsonlSink(path, make_provenance(trial, arm="a")) as sink:
                runner.run(trial, 1, sink, ask_mode="none", progress_every=0)
            with self.assertRaises(ValueError):
                res.append_answer(path, "whiff", 0, "probably")


# --------------------------------------------------------------------------
# The result stream
# --------------------------------------------------------------------------


class ResultStream(unittest.TestCase):
    def test_every_row_carries_its_own_provenance(self):
        world = FakeWorld()
        runner = Runner(FakeEmu(world), world, clock=DrivenClock(world),
                        printer=silent)
        trial = simple_trial()
        sink = res.MemorySink(make_provenance(trial, arm="baseline"))
        runner.run(trial, 2, sink, ask_mode="none", progress_every=0)
        self.assertTrue(sink.rows)
        for row in sink.rows:
            for key in ("run_id", "spec", "spec_digest", "state", "git_rev", "arm"):
                self.assertIn(key, row, "row %r lost %s" % (row["kind"], key))

    def test_a_truncated_final_line_is_tolerated_but_corruption_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.jsonl")
            with io.open(path, "w") as handle:
                handle.write('{"kind":"run","iteration":null}\n')
                handle.write('{"kind":"sample","iter')  # killed mid-write
            self.assertEqual(1, len(list(res.read_rows(path))))

            bad = os.path.join(tmp, "bad.jsonl")
            with io.open(bad, "w") as handle:
                handle.write("not json at all\n")
                handle.write('{"kind":"run"}\n')
            with self.assertRaises(ValueError):
                list(res.read_rows(bad))

    def test_gzip_is_transparent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.jsonl.gz")
            provenance = res.Provenance("r", "s", "d", "st", "rev", "a")
            with res.JsonlSink(path, provenance) as sink:
                sink.write("sample", iteration=0, frame=0, entity="player:0",
                           field="x", value=1)
            rows = list(res.read_rows(path))
            self.assertEqual(1, len(rows))
            self.assertEqual("player:0", rows[0]["entity"])


# --------------------------------------------------------------------------
# compare.py
# --------------------------------------------------------------------------


def write_metric_file(path, values, arm, digest_each=True, spec="d1"):
    provenance = res.Provenance("run-" + arm, "t", spec, "s.p2s", "rev", arm)
    with res.JsonlSink(path, provenance) as sink:
        sink.write("run", trial="t")
        for index, value in enumerate(values):
            sink.write("metric", iteration=index, metric="m", value=value)
            sink.write("iteration", iteration=index, status="ok",
                       digest=("d%d" % index) if digest_each else "same")
    return path


class Statistics(unittest.TestCase):
    def test_hedges_g_is_zero_not_infinite_for_two_constant_arms(self):
        self.assertEqual(0.0, cmp.hedges_g([1, 1, 1], [2, 2, 2]))

    def test_cliffs_delta_is_signed_and_bounded(self):
        self.assertEqual(1.0, cmp.cliffs_delta([1, 2, 3], [4, 5, 6]))
        self.assertEqual(-1.0, cmp.cliffs_delta([4, 5, 6], [1, 2, 3]))
        self.assertAlmostEqual(0.0, cmp.cliffs_delta([1, 2, 3], [1, 2, 3]))

    def test_a_sampled_p_value_is_never_exactly_zero(self):
        a = [0.0] * 40
        b = [100.0] * 40
        p, exact = cmp.permutation_p(a, b, seed=1)
        self.assertFalse(exact)
        self.assertGreater(p, 0.0)

    def test_small_samples_get_an_exact_p(self):
        p, exact = cmp.permutation_p([1, 2, 3], [4, 5, 6], seed=1)
        self.assertTrue(exact)
        self.assertAlmostEqual(0.1, p, places=6)

    def test_the_report_does_not_move_when_you_run_it_again(self):
        a, b = [1.0, 2.0, 3.0, 4.0, 9.0], [2.0, 3.0, 4.0, 5.0, 11.0]
        self.assertEqual(cmp._seed_for("m", a, b), cmp._seed_for("m", a, b))

    def test_holm_is_monotone_and_order_preserving(self):
        adjusted = cmp.holm([0.01, 0.04, 0.5])
        self.assertEqual(3, len(adjusted))
        self.assertLessEqual(adjusted[0], adjusted[1])
        self.assertLessEqual(adjusted[1], adjusted[2])
        self.assertAlmostEqual(0.03, adjusted[0])

    def test_the_needed_n_grows_as_the_effect_shrinks(self):
        big = cmp.n_required(sd=1.0, effect=1.0)
        small = cmp.n_required(sd=1.0, effect=0.1)
        self.assertLess(big, small)
        self.assertIsNone(cmp.n_required(sd=0.0, effect=1.0))


class Comparison(unittest.TestCase):
    def _compare(self, baseline, patched, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            a = write_metric_file(os.path.join(tmp, "a.jsonl"), baseline, "baseline",
                                  **kwargs)
            b = write_metric_file(os.path.join(tmp, "b.jsonl"), patched, "patched",
                                  **kwargs)
            return cmp.compare_files(a, b)

    def test_identical_digests_collapse_n_to_one_rather_than_reporting_a_p(self):
        # The dangerous case: deterministic trials, zero variance, a real
        # difference of means. Naive statistics call this infinitely
        # significant off two data points.
        report = self._compare([5.0] * 50, [9.0] * 50, digest_each=False)
        metric = report.metrics[0]
        self.assertEqual(cmp.VERDICT_DETERMINISTIC, metric.verdict)
        self.assertNotEqual(metric.p, metric.p)  # NaN: no p was computed
        self.assertTrue(any("n_effective is 1" in w for w in report.warnings))
        self.assertIn("n_effective = 1", cmp.format_report(report))

    def test_a_real_change_survives_correction(self):
        baseline = [float(x) for x in (10, 11, 9, 10, 12, 11, 10, 9, 11, 10)]
        patched = [float(x) for x in (20, 21, 19, 20, 22, 21, 20, 19, 21, 20)]
        report = self._compare(baseline, patched)
        self.assertEqual(cmp.VERDICT_CHANGE, report.metrics[0].verdict)
        self.assertGreater(abs(report.metrics[0].g), 3.0)

    def test_no_difference_is_reported_as_an_upper_bound_not_as_no_difference(self):
        baseline = [float(x) for x in (10, 11, 9, 10, 12, 11, 10, 9, 11, 10)]
        patched = [float(x) for x in (11, 10, 10, 9, 11, 12, 10, 10, 9, 11)]
        report = self._compare(baseline, patched)
        metric = report.metrics[0]
        self.assertEqual(cmp.VERDICT_NO_EVIDENCE, metric.verdict)
        text = cmp.format_report(report)
        self.assertIn("NOT evidence of no change", text.replace("not ", "NOT "))
        self.assertIn("could only have detected", text)
        self.assertEqual(metric.mde, metric.mde)  # a real number

    def test_too_few_iterations_is_underpowered_not_a_verdict(self):
        report = self._compare([1.0, 2.0], [8.0, 9.0])
        self.assertEqual(cmp.VERDICT_UNDERPOWERED, report.metrics[0].verdict)
        self.assertIn("No statistics attempted", cmp.format_report(report))

    def test_a_spec_mismatch_is_the_first_thing_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = write_metric_file(os.path.join(tmp, "a.jsonl"), [1.0] * 6,
                                  "baseline", spec="d1")
            b = write_metric_file(os.path.join(tmp, "b.jsonl"), [2.0] * 6,
                                  "patched", spec="d2")
            report = cmp.compare_files(a, b)
        self.assertIn("SPEC MISMATCH", report.warnings[0])

    def test_drift_within_an_arm_is_flagged(self):
        drifting = [float(x) for x in range(20)]
        flat = [10.0] * 20
        report = self._compare(drifting, flat)
        self.assertTrue(any("drifts with iteration index" in note
                            for note in report.metrics[0].notes))

    def test_a_metric_present_in_only_one_arm_is_missing_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = write_metric_file(os.path.join(tmp, "a.jsonl"), [1.0] * 6, "baseline")
            b = os.path.join(tmp, "b.jsonl")
            provenance = res.Provenance("r", "t", "d1", "s.p2s", "rev", "patched")
            with res.JsonlSink(b, provenance) as sink:
                sink.write("run", trial="t")
                for index in range(6):
                    sink.write("metric", iteration=index, metric="other", value=1.0)
                    sink.write("iteration", iteration=index, status="ok",
                               digest="d%d" % index)
            report = cmp.compare_files(a, b)
        verdicts = {m.name: m.verdict for m in report.metrics}
        self.assertEqual(cmp.VERDICT_MISSING, verdicts["m"])
        self.assertEqual(cmp.VERDICT_MISSING, verdicts["other"])

    def test_null_metrics_are_counted_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.jsonl")
            provenance = res.Provenance("r", "t", "d1", "s.p2s", "rev", "baseline")
            with res.JsonlSink(path, provenance) as sink:
                sink.write("run", trial="t")
                for index in range(10):
                    sink.write("metric", iteration=index, metric="m",
                               value=None if index < 5 else 1.0)
                    sink.write("iteration", iteration=index, status="ok",
                               digest="d%d" % index)
            other = write_metric_file(os.path.join(tmp, "b.jsonl"), [1.0] * 10,
                                      "patched")
            report = cmp.compare_files(path, other)
        self.assertEqual(5, report.metrics[0].missing_a)
        self.assertTrue(any("produced no value" in note
                            for note in report.metrics[0].notes))


# --------------------------------------------------------------------------
# The CLI
# --------------------------------------------------------------------------


class StaleWorld(unittest.TestCase):
    """The 179 bug, replayed forever.

    The first determinism run confirmed iteration 1's load with a predicate
    that was already true in iteration 0's still-running world. Its first
    samples photographed that outgoing world -- tick 179, the previous
    iteration's final frame -- and the analyzer convicted the engine of the
    instrument's crime. These tests pin the contract that killed it.
    """

    CONFIRM = LoadConfirm(addr=0x00601280, expected=0x00700000,
                          description="player array pointer is populated")

    def test_a_vacuous_confirm_on_a_later_iteration_needs_a_clock_edge(self):
        world = FakeWorld()
        emu = FakeEmu(world, load_lag=3)
        runner = Runner(emu, world, clock=DrivenClock(world), printer=silent)
        trial = simple_trial(load_confirm=self.CONFIRM,
                             stop=StopCondition(max_frames=30))
        first = runner.run_iteration(trial, 0)
        self.assertEqual("ok", first.status)
        # Iteration 1: the outgoing play is at tick ~30 and still satisfies
        # the predicate. The lagged load lands three polls in, rewinding the
        # clock -- and only that rewind may confirm it.
        second = runner.run_iteration(trial, 1)
        self.assertEqual("ok", second.status)
        self.assertEqual("declared-edge", second.confirmed)

    def test_a_failed_load_under_a_vacuous_confirm_is_refused_not_sampled(self):
        world = FakeWorld()
        emu = FakeEmu(world, load_fails=True)
        runner = Runner(emu, world, clock=DrivenClock(world), printer=silent)
        trial = simple_trial(load_confirm=self.CONFIRM,
                             stop=StopCondition(max_frames=30))
        self.assertEqual("ok", runner.run_iteration(trial, 0).status)
        # The predicate stays true -- it describes the stale world perfectly.
        # Without the edge requirement this samples 30 frames of a world the
        # trial believes it replaced; with it, the iteration refuses.
        second = runner.run_iteration(trial, 1)
        self.assertEqual("load_unconfirmed", second.status)
        self.assertEqual(0, second.frames)
        self.assertIn("clock edge", second.reason)

    def test_a_window_confirm_rejects_the_world_that_ran_past_it(self):
        # The spec-side fix: a window is false in the outgoing world, so it
        # confirms as an ordinary false -> true transition, no edge needed.
        world = FakeWorld()
        emu = FakeEmu(world, load_lag=2)
        runner = Runner(emu, world, clock=DrivenClock(world), printer=silent)
        confirm = LoadConfirm(addr=0x00700054, lo=1, hi=6,
                              description="snap counter inside the state's window")
        trial = simple_trial(load_confirm=confirm,
                             stop=StopCondition(max_frames=40))
        # Drive the world well past the window before "loading" over it.
        world.advance(30)
        emu.memory[0x00700054] = world.frames_since_snap()
        result = runner.run_iteration(trial, 1)
        self.assertEqual("ok", result.status)
        self.assertEqual("declared", result.confirmed)

    def test_a_window_admitting_zero_is_refused_at_construction(self):
        # Unmapped reads return 0, so a window containing 0 would confirm on
        # a drifted address forever.
        with self.assertRaises(ValueError):
            LoadConfirm(addr=0x00700054, lo=0, hi=6)


class CertifiedWorld(FakeWorld):
    """A fake with layer 3's certified batch: values keyed to the tick."""

    def __init__(self, tear_at=(), **kwargs):
        FakeWorld.__init__(self, **kwargs)
        self.tear_at = set(tear_at)
        self.batch_calls = 0

    def certified_batch(self, parts):
        self.batch_calls += 1
        tick = self._snap
        decoded = []
        for player, fields in parts:
            decoded.append({name: getattr(player, name, None) for name in fields})
        after = tick
        if tick in self.tear_at:
            self.tear_at.discard(tick)     # tear once, then read clean
            after = tick + 1
        return tick, after, decoded


class CertifiedSampling(unittest.TestCase):
    def _runner(self, world):
        return Runner(FakeEmu(world), world, clock=DrivenClock(world),
                      printer=silent)

    def test_rows_carry_the_batch_tick_and_its_certificate(self):
        world = CertifiedWorld()
        sink = res.MemorySink()
        self._runner(world).run_iteration(
            simple_trial(stop=StopCondition(max_frames=8)), 0, sink=sink)
        rows = sink.of_kind("sample")
        self.assertTrue(rows)
        self.assertTrue(all(row["certified"] for row in rows))
        self.assertTrue(all(row["tick"] is not None for row in rows))

    def test_a_torn_batch_is_retried_rather_than_recorded(self):
        world = CertifiedWorld(tear_at={2})
        sink = res.MemorySink()
        self._runner(world).run_iteration(
            simple_trial(stop=StopCondition(max_frames=8)), 0, sink=sink)
        # The tear at tick 2 forces a second batch; every recorded row is
        # still certified because the retry read clean.
        self.assertTrue(all(row["certified"] for row in sink.of_kind("sample")))

    def test_the_game_clock_row_is_the_certified_tick_itself(self):
        # A separate read of frames_since_snap would reintroduce the skew the
        # batch exists to remove; the emitted value must be the bracket tick.
        world = CertifiedWorld()
        sink = res.MemorySink()
        self._runner(world).run_iteration(
            simple_trial(stop=StopCondition(max_frames=8)), 0, sink=sink)
        for row in sink.of_kind("sample"):
            if row["entity"] == "game" and row["field"] == "frames_since_snap":
                self.assertEqual(row["tick"], row["value"])


class TickAlignedVerdict(unittest.TestCase):
    """The analyzer that acquitted the engine, pinned.

    Ordinal comparison called a bitwise-reproducible engine DIVERGENT because
    iterations started sampling at different moments of the play. Aligned on
    the game clock, every disagreement in that capture was bitwise-equal to
    the other run one or two ticks away -- sampling phase. The verdict must
    keep those two things distinct forever.
    """

    @staticmethod
    def _result(index, stream):
        frames = [Frame(tick - stream[0][0], dict(values), tick=tick,
                        certified=True)
                  for tick, values in stream]
        samples = Samples(frames)
        return IterationResult(index=index, frames=len(frames),
                               digest="d%d" % index, status="ok", reason="",
                               dropped=0, wall_ms=0.0, samples=samples,
                               confirmed="declared")

    def test_pure_phase_shift_is_deterministic_not_divergent(self):
        key = ("player:0:1", "block_mode")
        a = self._result(0, [(t, {key: 0 if t < 10 else 2}) for t in range(5, 15)])
        # The same transition, observed one tick later by the second run.
        b = self._result(1, [(t, {key: 0 if t < 11 else 2}) for t in range(5, 15)])
        fields, common, _un = runner_mod._tick_aligned_agreement([a, b])
        report = DeterminismReport(trial="t", repeats=2, digests=["x", "y"],
                                   identical=False, first_divergence=None,
                                   seeded=False, fields=fields,
                                   common_ticks=common, uncertified=0)
        self.assertEqual("DETERMINISTIC (phase noise only)", report.verdict)
        self.assertIn("phase", report.describe())

    def test_a_value_matching_nothing_nearby_is_a_real_divergence(self):
        key = ("player:0:1", "ai_state")
        a = self._result(0, [(t, {key: 32}) for t in range(5, 15)])
        b_vals = [(t, {key: 32}) for t in range(5, 15)]
        b_vals[4] = (9, {key: 77})       # 77 appears nowhere in run A
        b = self._result(1, b_vals)
        fields, common, _un = runner_mod._tick_aligned_agreement([a, b])
        report = DeterminismReport(trial="t", repeats=2, digests=["x", "y"],
                                   identical=False, first_divergence=None,
                                   seeded=False, fields=fields,
                                   common_ticks=common, uncertified=0)
        self.assertEqual("DIVERGENT", report.verdict)
        self.assertIn("REAL", report.describe())
        self.assertIn("tick 9", report.describe())

    def test_uncertified_samples_are_excluded_not_compared(self):
        key = ("player:0:1", "engagement")
        a = self._result(0, [(t, {key: 4}) for t in range(5, 10)])
        b = self._result(1, [(t, {key: 4}) for t in range(5, 10)])
        # Make one of B's frames torn, with a wild value: it must not count.
        b.samples.frames[2].certified = False
        b.samples.frames[2].values[key] = 999
        fields, _common, uncertified = runner_mod._tick_aligned_agreement([a, b])
        self.assertEqual(1, uncertified)
        self.assertTrue(all(f.real == 0 for f in fields))


class Cli(unittest.TestCase):
    """Parsing and dispatch, with no lower layers importable."""

    def setUp(self):
        from tools.madden_lab import __main__ as cli
        self.cli = cli

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_no_command_prints_help_and_fails(self):
        code, out, _err = self._run([])
        self.assertEqual(self.cli.EXIT_USAGE, code)
        self.assertIn("doctor", out)

    def test_compare_needs_no_emulator(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = write_metric_file(os.path.join(tmp, "a.jsonl"), [1.0] * 8, "baseline")
            b = write_metric_file(os.path.join(tmp, "b.jsonl"), [1.1] * 8, "patched")
            code, out, _err = self._run(["compare", a, b])
        self.assertEqual(self.cli.EXIT_OK, code)
        self.assertIn("baseline", out)

    def test_compare_json_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = write_metric_file(os.path.join(tmp, "a.jsonl"), [1.0] * 8, "baseline")
            b = write_metric_file(os.path.join(tmp, "b.jsonl"), [2.0] * 8, "patched")
            code, out, _err = self._run(["compare", a, b, "--json"])
        self.assertEqual(self.cli.EXIT_OK, code)
        self.assertEqual("m", json.loads(out)["metrics"][0]["name"])

    def test_an_unreachable_emulator_is_one_line_not_a_traceback(self):
        # These commands are run at a console next to a booting console; a
        # stack trace there costs a session, and layer 1's own message already
        # says which socket it looked for.
        code, _out, err = self._run(["doctor"])
        self.assertEqual(self.cli.EXIT_UNAVAILABLE, code)
        self.assertNotIn("Traceback", err)
        self.assertIn("error:", err)

    def test_a_bad_iteration_count_is_rejected_before_anything_connects(self):
        with self.assertRaises(SystemExit):
            self.cli.build_parser().parse_args(
                ["trial", "--spec", "x.py", "-n", "0"])

    def test_the_worked_example_loads_and_is_well_formed(self):
        root = Path(__file__).resolve().parent.parent
        trial = self.cli.load_spec(str(root / "experiments" / "lead_blocker.py"))
        self.assertEqual("lead_blocker_gate_a", trial.name)
        self.assertTrue(trial.metrics)
        self.assertTrue(trial.cannot_conclude)
        self.assertIsNotNone(trial.load_confirm)
        self.assertEqual((), trial.setup)  # baseline arm writes nothing
        for ask in trial.asks:
            self.assertTrue(ask.why_not_memory.strip())

    def test_the_worked_examples_metrics_run_against_a_fake_world(self):
        root = Path(__file__).resolve().parent.parent
        trial = self.cli.load_spec(str(root / "experiments" / "lead_blocker.py"))
        players = [FakePlayer(0, 0, 5), FakePlayer(1, 0, 6), FakePlayer(0, 1, 20)]
        world = FakeWorld(players=players, script={
            2: [(0, "block_mode", 3), (1, "block_mode", 1), (2, "ai_state", 22)],
        })
        emu = FakeEmu(world)
        # The spec's load confirm checks formation geometry against its own
        # savestate's bytes; the fake must stand the QB and HB on those spots
        # or the confirm (correctly) refuses to believe the load.
        import struct as _s
        as_word = lambda v: int.from_bytes(_s.pack("<f", v), "little")  # noqa: E731
        desc, base = 0x00700100, 0x00710000
        emu.memory.update({
            0x00600E48: desc, desc: base,
            base + 0x190: as_word(0.0), base + 0x194: as_word(13.4),
            base + 5312 + 0x190: as_word(-0.0403),
            base + 5312 + 0x194: as_word(7.9718),
        })
        runner = Runner(emu, world, pad=FakePad(),
                        clock=DrivenClock(world), printer=silent)
        result = runner.run_iteration(trial, 0)
        self.assertEqual("ok", result.status)
        self.assertEqual(1.0, result.metrics["lead_blockers_seen"])
        self.assertEqual(0.0, result.metrics["lead_blocker_in_pool_frames"])
        self.assertEqual(1.0, result.metrics["pool_blockers"])
        self.assertEqual(1.0, result.metrics["coverage_defenders_typical"])
        # Nobody is engaged with anybody, so Gate B's consequence is zero --
        # and zero here is a real measurement, not a missing one.
        self.assertEqual(0.0, result.metrics["blocks_on_coverage_defenders"])
        self.assertEqual(0.0, result.metrics["lead_blocker_partners"])


# --------------------------------------------------------------------------
# Episode scoping -- the defect this harness has now shipped three times
# --------------------------------------------------------------------------


def _spec_module(name):
    """Import an experiment file by path, as a MODULE rather than as a Trial.

    `cli.load_spec` returns the Trial and registers whatever it imported under
    one fixed name in `sys.modules`. These tests need the metric functions
    themselves, and need two specs resident at once, so they do the import
    directly under distinct names. Same reasoning as `load_spec`'s: experiments
    are not a package and should not become one.
    """
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "experiments" / (name + ".py")
    spec = importlib.util.spec_from_file_location("_spec_" + name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules["_spec_" + name] = module
    spec.loader.exec_module(module)
    return module


def _lerp(lo, hi, step, steps):
    return lo if steps <= 0 else lo + (hi - lo) * (float(step) / steps)


class EpisodeScoping(unittest.TestCase):
    """The slot 9 baseline, rebuilt synthetically, with no emulator.

    Every number below is from the measured run recorded in
    `docs/double-team-requirements.md`: three pairings of 13, 17 and 30 held
    frames, all finished by frame 43 of a 308-frame play; +0.410 yd (15 in) of
    real pushback on the doubled end; 3.178 yd of whole-play travel that was
    reported as pushback and was really pursuit after the block ended; and a
    linebacker who moves further than either but forwards and sideways, out of
    the block rather than back.

    The 0.410 and the 3.178 are put on ONE body here -- in the measured run the
    3.178 was the linebacker's and the end's whole-play figure was +0.873 --
    because a single position series that yields both readings is exactly what
    pins the bug: nothing but the scoping decides which number comes out.

    Those two numbers -- 0.410 and 3.178 -- are the whole point of this class.
    The same sample data produces both, and which one a metric reports is
    decided entirely by whether it is scoped to the frames where the pairing was
    live. 3.178 was reported to the operator as the pushback. The operator,
    watching the screen, said "maybe a few inches". These tests exist so that
    the harness cannot quietly go back to agreeing with itself instead of him.
    """

    ROLE_FREE = 5
    PLAY_FRAMES = 308

    #: entity -> {role: [frames it holds that role]}. Held frames are COUNTED,
    #: so the tight end's 30 are deliberately split either side of a gap: 2-16
    #: as primary and 29-43 as helper, spanning 42 frames. A metric that
    #: measured `last - first + 1` would call that a 42-frame hold and report
    #: R6 as nearly satisfied on a play the operator watched fail.
    HOLDS = {
        "player:0:9": {0: list(range(2, 10)), 1: list(range(10, 19))},   # RT, 17
        "player:0:4": {0: list(range(2, 17)), 1: list(range(29, 44))},   # TE, 30
        "player:0:8": {0: list(range(27, 36)) + list(range(40, 44))},    # RG, 13
        "player:1:10": {2: list(range(2, 19))},                          # DE, 17
        "player:1:13": {2: list(range(27, 40))},                         # LB, 13
    }
    #: A defender whose role byte reads 2 on exactly ONE frame. PINE reads are
    #: not synchronised with emulation, so this is what a torn read looks like;
    #: counted, it would make `dt_shortest_hold` 1 and `dt_last_hold_frame` 100.
    TORN = ("player:1:16", 100)

    def setUp(self):
        self.dt = _spec_module("double_team")

    def _roles(self):
        roles = {}
        for entity, by_role in self.HOLDS.items():
            frame_role = {}
            for role, frames in by_role.items():
                for frame in frames:
                    frame_role[frame] = role
            roles[entity] = frame_role
        roles[self.TORN[0]] = {self.TORN[1]: 2}
        return roles

    def _samples(self):
        roles = self._roles()
        frames = []
        for i in range(self.PLAY_FRAMES):
            values = {
                ("game", "frames_since_snap"): max(0, i - 4),
                ("game", "los"): 15.0,
                # The carrier starts behind the line, so the offence advances
                # toward increasing y and "driven backwards" is +y. Sampled
                # rather than assumed -- the spec derives the sign from these.
                ("game", "carrier_y"): 13.4 + 0.02 * i,
            }
            for entity, frame_role in roles.items():
                role = frame_role.get(i, self.ROLE_FREE)
                values[(entity, "dt_role")] = role
                values[(entity, "ai_state")] = 32 if role != self.ROLE_FREE else 20
                # A helper who is a statue during the block and a sprinter for
                # the 265 frames afterwards. The whole-play median of this
                # series is 0.90; the double team's median is 0.05.
                values[(entity, "speed_cmd")] = (
                    0.40 if role == 0 else 0.05 if role == 1 else 0.90)
            values.update(self._defender_positions(i))
            frames.append(Frame(i, values))
        return Samples(frames)

    def _defender_positions(self, i):
        """The doubled end and the doubled linebacker, frame by frame.

        The end is driven from (0.151, 16.280) to (0.061, 16.690) across his 17
        doubled frames -- dx -0.090, dy +0.410, 0.420 yd of travel, all of it
        backwards -- and then pursues the ball to y 19.458, which is exactly
        3.178 above where he started. The linebacker moves 1.184 sideways and
        0.213 *forwards* while doubled: further than the end, and in the wrong
        direction for R3.
        """
        if i <= 2:
            end = (0.151, 16.280)
        elif i <= 18:
            end = (_lerp(0.151, 0.061, i - 2, 16),
                   _lerp(16.280, 16.690, i - 2, 16))
        else:
            end = (0.061, _lerp(16.690, 19.458, i - 18, self.PLAY_FRAMES - 19))
        if i <= 27:
            backer = (1.000, 16.500)
        elif i <= 39:
            backer = (_lerp(1.000, -0.184, i - 27, 12),
                      _lerp(16.500, 16.287, i - 27, 12))
        else:
            backer = (-0.184, 16.287)
        return {("player:1:10", "pos_x"): end[0], ("player:1:10", "pos_y"): end[1],
                ("player:1:13", "pos_x"): backer[0],
                ("player:1:13", "pos_y"): backer[1]}

    # -- R6: how long the pairing lasts ------------------------------------

    def test_the_measured_holds_are_reproduced(self):
        samples = self._samples()
        self.assertEqual(30.0, self.dt.m_dt_longest_hold(samples))
        self.assertEqual(13.0, self.dt.m_dt_shortest_hold(samples))
        self.assertEqual(43.0, self.dt.m_dt_last_hold_frame(samples))

    def test_a_hold_is_frames_held_and_not_the_window_it_spans(self):
        # The tight end's 30 frames span 2..43. Spanning would say 42.
        holds = dict((e, h) for e, h, _f in self.dt._holds(self._samples()))
        self.assertEqual(30, holds["player:0:4"])
        self.assertEqual(13, holds["player:0:8"])
        self.assertEqual(17, holds["player:1:10"])

    def test_a_one_frame_role_byte_is_not_a_pairing(self):
        samples = self._samples()
        # The torn read sits at frame 100, well past the last real pairing.
        self.assertEqual(43.0, self.dt.m_dt_last_hold_frame(samples))
        self.assertEqual(13.0, self.dt.m_dt_shortest_hold(samples))
        self.assertEqual(5.0, self.dt.m_dt_registered(samples))

    def test_peel_off_does_not_count_as_held(self):
        # Role 3 is the pairing coming apart. If it counted, the right guard's
        # 13 frames would become 20 and a patch that only made blockers peel
        # more slowly would look like a patch that made them hold.
        roles = self._roles()
        frames = []
        for i in range(self.PLAY_FRAMES):
            values = {("game", "frames_since_snap"): max(0, i - 4)}
            for entity, frame_role in roles.items():
                role = frame_role.get(i, self.ROLE_FREE)
                if entity == "player:0:8" and 44 <= i <= 50:
                    role = 3
                values[(entity, "dt_role")] = role
            frames.append(Frame(i, values))
        holds = dict((e, h) for e, h, _f in self.dt._holds(Samples(frames)))
        self.assertEqual(13, holds["player:0:8"])

    # -- R3: pushback, the number that was wrong by 8x ---------------------

    def test_pushback_is_scoped_to_the_frames_the_man_was_doubled(self):
        samples = self._samples()
        ys = [v for v in samples.values("player:1:10", "pos_y") if v is not None]
        # What the old whole-play metric computed from this very series, and
        # reported as R3 being close to met.
        self.assertAlmostEqual(3.178, max(ys) - ys[0], places=3)
        # What the block actually did: fifteen inches.
        self.assertAlmostEqual(
            0.410, self.dt.m_defender_pushback(samples), places=3)

    def test_displacement_and_pushback_describe_the_same_body(self):
        samples = self._samples()
        # The linebacker travels 1.204 yd, three times the end's 0.420 -- but
        # forwards and sideways. Reporting his distance beside the end's
        # pushback would read as a man driven 43 inches backwards.
        self.assertAlmostEqual(
            0.420, self.dt.m_defender_displacement(samples), places=3)
        drive = self.dt._drive(samples, "player:1:13")
        self.assertAlmostEqual(-0.213, drive[0], places=3)
        self.assertAlmostEqual(1.203, drive[1], places=3)

    def test_a_defender_who_is_never_doubled_contributes_nothing(self):
        samples = self._samples()
        self.assertIsNone(self.dt._drive(samples, self.TORN[0]))

    # -- R2: the statue ----------------------------------------------------

    def test_speeds_are_scoped_to_the_role_the_player_held(self):
        samples = self._samples()
        # Unscoped, both roles converge on running speed and the gap vanishes:
        # that is how "R2 nearly satisfied" was reported off a play where the
        # helper stood still.
        whole_play = [v for v in samples.values("player:0:4", "speed_cmd")
                      if v is not None]
        self.assertAlmostEqual(0.90, sorted(whole_play)[len(whole_play) // 2])
        self.assertAlmostEqual(0.05, self.dt.m_helper_speed(samples), places=6)
        self.assertAlmostEqual(0.40, self.dt.m_primary_speed(samples), places=6)

    def test_a_man_who_holds_both_roles_lands_on_both_sides_by_frame(self):
        # The tight end is primary 2-16 and helper 29-43. A per-player split
        # would put all 308 of his frames on one side of the comparison.
        samples = self._samples()
        self.assertIn("player:0:4", self.dt._role_holders(samples, 0))
        self.assertIn("player:0:4", self.dt._role_holders(samples, 1))
        self.assertEqual(list(range(2, 17)),
                         self.dt._role_frames(samples, "player:0:4", (0,)))

    # -- the spec still declares what it measures --------------------------

    def test_the_spec_carries_the_r6_metrics_as_its_headline(self):
        from tools.madden_lab import __main__ as cli
        root = Path(__file__).resolve().parent.parent
        trial = cli.load_spec(str(root / "experiments" / "double_team.py"))
        names = [m.name for m in trial.metrics]
        for required in ("dt_longest_hold", "dt_shortest_hold",
                         "dt_last_hold_frame", "defender_pushback",
                         "defender_displacement"):
            self.assertIn(required, names)
        # R6 is the primary requirement, so duration leads the report.
        self.assertEqual("dt_longest_hold", names[0])


class PassProtectionEpisodes(unittest.TestCase):
    """The same defect, in the file where it was first found.

    A composite that resets at every lock-in cannot be summarised over the
    play, and a *step* in it cannot be taken across the gap between two reps:
    that differences one block against another, which is the confound
    `worst_drop_late` exists to avoid.
    """

    def setUp(self):
        self.pp = _spec_module("pass_protection")

    @staticmethod
    def _blocker(power_by_frame, engaged, entity="player:0:6", frames=40):
        values = []
        for i in range(frames):
            row = {("game", "frames_since_snap"): i,
                   (entity, "engagement"): 4 if i in engaged else 0}
            if i in power_by_frame:
                row[(entity, "contest_power")] = power_by_frame[i]
            values.append(Frame(i, row))
        return Samples(values)

    def test_a_step_is_never_taken_across_an_episode_boundary(self):
        # Two reps: 0-9 at ~1000 decaying once, then 20-29 recomputed to 800.
        power = {}
        for i in range(0, 10):
            power[i] = 1000.0 if i < 5 else 900.0
        for i in range(20, 30):
            power[i] = 800.0 if i < 25 else 850.0
        samples = self._blocker(power, set(range(0, 10)) | set(range(20, 30)))
        steps = self.pp._steps(samples, "player:0:6")
        # 900 -> 800 across the gap is not a decay step; it is one man's second
        # block being differenced against his first.
        self.assertEqual([5, 25], [snap for snap, _f in steps])
        self.assertAlmostEqual(-0.1, steps[0][1], places=6)
        self.assertAlmostEqual(0.0625, steps[1][1], places=6)
        self.assertEqual(1.0, self.pp.m_decay_steps(samples))
        self.assertEqual(1.0, self.pp.m_recompute_steps(samples))

    def test_a_rusher_whose_field_fills_in_late_is_still_measured(self):
        # The episode opens before the contest triple is populated, so the
        # series starts at 0.0. Basing the rise on that zero threw the whole
        # episode away and reported "no mirror observed".
        frames = []
        for i in range(20):
            row = {("game", "frames_since_snap"): i,
                   ("player:1:3", "engagement"): 4 if i < 10 else 0}
            row[("player:1:3", "contest_overall")] = (
                0.0 if i < 2 else 100.0 if i < 5 else 120.0)
            frames.append(Frame(i, row))
        gain = self.pp.m_rusher_gain(Samples(frames))
        self.assertIsNotNone(gain)
        self.assertAlmostEqual(0.2, gain, places=6)


if __name__ == "__main__":
    unittest.main()

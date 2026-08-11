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
        runner = Runner(FakeEmu(world), world, pad=FakePad(),
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


if __name__ == "__main__":
    unittest.main()

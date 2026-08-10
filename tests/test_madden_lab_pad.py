"""Layer 2: the pad. Everything here runs without a rig, an emulator or a device.

Three things are worth testing offline, and they are the three that would be
expensive to discover on the rig.

The **uinput ABI** is encoded into the ioctl numbers themselves -- the size of
`struct uinput_setup` is part of `UI_DEV_SETUP` -- so a struct format that is
one field or one pad byte wrong produces `-EINVAL` from the kernel and nothing
else. The constants below were taken from a C program compiled against
`/usr/include/linux/uinput.h`, so they are the kernel's own arithmetic rather
than a repeat of the module's.

The **scheduler** is where a trial silently becomes a different trial. Two taps
of one button with no gap between them are one long press as far as the game is
concerned, and a run built on that would be blamed on the engine.

The **failure path** matters because `/dev/uinput` is root-only out of the box
on Ubuntu, so the first thing anyone ever sees from this module is the error.
It has to name the fix.

The device is faked at the syscall boundary, which is the same shape as the
fake PINE server in `test_pine.py`: the module under test does real work, and
only the four calls that would touch hardware are intercepted.
"""

from __future__ import annotations

import errno
import io
import os
import struct
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.madden_lab import pad  # noqa: E402


class FakeKernel:
    """The four syscalls a uinput device makes, recorded rather than made."""

    def __init__(self, open_error=None, write_error=None, fd=7):
        self.open_error = open_error
        self.write_error = write_error
        self.fd = fd
        self.opened = []
        self.ioctls = []            # (request, argument)
        self.written = b""
        self.closed = []

    def as_kernel(self):
        return pad.Kernel(open=self.open, ioctl=self.ioctl,
                          write=self.write, close=self.close)

    def open(self, path, flags):
        self.opened.append((path, flags))
        if self.open_error is not None:
            raise self.open_error
        return self.fd

    def ioctl(self, fd, request, argument=0):
        assert fd == self.fd
        self.ioctls.append((request, argument))
        return 0

    def write(self, fd, blob):
        assert fd == self.fd
        if self.write_error is not None:
            raise self.write_error
        self.written += blob
        return len(blob)

    def close(self, fd):
        self.closed.append(fd)

    # -- convenience -------------------------------------------------

    def requests(self):
        return [request for request, _ in self.ioctls]

    def events(self):
        """The written bytes decoded back into (type, code, value)."""
        size = struct.calcsize(pad._EVENT_FORMAT)
        out = []
        for start in range(0, len(self.written), size):
            _, _, kind, code, value = struct.unpack(
                pad._EVENT_FORMAT, self.written[start:start + size])
            out.append((kind, code, value))
        return out


class FakeTime:
    """A monotonic clock the test advances, and a sleep that advances it."""

    def __init__(self, start=1000.0):
        self.now = start
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class FakeEmu:
    """Just the one method `MemoryClock` uses, plus a scripted memory."""

    def __init__(self, values=None, memory=None):
        self.values = list(values or [])
        self.memory = dict(memory or {})
        self.reads = []

    def read(self, address, size=4):
        self.reads.append((address, size))
        if address in self.memory:
            entry = self.memory[address]
            return entry.pop(0) if isinstance(entry, list) else entry
        return self.values.pop(0) if self.values else 0


# -- the kernel ABI ------------------------------------------------------


class Abi(unittest.TestCase):
    """The numbers a C compiler produces from the kernel headers.

    Not a restatement of the module: the ioctl request encodes the struct size,
    so these fail if `_SETUP_FORMAT` gains a field or loses a pad byte.
    """

    def test_ioctl_requests_match_the_kernel_headers(self):
        self.assertEqual(pad.UI_DEV_CREATE, 0x00005501)
        self.assertEqual(pad.UI_DEV_DESTROY, 0x00005502)
        self.assertEqual(pad.UI_DEV_SETUP, 0x405C5503)
        self.assertEqual(pad.UI_ABS_SETUP, 0x401C5504)
        self.assertEqual(pad.UI_SET_EVBIT, 0x40045564)
        self.assertEqual(pad.UI_SET_KEYBIT, 0x40045565)
        self.assertEqual(pad.UI_SET_ABSBIT, 0x40045567)

    def test_struct_sizes_match_the_kernel(self):
        self.assertEqual(struct.calcsize(pad._SETUP_FORMAT), 92)
        self.assertEqual(struct.calcsize(pad._ABS_SETUP_FORMAT), 28)
        self.assertEqual(struct.calcsize(pad._EVENT_FORMAT),
                         24 if sys.maxsize > 2 ** 32 else 16)

    def test_the_ps2_axis_range_is_the_native_one(self):
        # Resampling a stick anywhere between here and the game loses the
        # bottom bits of a deflection, and steering is mostly small ones.
        self.assertEqual((pad.AXIS_MIN, pad.AXIS_CENTRE, pad.AXIS_MAX),
                         (0, 128, 255))


# -- names ---------------------------------------------------------------


class Namespace(unittest.TestCase):
    def test_every_ps2_control_has_a_name(self):
        for name in ("cross", "circle", "square", "triangle", "up", "down",
                     "left", "right", "l1", "r1", "l2", "r2", "l3", "r3",
                     "start", "select"):
            self.assertIn(name, pad.BUTTONS, name)
        self.assertEqual(pad.STICKS, ("left", "right"))

    def test_face_buttons_use_the_positional_codes(self):
        # evdev names the face buttons by position, which is how the kernel's
        # own DualShock drivers report them. Getting this wrong swaps cross and
        # circle -- snap and slide protection -- and nothing would crash.
        self.assertEqual(pad.button_code("cross"), pad.BTN_SOUTH)
        self.assertEqual(pad.button_code("circle"), pad.BTN_EAST)
        self.assertEqual(pad.button_code("triangle"), pad.BTN_NORTH)
        self.assertEqual(pad.button_code("square"), pad.BTN_WEST)

    def test_the_dpad_is_not_a_key(self):
        # It goes out as a hat. If it were also a key an emulator bound to both
        # would count one press twice.
        for name in ("up", "down", "left", "right"):
            self.assertNotIn(name, pad._KEY_CODE)
            with self.assertRaises(ValueError):
                pad.button_code(name)

    def test_an_unknown_button_lists_the_real_ones(self):
        with self.assertRaises(ValueError) as caught:
            pad.button_code("a")
        message = str(caught.exception)
        self.assertIn("cross", message)
        self.assertIn("'a'", message)

    def test_a_near_miss_gets_a_suggestion(self):
        with self.assertRaises(ValueError) as caught:
            pad.PadState().pressing("triangel")
        self.assertIn("triangle", str(caught.exception))


class Axes(unittest.TestCase):
    def test_the_ends_and_the_centre_are_exact(self):
        self.assertEqual(pad.axis_from_unit(-1.0), 0)
        self.assertEqual(pad.axis_from_unit(0.0), 128)
        self.assertEqual(pad.axis_from_unit(1.0), 255)

    def test_negative_y_is_up(self):
        # Matches evdev and matches the raw PS2 byte, so "forward" is one
        # convention rather than two.
        state = pad.PadState().with_stick("left", 0.0, -1.0)
        self.assertEqual(state.left, (128, 0))

    def test_out_of_range_is_refused(self):
        for value in (-1.5, 2.0):
            with self.assertRaises(ValueError):
                pad.axis_from_unit(value)

    def test_a_non_number_is_refused(self):
        with self.assertRaises(ValueError):
            pad.axis_from_unit("hard left")

    def test_an_unknown_stick_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            pad.Stick("middle", 0.0, 0.0).validated()
        self.assertIn("left", str(caught.exception))

    def test_triggers_rest_at_zero_and_sticks_at_centre(self):
        # A trigger declared at mid-range reads as half-pressed forever,
        # because nothing here ever moves an untouched trigger.
        initial = {axis: value for axis, value, _, _ in pad.UinputDevice.axes()}
        self.assertEqual(initial[pad.ABS_X], pad.AXIS_CENTRE)
        self.assertEqual(initial[pad.ABS_RY], pad.AXIS_CENTRE)
        self.assertEqual(initial[pad.ABS_Z], 0)
        self.assertEqual(initial[pad.ABS_RZ], 0)
        self.assertEqual(initial[pad.ABS_HAT0X], 0)

    def test_no_deadzone_is_declared(self):
        for _, _, low, high in pad.UinputDevice.axes():
            self.assertLess(low, high)
        formats = [entry for entry in pad.UinputDevice.axes()
                   if entry[0] in (pad.ABS_HAT0X, pad.ABS_HAT0Y)]
        self.assertEqual(sorted(formats),
                         [(pad.ABS_HAT0X, 0, -1, 1), (pad.ABS_HAT0Y, 0, -1, 1)])


class Hat(unittest.TestCase):
    def test_the_dpad_becomes_one_hat(self):
        self.assertEqual(pad.PadState().pressing("right").hat, (1, 0))
        self.assertEqual(pad.PadState().pressing("left").hat, (-1, 0))
        self.assertEqual(pad.PadState().pressing("up").hat, (0, -1))
        self.assertEqual(pad.PadState().pressing("down").hat, (0, 1))

    def test_opposing_directions_cancel_rather_than_raising(self):
        # A hat cannot report both and neither can a thumb.
        self.assertEqual(pad.PadState().pressing("left", "right").hat, (0, 0))

    def test_diagonals_survive(self):
        self.assertEqual(pad.PadState().pressing("up", "right").hat, (1, -1))


# -- the evdev report ----------------------------------------------------


class Report(unittest.TestCase):
    def test_a_press_is_one_key_event_and_a_sync(self):
        events = pad.evdev_report(pad.NEUTRAL, pad.NEUTRAL.pressing("cross"))
        self.assertEqual(events, [(pad.EV_KEY, pad.BTN_SOUTH, 1),
                                  (pad.EV_SYN, pad.SYN_REPORT, 0)])

    def test_nothing_changed_means_nothing_is_written(self):
        # Sixty wakeups a second that say nothing is a cost with no benefit.
        self.assertEqual(pad.evdev_report(pad.NEUTRAL, pad.NEUTRAL), [])

    def test_a_report_always_ends_with_the_sync(self):
        # The SYN is what makes a frame atomic to the reader; without it a
        # stick move can be seen half-applied.
        state = pad.NEUTRAL.pressing("cross").with_stick("left", 1.0, -1.0)
        events = pad.evdev_report(pad.NEUTRAL, state)
        self.assertEqual(events[-1], (pad.EV_SYN, pad.SYN_REPORT, 0))
        self.assertEqual([e for e in events
                          if e[0] == pad.EV_SYN], [events[-1]])

    def test_a_trigger_is_both_a_button_and_a_saturated_axis(self):
        events = pad.evdev_report(pad.NEUTRAL, pad.NEUTRAL.pressing("l2"))
        self.assertIn((pad.EV_KEY, pad.BTN_TL2, 1), events)
        self.assertIn((pad.EV_ABS, pad.ABS_Z, pad.AXIS_MAX), events)

    def test_releasing_a_trigger_returns_its_axis_to_zero(self):
        held = pad.NEUTRAL.pressing("r2")
        events = pad.evdev_report(held, pad.NEUTRAL)
        self.assertIn((pad.EV_KEY, pad.BTN_TR2, 0), events)
        self.assertIn((pad.EV_ABS, pad.ABS_RZ, pad.AXIS_MIN), events)

    def test_the_dpad_moves_the_hat_axes(self):
        events = pad.evdev_report(pad.NEUTRAL, pad.NEUTRAL.pressing("up"))
        self.assertIn((pad.EV_ABS, pad.ABS_HAT0Y, -1), events)
        self.assertNotIn(pad.EV_KEY, [kind for kind, _, _ in events])

    def test_only_the_axis_that_moved_is_reported(self):
        moved = pad.NEUTRAL.with_stick("left", 1.0, 0.0)
        events = pad.evdev_report(pad.NEUTRAL, moved)
        self.assertEqual(events, [(pad.EV_ABS, pad.ABS_X, 255),
                                  (pad.EV_SYN, pad.SYN_REPORT, 0)])

    def test_the_two_sticks_use_different_axes(self):
        left = pad.evdev_report(pad.NEUTRAL,
                                pad.NEUTRAL.with_stick("left", 1.0, 1.0))
        right = pad.evdev_report(pad.NEUTRAL,
                                 pad.NEUTRAL.with_stick("right", 1.0, 1.0))
        self.assertEqual({code for _, code, _ in left[:-1]},
                         {pad.ABS_X, pad.ABS_Y})
        self.assertEqual({code for _, code, _ in right[:-1]},
                         {pad.ABS_RX, pad.ABS_RY})

    def test_the_report_order_is_stable(self):
        # Two runs of the same trial must produce byte-identical input.
        state = pad.NEUTRAL.pressing("cross", "r1", "up")
        self.assertEqual(pad.evdev_report(pad.NEUTRAL, state),
                         pad.evdev_report(pad.NEUTRAL, state))

    def test_an_event_packs_to_the_kernel_struct(self):
        blob = pad.pack_event(pad.EV_KEY, pad.BTN_SOUTH, 1)
        self.assertEqual(len(blob), struct.calcsize(pad._EVENT_FORMAT))
        self.assertEqual(struct.unpack(pad._EVENT_FORMAT, blob)[2:],
                         (pad.EV_KEY, pad.BTN_SOUTH, 1))


# -- the scheduler -------------------------------------------------------


def held(plan):
    """`(frame, sorted button names)` -- the readable shape of a plan."""
    return [(frame, sorted(state.buttons)) for frame, state in plan]


class Schedule(unittest.TestCase):
    def test_a_press_ends_the_frame_the_duration_says(self):
        # Half-open: (0, cross, 2) is down on frames 0 and 1, up on frame 2.
        self.assertEqual(held(pad.schedule([(0, "cross", 2)])),
                         [(0, ["cross"]), (2, [])])

    def test_offsets_are_relative_to_the_start_frame(self):
        self.assertEqual(held(pad.schedule([(0, "cross", 2)], start=9000)),
                         [(9000, ["cross"]), (9002, [])])

    def test_snap_wait_throw_is_the_shape_it_looks_like(self):
        plan = pad.schedule([(0, "cross", 2), (12, "triangle", 2)])
        self.assertEqual(held(plan), [(0, ["cross"]), (2, []),
                                      (12, ["triangle"]), (14, [])])

    def test_entries_need_not_be_in_order(self):
        out_of_order = pad.schedule([(12, "triangle", 2), (0, "cross", 2)])
        in_order = pad.schedule([(0, "cross", 2), (12, "triangle", 2)])
        self.assertEqual(out_of_order, in_order)

    def test_a_chord_presses_together(self):
        plan = pad.schedule([(0, ["cross", "r1"], 3)])
        self.assertEqual(held(plan), [(0, ["cross", "r1"]), (3, [])])

    def test_overlapping_different_buttons_are_fine(self):
        plan = pad.schedule([(0, "r1", 10), (2, "cross", 2)])
        self.assertEqual(held(plan), [(0, ["r1"]), (2, ["cross", "r1"]),
                                      (4, ["r1"]), (10, [])])

    def test_the_default_duration_is_two_frames(self):
        self.assertEqual(pad.DEFAULT_PRESS_FRAMES, 2)
        self.assertEqual(held(pad.schedule([(5, "start")])),
                         [(5, ["start"]), (7, [])])

    def test_only_changes_appear_so_a_long_script_stays_small(self):
        plan = pad.schedule([(0, "cross", 2), (3000, "circle", 2)])
        self.assertEqual(len(plan), 4)

    def test_an_empty_script_plans_nothing(self):
        self.assertEqual(pad.schedule([]), [])


class ScheduleSticks(unittest.TestCase):
    def test_a_stick_returns_to_centre_when_it_expires(self):
        plan = pad.schedule([(0, pad.Stick("left", 0.0, -1.0), 30)])
        self.assertEqual([(f, s.left) for f, s in plan],
                         [(0, (128, 0)), (30, (128, 128))])

    def test_a_later_nudge_wins_while_it_lasts(self):
        # Steering reads as overlapping nudges; the newest one is the thumb's
        # current position.
        plan = pad.schedule([(0, pad.Stick("left", 1.0, 0.0), 20),
                             (5, pad.Stick("left", -1.0, 0.0), 5)])
        self.assertEqual([(f, s.left) for f, s in plan],
                         [(0, (255, 128)), (5, (0, 128)),
                          (10, (255, 128)), (20, (128, 128))])

    def test_the_two_sticks_do_not_interfere(self):
        plan = pad.schedule([(0, pad.Stick("left", 1.0, 0.0), 4),
                             (0, pad.Stick("right", 0.0, 1.0), 4)])
        self.assertEqual(plan[0][1].left, (255, 128))
        self.assertEqual(plan[0][1].right, (128, 255))

    def test_a_stick_and_a_button_can_share_an_entry(self):
        plan = pad.schedule([(0, ["cross", pad.Stick("left", 1.0, 0.0)], 2)])
        self.assertEqual(sorted(plan[0][1].buttons), ["cross"])
        self.assertEqual(plan[0][1].left, (255, 128))


class ScheduleBase(unittest.TestCase):
    """A script composes with whatever `hold` left set, and returns to it."""

    def test_a_held_button_stays_held_through_a_script(self):
        base = pad.NEUTRAL.pressing("r1")
        plan = pad.schedule([(0, "cross", 2)], base=base)
        self.assertEqual(held(plan), [(0, ["cross", "r1"]), (2, ["r1"])])

    def test_a_script_ends_at_the_base_not_at_neutral(self):
        base = pad.NEUTRAL.with_stick("left", 1.0, 0.0)
        plan = pad.schedule([(0, pad.Stick("left", -1.0, 0.0), 4)], base=base)
        self.assertEqual(plan[-1][1], base)


class ScheduleRefusals(unittest.TestCase):
    def test_two_taps_with_no_gap_are_refused(self):
        # They would be one continuous hold: the pad reports state per frame,
        # so a release and a press on the same frame have no edge between them.
        with self.assertRaises(ValueError) as caught:
            pad.schedule([(0, "cross", 2), (2, "cross", 2)])
        message = str(caught.exception)
        self.assertIn("cross", message)
        self.assertIn("one press", message)

    def test_a_gap_of_one_frame_is_accepted(self):
        plan = pad.schedule([(0, "cross", 2), (3, "cross", 2)])
        self.assertEqual(held(plan),
                         [(0, ["cross"]), (2, []), (3, ["cross"]), (5, [])])

    def test_overlapping_presses_of_one_button_are_refused(self):
        with self.assertRaises(ValueError):
            pad.schedule([(0, "cross", 10), (5, "cross", 2)])

    def test_a_negative_offset_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            pad.schedule([(-1, "cross", 2)])
        self.assertIn("offset", str(caught.exception))

    def test_a_zero_frame_press_is_refused_and_says_why(self):
        with self.assertRaises(ValueError) as caught:
            pad.schedule([(0, "cross", 0)])
        self.assertIn("once per frame", str(caught.exception))

    def test_a_fractional_offset_is_refused(self):
        with self.assertRaises(ValueError):
            pad.schedule([(1.5, "cross", 2)])

    def test_an_unknown_button_is_refused_before_anything_runs(self):
        with self.assertRaises(ValueError) as caught:
            pad.schedule([(0, "a", 2)])
        self.assertIn("cross", str(caught.exception))

    def test_a_malformed_entry_names_its_index(self):
        with self.assertRaises(ValueError) as caught:
            pad.schedule([(0, "cross", 2), (1,)])
        self.assertIn("entry 1", str(caught.exception))

    def test_a_bare_string_is_not_a_script(self):
        with self.assertRaises(ValueError):
            pad.schedule("cross")


# -- clocks --------------------------------------------------------------


class Wall(unittest.TestCase):
    def test_a_frame_is_the_ntsc_one(self):
        self.assertAlmostEqual(pad.NTSC_FPS, 59.94005994, places=6)

    def test_frames_count_from_construction(self):
        clock = pad.WallClock(fps=60.0, now=FakeTime().monotonic)
        self.assertEqual(clock.frame(), 0)

    def test_waiting_sleeps_exactly_the_remaining_time(self):
        fake = FakeTime()
        clock = pad.WallClock(fps=60.0, now=fake.monotonic, sleep=fake.sleep)
        clock.wait_until(30)
        self.assertAlmostEqual(sum(fake.slept), 0.5, places=6)
        self.assertEqual(clock.frame(), 30)

    def test_a_frame_already_past_does_not_sleep(self):
        fake = FakeTime()
        clock = pad.WallClock(fps=60.0, now=fake.monotonic, sleep=fake.sleep)
        fake.now += 1.0
        clock.wait_until(30)
        self.assertEqual(fake.slept, [])

    def test_slip_records_how_late_the_wait_actually_was(self):
        # The honest part: a rig that cannot hold full speed shows up here
        # rather than as a quietly wrong result.
        fake = FakeTime()
        clock = pad.WallClock(fps=60.0, now=fake.monotonic, sleep=fake.sleep)
        fake.now += 1.0                       # overslept by 30 frames
        clock.wait_until(30)
        self.assertEqual(clock.slip, [30])

    def test_a_nonsense_frame_rate_is_refused(self):
        with self.assertRaises(ValueError):
            pad.WallClock(fps=0)


class Manual(unittest.TestCase):
    def test_it_jumps_to_the_frame_asked_for(self):
        clock = pad.ManualClock()
        clock.wait_until(12)
        self.assertEqual(clock.frame(), 12)
        self.assertEqual(clock.waits, [12])

    def test_it_never_goes_backwards(self):
        clock = pad.ManualClock(start=20)
        clock.wait_until(5)
        self.assertEqual(clock.frame(), 20)


class Memory(unittest.TestCase):
    def test_it_reads_the_counter_through_layer_one(self):
        emu = FakeEmu(memory={0x00601280: 4242})
        clock = pad.MemoryClock(emu, 0x00601280)
        self.assertEqual(clock.frame(), 4242)
        self.assertEqual(emu.reads, [(0x00601280, 4)])

    def test_a_pointer_chase_follows_the_offsets(self):
        # docs/ points at [[0x00601280] + 84] for frames-since-snap.
        emu = FakeEmu(memory={0x00601280: 0x01000000, 0x01000054: 7})
        clock = pad.MemoryClock(emu, 0x00601280, chain=(84,))
        self.assertEqual(clock.frame(), 7)

    def test_a_null_pointer_says_it_looks_like_the_main_menu(self):
        emu = FakeEmu(memory={0x00601280: 0})
        clock = pad.MemoryClock(emu, 0x00601280, chain=(84,))
        with self.assertRaises(pad.PadError) as caught:
            clock.frame()
        self.assertIn("main menu", str(caught.exception))

    def test_a_narrow_counter_is_refused_because_it_wraps(self):
        with self.assertRaises(ValueError) as caught:
            pad.MemoryClock(FakeEmu(), 0x100, size=2)
        self.assertIn("wraps", str(caught.exception))

    def test_waiting_polls_until_the_counter_arrives(self):
        fake = FakeTime()
        emu = FakeEmu(memory={0x100: [10, 10, 11, 12]})
        clock = pad.MemoryClock(emu, 0x100, sleep=fake.sleep,
                                now=fake.monotonic)
        clock.wait_until(12)
        self.assertEqual(clock.slip, [0])

    def test_slip_records_a_frame_that_was_overshot(self):
        fake = FakeTime()
        emu = FakeEmu(memory={0x100: [10, 15]})
        clock = pad.MemoryClock(emu, 0x100, sleep=fake.sleep,
                                now=fake.monotonic)
        clock.wait_until(12)
        self.assertEqual(clock.slip, [3])

    def test_a_counter_that_resets_is_reported_as_a_reset(self):
        # A frames-since-snap counter goes backwards every play, and that is a
        # different problem from a wedged emulator.
        fake = FakeTime()
        emu = FakeEmu(memory={0x100: [30, 30, 0]})
        clock = pad.MemoryClock(emu, 0x100, sleep=fake.sleep,
                                now=fake.monotonic)
        with self.assertRaises(pad.PadError) as caught:
            clock.wait_until(50)
        self.assertIn("backwards", str(caught.exception))

    def test_a_stalled_counter_times_out_with_both_explanations(self):
        fake = FakeTime()
        emu = FakeEmu(memory={0x100: 5})
        clock = pad.MemoryClock(emu, 0x100, timeout=0.05, interval=0.01,
                                sleep=fake.sleep, now=fake.monotonic)
        with self.assertRaises(pad.PadError) as caught:
            clock.wait_until(50)
        message = str(caught.exception)
        self.assertIn("paused", message)
        self.assertIn("not a frame counter", message)

    def test_calibration_accepts_a_real_frame_rate(self):
        fake = FakeTime()
        emu = FakeEmu(memory={0x100: [0, 60]})
        clock = pad.MemoryClock(emu, 0x100, sleep=fake.sleep,
                                now=fake.monotonic)
        self.assertAlmostEqual(clock.calibrate(seconds=1.0), 60.0, places=6)

    def test_calibration_rejects_a_counter_that_does_not_move(self):
        fake = FakeTime()
        emu = FakeEmu(memory={0x100: 5})
        clock = pad.MemoryClock(emu, 0x100, sleep=fake.sleep,
                                now=fake.monotonic)
        with self.assertRaises(pad.PadError) as caught:
            clock.calibrate(seconds=1.0)
        self.assertIn("not a frame counter", str(caught.exception))

    def test_calibration_rejects_the_wrong_rate(self):
        # Something that ticks once a second is a clock, not a frame counter,
        # and would stretch every duration in every trial by sixty.
        fake = FakeTime()
        emu = FakeEmu(memory={0x100: [0, 1]})
        clock = pad.MemoryClock(emu, 0x100, sleep=fake.sleep,
                                now=fake.monotonic)
        with self.assertRaises(pad.PadError) as caught:
            clock.calibrate(seconds=1.0)
        self.assertIn("59.94", str(caught.exception))


# -- the device ----------------------------------------------------------


class Device(unittest.TestCase):
    def _device(self, kernel=None):
        kernel = kernel or FakeKernel()
        device = pad.UinputDevice(kernel=kernel.as_kernel(),
                                  sleep=lambda _s: None)
        return device, kernel

    def test_creation_declares_the_event_types_before_the_codes(self):
        # The kernel ignores UI_SET_KEYBIT for an event type that was never
        # enabled, and says nothing about it.
        _, kernel = self._device()
        requests = kernel.requests()
        self.assertLess(requests.index(pad.UI_SET_EVBIT),
                        requests.index(pad.UI_SET_KEYBIT))
        self.assertEqual(requests[-2:], [pad.UI_DEV_SETUP, pad.UI_DEV_CREATE])

    def test_every_button_is_declared(self):
        _, kernel = self._device()
        declared = {argument for request, argument in kernel.ioctls
                    if request == pad.UI_SET_KEYBIT}
        self.assertEqual(declared, set(pad._KEY_CODE.values()))

    def test_every_axis_is_declared_with_its_range(self):
        _, kernel = self._device()
        setups = [argument for request, argument in kernel.ioctls
                  if request == pad.UI_ABS_SETUP]
        decoded = {struct.unpack(pad._ABS_SETUP_FORMAT, blob)[0]:
                   struct.unpack(pad._ABS_SETUP_FORMAT, blob)[1:4]
                   for blob in setups}
        self.assertEqual(decoded[pad.ABS_X], (128, 0, 255))
        self.assertEqual(decoded[pad.ABS_Z], (0, 0, 255))
        self.assertEqual(decoded[pad.ABS_HAT0Y], (0, -1, 1))

    def test_the_device_name_and_ids_reach_the_kernel(self):
        _, kernel = self._device()
        setup = [argument for request, argument in kernel.ioctls
                 if request == pad.UI_DEV_SETUP][0]
        bus, vendor, product, _version, name, _ff = struct.unpack(
            pad._SETUP_FORMAT, setup)
        self.assertEqual(bus, pad.BUS_USB)
        self.assertEqual(vendor, pad.DEFAULT_VENDOR)
        self.assertEqual(product, pad.DEFAULT_PRODUCT)
        self.assertTrue(name.startswith(b"madden-lab"))

    def test_it_is_not_a_sony_vendor_id(self):
        # Claiming to be a DualShock invites SDL to apply a mapping written
        # for a different axis layout, which is harder to diagnose than a pad
        # with no mapping at all.
        self.assertNotEqual(pad.DEFAULT_VENDOR, 0x054C)

    def test_applying_a_state_writes_the_report(self):
        device, kernel = self._device()
        device.apply(pad.NEUTRAL.pressing("cross"))
        self.assertEqual(kernel.events(),
                         [(pad.EV_KEY, pad.BTN_SOUTH, 1),
                          (pad.EV_SYN, pad.SYN_REPORT, 0)])

    def test_applying_the_same_state_twice_writes_once(self):
        device, kernel = self._device()
        state = pad.NEUTRAL.pressing("cross")
        device.apply(state)
        device.apply(state)
        self.assertEqual(len(kernel.events()), 2)

    def test_one_frame_is_written_in_one_call(self):
        # Two writes could be read as two frames, which would show a stick
        # move half-applied.
        device, kernel = self._device()
        writes = []
        device._kernel = pad.Kernel(
            open=kernel.open, ioctl=kernel.ioctl,
            write=lambda fd, blob: writes.append(blob) or len(blob),
            close=kernel.close)
        device.apply(pad.NEUTRAL.pressing("cross").with_stick("left", 1.0, 1.0))
        self.assertEqual(len(writes), 1)

    def test_closing_destroys_the_device_and_the_descriptor(self):
        device, kernel = self._device()
        device.close()
        self.assertEqual(kernel.requests()[-1], pad.UI_DEV_DESTROY)
        self.assertEqual(kernel.closed, [kernel.fd])

    def test_closing_twice_is_harmless(self):
        device, _ = self._device()
        device.close()
        device.close()

    def test_using_a_closed_device_is_an_error_not_a_bad_descriptor(self):
        device, _ = self._device()
        device.close()
        with self.assertRaises(pad.PadError):
            device.apply(pad.NEUTRAL.pressing("cross"))


class DeviceFailures(unittest.TestCase):
    """The first thing anyone sees from this module, so it must name the fix."""

    def test_a_missing_node_blames_the_module_and_gives_the_command(self):
        missing = "/nonexistent/uinput"
        with self.assertRaises(pad.PadError) as caught:
            pad.UinputDevice(path=missing)
        message = str(caught.exception)
        self.assertIn("modprobe uinput", message)
        self.assertIn(missing, message)

    def test_permission_denied_names_the_udev_rule_and_the_group(self):
        kernel = FakeKernel(open_error=PermissionError(
            errno.EACCES, "Permission denied"))
        with self.assertRaises(pad.PadError) as caught:
            pad.UinputDevice(kernel=kernel.as_kernel())
        message = str(caught.exception)
        self.assertIn("99-uinput.rules", message)
        self.assertIn("usermod -aG input", message)

    def test_permission_denied_mentions_the_re_login(self):
        # The step everyone misses: an SSH session opened before the usermod
        # keeps the old group list and fails identically afterwards.
        kernel = FakeKernel(open_error=PermissionError(errno.EACCES, "nope"))
        with self.assertRaises(pad.PadError) as caught:
            pad.UinputDevice(kernel=kernel.as_kernel())
        self.assertIn("log out", str(caught.exception))

    def test_a_failure_is_a_paderror_not_an_oserror(self):
        # A traceback out of fcntl tells the operator nothing they can act on.
        kernel = FakeKernel(open_error=PermissionError(errno.EACCES, "nope"))
        with self.assertRaises(pad.PadError):
            pad.UinputDevice(kernel=kernel.as_kernel())
        with self.assertRaises(pad.PadError):
            pad.UinputDevice(path="/nonexistent/uinput")

    def test_a_setup_ioctl_failure_closes_the_descriptor(self):
        kernel = FakeKernel()

        def failing_ioctl(fd, request, argument=0):
            if request == pad.UI_DEV_SETUP:
                raise OSError(errno.EINVAL, "Invalid argument")
            return kernel.ioctl(fd, request, argument)

        broken = pad.Kernel(open=kernel.open, ioctl=failing_ioctl,
                            write=kernel.write, close=kernel.close)
        with self.assertRaises(pad.PadError) as caught:
            pad.UinputDevice(kernel=broken)
        self.assertIn("4.5", str(caught.exception))
        self.assertEqual(kernel.closed, [kernel.fd])

    def test_a_write_failure_is_reported_as_a_pad_error(self):
        kernel = FakeKernel(write_error=OSError(errno.ENODEV, "No such device"))
        device = pad.UinputDevice(kernel=kernel.as_kernel(),
                                  sleep=lambda _s: None)
        with self.assertRaises(pad.PadError):
            device.apply(pad.NEUTRAL.pressing("cross"))

    def test_diagnose_is_quiet_when_the_node_opens(self):
        kernel = FakeKernel()
        self.assertIsNone(pad.diagnose(kernel=kernel.as_kernel()))
        self.assertEqual(kernel.closed, [kernel.fd])
        self.assertEqual(kernel.ioctls, [])      # no device is created

    def test_diagnose_reports_the_same_advice_as_the_constructor(self):
        self.assertIn("modprobe uinput", pad.diagnose("/nonexistent/uinput"))


# -- the pad -------------------------------------------------------------


class PadApi(unittest.TestCase):
    def _pad(self):
        device = pad.RecordingDevice()
        clock = pad.ManualClock()
        return pad.Pad(device=device, clock=clock), device, clock

    def test_a_press_goes_down_and_comes_back_up(self):
        harness, device, clock = self._pad()
        harness.press("cross", frames=2)
        self.assertEqual([sorted(s.buttons) for s in device.states],
                         [[], ["cross"], []])
        self.assertEqual(clock.waits, [0, 2])

    def test_a_press_is_relative_to_the_current_frame(self):
        harness, _, clock = self._pad()
        clock.advance(500)
        harness.press("start", frames=3)
        self.assertEqual(clock.waits, [500, 503])

    def test_a_script_runs_in_order(self):
        harness, device, clock = self._pad()
        harness.sequence([(0, "cross", 2), (12, "triangle", 2)])
        self.assertEqual(clock.waits, [0, 2, 12, 14])
        self.assertEqual([sorted(s.buttons) for s in device.states],
                         [[], ["cross"], [], ["triangle"], []])

    def test_a_hold_survives_a_script_and_the_script_returns_to_it(self):
        harness, device, _ = self._pad()
        harness.hold("r1")
        harness.press("cross", frames=2)
        self.assertEqual([sorted(s.buttons) for s in device.states],
                         [[], ["r1"], ["cross", "r1"], ["r1"]])
        self.assertEqual(harness.base.buttons, frozenset({"r1"}))

    def test_release_undoes_a_hold(self):
        harness, _, _ = self._pad()
        harness.hold("r1")
        harness.release("r1")
        self.assertEqual(harness.base, pad.NEUTRAL)

    def test_releasing_something_never_held_is_not_an_error(self):
        harness, _, _ = self._pad()
        harness.release("r1")
        self.assertEqual(harness.base, pad.NEUTRAL)

    def test_a_stick_stays_where_it_was_put(self):
        harness, device, _ = self._pad()
        harness.stick("left", 0.0, -1.0)
        self.assertEqual(device.states[-1].left, (128, 0))
        self.assertEqual(harness.base.left, (128, 0))

    def test_neutral_clears_everything(self):
        harness, _, _ = self._pad()
        harness.hold("r1")
        harness.stick("left", 1.0, 1.0)
        harness.neutral()
        self.assertEqual(harness.base, pad.NEUTRAL)

    def test_an_unknown_button_never_reaches_the_device(self):
        harness, device, _ = self._pad()
        before = len(device.states)
        with self.assertRaises(ValueError):
            harness.press("a")
        with self.assertRaises(ValueError):
            harness.hold("a")
        self.assertEqual(len(device.states), before)

    def test_closing_releases_everything_first(self):
        # A button still down when the harness exits is held into whatever
        # runs next, and a stuck cross on a play-call screen is a hard thing
        # to attribute afterwards.
        harness, device, _ = self._pad()
        harness.hold("cross")
        harness.close()
        self.assertEqual(device.states[-1], pad.NEUTRAL)
        self.assertTrue(device.closed)

    def test_the_context_manager_closes(self):
        device = pad.RecordingDevice()
        with pad.Pad(device=device, clock=pad.ManualClock()) as harness:
            harness.hold("start")
        self.assertTrue(device.closed)
        self.assertEqual(device.states[-1], pad.NEUTRAL)

    def test_a_pad_starts_neutral(self):
        _, device, _ = self._pad()
        self.assertEqual(device.states, [pad.NEUTRAL])

    def test_slip_is_exposed_for_a_trial_to_record(self):
        # The guarantee is "within a frame or two", so the run has to carry
        # evidence of which it was.
        harness, _, clock = self._pad()
        clock.advance(5)
        harness.press("cross", frames=2)
        self.assertEqual(harness.slip, clock.slip)
        self.assertEqual(len(harness.slip), 2)

    def test_slip_survives_a_clock_that_does_not_report_it(self):
        class Bare:
            def frame(self):
                return 0

            def wait_until(self, frame):
                pass

        harness = pad.Pad(device=pad.RecordingDevice(), clock=Bare())
        self.assertEqual(harness.slip, [])

    def test_it_drives_a_real_device_end_to_end(self):
        kernel = FakeKernel()
        device = pad.UinputDevice(kernel=kernel.as_kernel(),
                                  sleep=lambda _s: None)
        harness = pad.Pad(device=device, clock=pad.ManualClock())
        harness.press("cross", frames=2)
        harness.close()
        self.assertEqual(kernel.events(),
                         [(pad.EV_KEY, pad.BTN_SOUTH, 1),
                          (pad.EV_SYN, pad.SYN_REPORT, 0),
                          (pad.EV_KEY, pad.BTN_SOUTH, 0),
                          (pad.EV_SYN, pad.SYN_REPORT, 0)])


# -- CLI -----------------------------------------------------------------


class Cli(unittest.TestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = pad.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_buttons_lists_the_namespace(self):
        code, out, _ = self._run(["--buttons"])
        self.assertEqual(code, 0)
        self.assertIn("cross", out)
        self.assertIn("left:X,Y", out)

    def test_check_fails_with_the_setup_advice(self):
        code, _, err = self._run(["--check", "--path", "/nonexistent/uinput"])
        self.assertEqual(code, 2)
        self.assertIn("modprobe uinput", err)

    def test_a_dry_run_prints_the_plan_and_opens_nothing(self):
        code, out, _ = self._run(["--dry-run", "--press", "cross",
                                  "--frames", "3"])
        self.assertEqual(code, 0)
        self.assertIn("frame +0", out)
        self.assertIn("frame +3", out)

    def test_a_dry_run_understands_a_stick(self):
        code, out, _ = self._run(["--dry-run", "--press", "left:0.0,-1.0",
                                  "--frames", "4"])
        self.assertEqual(code, 0)
        self.assertIn("left=(128, 0)", out)

    def test_a_bad_button_is_an_error_not_a_traceback(self):
        code, _, err = self._run(["--dry-run", "--press", "a"])
        self.assertEqual(code, 2)
        self.assertIn("cross", err)

    def test_a_malformed_stick_is_an_error(self):
        code, _, err = self._run(["--dry-run", "--press", "left:1.0"])
        self.assertEqual(code, 2)
        self.assertIn("left:0.0,-1.0", err)

    def test_doing_nothing_is_refused(self):
        with self.assertRaises(SystemExit):
            self._run([])


if __name__ == "__main__":
    unittest.main()

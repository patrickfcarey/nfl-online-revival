r"""Layer 2: synthetic controller input, frame-relative, with no hands on the pad.

Every experiment this harness will run has the same shape -- snap, wait twelve
frames, throw -- so input has to be expressible against a frame number rather
than against wall time, and it has to be repeatable a few hundred times without
a human. The design doc offers two mechanisms. This module implements the
first and rejects the second, and the reasoning is here rather than in a commit
message because a future reader must not have to redo it.

## Chosen: a virtual gamepad on `/dev/uinput`

The emulator sees an ordinary Linux input device and binds it exactly as it
binds real hardware. Everything below the kernel boundary is the emulator's
normal, well-travelled input path, so nothing here depends on emulator
internals staying still.

What made it the safe choice, read out of the rig's own PCSX2 fork at
`/mnt/c/GitHub/pcsx2-VR` (HEAD `63ef1605`):

* Linux has exactly one controller backend -- SDL. `InputSourceType` is
  `Keyboard, Pointer, SDL` with `DInput`/`XInput` behind `#ifdef _WIN32`
  (`pcsx2/Input/InputManager.h:21-32`), and there is no evdev source in the
  tree. SDL3 is initialised with `SDL_INIT_JOYSTICK | SDL_INIT_GAMEPAD |
  SDL_INIT_HAPTIC` (`pcsx2/Input/SDLInputSource.cpp:659`).
* Hot-plug works, so the device may be created after the emulator started and
  survives it being restarted. `SDLInputSource::PollEvents` drains
  `SDL_PollEvent` every frame (`SDLInputSource.cpp:700-710`) and handles
  `SDL_EVENT_GAMEPAD_ADDED`; critically it *also* handles
  `SDL_EVENT_JOYSTICK_ADDED` and opens the device as a plain joystick when
  `SDL_IsGamepad` is false (`SDLInputSource.cpp:1168-1206`). A synthetic
  device that no mapping database has ever heard of is therefore still
  bindable -- it just binds by axis and button index.
* The emulator already injects synthetic pad state this way internally. Its
  input-recording feature overwrites `PadBase` once per vsync, right after
  real input is polled (`pcsx2/VMManager.cpp:2955-2971`,
  `pcsx2/Recording/PadData.cpp:95-118`). That is the same pipeline our events
  ride, one layer further out.

## Rejected: poking the pad buffer in EE memory

This one is worth spelling out because it *sounds* more deterministic, and
because the first half of the investigation makes it look viable.

The game uses stock Sony libpad. `extract/SLUS_207.52` carries the strings
`PsIIlibpad  2700`, `PADMAN.IRX` and `SIO2MAN.IRX`, and its `scePadPortOpen`
at `0x00502CB8` is the shipped library function -- same `andi v0, s4, 0x3F`
alignment test, same "buffer addr is not 64 byte align" complaint at
`0x005FCA78`. Its only caller, `0x0047B1A8`, opens all eight port/slot pairs
in a loop with buffers at `0x00638600 + (i << 8)`, and the per-pad state table
sits at `0x00657490`, stride `0x70` by port and `0x1C` by slot.

Those buffers are real and they are findable in the live image. Scanning
`extract/ee_mainmenu.bin` for the DualShock2 signature `00 79 ff ff` gives
four matches, and exactly two of them fall inside the region that loop
allocated: `0x00638640` and `0x006386C0` -- the two 128-byte halves of the
port 0, slot 0 buffer, pad data at `+0x40` in each half. The other six
buffers are zeroed, which is what one controller in port 1 looks like::

    00638640  00 79 ff ff 7f 7b 84 7c
              |  |  |     |     +-- left stick X, Y
              |  |  |     +-- right stick X, Y
              |  |  +-- buttons, 16 bits, active low (0xFFFF = nothing held)
              |  +-- 0x79: analogue mode, nine halfwords
              +-- status

The stick bytes are `7f 7b 84 7c`, not a clean `80 80 80 80`, which is the
tell that this is a live pad's drift and not something the game initialised.

So the address is known, the layout is known, and PINE can write it. It still
does not work, for four reasons, any one of which is fatal:

1. **The buffer is overwritten by the emulator, on the game's instruction.**
   PCSX2's SIO2 and pad code never touches EE RAM -- `grep -rn
   "memWrite\|eeMem\|vtlb_\|iopMem" pcsx2/SIO/` returns nothing, and the pad
   FIFOs are host-side `std::deque<u8>` (`pcsx2/SIO/Sio2.h:67-69`). But the
   IOP's `padman.irx` runs for real and ships the result to the EE over SIF0,
   and *that* is a write into EE RAM at a guest-chosen address:
   `sif0.fifo.read((u32*)ptag, readSize << 2)` where `ptag` comes from
   `sif0ch.madr` (`pcsx2/Sif0.cpp:23-40`, chain tags at `:88`). `madr` traces
   back to the pointer the game handed `scePadPortOpen` -- `0x00638600`. The
   emulator does not know or care what that address means; it copies over it
   every poll regardless.
2. **The buffer is double-buffered and the parity is not observable.** libpad
   alternates the two 128-byte halves. A poke must hit the half the game is
   about to read, and nothing readable from outside says which that is.
3. **A poke cannot be timed.** PINE has no pause, no resume and no frame step
   -- the opcode table stops at `MsgStatus = 0xF` (`pcsx2/PINE.cpp:144-163`),
   which is why `emu.pause()` refuses rather than pretending. `ParseCommand`
   also runs on the PINE server thread with no lock against the EE thread
   (`PINE.cpp:511-756`). So the write lands at an unknown point in the frame,
   in a race with both the SIF DMA and the game's own read of the buffer.
   There is no window to aim at.
4. **The emulator's own authors already measured this.** The VR fork's
   `pcsx2/VR/PadLook.h` exists because writing camera state into memory
   "gets recomputed away" -- proven live on NFL 2K5 -- and injecting at the
   pad level "rides the game's own input pipeline instead of fighting it".
   Same conclusion, from people with a debugger attached.

A fifth mechanism was considered and is out of reach rather than wrong:
PCSX2's input recording (`EmuConfig.EnableRecordingTools`) is the cleanest
possible seam, a per-vsync override of `PadBase` -- but it is in-process, and
PINE exposes no command that reaches it (`PINE.cpp:144-163` again). If the rig
ever runs a fork with a pad opcode, that is the mechanism to switch to, and
`Pad` is written against a device seam so only :class:`UinputDevice` would
change.

## Frame synchronisation, which is the part that will break first

Read these as assumptions, not as facts, because none of them can be checked
without a running emulator:

* **A frame is 1/59.94 s.** NTSC PS2. A PAL disc or a rig running unthrottled
  or in fast-forward invalidates every duration in every trial script.
* **Host input is latched once per emulated frame,** at `VSyncStart` ->
  `PollInputOnCPUThread` -> `InputManager::PollSources`
  (`pcsx2/Counters.cpp:487-501`, `pcsx2/VMManager.cpp:2951`). An event written
  to the device and withdrawn inside one frame can be missed entirely, and the
  latency from `write()` to that latch is at least one poll. This is why
  :meth:`Pad.press` defaults to two frames and refuses zero.
* **The game polls the pad on its own schedule, not the emulator's.** The
  emulator imposes nothing; `padman` transfers whenever the guest writes SIO2.
  Once per frame is the normal case for a PS2 title, and the two frames above
  assume it, but it is a guess until measured.
* **The default clock is wall time and is therefore open-loop.**
  :class:`WallClock` counts frames from `time.monotonic`, which drifts against
  the emulator immediately if the rig cannot hold full speed -- and a lab rig
  running a headset on the same GPU frequently cannot. It is the default only
  because nothing better exists yet.
* **The closed-loop clock needs an address nobody has confirmed.**
  :class:`MemoryClock` polls a per-frame counter in EE memory through layer 1
  and is the only construct here that is actually synchronised with
  emulation. `docs/` points at `[[0x00601280] + 84]` for frames-since-snap,
  which is a pointer chase, so the clock takes a `chain` of offsets; layer 3
  is confirming it. It refuses to run uncalibrated:
  :meth:`MemoryClock.calibrate` measures the observed rate and rejects an
  address that is not advancing at something like frame rate. Confirming that
  address is the highest-value open item for this layer.
* **A frames-since-snap counter resets.** If the address layer 3 lands on
  counts from the snap rather than from boot, it goes backwards every play.
  :class:`MemoryClock` treats a decrease as a reset and says so rather than
  waiting out its timeout, because "the counter went backwards" and "the
  emulator is wedged" want different responses from the operator.

**The guarantee this layer offers is "within a frame or two", never
"frame-exact".** Nothing available can do better. Stepping the emulator is
impossible -- there is no frame-advance opcode -- so every clock here is a
poll that discovers a frame boundary has passed rather than one that stops on
it. Both real clocks therefore record what they actually did: `clock.slip` is
one entry per wait, in frames late, and a trial that does not record it
alongside its results is reporting a timing it did not measure. A slip of 0
or 1 is the expected case; a run whose slips climb is a rig that has stopped
holding full speed, and its durations mean nothing.

Frame offsets in a script are relative to the frame :meth:`Pad.sequence` was
called on, so a script is portable between trials. Absolute frame numbers
never appear in a script.

## One-time operator setup

`/dev/uinput` ships root-only (`crw------- root root`) on Ubuntu, so this is
not optional. On the rig, once::

    sudo modprobe uinput
    echo uinput | sudo tee /etc/modules-load.d/uinput.conf
    printf 'KERNEL=="uinput", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"\\n' \\
        | sudo tee /etc/udev/rules.d/99-uinput.rules
    sudo usermod -aG input "$USER"
    sudo udevadm control --reload-rules && sudo udevadm trigger

Then log out and back in -- group membership is only picked up on a new login
session, and an SSH session that was already open will still fail. Check with
``python3 -m tools.madden_lab.pad --check``.

After that, once per PCSX2 profile: start the emulator, run
``python3 -m tools.madden_lab.pad --hold start --seconds 30`` to make the
device exist and visible, and bind it in Settings -> Controllers. Two
warnings on that step:

* Bind with **no other controller plugged in**. The fork stores bindings
  against the SDL player index as `SDL-%d` (`SDLInputSource.cpp:713`), and its
  own notes flag that as drift-prone; a second pad appearing first will shift
  the index and silently rebind the trial to nothing.
* If the emulator lists the device as a joystick rather than a gamepad, the
  buttons appear as indices with no names. That is expected -- see
  `SDL_EVENT_JOYSTICK_ADDED` above -- and binding by index works fine.
"""

from __future__ import annotations

import argparse
import collections.abc
import errno
import fcntl
import os
import struct
import sys
import time
from typing import (Callable, Dict, FrozenSet, List, NamedTuple, Optional,
                    Sequence, Tuple, Union)

#: NTSC PS2. Every duration in every trial script is denominated in these.
NTSC_FPS = 60000.0 / 1001.0          # 59.94005994...

UINPUT_PATH = "/dev/uinput"

#: pid.codes' community vendor id, which exists precisely so prototypes do not
#: squat on a real vendor's range. Deliberately not a Sony id: claiming to be
#: a DualShock would invite SDL to apply a mapping written for a different
#: axis layout, and a wrong mapping is harder to diagnose than no mapping.
# Microsoft Xbox 360 pad. Deliberate impersonation, and the reason matters:
# SDL only assigns an `SDL-N` gamepad index to devices its controller database
# recognises by vendor/product. Advertising an unregistered id (this was
# 0x1209/0x2004, the generic "prototype" pair) leaves the device visible as a
# *joystick* and absent from the gamepad list -- so an emulator binding of
# `SDL-0/FaceSouth` silently resolves to whatever real pad is plugged in, and
# every synthetic press goes to a device nobody reads. That failure looks
# exactly like a dead binding and cost an evening.
DEFAULT_VENDOR = 0x045E
DEFAULT_PRODUCT = 0x028E
DEFAULT_VERSION = 0x0114
DEFAULT_NAME = "madden-lab virtual pad"

#: PS2 analogue range. Kept as the device's native axis range so a stick value
#: passes through to the game without being resampled anywhere: 0x80 centre,
#: 0x00 left/up, 0xFF right/down, exactly as the bytes at 0x00638644 read.
AXIS_MIN = 0
AXIS_MAX = 255
AXIS_CENTRE = 128


class PadError(Exception):
    """The pad device is unusable, or the emulator side of it is not set up."""


# -- evdev and uinput ABI ------------------------------------------------
#
# Spelled out rather than imported because there is no `evdev` or `uinput`
# module on the rig and this whole repository is stdlib-only. Values checked
# against /usr/include/linux/{input,input-event-codes,uinput}.h.

EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
SYN_REPORT = 0

BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 0x130, 0x131, 0x133, 0x134
BTN_TL, BTN_TR, BTN_TL2, BTN_TR2 = 0x136, 0x137, 0x138, 0x139
BTN_SELECT, BTN_START = 0x13A, 0x13B
BTN_THUMBL, BTN_THUMBR = 0x13D, 0x13E

ABS_X, ABS_Y, ABS_Z = 0x00, 0x01, 0x02
ABS_RX, ABS_RY, ABS_RZ = 0x03, 0x04, 0x05
ABS_HAT0X, ABS_HAT0Y = 0x10, 0x11

BUS_USB = 0x03

_IOC_WRITE = 1


def _ioc(direction: int, kind: str, number: int, size: int) -> int:
    """Rebuild the kernel's `_IOC` macro, so the constants are checkable."""
    return (direction << 30) | (size << 16) | (ord(kind) << 8) | number


#: `struct uinput_setup` is 92 bytes and `struct uinput_abs_setup` 28; both
#: sizes are encoded into the ioctl number, so a mismatch is -EINVAL rather
#: than a corrupt device.
_SETUP_FORMAT = "@HHHH80sI"          # struct input_id, name, ff_effects_max
_ABS_SETUP_FORMAT = "@H2x6i"         # code, then struct input_absinfo
_EVENT_FORMAT = "@llHHi"             # struct input_event

UI_DEV_CREATE = _ioc(0, "U", 1, 0)
UI_DEV_DESTROY = _ioc(0, "U", 2, 0)
UI_DEV_SETUP = _ioc(_IOC_WRITE, "U", 3, struct.calcsize(_SETUP_FORMAT))
UI_ABS_SETUP = _ioc(_IOC_WRITE, "U", 4, struct.calcsize(_ABS_SETUP_FORMAT))
UI_SET_EVBIT = _ioc(_IOC_WRITE, "U", 100, 4)
UI_SET_KEYBIT = _ioc(_IOC_WRITE, "U", 101, 4)
UI_SET_ABSBIT = _ioc(_IOC_WRITE, "U", 103, 4)


# -- the button namespace ------------------------------------------------

CROSS, CIRCLE, SQUARE, TRIANGLE = "cross", "circle", "square", "triangle"
L1, R1, L2, R2 = "l1", "r1", "l2", "r2"
L3, R3 = "l3", "r3"
START, SELECT = "start", "select"
UP, DOWN, LEFT, RIGHT = "up", "down", "left", "right"

LEFT_STICK, RIGHT_STICK = "left", "right"
STICKS = (LEFT_STICK, RIGHT_STICK)

#: Face buttons are positional in evdev -- south is the bottom one -- which is
#: how the kernel's own DualShock drivers report them, so a pad bound by name
#: and a pad bound by index agree.
_KEY_CODE: Dict[str, int] = {
    CROSS: BTN_SOUTH, CIRCLE: BTN_EAST, TRIANGLE: BTN_NORTH, SQUARE: BTN_WEST,
    L1: BTN_TL, R1: BTN_TR, L2: BTN_TL2, R2: BTN_TR2,
    SELECT: BTN_SELECT, START: BTN_START, L3: BTN_THUMBL, R3: BTN_THUMBR,
}

#: The d-pad is a hat, not four keys. Real DualShocks report it that way
#: through `hid-playstation`, and emitting both forms would let an emulator
#: bound to both count one press twice.
_DPAD = (UP, DOWN, LEFT, RIGHT)

#: L2 and R2 are pressure-sensitive on a DualShock2 and Madden uses them as
#: switches, so they go out as a digital button *and* a saturated axis -- which
#: is also what a real DS4 does, and means either binding style works.
_TRIGGER_AXIS = {L2: ABS_Z, R2: ABS_RZ}

BUTTONS: Tuple[str, ...] = tuple(sorted(_KEY_CODE)) + _DPAD


def button_code(name: str) -> int:
    """The evdev key code for a digital button, or a loud complaint."""
    try:
        return _KEY_CODE[name]
    except (KeyError, TypeError):
        raise ValueError(_unknown(name, BUTTONS, "button")) from None


def _unknown(name: object, valid: Sequence[str], kind: str) -> str:
    # Naming the alternatives matters more here than anywhere else in the
    # harness: a typo in a trial script otherwise fails two hundred iterations
    # in, with the run already half spent.
    close = [v for v in valid if isinstance(name, str)
             and (v.startswith(name[:1]) or name in v)]
    hint = ("; did you mean %s?" % " or ".join(repr(c) for c in close[:3])
            if close else "")
    return ("%r is not a %s. Valid names: %s%s"
            % (name, kind, ", ".join(valid), hint))


def axis_from_unit(value: float) -> int:
    """A stick coordinate in -1.0..1.0 as the PS2's own 0..255 byte.

    -1 is left or up, matching both evdev and the raw pad byte, so `y=-1` is
    forward. Exact at the ends and at centre: -1 -> 0, 0 -> 128, 1 -> 255.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("stick value must be a number, got %r"
                         % (value,)) from None
    if not -1.0 <= number <= 1.0:
        raise ValueError("stick value must be within -1.0..1.0, got %r"
                         % (value,))
    return max(AXIS_MIN, min(AXIS_MAX, int(round((number + 1.0) * 127.5))))


class Stick(NamedTuple):
    """A stick deflection, usable anywhere a button name is."""

    which: str
    x: float
    y: float

    def validated(self) -> "Stick":
        if self.which not in STICKS:
            raise ValueError(_unknown(self.which, STICKS, "stick"))
        axis_from_unit(self.x)
        axis_from_unit(self.y)
        return self


class PadState(NamedTuple):
    """Everything the pad reports on one frame. Immutable, so it can be diffed.

    Analogue values are stored as PS2 bytes rather than floats: two states that
    should be identical must compare equal, and 0.3 does not round-trip.
    """

    buttons: FrozenSet[str] = frozenset()
    left: Tuple[int, int] = (AXIS_CENTRE, AXIS_CENTRE)
    right: Tuple[int, int] = (AXIS_CENTRE, AXIS_CENTRE)

    def pressing(self, *names: str) -> "PadState":
        for name in names:
            _check_button(name)
        return self._replace(buttons=self.buttons | frozenset(names))

    def releasing(self, *names: str) -> "PadState":
        for name in names:
            _check_button(name)
        return self._replace(buttons=self.buttons - frozenset(names))

    def with_stick(self, which: str, x: float, y: float) -> "PadState":
        Stick(which, x, y).validated()
        moved = (axis_from_unit(x), axis_from_unit(y))
        return (self._replace(left=moved) if which == LEFT_STICK
                else self._replace(right=moved))

    @property
    def hat(self) -> Tuple[int, int]:
        """The d-pad as one hat, because that is what a real pad reports.

        Up and down at once is not representable and is not representable on
        hardware either; opposing directions cancel rather than raising, so a
        script that holds `left` and taps `right` behaves like a thumb.
        """
        x = (RIGHT in self.buttons) - (LEFT in self.buttons)
        y = (DOWN in self.buttons) - (UP in self.buttons)
        return (x, y)


def _check_button(name: str) -> None:
    if name not in _KEY_CODE and name not in _DPAD:
        raise ValueError(_unknown(name, BUTTONS, "button"))


NEUTRAL = PadState()


def evdev_report(previous: PadState,
                 state: PadState) -> List[Tuple[int, int, int]]:
    """The `(type, code, value)` events that turn `previous` into `state`.

    Only differences are emitted -- the kernel filters unchanged values anyway,
    but a report that is empty except for the SYN is a wasted wakeup on a path
    that runs sixty times a second. A non-empty report always ends with
    SYN_REPORT, which is what makes the frame atomic to the reader.
    """
    events: List[Tuple[int, int, int]] = []

    for name in sorted(_KEY_CODE):
        was, now = name in previous.buttons, name in state.buttons
        if was != now:
            events.append((EV_KEY, _KEY_CODE[name], int(now)))
        if name in _TRIGGER_AXIS and was != now:
            events.append((EV_ABS, _TRIGGER_AXIS[name],
                           AXIS_MAX if now else AXIS_MIN))

    old_hat, new_hat = previous.hat, state.hat
    for axis, before, after in ((ABS_HAT0X, old_hat[0], new_hat[0]),
                                (ABS_HAT0Y, old_hat[1], new_hat[1])):
        if before != after:
            events.append((EV_ABS, axis, after))

    for axes, before, after in (((ABS_X, ABS_Y), previous.left, state.left),
                                ((ABS_RX, ABS_RY), previous.right,
                                 state.right)):
        for axis, was_value, value in zip(axes, before, after):
            if was_value != value:
                events.append((EV_ABS, axis, value))

    if events:
        events.append((EV_SYN, SYN_REPORT, 0))
    return events


def pack_event(kind: int, code: int, value: int) -> bytes:
    """One `struct input_event`. The timestamp is the kernel's to fill in."""
    return struct.pack(_EVENT_FORMAT, 0, 0, kind, code, value)


# -- the scheduler -------------------------------------------------------

#: What a script entry may name: one button, one stick move, or several of
#: either pressed together.
Action = Union[str, Stick]
Entry = Union[Tuple[int, Action], Tuple[int, Action, int],
              Tuple[int, Sequence[Action]], Tuple[int, Sequence[Action], int]]

DEFAULT_PRESS_FRAMES = 2


class _Span(NamedTuple):
    begin: int
    end: int
    order: int
    action: Action


def schedule(script: Sequence[Entry], start: int = 0,
             base: PadState = NEUTRAL) -> List[Tuple[int, PadState]]:
    """Turn a script into `(frame, state)` transitions, absolute and sorted.

    A script entry is `(frame_offset, action)` or
    `(frame_offset, action, duration)`, where an action is a button name, a
    :class:`Stick`, or a sequence of those held together. Offsets are relative
    to `start`; durations are in frames and the interval is half-open, so
    `(0, "cross", 2)` presses on frames 0 and 1 and is released on frame 2.

    `base` is the state held outside the script -- whatever :meth:`Pad.hold`
    left set -- so a script composes with a hold instead of stamping over it,
    and the last transition returns to `base` rather than to nothing.

    Only frames where the state actually changes appear in the result. That
    keeps a script that reaches frame 3000 to a handful of writes.
    """
    spans = _spans(script)
    _reject_touching_presses(spans)
    if not spans:
        return []

    boundaries = sorted({s.begin for s in spans} | {s.end for s in spans})
    transitions: List[Tuple[int, PadState]] = []
    previous = base
    for frame in boundaries:
        state = _state_at(spans, frame, base)
        if state != previous:
            transitions.append((start + frame, state))
            previous = state
    return transitions


def _spans(script: Sequence[Entry]) -> List[_Span]:
    if isinstance(script, (str, bytes)) or not isinstance(
            script, collections.abc.Sequence):
        raise ValueError("a script is a sequence of entries, got %r"
                         % (script,))
    spans: List[_Span] = []
    for index, entry in enumerate(script):
        if isinstance(entry, (str, bytes)) or not isinstance(
                entry, collections.abc.Sequence):
            raise ValueError("script entry %d is not a tuple: %r"
                             % (index, entry))
        if len(entry) == 2:
            offset, action, duration = entry[0], entry[1], DEFAULT_PRESS_FRAMES
        elif len(entry) == 3:
            offset, action, duration = entry
        else:
            raise ValueError(
                "script entry %d must be (frame, action) or "
                "(frame, action, duration), got %d fields" % (index, len(entry)))

        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("script entry %d has frame offset %r; offsets are "
                             "whole frames from 0" % (index, offset))
        if (not isinstance(duration, int) or isinstance(duration, bool)
                or duration < 1):
            raise ValueError(
                "script entry %d has duration %r; a press must last at least "
                "one frame, and one frame may be missed entirely because the "
                "emulator latches input once per frame -- prefer %d"
                % (index, duration, DEFAULT_PRESS_FRAMES))

        for action in _actions(action, index):
            spans.append(_Span(offset, offset + duration, index, action))
    return spans


def _actions(action: object, index: int) -> List[Action]:
    if isinstance(action, Stick):
        return [action.validated()]
    if isinstance(action, str):
        _check_button(action)
        return [action]
    if isinstance(action, Sequence):
        out: List[Action] = []
        for item in action:
            out.extend(_actions(item, index))
        return out
    raise ValueError("script entry %d holds %r, which is neither a button "
                     "name nor a Stick" % (index, action))


def _reject_touching_presses(spans: Sequence[_Span]) -> None:
    """Two presses of one button with no gap are one press. Say so.

    The pad reports state per frame, so a release on frame 2 and a press on
    frame 2 collapse into a single continuous hold and the game never sees the
    edge. Silently turning two taps into one is exactly the kind of thing that
    would be blamed on the engine for a week.
    """
    by_button: Dict[str, List[_Span]] = {}
    for span in spans:
        if isinstance(span.action, str):
            by_button.setdefault(span.action, []).append(span)
    for name, group in by_button.items():
        group = sorted(group, key=lambda s: (s.begin, s.end))
        for earlier, later in zip(group, group[1:]):
            if later.begin <= earlier.end:
                raise ValueError(
                    "%r is held over frames %d-%d and again over %d-%d with no "
                    "frame between them, so the game sees one press rather "
                    "than two. Leave at least one frame of gap."
                    % (name, earlier.begin, earlier.end,
                       later.begin, later.end))


def _state_at(spans: Sequence[_Span], frame: int, base: PadState) -> PadState:
    state = base
    active = [s for s in spans if s.begin <= frame < s.end]
    for span in active:
        if isinstance(span.action, str):
            state = state.pressing(span.action)
    # A later deflection of the same stick wins for as long as it lasts, which
    # is how steering reads when written as overlapping nudges.
    for which in STICKS:
        moves = [s for s in active
                 if isinstance(s.action, Stick) and s.action.which == which]
        if moves:
            latest = max(moves, key=lambda s: (s.begin, s.order))
            state = state.with_stick(which, latest.action.x, latest.action.y)
    return state


# -- clocks --------------------------------------------------------------
#
# A clock is anything with `frame() -> int`, `wait_until(frame) -> None` and a
# `slip` list of how many frames late each wait actually returned. `Pad` never
# looks at anything else, so layer 1 can grow a better one -- or a fork can
# grow a frame-advance opcode -- without this module changing.


class WallClock:
    """Frames counted off `time.monotonic`. Open-loop; see the module docstring.

    Correct only while the emulator holds full speed, and it cannot tell that
    it has stopped being correct: its `slip` measures lateness against its own
    idea of time, not against the emulator's. Use :class:`MemoryClock` for
    anything whose result will be quoted as a number of frames.
    """

    def __init__(self, fps: float = NTSC_FPS,
                 now: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if fps <= 0:
            raise ValueError("fps must be positive, got %r" % (fps,))
        self.fps = fps
        self.slip: List[int] = []
        self._now = now
        self._sleep = sleep
        self._epoch = now()

    def frame(self) -> int:
        return int((self._now() - self._epoch) * self.fps)

    def wait_until(self, frame: int) -> None:
        target = self._epoch + frame / self.fps
        while True:
            gap = target - self._now()
            if gap <= 0:
                break
            self._sleep(gap)
        self.slip.append(self.frame() - frame)


class ManualClock:
    """A clock driven by the caller. For dry runs and for tests."""

    def __init__(self, start: int = 0) -> None:
        self._frame = start
        self.waits: List[int] = []
        self.slip: List[int] = []

    def frame(self) -> int:
        return self._frame

    def advance(self, frames: int = 1) -> int:
        self._frame += frames
        return self._frame

    def wait_until(self, frame: int) -> None:
        self.waits.append(frame)
        self.slip.append(max(0, self._frame - frame))
        self._frame = max(self._frame, frame)


class MemoryClock:
    """Frames read out of EE memory through layer 1. The only closed loop here.

    `address` must hold a counter the game advances once per frame, reached
    through `chain` if it lives behind a pointer -- `docs/` points at
    `[[0x00601280] + 84]`, which is `MemoryClock(emu, 0x00601280, chain=(84,))`.
    No such address is confirmed yet, which is why this is not the default;
    :meth:`calibrate` is the tool for confirming one, and it refuses an address
    that is not advancing at something like frame rate.

    One read per poll, so it costs one PINE round trip per poll. That is
    affordable at 59.94 Hz on a Unix socket; a sampler that wants many
    addresses per frame should use layer 1's batched read instead of many of
    these.
    """

    #: A u16 counter wraps every eighteen minutes at 59.94 Hz, which is well
    #: inside a single run, so a narrow counter is refused rather than handled.
    MIN_WIDTH = 4

    def __init__(self, emu, address: int, size: int = 4,
                 chain: Sequence[int] = (), timeout: float = 5.0,
                 interval: float = 0.002,
                 sleep: Callable[[float], None] = time.sleep,
                 now: Callable[[], float] = time.monotonic) -> None:
        if size < self.MIN_WIDTH:
            raise ValueError(
                "a %d-byte frame counter wraps inside a single run; read the "
                "full %d-byte word" % (size, self.MIN_WIDTH))
        self.emu = emu
        self.address = address
        self.size = size
        self.chain = tuple(chain)
        self.timeout = timeout
        self.interval = interval
        self.slip: List[int] = []
        self._sleep = sleep
        self._now = now

    def frame(self) -> int:
        address = self.address
        for offset in self.chain:
            pointer = self.emu.read(address, 4)
            # A null here is the ordinary "not in a play yet" case -- the
            # player array pointer at 0x00600E48 is null at the main menu --
            # and reading 0 + offset would return a plausible-looking number
            # from the exception vectors instead of failing.
            if pointer == 0:
                raise PadError(
                    "the pointer at %#010x is null, so the frame counter does "
                    "not exist yet. This is what the main menu looks like: "
                    "load an in-play state before running a script."
                    % address)
            address = pointer + offset
        return self.emu.read(address, self.size)

    def wait_until(self, frame: int) -> None:
        deadline = self._now() + self.timeout
        started = self.frame()
        while True:
            current = self.frame()
            if current >= frame:
                self.slip.append(current - frame)
                return
            if current < started:
                raise PadError(
                    "the frame counter at %#010x went backwards, %d to %d, "
                    "while waiting for %d. It counts from something that has "
                    "just reset -- the snap, most likely -- so the script's "
                    "frame offsets no longer refer to anything."
                    % (self.address, started, current, frame))
            if self._now() >= deadline:
                raise PadError(
                    "frame counter at %#010x reached %d but the script wanted "
                    "%d, and it has not got there in %.1fs. Either the "
                    "emulator is paused -- PINE can see a pause but cannot "
                    "cause one, so something else paused it -- or %#010x is "
                    "not a frame counter."
                    % (self.address, current, frame, self.timeout,
                       self.address))
            self._sleep(self.interval)

    def calibrate(self, seconds: float = 1.0,
                  tolerance: float = 0.25) -> float:
        """Measure the counter's rate, and reject it if it is not frame-like.

        Returns the observed frames per second. A counter that does not move,
        or moves at some unrelated rate, is a wrong address -- and a wrong
        address here would silently stretch or compress every duration in
        every trial. Note that a *dropped* write is invisible on this path
        (PINE drops writes to handler pages without an error), so calibration
        is the only positive evidence that the address means anything.
        """
        first = self.frame()
        started = self._now()
        self._sleep(seconds)
        elapsed = self._now() - started
        advanced = self.frame() - first
        if elapsed <= 0:
            raise PadError("no time passed during calibration")
        if advanced <= 0:
            raise PadError(
                "%#010x did not advance in %.2fs, so it is not a frame "
                "counter (or the emulator is paused)"
                % (self.address, elapsed))
        rate = advanced / elapsed
        if abs(rate - NTSC_FPS) > tolerance * NTSC_FPS:
            raise PadError(
                "%#010x advances at %.1f Hz, not the %.2f Hz of an NTSC PS2 "
                "frame. Either it counts something else, or the emulator is "
                "not running at full speed -- both make frame-relative input "
                "meaningless." % (self.address, rate, NTSC_FPS))
        return rate


# -- devices -------------------------------------------------------------


class RecordingDevice:
    """A pad that goes nowhere and remembers everything. Dry runs and tests."""

    def __init__(self) -> None:
        self.states: List[PadState] = []
        self.events: List[Tuple[int, int, int]] = []
        self.closed = False
        self._state = NEUTRAL

    def apply(self, state: PadState) -> None:
        self.events.extend(evdev_report(self._state, state))
        self._state = state
        self.states.append(state)

    def close(self) -> None:
        self.closed = True


class Kernel(NamedTuple):
    """The four syscalls the device needs, injectable so tests can lie."""

    open: Callable[..., int] = os.open
    ioctl: Callable[..., int] = fcntl.ioctl
    write: Callable[[int, bytes], int] = os.write
    close: Callable[[int], None] = os.close


REAL_KERNEL = Kernel()

#: Printed verbatim whenever the device cannot be created, because the fix is
#: five commands and a re-login and nobody should have to find that twice.
SETUP_STEPS = """\
    sudo modprobe uinput
    echo uinput | sudo tee /etc/modules-load.d/uinput.conf
    printf 'KERNEL=="uinput", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"\\n' \\
        | sudo tee /etc/udev/rules.d/99-uinput.rules
    sudo usermod -aG input "$USER"
    sudo udevadm control --reload-rules && sudo udevadm trigger
then log out and back in -- a session that was already open keeps the old
group list, so an SSH connection opened before the usermod will still fail."""


class UinputDevice:
    """A virtual DualShock-shaped gamepad on `/dev/uinput`.

    Created on construction and destroyed on :meth:`close`. `settle` is the
    pause after `UI_DEV_CREATE`: udev has to create the `/dev/input/eventN`
    node and the emulator only notices the device on its next frame poll, so
    writing events immediately writes them into nothing.
    """

    def __init__(self, name: str = DEFAULT_NAME, path: str = UINPUT_PATH,
                 vendor: int = DEFAULT_VENDOR, product: int = DEFAULT_PRODUCT,
                 version: int = DEFAULT_VERSION, settle: float = 0.4,
                 kernel: Kernel = REAL_KERNEL,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.name = name
        self.path = path
        self._kernel = kernel
        self._state = NEUTRAL
        self.fd: Optional[int] = None

        try:
            fd = kernel.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            raise PadError(_open_advice(path, exc)) from None

        try:
            self._configure(fd)
            setup = struct.pack(_SETUP_FORMAT, BUS_USB, vendor, product,
                                version, name.encode("ascii", "replace")[:79],
                                0)
            kernel.ioctl(fd, UI_DEV_SETUP, setup)
            kernel.ioctl(fd, UI_DEV_CREATE)
        except OSError as exc:
            kernel.close(fd)
            raise PadError(
                "creating the virtual pad on %s failed: %s. The device node is "
                "there but the kernel refused the setup, which usually means "
                "the uinput ABI is older than UI_DEV_SETUP (kernel < 4.5)."
                % (path, exc)) from None
        self.fd = fd
        sleep(settle)

    def _configure(self, fd: int) -> None:
        kernel = self._kernel
        kernel.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        kernel.ioctl(fd, UI_SET_EVBIT, EV_ABS)
        for code in sorted(_KEY_CODE.values()):
            kernel.ioctl(fd, UI_SET_KEYBIT, code)
        for axis, value, low, high in self.axes():
            kernel.ioctl(fd, UI_ABS_SETUP,
                         struct.pack(_ABS_SETUP_FORMAT, axis, value,
                                     low, high, 0, 0, 0))

    @staticmethod
    def axes() -> List[Tuple[int, int, int, int]]:
        """`(code, initial, min, max)` per axis. Sticks keep the PS2's 0..255.

        The initial value is not decoration. A trigger declared at the centre
        of its range reads as half-pressed until something moves it, and
        nothing here ever moves an untouched trigger, so L2 would appear stuck
        at 50% for the whole run. Sticks centre, triggers rest at zero.

        `flat` and `fuzz` are left at zero deliberately: a driver-declared
        dead zone would quietly eat small deflections, and steering a ball
        carrier is mostly small deflections.
        """
        return ([(axis, AXIS_CENTRE, AXIS_MIN, AXIS_MAX)
                 for axis in (ABS_X, ABS_Y, ABS_RX, ABS_RY)]
                + [(axis, AXIS_MIN, AXIS_MIN, AXIS_MAX)
                   for axis in (ABS_Z, ABS_RZ)]
                + [(axis, 0, -1, 1) for axis in (ABS_HAT0X, ABS_HAT0Y)])

    def apply(self, state: PadState) -> None:
        if self.fd is None:
            raise PadError("the virtual pad has been closed")
        events = evdev_report(self._state, state)
        if not events:
            return
        blob = b"".join(pack_event(*event) for event in events)
        try:
            self._kernel.write(self.fd, blob)
        except OSError as exc:
            raise PadError(
                "writing to the virtual pad failed: %s" % exc) from None
        self._state = state

    def close(self) -> None:
        if self.fd is None:
            return
        fd, self.fd = self.fd, None
        try:
            self._kernel.ioctl(fd, UI_DEV_DESTROY)
        except OSError:
            pass                      # already gone; nothing useful to do
        self._kernel.close(fd)


def _open_advice(path: str, exc: OSError) -> str:
    if exc.errno == errno.ENOENT:
        return ("%s does not exist, so the uinput module is not loaded. On the "
                "rig, once:\n%s" % (path, SETUP_STEPS))
    if exc.errno in (errno.EACCES, errno.EPERM):
        return ("no permission to open %s (it ships root-only). On the rig, "
                "once:\n%s" % (path, SETUP_STEPS))
    return "cannot open %s: %s" % (path, exc)


def diagnose(path: str = UINPUT_PATH,
             kernel: Kernel = REAL_KERNEL) -> Optional[str]:
    """What is wrong with the uinput setup, or None if nothing is.

    Opens and immediately closes the node without creating a device, so it is
    safe to call while a trial is running.
    """
    try:
        fd = kernel.open(path, os.O_WRONLY | os.O_NONBLOCK)
    except OSError as exc:
        return _open_advice(path, exc)
    kernel.close(fd)
    return None


# -- the pad -------------------------------------------------------------


class Pad:
    """Frame-relative controller input.

    `device` defaults to a real :class:`UinputDevice`, which is created eagerly
    -- a pad that cannot be created should fail at the top of a run, not two
    hundred iterations in. `clock` defaults to :class:`WallClock`; read the
    module docstring before believing it.
    """

    def __init__(self, device=None, clock=None) -> None:
        # One pad per process, and only one pad on the machine. Two virtual
        # pads is the failure that looks most like a dead binding: a second
        # device takes the next `SDL-N` index, the emulator stays bound to the
        # first, and every press lands on a controller nobody reads. If a
        # keep-alive pad is holding a node open for a binding screen, stop it
        # before running a trial.
        self.device = UinputDevice() if device is None else device
        self.clock = WallClock() if clock is None else clock
        self._base = NEUTRAL
        self.device.apply(NEUTRAL)

    def __enter__(self) -> "Pad":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- state ---------------------------------------------------------

    @property
    def base(self) -> PadState:
        """What is held between scripts, from :meth:`hold` and :meth:`stick`."""
        return self._base

    @property
    def slip(self) -> List[int]:
        """How late, in frames, each wait so far actually returned.

        Belongs in a trial's provenance. This layer promises input within a
        frame or two of where the script asked for it, never frame-exactly,
        and this is the only record of which it was on the run that produced
        the numbers.
        """
        return list(getattr(self.clock, "slip", []))

    def hold(self, button: str) -> None:
        """Press and leave pressed. One edge, no waiting -- see :meth:`press`."""
        self._set(self._base.pressing(button))

    def release(self, button: str) -> None:
        """Let go. Releasing something that was not held is not an error, for
        the same reason it is not on hardware."""
        self._set(self._base.releasing(button))

    def stick(self, which: str, x: float, y: float) -> None:
        """Deflect a stick and leave it there. -1 is left or up."""
        self._set(self._base.with_stick(which, x, y))

    def neutral(self) -> None:
        """Everything released, both sticks centred."""
        self._set(NEUTRAL)

    def _set(self, state: PadState) -> None:
        self._base = state
        self.device.apply(state)

    # -- frame-relative ------------------------------------------------

    def press(self, button: str, frames: int = DEFAULT_PRESS_FRAMES) -> None:
        """Tap, and block until it has been released.

        Two frames by default because the emulator samples host input once per
        frame: a one-frame press can fall entirely between two samples and
        never reach the game. `hold` and `release` do no waiting at all, so a
        `hold`/`release` pair inside one frame is invisible for that reason.
        """
        self.sequence([(0, button, frames)])

    def sequence(self, script: Sequence[Entry]) -> None:
        """Run a script, blocking until it is done.

        `[(frame_offset, action, duration), ...]`, offsets relative to the
        frame this is called on, so the same script is reusable across trials.
        Every span is half-open, so the plan's last transition is always back
        to whatever :meth:`hold` had set -- a script cannot leave a button
        down by ending while it is held.
        """
        plan = schedule(script, start=self.clock.frame(), base=self._base)
        for frame, state in plan:
            self.clock.wait_until(frame)
            self.device.apply(state)

    def close(self) -> None:
        """Release everything, then destroy the device.

        The release matters: a button still held when the harness exits is
        held into whatever runs next, and a stuck `cross` on a play-call
        screen is a hard thing to attribute afterwards.
        """
        try:
            self.device.apply(NEUTRAL)
        except PadError:
            pass
        self._base = NEUTRAL
        self.device.close()


# -- CLI -----------------------------------------------------------------


def _parse_action(text: str) -> Action:
    """`cross`, or `left:0.0,-1.0` for a stick."""
    if ":" not in text:
        _check_button(text)
        return text
    which, _, coords = text.partition(":")
    parts = coords.split(",")
    if len(parts) != 2:
        raise ValueError("a stick reads like left:0.0,-1.0, got %r" % (text,))
    return Stick(which, float(parts[0]), float(parts[1])).validated()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drive a virtual PS2 pad, or check that one can exist.")
    parser.add_argument("--check", action="store_true",
                        help="report whether /dev/uinput is usable, and exit")
    parser.add_argument("--path", default=UINPUT_PATH)
    parser.add_argument("--press", metavar="ACTION",
                        help="tap a button, e.g. cross")
    parser.add_argument("--hold", metavar="ACTION",
                        help="hold a button or stick for --seconds, so the "
                             "emulator's binding screen can see it")
    parser.add_argument("--frames", type=int, default=DEFAULT_PRESS_FRAMES)
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the frame plan and touch no device")
    parser.add_argument("--buttons", action="store_true",
                        help="list the button names a script may use")
    args = parser.parse_args(argv)

    if args.buttons:
        print("buttons: %s" % ", ".join(BUTTONS))
        print("sticks : %s (as left:X,Y with X and Y in -1.0..1.0)"
              % ", ".join(STICKS))
        return 0

    if args.check:
        problem = diagnose(args.path)
        if problem:
            print("error: %s" % problem, file=sys.stderr)
            return 2
        print("%s is usable" % args.path)
        return 0

    if not (args.press or args.hold):
        parser.error("nothing to do: pass --press, --hold, --check or --buttons")

    try:
        action = _parse_action(args.press or args.hold)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if args.dry_run:
        frames = args.frames if args.press else int(args.seconds * NTSC_FPS)
        try:
            plan = schedule([(0, action, max(1, frames))])
        except ValueError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
        for frame, state in plan:
            print("frame %+d: %s" % (frame, _describe(state)))
        return 0

    try:
        pad = Pad()
    except PadError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    try:
        if args.press:
            pad.sequence([(0, action, args.frames)])
        else:
            if isinstance(action, Stick):
                pad.stick(action.which, action.x, action.y)
            else:
                pad.hold(action)
            time.sleep(args.seconds)
    finally:
        pad.close()
    return 0


def _describe(state: PadState) -> str:
    held = ",".join(sorted(state.buttons)) or "-"
    return "buttons=%s left=%s right=%s" % (held, state.left, state.right)


if __name__ == "__main__":
    sys.exit(main())

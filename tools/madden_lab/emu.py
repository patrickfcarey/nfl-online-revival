"""Layer 1: emulator control, and the loud truth about what PINE cannot do.

`tools.pine` already reads and writes EE memory. This adds the rest of the
protocol -- status, savestates, identity -- and batches reads, because forty
addresses a frame at one round trip each is a different performance profile
from forty addresses in one message.

The opcode table was derived from PCSX2's own server, `pcsx2/PINE.cpp`, read
on the rig at `~/penguinscreen2-dev` on 2026-08-10: md5
`071cdf3be9bfd439875c0f7aba014bb8`, identical byte for byte to
`~/pcsx2-VR-tracka` (the other build on that machine), to every branch of the
local `pcsx2-VR` checkout, and to `upstream/master`. PenguinScreen2 changed
nothing about PINE, including the socket name -- `PINE_EMULATOR_NAME` is still
"pcsx2" (PINE.cpp:70). Line references below are to that file.

One thing outside the protocol can still make `load_state` a no-op, and it
fails the silent way described below. The rebranded build reads its profile
from `~/.config/PenguinScreen2` and the older `pcsx2-VR-tracka` build from
`~/.config/PCSX2`; both have PINE on at slot 28011 and both hold
`SLUS-20752 (14F8B841).0N.p2s` states, but *different* ones. Which binary is
running decides which world slot 1 means.

Four findings shape everything above this layer, and none of them can be
worked around from here:

**`load_state` does not wait, and cannot fail out loud.** PINE hands the load
to the CPU thread through `Host::RunOnCPUThread` with `block = false`
(PINE.cpp:661, QtHost.cpp:1256) and replies OK immediately. OK means the
command was accepted, not that the state is loaded; the emulator is still
running the old world for some milliseconds afterwards. Worse, a load that
fails -- empty slot, corrupt file, wrong game -- is reported on the on-screen
display only (PINE.cpp:663-664) and never on the socket. **A trial that loads
a state and starts sampling without confirming the load has happened is
reading the previous trial's world.** :meth:`Emu.wait_until` is the confirmation
seam: probe an address the state is known to differ on.

**There is no pause.** The command table stops at `MsgStatus = 0xF`
(PINE.cpp:144-163); no opcode pauses, resumes or advances a frame, and PINE is
the only socket the emulator listens on. :meth:`Emu.status` can *observe* a
pause but nothing here can cause one. Pausing has to come from outside -- a
hotkey through layer 2, or a human -- so :meth:`Emu.pause` refuses rather than
pretending, unless the caller supplies a hook and takes responsibility for it.

**Reads are not synchronised with emulation.** `ParseCommand` runs on the PINE
server thread and goes straight at the vtlb with no lock (PINE.cpp:511-756),
while the EE thread keeps writing. Any multi-word read can tear mid-update.
Batching narrows the window to one pass over one buffer with no socket waits
inside it, which is as close to a coherent snapshot as this protocol gets.

**A bad address is not an error, it is a zero.** Reads go through
`vtlb_ramRead` with the result flag discarded (PINE.cpp:566), so an address on
a handler page -- I/O registers, unmapped memory -- returns 0 and a write to
one is silently dropped (vtlb.cpp:326-352). `doctor` cannot tell a wrong
address from a legitimately zero word; only a known non-zero anchor can.
"""

from __future__ import annotations

import enum
import struct
import time
from typing import Callable, Iterator, List, Optional, Sequence, Tuple

from .. import pine
from ..pine import PineError
from . import EXPECTED_CRC

#: Ceilings the server enforces on one message (PINE.cpp:117 and :123). The
#: request one matters more than it looks: a message declaring more than this
#: is dropped *without a reply at all* (PINE.cpp:458-462), so overshooting it
#: costs a timeout and a desynchronised socket rather than an error.
MAX_REQUEST_BYTES = 650000
MAX_REPLY_BYTES = 450000

_REQUEST_HEADER = 4          # the length prefix counts itself
_REPLY_HEADER = 5            # length prefix plus the result byte

_READ_OP = {1: pine.READ8, 2: pine.READ16, 4: pine.READ32, 8: pine.READ64}
_FORMAT = {1: "<B", 2: "<H", 4: "<I", 8: "<Q"}

#: Bytes a single read costs in the request: one opcode plus an address.
_READ_COST = 5

Spec = Tuple[int, int]       # (address, size in bytes)


class Status(enum.IntEnum):
    """What `MsgStatus` reports (PINE.cpp:169-174).

    `MsgStatus` is one of only two commands that answer with no game booted
    (`MsgVersion` is the other); everything else fails outright. So SHUTDOWN
    means the emulator is up and idle, not that it is gone.
    """

    RUNNING = 0
    PAUSED = 1
    SHUTDOWN = 2


class EmuNotRunning(PineError):
    """Nothing is listening, or what is listening is not an emulator."""


class UnsupportedCommand(PineError):
    """This build's PINE has no such command; retrying will not help."""


class WrongGame(PineError):
    """The emulator is running a build these addresses do not describe."""


class ReadOnly(PineError):
    """A write was attempted on a connection that was not opened for writing."""


class EmuTimeout(PineError):
    """The emulator did not reach the expected state in time."""


def _batches(specs: Sequence[Spec]) -> Iterator[List[Spec]]:
    """Split reads into messages that stay inside the server's ceilings.

    Sizes are assumed already validated; callers must reject a bad width
    before any of this is sent, not halfway through the third batch.
    """
    batch: List[Spec] = []
    request = _REQUEST_HEADER
    reply = _REPLY_HEADER
    for spec in specs:
        size = spec[1]
        # The reply check is `>=`, not `>`, in the server (PINE.cpp:256-260).
        too_big = (request + _READ_COST > MAX_REQUEST_BYTES
                   or reply + size >= MAX_REPLY_BYTES)
        if batch and too_big:
            yield batch
            batch, request, reply = [], _REQUEST_HEADER, _REPLY_HEADER
        batch.append(spec)
        request += _READ_COST
        reply += size
    if batch:
        yield batch


class Emu:
    """One connection to the emulator running the experiment.

    Writes are refused unless `writable` is set. That is the design contract's
    "read-only by default" made structural: an undeclared poke does not just
    spoil one trial, it makes every number in the run unreproducible, and on a
    shared rig it lands in whatever session is live.
    """

    def __init__(self, path: Optional[str] = None, timeout: float = 5.0,
                 pine_slot: int = pine.DEFAULT_SLOT, writable: bool = False,
                 pause_hook: Optional[Callable[[], None]] = None,
                 resume_hook: Optional[Callable[[], None]] = None) -> None:
        self.pine_slot = pine_slot
        self.path = path or pine.default_socket_path(pine_slot)
        self.writable = writable
        self.pause_hook = pause_hook
        self.resume_hook = resume_hook
        self.pine = pine.Pine(self.path, timeout)

    def __enter__(self) -> "Emu":
        self.connect()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def connect(self) -> None:
        """Open the socket, saying something useful when there is nothing there.

        A socket file that exists but refuses the connection is the ordinary
        aftermath of a crash: PCSX2 unlinks it only on a clean shutdown
        (PINE.cpp:487-492), so the stale file outlives the process and the
        bare `ECONNREFUSED` looks like a permissions problem instead.
        """
        try:
            self.pine.connect()
        except PineError as exc:
            raise EmuNotRunning(
                "%s (PINE slot %d). If the file is there but the connection "
                "is refused, the emulator crashed and left the socket behind "
                "-- it is only unlinked on a clean shutdown."
                % (exc, self.pine_slot)) from None

    def close(self) -> None:
        self.pine.close()

    def _client(self) -> pine.Pine:
        # Connect through our own method so the actionable message survives;
        # Pine reconnects silently on first use otherwise.
        if self.pine.sock is None:
            self.connect()
        return self.pine

    # -- memory --------------------------------------------------------

    def read(self, address: int, size: int = 4) -> int:
        """One word of EE memory. Zero if the address is not backed by RAM."""
        return self._client().read(address, size)

    def write(self, address: int, value: int, size: int = 4) -> None:
        """Write EE memory. Only inside a trial's declared setup."""
        if not self.writable:
            raise ReadOnly(
                "this Emu was opened read-only; pass writable=True and record "
                "the write in the trial's setup, or the run is not "
                "reproducible")
        self._client().write(address, value, size)

    def read_many(self, specs: Sequence[Spec]) -> List[int]:
        """Read many addresses in as few round trips as the ceilings allow.

        `specs` are `(address, size)`. The reply carries no tags -- results are
        concatenated in request order (PINE.cpp:511-756) -- so the sizes asked
        for are the only thing that says where one value ends and the next
        begins. Get them wrong and every value after the mistake is garbage
        that still parses.
        """
        for address, size in specs:
            if size not in _READ_OP:
                raise ValueError("size must be 1, 2, 4 or 8, got %r" % (size,))

        out: List[int] = []
        for batch in _batches(specs):
            payload = b"".join(struct.pack("<BI", _READ_OP[size], address)
                               for address, size in batch)
            data = self._client().request(payload)
            offset = 0
            for _address, size in batch:
                if offset + size > len(data):
                    raise PineError(
                        "batch reply is %d bytes, short of the %d these %d "
                        "reads asked for" % (len(data), offset + size,
                                             len(batch)))
                out.append(struct.unpack_from(_FORMAT[size], data, offset)[0])
                offset += size
        return out

    def read_bytes(self, address: int, count: int) -> bytes:
        """A run of EE memory, batched rather than a round trip per word.

        Overrides the one-word-at-a-time version in `tools.pine`: a 512-byte
        player struct is 128 round trips there and one here, and the shorter
        the read takes the less of the game moves underneath it.
        """
        if count <= 0:
            return b""
        words = (count + 3) // 4
        values = self.read_many([(address + 4 * i, 4) for i in range(words)])
        return struct.pack("<%dI" % words, *values)[:count]

    # -- state ---------------------------------------------------------

    def status(self) -> Status:
        raw = self._client().status()
        try:
            return Status(raw)
        except ValueError:
            raise PineError(
                "PINE reported status %d, which is not one of running, paused "
                "or shutdown" % raw) from None

    def pause(self) -> None:
        """Not available over PINE. See the module docstring.

        There is no pause opcode in this build (PINE.cpp:144-163). A caller
        that has another way in -- a hotkey through layer 2, say -- can pass it
        as `pause_hook`, but that is outside the protocol and outside anything
        this module can verify, so it must be recorded in the trial's
        provenance like any other out-of-band action.
        """
        if self.pause_hook is None:
            raise UnsupportedCommand(
                "PINE has no pause command in this build; its opcodes stop at "
                "MsgStatus. Pause from outside the protocol and pass it as "
                "pause_hook, or arrange the trial not to need one")
        self.pause_hook()

    def resume(self) -> None:
        """Not available over PINE. See :meth:`pause`."""
        if self.resume_hook is None:
            raise UnsupportedCommand(
                "PINE has no resume command in this build; its opcodes stop "
                "at MsgStatus. Resume from outside the protocol and pass it "
                "as resume_hook")
        self.resume_hook()

    def save_state(self, slot: int) -> None:
        """Ask for a savestate in `slot`, and do not believe it is written yet.

        Queued on the CPU thread and zipped on another one
        (PINE.cpp:647-651), so the reply arrives long before the file does.
        The file lands as `SLUS-20752 (14F8B841).<slot as 2 digits>.p2s`.
        """
        self._slot_command(pine.SAVESTATE, slot)

    def load_state(self, slot: int) -> None:
        """Ask for `slot` to be loaded. **This returns before the load happens.**

        The reset primitive of the whole harness, and it is fire and forget:
        queued on the CPU thread with no callback (PINE.cpp:661-665), OK on the
        socket the moment it is queued, and any failure reported only on the
        on-screen display. Follow it with :meth:`wait_until` on an address the
        state is known to differ on. Nothing else distinguishes a loaded world
        from the one that was already there.
        """
        self._slot_command(pine.LOADSTATE, slot)

    def _slot_command(self, opcode: int, slot: int) -> None:
        # The slot is read as a u8 (PINE.cpp:647), so 256 would silently become
        # 0 and quietly load a state belonging to some other experiment.
        if not isinstance(slot, int) or not 0 <= slot <= 255:
            raise ValueError("savestate slot must be 0-255, got %r" % (slot,))
        self._client().request(struct.pack("<BB", opcode, slot))

    def wait_until(self, predicate: Callable[["Emu"], bool],
                   timeout: float = 5.0, interval: float = 0.02) -> float:
        """Poll until `predicate` holds, and return how long that took.

        Exists because every asynchronous command here -- load, save, anything
        the CPU thread queues -- reports success before it has done anything.
        """
        deadline = time.monotonic() + timeout
        started = time.monotonic()
        while True:
            if predicate(self):
                return time.monotonic() - started
            if time.monotonic() >= deadline:
                raise EmuTimeout(
                    "condition still false after %.2fs" % timeout)
            time.sleep(interval)

    # -- identity ------------------------------------------------------

    def title(self) -> str:
        return self._client().title()

    def game_id(self) -> str:
        return self._client().game_id()

    def version(self) -> str:
        """The emulator's build. Answers even with no game booted."""
        return self._client().version()

    def game_crc(self) -> int:
        """The disc CRC PCSX2 keys its per-game settings and patches on.

        Comes back as eight hex characters rather than a number
        (PINE.cpp:701), so this parses; unpacking it as a word would give a
        number that looks plausible and matches nothing.
        """
        text = self._client().game_uuid()
        try:
            return int(text, 16)
        except ValueError:
            raise PineError(
                "PINE answered %r for the game CRC, which is not hex" % text
            ) from None

    def require_crc(self, expected: int = EXPECTED_CRC) -> int:
        """Refuse to go on unless the emulator is running the expected build.

        Every wrong answer this project has produced came from an address that
        had drifted or was never right. This is the one cheap check that
        catches the whole class.
        """
        actual = self.game_crc()
        if actual != expected:
            raise WrongGame(
                "expected CRC %08X, found %08X (%s, %s). The addresses in "
                "docs/ do not apply to this build."
                % (expected, actual, self.game_id(), self.title()))
        return actual

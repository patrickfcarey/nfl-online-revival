"""Talk to a running PCSX2 over PINE, so the console can be questioned live.

Every previous measurement here went through a savestate: patch the game to
store a value somewhere, ask a human to hit F1, unzip 32 MB of Zstandard, read
one word. That is a slow loop with a human in it, and it can only see values
something deliberately wrote to memory.

PINE is PCSX2's instrumentation socket. With it, any EE address can be read or
written while the game runs -- no patch, no savestate, no reboot. It is enabled
in `PCSX2.ini` via ``EnablePINE`` and listens on a Unix socket
(``$XDG_RUNTIME_DIR/pcsx2.sock``); ``PINESlot`` only matters for TCP.

Protocol, both directions:

    [4-byte LE total length, counting these 4][1-byte opcode][payload...]
    [4-byte LE total length][1-byte result: 0 ok, 0xFF fail][payload...]

Reads take a 4-byte little-endian address and return the value little-endian.
Writes take address then value. Everything is one command per round trip here --
PINE supports batching, but a batch that fails tells you less about which part
failed, and these calls are not hot.

**This writes to a live game.** Reads are always safe. A write lands in EE RAM
immediately with no validation, so a wrong address corrupts whatever is there.
"""

from __future__ import annotations

import argparse
import os
import socket
import struct
import sys
from typing import List, Optional

#: Where PCSX2 puts the socket on Linux. XDG_RUNTIME_DIR is per-user, so this
#: resolves per-user without configuration.
def default_socket_path() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()
    return os.path.join(runtime, "pcsx2.sock")


# Opcodes, from PCSX2's PINE server.
READ8, READ16, READ32, READ64 = 0, 1, 2, 3
WRITE8, WRITE16, WRITE32, WRITE64 = 4, 5, 6, 7
VERSION, SAVESTATE, LOADSTATE, TITLE = 8, 9, 0xA, 0xB
GAME_ID, GAME_UUID, GAME_VERSION, STATUS = 0xC, 0xD, 0xE, 0xF

_READ_OPS = {1: READ8, 2: READ16, 4: READ32, 8: READ64}
_WRITE_OPS = {1: WRITE8, 2: WRITE16, 4: WRITE32, 8: WRITE64}
_UNPACK = {1: "<B", 2: "<H", 4: "<I", 8: "<Q"}

OK = 0


class PineError(Exception):
    """PCSX2 refused the request, or is not listening."""


class Pine:
    """One connection to a running PCSX2."""

    def __init__(self, path: Optional[str] = None, timeout: float = 5.0):
        self.path = path or default_socket_path()
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None

    def __enter__(self) -> "Pine":
        self.connect()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def connect(self) -> None:
        if not os.path.exists(self.path):
            raise PineError(
                "no PINE socket at %s. Check `EnablePINE = true` in "
                "PCSX2.ini and that the emulator is running." % self.path)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        try:
            self.sock.connect(self.path)
        except OSError as exc:
            raise PineError("cannot connect to %s: %s" % (self.path, exc))

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    # -- transport -----------------------------------------------------

    def _request(self, payload: bytes) -> bytes:
        if self.sock is None:
            self.connect()
        assert self.sock is not None
        message = struct.pack("<I", 4 + len(payload)) + payload
        self.sock.sendall(message)

        header = self._recv_exactly(4)
        total = struct.unpack("<I", header)[0]
        if total < 5:
            raise PineError("reply claims %d bytes; the minimum is 5" % total)
        body = self._recv_exactly(total - 4)
        if body[0] != OK:
            raise PineError("PCSX2 rejected the request (code 0x%02x)" % body[0])
        return body[1:]

    def _recv_exactly(self, count: int) -> bytes:
        chunks: List[bytes] = []
        remaining = count
        assert self.sock is not None
        while remaining:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise PineError("PCSX2 closed the connection mid-reply")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    # -- memory --------------------------------------------------------

    def read(self, address: int, size: int = 4) -> int:
        """Read 1, 2, 4 or 8 bytes of EE memory."""
        if size not in _READ_OPS:
            raise ValueError("size must be 1, 2, 4 or 8, got %r" % (size,))
        data = self._request(struct.pack("<BI", _READ_OPS[size], address))
        return struct.unpack(_UNPACK[size], data[:size])[0]

    def write(self, address: int, value: int, size: int = 4) -> None:
        """Write EE memory. No validation happens anywhere -- be sure."""
        if size not in _WRITE_OPS:
            raise ValueError("size must be 1, 2, 4 or 8, got %r" % (size,))
        packed = struct.pack(_UNPACK[size], value & ((1 << (size * 8)) - 1))
        self._request(struct.pack("<BI", _WRITE_OPS[size], address) + packed)

    def read_bytes(self, address: int, count: int) -> bytes:
        """Read a run of bytes, one word at a time."""
        out = bytearray()
        while len(out) < count:
            out += struct.pack("<I", self.read(address + len(out), 4))
        return bytes(out[:count])

    def read_string(self, address: int, limit: int = 256) -> str:
        """A NUL-terminated string out of EE memory."""
        raw = self.read_bytes(address, limit)
        return raw.split(b"\x00", 1)[0].decode("latin-1", "replace")

    # -- identity ------------------------------------------------------

    def _text(self, opcode: int) -> str:
        data = self._request(struct.pack("<B", opcode))
        # These replies are a 4-byte length then the string, NUL-terminated.
        if len(data) >= 4:
            return data[4:].split(b"\x00", 1)[0].decode("latin-1", "replace")
        return ""

    def version(self) -> str:
        return self._text(VERSION)

    def title(self) -> str:
        return self._text(TITLE)

    def game_id(self) -> str:
        return self._text(GAME_ID)

    def status(self) -> int:
        return struct.unpack("<I", self._request(struct.pack("<B", STATUS))[:4])[0]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read or write a running PCSX2's memory over PINE.")
    parser.add_argument("--socket", help="PINE socket path")
    parser.add_argument("--info", action="store_true",
                        help="report what game is running")
    parser.add_argument("--read", type=lambda s: int(s, 0), action="append",
                        default=[], metavar="ADDR",
                        help="EE address to read; repeatable")
    parser.add_argument("--size", type=int, default=4, choices=(1, 2, 4, 8))
    parser.add_argument("--string", type=lambda s: int(s, 0),
                        help="read a NUL-terminated string at this address")
    parser.add_argument("--write", nargs=2, metavar=("ADDR", "VALUE"),
                        help="write VALUE to ADDR. Affects the live game.")
    args = parser.parse_args(argv)

    try:
        with Pine(args.socket) as pine:
            if args.info or not (args.read or args.write or args.string):
                print("game    : %s" % pine.title())
                print("serial  : %s" % pine.game_id())
                print("emulator: %s" % pine.version())
            for address in args.read:
                value = pine.read(address, args.size)
                print("%#010x = %d (0x%0*x)"
                      % (address, value, args.size * 2, value))
            if args.string is not None:
                print("%#010x = %r" % (args.string, pine.read_string(args.string)))
            if args.write:
                address = int(args.write[0], 0)
                value = int(args.write[1], 0)
                pine.write(address, value, args.size)
                print("wrote %#x to %#010x" % (value, address))
    except PineError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

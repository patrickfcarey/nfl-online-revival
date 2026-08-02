"""Read the roster checksum the console computed, out of a PCSX2 savestate.

The console never tells us this number. A wrong algorithm shows up only as
"your rosters are out of date", which is one bit of feedback per login and says
nothing about *how* wrong. Five candidate algorithms were tested against
hardware and all five rejected, at which point guessing costs more than
measuring.

So the measurement patch in ``patches/14F8B841.pnach`` replaces the compare

    0012a978  xor v0, v0, v1

with

    0012a978  sw  v0, -19396(gp)

which writes the console's own computed checksum into the slot that normally
holds the value we announced (gp is 0x006056f0, so the slot is 0x00600b2c).
The next ``news`` reply refills it. The overwrite is not quite free, though: an
earlier version of this note said "nothing else reads that slot", and that is
false -- 0x0012a990 is a getter for the same word, called from 0x00354674, which
serialises it into an outgoing buffer. So the patch also makes the console
transmit a wrong checksum. Harmless for a measurement run; worth knowing.

A PCSX2 savestate is a ZIP holding a raw 32 MB ``eeMemory.bin``, so EE address
0x00600b2c is simply offset 0x00600b2c in that file.

Usage:

    python3 tools/read_roster_checksum.py "~/.config/PCSX2/sstates/SLUS-20752 (14F8B841).01.p2s"

Take the savestate *after* the console has reached the roster check -- that is,
after it has logged in and shown the out-of-date prompt. Before that the slot
still holds whatever the server last announced, which is a number we already
know and would be easy to mistake for a result.
"""

from __future__ import annotations

import argparse
import shutil
import struct
import subprocess
import sys
import threading
import zipfile
from typing import List, Optional

#: Where the patch stores the console's computed checksum.
SLOT_ADDRESS = 0x00600B2C

#: The member of a PCSX2 savestate holding raw EE RAM, based at address 0.
EE_MEMORY = "eeMemory.bin"

#: PS2 EE RAM.
EE_SIZE = 32 * 1024 * 1024


class ReadError(Exception):
    pass


#: ZIP compression method 93 is Zstandard, which PCSX2 now uses and which
#: `zipfile` cannot decompress on its own. Rather than require a pip install on
#: the rig, shell out to the `zstd` binary, which ships with the distro.
METHOD_ZSTD = 93


def _raw_member(path: str, name: str) -> bytes:
    """The stored bytes of one ZIP member, without decompressing them.

    Needed because `zipfile` refuses to open a Zstandard member at all, so the
    compressed data has to be lifted out by hand and fed to `zstd` separately.
    """
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo(name)
    with open(path, "rb") as handle:
        handle.seek(info.header_offset)
        header = handle.read(30)
        if header[:4] != b"PK\x03\x04":
            raise ReadError("no local header for %s in %s" % (name, path))
        # The local header repeats the name and extra-field lengths, and they
        # can differ from the central directory's, so trust these.
        name_len, extra_len = struct.unpack_from("<HH", header, 26)
        handle.seek(info.header_offset + 30 + name_len + extra_len)
        return handle.read(info.compress_size)


def read_word(path: str, address: int = SLOT_ADDRESS) -> int:
    """One little-endian 32-bit word out of the savestate's EE memory."""
    # <= rather than <: EE_SIZE - 4 is the last address a whole word fits at,
    # and rejecting it put the final word of RAM out of reach.
    if not 0 <= address <= EE_SIZE - 4:
        raise ReadError("address %#x is outside EE RAM" % address)
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if EE_MEMORY not in names:
                raise ReadError("%s has no %s (members: %s)"
                                % (path, EE_MEMORY, ", ".join(names[:6])))
            method = archive.getinfo(EE_MEMORY).compress_type
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReadError("cannot open %s as a savestate: %s" % (path, exc))

    if method == METHOD_ZSTD:
        raw = _read_word_zstd(path, address)
    else:
        with zipfile.ZipFile(path) as archive:
            with archive.open(EE_MEMORY) as handle:
                handle.read(address)      # no seek on a ZipExtFile
                raw = handle.read(4)
    if len(raw) != 4:
        raise ReadError("savestate's EE memory is truncated at %#x" % address)
    return struct.unpack("<I", raw)[0]


def _read_word_zstd(path: str, address: int) -> bytes:
    """Stream the member through `zstd -d` and stop once we reach the word."""
    if shutil.which("zstd") is None:
        raise ReadError(
            "this savestate's EE memory is Zstandard-compressed and neither "
            "python-zstandard nor the `zstd` command is available. Install "
            "one of them, or save the state from a PCSX2 build that uses "
            "deflate.")
    blob = _raw_member(path, EE_MEMORY)
    process = subprocess.Popen(["zstd", "-d", "--stdout", "-"],
                               stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
    assert process.stdin is not None and process.stdout is not None

    # Feed the compressed data on a thread: 16 MB will not fit in a pipe
    # buffer, so writing it inline would deadlock against our own reads.
    def feed():
        try:
            process.stdin.write(blob)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass                      # we stopped reading; that is expected

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()

    try:
        remaining = address
        while remaining:
            chunk = process.stdout.read(min(remaining, 1 << 20))
            if not chunk:
                raise ReadError("EE memory ended before %#x" % address)
            remaining -= len(chunk)
        raw = process.stdout.read(4)
    finally:
        # Including on the raise above. Without this the decompressor is left
        # running with an open pipe on every short read, and a tool that is
        # normally invoked several times in a row over one savestate leaks a
        # process each time.
        try:
            process.stdout.close()
        except OSError:
            pass
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:      # pragma: no cover - zstd hung
            pass
    return raw


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read the console's computed roster checksum from a "
                    "PCSX2 savestate.")
    parser.add_argument("savestate", help="path to a .p2s file")
    parser.add_argument("--address", type=lambda s: int(s, 0),
                        default=SLOT_ADDRESS,
                        help="EE address to read (default %#x)" % SLOT_ADDRESS)
    parser.add_argument("--compare", type=lambda s: int(s, 0), action="append",
                        help="a candidate value to compare against; repeatable")
    args = parser.parse_args(argv)

    try:
        value = read_word(args.savestate, args.address)
    except ReadError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    print("EE %#010x = %d  (0x%08x)" % (args.address, value, value))

    if value == 0:
        print()
        print("Zero. Either the roster check has not run yet in this savestate,")
        print("or the measurement patch is not active. Check the PCSX2 log for")
        print("'1 cheat patch is active', and take the state after the console")
        print("has shown the out-of-date prompt.")
        return 1

    for candidate in args.compare or ():
        mark = "MATCH" if candidate == value else "no"
        print("  %-12d (0x%08x)  %s" % (candidate, candidate, mark))
    return 0


if __name__ == "__main__":
    sys.exit(main())

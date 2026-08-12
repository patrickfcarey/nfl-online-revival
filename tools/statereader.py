"""Read a PCSX2 savestate's EE memory offline -- no emulator, no rig.

    python3 tools/statereader.py <state.p2s> word 0x001ef918
    python3 tools/statereader.py <state.p2s> classify-play
    python3 tools/statereader.py <state.p2s> roster

Why this exists: the DT-3 patch was deployed, the operator restarted the
emulator for it, and only afterwards did a fact-check agent prove the patched
branch could never be taken on any savestate this project owns -- by opening
the .p2s files and evaluating the engine's own play classifier against their
memory. That check took no rig time and would have saved the whole deploy
cycle. This tool makes the technique routine: **REACHABILITY BEFORE DEPLOY** --
docs/state-reachability.md is the protocol.

A .p2s is a ZIP whose entries use zstd (compress_type 93, which zipfile cannot
inflate); eeMemory.bin decompresses to the full 32 MB EE image where virtual
address == file offset. Decompression shells out to the system `zstd` binary
because the `zstandard` module is not installed here.

`SavestateReader.read(addr, n)` matches the harness Emu's signature, so the
whole typed layer works offline against a state:

    from tools.madden_lab.world import World
    w = World(SavestateReader("experiments/states/double_team_slot9.p2s"))
    w.los(), w.players(), ...

Limits, honestly: a savestate is one instant. Every state in experiments/
is PRE-SNAP, so anything the engine rewrites after the snap (lane 2 flagged
the play-class bytes as unverified post-snap) is not testable this way; a
reachability PASS here is necessary, not sufficient, and a FAIL is decisive.
"""
from __future__ import annotations

import struct
import subprocess
import sys
import zipfile

#: Lane 2's traced classifier (docs/dt3-review/2-play-type.md): the play-class
#: getter 0x0015ada0 walks offensive slots 0..10 reading an authored AI-state
#: byte from the play record and maps state ids to classes; first non-zero
#: class wins. 45/54/55 are the only mapped ids; everything else is class 0.
PLAY_RECORD_BASE_PTR = 0x00609770
PLAY_RECORD_SIDE_STRIDE = 0xAFBC
PLAY_RECORD_SLOT_BLOCK = 0x1400
PLAY_RECORD_SLOT_STRIDE = 40
PLAY_RECORD_STATE_OFF = 0x3F
STATE_TO_CLASS = {55: 3, 54: 2, 45: 1}          # byte+0; 45 -> 1 (or 5 ctx)
OVERRIDE_STATE = 45                              # byte+4 == 45 -> class 4


class SavestateReader:
    """Emu-compatible `.read(addr, n)` over a .p2s's EE memory."""

    def __init__(self, path: str) -> None:
        self.path = path
        # zipfile refuses method 93 outright, so the member is extracted by
        # parsing the local header ourselves -- see _extract_zstd_member.
        self.mem = self._extract_zstd_member(path, "eeMemory.bin")
        if len(self.mem) != 32 * 1024 * 1024:
            raise ValueError("eeMemory.bin decompressed to %d bytes; expected 32 MiB"
                             % len(self.mem))

    @staticmethod
    def _extract_zstd_member(path: str, name: str) -> bytes:
        """Decompress one method-93 member via the system zstd binary.

        zipfile parses the directory fine but raises on method 93, so seek to
        the member's local header, skip it, and stream the compressed bytes
        through `zstd -d`. The local header must be parsed (not the central
        directory offset alone) because filename/extra lengths vary.
        """
        with zipfile.ZipFile(path) as z:
            info = z.getinfo(name)
        with open(path, "rb") as f:
            f.seek(info.header_offset)
            hdr = f.read(30)
            if hdr[:4] != b"PK\x03\x04":
                raise ValueError("bad local header for %s" % name)
            name_len, extra_len = struct.unpack("<HH", hdr[26:30])
            f.seek(info.header_offset + 30 + name_len + extra_len)
            comp = f.read(info.compress_size)
        out = subprocess.run(["zstd", "-d", "-c"], input=comp,
                             capture_output=True, check=True)
        return out.stdout

    def read(self, addr: int, n: int) -> int:
        """Little-endian integer, matching the live Emu's read()."""
        return int.from_bytes(self.mem[addr:addr + n], "little")

    def bytes_at(self, addr: int, n: int) -> bytes:
        return self.mem[addr:addr + n]


def classify_play(reader: "SavestateReader", side: int = 0) -> int:
    """Evaluate 0x0015ada0's classification against this memory image.

    Returns the class the ENGINE would return for `side`'s called play. The
    mode==3 screen-id arm (dead in play, lane 2) is not modelled.
    """
    base = reader.read(PLAY_RECORD_BASE_PTR, 4)
    if not 0x00100000 <= base < 0x02000000:
        return -1                                   # no play built
    for slot in range(11):
        rec = (base + side * PLAY_RECORD_SIDE_STRIDE
               + PLAY_RECORD_SLOT_BLOCK + slot * PLAY_RECORD_SLOT_STRIDE)
        b0 = reader.read(rec + PLAY_RECORD_STATE_OFF, 1) & 0x7F
        b4 = reader.read(rec + PLAY_RECORD_STATE_OFF + 4, 1) & 0x7F
        if b4 == OVERRIDE_STATE:
            return 4
        cls = STATE_TO_CLASS.get(b0, 0)
        if cls:
            return cls
    return 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    reader = SavestateReader(argv[0])
    cmd = argv[1]
    if cmd == "word":
        for a in argv[2:]:
            addr = int(a, 16)
            print("0x%08X = 0x%08X" % (addr, reader.read(addr, 4)))
    elif cmd == "classify-play":
        for side in (0, 1):
            print("side %d play class: %d" % (side, classify_play(reader, side)))
    elif cmd == "roster":
        sys.path.insert(0, __file__.rsplit("/", 2)[0])
        from tools.madden_lab.world import World
        w = World(reader)
        print("los =", w.los())
        for p in w.players():
            s = p.snapshot(("position", "ai_state", "dt_role", "engagement"))
            print("  %d:%-2d pos %-3s state %-4s dt_role %-3s eng %s"
                  % (p.side, p.index, s.get("position"), s.get("ai_state"),
                     s.get("dt_role"), s.get("engagement")))
    else:
        print("unknown command %r" % cmd)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""The automation harness: emulator control, input, observation, trials.

Layers and seams are fixed in ``docs/lab-design.md``.
"""

from __future__ import annotations

#: Madden NFL 2004, SLUS_207.52. Every other CRC is a different game or a
#: different build, and every address in ``docs/`` is wrong for it. Asserted
#: before every run -- a wrong build produces plausible numbers, not an error.
EXPECTED_CRC = 0x14F8B841

#: Bumped when the result-row schema changes in a way that breaks old readers.
SCHEMA_VERSION = 1

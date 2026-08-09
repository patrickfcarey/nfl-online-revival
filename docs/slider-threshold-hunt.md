# Slider threshold hunt — briefing for the scan agents

> **OUTCOME (2026-08-08): the hunt ran and succeeded — results are in
> `slider-behavior.md`.** Read that first; several of this brief's anchors
> turned out to be red herrings (both attribute-name clusters are UI-only,
> and the storage signature guessed below was wrong — sliders are u16 in an
> options cache at 0x0051ffd8, not bytes). This file is kept as the record
> of what was asked and assumed.

Prepared 2026-08-08. This is the launch brief for a fan-out of agents scanning
the Madden NFL 2004 executable for the code that consumes the gameplay
sliders. The question being answered: **what does a slider value actually do?**
Where is each value read, what is it compared against or multiplied by, and
what are the thresholds and scale factors that turn "Interceptions = 12" into
behaviour on the field.

## What we are scanning, and how addresses work

The code is not on the ISO in any meaningful sense — it is the ELF, already
extracted:

    extract/SLUS_207.52   (5,354,036 bytes, MIPS R5900, statically linked, STRIPPED)

One LOAD segment: file offset `0x1000` maps to vaddr `0x00100000`, so

    vaddr = file_offset + 0xFF000

`strings -t x` prints file offsets; add `0xFF000` before feeding anything to
the disassembler. `gp = 0x006056f0` (established during the roster-checksum
work), so `lw v0, N(gp)` slots resolve to `0x006056f0 + N`.

The ISO itself is NOT on this machine, the rig (192.168.68.85) was unreachable
at prep time, and the NAS share has no Madden copy at its top levels. That
only matters for lane D below; every other lane needs only the ELF.

## Tools — use these, not hand-decoding

`recon/mipsdis.py` is the project disassembler and has already caught real
mistakes that hand-reads and other disassemblers made (see its docstring):

* `Elf32(path)` — maps vaddrs to file bytes.
* `dump(elf, vaddr, count)` — listing from a virtual address.
* `find_address_refs(elf, target)` — finds the `lui`/`addiu` pairs that
  materialise an address. This is how you get from a string or global to the
  code that uses it.
* `find_jal_targets(elf, target)` — every call site of a function.
* `find_immediate(elf, value)` — every instruction carrying a 16-bit
  immediate. This is the threshold-finder: it is exactly how the playbook cap
  (`sltiu s1, v0, 101`) was located.

```python
import sys; sys.path.insert(0, '.')
from recon.mipsdis import Elf32, dump, find_address_refs, find_jal_targets, find_immediate
elf = Elf32('extract/SLUS_207.52')
```

## Known pitfalls — each one has already produced a wrong answer once

1. **`movn`/`movz` are conditional moves.** Read as plain moves they invert
   the logic of a whole chain (this happened twice: the DNAS poller and the
   playbook error path — see `patches/14F8B841.pnach`).
2. **Branch-likely delay slots execute only when taken.** BEQL is opcode
   0x14; a third-party disassembler once had it off by one.
3. **Strings reached through tables produce no `lui`/`addiu` hit.** The
   playbook error messages were table-indexed at `0x00599d88`;
   `find_address_refs` on the string found nothing, on the *table* found the
   dispatcher. If a string has no refs, hunt for a nearby pointer table.
4. **UI decisions are not all in the ELF.** Menu flow lives in UI Studio
   bytecode (`uis_*.dat` on the ISO). The slider *screen* is there; only the
   slider *consumption* is in the ELF. Expect the slider values to arrive in
   gameplay code via a settings struct or gp-relative slots, not via any
   UI-labelled path.
5. **A constant is not a threshold until you've seen who compares it.**
   Report the comparison instruction and its callers, not just the immediate.

## Anchors found during prep (string sweep of the ELF)

Slider menu labels ("QB Accuracy", "WR Catching", "Break Block"…) do **not**
appear in the ELF — consistent with pitfall 4; they are uis_*.dat text. What
the ELF does carry:

| file offset | vaddr | content |
|---|---|---|
| `0x48cad0`–`0x48cc50` | `0x0058bad0`… | the full penalty-name table: ILLEGAL PROCEDURE, PERSONAL FOUL, ROUGHING THE KICKER/PASSER, INTENTIONAL GROUNDING, FALSE START, 15yd/5yd FACEMASK, ENCROACHMENT, OFFSIDES, DELAY OF GAME, CLIPPING, DEF/OFF PASS INTERFERENCE… These are the same categories as the penalty sliders. |
| `0x4aa620`–`0x4aa6d8` | `0x005a9620`… | mixed-case attribute names: Injury resistance, Kick return, Fumbling, Pass Blocking, Run blocking, Kick Accuracy, Kick power, Pass accuracy, Arm strength, Run Power, Tackling |
| `0x4b0bd0`–`0x4b0c90` | `0x005afbd0`… | ALL-CAPS attribute names: KICK RETURN, RUN BLOCK, PASS BLOCK, BREAK TACKLE, KICK ACCURACY, KICK POWER, THROW ACCURACY, THROW POWER, CARRYING, CATCHING, AWARENESS, ACCELERATION, STRENGTH |

The two attribute clusters are almost certainly player-rating names (they
match TDB rating columns), not the slider menu — but the code that references
them may sit near the code that *modifies* effective ratings from sliders,
which is one plausible slider mechanism (slider = rating adjustment) versus
the other (slider = probability threshold). Distinguishing those two models
is a core deliverable.

## Lanes for the agent fan-out

Each lane is independent; each agent gets the ELF path, the vaddr formula,
the tools section, and the pitfalls verbatim.

* **Lane A — the slider storage block.** Find where slider values live in
  memory. Sliders are per-side (Human/CPU) arrays of small integers, almost
  certainly loaded/saved together. Approach: find_address_refs on the penalty
  name table (or its pointer table) → penalty display code → walk to the
  penalty *decision* code, which must read a penalty-slider slot; the
  gameplay sliders are plausibly the adjacent struct fields. A block of
  consecutive `lbu/lb` reads with small offsets from one base is the
  signature.
* **Lane B — penalty thresholds.** Penalties are the cleanest slider→outcome
  path: a per-play roll against a slider-scaled chance. Find the comparison
  per penalty type; report the scale (what value makes FALSE START never/
  always fire).
* **Lane C — fumble / interception / catch / block shedding.** The core
  gameplay sliders. Start from the attribute-name clusters' referencing code
  and from `find_immediate` sweeps of plausible scale constants (50, 100,
  and the slider tick maximum once lane A establishes it — do NOT assume the
  range; measure it from the storage or the clamping code). A slider that
  works as a rating modifier will show up as `attribute + f(slider)` feeding
  an existing comparison; one that works as a probability will show up as a
  fresh comparison against a random roll.
* **Lane D (blocked, note only) — UI side.** The slider screen definition in
  `uis_*.dat` would give the exact slider list, order, and tick range for
  free, but needs the ISO, which is not on this machine and the rig is down.
  If the rig comes back: read-only SSH is always allowed; the live-session
  check protocol in the global rules applies to anything beyond that.

## Deliverable format — per finding

The pnach files are the house style: every claim pinned to an address with
the disassembly quoted, corrections kept inline. Each agent reports:

1. the instruction(s), quoted as `vaddr  disassembly`;
2. the chain from an anchor (string, table, or call site) to that
   instruction — every link named by address;
3. what the comparison means in slider terms (which value flips it);
4. confidence, and specifically whether any `movn/movz` or branch-likely
   sits in the chain (per pitfalls 1–2);
5. what was searched and NOT found, so lanes don't silently overlap.

Findings get verified by disassembly re-read before anything is written into
a pnach. Nothing in this hunt patches anything yet — it is reconnaissance.

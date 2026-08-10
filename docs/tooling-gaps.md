# Tooling gaps blocking the modernization work

Assessment 2026-08-10, after ~25 agent investigations across the gameplay
engine. This is not a wish list — each gap is scored by **how many
currently-blocked findings it would unblock**, counted from the docs.

## What we have

| tool | what it does |
|---|---|
| `recon/mipsdis.py` + `fpudis.py` | MIPS/R5900 disassembly and cross-referencing (four known defects; repair in flight) |
| `tools/pine.py` | live EE memory read/write over PCSX2's PINE socket |
| `tools/madden_tdb.py` | TDB database reader (rosters, settings, playbook tables) |
| `tools/lzh1.py` | EA `LZH1` codec — opens every UIS container on the disc |
| `tools/patch_iso_roster.py`, `mark_roster.py`, `build_*_roster.py` | roster build and ISO injection |
| `recon/*` + `tests/` (22 test files) | the protocol-track harness, well covered |

That is a good static-analysis and roster stack. The gaps are all on the
**dynamic** and **play-data** sides — which is exactly where the
gameplay work has piled up.

## Gap 1 — a play-data parser (biggest single unlock)

**Blocks ~14 documented findings.** Nothing in `tools/` or `recon/`
parses play files; a grep for the play tables returns nothing.

We already know the format from the investigations: the play object comes
from `0x00248338(side)`, per-player records via `0x00242848(play, idx, 0)`
at **stride 40** inside a 440-byte block, with the **assignment class at
+2**, the **AI script index at +11**, **landmark floats at +16 (or +24
when a play flag is set)**, and **AI state chains as `{id|0x80, p1, p2,
p3}` records at `blob + 40·idx + 63`** installed by `0x0024397c`. The
playbook tables (`PBAI`/`PBPL`/`PLYL`) are ordinary TDB, which
`madden_tdb.py` can already read.

What it would unblock immediately:

* the **assignment-class → AI-state mapping**, currently open in three
  docs — the thing that would let us say "this is a Cover 3 hook" instead
  of "state 38";
* the **pull-path depth data fix** (#5's hang-up) — the cheapest real fix
  we have found and the only one that is pure data;
* the **per-play targeting delay** the owner specified, authored per play
  rather than as a global constant;
* **AI-group play counts** (#18c) — turning "under 18 plays per group" from
  an inference off the template into a measurement of the shipped books;
* the authored `p1/p2/p3` values behind zone landmarks and rush lanes.

## Gap 2 — a runtime measurement harness

**Blocks ~30 documented items.** `pine.py` gives `read`, `write`,
`read_bytes`, `read_string` — the primitives. Everything above that is
missing, and every doc that says "watch this on the rig" is really asking
for the same four things:

1. **Pointer-chase reads.** Nearly every open item is expressed as
   `[[0x00601280] + 84]` or `player + 0xB70 + 2·attr`. Doing that by hand
   over a socket is where mistakes happen.
2. **Watch-with-change-detection.** "Move the slider and see which byte
   changes" is the single most common unresolved question — it would have
   settled the shipped slider defaults in minutes.
3. **Breakpoints.** This is the important one: **the cave
   "is-it-really-dead" test is the hard gate on every code patch we have
   proposed**, and it needs execution breakpoints, which PINE does not
   provide. Without it, `code-caves.md` stays theoretical.
4. **A player/struct accessor layer.** We know the player stride, the
   effective-ratings table layout, the engagement record, and the ball
   object. A thin typed view over those would make a measurement script
   five lines instead of fifty.

## Gap 3 — a patch build-and-verify pipeline

We have accumulated roughly **50 proposed patch words** across nine docs.
Every one was hand-assembled and hand-verified by round-tripping through
the disassembler. There is no tool that will:

* read the **current** word at an address and assert it matches what the
  doc claims (the cheapest possible guard against a stale address, and we
  have already had stale addresses);
* assemble a symbolic patch (`sltiu at, at, 15`) into a word rather than
  hand-encoding it;
* emit a pnach in the house style with the before/after comment;
* **verify a whole doc's patch table in one pass** against the ELF.

A ~200-line `tools/pnach.py` covering assemble / verify / emit would pay
for itself the first time it catches one wrong word, and it makes the
"apply one lever at a time and attribute the change" discipline
mechanical instead of manual.

## Gap 4 — a machine-readable address index

Nineteen documents now cite **500+ addresses**, and the only way to ask
"what do we already know about `0x001a66f8`?" is to grep prose. Two
concrete failures this caused: the same function was investigated twice
under different names, and two docs made **contradictory claims about
`+0x41C`** (dead field vs late-phase component) that nobody noticed until
a fact-checker was pointed at both.

A flat `addresses.json` (address → name, doc, one-line claim, confidence)
generated from the docs, plus a checker that flags an address described
two different ways, would catch that class of error automatically.

## Gap 5 — cross-title location tooling

Four ledger questions (#1, #3, #4, #11) are blocked on 2002/2003/2005-era
ISOs. When those arrive, the work is *"find the 2004 function in a sibling
binary"* — and we have the raw material for it: distinctive idioms like
the 24-bit BAM math, the `rating × 2.55` transform, the `>>4` quantiser,
and the TDB query strings. A signature-matching helper (locate by
constant-set and call-shape rather than by address) turns each of those
four from a fresh reverse-engineering project into a diff.

## Gap 6 — process: agent scratch contention

Not code, but it cost real time: **three separate lanes reported
overwriting each other's scratch files**, and one had to fork to a private
directory mid-run. Convention going forward is per-lane subdirectories.
Related and larger: **eight lanes each rebuilt the same enhanced
disassembler** before the repair now in flight — the standing lesson is
that a tool defect costs the *product* of its lifetime and the number of
agents who hit it.

## Recommended order

1. **Finish the disassembler repair** (in flight) — it is upstream of
   everything else and eight lanes have paid for it already.
2. **Play-data parser** — biggest unlock per hour, no hardware needed, and
   the format is already reverse-engineered.
3. **Patch verify** (the `assert current word == expected` half alone) —
   a few hours, and it protects every fix we ship.
4. **Runtime harness**, in two stages: pointer-chase + watch first (cheap,
   settles many small questions), breakpoints second (harder, but it is
   the gate on all cave work).
5. **Address index** — mechanical, and it gets more valuable with every
   doc added.
6. **Cross-title signatures** — only when the ISOs actually exist.

## One thing worth saying plainly

The static analysis has run ahead of the ability to check it. We can now
describe mechanisms in this engine down to the instruction, but almost
every doc ends with an item that needs a running game to settle — shipped
slider defaults, whether a cave is dead, how long a swat window lasts,
whether the wind sign is inverted, which of two competing explanations
matches the tester's observation. **The next marginal hour is worth more
spent on measurement tooling than on more disassembly.**

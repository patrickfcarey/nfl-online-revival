# Where play data actually lives

Established 2026-08-10 while building `tools/madden_play.py`. The short
answer is that there are **two unrelated play formats**, and the one
named after play data is not the one the engine's SQL queries read.

## The two formats

**1. The TDB playbook schema — custom playbooks only.**
`TEMPLATE.DAT` member 11 holds nineteen tables joined into a graph:

```
FORM  formation
  └── SETL  set (personnel/alignment)
        └── PLYL  play (type, flags, name)
              └── PLYS  one row per position
                    ├── PSAL  step chain   ← the per-position assignment
                    └── ARTL  route art    ← the drawn route, 5 points
PBPL  a book's inclusion list  →  PBAI  (play, AI group, weight)
```

This is what the engine's runtime SQL reads — `ai-play-calling.md` found
the play caller querying `PBAI` filtered on `AIGR`. **Every table in
`TEMPLATE.DAT` has zero rows**: it is the create-a-playbook *template*,
so the schema is real but the shipped content is not here.

**2. `DMF` blobs — the shipped play content.**
`DATA/PLADATA.DAT` (61 MB) is a TERF container of **1038 LZH1-compressed
members**. A member decompresses to a `DMF\0`-magic blob — 400 KB for the
first one — carrying skeleton and animation names (`rhip`, `lknee`,
`up_torso`) alongside play geometry. **It is not a TDB and contains no
playbook tables.**

Searched and not found: no populated playbook tables in `DB_TEAMS.DAT`
(232 members) or `GAMEDATA.DAT` (76 members) either. The TDB playbook
schema exists *only* as the empty editor template.

## What that means

The engine appears to serve two paths: shipped plays come from `DMF`
content, while a **custom** playbook is stored as a TDB and queried with
the SQL the reverse engineering found. That reconciles a puzzle from
`ai-play-calling.md` — the AI-group query is real, but the 175-row cap it
inferred is the *template's* cap, so it bounds custom books, not the
shipped ones.

**Reversing `DMF` is now the blocker** for the play-data half of the open
questions, and it is a separate job from this parser.

## What the parser does today

`tools/madden_play.py`:

* **reads TDB playbooks** — formations, sets, plays, the book's inclusion
  list, AI groups with weights, and per-position assignments resolved
  through `PLYS → PSAL/ARTL`. Verified against the template's schema;
  will produce real data the moment a populated playbook exists (a custom
  book from a memory card would do it).
* **`--scan`** classifies every member of a container *without*
  decompressing it. This matters: the existing `read_terf` decompresses
  eagerly, which on `PLADATA.DAT` is minutes of bit-at-a-time Python
  before the first answer. On `PLADATA.DAT` the scan reports 74 `DMF`
  members, 208 empty, and 756 of some further type not yet classified.
* CLI: `--list`, `--plays`, `--ai-groups`, `--play N`, `--scan`.

## Cross-reference: the QB's progression table — provenance unresolved

`qb-read.md` found the QB's receiver priority order at `playRecord + 28`:
five entries of `(receiver number, priority weight)`, read at
`0x00243cb0`. Weight 0 and weight 1 are unreachable to the CPU QB, so
whatever sets those numbers decides *which receivers the CPU will never
look at* — which is why this table is the most valuable thing a completed
play-data reader could expose.

**What this document used to say — that the table has no writer anywhere
in the ELF, and is therefore authored play content — is withdrawn.** The
sweep behind it could not follow a struct base across a call boundary. Re-run
2026-08-10 with cross-function tracking, records returned by the play-record
getter `0x00242848` **are** written at +28, from two sites:

```
002971cc  jal 0x00242848
002971d4  daddu a3, v0, zero          ; a3 = the returned record
00297278  sdl v0, 31(a3)              ; with the sdr below, bytes 24..31
0029727c  sdr v0, 24(a3)
002972b0  swc1 f0, 28(a3)             ; a float, at exactly +28
```

and the same pair again at `0x00298b34`/`0x00298b50`.

**The negative is dead; the conclusion is merely unproven.** Those writes
are a float and an eight-byte blob, which does not match the reader's
five-byte-pair interpretation — and `0x00242848` takes a record-kind
argument, so it plausibly hands back more than one record layout. Both
readings survive the evidence:

* the getter returns different record types, these writes belong to
  another one, and the progression really is authored; **or**
* it is one record, engine code populates +28, and the progression is not
  play-file data at all.

Settling it means establishing what `0x00242848`'s third argument selects.
Until then this table must not be cited as authored content, and no fix
should assume editing a play file would change it.

Note also that `playRecord+28` is the **runtime** record the engine
builds. Even under the authored reading, how it maps onto either on-disc
format is unresolved.

## Next steps, in order

1. **Reverse `DMF`** — the header is `DMF\0`, then what looks like a
   version word, a count, and a named-entry table beginning `FM2400`.
   Skeleton names appear early, so it may be a shared animation/geometry
   container with play data as one section rather than a pure play format.
2. **Find a populated TDB playbook** — a custom playbook saved to a
   memory card would validate the whole reader against real rows and cost
   nothing but a rig session.
3. **Establish what `0x00242848`'s record-kind argument selects.** This now
   comes before mapping `playRecord+28` to any on-disc source, because it
   decides whether that table has an on-disc source at all.
4. **Map `playRecord+28` to its source**, if step 3 says it has one — that
   is what would actually unblock editing the QB progression.

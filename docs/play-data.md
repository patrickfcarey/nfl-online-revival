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

## Cross-reference: the QB's progression table is play-file data

`qb-read.md` found the QB's receiver priority order — five entries of
`(receiver number, priority weight)` at `playRecord + 28` — with **no
writer anywhere in the ELF**. That is authored play content, and it is
the single most valuable thing a completed play-data reader would expose:
weight 0 and weight 1 are unreachable to the CPU QB, so *the reason backs
and tight ends get ignored in the flat is a number in the play file*, and
it is editable.

Note that `playRecord+28` is the **runtime** record the engine builds. How
it maps onto either on-disc format is unresolved.

## Next steps, in order

1. **Reverse `DMF`** — the header is `DMF\0`, then what looks like a
   version word, a count, and a named-entry table beginning `FM2400`.
   Skeleton names appear early, so it may be a shared animation/geometry
   container with play data as one section rather than a pure play format.
2. **Find a populated TDB playbook** — a custom playbook saved to a
   memory card would validate the whole reader against real rows and cost
   nothing but a rig session.
3. **Map `playRecord+28` to its on-disc source**, which is what actually
   unblocks editing the QB progression.

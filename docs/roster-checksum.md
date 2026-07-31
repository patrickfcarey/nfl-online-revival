# The roster checksum

Solved and implemented. This replaces the open question that used to live in
`docs/TODO-roster-checksum.md`.

## What it is

Madden NFL 2004 asks the server for a roster `DATE` and `CSUM` in the `news`
reply, computes the same checksum over the roster it currently holds, and offers
to download an update when the two differ. `CSUM` is an *announcement*, not a
challenge — the server states which roster it has, and the console decides
whether that matches its own.

That is why this had to be solved rather than patched around. Silencing the
prompt is easy; **serving a roster is not possible without it**, because a
replacement roster has to be announced with its own correct checksum or the
console will not accept that the update landed.

## The algorithm

Three routines, all read out of `SLUS_207.52` rather than inferred.

`0x0012a968` performs the comparison:

```
jal   0x0012a888        ; compute the local checksum
lw    v1, -19396(gp)    ; the CSUM the server sent
xor   v0, v0, v1
sltiu v0, v0, 1         ; 1 when identical
```

`0x0012a888` drives the loop. It opens a cursor with the query text at
`0x00579b90`:

```
use 'GAEL' declare <cur> cursor for select * from 'YALP'
where ('DIGT' >= 1) and ('DIGT' <= 32) order by 'DIGP'
```

Four-character names are stored byte-reversed, so that is database `LEAG`, table
`PLAY`, team ids 1–32, ordered by player id. Each row is fetched through
`0x0012a730` — which is **not** arithmetic, despite being the obvious suspect.
It is a varargs marshaller that hands the query VM the format string at
`0x00579958` together with 31 pointers spaced four bytes apart across a 124-byte
buffer. That format string is the authority for the field list and its order.

`0x0039d7e8(buf, len, seed)` accumulates:

```
nor  v1, zero, a2       ; crc = ~seed
lbu  v0, 0(t0)
srl  a1, v1, 8
xor  v0, v1, v0
andi v0, v0, 0x00ff
sll  v0, v0, 2
lw   v1, 0(v0+0x00565510)
xor  v1, v1, a1         ; crc = tab[(crc^b)&0xff] ^ (crc>>8)
nor  v0, zero, v1       ; return ~crc
```

The 256-entry table at `0x00565510` was compared entry by entry against the
reflected `0xEDB88320` polynomial: **exact match, zero mismatches**. So this is
ordinary zlib CRC-32 and `zlib.crc32` substitutes for it directly.

## The part that matters

The loop does not seed from 0, and it does not seed from `0xFFFFFFFF`. It seeds
from the **row count**, read as a 16-bit quantity:

```
0012a8e4  lhu   s0, 16(sp)        ; s0 = rows the cursor returned
0012a8e8  beq   s0, zero, +15
0012a8ec  daddu s3, s0, zero      ; loop bound
0012a8f8  jal   0x0012a730        ; fetch a row into the buffer
0012a900  daddu a2, s0, zero      ; seed = running value
0012a908  jal   0x0039d7e8
0012a90c  addiu a1, zero, 124
0012a914  daddu s0, v0, zero      ; running value
```

Everything else is a stock CRC that anyone would have guessed. Seeding it with
the row count is the part that guessing does not recover, and it is the reason
this needed reversing rather than trying a handful of standard variants.

There is a `0xFFFFFFFF` stored nearby, at `sp+8`, which looks like a
conventional CRC init and is not one — it is cursor-struct initialisation and
never reaches the accumulator. `a2` is loaded from `s0`.

## The columns

Thirty-one, in the order the marshaller passes them:

```
PGID TGID PPOS PACC PAGI PAWR PBTK PCAR PCTH PJMP PIMP PINJ PKAC PKPR PPBK PRBK
PSPD PSTA PSTR PTAK PTHA PTHP PTGH PKRT POVR PUCL PHGT PWGT PHAN PTEN PSTY
```

Order is load-bearing: the CRC runs over the buffer in memory order, so a
permutation produces a wrong number that looks exactly as plausible as a right
one. Each is widened to 32 bits little-endian regardless of its packed width.

## Where the data lives

`DB_TEAMS.DAT` is a **TERF container** — a 64-byte header, a `DIR1` block of
`(offset, size)` pairs, and 232 members whose offsets are relative to the *end*
of the directory rather than to the file. Member 0 is the free-agent pool
(`TGID` 1009), members 1–32 are the NFL teams, and the remainder are historical
squads.

Each member is a standard Madden `DB` with bit-packed records. `PGID` is 15 bits
starting at bit 317 of a 108-byte record; `TGID` is 10 bits at 332. Bit order is
least-significant-first both within a byte and within a field — the convention
the NCAA Draft Class Editor's reader established empirically against a
known-good Madden 08 sample, reused here rather than re-derived.

The runtime merges the members into one `LEAG` database, so the `TGID` filter is
what selects the current league regardless of which member a player sits in.

## Result

For the retail disc:

```
$ python3 tools/roster_checksum.py DB_TEAMS.DAT
rows              : 1743
teams             : 32
players per team  : 53-55
PGID range        : 1-17045
duplicate PGIDs   : 0
2164823612  (0x8108963c)
```

1743 players across 32 teams at 53–55 apiece is the shape an NFL roster should
have, and no duplicate `PGID` means `order by PGID` is unambiguous.

Serve it with `python -m backend --roster-db /path/to/DB_TEAMS.DAT`.

## What is still unproven

**No console has confirmed this value.** The algorithm is verified line by line
against the executable, but the checksum also depends on the roster extraction
producing byte-identical rows to what the query VM produces, and that has only
been checked for plausibility.

Two things could still be wrong without any of the above being wrong:

- the bit-extraction convention, inherited from a Madden 08 reader and assumed
  unchanged for 2004;
- the assumption that a freshly booted console's `LEAG` holds exactly these 1743
  rows, rather than something the save or an earlier update has altered.

The test is direct: run the server with `--roster-db`, leave the pnach's roster
line disabled, and see whether the console still claims its rosters are out of
date. Re-enabling that line distinguishes a wrong checksum from an unrelated
regression.

## Still open: delivery

Knowing the checksum does not yet mean a roster can be *sent*. When the console
was offered a mismatch it opened no new connection and resolved no new hostname,
so the transport for the download itself is still unidentified. That is the next
piece, and it is now the only thing between here and serving real rosters.

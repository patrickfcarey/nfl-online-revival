# Open: compute the roster checksum for real

## Where this stands

The `news` reply now always carries `DATE` and `CSUM`, but **the checksum is a
placeholder** (`PLACEHOLDER_ROSTER_CSUM` in `backend/handlers.py`). It is not
derived from any roster and will not match a console's own.

That is inert today only because `patches/14F8B841.pnach` zeroes the comparison
at `0x0012A978`, so the console never acts on the mismatch.

## Why it has to be solved properly

Not to silence a prompt -- the patch already does that -- but because **serving
a roster requires it**. A replacement roster has to be announced with its own
correct checksum, or the console compares, disagrees, and will not accept that
the update landed. Any real roster delivery is blocked on this.

## What is already known

The console derives its checksum at `0x0012a888`, which runs the game's own
query language over its record store. The query text is at `0x00579b90`:

```
use 'GAEL' declare <cursor> for select * from 'YALP'
    where ('DIGT' >= 1) and ('DIGT' <= 32) order by ...
```

Four-character names are byte-reversed throughout this protocol, so that reads
as **`select * from PLAY where TGID between 1 and 32`** -- every player on all
thirty-two teams, in the `LEAG` database.

Each row is accumulated through `0x0012a730`. The result is compared at
`0x0012a960`:

```
0012a968  jal   0x0012a888      ; local checksum
0012a970  lw    v1, -19396(gp)  ; the CSUM the server announced
0012a978  xor   v0, v0, v1
0012a97c  sltiu v0, v0, 1       ; 1 when identical
```

## What is not known

* **The accumulator at `0x0012a730` has not been reversed.** It is a large
  function (368-byte frame, many saved registers) that walks per-row fields --
  the ordering, the field set, and the mixing function are all unestablished.
* Which fields of `PLAY` participate, and in what order. The `order by` clause
  was truncated in the string dump and needs reading in full.
* Whether the checksum covers only `PLAY`, or whether other tables contribute.

## Suggested approach

1. Read the full query text at `0x00579b90` -- the `order by` determines row
   ordering, which any reimplementation must match exactly.
2. Reverse `0x0012a730` far enough to identify the per-row field list and the
   mixing step. A CRC-32 or a simple additive/xor accumulator are the likely
   shapes; a constant table nearby would settle it quickly.
3. Validate without guessing: the disc's own roster is a known input. Compute a
   candidate checksum from it offline and compare against what the console
   produces -- readable from the game's memory at the `-19396(gp)` comparison,
   or by having the server announce candidate values and watching which one
   stops the update prompt with the pnach line removed.
4. Once it matches, drop the pnach line and serve `CSUM` for real.

## Connection to existing work

The `PLAY` table in `LEAG` is Madden's TDB format, which the roster tooling in
`C:\GitHub\NCAA-Draft-Class-Editor` already reads. Whatever computes this
checksum should live near that code, not here -- this repo only needs the
resulting value to announce.

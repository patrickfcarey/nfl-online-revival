# Roster delivery

The complete path a roster update takes to reach a booted Madden NFL 2004
(PS2, `SLUS-20752`) console, from a file on this machine to bytes the console
displays on screen. Every mechanism described here has been exercised against
real hardware, not merely read out of the executable: an edited roster was
served, installed, and observed. This is the document to work from when
building and serving a new one, and the one to come back to when it fails.

The checksum the console compares its roster against is its own subject,
covered in full in `docs/roster-checksum.md`; this document assumes that
derivation and only summarizes it where the delivery mechanism depends on it.
The two documents are complementary, not redundant -- that one is about a
number, this one is about a file.

All addresses are virtual addresses in `SLUS_207.52` (one `PT_LOAD` segment,
vaddr `0x00100000` = file offset `0x1000`; `$gp` is fixed at `0x006056f0` for
this build, so a `gp+N` displacement below resolves directly by subtraction).
`recon/mipsdis.py` reads and disassembles them straight from the ELF -- every
address below was checked against it, not carried forward on trust.

## The two gates a console checks before it will ask for anything

A console decides for itself, before any request is sent, whether its own
roster is stale. Two *independent* tests decide that, both carried in the
`news` category-0 reply (`NAME=0` -> `new0`). They have to be satisfied
together. Passing one while leaving the other tripped still produces "your
rosters are out of date" on screen, which is what makes the two easy to
confuse -- the symptom is identical no matter which gate failed.

### CSUM

`CSUM` is an announcement, not a challenge: the server states which roster it
has, in the same units the console will compute for itself, and the console
compares. Full derivation, the field list, the row-count seed, the extraction
cross-checks and the hardware confirmation (`tools/read_roster_checksum.py`
reading a patched console's own computed value out of a savestate) all live in
`docs/roster-checksum.md`. In brief: the console runs a query equivalent to
`select * from PLAY where TGID between 1 and 32 order by PGID` against its own
record store and reduces the 1,743 resulting rows with an otherwise-ordinary
zlib CRC-32, seeded not with 0 but with the row count. The comparison itself is
short:

```
0012a960  addiu sp, sp, -16
0012a968  jal   0x0012a888        ; compute the local checksum
0012a970  lw    v1, -19396(gp)    ; the CSUM the server last announced
0012a978  xor   v0, v0, v1
0012a97c  sltiu v0, v0, 1         ; 1 when identical
```

`tools/roster_checksum.py` computes the same value from any file shaped like
`DB_TEAMS.DAT`; running the server with `--roster-db DB_TEAMS.DAT` (or
`--roster-csum` directly, if the value is already known) makes it announce a
`CSUM` the console agrees with.

### DATE

`DATE` looks like a second, symmetrical version stamp. It is not, and getting
it backwards announces staleness to every console that has never taken an
update -- which, for a disc that shipped without this service, is all of them,
however well `CSUM` matches.

The function that consumes it, `0x00350960`, is not a getter. Its answer is
computed in the branch delay slot:

```
00350960  lw   v0, 2452(gp)     ; v0 = the DATE we last announced
00350964  jr   ra
00350968  sltu v0, a0, v0       ; return (a0 < v0), unsigned
```

`a0` is not the console's build date, and it is not fixed. Its one caller is
`0x0034fb08`, which gets it from `0x0034f3f0` -- a one-instruction getter for
`gp+2208`:

```
0034f3f0  jr   ra
0034f3f4  lw   v0, 2208(gp)
```

`gp+2208` is written in exactly one place in the whole executable: while
parsing an incoming `news` reply, at `0x0034ecc4`-`0x0034ece0`, which runs
`TagFieldFind(buffer, "LAST")` (`0x0044acc8`) -- the field name is the literal
string `"LAST"`, sitting alone at `0x00606010` and referenced from nowhere
else -- converts whatever it finds, and stores the result at `gp+2208`.

This server never sends a field called `LAST`. `service_news` in
`backend/handlers.py` builds the `NAME=0` reply out of `NAME`, `BUDDY_URL`,
`BUDDY_PORT`, `FPLY`, `BGNR`, `ELIT`, `TWRP`, `DATE` and `CSUM` -- nothing
named `LAST`. So the lookup always finds nothing, the converter falls back to
its default, and `gp+2208` -- and therefore `a0` in the comparison above -- is
0 for the life of the process, regardless of what this server does or does
not announce as `DATE`.

The comparison is therefore always `0 < announced_DATE`, unsigned, which is
true for anything greater than zero and false only at exactly zero. `DATE=0`
is not a placeholder standing in for a value nobody has measured yet -- unlike
`CSUM`'s default, which is a stand-in and will not match -- it is the one
correct value to serve whenever there is no roster newer than what a console
already holds. `backend/handlers.py`'s `DEFAULT_ROSTER_DATE` and the
`--roster-date` default in `backend/__main__.py` both already do this; the
only reason to override it is to genuinely announce a newer roster.

## What LEAG actually is

Get this wrong and the checksum can be flawless while a served roster still
never reaches the console, because the console never loaded the file being
edited in the first place.

`LEAG` -- the database name the query engine and the download installer both
target -- is bound to exactly one thing: member 0 of `template.dat`. The
binding happens at boot, in two calls, both with exactly one call site in the
executable:

`0x002fa148` calls `0x003b6c48(a0=409600, a1=<pointer to "template.dat">,
a2=0)`. That function stashes all three arguments into three fixed slots --
`a0` (409,600) into `gp+10792`, the return of a lower-level open call into
`gp+10796` (the archive handle), and `a2` (0, the member index) into
`gp+10800` -- and does nothing else with any of them. Then `0x002fa150` calls
`0x003b6d48`, which checks whether `GAEL` (`LEAG` reversed, packed as the
32-bit immediate `0x4741454c`) is already registered and, if not, calls

```
003b6d68  lui  a0, 0x4741
003b6d74  ori  a0, a0, 0x454c    ; a0 = 'GAEL'
003b6d78  jal  0x004c54d0        ; register('GAEL', archive=gp+10796, index=gp+10800, 0)
```

The size query a download depends on, `0x003b6cf0`, reads exactly those same
two slots -- `gp+10796` for the archive handle, `gp+10800` for the member
index -- and returns the on-disc size of that one member: 253,044 bytes. It
never reads `gp+10792`. That resolves the "409,600 is a red herring" note in
`tools/build_roster.py`: the value is real and it really is stored, but it is
the in-RAM capacity handed to the one registration call at boot, and nothing
on the download path reads that slot again. (Confirmed directly: extracting
member 0 of `template.dat` with `tools/build_roster.py --extract-member 0`
produces a file of exactly 253,044 bytes, matching what `0x003b6cf0` reports
independent of any knowledge of the 409,600 figure.)

`DB_TEAMS.DAT` is a different file -- a 232-member TERF archive -- and no
member of it is ever registered as `GAEL`. It is nonetheless what
`tools/roster_checksum.py` computes `CSUM` from, and it gives the right
answer: extracting `template.dat` member 0 and running the identical query
against it (`select * from PLAY where TGID between 1 and 32 order by PGID`)
independently yields the same 1,743 rows and the same `0x8108963c`. Both files
carry the same shipped roster, so both check out. That is a coincidence of the
retail disc, not a property of the mechanism -- see the correction in
`docs/roster-checksum.md` for how long a wrong belief about this ("the runtime
merges `DB_TEAMS.DAT`'s members into `LEAG`") survived exactly because it kept
producing the right number.

The practical consequence: if a future roster is built by editing
`DB_TEAMS.DAT`, that edit changes nothing the console ever loads. The file
that has to change is `template.dat` member 0. If `DB_TEAMS.DAT` is kept
around only as the source `tools/roster_checksum.py` reads `CSUM` from, the
two now have to be edited in parallel, in the same way, or the checksum
announced and the roster served stop agreeing with each other -- which looks
like an entirely different failure (see "What a failure looks like," below)
from wherever this note is.

## The delivery chain

The lobby connection that carries every other message in this protocol never
carries roster bytes. A roster is a separate, ordinary HTTP transfer that the
console's own client performs; the lobby's only job is to tell it where.

1. The console asks `news` with `NAME=2`. The reply is typed `news` but
   carries `new2` in the *status* word -- the one place in this protocol where
   a reply is not tagged with the request's own type (`docs/protocol-notes.md`)
   -- and its body is not ordinary `KEY=VALUE`. It is a list: one record per
   *line*, fields separated by a **TAB**, not a space. Space does not work --
   `backend/handlers.py`'s value copy stops at any byte below 32, and 0x09
   (tab) is below 32 while 0x20 (space) is not, so a space-separated record
   is read as one giant value. Measured on hardware: the console requested
   `GET /roster.dat CRC=3617576383 NAME=Roster HTTP/1.0` -- it had swallowed
   the rest of the line into the URL. One record reads
   `URL=<absolute URL>` TAB `CRC=<decimal>` TAB `NAME=<label>`.
2. The console's own HTTP client fetches that URL. The literal strings
   `'http'` and `'get '` sit back to back at `0x004468f4`/`0x004468f8` --
   this is the client building its own request line. There is no proxy or
   redirect layer in between; the console dials whatever host the manifest
   named, directly.
3. On completion, `0x003527c0` verifies. It reads the manifest's own `CRC`
   back out of memory (`0x003525c8`: `TagFieldFind(..., "CRC")` at
   `0x0044acc8`, then the plain `atoi` at `0x0044c550` -- the same pair of
   primitives that reads `"URL"` two lines earlier in the same function),
   asks `0x003b6cf0` for the size of the member backing `GAEL`, and runs

   ```
   00352800  jal   0x0039d7e8       ; crc = CRC32(downloaded_buffer, size, seed)
   00352804  daddu a2, zero, zero   ; seed = 0
   ```

   over the downloaded bytes -- the *same accumulator function* that computes
   the roster `CSUM`, seeded with 0 here instead of the row count. That is
   deliberate, and it is exactly the pair of values not to get crossed: two
   different numbers, over two different inputs, produced by the same
   routine. Swapping them fails a download with the identical symptom as a
   corrupted transfer.
4. On a match, `0x00352828` calls `0x003b6e38`, which calls

   ```
   003b6e44  lui  a0, 0x4741
   003b6e50  ori  a0, a0, 0x454c    ; a0 = 'GAEL'
   003b6e54  jal  0x004c5598        ; install('GAEL', buf, size, 0)
   ```

   `0x004c5598` has exactly one caller in the entire executable -- this one.
   An HTTP download that passes both checks is the *only* route by which a
   league database is ever replaced on a booted console.

Implemented end to end in `backend/rosterfile.py` (the one-file HTTP server)
and `backend/handlers.py`'s `_roster_manifest`; run the server with
`--roster-payload <file>`.

## Three constraints, and why each one is exactly what it is

### Exactly 253,044 bytes

Larger and smaller fail differently, and both fail before anything resembling
an install happens.

Before the transfer starts, the client already knows the size it expects --
`0x003b6cf0`'s answer, 253,044 bytes on retail -- and checks the very first
thing the server sends:

```
00305f94  slt  v0, v1, v0     ; v1 = declared Content-Length, v0 = the expected size
00305f98  beq  v0, zero, ...  ; declared <= expected: proceed
```

A larger `Content-Length` is refused right there, on the header alone, before
a single byte of body is read. A *smaller* payload is not refused up front --
it is refused at the checksum, because `0x003527c0` always hashes the full
253,044-byte buffer it allocated for the expected size, not however many
bytes actually arrived. A short transfer hashes real data followed by
whatever the buffer already contained, which is not the CRC anyone computed
the manifest against.

This number is a property of the retail disc's `template.dat`, not a
constant baked into the game logic; it will change if that file ever ships
differently.

### A raw TDB, not a container

`0x004c9e90` requires the downloaded file's first word to equal `0x08004244`
exactly, and its fifth byte to be 0. On disk those first four bytes read
`44 42 00 08` -- ASCII `"DB"` followed by the version `0x0800`, stored
little-endian -- which is the ordinary Madden TDB header, not the `TERF`
container `DB_TEAMS.DAT` uses. `tools/build_roster.py`'s container-building
mode produces exactly the kind of file this check exists to refuse; only its
`--extract-member` mode -- or a `tools/mark_roster.py`-edited copy of that
output -- produces something installable.

The check reports which half failed, distinctly:

| What's wrong | Error |
|---|---|
| low 16 bits of the first word are not `0x4244` (`"DB"`) | 44 |
| high 16 bits of the first word are not `0x0800` (version) | 37 |
| byte 4 is not 0 | 37 |
| a block's declared length exceeds what's actually there | 36 |

(the last of these belongs to the same validation pass and is covered fully
in the next section, alongside its counterpart, error 43.)

### Two checksums, two seeds, two different things

Already stated above but worth isolating: the manifest's `CRC` is plain
`zlib.crc32(payload)`, seed 0. The roster's `CSUM` is the *same* accumulator
function, seeded with the row count instead. They are computed by the same
routine in the executable, over different inputs, for different purposes, and
nothing forces them to be consistent with each other if one is set by hand.
`backend/rosterfile.py`'s `load()` derives the manifest CRC directly from
whatever bytes are actually being served, which is the only way to guarantee
this never drifts; the `--roster-crc` flag exists solely to override it on
purpose, for a diagnostic transfer that is meant to fail at exactly this
check.

## The TDB carries its own checksums too

Passing the manifest's `CRC` gets a payload installed; it says nothing about
whether the payload is internally consistent. The TDB format has a second,
independent checksum layer, over its own blocks, checked as the file is
parsed (`0x004ca810`, against a 256-entry table built once at
`0x004c8718` and stored at `0x00573438` -- CRC-32 with polynomial
`0x04C11DB7`, MSB-first, initial value `0xFFFFFFFF`, **no** final XOR; the
polynomial constant is visible directly in the table builder as
`lui t0, 0x04c1` / `ori t0, t0, 0x1db7`). Editing a record without also
updating the checksum for the block it lives in fails with error 43, at the
comparison immediately after the recomputed CRC is produced.

The layout, recovered by reproducing the stored values on an untouched file
rather than by reading the checker function to the end (`tools/mark_roster.py`'s
`reseal()` is the implementation):

- bytes 0-19 are the file header; its checksum is the word at byte 20.
- the table directory, bytes 24 through `24 + 8*tables`, is checksummed into
  the *first* table header's own leading word -- the field a JavaScript
  reader for this format calls `priorcrc`, which is exactly what it is: the
  checksum of the block before it, not of the structure it sits inside.
- each table header's bytes 4-36 are checksummed into its own byte 36.
- each table's data block runs from its byte 40 for
  `field_count * 16 + record_bytes * max_records` bytes -- the table's full
  *capacity*, not the number of records actually in use -- with its checksum
  in the word immediately after. In a tightly packed retail file that word
  lands inside the *next* table header's leading word, which is why "run to
  the next header" looks like the right rule and is not: it only agrees with
  "run for the declared capacity" because the file happens to have no gaps.

A block whose declared length does not match what is actually available
(checked separately, inside the same helper that computes the block's CRC)
fails with error 36 rather than 43 -- a different failure from a wrong
checksum, and the one to expect from a record insertion or deletion that
changed a table's shape rather than a value inside one.

Resealing an unedited file is a byte-for-byte no-op, and the converse is
informative too: comparing the extracted disc member against
`tools/mark_roster.py`'s output for a single-surname edit, byte for byte,
shows exactly sixteen of 253,044 bytes differ. Twelve are the name field
itself -- six letters changed, and six trailing pad bytes going from the
disc's uninitialized filler to the tool's explicit zero fill, both spanning
the same width -- and the other four are the one checksum word `reseal()`
recomputed for the `PLAY` table's data block, sitting immediately after it,
exactly where the layout above says it has to. Nothing else in the file
moves. That is what "reseal block by block, not globally" is supposed to
guarantee, and it holds.

## The hazard: it deletes before it checks anything

Worth its own warning, independent of everything above being done correctly.

The install path deletes every table already in `LEAG`
(`0x004c9ee8`-`0x004c9f14`) *before* it opens the stream and before it reads
the magic word -- the deletion and the validation are both inside the same
function, `0x004c9e90`, and the deletion runs first. There is no rollback.

A transfer that is truncated, or a payload that fails validation partway
through, leaves the league database empty or half-built, and the only
recovery is rebooting the console -- not retrying the transfer, which starts
from an already-deleted table set. This is a reason to be careful about what
gets served at all, not only about whether the checks above would eventually
catch a bad payload.

## Proven on a console

Serving the disc's own member back to the console cannot demonstrate that an
install happened, even a fully successful one: the bytes that end up in
memory are identical to what was already there whether the transfer ran or
not, so there is nothing afterward to distinguish "installed" from "booted
normally, never touched." Demonstrating the install therefore required a
payload that differs from the disc, which is what `tools/mark_roster.py` is
for -- it rewrites one player's surname in place (record 0 of `PLAY`, whose
surname on the retail disc is `Kreutz`) and reseals every block checksum the
edit touches.

That payload was served and installed. Reading live console memory
afterward found twelve whole `PLAY` records -- 1,296 bytes -- byte-identical
to what had been served, and differing from the disc's own roster by exactly
the twelve bytes of the edited name. Nothing about that result is explainable
by the console having simply booted with its own disc roster; the served
bytes were the ones in memory.

## Building and serving a roster, step by step

```
# 1. Extract member 0 of template.dat -- the actual GAEL/LEAG payload, not
#    DB_TEAMS.DAT, and not anything build_roster.py's container mode makes.
python3 tools/build_roster.py TEMPLATE.DAT roster.dat --extract-member 0
#   roster.dat: 253044 bytes, crc32 <n> (0x...)
#   serve with --roster-payload; the server derives the CRC itself.

# 2. Edit. mark_roster.py is the worked, tested example: it rewrites one
#    player's surname and reseals every block checksum that edit touches.
python3 tools/mark_roster.py roster.dat marked.dat --surname ZZTEST
#   marked.dat: 253044 bytes, crc32 <n>
#   player 0 surname: 'Kreutz' -> 'ZZTEST'
#
#    A different kind of edit -- a stat, a team assignment, anything else
#    reachable through tools/madden_tdb.py's Table/Field objects -- means
#    writing the new bytes at the same field offsets rename_first_player
#    uses (madden_tdb.py's read_bits/Field give the offset and width; write
#    least-significant-bit-first, matching how they are read) and then
#    calling mark_roster.reseal() over the whole file, the same call
#    rename_first_player already makes internally. Skipping that step is
#    exactly what produces error 43 on install.

# 3. Serve it. Announce a real CSUM (from DB_TEAMS.DAT, or the exact value
#    for the roster actually being served, if it differs) and DATE=0 unless
#    genuinely announcing something newer than any console already holds:
python -m backend --advertise-host <rig-lan-ip> \
    --roster-db DB_TEAMS.DAT \
    --roster-date 0 \
    --roster-payload marked.dat
#   roster payload: 253044 bytes, CRC <n> (0x...)
#   roster URL: http://<rig-lan-ip>:10080/roster.dat
```

`--roster-crc` overrides the advertised CRC on purpose, for a diagnostic
transfer engineered to fail at the checksum rather than the size -- useful
for confirming a payload's *length* is accepted without risking an install.
`--roster-csum-sweep` still exists for the case of an unknown checksum (a
hand-built database not derived from the extraction above), serving a
different candidate on each login instead of costing a reboot per attempt --
but for anything built the way this section describes, `tools/roster_checksum.py`
already gives the exact value, and the sweep should not be needed.

## What a failure looks like

| Symptom | Likely cause |
|---|---|
| Console never opens a connection to the roster HTTP port at all | The `new2` manifest is empty -- no `--roster-payload` given, or the URL/CRC never made it into the running config. `_roster_manifest` sends a deliberately empty body in this case rather than a broken record, so nothing is offered. |
| Console reports a failed download, but it did open a connection and read something first | The manifest record was encoded the ordinary `KEY=VALUE` way instead of as a TAB-separated list line. That yields three records instead of one, and the console fetches the first, which has no `URL` at all. |
| "Your rosters are out of date" keeps appearing even though the announced `CSUM` matches what the console computes | `DATE` is nonzero. The two staleness tests are independent; satisfying `CSUM` alone is not enough. Serve `DATE=0` unless a genuinely newer roster is being announced. |
| The console never asks for an update at all, and `CSUM`/`DATE` both look right | Not a failure -- this is what "not stale" looks like. The manifest fetch only happens once a console decides its roster is old. |
| HTTP connection opens and closes almost immediately, well before a real transfer could complete | The served payload is larger than 253,044 bytes. `0x00305f94` refuses on the `Content-Length` header alone, before any body is read -- serving `DB_TEAMS.DAT` or a TERF container by mistake looks exactly like this. |
| The transfer runs to completion (the full byte count is logged as sent), but the console still reports a bad download | Either the payload is smaller than 253,044 bytes (the fixed-size hash reads past real data into whatever the buffer already held), or the manifest's `CRC` does not match `zlib.crc32(payload)` seeded at 0 -- check for a stale `--roster-crc` override. |
| Console rejects the file right after the transfer, before anything resembling an install | The payload is a TERF container (`build_roster.py`'s default/container mode), not a raw TDB. Only `--extract-member` output, or an edited copy of it, is installable. |
| Console rejects the file with what looks like a database-integrity failure after a hand edit | A record was edited without resealing. Any block whose bytes changed needs its checksum word recomputed (`tools/mark_roster.py`'s `reseal()`), or it fails with error 43. |
| The roster/league data is empty or corrupted in-game after a failed or interrupted transfer, and stays that way | The known hazard: the console deletes every `LEAG` table before it opens the stream or checks the magic word, with no rollback. Reboot the console; there is no in-session recovery. |
| A newly served roster does not seem to take effect, no matter what changes | A second `python -m backend` may have failed to bind while an older instance -- still holding the old roster -- keeps answering. `backend/__main__.py` takes an exclusive lock on its ports for exactly this reason; check the startup log for "a backend already owns ports." |
| An installed payload produces no visible change on screen | Expected if the served payload is byte-identical to the disc's own member -- there is nothing to distinguish an install from an ordinary boot. Use an edit that changes something visible (`mark_roster.py --surname`) to confirm an install is actually happening at all. |
| `CSUM` matches, `DATE` is 0, the manifest is well-formed, and it *still* does not work | Check that the roster being served and the roster `CSUM` was computed from are the same file. Editing `DB_TEAMS.DAT` while serving `template.dat` member 0 (or vice versa) satisfies neither checksum against the other -- see "What LEAG actually is," above. |

## What is not settled

- The pre-validation deletion loop (`0x004c9ee8`-`0x004c9f14`) is reached
  through a conditional branch (`bne s3, zero`, which skips deletion when a
  particular incoming argument is non-zero) that was not traced far enough to
  find out when, in practice, that argument is zero versus not. Treat "the
  console wipes before it validates" as established -- it matches every
  symptom observed so far -- but the exact circumstances under which deletion
  might be skipped, if it ever is, are not.
- `0x004c9e90` has two call sites in the executable (`0x004c684c` and
  `0x004c68b4`), not one, and the block-checksum machinery underneath it
  (`0x004c8828`, `0x004ca810`) has the shape of a general "load and verify a
  TDB" primitive rather than something written only for `GAEL`. Plausible but
  not confirmed: whether some other on-disc database load reuses this same
  path, which would matter if a future project needs to load anything besides
  a roster this way.
- Serving a second, different roster to a console that already installed one
  earlier in the same session has not been tested, and neither has updating a
  console that already holds a previously-customized roster rather than the
  disc default. `docs/roster-checksum.md` flags the closely related question
  of whether a save file can alter what a booted console holds; it is still
  open there too.
- The bit-packing convention `tools/madden_tdb.py` reads records with
  (least-significant-bit-first, both within a byte and within a field) is
  sourced from a known-good reader for a different Madden title, not
  re-derived from this executable. It has held up against every cross-check
  run against it so far, which is evidence, not proof.

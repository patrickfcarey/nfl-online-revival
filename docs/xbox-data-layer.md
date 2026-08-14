# The Xbox data layer: does the PS2 data tooling read it?

Run 2026-08-14, static, off the disc image only — no rig, no emulator, no
console. This is phase X1's second half: X0/X1 established a **shared build**
from the executable side (14/14 fourccs, byte-identical `ptrk` weight tables —
`docs/xbox-madden-2004-plan.md`). The question here is the other half, and it is
the cheaper and more decisive one: **do the readers we already have parse Xbox
data unchanged?**

**Verdict: yes, all of them, with nothing ported.** Every PS2 data tool in
`tools/` reads the Xbox disc's files without a line changed, and the two most
load-bearing files are not merely compatible but **byte-identical across
platforms**. The data layer is not "similar" — for the roster and playbook
paths it is the same bytes.

## Provenance

| what | where |
|---|---|
| Xbox image | `extract/xbox/Madden NFL 2004 (USA)/Madden NFL 2004 (USA).xiso.iso`, 3,114,663,936 B |
| Xbox files | `extract/xbox/DATA/` (gitignored), all re-verified against the image |
| PS2 files | `extract/DB_TEAMS.DAT`, `extract/TEMPLATE.DAT`, `extract/GAMEDATA.DAT`, `extract/PLADATA.DAT`, `extract/UIS_SETT.DAT`, `extract/UIS_PAUC.DAT` |
| reader | `recon/xdvdfs.py` (new), tested by `tests/test_xdvdfs.py` |

**Finding — the prior run's extractions are sound and were reused, not
re-extracted.** All seven files a previous session left in `extract/xbox/DATA/`
match both the inventory's declared sizes *and* a SHA-256 of the image bytes at
their declared sectors. Six further small files were extracted for this work
(`UIS_GRP_PAUSECOMMON.DAT`, `STREAMEDDATA.DB`, `ICONS.DAT`, `FACEGEOM.DAT`,
`UIS_LIB_DUMMY.DAT`, `UIS_CRED.DAT`). Nothing large was extracted: `PLADATA.DAT`
is sampled at its first 2 MB and `CAMEO*`/`SOUND*`/`SPCH*`/`MOVIE*`/`XSELL` were
never touched.

## The instrument: `recon/xdvdfs.py`

Stdlib-only XDVDFS (XISO) reader in the `recon/` style — detect the game
partition, parse the volume descriptor, walk the directory btree, list and
extract, with a `list`/`extract` CLI. The format as observed is documented in
the module docstring; two things are worth repeating here because both cost
time.

**Finding — the directory tree walks clean and completely.** The walk yields
exactly the 66 files in `extract/xbox/inventory.txt`, with matching start
sectors and sizes and no others. An independent *linear* scan of both directory
tables (stepping entry by entry rather than following child pointers) finds the
same 67 records, root's `/DATA` included. Two methods, one answer, closed set.

**Correction — the `0xFFFF` story I first wrote down was wrong.** The root
table's second entry looked like it carried `left = 0xFFFF`:

```
0000  00 00 05 00 5d 0a 00 00 00 10 00 00 10 04 44 41  |....].........DA|
0010  54 41 ff ff 00 00 00 00 09 01 00 00 00 a0 4a 00  |TA............J.|
0020  20 0b 64 65 66 61 75 6c 74 2e 78 62 65 ff ff ff  | .default.xbe...|
```

Those `ff ff` bytes at offset 0x12 are not a field. The `DATA` record ends at
byte 18 and the next must start 4-byte aligned, so they are **inter-entry
alignment padding**. `default.xbe` starts at byte 20 with `left = right = 0`.
Re-derived across all 67 records: **no child field on this disc is ever
`0xFFFF`** — the disc spells "no child" as 0 and uses `0xFF` only as fill. The
reader accepts both spellings anyway, since other authoring tools emit `0xFFFF`,
and its end-of-table test reads the full 14-byte header rather than four bytes —
a leaf written the other way begins `ff ff ff ff` and would otherwise be thrown
away as padding. That defect was real, was in the first draft, and was caught by
the synthetic fixture in `tests/test_xdvdfs.py`, not by the disc.

## What works unchanged

Every row is "ran it, here is what came back."

| tool | Xbox input | result |
|---|---|---|
| `tools/madden_tdb.py` | `DB_TEAMS.DAT` | TERF OK, 232 members, **232 parse as TDB** — same as PS2 |
| `tools/madden_tdb.py` | `DB_TEMPLATES.DAT` | TERF OK, 12 members, member 0 = 253,044 B TDB with `DCHT/INJY/PLAY/TEAM` |
| `tools/madden_tdb.py` | `GAMEDATA.DAT` | TERF OK, 76 members (128-byte TERF header — handled) |
| `tools/roster_checksum.py` | `DB_TEAMS.DAT` | 1,743 rows, 32 teams, 53–55 per team, **`0x8108963c`** |
| `tools/lzh1.py` | `GAMEDATA.DAT` | 76 members, 6,014,131 B out, every declared size matched |
| `tools/lzh1.py` | `PLADATA.DAT` (head) | codec-5 members decompress to their exact declared sizes |
| `tools/lzh1.py` | `UIS_GRP_SETTINGS.DAT` | 3 members, 219,233 B out |
| `tools/lzh1.py` | `UIS_GRP_PAUSECOMMON.DAT` | 27 members, 2,521,182 B out |
| `tools/lzh1.py` | `ICONS`, `FACEGEOM`, `UIS_LIB_*` | 17 / 21 / 1 members, all clean |
| `tools/build_roster.py` | `DB_TEMPLATES.DAT --extract-member 0` | 253,044 B, crc32 `0x6506bb5e` |
| `tools/mark_roster.py` | that payload, `--surname ZZTEST` | reseals; **exactly 16 bytes change** |
| `tools/madden_play.py --scan` | `GAMEDATA.DAT` | 64 tdb / 12 other — identical census to PS2 |

**Zero readers needed porting. Zero readers needed a flag. No divergence in any
reader's error path, because no reader took one.**

`tools/roster_checksum.py`'s answer deserves emphasis: `0x8108963c` is the value
`docs/roster-checksum.md` derived from the PS2 executable and confirmed against
a patched console's own computed checksum. The Xbox disc's roster produces the
same number because it is the same roster.

## The container-format verdict: identical

The nesting `docs/roster-delivery.md` and `tools/madden_tdb.py` describe holds
on Xbox with no change at any layer.

- **TERF** — same magic, same chunk chain `TERF → DIR1 → [COMP] → DATA`, same
  little-endian words, member offsets still relative to the end of the
  directory block. Member count still the u16 at chunk offset 0x0E.
- **DIR1 / COMP** — same `(offset, size)` and `(codec, uncompressed_size)`
  pairs.
- **LZH1 (codec 5)** — bit-identical behaviour. Every compressed member
  decompressed to its declared size exactly; `read_terf`'s internal
  `assert len(r) == usz` never fired on any Xbox file.
- **TDB** — same `DB` magic with version `0x0800` (first word `0x08004244`,
  byte 4 zero — the very check `0x004c9e90` enforces), same 24-byte header,
  same 8-byte directory entries, same 40-byte table headers with 16-byte field
  definitions, same LSB-first bit-packed records. Field names still stored
  unreversed in field definitions while 4CCs are reversed elsewhere.

**Endianness is unchanged, and that is the load-bearing part.** MIPS PS2 and
x86 Xbox are both little-endian here, and every structure the readers touch is
little-endian on both. No byte-swap layer is needed anywhere.

**The one format-level delta, and it is cosmetic.** The `TERF` header chunk is
64 bytes on PS2 and **128 bytes on Xbox** for `GAMEDATA.DAT` and `PLADATA.DAT`
(the u16 at payload offset 4 states it: `0x40` vs `0x80`). `DB_TEAMS.DAT` and
the template file use 64 bytes on both. This breaks nothing: both readers locate
`DIR1` from the chunk's own size field rather than from a constant, so the
larger header is followed correctly and transparently. A reader that had
hardcoded 64 would have failed here — ours did not.

## File by file

### `DB_TEAMS.DAT` — byte-identical

```
sha256 1bd9b82b0ae49c9f3493a90cbfd7f75c35d63c27f562bfcb516aa39979294baf
8,439,360 bytes on both discs
```

**Finding.** The same file ships on both platforms. 232 TERF members, all TDBs,
1,743 players across teams 1–32, checksum `0x8108963c`.

### `TEMPLATE.DAT` (PS2) / `DB_TEMPLATES.DAT` (Xbox) — same file, two edits

2,723,072 B vs 2,722,752 B, a 320-byte difference, and it decomposes completely.

**Finding — member 0, the `LEAG`/`GAEL` payload, is byte-identical.**
253,044 bytes, `sha256 6dff9bf1…`, same four tables, `PLAY` with the same
geometry (108-byte records, 863 bits, max 2048, 1990 rows, 112 fields).
`build_roster.py --extract-member 0` produces the *same file with the same
CRC32* from either disc. This is the single most important result in this
document; see "The roster pipeline" below.

**Finding — 10 of 12 members are byte-identical.** Members 7 and 9 differ, and
both were run down:

- **Member 9 — no real difference at all.** All 6 tables have identical
  geometry, identical field lists and identical record counts, and every
  differing record differs *only* in bytes past a string field's NUL
  terminator. Checked mechanically: no numeric field's value differs, no string
  differs before its terminator, and no byte outside a declared field differs.
  The filler is MSVC's debug-heap signature — `0xCD` uninitialized, `0xDD`
  freed, `0xFD` guard — mixed with stale pointers from the authoring machine
  (`98 c2 33 05`, `20 b1 33 05`, `cd f7 a6 01`). Two runs of the same authoring
  tool on different days; the data is the same. Table `OSUI` in member 7 is the
  same story.
- **Member 7 — one real difference, and it is the credits.** 69 of 70 tables
  are byte-identical or padding-only. Table `TCcl` has **82 records on PS2 and
  81 on Xbox**, at 332 bytes per record — which is exactly the member's 332-byte
  size delta, so this single row accounts for the whole file difference. The
  content is legal text: PS2 carries `'Development tools and related technology
  provided under license from Logitech…'` and Xbox does not, with the rest of
  the credits shifted up one slot.

**A platform delta in the credits screen is the entire divergence in the
template database.** Nothing in the roster, ratings, schedule, franchise or
playbook schema differs.

### `GAMEDATA.DAT` — 70 of 76 members byte-identical

**Finding — asset #69 is byte-identical.**

```
sha256 82b087e3ab1feffa5380b87083721c1a7ad522cc809748e78f647aa2a6476852
28,301 bytes, both platforms
```

This is the situational-policy script `docs/ai-coach-playcalling-requirements.md`
calls out as work item C2 and `xbox-madden-2004-plan.md` listed as "plausibly
byte-identical across platforms." It is not plausible now; it is measured. Any
decompiler, disassembler or re-authoring tool built for that bytecode on PS2
applies to Xbox unchanged, and a re-authored script is a **single artifact for
both platforms**.

The 6 divergent members are 70–75, all `MMAP`-magic blobs (front-end/menu
layout), sizes 2,156–132,636 B. Platform-specific UI, not gameplay data.

### `PLADATA.DAT` — same container, platform-specific payload

This is the one genuine payload divergence, and its shape is clean.

| | PS2 | Xbox |
|---|---|---|
| file size | 61,772,800 | 262,631,936 |
| members | 1038 | 1038 |
| empty members | 208 | 208 — **the same index set** |
| codec 5 / codec 0 | 774 / 264 | 790 / 248 |
| payload magic | `DMF\0` | `TMdl` |

**Finding — the container is shared and the geometry format is not.** Sampling
the 225 members whose compressed bytes fall inside the first 2 MB of each file:
both give 208 empty, 16 model members and 1 `MMAP`, at the same indexes. Every
sampled Xbox member decompressed with `tools/lzh1.py` to its exact declared size
(e.g. member 1: 79,478 → 226,976 B). But PS2 members open `DMF\0` and Xbox
members open `TMdl`.

**Hypothesis:** `TMdl` is the Xbox's model/geometry container and the divergence
is GPU-format driven — the expected platform delta for art assets, and the
reason the file is 4× larger. This is *not* a blocker for the gameplay port:
`docs/play-data.md` treats reversing `DMF` as an open job that nothing currently
depends on, and per the finding below the shipped playbook data is not in this
file anyway.

### `UIS_*` — same format, different screens

PS2 `UIS_SETT.DAT` (42,168 B) and Xbox `UIS_GRP_SETTINGS.DAT` (42,380 B) are
both 3-member LZH1 TERFs whose members open with the same header words;
`UIS_PAUC.DAT` / `UIS_GRP_PAUSECOMMON.DAT` are both 27-member containers. Sizes
and content differ — different controller glyphs, different options screens.
Format identical, content platform-appropriate. `lzh1.py` reads both.

Note the naming: the PS2 disc uses ISO 9660-constrained 8-character names
(`UIS_SETT`, `UIS_PAUC`), the Xbox disc uses XDVDFS long names
(`UIS_GRP_SETTINGS`, `UIS_GRP_PAUSECOMMON`). Any port of a PS2 script that
hardcodes a filename needs the mapping; nothing else does.

### `STREAMEDDATA.DB` — an Xbox file that is a member of another file

**Finding.** `/DATA/STREAMEDDATA.DB` (862,948 B) is **byte-identical to
`DB_TEMPLATES.DAT` member 7** (`sha256 bcee008c…`), shipped loose as a raw TDB
with `DB\0\x08` magic. Found by noticing the size coincidence in the inventory
and testing it. The PS2 disc has no such file among what is extracted here,
though whether the PS2 disc carries one at all is **not established** — there is
no PS2 disc inventory on this machine, only individual files.

## The roster pipeline ports with zero changes

`docs/roster-delivery.md` describes a chain with several places to go wrong.
Every constraint in it survives the platform change intact:

| constraint (PS2, from `roster-delivery.md`) | on Xbox |
|---|---|
| `LEAG`/`GAEL` is member 0 of the template file | same member, byte-identical |
| the payload is exactly 253,044 bytes | 253,044 |
| it must be a raw TDB, first word `0x08004244`, byte 4 zero | confirmed on the extracted member |
| `CSUM` = row-count-seeded CRC-32 over 1,743 rows | `0x8108963c`, same value |
| block checksums reseal per block, not globally | `mark_roster.py` reseals; 16 bytes change, exactly as documented |

The strongest single check: `build_roster.py --extract-member 0` on the Xbox
`DB_TEMPLATES.DAT` and on the PS2 `TEMPLATE.DAT` produce **the same 253,044
bytes with the same crc32 `0x6506bb5e`**, and `mark_roster.py --surname ZZTEST`
over each produces the same marked file (`sha256 dfc57462…`) with exactly the 16
differing bytes `roster-delivery.md` documents — twelve for the name, four for
the recomputed `PLAY` data-block checksum.

**So a roster built by `tools/build_year_roster.py` is a single artifact valid
on both platforms.** What does *not* port is the delivery mechanism, and that is
already known and already out of scope: the Xbox build has no XNET/XONLINE and
no EA-server client (X0), so there is no `news` reply, no `CSUM`/`DATE`
handshake and no HTTP install path. On Xbox a new roster has to reach the game
some other way — baked into the disc image or the game's files. The *file* ports;
the *transport* does not exist.

## Correction owed to another document

**Finding — `docs/play-data.md`'s GAMEDATA negative is refuted.** It states:
"Searched and not found: no populated playbook tables in `DB_TEAMS.DAT` (232
members) or `GAMEDATA.DAT` (76 members) either. The TDB playbook schema exists
*only* as the empty editor template."

`GAMEDATA.DAT` holds the shipped playbooks, **on both platforms, identically**.
64 of its 76 members are LZH1-compressed TDBs, and they are populated:

```
member 4:  FORM 8   SETL 15   PLYL 157   PLYS 1727   PSAL 510
           ARTL 148  PBPL 157  PBAI 447   PBST 12
totals across all 64 TDB members:  PBAI 36,531 rows   PLYL 10,060 rows
```

Identical counts on PS2 and Xbox. `PBAI` is the AI-group weight table
`docs/ai-play-calling.md` found the play caller querying — 36,531 shipped rows
of it, not the template's zero.

Why it was missed, and why this is a rule-4 case rather than a rule-5 one: the
members are LZH1-compressed, so `madden_tdb.Container.database()` — which tests
the *stored* bytes for `DB` magic — returns `None` for every one of them, and a
raw scan of the file finds nothing. `tools/madden_play.py --scan` already
reported "64 tdb" and was right; `--list` and `--plays` report "0 playbook
members of 76" because `playbooks()` goes through the same uncompressed path.
That gap is **pre-existing, PS2-side, and platform-independent** — the tool
behaves identically on `extract/GAMEDATA.DAT`. `docs/fact-check-2026-08.md`
already flagged this negative as unverifiable for want of game data on this
machine; the data is here now and the negative does not hold.

Scope-tested per rule 1: this lives in `tools/madden_play.py`'s
`playbooks()`/`Container.database()` path, not in anything this task changed, so
it is **captured, not fixed**. It is a decision for the Architect — but it is
load-bearing for the coach-AI work on *both* platforms, since it means the
shipped `PBAI` candidate pool is readable today with a two-line change to how
members are opened.

## What this implies for the port

1. **The data layer is not a porting cost. It is zero.** Every reader in
   `tools/` runs on Xbox data as-is. Budget nothing for it.
2. **The roster/playbook artifacts are cross-platform.** The `LEAG` payload,
   `DB_TEAMS.DAT` and the playbook TDBs are the same bytes. Anything built or
   edited once serves both discs.
3. **Asset #69 is the same 28,301 bytes.** The coach-AI's C2 work item —
   decompile and re-author the situational-policy bytecode — is done once, not
   twice. This is the largest single confirmation of the "anchor on data" thesis
   so far, because it is the one asset the coach-brain design actually needs.
4. **Divergence is confined to presentation.** Credits text, front-end `MMAP`
   blobs, UI screens, and the model/geometry payload in `PLADATA.DAT`. No
   gameplay table, rating, weight or schema differs anywhere we looked.
5. **Roster *delivery* remains unported and unportable** in its current form —
   no online stack on Xbox. A patched-image or patched-files route is the only
   one; that is an X3/X4 question, not a data-layer one.

## What was not checked

Closed sets only where they are closed, and this one is not.

- **`PLADATA.DAT` beyond its first 2 MB.** 813 of 1038 members were never
  sampled on either disc. The `DMF`/`TMdl` split is established for 16 sampled
  members per platform, not for all of them.
- **Files with no PS2 counterpart on this machine** were format-checked but not
  compared: `ANIMDATA.DAT`, `COACHES.DAT`, `COACFACE.DAT`, `STADATA.DAT`,
  `FIELDART.DAT`, `LOADDATA.DAT`, `PLYRFACE.DAT`, the `.QKL` files. A PS2 disc
  inventory would close this.
- **The huge assets** (`CAMEO*`, `SOUND*`, `SPCH*`, `MOVIE*`, `XSELL`,
  `STADIUMS`, `UIS_LIB_FMV`, `UIS_LIB_MCARD_FULL`) were deliberately not
  extracted.
- **Whether the PS2 disc also ships `STREAMEDDATA.DB`.**
- **`TMdl`'s internal format.** Only its magic is known.
- Everything here is static. Nothing has been observed on hardware or under
  xemu, and no claim above depends on runtime behaviour.

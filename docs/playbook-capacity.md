# Playbook capacity — how many plays fit, and where the ceiling lives

Ledger item **C1** (`ai-coach-playcalling-requirements.md` §7), answered from the
shipped bytes rather than from the editor template. Run 2026-08-14, static, no
rig. Also closes the **data half of B2** (defensive personnel packages).

**The headline, in one line:** a shipped team playbook already holds up to **243
plays** — 34 of the 38 full-size offensive books are over 200 — and the largest
single formation observed holds **114 plays** (Single Back), with **I-Form
topping out at 84**. The ~175 figure is not a play cap and not an ELF constant:
it is the `PBAI` **row cap in the create-a-playbook template's own table
header**, and the play cap next to it is **100**.

## Provenance

| what | where |
|---|---|
| shipped playbooks | `extract/GAMEDATA.DAT` (PS2) and `extract/xbox/DATA/GAMEDATA.DAT` (Xbox) |
| the editor template | `extract/TEMPLATE.DAT` member 11 |
| executable | `extract/SLUS_207.52`, vaddr = file offset + `0xFF000` |
| readers | `tools/madden_tdb.py` (patched here), `tools/lzh1.py`, `tools/madden_play.py` (unchanged), `recon/mipsdis.py` |

**Finding — the two discs carry the same playbooks, byte for byte.** All 76
members of `GAMEDATA.DAT` were decompressed on both platforms and compared:
**70 of 76 are byte-identical**, and the 6 that differ (members 70–75) are all
`MMAP` front-end blobs. **Every one of the 64 playbook databases is identical
across PS2 and Xbox.** Every number below therefore holds on both.

## The unblocker: the reader now opens packed members

`tools/madden_tdb.py` is patched, and this is the enabling change for everything
else here. Two defects, both pre-existing, both PS2-side and
platform-independent:

1. **The `DB` magic test ran on the *stored* bytes.** 67 of `GAMEDATA.DAT`'s 76
   members are LZH1-packed (codec 5), so `Container.database()` returned `None`
   for every playbook in the file.
2. **Member offsets were measured to the end of `DIR1`.** That is correct only
   when there is no `COMP` chunk. With one — as in `GAMEDATA.DAT`, `PLADATA.DAT`
   and the `UIS_*` files — the byte after `DIR1` is `COMP`, not `DATA`, so every
   member offset landed 640 bytes early even before decompression.

The reader now walks the chunk chain (`TERF → DIR1 → [COMP] → DATA`), takes the
member base from `DATA`, decompresses on demand (cached, so opening one member
of `GAMEDATA.DAT` costs 0.24 s rather than the 12 s an eager walk costs), and
**raises on a codec it cannot decode instead of returning `None`** — "I cannot
open this" and "this holds no database" are different answers, and conflating
them is exactly what produced the false negative.

The public API is unchanged; `codec()`, `stored()`, `member()` and `compressed`
are added. `tests/test_madden_tdb.py` (19 tests) covers both defects with
hand-built TERF containers and hand-built LZH1 streams — the fixture's packer is
verified against the shipped decompressor so a wrong fixture cannot agree with a
wrong reader. Regression check: `tools/roster_checksum.py` on `DB_TEAMS.DAT`
still gives `0x8108963c` and `build_roster --extract-member 0` still gives
crc32 `0x6506bb5e`; 253 tests across the eight affected modules pass.

`tools/madden_play.py` needed **no change** — `--list`, `--plays` and
`--ai-groups` all work on the shipped books through the fixed container.

## 1. What a shipped playbook holds

`GAMEDATA.DAT` members 4–67 are the 64 playbook databases. Totals across all 64:

| table | rows | | table | rows |
|---|---:|---|---|---:|
| `PLYS` | 110,660 | | `PLCM` | 5,977 |
| `PSAL` | 62,033 | | `SPKG` | 5,846 |
| `PBAI` | 36,531 | | `PLPD` | 5,419 |
| `SETG` | 24,677 | | `SPKF` | 3,223 |
| `ARTL` | 14,615 | | `PLRD` | 2,870 |
| `SETP` | 13,695 | | `PBST` | 1,291 |
| `PBPL` | 10,066 | | `SETL` | 1,245 |
| `PLYL` | 10,060 | | `PBAU` | 902 |
| `SGF` | 6,793 | | `FORM` | 624 |
| | | | `PBFM` | 579 |

Books split by formation type (`FORM.FTYP`: 1 offensive, 11 defensive):
**50 offensive-formation books, 14 defensive-formation books**. Of those, the
full-size team books are **members 10–47 (38 offensive)** and **members 4–9
(6 defensive)**; the rest are small special-teams, mini-camp and drill books
(e.g. member 52 "Precision Passing", member 53 "LB Drill"). *Which member
belongs to which NFL team is not established here* — nothing in the container
labels them, and nothing below needs it.

**Per book (the 38 full-size offensive books):** plays min **184**, median
**223**, mean 218, max **243** (member 22). 34 of the 38 are at or above 200.
The 6 full-size defensive books run **63–157** plays.

**Per AI group (`PBAI.AIGR`).** 1,709 groups exist across the 64 books.
Offensive books use 30 distinct group ids (0–22 and 32–38); defensive books use
25 (0–24). Group size: **median 6, mean 21.4, 95th percentile 86, max 167**.
Only four groups anywhere exceed 150 rows.

A group is the CPU's whole candidate set (`ai-play-calling.md`: the only
predicate is `AIGR == <group>`), so those are the real choice-set sizes. Member
15's group 6 — 167 plays, total weight 6,585 — is the largest pool the shipped
game ever hands the play caller.

## 2. Per formation — the I-Form question

Plays reach a formation through `PLYL.SETL → SETL.FORM`. There is **no
per-formation row cap anywhere in the schema**: a formation holds as many plays
as its sets carry.

Across the 38 full-size offensive books:

| formation | books | min | median | **max** |
|---|---:|---:|---:|---:|
| Single Back | 38 | 36 | 69 | **114** |
| **I Form** | 38 | 15 | 43 | **84** |
| Shotgun | 29 | 12 | 39 | 68 |
| Split Backs | 21 | 9 | 18 | 45 |
| Weak I | 28 | 9 | 21 | 39 |
| Strong I | 27 | 9 | 18 | 33 |
| Full House | 1 | 33 | 33 | 33 |
| Near | 7 | 9 | 9 | 12 |
| Far | 5 | 9 | 9 | 12 |
| Goaline | 38 | 6 | 9 | 9 |
| Jumbo T | 1 | 9 | 9 | 9 |

**Finding — the largest I-Form ever shipped is 84 plays**, in member 34, spread
over four sets: `Normal` 24, `Big` 24, `Twin WR` 24, `3WR` 12. The deepest
single formation of any kind is member 15's Single Back at **114 plays over 8
sets**. Defensive formations top out at 4-3 **48**, Nickel **42**, 3-4 **42**,
Dime / Quarter / 46 **27** each.

**The real shape of the ceiling is two structural numbers, not one:**

* **plays per set: max 27 observed** (a 4-3 "Normal"), and counts are almost
  always multiples of 3 — 3/6/9/12/15/18/21/24/27 covers the overwhelming
  majority, which is what the play-call screen's page of three looks like;
* **sets per formation: max 8 observed** (member 15's Single Back).

So 27 × 8 ≈ 216 is the largest formation the *shipped authoring* ever
approaches, and 114 is the largest actually built. Neither is enforced by the
data format: `SETL` ids are 8-bit (≤255 sets), `PLYL`/`PBPL` ids are 16-bit
(≤65,535 plays), and a table's row count is a `u16` (≤65,535). **Hypothesis:**
the 3-per-page and the play-call UI's set tabs are what really bound how big a
usable formation is, not the database. Settling that means a rig session with an
oversized formation, not more static work.

## 3. Where the ~175 cap lives: the DB schema, not the ELF

**Finding — the row cap is `max_records`, a `u16` at offset 20 of each TDB table
header, and the engine enforces it.** Re-derived from the executable:

*Load* — `0x004CB338`–`0x004CB3D0` copies the 32-byte on-disc table header to
the stack and unpacks it into the runtime table object:

```
004cb338  ldl/ldr v0..a3, 0(a2)      ; 32 bytes of the on-disc table header
004cb3a8  lhu v0, 20(sp)  ->  sh v0, 110(a0)   ; max_records  -> +110 (capacity)
004cb3bc  lhu v0, 20(sp)  ->  sh v0, 108(a0)   ;              -> +108
004cb3cc  lhu v0, 22(sp)  ->  sh v0, 112(a0)   ; record_count -> +112
004cb3d4  lbu v1, 28(sp)  ->  sb v1, 118(a0)   ; field count  -> +118
```

*Insert* — three sites, the same shape (`0x004D90F8`, `0x004DA8D8`,
`0x004DB1B4`):

```
004d90f8  lhu v0, 112(s1)     ; record_count
004d90fc  lhu v1, 110(s1)     ; capacity  (= max_records from the file)
004d9100  sltu v0, v0, v1     ; count < capacity ?
004d9104  beql v0, zero, ...  ; if not:
004d9108    addiu s6, zero, 19  ;   error 19 -- table full
004d910c  lhu s4, 112(s1)     ; else take the slot and
004d9114  sh  v0, 112(s1)     ;   count++
```

There is **no implicit growth on insert**. A separate resize entry point does
exist (`~0x004DAED0`: multiply, allocate at `0x004C5480`, then write the new
capacity to +108/+110), so capacity *can* be raised by code — but not by the
insert path.

The editor's user-facing side of that failure is a five-entry message table at
`0x00599D88`, read by `0x002C3870`:

| code | string |
|---:|---|
| 0 | `no error found.` |
| 1 | `this information already exists.` |
| 2 | `there are too many formations in this playbook.` (`0x00599D08`) |
| 3 | `there are too many plays in this playbook.` (`0x00599D38`) |
| 4 | `unexpected error has occured.` |

**Finding — the numbers themselves are in `TEMPLATE.DAT` member 11**, the
create-a-playbook template, whose 19 tables are empty but whose headers declare:

| table | template `max_records` | | table | template `max_records` |
|---|---:|---|---|---:|
| `FORM` (formations) | **20** | | `PLYS` | 1,100 |
| `SETL` (sets) | **20** | | `PSAL` | 3,500 |
| **`PLYL` (plays)** | **100** | | `ARTL` | 1,100 |
| **`PBPL` (book entries)** | **100** | | `SETG` | 1,980 |
| **`PBAI` (AI assignments)** | **175** | | `SETP` | 220 |
| `PBST` | 20 | | `SGF` | 180 |
| `PBFM` | 20 | | `SPKF` | 150 |
| `PBAU` | 15 | | `SPKG` | 750 |
| `PLCM` | 81 | | `PLPD` / `PLRD` | 100 / 100 |

**So the "~175" in the requirements doc is `PBAI`'s cap — the cap on
*play-to-AI-group assignments*, not on plays. The play cap sitting next to it is
100** (`PLYL` and `PBPL`). `ai-play-calling.md`'s "175 rows total, under 18 per
group" reads that row correctly; what it could not know is that the play count
is capped harder still.

**Finding — these are not engine-wide constants.** Shipped member 44 holds
**989** `PBAI` rows and member 22 holds **243** plays: 5.7× and 2.4× the
template's caps, in the same engine, through the same loader. Corroborating: the
immediate 175 appears 39 times in the whole ELF and **not once** in the playbook
editor's code (`0x2C3000`–`0x2CA000`) or the TDB library (`0x4C0000`–`0x4E0000`).

*Not settled:* whether raising `max_records` in a template is **sufficient**, or
whether the reserved data area must grow with it. The template physically
reserves exactly `max_records × record_bytes` for 15 of its 19 tables
(`ARTL` 1100×40 = 44,000 B, `PLYL` 100×40 = 4,000 B, `PBAI` 175×8 = 1,400 B …)
and reserves *nothing* for the other four (`PLCM`, `FORM`, `PLPD`, `PLRD`),
which is not a consistent enough rule to build on. What would settle it: pin the
load-time record-storage allocator (the `0x004C8B10` neighbourhood), or simply
author a template with a raised cap and open it in the editor on the rig.

## 4. The 225 bound: 58 rows of headroom, and a citation that does not hold

**Finding — the largest single `AIGR` group in any shipped playbook is 167
rows** (member 15, group 6). Against a 225-slot candidate buffer that is **58
rows / 1.35× of headroom**, and only four of 1,709 shipped groups exceed 150.

Scaling matters more than the margin: member 15 carries 220 plays and puts 167
of them in one group — a density of 0.76. Held constant, that density crosses
225 at about **296 plays**. **A 200-play book is comfortably safe (≈152); a
250-play book (≈190) still is. What is not safe is a book that dumps a large
share of a big pool into one group** — the buffer bounds a *group*, not a
playbook, so an author can overflow it at 180 plays by being lopsided.

**Correction — the evidence cited for the 225 bound does not hold at the
addresses given.** `ai-coach-playcalling-requirements.md` §5 says "the bound is
real — `224` immediates verified at `0x2BD2EC`/`0x2C1E20`". Re-derived:

```
002bd2ec  27a400e0  addiu a0, sp, 224      ; a pointer into the frame
002c1e20  27bd00e0  addiu sp, sp, 224      ; a function epilogue
```

Both are stack-frame arithmetic. Further, **no `slti`/`sltiu` against 224, 225
or 226 exists anywhere in `0x2B0000`–`0x2D0000`** — consistent with
`ai-play-calling.md` F5's "the row-fetch loop has no bound check", but it means
the 225 figure has no re-derivable static citation on record. The three large
frames in that region are `0x002BFAA0` (−3,120 B, the frame containing the query
path at `0x002BFF68`), `0x002C2038` (−5,056 B) and `0x002C26E8` (−5,424 B);
225 × 24 = 5,400 fits the last of those with 24 bytes to spare, which is
suggestive and nothing more. **This is ledger A2's to close on the rig** — do
not raise a group past ~150 on the strength of a number whose provenance is
currently a stack offset.

## 5. B2's data half: the packages are authored, and there are 5,846 of them

The question was whether the shipped **defensive** books contain `SPKF`/`SPKG`
personnel-package rows, because a row count of 0 would mean nickel needs new
authored content rather than a code poke.

**Finding — the count is not 0. All 64 playbook databases carry both tables,
and 13 of the 14 defensive books have rows in them:**

| | `SPKF` (packages) | `SPKG` (slot swaps) |
|---|---:|---:|
| 14 defensive books | **279** | **574** |
| 50 offensive books | 2,944 | 5,272 |
| all 64 | **3,223** | **5,846** |

Per defensive book: 13–31 `SPKF` and 31–64 `SPKG` rows (member 66, a kick-return
-only book, is the single zero).

The schema and the content both read as personnel substitution:

* `SPKF(SPF_, SETL, name)` — a named package hanging off one **set**.
* `SPKG(SPF_, poso, DPos, EPos)` — for that package, lineup **slot** `poso`
  (0–10) is filled by the `DPos`-th player at position `EPos`.

The defensive package names, by frequency: `Safety Swap` 57, `CB Swap` 45,
`Coverage Flip` 26, `MLB` 19, `LOLB` 19, `Strong Shift` 18, `Strong Nickel` 18,
`LB Ends` 11, `3 DT` 11, `Jumbo` 11, `Strong` 11, `Linebackers` 8, `4th CB` 8,
`Speed` 5, `46 Swap` 5, plus five singletons. (Offensive, for contrast:
`WR Swap` 438, `Spell HB` 351, `Dual HB` 276, `HB Slot` 270, `TE Slot` 262,
`Jumbo Backfield` 258, `3 WR` 44, `Quad WR` 39.)

**The `DPos`/`EPos` reading is a Hypothesis, but a well-fitted one:** the `4th
CB` package is exactly `(poso 9, DPos 4, EPos 16)`; `MLB` is `(5, 1, 14)`;
`LOLB` is `(5, 1, 13)`; `3 DT` uses `EPos 12`. That is Madden's standard
position enum (…12 DT, 13 LOLB, 14 MLB, 15 ROLB, 16 CB, 17 FS, 18 SS) with
`DPos` as the depth-chart rank. Defensive `EPos` values observed: 10, 12, 13,
14, 15, 16, 17, 18; `DPos` 1–4.

**And nickel/dime are not packages at all — they are formations with plays.**
Every full-size defensive book carries a `Nickel` formation (10–42 plays), 6 of
6 carry `Dime` (15–27), 5 carry `Quarter` (12–27), 3 carry `46` (12–27).

**Verdict for B2: the nickel feature needs no new authored rows.** The
formations, the plays and the personnel-swap rows all ship. The gap that
last night's hunt found — nothing *computes* a package from the offensive
personnel, and every hard-coded defensive lineup in the ELF is a 4-3 — is
entirely a selection-logic gap. That is a strictly better position than the
"author new content" branch B2 was hedging against.

## 6. What this means for the ≥200-play target (T-pool)

**≥200 plays is not a new engine capability.** 34 of the 38 shipped team
offensive books are already at or above 200, the biggest is 243, and the engine
loads them through the same TDB path a custom book uses. The work is entirely in
the **create-a-playbook template's caps**, and the sizing is knowable because
the shipped books show what a 243-play book actually costs.

Nine of the nineteen caps are already exceeded by the largest shipped book, and
three more sit at the line:

| table | template cap | largest shipped | worst rows/play | need @200 | need @250 |
|---|---:|---:|---:|---:|---:|
| `PBAI` | 175 | **989** | 5.72 | 1,144 | 1,430 |
| `PLYS` | 1,100 | **2,673** | 11.00 (exactly 11/play) | 2,200 | 2,750 |
| `PLYL` | 100 | **243** | 1.00 | 200 | 250 |
| `PBPL` | 100 | **243** | 1.00 | 200 | 250 |
| `SETP` | 220 | **308** | 4.30 | 861 | 1,077 |
| `PLCM` | 81 | **185** | 0.83 | 166 | 208 |
| `PLPD` | 100 | **166** | 0.84 | 168 | 209 |
| `SETL` | 20 | **28** | 0.39 | 79 | 98 |
| `PBST` | 20 | **28** | 0.30 | 61 | 77 |
| `PBAU` | 15 | 15 *(at the line)* | 0.35 | 70 | 88 |
| `PLRD` | 100 | 98 *(at the line)* | 0.84 | 168 | 209 |
| `SGF` | 180 | 170 *(at the line)* | 1.96 | 392 | 490 |
| `SETG` | 1,980 | 672 | 8.20 | 1,641 | 2,052 |
| `ARTL` | 1,100 | 332 | 2.57 | 514 | 642 |
| `PSAL` | 3,500 | 1,547 | 10.87 | 2,174 | 2,718 |
| `SPKF` | 150 | 94 | 1.02 | 205 | 256 |
| `SPKG` | 750 | 170 | 1.85 | 370 | 462 |
| `FORM` | 20 | 14 | 0.24 | 48 | 60 |
| `PBFM` | 20 | 13 | 0.18 | 37 | 46 |

("worst rows/play" is the maximum ratio over every shipped book of 40+ plays, so
the `need@` columns over-provision on purpose. `PLYS` is exactly 11 rows per
play — one per position — in every book checked.)

**The shape of the job, then:** not an ELF patch and not a code cave, but a
**template rebuilder** — a tool that takes `TEMPLATE.DAT` member 11, raises the
`max_records` words, grows each table's reserved data area to match, fixes the
table directory offsets and the DB size word, and reseals the container. That is
the same class of work `tools/build_roster.py` already does for the roster
payload, on a file this repo already reads and writes.

Two things gate it, in this order:

1. **Does raising `max_records` alone work, or must the reserved area grow with
   it?** (§3's open question. Cheapest answer: build one raised template, open
   the editor on the rig.)
2. **Does the play-call UI cope with a formation of 100+ plays, and with more
   than 20 sets?** The data format does not care; the screen might. §2's
   Hypothesis, and a rig question.

Neither gates the coach-brain's Phase 1: at stock book sizes the pool is already
184–243 plays with a 167-play worst-case group, which is what the selector will
be built and measured against.

## Correction owed to another document

**`docs/play-data.md` should be corrected; it is not edited here.** It states:

> Searched and not found: no populated playbook tables in `DB_TEAMS.DAT` (232
> members) or `GAMEDATA.DAT` (76 members) either. The TDB playbook schema exists
> *only* as the empty editor template.

That is refuted. `GAMEDATA.DAT` members 4–67 are 64 populated playbook TDBs on
both discs, carrying 36,531 `PBAI` rows and 10,060 plays. The document's
downstream conclusions inherit the error: "shipped plays come from `DMF`
content, while a **custom** playbook is stored as a TDB" is wrong in its first
half — shipped playbooks are TDBs — and "the 175-row cap it inferred is the
*template's* cap, so it bounds custom books, not the shipped ones" is right for
the wrong reason and understates the case (the cap on *plays* is 100). Its "Next
steps" item 2, "find a populated TDB playbook", is done: there are 64, on the
disc, and the reader opens them today.

## What was not checked

* **Which member is which team's book.** Nothing in the container labels them;
  members 2 and 3 of `GAMEDATA.DAT` are animation/behaviour data, not a name
  table. 38 full-size offensive books for 32 teams means at least six are
  generic or shared, and that is unresolved.
* **Whether the editor creates its tables from the template at all**, versus
  calling the library's create-table entry point with capacities of its own. The
  absence of a `175` immediate in the editor argues for the template; it is not
  proof.
* **`PBST`/`PBFM` as the play-call UI hierarchy.** The field names and the
  `ax0_`…`ay10` coordinate pairs make it near-certain, but no screen was
  observed.
* **Anything at runtime.** No claim here depends on the game having been run.

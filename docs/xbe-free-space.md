# Free-space survey: where injected code can live in the XBE

Surveyed 2026-08-14 against `extract/xbox/default.xbe` (Madden NFL 2004, Xbox,
retail, 4,890,624 B) with `tools/xbe_space.py`. This is the Xbox counterpart of
`docs/code-caves.md` (the PS2 survey), and it inherits that survey's posture:
**it reports evidence, not verdicts.** Four regions this project once
documented as safe were later found live. Nothing here is proven until a
runtime check passes on it.

```
python3 tools/xbe_space.py extract/xbox/default.xbe                  # ~5 s
python3 tools/xbe_space.py extract/xbox/default.xbe --json space.json
python3 tools/xbe_space.py extract/xbox/default.xbe --no-census      # instant, stdlib only
python3 -m unittest tests.test_xbe_space                             # 50 tests, pins every number below
```

The tool never patches anything. It answers one question.

---

## Bottom line

**The Xbox answer is structurally better than the PS2 answer, and it is not
close.** The PS2 budget was 9.2 KB of scavenged dead code with no growth path.
The XBE is PE-derived, so **adding a section is a legitimate operation** — and
on this image it costs *nothing that has to move*.

| route | budget | verdict |
|---|---|---|
| **A. append a section** | **not bounded by the format** | **the answer.** No section data moves. Costs 616 B of header room to relocate the section table, plus ~72 B per new section, out of 1,624 B free. |
| **B. zero-reference dead code** | 78,093 B / 76.3 KB in 1,213 regions ≥ 32 B | the fallback if an append is ever refused. Every region **UNPROVEN**. |
| C. in-file slack | **0 B usable** (30,104 B exists) | ⚠ **not a cave.** No virtual address. See below. |
| D. inter-section VA gaps | 92 B, largest 20 B | crumbs. |
| E. virtual zero-fill | **0 B** (688,964 B exists) | occupied and unbakeable — the `.bss` answer. |

**Finding:** routes A and B are the only two that hold code. C is the *raw
backing* for A. D and E are not space at all and are listed only so a reader
totalling the columns cannot mistake them for space.

**Reachability is a non-issue, again, for a different reason than on PS2.** x86
`E8`/`E9 rel32` reaches ±2 GB — any site in this image can call or jump to any
other in five bytes, including a section appended above the image at
`0x0055C000`. There is no MIPS-style 256 MB region constraint and no trampoline
problem. Distance only affects I-cache locality.

---

## Route A — append a section (the primary answer)

### The header block, byte for byte

`SizeOfHeaders` is `0x9A8`; the first section's raw data starts at `0x1000`.
Measured occupancy:

| file span | size | what |
|---|---|---|
| `0x000`–`0x004` | 4 | magic `XBEH` |
| `0x004`–`0x104` | 256 | digital signature (RSA-2048), **populated** — 256/256 nonzero |
| `0x104`–`0x184` | 128 | image header fields (`SizeOfImageHeader` = `0x184`) |
| `0x184`–`0x370` | 492 | certificate (`0x1EC`) |
| `0x370`–`0x5D8` | 616 | **section table** — 11 × `0x38` |
| `0x5D8`–`0x5F0` | 24 | **head/tail shared-page refcount array** — 12 × `u16`, all zero |
| `0x5F0`–`0x634` | 68 | section-name string pool |
| `0x634`–`0x6A4` | 112 | library-version table (7 × `0x10`) |
| `0x6A4`–`0x6BE` | 26 | debug unicode filename |
| `0x6BE`–`0x6F1` | 51 | debug pathname (`f:\Proj\Madden04\Project\XBOX\Release\Madden04.exe`) |
| `0x6F4`–`0x9A6` | 690 | logo bitmap |
| **`0x9A6`–`0x1000`** | **1,626** | **FREE, all zero** |

**Finding — the section table cannot grow in place.** It ends at `0x5D8`, and
`0x5D8` is the first byte of the shared-page refcount array, immediately
followed by the name pool at `0x5F0`. Verified independently: every section
header's `HeadSharedRefCount`/`TailSharedRefCount` pointers (`+0x1C`/`+0x20`)
land in `0x5D8`–`0x5F0`, and **section *i*'s tail slot is section *i+1*'s head
slot** — `.text` head `0x5D8` tail `0x5DA`, D3D head `0x5DA` tail `0x5DC`, and
so on to `$$XTIMAGE` tail `0x5EE`. That overlap is how the format expresses two
sections sharing a physical page. The prior review's claim is confirmed exactly.

**Finding — the headroom is 1,626 B, of which 1,624 B lie above
`SizeOfHeaders`.** The prior review's "1,624 bytes" is the count above
`SizeOfHeaders` (`0x9A8`); the free *run* begins two bytes earlier at `0x9A6`,
where the logo bitmap's tail padding sits. Both numbers are right; the tool
reports both so they cannot drift apart again.

**It is headroom in both spaces.** The header block maps at `base 0x00010000`
and `.text` starts at VA `0x00011000`, so the whole run lies inside the
already-mapped first page. Nothing moves to use it.

### The recipe, with the five fields spelled out

1. **Relocate the whole section table into the headroom.** 12 × `0x38` = `0x2A0`
   = 672 B, comfortably inside 1,626 B. Repoint `SectionHeadersAddress`
   (`+0x120`).
2. **New name string** in the headroom; `SectionNameAddress` points at it.
3. **Two fresh `u16` refcount slots** in the headroom for the new section's
   head/tail. Legal because those header fields are **absolute VAs into the
   header block, not indices into an implicit array** — and the appended
   section shares a page with nothing, being page-aligned above the image.
4. **`SizeOfHeaders`** (`+0x108`, now `0x9A8`) grows to cover what was added.
   **HARD CAP: it must stay ≤ `0x1000`** or `.text`'s raw data moves and every
   raw offset in the file moves with it.
5. **`SizeOfImage`** (`+0x10C`, now `0x54B460`) grows to cover the new VA range;
   **`NumberOfSections`** (`+0x11C`) goes from 11 to 12.

**Placement.** Raw at `0x4AA000` — the current file end, already page-aligned
(the 2,048 B tail slack after `$$XTIMAGE` is *not* page-aligned and should be
left alone). VA at `0x0055C000`, the first page above image top `0x0055B460`.

**Capacity:** ~72 B of header room per appended section (`0x38` header + name +
4 B of refcount slots), so after the 672 B relocation the headroom carries
**up to 14 appended sections**. In practice one is enough; a cave section can
be any size.

**How much code can go in it?** Not bounded by the XBE format — `SizeOfImage`
is a `u32` and currently `0x0054B460` (5.4 MB). *Hypothesis* (not verified
against the binary or a console): the real ceiling is retail RAM, 64 MB, which
this image uses less than a tenth of. The verified constraints are the header
cap above and the ISO repack in `pnach-to-xbe-pipeline.md` §8.

---

## Route B — zero-reference dead code

The PS2 five-axis census ported to x86. Split `.text` at `ret`/`jmp`/`int3`
boundaries so no fragment can be entered by fall-through, then require that
**nothing** targets any byte of it.

| axis | how |
|---|---|
| `call` / `jmp` / `jcc` | direct transfers from a *decoded* instruction stream — rel8 **and** rel32, computed both directions |
| `rel32` | a second, *unaligned* scan of the whole file for `E8`/`E9` only, to catch anything the linear sweep missed |
| `word` | every 4-byte little-endian word anywhere in the image whose value lands in the region — the vtable / jump-table / function-pointer test |
| `entry` | the entry point, added by hand |
| structural | the region's first byte must be unreachable by fall-through |

**Two axis notes that decided the design.**

*Why rel8 comes only from decoded instructions.* One byte in sixteen is a `7x`
opcode, and a random rel8 reaches any given 400-byte window with better than
even odds. An unaligned byte scan for short jumps rejects every region in the
image and is worthless. rel32 is the opposite: a random rel32 lands inside
3.5 MB with probability ~1/1200, so over-scanning it unaligned is free.

*Why the entry point needs its own axis.* An XBE stores the entry point
XOR'd (`raw 0xA8D9F16D` → `0x0025A6C6`, retail key). **Finding:**
`find_le32(0x0025A6C6)` returns **zero hits in the entire file** — the plain
entry VA appears nowhere, so a pointer scan cannot see it. Pinned as a test.

### Sweep quality (reported, not assumed)

| measure | value |
|---|---|
| instructions decoded in `.text` | 1,240,285 |
| decode failures | 298 |
| byte coverage | 99.9916 % |
| distinct referenced addresses found | 166,972 |
| harvested call targets landing on a sweep boundary | 14,037 / 14,368 = **97.7 %** |

### Result

**78,093 B / 76.3 KB in 1,213 regions ≥ 32 B.**

| threshold | regions | bytes |
|---|---|---|
| ≥ 32 B | 1,213 | 78,093 |
| ≥ 64 B | 410 | 42,779 |
| ≥ 128 B | 66 | 13,500 |
| ≥ 256 B | 11 | 3,842 |
| ≥ 512 B | 0 | 0 |

**The budget is fragmented and there is no single big cave.** The largest is
466 B. Compare PS2: 9,248 B in 56 regions with a 640 B maximum — the same
shape, eight times the volume.

**32.8 % of it (25,641 B) is `int3` inter-function padding**, not dead bodies;
52,452 B is dead instruction bytes. 90 regions totalling 7,676 B are *pure*
dead code with no padding at all, and 34 regions totalling 2,045 B are ≥ 90 %
padding — the lowest-risk kind, the x86 analogue of PS2 cave #11 ("owned by no
object").

The top regions:

| vaddr | file | size | insns | int3 | character |
|---|---|---|---|---|---|
| `0x00264474` | `0x254474` | **466 B** | 172 | 0 | float/vector routine |
| `0x00263A4A` | `0x253A4A` | 461 B | 180 | 0 | float/vector routine |
| `0x0026283D` | `0x25283D` | 433 B | 169 | 0 | float/vector routine |
| `0x0022CC23` | `0x21CC23` | 425 B | 213 | 82 | code |
| `0x0026421D` | `0x25421D` | 343 B | 132 | 0 | float/vector routine |
| `0x003302A2` | `0x3202A2` | 342 B | 57 | 0 | **import-thunk band** ⚠ |
| `0x002338FA` | `0x2238FA` | 287 B | 284 | 263 | mostly padding |
| `0x002C2AC5` | `0x2B2AC5` | 283 B | 135 | 51 | code |
| `0x00263408` | `0x253408` | 272 B | 100 | 0 | float/vector routine |
| `0x002AB2F1` | `0x29B2F1` | 271 B | 193 | 135 | mostly padding |

The dead code clusters in a math-library band around `0x0026xxxx` (52 regions,
5,415 B) and a second around `0x0023xxxx` (69 regions, 5,025 B). 41 regions are
float/vector routines — dead inline-math library, the usual shape.

⚠ **Six regions totalling 698 B are import-thunk bands** — runs of
`jmp dword ptr [__imp_*]` reading the kernel thunk table: `0x003302A2` (342 B),
`0x00330452` (110 B), `0x00330218` (102 B), `0x003702D4` (54 B), `0x003303FE`
(48 B), `0x00370310` (42 B). They are dead by every axis, and overwriting one
corrupts nothing, but it **permanently removes the ability to call those
imports** — the linker emitted a thunk per import descriptor and the game never
calls these. Different failure mode from clobbering a dead leaf; the tool
labels them so an allocator can decline.

**Clean structural results, both worth stating as closed-set negatives:** no
reported region contains an internal branch target (so a partial overwrite
cannot corrupt a live-looking body — 0 of 1,213), and only 2 regions contain
any byte the sweep could not decode.

### Why the census defaults to `.text` only

The other nine executable sections are library blobs. Run with
`--sections .text,DOLBY` and DOLBY alone yields 20 KB more "dead" code — but
its decode coverage is 98.9 % against `.text`'s 99.99 %, and it offers **one**
direct call target against `.text`'s 14,368. Its code is entered almost
entirely indirectly, so the census has nearly nothing to test against and
over-reports by construction. The tool prints a `** WEAK EVIDENCE` banner on
any section in that state. Those regions are candidates to test on the rig, not
budget.

### Standing rule, inherited verbatim from `code-caves.md`

**A dead region is UNPROVEN until an execute-breakpoint test passes on it, per
region, not once for the survey.** A clean static census is necessary, not
sufficient. The PS2 survey shipped four regions that were referenced.

---

## Route C — in-file slack: ⚠ NOT USABLE FOR CODE

**30,104 B / 29.4 KB exists, every byte zero-filled, and not one of it is
loadable.**

| file span | size | between |
|---|---|---|
| `0x0009A8`–`0x001000` | 1,624 | headers → `.text` *(this run is route A's headroom)* |
| `0x361F4C`–`0x362000` | 180 | `.text` → D3D |
| `0x38196C`–`0x382000` | 1,684 | D3D → XGRPH |
| `0x384874`–`0x385000` | 1,932 | XGRPH → DSOUND |
| `0x3AB004`–`0x3AC000` | 4,092 | DSOUND → WMADEC |
| `0x3C725C`–`0x3C8000` | 3,492 | WMADEC → D3DX |
| `0x3CB270`–`0x3CC000` | 3,472 | D3DX → XPP |
| `0x3D44B8`–`0x3D5000` | 2,888 | XPP → `.rdata` |
| `0x40C9D0`–`0x40D000` | 1,584 | `.rdata` → `.data` |
| `0x49E2D0`–`0x49F000` | 3,376 | `.data` → DOLBY |
| `0x4A616C`–`0x4A7000` | 3,732 | DOLBY → `$$XTIMAGE` |
| `0x4A9800`–`0x4AA000` | 2,048 | tail after `$$XTIMAGE` |

**Finding, proven per byte:** all 30,104 bytes lie outside every section's
`[raw_off, raw_off + raw_size)`. The surveyor asked `off_to_va()` for **every
one of them** and **zero** answered. Nothing is loaded there; nothing can jump
there. Code written into slack is a **silent no-op** — the worst failure mode a
patch tool has.

**Correction to the earlier draft of `pnach-to-xbe-pipeline.md` §7b:** a draft
described these bytes as "ideal for small caves". They are not caves. They are
usable only as the **raw home of a section you also declare** — which is route
A, and route A already places its section at the file end anyway.

---

## Routes D and E — not space

**D. Inter-section VA gaps total 92 B**, largest 20 B (`.text` → D3D at
`0x00371F4C`). Confirmed. Useless. The room is *above* the image: everything at
or above `0x0055B460` is unclaimed, and `$$XTIMAGE`'s virtual end is exactly
that address.

**E. Virtual zero-fill — 688,964 B / 672.8 KB across five sections, and it is a
refusal class, not a free one.**

| section | zero-fill | VA range |
|---|---|---|
| `.data` | 674,796 | `0x004ACEF0`–`0x00551ADC` |
| D3D | 13,508 | `0x003918CC`–`0x00394D90` |
| DSOUND | 624 | `0x003BD624`–`0x003BD894` |
| DOLBY | 20 | `0x00558C4C`–`0x00558C60` |
| `.rdata` | 16 | `0x0041BC10`–`0x0041BC20` |

This is the exact analogue of PS2 `.bss` and gets the exact same answer: the
loader zeroes it, there are no file bytes to bake, and `va_to_off()` already
refuses it. Reported so it cannot read as free space.

---

## Constraints — each can invalidate an otherwise-free region

### Section digests: 10 of 11 verify; `.text` does not

**Finding, verified independently this session.** Each section header carries a
20-byte SHA-1 at `+0x24`, and **ten of eleven reproduce exactly as
`SHA-1( le32(raw_size) ‖ raw_bytes )`**: D3D, XGRPH, DSOUND, WMADEC, D3DX, XPP,
`.rdata`, `.data`, DOLBY, `$$XTIMAGE`.

**`.text` does not** — stored `824de671699414308099c11578c9b065b34c9277`,
computed `ab6d8738…`. Closed-set negative: it also fails under plain SHA-1, a
big-endian length prefix, a virtual-size prefix, a header-inclusive span, and a
brute-forced end position anywhere in `raw_size ± 0x4000` with either a
length-prefixed or bare hash. No rule found.

Two readings, and the surveyor deliberately picks neither: either this retail
image ships a stale `.text` digest — in which case a console that boots this
disc plainly does not enforce it — or `.text` uses a rule we have not found.

**The engineering answer makes the question moot: recompute the digest under
the verified rule for every section EMIT touches, and compute a correct one for
any appended section.** The one thing not to do is leave a modified section
carrying its old digest on the theory that nothing checks.

**Hypothesis (not evidenced): whether a softmodded kernel enforces section
digests or the certificate signature.** That is a property of the boot path,
not of this file, and it is not decidable from the image. The RSA-2048
signature at `+0x004` is present and fully populated (256/256 nonzero bytes)
over the header and cert; a softmod normally skips it for HDD launches, which
is the delivery route (`docs/xbox-madden-2004-plan.md`), but the friend's
softmod has not been characterised and should not be assumed to behave like
every other softmod.

### Section flags — "not executable" will almost never be the disqualifier

**10 of 11 sections are already marked executable.** Only `$$XTIMAGE` is not.

| section | flags |
|---|---|
| `.text` | `PRELOAD\|EXECUTABLE\|HEAD_PAGE_READ_ONLY` (`0x16`) |
| D3D, XGRPH, DSOUND, WMADEC, D3DX, XPP, `.data` | `WRITABLE\|PRELOAD\|EXECUTABLE` (`0x07`) |
| `.rdata` | `PRELOAD\|EXECUTABLE` (`0x06`) |
| DOLBY | `PRELOAD\|EXECUTABLE\|TAIL_PAGE_READ_ONLY` (`0x26`) |
| `$$XTIMAGE` | `INSERTED_FILE\|HEAD_PAGE_READ_ONLY\|TAIL_PAGE_READ_ONLY` (`0x38`) — **not executable** |

Note `.text` is *not* writable. A self-modifying cave in `.text` would need the
flag changed; a cave in an appended section is writable by construction, which
is where the PS2 canaries at `0x00514978`/`0x0051497C` should go.

### `$$XTIMAGE` and appending after it

It is last by **both** VA (`0x00558C60`) and raw offset (`0x4A7000`), flagged
`INSERTED_FILE`, not executable. **Appending after it in the file and above it
in VA touches nothing it owns** — no existing raw offset or virtual address
moves. (The tool checks last-by-VA and last-by-raw separately and shouts if
they disagree; on this image they do not.)

### Alignment

**Measured:** all 11 raw offsets are `0x1000`-aligned. Virtual addresses are
**not** — only `.text` is page-aligned; the rest are packed tightly (D3D at
`0x00371F60`, `.data` at `0x0041BC20`), which is exactly why the head/tail
shared-page refcount mechanism exists. The file size `0x4AA000` is
page-aligned, and is also exactly 2,388 XISO sectors.

An appended section should be `0x1000`-aligned in **both** spaces: page-aligned
raw so it matches every other section, page-aligned VA so it shares a page with
nothing and its refcount slots stay trivially correct.

### TLS, kernel thunks, entry-point XOR

* **TLS** directory at VA `0x003E4804` (in `.rdata`). `AddressOfCallBacks` is
  **0** — no TLS callbacks to preserve. `AddressOfIndex` is `0x004ACF60`, which
  is inside `.data`'s zero-fill, consistent with a runtime-initialised slot.
  Zero-fill 144 B. A title *with* callbacks would need them preserved; this one
  has none.
* **Kernel thunk table** — 161 entries, 648 B, at VA `0x003E4240`, which is the
  **first byte of `.rdata`**. Live data. A census that treats `.rdata` as
  ordinary data will call it free; it is not. (This survey censuses `.text`
  only by default, for exactly this reason — `--sections` opts in.)
* **Entry point and kernel thunk are stored XOR'd** with the retail key. Any
  tool that rewrites them must re-XOR with the same key, and no pointer scan
  will ever see the plain entry VA.

---

## What cannot be settled statically

Unchanged in substance from `code-caves.md`:

1. **Runtime overwrites.** Nothing here proves the image does not rewrite its
   own code — an overlay loader, a decompressor, or a copy through a computed
   base. Computed store bases cannot be enumerated statically.
2. **Invisible reachability.** An address arriving from a data file, a computed
   indirect call through a pointer that is never a literal, or a callback
   registered by handle evades every axis here. The axes are strong, and they
   are static.
3. **Whether the console enforces the digests or the signature.** A property of
   the boot path, not of the file.
4. **Linear-sweep drift.** Where the sweep drifts, a rel8 `jcc` into a
   candidate region could be missed. This is why the tool reports its decode
   failure count (298) and call-target corroboration rate (97.7 %) instead of
   presenting the census as an oracle.

---

## Runtime verification plan (rig)

Observe the H-2 live-session check before any emulator action.

| # | test | pass condition |
|---|---|---|
| **1** | **Is the region really dead?** *(unpatched, per region)* Execute-breakpoint on the region base; boot → menus → roster/franchise → a full quarter → replay → halftime → save/load | never trips |
| 2 | Does an appended section load? Append a 4 KB section at `0x0055C000` with a recognisable fill, read it back at runtime | bytes present at `0x0055C000` |
| 3 | Are digests enforced? Modify one byte of `.text` **without** fixing its digest, boot | if it boots, the softmod does not enforce them — a *measurement*, not an assumption |
| 4 | Does the appended section survive? Read `0x0055C000` at kickoff, after halftime, after a load | unchanged |
| 5 | Regression | stock behaviour returns with the patch removed |

**Test 1 gates all class-3 work, per region — not once for the survey.** Test 3
is the only way to settle the digest question, and it is cheap.

---

## Recommended allocation

1. **Append one writable, executable section** at `0x0055C000` and put every
   new cave body, scratch word and canary in it. It is unbounded, page-aligned,
   shares nothing, and needs no liveness proof at all — the entire class-3 risk
   surface disappears.
2. **Hold route B in reserve.** It exists (76.3 KB), it is well evidenced, and
   it is the answer if an append is ever refused by the delivery path. If it is
   ever used, start with the pure-padding regions (2,045 B across 34 regions,
   largest 287 B at `0x002338FA`) — they are owned by no object — and run test 1
   on each before writing a byte.
3. **Never use slack, VA gaps or zero-fill.** They are not space.

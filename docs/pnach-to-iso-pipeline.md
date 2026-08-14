# pnach → patched ISO: the bake pipeline

Scoped 2026-08-13. The dev loop patches EE memory through the emulator's cheat
engine (pnach, re-applied every vsync); the ship loop bakes the same words into
the disc image so the game carries its fixes everywhere — no cheat files, no
emulator settings, eventually real hardware. This is Workstream W's concrete
plan (`ai-coach-playcalling-requirements.md` §5/B6) and a Track-1 deliverable in
its own right: the modified ISO for online play (the DNAS word baked in).

**Division of labor:** pnach = iteration (poke, reboot, retune — unbeatable);
baked ISO = the product. Both stay alive; the pipeline is the bridge.

## Ground truth (verified 2026-08-13 against the image and the live pnach set)

- `SLUS_207.52` is **one PT_LOAD**: file offset `0x1000` → vaddr `0x00100000`,
  `filesz 0x509579`, `memsz 0x559FDC`, ELF file 5,354,036 B. The project's
  `vaddr = file_offset + 0xFF000` convention is exact, segment-wide.
- **Bake audit of the deployed set** (`14F8B841.c1-plus-doubleteam.pnach`, 102
  patch lines): **102/102 file-backed, 0 in .bss, 0 outside.** The entire
  current patch set — double team, C1, caves, the k constant — bakes as direct
  file edits. (`.bss` = the `0x509579..0x559FDC` tail; a future pnach landing
  there cannot bake and needs an init hook — the audit step exists to catch it.)
- **Prior art:** `tools/patch_iso_roster.py` already does safe same-size
  in-place ISO patching (xorriso locate + scan fallback, size-mismatch refusal,
  `-o`/`--in-place`). The ELF baker is a sibling, not an invention.

## Phase P1 — same-size bake (unblocked NOW; covers every current patch)

Word patches don't change the file size, so the ISO's filesystem is never
touched — the ELF's bytes are overwritten where they lie, exactly the roster
tool's trick.

1. **`tools/bake_pnach.py`** — **BUILT 2026-08-14** (49 tests). pnach in, patched ELF out.
   - Parse `patch=1,EE,addr,word,value` lines; **classify each**: file-backed →
     write at `vaddr − 0xFF000`; `.bss` → REFUSE (until an init-hook mechanism
     exists); outside → error. Duplicate-address collision check.
   - Emit a **manifest** (addr, old word, new word) — the audit/revert record,
     and the input to verification.
2. **`tools/patch_iso_elf.py`** — **BUILT 2026-08-14** (25 tests). Patched ELF into the ISO.
   - Locate `SLUS_207.52;1` in the image (reuse `patch_iso_roster.py`'s
     `locate()`: xorriso, then scan). Same-size overwrite; refuse otherwise;
     `-o` / `--in-place`.
3. **Verify** — three arms, all cheap:
   - re-extract the ELF from the patched ISO, byte-diff against the baker's
     output (and the manifest);
   - boot the baked ISO with the **cheats dir cleared** (see T4) and re-run the
     standing acceptance oracles (slot-9 double-team, C1 guard behavior) — on
     **fresh savestates** (see T2);
   - a full quarter of play for stability.

The ISO itself is user-supplied (a dump of an owned disc; none lives in this
repo). Distribution of results = the **patcher tools + pnach/manifest**, never
the image.

## Semantics & traps (pnach ≠ bake; each of these can eat an evening)

- **T1 — vsync re-apply vs write-once.** `patch=1` re-stamps every vsync; a
  baked word is written once, at mastering. Equivalent **iff the game never
  writes the address at runtime**. True of the entire current set (code words,
  cave bodies, data constants with no runtime writer). Rule: any pnach that
  *fights a runtime writer* cannot bake — the baker's classify step must flag
  suspected fighters for a manual writer-census before shipping.
- **T2 — the CRC changes, and savestates are keyed to it.** Patching the ELF
  moves the CRC off `14F8B841`. Consequences: (a) **every existing savestate
  (slots 1–9, all experiment states) will not load under the baked build** —
  new baselines must be captured; (b) per-game settings
  (`SLUS-20752_14F8B841.ini`) detach — recreate (and `EnableCheats` is no
  longer needed); (c) old pnach files stop auto-matching — which is hygiene,
  not loss (prevents double-stomping). Expect a **re-baseline session** as part
  of first bake.
- **T3 — stale savestates resurrect unpatched code.** A state carries all of EE
  RAM, code included; restored under a baked ISO with no pnach re-stomping,
  old code would silently return. The CRC keying (T2) forecloses the common
  case; renaming states to force-load them re-opens it. **Never migrate old
  states onto a baked build.**
- **T4 — mixed mode is a feature, with one rule.** pnach OVER a baked base
  works (idempotent word writes) and is the right way to retune (e.g., a k
  sweep atop the baked set). The rule: when *validating a bake*, clear the
  cheats dir — a stale pnach masks a bad bake.
- **T5 — self-integrity checks: none observed, verify once.** The DNAS word
  and every cave patch have run for weeks in RAM without tripping anything;
  no evidence of code checksumming. First baked boot confirms; real-hardware
  DNAS media checks are a separate, console-only concern.

## Phase P2 — the grow path (Workstream W; needed by the coach-brain)

Same-size baking hits a hard wall at `filesz`: new code beyond ~9 KB of caves
needs a **bigger ELF** — a second PT_LOAD (or a grown segment) and an ISO that
can hold a larger file. Scoped, not started:

- **ELF side — B6 ANSWERED 2026-08-14 (`docs/shipping-questions.md`).** The
  premise "find free EE RAM" was **wrong: there is none.** The pool partitioner
  `0x002F9A00` (one caller, unconditional boot path) carves *every* byte from
  `_end` to the top of RAM into named pools — STATE `0x00660000`, SOUND
  `0x006CB000`, MAIN `0x006CC800`, DB `0x01D5AFF0`… gapless, and the static
  computation reproduces two live EE dumps byte-for-byte. `0x01000000` is 73%
  written in play; only 16 bytes at `0x01FFFFF0` are provably free.
  **So the segment is CARVED, in two words:** `0x002F9A08` `3C0D0066`→`3C0D0067`
  and `0x002F9A0C` `3C0B0006`→`3C0B0005`. Since `SOUND.base = t5 + t3`, adding
  64 KB to the base and subtracting it from the size leaves **every other pool
  boundary byte-identical** (coordinator-verified by disassembly). Recommended
  segment: **`0x00660000`, 64 KB.** Both patches bake through the existing P1 path.
- **And the file side is already solved:** **64,744 bytes of all-zero file space
  at `0x0050A5C0..0x0051A2A8`** (the DVP overlay bodies + `.stack`) lie **outside
  every PT_LOAD** — coordinator-verified all-zero and past the segment's
  `0x50A579` end. `code-caves.md` rejected that space for being *unloaded*, which
  is precisely what makes it usable for a **new** program header. The phdr table
  has **4,012 verified-zero bytes of room at `0x54`** (phnum is 1), so a second
  PT_LOAD needs **no file growth → no ISO directory surgery at all**, and
  `bake_pnach.py` already iterates all program headers with per-segment deltas, so
  it needs no change. ISO record-repointing is only required above ~63 KB.
- **ISO side:** a bigger file no longer fits its extents. Standard modder
  path: **append the enlarged ELF to the end of the image** and repoint the
  directory record's LBA+size (no relayout of anything else); full rebuild is
  the fallback. Needs a small ISO9660 directory-record editor — a sibling of
  `locate()`.
- **Emulator compatibility:** PCSX2 loads what the ELF headers declare; a
  well-formed second segment should Just Work — verify early with a trivial
  segment before the coach-brain depends on it.

## P1 RESULT (2026-08-14): BUILT AND VERIFIED AGAINST THE LIVE SET

Both tools exist, tested, and independently re-verified by the coordinator (bake
re-run from scratch, output disassembled directly — not trusting the builder's
own report):

    102 patch lines | file-backed 102 | .bss 0 | outside 0
    102 words written (7 already matched) | 5,354,036 bytes, size unchanged
    verify: 102/102 read back as intended; NO byte outside a patched word differs
    original ELF md5 1a5e551634f2644739cfb4ba39025ef8 UNCHANGED

Spot-disassembly of the baked image (coordinator's own run): C1 `0x001F2D60` =
`beq zero,zero` (was `beq s6,zero`, same target); N-1 `0x001F153C` =
`jal 0x004f4aa0` (was `jal 0x001f0c40`); T3 k at `0x004F4BCC` = float 0.8;
P11 `0x001F21E8` = `addiu a1,zero,0`; P1 `0x001F4A30` = `j 0x00514920`. 95
distinct words differ (102 written − 7 that already held their value: cave
delay-slot zeros into already-zero space).

Safety paths proven on the REAL ELF: a `.bss` address (`0x0060A000`) and an
out-of-segment address are refused by name with the `.bss` span printed and
**no output file created**. Round trip proven end to end on a synthetic ISO
carrying the real stock ELF: patch → re-extract → byte-identical to the baker's
output, image size unchanged, differences confined to the ELF's extent.

Beyond spec, the builder added: straddle-the-filesz detection, duplicate vs
conflict handling (`--allow-conflicts`, last-line-wins as the cheat engine
resolves it), `--audit` (classify, write nothing), unknown-directive refusal
(a silently skipped line is a patch that didn't ship), and a real ISO9660
directory walk as the xorriso fallback (yields the exact LBA *and* size the
size-check needs) with `SYSTEM.CNF`/`BOOT2` boot-file autodetect.

**What remains for a shippable ISO:** the operator's own disc image. The tools
are ready; `patch_iso_elf.py` has never run against a real PS2 ISO.

## Status

P1 is **BUILT** (above) — the audit already passed on the live
set; the two tools are an afternoon each against existing prior art. P2 is
scoped with one real unknown (segment placement vs heap) parked in the
ai-coach ledger as B6. First deliverable when built: the **baked
double-team + C1 ISO** — the current gameplay set, playable with an empty
cheats folder.

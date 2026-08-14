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

1. **`tools/bake_pnach.py`** *(to build)* — pnach in, patched ELF out.
   - Parse `patch=1,EE,addr,word,value` lines; **classify each**: file-backed →
     write at `vaddr − 0xFF000`; `.bss` → REFUSE (until an init-hook mechanism
     exists); outside → error. Duplicate-address collision check.
   - Emit a **manifest** (addr, old word, new word) — the audit/revert record,
     and the input to verification.
2. **`tools/patch_iso_elf.py`** *(to build)* — patched ELF into the ISO.
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

- **ELF side:** add a PT_LOAD (phnum 1→2, new header entry, section appended to
  the file). **Placement is the open question:** `.bss` runs to `0x559FDC`,
  the live stack sits at `0x0065A000`, and the runtime heap's start is
  unmapped — the new segment's vaddr must dodge all three (candidates: just
  past the stack once the heap is mapped, or a high fixed address in the
  mostly-empty upper half of the 32 MB). **B6 investigation item.**
- **ISO side:** a bigger file no longer fits its extents. Standard modder
  path: **append the enlarged ELF to the end of the image** and repoint the
  directory record's LBA+size (no relayout of anything else); full rebuild is
  the fallback. Needs a small ISO9660 directory-record editor — a sibling of
  `locate()`.
- **Emulator compatibility:** PCSX2 loads what the ELF headers declare; a
  well-formed second segment should Just Work — verify early with a trivial
  segment before the coach-brain depends on it.

## Status

P1 is fully specified and **unblocked** — the audit already passed on the live
set; the two tools are an afternoon each against existing prior art. P2 is
scoped with one real unknown (segment placement vs heap) parked in the
ai-coach ledger as B6. First deliverable when built: the **baked
double-team + C1 ISO** — the current gameplay set, playable with an empty
cheats folder.

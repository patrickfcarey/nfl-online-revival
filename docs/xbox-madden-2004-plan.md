# Xbox Madden 2004: the port investigation plan

Scoped 2026-08-13. Goal: carry the gameplay work — the blocking fixes and,
eventually, the coach AI — to the **Xbox** version of Madden NFL 2004, for a
friend. This is the plan for the *investigation*; nothing here is started.

## Scope: gameplay only — there is no online to revive on this platform

**Xbox Madden 2004 shipped without online play** (EA withheld Xbox Live support
until the 2004–05 titles; the PS2 SKU's EA-server online has no Xbox
counterpart). *High confidence, verify from the disc on X0 — the XBE should
contain no DNAS-analogue or network config for game services.* Consequence:
Track 1 does not apply here. The deliverable is a **patched `default.xbe`** a
friend runs on a softmodded console or under xemu — offline gameplay, made
good. (ESPN NFL 2K5's Xbox Live world — Insignia — is a separate concern and
stays in Track 1.)

## The thesis that makes this tractable: anchor on DATA, not code

Same EA gameplay codebase, compiled for x86 instead of MIPS. Every code address
we own dies in the port; **the data survives compilation** — fourccs, tuned
constants, table shapes, and (likely) whole disc assets. The PS2 work therefore
transfers as a *map of what to look for*, which is exactly what the ai-coach
spec's "platform-agnostic module" clause (§3) was written to enable.

**The verified PS2 anchor inventory to sweep for on Xbox:**

| class | anchors (all verified on PS2) |
|---|---|
| registry fourccs | `ptrk`, `fatg`, `madt`, `prac` |
| save/DB tags | `GBIN`, `STPG`, `HCOC`, `PBAI`, `AIGR`, `LPBP`, `RGIA`, `tcrp`, `TSBP`, `IABP` |
| float constants | 165.75, 0.175, 0.06, −0.13, 2.1, 335.4, 12.75, 0.3, 32767.0 |
| weight tables | recency 1/24, 1/48, 1/96, 1/192; intensity 0.012…0.45 |
| structure shapes | the two-side 48×16 ring (1556-B alloc), the 6×5 outcome grid, the 24-entry pairing table, ~20-B fatigue entries |
| disc assets | the playbook TDB, play data, **the situational-policy script (asset #69)** — plausibly byte-identical across platforms |

If the fourccs and constants hit broadly, the port is **re-derivation from a
map** (find the x86 twin of each known hook), not re-research from gameplay
observations. If they don't, we re-scope honestly. That single sweep sizes the
whole effort — which is why it's phase X1 and cheap.

**Best early leverage:** the data-side tooling may work *unchanged* —
`tools/madden_tdb.py` and `lzh1.py` against the Xbox disc's data files is a
one-evening test that answers "how shared is this build" before any
disassembly.

## Effort model — why this is far cheaper than the PS2 campaign (operator, 2026-08-14)

The PS2 cost was **discovery**: learning what the systems are, sixteen dead ends,
seven refuted patches. The Xbox inherits the answers — the system map, the
requirements and acceptance tests, the field semantics, which one-word levers do
what. Porting answers ≫ faster than asking questions; the multiplier is already
measured *on PS2 itself*: with the map in hand, C1 went diagnose→patch→
operator-confirmed in ~an hour and W1 located-and-poked in minutes, versus the
full sessions their original investigations cost. X2's job — find the x86 twin of
a known site from known anchors — is minutes-to-hours per site, not weeks.

Where the time actually goes (the honest residue):
1. **The instrument, not the science** — the xemu/XBE operational layer (GDB stub
   in the PINE role, XBE bake in the pnach role). Harness *design* transfers;
   transport is new. The biggest single build item.
2. **Re-verification per ported site** — fast with a map, never skippable
   (rule 4 is this project's core lesson).
3. **Genuine divergence** — wherever the Xbox build actually differs, the map
   doesn't cover; X1 prices that in one sweep before anything is committed.

## Toolchain

- **Disc:** XISO (XDVDFS) extraction — the volume descriptor is trivially
  parseable; a small stdlib reader in the `recon/` style, or an existing
  extractor. The friend's (or our own) disc dump; nothing enters the repo.
- **Executable:** XBE parser (header, base vaddr — typically `0x00010000` —
  sections, entry/thunk de-XOR; well documented). Small stdlib tool.
- **x86 disassembly:** **Ghidra** as the workhorse (free, XBE loaders exist,
  headless scripting for sweeps). Open decision: pure-Python scripted sweeps
  would want capstone — the repo's first pip dependency — vs exporting Ghidra
  listings and keeping the repo stdlib-pure. Decide at X1.
- **Runtime:** **xemu on the rig** (the PenguinBox checkout;
  `emulator-capture.md` already documents xemu config). The GDB stub plays the
  role PINE plays on PS2 — live reads, pokes, breakpoints. **All rig rules
  apply: H-2 headset check before any xemu launch, every AGENTS.md hard rule.**
- **Patch delivery — RESOLVED (operator, 2026-08-13): the friend runs a
  softmodded console.** No pnach ecosystem on Xbox — **bake into `default.xbe`
  from day one** (the ISO-pipeline philosophy arrives here earlier). Delivery is
  the classic softmod flow: FTP the patched XBE into the game's folder on the
  Xbox HDD; the softmodded kernel skips retail signing for HDD launches. XBE
  section-append is well-trodden modder ground, so the coach-brain has a home
  from the start. Split of duties: **we test under xemu on the rig; he plays on
  hardware** — gameplay-logic patches are timing-agnostic, but each delivered
  build gets a hardware smoke test from him (boot + one game) as the final arm.

## Phases

- **X0 — acquire + inventory.** Dump the disc (owned copy), extract the XBE +
  data tree, verify the no-online assumption. (The delivery-platform question is
  already answered: softmodded console — see Patch delivery above.)
- **X1 — the anchor sweep (decisive, cheap).** Run the fourcc/constant/shape
  sweep over the XBE; run `madden_tdb`/`lzh1` against the Xbox data files.
  Output: a **similarity verdict** that sizes everything downstream.
- **X2 — map the port surface.** Re-derive the hook table from the PS2 map:
  the seam-equivalent (the script command handler — likely reachable *via the
  shared script asset*), the two ptrk getters, the recorder, `IsRun`, the
  shift picker, the situation object, and the player-struct offsets (found via
  the ratings-block signature). Each: x86 address + evidence, same discipline
  as here.
- **X3 — tracer bullets.** Port one or two *proven one-worders* first (the C1
  eligibility analogue, or neutering a cheat getter) end-to-end: patch XBE →
  boot → observe. Validates the entire toolchain before anything big rides it.
- **X4 — the big ports.** The blocking set, then the coach-brain — *after* the
  PS2 build ships it, using the module's platform-agnostic spec as the input.

## Risks & open questions

- **Compiler divergence** — inlining merges/splits functions across platforms;
  code-shape matching fails where data anchors don't. Mitigation is the thesis
  itself: anchor on data.
- **Content divergence** — the Xbox build may carry different tuning or feature
  deltas; X1's sweep catches gross divergence, X2's evidence discipline catches
  the subtle kind.
- **Toolchain decision** — Ghidra-headless vs capstone (the pip-purity call).
- ~~The friend's platform~~ — **RESOLVED: softmodded console** (delivery via
  FTP'd patched XBE; hardware smoke test as the final acceptance arm).
- **xemu GDB stub state in the rig build** — verify before counting on live
  debugging.

## X0 + X1 RESULTS (2026-08-14): SHARED BUILD — the port thesis is CONFIRMED

Run same-day against the operator's dump (`Madden NFL 2004 (USA).xiso.iso`,
3.11 GB, XISO game-partition format, magic at 0x10000).

**X0 — extraction + inventory: DONE.**
- XDVDFS walked clean: 66 files; the `/DATA/` tree mirrors the PS2 disc
  nearly name-for-name (`DB_TEAMS.DAT`, `DB_TEMPLATES.DAT`, `PLADATA.DAT`,
  `GAMEDATA.DAT`, the whole `UIS_*` family our `lzh1` tooling opens).
  Inventory: `extract/xbox/inventory.txt`.
- `default.xbe` extracted (4,890,624 B; PS2 ELF is 5.35 MB — sibling-sized).
  Cert title `'Madden NFL 2004'`, title id `0x45410036`, retail entry
  `0x0025A6C6`, base `0x00010000`, 11 sections (`.text` 3.55 MB at `0x11000`;
  `.rdata` raw `0x3D5000`; `.data` raw `0x40D000`).
- **No-online is now a FINDING, not an assumption:** the linked-library table
  is XAPILIB / D3D8 / XGRAPHC / XBOXKRNL / DSOUND / LIBCMT / D3DX8 —
  **no XNET, no XONLINE**; zero `DNAS`/`easo.ea.com` strings. Gameplay-only
  scope confirmed.

**X1 — the anchor sweep: DONE, verdict SHARED BUILD.**
- **14/14 fourccs present.** Registration constants as reversed-byte code
  immediates exactly as predicted (`ptrk` @0x11C215 in `.text`, `fatg`
  @0x39824, `madt`, `prac`); DB tags as forward ASCII in the string pool
  (`HCOC` ×346 in `.rdata`, `LPBP` ×30, `tcrp` ×5…). (Counting note: the
  palindromic pairs PBAI↔IABP and AIGR↔RGIA match each other's reversed form.)
- **The ptrk weight tables, byte-identical:** the 16-byte recency run
  1/24, 1/48, 1/96, 1/192 at **0x44C2F4** — with the **success table adjacent**
  (0.0625, 1/96, 1/192, ~0.00269) and two further 4-float weight tables at
  +0x20 (richer than what we dumped on PS2; note for X2).
- **The same DB-query dialect**, in the same neighborhood: `select 'YTDC' into
  … from 'HCOC' where 'DIGT' = … and 'SPOC' = …`, and the clincher —
  **`select 'STPG' into … from 'NIBG'`** (`GBIN` reversed): the ptrk franchise
  save/load path, present and identical in shape.
- **Constants:** `165.75` (the shed power gate) **exactly once** — unique on
  PS2 too; `12.75` (fatigueB→ratings) ×4; `0.175` ×3; `2.1` ×8; `32767.0` ×6;
  `0.3` ×67 (noisy, expected).
- **Two genuine divergence candidates:** `−0.13` (the kind-8 helper debuff) and
  `335.4` (collision inverse-mass) absent as float AND double (and as
  0.13 / reciprocal). Either different tuning, computed forms, or changed
  implementation — **X2 items**, resolved by finding each site's twin and
  reading what it actually loads. Two misses against this wall of confirmation
  does not move the verdict.

**Consequence:** X2 proceeds as re-derivation-from-a-map, as the effort model
predicted. Immediate next steps: (1) the data-tooling test — `madden_tdb.py` /
`lzh1` against `/DATA/` (if the PS2 tooling reads Xbox data unchanged, the
data layer is fully shared); (2) stand up the Ghidra project over `default.xbe`
and locate the twins of the priority hooks (the seam, the two ptrk getters, the
recorder, `IsRun`, the shift picker); (3) solidify the one-shot XDVDFS/XBE
scripts into `recon/` tools with tests.

## Status

X0 and X1 **DONE — shared build confirmed** (above). Next: the data-tooling
test and the X2 hook re-derivation. The rig enters only at X3 (xemu boot of a
patched XBE); delivery target remains the friend's softmodded console.

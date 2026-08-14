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

## Status

Plan only — nothing acquired, nothing swept. **X1 is the everything-gate**: one
cheap sweep decides whether this is a port or a second research project. No rig
or PS2-side work is blocked on any of this; it proceeds whenever a disc dump
exists.

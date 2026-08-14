# `pnach2xbe` — turning a PS2 pnach into a patched Xbox ISO

Specification, 2026-08-14. The ask: **a program that takes any pnach and produces
a patched Xbox XBE/ISO** — giving the project a single patch source that fans out
to **both** consoles: pnach → PS2 ISO (built, verified) and pnach → Xbox XISO
(this spec). Plus a standalone utility (§7b) that reports **where and how much
free space the XBE has for injected code**. This spec says what such a program can
and cannot do, decomposes it into buildable stages, marks the research boundary
honestly, and phases the work so useful output arrives early.

---

## 0. The verdict up front

**A byte-level transcoder is impossible, and no amount of engineering fixes
that.** A pnach line is `(PS2 address, 32-bit MIPS word)`. Neither half survives
the crossing:

- The **word** is MIPS machine code. `1000000F` is `beq zero,zero` on MIPS and
  gibberish on x86. There is no byte mapping.
- The **address** has no arithmetic relation to its Xbox counterpart. PS2
  `0x001F2D60` ≡ Xbox `<twin>` only because a human *found* it.

**But the tool is still buildable**, because a patch is not really bytes — it is
an *intent* applied at a *place*. Both are recoverable:

| what a patch is | recoverable? | how |
|---|---|---|
| the **place** | yes, with confidence scoring | cross-architecture function matching on data anchors (proven: `'ptrk'` immediate, `0x614` alloc size, `0xAFBC` stride all matched by hand in one evening) |
| the **intent** — for edits to *existing* instructions | yes, mechanically | classify the stock instruction, classify the patched one, emit the equivalent edit per-ISA |
| the **intent** — for *injected code* (caves) | partially | a bounded MIPS→x86 translator for the cave dialect; general translation is research |

So: **`pnach2xbe` is a semi-automatic porting tool with a confidence model, not a
transcoder.** It automates the mechanical majority, and it *tells you precisely*
what it cannot do rather than guessing.

**Reality check on our own patch set** (measured): the deployed 102-word set is
**5 site patches + 97 words of cave body**. By *count* it is 95% cave; by
*significance* the 5 sites are most of the behaviour. Both numbers matter — the
sites port nearly free, the caves are the work.

---

## 1. Decomposition — three independent problems

```
   pnach ──▶ [1. LOCATE]  where is this on Xbox?      ── the correspondence problem
         ──▶ [2. LIFT]    what does this patch DO?     ── the semantics problem
         ──▶ [3. EMIT]    write it into the XBE + ISO  ── the packaging problem
```

Only #2's cave case touches research. #1 is applied binary diffing with prior
art. #3 is well-trodden file-format work.

---

## 2. Capability matrix (what the tool actually promises)

| patch class | example from our set | automation | notes |
|---|---|---|---|
| **branch force** (always/never taken) | C1 `beq s6,zero` → `beq zero,zero` | **AUTOMATIC** | classify stock vs patched → same edit on the x86 conditional (`jz` → `jmp`, or `nop`) |
| **immediate change** | P4 `sltiu …,61` → `…,361`; P11 `addiu a1,zero,16` → `0` | **AUTOMATIC** | find the corresponding x86 immediate operand; width/sign checked |
| **nop-out** | (common in cheats) | **AUTOMATIC** | x86 needs length-aware nopping (variable-length ISA) — emit `0x90`×n or a `jmp` over |
| **data word** (constants, table entries) | T3's `k` = 0.8 | **AUTOMATIC** | both platforms little-endian; needs the data-symbol map, not the code map |
| **hook + cave** (divert to injected code) | P1, N-1, T3 | **ASSISTED** | hook is automatic; **cave body needs an x86 implementation** (translated or hand-written) |
| **arbitrary foreign pnach** (someone else's cheat, unknown intent) | — | **BEST-EFFORT + REPORT** | classifiable edits port; unclassifiable words are refused with a reason |

**Design rule, non-negotiable:** the tool **never guesses**. Anything it cannot
classify with evidence is reported as unported, with the address, the stock
instruction, the patched instruction, and why it failed. A wrong patch that boots
is far worse than a refusal — this project's own history is a catalogue of
plausible-looking wrong answers.

---

## 3. Architecture — ONE front-end, TWO back-ends

The pnach is the **single source of truth for a patch**, and it fans out to both
consoles. The PS2 path already exists and is verified; the Xbox path is what this
spec adds. Same IR, same manifest discipline, same verification posture:

```
                          ┌──────────────────────────────┐
   pnach ────────────────▶│  1. LIFT (pnach → PatchIR)   │──▶ PatchIR (json)
   PS2 ELF ──────────────▶│  classify each line's INTENT │        │
                          └──────────────────────────────┘        │
                                                                  │
                    ┌─────────────────────────────────────────────┤
                    │                                             │
        ┌───────────▼──────────────┐              ┌───────────────▼─────────────┐
        │  PS2 BACK-END   ✅ BUILT │              │  XBOX BACK-END    ← to build│
        │  bake_pnach.py           │              │  2. LOCATE (correspondence) │
        │   → patched ELF          │              │  2b. SPACE SURVEY (§8)      │
        │  patch_iso_elf.py        │              │  3. EMIT → patched XBE      │
        │   → patched PS2 ISO      │              │  4. PACKAGE → XISO          │
        └──────────────────────────┘              └─────────────────────────────┘
                    │                                             │
              PS2 ISO (playable)                            XISO (playable)
```

**Consequence of the symmetry:** one patch definition, two shippable discs. Write
a fix once, verify it on PS2 where the harness and savestates live, then emit the
Xbox build for the friend. It also means the *manifest* format, the `--verify`
contract, and the refusal discipline are shared — the PS2 side already proved
them on the live 102-word set.

Each stage is a standalone tool with a file format between them — so a human can
inspect, correct, and re-run any stage. That is the whole reason for the IR: the
correspondence map is *reviewable* before anything is written.

---

## 4. Stage 1 — LIFT: pnach → PatchIR

**Input:** a pnach + the PS2 ELF it targets (needed for the *stock* bytes).
**Output:** `PatchIR` — one record per patch line, carrying intent.

For each line: disassemble the **stock** word and the **patched** word at that
address, then classify the delta:

```json
{ "site": "0x001F2D60",
  "stock":   { "raw": "12C0000F", "mnemonic": "beq",   "ops": ["s6","zero","0x001f2da0"] },
  "patched": { "raw": "1000000F", "mnemonic": "beq",   "ops": ["zero","zero","0x001f2da0"] },
  "intent":  { "kind": "branch_force", "value": "always", "target_preserved": true },
  "confidence": "certain" }
```

Classifier rules (extensible; each must be evidence-backed):
- same mnemonic, operands become `zero,zero` → **branch_force always**
- same mnemonic, immediate differs → **immediate_change** (old, new)
- patched word is `nop`/`0` → **nop_out**
- patched word is `j`/`jal` to an address **outside the game's code band** →
  **hook_to_cave** (records the cave target)
- the address lies **inside a known cave region**, and stock is dead space →
  **cave_body** (accumulate contiguous runs into one cave object)
- neither side disassembles as sane code → **data_word**
- anything else → **unclassified** (reported, never guessed)

Cave bodies are grouped into a **Cave** object: base address, word list, entry
point, plus the discovered outbound `jal`s into the host game (these become
*symbolic* references — `call host:0x001f0c40` — because the x86 emitter must
retarget them through the hook map).

**Ambiguity is expected and handled:** MIPS delay slots mean a "one-word" site
patch may pair with the next instruction; the classifier records the delay-slot
instruction alongside and flags any patch that changes control flow without
accounting for it.

---

## 5. Stage 2 — LOCATE: cross-architecture correspondence

The heart of the tool. Input: PS2 ELF + Xbox XBE. Output: a **scored** map from
PS2 addresses to Xbox addresses, plus a residue list of unmatched sites.

**Method, in confidence order** (all four are proven by hand in this project):

1. **Data anchors (strongest).** Registration fourccs (`'ptrk'` = `0x7074726B`),
   unique float constants (`165.75` occurs exactly once in *both* builds),
   allocation sizes (`0x614` = 1556), structure strides (`0xAFBC`). Find the
   constant in both binaries, then find the code that references it. On x86 this
   means scanning `.text` for the 4-byte absolute VA; on MIPS, `lui`/`addiu`
   pairs and gp-relative loads.
   *Hard-won lesson, encoded as a tool invariant:* the sweep must convert **file
   offsets to virtual addresses** before searching for xrefs. Searching a file
   offset yields zero hits and reads exactly like "this build lacks the feature."
   This error occurred in this project on 2026-08-14 and cost a full re-run.
2. **Call-graph shape.** Once N functions are anchored, their callers/callees
   constrain neighbours: a function calling three known twins in the same order
   is almost certainly the twin of the corresponding PS2 caller.
3. **Structural fingerprints.** Constant sequences, table sizes, loop counts,
   switch-table arity, string references — architecture-independent features that
   survive compilation.
4. **Intra-function offset.** Having matched a *function*, find the specific
   *instruction* by role (the branch testing this field, the immediate holding
   this constant) rather than by byte position. This is the weakest link and the
   one most in need of human review.

Every mapping carries evidence and a score: `certain` (unique anchor),
`probable` (multiple corroborating features), `possible` (one weak feature),
`unmatched`. **Only `certain` and human-approved `probable` are patched.**

**The map is a durable, reviewable artifact** (`maps/ps2-to-xbox.json`) that
accumulates across sessions — every hand-verified twin (we already have eight
from the X2 pass) is seeded into it, and the tool proposes new ones against it.

---

## 6. Stage 3 — EMIT: write the patch into the XBE

**Site edits (the automatic classes).** Disassemble the x86 at the mapped site,
apply the classified intent, re-encode:
- `branch_force always` → replace `jz rel` with `jmp rel`; if lengths differ, pad
  with `nop`s (x86 instruction lengths vary, so the emitter must fit the original
  byte span *exactly* or refuse).
- `immediate_change` → locate the immediate operand, verify the width holds the
  new value, patch in place.
- `nop_out` → `0x90` × instruction length.
- `data_word` → little-endian store at the mapped data address.

**Caves.** The x86 body must exist (see §7). Placement: **append a new section**
to the XBE (update the section table, header sizes, and — if present — the
section digests). The hook becomes a `call`/`jmp rel32` to the new section, which
x86 reaches trivially at any distance. Outbound `call host:*` references inside
the cave are relocated through the hook map at emit time.

**Verification (mandatory, mirrors `bake_pnach.py`'s `--verify`):** re-read the
written XBE, disassemble each patched site, and assert it matches the intent;
assert no byte outside a declared patch span changed; emit a manifest with
before/after for every edit.

---

## 7. The cave problem — the honest research boundary

97 of our 102 words are cave body. Three strategies, in increasing ambition:

**(a) Hand-written x86 twin (recommended now).** Someone writes the x86 version
of each cave once; the pipeline manages placement, relocation, verification and
packaging. For our set that is **two caves** (P1's 21 words, N-1+T3's 66). This
is a bounded, honest afternoon or two per cave — and the cave logic is simple
(read player fields, float-add into three comps, store).

**(b) Bounded MIPS→x86 translator for the "cave dialect."** Our caves use a small
subset: load/store at a struct offset, integer compare-and-branch, float add/mul,
`jal` into the host. That subset *is* mechanically translatable — the obstacles
are real but bounded: 32 MIPS registers → 8 x86 (needs a register allocator or a
memory-backed virtual register file), MIPS COP1 flat registers → x87 stack or
SSE, and delay-slot semantics. Feasible; a genuine project, not a weekend.

**(c) Stop writing caves in MIPS.** The strategic answer: author *future* caves in
a small IR (or C compiled to both targets) so the port is free by construction.
This is what the coach-brain should do from day one — it is already required to
be a "platform-agnostic module" (`ai-coach-playcalling-requirements.md` §3).
Then only the legacy blocking caves ever need hand-porting.

**Recommendation: (a) now, (c) for everything new, (b) only if hand-porting
becomes the bottleneck.**

---

## 7b. `xbe_space.py` — the free-space surveyor (a standalone utility)

**Operator requirement (2026-08-14): a separate tool whose ONLY job is to report
where and how much free space exists in the XBE for our new code.** It answers
one question and answers it with evidence — it never patches anything.

This is the Xbox counterpart of `docs/code-caves.md` (the PS2 survey that found
~9.2 KB of dead code), but the Xbox answer is expected to be *structurally
different and much better*, because the XBE is a PE-derived format where **adding
a section is a legitimate operation** — we are not limited to scavenging dead
bytes. The surveyor's job is to quantify every option and flag every constraint.

**What it reports, in four classes:**

| class | what | why it matters |
|---|---|---|
| **1. Section-append headroom** *(expected primary answer)* | can a new section be added? how large? what constrains it — the section-table location, header room before the first section's raw data, image-size fields, any hard cap | this is where the coach-brain lives; likely effectively unbounded |
| **2. In-file slack** | padding between sections (raw size vs virtual size, file-alignment gaps), the tail after the last section | free bytes needing no structural change — ideal for small caves |
| **3. Zero-reference dead code** | the PS2 five-axis census, ported to x86: split at `ret` boundaries, then require no `call`/`jmp`/branch into the region, no absolute VA anywhere in the image pointing inside it, no jump-table entry | the drop-in-place option; **x86 makes the xref scan easier** (absolute VAs are literal 4-byte words) |
| **4. Virtual-address room** | gaps in the VA layout between sections, and above the last section, that a new segment could claim | needed if a section must land at a specific VA |

**Hard constraints it must check and report** (each can invalidate an otherwise
"free" region):
- **Section digests / image integrity.** XBE section headers carry SHA-1 digests
  and the cert carries header/image hashes. The surveyor must report *which*
  integrity fields exist, whether the softmodded loader enforces them, and
  therefore what an emitter has to recompute. **Unverified for this title until
  checked — treat as blocking until proven otherwise.**
- **Section flags** (executable/writable/preload) — a "free" region that isn't
  executable is not a cave.
- **`$$XTIMAGE` / trailing sections** — whether appending after them is safe.
- **Alignment rules** (raw and virtual) the appended section must satisfy.
- **Anything relocated or overwritten at runtime** — the PS2 survey's standing
  caveat (a computed store can invalidate a static census); the surveyor states
  what it *cannot* prove statically.

**Output:** a ranked inventory — address, size, class, executable?, evidence, and
a risk note — in both human-readable and JSON form, so the EMIT stage can consume
it directly as an allocator input. Same posture as `recon/cave_census.py`: this
project has documented four "safe" regions that were live, so the surveyor
reports *evidence*, and its regions stay unproven until a runtime check passes.

**Standalone by design.** It runs against any XBE, needs no pnach and no
correspondence map, and is useful on day one — knowing the space budget shapes
how the caves get written (§7). Ship it in `tools/xbe_space.py` with tests
against `default.xbe`.

## 8. Stage 4 — PACKAGE: XBE → XISO

Mechanical. `recon/xdvdfs.py` already *reads* XDVDFS; this adds the writer:
- **Same-size XBE** → in-place overwrite, image otherwise byte-identical (the
  `patch_iso_elf.py` pattern, already built and tested for PS2).
- **Grown XBE** (any cave append) → either rewrite the image, or append the file
  at the end and repoint its directory entry's sector+size. The XDVDFS directory
  is a btree of fixed-shape records, so an in-place size/LBA edit is tractable.
- Output verification: re-extract the XBE from the produced ISO and byte-compare
  against the emitter's output.

**Delivery:** the friend runs a softmodded console — FTP the patched XBE (or burn
/ mount the XISO), with a hardware smoke test as the final acceptance arm.

---

## 9. Phasing (useful output early)

| phase | deliverable | unlocks |
|---|---|---|
| **A** | `lift_pnach.py` — pnach + ELF → PatchIR, with the classifier and a report of what it can/can't port | tells us *today* exactly which lines of any pnach are portable |
| **A2** | **`xbe_space.py` — the free-space surveyor (§7b)**. Standalone, no dependencies on the rest | the space budget, which decides how caves get written; useful immediately |
| **B** | the hook-map format + seeding from the 8 hand-verified twins; `locate.py` for data-anchor matching with scores | the reviewable correspondence artifact |
| **C** | `emit_xbe.py` for **site edits only** (no caves) + XBE writer + verify | **C1 on Xbox as a tracer bullet** — one branch, end-to-end, boots under xemu |
| **D** | XISO writer + packaging | a sendable disc image |
| **E** | hand-written x86 caves for P1 and N-1/T3 | **the friend's working double teams** |
| **F** | *(optional)* the cave-dialect translator | future caves port automatically |

Phase C is the milestone that matters: it proves LOCATE→EMIT→boot on a patch
whose behaviour we already know by eye.

---

## 10. Acceptance tests

- **T-lift:** every line of the deployed 102-word set classifies; the 5 site
  patches land as `branch_force`/`immediate_change`/`hook_to_cave`; the 97 cave
  words group into exactly 2 caves with correct entry points.
- **T-lift-negative:** a deliberately garbled pnach line is reported
  `unclassified`, never guessed.
- **T-locate:** the tool independently rediscovers the 8 hand-verified twins from
  `docs/xbox-hook-map.md` and scores them `certain`; it must NOT claim a twin for
  a PS2 function known to be absent on Xbox.
- **T-emit:** a `branch_force` written into the XBE disassembles as the intended
  unconditional jump; no byte outside the patch span changes; `--verify` passes.
- **T-space:** the surveyor reports a section-append budget for `default.xbe` and
  enumerates in-file slack; every claimed dead region survives the x86 xref census
  (no call/jmp/branch in, no absolute VA anywhere pointing inside); the integrity-
  field question (digests enforced or not) is answered explicitly, not assumed.
- **T-package:** XBE re-extracted from the produced XISO is byte-identical to the
  emitter's output.
- **T-boot (rig):** the C1-only XBE boots under xemu and reaches a game.
- **T-behaviour (operator):** on the C1 XBE, the pulling guard takes the right
  man — the same result confirmed on PS2 on 2026-08-13.

---

## 11. Scope limits — stated plainly

1. **Foreign pnaches port only their classifiable lines.** For a cheat we did not
   write, intent must be *inferred* from the instruction delta. Simple edits
   (infinite time, a stat cap, a branch force) will port. A cheat carrying its own
   MIPS payload will not, without §7(b).
2. **Game-version coupling.** The map is per title-pair (SLUS-20752 ↔ this XBE).
   Other titles need their own anchors, though the *method* transfers.
3. **No behavioural guarantee.** A correctly-ported patch can still behave
   differently if the Xbox build diverges (two constants already diverge:
   `−0.13` and `335.4`). Every ported patch keeps its PS2 acceptance test and is
   re-verified on Xbox — the operator's eyes remain the instrument of record.
4. **Not a general emulator/recompiler.** This ports *patches*, not the game.

---

## 12. Status

Specification only; nothing built. Existing pieces that plug straight in:
`bake_pnach.py` (the classifier's sibling and the PS2 emit path), `recon/xbe.py`
(XBE parse), `recon/xdvdfs.py` (XISO read), `docs/xbox-hook-map.md` (8 seed
mappings), capstone 5.0.7 (x86 encode/verify), `recon/mipsdis.py` + `fpudis.py`
(MIPS decode). Phase A can start immediately and is useful on its own.

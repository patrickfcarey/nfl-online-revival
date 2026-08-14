# `pnach2xbe` — turning a PS2 pnach into a patched Xbox ISO

Specification, 2026-08-14. The ask: **a program that takes any pnach and produces
a patched Xbox XBE/ISO** — giving the project a single patch source that fans out
to **both** consoles: pnach → PS2 ELF (built and verified on the live set; the
ISO arm is built but untried on a real image — §3) and pnach → Xbox XBE/XISO
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
| the **place** | yes, to *function* granularity, with confidence scoring | cross-architecture function matching on data anchors (proven: `'ptrk'` immediate, `0x614` alloc size, `0xAFBC` stride all matched by hand in one evening). To *instruction* granularity: unproven here (§5, method 4) |
| the **intent** — for edits to *existing* instructions | mechanically **stated**, not always mechanically **applied** | classify the stock instruction, classify the patched one, emit the equivalent edit per-ISA — but see §2: for a branch, "the equivalent edit" depends on which x86 arm is which, and no byte pattern says |
| the **intent** — for *injected code* (caves) | partially | a bounded MIPS→x86 translator for the cave dialect; general translation is research |

So: **`pnach2xbe` is a semi-automatic porting tool with a confidence model, not a
transcoder.** It automates the mechanical majority, and it *tells you precisely*
what it cannot do rather than guessing.

**Reality check on our own patch set** (re-measured against
`patches/14F8B841.c1-plus-doubleteam.pnach` and `extract/SLUS_207.52` during the
§13 review, not carried over from an earlier note):

```
102 patch lines, 102 distinct addresses, all EE, all width=word
  5  site patches  001F153C  001F21E8  001F2D60  001F4A30  001F6A74
 76  words  004F4AA0..004F4BCC   (N-1 cave + T3 tail + the k constant)
 21  words  00514920..00514970   (P1 cave)
```

By *count* it is 95% cave; by *significance* the 5 sites are most of the
behaviour. Both numbers matter — but "the sites port nearly free" is the claim
§2 had to walk back: on x86 a site is free to *encode* and not free to *aim*.

---

## 1. Decomposition — three independent problems

```
   pnach ──▶ [1. LIFT]    what does this patch DO?     ── the semantics problem
         ──▶ [2. LOCATE]  where is this on Xbox?       ── the correspondence problem
         ──▶ [3. EMIT]    write it into the XBE        ── the encoding problem
         ──▶ [4. PACKAGE] put the XBE back in an image ── the file-format problem
```

(Stage numbers are LIFT=1, LOCATE=2, EMIT=3, PACKAGE=4 throughout — §4, §5, §6,
§8. An earlier draft numbered LOCATE first here and LIFT first below; they are
reconciled to this order.)

Only LIFT's cave case touches research. LOCATE is applied binary diffing with
prior art. EMIT and PACKAGE are well-trodden file-format work — but see §6:
"well-trodden" is not the same as "automatic", and the honest boundary between
the two moved during this review.

---

## 2. Capability matrix (what the tool actually promises)

Three levels, and the third column is the one that matters: **what a human must
supply before the automation can run.** A row is only AUTOMATIC if that column
is empty. The first draft of this table had four AUTOMATIC rows; the review in
§13 downgraded three of them, because "the encoding fits" and "the edit is
correct" are different claims and only the first one is mechanical.

| patch class | example from our set | automation | what a human must supply |
|---|---|---|---|
| **branch force** (always/never taken) | C1 `beq s6,zero` → `beq zero,zero` | **ASSISTED** | **which x86 arm is which.** The encoding always fits (§6). The *sense* does not transfer: MSVC is free to emit the inverted condition with the blocks swapped, in which case `jz`→`jmp` forces the opposite behaviour. A human (or a decompiler) must say which successor of the x86 branch is the twin of the PS2 fall-through. |
| **call retarget** (an existing `jal` gets a new target) | N-1 `jal 0x001F0C40` → `jal 0x004F4AA0` | **AUTOMATIC** | nothing, for the *site*. This is the cleanest class on x86: rewrite the 4-byte `rel32` of an existing `E8`. The cave it points at is a separate problem. |
| **immediate change** | P4 `sltiu …,61` → `…,361`; P11 `addiu a1,zero,16` → `0` | **ASSISTED** | **which instruction holds the constant.** Once identified, re-encoding is mechanical and width/sign are checkable. Identification is LOCATE method 4 (§5), which has *no* track record in this project yet. MSVC may also fold or pre-scale the constant, or reach it through a different instruction entirely. |
| **nop-out** | (common in cheats) | **AUTOMATIC** | nothing, once the instruction boundary is known — and it is, because EMIT disassembles from a harvested function head rather than guessing a boundary (`docs/xbox-hook-map.md` §2). `0x90`×len. |
| **data word — scalar constant** | T3's `k` = 0.8 (but see below) | **ASSISTED** | **the data twin, and the representation.** Both platforms are little-endian, but the EE's COP1 is single-only while MSVC freely uses `double` — `docs/xbox-hook-map.md` §4 records a `double 1.02667` on Xbox, and X1 had to search `−0.13`/`335.4` "as float AND double". A 4-byte PS2 float can correspond to an 8-byte Xbox double: not a copy, a conversion. |
| **data word — pointer or handle** | W1 `0x00583868`: `0x001F3848` → `0x001F3518` | **REFUSE unless the value maps** | the *value* is a PS2 code address. Writing it verbatim into an Xbox function-pointer table is a guaranteed crash. This class needs the **code** map applied to the value and the **data** map applied to the address — two lookups, both of which must be `certain`. |
| **hook + cave** (divert to injected code) | P1, N-1, T3 | **ASSISTED** | **the register/stack binding, and the displaced instructions.** MIPS replaces exactly one word and keeps every live register; x86 must carve ≥5 bytes (72.3% of instructions in this `.text` are shorter than that — §6), relocate what it displaced, and be told where the values the cave reads actually live. Plus an x86 cave body (§7). |
| **arbitrary foreign pnach** (someone else's cheat, unknown intent) | — | **BEST-EFFORT + REPORT** | classifiable edits port; unclassifiable words are refused with a reason |

**On T3's `k`:** it is listed above as the scalar-constant example because it is
the one tunable float in the set, but it is **not** a stock data word — it lives
at `0x004F4BCC`, *inside* the N-1 cave, four bytes past the cave's last
instruction. It has no PS2 twin to map because we put it there. It ports with
the cave, not with the data map. The set as deployed contains **no** stock data
word at all; the only one we have written (W1) is disabled and is a pointer.

**Design rule, non-negotiable:** the tool **never guesses**. Anything it cannot
classify with evidence is reported as unported, with the address, the stock
instruction, the patched instruction, and why it failed. A wrong patch that boots
is far worse than a refusal — this project's own history is a catalogue of
plausible-looking wrong answers.

**Corollary the matrix makes explicit:** the leverage in this tool is not the
pnach front-end. It is the **map plus the manifest discipline** — a reviewable,
versioned record of every correspondence and every byte written. The pnach is a
convenient input format, not a source of automation.

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
        │   → patched ELF          │              │  2b. SPACE SURVEY (§7b)     │
        │  patch_iso_elf.py        │              │  3. EMIT → patched XBE      │
        │   → patched PS2 ISO ⚠    │              │  4. PACKAGE → XISO          │
        └──────────────────────────┘              └─────────────────────────────┘
                    │                                             │
              PS2 ISO (playable)                            XISO (playable)
```

⚠ `patch_iso_elf.py` is built and unit-tested (25 tests, round-tripped on a
synthetic ISO carrying the real stock ELF) but **has never run against a real
PS2 disc image** (`docs/pnach-to-iso-pipeline.md`, "What remains"). "Verified"
below means verified on the ELF arm, which is the arm this spec reuses.

**Consequence of the symmetry:** one patch definition, two shippable discs. Write
a fix once, verify it on PS2 where the harness and savestates live, then emit the
Xbox build for the friend. It also means the *manifest* format, the `--verify`
contract, and the refusal discipline are shared — the PS2 side already proved
them on the live 102-word set.

**And it constrains the front-end:** because one source must not mean two
things, `pnach2xbe` adopts `bake_pnach.py`'s parsing decisions *exactly* —
`word` only (`byte`/`short`/`extended` refused by name), unknown directives
refused rather than skipped, duplicate-vs-conflict handling identical, and
`patch=0` lines **parked, not applied**. That last one is a project convention,
not the cheat engine's semantics (PCSX2's `patch=0` is `PPT_ONCE_ON_LOAD`, which
is arguably what a bake *is* — `docs/code-caves.md`, "pnach mechanics"). Keep
the two back-ends wrong in the same direction rather than right in different
ones; if the convention changes, it changes in both tools in one commit.

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
- same mnemonic and same opcode class, immediate differs → **immediate_change**
  (old, new)
- both sides are `jal`, target differs → **call_retarget** (old target, new)
- patched word is `nop`/`0` → **nop_out**
- patched word is `j`/`jal` whose target is **also an address this pnach
  writes** → **hook_to_cave** (records the cave target)
- the address is reachable by walking forward from a `hook_to_cave` target
  through addresses this pnach writes → **cave_body**
- neither side disassembles as sane code → **data_word**
- anything else → **unclassified** (reported, never guessed)

**Precedence, because two of those rules overlap on our own set.** N-1 matches
both `call_retarget` (both sides `jal`) and `hook_to_cave` (the new target is a
pnach-written address). The classes are orthogonal and both are recorded: the
*kind* is `call_retarget` — that is what the site edit is — with
`target_is_cave: true` naming the cave the change also owns. A rule set that
forces a single label here loses either the cheap site edit or the cave
dependency, and both matter downstream.

**Two rules in the first draft were wrong and are corrected above** — both
checked against the real files, because both would have misclassified most of
the deployed set:

- *"a `j`/`jal` to an address outside the game's code band"* has no referent.
  Both of our caves are **inside** the loaded image: N-1's at `0x004F4AA0` is
  dead libc inside `.text`, P1's at `0x00514920` is the 96-byte linker pad
  between `.vutext` and `.data` (`.data` starts at `0x00514980` — read from the
  ELF section table). There is no band test that separates them from ordinary
  code. **Cave detection is intra-pnach**: a jump target that the same pnach
  also fills in is a cave, and nothing else is.
- *"stock is dead space"* is not a zero test. The P1 cave's 21 stock words are
  all zero, but **73 of the N-1 cave's 76 stock words are non-zero** — it is
  dead *code*, not padding (`docs/code-caves.md` region #4). A classifier that
  requires zeros refuses 76 of the 97 cave words in our own set.

Cave bodies are grouped into a **Cave** object: base address, word list, entry
point, and three things the first draft omitted, each of which is load-bearing:

1. **Outbound host calls**, as *symbolic* references (`call host:0x001f0c40`) —
   the x86 emitter must retarget them through the map. Our N-1 cave has two:
   `0x001F0C40` (the displaced original callee) and `0x0013B798` (the helper
   resolver). **Neither has an Xbox twin in `docs/xbox-hook-map.md`.** Each also
   needs its *calling convention and arity* recorded, because MIPS `a0..a3`
   becomes `__cdecl` / `__stdcall` / `__thiscall` on Xbox and getting
   caller-cleanup wrong corrupts the stack silently.
2. **Internal control flow.** The T3 tail is not a second cave: `0x004F4B60`
   is `j 0x004F4BA8` and `0x004F4BC4` is `j 0x004F4B68`, both *inside* the same
   contiguous run. A cave has one external entry and may have any number of
   internal targets; the IR must distinguish them or the emitter will try to
   map an internal label through the hook map.
3. **The cave's data footprint**, which is invisible in the patch lines. The
   N-1 cave stores canaries to `0x00514978` and `0x0051497C` (`lui t0,0x51` /
   `sw t1,0x497C(t0)`), and loads `[gp − 17520]`. **Neither address appears as
   a patch line.** A port that carries only the written words carries a cave
   that scribbles on two Xbox addresses nobody chose. LIFT must extract every
   absolute and `gp`-relative address a cave touches and put it in the IR as a
   *requirement on the map*.

**`gp` is resolvable, and the IR must resolve it.** MIPS `gp`-relative loads
(`lw t2,-17520(gp)`) have no x86 counterpart, but they are not an obstacle:
`SLUS_207.52`'s `.reginfo` section carries `ri_gp_value = 0x006056F0` (read from
the ELF this session), so `-17520(gp)` is `[0x00601280]` — an ordinary absolute
global that goes through the data map like any other. LIFT rewrites every
`gp`-relative access to its absolute address before the IR is emitted; a cave
that survives into EMIT still carrying `gp` is a bug.

**Delay slots, stated properly.** "One-word patch" is a MIPS fiction: the word
after a patched branch or jump still executes, so the smallest unit of ported
behaviour is two instructions, and the classifier must carry both. Three cases,
all present in our own set:

| case | our example | what the port must reproduce |
|---|---|---|
| ordinary delay slot, unchanged | N-1 `0x001F1540` = `daddu a1,s4,zero` | the argument setup still runs before the call — on x86 it is simply part of the code the hook displaces |
| **branch in a jump's delay slot** | P1: `j 0x00514920` at `0x001F4A30`, delay slot `0x001F4A34` = `beql s0,v0,0x001F4AB0` | architecturally undefined on the R5900. The cave's two exits (`j 0x001F4A34`, `j 0x001F4AB0`) show the author routing around it. **LIFT must flag this, not port it** — the x86 twin has to be re-derived from intent, and the PS2 line itself deserves a second look. |
| **branch-likely** (`beql`/`bnel`) | the same `beql` | the delay slot is *annulled* when not taken. A classifier that treats `beql` as `beq` gets the not-taken path wrong. Branch-likely and REGIMM forms are enumerated explicitly, as `docs/code-caves.md`'s census already had to do. |

**Grouping: the unit of porting is a change, not a line.** P1 is one change made
of a site word, a delay slot, 21 cave words and 2 canary addresses; N-1+T3 is
one change made of a site word and 76 cave words. The pnach format has no syntax
for this, so LIFT infers it (hook → cave → contiguous run → internal targets)
and accepts a sidecar `.changes.json` to declare what it cannot infer.
**A change ports whole or not at all** — a half-ported hook is the worst
possible output, worse than a refusal, because it boots.

**Input guards LIFT must apply before any of the above:** the pnach's
`gametitle` CRC must match the ELF it is given (our set is `14F8B841`; the
tool has no way to notice a pnach aimed at another build otherwise), and the
ELF's own identity is recorded in the manifest by SHA-256, as `bake_pnach.py`
already does.

---

## 5. Stage 2 — LOCATE: cross-architecture correspondence

The heart of the tool. Input: PS2 ELF + Xbox XBE. Output: a **scored** map from
PS2 addresses to Xbox addresses, plus a residue list of unmatched sites.

**Two maps, not one.** Code correspondence and data correspondence are different
problems with different methods and they must not share a namespace:

| | code map (PS2 VA → Xbox VA) | data map (PS2 VA → Xbox VA) |
|---|---|---|
| method | function matching (below), then instruction-by-role | value identity + reference shape |
| unit | function head, then instruction | object/field, then byte offset |
| fails when | the compiler inlined or split | the value is not unique, or the type changed (float → double) |
| consumer | `branch_force`, `immediate_change`, `nop_out`, `call_retarget`, hook sites, cave outbound calls | `data_word`, cave loads/stores, `gp`-resolved globals |

The first draft said data words port "with the data-symbol map, not the code
map" and then never specified that map. It is specified here, and it has a case
the code map does not: **a data word whose *value* is a code address** (W1)
needs both maps, and a `data_word` whose value happens to look like a PS2 VA is
refused by default rather than copied.

**Method, in confidence order.** Levels 1 and 3 are proven by hand in this
project (the X2 pass); level 2 was used only in its weak form ("sole reader of
this table"); **level 4 has never been done here at all**, and level 4 is what
every site patch in our set actually needs. That asymmetry is the single
largest risk in this spec and the reason §2 downgraded three rows.

1. **Data anchors (strongest).** Registration fourccs (`'ptrk'` = `0x7074726B`),
   distinctive float constants, allocation sizes (`0x614` = 1556), structure
   strides (`0xAFBC`). Find the constant in both binaries, then find the code
   that references it. On x86 this means scanning `.text` for the 4-byte
   absolute VA; on MIPS, `lui`/`addiu` pairs and `gp`-relative loads.
   *Correction, measured this session:* `165.75` was cited here (and in
   `docs/xbox-madden-2004-plan.md` X1) as occurring "exactly once in both
   builds". It occurs **six times** in `SLUS_207.52` as the literal
   `0x4325C000`, at VAs `0x005FE3C4`, `0x005FE3C8`, `0x005FE574`, `0x005FE578`,
   `0x005FEE04`, `0x005FEE28` — a constant pool with duplicates. It is unique on
   Xbox (one word, six referencing sites). So the anchor is **N:1, not 1:1**,
   and the scorer must treat multiplicity on *either* side as a downgrade, not
   silently take the first hit. Uniqueness is a property to be measured per
   anchor per binary, never assumed.
   *Hard-won lesson, encoded as a tool invariant:* the sweep must convert **file
   offsets to virtual addresses** before searching for xrefs. Searching a file
   offset yields zero hits and reads exactly like "this build lacks the feature."
   This error occurred in this project on 2026-08-14 and cost a full re-run.
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
   this constant) rather than by byte position. This is the weakest link, it is
   the one every site patch depends on, and **it has zero track record in this
   project.** Treat every level-4 result as `possible` until a human approves it,
   and expect the human step to be the real cost of a site port.

Every mapping carries evidence and a score: `certain` (unique anchor on both
sides), `probable` (multiple corroborating features), `possible` (one weak
feature, or an anchor that is unique on only one side), `unmatched`. **Only
`certain` and human-approved `probable` are patched.**

### The anchor desert — say it before Phase C is planned around it

The eight twins in `docs/xbox-hook-map.md` are *all* coach/`ptrk`-side
(`0x0024xxxx`, `0x0014xxxx`, the seam, the selectors). **Not one of them is in
the blocking module, and all five of our site patches are** (`0x001F153C`
… `0x001F6A74`). The nearest thing to a foothold is `IsRun`, PS2 `0x001F82E8` →
Xbox `0x000A8F70`, which is in the band but is not a caller or callee of any
patched site.

So "seed the map from the eight verified twins" gives LOCATE **no head start on
the patches we actually want to ship**. Phase B's real deliverable is not the
seeding, it is *finding the first anchor inside the blocking module* —
candidates: the `165.75` shed-power gate (already located on Xbox at
`0x0041B830`, 6 referencing sites, and now known to be N:1 on PS2), the engine's
own kind constants, the player-struct offsets `+0x437`/`+0x436`/`+0x3E0` that
the caves index, and the `−0.13`/`335.4` divergence candidates. This is a real
research step and it belongs in the phase table, not in a parenthesis.

### The map is an artifact with a lifecycle, not a file

`maps/ps2-to-xbox.json`, durable across sessions. It needs more than
address pairs:

- **Identity.** Which binaries this map is *about*: PS2 ELF SHA-256 + the
  `14F8B841` CRC, Xbox XBE SHA-256 + cert title id `0x45410036` + section-table
  digest. A map applied to a different dump is a wrong answer that boots.
- **Schema version**, and a refusal (not a coercion) on mismatch.
- **Per-entry provenance:** method (1–4), evidence string, who/what produced it,
  date, `status ∈ {proposed, approved, rejected}`, `approved_by`, and — separate
  from all of that — `runtime_verified` with the session that proved it. Static
  approval and runtime proof are different claims; `docs/code-caves.md` learned
  that the hard way with four "safe" regions that were live.
- **Append-only conflict handling.** A re-run that disagrees with an `approved`
  entry **never overwrites it**; it records a `conflict` and exits non-zero. The
  four-corrections history in `docs/` is what this rule is for.

**The review workflow, because a scored map nobody can read is not reviewable.**
`locate.py --review` walks the `proposed` entries oldest-first and prints, per
entry: the PS2 disassembly window, the Xbox disassembly window, the evidence
that produced the score, the anchor multiplicity on both sides, and the specific
question the reviewer is being asked (*"is this the branch that tests the state
filter?"*). The reviewer writes `approved`/`rejected` plus a one-line reason
into the map. EMIT reads `status`, not `score`: an unreviewed `probable` is not
patchable, and a `rejected` entry is a permanent negative that later runs must
not re-propose without new evidence.

---

## 6. Stage 3 — EMIT: write the patch into the XBE

**Site edits.** Disassemble the x86 at the mapped site — always forward from a
harvested function head, never a linear sweep (`docs/xbox-hook-map.md` §2: the
linear sweep desynchronises on the first data island and stays wrong) — apply
the classified intent, re-encode:

- `branch_force always` → **the encoding is never the problem, the arm is.**
  Measured over a 136,781-instruction sample of this `.text`: conditional jumps
  are 11,262 short (`70–7F`, 2 bytes) against 2,480 near (`0F 80–8F`, 6 bytes).
  Short `jcc` → `EB rel8` fits exactly; near `jcc` → `E9 rel32` is 5 bytes with
  one trailing `0x90`, which is unreachable. Forcing a branch *never* taken is
  `0x90` × length. So the span always fits, both ways.
  What does not come free is **which edit expresses the intent**. If MSVC
  emitted the inverted condition with the blocks swapped — routine, and nothing
  in the byte pattern reveals it — then `jz`→`jmp` forces *reject-always* where
  PS2's `beq zero,zero` forced *admit-always*, and the result boots and plays
  and is wrong. The emitter therefore refuses a `branch_force` unless the map
  entry carries an explicit `arm_correspondence` field naming which x86
  successor is the twin of the PS2 taken-path. No field, no patch.
- `immediate_change` → locate the immediate operand, verify the width and
  signedness hold the new value, verify the *stock* immediate equals the PS2
  stock immediate (P4: `0x3D` = 61 → `0x169` = 361; P11: `0x10` = 16 → `0`) and
  refuse if it does not. A stock mismatch is the cheapest available proof that
  the map entry is wrong, and it costs nothing to check.
- `nop_out` → `0x90` × instruction length.
- `call_retarget` → rewrite the `rel32` of the existing `E8`. Assert the stock
  target equals the mapped twin of the PS2 stock target first.
- `data_word` → little-endian store at the mapped data address, **after** the
  representation check (float vs double, §2) and the pointer check (a value that
  falls inside the PS2 image's VA range is refused unless the map resolves it).

**Two refusals the first draft did not have, both structural to the XBE:**

- **The Xbox `.bss`.** `.data` has virtual size `0x135EBC` against raw size
  `0x912D0` — **674,796 bytes of zero-fill at VA `0x004ACEF0`–`0x00551ADC` with
  no bytes in the file** (plus 13,508 in `D3D`, 624 in `DSOUND`, 20 in `DOLBY`,
  16 in `.rdata`). This is exactly `bake_pnach.py`'s `.bss` case and gets exactly
  the same treatment: **refuse by name, print the span, write nothing.**
  `Xbe.va_to_off` already returns `None` there, so the check is free.
- **A patch that lands in a file gap.** 29.4 KB of the file lies between
  sections and is mapped nowhere (§7b); `Xbe.off_to_va` returns `None` for all
  of it. An emitter that works in file offsets rather than VAs can write there
  happily and produce an XBE where the bytes exist and are never loaded.

**Caves — the hook is the hard part, not the placement.** The x86 body must
exist (§7). Placement is genuinely easy: append a section (recipe in §7b), and
`call`/`jmp rel32` reaches it from anywhere in a 32-bit image. What the first
draft called automatic is not:

1. **Carving the site.** MIPS replaces exactly one 4-byte word. x86 needs 5
   contiguous bytes for `E8`/`E9 rel32`, and **72.3% of the instructions in this
   `.text` are shorter than 5 bytes** (length histogram over the same sample:
   1B 22,532 · 2B 37,712 · 3B 23,468 · 4B 15,139 · 5B 16,483 · 6B 16,751 · ≥7B
   4,696). So the usual case is displacing two or three whole instructions, not
   one. The emitter must (a) disassemble forward from the site until it has ≥5
   bytes of *whole* instructions, (b) prove **nothing branches into the middle
   of that span** — the x86 analogue of the control-flow proof
   `docs/code-caves.md` did for the lead-blocker site, and mandatory for the same
   reason, (c) copy the displaced instructions to the head of the cave,
   re-encoding any `rel8`/`rel32` among them for their new address, and (d)
   pad the tail of the carved span with `0x90`. A `jcc` or a `call` inside the
   displaced span is a refusal, not a re-encode, in v1.
2. **Binding the cave to live values.** Our PS2 caves read specific live
   registers at the hook site: N-1 needs `s0` (blocker) and `s4` (defender)
   valid across the `jal`, and takes `a1` from a delay slot (`daddu a1,s4,zero`
   at `0x001F1540`). **There is no rule that turns `s0` into an x86 location.**
   The Xbox twin may hold the blocker in `esi`, in `ecx` (a `__thiscall` this),
   or in a stack slot at `[ebp-0x1C]`. Deriving it means reading the x86
   function. So the map needs a per-hook `live_values` table —
   `{"blocker": "esi", "defender": "[ebp-0x24]"}` — supplied by a human and
   reviewed like any other entry, and the cave body is written against *that*,
   not against the PS2 register names.
3. **Preserving what the site owned.** A MIPS `jal` clobbers `ra` and the cave
   saves it explicitly (`addiu sp,sp,-16` / `sd ra,0(sp)`). The x86 twin must
   preserve every register the surrounding function still holds live *and*
   `EFLAGS` if the hook sits between a `cmp` and its `jcc` — `pushfd`/`popfd`
   around the body, or an explicit statement in the IR that flags are dead
   there. It must also leave `esp` exactly as it found it: `__stdcall` callees
   clean their own arguments and `__cdecl` ones do not, so every
   `call host:*` inside a cave needs its convention recorded (§4) and the
   emitter asserts the net stack delta is zero.
4. **Retargeting outbound calls.** `call host:*` is relocated through the code
   map at emit time — and refused if the target is not `approved`.

**Verification (mandatory, mirrors `bake_pnach.py`'s `--verify`):** re-read the
written XBE, disassemble each patched site, assert it matches the intent, assert
no byte outside a declared patch span changed, and emit a manifest with
before/after for every edit. Three additions the PS2 tool did not need:

- **Header self-consistency after a section append:** re-parse the output with
  `recon/xbe.py` from scratch and assert the section table, `SizeOfImage`,
  `SizeOfHeaders` and every digest agree with the bytes actually present.
- **Digests recomputed, not hoped for.** They exist and are populated in this
  image; the rule and the one anomaly are in §7b. Recompute for every section
  touched and compute a correct one for any appended section, so the question of
  whether the loader enforces them never has to be answered.
- **Rollback.** The manifest is the revert record, as on PS2; additionally the
  emitter never writes over its input (`bake_pnach.py`'s `-o` rule) and an
  aborted run leaves no partial file — write to a temp path, `fsync`, rename.

---

## 7. The cave problem — the honest research boundary

97 of our 102 words are cave body. Three strategies, in increasing ambition:

**(a) Hand-written x86 twin (recommended now).** Someone writes the x86 version
of each cave once; the pipeline manages placement, relocation, verification and
packaging. For our set that is **two caves**: P1's **21 words** at `0x00514920`
and N-1+T3's **76 words** at `0x004F4AA0` (the first draft said 66, which is the
N-1 body alone before the 10-word T3 tail and its `k` constant — 21 + 76 = 97,
which is the number §0 quotes). Bounded and honest, an afternoon or two per
cave, and the logic is simple (read player fields, float-add into three comps,
store).

Two things that are *not* in the 97 words and must be ported with them:

- **The host callees.** N-1 calls `0x001F0C40` (the original callee it displaced)
  and `0x0013B798` (the helper resolver). Neither twin is in
  `docs/xbox-hook-map.md`. Porting cave N-1 therefore **requires two new LOCATE
  results in the blocking module** — the anchor desert (§5) again, in the
  critical path of Phase E.
- **The scratch addresses.** The canaries at `0x00514978`/`0x0051497C` need Xbox
  homes. On PS2 they were free because they sit in linker padding; on Xbox they
  should simply live in the appended cave section, which is writable by
  construction. That is a small change to the cave, not a search.

**(b) Bounded MIPS→x86 translator for the "cave dialect."** Our caves use a small
subset: load/store at a struct offset, integer compare-and-branch, float add/mul,
`jal` into the host. That subset *is* mechanically translatable — the obstacles
are real but bounded: 32 MIPS registers → 8 x86 (needs a register allocator or a
memory-backed virtual register file), MIPS COP1 flat registers → x87 stack or
SSE, and delay-slot semantics.

**But it does not remove the hard part, and the first draft implied it would.**
A translator produces a *body*; the body is only correct if it is bound to the
right x86 registers and stack slots at the hook (§6, "Binding the cave to live
values"), and that binding is a human reading of the x86 function either way. For two caves, (b) is strictly
more work than (a) for strictly less certainty. Its real value arrives only when
there are many caves — which is exactly the situation (c) is designed to prevent.

**Float results will not be bit-identical, and acceptance must not require it.**
The EE's COP1 is single-precision and non-IEEE (flush-to-zero, no denormals, no
Inf/NaN — it clamps); x87/SSE is IEEE and MSVC computes in `double` in places
(`docs/xbox-hook-map.md` §4 records a `double 1.02667` in the direction
encoder). A ported float cave will differ in the last bits and can differ in
edge cases. Every acceptance test for a ported cave must therefore be
behavioural (the pancake happens, the pull path is kept) or thresholded, never
an exact float compare — and any PS2 cave that depends on flush-to-zero or on
clamping-instead-of-Inf is a **refusal**, not a port.

**(c) Stop writing caves in MIPS.** The strategic answer: author *future* caves in
a small IR (or C compiled to both targets) so the port is free by construction.
This is what the coach-brain should do from day one — it is already required to
be a "platform-agnostic module" (`ai-coach-playcalling-requirements.md` §3).
Then only the legacy blocking caves ever need hand-porting.

*Free by construction* covers the **body** only. The cave/host boundary — which
host functions it calls, with what convention, and where the live values are —
is per-platform by definition and still costs one human reading per hook. The
right shape is a cave written against a **declared interface** (`blocker`,
`defender`, `AddComp(...)`) with a thin per-platform shim that binds that
interface to registers, so the shim is the only hand-written part and it is 10
lines instead of 76 words.

**Recommendation: (a) now, (c) for everything new, (b) only if hand-porting
becomes the bottleneck** — and note that with (c) in place, (b) is unlikely to
ever pay for itself.

---

---

## 7b. `xbe_space.py` — the free-space surveyor (a standalone utility)

**Operator requirement (2026-08-14): a separate tool whose ONLY job is to report
where and how much free space exists in the XBE for our new code.** It answers
one question and answers it with evidence — it never patches anything.

This is the Xbox counterpart of `docs/code-caves.md` (the PS2 survey that found
~9.2 KB of dead code), but the Xbox answer is *structurally different and much
better*, because the XBE is a PE-derived format where **adding a section is a
legitimate operation** — we are not limited to scavenging dead bytes.

**Most of the survey has now been run by hand** (this review, against
`extract/xbox/default.xbe` with `recon/xbe.py`), which changes the tool's job
from "go find out" to "reproduce these numbers on any XBE and keep them
honest". The measured state of *this* image:

| class | measured for `default.xbe` | verdict |
|---|---|---|
| **1. Section-append headroom** | headers occupy file `0x000–0x9A8`; the first section's raw data starts at `0x1000`. **1,624 zero bytes of headroom**, and it is headroom in *both* spaces — the header block maps at `base 0x00010000` and `.text` starts at VA `0x00011000`, so the whole gap is inside the already-mapped first page. A section header is `0x38`; there is room for ~28 of them plus names before anything moves. | **the answer.** No section data has to move to add a section. |
| **2. In-file slack** | 29.4 KB total (headers→`.text` 1,624; `.text`→D3D 180; …; DSOUND→WMADEC 4,092; the 2,048-byte zeroed tail after `$$XTIMAGE`). | **NOT a cave, and the first draft was wrong to call it "ideal for small caves".** Every one of those bytes lies outside all 11 sections' `[raw_off, raw_off+raw_size)`, so it has **no virtual address** — `Xbe.off_to_va` returns `None` for all of it and nothing can jump to it. It is usable only as the *raw home* of a section you also declare. Report it as such. |
| **3. Zero-reference dead code** | not yet run. The x86 census is the PS2 five-axis method ported: split at `ret`/`int3` boundaries, then require no `call`/`jmp`/`jcc` into the region and no absolute VA anywhere in the image pointing inside it. **x86 makes the xref scan easier** (absolute VAs are literal 4-byte words — `Xbe.find_le32` already does it) and the function-head harvest is proven: 12,337 heads from `E8 rel32` targets, corroborated by `0xCC` padding. | the drop-in-place option, needed only if a section append is ever refused. |
| **4. Virtual-address room** | inter-section VA gaps total **92 bytes** across the entire image (largest 20, `.text`→D3D). The real room is **above the image**: `base + SizeOfImage = 0x0055B460`, and everything above it is free. | gaps are useless; "above the last section" is unbounded in practice (the console has 64 MB and this image ends at 5.4 MB). |

**A fifth class the first draft omitted, and it is a refusal class, not a free
one: virtual zero-fill.** Five sections declare more virtual size than raw size:
`.data` **674,796 bytes** (VA `0x004ACEF0`–`0x00551ADC`), `D3D` 13,508,
`DSOUND` 624, `DOLBY` 20, `.rdata` 16 — **689 KB with no bytes in the file.**
This is the exact analogue of PS2 `.bss` and gets the exact same answer: it
cannot be baked, and the surveyor must report it as *occupied and unbakeable*
rather than letting it read as free space. (`Xbe.va_to_off` already returns
`None` there; see §6.)

**The append recipe, spelled out**, because "update the section table" hides
five fields:

1. **The section table cannot grow in place.** It runs `0x370`–`0x5D8`
   (11 × `0x38`), and `0x5D8` is already occupied by the head/tail shared-page
   reference-count array (12 `u16`s, `0x5D8`–`0x5F0`, all zero; section *i*'s
   tail slot **is** section *i+1*'s head slot, which is how the format expresses
   two sections sharing a page), immediately followed by the name strings at
   `0x5F0`. So the emitter **relocates the whole table into the headroom** at
   `0x9A8` (12 × `0x38` = `0x2A0`, comfortably inside 1,624 bytes), repoints
   `SectionHeadersAddress` (`0x120`), and bumps `NumberOfSections` (`0x11C`).
   The new section's `head`/`tail` refcount pointers get two fresh `u16` slots
   in the headroom — legal, because those fields are absolute VAs into the
   header block, not indices into an implicit array — and the new section shares
   a page with nothing, being page-aligned above the image.
2. Name string in the headroom; `SectionNameAddress` points at it.
3. `SizeOfHeaders` (`0x108`) grows to cover whatever was added — it must stay
   ≤ `0x1000` or `.text`'s raw data moves.
4. `SizeOfImage` (`0x10C`) grows to cover the new section's VA range.
5. Raw placement: the file is `0x4AA000` bytes and every existing section's
   `raw_off` is `0x1000`-aligned, so the new section goes at `0x4AA000` (file
   end, already aligned) with VA at the next page above the image, `0x0055C000`.
   The 2,048-byte tail slack is *not* page-aligned and should be left alone.

**Hard constraints it must check and report** (each can invalidate an otherwise
"free" region):

- **Section digests — present, populated, and the rule is now known.** Each
  section header carries a 20-byte SHA-1 at `+0x24`. Measured this session:
  **10 of the 11 reproduce exactly as `SHA-1( le32(raw_size) ‖ raw_bytes )`.**
  The exception is `.text`, which does not reproduce under that rule, nor under
  plain SHA-1, a big-endian length prefix, a virtual-size prefix, or
  header-inclusive spans. Two readings, and the surveyor must not pick one for
  us: either this retail image ships a stale `.text` digest — in which case a
  console that boots this disc plainly does not enforce it — or `.text` uses a
  rule we have not found. **The engineering answer makes the question moot:
  recompute the digest under the verified rule for every section EMIT touches,
  and compute a correct one for any appended section.** The one thing not to do
  is leave a modified section carrying its old digest on the theory that nothing
  checks. The cert (`0x10184`, `0x1EC` bytes) is separately hashed by the retail
  signature at header `+0x004`; a softmodded kernel skips that check for HDD
  launches, which is the delivery route (`docs/xbox-madden-2004-plan.md`), but
  the surveyor should still report the signature's presence rather than assume
  the friend's softmod behaves like every other softmod.
- **Section flags** — report them, but note the measured reality: **10 of 11
  sections are already marked executable** (only `$$XTIMAGE`, flags `0x38`, is
  not; `.rdata` `0x06` and `.data` `0x07` both are). On this title "not
  executable" will almost never be the thing that disqualifies a region.
- **`$$XTIMAGE` / trailing sections.** It is the last section by both VA
  (`0x00558C60`) and raw offset (`0x4A7000`), is flagged `INSERTED_FILE` and is
  not executable. Appending *after* it in the file and *above* it in VA touches
  nothing it owns.
- **TLS.** The directory is at `0x003E4804` (in `.rdata`): `AddressOfCallBacks`
  is **0**, so there are no TLS callbacks to worry about; the index lives at
  `0x004ACF60`, inside `.data`'s zero-fill, which is consistent. Report it,
  because a title *with* callbacks would need them preserved.
- **The kernel thunk table** at `0x003E4240` — the first bytes of `.rdata`. Not
  free, and a census that treats `.rdata` as ordinary data will say it is.
- **Alignment rules** (raw and virtual) the appended section must satisfy.
- **Anything relocated or overwritten at runtime** — the PS2 survey's standing
  caveat (a computed store can invalidate a static census); the surveyor states
  what it *cannot* prove statically.

**Output:** a ranked inventory — address, size, class, executable?, evidence, and
a risk note — in both human-readable and JSON form, so the EMIT stage can consume
it directly as an allocator input. Same posture as `recon/cave_census.py`: this
project has documented four "safe" regions that were live, so the surveyor
reports *evidence*, and its regions stay unproven until a runtime check passes.
The class-3 regions in particular inherit `docs/code-caves.md`'s rule verbatim:
**a dead region is unproven until an execute-breakpoint test passes on it, per
region, not once for the survey.**

**Standalone by design.** It runs against any XBE, needs no pnach and no
correspondence map, and is useful on day one — knowing the space budget shapes
how the caves get written (§7). Ship it in `tools/xbe_space.py` with tests
against `default.xbe` that pin the numbers above, the same way
`tests/test_xbe.py` pins the corrected recency-table VA.

---

## 8. Stage 4 — PACKAGE: XBE → XISO

Mechanical. `recon/xdvdfs.py` already *reads* XDVDFS; this adds the writer:
- **Same-size XBE** → in-place overwrite, image otherwise byte-identical (the
  `patch_iso_elf.py` pattern, already built and tested for PS2 — though note the
  ⚠ in §3: that tool has still never run against a real disc image).
- **Grown XBE** (any cave append) → either rewrite the image, or append the file
  at the end and repoint its directory entry's sector+size. The XDVDFS directory
  is a btree of fixed-shape records, so an in-place size/LBA edit is tractable.
  `default.xbe` is currently `0x4AA000` bytes = exactly **2,388 sectors** of
  2,048; a grown XBE must be padded to a whole sector and the directory record's
  size field set to the true byte count, not the padded one.
- **The source image is never modified.** `-o` required, input refused as
  output, as on PS2. The operator's 3.11 GB dump is the only copy.
- Output verification: re-extract the XBE from the produced ISO and byte-compare
  against the emitter's output.

### 8.1 XISO layout — measured against the operator's image (2026-08-14)

A strict pass over the real 3.11 GB dump. **Every number below was read from the
image**, and one of them overturns the draft's assumption.

| fact | value |
|---|---|
| image size | 3,114,663,936 B = **1,520,832 sectors exactly** (no partial tail sector) |
| files / dirs | 66 files, 1 directory (`/DATA/`) |
| `default.xbe` | start sector **265**, size **4,890,624 B = exactly 2,388 sectors** (already whole-sector) |
| its directory record | root table sector **264**, record at table offset `0x14` → **file offset `0x84014`** |
| record fields | `start_sector` u32 at **+4**, `size` u32 at **+8** — a repoint is an **8-byte write** |
| packing density | sum of sector-rounded extents = **100.0%** of the image |
| tail slack after the last file | 62,848 B, and **not zero-filled** |

**⚠ CORRECTION to the draft — in-place XBE growth headroom is ZERO, not 4 KB.**
The two sectors immediately after `default.xbe` (2653–2654) look like a gap
between files, and a naive "next file starts at 2655" calculation reports 4,096
free bytes. They are **the `/DATA/` directory table** (root entry: `DATA`,
start_sector 2653, size 4,096 — and the bytes there decode as directory records,
e.g. `UIS_GRP_MADDEN_REC`). Overwriting them would destroy the filesystem for
every other file on the disc. **Any XBE growth requires relocation.**

**Consequence — the packaging design is forced, and it is cheap:**
1. **Same-size XBE** (site patches only, no new section) → in-place overwrite at
   sector 265. The image is otherwise byte-identical. This is the C1-tracer-bullet
   path and needs no directory surgery at all.
2. **Grown XBE** (any cave, i.e. any new section) → **append the padded XBE at the
   end of the image and repoint its record**: write the new `start_sector` and the
   true byte `size` (not the padded length) as two u32s at file offset `0x84014`.
   The old extent is left in place as garbage — harmless, and it keeps the edit to
   eight bytes. The image grows by the padded XBE size.
   *Do not attempt to reclaim the vacated 2,388 sectors*: the image is 100% packed
   by construction, so there is nothing to gain and a rebuild to lose.
3. **Never rebuild the whole image** unless a future need forces it — the append +
   repoint path avoids relayout entirely.

**Why this is low-risk despite being a growth path:** the only structural edit is
eight bytes in one directory record whose offset is known and verified. There is
no ISO-level checksum, no volume-size field that must agree (the volume descriptor
records the root table's sector/size, not the image length), and no other record
references `default.xbe`.

**Sector arithmetic to get right (the failure modes):** the appended XBE must
start on a sector boundary and be zero-padded to a whole sector, while the
directory record's `size` field must carry the **true byte count** — the reader
uses `size` for extraction, so a padded value corrupts every downstream byte-diff.
The current XBE is a whole number of sectors, which makes the same-size case
trivially safe, and this is exactly the invariant the output verification checks.

**Delivery — and why this phase is not on the critical path.** The friend runs a
softmodded console, and the delivery mechanism decided in
`docs/xbox-madden-2004-plan.md` is **FTP the patched `default.xbe` into the
game's folder on the Xbox HDD**. That needs no ISO at all. The XISO writer is
for xemu convenience and for a self-contained artifact; it should be built when
it is wanted, not before Phase E. A hardware smoke test (boot + one game) from
the friend remains the final acceptance arm either way.

---

## 9. Phasing (useful output early)

(Phase letters below; "the C1 patch" always means the eligibility branch force at
`0x001F2D60`, never a phase.)

| phase | deliverable | unlocks |
|---|---|---|
| **A** | `lift_pnach.py` — pnach + ELF → PatchIR, with the classifier and a report of what it can/can't port | tells us *today* exactly which lines of any pnach are portable |
| **A2** | **`xbe_space.py` — the free-space surveyor (§7b)**. Standalone, no dependencies on the rest | the space budget, which decides how caves get written; useful immediately |
| **B** | the map format (identity, versioning, `status`, conflict rule) + seeding from the verified twins; `locate.py` for data-anchor matching with scores; `--review` | the reviewable correspondence artifact |
| **B2** | **the first anchor inside the blocking module** — the `165.75` gate, the kind constants, the player-struct offsets the caves index | removes the §5 anchor desert, which otherwise blocks C2 *and* E |
| **C1** | `emit_xbe.py` + XBE writer + `--verify` + XISO/FTP delivery, exercised on a site whose twin is **already in the map**: the 7-byte cheat-getter neuter at `0x0012CA40`/`0x0012CA70` (`docs/xbox-hook-map.md` §4 — same-size, no relocation, no cave) | **proves EMIT→write→boot with LOCATE held constant.** The pipeline's plumbing, tested alone (rule 2). |
| **C2** | the C1 eligibility patch ported end-to-end: LOCATE the `0x001F2D60` twin, name the arms, emit, boot | **proves LOCATE→EMIT→boot** on a patch whose behaviour we know by eye. The milestone that matters. |
| **E** | hand-written x86 caves for P1 and N-1/T3, plus the two host-callee twins (`0x001F0C40`, `0x0013B798`) | **the friend's working double teams** |
| **D** | *(deferrable)* XISO writer + packaging | a sendable disc image — not needed for FTP delivery (§8) |
| **F** | *(optional)* the cave-dialect translator | future caves port automatically |

**What changed and why:** the first draft made "the C1 patch on Xbox" the single
tracer bullet, which bundles two independent risks — an untested XBE writer and
an unproven LOCATE result in a module with no anchors — into one experiment that
can only be tested all-at-once. That is the shape project rule 2 exists to
forbid. C1 and C2 split them: C1 fails only if the writer is wrong, C2 fails only
if the map is wrong, and each has its own acceptance metric. D moved after E
because delivery is FTP, not a disc.

---

## 10. Acceptance tests

Every threshold below is a number the test asserts, not a number a human reads.

**LIFT**

- **T-lift:** every line of the deployed 102-word set classifies. Specifically:
  `0x001F2D60` → `branch_force always`; `0x001F6A74` and `0x001F21E8` →
  `immediate_change` (`0x3D`→`0x169`, `0x10`→`0x0`); `0x001F153C` →
  `call_retarget` with `target_is_cave` (old target `0x001F0C40`, new
  `0x004F4AA0`); `0x001F4A30` → `hook_to_cave` (`0x00514920`). The 97 cave words
  group into **2 contiguous regions with 2 external entry points**
  (`0x004F4AA0`, 76 words; `0x00514920`, 21 words), the N-1 region carrying
  **2 internal jump targets** (`0x004F4BA8`, `0x004F4B68`) that are *not*
  reported as caves and **1 embedded data word** (`k` at `0x004F4BCC`).
- **T-lift-footprint:** the N-1 cave's IR lists `0x00514978` and `0x0051497C`
  (canary stores) and `0x00601280` (the `gp`-resolved global, `gp = 0x006056F0`
  from `.reginfo`) as map requirements. A cave whose footprint is empty when it
  has absolute stores is a failed extraction, not a clean one.
- **T-lift-delayslot:** `0x001F4A30`'s record carries `0x001F4A34` = `beql
  s0,v0,0x001F4AB0` as its delay slot, is flagged `branch_in_delay_slot`, and
  the whole change is marked **not automatically portable**.
- **T-lift-negative:** a deliberately garbled pnach line is reported
  `unclassified`, never guessed. A `byte`/`short`/`extended` width is refused by
  name; an unknown directive is refused, not skipped; a pnach whose `gametitle`
  CRC is not `14F8B841` against this ELF is refused.
- **T-lift-partial:** removing one cave word from the pnach makes LIFT report
  the whole P1 change as incomplete and refuse to port it. Half a hook must be
  impossible to emit.

**LOCATE**

- **T-locate:** the tool independently rediscovers the hand-verified twins in
  `docs/xbox-hook-map.md` from their anchors and scores them `certain`.
- **T-locate-negative-absent:** `−0.13` and `335.4` are present on PS2 and were
  **not found on Xbox as float or double** (`docs/xbox-madden-2004-plan.md` X1).
  The tool must return `unmatched` for both, and must not match a numerically
  nearby constant.
- **T-locate-negative-multiplicity:** `165.75` (`0x4325C000`) has **six**
  occurrences in `SLUS_207.52` and one in `default.xbe`. The tool must score it
  no better than `probable`, must report the multiplicity on both sides, and
  must not silently adopt the first PS2 hit as *the* anchor.
- **T-locate-negative-unmappable:** a PS2 address in `.vutext` or in a DVP
  overlay (VU memory, not EE-addressable — `docs/code-caves.md`) returns
  `unmatched`, never a plausible `.text` neighbour.
- **T-locate-conflict:** a second run that disagrees with an `approved` entry
  records a conflict and exits non-zero without touching the entry.

**EMIT** — the negatives here are the ones that catch a *silently wrong* port,
which is the failure mode this whole spec exists to prevent.

- **T-emit:** a `branch_force` written into the XBE disassembles as the intended
  unconditional jump; no byte outside the patch span changes; `--verify` passes.
- **T-emit-null:** an empty pnach produces an XBE **byte-identical** to the
  input. Catches emitter drift, header rewrites nobody asked for, and digest
  churn.
- **T-emit-idempotent:** running the emitter twice produces identical bytes.
- **T-emit-arm:** a `branch_force` whose map entry has no `arm_correspondence`
  field is **refused**, with the two candidate successors printed.
- **T-emit-stock-mismatch:** an `immediate_change` whose x86 stock immediate does
  not equal the PS2 stock immediate is refused. Cheapest possible proof that a
  map entry is wrong.
- **T-emit-zerofill:** a patch aimed inside `.data`'s zero-fill (e.g.
  `0x00500000`) is refused by name, with the span `0x004ACEF0–0x00551ADC`
  printed and **no output file created** — the exact shape `bake_pnach.py`
  proved on the real ELF for `.bss`.
- **T-emit-pointer:** a `data_word` whose value falls inside the PS2 image's VA
  range is refused unless the map resolves the value (the W1 case,
  `0x001F3518`).
- **T-emit-hook-span:** a hook whose 5-byte carve would straddle a known branch
  target, or would displace a `jcc`/`call`, is refused rather than relocated.
- **T-emit-integrity:** after a section append, re-parsing the output with
  `recon/xbe.py` from scratch yields a self-consistent header, and every section
  digest recomputes under `SHA-1( le32(raw_size) ‖ raw_bytes )` (with `.text`'s
  anomaly handled explicitly, not ignored — §7b).

**SPACE**

- **T-space:** on `default.xbe` the surveyor reports **1,624 bytes** of header
  headroom (`0x9A8`→`0x1000`), **92 bytes** total of inter-section VA gap,
  **29.4 KB** of in-file slack *explicitly marked unaddressable* (`off_to_va`
  is `None` for all of it), and **689 KB** of virtual zero-fill *explicitly
  marked unbakeable* (674,796 of it in `.data`). Every claimed class-3 dead
  region survives the x86 xref census (no `call`/`jmp`/`jcc` in, no absolute VA
  anywhere pointing inside). The integrity question is answered with the
  measured digest rule and the `.text` anomaly stated, not assumed either way.

**PACKAGE / runtime**

- **T-package:** XBE re-extracted from the produced XISO is byte-identical to the
  emitter's output; the source image is unmodified (SHA-256 unchanged).
- **T-boot (rig):** the phase-C1 XBE boots under xemu and reaches a game. Then,
  separately, the phase-C2 XBE. **Each patch on its own build** (project rule 2)
  before any combination.
- **T-behaviour (operator):** on the C1-eligibility XBE, the pulling guard takes
  the right man — the same result confirmed on PS2 on 2026-08-13.
- **T-behaviour-negative:** the *unpatched* Xbox build is watched on the same
  scenario first, and does **not** show the fixed behaviour. Without this arm,
  "it works on Xbox" is unfalsifiable — the Xbox build could have shipped the
  behaviour we are trying to add.
- **T-differential (later, needs the xemu instrument):** the same scripted
  scenario run on PCSX2+PINE and on xemu+GDB stub, comparing an agreed scalar
  (blocker comps after the frame-23 shed) within a stated tolerance — **not** for
  bit-equality, which the EE's non-IEEE COP1 forbids (§7).

---

## 11. Scope limits — stated plainly

1. **Foreign pnaches port only their classifiable lines.** For a cheat we did not
   write, intent must be *inferred* from the instruction delta. Simple edits
   (infinite time, a stat cap, a branch force) will port. A cheat carrying its own
   MIPS payload will not, without §7(b).
2. **Game-version coupling.** The map is per title-pair (SLUS-20752 ↔ this XBE).
   Other titles need their own anchors, though the *method* transfers.
3. **No behavioural guarantee.** A correctly-ported patch can still behave
   differently if the Xbox build diverges. *Correction to the first draft:* it
   said "two constants already diverge: `−0.13` and `335.4`". They do not
   *diverge* — they were **not found** on Xbox as float or double, which is an
   open X2 item with at least three explanations (different tuning, a computed
   form, a changed implementation) and no verdict
   (`docs/xbox-madden-2004-plan.md` X1). Calling an unresolved absence a
   divergence is the exact species of plausible-looking claim rule 4 exists to
   stop. Every ported patch keeps its PS2 acceptance test and is re-verified on
   Xbox — the operator's eyes remain the instrument of record.
4. **Not a general emulator/recompiler.** This ports *patches*, not the game.
5. **Numeric results are not reproduced, behaviour is.** The EE's COP1 is
   single-precision and non-IEEE; MSVC uses `double` in places. Ported float
   code differs in the last bits by construction (§7).
6. **`word` width only, in both back-ends.** `byte`/`short`/`extended` are
   refused by name. Widening is a change to `bake_pnach.py` and `pnach2xbe`
   together, never one of them — the pnach must not mean two different things.
7. **The map is only as good as its review.** Everything downstream of a
   `probable` entry inherits its uncertainty. The tool's guarantee is that the
   uncertainty is *visible and attributable*, not that it is absent.

---

## 12. Status

Specification only; nothing built. Existing pieces that plug straight in:
`bake_pnach.py` (the classifier's sibling and the PS2 emit path), `recon/xbe.py`
(XBE parse — including `va_to_off`/`off_to_va`/`find_le32`, which the refusal
checks in §6 and the whole of §7b are built on), `recon/xdvdfs.py` (XISO read),
`docs/xbox-hook-map.md` (the seed mappings), capstone 5.0.7, `recon/mipsdis.py`
+ `fpudis.py` (MIPS decode). Phase A can start immediately and is useful on its
own.

**Correction on the toolchain: capstone does not assemble.** The first draft
listed it as "x86 encode/verify"; capstone is a disassembler only (there is no
assembler API on the installed 5.0.7, and keystone is **not** installed — checked
this session; `/usr/bin/as` and `gcc` are). So EMIT needs its own encoder. That
is fine, and it is the right answer anyway: the whole edit vocabulary is
`EB rel8` / `E9 rel32` / `E8 rel32` / `0x90` fill / an immediate field overwritten
in place, all of which are a few dozen lines of hand-encoding whose output is
then **disassembled with capstone and asserted against the intent** — the same
"write it, read it back, prove it" loop `bake_pnach.py --verify` already runs on
PS2. Do not take an assembler dependency for five instruction forms.

The disassembly harness itself is still uncommitted scratch
(`docs/xbox-hook-map.md` §9): promoting it to `recon/x86dis.py`, with the
function-head harvest that this review re-reproduced (12,337 heads), is a
prerequisite for Phase B and should be counted in Phase B's cost.

---

## 13. Review log (2026-08-14)

Adversarial review of the first draft. Every structural claim below was checked
against the real artifacts rather than against the prose (project rule 4):
`extract/xbox/default.xbe` via `recon/xbe.py` + capstone 5.0.7,
`extract/SLUS_207.52` via its own ELF headers, and
`patches/14F8B841.c1-plus-doubleteam.pnach`. Nothing outside this document was
edited.

### Corrections — a claim in the draft was wrong

| # | claim | what is actually true | evidence |
|---|---|---|---|
| C-1 | §1 numbered LOCATE=1, LIFT=2; §§4–8 numbered LIFT=1, LOCATE=2 | reconciled to LIFT/LOCATE/EMIT/PACKAGE = 1/2/3/4 | internal |
| C-2 | the §3 diagram pointed at "SPACE SURVEY (§8)" | the surveyor is §7b; §8 is PACKAGE | internal |
| C-3 | §7: "two caves (P1's 21 words, N-1+T3's **66**)" — and 21+66 ≠ the 97 quoted in §0 | the N-1 region is **76** words (`0x004F4AA0`–`0x004F4BCC`); 66 is the N-1 body before the 10-word T3 tail. 21 + 76 = 97 ✓ | address run over the pnach's 102 lines |
| C-4 | §2: "in-file slack … **ideal for small caves**" | in-file slack has **no virtual address**. All 29.4 KB lies outside every section's `[raw_off, raw_off+raw_size)`; `Xbe.off_to_va(0x361F60)` and `(0x4A9900)` both return `None`. Nothing can execute there | `recon/xbe.py` |
| C-5 | §5: "`165.75` occurs exactly once in *both* builds" | **six** occurrences of `0x4325C000` in `SLUS_207.52` (`0x005FE3C4`, `…C8`, `0x005FE574`, `…78`, `0x005FEE04`, `0x005FEE28`). Unique on Xbox only. The flagship anchor is N:1 | LE word scan of the ELF |
| C-6 | §5: "all four [methods] are proven by hand in this project" | methods 1 and 3 are; method 2 only in a weak form; **method 4 has never been used here** — and method 4 is what every site patch needs | `docs/xbox-hook-map.md` §§2–3 |
| C-7 | §11.3: "two constants already diverge: `−0.13`, `335.4`" | they were **not found** on Xbox — an unresolved X2 item, not a proven divergence | `docs/xbox-madden-2004-plan.md` X1 |
| C-8 | §12: "capstone 5.0.7 (x86 **encode**/verify)" | capstone disassembles only; keystone is not installed. EMIT needs its own encoder | import check this session |
| C-9 | §4 classifier: "`j`/`jal` to an address **outside the game's code band**" | both our caves are *inside* the image (`0x004F4AA0` dead libc in `.text`; `0x00514920` the `.vutext`/`.data` linker pad — `.data` begins `0x00514980`). Cave detection must be intra-pnach | ELF section table |
| C-10 | §4 classifier: cave body requires "stock is dead space" | **73 of the N-1 cave's 76 stock words are non-zero** (dead code, not padding). A zero test refuses 76 of our 97 cave words | word dump of the stock ELF |
| C-11 | §7b: "the section digests — *if present*" | they are present and populated, and **10 of 11 verify as `SHA-1(le32(raw_size) ‖ raw_bytes)`**. `.text` alone does not reproduce under that rule or the simple variants tried | digest recomputation over all 11 sections |

### Downgrades — the claim was true but the automation claim was not

- **`branch force`: AUTOMATIC → ASSISTED.** The encoding always fits (short
  `jcc`→`EB` 2→2; near `jcc`→`E9`+`nop` 6→5+1; measured 11,262 short vs 2,480
  near in a 136,781-instruction sample). What does not transfer is the *sense*:
  if MSVC inverted the condition and swapped the blocks, `jz`→`jmp` forces the
  opposite behaviour and the result boots. Requires a human `arm_correspondence`.
- **`immediate change`: AUTOMATIC → ASSISTED.** Re-encoding is mechanical;
  *finding the instruction* is LOCATE method 4, which has no track record (C-6).
- **`data word`: AUTOMATIC → ASSISTED, and split in two.** Little-endianness is
  not sufficient: the EE's COP1 is single-only while MSVC uses `double`
  (`docs/xbox-hook-map.md` §4 records a `double 1.02667`), so a 4-byte PS2 float
  can correspond to an 8-byte Xbox double. And a **pointer-valued** data word
  (W1: `0x00583868`, stock `0x001F3848` → `0x001F3518`, verified in the ELF)
  carries a PS2 *code address* as its value and must be refused unless the code
  map resolves it. Also noted: the draft's only data-word example, T3's `k`,
  is not a stock data word at all — it lives inside our own cave.
- **"hook is automatic": withdrawn.** Three reasons, all measured: (a) `E8`/`E9
  rel32` needs 5 bytes and **72.3% of instructions in this `.text` are shorter
  than 5 bytes**, so the usual case displaces 2–3 whole instructions and must
  prove nothing branches into the carved span; (b) our caves depend on specific
  live registers (`s0` blocker, `s4` defender across N-1's `jal`, `a1` set in
  the delay slot at `0x001F1540`) and **no rule maps `s0` to an x86 location**;
  (c) each `call host:*` needs its calling convention recorded or the stack
  silently drifts. Hook *placement* is mechanical; hook *binding* is manual.
- **§7(b), the translator: still feasible, but re-priced.** It produces a body
  and does not touch the binding problem, which is the expensive half. For two
  caves it is strictly more work than hand-writing for strictly less certainty.

### Additions — gaps the draft did not cover

- **A `call_retarget` class.** N-1 is not a divert-from-a-non-call-site: stock
  `0x001F153C` is `jal 0x001F0C40`, patched to `jal 0x004F4AA0`, and the cave
  calls the original itself. On x86 that is the *cheapest* class in the set —
  rewrite an existing `E8`'s `rel32` — and the draft had no row for it.
- **Delay slots, properly.** Including the case in our own set: P1's `j` at
  `0x001F4A30` has **`beql s0,v0,0x001F4AB0` in its delay slot** — a branch in a
  jump's delay slot, architecturally undefined on the R5900, which the cave's
  two exits route around. Flagged as not automatically portable, and branch-
  likely annulment semantics called out separately.
- **Cave data footprint.** The N-1 cave stores canaries to `0x00514978`/
  `0x0051497C` and loads `[gp−17520]`; **none of those addresses is a patch
  line**. A port carrying only the written words scribbles on Xbox addresses
  nobody chose.
- **`gp` is resolvable.** `SLUS_207.52`'s `.reginfo` gives
  `ri_gp_value = 0x006056F0`, so `-17520(gp)` is `[0x00601280]`. LIFT rewrites
  `gp`-relative accesses to absolute before EMIT ever sees them.
- **Two maps, not one** (code vs data), with different methods and a case that
  needs both.
- **Map lifecycle:** identity binding to both binaries, schema version,
  per-entry provenance and `status`, append-only conflict handling, and a
  concrete `locate.py --review` workflow — the draft called the map "reviewable"
  without saying by whom, how, or what the reviewer writes back.
- **The anchor desert.** All eight seeded twins are coach/`ptrk`-side; **all five
  site patches are in the blocking module, where we have no anchors.** New phase
  B2 exists for this, because it silently gated the draft's C and E.
- **Two host callees the caves need** — `0x001F0C40` and `0x0013B798` — neither
  of which has an Xbox twin yet. Phase E depends on them.
- **The Xbox `.bss`.** `.data` declares 674,796 bytes of zero-fill with no file
  bytes (689 KB across five sections). Unbakeable, exactly like PS2 `.bss`, and
  entirely absent from the draft. Now a named refusal in §6 and a fifth
  surveyor class in §7b.
- **The append recipe, measured.** 1,624 zero bytes of header headroom
  (`0x9A8`→`0x1000`, mapped, since headers load at `0x00010000` and `.text`
  starts at `0x00011000`); the section table cannot grow in place because the
  head/tail shared-page refcount array sits immediately after it at `0x5D8`;
  relocate the table into the headroom; new section at file `0x4AA000`, VA
  `0x0055C000`. TLS has **no callbacks** (`AddressOfCallBacks = 0`). 10 of 11
  sections are already executable.
- **Front-end parity with `bake_pnach.py`** made explicit (widths, unknown
  directives, `patch=0` parking) so one pnach cannot mean two things.
- **Nine new acceptance tests**, all negative or invariant:
  `T-lift-footprint`, `T-lift-delayslot`, `T-lift-partial`, `T-emit-null`,
  `T-emit-idempotent`, `T-emit-arm`, `T-emit-stock-mismatch`,
  `T-emit-zerofill`, `T-emit-pointer`, `T-emit-hook-span`, `T-emit-integrity`,
  `T-locate-negative-{absent,multiplicity,unmappable}`, `T-locate-conflict`,
  `T-behaviour-negative`, `T-differential`. The draft's `T-locate` negative
  ("must not claim a twin for a PS2 function known to be absent on Xbox") was
  **unfixturable** — no such function is documented — and has been replaced with
  three negatives that have real fixtures.
- **Phasing split.** The draft's single tracer bullet bundled an untested XBE
  writer with an unproven LOCATE result in an anchor-free module — a
  test-it-all-at-once experiment, which project rule 2 forbids. Now C1 (writer,
  on an already-mapped 7-byte site) and C2 (the C1 eligibility patch, which
  tests LOCATE). D moved after E because delivery is FTP, not a disc.

### Not changed, and why

- **The verdict in §0 stands.** A byte-level transcoder is impossible; a
  semi-automatic porting tool with a confidence model is buildable. Nothing
  found here contradicts that, and the XBE side turned out *easier* than the
  draft assumed (section append is not merely legitimate, it is measurably free).
- **The `.text` digest anomaly is left open, deliberately.** Either this retail
  image ships a stale `.text` digest — in which case a console that boots this
  disc does not enforce it — or `.text` uses a rule not yet found. Both readings
  are recorded; neither is adopted; and §6 makes the question moot by
  recomputing under the verified rule for everything it touches. Resolving it is
  a task for `xbe_space.py`, not for prose.
- **Nothing about the eight X2 twins was re-derived.** They were taken as given
  from `docs/xbox-hook-map.md`; this review checked the *spec's use* of them,
  not the twins themselves. One corroboration fell out for free: the 12,337
  function heads that document reports were independently reproduced here.

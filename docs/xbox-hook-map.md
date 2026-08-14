# Xbox hook map — the PS2→x86 correspondence table

**Phase X2, first pass. 2026-08-14.** Static reverse engineering only: no rig,
no emulator, nothing executed. Target is `extract/xbox/default.xbe`
(4,890,624 B, retail, title id `0x45410036`), the operator's own dump.

X1 answered "is it the same build" (yes). X2 answers "where did each known hook
land". This document is the answer for the priority set: the `ptrk` ctor, the
two tendency getters, the recorder, `IsRun`, and the play-select seam. Every row
below was read out of the binary in this session; nothing is carried over from
the PS2 map except the *question*.

---

## 0. Toolchain — what is installed, and the verdict

Checked first, before any analysis, because the answer changes the method:

| tool | status |
|---|---|
| **capstone 5.0.7** (python3.9, `~/.local/lib`) | **present** |
| `objdump`, `readelf`, `nm`, `gdb`, `strings` | present (ELF-only; they do not read XBE) |
| Ghidra | **absent** — not on PATH, no install found |
| radare2 / rizin / pefile | absent |

**Verdict: capstone, and it should become the repo's first pip dependency.**

The plan doc left this open ("Ghidra-headless vs capstone — decide at X1"). The
decision is now easy, and it is not really about preference:

1. **Everything in this document was produced with capstone in an afternoon.**
   Six priority hooks, the full `ptrk` object layout, the record schema, the VM
   dispatch table. Ghidra would have to be installed, a third-party XBE loader
   found and trusted, a project built, and headless scripting stood up before
   the first question got an answer.
2. **The work is sweep-shaped, not browse-shaped.** Every question here was of
   the form "which of 12,337 functions touches this address" or "which
   instruction in the image writes `+0x610`". That is a loop over a disassembly
   index, which is what capstone gives you and what a GUI does not.
3. **The stdlib-purity argument does not actually apply.** The purity rule
   exists so `recon/` drops onto the rig with no install step — that is a
   *runtime* concern for the DNS/sink/PINE tools that run during a capture
   session. Static RE runs on the dev box. `recon/xbe.py` (this session's other
   deliverable) is stdlib and stays that way; the disassembly layer is a
   dev-box-only dependency and should be declared as such, not smuggled in.

**Recommendation:** add `capstone>=5.0` as an optional/dev dependency, keep
`recon/xbe.py` stdlib so the parser itself never gains one, and gate any
capstone import behind a clear error. Ghidra stays a reasonable *second* tool
for interactive exploration of unfamiliar code — but it is not on the critical
path and X2 no longer waits for it.

**What is blocked without a disassembler:** essentially nothing that was asked
for. What is blocked without a *decompiler* (Ghidra/IDA) is the next tier —
reading the 0x4F0-byte scoring function `0x0012BB20` as an algorithm rather than
as instructions, and identifying which of the 30 `IsRun` callers is which
defensive behaviour. Those are X3/X4 questions.

---

## 1. Correction: the X1 anchor addresses were FILE OFFSETS, not VAs

**Correction.** The X1 sweep recorded the `ptrk` weight tables at "vaddr
`0x44C2F4`", the DB query strings "near vaddr `0x44C340`", and `165.75` at
"vaddr ~`0x40C5F0`". Those are all **file offsets**. The virtual addresses are
`0x0045AF14`, `0x0045AF58` and `0x0041B830`.

This is not pedantry — it is the difference between this document existing and
not. x86 cross-referencing works by searching `.text` for the 4-byte
little-endian **absolute VA** of the data you care about. Searching for
`F4 C2 44 00` finds nothing; searching for `14 AF 45 00` finds the one
instruction that reads the recency table, and that instruction is inside the
RepetitionFactor twin. A miss would have read as "the Xbox build does not have
this", which is exactly the failure mode `docs/fact-check-2026-08.md` is about.

`tests/test_xbe.py::test_recency_table_va` pins the corrected number.

**The conversion rules (verified, from the section table):**

| section | vaddr | raw file off | raw size | rule |
|---|---|---|---|---|
| `.text` | `0x00011000` | `0x00001000` | `0x00360F4C` | `off = va - 0x10000` |
| `.rdata` | `0x003E4240` | `0x003D5000` | `0x000379D0` | `off = va - 0xF240` |
| `.data` | `0x0041BC20` | `0x0040D000` | `0x000912D0` | `off = va - 0xEC20` |

`.data`'s virtual size (`0x135EBC`) exceeds its raw size — the tail is zero
fill with no bytes in the file. `Xbe.va_to_off` returns `None` there rather than
a plausible-looking offset.

---

## 2. Method, and what a match is worth

**Anchor on data, cross-reference into code.** For a known data VA, scan `.text`
for its 4-byte LE encoding (`Xbe.find_le32`, unaligned). Each hit is a byte
pattern *inside* an instruction — for `mov`/`fadd`/`push` it is the
displacement or immediate, so the instruction starts 1–3 bytes earlier.

**x86 is variable-length, so a raw 4-byte match is not proof of an instruction
boundary.** Two independent guards were used throughout:

- **Disassemble to confirm.** Every address in the table below was disassembled
  and the referencing instruction read. A match that does not decode into a
  sensible instruction at a sensible offset was not counted.
- **Function starts from call targets + `0xCC` padding.** 12,337 function
  entries were harvested by scanning every `E8 rel32` whose target lands in
  `.text`. MSVC pads between functions with `int3` (`0xCC`), so a claimed
  function head that is preceded by `0xCC` bytes is corroborated by the linker's
  own layout, independently of the call scan. **Every hook in the table below
  is `0xCC`-padded** (pad lengths 3–15 bytes, listed per row).

Function-relative disassembly (restarting at each harvested head) is used rather
than a linear sweep — a linear sweep from `.text`'s start desynchronises on the
first data island and stays wrong. That is why the "instructions touching
`+0x610`" search returned 0 hits on the linear pass and 21 correct hits on the
per-function pass.

**Caveat on negatives.** "N callers" below means N `E8 rel32` sites. A function
reached only through a pointer table or a computed jump shows zero. Where that
matters it is called out (the VM handler `0x00133580` is exactly this case).

---

## 3. The correspondence table

Confidence: **A** = disassembled, semantics read, corroborated by two or more
independent anchors. **B** = disassembled and consistent, one anchor. **C** =
located by anchor, semantics inferred.

| what | PS2 | **Xbox VA** | file off | evidence | conf |
|---|---|---|---|---|---|
| `ptrk` ctor / registration | `0x0024D890` | **`0x0012C210`** | `0x11C210` | `push 0x7074726B`+`push 0x614`(1556)+`push &global`; `rep stosd` 0x185 dwords = 1556 B | **A** |
| `ptrk` object pointer | `0x00601EB4` | **`0x00533080`** | — | the ctor's `push 0x533080`; 48 xrefs across 29 functions | **A** |
| recency weight table | `0x00540FE0` | **`0x0045AF14`** | `0x44C2F4` | floats 1/24, 1/48, 1/96, 1/192 byte-identical | **A** |
| success weight table | `0x00540FF0` | **`0x0045AF24`** | `0x44C304` | floats 0.0625, 1/96, 1/192, 0.0026875 | **A** |
| two extra weight tables | (absent) | **`0x0045AF34`, `0x0045AF44`** | `0x44C314/24` | 0.0541667, 0.01875, 0.009375, 0.0046875 (identical pair); read by `0x0012CB30` | **B** |
| **RepetitionFactor accumulator** | (in `0x0024E188`) | **`0x0012C9E0`** | `0x11C9E0` | the sole reader of `0x0045AF14`; walks the ring, bands `i/24` | **A** |
| **success accumulator** | (in `0x0024E1C0`) | **`0x0012B7E0`** | `0x11B7E0` | the sole reader of `0x0045AF24`; gates on record `+0x0E == 5` and `+0x0D > 2.0` | **A** |
| **cheat getter — repetition** | `0x0024E188` | **`0x0012CA40`** | `0x11CA40` | returns cached `ptrk[+0x00]`; **6 call sites in 6 gameplay functions** | **A** |
| **cheat getter — success** | `0x0024E1C0` | **`0x0012CA70`** | `0x11CA70` | returns cached `ptrk[+0x04]`; **4 call sites in 2 gameplay functions** | **A** |
| cached-factor update | — | **`0x0012C2F0`** | `0x11C2F0` | calls both accumulators, `fstp` into `ptrk[+0x00]`/`[+0x04]` | **A** |
| **per-play recorder** | `0x00148900` | **`0x0012C360`** | `0x11C360` | only writer of the count field `+0x610`; 47-slot shift-down; count clamped at `0x30` | **A** |
| recorder driver | (same fn) | **`0x000695B0`** | `0x695B0` | sole caller of the recorder; inlines the `+0x04` and `+0x0F` writes | **A** |
| **`IsRun` twin** (returns *is-pass*) | `0x001F82E8` | **`0x000A8F70`** | `0xA8F70` | reads authored play type `playbook[side]+0x1414`, true for type ∈ [1,6]; **30 callers** | **A** |
| authored play-type source | `0x00243F58` | **`[0x0051DA6C] + side*0xAFBC + 0x1414`** | — | read by `0x000A8F70` and by the recorder's skip test (`== 0x15`) | **A** |
| **the SEAM** — select play from group | `0x00249498` | **`0x001311C0`** | `0x1311C0` | `cdecl(side, group)`; indexes `[0x0051DA6C]` at stride `0xAFBC`, calls the selection query, publishes the pick | **A** |
| selection query (weighting/roulette) | `0x002BFF68` | **`0x00203F00`** | `0x203F00` | builds an `LPBP`/`IABP`/`RGIA`/`tcrp` descriptor; calls both ptrk scorers; 225×12 B candidate buffer | **A** |
| DB query engine | `0x004C7E38` | **`0x0031F230`** | `0x31F230` | the callee of every `select …` string site, incl. the ctor's `'STPG'` load | **A** |
| VM command handler | `0x0024BB50` | **`0x00133580`** | `0x133580` | `jmp [ecx*4 + 0x133984]`, 12 arms; two arms call the seam | **B** |
| playbook table base | `[0x00609770]` | **`[0x0051DA6C]`** | — | `imul …, 0xAFBC` at 499 sites; the seam and `IsRun` both index it | **A** |
| situation object | `*0x00601F4C` | **`[0x00533090]`** | — | possession byte at `+0x40` — the PS2 offset, unchanged | **A** |
| `'STPG'` GBIN section register | `0x0024E458` | **`0x002AE4B0`** | — | ctor tail: `push 0x53545047`, `push obj`, `call` | **A** |
| `fatg` ctor (bonus) | — | **`0x00049820`** | `0x39820` | `push 0x66617467`, size 8, global `0x00532B18` | **B** |
| `165.75` shed-power gate | — | **`0x0041B830`** (const) | `0x40C5F0` | unique in the image; **6** referencing sites in 6 functions | **B** |

---

## 4. `ptrk` — the object and the record, read from the code

The PS2 note "1556 B = 16-B header + two 48×16 rings + counts" is confirmed
**exactly**, from three independent instructions:

- ctor allocates `0x614` = **1556** and zeroes `0x185` dwords = **1556 B**;
- every ring access computes `obj + 0x10 + side*0x300` (`lea edx,[eax+eax*2]` /
  `shl edx,8` → `side*768`, i.e. **48 entries × 16 B**);
- the count pair is `word [obj + side*2 + 0x610]`, and `0x610` = `0x10 + 2*0x300`.

**Header (16 B at `obj+0x00`)** — all four written by `0x0012C2F0`:

| off | meaning | evidence |
|---|---|---|
| `+0x00` | cached **repetition factor** (float) | `fstp [eax]` after `call 0x0012C9E0`; read by getter `0x0012CA40` |
| `+0x04` | cached **success factor** (float) | `fstp [ecx+4]` after `call 0x0012B7E0`; read by getter `0x0012CA70` |
| `+0x08`, `+0x0C` | two further cached values | filled by `0x0012C500` (a 0x410-byte function, 4 callers) — *Hypothesis*: the twins of the two extra weight tables |

**Record (16 B, entry 0 = most recent):**

| off | PS2 | meaning | evidence |
|---|---|---|---|
| `+0x00` | — | **the play just called** | recorder stores its stack arg here; the recency accumulator's `cmp ebx,[esi]` compares against it |
| `+0x04` | `@4` | **opponent's play id** | written inline by the driver at `0x00069669` from `playbook[side^1]+0x3FD4`; the standalone setter `0x0012C3F0` exists but has **0 callers** |
| `+0x08` | `@8` | **direction/zone bitmask** | written by `0x0012C430` from the encoder `0x0012C010` |
| `+0x0C` | — | init `0xFF` sentinel | recorder's `mov byte [ecx+0xC], 0xFF`; setter `0x0012C410` has **0 callers** |
| `+0x0D` | `@13` | **yards** (signed byte) | success accumulator's `movsx eax, byte [esi]`; setter `0x0012C470` takes a float and calls `ftol` |
| `+0x0E` | `@14` | **5-way outcome** | success accumulator's `cmp byte [esi+1], 5`; setter `0x0012C490` is **write-once** (`cmp byte [eax],0; jne; mov [eax],dl`) |
| `+0x0F` | `@15` | **run/pass**: 1 = run, 2 = pass | driver writes it inline from `call 0x000A8F70`; the encoder tests `cmp cl,2` for the pass branch |

**The `@4` discrepancy is resolved, not waved at.** The PS2 note put the
opponent play at `@4` and said nothing about `@0`. Xbox has both: `@0` is the
*own* play (set at push time by the recorder) and `@4` is the *opponent's* play
(set immediately after by the driver). The two are written 0x28 bytes apart in
the same basic block. So the Xbox record has one more populated field than the
PS2 note describes; whether PS2 also populates `@0` is an **open item** — it
should be re-read on the PS2 side rather than assumed either way.

**The direction/zone encoder `0x0012C010`** builds a bitmask (not an enum) from
the play's start and end positions plus the run/pass byte. Observed bits:
`0x002`, `0x004`, `0x008`, `0x010`, `0x020`, `0x040`, `0x080`, `0x100`,
`0x200`, `0x10000000`, `0x20000000`, `0x40000000`. Thresholds it compares
against: **4.5**, **7**, **15** (yards, `0x0041AD58` / `0x0041ABC0` /
`0x0041AB88`) and a double **1.02667** (`0x0041AD50`). Reading the exact bit
semantics is a Phase-2 job; the important structural fact for the coach-brain
is that `@8` is already a rich bitfield, not a 4-value enum.

### The recency model, in full

```
0x0012C9E0   ; float RepetitionAccumulate(AL = side, EBX = play id)
  mov  ecx, [0x533080]              ; the ptrk object
  fld  dword ptr [0x41AAF4]         ; 0.0 -- the accumulator seed
  movzx eax, al
  lea  edx, [eax + eax*2]
  mov  ax,  word ptr [ecx+eax*2+0x610]   ; count[side]
  shl  edx, 8                       ; side * 0x300
  lea  esi, [edx + ecx + 0x10]      ; ring base
  jbe  done                         ; count == 0 -> return 0.0
loop:
  cmp  ebx, [esi]                   ; this record's play == the queried play?
  jne  next
  mov  eax, 0x2AAAAAAB
  imul ecx  /  sar edx,1  /  ...    ; eax = i / 24   (the band)
  fadd dword ptr [eax*4 + 0x45AF14] ; += recencyWeight[band]
next:
  inc  ecx / add esi,0x10 / dec edi / jne loop
```

Bands are `i/24` over a 48-entry ring, so only bands 0 and 1 are ever reachable
from a full ring — **weights `1/96` and `1/192` are dead at stock ring size**.
That is the same unclamped 4-band shape the PS2 note records, and it is a
concrete argument for the Phase-5 "reformulate as a decay curve" item.

The success accumulator `0x0012B7E0` is the same walk with a different
predicate: no play-id compare (it is a per-side aggregate), accumulate when
`record[+0x0E] == 5` **and** `record[+0x0D] > 2.0` (`fcomp [0x0041AC38]`).

### De-cheese: two seven-byte patches

Both getters are trivially short, and both already contain the instruction the
patch needs:

```
0x0012CA40   mov  eax, [0x532B48]          ; a settings object
             test eax, eax
             je   0x12CA5A
             mov  cl,  byte ptr [eax+0x17E]
             test cl,  cl
             je   0x12CA5A
             fld  dword ptr [0x41AAF4]     ; <- 0.0,  d9 05 f4 aa 41 00
             ret
0x12CA5A:    mov  eax, [0x533080]
             fld  dword ptr [eax]          ; the cached repetition factor
             ret
```

`0x0012CA70` is byte-identical but for `fld dword ptr [eax+4]`.

**Neutering is a 7-byte overwrite at each entry point** — `d9 05 f4 aa 41 00 c3`
(`fld [0x41AAF4]; ret`) — same size, no relocation, no cave, no branch fixups.
Cheaper than the PS2 equivalent.

**And there is already a kill switch in the shipped code.** Both getters return
0.0 when `byte [[0x00532B48] + 0x17E]` is non-zero. *Hypothesis*: a difficulty
or "classic/cheat-off" option. If it is settable from the options menu or by a
single poke, **Phase-4's acceptance test can be run with no patch at all** —
flip the byte, play, observe. That is the cheapest experiment on the board and
it should be tried before anything is written to `.text`.

**The consumer surface** (the "9 cheat consumers" on PS2 → **10 call sites in 6
distinct functions** here):

| getter | call sites | consumer functions |
|---|---|---|
| `0x0012CA40` (repetition) | 6 | `0x00092F60`, `0x000E2C70`, `0x000F8170`, `0x000FA300`, `0x000FAA20`, `0x000FC3C0` |
| `0x0012CA70` (success) | 4 | `0x000E2C70` (×2), `0x000FAA20` (×2) |

Naming each consumer (coverage / break-block / tackle / ball-contest /
event-rate) is **not** done here — that needs either a decompiler or a live
session, and guessing would be exactly the sin rule 4 exists to prevent.

---

## 5. The seam

```
0x001311C0   ; void SelectAIPlayFromGroup(int side, int group)   [cdecl, stack args]
  mov  eax, [esp+4]                 ; side
  mov  ecx, [0x51DA6C]              ; playbook table base
  mov  edx, [esp+8]                 ; group id
  movzx ebp, al
  imul ebp, ebp, 0xAFBC             ; per-team block
  mov  esi, [ecx+ebp+0x0C]          ; that team's playbook
  push edx / push esi / push eax
  call 0x00203F00                   ; <-- the selection query (weighting+roulette)
  add  esp, 0x0C
  lea  edi, [esp+0x10]
  mov  ebx, eax
  call 0x00203AD0                   ; fetch the chosen play's data
  ...
  mov  [0x51D728], eax              ; publish the pick (only if side == possession)
  mov  [ecx+ebp+0x10], edx          ; store selected play into the team block
  rep movsd (0x57A dwords)          ; blit 5608 B of play data into +0x1610
```

**Signature.** `cdecl`, two stack args, caller-cleaned, no `ret N`. PS2 had
three (`a0`=side, `a1`=flag, `a2`=group); on Xbox the flag rides in the group
word — both VM call sites mask it off with `and reg, 0xFFFF7FFF` before pushing,
i.e. **bit 15 of the group argument is the PS2 `a1` flag**.

**Call sites — six, not two.** This is a real divergence from the PS2 map and it
matters for the hook:

| caller | sites | group ids | role |
|---|---|---|---|
| `0x00133580` | `0x001336C5`, `0x001338C0` | from the VM command word | **the VM command handler** — the PS2 pair |
| `0x00076E30` | `0x00076E6B`, `0x00076E92`, `0x00076EBB` | `2`, `0x20`, `0x21` | special teams (jump-table dispatch on a 1..4 mode) |
| `0x00077060` | `0x000771C6` | — | *unclassified* |

Retargeting only the `0x00133580` pair reproduces the PS2 hook exactly. The
other four sites are the honest residue — either they are genuinely a different
code path (special teams, which the coach-brain may not want to own), or the
Xbox compiler split what PS2 kept together. **Scope-test them before including
them** (project rule 1): they are the same *topic*, not obviously the same
*path*.

**The VM command handler `0x00133580`** takes a command struct in `EAX`, reads
the situation object (`+0x40` possession, `+0x38` compared against 6), and
dispatches through `jmp dword ptr [ecx*4 + 0x00133984]` — a 12-arm table
(cmd 0…11) that lives inside `.text`. The seam call at `0x001338C0` is in the
**cmd 8** arm. PS2 put the seam under cmd11 and set-specific-play under cmd8/9,
so **the command numbering does not transfer** — treat any PS2 command index as
unverified here. The handler has **zero `E8` callers**: it is entered
indirectly, so the usual caller scan finds nothing, and neither `0x00133580` nor
`0x001311C0` appears anywhere in the image as an absolute pointer (checked over
the whole file, not just `.data`).

**The 225-slot candidate buffer is here too.** `0x00203F00` reserves
`sub esp, 0xBAC` and zeroes `0x2A3` dwords = 2700 B at `[esp+0x120]` —
**225 × 12 B**, the PS2 number. Consistent with PS2's F5: no `cmp` against
224/225 appears anywhere in the `0x00200000–0x00206000` module (closed-set
negative over the harvested function index — see the caveat in §2).

---

## 6. Globals and helpers worth having

| VA | what | how known |
|---|---|---|
| `0x00533080` | `ptrk` object pointer | ctor arg; never written by a direct `mov` — the registry helper writes it |
| `0x00533090` | **situation object** pointer | possession `+0x40`, ball pos `+0x0C`/`+0x10` (floats), `+0x38` (vs 6), `+0x90` (recording side) |
| `0x0051DA6C` | **per-team playbook table** base, stride `0xAFBC` | 793 xrefs / 332 functions |
| `0x0051D728` | last AI-selected play (published by the seam) | seam tail |
| `0x00532B48` | settings/options object; `+0x17E` is the tendency kill switch | both getters |
| `0x00532B18` | `fatg` object pointer | fatigue ctor |
| `0x00256800` | registry create (`fourcc`, flags, size, `&global`) | ctor of both `ptrk` and `fatg` |
| `0x002567F0` / `0x00256850` | registry deref / release | ctor body |
| `0x00256880` / `0x002568B0` | register serializer / callback triples | `fatg` ctor |
| `0x0015DE10` | game-mode getter (ctor gates on `{0,1,2,4,5,6,7}`) | ctor |
| `0x0031F230` | **DB query engine** | every `select …` string site |
| `0x002AE4B0` | GBIN save-section register | ctor tail |
| `0x0034A458` | float→int — *Hypothesis*: `ftol` | yards setter: `fld [esp+8]; call; mov byte […], al` |

**Query strings** (`.data`, the ptrk block is contiguous — weight tables at
`0x0045AF14`, strings from `0x0045AF58`, exactly the "one translation unit"
shape PS2 showed):

- `0x0045AF58` `select 'YTDC' into … from 'HCOC' where 'DIGT' = … and 'SPOC' = …`
- `0x0045AFE4` `select 'PNIG' into … from 'FNIG'`
- `0x0045B008` `select 'STPG' into … from 'NIBG'` — the franchise save/load path
- `0x00494340` / `0x00494380` / `0x004943C4` — the `IABP where RGIA` group
  queries, used by `0x002013B0`, which is the **AI-percentage rebuild**
  (`tcrp` = 100/count per group), *not* the runtime selector

---

## 7. Closed-set negatives (evidence, not absence of evidence)

- **`ptrk` and `fatg` never appear as forward ASCII** anywhere in the 4.89 MB
  image (0 hits each). They exist only as little-endian immediates — `krtp` at
  file `0x11C215`, `gtaf` at file `0x39824`. Searching for the readable spelling
  finds nothing, and that miss means nothing.
- **`0x00533080` is never the destination of a `mov`.** Zero
  `mov [0x533080], reg` in the harvested index. The pointer is written by
  `0x00256800`, which receives `&global` as an argument — so "nothing writes
  this" is *true and harmless*, and is exactly the shape of claim
  `docs/fact-check-2026-08.md` warns about. Recorded with its explanation.
- **`0x0012C3F0`, `0x0012C410`, `0x0012C470`, `0x0012C4B0`, `0x0012C4D0` have
  zero callers.** All five are the standalone setters/dispatchers whose work the
  compiler inlined into `0x000695B0`. They are live code paths in the *source*
  and dead call targets in the *binary*.
- **No 224/225 bound check** in the selection module (§5).
- **`0x00133580` and `0x001311C0` appear as absolute pointers nowhere in the
  image** — checked across all 11 sections plus the header block.

---

## 8. Open items for the rest of X2

1. **The `@0` field.** Xbox records the own-play at record `+0x00`; the PS2 note
   does not mention it. Re-read the PS2 recorder — this is a PS2-side question
   the Xbox work raised, not an Xbox uncertainty.
2. **The four extra seam call sites** (`0x00076E30` ×3, `0x00077060`) — scope-test
   against the coach-brain's intended blast radius before hooking them.
3. **VM command numbering** — PS2's cmd8/9/11 mapping does not transfer. If the
   script asset really is shared across platforms, the *asset* will resolve this
   faster than the disassembly will.
4. **`0x0012C500` and `0x0012CB30`** — the consumers of the two extra weight
   tables at `0x0045AF34`/`0x0045AF44` that PS2 did not appear to have. X1
   flagged them as "richer than PS2"; whether that is real divergence or just a
   more complete dump is unresolved.
5. **The two X1 divergence candidates** (`-0.13`, `335.4`) are untouched — they
   belong to the blocking campaign's sites, not the coach's, and are found the
   same way: locate the twin, read what it loads.
6. **Consumer naming** for the 6 cheese functions and the 30 `IsRun` callers —
   needs a decompiler or a live session.
7. **`byte [[0x00532B48]+0x17E]`** — find out what sets it. If it is an existing
   option, Phase 4 gets a free acceptance run.

---

## 9. Tools produced this session

- **`recon/xbe.py`** — stdlib XBE parser: header, certificate (title + title
  id), section table, linked-library table, retail/debug entry-point and
  kernel-thunk XOR de-obfuscation, `va_to_off`/`off_to_va` honouring raw-vs-
  virtual size, and `find_le32`/`xrefs` (the unaligned cross-reference
  primitive everything above is built on). CLI: `--sections`, `--va`, `--off`,
  `--xref`.
- **`tests/test_xbe.py`** — 14 tests. A synthetic XBE built in memory covers the
  format reader with no dump present; the image-backed half pins the numbers
  this document cites, including the corrected recency-table VA.

The disassembly harness used for §3–§7 (capstone-based call-target harvesting
and the per-function index) was scratch and is **not** committed — it should be
promoted to `recon/x86dis.py` when the capstone dependency decision in §0 is
taken, since every remaining X2 question needs it again.

# C1 on Xbox — the block-target eligibility filter, located and specified

**2026-08-14. Static only: no rig, no emulator, nothing executed.** Target is
`extract/xbox/default.xbe` (4,890,624 B, retail, title id `0x45410036`,
SHA-256 `cff297ed…7996`); reference is `extract/SLUS_207.52`
(SHA-256 `5cb956b6…d36c`, `gametitle` CRC `14F8B841`).

This is phase **C2** of `docs/pnach-to-xbe-pipeline.md` §9 — the LOCATE→EMIT arm
for the one patch whose behaviour we know by eye. It is also the first anchor
ever placed **inside the blocking module**, which §5 of that spec calls "the
anchor desert". §7 below records the neighbours found along the way.

---

## 0. The answer, up front

| | |
|---|---|
| **PS2 site** | `0x001F2D60`  `12C0000F`  `beq s6, zero, 0x001F2DA0` |
| **Xbox twin** | **VA `0x000A4496`**, **file offset `0x00094496`** |
| **stock bytes** | `74 23` — `je 0x000A44BB` (2 bytes) |
| **patched bytes** | `EB 23` — `jmp 0x000A44BB` (2 bytes) |
| **length** | 2 → 2. **Exact fit. No `nop` padding.** |
| **polarity** | **taken = ADMIT.** No MSVC inversion; the x86 arms are *not* swapped. |
| **confidence** | **certain** on the function and the branch; **certain** on the polarity. §6. |

Enclosing function: **Xbox `0x000A43E0`** (file `0x000943E0`) — the twin of PS2
`0x001F2CD8`, the defender-list builder ("Gate B" of `docs/fb-wr-blocking.md`).

---

## 1. What C1 is (recap, PS2 side)

`docs/fb-wr-blocking.md` Gate B / `docs/block-dominance-requirements.md` C1. The
block-target eligibility filter admits a defender to the blocking system's
candidate pool only if his `ai_state` byte is **2** (pursuit), **30** (rush /
engaged) or **51** (authored wait), or he is human-controlled (flag `0x4000`).
A corner in man coverage (state 22) or zone (37/38/40) and a deep safety are
all rejected — which is why a coverage DB could never be blocked by anybody.

C1 forces the *first* branch of that filter always-taken, which lands on the
admit block and bypasses every test. Operator-confirmed live on PS2: the pulling
guard stops chasing the wrong man and reads the fit.

---

## 2. Locating the twin — the evidence chain

**Method.** Function heads harvested from every `E8 rel32` whose target lands in
`.text` (**12,337 heads**, reproducing `docs/xbox-hook-map.md` §2), then
disassembly restarted at each head — never a linear sweep. 1,228,480
instructions indexed.

### 2.1 The state triple is unique in the image

**Finding.** The constant sequence 2 / 30 / 51 compared in order against a byte
occurs **exactly once** in `.text`.

Two independent searches agree:

1. *Instruction-level*, over the 1.23 M-instruction index — a `cmp` against 2
   followed within 45 instructions (same function) by a `cmp` against `0x1E`
   then a `cmp` against `0x33`: **1 hit**, at `0x000A44A8` / `0x000A44AD` /
   `0x000A44B2`.
2. *Byte-level*, over raw `.text` and independent of the head harvest — every
   plausible compare-immediate encoding (`3C ib`, `80 /7 ib`, `83 /7 ib`,
   `80 38+r ib`, `3D id`, `81 /7 id`) carrying `0x1E`, paired with any carrying
   `0x33` within 32 bytes: 120 × 110 candidates → **1 surviving pair**, the same
   one, with a `cmp`-vs-2 site 10 bytes ahead of it.

The second search does not depend on the head harvest being right, so a bad
harvest cannot have produced this hit.

### 2.2 The filter itself, quoted

Xbox `.text`, VA = file offset + `0x10000`:

```
000A4490  8b4c2410      mov  ecx, dword ptr [esp + 0x10]   ; the loop-invariant gate (s6 twin)
000A4494  85c9          test ecx, ecx
000A4496  7423          je   0x000A44BB                    ; <== C1 SITE. taken -> ADMIT
000A4498  8b480c        mov  ecx, dword ptr [eax + 0xc]    ; defender flags   (PS2: lw v0,12(a2))
000A449B  f6c540        test ch, 0x40                      ; ch&0x40 == ecx&0x4000  (human?)
000A449E  751b          jne  0x000A44BB                    ; human -> ADMIT
000A44A0  8b88fc020000  mov  ecx, dword ptr [eax + 0x2fc]  ; -> ai_state object (+0x2FC)
000A44A6  8a09          mov  cl, byte ptr [ecx]            ; the state byte
000A44A8  80f902        cmp  cl, 2
000A44AB  740e          je   0x000A44BB                    ; state 2  -> ADMIT
000A44AD  80f91e        cmp  cl, 0x1e
000A44B0  7409          je   0x000A44BB                    ; state 30 -> ADMIT
000A44B2  80f933        cmp  cl, 0x33
000A44B5  0f8583000000  jne  0x000A453E                    ; not 51   -> REJECT
000A44BB  8b4d08        mov  ecx, dword ptr [ebp + 8]      ; ADMIT block begins
```

All three signature elements from the brief are present and in the right
relationship: `0x2FC` dereferenced then a **byte** read at `[0]` of the result;
the `0x4000` flag test (as `test ch, 0x40`, MSVC's byte-narrowed form); and the
2 / 30 / 51 triple against that byte.

### 2.3 Instruction-for-instruction alignment with the PS2 function

**Finding.** Xbox `0x000A43E0` and PS2 `0x001F2CD8` align across the whole
function. Every struct offset is *byte-identical* between the two builds.

| PS2 `0x001F2CD8` | Xbox `0x000A43E0` | what |
|---|---|---|
| `lbu v0, 3020(a2)` | `movzx ecx, byte [eax + 0xbcc]` | **same offset 0xBCC** |
| `addiu a0, v0, -16` | `add ecx, -0x10` | same bias |
| `sltiu v1, a0, 77` / `beq v1,zero,0x001F2D60` | `cmp ecx, 0x4c` / `ja 0x000A4490` | same 77-wide range, default → the filter |
| jump table `0x00583650`, 77 × u32 | `jmp [ecx*4 + 0xA4580]` via byte index `0xA4588` | **see 2.4** |
| `beq s6, zero, 0x001F2DA0` | `je 0x000A44BB` | **the C1 branch** |
| `lw v0, 12(a2)` / `andi v0,v0,0x4000` / `bne` | `mov ecx,[eax+0xc]` / `test ch,0x40` / `jne` | flags +0xC, mask 0x4000 |
| `lw v0, 764(a2)` / `lbu a0, 0(v0)` | `mov ecx,[eax+0x2fc]` / `mov cl,[ecx]` | **same offset 0x2FC** |
| `beq a0,2` / `beq a0,30` / `bne a0,51` | `cmp cl,2 je` / `cmp cl,0x1e je` / `cmp cl,0x33 jne` | the triple |
| `mult v1, s3, 112` / `addu s1, v1, s5` | `movzx edx,bl` / `imul edx,edx,0x70` / `lea esi,[edx+ecx]` | **entry stride 112 = 0x70** |
| `sw a2, 92(s1)` | `mov [esi+0x5c], eax` | **+92 = defender pointer** |
| `sw zero, 0(s1)` | `mov dword [esi], 0` | |
| `jal 0x001EEE10` | `call 0x000A0B00` | |
| `jal 0x004ADDA8` (3 args) | `call 0x002A8620` (3 pushed args) | |
| `lw v0,-17520(gp)` → `0x00601280`; `lbu v1, 88(v0)` | `mov eax,[0x00532CA4]`; `mov cl,[eax+0x58]` | **+0x58 byte, same guard** |
| `abs.s f0, f0` else `jal 0x004AD760` | `fabs` else `call 0x002A8080` | same two-arm float |
| `swc1 f0, 104(s1)` | `fstp dword [esi+0x68]` | **+104 = 0x68** |
| `jal 0x00469E78` (2 floats) → `sw v0, 96(v1)` | `call 0x002908A0` → `mov [esi+0x60], eax` | **+96 = 0x60** |
| `sb zero, 108(v1)` | `mov byte [esi+0x6c], 0` | **+108 = 0x6C** |
| `jal 0x001F2B00` with `a1 = s6` | `call 0x000A4260` with `ecx = [esp+0x10]` | the gate value passed on |
| `addiu v0,s3,1` / `andi s3` (admit count++) | `inc bl` | admit counter |
| `andi s4,v0,0xff` / `sltiu v1,s4,11` | `mov [esp+0x14], 0xb` / `dec` / `jne` | **11 iterations** |

### 2.4 The dispatch table matches 77/77 — the decisive check

The position-class pre-filter dispatches through a table on both platforms. PS2
uses 77 full u32 targets; MSVC compressed the same table into a 77-byte index
plus a 2-entry target array. Decoded to `{0 = skip, 1 = run the filter}`:

```
PS2  (0x00583650, 77 × u32 -> {0x001F2E2C, 0x001F2D60}) : 00111111111111110111111111111111100111111111110111111111101111111111111110110
Xbox (0x000A4588, 77 × u8  -> {0x000A453E, 0x000A4490}) : 00111111111111110111111111111111100111111111110111111111101111111111111110110
                                                          ^^ IDENTICAL, 77/77
```

Skip-entries at indices `[0, 1, 16, 33, 34, 46, 57, 73, 76]` on **both**.

**Finding.** Two independently compiled builds emitting the same 77-entry
sparse pattern is not a coincidence. This pins the function correspondence, and
it pins the *arms*: PS2's non-skip target is `0x001F2D60` — the C1 branch
itself — and Xbox's is `0x000A4490`, which falls through into `0x000A4496`.
(The two differ by 6 bytes because MIPS keeps the gate in the callee-saved `s6`
while MSVC reloads it from `[esp+0x10]` each iteration.)

### 2.5 Function-boundary corroboration

- `0x000A43E0` is a harvested head with **3 `E8` callers**
  (`0x000A66F3`, `0x000A676A`, `0x000A6969`).
- The 16 bytes before it are `C3 CC×15` — a `ret` then MSVC `int3` padding.
  The head is confirmed by the linker's own layout, independently of the call
  scan.
- PS2 has exactly **1** caller of `0x001F2CD8`: the `jal` at `0x001F556C`,
  inside `0x001F5510`, whose body is *"call `0x001F2EA0` (blocker list), then
  call `0x001F2CD8` (defender list), return"*. On Xbox that wrapper was
  **inlined**: all three call sites to `0x000A43E0` are preceded within 15 bytes
  by a call to `0x000A45E0` (§7), at `0xA66EB`/`0xA66F3`, `0xA675B`/`0xA676A`,
  `0xA695A`/`0xA6969`. Same pairing, three copies.

---

## 3. The branch — identification

**Finding.** The Xbox twin of PS2 `0x001F2D60 beq s6, zero, 0x001F2DA0` is

```
000A4496  74 23     je 0x000A44BB
```

Its guard is the pair immediately above it, `mov ecx,[esp+0x10]` /
`test ecx,ecx`. `[esp+0x10]` is the twin of PS2's `s6`:

- **written only before the loop** — at `0x000A440E` and `0x000A4428`, both
  above the loop head `0x000A4455`; nothing inside the loop body
  (`0xA4455`–`0xA4548`) writes it. Loop-invariant, exactly like `s6`.
- **read in the same two places as `s6`** — here at the gate, and again at
  `0x000A4523` (`mov ecx,[esp+0x18]` after two pushes = the same slot) as the
  argument to `call 0x000A4260`, mirroring PS2's `daddu a1, s6, zero` before
  `jal 0x001F2B00`.
- its producer is PS2's `jal 0x0015ADA0`, which MSVC **inlined** at
  `0x000A43E9`–`0x000A442C` (globals `0x00532BAC` / `0x00532BA0`, else
  `call 0x000740E0` / `call 0x00073C40`).

---

## 4. POLARITY — the trap, and why it is not sprung here

**This is the claim the brief says must not be wrong, so it is argued from
control flow, not from the mnemonic.**

**Verdict: `je` taken → ADMIT. The MSVC build did NOT invert the condition or
swap the blocks. `74` → `EB` is the correct edit.**

Three independent proofs, each sufficient on its own.

### 4.1 The taken successor writes the candidate list and increments the count

`0x000A44BB` is the head of the admit block:

```
000A44BB  mov   ecx, dword ptr [ebp + 8]     ; arg0 = candidate-list base
000A44BE  movzx edx, bl                      ; bl = number ADMITTED so far
000A44C1  imul  edx, edx, 0x70               ; * 0x70 entry stride
000A44C4  lea   esi, [edx + ecx]             ; esi = &list[count]
...
000A44CC  mov   dword ptr [esi + 0x5c], eax  ; <== STORE THE DEFENDER into the entry
000A44CF  mov   dword ptr [esi], 0
...
000A452A  mov   dword ptr [esi + 0x60], eax
000A452D  mov   byte  ptr [esi + 0x6c], 0
000A4531  call  0x000A4260
000A453C  inc   bl                           ; <== COUNT++  (admit path only)
```

`eax` is the defender under test — it is the same `eax` whose `+0xC` flags and
`+0x2FC` state the filter just read. The taken arm stores it into the pool and
bumps the pool count. That is the definition of admit.

### 4.2 The not-taken successor's terminal branch skips exactly that block

The fall-through runs the filter, whose last test is
`0x000A44B5 jne 0x000A453E`. `0x000A453E` is the loop tail:

```
000A453E  mov  eax, dword ptr [esp + 0x14]   ; loop countdown
000A4542  inc  edi                           ; player index++
000A4543  dec  eax
000A4544  mov  dword ptr [esp + 0x14], eax
000A4548  jne  0x000A4455                    ; next player
```

Note what is *missing*: `inc bl`. The admit block ends at `0x000A453C inc bl`
and then **falls into** `0x000A453E`. So the reject arm reaches the loop tail
having skipped both the store block and the counter. Reject and admit differ by
exactly "did the entry get written and counted".

### 4.3 The dispatch table names the reject arm independently

`0x000A453E` is also one of the two jump-table targets (§2.4) — it is where the
*position* pre-filter sends the classes it discards, and its PS2 counterpart is
`0x001F2E2C`, which is the PS2 loop tail (`addiu v0,s4,1` / `andi s4,v0,0xff` /
`sltiu v1,s4,11`). Two different mechanisms (state filter, position pre-filter)
independently route rejects to the same address, on both platforms.

### 4.4 Sense agreement with MIPS

PS2 `beq s6, zero, admit` is taken when the gate is **zero**.
Xbox `test ecx,ecx ; je admit` is taken when the gate is **zero**.
Same register role, same comparison, same sense, same target class. Forcing
either always-taken bypasses the filter and admits every defender.

### 4.5 What the *wrong* patch would have been

If MSVC had inverted (emitting `jne` to the filter with admit as the
fall-through), the mechanical edit `jcc → jmp` would have jumped to the
**reject** path and rejected every defender — booting cleanly and playing
subtly wrong. That did not happen here, and §4.1–4.3 are the proof rather than
the assumption.

For the record, so the alternatives are not confused later: **`90 90` is not the
inverse of this patch.** Nopping the branch makes the gate never fire, so the
state filter always runs — *stricter* than stock, not reject-all. The
never-admit edit would be an unconditional jump to `0x000A453E`, which does not
fit in 2 bytes (`EB` reaches `0x000A44BB`+127 max, and `0x453E − 0x4498 = 0xA6`
is out of `rel8` range). **The only edit specified here is `74 23` → `EB 23`.**

---

## 5. The patch specification

**One branch edit. No cave, no relocation, no section append, no size change to
the XBE — so `docs/pnach-to-xbe-pipeline.md` §8's same-size in-place path
applies (sector 265, image otherwise byte-identical).**

```
file:            extract/xbox/default.xbe
file offset:     0x00094496        (VA 0x000A4496; .text rule off = va - 0x10000)
length:          2 bytes
original bytes:  74 23             je  0x000A44BB
replacement:     EB 23             jmp 0x000A44BB
padding:         NONE REQUIRED — jcc rel8 (2B) and jmp rel8 (2B) are the same length
```

The `rel8` displacement `0x23` is **unchanged**: `0xA4498 + 0x23 = 0xA44BB`
under both opcodes, so the target survives the edit untouched and the next
instruction boundary (`0x000A4498`) is unmoved.

**Byte-search is not a valid way to find this site.** The pattern `74 23`
occurs **220 times** in `.text`. The site is identified by function, not by
pattern — patch the offset, and assert the stock bytes are `74 23` before
writing.

### Ready-to-run spec for `tools/patch_xbe.py`

The EMIT tool already exists and takes VA-based specs with an `expect` guard
(which is the right way round — `docs/pnach-to-xbe-pipeline.md` §6 warns that an
emitter working in file offsets can happily write into an unmapped file gap).
Either form:

```
# text
0x000A4496 = EB 23 : 74 23
```
```json
{"patches": [{"va": "0x000A4496", "new": "EB23", "expect": "7423",
              "note": "C1: block-target eligibility filter -> admit always (Gate B, fn 0x000A43E0)"}]}
```

```
python3 tools/patch_xbe.py extract/xbox/default.xbe patches/xbox-c1.json -o out.xbe --verify
```

⚠ **`tools/patch_xbe.py`'s docstring example is a placeholder, not this site.**
It shows `0x0011E2A0 = EB 0F : 74 0F` labelled "C1: force the eligibility
branch". That VA and those bytes were written before the twin was located and
are fictitious. The located site is `0x000A4496`, `74 23` → `EB 23`.

### Verification after writing (EMIT `--verify` contract)

1. `data[0x94496:0x94498] == b"\xEB\x23"`.
2. Disassemble `.text` forward from head `0x000A43E0`; assert the instruction at
   `0x000A4496` is `jmp` with target `0x000A44BB` and size 2.
3. Assert no byte outside `[0x94496, 0x94498)` changed (whole-file diff).
4. `.text`'s section SHA-1 at its header `+0x24` must be recomputed
   (`docs/pnach-to-xbe-pipeline.md` §7b — note `.text` is the one section whose
   stock digest does not reproduce under the verified rule; write a correct one
   rather than leaving the stale value).

### Sanity arm (run 2026-08-14, capstone 5.0.7, x86-32)

Fed back through capstone at the real address:

```
=== STOCK ===                              === PATCHED ===
000A4490 8b4c2410  mov ecx,[esp+0x10]      000A4490 8b4c2410  mov ecx,[esp+0x10]
000A4494 85c9      test ecx, ecx           000A4494 85c9      test ecx, ecx
000A4496 7423      je 0xa44bb              000A4496 eb23      jmp 0xa44bb     <== patched
000A4498 8b480c    mov ecx,[eax+0xc]       000A4498 8b480c    mov ecx,[eax+0xc]
000A449B f6c540    test ch, 0x40           000A449B f6c540    test ch, 0x40
...                                        ...
```

`ASSERT OK: EB 23 @0x000A4496 decodes as a single 2-byte 'jmp 0xa44bb'`
`ASSERT OK: next instruction boundary unchanged: 0x000A4498 == 0x000A4498`

**Dead-but-harmless residue.** `mov ecx,[esp+0x10]` / `test ecx,ecx` at
`0x000A4490`–`0x000A4495` become dead. They are safe to leave: `EFLAGS` is not
read on the admit path (`mov`, `movzx`, then `imul` which redefines flags), and
`ecx` is overwritten by the first instruction of the admit block. Nothing
branches into the middle of the patched span — every entry to this block is
`0x000A4490` (jump table + the `ja` default) and every branch target inside the
function was enumerated (§2.5 method); the only targets in the filter region are
`0x000A4490`, `0x000A44BB` and `0x000A453E`.

---

## 6. Confidence statement

| claim | level | basis |
|---|---|---|
| Xbox `0x000A43E0` is the twin of PS2 `0x001F2CD8` | **certain** | 77/77 dispatch-table identity; ~20 matching struct offsets and constants; matching caller shape; `0xCC`-padded head |
| `0x000A4496` is the twin of PS2 `0x001F2D60` | **certain** | unique-in-image state triple; instruction-for-instruction alignment; the dispatch table lands on this basic block on both platforms |
| taken arm = ADMIT, `74`→`EB` is the correct edit | **certain** | three independent control-flow proofs (§4.1–4.3) plus MIPS sense agreement (§4.4) |
| the 2-byte edit is encoding-correct and boundary-safe | **certain** | capstone round-trip (§5 sanity arm); same length, same `rel8`, boundary unmoved |
| the patched game *behaves* like patched PS2 | **untested** | static port. Needs a boot + an eyes-on-console run. See below. |

**Residual risk is behavioural, not structural.** Two honest caveats:

1. Nothing here was executed. The acceptance arm is the same one C1 passed on
   PS2 — a lead blocker that engages a coverage defender instead of running past
   him — and it needs the friend's console or xemu.
2. The gate value `[esp+0x10]` is produced by code MSVC **inlined**
   (PS2 `jal 0x0015ADA0`), so its twin is a *block*, not a function. That does
   not affect the patch (the branch bypasses the filter regardless of what the
   gate holds) but it means the PS2 correspondence for `0x0015ADA0` is
   many-to-one and must not be entered in the map as a function pair.

**Nothing in this document promotes a Hypothesis to a Finding.** The one
inference left unproven is flagged as such in §7.

---

## 7. Neighbours found along the way — seeding the anchor desert

`docs/pnach-to-xbe-pipeline.md` §5 records that all eight previously-mapped
twins are coach/`ptrk`-side and **none** is in the blocking module. These are
the first blocking-module entries. All were read out of the binary in this
session.

### 7.1 Code twins — `certain`

| PS2 | Xbox | what it is | evidence |
|---|---|---|---|
| `0x001F2CD8` | **`0x000A43E0`** | **Gate B** — defender-list builder (the C1 function) | §2 |
| `0x001F2EA0` | **`0x000A45E0`** | **Gate A** — blocker-list builder | §7.2 |
| `0x001F5510` | *inlined* at `0x000A66EB`, `0x000A675B`, `0x000A695A` | the wrapper that calls Gate A then Gate B | §2.5 |
| `0x001F2B00` | **`0x000A4260`** | called at the tail of Gate B's admit block with the gate value as an argument | aligned call; **caller count matches exactly: 1 and 1** (PS2 `0x001F2E1C`, Xbox `0x000A4531` — both inside Gate B) |
| `0x001EEE10` | **`0x000A0B00`** | first call in Gate B's admit block | aligned call; **caller count matches exactly: 4 and 4** |
| `0x004ADDA8` | **`0x002A8620`** | 3-arg math helper (570 Xbox callers) | aligned call in both Gate A and Gate B |
| `0x004AD760` | **`0x002A8080`** | float-magnitude helper, the non-`fabs` arm (356 callers) | aligned call, same `[global+0x58]` guard on both platforms |
| `0x00469E78` | **`0x002908A0`** | 2-float → int (bearing/atan2-like); result stored at entry `+0x60` (593 callers) | aligned call |

### 7.2 Gate A, quoted — because it is P-set adjacent

Xbox `0x000A45E0` ⟷ PS2 `0x001F2EA0`. `docs/fb-wr-blocking.md` Gate A: the
blocker list admits **block modes 1 and 2 only**, which is why a lead-blocking
FB (mode 3) is never entered into the assignment system.

```
000A4638  8b86f0030000  mov  eax, dword ptr [esi + 0x3f0]   ; block mode   (PS2: lw v1,1008(s1))
000A463E  85c0          test eax, eax
000A4640  0f8e89000000  jle  0x000A46CF                     ; mode <= 0 -> skip
000A4646  83f802        cmp  eax, 2
000A4649  0f8f80000000  jg   0x000A46CF                     ; mode > 2  -> skip   == "modes 1 and 2 only"
000A464F  8b86e0030000  mov  eax, dword ptr [esi + 0x3e0]   ; (PS2: lw v1,992(s1))
000A4655  83f804        cmp  eax, 4
000A4658  7475          je   0x000A46CF
000A465A  83f805        cmp  eax, 5
000A465D  7470          je   0x000A46CF
000A465F  83f806        cmp  eax, 6
000A4662  746b          je   0x000A46CF
000A4664  85c0          test eax, eax
000A4666  7467          je   0x000A46CF                     ; PS2: addiu -4 / sltiu 3 / bne + beq zero
000A4668  0fb6c3        movzx eax, bl
000A466B  6bc070        imul eax, eax, 0x70                 ; same 0x70 entry stride
000A466E  8d8e90010000  lea  ecx, [esi + 0x190]             ; PS2: addiu a2, s1, 400
```

4 callers (`0x000A6450`, `0x000A66EB`, `0x000A675B`, `0x000A695A`), and the
count reconciles exactly with PS2's 2 (`0x001F51BC`, `0x001F5560`): the wrapper
`0x001F5510` holding `0x001F5560` was inlined at 3 Xbox sites (§2.5), the other
PS2 caller accounts for the fourth (`0x000A6450`). 1 + 3 = 4.

### 7.3 Struct offsets — verified identical across builds

**Finding.** Every player-struct offset in this module is the same integer on
both platforms. This is the single most reusable result here: field offsets do
**not** need re-deriving for the rest of the blocking set.

| offset | meaning | PS2 form | Xbox form |
|---|---|---|---|
| `+0x00C` | defender flags; `0x4000` = human-controlled | `lw v0,12(a2)` / `andi 0x4000` | `mov ecx,[eax+0xc]` / `test ch,0x40` |
| `+0x2FC` | → state-chain object; `[0]` = current state byte | `lw v0,764(a2)` / `lbu a0,0(v0)` | `mov ecx,[eax+0x2fc]` / `mov cl,[ecx]` |
| `+0x190` | passed to the 3-arg helper from Gate A | `addiu a2, s1, 400` | `lea ecx,[esi+0x190]` |
| `+0x3E0` | the value Gate A rejects when 0/4/5/6 | `lw v1,992(s1)` | `mov eax,[esi+0x3e0]` |
| `+0x3F0` | **block mode** (Gate A admits 1–2) | `lw v1,1008(s1)` | `mov eax,[esi+0x3f0]` |
| `+0xBCC` | position/class byte, biased by −16, 77-wide switch | `lbu v0,3020(a2)` | `movzx ecx,[eax+0xbcc]` |

Candidate-entry layout (stride **`0x70`** = 112 on both): `+0x00` cleared,
`+0x5C` defender/blocker pointer, `+0x60` bearing result, `+0x64` (written by
Gate A's admit block, cleared by Gate B's tail — PS2 `sw zero,100(v0)` ⟷ Xbox
`mov [eax+0x64], edx`), `+0x68` float magnitude, `+0x6C` flag byte.

### 7.4 Data twin

| PS2 | Xbox | evidence |
|---|---|---|
| `0x00601280` (reached as `-17520(gp)`, `gp = 0x006056F0`) | **`0x00532CA4`** | both loaded, then the **`+0x58`** byte tested to choose `abs.s`/`fabs` over the helper call — in the same instruction position in both functions. Also written (`mov byte [eax+0x58], 1`) at Xbox `0x000A67C2`. |

### 7.5 Player-array addressing on Xbox

**Finding.** MSVC inlined PS2's `GetPlayer` (`jal 0x001655B0`). The Xbox form,
in both gates:

```
mov   ecx, dword ptr [0x00532BCC]      ; roster/context object
movzx eax, word ptr [ecx + 8]          ; players per side
imul  eax, <sideFlagByte>              ; 0 or 1
add   eax, <loopIndex>
imul  eax, eax, 0x14c0                 ; PLAYER STRUCT STRIDE = 0x14C0 (5312)
add   eax, dword ptr [ecx]             ; + array base
```

Both gates iterate **11** players (`mov dword ptr [esp+0x14], 0xb`), matching
PS2's `sltiu v1, s4, 11`.

### 7.6 Calling-convention warning for the cave work (P1 / N-1 / T3)

**Finding.** This module is built with a custom/whole-program calling
convention: `call 0x000A0B00` at `0x000A44D5` receives the defender in **`eax`**
with only the scratch pointer pushed, and `call 0x000A4260` at `0x000A4531`
receives the gate value in **`ecx`** with the entry pointer left in **`esi`**.
Neither is `__cdecl`/`__stdcall`/`__thiscall`. Stack cleanup is also *merged*
(`add esp, 0x10` at `0x000A44F5` cleans four dwords pushed across two calls).

**Consequence for `docs/pnach-to-xbe-pipeline.md` §6.2:** the per-hook
`live_values` table cannot be inferred from a convention name in this module —
it must be read off each hook site individually, and a cave that assumes
`__cdecl` argument passing here will corrupt the stack silently. This does not
affect C1 (a branch edit touches no ABI).

### 7.7 Not yet located — the rest of the site set

**Hypothesis (unproven, recorded so it is not mistaken for a result):** source
order appears preserved in this module (PS2 `0x001F2B00` → `0x000A4260`,
`0x001F2CD8` → `0x000A43E0`, `0x001F2EA0` → `0x000A45E0` are monotone), which
suggests the remaining sites — N-1's hook `0x001F153C`, P11 `0x001F21E8`, P1's
hook `0x001F4A30`, P4 `0x001F6A74`, the pair scorer `0x001F4790`, `SetTarget`
`0x001F7398`, the engagement manager `0x001F7298` — lie in the Xbox band
roughly `0x000A0000`–`0x000A9000`. **The mapping is not linear and this is not
an address prediction.** Each still needs its own signature hunt. Note that
`IsRun` (PS2 `0x001F82E8` → Xbox `0x000A8F70`, already in
`docs/xbox-hook-map.md`) sits at the top of that band and is called at
`0x000A67F3`, which is consistent.

---

## 8. What would settle the one open thing

The polarity is settled (§4). The only unsettled claim is behavioural:

**Acceptance test, unchanged from PS2 C1.** Patch the XBE, FTP it to the
console, run a play where a lead blocker's nearest threat is a coverage
defender, and observe whether the blocker engages him. Pre-registered pass
condition: the blocker engages a defender whose `ai_state` is not in
{2, 30, 51} — visible as the pull path being kept instead of the blocker
running past the man. A stock XBE run on the same play is the control.

Per project rule 2, C1 is tested **alone** before it is combined with anything
else, and per `docs/pnach-to-xbe-pipeline.md` §9 it is phase **C2**, which should
follow phase **C1** (the 7-byte cheat-getter neuter) so that a failure separates
"the writer is wrong" from "the map is wrong".

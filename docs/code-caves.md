# Free-space survey: where injected code can live

Surveyed 2026-08-09 against `extract/SLUS_207.52` (Madden NFL 2004, PS2).
Nearly every gameplay fix this project has designed needs somewhere to
host injected code reachable from a pnach patch. This is that inventory,
plus a proven end-to-end example.

**Bottom line: caves exist and the mechanism works, but the budget is
small and it is all dead code, not padding.** ~9.2 KB / 2,312
instructions of provably zero-reference `.text`, plus ~260 bytes of true
linker padding. `.bss`, the `.data` zero-blocks, and `.text` alignment
padding are all dead ends. Caves should be **pooled and allocated
deliberately**, not claimed one-per-fix — the budget is fixed with no
growth path.

## What is not free (and why each looked like it might be)

| region | why it's rejected |
|---|---|
| 200 zero-runs totalling 153 KB in `.data`/`.rodata` | every one is interior to a live array — sparse tables and zeroed struct members. All 13 largest checked individually: each has 5–20 code-computed addresses and/or pointer words inside it. |
| `.text` alignment padding | 8,776 bytes exists, but as **956 crumbs** (max 48 B). The linker aligned sections to 64 and packed functions back-to-back. |
| `.bss` / `.sbss` beyond FileSiz | **fully allocated** — 1,986 distinct referenced addresses, the highest at `0x00659FD0`, twelve bytes from the end of MemSiz. |
| `.stack` (`0x0065A000`, 24 KB) | PROGBITS in the file but **outside PT_LOAD** — never loaded; that address range is the live runtime stack. |
| the 26 DVP overlay sections (~64 KB) | `vaddr 0`, outside PT_LOAD, VU memory — not EE-addressable. |

## What is free: zero-reference dead code

Method (worth reusing): split `.text` at every `jr ra`+delay-slot
boundary so no fragment can be entered by fall-through, then require that
**nothing** targets any word in the fragment — no `jal`, no `j`, no
branch of any form (including branch-likely, REGIMM, and COP branches)
from outside, no `lui`+`addiu`/`ori` pair anywhere materialising an
address inside it, and **no 32-bit word anywhere in the file** equal to
any address in it (the vtable / jump-table / function-pointer test).

Result: **86 zero-reference fragments → 56 regions, 9,248 bytes / 2,312
instructions**; 22 of them ≥ 128 bytes.

| # | vaddr | file | size | insns | what it is | risk |
|---|---|---|---|---|---|---|
| **1** | `00139A68` | `03AA68` | **456 B** | 114 | three dead block-copy leaves — **the only sizeable cave inside the gameplay address band** | low |
| 2 | `0044C1C0` | `34D1C0` | 640 B | 160 | dead libc varargs formatter | low |
| 3 | `0045F598` | `360598` | 624 B | 156 | dead libc wrappers | low |
| 4 | `004F4AA0` | `3F5AA0` | 608 B | 152 | dead string routines | low |
| 5 | `00447888` | `348888` | 600 B | 150 | dead libc | low |
| 6 | `0044BEB0` | `34CEB0` | 584 B | 146 | dead libc | low |
| 7 | `00443270` | `344270` | 480 B | 120 | byte-swap family (pure leaf, no stack frame — smallest blast radius) | low |
| 11 | `00514920` | `415920` | 96 B | 24 | **linker padding between `.vutext` and `.data` — owned by no object at all**, the lowest-risk region in the image | lowest |
| ⛔ | `002FB724` | `1FC724` | 424 B | 106 | **DO NOT USE** — zero refs, but the body is COP0 code (`di`, `mfc0/mtc0 $12`); plausibly relocated or installed as a handler at runtime | high |

**Reachability is a non-issue.** MIPS `J`/`JAL` reach the 256 MB region
sharing the top 4 bits of PC; every address in this image — and every fix
site — has top nibble 0. **Any site can `j`/`jal` any cave in one word,
no trampoline, no register clobber.** Distance only affects I-cache
locality (8 KB EE I-cache), which is why per-frame code should prefer
cave #1.

**Recommended allocation:** cave #1 for per-frame gameplay-tick fixes;
caves #2–#7 (the libc band, ~3.5 KB) for larger, colder bodies (a
`FindNearestPlayer` filter loop, the dedup check); cave #11 held in
reserve for anything that must be provably owned by nobody.

## Worked example: the lead-blocker minimum-steps gate

Proves the whole mechanism. **11 pnach lines, 10 cave words, exactly one
clobbered register (`at`).** Site `0x001EFA38` (the engagement kind
stamp); the gate holds kind at 1 until frames-since-snap ≥ 15 so a puller
runs his authored path instead of grabbing the first man he passes.

The one-word site patch is possible because of a control-flow proof:
nothing in the image branches into `0x001EFA30`–`0x001EFA50`, so the
value `v1 == 1` set in a plain (non-likely) delay slot is **guaranteed
live** at the stamp — the cave reuses it via `movn` instead of
materialising a constant.

```
// site: one word. Delay slot 0x001efa3c is unchanged and still runs.
patch=1,EE,001EFA38,word,0804E69A   // j 0x00139a68  (was: sw s2,992(s0))

// cave at 0x00139a68 -- 40 B used, 416 B left free at 0x00139a90
patch=1,EE,00139A68,word,24010004   // addiu at, zero, 4
patch=1,EE,00139A6C,word,16410005   // bne   s2, at, 0x00139a84  (kind != 4: pass through)
patch=1,EE,00139A70,word,00000000   // nop
patch=1,EE,00139A74,word,8F81BB90   // lw    at, -17520(gp)      ; [0x00601280]
patch=1,EE,00139A78,word,8C210054   // lw    at, 84(at)          ; frames since snap
patch=1,EE,00139A7C,word,2C21000F   // sltiu at, at, 15          ; THRESH
patch=1,EE,00139A80,word,0061900B   // movn  s2, v1, at          ; v1 == 1 here
patch=1,EE,00139A84,word,AE1203E0   // sw    s2, 992(s0)         ; displaced store
patch=1,EE,00139A88,word,0807BE90   // j     0x001efa40
patch=1,EE,00139A8C,word,00000000   // nop
```

Every word was hand-assembled and round-tripped through
`recon/mipsdis.py`. `THRESH` is the low half of `0x2C210000`; the engine's
own gates in this idiom use 20 and 46. `0x2C217FFF` disables engagement
entirely — useful as the "is the cave live?" probe.

**Why demote rather than skip:** jumping past the stamp would leave the
*reverse* pairing to fire, stamping the defender's side of an engagement
the blocker isn't in. Demoting to kind 1 routes through the engine's own
release-to-idle path, which zeroes the target handle and suppresses the
reverse pairing. Behaviour to watch: with old-kind == new-kind after
demotion, the manager re-enters the gate every frame, so the idle-release
runs once per frame during the window instead of once. It should be
idempotent; verify on the rig.

## pnach mechanics learned here

* `patch=1` is `PPT_CONTINUOUSLY` — re-applied **every vsync**, after the
  ELF load and after crt0 zeroes `.bss`. This is why `.bss` writes stick
  with `patch=1` and would *not* with `patch=0`.
* `patch=0` (`PPT_ONCE_ON_LOAD`) is applied at ELF-load time. Safe for
  `.text` cave bodies and cheaper — `patch=1` dirties the cave and site
  pages every frame, forcing recompilation. If frame pacing suffers, move
  cave bodies to `patch=0` and keep the site line at `patch=1`.
* Data-pool addresses are patched exactly like code addresses; both are
  just EE memory once mapped.

## Doc correction this survey produced

The four functions previously recorded in project docs as having zero
callers are **all referenced** and must not be used as caves:

| address | actually |
|---|---|
| `0x00153048` | reached by tail-call `j` from `0x0017992C` (already corrected in `play-tendency-ai.md`) |
| `0x0015F7E0` | reached by tail-call `j` from `0x0015FCBC` |
| `0x0016589C` | is *inside* the live `FindNearestPlayer 0x001657c0` |
| `0x00165E58` | referenced by a function-pointer word in `.data` at `0x00529154` |

**The lesson for future work:** a `jal`-only caller search is not a
liveness test. Tail-call `j`, mid-function addresses, and pointer tables
all evade it. That is why the survey's own method requires the file-wide
pointer-word scan.

## What cannot be settled statically

1. **Does anything overwrite `.text` at runtime?** No static evidence of
   an overlay loader or decompressor targeting EE code, but computed
   store bases cannot be exhaustively enumerated.
2. **Are the "dead" functions reached by an invisible path?** A computed
   `jalr` through a pointer never appearing as a literal, a kernel
   callback registered by handle, or an address arriving from a data file
   would evade every test run here. The tests are strong but static.
3. **I-cache coherency on real hardware** would need a `FlushCache`;
   PCSX2's recompiler invalidates on write, so emulation is fine.

## Runtime verification plan (rig)

Observe the H-2 live-session check before any emulator action.

| # | test | pass condition |
|---|---|---|
| **1** | **Is the cave really dead?** *(unpatched, do this first)* Execute-breakpoints at `0x00139A68`, `0x00139AA0`, `0x00139B50`, `0x00139C28`; play boot → menus → roster/franchise → a full quarter → replay → halftime → save/load | never trips |
| 2 | Does the cave survive? Read `0x00139A68` via PINE at kickoff, mid-drive, after halftime/load | always `0x24010004` |
| 3 | Is it wired up? Set `0x00139A7C` to `0x2C217FFF` (gate never lapses), run a sweep | run blocking visibly stops; revert after |
| 4 | Does the gate work? Watch `[[0x00601280]+84]` against the puller's `player+0x3E0` on a sweep | `+0x3E0` reads 1 for frames 0–14, may reach 4 from frame 15; the guard keeps running his pull path throughout |
| 5 | Regression: comment out the site line | stock behaviour returns |

**Test 1 gates all cave work, per cave — not once for the survey.** Until
a region passes it, treat it as unproven.

# DT-3 fact-check, lane 1 — the two arms of `0x001f4ae8`

Static only, against `extract/SLUS_207.52` (vaddr = file_offset + 0xFF000,
gp = 0x006056F0), via `recon.mipsdis`. Every instruction below was read from
the binary this pass. No rig, no network, no emulator. Nothing is carried over
from another document without re-derivation; where another document is used as
evidence it is cited as such and its grade is stated.

**VERDICT: PATCH-CORRECT-DIRECTION — but MISTARGETED for the symptom it claims
to fix, and its stated rationale is wrong in two places.** The `nop` does what
the patch author intended (it removes an exclusion, it is not inverted, it does
not obviously crash). It is also, on the evidence below, a **no-op on both
savestates in its own acceptance plan**, so the test as written cannot pass or
fail it.

---

## 1. The fall-through arm (0x001f4af0 …) — it IS helper assignment

Quoted from the binary:

```
001f4adc  0c056b68  jal 0x0015ada0
001f4ae0  00000000  nop
001f4ae4  24030002  addiu v1, zero, 2
001f4ae8  10430046  beq v0, v1, 0x001f4c04      ; <- the patched word
001f4aec  0000902d  daddu s2, zero, zero        ; delay slot: player index := 0
001f4af0  241e0003  addiu fp, zero, 3
001f4af4  24170006  addiu s7, zero, 6
001f4af8  24150005  addiu s5, zero, 5
001f4afc  8fa40038  lw a0, 56(sp)
001f4b00  0c05956c  jal 0x001655b0              ; GetPlayer(ctx, s2)
001f4b04  0240282d  daddu a1, s2, zero
001f4b08  0040882d  daddu s1, v0, zero
001f4b0c  24040004  addiu a0, zero, 4
001f4b10  8e2203e0  lw v0, 992(s1)              ; s1->+0x3E0 engagement kind
001f4b14  10440009  beq v0, a0, 0x001f4b3c      ; kind == 4 ?
001f4b18  24050002  addiu a1, zero, 2
001f4b1c  10450008  beq v0, a1, 0x001f4b40      ; kind == 2 ?
001f4b20  262403e4  addiu a0, s1, 996           ; (delay, always) a0 = s1+0x3E4
001f4b24  105e0006  beq v0, fp, 0x001f4b40      ; kind == 3 ?
001f4b2c  10570004  beq v0, s7, 0x001f4b40      ; kind == 6 ?
001f4b34  5455002f  bnel v0, s5, 0x001f4bf4     ; kind != 5 -> next player
001f4b38  26520001  addiu s2, s2, 1
```

**First correction to the pnach.** The comment says the fall-through's "first
instructions load the two-man-animation engagement kinds (3/6/5)". They do not.
`fp`/`s7`/`s5` are **comparison operands**, consumed three instructions later by
`beq v0, fp` / `beq v0, s7` / `bnel v0, s5`. There is no store of 3, 6 or 5
anywhere in this block. What the constants actually express is an **accept
filter on the player's current `+0x3E0`**: the block considers a player only if
his engagement kind ∈ {2, 3, 4, 5, 6} — i.e. anyone already assigned to,
approaching, touching, or animating with a defender. 5 and 6 appear here as
*inputs* (a man inside a two-man animation is still a legal man to help), not as
outputs. The claim "loads the kinds" and the claim "this is the helper block"
happen to point at the same code, but only the second one is true, and the doc
should not keep the first as its supporting argument.

The block's actual work, continuing:

```
001f4b40  0c04ede6  jal 0x0013b798              ; resolve s1->+0x3E4 -> s3 = the man he is on
001f4b44  02c0802d  daddu s0, s6, zero          ; s0 = candidate list base (s6 = fn arg)
001f4b48  3c013f80  lui at, 0x3f80              ; f20 = 1.0f  (score floor)
001f4b4c  4481a000  mtc1 at, f20
001f4b50  0040982d  daddu s3, v0, zero
001f4b54  8e02005c  lw v0, 92(s0)               ; list is NUL-terminated at +92
001f4b58  10400018  beq v0, zero, 0x001f4bbc    ; empty list -> next player
001f4b64  8e04005c  lw a0, 92(s0)               ; candidate blocker
001f4b6c  0c07d310  jal 0x001f4c40              ; HELPER SCORER (block-cycle.md's dt geometry fn)
001f4b70  0260302d  daddu a2, s3, zero          ;   a2 = the defender to be doubled
001f4b78  4601a034  c.lt.s f20, f1              ; keep the best score > 1.0
001f4b9c  46000d06  mov.s f20, f1
001f4ba0  0200a02d  daddu s4, s0, zero          ; s4 = best candidate entry
...
001f4bc0  5280000d  beql s4, zero, 0x001f4bf8   ; no candidate -> next player
001f4bc8  c6800064  lwc1 f0, 100(s4)            ; candidate's OWN assignment score
001f4bcc  46140034  c.lt.s f0, f20              ; only if helping beats his own man
001f4bd4  45000007  bc1f 0x001f4bf4
001f4bdc  e6940064  swc1 f20, 100(s4)           ; overwrite his score
001f4be0  8e84005c  lw a0, 92(s4)
001f4be4  0c07dce6  jal 0x001f7398              ; SetEngagement(blocker, defender, kind)
001f4be8  24060007  addiu a2, zero, 7           ; ***  KIND 7 = second man on a double  ***
```

`0x001f7398` is the kind-setter named in `double-team-mechanism.md` §1/§3, and
`a2 = 7` is `addresses.yaml`'s `engagement_kind` 7, "assigned as the second man
on a double team". So the fall-through arm **is** the helper-assignment pass —
this half of the patch note is right, and it matches `block-cycle.md`'s
"`0x001f4790` phase 4 … assign the best as kind 7, but only if helping beats
that blocker's own 1-on-1 assignment", which is the `c.lt.s f0, f20` at
`0x001f4bcc`.

Every store and call in the block, exhaustively: `jal 0x001655b0` (GetPlayer,
11×), `jal 0x0013b798` (handle resolve), `jal 0x001f4c40` (scorer, read-only as
far as this block is concerned — not audited here), `swc1 f20, 100(s4)` (writes
the caller's scratch list), and `jal 0x001f7398, a2=7`. **No write to any
double-team registry, no `+0x436`/`+0x437` byte, no call to `0x001f74c8`.** The
registry is not touched here at all; it is touched later in the frame by
`0x001f6d10`.

## 2. The taken arm (0x001f4c04) — it is the epilogue. Not backwards.

```
001f4c04  dfbf00e0  ld ra, 224(sp)
001f4c08  dfbe00d0  ld fp, 208(sp)
...
001f4c34  03e00008  jr ra
001f4c38  27bd0100  addiu sp, sp, 256
```

`0x001f4c04` is register restore + `jr ra`. There is no helper assignment on
the taken side, no alternate block, nothing at all: on play type 2 the function
**returns**. The "patch is exactly backwards" hypothesis is **refuted**.
(Independently reached by `review-2026-08-11.md` D12, which also warns the prose
in `double-team-plan.md:33` — "gated on play type 2" — reads as *requires* type
2 and would invert the patch if anyone "fixed" the patch to match it. That
warning is correct and still unactioned.)

**Second correction to the pnach.** Its comment says that on a type-2 play "the
registry forms but the second man is never ASSIGNED". That is not what the code
does. Registration (`0x001f64e0` seek filter) requires the seeker's own kind ∈
{7, 8}:

```
001f6568  8c8203e0  lw v0, 992(a0)
001f656c  2442fff9  addiu v0, v0, -7
001f6570  2c420002  sltiu v0, v0, 2             ; kind ∈ {7,8}
001f6574  10400004  beq v0, zero, 0x001f6588
001f657c  0c07d8ce  jal 0x001f6338              ; register
```

and `0x001f4be4` is the **only bootstrap of kind 7 in the image**. Census
method: every store to `+0x3E0` in the whole ELF (`find_immediate_all(992)`
filtered to stores) is `0x001e81ec` (state-32 animation), `0x001efa38`,
`0x001efab8`, `0x001f5cf4`, `0x001f73c4` (= `0x001f7398`), `0x001f7448`,
`0x001f74fc` (= `0x001f74c8`), `0x001f7544`; the rest are `sd rX, 992(sp)`
frame saves. Then every `jal` to the two setters with its `a2`: `a2 = 7` occurs
at exactly three sites — `0x001f4be4` (here), `0x001f6424` (inside register fn
`0x001f6338`, whose sole caller is the kind∈{7,8} filter above), and
`0x001f68b4` (inside manage fn `0x001f6640`, which walks existing records). The
two computed-`a2` sites `0x001f5368`/`0x001f539c` are in `0x001f5158`, whose
kind-7 arms are entered only by `beq s1, 7` / `beq s2, 7` on an *existing* kind
(`0x001f5264`, `0x001f5288`) — it swaps which of a pair is the helper, it cannot
create one. `0x001efa38` can write 7 only from the current-kind-8 table arm
(`0x001ef9b0-b4`), and `0x001f5cf4` writes 2. **So with the branch taken, a
type-2 play cannot form a double-team record at all** — the pnach's "the
registry forms" is false, and the mechanism it blames ("he has nothing to
execute at the touch") is not what this branch causes.

## 3. Containing function, callers, cadence

Prologue `0x001f4790 addiu sp, sp, -256`, `0x001f4798 daddu s6, a0, zero`;
epilogue `0x001f4c04-0x001f4c38`. Caller scan run against the padded address as
well as the real one (`double-team-mechanism.md`'s +4-nop trap):
`find_jal_targets(0x001f478c) = []`, `find_jal_targets(0x001f4794) = []`,
`find_jal_targets(0x001f4790) = [0x001f55c8]`. Single caller, and it is
unconditional in its block:

```
001f55c8  0c07d1e4  jal 0x001f4790
001f55cc  03a0282d  daddu a1, sp, zero
```

inside `0x001f5590`, whose sole caller is `0x001f72d0` — the per-frame block
manager `0x001f7298`, slot 2 of ten. **This runs every frame of every play**
(the manager itself is gated on `0x00154790() ∈ {3,4}`, meaning unverified).
It is not registration-time code.

## 4. Safety of the `nop`

Registers the fall-through consumes, and where each is established:

| reg | set at | on the type-2 path too? |
|---|---|---|
| `s2` (player index) | `0x001f4aec`, the branch's own delay slot | **yes** — ordinary `beq`, delay slot always executes. The pnach's "safe either way" note is correct |
| `s6` (candidate list base) | `0x001f4798`, prologue, `= a0` | yes, unconditional |
| `56(sp)` (GetPlayer ctx) | `0x001f482c sw v0, 56(sp)` from `jal 0x001f82b8` at `0x001f4828` | yes — it precedes the function's first conditional branch (`0x001f4838`) |
| `fp`/`s7`/`s5` | `0x001f4af0-f8`, after the branch | yes, they are written by the block itself; all three are `sd`-saved at `0x001f47a0-b0` and restored at `0x001f4c08-18`, so clobbering them on a type-2 play is contained |
| `f20` | `0x001f4b4c`, inside the block | yes; `swc1 f20, 240(sp)` / `lwc1` pair brackets the function |

Guards the block **has**: NUL-terminated list test `beq v0, zero` at
`0x001f4b58` (empty list → next player, so an empty candidate array is safe);
`beql s4, zero` at `0x001f4bc0` (no best candidate → skip); score floor 1.0f at
`0x001f4b48`; and the "helping must beat his own assignment" test at
`0x001f4bcc`.

Guards it **lacks**, both quoted as absences:

* `0x001f4b08 daddu s1, v0, zero` → `0x001f4b10 lw v0, 992(s1)` — **no null
  test** on the `GetPlayer` return before a `+0x3E0` dereference. Mitigation
  (not proof): the identical unguarded pattern is used by the DT seek filter
  (`0x001f6540` → `0x001f654c addiu v1, a0, 992`) and by the per-play reset
  `0x001f6ff0`, so the engine treats "11 offence slots always resolve" as an
  invariant. Whether that invariant holds on a type-2 play is **unverified**.
* `0x001f4b40 jal 0x0013b798` → `s3`, passed straight to the scorer as `a2` at
  `0x001f4b70` with **no null test**, where the DT manage fn does test the same
  resolve (`0x001f6704 beq a0, zero, 0x001f6738`). The kind filter is what
  stands in for the guard: kinds 2-6 are supposed to imply a live `+0x3E4`,
  written by `0x001f7398` at `0x001f73dc`. Whether `0x001f4c40` dereferences
  `a2` before checking it was **not** traced in this lane — that is the one
  crash path worth closing before the next deployment, and it is cheap
  (disassemble `0x001f4c40`'s first 20 instructions).

New side effects on a type-2 play, beyond the intended kind-7 write:
`swc1 f20, 100(s4)` mutates the caller's scratch score list, which the
subsequent pass `0x001f5158` reads in the same frame; and a blocker flipped to
kind 7 is a blocker taken off his own assignment. Both are inherent to the
feature, not bugs, but they are the blast radius and neither is mentioned in
the pnach.

## 5. Does the fall-through produce the kinds-5/6 two-man blocks? No — and the
patch is a no-op on both of its own test states

It produces **kind 7**, not 5/6. Kinds 5 and 6 are owned by AI state 32
(`addresses.yaml:1011`, "scripted two-man block animation, owns engagement kinds
5 and 6"); the only writer of 5/6 into `+0x3E0` in the store census above is
`0x001e81ec`, which is in the state-32 region, not here. The chain
kind 7 → registration → kind 8 is the double-team system; the 5/6 animation is a
different machine that this block only *reads* (as a filter operand). So "the
second man has no animation to execute" is not repaired by this word: nothing in
either arm dispatches a two-man animation.

**The decisive check.** `0x001f4be4` is the sole bootstrap of kind 7 (§2), and
kind 7 is a precondition for any `dt_role` 0/1/2 (those three bytes are written
only at `0x001f6490/94/9c` inside register fn `0x001f6338` —
`double-team-mechanism.md` §3 writer census, re-confirmed here by the single
caller `0x001f657c`). Therefore *any* live sighting of kind 7, or of dt_role
0/1/2, proves the branch was **not taken** on that play. Two exist in this
project's own measurements:

* slot 9 (run, lead dive): `double-team-requirements.md:311-317` tabulates
  RT `role 0,1`, TE `role 0,1`, RG `role 0`, DE `role 2`, LB `role 2`;
* slot 7 (pass protection): `dt-hold-90-review/4-pass-blast-radius.md:326`
  reports the RG oscillating 7↔8 for 130+ frames.

So on the acceptance state (slot 9) **and** the regression state (slot 7),
`0x0015ada0()` already returns something other than 2 and the patched word never
executed as a taken branch. The pnach's acceptance criteria — "dt windows extend
past 43 / the RG stays engaged after the touch" — cannot be moved by this
change on that state. Expect a frame-identical null result, exactly like
DT-HOLD-90 and for the same class of reason: correct code, wrong play.

### What `0x0015ada0` actually returns (re-derived, since the enum decides everything)

`0x0015ada0` → `0x00154790()`; if 3 → `0x001485d8()`, else → `0x0015ade0` →
`0x0015b418` (loop `GetPlayer(side, 0..N)`, return the first player whose
classification is non-zero) → `0x0015aeb8`, which resolves that player's
authored state chain (`0x00248360(side)` → `0x00243c98(blob, side, idx)`, the
4-byte `{id,p1,p2,p3}` records of `fb-wr-blocking.md`) and maps:

```
0015aef0  lbu v0, 0(s0)        ; chain[0].id
0015aefc  and v0, v0, s1       ; s1 = ~0x80, strip the "more follow" bit
0015af08  movn a3, zero, v1    ; id != 0x37 -> 0
0015af10  movz a3, a1, a0      ; id == 0x36 -> 2      (id == 0x37 -> 3)
0015af30  movz a3, v0, v1      ; id == 0x2D -> 1, or 5 if [0x00260a38()+25] == -2
0015af34  lbu v1, 4(s0)        ; chain[1].id
0015af50  movn v0, a3, v1      ; chain[1].id == 0x2D -> 4, overriding
```

So the return is a **6-valued classification of the called play** (0..5),
stable for the play, consistent with the four 6-entry jump tables that index it
(`0x0057b1b0`, `0x0057cd70`, `0x0057cf10`, `0x0057cf50`). Two facts about the
enum are established: `0x0015ae40`'s table groups **{2,3} against {0,1,4,5}** —
2 and 3 are the pair that own a ball-carrier play record — and the jam-eligibility
helper requires **3** (`0x001b9394-98 addiu v1, zero, 3; bne v0, v1, exit`),
which `addresses.yaml` reads as "3 = pass". **Which play class is 2 is
UNVERIFIED.** It is not slot 9's run and not slot 7's pass (both proven above by
the presence of kind 7), so the label "run play" that a reader would naturally
attach to it is *wrong or at least incomplete*. Also resolved in passing:
`review-2026-08-11.md`'s open question "is `0x0015ada0`'s return the same enum as
`block_mode`" — **no**. `block_mode` is a per-player word at `+0x3F0` written by
`SetBlockMode 0x001F7568`; this is a per-play value derived from authored chain
bytes. DT-3 is not DT-1.

## 6. What lane 1 could not close

1. **Which play class value 2 is.** Needs the play blob read (`play-data.md`) or
   one live read of `0x0015ada0`'s return per savestate. Until then DT-3's
   *value* is unknown even though its *direction* is right.
2. **Whether `0x001f4c40` dereferences a null `a2`.** The one plausible crash
   path opened by the `nop`; not traced here.
3. **Whether `GetPlayer(56(sp), 0..10)` can return null on a type-2 play.**
   Engine-wide invariant assumed, not proven.
4. **What `0x001f5510` puts in the 112-byte candidate list** and therefore what
   `swc1 f20, 100(s4)` perturbs downstream in `0x001f5158`.
5. **`0x00154790()`'s mode values** — the manager's outer gate, still unlabelled
   (same open item as `double-team-mechanism.md` §5.9).

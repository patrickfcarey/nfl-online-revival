# A per-frame host for the drive cave — lane 1, static

Investigated 2026-08-12 against `extract/SLUS_207.52` (vaddr = file offset +
0xFF000, gp = 0x006056F0) with `recon.mipsdis`, and against
`experiments/states/ball_in_air_slot8.p2s` / `double_team_slot9.p2s` with
`tools/statereader.py`. **Every instruction quoted below was dumped from the
image this pass; every live value was read from a savestate this pass** (rule
4). No rig, no network, no emulator, no commits. Residue is in §8, marked, not
assumed.

**The question:** the 32-word cave fires ~5×/play from two different hosts
(Site B `0x001F2164`, sweep `0x001F2070`) while its gate conditions hold for
35–56 harness frames. Find a host that executes **every frame** while the
blocking pair is engaged, proven by call chain from the frame tick.

**The answer:** hook the **state-32 ai_think `0x001E8088` at `0x001E80C0`**.
It is dispatched once per sim frame for *each member of a captured pair* by
the gate-free AI dispatcher `0x001AF9D0` (chain quoted end-to-end in §1–§3),
it holds **both pair members in registers** (s0 = member, s1 = partner,
already resolved) at a one-word hook site with no branch above it, and its
gate — the AI state byte `+0xBCC == 32` — is written only by the state-machine
transition path, unlike the engagement kinds that starved both previous
hosts, which are rewritten inside the block manager itself every frame (§5).
Runner-up with even wider coverage but no pair register: the locomotion
service loop `0x002512B8` (§4.2).

---

## 1. The frame spine, walked from the main loop down

Every link below was re-derived this pass (`find_jal_targets`, plus j-target
and data-word scans for the two links that have no jal callers). Single
caller at every level unless stated.

```
0x00103A88   game frame fn (5 jal sites 0x001037CC..EC in the outer driver)
  00103b70  jal 0x0039db10          ; pending sim ticks n (drains [0x00607FFC]+12)
  00103b7c  bne s0, zero, 0x00103b30    ; while n != 0:
  00103b38  mtc1 s0, f12 / cvt.s.w      ;   f12 = n (ticks, as float)
  00103b60  jal 0x00154660              ;   SIM STEP (once per drain)
0x00154660   sim step
  00154670  lbu v0, 8(v1)           ; [0x00600CE4]+8: replay active?
  00154674  beq v0, zero, 0x0015469c    ; == 0 -> live path
  001546c4  beq v0, zero, .. / jalr v0  ; per-mode handler from table 0x0058C860
  001546e0  j   0x001540d8          ; -> sim body, unconditional on live path
0x001540D8   sim body
  001540e8  jal 0x0011f170          ; pause check on [0x00600B0C]
  001540f0  beq v0, zero, 0x00154114    ; not paused -> full sim
  00154114  jal 0x00154178          ; wrapper 1: AI + gameplay   <- this lane
  0015411c  jal 0x001543e0          ; wrapper 2
  00154124  jal 0x00154238          ; wrapper 3: locomotion      <- §4.2
0x00154178   wrapper 1
  00154184  jal 0x00183410
  0015418c  jal 0x00164ec0          ; THE GAMEPLAY TICK, unconditional
0x00164EC0   gameplay tick
  00164ec4  lw v0, -18600(gp)       ; [0x00600E48] player array descriptor
  00164ef0  lhu s4, 10(v0)          ; player count (22 in both savestates)
  00164f14  beq s4, zero, 0x00164fb4    ; loop over ALL players, stride 0x14C0:
  00164f30  addu s0, v1, s3         ;   s0 = player
  00164f40  jal 0x0016d0b0(player)
  00164f70  jal 0x00188958(player)
  00164f88  jal 0x001af9d0          ;   AI STATE DISPATCH  <- §2
  00164f8c  addiu a1, s0, 3020      ;   (ds) a0=0, a1=&player+0xBCC, a2=player, a3=valid
  00164f90  jal 0x0014eb60(player)
  00164f98  jal 0x00164820(player)  ;   timed re-dispatch (+0xBC4/+0xBC8 request path)
  00164fa4  jal 0x00164bf8(player, valid)
  00164fac  bne s2, zero, 0x00164f28    ; next player
  00164fb4  jal 0x00200040 / 00188030
  00164fc4  jal 0x001f7298          ; block manager: ONCE per tick, after the loop
```

Gates on the whole spine: replay byte `[0x00600CE4]+8`, pause
`0x0011F170([0x00600B0C])`, and — for the block manager only — play mode
`0x00154790() ∈ {3,4}` (`0x001f72a0-0x001f72c0`). **There is no think
cadence, no player time-slice, no 1-in-N divider anywhere on this spine.**
Everything above runs once per sim step; the sim steps once per pending-tick
drain (n ≥ 1, f12 carries n so a lagging frame is simulated once with a
bigger dt, not re-run).

## 2. The AI dispatcher `0x001AF9D0` — gate-free, both thinks, every call

Called at `0x00164F88` for every player every tick. Quoted in full relevant
part:

```
001afa00  beq a3, zero, 0x001afa80  ; a3=0 skips USER think only
001afa08  sb zero, -17732(gp)       ; [0x006011AC] "state changed" latch := 0
001afa10  lw v0, -17740(gp)         ; [0x006011A4] machine array
001afa18  lbu a1, 0(s0)             ; CURRENT state byte (player+0xBCC)
001afa20  mult a1, a1, v1           ; state * 24
001afa24  lw v0, 0(a2)              ; machine[0] object
001afa28  lw v1, 4(v0)              ; object+4 = TABLE BASE
001afa2c  addu a1, a1, v1           ; row = table + state*24
001afa30  lw v0, 12(a1)             ; row+12 = user_think
001afa34  jalr v0                   ; user_think(player)
  ...transition dance only if it returned 1...
001afa80  bne s4, zero, exit        ; (s4=1 only if user think transitioned)
001afa88..001afaac                  ; same row computation again
001afab0  lw v0, 8(a1)              ; row+8 = AI_THINK
001afab4  jalr v0                   ; ai_think(player)   <- EVERY call
```

The **ai_think of the current state runs on every dispatcher call** — the
only skip is the frame a transition was just requested. The state byte it
dispatches on is `player+0xBCC`; `player+0x2FC` is literally
`&(player+0xBCC)` (verified live: `0x0066d650+0xBCC = 0x0066e21c =
[+0x2FC]`).

**Live verification of every pointer in the chain** (both savestates,
`tools/statereader.py`):

| value | ball_in_air_slot8 | double_team_slot9 |
|---|---|---|
| `[0x006011A4]` machine array | 0x00FCA790 | 0x00F991E0 |
| machine[0] object | **0x006012D0** (the static record) | same |
| object+4 (table) | **0x00527238** — the state table of `state-dispatch-table.md` | same |
| row 32 = 0x00527538: ai_think (+8) | 0x001E8088 (ELF: table word 0x00527540) | same |

The **only** reference to `0x001E8088` in the entire image is that table word
(data scan: `[0x527540]`; jal/j/branch scans: none) — the think executes
exclusively via this dispatcher, so its prologue premises hold on every
execution.

## 3. Candidate 1 (CHOSEN): state-32 ai_think `0x001E8088`, hook `0x001E80C0`

State 32 is the scripted two-man block animation and owns engagement kinds
5/6 (`block-cycle.md`, re-confirmed: the only 5→6 kind write in the image,
`sw v0, 992(s0)` at `0x001E81EC`, is inside this think). **Live proof of
membership** (ball_in_air_slot8, the mid-play kind-6 pair): OL 0:9 and DE 1:3
**both hold `+0xBCC = 32`** while kind = 6, and their `+0x150` handles are
mutual and equal to `+0x3E4` (0x00030101 ↔ 0x00090001 = each other). So for
the entire captured window — the 161-clip window the drive must fill — **each
pair member's think runs once per sim frame**, defender included.

### 3.1 The head, and the hook word

```
001e8088  addiu sp, sp, -96         ; prologue: s0-s4 + ra all saved by 0x001e80b4
001e8090  daddu s0, a0, zero        ; s0 = PLAYER (this member)
001e8098  addiu s2, s0, 336         ; s2 = player+0x150 (interaction block)
001e80b0  jal 0x0013b798            ; resolve handle at +0x150
001e80b4  sd s3, 48(sp)             ; (ds)
001e80b8  addiu a0, zero, 1
001e80bc  daddu s1, v0, zero        ; s1 = PARTNER (other pair member, or 0)
001e80c0  sb a0, 10(s2)             ; +0x15A := 1   <<< THE HOOK WORD (0xA244000A)
001e80c4  addiu v1, zero, 6
001e80c8  lw v0, 992(s0)            ; kind
001e80cc  beql v0, v1, +8: sb a0, 8(s2)
001e80d4  jal 0x001e7df8(s0)        ; per-frame pair service (advances the anim side)
...
001e80f8  beq s1, zero, 0x001e8138  ; the think's own null-partner guard
001e8120  jal 0x00213628(s0,s1) / (s1,s0)  ; re-registers mutual no-collide EVERY frame
001e81dc..001e81f0                  ; kind 5 -> 6 promote
```

There is **no branch of any kind between the prologue and `0x001E80C0`**, and
a full-image census (all j/jal/beq..bgtz/likely/REGIMM/bc1x forms) finds
**zero external jumps into `0x001E8088..0x001E80D8`** — the hook word is
reached only through the prologue, on every execution.

### 3.2 Site contract at `0x001E80C0`

* Patch shape: `0x001E80C0: 0xA244000A → jal <cave>`; cave executes the
  displaced `sb a0, 10(s2)` first, returns to `0x001E80C4`.
* Stock word verified 0xA244000A in the ELF **and in both savestates** this
  pass.
* **Live and must be preserved:** s0 = this member, s1 = partner (0 if none —
  cave must gate `s1 != 0` itself; the think's own guard is below the hook),
  s2 = member+0x150, s4 = 0, gp/sp/fp, and **a0 must equal 1 on return** (the
  beql delay slot at `0x001E80D0` stores it; one `addiu a0, zero, 1` in the
  cave exit restores it).
* **Dead, freely clobberable:** at, v0 (its partner value is already copied
  to s1; next def `0x001E80C8`), v1, a1–a3, t0–t9, hi/lo (no mult/div in the
  fn; the dispatcher recomputes its own), **every FPU register and the FPU
  condition flag** (the fn touches only f0, freshly set at each use), ra
  (saved at `0x001E80A8`, restored in the epilogue).
* Fires once per member per frame → **twice per pair**. Gate on
  `lbu s0+0x437 == 2` (defender dt_role) to fire exactly once per pair per
  frame, from the defender's own think, with s1 = the primary blocker
  already resolved. The helper (third body, not a state-32 participant) is
  reachable via the DT record: `lbu s0+0x436` slot, `T = lw -17520(gp)`,
  `R = T+4+20*slot`, helper handle at R+4 → `0x0013B798` — the registry
  fields re-verified against `manage-fn-annotated.md` §1 this pass.
* Blast radius: the think executes only for players inside two-man block
  animations — every other player, every other state, is untouched code.

### 3.3 Coverage arithmetic (from the project's own measurements)

Defender state-32 residency = his kinds-5/6 window (79 harness frames in
4/5/6; 5/6 entered at ~f43 per the A2 series). Role bytes live as long as the
record (window 2..57–64 stock). **With the companion yes-set word
(`0x0058339C`, arm B) already specified in `motion-block-cave.md` §7, the
full 37-frame 161 clip is inside both gates** → at the measured step
0.038–0.045 yd/f: **1.4–1.7 yd**, through R3's ≥ 1.0. Without arm B the
record dies ~21 frames into the clip → 0.6–0.9 yd. Arm B stays load-bearing.

## 4. The other per-frame candidates, ranked

### 4.1 Ranking table

| # | host | hook | cadence, proven how | pair regs | fires while |
|---|---|---|---|---|---|
| **1** | **state-32 ai_think `0x001E8088`** | **`0x001E80C0`** | 1× per sim frame per captured member — §1–§3 chain, zero gates, live-verified membership | **s0 + s1 both** | pair captured (5/6) = the clip window |
| 2 | locomotion service `0x002512B8` | at `0x002512DC` (`jal 0x00250e10` word) | 1× per sim frame per player, **no state/kind gate at all** — §4.2 chain | s0 = player only; partner via +0x3E4/record | always, all 22 players |
| 3 | tick-loop dispatch site `0x00164F88` | the `jal 0x001af9d0` word | same guarantee class as #2 | s0 = player; a0–a3 live (must preserve) | always, all 22 players |
| 4 | block-manager slot word (e.g. `0x001F733C`) | replace a slot jal | 1× per manager frame (mode 3/4) | none — cave must loop players | play modes 3/4 |
| 5 | Site B `0x001F20F8` kind-8 arm | `0x001F2164` | per manager frame **∧ kind==8 at pass 9** — starved in practice (§5.2) | s0 helper, s2 defender | kind-8-at-pass-9 frames only |
| 6 | sweep `0x001F1C20` interior | `0x001F2070` | per manager frame **∧ blocker kind==4** — ≈5 f/play, statically explained (§5.1) | s1/s2 both | kind-4 (pre-capture) only |
| 7 | root-motion applier `0x0018F980` (host `0x0018FBE8` phase 2) | — | **never executes for pair clips** — opcode-38 burst never armed; callers re-verified this pass (applier ← `0x0018FCE8` only, converter ← `0x0018FCC4` only); live disproof in `motion-block-cave.md` §1.3 stands | — | never |

### 4.2 Candidate 2 in full: the locomotion service loop

Chain, all single-caller, re-derived: sim body `0x001540D8` →
`jal 0x00154238` (at `0x00154124`) → `jal 0x00165170` (at `0x00154244`,
first call, unconditional) →

```
00165194  lw v0, -18600(gp)         ; player descriptor
00165198  lhu s3, 10(v0)            ; count = 22
001651b4  addu s0, v0, s2           ; s0 = player (stride 0x14C0, ALL players)
001651bc  jal 0x002512b8(player)    ; locomotion service
001651c4  jal 0x00164b58(player)
001651cc  jal 0x001603c0(&pos)
001651d4  jal 0x001837b0(player)
001651dc  jal 0x0018fd50(player)
001651e4  bne s1, zero, 0x001651a8  ; next player
```

and inside `0x002512B8`: `jal 0x00160000(&pos)` then **`jal 0x00250e10`
(player) at `0x002512DC` — unconditional, before the +0x204 mode jump table
(0x00587110)**. A cave replacing the `0x002512DC` word (do gated work off
s0 = player, preserve a0, then `j 0x00250e10` tail) runs for every player
every sim frame with **zero** dependence on engagement kinds, AI states, or
play mode — the maximum-coverage host (the full 56-frame role-pair window,
kind-4 frames included). Registers at that site: s0 = player, s1 =
player+0x190; s2 not yet live; ra saved. Cost vs #1: the partner is not in a
register (resolve `+0x3E4`/record in-cave), and the hook sits in a loop that
runs 22× per frame for the whole game — widest blast radius of the viable
hosts. It runs in wrapper 3, i.e. **after** the block manager updated kinds
that frame, so kind/role gates read post-manager (= end-of-frame) values —
the same values the harness samples.

## 5. Why the two failed hosts starved — now statically explained

### 5.1 The P9 host (`0x001F2070`): the sweep only processes kind-4 players

Re-derived from `0x001F1C20`'s head this pass (single caller `0x001F7344`):

```
001f1c90  addiu v1, zero, 4
001f1c94  lw v0, 992(s1)            ; each offense player's kind
001f1c98  bne v0, v1, 0x001f20b4    ; != 4 -> next player
```

Everything at `0x001F2054–0x001F2084` (the copier where P9 hooked) is inside
this filter. A captured blocker is kind 5/6, not 4 — so the host fires only
during the ~5-frame contact window before capture. **The ~5/play count is
the blocker's kind-4 residency, exactly.** No gate widening inside the cave
could ever change it.

### 5.2 The Site B host (`0x001F2164`): kind 8 is re-decided every frame by the maintain fn — and the 5-vs-35 residue is NOT "the manager skips frames"

The servicer's kind-8 arm has **no internal frame gate**: from
`bne v0, v1, 0x001f2234` (kind != 8 → next player) at `0x001F2150` straight
through the freeze at `0x001F2164` — and the manager calls the servicer
unconditionally in mode 3/4 (`0x001F733C`, both parity arms converge there).
So the arm runs on every manager frame in which the helper's kind reads 8 at
pass 9. What decides that: pass 4 (`0x001EF820`, slot `0x001F72E0`, **before**
the servicer) calls the maintain fn per player and dispatches:

```
001ef880  jal 0x001f7590            ; maintain (resolves helper->defender->primary;
                                    ;   primary kind==4 -> distance test; else 0x001f0ba8 decides)
001ef888  beq v0, zero, 0x001ef950  ; FAIL -> kind jump table 0x00583340:
001ef9b0/b4:  s2 := 7               ;   kind-8 failure arm = DEMOTE to 7
001ef938/3c:  jal 0x001f5d90; s2 := 8   ; SUCCESS arm (kinds 7/8) = hold/promote 8
001efa2c  beq a1, s2 / 001efa38 sw s2, 992(s0)  ; store only on change
```

Nothing that runs after the servicer writes the helper's kind: the sweep
(pass 10) processes kind-4 players only (§5.1 filter), and the manager tail
`0x001F5E80` was scanned this pass — **zero `sw …,992()` stores and zero
calls to the kind setters** in its body (its deeper callees unwalked, noted
in §8). So **kind-8-at-pass-9 equals kind-8-at-end-of-frame** — the value
the harness samples. The measured 35
end-of-frame kind-8 frames vs 5 cave hits therefore cannot be explained by
manager cadence (and the record-window agreement — role-pair 56 harness
frames ≈ record window 2..57–64 manager ticks — pins harness sampling ≈ 1:1
with sim frames, killing the oversampling reading). What remains is §8.2:
either the two cave gates the harness never counted (link-handle kind, D > 0
with run-play margin fields) rejected 30 of 35 frames, or the A2 kind series
itself needs re-reading. The P8b-proposed three-canary run (entry / post-role
/ driven) was never executed and remains the one-run discriminator. Either
way the conclusion for host selection is unchanged: **anything gated on
`+0x3E0` engagement kinds inherits per-frame re-decisions made inside the
manager; the AI state byte does not** — it changes only through the
dispatcher's transition machinery.

## 6. Where in the frame each host falls

Per sim step, in order: wrapper 1 → tick per-player loop (**all AI thinks,
incl. state 32**) → block manager `0x001F7298` (all ten passes, Site B,
sweep) → wrapper 2 → wrapper 3 (**locomotion service**, then `0x0018FD50`
per player). A position add from host #1 lands before that frame's manager
passes; from host #2, after them. Both are accumulate-in-place adds on
`+0x190/+0x194`; order does not change the sum.

## 7. What this run verified live (savestates, not the rig)

* Machine array `[0x006011A4]` → machine[0] = `0x006012D0` → table
  `0x00527238`; row-32 ai_think word `[0x00527540] = 0x001E8088` — both
  states.
* Both members of the live kind-6 pair hold `+0xBCC = 32`; mutual `+0x150`
  handles equal to `+0x3E4` (0x00030101 ↔ 0x00090001).
* `+0x2FC = &(+0xBCC)` (the "AI block" pointer is the state byte's address).
* Stock words at `0x001E80C0` (0xA244000A), `[0x00527540]`, `0x001AFAB0`,
  `0x00164F88`, `0x002512DC` — both states, so the hook plan starts from a
  clean surface and `load_state` wipes nothing it shouldn't.
* Pre-snap slot 9: pair members in states 69/42, `+0x150 = 0`, dt_role 5 —
  the chosen host is dormant pre-snap by construction.

## 8. What I could not establish

1. **Whether a `+0x194` add sticks during kinds 5/6.** P9 proved position
   writes land and move outcomes during kind-4 frames; during 5/6 the
   animation owns the transforms (`block-cycle.md`). The applier's own
   accumulate-in-place semantics and the live pair's frame-over-frame
   drifted positions argue nothing re-derives `+0x190/194` absolutely, but
   the exhaustive 5/6-active writer census (`motion-block-cave.md` §10.2)
   is still unrun. F2's K=0.25 teleport probe remains the designed one-run
   catch.
2. **The Site B 5-vs-35 residue** (§5.2): cave-gate rejection (D ≤ 0 from
   run-play margin fields — §10.3 there — or link-handle kind ≠ 1) vs a
   flaw in the kind-8 frame counts. Decidable in one run with the three
   canaries at 0x00514974/78/7C; not decidable statically.
3. **Sim step ≡ 60 Hz on the rig.** The sim runs once per pending-tick drain
   with f12 = n ticks; if the emulator falls behind, one step covers n
   ticks. Every candidate host shares this cadence, so it cannot cause
   host-to-host differences; absolute rate is checkable live by logging
   `[0x00607FFC]+4` deltas.
4. **Edge exactness of kinds 5/6 ⇔ state 32.** Membership is live-verified
   mid-window and the 5→6 promote is inside the think itself; the exact
   entry/exit frames (enter `0x001E7EE0` / the `0x001FD048(s0, 2)` exit arm)
   were not walked. A one-frame skew at either edge is possible.
5. **`0x00154790`'s mode enum** (the manager's {3,4} gate) — still unlabeled,
   same open item as prior lanes; measured behaviour (counters tick during
   plays) is the working evidence.
6. **The second caller of the maintain fn** (`0x001CA0F4`, inside
   `0x001C9E28`) — which state's think region that is was not chased; it
   does not affect host selection.
7. **Semantics of the displaced byte `+0x15A := 1`** — consumers not
   traced; the cave preserves the store verbatim, so its meaning is not
   load-bearing for hosting.
8. **Deep callees of the sweep body (`0x002CFC00`) and of the manager tail
   `0x001F5E80`** were not exhaustively walked for indirect kind writes;
   the direct scan of the tail (no `sw …,992()`, no setter jals) is the
   evidence behind §5.2's ordering claim.

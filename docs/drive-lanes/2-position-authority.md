# Drive lane 2: position authority — who owns an engaged defender's +0x190/+0x194, and whether a per-frame write survives

Recorded 2026-08-12. Static lane, maximum effort. Sources: `extract/SLUS_207.52`
(vaddr = offset + 0xFF000, gp = 0x006056F0) via `recon.mipsdis`
(`find_field_refs` / `find_jal_targets` / `find_address_refs` plus raw data-word
scans); `experiments/states/ball_in_air_slot8.p2s` (live kind-6 pair, MID-PLAY)
via `tools/statereader.py`. No rig, no network, no emulator, no commits. Every
address, instruction and live value quoted below was re-read from the image or
the savestate **this pass** (rule 4). **UNVERIFIED** marks the residue.

**One sentence:** position is accumulate-in-place everywhere in this engine —
nothing re-derives +0x190/+0x194 from scratch per frame — so a cave write is
never erased; but for exactly as long as a pair-animation participant's
convergence record is armed (t ramping 0.0545/service toward 1.0, ~19 service
ticks per (re)launch), the anim-service warp `0x00196FE0` lerps him toward a
world-frozen, partner-anchored target every serviced frame, geometrically
eating any displacement the target did not share — so the drive must either
move the TARGET words with the body or be hosted inside that same service.

Three findings correct earlier documents:

1. **The pair family HAS a per-frame root-motion applier** — not `0x0018F980`
   (the linear burst applier; correctly ruled out) but the convergence warp
   `0x00196FE0`, reached through a second, previously unmapped interaction
   host `0x00197258` that a 175-entry PER-CLIP-ID handler table (0x00520198)
   installs for every pair/block id {146–151, 158, 161, 168–170, 173} and for
   the whole shed/segment vocabulary. `motion-block-cave.md` §1.3's "the
   defender's block is stale float garbage from an earlier tenant" is wrong:
   those words are the **live convergence record of the current clip** (§4).
2. **P9's ~5 driven frames/play at the sweep hook is not a cadence mystery**:
   the sweep's copy block (P9's host) sits behind the sweep's own
   `+0x3E0 == 4` gate — it sees only KIND-4 frames, and the doubled DE spends
   ~5 frames in kind 4 before capture promotes him to 5/6 (§5).
3. The "locomotion writer 0x00212974" that P7 hooked is the B-side velocity
   store of the pairwise collision-impulse resolver `0x00212880`, not a
   locomotion integrator. The real universal mover is `0x00160028`:
   `pos += vel` (§1.2, §2).

---

## 1. The writer census (closed set)

Method: `find_field_refs(elf, 0x190/0x194, stores_only=True)` — all three
forms (direct, biased-base, and stores through a base passed to a callee) —
plus manual coverage of the 64-bit forms (`sd`/`sdl`+`sdr` at +0x190 write
pos_x AND pos_y in one instruction; the tool reports them, and every one found
was chased). Raw yield: 315 hits for +0x190, 163 for +0x194. After dropping
sp-based stores (182 prologue saves / stack locals), **161 distinct functions**
contain a candidate store. Grouped and classified this pass:

### 1.1 Writers that CAN touch an on-field player mid-play (the set that matters)

| writer | store site(s) | mechanism | runs for an ENGAGED (4–8) / CAPTURED (5/6) defender? |
|---|---|---|---|
| **Movement integrator `0x00160028`** (phase-3 pipeline `0x002512B8`, per player, every frame) | `0x004ADD70` vec-add called at `0x001601E0` with (out=&pos, a=&pos, b=&vel) | prev := pos (`0x00160068/6C`: pos→+0x19C/+0x1A0), then **pos += vel**; gravity on vel_z at `0x001601D4` when +0x198 > 0 | YES, every frame, all 22 — but it is an ADD of vel, and a captured player's vel decays to ~0 (mode-0 zeroing + decel arm). Live pair: vel = (−0.03, 0.01)/(−0.05, 0.01) residual, speed_cmd = 0, +0x1F8 = 0 |
| **Convergence warp `0x00196FE0`** (anim service host `0x00197258` phase 2, per serviced slot) | `swc1 f1, 400(s4)` @ `0x001970D4`; `swc1 f0, 4(s5)` @ `0x001970F0`; heading @ `0x00197134/48` | **pos ← pos + (target − pos)·t**, t += 0.0545/service ([0x005FE258]), self-disarms at t ≥ 1.0 (final frame = hard snap pos := target); heading blended the same way | **YES — this is the pair-clip position authority.** Armed per participant at clip (re)launch only if his variant's component row carries authored alignment (§4). Live slot-8: defender ARMED mid-kind-6 (t = 0.654), blocker not |
| **Collision separation `0x00213038`** (pair callback `0x00164688`, phase 4) | `sdl/sdr v1, 407/400(s4)` @ `0x002133DC/E0` (+`sw` 408) and mirror `0x00213424/28` | **pos := prev** (+0x19C..+0x1A4 copied over +0x190..+0x198) — reverts THIS frame's movement of an interpenetrating body; inverse-mass math (1/(w·335.4)) scales the rest | Only vs THIRD parties: `0x00212E80` @ `0x00213408` scans the victim's +0x25C attach list and skips registered pairs (state 32 registers both directions, re-verified live: 0x00030101/0x00090001). Flags-bit-8/0x10 exemptions. Note: prev is stamped in phase 3 AFTER a phase-1/2 write, so even a revert PRESERVES earlier same-frame writes |
| **Collision impulse `0x00212880`** (other pair-callback arm, phase 4) | vel only (`0x00212928/30`, `0x00212978/80` = +0x1B8/+0x1BC) | velocity exchange; positions untouched | pair-exempt the same way; vel writes reach pos next frame via the integrator |
| **Linear burst applier `0x0018F980`** (PROPC opcode-38 host `0x0018FBE8` phase 2, message-driven) | `swc1 f0, 400(a2)` @ `0x0018F9BC`; `swc1 f0, 4(a3)` @ `0x0018F9D4` | pos_x += dx, pos_y += dz, heading += dheading (re-read fresh this pass, §3) | **NO for pair clips** — no type-9 spec exists for g15–g19 and the live motion block shows the applier never ran (§3). Runs only for spec-carrying solo/kick bursts |
| **Glide host `0x0018C7C0`** (store `0x0018CC30/3C`) | steps pos by [ctl+0x1C]/[ctl+0x20] per frame; killed by byte [0x0060111D] == 0 | per-clip-id handler for the KICK family only — table slots 140/141/143/144/145 (`0x00168DE0-EC`, `0x00168D78`). Never services a pair id | NO for blocks (corrects the 3-clip-inventory §2 reading that left its scope open) |
| **Sideline/OOB service `0x001837B0`** (phase-3 post pass, per player) | pushes via `0x00183540` → `0x00183BB8/0x00183FE8` (`sw` into +0x190 via base+0x178) | out-of-bounds handling: only when play-mode ∈ {3,4} AND |pos_x| > [0x005FE0A4] = 25.67 yd | Only at the sideline. Cannot fire on an interior drive |
| **pos_y floor clamp `0x00164B58`** (phase-3 post pass, per player) | `swc1 f0, 404(s0)` @ `0x00164BE0` (bc1tl-guarded) | pos_y := max(pos_y, [0x00260208 spot] + 0.15625 + [0x005FDEEC]) | **NO mid-play**: gated on play-mode == 2 (pre-snap) AND side == possession side (offence only) AND flag-63 clear. It is the pre-snap LOS floor |
| **Ground clamp `0x001603C0`** (phase-3 post pass, per player) | +0x198/+0x1C0 only | z := 0 when below ground and falling | never touches x/y |
| **State ENTER/think placement writers** (state machine) | e.g. state 42 (defensive pre-snap) think `0x001A52C8` store @ `0x001A5774`; state enters `0x001D5218`/`0x001D5620`/`0x001D62A0`/`0x001D6A68`/`0x001D7578`/`0x001D7C60`/`0x001DA068` = states 101/109/113/97/100/99/98; move-arc family `0x0019E940`, `0x001D0308` (state-62 region) | absolute placements at state entry, or per-frame only for the player IN that state | a CAPTURED defender is in state 32, whose think `0x001E8088` was read in full this pass: **zero position stores** (anim bookkeeping, mutual no-collide `0x00213628`, 5→6 promote @ `0x001E81EC`, exit-only locomotion re-arm +0x1F4=1/0.46 @ `0x001E821C-24`). Other states' writers cannot run for him |
| **Spawn/reset/placement** | `0x001644E8` (spawn: formation x/y, weight_copy 170.0), `0x00252680` (per-side lineup placement), `0x001C4158` (reset via zeroer `0x0015FFD8`), `0x0016E070`/`0x0016E3B8` (place/init pseudo-actor), mirror flip `0x001603F8` (negate x/y + 180° headings; caller `0x00165710` loop = field flip) | absolute sets | episodic (pre-play, resets, half flips) — never per-frame in-play |

### 1.2 Everything else (bulk classification, all inspected this pass)

* **Math library `0x004ADxxx`–`0x004AExxx`** (`0x004ADC40` rotate, `0x004ADD48/70`
  add, `0x004ADDA8` sub, `0x004ADED8`, `0x004AE1E8/240/2F8` …): vector ops that
  write through an out-pointer; every census hit here is attributed to its
  CALLER (the integrator, the collision layer, state code). Not independent writers.
* **False positives by construction**, individually confirmed: `0x00168CD8`
  `sw a2, 400(s0)` = installing handler 0x00196AD0 into per-clip TABLE entry
  100 (s0 = 0x00520198, not a player); `0x001B74B0`'s +0x194 hit = lead-blocker
  scratch vector (base+176 block, the "x2 velocity lead" copy, not the record);
  `0x0012B878`-family = jump-table dispatch, no stores.
* **Other-struct writers** (ball records via [0x006012F0] stride 240; camera,
  chain/officials, replay/presentation `0x0022xxxx`–`0x0026xxxx`, franchise/UI
  `0x0029xxxx`+, renderer/skeleton `0x003Axxxx`+ reached from UI/replay callers,
  EA library `0x004Bxxxx`+): same +0x190-shaped offsets on different structs, or
  play-mode-gated presentation paths (e.g. `0x00216720` — callers are franchise
  code `0x002902E0/3B0`). None runs against a live player record during a snap.
  Per-function gates here were NOT individually proven — region + caller
  classification only (UNVERIFIED at that granularity, listed for closure).

**Census bottom line:** for a CAPTURED (kind-5/6) defender the complete set of
code that touches +0x190/+0x194 in a live frame is: the integrator (adds ~0),
the convergence warp (while armed), and third-party collision separation
(reverts to same-frame prev). For a kind-4 defender, add the same list — the
sweep/engagement system itself writes intent fields only (+0x1E8/+0x1EC/+0x1F0/
+0x1F4), never position, confirming pass-vs-run-blocking.md at the store level.

## 2. Order of operations inside one frame

The frame sequencer `0x00154114` (read in full this pass) runs five phases in
fixed order; every claim below is from its body and its callees' bodies:

```
PHASE 1  0x00154178: 0x00183410; MASTER TICK 0x00164EC0
           per player (stride 0x14C0): pad steer 0x0016D0B0; 0x00188958;
           AI THINK driver 0x001AF9D0 (state thinks: stage +0x1E8/+0x1EC/+0x1F4;
           state-32 think = NO position writes); 0x0014EB60; block timers
           0x00164820; 0x00164BF8
           then: ENGAGEMENT DRIVER 0x001F7298 @ 0x00164FC4
                 (lock-ins, kind-8 Site B, kind-4 sweep = P8/P9 cave hosts)
PHASE 2  0x001543E0: 0x001652C0 per player: +0x1F4 mode dispatch (table
           0x00579C00; mode 5 = gait command copy +0x1E8→+0x1F8 + clip select —
           no position writes); 0x00165318 per player: ANIM ADVANCE 0x001648B0
           → 0x003AD1B8: per active slot, countdown −= dt, and on service:
           table[clip_id] PHASE-2 call @ 0x003AD284 (armed slots only)
           → 0x00197258 phase 2 → CONVERGENCE WARP 0x00196FE0 WRITES POSITION
PHASE 3  0x00154238: 0x00165170 per player: movement pipeline 0x002512B8
           { 0x00160000 reset; locomotion 0x00250E10 (speed caps only);
             INTEGRATOR 0x00160028: prev := pos, pos += vel };
           then pos_y floor clamp 0x00164B58 (pre-snap only); ground clamp
           0x001603C0; sideline OOB 0x001837B0; anim-id cache 0x0018FD50
PHASE 4  0x00154318 (skipped in modes 1/5/6/7): collision passes 0x00211A28 ×2
           → pair callback 0x00164688 → impulse 0x00212F50 (vel) or separation
           0x00213038 (pos := prev), +0x25C no-collide honored
PHASE 5  0x001544A8: presentation/misc (0x00165550 …)
```

**Who writes LAST:** for a captured defender, in written order: cave-at-P9's-
host (phase 1) → convergence warp (phase 2) → integrator (+≈0, phase 3) →
[clamps: not applicable mid-field] → collision separation (phase 4, pair-exempt,
and it reverts only to the phase-3 prev — which already CONTAINS any phase-1/2
write). **Nothing hard-overwrites a phase-1 position write.** The only code
that *pushes back* is the convergence warp, and it pushes toward its target by
fraction t — it does not restore a saved position.

(The sequencer's own caller was not traced — no jal reaches `0x00154114`;
UNVERIFIED that no second frame path exists, but all five phases and both
per-player pipelines hang off this one body, and the subsystem families it
drives are the complete in-play set found by the census.)

## 3. Does `0x0018F980` run for a pair clip? No — re-verified, with a correction

Fresh disassembly this pass (complete):

```
0018f980  lw   v0, 12(a1)          ; frames
0018f98c  addiu v0, v0, -1
0018f990  blez v0, 0x0018f9d8      ; exhausted -> no store at all
0018f994  sw   v0, 12(a1)          ; (ds) frames--
0018f998  lwc1 f1, 0(a1)           ; dx
0018f9a0  lwc1 f0, 400(a2)         ; pos_x
0018f9ac  add.s f0, f0, f1
0018f9bc  swc1 f0, 400(a2)         ; pos_x += dx      <- pure accumulate
0018f9a8/b4/b8/c0/c4               ; heading += dheading, BAM24-masked
0018f9c8  lwc1 f0, 4(a3)           ; pos_y (a3 = player+0x190)
0018f9d0  add.s f0, f0, f1
0018f9d4  swc1 f0, 4(a3)           ; pos_y += dz
```

Its sole caller is `0x0018FCE8` (phase 2 of the PROPC opcode-38 host
`0x0018FBE8`), and its input (the linear burst block {dx, dz, dheading,
frames}) is only ever produced by the converter `0x0018F9E0` from a **type-9
static spec** — which no pair-block family ships (5-clip-semantics §3). So for
pair clips there is nothing for it to apply and no evidence it is invoked.

**Correction to motion-block-cave.md §1.3(2):** the defender's slot+0x10 words
(0.654 / 4.5146 / 6.0998 / 0x3F329699 / 0x3E1DBF14) are not "stale float
garbage from an earlier tenant". Slot+0x10 is a UNION: the linear host lays
{dx, dz, dheading, frames, cookie} there, and the pair/segment host `0x00197258`
lays its convergence record there — {t, target_x, target_y, acc_x, acc_y,
target_heading @+0x24, clip_id @+0x28, enable/rec#/mirror/flag bytes @+0x2C}.
Re-read live this pass, the defender's block decodes exactly as the SECOND
layout for the CURRENT clip 147: t = 0.654 (12 services in at 0.0545/step),
target (4.51, 6.10) = 0.75 yd behind him along the pair axis (the authored
1.8–2.0-yd attach separation), clip_id = 147, enable = 1. The blocker's block:
clip_id = 147 stamped, rec# = 5, enable = 0, t = 0 — phase 0 ran for him too
and DISARMED him (null-alignment participant row). §1.3's conclusion (the
LINEAR burst never runs for pairs) stands; its evidence reading does not.

## 4. The constraint exists — the partner-anchored convergence warp

This is the answer to "find the code that enforces the pair's fixed relative
geometry." It is not a per-frame constraint solver; it is a self-expiring lerp.

**Registration.** `0x00168B30` (called from world-init `0x00166BE0`, which also
registers the record {count=175, table=0x00520198} at 0x00600E80 with the anim
runtime as service #1 via `0x003ACDA8`) fills a 175-entry PER-CLIP-ID handler
table. Installed with host `0x00197258`: ids 25, 30, 31, 49–66 (kind-4 grid
segments), 79, 93–96, 106–108, 118–131 (shed outcomes), **146–151, 158, 161,
168–170, 173 (every pair-block id)**. Installed with `0x0018C7C0` (glide):
140–145 (kicks). Solo/stance families get their own handlers (0x001958F0 etc.).

**Invocation.** The anim runtime's slot pump `0x003AD1B8` (reached per player
per frame from phase 2: `0x00165318` → `0x001648B0/0x00164950`) decrements the
slot countdown by dt and, each time it elapses, advances the stream and calls
`0x003AC698` → `table[clip_id]` with **phase 2** (`0x003AD284`, armed-bit slot+8
gated) — and with phase 1 at clip end (`0x003AD2C8`). Phase 0 runs at launch
(sender not re-traced; its execution for pair clips is PROVEN by the stamped
clip_id/rec# in both live blocks).

**Setup (`0x00196CC0`, phase 0).** Walks the selected variant's component
participant rows (20-byte rows) for the row matching this player's participant
word (+0x3DC): no matching/aligned row → `sb zero, 28(block)` (disarmed).
Matched → enable := 1 and:

```
00196eb4  kind ∈ {4,5} or 2 or 9 -> anchor := partner from +0x3E4 (0x0013B798)
          (other AI states: anchor := carrier / possession player — table 0x00581A80)
00196eec  ldl/ldr row+8 -> block+4/+8      ; authored {relx, relz}
00196f08  mirror byte set -> negate relz
00196f18  0x004ADA50(block+4, block+4, anchor.heading +0x1A8)   ; rotate
00196f20  block+4 += anchor.pos_x; block+8 += anchor.pos_y      ; WORLD target
00196f80  block+0x14 := anchor.heading ± row heading            ; target facing
00196f88  block+0 (t) := 0
```

**The target is world-frozen at launch** — partner.pos at that instant plus the
authored offset rotated by partner.heading. It does not track the partner
afterward.

**Application (`0x00196FE0`, phase 2, quoted).** Per service:

```
00197014  t += 0.0545 ([0x005FE258]);  if t >= 1.0: t := 1.0, enable := 0
00197050  block+0xC += rotate(player+0x210, −[rec+0xC])   ; fold in own recent
          movement (+0x210 = last movement delta; writer UNVERIFIED)
00197088  sp := rotate(block+0xC, block+0x14) + block+4    ; effective target
001970a0  f0 := (target_x − pos_x)·t;  pos_x += f0         ; 0x001970D4
001970d8  f2 := (target_y − pos_y)·t;  pos_y += f2         ; 0x001970F0
001970ec  heading += angle_diff(target_heading, heading)·t ; BAM-wrapped
```

So while enable = 1: `pos_new = (1−t)·pos + t·target` on the serviced axis pair
— **any displacement the target does not share decays by factor (1−t) per
service**, hardest near the end of the ramp, with one final full snap
(pos := target exactly) on the t = 1.0 service, after which the record disarms
and position is UNOWNED again. ~19 services per arm at 0.0545/step; re-armed at
every pair-anim (re)launch whose variant row carries alignment — i.e. across
the capture's segment relaunches, a captured defender can be re-armed
repeatedly. Live proof of mid-capture activity: the slot-8 defender at
t = 0.654, enabled, 12 services in, mid-kind-6.

The side-flip guard in the host (`0x00197454-84`: negate block+4/+8 and rotate
block+0x14 by 180° when the possession side changed) and the current-clip
gate (`0x00197428`: `0x003AD410` must still return this clip) both precede the
warp call at `0x0019748C`.

## 5. The starvation numbers, re-explained by this map

* **P9's host (the kind-4 sweep copy block, `0x001F2070` region) executes only
  for `+0x3E0 == 4` pairs** — the sweep's own gate. The doubled DE holds kind 4
  for ~5 frames before state 32 captures him into 5/6 (A2 series: capture at
  f43), and canary ≈ 5/play matches that window exactly. It was never a
  cadence problem and no gate widening inside the cave could fix it: the host's
  VIEW of the pair ends at capture. During the ~56-frame 5/6 window the
  engagement driver deliberately does not process the pair (block-cycle.md's
  jump-table no-op, unchanged) — so hosting a per-frame drive there is
  structurally impossible.
* Site B's ~5/play against a 35-frame kind-8 window is NOT explained by this
  pass (the kind-8 servicer is reached per frame from `0x001F7298`; its
  effective cadence per helper remains the open item motion-block-cave.md
  §10.5 flagged — UNVERIFIED here).
* The anim-service host (`0x00197258` phase 2) fires per slot service for the
  ENTIRE life of the pair clip — exactly the 5/6 window the drive needs.
  Service cadence in game frames = the slot countdown re-arm (`0x003AE230`),
  not decoded this pass: per-anim-frame, so once per 1–2 game ticks
  (UNVERIFIED which). Count it before trusting it, per standing rule.

## 6. VERDICT

**Survive, be overwritten, or be fought?** A per-frame add to +0x190/+0x194:

* **Is never OVERWRITTEN.** No writer re-derives position; the universal mover
  is `pos += vel`; the only pos := X writers are episodic placements, pre-snap/
  sideline clamps that cannot fire on an interior drive, and the collision
  revert — which restores the SAME-frame prev that already contains the write,
  and which the pair is exempt from anyway.
* **Is FOUGHT — precisely, and only, while the defender's convergence record is
  armed.** During each armed window (~19 services from each pair-anim launch or
  relaunch, participant-row dependent), the warp eats fraction t (0.05 → 1.0
  ramping) of any offset from its frozen target per service, and erases the
  remainder at the t = 1.0 snap. Writes made outside armed windows stick in
  full. This — not only host starvation — is the mechanism behind P9's
  measurable-but-tiny response and is exactly failure mode F2's "adds partially
  eaten", now with the eater named.
* **Scaling P9 to ~56 frames therefore buys real yardage only if the fight is
  removed.** Two clean ways, one field:

**Recommended write: move the TARGET with the body.** The armed convergence
target is two words in the defender's active anim slot: `slot+0x14`
(target_x) and `slot+0x18` (target_y) (slot = `[player+0x304] + 0x64·k` for
the status-3 slot, = the `0x003AD3D0` block +4/+8). Each driven frame, add the
SAME {vx, vz} to pos_x/pos_y AND to slot+0x14/+0x18 (for each driven body whose
record is armed; a disarmed record needs no compensation, and the t = 1.0 snap
then lands where the drive says). That makes the cave's displacement invisible
to the warp — the animation carries the defender to the driven target instead
of pulling him back, which is also the on-skates visual (161 already animates
the feet). Equivalently and more simply hosted: **put the drive INSIDE the
service — hook `0x00197258` phase 2 (or `0x00196FE0` itself)** — it runs
exactly and only while the pair clip plays (the whole 5/6 window), has the
player in a register (t0 arg = player; s4 in the warp), and a write there
lands after the warp in the same service, un-eaten until the next service.
First step either way, per the discipline: point the 32-word counting cave at
`0x00197258` phase-2 and read the canary against the pair window — the
predicted count is the full clip-service count (~28–56), not ~5.

## Helper lookup for N-1

Appended same pass, on redirect: N-1 (fold the attached helper's weight+STR
into the primary's comps at the lock-in's contest call) needs defender → role-1
helper. Everything below was re-derived this pass from the image and checked
against both savestates.

### The verified path (design-doc assertion CONFIRMED, with the value form pinned)

The record layout is proven at its **creation site** — the manage fn's claim
walk (`0x001F63A0-0x001F649C`, quoted):

```
001f63a0  lw   a0, -17520(gp)       ; manager = [0x00601280]
001f63b0  addiu a1, zero, 20        ; stride
001f63b4  mult v0, s4, a1           ; k = 0..3 (retry loop back-edge 0x001f64a8)
001f63b8  addiu v0, v0, 4
001f63bc  addu s2, a0, v0           ; record = manager + 4 + 20k
001f63c0  lbu  v1, 16(s2)           ; +0x10 active — nonzero record is skipped
001f63cc  sb   s5, 16(s2)           ; claim: active := 1
001f6460  lw   v0, 0(s0)            ; primary's OWN +0x00 word
001f646c  sw   v0, 0(s2)            ; record+0x00 := primary HANDLE
001f6470  lw   v1, 0(s1)
001f6474  sw   v1, 4(s2)            ; record+0x04 := HELPER HANDLE  <- the slot
001f6478  lw   v0, 0(s3)
001f6480  sw   v0, 8(s2)            ; record+0x08 := defender HANDLE (ds)
001f647c  jal  0x0013b870(0, s2+12) ; record+0x0C := null (second-level)
001f6484  sb   s4, 1078(s0/s1/s3)   ; +0x436 := k on all three
001f6490  sb 0/1/2, 1079(...)       ; roles: primary 0, HELPER 1, defender 2
```

* **The stored value is a HANDLE, not a pointer** — the player's own +0x00
  self-handle word (encoding index<<16 | side<<8 | kind, kind 1 = player;
  live-verified: 0:9's +0x00 = 0x00090001, 1:3's = 0x00030101 in both states).
  It must be resolved. `0x0013B798(a0 = ADDRESS of the handle word)` does it:
  loads [a0], dispatches on the kind byte (< 10, table 0x0057B680), kind-1 arm
  `0x0013B7E0` calls GetPlayer `0x001655B0(index, side)` → v0 = base, 0 if null.
* The helper is **always record+0x04 / role 1** — the possible post/drive-man
  swap (`0x001F6454-5C`) happens BEFORE the stores, so the slot meaning is
  stable.
* **+0x436 is written, never read** — an image-wide scan finds ZERO readers of
  displacement 1078 (only the three creation stamps, `0x001F6734`, and the
  teardown clear `0x001F7088` `sb zero, 1078(s0)`). Two consequences: the
  engine has NO "find my record/helper" function to borrow (every consumer
  walks the 4 records comparing handles, or already holds the record), and
  teardown resets the index to 0, not 0xFF — **+0x436 is only meaningful while
  +0x437 != 5**, so N-1's dt_role==2 gate is also the index's validity gate.
* Savestate check (slot 9 AND slot 8): manager = 0x006C0010 in both; records
  at +4+20k = 0x006C0014/28/3C/50; all four inactive with zeroed handles
  (slot 9 is pre-snap, slot 8 is a pass play — records never form on pass), so
  the layout is field-verified structurally and the LIVE-record content check
  stays open (no owned state carries an active record — UNVERIFIED only in
  that sense; the creation-site quote is the authority).

**Cheapest correct sequence from the defender base (s4 at the N-1 site):**

```
lbu  t0, 0x437(s4);  li at,2;  bne t0,at,stock     ; role gate = validity gate
lbu  t1, 0x436(s4)          ; (ds) k
lw   t2, -17520(gp)         ; manager
sll  t3, t1, 2;  sll t4, t1, 4;  addu t3, t3, t4   ; k*20
addu t2, t2, t3;  addiu t2, t2, 4                  ; record base
lbu  t5, 0x10(t2);  beq t5, zero, stock            ; active
lw   t6, 0(s4);  lw t7, 8(t2);  bne t6, t7, stock  ; (4-word stale-index insurance, optional)
addiu a0, t2, 4                                    ; &record.helper
jal  0x0013b798                                    ; v0 = HELPER BASE or 0
```

~15 words + one jal (18 with the insurance). **No one-jal engine shortcut
exists** (proven by the +0x436 reader census and by inspection of every
[0x00601280] consumer — all inline their walks). The only jal-free alternative
is inlining GetPlayer's own math on the handle bytes (descriptor [0x00600E48],
side*per_side+index, stride 0x14C0 — the exact 7-word pattern the motion-block
cave already uses at 0x004432BC-EC); since the cave must save ra for the
displaced contest call anyway, the resolve jal costs only ~3 extra words and
buys the engine's own null/kind validation — recommended.

### Register and stack budget at 0x001F153C

The lock-in `0x001F14D0(a0=blocker, a1=defender, a2=packed_ref)` prologue
(quoted this pass): frame **192 bytes**; saves s0-s6 (s0@48, s1@64, s2@80,
s3@96, s4@112, s5@128, s6@144), ra@160, f20@176, f21@184, a2→0(sp). **s7 is
NOT saved** — it holds the caller's value; do not touch it (or save it).

At the `jal 0x001f0c40` site (`0x001F153C`, delay slot `daddu a1, s4, zero`):

* **Live across the site (read-only for the cave):** s0 = BLOCKER base,
  s4 = DEFENDER base (coordinator's assertion CONFIRMED), s1 = pair-axis BAM,
  s2 = blocker+0x404, s3 = defender+0x404, s5 = blocker+0x190,
  s6 = blocker+0x3E0, sp/fp/gp, 0(sp) = saved packed_ref. s2/s3 are exactly
  the comp blocks N-1 wants to touch — already in registers.
* **Dead / free at the site:** at, v0, v1, a0-a3, t0-t9, hi/lo — the compiler
  already treats them as clobbered by the contest call; the cave may use them
  freely, but nothing in them survives the cave's own nested jals.
* **FPU:** f20/f21 are DEAD at the site — both comparison arms define them
  (`div.s f21` @ 0x001f1580/0x001f1644, `div.s f20` @ 0x001f15a0/0x001f1660/
  0x001f167c) before any read. f0-f19 are caller-saved. So f0-f21 all usable;
  leave f22+ alone (not saved by this fn).
* **ra:** holds 0x001F1544 (the comp loads). The cave must save it around its
  nested calls and restore before returning — one slot.
* **sp adjustment is safe** — this is an ordinary call boundary; a balanced
  `addiu sp, sp, -16 … addiu sp, sp, 16` shadows nothing (the fn's own frame
  data sits above; 0(sp) is only read back via the caller-relative sp).
* **Recommended cave shape** (2 nested jals, 16-byte frame): save ra +
  resolve the helper FIRST (sequence above) + stash v0 in the cave frame →
  set a0=s0/a1=s4, `jal 0x001f0c40` (displaced call) → reload helper base;
  if nonzero, read helper +0xAEC/+0xB8E and fold into the blocker comps at
  16(s2)/20(s2) (+0x414/+0x418) — the fold lands BEFORE the lock-in's first
  comp reads at 0x001F1544/48, which is the whole point of hooking here →
  restore ra, return. Total ≈ 35-40 words including the fold arithmetic.

**One design-level caveat (flagged, not resolved here):** at FIRST-contact
lock-in the record does not exist yet (roles are 5 — creation happens on the
kind-7→8 attach path), so a role-2-gated N-1 no-ops on the initial stamp and
fires only on lock-ins that run while the record lives — i.e. through the
+0x42E re-lock latch (per-frame for pass-pro pairs via `0x001F2214`; on run
plays only on kind-4 re-entry). If the slot-9 double never re-locks between
record creation (~f23) and capture (f43), the fold never executes — count
re-locks before trusting N-1's window.

## 7. What I could not establish

1. **The writer of player+0x210** (the movement-delta the warp folds into its
   accumulator). Live value ≡ vel ≡ pos−prev on serviced players, stale on
   others; no store found by literal/biased scan (likely written through a
   base my pass didn't chase). Until named, whether a phase-1 cave write leaks
   into the target-follow term (which would SOFTEN the fight) is open — the
   phase order (prev stamped in phase 3) argues it does not.
2. **Service cadence in game frames** — the slot countdown re-arm value inside
   `0x003AE230` (per anim frame vs per tick). Bounds the true t-ramp duration
   (19–38+ ticks) and the drive-host frame count. Countable in one rig run.
3. **Phase-0 sender** for the per-clip-id service (the launch path reaching
   `0x003ACDF0`/`0x003AD0FC` — no jal callers; presumably an indirect call
   inside the launcher chain `0x0018E378`/`0x003A8930`). Execution for pair
   clips is live-proven regardless.
4. **Which 161/segment variants carry non-null alignment rows** — i.e. on the
   slot-9 double, which of the three bodies gets armed, when, and for how many
   relaunches. The slot-8 147 pair shows defender-armed/blocker-disarmed;
   per-clip and per-variant generality is data I did not decode (the component
   rows exist; the walk is mechanical but was not run for all 24 variants).
5. **Site B's ~5/35 cadence** (motion-block-cave §10.5's side-semantics
   question, untouched by this pass).
6. Per-function gate proof for the presentation/franchise/replay region writers
   bucketed in §1.2, and the exact object identities of the 0x0022xxxx actor-
   sync family (flags-bit-0x10-gated) — region+caller classification only.
7. The +0x1F4 mode-table handlers not read (modes other than 0/1/5): none is a
   position writer by census, but their semantics were not decoded.

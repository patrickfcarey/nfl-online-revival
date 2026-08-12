# Drive machinery — how a locked pair physically moves

Static lane for `docs/on-skates-requirements.md` Q1–Q4. Investigated
2026-08-11 against `extract/SLUS_207.52` (vaddr = offset + 0xFF000,
gp = 0x006056F0) with `recon/mipsdis.py`. Every load-bearing claim below is
quoted from the image; anything not re-derived is marked UNVERIFIED. Sibling
lanes own the registry manage fn (`0x001f6640`), help-score, and the run/pass
contrast — this document stops at their boundaries.

Offsets: player struct unless said otherwise. `s2 = player+0x404` means the
register was observed biased in the quoted code.

## The machinery map (all of it found this pass)

The block system's frame tick tail runs, in order:

```
001f7324  jal 0x001f00d8      ; engager A (initial contact -> lock-in), 1 caller
001f7334  jal 0x001f06a0      ; engager B (second establishment path), 1 caller
001f7344  jal 0x001f1c20      ; per-frame locked-pair sweep, 1 caller
001f7350  j   0x001f5e80
```

| fn | role |
|---|---|
| `0x001ef820` | engagement-kind state machine (2/3→4, 7→8); initialises `reselect_timer` |
| `0x001f00d8`, `0x001f06a0` | establishment: admission gates + first lock-in call |
| `0x001f14d0` | **THE LOCK-IN**: margin → staged drive/bearing/facing, both men |
| `0x001f0c40` | contest components (+0x414/418/41C); sole caller is the lock-in |
| `0x001f1c20` | **per-frame sweep** over kind-4 pairs: re-lock latch, anim, copier |
| `0x001f20f8` | kind-8 (live DT helper) per-frame: drive=0, re-aim at partner |
| `0x00250e10` | locomotion apply: mode dispatch on +0x1F5, speed caps, no decay |
| `0x0012ba18` | anim-event dispatcher: events 0xC000+n set `flags_0c` bits |

New struct facts found on the way (not yet in `addresses.yaml`):

* **+0x408, f32** — staged normalized **comp2 margin**, written identically to
  both men by the lock-in alongside staged_drive. No reader located (below).
* **+0x42E, u8** — the **"re-lock now" latch** the sweep consumes. Set on
  entering kind 4; cleared by every kind transition.
* **+0x25C** — 2-slot handle list; `0x00213628(self, partner)` registers the
  partner there, `0x002136d0` removes. The pairing layer's attach list.
* **+0x1F4 / +0x1F5, u8** — locomotion mode bytes; block code writes 5 into
  +0x1F4, locomotion dispatches a 40-entry jump table at `0x00587140` on +0x1F5.
* **+0x1A8, u32 BAM** — a per-player heading angle. Used as the drive bearing
  when the blocker wins (Q3), compared between the two men by the disengage
  test at `0x001ef9c0` (`lw a0,424(s0); lw a1,424(s1); jal 0x00469fc8`).
  Producer not isolated (~60 plausible writers) — semantics beyond
  "current heading in BAM" UNVERIFIED.

---

## Q1 — margin → translation speed, the full chain. VERDICT: MAPPED

### Stage 1: contest → staged drive (once per lock-in), `0x001f14d0`

`0x001f14d0(A = offensive blocker, B = defender, packed_ref)` first computes
the pair axis, then calls the contest, then compares components:

```
001f1520  jal 0x004adda8            ; delta = packed_ref - B.pos  (out = a1 - a2)
001f1528  lwc1 f12, 20(sp)          ; dy
001f152c  jal 0x00469e78            ; atan2 -> BAM angle
001f1534  daddu s1, v0, zero        ; s1 = angle(ref - B) — the pair axis
001f153c  jal 0x001f0c40            ; contest: fills +0x414/418/41C both men
001f1544  lwc1 f4, 20(s2)           ; A comp2 (+0x418)   [s2 = A+0x404]
001f1548  lwc1 f3, 20(s3)           ; B comp2            [s3 = B+0x404]
001f154c  c.lt.s f3, f4             ; who drives whom: comp2
```

A-wins-both arm (the canonical won rep):

```
001f1560  lwc1 f1, 16(s2)           ; A comp1 (+0x414)
001f1568  lwc1 f2, 16(s3)           ; B comp1
001f156c  sub.s f0, f4, f3          ; comp2 margin
001f157c  sw v0, 12(s2)             ; A staged_facing (+0x410) = axis+180
001f1580  div.s f21, f0, f4         ; f21 = (A2-B2)/A2   normalized comp2 margin
001f1588  sw s1, 12(s3)             ; B staged_facing = axis
001f158c  lw v0, 24(s5)             ; A's +0x1A8 heading  [s5 = A+0x190]
001f1590  sub.s f0, f1, f2          ; comp1 margin
001f159c  sw v0, 8(s2)              ; A staged_bearing (+0x40C) = A heading
001f15a0  div.s f20, f0, f1         ; f20 = (A1-B1)/A1   normalized comp1 margin
001f15a8  sw v0, 8(s3)              ; B staged_bearing = same
```

Confluence — the margin becomes the drive, **identically into both men**:

```
001f16dc  swc1 f20, 0(s2)           ; A staged_drive (+0x404) = comp1 margin
001f16e0  swc1 f20, 0(s3)           ; B staged_drive = same
001f16e4  swc1 f21, 4(s2)           ; A +0x408 = comp2 margin
001f16e8  swc1 f21, 4(s3)           ; B +0x408 = same
```

Two modifiers on the way there:

* **Split decision halves everything.** When the comp1 winner is not the comp2
  winner, both margins are multiplied by 0.5 (`0x001f1614/18`
  `mul.s f21,f21,f1; mul.s f20,f20,f1` with f1 = 0.5; mirrored at
  `0x001f168c`/`0x001f16d8`).
* **Pass-set LOS freeze.** When A wins and `A+0x3F0 == 1` (pass block), if
  `ballspot.y − A.y < 1.5` the drive is zeroed before the store
  (`0x001f15cc` loads 1.5, `0x001f15ec mtc1 zero, f20`). This is S5's
  existing containment, already in the engine.

So **staged_drive is exactly the normalized comp1 margin** —
`(winner − loser)/winner` in [0,1] — confirming pass-vs-run-blocking.md's
"+0x414 governs the drive speed" and giving it the precise formula.

### Stage 2: staged → live, EVERY FRAME, `0x001f1c20`

The sweep (single caller `0x001f7344`) loops the 11 offense players
(`jal 0x00260598` offense team; `jal 0x001655b0` GetPlayer;
`sltiu v0, s0, 11`), takes only `+0x3E0 == 4` (in contact), resolves the
partner from +0x3E4, and — on the no-anim-change path taken almost every
frame — copies the staged triple into the live locomotion fields **of both
men**:

```
001f2054  lw v0, 44(s3)             ; staged_bearing        [s3 = blocker+0x3E0]
001f205c  lwc1 f0, 36(s3)           ; staged_drive
001f2064  sw v0, 492(s1)            ; desired_bearing (+0x1EC)
001f2068  swc1 f0, 488(s1)          ; speed_cmd (+0x1E8) = staged_drive
001f206c  lw v0, 48(s3)             ; staged_facing
001f2070  sw v0, 496(s1)            ; facing (+0x1F0)
001f2078  lw v0, 44(s6)             ; ... identical block for the DEFENDER
001f2084  swc1 f0, 488(s2)          ;     [s6 = defender+0x3E0]
```

(the addresses.yaml `speed_cmd` citation `swc1 f0, 488(s1)` at `0x001F2068`
is this store), then `jal 0x002cfc00(13, pos, self, partner, 0)` per man —
the pair's per-frame anim/locomotion tick. The same copy exists twice more in
the anim-switch arms (`0x001f1df0`/`0x001f1e0c`, `0x001f1f68`/`0x001f1f84`).

**Consequence: while a kind-4 engagement holds, speed_cmd cannot decay — it
is re-stamped from staged_drive every frame.** Both bodies translate at the
margin speed along the shared bearing until the engagement ends. Locomotion
carries them; there are still zero position writes anywhere in this chain.

### The 0.46 grants are a DIFFERENT path (and there are fifty of them)

The state-32 grant at `0x001e8218/24` (`lwc1 f0, -26548(gp)` = 0x005fef3c →
`swc1 f0, 488(s0)`) is the scripted-animation walk speed: a flat constant,
bearing = own +0x1A8, **not margin-derived**. Its state-33 sibling is in
state 33's *enter*:

```
001dc1dc  lw v0, 424(s1)            ; +0x1A8 heading
001dc1e0  lwc1 f0, -26924(gp)       ; 0x005fedc4 = 0.46 — its OWN data word
001dc224  sw v1, 492(s1)            ; desired_bearing = heading
001dc228  swc1 f0, 488(s1)          ; speed_cmd = 0.46
```

A gp-relative scan finds **50 single-reader data words all holding 0.46**
(0x005fd880 … 0x005ff0d0), one per code site — the engine's universal
approach/walk speed, materialized per call site. The slot-8 C/RG reading of
exactly 0.4600 in state 33 is this enter grant, not state 32's word. Patching
`0x005fef3c` moves only the state-32 grant; each site has its own knob
(engagers included: `0x001f0228`→0x005ff0b4, `0x001f07f0`→0x005ff0d0).

### What "decays" speed_cmd — the 0.1908→0.1380 answer

**There is no friction or per-frame multiplier on speed_cmd anywhere in the
image.** An exhaustive scan for read-modify-write on +0x1E8 (lwc1 488 …
swc1 488, same base) returns exactly one site, and it is a clamp, not a
decay — locomotion's entry:

```
00250e44  lwc1 f1, 488(s3)
00250e48  max.s f1, f1, f0          ; f0 = 0.0 — clamp negative to zero
00250e4c  swc1 f1, 488(s3)
```

The locomotion body (`0x00250e10`, mode dispatch on +0x1F5 through the
40-entry table at `0x00587140`) only ever **caps** speed
(`min.s f0, f0, f20` at `0x00250f4c` with a turn-rate-scaled cap from
0x005ffb28-30; ball-carrier cap 0x005ffb34 at `0x00250f98`) and writes the
result to +0x1F8 and back to +0x1E8. No multiply-down, no weight, no
friction.

The measured decay is the owning state's **arrival steering re-writing the
field each frame**. State 62 (where the probe caught it), think `0x001d0930`:

```
001d0cd4  jal 0x004ad760            ; f1 = |delta to target|
001d0ce0  lwc1 f0, -27416(gp)       ; 0x005febd8 = 0.30
001d0cf0  mov.s f20, f0
001d0cfc  min.s f20, f1, f0(=1.0)   ; f20 = min(distance, 1.0)
001d0d08  c.lt.s f20, 0.25          ; inside 0.25 yd: mode 0, stop
001d0d50  lwc1 f0, -27412(gp)       ; 0x005febdc = 0.46
001d0d60  mul.s f0, f20, f0         ; speed = 0.46 * clamp(distance)
001d0d70  swc1 f0, 488(s1)          ; re-written EVERY think tick
```

0.1908/0.46 = 0.415, 0.1660/0.46 = 0.361, 0.1380/0.46 = 0.300 — the man was
0.42 → 0.30 yd from his target and closing. **The "decay" is
speed = 0.46 × distance with distance shrinking.** A statue between reps is a
man whose steering has arrived. The first-class "on skates" lever is therefore
NOT a friction constant — it is (a) staged_drive magnitude while engaged, and
(b) rep lifetime (sibling lane).

---

## Q2 — flags_0c bit 2: every setter. VERDICT: MAPPED

Consumers are everywhere (~120 `lw 12()`→`andi 4` sites — every state think
has one; the grant-block pattern is universal, one-shot by construction).
Setters are rare. Exhaustive scan (`ori` imm 4 near `sw …,12()`, plus the
event table):

**1. The anim-event dispatcher `0x0012ba18` — the real arm side.**
`0x0012ba18(player, event_u16, anim_id)` requires the player's current
animation (`0x003ad410(player+0x304)`) to equal `anim_id` (or 0xFFFF), maps
`event − 0xC000` (must be < 29) through the jump table at **0x00579F40**, and
each arm ORs one bit into `flags_0c`:

```
0012ba64  sltiu v1, a1, 29          ; event index bound
0012ba70  sll v1, a1, 2             ; table at 0x00579F40
0012ba80  jr a0
...
0012bbcc  lw v0, 12(s0)             ; arm for EVENT 0xC004:
0012bbd4  ori v0, v0, 0x0004        ;   flags |= bit 2
0012bbdc  sw v0, 12(s0)
```

**Bit 2 = anim event 0xC004.** (Bit 18 — the always-set 0x00040000 in the
slot-8 probe — is event 0xC00C at `0x0012ba88`.) The dispatcher itself has no
jal callers; its address is materialized exactly once, at `0x0012c0c0`
(lui/addiu completed `0x0012c0c8`) — registered as a callback with the anim
system. So the arm is **authored in animation data**: a keyframe posts 0xC004
("the shove/step moment"), the dispatcher sets bit 2, the state's block
consumes it once and self-clears with 0xFFFFFFFB. A held pose never re-posts
the event — that is the whole one-shot mechanism.

**2. `0x001c6d84`** — inside state 15's think (`0x001c6d08`, deliver-ball):
requires play-mode 3 (`jal 0x00154790`), flags bit 12 set, engagement kind
4/5, and current anim == 69, then `ori v0, v0, 0x0004; sw v0, 12(s1)`. A
special-case arm, not the general path.

**3. `0x00464a7c`** — library region, base not shown to be a player.
UNVERIFIED, presumed unrelated.

**Re-arm while winning — assessment.** For a LOCKED (kind-4) pair, bit 2 is
the wrong lever: the sweep re-stamps speed_cmd from staged_drive every frame
regardless (Q1), and in the sweep bit 2 only forces an anim-variant re-match
(`0x001f1e80-0x001f1eb4`: tests either man, clears both). Re-arming matters
only for the scripted kind-5/6 blocks (state 32) and non-contact states,
where the grant is the sole speed source. There, "re-arm per re-evaluation"
cannot be a data patch — no existing branch flips into a repeat — it is
either a **cave** (`flags |= 4` gated on margin) or a repost of event 0xC004
through `0x0012ba18` (which requires the anim-id match). The genuinely small
lever for sustained drive is elsewhere: the +0x42E re-lock latch (Q3/levers).

---

## Q3 — the shared bearing: computed where, frozen or re-aimed. VERDICT: MAPPED (one operand caveat)

### The computation (quoted in Q1)

The axis `s1 = atan2(packed_ref − B.pos)` — **B is the defender; the
reference point is NOT the blocker**. It is supplied by the caller:
the sweep gets it from `0x001f2ff0(sp+112, sp+128)` whose default arm packs
the **ball spot** (`jal 0x00260208` at `0x001f302c`, packed via sdl/sdr);
it consults the carrier (`0x00200040`) and two other getters first, and its
non-default arms (`0x001f3110`) were not decoded — which point is packed
mid-play is UNVERIFIED (ball spot is the default-arm reading). Operand order
of the delta IS verified: `0x004adda8` computes `a1 − a2` (out.x =
`a1.x − a2.x`, quoted from `0x004adda8-0x004addc8`), so s1 points **from the
defender toward the ball reference**.

Assignments, per arm of the double comparison (comp2 picks facings, comp1
picks the drive bearing):

| arm | staged_facing | staged_bearing (both men) |
|---|---|---|
| blocker wins comp2+comp1 | A: axis+180, B: axis | **A's +0x1A8 heading** (`0x001f158c/9c/a8`) |
| A wins comp2, B comp1 | (A: axis+180, B: axis) | **B's live +0x1EC desired_bearing** (`0x001f15fc/1608/1610`), halved drive |
| defender wins both | B: axis+180, A: axis | **the raw axis s1** (`0x001f1654/58`) |
| B wins comp2, A comp1 | (B: axis+180, A: axis) | A's +0x1A8, halved, pass-LOS clamp applies |

The facings are always the pair axis ±180 — the "frozen shared axis". The
**drive bearing is only geometry-derived when the DEFENDER wins** (he pushes
the blocker straight back along the ball axis). When the **blocker** wins,
the pair drives along the blocker's own current heading — whatever direction
he happened to be moving — which is precisely why won reps drift sideways
instead of going backward. **S2's defect is these two instructions**
(`0x001f159c/0x001f15a8` storing +0x1A8 instead of axis+180).

### Frozen or re-aimed

The staged triple is recomputed **only when the +0x42E latch is set**:

```
001f1cac  lbu v0, 78(s3)            ; +0x42E latch
001f1cb0  beq v0, zero, 0x001f1e70  ; clear -> use frozen values
001f1cb8  sb zero, 78(s3)           ; consume
001f1cc4  jal 0x001f14d0            ; re-run lock-in
```

Setters of +0x42E, exhaustively: **(a)** entering kind 4, in the transition
fn `0x001ef820` (`0x001efa5c sb v1, 1070(s0)` inside the kind==4 arm, right
after `sh s4, 1074(s0)` initializes reselect_timer — the `30 − (PBK|RBK)>>4`
formula is quoted below); **(b)** `0x001f2214`, in the kind-8 helper fn
`0x001f20f8`: every frame, if the helper's partner is in kind 4 with
`block_mode == 1`, set the partner's latch — **pass-pro pairs with a live DT
helper re-lock every frame; run pairs never do**. All other writers are
clears (`0x001f1cb8`, `0x001f73e0`, `0x001f745c`, `0x001f74e8`,
`0x001f754c` — every engagement transition). Three `sh …,78()` sites
(`0x0013c9b0`, `0x0015e888`, `0x0024b5b0`) have unconfirmed bases —
UNVERIFIED, none sits in block code.

**Answer: for a run block the bearing (and margin) is stamped once at
contact and frozen for the rep.** The 14-30-frame "re-evaluation" is not a
re-aim of a held pair — it is the rep ENDING (reselect/teardown, sibling
lane) and a fresh kind-4 entry stamping a new triple. The engine already
contains the per-frame re-lock pattern (the kind-8/pass path), so re-aiming
a held pair is a proven in-engine behaviour, not an invention.

Reselect-timer init, confirmed in the binary for the first time
(`0x001ef8d8-0x001ef8fc`, PBK at +0xB86 index 11 / RBK at +0xB88 index 12
picked by `+0x3F0 == 1`):

```
001ef8e4  lhu v0, 2950(s0)          ; PBK (or 2952 = RBK)
001ef8e8  addiu v1, zero, 30
001ef8f0  sra v0, v0, 20            ; (after sll 16): rating >> 4
001ef8f4  subu v1, v1, v0           ; 30 - rating/16
001efa34  sh s4, 1074(s0)           ; -> reselect_timer (+0x432)
```

---

## Q4 — weight: every reader in block/locomotion code. VERDICT: MAPPED

All `lwc1 …, 2796()` sites (17 by exact displacement; the docs' 19 likely
counts biased forms — the four contest-fn sites below were reached through
biased bases in prior docs):

| sites | where | use class |
|---|---|---|
| `0x0017d034`, `0x0017d148` | roster fill | copies +0xAEC → +0x1E4 |
| `0x00185c2c/34` | tackle contest 1 base `0x00185c18` | **contest score** (tackle-contest.md's +19/+11) |
| `0x0018657c/8c`, `0x00186dd4` | same tackle-contest region | contest score |
| `0x001a67fc/6800`, `0x001a68e0/e4` | pass-rush region (pass-rush.md names `0x001a6758/68`) | contest score (pairwise weight compares) |
| `0x001efd14`, `0x001efd6c` | blocking power axis (STR+AGI+trunc(weight)+PBK\|RBK) | **contest score** |
| `0x001f0cac`, `0x001f0dc4`, `0x001f0e40`, `0x001f0f30` | **inside the contest fn `0x001f0c40`** | contest score |

**Motion uses: none.** The lock-in, the sweep/copier, and locomotion
`0x00250e10` never read +0xAEC or +0x1E4. Weight reaches the drive only
through the comp1/comp2 scores, i.e. through the *margin ratio* — two
pairings with equal margins translate at identical speed regardless of mass.
S3's damping does not exist today.

One genuine mass-in-motion site exists OUTSIDE the block path: the pairing/
physics layer fn `0x00213038` (weight_copy +0x1E4 readers `0x002131f4/3200`,
`0x00213260/88`, plus `0x002128cc/d0`, `0x00212c10` in its callees):

```
002131f4  lwc1 f1, 484(s4)          ; weight A     002131f8 lwc1 f2, 0(v0)
00213208  div.s f0, f0, f2          ; normalize both weights by a shared divisor
0021325c  lwc1 f21, -25076(gp)      ; 0x005ff4fc = 335.4
00213274  mul.s f12, f12, f21
00213284  div.s f12, f20, f12       ; f12 = 1.0 / (weight * 335.4) — INVERSE MASS
```

— inverse-mass scaling of a vector (collision separation/impulse: heavier
man moves less). Its caller `0x00164710` sits in `0x00164688`, which has no
jal callers (vtable) — whether this path runs during a held block is
UNVERIFIED. It is the engine's proof that per-player inverse mass is an
established pattern, and +0x1E4 is the copy built for exactly this layer.

---

## Smallest-change lever set for S1–S3 (input to the solution agent)

Addresses, not deployables. Rule 2 applies to anything built from these.

* **S1 sustain — re-lock a held pair per evaluation.** The engine's own
  mechanism: set +0x42E while the pair holds. (a) One-word diagnostic:
  `0x001f1cb0` `beq v0, zero, 0x001f1e70` → nop makes every kind-4 pair
  re-run lock-in every frame (also re-rolls the contest RNG terms and the
  anim re-match — diagnostic only). (b) Proper shape: replicate the
  `0x001f2214` pattern (the pass/kind-8 per-frame `sb 1, 1070(partner)`)
  gated on margin, or a cave at the sweep. Because the copier already
  sustains speed_cmd every frame, S1 for a HELD pair is entirely about rep
  lifetime (sibling lane) plus margin magnitude — the margin itself is
  `div.s f20` at `0x001f15a0` (and three sibling arms), scale/curve there.
  The split-decision 0.5s at `0x001f1614/18` and `0x001f168c/0x001f16d0`
  are ready-made attenuation constants.
* **S2 backward bias — two stores.** In the blocker-wins arm, staged_bearing
  is A's heading: `0x001f159c` / `0x001f15a8` (`sw v0, 8(s2/s3)` with
  v0 = +0x1A8). The wanted value — axis+180, pointing from the ball through
  the defender — is already computed in the same function (the masked
  `s1 + 0xFF800000` in v0 at `0x001f1574`, stored as A's facing at
  `0x001f157c`). Redirecting the bearing stores to that value is a
  few-instruction cave (register pressure: v0 is clobbered between), not a
  word flip. Mirror arm at `0x001f1678/84` if B-wins-comp2/A-wins-comp1
  should also bias. The defender-wins arm (`0x001f1654/58`) already uses the
  axis and needs nothing.
* **S3 mass damping — one hook covers all arms.** The confluence
  `0x001f16dc-0x001f16e8` is the single point where f20/f21 reach both men.
  A cave there scaling f20 by a loser-weight term (weights at +0xAEC, or
  +0x1E4 as locomotion-adjacent copy; the divisor pattern to imitate is
  `0x00213208`) adds mass to the drive with zero effect on who wins.
  Tuning-data candidates already exist per site (the 50-word 0.46 family,
  e.g. `0x005fef3c` state-32 grant, `0x005fedc4` state-33 enter).
* **S5 guard already present:** the 1.5-yd pass-set freeze at
  `0x001f15cc-0x001f15f4` — do not disturb; it is the containment S5 wants.

## Could not establish

* **+0x1A8's producer** — bearing-like (BAM), confirmed consumer-side only;
  ~60 writers of displacement 424, not triaged. Whether it is "movement
  direction last frame" or "facing copy" is open.
* **Which reference point `0x001f2ff0` packs mid-play** — ball spot in the
  default arm; carrier/other arms (`0x001f3110`+) undumped.
* **Reader of staged +0x408** (comp2 margin) — writer proven, no consumer
  found in the paths walked; possibly feeds the anim-variant choice or the
  tug fields. tug_* (+0x420/424/428) writers: none in 0x001e0000-0x00200000
  by direct displacement — biased-base writers not ruled out.
* **The three `sh …,78()` sites** (`0x0013c9b0`, `0x0015e888`, `0x0024b5b0`)
  — bases unproven; if any is player+0x3E0-biased it would be an extra
  +0x42E writer.
* **Whether the inverse-mass collision path (`0x00213038` via vtable fn
  `0x00164688`) runs for engaged pairs** — needs a live breakpoint or a
  vtable trace.
* **`0x00212ee0`'s condition** (multiplies both normalized weights by 0.35 at
  `0x0021322c`, word 0x005ff4f8) — untraced.

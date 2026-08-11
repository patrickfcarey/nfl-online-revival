# DT-HOLD-90 v2 — lane 4: the PASS blast radius

Hostile static review of `patches/14F8B841.dt-hold-90.pnach`, against
`extract/SLUS_207.52` (vaddr = file_offset + 0xFF000, gp = 0x006056F0), using
`recon/mipsdis.py`. **Static only** — no rig, no emulator, no network. Every
instruction below was read out of the ELF this session; nothing is carried from
another document without re-derivation (CLAUDE.md rule 4). Claims that could
not be closed are in §6, not softened in place.

The patch's own comment block makes three assertions about pass plays. This
lane finds **one right, one wrong, and one unproven**:

| the pnach says | verdict |
|---|---|
| "engagement kind 8 occurs on pass plays … Site A fires there, so pass protection CAN move" | **RIGHT**, and stronger than stated — see §2 |
| "Watch the +0x432 reader at 0x001ca0e8 (`slti v0, v0, 61`): a 75-90 timer flips that gate for its first ~29 ticks" | **WRONG.** That gate is unreachable with a Site-A stamp in the field — §3 |
| "slot 7's pass_protection.py run is a REQUIRED regression arm" | **RIGHT, but for a different reason than the one given** — §4, §5 |

## VERDICT

```
VERDICT: PASS-AFFECTED: Site A (0x001ef918) is the sole creator of engagement
         kind 8 engine-wide and is not play-type gated, so the patched word
         executes on every pass rep that reaches kind 8. Its ONE effect is the
         length of an uninterrupted kind-8 attachment (0x001f5c4c down-counter,
         underflow -> kind 1). It does NOT reach the pass-pro assignment-drop
         gate at 0x001ca0e8 (that gate is kind==3 only, and kind 3 always
         enters with the timer zeroed). Whether the extension bites at all on
         pass is UNPROVEN and is exactly what the slot 7 regression decides.
```

---

## 1. Nothing in the pass-protection states initialises +0x432

**Q: which code initialises +0x432 for states 31/32?**
**A: none of it. Neither state handler nor any of their helpers writes the
field at all.**

State table (`docs/state-dispatch-table.md`, `0x00527238`): state **31** =
pass protection (enter `0x001cabd8`, ai_think `0x001cb008`, can_leave
`0x001cb458`, user_think `0x001cb490`); state **32** = scripted two-man block
animation (think `0x001e8088`), which owns kinds 5/6.

Exhaustive scan of every load/store in `0x001c8000-0x001cc800` (all of state
31's code region and its helpers) with a literal offset in
{82, 83, 1074, 1075, 1076, 1077} returns exactly three hits:

```
001ca0e4  86a20432  lh v0, 1074(s5)     ; the ONLY +0x432 access in pass pro — a READ
001caab8  a2420435  sb v0, 1077(s2)     ; +0x435, a different byte
001caae0  a2420435  sb v0, 1077(s2)     ; +0x435
```

The same scan over state 32's region (`0x001e7ee0-0x001e8400`) returns **zero**
+0x432 accesses; its only kind traffic is `001e81ec sw v0, 992(s0)` with
`001e81e8 addiu v0, zero, 6`.

So +0x432 is initialised entirely outside the AI states, in the global
per-frame block manager `0x001f7298` (Site A `0x001efa34`, Site B `0x001f2230`,
and the kind-setters' zeroing). Confirmed sole caller of the manager:

```
001f7298  jal 0x00154790 ; ...  beq v0,3 -> run  /  bne v0,4 -> return
001f72c8  jal 0x001f5b60   ; timer tick
001f72e0  jal 0x001ef820   ; Site A  (find_jal_targets(0x001ef820) = [0x001f72e0], sole caller)
001f733c  jal 0x001f20f8   ; Site B  (sole caller)
```

## 2. Site A executes on pass plays — and it is the *only* road into kind 8

Fn `0x001ef820` is **not state-gated and not mode-gated**. Its shape:

```
001ef854  jal 0x00260598              ; s7 = side byte at [0x00601f4c]+0x40
001ef868  jal 0x001655b0              ; s0 = GetPlayer(side=s7, index=s5)
001ef874  jal 0x0013b798 (s0+0x3E4)   ; s1 = engagement partner
001ef880  jal 0x001f7590              ; THE GATE (a0 = s0)
001ef888  10400031  beq v0, zero, 0x001ef950   ; gate==0 -> jump-table path
001ef88c  0000a02d  daddu s4, zero, zero       ; (always) s4 := 0
001ef890  8e0503e0  lw a1, 992(s0)             ; a1 = engagement kind (+0x3E0)
...
001efae0  2ea3000b  sltiu v1, s5, 11           ; loop: 11 players of one side
```

Band selection is on the **engagement kind**, never on `+0x3F0` and never on
the AI state. Kind 2-4 -> the unpatched copy at `0x001ef8e8`; kind 7-8 -> the
**patched** copy:

```
001ef8b8  14400059  bne v0, zero, 0x001efa20    ; kind 5/6 -> no change
001ef8c0  8e0303f0  lw v1, 1008(s0)             ; +0x3F0 block mode
001ef8c4  24020001  addiu v0, zero, 1
001ef8c8  54620013  bnel v1, v0, 0x001ef918     ; mode != 1 (not pass) -> +0xB88
001ef8cc  96020b88  lhu v0, 2952(s0)
001ef8d0  10000011  beq zero, zero, 0x001ef918  ; mode == 1 (PASS)     -> +0xB86
001ef8d4  96020b86  lhu v0, 2950(s0)
001ef918  2403001e  addiu v1, zero, 30          ; *** THE PATCHED WORD -> 90 ***
001ef91c  00021400  sll v0, v0, 16
001ef920  00021503  sra v0, v0, 20              ; rating/16
001ef924  00621823  subu v1, v1, v0
001ef92c  0060a02d  daddu s4, v1, zero
001ef93c  24120008  addiu s2, zero, 8           ; new kind := 8 (delay slot)
```

The `+0x3F0 == 1` arm at `0x001ef8c8-d4` is *inside the kind-7/8 band*: the
engine explicitly expects **pass-mode** blockers here and picks their pass
rating for them. That alone answers the "is pass pro even in scope" question,
but the decisive fact is a census.

**Census of every writer of the engagement kind (`sw rX, 992(rY)`), whole
image, all 12 hits:**

| vaddr | value written | note |
|---|---|---|
| `001e81ec` | 6 (`001e81e8 addiu v0, zero, 6`) | state 32 |
| **`001efa38`** | **s2 ∈ {1,2,4,7,8,unchanged}** | **Site A** |
| `001efab8` | s3 = partner's own earlier kind, or 0/9 | Site A, partner arm |
| `001f5cf4` | 2 (`001f5ce8 addiu v0, zero, 2`) | tick cluster |
| `001f73c4` | a2 | setter `0x001f7398` |
| `001f7448` | 0 | teardown |
| `001f74fc` | a2 | partner setter `0x001f74c8` |
| `001f7544` | 0 | reset helper |
| `003dd50c`, `003ebbb0`, `003efcfc`, `003efff4` | — | `sp`-based, not player structs |

Cross-checked with a full caller census of both setters (`0x001f7398`: 45 call
sites; `0x001f74c8`: 19), reading the `a2` immediate at each: the literals used
are **1, 2, 3, 5, 6, 7, 9** — **no call site anywhere passes an immediate 8**.
Two sites supply `a2` in a register (`001f536c daddu a2, s1, zero` and
`001f53a0 daddu a2, s2, zero`, both in fn `0x001f5154`); tracing every write to
those two registers in that function gives `001f5228 lw s1, 992(s3)` /
`001f5230 lw s2, 992(s4)` (a player's **own current** kind), `addiu 7`, or a
swap between the two — so they can re-assert an existing 8 but cannot create
one. *(Side effect worth carrying: re-asserting 8 through the setter zeroes
+0x432 at `0x001f73cc` without Site A re-stamping it, because `001efa2c beq a1,
s2` sees the kind unchanged — a third, patch-independent way a kind-8 rep can
die. Not traced further.)*

Therefore **the only instruction in the game that puts a player into
engagement kind 8 is `0x001efa38`, and the instruction immediately before it is
the store of the patched value**:

```
001efa2c  10b2001d  beq a1, s2, 0x001efaa4   ; kind unchanged -> no write at all
001efa34  a6140432  sh s4, 1074(s0)          ; timer := 90 - rating/16   (patched)
001efa38  ae1203e0  sw s2, 992(s0)           ; kind  := 8
```

`experiments/pass_protection.py`'s slot 7 measurement (RG oscillating kinds
7<->8 for 130+ frames) is therefore direct evidence that **this exact word ran
on that pass play**. The pnach's "pass blast radius is not zero" is correct and
the "PASS-UNTOUCHED" option is closed. *(Site B `0x001f2108` inherits the same
reachability: its per-player gate is `001f2148 lw v0, 992(s0); 001f2150 bne
v0, v1` with `001f2144 addiu v1, zero, 8` — own kind must be 8, which only
Site A can produce, on either play type.)*

## 3. The drop-check gate at 0x001ca0e8 — the pnach's stated mechanism is FALSE

**Which function, reached from where.** `0x001ca0e8` lives in the function
whose real entry is **`0x001c9e28`** — `0x001c9e24` is a `nop` pad, and a
caller scan against the padded address returns "dead code" (the same trap
`double-team-mechanism.md` recorded for the DT functions). Real answer:

```
find_jal_targets(0x001c9e28) = [0x001cb2ec]      # exactly one caller
find_jal_targets(0x001c9e24) = []                # the trap
```

A whole-image sweep of every `j` and `jal` confirms `0x001cb2ec` is the **only**
reference. `0x001cb2ec` sits inside `0x001cb004`-`0x001cb458`, i.e. **state
31's `ai_think` (`0x001cb008`)**. Nothing else reaches it — not state 32's
think, not state 31's `user_think` (`0x001cb490`), not the run-block or
lead-block states. It is the AI pass-protector's private helper, and it is the
P2 body from `docs/pass-vs-run-blocking.md`.

```
001cb2e4  0220202d  daddu a0, s1, zero        ; a0 = the blocker
001cb2ec  0c07278a  jal 0x001c9e28
001c9e3c  0080a82d  daddu s5, a0, zero        ; s5 = the blocker
```

**What the gate actually is.** The entire drop block is behind a kind test:

```
001ca07c  24020003  addiu v0, zero, 3
001ca080  8ea303e0  lw v1, 992(s5)            ; blocker's engagement kind
001ca084  14620025  bne v1, v0, 0x001ca11c    ; *** kind != 3 -> the whole block is skipped ***
001ca08c  c66101bc  lwc1 f1, 444(s3)          ; \
001ca090  c780939c  lwc1 f0, -27748(gp)       ;  | 0.088618
001ca09c  45010015  bc1t 0x001ca0f4           ;  |
001ca0a8  3c014040  lui at, 0x4040            ;  | 3.0   (P2 site)
001ca0b8  4501000e  bc1t 0x001ca0f4           ;  | three geometry tests, any of
001ca0c8  3c013fc0  lui at, 0x3fc0            ;  | which sends you to the DROP
001ca0dc  45010005  bc1t 0x001ca0f4           ; /
001ca0e4  86a20432  lh v0, 1074(s5)           ; reselect_timer
001ca0e8  2842003d  slti v0, v0, 61
001ca0ec  1440000b  bne v0, zero, 0x001ca11c  ; timer < 61 -> KEEP the man
001ca0f4  0c07dd64  jal 0x001f7590            ; timer >= 61 -> may drop; last chance
001ca0fc  14400007  bne v0, zero, 0x001ca11c  ; gate says still engaged -> KEEP
001ca104  0c07dd0a  jal 0x001f7428            ; TEARDOWN
001ca108  0000982d  daddu s3, zero, zero      ; target cleared (delay slot)
001ca114  0c07dce6  jal 0x001f7398
001ca118  24060001  addiu a2, zero, 1         ; kind := 1, partner := NULL
```

So the drop **sheds the man** — worse protection — and `timer >= 61` is what
*enables* it. That much of the pnach's worry is the right shape.

**But a Site-A stamp can never be sitting in +0x432 when this reads it.** Two
independent instruction-level facts close it:

1. **Site A never writes kind 3.** Enumerating every write to `s2` in
   `0x001ef820` (the value stored at `0x001efa38`): `0x001ef8bc` (= current
   kind, 5 or 6), `0x001ef908` = 4, `0x001ef93c` = **8**, `0x001ef94c` (=
   current kind), `0x001ef990` = 2, `0x001ef9b4` = **7**, `0x001ef9bc` (=
   current kind), `0x001ef9fc` (= current kind), `0x001efa00` = 1, `0x001efa1c`
   (= current kind). The jump table it dispatches through
   (`001ef970 addiu v1, fp, 13120`, `fp = 0x00580000` -> base **0x00583340**,
   index = kind-2) reads:
   `[2]=001efa1c [3]=001ef9b8 [4]=001ef984 [5]=001efa1c [6]=001efa1c
   [7]=001efa1c [8]=001ef9b0`. **3 is not in the produced set.**
2. **Every path into kind 3 zeroes the timer.** Kind 3 is only ever written by
   the two setters, and both zero +0x432 on the *same* player:

```
001f73a0  0080902d  daddu s2, a0, zero      ; 0x001f7398: s2 = player
001f73c0  265003e0  addiu s0, s2, 992
001f73c4  ae5103e0  sw s1, 992(s2)          ; kind := a2
001f73cc  a6000052  sh zero, 82(s0)         ; +0x432 := 0   (s0 = same player + 992)

001f74d4  0080882d  daddu s1, a0, zero      ; 0x001f74c8: s1 = player
001f74dc  263003e0  addiu s0, s1, 992
001f74f0  a6000052  sh zero, 82(s0)         ; +0x432 := 0
001f74fc  ae2603e0  sw a2, 992(s1)          ; kind := a2
```

At kind 3 the field is therefore an **up-counter from 0**, driven by the tick's
default arm (`001f5cfc addiu v1, v1, 1`; `001f5d04 sh v1, 1074(s0)`), and it
takes 61 frames of continuous kind-3 to open the gate — with a further hazard
at `001f5cd0 slti v0, v0, 21` / `001f5cdc jal 0x001f82e8` / `001f5cf4 sw v0,
992(s0)` promoting kind 3 -> kind 2 from tick 21 onward.

> **Finding: the sentence "a 75-90 timer flips that gate for its first ~29
> ticks, a path baseline timers never took" is false.** The patched value is
> stored only in the same breath as `kind := 8` (`0x001efa38`), the gate only
> reads the field when `kind == 3` (`0x001ca084`), and every transition into
> kind 3 zeroes it first. The `slti v0, v0, 61` gate behaves identically
> patched and unpatched. **The correct answer to "does the drop check make pass
> blockers shed men, hold longer, or nothing?" is: NOTHING — the patch does not
> reach it.** The pnach comment should be corrected rather than trusted.

## 4. The 7<->8 oscillation, and the 329-frame reconciliation

**Both directions of the flip are inside Site A**, and the switch is
`0x001f7590`:

* **7 -> 8** — gate != 0, kind-7/8 band, `s4` = the patched formula:
  `001ef93c addiu s2, zero, 8` then `001efa34 sh s4` / `001efa38 sw s2`.
* **8 -> 7** — gate == 0, so `001ef888 beq v0, zero, 0x001ef950` is taken and
  the jump table sends kind 8 to `0x001ef9b0`:

```
001ef9b0  1000001d  beq zero, zero, 0x001efa28
001ef9b4  24120007  addiu s2, zero, 7        ; new kind := 7
```

  `s4` was zeroed at `0x001ef88c` (a `daddu`-zero idiom, invisible to an
  `addiu` scan) and is never rewritten on that branch, so `0x001efa34` stores
  **timer := 0** alongside `kind := 7`.

**The gate itself is a distance test**, not a clock:

```
001f75d4  2c620009  sltiu v0, v1, 9          ; kind band checks on the caller's own kind
001f7618  0c12b7ca  jal 0x004adf28           ; (own +0x190, partner +0x190)
001f7620  c7819ad0  lwc1 f1, -25904(gp)      ; 0x005ff1c0 = 2.1
001f7624  46010034  c.lt.s f0, f1
001f762c  4501000f  bc1t 0x001f766c          ; within 2.1 -> jal 0x001f7698 -> s2 = its result
001f7674  0040902d  daddu s2, v0, zero
001f7678  0240102d  daddu v0, s2, zero       ; else return 0
```

**Does +0x432 participate in ending pass engagements?** The kind-8 arm of the
tick, and only on one side of the ball:

```
001f5b90  0040a82d  daddu s5, v0, zero        ; s5 = 0x00260598() = the possessing side
001f5bac  0260202d  daddu a0, s3, zero        ; s3 = outer team loop, 0..1
001f5c44  1675001e  bne s3, s5, 0x001f5cc0    ; OTHER side -> 001f5cc0: timer++ (never expires)
001f5c4c  96020432  lhu v0, 1074(s0)
001f5c50  2442ffff  addiu v0, v0, -1          ; own side: DOWN-count
001f5c58  0461002f  bgez v1, 0x001f5d18
001f5c5c  a6020432  sh v0, 1074(s0)
001f5c68  0c07dce6  jal 0x001f7398
001f5c6c  24060001  addiu a2, zero, 1         ; underflow -> kind 1 (and timer zeroed)
```

**Reconciliation of the measured 329-frame rep against a 15-30 init.** Both are
true simultaneously, and the reason is that *the counter is restarted from
scratch on every flip in either direction*. A kind-8 run only ends by underflow
if the gate holds `!= 0` for `init + 1` consecutive frames. Two readings remain
open, and they have opposite consequences for this patch:

* **(i) the counter never governs on pass.** The pair hovers around the 2.1
  boundary, the gate flips every few frames, and each 8->7 zeroes the timer
  long before the 15-30 countdown could underflow. The observed 130+ frames of
  7<->8 is the gate oscillating. **Then raising 30 -> 90 is inert on pass:** it
  raises a ceiling that is never reached. Weak supporting evidence, flagged as
  such: `m_decay_steps`'s docstring records one blocker holding +0x414 at
  1052.76990 unchanged for 283 frames, i.e. no re-lock-in recompute in that
  window.
* **(ii) the counter does govern, and kind 1 is invisible.** The underflow sets
  kind 1 during manager stage `0x001f72c8`; pair scoring at stage `0x001f72d0`
  re-attaches kind 7 (`001f4be4 jal 0x001f7398` / `001f4be8 addiu a2, zero, 7`,
  in fn `0x001f4790`, sole caller `0x001f55c8`); Site A at stage `0x001f72e0`
  re-promotes to 8 — all within one frame, so a once-per-frame PINE sample
  never sees the kind-1 seam and one "329-frame episode" is really ~11-22
  stitched 15-30-tick reps. **Then the patch triples the rep period on pass.**

Static analysis cannot choose between them: the discriminator is the *rate* at
which `0x001f7590` flips, which depends on live geometry. **The sampled
`engagement` series already in `pass_protection.py` decides it** — see §5.

## 5. Predicted slot 7 regression signature (the acceptance oracle)

The measured slot 7 baseline named in the brief: RG oscillating 7<->8 for 130+
frames; reps up to 329 frames; QB never sacked in 480 frames;
`worst_drop_early` 0.252 / `worst_drop_late` 0.678.

The patch touches **no** float, no snap counter, no contest component, no QB
code and no state handler. Its whole footprint on pass is the initial value of
one down-counter. That makes the signature sharp and mostly *negative*:

| metric | if (i) — patch inert on pass | if (ii) — patch bites | why |
|---|---|---|---|
| `recompute_steps` | **fixed** | **DOWN, materially** | upward +0x414 steps = re-lock-ins; the primary discriminator |
| `decay_steps` | fixed | UP or flat *(direction unverified)* | longer uninterrupted reps = more decay applications per rep |
| `decay_within_rep` | fixed | UP | a rep that survives longer decays further before restore |
| `longest_episode` | fixed | UP or flat | episodes cut at kind >= 2; only moves if kind-1 seams were ever sampled |
| `block_episodes` | fixed | DOWN or flat | same caveat |
| `worst_drop_early` (0.252) | **fixed** | **fixed** | step magnitude is set by the global snap-clock ramp, untouched |
| `worst_drop_late` (0.678) | **fixed** | **fixed** | same |
| `earliest_rep_frame` | **fixed** | **fixed** | the patch changes the value stamped, not when the first kind-8 occurs |
| `qb_dropback` | **fixed** | **fixed** | snap-and-hold script; no QB code touched |
| `blockers_engaged` (7 set up) | **fixed** | **fixed** | plumbing control |
| `contest_ever_nonzero` | **fixed** | **fixed** | address-map control |
| `max_snap_frame`, `play_length` | **fixed** | fixed unless the sack timing moves | run bounded by the 480-frame stop |
| `carrier_yards` | **fixed** | moves only if a sack appears/disappears | outcome; QB was never sacked at baseline |
| `rusher_gain` | fixed | *unverified* | rusher's +0x41C is raised by the same decay fn, not by +0x432 |

**The single strongest oracle is not in `METRICS` and should be added for this
run** (a new derived quantity over already-sampled fields, so it costs no extra
capture): from the per-frame `engagement` series of each offensive lineman,
**the mean length of a maximal run of kind == 8**. Prediction, stated to be
falsifiable:

* patched mean kind-8 run length ~= baseline -> reading **(i)**, patch is
  pass-inert, regression arm passes trivially;
* patched mean kind-8 run length ~= 3x baseline, capped near 75-90 -> reading
  **(ii)**, patch bites; then judge protection by `carrier_yards` and the
  operator's `blockers_held` answer.

A **fail** for the regression arm is: `carrier_yards` turning negative (a sack
that did not exist at baseline), or `blockers_engaged` dropping below 7, or
`worst_drop_early` / `worst_drop_late` moving — the last of which would mean the
patch has an effect this analysis cannot explain and the review is wrong.

## 6. Could not establish

1. **Which of §4's readings (i)/(ii) is true.** Needs the live flip rate of
   `0x001f7590`; the brief forbids the rig, and the existing slot 7 capture was
   not re-read here.
2. Whether `0x004adf28` returns a distance or a squared distance, so the
   physical size of the 2.1 hysteresis band at `0x005ff1c0` is unknown.
3. What `0x001f7698` (the gate's final call, `0x001f766c`) actually decides. It
   is the thing that flips 7<->8; only its position in the control flow is
   proven.
4. That `0x00260598` returns literally *the offense*. Proven: it returns the
   byte at `[0x00601f4c]+0x40`; that it is the side Site A iterates
   (`0x001ef868`) and the side whose kind-4/8 timers **down**-count
   (`0x001f5c44`, `0x001f5c7c`). The label "offense" is inference from those
   two uses plus the 11-player loop bound.
5. Whether the drop check at `0x001ca0e8` **ever** fires at baseline. Reaching
   it needs 61 consecutive kind-3 frames with `0x001f82e8` returning 0
   throughout (else the tick promotes to kind 2 at tick 21). Unproven in either
   direction — but unchanged by the patch either way, which is the point of §3.
6. `jalr` / non-`ra` `jr` targets inside state 31's think closure were not
   individually resolved, so "no other +0x432 access in pass pro" is a
   direct-call census (§1), not an indirect-call one. Mitigation available from
   `double-team-mechanism.md` §4 item 4 was not re-run for these addresses.
7. Site B's (`0x001f2108`) full pass blast radius. Established here only that
   its gate `kind == 8` is reachable on pass (§2); its kind-4 stamp on `p3` and
   that timer's own down-counter (`0x001f5c84`, underflow -> kind 2 + partner
   kind 9) were not traced through the pass path. **This is a second, separate
   pass-side exposure the pnach does not mention at all.**
8. Whether a longer kind-8 attachment *helps or hurts* pass protection. The
   mechanism is "the blocker stays glued to his current man longer" (via the
   re-selection gate `001efd08 lh v0, 82(s4)` / `001efd0c bnel v0, zero,
   0x001eff30`, which skips a player whose timer is non-zero). Glued to the
   right man is better protection; glued to the wrong man while a second rusher
   comes free is worse. Direction is not decidable statically.

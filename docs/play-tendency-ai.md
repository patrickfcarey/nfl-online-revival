# The hidden CPU boost is an anti-repetition tracker (`ptrk`)

Dedicated investigation (2026-08-09) into the CPU-only awareness
multiplier discovered during the sdchargersfanboy coverage work
(`sdchargersfanboy.md`): coverage break-off rolls compute `AWR + AWR·f`
for CPU-controlled defenses, with `f` fetched through a runtime pointer
(`gp−14396 = 0x00601eb4`) that is null in the ELF image. The community
has alleged hidden CPU difficulty layers in Madden for decades; this one
is now fully mapped, and it is neither score- nor clock-driven.

## What it is

A per-game **48-play rolling play-history tracker**, registry object
`'ptrk'` (constructor `0x0024d890`, 1556 bytes: a 16-byte header plus
two 48×16 record rings, one per side, plus counts). Every play, a
recompute at `0x0024d9c0` runs and stores two floats in the header:

* **`+0x00` — the repetition factor `f`.** `RepetitionFactor(side,
  playId)` at `0x0024e0f0` scans the offense's last 48 play calls for
  the play *just called* and sums recency weights from the table at
  `0x00540fe0`: **1/24 per hit in the last 12 plays, 1/48 for plays
  13–24, 1/96 for 25–36, 1/192 for 37–48**. Bounded by construction:
  `f ∈ [0, 0.9375]`. Call the same play `k` times in the last 12 snaps
  and `f = k/24`.
* **`+0x04` — a recent-success factor.** Counts completed plays gaining
  more than 2 yards, same recency-weighting idea (table `0x00540ff0`).
  Every consumer uses it as a boolean, `f > 0.25` — roughly "4+ gains
  of 2+ yards in the last 12 plays".

No score, no clock, no possession margin, no skill level, and no option
is read anywhere in the module — the sweep is closed. The inputs are the
play ring and two coach-personality DB fields (`HCOC` table). **In
franchise/season modes the ring is saved and reloaded** (`GBIN`/`STPG`
via `0x0024e458`), so your play-calling reputation follows you between
games. Both factors are hard-zeroed in practice/mini-camp modes (the
`'prac'` object's mode flag, `0x00172960`).

## Who consumes it — a one-sided census

Fifteen call sites, zero indirect, all classified. **Nine of the ten
live consumers are CPU-advantage; none are human-advantage or
symmetric.** Several are explicitly gated on the *opponent* being human:

| consumer | what gets boosted | gate |
|---|---|---|
| all five coverage states (`0x001eeaec`, `0x001ea364`, `0x001ec748`, `0x001ed99c`, `0x001be924`) | break-off AWR: `AWR + AWR·f` | defender's side has no human controller |
| `0x001a6aa0` | Break-Block contest score: `+ (s/2)·f` (up to ×1.47) | CPU side only |
| `0x00186cc8` | Tackle contest score: `+ (s/3)·f` (up to ×1.31) | **human ball-carrier AND CPU tackler** — explicitly anti-human |
| `0x001f1250` | three ball-contest floats: `×(1 + 0.25·f)` | CPU defender vs human |
| `0x00147674` | a 1-in-N event denominator: up to −25% N (≈ +33% rate), plus a further ×0.85 when the play matches the stored run/pass tendency mask | defense side |
| `0x001d2d80` | — dead call: return value clobbered before use | none |

The success factor (`+0x04`) gates five defensive-AI branches (assignment
selection, pursuit entry, a widened commit threshold) — same one-sided
pattern. The CPU play-caller (`0x001459b4`+) also reads the raw history
to *choose* its own plays.

In human-vs-human (i.e. online) none of this fires — no side has
controller 255 — so **the revival server never needs to model it.**

## What it means in play

Repeat a play against the CPU and its defenders literally get smarter
about it, immediately and measurably. Numbers for the coverage break-off
roll (`P = min(1, (AWR + ⌊AWR·f⌋)/255)`), base-85 safety, sliders 50:

| | fresh play | 4 repeats /12 | 12 repeats /12 | max (0.9375) |
|---|---|---|---|---|
| Pro | 33% | 39% | 50% | 64% |
| All-Madden | 55% | 64% | 83% | **100%** |

So the age-old advice "you can't run the same money play against the CPU
all game" is not superstition — it is a weight table at `0x00540fe0`.
Conversely, the "psychic CPU defense" feeling is mostly this tracker,
not a stat cheat: the boost is to *decisiveness* (the same break-off
mechanism documented in `sdchargersfanboy.md`), keyed to your own
repetition.

## Bonus resolution: the other hidden multipliers are Madden Cards

The two unexplained float multipliers on the effective-ratings path
(types 3 and 6 via `0x00116a58`/`0x00116b50`) and the ×4.0 penalty
multiplier's gate (`0x00116898`, key 17) all read the structure at
`*(gp−20092)` — which earlier docs called a "team-strategy table" and
which is actually the **Madden Cards active-effect list**: fourcc
`'madt'`, two sides × `{count; card[100]}`, 64-byte cards. `MCTp` is the
effect type, `MCDI` the effect key (the "17"), `MCUV` the magnitude in
percent; card data comes from the `GODA` database on disc, and the only
writer is the card-play dispatcher. Neutral is exactly 1.0 with no cards
active. So: not a hidden difficulty system — a player-facing cheat-card
feature, orthogonal to `ptrk`, and the ×4.0 penalty multiplier is simply
"someone played a penalty card". `ptrk` and `'madt'` share no code and
no data beyond the generic allocator and DB engine.

## Corrections to earlier docs recorded here

* `slider-behavior.md` said the lone human's difficulty class is
  "always 1". Almost: the setter `0x00153048` is not dead — it is
  tail-jump-reached from `0x0017992c` with the value 3, on a
  practice-flagged mode path (mini-camp). In normal play the claim
  holds.
* The "team-strategy table" naming used in earlier docs for
  `*(gp−20092)` is superseded by the Madden Cards identification above.

## Remaining runtime items (rig / PINE)

* Whether the just-called play is already in the ring when the
  recompute runs — watch `f32 [*0x00601eb4 + 0]` across a snap of a
  brand-new play: `0.0` means excluded, `0.041667` means included.
* The `MCUV` magnitudes for specific Madden Cards (`GODA` on disc —
  extractable with the ISO tooling rather than PINE, if wanted).

## Evidence index

| item | address |
|---|---|
| getter (repetition) / (success) | `0x0024e188` / `0x0024e1c0` |
| `'ptrk'` constructor, 1556 bytes | `0x0024d890` |
| per-play recompute (the writer) | `0x0024d9c0` |
| RepetitionFactor scan + weights | `0x0024e0f0`, table `0x00540fe0` |
| success factor + weights | `0x0024ce20`, table `0x00540ff0` |
| ring AddPlay / count cap (movz) | `0x0024da20` / `0x0024dad8` |
| franchise save/load of the ring | `0x0024e458` (`'STPG'` in `GBIN`) |
| practice gate | `0x00172960` (`'prac'` +382) |
| Madden Cards list `'madt'` ctor / effect getters | `0x00116668` / `0x00116898`, `0x00116a58`, `0x00116b50` |

## Practice mode, verified live (2026-08-11)

The "hard-zeroed in practice" claim above is **confirmed — but the mechanism is
in the getters, and the tracker itself keeps working**. Both matter.

Live read, in practice mode (`prac+382` = 1), after the operator had repeated
one play:

```
stored repetition f = 0.041667      <- exactly 1/24: ONE recent repeat at the
stored success    s = 0.000000         top recency weight. The table works.
ring: two 16-byte records, same play id 0xa11 in both
```

So the ring **records play calls in practice** and the recompute runs and
stores real factors. What zeroes practice is the read side: both getters call
the practice check `0x00172960` FIRST and return 0.0 without touching the
stored float when it says practice —

```
0024e190  jal 0x00172960          ; in practice?
0024e198  beq v0, zero, ->read    ; no: fall through to the real value
0024e1a0  mtc1 zero, f0           ; yes: return 0.0
```

Every consumer goes through these getters, so **no ptrk effect reaches play in
practice mode** — but a live read of the raw structure shows non-zero factors,
which is exactly the trap: probing `[0x00601eb4]+0` directly says the tracker
is boosting the CPU in practice, and it is not. Probe the getters' behaviour,
not the store.

Consequence for experiment design: rep-to-rep variation in practice with the
same play selected is **not** ptrk — it is the RNG streams resuming
mid-sequence (`rng_context` in addresses.yaml). In real game modes ptrk DOES
add adaptation on top, growing ~1/24 per recent repeat, and a savestate freezes
both: the ring is EE memory, so every harness reload resets the reputation too.

Ring record shape, observed not derived: 16 bytes -- {u32 play id, u32
0x4eda (undecoded), u32 4 (undecoded), u32 flags-like}. Two records after two
manual reps; the harness's own iterations do not accumulate because each one
reloads the state.

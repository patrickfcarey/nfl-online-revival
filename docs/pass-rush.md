# Pass rush: finesse vs power, leverage, and gap control

Investigated 2026-08-10 against `SLUS_207.52` (Madden NFL 2004), in
answer to open question #16. Corrects two claims in
`cpu-dt-animations.md` and adds a system that document missed entirely.

## Headlines

1. **A genuine finesse-vs-power model exists** — a hidden three-axis
   rating profile stamped per engagement that we had previously
   mislabelled a "leverage test".
2. **Leverage exists too, and it is the strongest term in the contest** —
   a 4× swing from the angle the rusher has won, which dwarfs every
   rating.
3. **Gap control does not exist.** A lineman has a frozen rush *lane*
   from the play data, but no gap identity and no re-fit.
4. **AI move selection reads no ratings at all.** A 95-rated rusher and a
   60-rated one draw from the same uniform distribution — which, with a
   saturating contest, is why pass rush feels undifferentiated.

## The move families: three behaviours, seven animations

`sltiu a0, s7, 7` + jump table `0x00581e40`. Moves **0–3 are
arithmetically identical to each other**, **4–5 are identical to each
other**, and **6** stands alone.

Enabling discovery: **`player+0xAEC` is PWGT — the player's weight in
pounds minus 160** (`+0xAE8` is height in inches). Proven from the
default-player template and corroborated by `tools/build_year_roster.py`.
So the unnamed "+0xAEC term" in earlier notes is *literally weight*, and
it is the power currency of the whole system.

| move | contest arithmetic | axis tested | label | evidence |
|---|---|---|---|---|
| 0 / 1 | both sides `+AGI` | finesse only | **finesse move A**, left/right ("swim/arm-over") | family proven; name inferred |
| 2 / 3 | same as 0/1 | finesse only | **finesse move B**, left/right ("spin/club") | family proven; name inferred |
| 4 / 5 | both `+AGI/2 +WGT/2`; **shedder halved unless STR ≥ 65** | finesse **and** power | **power move**, left/right ("rip") | family proven; name inferred |
| 6 | both `+WGT`; no AGI, no side, **no geometry term** | power only | **bull rush** | high confidence |

The pairing is proven by the upgrade paths: both the human
(`0x001a7218`) and the AI (`0x0019ba2c`/`0x0019ba64`) map **0→4 and 1→5
only** — so 4/5 is "the same move with force" applied to 0/1, and 2/3 is
a genuinely different move with no power version.

Animation records are **`0xFFFF`-terminated fallback chains**, not single
IDs: on a pass play move 0 tries anim 118 and falls back to 62.

**No names exist in the binary.** A full string sweep (104k strings)
returns zero hits for swim, rip, club, bull, shed, finesse, stunt, twist,
leverage, or any gap vocabulary. The only in-engine move enum is the
*ball carrier's* (Juke/Dive/Hurdle/Spin/Sprint/Stiff Arm at
`0x00522fe8`). Every family name above is inference from arithmetic.

## The finesse/power axes — the system we had missed

`0x001f0c40`, called once per engagement from the lock-in path, stamps
three randomized composites into **both** players:

| slot | blocker | defender |
|---|---|---|
| `+0x414` **POWER** | PPBK\|PRBK + STR + **WGT** | TAK + STR + **WGT** |
| `+0x418` **FINESSE** | PPBK\|PRBK + AWR/2 + STR/2 + **AGI** | AGI + STR + AWR |
| `+0x41C` overall | BLK + AWR + AGI + STR + WGT | TAK + AWR + AGI + STR + WGT |

Each then gets `+= RandInt(0, 0.33 × value)` — roughly ×[1.0, 1.33) of
jitter, **re-rolled at every lock-in**. The contest then tests:

* moves 0–3 → **finesse axis only**; win → blocker ×3/4
* moves 4–5 → **finesse AND power**; must win both → blocker ×3/4
* move 6 → **power axis only**; win → blocker ×4/5

This is a real finesse-vs-power model. `cpu-dt-animations.md` called the
`+0x14` comparison a "leverage test" — **that was wrong**; it is an
engagement-profile advantage test.

`+0x41C` is computed for both players at every lock-in and **never read
anywhere in the image** — a dead field, and a free hook for a fix.

There are also **separate finesse and power tug-of-war accumulators** per
engagement (`+0x420`, `+0x424`, `+0x428`): moves 0–3 push slot 1, moves
4–6 push slot 0, every move pushes slot 2, ±0.075 per rep and doubled for
a human holding the power button.

## Leverage: real, and the dominant term

At `0x001a6930`–`0x001a6a0c`, with the **rusher as vertex**, θ = the angle
between (rusher→blocker) and (rusher→ball-carrier):

* θ ≤ 90°: `blockScore ×= (1 − 0.5·sin θ)` → 1.0 … 0.5
* θ > 90°: `blockScore = (blockScore/2)·(1 + 0.5·cos θ)` → 0.5 … 0.25

θ=0 means the blocker is squarely between rusher and ball (full
strength); θ=180° means the rusher is completely around him (blocker at
**25%**). A textbook leverage model whose **4× span dwarfs every rating
term** in the contest.

**Correction:** `cpu-dt-animations.md` described this as "±50%". It is
1.0 → 0.25, a 4× swing.

**The bull rush is exempt** — a flag zeroed for move 6 routes it past the
whole block (`0x001a692c`). Angle-independent bull rush is thematically
right, and that gate is a one-word lever.

## Gap control: does not exist

State 30's enter reads a per-player assignment record from the play file
and derives a **rush lane**: `assignment[1] × 0.125` = distance,
`assignment[2] << 17` = direction, anchored at the snap position. The
think then runs that bearing at speed 1.0 and **exits the state once the
distance is exhausted**.

So a lineman does have an authored (angle, distance) lane — the analogue
of the zone landmark. But it is a **frozen vector from the snap**, never
re-derived; there is **no gap identity, no A/B/C, no reference to the
offensive line's alignment, and no re-fit when a blocker slides**.
Nothing recomputes it as the carrier moves. Gap control as a football
concept is absent, and no fourcc anywhere encodes a gap or technique.

## Run vs pass shedding: one system, three differences

**Correction to `cpu-dt-animations.md`: the run and pass animation tables
are swapped in that document.** `0x00526668` is the **run** table;
`0x00526710` is the **pass** table. Two independent condition chains
agree (the PPBK/PRBK rating pick and the table pick), and the play-type
classifiers confirm it: pass = playType ∈ [1,6], run = [11,18] ∪ {37,41}.

Everything else is shared. The only differences are the blocker's base
rating (PPBK vs PRBK), the animation table (the pass table merely
prepends a first-choice clip to moves 0/1 and 4/5), and a flag that
forces run mode for both once the carrier is past the line. **There is no
separate run-block-shedding system** — it is the same contest with one
rating swapped.

## Why pass rush feels undifferentiated — two independent causes

1. **Move choice ignores ratings entirely.** A full field census of
   `0x001a6c98` and its spatial helper found **not one access in the
   attribute range**. The algorithm is: uniform `RandInt`, then a remap
   that only picks which *side* of the blocker to work. Family mix is
   fixed at 2/7 finesse-A, 2/7 finesse-B, 2/7 power, 1/7 bull.
2. **The contest saturates.** With `P(win) = 1 − K/(2S)` for `S ≥ K`,
   once the shedder's ×4 multiply and the difficulty scalers push `S`
   well above `K`, the derivative with respect to any rating collapses
   toward zero. *(Published as a model, not a measurement.)*

Ratings enter the pass rush in exactly three places, none of them *which
move*: the contest score, the advantage axes, and how often he tries —
and that last one is `31 − (AWR+TAK)/32`, which uses **tackle**, not any
rusher-specific rating.

Asymmetry worth noting: the AI's state-30 path **bypasses the STR ≥ 65
gate** that a human must pass, so a CPU rusher can draw a power move at
any strength.

## Fix candidates

Caves are proven (`code-caves.md`); cave #1 (`0x00139A68`, 456 B, inside
the gameplay band) is the recommended host.

| # | goal | site | change | risk |
|---|---|---|---|---|
| **T1** | **rating-weighted AI move choice** (the headline ask) | `0x001a6d38` / `0x001a6d48` | replace the `jal RandInt` with a `jal` to a cave that reads AGI/STR/WGT and returns a *family* (0/2/4/6); the existing remap still picks the side. Cave must save `ra` | Med |
| T1-lite | shift the family mix without a cave | `0x001a6d3c` / `0x001a6d4c` | lower the RandInt bound (e.g. 4 → AI never power/bull rushes) | Low |
| T2 | who may use power moves | `0x005fe3c4/c8` (AI), `0x005fe578` (human) | lower the 165.75 (= STR 65) gate | Low |
| T3 | how hard weak rushers are punished for a power move | `0x005fe574` | independent penalty gate — do not conflate with T2 | Low |
| T4 | make the bull rush respect leverage | `0x001a692c` | nop the exemption | Low |
| T5 | make attempt *rate* depend on a rusher rating | `0x001cb984` | swap the TAK load for AGI or STR | Low |
| T6 | raise/lower the flat 25% shed-attempt rate | `0x001cb9b4` | change the immediate (flat for everyone) | Low |
| T7 | rebalance the axis payoff / synthesise a finesse rating | `0x001f0c40`, or repurpose the dead `+0x41C` | cave | Med |
| T9 | close the CPU-only STR-gate bypass | `0x001a71c0` | route state 30 through the upgrade arm; pair with T1 | Med |

## Hazard flags

The PPBK/PRBK rating loads sit in **branch-likely delay slots**
(`0x001a6758`, `0x001a6768`) — misreading them inverts the run/pass
finding. The ×3/4 rounding, the score clamps, and the move-upgrade
selects are all **conditional moves**. The axis computations contain
**REGIMM `bltz`** fixups that stock `mipsdis` prints as `.word` — miss
them and the axis formulas come out wrong. The 24-byte table stride uses
**R5900 3-operand `mult`**.

Two identifications rest on inference rather than a literal read: sin/cos
(`0x00469a30`/`0x00469b00`) by continuity of the two branches at 90°
(their coefficient table is in `.bss`, outside the file), and the
"carrier past the LOS" flag from a single float comparison. Both are
flagged for a rig check.

## Process note

Lane T reported **scratchpad contention** — another concurrent lane
overwrote a file it had written at the top level. Concurrent lanes should
use per-lane subdirectories.

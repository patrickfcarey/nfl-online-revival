# Break tackle vs tackle: why monsters don't feel like monsters

Investigated 2026-08-10 against `SLUS_207.52` (Madden NFL 2004), in
answer to open question #13.

> "I want monsters like Alstott and the Bus to FEEL their 94+ strength
> and break-tackle ratings — especially against DBs with 60s — while
> still respecting the ratings of linemen and linebackers. Is there a way
> to tune break tackle vs tackle that isn't just dropping the tackle
> slider, raising HB ability, and hoping the rest of the game survives?"

**Short answer: the wish is achievable, in four words of patch, without
touching a slider — and the reporter's instinct that sliders are the
wrong tool is arithmetically correct.**

## It is two contests, not one

1. **Does a tackle event start?** `TackleScore 0x00186b08` produces a
   number that *is* a percentage, rolled against `RandInt(0,100)`. A
   score of 0 short-circuits — no tackle attempt happens at all.
2. **If it starts, is it broken?** `BreakTackleScore 0x00186d58` is rolled
   against `RandInt(0,400)`; winning increments the carrier's
   broken-tackle counter and he keeps running.

The community's mental model — "break tackle rating fights tackle
rating" — lives in the *second* contest. Both read PBTK, but weakly.

## The formula

**Base** (`0x00185c18` — the entire rating content of contest 1):

> `T0 = K + (TAKt/16 + Wt/16 + STRt/32) − (BTKc/16 + Wc/16 + STRc/32)`

where `K = RandInt(0,25) + 1`, and weights are the player's real weight
(`player+0xAEC`, pounds − 160).

Then: a **flat additive** difficulty bonus (25/35/40/50 by class, keyed on
the tackler's side, and **skipped entirely if the score is already ≤ 0**);
a multiplicative geometry term (×0.25 … ×2.11); tackler-state penalties
minus the carrier's break bonus; the Tackling slider (×0.60 … ×1.40); and
the `ptrk` boost (up to ×1.31, human-carrier-vs-CPU-tackler only).

**Break contest:** `B = ((100−T)/3 + BTKc/16 + Wc/16 + STRc/32 + state −
(TAKt/16 + Wt/16 + STRt/32)) × class`, then **halved for each tackle
already broken this play**. Note a high tackle score directly suppresses
the break score.

## Why the differential is swamped — three reasons

**1. The dominant terms are noise and a flat constant.** Of ~100 points:

| input | span |
|---|---|
| random seed `K` | **24** |
| difficulty flat add | **25** |
| tackler weight (185→320 lb) | 9 |
| tackler TAK (50→99) | 8 |
| **carrier BTK (50→99)** | **8** |
| carrier weight | 5 |
| STR either side | 4 |

**2. Quantisation.** The `>>4` on a `rating × 2.55` scale means **one
score point per ~6.3 rating points**. So `BTK 94` and `BTK 90` are the
*same number*, and **Alstott at 94 is arithmetically identical to an
88-rated back**. Alstott and Bettis produce byte-identical results.

**3. Double saturation in the break-tackle helpers.** The two functions
that look like "the break-tackle move" **don't read PBTK at all** — they
read STR and weight, and clamp: `clamp(STR, 100, 200)` means effective
200 = **rating 78.4**, so Alstott is treated the same as a 79-STR back,
and *every tackler below 78 STR is treated as exactly 100* — a 60-STR DB
and a 78-STR linebacker give identical break resistance.

## The reporter's scenario, quantified

Escape rate per attempt, Pro, sliders 50 (model in the lane's scratch, re-runnable):

| carrier | vs CB 60/60/190 | vs SS 80/70/205 | vs MLB 95/88/245 | vs DT 85/97/305 |
|---|---|---|---|---|
| **Alstott 94/94/248** | 68.7% | 53.6% | 28.8% | 26.1% |
| scatback 94/70/205 | 53.6% | 37.1% | 22.5% | 20.4% |
| average HB 70/70/215 | 47.2% | 30.2% | 20.4% | 18.4% |

Two things jump out. The monster-minus-average gap against a DB is only
**21.5 points**. And the *scatback* — same 94 break tackle, lighter —
loses 15 points to Alstott: **almost all of the "monster" feel that
exists today is weight, not break tackle.**

## Position does not enter the contest — only weight does

Field census over the entire score chain: **zero reads of the position
byte**, no weight class, no body-type table. A 60-rated DB tackles
exactly like a 60-rated linebacker of the same weight. The only "who is
tackling" signal is weight at `/16`: a 305-lb DT gets +19, a 190-lb CB
+11. **That 8-point spread is as large as the entire 50→99 tackle-rating
range** — weight is as load-bearing as the tackle rating itself.

## Sliders make it worse — the arithmetic behind the reporter's instinct

| setting | Alstott vs CB | average HB vs CB |
|---|---|---|
| default | 68.7% | 47.2% |
| RB Ability 100 | 71.5% | **65.8%** |
| RB Ability 100 + Tackling 0 | 92.3% | **89.4%** |

RB Ability 100 buys Alstott **+2.8 points** and the average back
**+18.6** — because the rating rescaler clamps at 255 and Alstott is
already at 239 with almost no headroom. **Sliders compress the very
differential the wish is about**: the two levers together take a
21.5-point gap down to 2.9. All-Madden does the same thing from the other
side (it rewrites CPU ratings to `2r/3 + 85`, lifting a 60-TAK corner to
an effective 73).

## The fix: four words, no sliders

**F1 — double the resolution of break tackle and tackle** (`>>4` → `>>3`):

| site | current | patched |
|---|---|---|
| `0x00185c74` | `00042503` | `000424c3` |
| `0x00185c94` | `00021503` | `000214c3` |
| `0x00186dfc` | `00052d03` | `00052cc3` |
| `0x00186efc` | `00031d03` | `00031cc3` |

No overflow risk (the halfword is zero-extended before the shift).

**F2 — retune the flat class add** (`0x00153860/38/68/6c`: 25/35/40/50 →
30/42/48/58) to restore the front seven after F1. That function has
**exactly one caller**, so nothing else in the game moves.

Simulated F1+F2 against the same scenarios: Alstott vs the CB **68.7 →
78.9%**, vs the MLB **28.8 → 21.0%**; average HB vs the MLB **20.4 →
11.6%**. Monster-minus-average gap vs a DB: **21.5 → 35.3 points**. That
is exactly the wish — monsters run through defensive backs, elite front
sevens still bring them down, and everyone below elite gets *worse*.

Optional: **F4** un-saturates the strength clamps; **F5** neutralises the
`ptrk` anti-human boost. **Do not ship F3** (de-randomising the seed)
alongside F1 — together they push Alstott to a 100% escape rate against a
weak corner.

**Blast radius is closed:** the class function has one caller, the base
has three (all in the tackle module), and nothing here is shared with
blocking, catching, coverage, kicking or penalties.

## Where PBTK is actually used

Closed census: the tackle base (`−BTKc/16`, max −15), the break contest
(`+BTKc/16`, 15 of a 400 denominator = **2% of break probability**), the
ball-carrier's juke/spin/stiff-arm move chooser, an athleticism composite,
and roster grading. **The two "break the tackle" helper functions read it
zero times.**

## Hazard flags and open items

The `+35` class bonus rides a **branch-likely** delay slot. Several
multiplicative penalties ride `bc1tl`. The final clamp and the
state-34/35 break bonus are **conditional moves**. Two of the class
functions use **R5900 3-operand `mult`**, and one composite uses **MMI
`div1`/`mflo1`**, invisible to stock `mipsdis`.

Open: the tackle sub-state byte's value domain isn't closed — values 6
and 7 (which select the saturating strength helper) weren't produced by
any resolvable immediate, though three functions test for them. A PINE
watch during a tackle would settle it. The geometry mix in the tables
above is an equal-weight assumption, so **relative comparisons are robust;
absolute percentages are not.**

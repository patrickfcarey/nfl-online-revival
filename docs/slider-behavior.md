# How Madden NFL 2004's gameplay sliders actually work

Findings of a five-agent scan of `SLUS_207.52` (2026-08-08), the UI Studio
screen files from the ISO, and a second adversarial verification pass plus
an AI-behaviour lane (2026-08-09). The verification pass re-derived every
load-bearing claim from the binary and corrected this document in a dozen
places; where a claim below rests on a conditional move, a branch-likely
delay slot, or a hand-decoded REGIMM branch, it says so. The hunt brief is
`slider-threshold-hunt.md`; the tools the scan produced are `tools/lzh1.py`
(UIS decompressor) and `recon/fpudis.py` (COP1 decoder).

## The short answer

Sliders are 0–100 with 50 as exact neutral, stored as `u16` in a 131-entry
options cache loaded from the TDB settings database. Gameplay sliders all
pass through one multiplicative transform, `x' = x·(1 + S·0.02·(v−50))`;
whether that reads as a probability threshold or a rating modifier is
decided by the call site. Penalty sliders use a two-segment linear ramp
hinged at 50, where 0 is a hard off and 100 hits a per-penalty cap.
**There is no fumble slider anywhere in the game**, and **four sliders —
Awareness, Tackling, QB Accuracy, RB Ability — additionally reach AI
decision-making** by rescaling players' effective attribute ratings;
Awareness is *purely* a behaviour control, with no outcome consumer at all.

## Storage: the one true options array

* 4CC name table: `u32 fourcc[131]` at `0x0051fdc8` (index 0 = `OQLN`,
  quarter length), followed (after one zero pad word at `0x0051ffd4`) by
  the live values: `u16 value[131]` at `0x0051ffd8`. Accessors
  `GetOption 0x0015ea40` / `SetOption 0x0015ea58`; bulk loader
  `LoadAllOptions 0x0015e810` (`slti 131` bound). Data quirk: indices 7
  and 8 both carry the 4CC `OFMO` — a shipped table typo, so one of the
  two can never be addressed by name.
* Values are sourced per index range from the settings TDB via query
  fragments in the ELF: index < 71 → table `GOPT` (game options — all the
  sliders; query at `0x0057ce78`), 71–110 → `SOPT` (system, `0x0057ce58`),
  ≥ 117 → `UOPT` (user, `0x0057ce28`), ≥ 126 → database `SETT` table
  `OLOP` (`0x0059a418`); 111–116 is a dead range returning 0 (that path
  rides a branch-likely, `0x0015e720`). **For the revival server: the
  sliders are columns of the `GOPT` row, one `u16` per 4CC.**
* Index map (the parts that matter):
  * 11 `OFPN` penalties on/off; 15 `OCVW` camera (set to literal 2 at
    `0x0021c784`, and to runtime values at `0x0020a238` / `0x002b1568`)
  * **16–25 penalty sliders**: `OPFS OPHO OPFM OPOP OPDP OPPI OPCL OPIG OPRP OPRK`
  * **26–40 Human gameplay sliders** `OH*`, **41–55 the CPU mirror** `OA*`:
    AC=QB Accuracy, OP=Pass Blocking, RC=WR Catching, RA=RB Ability,
    RB=Run Blocking, AW=Awareness, DK=Knockdowns, IN=Interceptions,
    DB=Break Block, TA=Tackling, FL/FA=FG Length/Accuracy,
    PL/PA=Punt Length/Accuracy, KL=Kickoff Length
  * 60–67 and 70 EAsy Play (`EP*`); 68 `OLEX` and 69 `OFFD` sit inside
    that run but are not EAsy Play
  * 87–109 and 111–116 the `NO*` online/network block; 110 is `OHDT`,
    not part of it
* **Shipped default values are not recoverable statically.** The ELF has
  no defaults table for the 131 options, and the `GOPT` row in
  `extract/TEMPLATE.DAT` (member 9, the `SETT` database) has every `OP*`
  and slider column at 0 — a blank template. An earlier pass reported
  penalty defaults `50,15,10,0,20,5,35,10,0,30`; those numbers are words
  81–90 of the **create-a-player default record** at `0x00561a64` (see
  Red herrings) and say nothing about sliders. 50 is the design midpoint
  by construction of the transforms; the actual first-boot values would
  have to come from a retail settings save or a live console.
* Bounds: the option words themselves are never clamped (`SetOption` is a
  bare `sh`; the 0–100 bounds live in the UI Studio widget data), but two
  things bite out-of-range values downstream: `FillSliderBlock` stores
  each option with `sb`, truncating mod 256; and two transforms clamp
  their *output* (Knockdowns to 0..100, the rating rescaler to ≤255).
* EAsy Play rewrites the slider cache in place at load (`0x0015e858`+,
  all offsets re-verified): Kick Assist sets the five human special-teams
  sliders to 75 and the CPU five to 25; Pass Assist hits QB Accuracy,
  Pass Blocking, Awareness (75/25); Run Assist hits RB Ability, Run
  Block, Knockdowns, INT, Break Block, Tackling (75/25); Catch Assist
  hits WR Catching (75/25). The EAsy-Play-off path forces idx 4 = 1 and
  idx 62 = 1, zeroes idx 11, 61, 64–67, 122, and (via a branch-likely at
  `0x0015e94c`) zeroes idx 63 when idx 62 is 0.

## Gameplay sliders: one transform, applied eleven ways

`FillSliderBlock 0x00144330` copies options 26..40 and 41..55 into a
31-byte heap block — human row at +0, CPU row at +15, and **byte 30 is a
master enable flag** — whose pointer lives at `gp−19164 = 0x00600C14`.
Every transform starts by testing that flag and returns its input
unchanged when it is 0; one game mode clears it (`0x00144488` called with
0 from `0x00217a84`), turning every gameplay slider off wholesale.
`GetSliderRow 0x001443c8` picks the row by controller side (no human on
that side → CPU row).

**Eleven** dedicated functions — entry points `0x001444a0, 0x00144588,
0x00144610, 0x00144698, 0x00144718, 0x001447a8, 0x00144838, 0x001448e8,
0x001449b8, 0x00144aa0, 0x00144b18`, exactly the eleven callers of
`GetSliderRow` — apply

    x' = x · (1 + S · 0.02 · (v − 50))      50 → exact identity

with per-slider strength `S` from **0.1125 to 1.0** (kick-length group
0.20; one rating path 0.1125; QB Accuracy is the outlier with no strength
multiply at all, S = 1.0, and *inverted* — it scales an error radius).
Most read one slot; four select among slots at runtime: the blocking
function picks Pass vs Run Block by play state (two branch-likelies,
`0x001444f0`/`0x00144500`), the kick pair pick FG/Punt/KO length and
FG/Punt accuracy, and the rating rescaler picks by attribute (below).

Proven end-to-end example — Knockdowns (`0x0019bd74`): base chance 50 is
slider-scaled to `clamp(0,100, 50 + 0.75·(v−50))` (clamp = `movn` plus a
REGIMM `bltz`) and compared `sltu` against `RandomInt(0,100)` (roll in
0..99). In float32 with the EE's round-toward-zero the span is exactly
**13%–87%**: the slider can never fully disable nor force a knockdown.
WR Catching gates the drop roll against `RandomFloat` (`c.lt.s` at
`0x002573f0`); Tackling and Break Block scale integer contest scores;
Pass/Run Blocking scale a 3-float blocker impulse (S = 0.5/0.5/0.35);
kick accuracy scales an aim-error magnitude whose two sign arms collapse
to one formula only because of a branch-likely (`0x00144a4c`).

RNG: `RandomFloat(stream) 0x002f93b0` → [0,1) (capped below 1.0 via a
branch-likely; a debug byte can force 0.9999 or 0.0), `RandomInt(stream,n)
0x002f9428` → 0..n−1. Streams dispatch through a table at `0x0056E358` to
one of **three** generators: the Numerical-Recipes LCG (1664525 /
1013904223, split across `lui`/`ori` at `0x00468944`), a Park–Miller
16807, and a lagged-Fibonacci ring — which one a stream uses is a runtime
property.

## Do sliders affect AI behaviour? Four do.

The consumer census is closed: all 19 references to the slider-block
pointer, all 11 `GetSliderRow` callers, all 35 `GetOption` call sites
(none with a slider index outside `FillSliderBlock`), zero indirect
references. So the following is a proven set, not a sample.

Ten of the eleven transforms are pure outcome resolution. The eleventh,
**`0x00144b18`, rescales players' effective attribute ratings** at play
setup — same formula, applied to the rating, clamped to 255 — via the
effective-ratings builder `0x00168598`, which writes 21 halfwords per
player at `player + 0xB70 + 2·attr` for all 22 players. Those slots have
**346 read sites** across the gameplay/AI code. The slider→attribute
wiring (selection logic includes a load-bearing branch-likely at
`0x00144b94`):

| side | attribute | slider (slot) | S |
|---|---|---|---|
| defence | 2 = Awareness | Awareness (5) | 0.45 |
| defence | 16 = Tackle | Tackling (9) | 0.45 |
| offence, QB | 17, 18 (throw ratings) | QB Accuracy (0) | 0.45 |
| offence, ball-carriers | 0–4, 14, 15 | RB Ability (3) | 0.45 |
| offence, ball-carriers | 13 | RB Ability (3) | 0.1125 |

Consequences, walked to their comparisons:

* **Awareness is purely a behaviour control** — slot 5 has exactly one
  reader in the image (`0x00144b18`) and no outcome consumer. The scaled
  AWR (×0.55 at slider 0, ×1.45 at 100) feeds AI reaction timers
  (`timer = rand + (255−AWR)/32` at `0x001cb6c0`, gated by a REGIMM
  `bgez`), the per-tick probability that a defender changes AI state
  (`rand(100) < f(AWR)` at `0x001cb7f4` — a four-way AWR/1·2·4·8 split
  keyed on the *target kind*, not the AI state as an earlier note said —
  and, in every coverage state, the break-off roll `rand(255) < AWR`
  documented in `sdchargersfanboy.md`), per-player AI update-interval
  bytes (`0x001dfcd8`), a
  defensive matchup predicate whose threshold moves with both players'
  AWR (`0x001ef4e8`), and the penalty awareness modifier (`0x0025ba88`
  reads the same `player+0xB74` slot — so the Awareness slider also
  nudges penalty rates).
* **Tackling double-dips**: outcome (tackle contest score) and behaviour
  (scaled TAK feeds the `(AWR+TAK)/32` reaction timer at `0x001cb97c`).
* **QB Accuracy double-dips**: outcome (throw-error radius) and the QB's
  effective throw ratings, which build a "QB quality" factor at
  `0x001c5248`; the traced consumers of that factor are placement
  scatter, so this one is behaviour-adjacent rather than a choice.
* **RB Ability** rescales seven ball-carrier attributes that ball-carrier
  AI reads (no specific juke decision walked to its comparison — medium
  confidence).
* **The other eleven sliders never reach a rating or a decision.** In
  particular: CPU play calling, audibles, receiver targeting, coverage
  assignment, punt/FG/go-for-it and clock management read no slider,
  directly or indirectly (closed-set negative). The Interceptions slider
  is purely the catch roll — the "does the defender play the ball" gate
  (`0x00254de8`) is evaluated first and reads no slider.

Contrast — what actually drives AI difficulty: skill level. The
difficulty class from `GetDifficultyClass 0x00152ff0` feeds **25**
modifier functions (vs the sliders' 11), applied immediately *before*
the slider modifier in every shared chain. The designers' own statement
of what makes the CPU harder is `0x00153068`: difficulty class 2 boosts
*only* Awareness (`2·AWR/3 + 85`), class 3 boosts everything but
attribute 13. The only `GetOption(1)` (Skill Level) site in the image,
`0x001631b0`, sets a CPU deliberation clock (`(4−skill)·60` frames +
jitter), and `0x00153360` scales an AI re-evaluation countdown 2/4/6/8
across the classes. **Sliders scale outcomes; skill level and ratings
drive decisions — except the four sliders wired into the rating pipeline,
which drive decisions too.**

## Penalty sliders: probability with a hinge at 50

The penalty engine keeps its own byte copy of sliders, loaded from the
`GOPT` row at `0x0015ed18` — **eleven** columns (field-code list at
`0x0057ceb8`): the ten options-array `OP*` codes plus `OPPF` (Personal
Foul) inserted at manager index 6, so the manager's index order is *not*
the options-array order. Getter `0x0015fef0` normalises `s = raw/99`
(the constant at `gp−30780` is exactly float32(1/99)) and returns 0 when
`GetOption(62)` is 0. All penalties funnel through `TryPenalty
0x0015fa98` (exactly 23 direct call sites, zero indirect; it also
requires `GetOption(62) == 1` *exactly* — index 62 (`EPPN`) **is** the
master penalty enable: the Penalty Settings screen's "Penalties" on/off
row writes it, via settings id 78 in the UI's settings-id layer — see
below; resolved 2026-08-09).
The roll is `RandomFloat < chance` (`c.lt.s` / `bc1f` in `0x0015f560`).
Chance comes from `ApplySlider 0x0025d2c8`:

    raw = 0        chance = 0            (exact c.eq.s — hard off)
    raw < 50       chance = base · raw/50
    raw = 50       chance = base         (bit-exact, and raw=50 is the
                                          only value that hits it)
    raw > 50       chance = base + (cap − base) · (raw/50 − 1)
                   …but only when base < cap (c.lt.s guard at 0x0025d340);
                   equal → value untouched

(The `raw/99 · 1.98` trick equals `raw/50` to 1–2 ULP, not bit-exactly.)
Per-penalty behaviour, all verified:

| penalty | quirk |
|---|---|
| False Start | two independent generators, both 1-in-N and slider-gated: the snap-timer path (`0x001c4548`) has N ∝ (0.5/s)² — the square is unconditional (REGIMM `bltz` + `movz`, hand-verified) — so slider 100 is exactly 4× slider 50; the pre-snap-flinch path (`0x001a4c50`) is linear in 0.5/s, ~2× at 100 |
| Holding | the above-50 segment is damped ×0.4 (branch-likely at `0x0025d350`; the conclusion survives a misread only because the fall-through overwrites the register) |
| Facemask | one 15-yard branch calls TryPenalty with chance literally 0.0 — reachable code, but a roll that can never win; TryPenalty rolls its state back |
| KR/PR Catch Interference | base = cap = 100, so the `base < cap` guard makes values above 50 inert; below 50 scales down normally |
| Intentional Grounding | binary gate — any nonzero value behaves identically (`c.lt.s 0 < s` at `0x0025e350`, s then discarded; both call sites pass chance 1.0) |
| Roughing the Passer | also shrinks a post-throw window: `17 − 20·(s−0.5)` frames at `0x001c8ca8`, ≈27 → ≈7 across the range (integer endpoints depend on FPU rounding; raw 0 unreachable in practice — the penalty is hard-off there) |
| Personal Foul | present in the manager array (index 6) and read by nothing — consumer census closed |

Four penalty types — Delay of Game (1), Encroachment (3), Unsportsmanlike
(15), Personal Foul (16) — have names, yardage-table rows and handlers
but **no generating code**: the 23 call sites' type arguments cover
{0,2,4–14,17} and nothing else. An awareness modifier (`0x0025ba88`,
`chance ·= 1.2 − attr·0.4/255`, reading `lh player+0xB74` — the
slider-scaled AWR) applies on most in-play paths, plus a ×4.0 multiplier
gated on `0x00116898(otherTeam, 17, 0) == 1` — a generic "is team-strategy
slot 17 active" query; which strategy slot 17 is remains unidentified.

## The UI side

The UIS files on the ISO are `TERF` containers compressed with EA codec 5
(`LZH1`): deflate's symbol grammar, MSB-first bits, raw 4-bit code-length
tables, max match 227. Fully reversed from the ELF (`0x0047de00` parser,
`0x004ffe30`/`0x00500008` decoder) and reimplemented as `tools/lzh1.py`,
validated bit-exact on all 190 sub-files examined. The slider screens live
in `DATA/UIS_PAUC.DAT` (gameplay/custom/penalty/A.I./EAsy-Play settings);
`UIS_SETT.DAT` holds only the tab shell. Screen labels and order match the
storage map 1:1 (the A.I. screen: offense/defense/special-teams sub-tabs,
Human/CPU toggle — 15 sliders per side). The penalty screen shows ten
sliders; its on-screen order differs from storage order, and it also hosts
the master Penalties on/off (option 62 `EPPN`) and an Offsides on/off row
(option 63 `EPOS` — an earlier version of this doc wrongly said it had no
backing word).

How the screens bind to storage (extracted from the UI bytecode,
2026-08-09): the settings screens never call `GetOption`/`SetOption`.
They go through a **settings-id layer** — script natives `GetSetting`
(method 230, `0x002c5650`) and `SetSetting` (method 229, `0x002c56e0`)
resolve a settings id through a 392-entry master table (`u32
fourcc[392]` at `0x00545918`, storage-class array at `0x00545f38`)
straight to the TDB by 4CC, no scaling, no clamping. Settings ids 6–15 =
the penalty sliders, 16–30 = `OH*`, 31–45 = `OA*`, 76–83 = `EP*`. The
widget→option bindings were verified exact for the A.I. screen (defense
tab: Awareness→31/46 … Tackling→35/50, zero shift) and the penalty
screen (the claimed permutation, confirmed). The value moved between
screen and database is the raw 0–100 number; the 0–20 tick display seen
by players is a widget-formatter detail (×5 mapping consistent with
EAsy Play's 75/25 but not located in the ELF or the UIS scripts).

## Red herrings, recorded so nobody re-walks them

* `0x006243e8` (int32[110], .bss) is the **create-a-player working
  record**, not a settings object. Its words 17–39 — 23 ints defaulting
  to 50 that an early lane took for the gameplay sliders — are the
  created player's *ratings*; their only reader, `0x003c6ab0`, is a
  position-keyed rating-tier grader shared with the roster code. Its
  default image at `0x00561920` (copied by a 110-word REGIMM loop at
  `0x0035b7c0`) is also where the bogus "penalty defaults" came from.
* The copy of that struct's words 81–99 into `model+0xB2B..0xB3D` (by
  `0x0035c560`, in a **permuted** order — all 19 pairs verified) and the
  Q12 conversion at `0x00169b04` (`((v·128)/100)·32`, 100 → 4096 = 1.0)
  is **blend-shape weighting for the 3D player model**: the 20 Q12
  halfwords land in the render object at +0xE44 (selector-4 path of
  `0x0031bcb0`; selector 2 fills 74 halfwords at +0xE6C), dispatched by
  `0x0039ebc8` (whose branch-likely filters by flag mask, not weight) and
  consumed by the blenders `0x0039e518` (zero weights skipped by a
  `beql`) and `0x0039ece8` (plain `beq`). Preset weight rows at
  `0x00552170` (stride 40) are mostly single-weight but not one-hot.
  They are not gameplay and do not cross the wire (offset sweep re-run
  over load/store opcodes — the original `find_immediate` sweep couldn't
  see loads/stores at all and was right by luck).
* The attribute-name string clusters (`0x005a9620`, `0x005afbd0`) are
  franchise mini-camp and create-a-player UI respectively; neither leads
  to gameplay.
* The penalty display-name pointer table at `0x005435e8` **is** the
  engine's penalty-type enum, indexed forward by TryPenalty's type
  argument (0 = CLIPPING … 9 = PASS INTERFERENCE … 17 = ILLEGAL
  PROCEDURE), corroborated entry-by-entry by the 18-row yardage table at
  `0x00541338`. Only the string *literals* are laid out in reverse
  address order — an earlier note here calling the enum "a different
  ordering" was wrong.

## Tooling notes

* `recon/mipsdis.py:find_immediate` filters opcodes 0x08–0x0E, so it
  misses `lui` halves of 32-bit constants **and every load/store** — an
  "exhaustive offset sweep" done with it is unsound. Worth fixing.
* `mipsdis` renders all COP1 as `.word` (use `recon/fpudis.py`) and all
  REGIMM branches (`bltz`/`bgez`, opcode 0x01) as `.word` — several
  load-bearing branches in this document were hand-decoded; folding
  REGIMM + COP1 into `mipsdis` properly would remove that risk.
* Resolved since first writing (details in `play-tendency-ai.md`): the
  ×4.0 penalty multiplier's gate and the two extra multipliers on the
  effective-ratings path are **Madden Cards** effects (the structure at
  `*(gp−20092)` is the cards' active-effect list `'madt'`, not a
  "team-strategy table" as earlier text said; card magnitudes live in
  the `GODA` database on disc). The CPU-only awareness multiplier is
  the **anti-repetition play-history tracker `'ptrk'`**. Option 62 as
  the penalty master gate: resolved — see the penalty section. One
  nuance: "the lone human's team is always class 1" holds in normal
  play, but a practice-mode path (`0x0017992c` → `0x00153048`) can set
  the class word to 3.
* Still open for a runtime check (PINE/savestate on the rig): the real
  shipped slider defaults (not statically recoverable), and the
  per-frame swat-window length on deep balls.

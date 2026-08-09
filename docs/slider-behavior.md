# How Madden NFL 2004's gameplay sliders actually work

Findings of a five-agent scan of `SLUS_207.52` (2026-08-08), plus the UI
Studio screen files from the ISO. Every claim here was pinned to an address
with quoted disassembly in the underlying lane reports; this document keeps
the addresses and drops the listings. The hunt brief that launched it is
`slider-threshold-hunt.md`; the two tools it produced are
`tools/lzh1.py` (UIS decompressor) and `recon/fpudis.py` (COP1 decoder).

## The one-paragraph answer

Sliders are 0–100 with 50 as exact neutral, stored as `u16` in a 131-entry
options cache loaded from the TDB settings database. Gameplay sliders all
pass through a single multiplicative transform, `x' = x·(1 + S·0.02·(v−50))`,
where the call site decides whether `x` is a probability (then the slider
looks like a threshold) or a magnitude (then it looks like a rating
modifier). Penalty sliders use a different, two-segment linear ramp hinged
at 50, where 0 is a hard off and 100 hits a per-penalty cap exactly.
**There is no fumble slider anywhere in the game.**

## Storage: the one true options array

* 4CC name table: `u32 fourcc[131]` at `0x0051fdc8` (index 0 = `OQLN`,
  quarter length). Immediately followed by the live values:
  `u16 value[131]` at `0x0051ffd8`. Accessors `GetOption 0x0015ea40` /
  `SetOption 0x0015ea58`; bulk loader `LoadAllOptions 0x0015e810`.
* Values are sourced per index range from the settings TDB via query
  fragments in the ELF: index < 71 → table `GOPT` (game options — all the
  sliders), 71–110 → `SOPT` (system), ≥ 117 → `UOPT` (user), ≥ 126 →
  database `SETT` table `OLOP`. **For the revival server: the sliders are
  columns of the `GOPT` row, one `u16` per 4CC.**
* Index map (the parts that matter):
  * 11 `OFPN` penalties on/off; 15 `OCVW` camera (force-set to 2)
  * **16–25 penalty sliders**: `OPFS OPHO OPFM OPOP OPDP OPPI OPCL OPIG OPRP OPRK`
  * **26–40 Human gameplay sliders** `OH*`, **41–55 the CPU mirror** `OA*`:
    AC=QB Accuracy, OP=Pass Blocking, RC=WR Catching, RA=RB Ability,
    RB=Run Blocking, AW=Awareness, DK=Knockdowns, IN=Interceptions,
    DB=Break Block, TA=Tackling, FL/FA=FG Length/Accuracy,
    PL/PA=Punt Length/Accuracy, KL=Kickoff Length
  * 60–70 EAsy Play (`EP*`), 87–116 the `NO*` online/network block
* There is **no clamp anywhere in the ELF** — the 0–100 bounds live in the
  UI Studio widget data. Out-of-range values written by a patch are scaled
  without complaint (a penalty slider of 200 extrapolates to 2× its cap).
* EAsy Play literally rewrites the slider cache at load (`0x0015e858`+):
  Catch/Pass/Run/Kick Assist each set their exact slider subset to 75 for
  the human rows and 25 for the CPU rows — an independent confirmation of
  the slot map, and a useful behavioural fact in itself.

## Gameplay sliders: one transform, two apparent behaviours

`FillSliderBlock 0x00144330` copies options 26–55 into a 2×15 byte block at
`*(gp-19164) = 0x00600C34` (row 0 human, row 1 CPU; `GetSliderRow
0x001443c8` picks by controller side, falling back to the CPU row when no
human holds the side). Ten dedicated functions at `0x00144588`–`0x00144b38`
each read one slot and apply

    x' = x · (1 + S · 0.02 · (slider − 50))      50 → exact identity

with a per-slider strength `S` (0.4–1.0; float pool `0x005FDAB0`–`0x005FDB08`)
and per-function sign — QB Accuracy is *inverted* (`sub`), because the value
it scales is an error magnitude.

Proven end-to-end example — Knockdowns (`0x0019bd74`): base chance 50 is
slider-scaled to `clamp(0,100, 50 + 0.75·(v−50))` and compared with
`RandomInt(0,100)` (`sltu` at `0x0019bd90`). So the slider spans 12%–87%:
it can never fully disable nor guarantee a swat. WR Catching gates the drop
roll against `RandomFloat` (`c.lt.s` at `0x002573f0`) — threshold behaviour;
Tackling and Break Block scale integer scores feeding existing comparisons —
modifier behaviour; Pass/Run Blocking scale a 3-float blocker impulse
vector. Same transform throughout; the call site sets the meaning.

RNG: `RandomFloat(stream) 0x002f93b0` → [0,1), `RandomInt(stream,n)
0x002f9428`; behind both, the Numerical-Recipes LCG (1664525 /
1013904223) at `0x00468940` — the constants are split across `lui`/`ori`,
which is why a raw 32-bit constant search misses them.

## Penalty sliders: probability with a hinge at 50

The penalty engine keeps its own byte copy of sliders (manager+82..92,
loaded from the `GOPT` row at `0x0015ed18`; getter `0x0015fef0` normalises
to `s = raw/99`). All penalties funnel through `TryPenalty 0x0015fa98`
(23 call sites, all direct) whose roll is `RandomFloat < chance`
(`c.lt.s` at `0x0015f5f0`). Chance comes from `ApplySlider 0x0025d2c8`:

    raw = 0        chance = 0            (exact compare — hard off)
    raw < 50       chance = base · raw/50
    raw = 50       chance = base         (the shipped tuning)
    raw > 50       chance = base + (cap − base) · (raw/50 − 1)

with per-penalty base/cap constants at the call sites. Notable per-penalty
behaviour, all verified:

| penalty | quirk |
|---|---|
| False Start | different mechanism: 1-in-N roll with N ∝ (0.5/s)², so 100 ≈ 4× the default rate |
| Holding | the above-50 segment is damped ×0.4 |
| Facemask | one 15-yard branch calls TryPenalty with chance literally 0.0 — dead code |
| Off. PI / Roughing Passer | **default 0 — cannot occur on factory settings** |
| KR/PR Catch Interference | base = cap = 100: saturates at slider 50; values above 50 are inert |
| Intentional Grounding | binary gate — any nonzero value behaves identically |
| Roughing Passer | also shrinks a post-throw timing window (27→7 frames across the range) |
| Personal Foul | slider slot exists in the DB and is read by nothing |

Four penalty types — Delay of Game, Encroachment, Unsportsmanlike, Personal
Foul — have names, yardage tables and handlers but **no generating code**;
they can never be called. An awareness modifier (`0x0025ba88`,
`chance ·= 1.2 − attr·0.4/255`) and a conditional ×4.0 multiplier apply on
most in-play penalty paths; the ×4.0's trigger condition was not resolved.
One open label: `TryPenalty` also requires `GetOption(62) == 1`, and index
62 decodes as `EPPN` — the EAsy-Play-off path sets it to 1, but the 4CC
reading sits oddly with its role as the master penalty enable. Unresolved.

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
sliders; the on-screen order differs from storage order, and it also hosts
the master Penalties on/off plus an Offsides on/off row with no backing
option word.

## Red herrings, recorded so nobody re-walks them

* `0x006243e8` (int32[110], .bss) is the **create-a-player working record**,
  not a settings object. Its words 17–39 — 23 ints defaulting to 50 that an
  early lane took for the gameplay sliders — are the created player's
  *ratings*; their only reader, `0x003c6ab0`, is a position-keyed
  rating-tier grader shared with the roster code. Gameplay reads none of it.
* The copy of that struct's words 81–99 into `model+0xB2B..0xB3D`
  (by `0x0035c560`, in a **permuted** order) and the Q12 conversion at
  `0x00169b04` (`((v·128)/100)·32`, 100 → 4096 = 1.0) that consumed an
  entire lane's attention is **blend-shape weighting for the 3D player
  model** — the 20 Q12 halfwords land in the render object at +0xE44 and
  are consumed by the geometry blender at `0x0039ebc8`. They are not
  gameplay and do not cross the wire (exhaustive offset sweep).
* The attribute-name string clusters (`0x005a9620`, `0x005afbd0`) are
  franchise mini-camp and create-a-player UI respectively; neither leads to
  gameplay. The penalty *display* names (`0x005435e8` pointer table, stored
  in reverse) are UI-only; the engine's penalty-type enum is a different
  ordering, recovered separately.

## Tooling notes

* `recon/mipsdis.py:find_immediate` filters opcodes 0x08–0x0E and omits
  `lui` (0x0F), so the high half of every 32-bit constant is invisible to
  it — this cost two lanes real time. Worth a one-line fix.
* `mipsdis` renders all COP1 as `.word`; the slider/penalty decision layer
  is almost entirely FPU code. `recon/fpudis.py` (from this scan) covers
  the gap and is worth folding in properly.
* REGIMM branches (`bgez`/`bltz`) also print as `.word`; two showed up in
  load-bearing loops during this scan.

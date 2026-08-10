# Open investigations — the community question ledger

Questions asked of this project by the community (collected 2026-08-09,
"Madden 2004 uplift" list plus follow-ups). Each entry records the
question as asked, what this project already knows that bears on it,
what materials it needs, and the first investigative angle. Status
values: **open**, **leads**, **resolved**.

Reference base: the Madden 2004 (SLUS-20752) engine is now well-mapped —
`slider-behavior.md` (options/sliders/transforms), `sdchargersfanboy.md`
(coverage AI, break-off mechanism), `play-tendency-ai.md` (`ptrk`
anti-repetition tracker, Madden Cards). That map is the baseline and
search-signature source for every cross-title question below.

Titles on hand (rig, `~/Games/ps2/`): Madden 2001, **2004**, 06, 08
(+ a second copy and Ghidra project in `ps2_madden_recomp`), 09, 12,
12 Deluxe. **Not on hand: Madden 2002, 2003, 2005; NCAA 2005, 2006** —
entries needing them are blocked on materials.

---

## 1. "Concrete shoes" lateral reaction in Madden 2002 (not 2003; returns in NCAA 2006)

> Why do players in Madden 2002 have concrete-shoes reaction for lateral
> plays, but not in Madden 2003 — and can we retroactively fix it? The
> same problem returns in NCAA 2006.

**Status: open — blocked on materials (M2002, M2003, NCAA 2006 ISOs).**
What we know from 2004: defender responsiveness is governed by decision
cadences (`skillTerm + rand(0,(255−AWR)/32)` refills, `(AWR+TAK)/32`
timers) and by the locomotion command block (`player+0x1E8` speed scale,
desired bearing, and a ≤25.02° turn gate at `+0x1F5`). "Concrete shoes
on lateral plays" smells like the turn gate / turning-rate side, not the
cadence side. First angle: get the three ELFs, locate the same
locomotion command writer by idiom (the 2004 signatures — the 24-bit BAM
bearing math, `0x01000000`=360°, the 25° constant — are distinctive),
and diff the turn-rate constants and any lateral-speed penalty between
2002 and 2003. If it is a constant, the retro-fix is a pnach.

## 2. Runners over-run their blocks on pitch plays

> Why do runners over-run their blocks on pitch plays, and can we fix
> their vision/understanding of blocks? [both games and beyond]

**Status: open — investigable in 2004 now.** Relevant map: ball-carrier
AI lives in the same 93-state machine (`0x00527238`); the RB Ability
slider rescales ball-carrier attrs 0–4/13/14/15 via `0x00144b18`;
blocker↔defender engagement is a mutual link maintained by
`SetTarget 0x001f73a0` / `ClearTarget 0x001f7428`. First angle: identify
the ball-carrier states (enter/think pairs that read the carrier flag
and the locomotion block), find where the carrier's steering target is
chosen on outside runs, and check whether blocker positions/engagements
appear in that choice at all. Hypothesis to test: the carrier steers to
a play-authored landmark (like the zone landmarks at `0x001ee5f0`) and
never reads blocks — in which case "vision" was never there to fix, and
the fix is landmark/speed-scale tuning per play.

## 3. Warping / sliding / leaping defense starting in Madden 2005 & NCAA 2005

> Why did the 2005 games start with extreme warping, sliding, and
> leaping on defense instead of respecting the physics of the 2002–2004
> era? Can we fix that?

**Status: open — blocked on materials (2005 ISOs).** The 2004 baseline
is the "good" physics: coverage steering writes a normalized locomotion
command and the locomotion layer applies ratings; the swat requires
facing gates, catchable height, and distance < 6.0 (`0x0019b338` chain)
— no teleportation anywhere in the walked code. First angle: find the
2005 equivalents of the swat gates and the locomotion clamp; warping
usually means either the position is written directly (bypassing the
locomotion layer) or the turn/speed clamps got removed. The 2004
constants give the diff targets. A fix, if it is constants or a removed
clamp, may be patchable; if it is animation-driven root motion, mark
infeasible early.

## 4. Real zone blocking in Madden/NCAA 2005

> Is it possible to have real zone blocking through existing animations
> and code in 2005, and/or can we cut/fix the garbage zone plays to play
> correctly?

**Status: open — blocked on materials (2005 ISOs), with a 2004 lead.**
Lead: in 2004, per-player AI assignments are **play-file data** — chains
of `{stateId|0x80, p1, p2, p3}` records at `blob + 40·idx + 63`
(`0x0024397c` installer), not ELF constants. If 2005 kept that scheme,
"fixing garbage zone plays" may be a play-data edit (re-author the
assignment records) rather than a code patch — no new animations needed.
First angle in 2005: find the installer by idiom, dump a zone-run play's
records, and see what the blockers are actually told to do.

## 5. Targeting of pulling / lead blockers

> What is with the targeting of pulling/lead blockers and can we fix
> that?

**Status: open — investigable in 2004 now.** Relevant map: the blocking
slider function `0x001444a0` scales an existing impulse vector but does
NOT pick targets; target selection happens upstream in the engagement
system (`SetTarget 0x001f73a0`, kind codes at `player+0x3E0`). First
angle: find the block-target chooser (callers of SetTarget from
offensive-lineman states), determine its inputs (nearest? play-assigned?
threat-ranked?), and specifically what a pulling guard's state chain
does between pull and contact — the community complaint suggests the
chooser re-evaluates late or ranks the wrong defender. Fixability
depends on whether ranking weights are data or code.

## 6. Zone defenders bunch up / hook defenders abandon the middle

> Why do zone defenders often bunch up instead of playing their zones?
> Two middle hook defenders will both spread out and abandon the middle,
> leaving it wide open. Can we fix that?

**Status: RESOLVED — `zone-bunching.md`.** The reference the landmark
slides with is not a per-defender receiver — it is a **single shared
object, the ball carrier** (`ball->carrier`, QB→catcher), so every zone
defender keys the same X and slides together. There is **no
teammate-separation term anywhere** in the zone-steering code (proven by
exhaustive field census). The coefficient is centrifugal on outside
thirds (0.75) with a 3× step at the hash and no hysteresis. Shared
reference + no separation = the bunching. Recommended fix: one word at
`0x001EE664` (kill the 0.75 centrifugal arm). Four zone states mapped to
roles (37 CB, 38 hook, 39 transition, 40 deep safety). Still open only:
authored per-assignment play-data values (needs an ISO/rig read).

## 7. Default-slider behavior uplift (the meta-goal)

> Get the game to fire the animations and behaviors we want — that we
> can only get from extreme slider settings — on default settings.
> E.g. defenders contesting the ball in the air without warping/sliding.

**Status: leads — lever catalog delivered, `default-uplift-tuning.md`.**
The verified patch-point catalog (every lever pinned to its instruction,
with encodings, safety analysis, an interaction map, and a draft pnach) is
done; what remains is tuning and playtesting, plus one runtime measurement
(the swat-window frame count) that decides gate-widening vs base-raising.

The whole transform
layer is mapped, so "extreme-slider behavior at default settings" is a
constant-patching exercise, not a mystery: every gameplay slider runs
`x' = x·(1 + S·0.02·(v−50))` with per-slider S in a float pool
(`0x005FDAB4`–`0x005FDAF8`), and the interesting base rates are
literals at the call sites (e.g. knockdown base chance 50 at
`0x0019bd7c`, the eligibility gates at `0x0019b338`, the break-off
weights). Moving the *base*, not the slider, changes default behavior
while keeping sliders meaningful around it. First concrete candidates:
raise the knockdown base (contest-the-ball) without touching AWR;
retune the coverage break-off constant so discipline survives higher
AWR (decouple "react fast" from "abandon assignment" — two different
instructions, patchable separately). Deliverable shape: a gameplay-tuning
pnach with each change documented against `slider-behavior.md`.

## 8. Maxed AWR/Tackling gets strong safeties burned deep; maxed Knockdowns "fixes" it

**Status: RESOLVED — `sdchargersfanboy.md`.** Both halves real: high
effective AWR maximizes coverage *abandonment* (break-off roll AWR/255,
evaluated every 2 frames at All-Madden); Knockdowns is the only slider
that puts a thrown ball on the ground (87% per eligible frame at max,
no depth gating). The perceived "AWR boost" is swats. Companion finding:
the CPU-only anti-repetition tracker (`play-tendency-ai.md`).

## 9. CPU-only animations in pass rushing and run defense, especially at DT

> Why does the CPU have access to animations that the user doesn't in
> pass rushing and run defense, especially at DT?

**Status: RESOLVED — `cpu-dt-animations.md`.** No CPU-exclusive animation
and no controller gate. Win vs lose on the block-shed contest
(`0x001a66f8`) selects disjoint animation-ID sets (win = shed/swim/rip/
club, lose = driven-back/pancake); the contest boosts only the shedder's
score, and only on the CPU side, via the skill-class modifier
(`0x00153498`, ×1.4 at All-Madden, human always class 1) and the `ptrk`
boost (`0x001a6aa0`, CPU-only). The AI auto-shed runs for the human's DT
too (the user-think slot never suppresses it), so the human is locked out
of nothing — he loses the roll: 44% shed vs the CPU's 59% at All-Madden
(72% with a repeated play). Fix: one word at `0x001a6a98` neutralizes the
`ptrk` boost; `0x001534b8` equalizes the class modifier. Confirms #9 is a
symptom of the `ptrk` tracker (`play-tendency-ai.md`), not a separate
system.

---

## Cross-cutting notes

* Items 1, 3, 4 need ISOs we don't have (M2002, M2003, M2005, NCAA
  2005/2006). Once on the rig, the extraction path is proven:
  sector-listing + `tools/lzh1.py` for UIS-era files, `recon/mipsdis.py`
  + `recon/fpudis.py` for the ELFs, and the 2004 idiom signatures for
  fast cross-title location of the same subsystems.
* Items 2, 5, 6, 9 are 2004-native and can start any time; 6 and 9
  already have candidate mechanisms identified.
* Item 7 is the umbrella: each resolved item feeds its tuning constants
  into the default-uplift pnach.

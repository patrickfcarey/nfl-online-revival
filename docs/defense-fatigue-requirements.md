# Defensive fatigue from the run game — requirements before any patch

New campaign opened 2026-08-13. Separate code path from all blocking work (the
stamina/energy system, not the block pipeline) — captured on its own per rule 1.
**We have never investigated stamina in this project** (confirmed by census), so
this is requirement + investigation plan only. No patch until the system is
located and its accumulation/recovery model is read from the binary (rule 4).

## The vision (operator, 2026-08-13)

> The more you run the ball against a defense, the more the defense gets tired —
> more than a pass play — and it accumulates over the game, so if you have 200
> yards on a team and it's the 4th quarter (say ~30 run plays) they should be
> exhausted.

## Decomposition (four independent claims, each its own acceptance arm)

1. **Run > pass for defensive fatigue.** A defensive front-seven player loses
   more energy defending a run than a pass (more exertion: taking on blocks,
   pursuit, contact).
2. **Accumulation across the game.** Energy loss persists play to play and drive
   to drive — it does NOT fully recover between snaps. This is the crux: if the
   engine restores energy each play, there is nothing to accumulate and the
   whole feature rests on changing recovery, not consumption.
3. **Calibration target.** ~30 run plays / ~200 rush yards ⇒ the defense is
   "exhausted" by Q4 (a finetune curve, set once the field + scale are known).
4. **Fatigue must degrade performance.** An exhausted defender is measurably
   worse — slower pursuit, weaker tackle/block-shed contest — or the number is
   cosmetic. Requires finding the consumer that scales performance by energy.

## Requirement (provisional — pending the investigation)

Defending run plays drains a defensive player's runtime energy faster than
defending pass plays; energy recovers slowly enough to accumulate across a game;
low energy measurably degrades that player's pursuit/tackle/shed performance.

## Acceptance test (to build once the field is found)

- **Accumulation:** track a front-seven defender's energy field across a scripted
  sequence. After ~30 run plays it falls to an "exhausted" band (threshold TBD);
  after ~30 pass plays it falls markedly less. Monotone decline, not per-play
  reset — the direct test of claim 2.
- **Asymmetry:** run-play energy loss per snap > pass-play loss per snap, same
  player, measured.
- **Effect:** at low energy, the same defender shows degraded pursuit speed
  and/or a worse tackle/shed contest vs his fresh baseline (ties energy to
  outcome, claim 4).
- **Regression:** offensive players' stamina, the substitution/sub-in logic, and
  a fresh player's single-play performance are unchanged.

## Investigation — what must be found BEFORE any patch (gates the design)

| Q | question | why it gates |
|---|---|---|
| F1 | Is there a per-player **runtime energy/fatigue** field (distinct from the STA *rating*)? Where (offset)? | the whole feature hooks here; unknown today |
| F2 | What **consumes** it per play — sprint, contact, taking on a block? Which function decrements it? | the run>pass asymmetry is injected at the consumer |
| F3 | Does it **recover**, and how fast (per frame / per play / per drive)? | claim 2 lives or dies here — fast recovery = no accumulation |
| F4 | Is there ANY run-vs-pass differential in exertion today? | tells us if we add asymmetry or only amplify it |
| F5 | Where is energy **consumed by performance** (ratings scaled by energy)? | claim 4's hook; also the "exhausted looks slow" effect |
| F6 | The **STA rating** attribute index (`+0xB70+2i`) — governs recovery rate? | calibration + whether high-STA defenders resist it |

## Blast radius (rule 1)

The stamina system is **global** — it drives offense and defense, substitutions,
and rating scaling. A defensive run-fatigue change must not wreck offensive
stamina, break auto-subs, or nerf fresh players. Any hook is gated to defenders
on run plays; everything else defaults to stock. Play-type (run vs pass)
detection reuses the "play turned into a run" global bit already known from
`fb-wr-blocking.md` / `pass-vs-run-blocking.md` (state 72 → 31/33 decider) —
verify it is readable at the fatigue consumer.

## Investigation RESULT (2026-08-13) — full detail in `defense-fatigue-investigation.md`

The static hunt found a complete, dedicated fatigue subsystem (`"fatg"`
registration). Spot-checked against the binary this session (manager ref, fourcc,
F5 scaler, drain fn, STA index all confirmed). Answers:

- **F1 — FOUND.** Per-player fatigue lives in a `"fatg"` side table: manager
  `[0x00600CC0]`, 20-byte entries keyed by `player+0xAF2`, three f32 accumulators
  (A `+0x0`, B `+0x4`, C `+0x8`; climb = more tired, 0 = fresh). Tired latch
  `+0x11` bit0. **Finding.**
- **F2 — drain fn `0x0014FE20`** (16 AI call sites): `ΔA ≈ 0.5·(1−STA/255)·
  intensity[type]·timefactor`, B ×3; intensity table `0x0051D5A0`. **Finding.**
- **F3 (CRUX) — recovers gradually per frame (decay, STA-scaled, gated:
  resting recovers, engaged skips); NO per-frame reset exists** (closed-set:
  only writers are drain-add, recovery-sub, and the table-builder memset).
  Whether the hard memset reset fires per-play or per-game is **NOT settlable
  statically → Hypothesis: per-game (accumulation is native).** One live read
  settles it. **Gates the whole feature.**
- **F4 — no run/pass flag in the fatigue path today;** asymmetry is emergent
  (runs = more tiring actions). Explicit run>pass = add a read of the known
  "turned into a run" bit at defender drain sites. **Finding (negative).**
- **F5 — fatigue already scales the `+0xB70` effective-ratings block every tick**
  (`0x0014F1B8` ×(1−A·0.3); `0x0014F360` −B·12.75) and fires the auto-sub
  callback at a fatigue threshold. Exhaustion already degrades play. **Finding.**
- **F6 — STA (PSTA) = index 14 → `+0xB8C`;** governs both drain and recovery.
  **Finding.**

## Feasibility & plan

Feasible with clean hooks — everything the feature needs exists EXCEPT an
explicit run>pass term (a small localized add at defender drain sites, gated to
defenders). Order:
1. **LIVE READ (gates everything):** watch a front-seven defender's accumulator
   `[fatg entry]+0x0` across several snaps and play boundaries — does it climb
   and hold (accumulation, per-game reset) or zero each play (per-play reset)?
   Confirms/kills F3's hypothesis before any requirement is promoted.
2. If persistent: inject explicit run>pass at the drain sites (read the run bit,
   bump type/intensity), then calibrate intensity/recovery to the ~30-run/Q4
   curve. F5 already supplies the performance effect.

## Status

Static investigation DONE (F1–F6 above). **Requirement still provisional —
promotion gated on the F3 live read** (does fatigue accumulate across a game?).
Needs a savestate + a live read of a defender's `fatg` accumulator across snaps.

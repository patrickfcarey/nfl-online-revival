# "CPU-only" pass-rush / run-defense animations at DT (open question #9)

Investigated 2026-08-09 against `SLUS_207.52` (Madden NFL 2004). The
community perception: the CPU has shed/swim/rip/club animations at
defensive tackle that the human never seems to get. Verdict: **there is
no CPU-exclusive animation list and no controller gate on any shed
animation.** Win and lose on the block-shed contest select *disjoint*
animation-ID sets, and the contest's score is difficulty- and
`ptrk`-boosted on the CPU's side only. The human DT plays the "lose"
animations because he loses the roll more often.

## The mechanism

The block-shed contest is `BreakBlockContest 0x001a66f8` (sole caller
`TryShedMove 0x001a7130`, reached from the defender's AI-think). It
scores shedder vs blocker, then plays an animation chosen by *who won*:

* **Blocker score** = pass-block or run-block rating (by play phase) +
  Strength/3 + move-type terms. It receives **no** difficulty, slider, or
  `ptrk` modifier — in either direction.
* **Shedder score** = Strength + move-type terms, then three modifiers
  applied *only to the shedder*:
  1. skill-class (`0x00153498`, one caller): ×0.75 / 1.0 / 1.2 / 1.4 for
     Rookie / Pro / All-Pro / All-Madden — and the human is *always*
     class 1, so the human DT never gets this;
  2. the Break-Block slider (`0x00144718`, slot 8, S=0.70), identity at
     slider 50;
  3. the `ptrk` anti-repetition boost (`0x001a6aa0`, `+ (score/2)·f`,
     `f ≤ 0.9375`), gated `controller(side) == 255` — CPU only.
* **Outcome** = two independent uniform draws: `A = RandInt(0, shedScore)`,
  `B = RandInt(0, blockScore)`, shedder wins iff `B < A`. Not a threshold
  — a genuine contested roll.
* **Animation** = `0x001a7070(win, moveType)` returns a record; win and
  lose point at different ID sets. Pass-play win IDs 62/63/122/123/126/
  127/130 (shed/swim/rip/club), lose IDs 120/121/124/125/128/129/131
  (driven back / pancaked). The human sees his DT play the lose set; the
  CPU DT plays the win set — because the CPU's score is inflated.

## Why it is not a hard gate (the census)

The per-player "is this the user's guy" test is `player+8` (controller
index, 255 = AI). The whole shed/rush neighborhood was swept for it:

* The **only** controller-conditional instructions in the contest are the
  CPU-only `ptrk` gate (`0x001a6a98`) and a human-only branch
  (`0x001a7184`) — and every consequence of the human branch *favors the
  human* (an extra move-4/5 upgrade roll, a doubled shed impulse, a
  shorter engagement lock).
* The decisive question — does the engaged-with-blocker AI skip its
  auto-shed for the user-controlled DT? **No.** The state dispatcher
  (`0x001af9d0`) runs the user-think slot first and only skips the
  AI-think if it returns 1; the DL user-think (`0x0016c7e8`/`0x0016ccd0`)
  returns 0 on every path, so the AI auto-shed runs for the human's DT
  exactly as for the CPU's. Both sides reach every move type, including
  move 6 (button-reachable) and moves 4/5 (same Strength>65 + 50% roll).
* The two-man animation subsystem itself has zero controller tests.

So the human is not locked out of any animation; he is simply on the
losing end of a stacked contest more often.

## The asymmetry, quantified

Pass play, sliders 50, DT Str 88 / RG PBK 85 Str 85 (effective ratings =
rating × 2.55 on a 0–255 scale), shed wins iff `B < A`:

| shedder | P(shed wins) | vs human DT |
|---|---|---|
| **Human DT (any difficulty)** | 43.8% | — |
| CPU DT, Pro, f=0 | 43.8% | ±0 |
| CPU DT, All-Pro, f=0 | 52.4% | +8.6 |
| **CPU DT, All-Madden, f=0** | **59.2%** | +15.4 |
| CPU DT, All-Madden, f=0.5 (repeated play) | 67.3% | +23.5 |
| CPU DT, All-Madden, f=0.9375 (max) | 72.2% | +28.4 |

The CPU shedder's score is multiplied by up to ×2.06; the blocker's score
is never touched. Over a game, and especially against a repeated
offensive play, the CPU DT shows the "win" (highlight) animation set far
more than the human's — exactly the reported phenomenon.

## Fix candidates (single-word, `file_offset = vaddr − 0xFF000`)

1. **Neutralize the `ptrk` boost on Break-Block** — `0x001a6a98`:
   `1443000d` (`bne`) → `1000000d` (`beq zero,zero`). Delay slot still
   runs; clamp unaffected. Same for tackling at `0x00186cc0`:
   `14560010` → `10000010`. *Risk: very low.* **Recommended minimum** —
   it is the only term that is both anti-human by construction and
   invisible to the player.
2. **Equalize the skill-class modifier** — `0x00153498` (one caller) body
   at `0x001534b8`: `1062001a` → `1000001a` makes it identity for all
   classes, removing the ~×1.4 CPU shed power at All-Madden. Combine with
   (1) for full parity. *Risk: low.*
3. **Make the human's own bonus unconditional** — `0x001a71a8`:
   `sltu s4,zero,v0` → `addiu s4,zero,1`, so a user DT always gets the
   move-4/5 upgrade, doubled impulse, and short lock. *Risk: moderate —
   also shortens every human shed's engagement lock, changing DL pacing.*

Recommended: (1) alone for the default-uplift pnach.

## Cross-references and toolchain

* This is the same `ptrk` tracker documented in `play-tendency-ai.md`
  (Break-Block boost `0x001a6aa0`, tackle boost `0x00186cc8`) — question
  #9 is a *symptom* of it, not a separate system.
* Attribute order at `player+0xB70+2·attr` confirmed via the fourcc list
  at `0x00520140`: 2=AWR, 13=SPD, 15=STR, 16=TAK (Strength=15 is the
  contest driver here).
* Effective ratings are the 0–100 Madden rating × 2.55 on a 0–255 scale
  (proven: the STR>65 move-4/5 gate compares against 165.75 = 65×2.55).
* No REGIMM/MMI in any walked path here; but Lane L again rebuilt the
  enhanced disassembler in scratch — the standing request to fold
  REGIMM/MMI/3-operand-mult/gp-annotation into `recon/mipsdis.py` is now
  made by five lanes.

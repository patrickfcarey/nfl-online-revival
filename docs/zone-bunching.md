# Zone defenders bunch up / abandon the middle (open question #6)

Investigated 2026-08-09 against `SLUS_207.52` (Madden NFL 2004). The
community report: zone defenders bunch together instead of playing their
zones — two middle hook defenders both drift to the same side and leave
the middle wide open. The mechanism is now fully walked, and it is worse
(more fixable) than the original guess.

## The one-paragraph answer

Every zone defender's landmark slides laterally with a **single shared
reference: the ball carrier** (the QB from snap to catch, via
`ball->carrier` with a `ball->lastCarrier` fallback while the ball is in
flight). There is no per-defender receiver, no nearest-receiver search,
and — proven by exhaustive field census — **no teammate-separation term
anywhere in the zone-steering code**. So all hook defenders read the same
X and slide the same direction together; nothing pushes two of them
apart. Worse, the slide coefficient is centrifugal on outside thirds
(0.75) and has a 3× step discontinuity at the hash mark with no
hysteresis, so a QB drifting across a hash steps a defender's landmark
1.5 yards instantly. Shared reference + no separation =
the reported bunching, and it is fixable with a single one-word patch.

## How the landmark actually moves

The sliding update is `0x001ee5f0` (one caller, state 38's think). It
computes `landmark.x = base.x + k · carrier.x`, clamped to a per-state
lateral bound, where:

* **The reference (`carrier.x`)** comes from `0x001ee4d0`:
  `ball->carrier` (`0x00200040`), or `ball->lastCarrier`
  (`0x00200070`) when the ball state is 4 (in flight). `+0xB8` has a
  single writer (`SetBallCarrier 0x001fff18`); `+0xB4` is additionally
  *cleared* at three sites (`0x001fef24`, `0x001ffea8`, `0x001ffef4`) —
  which is exactly what makes the in-flight fallback fire. It is one global object —
  every zone defender keys the *same* X. (A genuine per-defender receiver
  nomination exists at `0x001edc88`, reading `p1` from the state record,
  but it feeds the man-tracking arm, never the landmark.)
* **The coefficient `k`** (`0x001ee530` classifies an X by hash mark,
  **±3.08 yd** — the pool constants are ∓3.0799999): `k = 0.5` if the *authored base* is between the hashes;
  else `0.75` if the carrier is in the same outside third as the base,
  `0.25` if not. So the outside arm is *centrifugal* and there is a **3×
  jump in the coefficient at the hash, with no hysteresis** — a one-inch
  carrier crossing steps the landmark by `0.5 × 3.08 = 1.54 yd`, and
  mirror-based defenders flip at different times (one lurches out while
  the other creeps in). *(An earlier version said 5 yards; the
  coefficient step is 3×, the positional step is 1.54 yd.)*
* **The clamp** is state-specific static data: state 38 = ±22.667
  (`0x005ff078/07c`), state 37 = ±21.667 (`0x005ff004/008`), state 40 =
  ±24.667 (`0x005fefd4/d8`) — i.e. 4/5/2 yards inside the ±26.667
  sideline. No runtime writer. (One reader each, but that is the generic
  float-pool pattern — every constant in that pool has exactly one reader
  — so it means "safe to patch individually", not "special".)

Worked example: a dead-center hook (base 0, k forced to 0.5) with the QB
rolling to +22 lands at +11 — eight yards outside the right hash. Two
hooks both outside the same hash both take the 0.75 arm and translate
outward together, gap preserved, **middle emptied**.

## Why they never separate

Exhaustive load/store field census over the entire state-38 chain (and
the sibling zone states 37/39/40): every position/heading/velocity read
resolves to exactly three base registers — self, the latched receiver,
or the ball carrier. **No loop, no teammate array, no proximity term.**
Corroborating negatives: the roster array pointer (`gp−18600`) is
confined to the accessor module; the only proximity primitive
(`FindNearestPlayer 0x001657c0`) is reached from state 37's think chain
(`0x001ed878` → `0x001eca68` → `0x001ed060`) but with the side filter set
to the **offense**, so it is a receiver search, not separation — no zone
state scans its own side; the state-machine's spacing hooks are stubs
(`return 0/1`). (An earlier version of this paragraph said
`FindNearestPlayer` was "never called by a zone state" — it is, just not
for teammates — and said the
"wide-scan variants `0x0016589c`/`0x00165e58` have zero callers". That is
wrong and was corrected by the free-space survey: `0x0016589c` is *inside*
the live `FindNearestPlayer 0x001657c0`, and `0x00165e58` is referenced by
a function-pointer word in `.data` at `0x00529154`. Neither is dead, and
neither is called by a zone state — the conclusion stands, the evidence
sentence did not.)
Nothing repels two defenders in adjacent zones. Confident negative.

## The four zone states (roles identified)

| state | role | evidence |
|---|---|---|
| 37 | CB outside zone (flat/deep-outside) | enter requires `position == 16` (CB) for the jam; sideline-referenced clamp; shallow depths — high confidence |
| 38 | underneath hook / curl | LOS+11 drop (18 for MLB **and** \|ΔX\| < 6.0 — the ΔX condition was omitted in the first version), 270° facing, the sliding landmark — high confidence |
| 39 | intermediate/transition drop (a phase, no landmark, no lateral clamp) | medium |
| 40 | deep safety zone | handler gated to positions 17/18 (FS/SS), 270° facing, AWR-scaled hold timer — high confidence |

Structural caveat: for several play types state 38 runs the landmark path
only on the first frame then degrades to a slow shuffle, which only makes
sense if these states are **phases chained by play-file data**
(`{id|0x80,p1,p2,p3}` records) — so "which state = which zone" is partly
"which state = which phase". The assignment-class correlation couldn't be
closed (no play files in the repo; `TEMPLATE.DAT` has none).

## The slide is not per-frame (added 2026-08-09)

A verification pass found an omission that matters for tuning: the whole
landmark block — including the `jal 0x001ee5f0` — runs only on frames
where a byte timer at `[state+2]` underflows (`0x001ee8d0 bgez`, REGIMM;
`0x001ee94c beql`, branch-likely). The re-arm is **difficulty- and
AWR-scaled**: `0x001535f8` returns 11 / 6 / 4 / 2 frames for Rookie /
Pro / All-Pro / All-Madden, plus `RandInt(0, (255−AWR)>>5)`. So the zone
landmark carries a reaction latency, and **Awareness does buy decision
cadence here** — consistent with the "AWR is decisiveness" finding.

## Fix candidates

| # | change | address | current → patched | effect | risk |
|---|---|---|---|---|---|
| **a1 (recommended)** | kill the centrifugal arm, 0.75 → 0.25 | `0x001EE664` | `3C013F40` → `3C013E80` | removes the outward drag and the hash discontinuity (both arms 0.25); keeps mirror behaviour, depths, break-off, siblings | **Low** — one immediate, no control flow |
| a2 | middle arm 0.5 → 0.25 | `0x001EE640` | `3C013F00` → `3C013E80` | midfield hook slides 5.5 yd not 11 for a +22 carrier | Low |
| c | freeze the slide entirely | `0x001EE610` | `beq s0,zero` → `beq zero,zero` | landmark stays at enter value; fully static zones | Low-med — QBs scrambling get easier |
| b | tighten the state-38 clamp | `0x005FF078`/`07C` | ±22.667 → ±12 or ±8 | hard cap on lateral pull | Med — yanks any wide-authored state-38 zone inward; needs a play-data check |
| c′ | per-defender receiver nomination | — | — | infeasible as a byte patch (needs a code cave + new arg) | High |
| d | real teammate separation | — | — | no reusable avoidance fn exists; code cave calling `FindNearestPlayer` | High |

**Recommended minimal patch: a1 alone** — it removes the two mechanisms
that actually produce "both hooks spread out" (the centrifugal 0.75 arm
and the hash discontinuity) while leaving everything else intact.
Consider a1 + b(±12) if a play-data check clears the flat-zone risk.

## Notes for the toolchain and next lanes

* A shipped dead-store: state 38's authored landmark **Y** and the
  second component of the `p2` nudge are computed and never read (drop
  depth comes from LOS+11 instead).
* The hash classifier has **no hysteresis** — the coefficient chatters if
  the carrier sits on a hash; a hysteresis band would be a cleaner fix
  than a1 but needs a code cave.
* Field frame established canonical: offense attacks +Y, X lateral 0 at
  center, ±26.667 sidelines, ±3.083 hashes; BAM `0x01000000` = 360°.
* Lane K (like earlier lanes) rebuilt an enhanced disassembler in scratch
  adding REGIMM, MMI, R5900 3-operand `mult`, and gp annotation — the
  standing request to fold these into `recon/mipsdis.py` is now made by
  four separate lanes and should be done.

## Runtime / materials still open

* The authored `p1/p2/p3` values for real hook assignments (needs a play
  file off an ISO or a PINE read on the rig) — affects the authored
  bases, not the mechanism.
* The assignment-class → state-id mapping (play-file data).

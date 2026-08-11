"""Does the pass-protection collapse decay actually run, and what drives its clock?

`pass-vs-run-blocking.md` (P3) records four single-use floats that govern a
"pocket collapse" decay on the blocker's three block-contest components. That
claim was `doc`-grade -- read out of a document, never re-derived -- which is
exactly the class of claim CLAUDE.md rule 4 says to distrust. It has now been
re-derived against the binary, and the derivation changed what this experiment
should measure.

## What the binary says (verified 2026-08-11, not quoted from a doc)

The four floats are gp-relative, which is why `find_address_refs` reports zero
hits for them: it only pairs `lui`/`addiu`. Found instead by scanning every
load/store with `$gp` as base.

    005ff100 = 0.00555556 (1/180)   005ff104 = 0.95
    005ff114 = 0.00333333 (1/300)   005ff118 = 0.45

The function that consumes them takes two players and derives both contest
blocks from them:

    001f0c5c  daddu s5, a0, zero        ; s5 = blocker
    001f0c54  daddu s6, a1, zero        ; s6 = rusher
    001f0c74  addiu s1, s5, 1028        ; s1 = blocker + 0x404
    001f0c6c  addiu s2, s6, 1028        ; s2 = rusher  + 0x404

1028 is 0x404, so `16/20/24(s1)` **are** +0x414/+0x418/+0x41C. That confirms
the offsets in `addresses.yaml` against the image; they are no longer doc-grade.

The decay itself, at 0x001f109c-0x001f1100:

    001f109c  lui at, 0x4334            ; 180.0
    001f10a4  lwc1 f1, -26096(gp)       ; 1/180
    001f10a8  min.s f20, f20, f0        ; f20 = min(frames_since_snap, 180)
    001f10b0  mul.s f20, f20, f1        ;     / 180          -> a 0..1 ramp
    001f10bc  mul.s f12, f20, f12       ;     * 0.95
    ...       jal 0x001532d0            ; (ramp*0.95, byte at rusher+1) -> f0
    ...       jal 0x002f93b0            ; a fresh random per component
    001f10d0  lwc1 f1, 16(s1)           ; blocker + 0x414
    001f10d8  mul.s f0, f1, f0
    001f10dc  sub.s f1, f1, f0
    001f10e4  swc1 f1, 16(s1)           ; *** STORED BACK ***

**The multiply is written back into the player struct.** That is the finding
that makes this experiment possible at all: had the decay been applied to a
copy at contest time, the field would read flat from memory and this spec would
have "refuted" a mechanism that was really there. It is a read-modify-write, so
it compounds every time the function runs.

A second ramp at 0x001f11c4 does the same over `min(frames, 300)/300 * 0.45`.
The rusher gets the opposite treatment: his +0x41C is *increased* by a random
fraction (0x001f1050, using 0x005ff0fc).

## The question that is actually open

The ramp reads **`frames_since_snap`** -- `[[0x00601280]+0x54]`, the same
global counter this harness already samples -- and *not* a per-engagement
timer. If that reading is right it has a sharp, falsifiable consequence:

> A block that locks up late should begin **already deep into the decay**,
> rather than starting fresh.

That is the headline. It is also the difference between "pass protection
weakens as the play ages" (a design choice) and "a blocker who engages at
frame 150 is born at 21% strength" (a bug that would explain why late help
never helps).

What is *not* known, and what this spec is shaped to find out rather than
assume: **the call cadence**. A per-frame read-modify-write with a factor up
to 0.95 would drive the components to zero within a few frames, so the
function is presumably called at lock-in or on some re-evaluation interval,
not every frame. This spec therefore *describes* the observed series -- where
it resets, how far it falls, what it starts at -- instead of asserting a curve
it has not seen. The cadence falls out of the data.

## Why this play, and why the composites need segmenting

The composites are recomputed from raw ratings at every lock-in (nothing
accumulates between reps -- `pass-vs-run-blocking.md`), so the raw series is a
sawtooth: high at lock-in, decaying within the rep. Any statistic taken over
the whole play averages across resets and sees nothing. Every metric below is
therefore computed **per engagement episode**.
"""

from __future__ import annotations

import struct
from typing import Any, List, Optional, Sequence, Tuple

from tools.madden_lab.trial import (EntitySelector, Frame, InputEvent,
                                    LoadConfirm, Metric, OperatorAsk,
                                    SampleSpec, Samples, StopCondition, Trial)
from tools.madden_lab.world import Handle

# --------------------------------------------------------------------------
# Cost declaration -- see menu.py. UNMEASURED: these are estimates carried
# over from lead_blocker.py scaled by field count (14 field-slots here against
# its 9) and by the longer play. Replace both with measured values after the
# first full run, exactly as that spec's comment records having done.
# --------------------------------------------------------------------------

#: Wall-clock seconds per iteration, including savestate load and confirm.
SECONDS_PER_ITERATION = 13.0
#: Output megabytes per iteration. Deliberately wide while unmeasured.
MB_PER_ITERATION = (14.0, 32.0)

SIDE_OFFENSE = 0
SIDE_DEFENSE = 1
OFFENSE = "player:%d:" % SIDE_OFFENSE
DEFENSE = "player:%d:" % SIDE_DEFENSE
PLAYER = "player:"

SNAP_BUTTON = "cross"

#: Interior and edge line positions, from the slot 7 dump: 5=LT 6=LG 7=C 8=RG
#: 9=RT. Used only for *reporting*, never for selecting who to measure -- see
#: `_blockers`, which finds blockers behaviourally for the same reason
#: lead_blocker.py refuses to name a position constant.
LINE_POSITIONS = (5, 6, 7, 8, 9)
POSITION_QB = 0

#: Double indirection: `gp-17520` at 0x00601280 holds a pointer; the counter is
#: at +84 off it. This is the same counter the decay ramp reads.
SNAP_COUNTER_PTR = 0x00601280
SNAP_COUNTER_OFF = 84

#: The engine's own spot object; +0x10 is the line of scrimmage (15.000 here).
SPOT_PTR = 0x00601F4C
SPOT_LOS = 0x10

#: This savestate's own pre-snap anchors, read out of slot 7's live memory.
#: NOTE: slot 6 and slot 7 are the SAME formation with different play calls, so
#: these anchors CANNOT tell the two states apart. That is why `m_qb_dropback`
#: exists: it is a post-snap control that can.
PLAYER_DESC_PTR = 0x00600E48
PLAYER_POS_X = 0x190
PLAYER_POS_Y = 0x194
QB_SPOT = (0.0000, 13.4000)
HB_SPOT = (-0.0403, 7.9718)
SPOT_TOL = 0.35

#: An engagement kind of 2+ is "committed to a man"; 4+ is live contact.
KIND_COMMITTED = 2


def _f32(emu, addr: int) -> float:
    return struct.unpack("<f", emu.read(addr, 4).to_bytes(4, "little"))[0]


def loaded_state_is_pre_snap(emu) -> bool:
    """Snap counter parked at 0 and the formation on its marks.

    Same predicate as lead_blocker.py, and the same reasoning: a counter test
    alone is satisfiable by the *outgoing* world, because a finished play parks
    the counter at 0 too.

    The honest limit here is stated in `cannot_conclude`: this proves a
    pre-snap world in this formation, not that the world came from slot 7.
    """
    pointer = emu.read(SNAP_COUNTER_PTR, 4)
    if not 0x00100000 <= pointer < 0x02000000:
        return False
    if emu.read(pointer + SNAP_COUNTER_OFF, 4) != 0:
        return False
    desc = emu.read(PLAYER_DESC_PTR, 4)
    if not 0x00100000 <= desc < 0x02000000:
        return False
    base = emu.read(desc, 4)
    if not 0x00100000 <= base < 0x02000000:
        return False
    stride = 5312
    for slot, (x0, y0) in ((0, QB_SPOT), (1, HB_SPOT)):
        b = base + slot * stride
        if abs(_f32(emu, b + PLAYER_POS_X) - x0) > SPOT_TOL:
            return False
        if abs(_f32(emu, b + PLAYER_POS_Y) - y0) > SPOT_TOL:
            return False
    return True


class PlayEnded:
    """True once the snap counter stops advancing for `patience` samples."""

    def __init__(self, patience: int = 8):
        self.patience = patience
        self._last: Optional[int] = None
        self._stalled = 0

    def reset(self) -> None:
        self._last = None
        self._stalled = 0

    def __call__(self, frame: Frame) -> bool:
        current = frame.get("game", "frames_since_snap")
        if not isinstance(current, int):
            return False
        if self._last is not None and current <= self._last:
            self._stalled += 1
        else:
            self._stalled = 0
        self._last = current
        if current <= 0:
            self._stalled = 0
        return self._stalled >= self.patience


# --------------------------------------------------------------------------
# Episode segmentation -- the thing every metric here is built on
# --------------------------------------------------------------------------


def _offense(samples: Samples) -> List[str]:
    return samples.entities(OFFENSE)


def _defense(samples: Samples) -> List[str]:
    return samples.entities(DEFENSE)


def _episodes(samples: Samples, entity: str,
              min_frames: int = 4) -> List[Tuple[int, int]]:
    """Contiguous runs where `entity` is committed to a man (kind >= 2).

    Returned as (first_index, last_index) into the sample series, not frame
    numbers, so callers can line them up with any other field.

    `min_frames` drops one- and two-sample flickers. PINE reads are not
    synchronised with emulation and a snapshot can tear, so a bare `!= 0` test
    over a few hundred frames will manufacture episodes out of noise -- and
    since the headline statistic is computed *per episode*, a spurious
    two-frame episode contributes a garbage decay fraction with the same weight
    as a real 90-frame rep.
    """
    runs: List[Tuple[int, int]] = []
    start: Optional[int] = None
    last: Optional[int] = None
    for index, value in samples.series(entity, "engagement"):
        live = isinstance(value, int) and value >= KIND_COMMITTED
        if live and start is None:
            start = index
        elif not live and start is not None:
            if last is not None and last - start + 1 >= min_frames:
                runs.append((start, last))
            start = None
        if live:
            last = index
    if start is not None and last is not None and last - start + 1 >= min_frames:
        runs.append((start, last))
    return runs


def _at(samples: Samples, entity: str, field: str, index: int) -> Optional[float]:
    for i, value in samples.series(entity, field):
        if i == index:
            return None if value is None else float(value)
    return None


def _span(samples: Samples, entity: str, field: str,
          lo: int, hi: int) -> List[float]:
    return [float(v) for i, v in samples.series(entity, field)
            if lo <= i <= hi and v is not None]


def _blocker_episodes(samples: Samples) -> List[Tuple[str, int, int]]:
    """Every (entity, start, end) episode on the offence, ordered by start.

    Blockers are found behaviourally -- anyone on offence who commits to a man
    -- rather than by position byte. On this savestate the RB and TE stay in to
    block, so a line-position filter would silently drop two of the seven
    blockers the operator actually set up.
    """
    out: List[Tuple[str, int, int]] = []
    for entity in _offense(samples):
        for start, end in _episodes(samples, entity):
            out.append((entity, start, end))
    out.sort(key=lambda row: row[1])
    return out


def _decay_fraction(samples: Samples, entity: str,
                    start: int, end: int) -> Optional[float]:
    """How much of its starting value the component loses within one rep.

    **Zeros are excluded, and that is not a detail.** An engagement episode
    begins a few frames before the contest triple is populated, so the raw
    series opens with 0.0. An earlier version of this took `min()` over the
    whole span and reported a decay fraction of 1.000 -- "the component fell to
    nothing" -- when what it had actually measured was the field being
    *unpopulated* and then filled in. It made a flat rep look like a total
    collapse, and it made the mechanism look confirmed on an iteration where
    that blocker's triple never moved at all.
    """
    values = [v for v in _span(samples, entity, "contest_power", start, end)
              if abs(v) > 1e-9]
    if len(values) < 3:
        return None
    top = max(values)
    if top <= 0.0:
        return None
    return float((top - min(values)) / top)


def _steps(samples: Samples, entity: str) -> List[Tuple[int, float]]:
    """Every change in +0x414 while committed, as (snap_frame, fraction).

    Negative entries are decay events; positive ones are recomputes at a new
    lock-in, which reset the component from raw ratings. The series is sparse
    and irregular -- roughly a dozen changes across two hundred frames -- so
    any statistic taken per *sample* rather than per *change* mostly measures
    how long the value sat still.
    """
    out: List[Tuple[int, float]] = []
    previous: Optional[float] = None
    committed = set()
    for start, end in _episodes(samples, entity):
        committed.update(range(start, end + 1))
    for index, value in samples.series(entity, "contest_power"):
        if value is None or index not in committed:
            continue
        current = float(value)
        if abs(current) <= 1e-9:
            continue
        if previous is not None and abs(current - previous) > 1e-4 and previous > 0:
            snap = _at(samples, "game", "frames_since_snap", index)
            out.append((int(snap if snap is not None else index),
                        (current - previous) / previous))
        previous = current
    return out


def _worst_drop(samples: Samples, lo: int, hi: int) -> Optional[float]:
    """Largest single-step fractional drop with a snap frame in [lo, hi)."""
    drops = [-fraction
             for entity in _offense(samples)
             for snap, fraction in _steps(samples, entity)
             if fraction < 0 and lo <= snap < hi]
    return float(max(drops)) if drops else None


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def m_qb_dropback(samples: Samples) -> Optional[float]:
    """Deepest the QB gets behind the LOS. **The control for this whole spec.**

    Slot 6 and slot 7 hold the same formation with different play calls, so the
    load confirm's geometry check cannot tell them apart. This can: on the pass
    play the QB drops several yards; on the misdirection run he does not. A
    near-zero value here means the wrong state was loaded and every other
    number in the run is about a different play.
    """
    los = _los(samples)
    if los is None:
        return None
    qb = None
    for entity in _offense(samples):
        positions = samples.values(entity, "position")
        if positions and positions[0] == POSITION_QB:
            qb = entity
            break
    if qb is None:
        return None
    ys = [xy for _i, xy in samples.series(qb, "pos_y") if xy is not None]
    if not ys:
        return None
    forward = _forward_sign(samples, los)
    # Behind the line is the negative-forward direction.
    return float(max((los - y) * forward for y in ys))


def m_blockers_engaged(samples: Samples) -> Optional[float]:
    """Distinct offensive players with at least one real episode.

    The control that separates "the mechanism did not fire" from "the run
    measured nothing". The operator set up seven potential blockers (five
    linemen plus the RB and TE), so a value of zero is a plumbing failure, not
    a finding.
    """
    return float(len({e for e, _s, _t in _blocker_episodes(samples)}))


def m_block_episodes(samples: Samples) -> Optional[float]:
    return float(len(_blocker_episodes(samples)))


def m_longest_episode(samples: Samples) -> Optional[float]:
    episodes = _blocker_episodes(samples)
    if not episodes:
        return None
    return float(max(end - start + 1 for _e, start, end in episodes))


def m_contest_ever_nonzero(samples: Samples) -> Optional[float]:
    """Offensive players whose +0x414 is ever non-zero.

    Separate from `blockers_engaged` on purpose. If players engage but this
    stays at zero, then either the field is not what `addresses.yaml` says it
    is or it is not populated on this path -- and that is a finding about the
    address map, not about pass protection.
    """
    count = 0
    for entity in _offense(samples):
        values = [v for v in samples.values(entity, "contest_power")
                  if v is not None]
        if any(abs(float(v)) > 1e-9 for v in values):
            count += 1
    return float(count)


def m_decay_within_rep(samples: Samples) -> Optional[float]:
    """Median fraction of +0x414 lost inside a single block rep.

    The direct observable of the P3 mechanism. Zero means the components are
    flat within a rep -- which, given the store-back is now verified in the
    image, would mean the function is not being called on this path at all.
    """
    fractions = [f for f in
                 (_decay_fraction(samples, e, s, t)
                  for e, s, t in _blocker_episodes(samples))
                 if f is not None]
    if not fractions:
        return None
    fractions.sort()
    middle = len(fractions) // 2
    if len(fractions) % 2:
        return float(fractions[middle])
    return float((fractions[middle - 1] + fractions[middle]) / 2.0)


def m_worst_drop_early(samples: Samples) -> Optional[float]:
    """Largest single-step fall in +0x414 before snap frame 60.

    The ramp's ceiling here is `60/180 * 0.95` = 0.32, so a drop much past a
    third is evidence against this being the mechanism doing the work.
    """
    return _worst_drop(samples, 0, 60)


def m_worst_drop_late(samples: Samples) -> Optional[float]:
    """Largest single-step fall in +0x414 at or after snap frame 150.

    **The headline, paired with `worst_drop_early`.** The ramp saturates at
    frame 180, so its ceiling here is at or near 0.95 -- roughly three times
    the early ceiling. If protection decays on the *global snap clock* rather
    than per engagement, this is the metric that shows it, and the pair is
    reported as two numbers rather than one ratio so that a `None` on either
    side stays visible instead of silently becoming a verdict.

    An earlier version of this asked whether a rep that *starts* later starts
    weaker, comparing the first episode's opening value against the last
    episode's. That was wrong in a way worth recording: the earliest and latest
    episodes usually belong to **different players**, whose composites differ
    because their ratings differ. It could not separate "the ramp weakened this
    block" from "the man who happened to engage late is a worse blocker", and
    it returned a confident 0.26-0.36 on a run where one guard's triple never
    moved at all. Steps within one player's own series have no such confound.
    """
    return _worst_drop(samples, 150, 10 ** 9)


def m_decay_steps(samples: Samples) -> Optional[float]:
    """How many downward steps the whole offence took.

    Cadence, not magnitude. The value is recomputed at lock-in and then sits
    still for long stretches -- one blocker held 1052.76990 unchanged for 283
    frames -- so this separates "the mechanism never fired" from "it fired and
    the effect was small".
    """
    return float(sum(1 for entity in _offense(samples)
                     for _snap, fraction in _steps(samples, entity)
                     if fraction < 0))


def m_recompute_steps(samples: Samples) -> Optional[float]:
    """Upward steps: recomputes from raw ratings at a new lock-in.

    Reported because it is the confound for every decay statistic here. A
    blocker who re-locks often keeps getting restored to full strength, so the
    same decay mechanism produces a much weaker net effect on him than on a man
    who holds one rep for the whole play.
    """
    return float(sum(1 for entity in _offense(samples)
                     for _snap, fraction in _steps(samples, entity)
                     if fraction > 0))


def m_earliest_rep_frame(samples: Samples) -> Optional[float]:
    """Snap frame of the first real block rep -- context for the headline."""
    episodes = _blocker_episodes(samples)
    if not episodes:
        return None
    snap = _at(samples, "game", "frames_since_snap", episodes[0][1])
    return None if snap is None else float(snap)


def m_latest_rep_frame(samples: Samples) -> Optional[float]:
    episodes = _blocker_episodes(samples)
    if not episodes:
        return None
    snap = _at(samples, "game", "frames_since_snap", episodes[-1][1])
    return None if snap is None else float(snap)


def m_rusher_gain(samples: Samples) -> Optional[float]:
    """Does any defender's +0x41C ever rise within an episode?

    The mirror of the decay: at 0x001f1050 the *rusher's* +0x41C is increased
    by a random fraction while the blocker's three components are cut. Seeing both
    signs in one run is much stronger evidence that this function is the one
    running than seeing the decay alone, which a dozen other mechanisms could
    also produce.
    """
    best = None
    for entity in _defense(samples):
        for start, end in _episodes(samples, entity):
            values = _span(samples, entity, "contest_overall", start, end)
            if len(values) < 3 or values[0] <= 0.0:
                continue
            rise = (max(values) - values[0]) / values[0]
            best = rise if best is None else max(best, rise)
    return None if best is None else float(best)


def _los(samples: Samples) -> Optional[float]:
    """The engine's own line of scrimmage, sampled as `game.los`.

    Never derived from a body position. Deriving it from the centre reads
    14.219 against the engine's 15.000, a 0.78 yd bias that silently
    contaminated every absolute yardage this project quoted.
    """
    for value in samples.values("game", "los"):
        if value is not None:
            return float(value)
    return None


def _forward_sign(samples: Samples, los: Optional[float]) -> float:
    if los is None:
        return 1.0
    ys = [y for y in samples.values("game", "carrier_y") if y is not None]
    if not ys:
        return 1.0
    return 1.0 if los >= ys[0] else -1.0


def m_carrier_yards(samples: Samples) -> Optional[float]:
    """Net yards for the carrier. On a held-ball pass play this is the QB, so a
    negative value is a sack -- which is the natural end of every iteration
    here and tells us how long protection actually held."""
    los = _los(samples)
    ys = [y for y in samples.values("game", "carrier_y") if y is not None]
    if los is None or len(ys) < 2:
        return None
    forward = _forward_sign(samples, los)
    deepest = max(ys) if forward > 0 else min(ys)
    return float((deepest - los) * forward)


def m_play_length(samples: Samples) -> Optional[float]:
    return float(len(samples))


def m_max_snap_frame(samples: Samples) -> Optional[float]:
    """How far the snap counter got. The 0.95 ramp saturates at 180 and the
    0.45 ramp at 300; if this never reaches 180 the run cannot see the top of
    either curve, and the decay metrics are measuring the ramp's early slope
    rather than its full depth."""
    values = [v for v in samples.values("game", "frames_since_snap")
              if v is not None]
    return float(max(values)) if values else None


# --------------------------------------------------------------------------
# Operator questions
# --------------------------------------------------------------------------

ASK_POCKET = OperatorAsk(
    id="qb_left_pocket",
    question="Did the QB leave the pocket at any point before he went down?",
    watch_for=("The QB from the snap until the play ends. 'Left the pocket' "
               "means drifting or stepping out past the tackles, not simply "
               "dropping straight back."),
    why_not_memory=(
        "There is a pass-only 4x swing keyed on the QB appearing to leave the "
        "pocket (0x001f1348 / 0x001f1360): the blocker's components are halved "
        "and the rusher's doubled. That is roughly four times the size of the "
        "effect this experiment is trying to measure, so an iteration where the "
        "QB drifted is not a clean sample of the decay -- it is a sample of the "
        "swing. Nothing in memory says 'the QB is out of the pocket'; the test "
        "the engine applies has not been located, only its consequence."),
    choices=("yes", "no", "unclear"),
    every=20, limit=6)

ASK_BLOCKERS = OperatorAsk(
    id="blockers_held",
    question="Roughly how long did the protection hold before the first rusher broke free?",
    watch_for="The five linemen plus the RB and TE, from the snap to the sack.",
    why_not_memory=(
        "'Broke free' is a judgment about what the animation looks like. The "
        "harness can see an engagement word drop to zero, but a blocker who is "
        "beaten and one who passes his man off in a stunt produce the same "
        "transition in memory, and the difference is the whole question."),
    choices=("under 2s", "2-4s", "over 4s", "never broke free", "unclear"),
    every=25, limit=5)


# --------------------------------------------------------------------------
# The trial
# --------------------------------------------------------------------------

OFFENSE_FIELDS = (
    "position",
    "engagement",         # +0x3E0 -- episode segmentation depends on this
    "engagement_link",    # +0x3E4
    "block_mode",         # +0x3F0
    "contest_power",      # +0x414 -- verified this session as 16(s1), s1=base+0x404
    "contest_finesse",    # +0x418
    "contest_overall",    # +0x41C
    "pos_y",              # for the QB dropback control
)

DEFENSE_FIELDS = (
    "position",
    "ai_state",
    "engagement",
    "engagement_link",
    "contest_overall",    # the rusher's +0x41C, which the same function raises
)

METRICS = (
    Metric("qb_dropback", m_qb_dropback, "yards",
           "CONTROL: deepest the QB gets behind the LOS; near zero means the "
           "wrong savestate was loaded"),
    Metric("blockers_engaged", m_blockers_engaged, "players",
           "CONTROL: distinct offensive players with a real block rep"),
    Metric("contest_ever_nonzero", m_contest_ever_nonzero, "players",
           "CONTROL: offensive players whose +0x414 is ever populated"),
    Metric("block_episodes", m_block_episodes, "episodes",
           "how many block reps the play produced"),
    Metric("longest_episode", m_longest_episode, "frames",
           "longest single block rep"),
    Metric("decay_within_rep", m_decay_within_rep, "fraction",
           "median fraction of +0x414 lost inside one rep (zeros excluded)",
           higher_is="worse"),
    Metric("worst_drop_early", m_worst_drop_early, "fraction",
           "HEADLINE (a): largest single decay step before snap frame 60; "
           "the ramp's ceiling there is 0.32", higher_is="worse"),
    Metric("worst_drop_late", m_worst_drop_late, "fraction",
           "HEADLINE (b): largest single decay step at or after frame 150; "
           "the ramp's ceiling there is 0.95. Late >> early is the signature "
           "of a decay on the global snap clock", higher_is="worse"),
    Metric("decay_steps", m_decay_steps, "steps",
           "how many downward steps the offence took: cadence, not magnitude"),
    Metric("recompute_steps", m_recompute_steps, "steps",
           "upward steps -- lock-in recomputes that restore full strength"),
    Metric("earliest_rep_frame", m_earliest_rep_frame, "frame",
           "snap frame of the first rep"),
    Metric("latest_rep_frame", m_latest_rep_frame, "frame",
           "snap frame of the last rep"),
    Metric("rusher_gain", m_rusher_gain, "fraction",
           "largest rise in a defender's +0x41C within a rep -- the mirror "
           "half of the same function"),
    Metric("carrier_yards", m_carrier_yards, "yards",
           "negative on a sack; how far the QB got", higher_is="better"),
    Metric("max_snap_frame", m_max_snap_frame, "frames",
           "did the play reach 180 (0.95 ramp) and 300 (0.45 ramp)?"),
    Metric("play_length", m_play_length, "frames",
           "duration control; count metrics scale with it"),
)


def build() -> Trial:
    return Trial(
        name="pass_protection_decay",
        state="SLUS-20752 (14F8B841).07.p2s  [pre-snap, pass, RB+TE blocking]",
        state_slot=7,
        question=("Does the pass-protection collapse decay run on this path, and "
                  "is its ramp keyed to the global snap counter rather than to "
                  "each engagement?"),
        load_confirm=LoadConfirm(
            check=loaded_state_is_pre_snap,
            description="snap counter at 0 AND the QB and HB on this state's "
                        "formation spots (NOTE: shared with slot 6 -- see "
                        "cannot_conclude)",
            require_reset=False,
            timeout_s=15.0),
        sample=SampleSpec(
            entities=(
                EntitySelector("player", OFFENSE_FIELDS, side=0, label="offense"),
                EntitySelector("player", DEFENSE_FIELDS, side=1, label="defense"),
                EntitySelector("game", ("frames_since_snap", "carrier_y", "los")),
            ),
            every=1,
        ),
        # Snap and then nothing. No movement, no throw: the QB must hold in the
        # pocket, because leaving it triggers a 4x swing that is four times the
        # size of the effect being measured.
        script=(InputEvent(frame=4, button=SNAP_BUTTON, duration=3),),
        # 480 rather than 420: the second ramp saturates at 300 frames, and a
        # window that stops before then can only ever see the early slope.
        stop=StopCondition(max_frames=480, until=PlayEnded(), until_name="whistle",
                           timeout_s=110.0),
        metrics=METRICS,
        asks=(ASK_POCKET, ASK_BLOCKERS),
        setup=(),
        cannot_conclude=(
            "That the world under test came from slot 7 rather than slot 6. The "
            "two states share a formation and differ only in the play call, which "
            "is not visible pre-snap. `qb_dropback` is the post-snap control that "
            "distinguishes them; read it before trusting anything else here.",
            "The call cadence of the decay function. This measures the series the "
            "field takes, which constrains the cadence but does not observe the "
            "call. A flat series means 'not called on this path', not 'the "
            "mechanism does not exist' -- the store-back is verified in the image.",
            "Anything about run blocking. The decay floats are read on the pass "
            "path; whether the run path shares the function is untested.",
            "Whether a rep ended because the blocker was beaten or because he "
            "passed his man off. Both read as the engagement word dropping.",
            "Whether an iteration is a clean sample of the decay at all, without "
            "the operator's pocket answer. The 4x out-of-pocket swing has no "
            "known memory flag, so a drifting QB contaminates the measurement "
            "invisibly.",
            "Anything at single-frame resolution: PINE has no frame advance, so "
            "the harness polls and misses frames under load. Episode boundaries "
            "are therefore approximate, which is why short runs are discarded.",
        ),
    )


TRIAL = build()

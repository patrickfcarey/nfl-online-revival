"""Does a double team ever register, and what does the helper actually do?

Slot 9: **HB Lead Dive from I-Form against a 3-4 with the nose head-up on the
centre.** Chosen by the arithmetic in `double-team-plan.md` -- a combo block
needs an uncovered lineman beside a covered one, so 5 OL against 3 DL leaves
**two** spare blockers, the most any front allows. Confirmed on the loaded
state: down linemen at y 16.28 with the nose at x 0.151 against the centre's
0.013, four linebackers behind them.

This is the first savestate in the project that can produce a double team at
all. Everything in `double-team-requirements.md` needs the baseline it gives.

Four things are being asked at once, in priority order:

1. **Does the pairing PERSIST?** R6, and since 2026-08-11 the *primary*
   requirement -- `dt_longest_hold`, `dt_shortest_hold`, `dt_last_hold_frame`.
   The baseline is holds of **13, 17 and 30 frames, every one finished by frame
   43 of 308**: a fifth to half a second. R1's 1.5x multiplier and R3's pushback
   are close to irrelevant against a block that short, because a stronger block
   that still releases at frame 43 changes nothing on screen.
2. **Does one register?** `dt_role != 5` on anybody. 5 is UNASSIGNED -- read
   live on 22 players with no double team on the field, and confirmed at
   `0x001f66dc`/`0x001f670c` where the engine tests `bne v1, 5`.
   `block-cycle.md`'s published enum omits 5, so a metric written from that doc
   alone would report all 22 players as doubled.
3. **Is the helper a statue?** His `speed_cmd` (+0x1E8) against the primary's.
4. **Is the doubled defender driven backwards?** His displacement, which needs
   coordinates on the defence -- sampled here for that reason.

Registration is gated to a **60-frame post-snap window** (`block-cycle.md`
DT-2), so a run that never reaches frame 60 proves nothing; `max_snap_frame`
is a metric so that failure is visible rather than silent.

## Every metric here is scoped to the pairing, and that was learned the hard way

The double team occupies 13-30 frames of a 308-frame play. A statistic taken
over the play is therefore nine parts pursuit and free running to one part
double team -- and on 2026-08-11 this file quoted three of those to the
operator as if they described the block:

    defender_pushback   3.178 yd  whole play  ->  0.410 yd  while dt_role == 2
    helper_speed        0.435     whole play  ->  median while dt_role == 1
    primary_speed       0.460     whole play  ->  median while dt_role == 0

The operator, watching the screen, said he saw "maybe a few inches" and "a
right guard briefly touch someone and then go to the second level". He was
right and the numbers were wrong: 3.178 yd was a defender **flowing to the
ball after the block had ended**, measured as though he had been driven there.

That is the third time this defect class has shipped in this harness. The other
two are in experiments/pass_protection.py -- the block-contest composites reset
at every lock-in, so a whole-play median saw nothing, and `_decay_fraction`
read an unpopulated field at the top of an episode as a total collapse. The
rule that keeps being relearned: **on this engine, any statistic not scoped to
an engagement episode is measuring the wrong thing.** Where a whole-play figure
is genuinely what is wanted below, its docstring says so in as many words, so
the next reader can tell a choice from an oversight.
"""

from __future__ import annotations

import math
import struct
from typing import List, Optional, Sequence, Tuple

from tools.madden_lab.trial import (EntitySelector, Frame, InputEvent,
                                    LoadConfirm, Metric, SampleSpec, Samples,
                                    StopCondition, Trial)

SECONDS_PER_ITERATION = 12.0
MB_PER_ITERATION = (18.0, 34.0)

OFFENSE = "player:0:"
DEFENSE = "player:1:"
SNAP_BUTTON = "cross"

#: The empty-slot value for `dt_role`. NOT zero -- zero is "primary".
UNASSIGNED = 5
#: The roles that mean a pairing is live on this player: 0 primary, 1 helper,
#: 2 the man the two of them are on. Role **3 (peel-off) is deliberately absent**
#: -- it marks the pairing coming apart, and counting it as held time would
#: inflate the single number R6 exists to move. Enum from `addresses.yaml`
#: (`dt_role`), which is `doc`-grade; 5 is the one value in it confirmed live.
LIVE_ROLES = (0, 1, 2)
#: State 32 owns engagement kinds 5 and 6; confirmed in the image at
#: 0x001e81dc, where the handler advances one to the other itself.
TWO_MAN_STATE = 32

SNAP_COUNTER_PTR = 0x00601280
SNAP_COUNTER_OFF = 84
PLAYER_DESC_PTR = 0x00600E48
PLAYER_POS_X, PLAYER_POS_Y = 0x190, 0x194
#: Slot 9's own anchors: the QB under centre and the FULLBACK at 10.038 -- the
#: fullback is what distinguishes this I-Form state from slots 6/7/8, all of
#: which are single-back. Unlike those three, this geometry check really does
#: identify the state.
QB_SPOT = (0.0000, 13.4000)
FB_SPOT = (-0.0060, 10.0380)
SPOT_TOL = 0.35


def _f32(emu, addr: int) -> float:
    return struct.unpack("<f", emu.read(addr, 4).to_bytes(4, "little"))[0]


def loaded_state_is_pre_snap(emu) -> bool:
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
    for slot, (x0, y0) in ((0, QB_SPOT), (2, FB_SPOT)):
        b = base + slot * 5312
        if abs(_f32(emu, b + PLAYER_POS_X) - x0) > SPOT_TOL:
            return False
        if abs(_f32(emu, b + PLAYER_POS_Y) - y0) > SPOT_TOL:
            return False
    return True


class PlayEnded:
    def __init__(self, patience: int = 8):
        self.patience, self._last, self._stalled = patience, None, 0

    def reset(self) -> None:
        self._last, self._stalled = None, 0

    def __call__(self, frame: Frame) -> bool:
        current = frame.get("game", "frames_since_snap")
        if not isinstance(current, int):
            return False
        self._stalled = self._stalled + 1 if (
            self._last is not None and current <= self._last) else 0
        self._last = current
        if current <= 0:
            self._stalled = 0
        return self._stalled >= self.patience


def _assigned(samples: Samples, prefix: str) -> List[str]:
    """Entities that ever hold a dt_role other than UNASSIGNED, for 2+ frames.

    Two frames because PINE reads are not synchronised with emulation and a
    torn byte would otherwise manufacture a double team out of noise -- and
    "did one register at all" is the headline, so a single bad frame would
    answer the whole experiment wrongly.
    """
    return [e for e in samples.entities(prefix)
            if samples.holds_for(e, "dt_role",
                                 lambda v: isinstance(v, int)
                                 and v != UNASSIGNED, run=2)]


def m_dt_registered(samples: Samples) -> Optional[float]:
    """How many players ever take a real double-team role.

    **Whole-play on purpose**, and one of the few here that should be: the
    question is "did this mechanism fire at all during the play", which is an
    existence test over the whole window by definition. Scoping it to an
    episode would be circular -- the episodes are the thing it is counting.
    """
    return float(len(_assigned(samples, "player:")))


def m_dt_first_frame(samples: Samples) -> Optional[float]:
    """When registration first happens, as a frame index.

    Whole-play by construction (it is a search for the earliest event), and it
    is the *opening* bracket of the pairing that `dt_last_hold_frame` closes.
    Both are frame indices -- the game-tick offset the harness stamps on each
    sample -- and **not** `frames_since_snap`, so neither can be read straight
    against DT-2's 60-frame post-snap gate without adding the pre-snap frames
    the iteration spent before the snap at script frame 4. `max_snap_frame` is
    the metric that answers the DT-2 question directly.
    """
    best = None
    for entity in _assigned(samples, "player:"):
        f = samples.first_frame_where(
            entity, "dt_role",
            lambda v: isinstance(v, int) and v != UNASSIGNED, run=2)
        if f is not None:
            best = f if best is None else min(best, f)
    return None if best is None else float(best)


def _role_holders(samples: Samples, role: int) -> List[str]:
    """Players who hold `role` for two consecutive samples at any point.

    A player can appear under two roles: on this savestate the right tackle and
    the tight end each read 0 *and* 1 at different moments of the same play, so
    these lists are not a partition and the three count metrics below can sum to
    more than `dt_registered`. That is a fact about the engine (roles swap
    within a pairing), not a bug in the counting -- but it is exactly why
    anything measuring *behaviour* per role has to scope frame-by-frame with
    `_role_frames` rather than per player.
    """
    return [e for e in samples.entities("player:")
            if samples.holds_for(e, "dt_role", lambda v: v == role, run=2)]


def _role_frames(samples: Samples, entity: str,
                 roles: Sequence[int] = LIVE_ROLES) -> List[int]:
    """Sample indices where `entity` holds one of `roles`. **THE SCOPE.**

    Every measurement below that claims to describe what happened *during* a
    double team starts here, because the alternative -- taking the statistic
    over the play -- is the defect described at the top of this file, three
    times shipped.

    The role byte is its own episode marker: it reads 5 (UNASSIGNED) whenever no
    pairing is live on that player, so scoping to it needs none of the
    engagement-word segmentation pass_protection.py has to do. Indices are
    returned rather than a count so the caller can line these frames up against
    another field -- `speed_cmd`, `pos_x`/`pos_y` -- sampled in the same frames.
    """
    wanted = tuple(roles)
    return [index for index, value in samples.series(entity, "dt_role")
            if isinstance(value, int) and value in wanted]


def m_primaries(samples: Samples) -> Optional[float]:
    """dt_role 0. Whole-play existence count, deliberately -- see
    `m_dt_registered`; and see `_role_holders` on why 0/1/2 can double-count
    a man whose role changes mid-pairing."""
    return float(len(_role_holders(samples, 0)))


def m_helpers(samples: Samples) -> Optional[float]:
    """dt_role 1. Whole-play existence count, deliberately."""
    return float(len(_role_holders(samples, 1)))


def m_doubled_defenders(samples: Samples) -> Optional[float]:
    """dt_role 2. Whole-play existence count, deliberately."""
    return float(len(_role_holders(samples, 2)))


# --------------------------------------------------------------------------
# R6: how long the pairing lasts. Since 2026-08-11 the primary measurement.
# --------------------------------------------------------------------------


def _holds(samples: Samples) -> List[Tuple[str, int, int]]:
    """(entity, frames held, last frame) for every player who ever pairs up.

    Held frames are **counted, not spanned**: the number is how many sampled
    frames carried a live role, not `last - first + 1`. The two disagree on the
    baseline run -- the right guard holds 13 frames across the window 27-43,
    which spans 17 -- and the counted form is the one the 13/17/30 figures in
    `double-team-requirements.md` were measured with. Spanning would report 17
    for that guard and quietly break the comparison against the baseline every
    R6 patch is judged on. It would also charge a dropped poll to the block:
    PINE has no frame advance, so the harness misses frames under load, and a
    span counts the frames it never saw.

    Entities come from `_assigned`, which needs the role to survive two
    consecutive samples. Without that guard a single torn read would invent a
    one-frame "pairing" and drag `dt_shortest_hold` to 1.
    """
    out: List[Tuple[str, int, int]] = []
    for entity in _assigned(samples, "player:"):
        frames = _role_frames(samples, entity)
        if len(frames) < 2:
            continue
        out.append((entity, len(frames), frames[-1]))
    return out


def m_dt_longest_hold(samples: Samples) -> Optional[float]:
    """**THE HEADLINE (R6):** the longest any pairing survives, in frames.

    Baseline to reproduce: **30 frames** (the tight end). R6a's acceptance is
    ~60 -- one second -- so this number roughly doubling is the whole target of
    the reselect-timer work, and it is the only metric here that can tell a
    patch that changed the hold from a patch that changed nothing. Both of the
    duration patches tried so far (`0x001f6b0c` 30 -> 300, and by extension the
    five immediate 20s in the same region) left this at 30, which is how they
    were refuted.
    """
    holds = _holds(samples)
    return float(max(held for _e, held, _f in holds)) if holds else None


def m_dt_shortest_hold(samples: Samples) -> Optional[float]:
    """The shortest pairing, in frames. Baseline: **13** (the right guard).

    Reported beside the longest because the baseline is a *range* -- 13, 17 and
    30 frames on three blockers in one play, reproducible across runs -- and
    the spread is itself the finding. A single shared duration constant cannot
    produce three numbers; `reselect_timer` (+0x432, initialised to
    `30 - blockRating/16`) can. If a patch moves the longest hold and leaves
    this at 13, it has changed one man's clock, not the rule.
    """
    holds = _holds(samples)
    return float(min(held for _e, held, _f in holds)) if holds else None


def m_dt_last_hold_frame(samples: Samples) -> Optional[float]:
    """The frame the last pairing ends. Baseline: **43**, of a 308-frame play.

    The number that made the operator's "he briefly touches someone and goes to
    the second level" legible as a measurement: every double team on this play
    is over inside the first seventh of it.

    Frame *index*, on the same clock as `dt_first_frame` and `play_length` --
    the game-tick offset stamped on each sample, not `frames_since_snap`.
    Mixing the two clocks in one table is a mistake this file can make in one
    careless line, so all three are stated in the same units and
    `max_snap_frame` is kept as the separate post-snap reading.
    """
    holds = _holds(samples)
    return float(max(last for _e, _h, last in holds)) if holds else None


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    values = sorted(values)
    middle = len(values) // 2
    if len(values) % 2:
        return float(values[middle])
    return float((values[middle - 1] + values[middle]) / 2.0)


def _speed_while(samples: Samples, role: int) -> List[float]:
    """Every `speed_cmd` sample taken while a player was holding `role`.

    Scoped per frame, not per player, and both halves of that matter. Per
    player is wrong because the right tackle and the tight end each hold role 0
    *and* role 1 on this savestate: splitting by player would put the same man's
    frames on both sides of the helper-versus-primary comparison and quietly
    average the difference away. Per frame is also what keeps the 13-30 frames
    of block out of the 275 frames of pursuit that follow.
    """
    out: List[float] = []
    for entity in _role_holders(samples, role):
        scope = set(_role_frames(samples, entity, (role,)))
        out.extend(float(value)
                   for index, value in samples.series(entity, "speed_cmd")
                   if index in scope and value is not None)
    return out


def m_helper_speed(samples: Samples) -> Optional[float]:
    """R2's measurement: median `speed_cmd` **while holding role 1**.

    Zero -- or near it -- is the statue.

    WAS WRONG UNTIL 2026-08-11: the median was taken over the whole play, so a
    helper who stood still for his 17 frames of block and then ran downfield for
    the remaining 291 was reported at 0.435, indistinguishable from a man who
    blocked hard. It was quoted to the operator as "R2 close to satisfied" on a
    run where he had watched the helper do nothing. Same defect as
    `defender_pushback` below and as the pass-protection composites: the
    statistic outlived the episode it was supposed to describe.
    """
    return _median(_speed_while(samples, 1))


def m_primary_speed(samples: Samples) -> Optional[float]:
    """The control for `helper_speed`: median `speed_cmd` **while role 0**.

    Two men in the same block should move together; a large gap between these
    two IS the defect R2 names. The control only works if both are measured over
    the same kind of window, which is the second reason the whole-play version
    was useless -- it compared two men's whole plays, and after the block ends
    both are simply running, so both medians converged on "running speed" (0.435
    against 0.460) and the pair looked healthy.
    """
    return _median(_speed_while(samples, 0))


def m_two_man_state_players(samples: Samples) -> Optional[float]:
    """Players entering state 32, independent of the dt_* bytes.

    Kept separate on purpose: state 32 is the *animation* and the dt_ bytes are
    the *registration*, and slot 8 showed the speed behaviour without any
    registration at all. If these two disagree, that disagreement is the
    finding.
    """
    return float(sum(1 for e in samples.entities("player:")
                     if samples.holds_for(e, "ai_state",
                                          lambda v: v == TWO_MAN_STATE, run=2)))


def _los(samples: Samples) -> Optional[float]:
    """The engine's own line of scrimmage, sampled as `game.los`.

    Never derived from a body position: deriving it from the centre reads
    14.219 against the engine's 15.000, a 0.78 yd bias that contaminated every
    absolute yardage this project quoted (see pass_protection.py, same helper).
    """
    for value in samples.values("game", "los"):
        if value is not None:
            return float(value)
    return None


def _forward_sign(samples: Samples, los: Optional[float]) -> float:
    """+1 if the offence advances toward increasing y on this state, else -1.

    Read off the engine's own values rather than hard-coded. The previous
    version of `m_defender_pushback` carried the comment "the defence starts
    downfield of the LOS here (y 16.28 against 15.0), so driven backwards is
    increasing y" -- true of slot 9 and of nothing else. The instant this spec
    is pointed at a state with the offence going the other way, a hard-coded
    sign reports a defender being driven backwards as a defender charging
    forwards, with no symptom to notice.

    Falls back to +1 when `los` or `carrier_y` never arrived, matching
    pass_protection.py. That fallback is the slot 9 convention, so it is right
    here and unproven anywhere else.
    """
    if los is None:
        return 1.0
    ys = [y for y in samples.values("game", "carrier_y") if y is not None]
    if not ys:
        return 1.0
    return 1.0 if los >= ys[0] else -1.0


def _drive(samples: Samples, entity: str) -> Optional[Tuple[float, float]]:
    """(signed downfield displacement, 2-D distance) while `entity` is doubled.

    Endpoint to endpoint across the frames where `dt_role == 2` -- the first and
    last such frame that also carried both coordinates. Endpoints rather than
    `max(y) - y[0]`, because a peak excursion counts a wobble that the defender
    gave straight back as though he had been driven and kept there.

    Both numbers describe the same displacement, which is what makes them worth
    reading together: 0.410 yd of pushback out of 0.420 yd of travel says he
    went straight back, while 0.410 out of 3.0 says the pair shoved him sideways.
    """
    scope = _role_frames(samples, entity, (2,))
    if len(scope) < 2:
        return None
    xs = {i: float(v) for i, v in samples.series(entity, "pos_x") if v is not None}
    ys = {i: float(v) for i, v in samples.series(entity, "pos_y") if v is not None}
    placed = [i for i in scope if i in xs and i in ys]
    if len(placed) < 2:
        return None
    first, last = placed[0], placed[-1]
    dx, dy = xs[last] - xs[first], ys[last] - ys[first]
    # Driven backwards = away from the offence = the offence's forward direction.
    return dy * _forward_sign(samples, _los(samples)), math.hypot(dx, dy)


def _best_drive(samples: Samples) -> Optional[Tuple[float, float]]:
    """The most-driven doubled defender's (pushback, distance), as ONE body.

    Both numbers come from the same man on purpose. Maximising each
    independently would print the pushback of one defender beside the travel of
    another: on the baseline that pairs the defensive end's +0.410 yd of drive
    with the linebacker's 1.204 yd of travel, a 43-inch number belonging to a
    man who moved *forward and sideways* out of the block and was never driven
    anywhere. pass_protection.py's `worst_drop_late` carries the same warning
    for the same reason -- a statistic that silently changes subject between two
    bodies cannot be argued with.
    """
    best = None
    for entity in _role_holders(samples, 2):
        drive = _drive(samples, entity)
        if drive is None:
            continue
        if best is None or drive[0] > best[0]:
            best = drive
    return best


def m_defender_pushback(samples: Samples) -> Optional[float]:
    """R3: yards the doubled defender is driven back **while he is doubled**.

    Positive means driven backwards, toward his own end. Requires coordinates on
    the defence, which no earlier spec sampled -- which is why no earlier spec
    could have measured this at all.

    WAS WRONG UNTIL 2026-08-11, and this is the wrong answer that cost the most.
    The metric computed `max(pos_y) - pos_y[0]` **over the entire play** and
    reported **3.178 yd**, which was written up as R3 being nearly met. That
    3.178 belonged to the doubled *linebacker*, whose pairing lasted 13 frames
    of 308 -- the other 295 frames are him flowing to the ball, and pursuit is
    what the number measured. The doubled end read +0.873 the same way.

    Scoped to `dt_role == 2`, the same samples give **+0.410 yd for the end --
    fifteen inches** -- and **-0.213 yd for the linebacker**, who was not driven
    backwards at all but *forwards*. The operator, watching, had said "maybe a
    few inches" and "I don't see much at all". He was right twice on the same
    run and the harness was wrong twice.

    Reported as the best case across doubled defenders, so a value near zero
    means nobody was driven -- not that the metric picked the wrong man.
    """
    drive = _best_drive(samples)
    return None if drive is None else float(drive[0])


def m_defender_displacement(samples: Samples) -> Optional[float]:
    """How far that same defender actually moved while doubled, in 2-D yards.

    The companion to `defender_pushback`, and the reason a bare downfield figure
    cannot be trusted on its own: displacement is a vector and R3 only wants one
    component of it. Baseline is **0.420 yd against 0.410 of pushback** -- the
    defensive end went straight backwards, all fifteen inches of it.

    Where this runs far ahead of the pushback, the pair moved the man sideways
    or he ran through them; where both are near zero, nothing happened. Same
    body as the pushback by construction (`_best_drive`), never a maximum taken
    over a different defender.
    """
    drive = _best_drive(samples)
    return None if drive is None else float(drive[1])


def m_carrier_yards(samples: Samples) -> Optional[float]:
    """Yards the ball carrier gains past the LOS -- the play's outcome, and the
    number every double-team fix ultimately answers to. **Whole-play on
    purpose**: forward progress is a property of the play, not of any block
    episode.

    This metric was missing from the spec's first version, and its absence hid
    the headline: the operator asked how the run was actually doing, and the
    honest answer was that nobody had computed it. Computed after the fact from
    the sampled `carrier_y`/`los`, the baseline is **-0.70 yards on every
    iteration of every run** -- start 7.97, deepest 14.30 against a 15.00 line.
    The lead dive behind these 13-30-frame double teams is stuffed behind the
    line, deterministically. R6's acceptance is stated in hold frames and
    pushback, but this is the number that says whether any of it mattered.

    Deepest-point form (football's forward-progress rule), same as
    lead_blocker.py and pass_protection.py: robust to the carrier being driven
    back after being wrapped up and to the single reset frame at the whistle.
    """
    los = _los(samples)
    ys = [y for y in samples.values("game", "carrier_y") if y is not None]
    if los is None or len(ys) < 2:
        return None
    forward = _forward_sign(samples, los)
    deepest = max(ys) if forward > 0 else min(ys)
    return float((deepest - los) * forward)


def m_max_snap_frame(samples: Samples) -> Optional[float]:
    """How far the snap counter got. **Whole-play on purpose** -- it is the
    control that says whether the run was long enough for DT-2's 60-frame
    registration window to have been reachable at all, so scoping it to an
    episode would defeat the entire point of having it."""
    values = [v for v in samples.values("game", "frames_since_snap")
              if v is not None]
    return float(max(values)) if values else None


def m_play_length(samples: Samples) -> Optional[float]:
    """Samples in the iteration. **Whole-play on purpose**: it is the
    denominator every duration metric here should be read against -- 30 frames
    of double team means something quite different in a 308-frame play than in
    a 40-frame one."""
    return float(len(samples))


FIELDS = ("position", "dt_record", "dt_role", "ai_state", "engagement",
          "engagement_link", "speed_cmd", "pos_x", "pos_y")

METRICS = (
    Metric("dt_longest_hold", m_dt_longest_hold, "frames",
           "HEADLINE (R6): longest a pairing survives, in frames the role was "
           "held; baseline 30, acceptance ~60", higher_is="better"),
    Metric("dt_shortest_hold", m_dt_shortest_hold, "frames",
           "R6: shortest pairing; baseline 13. The 13-30 spread is why the hold "
           "cannot be one constant", higher_is="better"),
    Metric("dt_last_hold_frame", m_dt_last_hold_frame, "frame",
           "R6: frame index the last pairing ends; baseline 43 of 308",
           higher_is="better"),
    Metric("dt_registered", m_dt_registered, "players",
           "players ever holding a dt_role other than 5 (5 = unassigned); "
           "whole-play existence test, deliberately", higher_is="better"),
    Metric("dt_first_frame", m_dt_first_frame, "frame",
           "frame index registration happens; same clock as dt_last_hold_frame, "
           "NOT frames_since_snap"),
    Metric("primaries", m_primaries, "players", "dt_role 0"),
    Metric("helpers", m_helpers, "players", "dt_role 1"),
    Metric("doubled_defenders", m_doubled_defenders, "players", "dt_role 2"),
    Metric("helper_speed", m_helper_speed, "units",
           "R2: median speed_cmd measured ONLY while dt_role == 1; near zero is "
           "the statue", higher_is="better"),
    Metric("primary_speed", m_primary_speed, "units",
           "control for helper_speed, ONLY while dt_role == 0; the two should "
           "be close"),
    Metric("two_man_state_players", m_two_man_state_players, "players",
           "players entering state 32, independent of the dt_ bytes"),
    Metric("defender_pushback", m_defender_pushback, "yards",
           "R3: yards the doubled defender is driven back, measured ONLY while "
           "dt_role == 2; baseline +0.410 (15 in)", higher_is="better"),
    Metric("defender_displacement", m_defender_displacement, "yards",
           "2-D distance that same defender travelled while doubled; baseline "
           "0.420. Far above the pushback means sideways, not back"),
    Metric("carrier_yards", m_carrier_yards, "yards",
           "OUTCOME: yards the carrier gains past the LOS; baseline -0.70 on "
           "every iteration -- the dive is stuffed behind the line",
           higher_is="better"),
    Metric("max_snap_frame", m_max_snap_frame, "frames",
           "CONTROL: below 60 and a negative proves nothing"),
    Metric("play_length", m_play_length, "frames", "duration control"),
)


def build() -> Trial:
    return Trial(
        name="double_team_baseline",
        state="SLUS-20752 (14F8B841).09.p2s  [pre-snap, I-Form HB lead dive vs 3-4]",
        state_slot=9,
        question=("Does a double team register on an I-Form lead dive against a "
                  "3-4 nose, HOW LONG does the pairing hold (R6, the primary "
                  "requirement), and is the helper a statue?"),
        load_confirm=LoadConfirm(
            check=loaded_state_is_pre_snap,
            description="snap counter at 0 AND the QB and FULLBACK on slot 9's "
                        "spots (the fullback distinguishes this from 6/7/8)",
            require_reset=False, timeout_s=15.0),
        sample=SampleSpec(
            entities=(
                EntitySelector("player", FIELDS, side=0, label="offense"),
                EntitySelector("player", FIELDS, side=1, label="defense"),
                EntitySelector("game", ("frames_since_snap", "carrier_y", "los")),
            ),
            every=1),
        script=(InputEvent(frame=4, button=SNAP_BUTTON, duration=3),),
        stop=StopCondition(max_frames=360, until=PlayEnded(),
                           until_name="whistle", timeout_s=90.0),
        metrics=METRICS,
        asks=(),
        setup=(),
        cannot_conclude=(
            "That no double team is possible in this engine, if dt_registered is "
            "zero. It would mean this play and this front produce none -- which "
            "points at DT-3's play-type gate (0x001F4AE8), testable by applying "
            "that one word and re-running this same spec.",
            "Which blockers were ASSIGNED to double. dt_role names the roles but "
            "the assignment-pool word is still unlocated (SEAM REQUEST 6).",
            "Whether one player's held frames are ONE pairing or two. The role "
            "byte is counted per player across the play, so a blocker who "
            "doubles, releases and doubles again on the same man reads as a "
            "single long hold -- which would overstate the one number R6 turns "
            "on. On the baseline the held counts (13/17/30) fall short of their "
            "own windows (27-43, 2-36, 2-43), which is equally consistent with a "
            "flickering role byte, a dropped poll, or two pairings, and nothing "
            "here separates those. `dt_record` (+0x436, the registry index) is "
            "sampled and would separate them, but its encoding is doc-grade and "
            "undecoded; decoding it is a separate piece of work with its own "
            "acceptance test, not a change to smuggle into a metric.",
            "Anything about pass protection: DT-1 says double teams register on "
            "run blocking only, and this is a run play.",
            "Anything at single-frame resolution. The one-shot speed trigger on "
            "`player.flags_0c` bit 2 (+0x0C, now in addresses.yaml) lasts a "
            "single frame and a 22-player sample rate will miss it; that needs "
            "the targeted single-player probe. It is deliberately NOT in FIELDS "
            "for that reason -- sampling it here would add 22 reads a frame and "
            "still miss the transient.",
        ),
    )


TRIAL = build()

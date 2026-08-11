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

import struct
from typing import List, Optional

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
    """THE HEADLINE: how many players ever take a real double-team role."""
    return float(len(_assigned(samples, "player:")))


def m_dt_first_frame(samples: Samples) -> Optional[float]:
    best = None
    for entity in _assigned(samples, "player:"):
        f = samples.first_frame_where(
            entity, "dt_role",
            lambda v: isinstance(v, int) and v != UNASSIGNED, run=2)
        if f is not None:
            best = f if best is None else min(best, f)
    return None if best is None else float(best)


def _role_holders(samples: Samples, role: int) -> List[str]:
    return [e for e in samples.entities("player:")
            if samples.holds_for(e, "dt_role", lambda v: v == role, run=2)]


def m_primaries(samples: Samples) -> Optional[float]:
    return float(len(_role_holders(samples, 0)))


def m_helpers(samples: Samples) -> Optional[float]:
    return float(len(_role_holders(samples, 1)))


def m_doubled_defenders(samples: Samples) -> Optional[float]:
    return float(len(_role_holders(samples, 2)))


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    values = sorted(values)
    middle = len(values) // 2
    if len(values) % 2:
        return float(values[middle])
    return float((values[middle - 1] + values[middle]) / 2.0)


def m_helper_speed(samples: Samples) -> Optional[float]:
    """R2's measurement. Zero -- or near it -- is the statue."""
    out = []
    for entity in _role_holders(samples, 1):
        out.extend(float(v) for v in samples.values(entity, "speed_cmd")
                   if v is not None)
    return _median(out)


def m_primary_speed(samples: Samples) -> Optional[float]:
    """The control for `helper_speed`. Two men in the same block should move
    together; a large gap between these two IS the defect R2 names."""
    out = []
    for entity in _role_holders(samples, 0):
        out.extend(float(v) for v in samples.values(entity, "speed_cmd")
                   if v is not None)
    return _median(out)


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


def m_defender_pushback(samples: Samples) -> Optional[float]:
    """R3: yards the doubled defender is driven back, toward his own end.

    Positive means driven backwards. Requires coordinates on the defence, which
    no earlier spec sampled -- which is why no earlier spec could have measured
    this at all.
    """
    los = None
    for value in samples.values("game", "los"):
        if value is not None:
            los = float(value)
            break
    if los is None:
        return None
    best = None
    for entity in _role_holders(samples, 2):
        ys = [float(v) for v in samples.values(entity, "pos_y") if v is not None]
        if len(ys) < 2:
            continue
        # The defence starts downfield of the LOS here (y 16.28 against 15.0),
        # so "driven backwards" is increasing y.
        push = max(ys) - ys[0]
        best = push if best is None else max(best, push)
    return None if best is None else float(best)


def m_max_snap_frame(samples: Samples) -> Optional[float]:
    values = [v for v in samples.values("game", "frames_since_snap")
              if v is not None]
    return float(max(values)) if values else None


def m_play_length(samples: Samples) -> Optional[float]:
    return float(len(samples))


FIELDS = ("position", "dt_record", "dt_role", "ai_state", "engagement",
          "engagement_link", "speed_cmd", "pos_x", "pos_y")

METRICS = (
    Metric("dt_registered", m_dt_registered, "players",
           "HEADLINE: players ever holding a dt_role other than 5 "
           "(5 = unassigned)", higher_is="better"),
    Metric("dt_first_frame", m_dt_first_frame, "frame",
           "when registration happens; DT-2 gates it to the first 60"),
    Metric("primaries", m_primaries, "players", "dt_role 0"),
    Metric("helpers", m_helpers, "players", "dt_role 1"),
    Metric("doubled_defenders", m_doubled_defenders, "players", "dt_role 2"),
    Metric("helper_speed", m_helper_speed, "units",
           "R2: median speed_cmd of the helper; near zero is the statue",
           higher_is="better"),
    Metric("primary_speed", m_primary_speed, "units",
           "control for helper_speed; the two should be close"),
    Metric("two_man_state_players", m_two_man_state_players, "players",
           "players entering state 32, independent of the dt_ bytes"),
    Metric("defender_pushback", m_defender_pushback, "yards",
           "R3: yards the doubled defender is driven back", higher_is="better"),
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
                  "3-4 nose, and if so is the helper a statue?"),
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
            "Anything about pass protection: DT-1 says double teams register on "
            "run blocking only, and this is a run play.",
            "Anything at single-frame resolution. The one-shot speed trigger on "
            "player+0x0C bit 2 lasts a single frame and a 22-player sample rate "
            "will miss it; that needs the targeted single-player probe.",
        ),
    )


TRIAL = build()

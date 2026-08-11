"""Does the lead-blocking fullback ever enter the assignment system?

`docs/fb-wr-blocking.md` closes open question #15 with a static derivation. Two
of its claims are the ones worth measuring first, because everything else in
that document is built on them and because both are falsifiable in a single
snap-to-whistle run:

* **Gate A.** The blocker list at `0x001f2ea0` admits block modes 1 and 2 only
  (`0x001f2efc`), and state 47's enter at `0x001b6780` is the *only* writer of
  mode 3 in the whole image. Therefore a lead-blocking fullback is in mode 3,
  is never entered into the assignment system at all, and his only targeting is
  state 47's own cone-lean.
* **Gate B.** The defender list at `0x001f2cd8` accepts only states 2, 30, 51
  and human-controlled, so a corner in man coverage (state 22) or zone
  (37/38/40) is not an eligible block target for anybody.

This experiment does not try to prove the disassembly right. It looks for the
observable consequences, which is a weaker but independent kind of evidence:
if exactly one offensive player holds block mode 3 for most of a run play, and
that player's assignment fields never move while every mode-1/2 blocker's do,
the static reading survives a test it could have failed.

## The identification trick

The fullback is found **behaviourally, not by position byte**. This spec never
names a position constant, because the position enum is only partly known --
`fb-wr-blocking.md` documents 17/18 as FS/SS and nothing else -- and a guessed
constant is exactly the class of error that has produced every wrong answer in
this project so far. Mode 3 has a single writer in the entire image, so "the
player in block mode 3" *is* "the player state 47 installed", with no
assumption in between.

## What this cannot conclude

Listed on the `Trial` too, so it lands in the run header and cannot be lost:

* It says nothing about **which blocking class a play actually authors** for
  any player. That lives in the play file, which is still unread
  (`play-data.md`). "Never told to block" and "told to block, but the target is
  not legal" look identical here.
* It observes **one savestate**: one play, one formation, one personnel group,
  one defensive call. Nothing here generalises to the run game.
* `+0xBCC` was inference when `fb-wr-blocking.md` was written (its hazard 5).
  It is not any more: layer 3 verified that in `extract/ee_inplay.bin` it equals
  byte 0 of the state-chain record for all 22 players, with no exceptions. The
  coverage-state metrics rest on that check rather than on the two-jump-table
  agreement.
* The engagement **kind** encoding inside the `+0x3E0` word is not documented,
  so `lead_blocker_engaged_frames` counts a non-zero word, which is "in some
  engagement" and not "at contact".
* **There is no readable "assigned target" field.** `engagement_link` (+0x3E4)
  is who a player is *in contact with*, which is downstream of the assignment
  and can also be caused by the defender. So this experiment bounds Gate B by
  its observable consequence and cannot test the gate itself; the pool
  membership word remains unlocated (SEAM REQUEST 6).
"""

from __future__ import annotations

from typing import Any, List, Optional

from tools.madden_lab.trial import (EntitySelector, Frame, InputEvent,
                                    LoadConfirm, Metric, OperatorAsk,
                                    SampleSpec, Samples, StopCondition, Trial)
from tools.madden_lab.world import Handle

# --------------------------------------------------------------------------
# Constants, each with the line of `docs/` it came from
# --------------------------------------------------------------------------

#: The only mode state 47's enter writes (0x001b6780), and the mode the blocker
#: list at 0x001f2efc rejects. fb-wr-blocking.md, Gate A.
MODE_LEAD_BLOCK = 3
#: The modes 0x001f2efc admits into the assignment system.
MODES_IN_POOL = (1, 2)
#: Coverage states per press-and-routes.md (man) and zone-bunching.md (zone),
#: none of which 0x001f2cd8 accepts. fb-wr-blocking.md, Gate B.
COVERAGE_STATES = (22, 37, 38, 39, 40, 41)

#: Layer 2's name for the PS2 X button. SEAM REQUEST 7: the button vocabulary
#: is pad.py's to define; this spec assumes lowercase PS2 face-button names and
#: will need one edit if pad.py chose SDL or DualShock numbering instead.
SNAP_BUTTON = "cross"

SIDE_OFFENSE = 0
SIDE_DEFENSE = 1

#: Row keys are "player:<side>:<index>" -- the side is part of the key because
#: layer 3 numbers players per side, so offence #5 and defence #5 would
#: otherwise collide into one time series.
OFFENSE = "player:%d:" % SIDE_OFFENSE
DEFENSE = "player:%d:" % SIDE_DEFENSE
PLAYER = "player:"

#: Double indirection, verified in lead-blocker-targeting.md: `gp-17520` at
#: 0x00601280 holds a *pointer*, and the true frames-since-snap counter is at
#: +84 off it. Written as `[0x00601280+84]` in an earlier draft of that doc,
#: which reads garbage -- the indirection is the whole point.
SNAP_COUNTER_PTR = 0x00601280
SNAP_COUNTER_OFF = 84


def loaded_state_is_pre_snap(emu) -> bool:
    """Positive proof that our savestate is the world now in memory.

    PINE's `load_state` returns as soon as the load is *queued* and never
    reports failure, so without a check like this the first frames of every
    iteration describe the previous iteration's world -- and a load that failed
    outright is invisible. This is the cheapest honest test available here: the
    savestate is a pre-snap frame, so its snap counter is at zero, while the
    iteration that just finished left it around three hundred.

    The null-pointer guard is not defensive padding. Reads of unmapped pages
    return 0 rather than erroring, so a drifted `SNAP_COUNTER_PTR` would make
    `emu.read(0 + 84)` return 0 and this function return True forever -- a
    confirmation that confirms nothing, which is worse than none at all.
    """
    pointer = emu.read(SNAP_COUNTER_PTR, 4)
    if not pointer:
        return False
    return emu.read(pointer + SNAP_COUNTER_OFF, 4) == 0


# --------------------------------------------------------------------------
# Stop condition
# --------------------------------------------------------------------------


class PlayEnded:
    """True once the snap counter has stopped advancing for `patience` samples.

    Uses only `frames_since_snap`, which is a documented, verified counter
    (`*(0x00601280) + 84`, reset at play init `0x001f7034`, incremented at
    `0x001f5b94` -- lead-blocker-targeting.md). At the whistle it either
    freezes or resets for the next play; both show up as "not advancing", so
    this detects the end of the play without needing the phase encoding, which
    is not documented anywhere yet.

    Stateful, and the `Trial` holding it is one object reused for every
    iteration -- so without `reset()` iteration 1 would inherit iteration 0's
    history and stop on its first frame. The runner calls `reset()` at the top
    of each iteration if the predicate has one; that is the contract a stop
    condition with memory has to meet.
    """

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
            # Still pre-snap. Never end the play before it has started.
            self._stalled = 0
        return self._stalled >= self.patience


# --------------------------------------------------------------------------
# Metric helpers
# --------------------------------------------------------------------------


def _offense(samples: Samples) -> List[str]:
    return samples.entities(OFFENSE)


def _defense(samples: Samples) -> List[str]:
    return samples.entities(DEFENSE)


def _linked_entity(word: Any) -> Optional[str]:
    """Turn an `engagement_link` handle word into a row key, or None.

    The engine stores every cross-object reference as a handle rather than a
    pointer: kind in byte 0, side in byte 1, index in byte 2. Only kind 1 is a
    player, so anything else -- and zero, meaning nobody -- is not a link to a
    man on the field.
    """
    if not isinstance(word, int) or not word:
        return None
    handle = Handle(word)
    if not handle.is_player:
        return None
    return "player:%d:%d" % (handle.side, handle.index)


def _lead_blockers(samples: Samples) -> List[str]:
    """Offensive players that held block mode 3 for at least two samples.

    Two, not one. PINE reads are not synchronised with emulation, so a
    snapshot can tear mid-update and report a value the game never held. With
    22 players sampled across a few hundred frames, `any(v == 3)` will
    eventually find a torn 3 somewhere -- and since the headline result of this
    experiment is *how many* players hold mode 3, a single spurious frame would
    manufacture a second lead blocker and refute the single-writer reading of
    `0x001b6780` with noise.
    """
    return [entity for entity in _offense(samples)
            if samples.holds_for(entity, "block_mode",
                                 lambda v: v == MODE_LEAD_BLOCK, run=2)]


def _the_lead_blocker(samples: Samples) -> Optional[str]:
    """The one that held mode 3 longest, or None.

    Longest rather than first because a mode can be written transiently at play
    setup; whoever holds it across the play is the one running state 47.
    """
    ranked = [(samples.count_frames_where(entity, "block_mode",
                                          lambda v: v == MODE_LEAD_BLOCK), entity)
              for entity in _lead_blockers(samples)]
    if not ranked:
        return None
    ranked.sort(reverse=True)
    return ranked[0][1]


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def m_lead_blockers_seen(samples: Samples) -> Optional[float]:
    """How many players ever hold mode 3.

    Zero on a play with no lead blocker, which is information; two or more
    would contradict the single-writer reading of `0x001b6780` and would be the
    most interesting result this experiment could produce.
    """
    return float(len(_lead_blockers(samples)))


def m_frames_in_mode3(samples: Samples) -> Optional[float]:
    entity = _the_lead_blocker(samples)
    if entity is None:
        return None
    return float(samples.count_frames_where(
        entity, "block_mode", lambda v: v == MODE_LEAD_BLOCK))


def m_first_mode3_frame(samples: Samples) -> Optional[float]:
    entity = _the_lead_blocker(samples)
    if entity is None:
        return None
    frame = samples.first_frame_where(entity, "block_mode",
                                      lambda v: v == MODE_LEAD_BLOCK)
    return None if frame is None else float(frame)


def m_lead_blocker_in_pool_frames(samples: Samples) -> Optional[float]:
    """Frames the lead blocker spent in a pool-eligible mode.

    Gate A predicts **zero** for the whole time he is lead blocking. A non-zero
    result here is the cleanest possible falsification of the claim, which is
    why it is a metric rather than an assertion.
    """
    entity = _the_lead_blocker(samples)
    if entity is None:
        return None
    return float(samples.count_frames_where(
        entity, "block_mode", lambda v: v in MODES_IN_POOL))


def m_lead_blocker_engaged_frames(samples: Samples) -> Optional[float]:
    """Frames his `+0x3E0` engagement word is non-zero.

    Gate A says he is never *assigned*; it does not say he is never engaged --
    a defender can still capture him, which is the hang-up mechanism in
    `lead-blocker-targeting.md`. This metric separates those two stories, and
    a fix that raises it is doing something different from a fix that raises
    the target metrics.
    """
    entity = _the_lead_blocker(samples)
    if entity is None:
        return None
    return float(samples.count_frames_where(entity, "engagement", bool))


def m_pool_blockers(samples: Samples) -> Optional[float]:
    """Distinct offensive players that ever hold a pool-eligible mode.

    The control. If this is zero too, the run measured nothing and every other
    metric's zero is a plumbing failure rather than a finding -- which is a
    distinction no amount of statistics downstream can recover.
    """
    return float(sum(1 for entity in _offense(samples)
                     if samples.holds_for(entity, "block_mode",
                                          lambda v: v in MODES_IN_POOL, run=2)))


def m_lead_blocker_partners(samples: Samples) -> Optional[float]:
    """How many different men the lead blocker is ever engaged with.

    `engagement_link` (+0x3E4) is the nearest thing in memory to "who this
    blocker is on", and it is the *contact* relationship, not the assignment.
    That distinction is the whole reason SEAM REQUEST 6 is still open: Gate A
    is a claim about the assignment pool, and the pool membership word is not
    exposed by any address the project has found. So this metric bounds the
    claim rather than testing it -- a lead blocker who is never linked to
    anybody is consistent with Gate A, and one who is linked has still only
    been shown to have been *hit*, possibly by a defender who chose him.
    """
    entity = _the_lead_blocker(samples)
    if entity is None:
        return None
    partners = set()
    for value in samples.values(entity, "engagement_link"):
        linked = _linked_entity(value)
        if linked:
            partners.add(linked)
    return float(len(partners))


def m_blocks_on_coverage_defenders(samples: Samples) -> Optional[float]:
    """Frames an offensive player is engaged with a defender who is covering.

    The observable consequence of Gate B. `0x001f2cd8` admits only states 2,
    30, 51 and human-controlled into the defender pool, so a corner in man
    (22) or zone (37/38/40) cannot be *assigned* to any blocker. If that gate
    is real, this number should be at or near zero -- and any residue is
    informative in itself, because it can only have come from the defender
    initiating the engagement rather than from the assignment system.

    Requires both halves of a pair to be sampled, which is why the spec reads
    `ai_state` on the defence and `engagement_link` on the offence.
    """
    total = 0
    for frame in samples:
        covering = {entity for entity in frame.entities(DEFENSE)
                    if frame.get(entity, "ai_state") in COVERAGE_STATES}
        if not covering:
            continue
        for entity in frame.entities(OFFENSE):
            linked = _linked_entity(frame.get(entity, "engagement_link"))
            if linked in covering:
                total += 1
    return float(total)


def m_coverage_defenders_typical(samples: Samples) -> Optional[float]:
    """Median number of defenders in a coverage state across the play.

    Median, not maximum. Gate B's premise is that these men are on the field
    for the duration; a maximum over three hundred frames is a statistic that
    only ever moves upward on noise, so one torn read would set it for the
    whole iteration. The median asks the same question and cannot be moved by
    a handful of bad frames.

    Zero here means Gate B is untestable on this savestate -- either the play
    is not a coverage call, or `+0xBCC` is not a state id -- rather than
    confirmed.
    """
    counts = []
    for frame in samples:
        counts.append(sum(1 for entity in frame.entities(PLAYER)
                          if frame.get(entity, "ai_state") in COVERAGE_STATES))
    if not counts:
        return None
    counts.sort()
    middle = len(counts) // 2
    if len(counts) % 2:
        return float(counts[middle])
    return (counts[middle - 1] + counts[middle]) / 2.0


def m_play_length(samples: Samples) -> Optional[float]:
    """Sampled frames. Included so a short iteration is visible as short.

    Without it, a play that ended at frame 20 and one that ran to 300 both
    contribute a count metric to the same mean, and the difference reads as
    behaviour instead of as duration.
    """
    return float(len(samples))


# --------------------------------------------------------------------------
# The operator's two questions
# --------------------------------------------------------------------------

ASK_MISSED_BLOCK = OperatorAsk(
    id="should_have_blocked",
    question=("Was there a defender the lead blocker clearly should have taken, "
              "and did not?"),
    watch_for=("The back-side blocker running ahead of the carrier, from the snap "
               "until the carrier is tackled. Ignore whether he wins the block; "
               "the question is only whether he addressed the right man."),
    why_not_memory=(
        "There is no authored block target anywhere in the game. "
        "fb-wr-blocking.md establishes that as a closed-set negative: a census "
        "of every read of *(player+0x2FC) in states 25/26/31/33/47/72 against "
        "all 45 SetTarget call sites found hold timers, bearings, landmarks and "
        "teammate indices, and no defender reference. So 'the defender he ought "
        "to have blocked' is not a quantity in RAM to read -- it exists only in "
        "the mind of someone who knows what the play was drawn to do."),
    choices=("yes", "no", "no-lead-blocker", "unclear"),
    every=25, limit=8)

ASK_WARP = OperatorAsk(
    id="warp",
    question="Did any block land by a warp or position-snap rather than a collision?",
    watch_for=("The moment of contact on every block in frame. A warp reads as a "
               "player arriving without travelling, or as two bodies "
               "interpenetrating and then popping apart."),
    why_not_memory=(
        "Positions are readable, but 'a warp' is a claim about what the "
        "animation looks like at 60 Hz, and this project has no rubric that "
        "turns a position delta into that judgment. It matters because "
        "lead-blocker-targeting.md lists position-snap as an explicit "
        "ANTI-GOAL: a fix that raises the block-success metrics by warping has "
        "made the game worse while making the numbers better, and this is the "
        "only instrument that can catch it."),
    choices=("yes", "no", "unclear"),
    every=50, limit=4)


# --------------------------------------------------------------------------
# The trial
# --------------------------------------------------------------------------

#: Fields, and the doc each offset was taken from. The names must match
#: `addresses.yaml`; the offsets are here only so a mismatch is findable.
OFFENSE_FIELDS = (
    "position",     # +0xB04, fb-wr-blocking.md
    "block_mode",   # +0x3F0, fb-wr-blocking.md -- the Gate A field
    "engagement",   # +0x3E0, block-cycle.md
    "engagement_link",  # +0x3E4, a handle: who he is engaged with
)

DEFENSE_FIELDS = (
    "position",        # +0xB04
    "ai_state",        # +0xBCC -- see the note on the coverage states below
    "engagement",      # +0x3E0
    "engagement_link",  # +0x3E4
)

METRICS = (
    Metric("lead_blockers_seen", m_lead_blockers_seen, "players",
           "how many players ever hold block mode 3"),
    Metric("frames_in_mode3", m_frames_in_mode3, "frames",
           "how long the lead blocker stays out of the assignment pool"),
    Metric("first_mode3_frame", m_first_mode3_frame, "frame",
           "when state 47 installs"),
    Metric("lead_blocker_in_pool_frames", m_lead_blocker_in_pool_frames, "frames",
           "Gate A predicts zero", higher_is="worse"),
    Metric("lead_blocker_engaged_frames", m_lead_blocker_engaged_frames, "frames",
           "engaged by anyone, assigned or not"),
    Metric("pool_blockers", m_pool_blockers, "players",
           "control: somebody must be in the pool"),
    Metric("lead_blocker_partners", m_lead_blocker_partners, "players",
           "distinct men the lead blocker is ever engaged with"),
    Metric("blocks_on_coverage_defenders", m_blocks_on_coverage_defenders, "frames",
           "Gate B's observable consequence; predicted at or near zero",
           higher_is="worse"),
    Metric("coverage_defenders_typical", m_coverage_defenders_typical, "players",
           "Gate B's premise: coverage defenders are on the field"),
    Metric("play_length", m_play_length, "frames",
           "duration control; count metrics scale with it"),
)


def build() -> Trial:
    """Fresh `Trial` per call -- the stop condition carries per-play state."""
    return Trial(
        name="lead_blocker_gate_a",
        # Must be a snap-ready pre-snap frame of a run play with a lead blocker.
        # The savestate is the experiment: change it and nothing below transfers.
        # The filename is the provenance; the slot is what layer 1 can actually
        # load (SEAM REQUEST 1). Both, or the result is either unreproducible
        # or unrunnable. The operator must have this file in slot 3 -- `doctor`
        # cannot check that, which is why load_confirm below exists.
        state="SLUS-20752 (14F8B841).06.p2s  [pre-snap, scratch]",
        state_slot=6,
        question=("Does the lead-blocking fullback ever enter the block-assignment "
                  "system, and are coverage defenders ever targeted?"),
        load_confirm=LoadConfirm(
            check=loaded_state_is_pre_snap,
            description="*(0x00601280)+84 == 0 and the pointer is non-null",
            timeout_s=15.0),
        sample=SampleSpec(
            entities=(
                EntitySelector("player", OFFENSE_FIELDS, side=0, label="offense"),
                EntitySelector("player", DEFENSE_FIELDS, side=1, label="defense"),
                # Not `phase`: layer 3 established there is no play-phase
                # word to read -- 0x0015ADA0 returns a property of the called
                # play, not of where the play has got to. The clock is
                # frames_since_snap and the "what is he doing" signal is
                # ai_state, both of which this spec already samples.
                EntitySelector("game", ("frames_since_snap",)),
            ),
            # Every frame. Block mode transitions are single-frame events and
            # the whole experiment is about catching one.
            every=1,
        ),
        # Snap at frame 4, not 0: the state loads mid-frame and the first
        # sampled frames are the emulator settling, not the game deciding.
        script=(InputEvent(frame=4, button=SNAP_BUTTON, duration=3),),
        stop=StopCondition(max_frames=420, until=PlayEnded(), until_name="whistle",
                           timeout_s=90.0),
        metrics=METRICS,
        asks=(ASK_MISSED_BLOCK, ASK_WARP),
        # No setup writes: this is the baseline arm. The patched arm is the same
        # spec with the C3 word from fb-wr-blocking.md added here --
        #   Write(0x001F2F00, <sltiu v0,v1,4>, "fb-wr-blocking.md C3: admit mode 3")
        # -- which is why `setup` is a spec field and not a command-line flag.
        setup=(),
        cannot_conclude=(
            "Which blocking class the play authors for any player. That is play-file "
            "data (play-data.md, unread). 'Never told to block' and 'told to block, "
            "but no legal target' are indistinguishable here.",
            "Anything about run blocking in general. One savestate is one play, one "
            "formation, one personnel group and one defensive call.",
            "Whether a blocker was ever *assigned* a coverage defender, as opposed to "
            "ending up engaged with one. The assignment-pool membership word has no "
            "known address (SEAM REQUEST 6); engagement_link is contact, and a "
            "defender can initiate it.",
            "Whether the lead blocker is at contact, as opposed to in some engagement. "
            "The kind encoding inside +0x3E0 is not documented.",
            "Anything at single-frame resolution. There is no frame-advance in PINE, so "
            "the harness polls a counter and misses frames under load; every metric "
            "here is deliberately an aggregate or a two-frame-persistent event, and "
            "the run header records what fraction of frames were actually sampled.",
        ),
    )


TRIAL = build()

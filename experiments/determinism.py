"""Is a trial reproducible at all? Load, watch, repeat -- no input.

Everything the harness will ever claim depends on the answer. If loading one
savestate and letting the game run twice gives byte-identical sample streams,
a comparison needs a handful of iterations and any difference between two arms
is the patch. If it diverges, every trial is a draw from a distribution and a
difference needs statistics to believe. `compare.py` refuses to do statistics
on identical streams for exactly this reason, so this must be answered before
any experiment is run, not after.

**No input is sent.** That is the point: this isolates the emulator and the
game from the input layer. A press whose timing wobbled by a frame would make
a deterministic engine look random, and the conclusion would be wrong in the
direction that costs the most -- hundreds of unnecessary iterations forever.

**The state must be mid-play, not pre-snap.** `frames_since_snap` is the
runner's clock and it reads 0 until the ball is snapped, so a pre-snap state
leaves the runner waiting for a counter that cannot move until an input it has
not yet sent (SEAM REQUEST 4). Loading a play already in flight sidesteps that
entirely and exercises the interesting part anyway: the engine's per-frame
decisions, contest rolls and all.
"""
from tools.madden_lab.trial import (EntitySelector, LoadConfirm, SampleSpec,
                                    StopCondition, Trial)

#: Layer 3 verified these against real memory; see `addresses.yaml`.
FIELDS = ("position", "engagement", "block_mode", "ai_state", "xyz")


#: The snap counter, reached through a pointer. Layer 3 verified both.
SNAP_COUNTER_PTR = 0x00601280
SNAP_COUNTER_OFF = 84


def loaded_state_is_mid_play(emu) -> bool:
    """The load has landed and the play is running.

    Takes an `emu`, not a `World`: `LoadConfirm.satisfied` calls this with
    layer 1's object. Passing a world-shaped predicate instead raises inside
    the poll loop, the exception is swallowed as "not yet true", and the run
    dies fifteen seconds later blaming the savestate -- which is what happened
    the first time this was written.

    A savestate load replies OK when it means *queued* (layer 1), so sampling
    immediately reads the previous iteration's world. Mid-play the counter is
    non-zero, which is positive evidence rather than the absence of any. The
    null-pointer guard matters: reads of unmapped pages return 0 rather than
    erroring, so a drifted pointer would make this read `0 + 84` forever and
    the guard is what stops that being mistaken for a confirmed load.
    """
    base = emu.read(SNAP_COUNTER_PTR)
    if not 0x00100000 <= base < 0x02000000:
        return False
    return emu.read(base + SNAP_COUNTER_OFF) > 0


def build() -> Trial:
    return Trial(
        name="determinism_no_input",
        state="SLUS-20752 (14F8B841).07.p2s  [mid-play, scratch copy of 03]",
        state_slot=7,
        question="Do two loads of one savestate produce identical sample streams?",
        load_confirm=LoadConfirm(
            check=loaded_state_is_mid_play,
            description="frames_since_snap > 0 (the play is running)",
            timeout_s=15.0),
        sample=SampleSpec(
            entities=(
                EntitySelector("player", FIELDS, side=0, label="offense"),
                EntitySelector("player", FIELDS, side=1, label="defense"),
                EntitySelector("game", ("frames_since_snap",)),
            ),
            every=1,
        ),
        script=(),                      # deliberately none -- see the docstring
        stop=StopCondition(max_frames=180, timeout_s=60.0),
        metrics=(),
        asks=(),
        setup=(),
        cannot_conclude=(
            "Whether input timing is reproducible. No input is sent here by "
            "design; that is a separate question and a harder one.",
            "Whether determinism holds across an emulator restart, or across a "
            "different savestate. This observes one state in one session.",
        ),
    )

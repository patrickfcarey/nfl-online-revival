"""Watch a live play and report whether a double team ever registers.

    python3 tools/dt_detect.py [seconds]

Run it, then call a play and snap. It polls until the snap counter moves,
watches the registration window, and prints a verdict. The point is to turn
"which play produces a double team?" from a reverse-engineering question into a
thirty-second test the operator can repeat across a playbook.

Why this is needed at all: nothing in `docs/` says which plays author a double
team, and guessing wrong is expensive because the negative is silent -- a play
with no double team looks exactly like a play whose double team the harness
failed to see.

Reads two bytes per player:

* `dt_record` (+0x436) -- the double-team record index
* `dt_role`   (+0x437) -- 0 primary, 1 helper, 2 doubled defender, 3 peel-off,
                          **5 = UNASSIGNED**

`5` is the empty-slot value, established 2026-08-11 by reading all 22 players
live (every one read 5 with no double team on the field) and confirmed in the
image at `0x001f66dc` / `0x001f670c`, where the engine tests `bne v1, 5`.
`block-cycle.md`'s published enum does not mention 5, so a detector that treats
"non-zero role" as "in a double team" reports all 22 players and is useless.

Timing matters: `block-cycle.md`'s DT-2 puts a **60-frame post-snap window** on
registration, so a double team that is going to form does so inside the first
second. Sampling late finds nothing and proves nothing.
"""
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from tools.madden_lab.emu import Emu                           # noqa: E402
from tools.madden_lab.world import World                       # noqa: E402

UNASSIGNED = 5
ROLES = {0: "primary", 1: "helper", 2: "doubled defender", 3: "peel-off"}
#: State 32 owns engagement kinds 5 and 6 (addresses.yaml, confirmed in the
#: image at 0x001e81dc). Seeing it is independent evidence of a two-man block
#: even if the dt_* bytes never populate.
TWO_MAN_STATE = 32


def main(seconds=12.0):
    seconds = float(seconds)
    world = World(Emu())

    print("waiting for the snap ... (call a play and snap)")
    start = world.frames_since_snap()
    deadline = time.time() + 90
    while time.time() < deadline:
        now = world.frames_since_snap()
        if now is not None and start is not None and now > start and now < 20:
            break
        if now is not None and (start is None or now < start):
            start = now
        time.sleep(0.05)
    else:
        print("no snap seen in 90 s -- giving up")
        return 1

    print("snapped. watching %.0f s ...\n" % seconds)
    hits = {}                      # (side, index) -> (frame, record, role)
    two_man = {}                   # (side, index) -> frame
    seen = set()
    t0 = time.time()
    peak = 0
    while time.time() - t0 < seconds:
        frame = world.frames_since_snap()
        if frame is None or frame in seen:
            continue
        seen.add(frame)
        peak = max(peak, frame)
        for player in world.players():
            fields = player.snapshot(("dt_record", "dt_role", "ai_state"))
            role = fields.get("dt_role")
            key = (player.side, player.index)
            if role is not None and role != UNASSIGNED and key not in hits:
                hits[key] = (frame, fields.get("dt_record"), role)
            if fields.get("ai_state") == TWO_MAN_STATE and key not in two_man:
                two_man[key] = frame

    print("sampled %d frames, reached snap frame %d" % (len(seen), peak))
    if peak < 60:
        print("WARNING: never reached frame 60, the registration window "
              "(DT-2). A negative here is not evidence.")
    print()

    if hits:
        print("DOUBLE TEAM REGISTERED:")
        for (side, index), (frame, record, role) in sorted(hits.items()):
            print("   %s:%-2d  frame %-4d  record %-3s  role %d (%s)"
                  % ("OFF" if side == 0 else "DEF", index, frame, record,
                     role, ROLES.get(role, "?")))
    else:
        print("no dt_role other than %d on any player -- NO double team "
              "registered" % UNASSIGNED)

    print()
    if two_man:
        print("players that entered state %d (two-man block animation):"
              % TWO_MAN_STATE)
        for (side, index), frame in sorted(two_man.items()):
            print("   %s:%-2d  first seen at frame %d"
                  % ("OFF" if side == 0 else "DEF", index, frame))
    else:
        print("nobody entered state %d" % TWO_MAN_STATE)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))

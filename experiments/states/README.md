# Savestates — the experiments' inputs

Each `.p2s` here is a PCSX2 savestate for **Madden NFL 2004, SLUS-20752, CRC
`0x14F8B841`**, parked pre-snap. They live next to the specs rather than in
`extract/` (which is gitignored, and holds derived dumps) because a spec
without its state is not reproducible: every geometry anchor, LOS value and
player index in the Python is read out of these bytes.

Load one into the slot its spec names, then run the spec. `state_slot` in the
`Trial` is what the harness actually loads; the `state` string is provenance.

| file | slot | spec | what it is |
|---|---|---|---|
| `lead_blocker_slot6.p2s` | 6 | `lead_blocker.py` | misdirection **run**, single-back 3-WR, pulling right guard |
| `pass_protection_slot7.p2s` | 7 | `pass_protection.py` | **pass**, same formation, RB and TE both stay in to block |
| `ball_in_air_slot8.p2s` | 8 | — (probe only) | **mid-play, ball in flight**; used to test the bit-0 catch gate and to read live `dt_role`/`speed_cmd` |

## The two states share a formation

This matters and has already cost time. Slot 6 and slot 7 hold the **same
offensive formation with different play calls**, so their pre-snap player
positions agree to ~0.003 field units:

```
QB  ( 0.000, 13.400)     C   ( 0.009, 14.172)     LOS 15.000
HB  (-0.040,  7.972)     RG  ( 1.672, 14.138)
```

A geometry-based load confirm therefore proves "a pre-snap world in this
formation", **not** which state was loaded. The play call is not visible in
memory before the snap. `pass_protection.py` carries a post-snap control for
this — `qb_dropback`, which reads ~7.2 yards on the pass and near zero on the
run — and any new spec sharing this formation needs one too.

## Recording a new state

Save it in a scratch slot (**6 or higher**; slots 1–5 belong to the operator),
copy it out of `~/.config/PenguinScreen2/sstates/` on the rig, and add a row
above. Save **after both plays are called, at the line, pre-snap, with nothing
pressed** — the harness supplies the snap itself, and a state saved mid-input
replays that input.

These are ~19 MB each and committed as plain binaries, not LFS. If this
directory grows past a handful of states, that decision is worth revisiting.

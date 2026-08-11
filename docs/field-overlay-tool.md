# Field overlay tool — paint proven world points onto a game screenshot

Design, 2026-08-11. A tool that takes a game state and produces the frame's
screenshot with exact field locations painted on it — a blocker's authored
landmark, a route, a coverage zone — each placed at a screen pixel that is
*mathematically proven* to be where that world point renders, so a subject
matter expert can eyeball whether the game's intent matches football.

## Why "proven" is the hard requirement

Painting a dot is easy. Painting it at the *exact* pixel where the PS2 would
render that world coordinate is the whole tool, and it is a camera-projection
problem: world `(x, y, z)` → screen `(u, v)`. Get the projection wrong and the
overlay lies convincingly, which is worse than no tool. So the projection is
not assumed — it is **derived and validated against ground truth in the same
frame.**

## The projection, and how it is proven

The engine transforms world → view → projection → screen through a matrix it
builds each frame and uploads to VU1. The tool needs that world-to-screen
map `M`. Two ways to get it, in preference order:

1. **Solve it from correspondences (self-validating).** We already know the
   world position of all 22 players (read from memory, `player+0x190/194/198`)
   and we can see where they render in the screenshot. Six or more
   world↔screen pairs over-determine `M` (the classic camera DLT), and the
   fit's **reprojection error in pixels is the proof** — a good `M` puts every
   projected player on its own sprite.
2. **Read `M` from memory.** Find where the render code stores the
   view-projection matrix and read it directly. Cleaner if located; the search
   is the RE cost.

Either way, **validation is identical and non-negotiable:** project all 22
known player positions and require they land on the rendered players. The two
on-screen **name labels are measured anchors** — in the in-play frame the
"JOHNSON" label sits under the QB and "PITTMAN" under the HB, giving two
hand-measured screen points to check reprojection error against in pixels. The
tool reports that error and refuses to paint a landmark if it exceeds a
threshold: no proof, no overlay.

## Milestones

* **M1 — inputs.** From a savestate: the embedded `Screenshot.png` and its
  resolution, and every player's world position. Most of this exists
  (`world.py`, the savestate reader).
* **M2 — the projection, proven.** Recover `M`, validate by reprojecting the
  22 players, report anchor error in pixels. This is the crux and the RE work.
* **M3 — the landmark's world coordinates.** Read the lead blocker's authored
  landmark from the play record (state-47 enter reads `record+1/+3` as a point
  or `record+2` as a bearing into the fields near `+0x164`). **This is open
  question O2 from `lead-blocker-requirements.md` — so building this tool
  resolves O2 as a byproduct**, which is why the landmark work "deserves its
  own tool" was the right call.
* **M4 — overlay.** Draw on the screenshot: each player (proof markers), the
  landmark (the headline), optionally the blocker→landmark vector and his
  facing cone. Output an annotated PNG.
* **M5 — batch.** Run M1–M4 across a set of run-play savestates and emit a
  contact sheet, so the SME compares landmarks across plays at a glance.

## Interfaces

* Input is a savestate path (offline, no rig) or a live PINE snapshot. The
  projection and overlay are pure functions of `(memory image, screenshot)`,
  so the whole tool runs against `extract/ee_inplay.bin` +
  `extract/ee_inplay.png` with no emulator.
* Output is a PNG plus a small JSON of what was drawn and the proven
  reprojection error, so a result is auditable later.

## What this is NOT

* Not a live HUD — it annotates a captured frame, because the proof needs the
  exact frame the screenshot came from.
* Not trusted until M2's reprojection error is small on the anchors. A tool
  that paints a plausible-but-wrong dot is the exact failure mode this project
  exists to avoid; the error report is the guard.

## Status

Design only. M2 (the camera projection) is the crux and the first real work,
and it is doable offline against the in-play dump. M3 doubles as O2. No overlay
is trusted until the reprojection proof passes.

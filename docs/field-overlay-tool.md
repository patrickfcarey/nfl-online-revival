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

**Corrected 2026-08-11 after a strict review — the first draft of this
section was mathematically wrong.** See "Review corrections" below.

The tool needs a world-to-screen map. **It is a homography, not a full camera
matrix.** Every player stands on the field at `z = 0` (verified: all 22 have
z exactly 0.0 in the in-play dump), so the correspondences are **coplanar**,
and a full 3×4 projective camera (the classic DLT) is **degenerate** on
coplanar points — it cannot be recovered from them. What *can* be recovered,
and all we need, is the 3×3 homography `H` mapping the ground plane to the
image: 8 degrees of freedom, **4 correspondences minimum**, well-conditioned
with 22. This makes the tool simpler than the first draft claimed, not harder.

* **Fit `H` from ground-plane correspondences.** World `(x, y)` on the field →
  screen `(u, v)`. Fit on 4+ points, and the **reprojection error on the
  points not used for the fit is the proof.**
* **Alternative: read the matrix from memory** if the view-projection matrix
  can be located. Would also give the airborne case (below). Not required.

**Validation is non-negotiable:** fit on a subset, reproject the rest, report
error in pixels, and refuse to paint if it exceeds a threshold. No proof, no
overlay.

**Getting the screen-side of the correspondences costs manual work**, which
the first draft glossed: memory gives world positions, but where each player
*renders* has to come from hand-annotating the screenshot once per camera
setup (or sprite detection). The on-screen selected-player label helps —
see the corrections below — but it is one point, not a free set.

**Limitation this creates:** a ground-plane homography can only place points
**on the ground**. That covers landmarks, routes and zones — everything this
tool is for — but *not* a ball in flight or a jumping player. Painting
anything airborne needs the full camera matrix from memory.

## Milestones

* **M1 — inputs.** From a savestate: the embedded `Screenshot.png` and its
  resolution, and every player's world position. Most of this exists
  (`world.py`, the savestate reader).
* **M2 — the projection, proven.** Recover `M`, validate by reprojecting the
  22 players, report anchor error in pixels. This is the crux and the RE work.
* **M3 — the landmark's world coordinates. Bigger than the first draft
  assumed.** State-47's enter (`0x001B66A8`) decodes the play record into
  three fields, now read precisely off the disassembly:
    * `record+2` → 24-bit BAM bearing → **`self+0x164`**;
    * and *only when that bearing is zero*, `record+1` → **`self+0x158`** as
      `(v>>3) + (v&7)·(1/7)`, and `record+3` → **`self+0x15C`** as `v·(1/255)`.

  So "the point lands near `+0x164`" (first draft) is wrong: `+0x164` is the
  *bearing*; the other two fields are `+0x158`/`+0x15C`.

  **And they may not be an x/y point at all.** The scale factors argue
  against it: `1/255` is a *normalisation* to 0..1, not a yard coordinate,
  and the decoded `+0x158` value is immediately passed as `f12` to
  `0x004ADC40` — the same routine the cone code calls with an *angle*. A
  bearing/‌distance or angle/‌fraction pair fits the evidence better than
  "x and y in yards". **This is unresolved, and it is the real content of
  O2.** The tool must not paint a landmark until the representation is
  settled, or it will paint a confident dot in the wrong place — the exact
  failure this design exists to prevent.
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

## Review corrections (2026-08-11, strict pass)

What a hostile read of the first draft found, all verified against the binary
or the data:

1. **DLT on coplanar points — a real mathematical error.** All 22 players sit
   at `z = 0`, so a full 3×4 camera cannot be solved from them. Corrected to a
   ground-plane homography, which needs 4 points instead of 6 and is better
   conditioned. Caught before any code was written.
2. **"Two name-label anchors in one frame" — false.** The blue disc + name is
   the *selected/ball-carrier* marker, and there is **one per frame**. The
   "JOHNSON" and "PITTMAN" labels cited came from **two different
   screenshots** (the pre-snap frame and the mid-play frame), not one. So a
   frame yields **one** hand-measurable anchor, and using it also requires
   knowing *which* player is selected.
3. **Landmark fields were imprecise and the interpretation is unproven** —
   `+0x158`/`+0x15C`, not "near `+0x164`", and probably not an x/y point.
   See M3.
4. **The VU1 claim was unverified.** The first draft asserted the engine
   "builds a matrix each frame and uploads it to VU1" as fact. That is typical
   PS2 architecture but **was never checked for this game**, and nothing in
   this design depends on it. Removed rather than left as decoration.

What survived the review:

* **1 field unit = 1 yard — solid.** Independently confirmed: the hash-mark
  constant in `zone-bunching.md` is ±3.0799999, and real NFL hashes are
  18′6″ apart = ±3.083 yd from centre. Agreement to 0.003 yd.
* **The 22 world positions read cleanly** and are internally consistent with
  the formation in the screenshot.
* **The 60° cone is a half-angle** (±60°, 120° total): `0x00469FC8` computes
  `min(diff, 360−diff)`, a true absolute angular difference, tested `< 60°`.
  So `lead-blocker-requirements.md`'s R2 reasoning — a man directly behind is
  already excluded, and the cone is generous at the sides — is correct.

## Status

Design only, now corrected. **M3/O2 is the blocker, not M2:** the camera
homography is a well-understood fit against data already in hand, whereas the
landmark's *representation* is genuinely unknown and must be settled before
anything is painted. Sequence: resolve what `+0x158`/`+0x15C` mean, then M2,
then paint. No overlay is trusted until the reprojection proof passes.

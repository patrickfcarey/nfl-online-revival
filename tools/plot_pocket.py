"""Fan-plot a whole pass pocket across every iteration of a run.

    python3 tools/plot_pocket.py results.jsonl out.png

`plot_routes.py` draws one man's route, which is the right picture for a
pulling guard. Pass protection is not one man: it is seven blockers, four
rushers and a quarterback, and the thing you want to see is the *shape* they
make and where it breaks. So this draws everyone, one translucent line per
player per iteration, with the defence in red and the offence in amber.

Reads `pos_x`/`pos_y`, and falls back to the older `xyz` tuple so it still
works on result files recorded before those were sampled separately.

Line of scrimmage comes from the file's own `game`/`los` sample when present.
Never derive it from the centre's body: that reads 14.219 against the engine's
15.000, and the 0.78 yd bias it introduces is invisible in a picture -- every
line simply sits three quarters of a yard too deep and still looks plausible.
"""
import collections
import json
import sys

from PIL import Image, ImageDraw, ImageFont

SRC = sys.argv[1] if len(sys.argv) > 1 else "pp20.jsonl"
OUT = sys.argv[2] if len(sys.argv) > 2 else "pocket.png"
LOS = 15.000                                   # fallback only; see module docstring

W, H = 1500, 1150
X0, X1, Y0, Y1 = -22., 22., 2., 26.


def font(size, bold=False):
    try:
        return ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"
            % ("-Bold" if bold else ""), size)
    except Exception:
        return ImageFont.load_default()


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------

xs = collections.defaultdict(dict)      # (iteration, entity) -> {frame: x}
ys = collections.defaultdict(dict)
position = {}                           # entity -> position byte
commit = {}                             # (iteration, entity) -> first committed frame
los_seen = None

truncated = 0
for line in open(SRC):
    if '"sample"' not in line:
        continue
    try:
        row = json.loads(line)
    except ValueError:
        # These files are append-only and multi-gigabyte, so a partial last
        # line is normal: a killed run, a copy taken while writing, a `head`
        # for a quick look. Refusing to plot the other 199 iterations because
        # the 200th is half-written would be the wrong trade -- but a silent
        # skip would be worse, so the count goes in the caption.
        truncated += 1
        continue
    entity, field, value = row.get("entity"), row.get("field"), row.get("value")
    if value is None:
        continue
    if entity == "game":
        if field == "los":
            los_seen = float(value)
        continue
    if not entity or not entity.startswith("player:"):
        continue
    key = (row["iteration"], entity)
    frame = row["frame"]
    if field == "pos_x":
        xs[key][frame] = float(value)
    elif field == "pos_y":
        ys[key][frame] = float(value)
    elif field == "xyz":                # legacy files
        xs[key][frame], ys[key][frame] = float(value[0]), float(value[1])
    elif field == "position":
        position.setdefault(entity, int(value))
    elif field == "engagement" and isinstance(value, int) and value >= 2:
        if key not in commit or frame < commit[key]:
            commit[key] = frame

if los_seen is not None:
    LOS = los_seen

paths = {}
for key in xs:
    frames = sorted(set(xs[key]) & set(ys[key]))
    if len(frames) > 1:
        paths[key] = [(xs[key][f], ys[key][f]) for f in frames]

iterations = sorted({it for it, _e in paths})

# --------------------------------------------------------------------------
# Draw
# --------------------------------------------------------------------------

sx = lambda x: (x - X0) / (X1 - X0) * W
sy = lambda y: H - 66 - (y - Y0) / (Y1 - Y0) * (H - 120)

im = Image.new("RGB", (W, H), (24, 84, 32))
d = ImageDraw.Draw(im)
for line_y in range(int(Y0), int(Y1) + 1):
    d.line([(0, sy(line_y)), (W, sy(line_y))],
           fill=(255, 255, 255) if line_y % 5 == 0 else (150, 190, 155),
           width=3 if line_y % 5 == 0 else 1)
# Hash marks, at the engine's own +-3.08.
for hx in (-3.08, 3.08):
    d.line([(sx(hx), 0), (sx(hx), H)], fill=(120, 170, 128), width=1)
d.line([(0, sy(LOS)), (W, sy(LOS))], fill=(255, 235, 59), width=5)
d.text((14, sy(LOS) - 30), "LINE OF SCRIMMAGE  (%.3f)" % LOS,
       font=font(19, True), fill=(255, 235, 59))

# Alpha scales down with iteration count so a 20-run fan and a 200-run fan are
# both readable: identical plays stack into a bright core, outliers stay faint.
alpha = max(25, min(140, int(1300 / max(1, len(iterations)))))

QB, OFFENCE, DEFENCE = (255, 255, 255), (255, 193, 7), (239, 83, 80)

# ONE LAYER PER ITERATION, composited in turn. Drawing every path into a single
# shared layer does NOT stack: PIL's ImageDraw *replaces* the pixels it touches
# rather than alpha-blending into them, so twenty identical routes drawn at
# alpha 18 produce a line of alpha 18, not a bright core. Both this tool and
# plot_routes.py claimed stacking in their captions while doing exactly that --
# the picture looked like a faint single run whether the engine was
# deterministic or not, which is the one thing the fan exists to distinguish.
canvas = im.convert("RGBA")
for iteration in iterations:
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for (it, entity), pts in sorted(paths.items()):
        if it != iteration:
            continue
        side = entity.split(":")[1]
        if side == "0":
            colour = QB if position.get(entity) == 0 else OFFENCE
        else:
            colour = DEFENCE
        width = 4 if position.get(entity) == 0 else 3
        ld.line([(sx(x), sy(y)) for x, y in pts],
                fill=colour + (alpha,), width=width, joint="curve")
        frame = commit.get((iteration, entity))
        if frame is not None and side == "0":
            frames = sorted(set(xs[(iteration, entity)]) & set(ys[(iteration, entity)]))
            first = [(x, y) for f, (x, y) in zip(frames, pts) if f >= frame]
            if first:
                cx, cy = first[0]
                ld.ellipse([sx(cx) - 8, sy(cy) - 8, sx(cx) + 8, sy(cy) + 8],
                           outline=(255, 23, 68, min(255, alpha * 2)), width=3)
    canvas = Image.alpha_composite(canvas, layer)

im = canvas.convert("RGB")
d = ImageDraw.Draw(im)

d.rectangle([0, 0, W, 56], fill=(12, 42, 16))
d.text((14, 14), "Pass pocket: %d plays overlaid, %d player-paths"
       % (len(iterations), len(paths)), font=font(23, True), fill=(255, 255, 255))

d.rectangle([0, H - 66, W, H], fill=(12, 42, 16))
qb_depth = []
for (iteration, entity), pts in paths.items():
    if position.get(entity) == 0:
        qb_depth.append(LOS - min(y for _x, y in pts))
if qb_depth:
    qb_depth.sort()
    d.text((14, H - 60),
           "QB drop: median %.2f yd, range %.2f..%.2f over %d plays"
           % (qb_depth[len(qb_depth) // 2], qb_depth[0], qb_depth[-1], len(qb_depth)),
           font=font(16), fill=(255, 241, 180))
d.text((14, H - 36),
       "White = QB.  Amber = offence, red ring where a blocker first commits.  "
       "Red = defence.  Identical plays stack bright; outliers stay faint.",
       font=font(15), fill=(255, 171, 145))

if truncated:
    d.text((14, H - 14), "%d unparseable line(s) skipped (truncated file)" % truncated,
           font=font(13), fill=(255, 138, 128))

im.save(OUT)
print("wrote %s  (%d iterations, %d paths, LOS %.3f%s%s)"
      % (OUT, len(iterations), len(paths), LOS,
         "" if los_seen is not None else "  [FALLBACK -- file carried no los sample]",
         "  [%d lines skipped]" % truncated if truncated else ""))

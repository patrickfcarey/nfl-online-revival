"""Fan-plot the pulling guard's route across every iteration of a run.

    python3 tools/plot_routes.py results.jsonl out.png [entity]

One line per iteration, a ring where he first engages, and the commit-depth
distribution in the caption. Written because a single route shows either the
good outcome or the bad one and looks definitive either way -- the fan shows
the defect *and* its run-to-run spread, which is what an acceptance test on a
near-deterministic engine actually needs.
"""
import json, sys, math, struct, collections
from PIL import Image, ImageDraw, ImageFont

SRC = sys.argv[1] if len(sys.argv) > 1 else "base2.jsonl"
OUT = sys.argv[2] if len(sys.argv) > 2 else "routes.png"
WHO = sys.argv[3] if len(sys.argv) > 3 else "player:0:9"
# The engine's own line of scrimmage, [0x00601F4C]+0x10, verified 15.000 in
# both dumps. This was 14.17 -- derived from the centre's body position -- and
# every commit-depth caption this tool printed was ~0.78 yd too deep, the same
# bias already fixed in lead_blocker._los. A result file that carries a `los`
# sample overrides this; the constant is only the fallback for older files.
LOS = 15.000

def font(sz, b=False):
    try:
        return ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % ("-Bold" if b else ""), sz)
    except Exception:
        return ImageFont.load_default()

routes, commits = collections.defaultdict(list), {}
los_seen = None
for line in open(SRC):
    if '"sample"' not in line:
        continue
    r = json.loads(line)
    if r.get("entity") != WHO:
        continue
    if r.get("entity") == "game" and r.get("field") == "los" and r.get("value"):
        los_seen = float(r["value"])          # prefer the engine's own value
    if r["field"] == "xyz":
        routes[r["iteration"]].append((r["tick"], r["value"]))
    elif r["field"] == "engagement" and r["value"] >= 2:
        t = commits.get(r["iteration"])
        commits[r["iteration"]] = r["tick"] if t is None else min(t, r["tick"])
for k in routes:
    routes[k].sort()

if los_seen is not None:
    LOS = los_seen
W, H = 1400, 1100
X0, X1, Y0, Y1 = -20., 20., 6., 30.
sx = lambda x: (x - X0) / (X1 - X0) * W
sy = lambda y: H - 58 - (y - Y0) / (Y1 - Y0) * (H - 108)
im = Image.new("RGB", (W, H), (24, 84, 32))
d = ImageDraw.Draw(im)
for yl in range(6, 31):
    d.line([(0, sy(yl)), (W, sy(yl))],
           fill=(255, 255, 255) if yl % 5 == 0 else (150, 190, 155),
           width=3 if yl % 5 == 0 else 1)
d.line([(0, sy(LOS)), (W, sy(LOS))], fill=(255, 235, 59), width=5)
d.text((14, sy(LOS) - 30), "LINE OF SCRIMMAGE", font=font(20, True), fill=(255, 235, 59))

# Translucent overlay: with many runs the identical ones stack into a bright
# core and the outliers stay visibly faint, which is the point of the fan.
lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
ld = ImageDraw.Draw(lay)
depths = []
for it in sorted(routes):
    pts = [(sx(p[0]), sy(p[1])) for _t, p in routes[it]]
    if len(pts) > 1:
        ld.line(pts, fill=(255, 193, 7, 60), width=3, joint="curve")
    ct = commits.get(it)
    if ct is not None:
        cp = next((p for t, p in routes[it] if t >= ct), None)
        if cp:
            depths.append(cp[1] - LOS)
            X, Y = sx(cp[0]), sy(cp[1])
            ld.ellipse([X - 9, Y - 9, X + 9, Y + 9], outline=(255, 23, 68, 120), width=3)
im = Image.alpha_composite(im.convert("RGBA"), lay).convert("RGB")
d = ImageDraw.Draw(im)
d.rectangle([0, 0, W, 50], fill=(12, 42, 16))
d.text((14, 12), "Pulling guard: %d runs overlaid" % len(routes), font=font(23, True),
       fill=(255, 255, 255))
d.rectangle([0, H - 58, W, H], fill=(12, 42, 16))
if depths:
    depths.sort()
    med = depths[len(depths) // 2]
    d.text((14, H - 52), "Commit depth: median %+.2f yd, range %+.2f..%+.2f over %d runs"
           % (med, depths[0], depths[-1], len(depths)), font=font(16), fill=(255, 241, 180))
d.text((14, H - 28), "Each faint line is one play. Identical runs stack into a bright core; "
       "outliers stay faint. Acceptance target: commit >= 4 yd.",
       font=font(15), fill=(255, 171, 145))
im.save(OUT)
print("wrote %s (%d routes, %d with a commit)" % (OUT, len(routes), len(depths)))

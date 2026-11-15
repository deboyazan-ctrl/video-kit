#!/usr/bin/env python3
"""
track_sheet.py <scene_dir> --range FIRST LAST --ref N
               --rows Y0 Y1 --cols X0 X1 [--hold-after N] [--out track.json]

Vertical tracking for a sheet that has nothing on it but the typed line.

solve_track.py cannot be used here. It fits a homography to image gradient, and
on this shot the only gradient inside the card is the line itself - which the
typebars cut across on exactly the frames that matter, so every variant tried
(incremental search, raw-value template matching, gradient template matching,
and tracking the sheet's top edge) locked onto the paper's smooth shading or
onto the machine instead of the paper, and all of them reported a drift of
about 2 px where the real drift is 42.

What does work is measuring the line's own row directly, with the machine
excluded by brightness: the typebars and the carriage are far darker than the
ink, so ink is "darker than paper but not by that much", and a row is only
counted when it is still mostly paper. The platen rolls, so the motion is a
vertical translation and one number per frame describes it.
"""
import argparse
import json
from pathlib import Path
import subprocess

import numpy as np


def probe(path, entries, stream=None):
    cmd = ["ffprobe", "-v", "error"]
    if stream:
        cmd += ["-select_streams", stream]
    cmd += ["-show_entries", entries, "-of", "default=nw=1:nk=1", str(path)]
    return [l for l in subprocess.run(cmd, capture_output=True, text=True,
                                      check=True).stdout.strip().splitlines() if l]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene_dir")
    ap.add_argument("--range", type=int, nargs=2, required=True, metavar=("FIRST", "LAST"))
    ap.add_argument("--ref", type=int, required=True)
    ap.add_argument("--rows", type=int, nargs=2, required=True, metavar=("Y0", "Y1"),
                    help="a band tall enough to hold the line wherever it goes")
    ap.add_argument("--cols", type=int, nargs=2, required=True, metavar=("X0", "X1"))
    ap.add_argument("--hold-after", type=int,
                    help="past here the shot is dissolving and the reading is"
                         " the incoming shot; hold the last clean value")
    ap.add_argument("--template", type=int, nargs=4, metavar=("X0", "Y0", "X1", "Y1"),
                    help="switch to a 2-D search matching this piece of the reference "
                         "frame's ink mask. Needed when the surface moves sideways as "
                         "well - on the typewriter shot the carriage returns 62 px "
                         "between 1072 and 1084. Put the box on a part of the line the "
                         "carriage never swallows, and note that a box containing any "
                         "of the machine will lock onto it instead: the typebars are "
                         "near black on white paper and the ink is only grey, so the "
                         "match follows whichever is stronger. That is why this works "
                         "on the ink MASK and not on the image or its gradient - both "
                         "of those reported a confident zero on a shot that moves.")
    ap.add_argument("--search", type=int, nargs=4, default=[-40, 40, -80, 40],
                    metavar=("DY0", "DY1", "DX0", "DX1"),
                    help="how far to look, in reference-frame pixels")
    ap.add_argument("--median", type=int, default=4,
                    help="median filter radius for the 2-D search, whose per-frame "
                         "correlation is only 0.3-0.4 where the typebars cover the line")
    ap.add_argument("--vertical-only", action="store_true",
                    help="hold dx at zero. On a platen the paper only rolls, and"
                         " the horizontal reading is not measuring the paper: the"
                         " typebars hide different parts of the line on different"
                         " frames, so the ink centroid slides sideways by tens of"
                         " pixels while the line itself has not moved at all.")
    ap.add_argument("--smooth", type=int, default=2)
    ap.add_argument("--out", default="track.json")
    args = ap.parse_args()

    scene = Path(args.scene_dir)
    rec = json.loads((scene / "recipe.json").read_text())
    video = Path(rec["source"])
    W, H = (int(x) for x in probe(video, "stream=width,height", "v:0")[:2])
    first, last = args.range
    y0, y1 = args.rows
    x0, x1 = args.cols

    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(video),
         "-vf", f"select='between(n\\,{first}\\,{last})',format=gray",
         "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True).stdout
    n_px = W * H
    frames = {first + i: np.frombuffer(raw[i * n_px:(i + 1) * n_px], np.uint8)
              .reshape(H, W).astype(np.float32) for i in range(len(raw) // n_px)}
    print(f"decoded {len(frames)} frames")

    pos = {}
    if args.template:
        tx0, ty0, tx1, ty1 = args.template

        def ink(f):
            b = frames[f]
            paper = np.percentile(b[y0:y1, x0:x1], 90)
            return ((b < paper - 22) & (b > paper - 95)).astype(np.float32)

        masks = {f: ink(f) for f in frames}
        t = masks[args.ref][ty0:ty1, tx0:tx1]
        t = t - t.mean()
        print(f"template: {int(masks[args.ref][ty0:ty1, tx0:tx1].sum())} ink px")
        for f in sorted(frames):
            M = masks[f]
            def score(dx, dy):
                c = M[ty0 + dy:ty1 + dy, tx0 + dx:tx1 + dx]
                if c.shape != t.shape:
                    return -1.0
                c = c - c.mean()
                d = np.sqrt((t * t).sum() * (c * c).sum())
                return float((t * c).sum() / d) if d > 1e-9 else -1.0

            # Coarse to fine: the full window is wide enough to hold both a
            # carriage return and the platen roll, and searching every offset
            # in it one pixel at a time is minutes per shot for nothing.
            sy0, sy1, sx0, sx1 = args.search
            best = None
            for dy in range(sy0, sy1 + 1, 3):
                for dx in range(sx0, sx1 + 1, 3):
                    v = score(dx, dy)
                    if best is None or v > best[0]:
                        best = (v, dx, dy)
            for dy in range(best[2] - 3, best[2] + 4):
                for dx in range(best[1] - 3, best[1] + 4):
                    v = score(dx, dy)
                    if v > best[0]:
                        best = (v, dx, dy)
            pos[f] = (float(best[1]), float(best[2]))
        if args.median:
            ks = sorted(pos)
            r = args.median
            pos = {f: (float(np.median([pos[k][0] for k in ks[max(0, i - r):i + r + 1]])),
                       float(np.median([pos[k][1] for k in ks[max(0, i - r):i + r + 1]])))
                   for i, f in enumerate(ks)}
        rx0, ry0 = pos[args.ref]
        pos = {f: (pos[f][0] - rx0, pos[f][1] - ry0) for f in pos}
        print(f"  reference frame reads {pos[args.ref]} - must be (0.0, 0.0)")
        assert pos[args.ref] == (0.0, 0.0)

    for f in sorted(frames) if not args.template else []:
        b = frames[f][y0:y1, x0:x1]
        paper = np.percentile(b, 90)
        # ink is darker than the paper, but the typebars and carriage are darker
        # still - that gap is what separates the two.
        ink = ((b < paper - 22) & (b > paper - 95)).astype(np.float32)
        ink *= (((b > paper - 95).mean(axis=1)) > 0.80)[:, None]
        rows = ink.sum(axis=1)
        if rows.sum() < 25:
            continue
        i = int(np.argmax(np.convolve(rows, np.ones(9) / 9, mode="same")))
        lo, hi = max(i - 14, 0), min(i + 15, len(rows))
        w = rows[lo:hi]
        cy = y0 + float((np.arange(lo, hi) * w).sum() / max(w.sum(), 1e-6))
        cols = ink[lo:hi].sum(axis=0)
        cx = x0 + float((np.arange(len(cols)) * cols).sum() / max(cols.sum(), 1e-6))
        pos[f] = (cx, cy)

    if args.ref not in pos:
        raise SystemExit(f"no reading on the reference frame {args.ref}")

    if args.hold_after is not None:
        held = max(f for f in pos if f <= args.hold_after)
        for f in list(pos):
            if f > args.hold_after:
                pos[f] = pos[held]
        print(f"held from frame {held} onward (shot is dissolving)")

    ks = sorted(pos)
    if args.smooth:
        r = args.smooth
        sm = {}
        for i, f in enumerate(ks):
            w = ks[max(0, i - r):i + r + 1]
            sm[f] = (float(np.mean([pos[k][0] for k in w])),
                     float(np.mean([pos[k][1] for k in w])))
        pos = sm

    rx, ry = pos[args.ref]
    corners = np.array([[0, 0], [W, 0], [W, H], [0, H]], float)
    track = {}
    for f in ks:
        dx, dy = pos[f][0] - rx, pos[f][1] - ry
        if args.vertical_only:
            dx = 0.0
        track[str(f)] = {"corners": (corners + np.array([dx, dy])).tolist(),
                         "corr": 1.0, "dx": round(dx, 2), "dy": round(dy, 2)}

    print("%6s %8s %8s" % ("frame", "dx", "dy"))
    for f in ks:
        if f % 10 == 0:
            print("%6d %8.1f %8.1f" % (f, track[str(f)]["dx"], track[str(f)]["dy"]))

    out = scene / args.out
    out.write_text(json.dumps(track))
    print("->", out)


if __name__ == "__main__":
    main()

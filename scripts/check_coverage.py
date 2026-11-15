#!/usr/bin/env python3
"""
check_coverage.py <scene_dir> [--window X0 Y0 X1 Y1] [--polarity bright|dark]

How much of the old lettering the patch will actually touch.

Run this before rendering. Every mark that the edit is supposed to remove has to
fall inside one of the patch boxes; anything outside them is never touched by
anything and simply stays in the picture. On scene 3 a vertical beaded stroke
ran below the first box and to the left of the second, landed in the gap between
them, and survived three rounds of chasing the wrong cause - a visible mark that
a single number here would have found immediately.

Reports the share of old-only marks left uncovered, where they are, and writes a
picture with them in red.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def box_blur(a, r, passes=3):
    k = 2 * r + 1
    out = a.astype(np.float32)
    for _ in range(passes):
        for ax in (0, 1):
            pad = [(0, 0), (0, 0)]
            pad[ax] = (r, r)
            q = np.pad(out, pad, mode="edge")
            c = np.cumsum(q, axis=ax)
            c = np.concatenate([np.zeros_like(np.take(c, [0], axis=ax)), c], axis=ax)
            out = (np.take(c, range(k, c.shape[ax]), axis=ax)
                   - np.take(c, range(0, c.shape[ax] - k), axis=ax)) / k
    return out


def dilate(m, r):
    out = m.copy()
    for _ in range(r):
        p = np.zeros_like(out)
        p[1:, :] |= out[:-1, :]; p[:-1, :] |= out[1:, :]
        p[:, 1:] |= out[:, :-1]; p[:, :-1] |= out[:, 1:]
        out |= p
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene_dir")
    ap.add_argument("--window", type=int, nargs=4, metavar=("X0", "Y0", "X1", "Y1"),
                    help="area to look in; defaults to the patch boxes plus a margin")
    ap.add_argument("--polarity", choices=["bright", "dark"], default="dark",
                    help="are the marks brighter than the surface, or darker")
    ap.add_argument("--threshold", type=float, default=9.0)
    ap.add_argument("--out", default="coverage.png")
    args = ap.parse_args()

    scene = Path(args.scene_dir)
    rec = json.loads((scene / "recipe.json").read_text())
    A = np.asarray(Image.open(scene / rec["plates"]["original"]).convert("L")).astype(np.float32)
    P = np.asarray(Image.open(scene / rec["plates"]["edited_matched"]).convert("L")).astype(np.float32)
    H, W = A.shape

    patch = rec["patch"]
    cores = list(patch.get("cores") or [patch["core"]]) + list(patch.get("fill_cores", []))
    covered = np.zeros((H, W), bool)
    for c in cores:
        covered[c[1]:c[3], c[0]:c[2]] = True
    for er in rec.get("erase", []):
        r = er["rect"]
        covered[r[1]:r[3], r[0]:r[2]] = True

    if args.window:
        wx0, wy0, wx1, wy1 = args.window
    else:
        ys, xs = np.nonzero(covered)
        m = 70
        wx0, wy0 = max(xs.min() - m, 0), max(ys.min() - m, 0)
        wx1, wy1 = min(xs.max() + m, W), min(ys.max() + m, H)
    win = np.zeros((H, W), bool)
    win[wy0:wy1, wx0:wx1] = True
    print(f"looking in x {wx0}-{wx1}, y {wy0}-{wy1}")

    def marks(img):
        dev = img - box_blur(img, 16)
        return (dev > args.threshold) if args.polarity == "bright" else (dev < -args.threshold)

    old = marks(A) & win
    new = marks(P) & win
    old_only = old & ~dilate(new, 5)
    missed = old_only & ~covered

    pct = 100 * missed.sum() / max(old_only.sum(), 1)
    print(f"old-only marks: {int(old_only.sum())} px")
    print(f"  not inside any patch box: {int(missed.sum())} px  ({pct:.0f}%)")
    if missed.sum():
        ys, xs = np.nonzero(missed)
        print(f"  they sit at x {xs.min()}-{xs.max()}, y {ys.min()}-{ys.max()}")
        # the densest cluster is the one that will actually show
        h, _, _ = np.histogram2d(ys, xs, bins=[12, 12],
                                 range=[[wy0, wy1], [wx0, wx1]])
        i, j = np.unravel_index(h.argmax(), h.shape)
        cy = wy0 + (i + 0.5) * (wy1 - wy0) / 12
        cx = wx0 + (j + 0.5) * (wx1 - wx0) / 12
        print(f"  densest patch of them near x {cx:.0f}, y {cy:.0f}"
              f" ({int(h.max())} px) - widen a box to include it")

    vis = np.asarray(Image.open(scene / rec["plates"]["original"]).convert("RGB")).copy()
    vis[missed] = [255, 0, 0]
    out = scene / args.out
    Image.fromarray(vis).save(out)
    print("->", out)
    if pct > 3:
        print("\nthat is enough to see - fix the boxes before rendering")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
erase_plate.py <scene_dir> [--out original_erased.png]

Build the baseline a patch should measure against when the old text is being
destroyed rather than subtracted.

Removing lettering by subtracting a plate cancels it exactly on the reference
frame and nowhere else: a little too much comes off everywhere else and the old
words print back BRIGHTER than the paper, in the gaps between the new ones. A
per-frame blur does not have that problem, because it works off each frame's
own paper.

So the blur takes the old text out, and the patch is left with only the new
text to add - which means its baseline has to be the reference frame with the
same blur already applied. That is what this writes. Reads the erase rects
straight out of the recipe, so the two can never drift apart.

Use it with "erase_first": true in the recipe.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def box_blur(a, r, passes=3):
    out = a.astype(np.float32)
    for _ in range(passes):
        for ax in (0, 1):
            pad = [(0, 0), (0, 0), (0, 0)]
            pad[ax] = (r, r)
            q = np.pad(out, pad, mode="edge")
            c = np.cumsum(q, axis=ax)
            c = np.concatenate([np.zeros_like(np.take(c, [0], axis=ax)), c], axis=ax)
            out = (np.take(c, range(2 * r + 1, c.shape[ax]), axis=ax)
                   - np.take(c, range(0, c.shape[ax] - 2 * r - 1), axis=ax)) / (2 * r + 1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene_dir")
    ap.add_argument("--out", default="original_erased.png")
    args = ap.parse_args()

    scene = Path(args.scene_dir)
    rec = json.loads((scene / "recipe.json").read_text())
    src = rec["plates"].get("original_source", "original.png")
    A = np.asarray(Image.open(scene / src).convert("RGB")).astype(np.float32)
    H, W = A.shape[:2]
    xs = np.arange(W)[None, :]
    ys = np.arange(H)[:, None]

    out = A.copy()
    for er in rec.get("erase", []):
        x0, y0, x1, y1 = er["rect"]
        soft = float(er.get("ramp", 14))
        r = int(er.get("radius", 14))
        dx = np.maximum(np.maximum(x0 - xs, xs - x1), 0).astype(np.float32)
        dy = np.maximum(np.maximum(y0 - ys, ys - y1), 0).astype(np.float32)
        t = np.clip(np.sqrt(dx ** 2 + dy ** 2) / soft, 0, 1)
        m = (1.0 - (3 * t ** 2 - 2 * t ** 3))[..., None]
        yb0, yb1 = max(y0 - r - 2, 0), min(y1 + r + 2, H)
        xb0, xb1 = max(x0 - r - 2, 0), min(x1 + r + 2, W)
        sub = out.copy()
        sub[yb0:yb1, xb0:xb1] = box_blur(out[yb0:yb1, xb0:xb1], r)
        out = out * (1 - m) + sub * m
        print(f"  erased {er['rect']}  radius {r}  ramp {soft:g}")

    d = np.abs(out - A).mean(axis=2)
    if (d > 3).any():
        ys_, xs_ = np.nonzero(d > 3)
        print(f"changed x {xs_.min()}-{xs_.max()}  y {ys_.min()}-{ys_.max()}")
    Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(scene / args.out)
    print("->", scene / args.out)


if __name__ == "__main__":
    main()

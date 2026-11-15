#!/usr/bin/env python3
"""
prepare_plate.py <scene_dir> --raw FILE --surface X0 Y0 X1 Y1
                 [--edit X0 Y0 X1 Y1]... [--erase X0 Y0 X1 Y1 DY]...

Turns a raw image-model plate into one that can be used as a patch.

Two things always have to happen first.

TONE. The model re-renders the whole photograph rather than editing it, and its
version of the paper comes out several levels off. Dropped in as a patch that
reads as a dull or bright rectangle around the new words however soft the edge
is. The fix is a per-channel linear fit solved on the parts of the surface that
were NOT supposed to change - pass those in as --surface minus the --edit
regions - which spans ink-dark to paper-bright rather than only the narrow
range of the paper itself.

REGISTRATION. The plate is only usable if everything the model was told to
leave alone lands back where it was. This reports the shift on a few unchanged
features so that can be judged before anything is rendered, rather than after.

--erase additionally wipes a region by copying surface from DY pixels away,
feathered. Used for small printed marks that should simply disappear, where
asking the model to remove them would cost another round and another
regeneration of everything else.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def smoothstep_mask(shape, rect, ramp):
    H, W = shape
    xs = np.arange(W)[None, :]
    ys = np.arange(H)[:, None]
    dx = np.maximum(np.maximum(rect[0] - xs, xs - rect[2]), 0).astype(np.float32)
    dy = np.maximum(np.maximum(rect[1] - ys, ys - rect[3]), 0).astype(np.float32)
    t = np.clip(np.sqrt(dx ** 2 + dy ** 2) / ramp, 0, 1)
    return 1.0 - (3 * t ** 2 - 2 * t ** 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene_dir")
    ap.add_argument("--raw", required=True)
    ap.add_argument("--surface", type=int, nargs=4, required=True,
                    metavar=("X0", "Y0", "X1", "Y1"))
    ap.add_argument("--edit", type=int, nargs=4, action="append", default=[],
                    metavar=("X0", "Y0", "X1", "Y1"),
                    help="a region the model was asked to change (excluded from the fit)")
    ap.add_argument("--erase", type=int, nargs=5, action="append", default=[],
                    metavar=("X0", "Y0", "X1", "Y1", "DY"),
                    help="wipe this rect using surface copied DY pixels away")
    ap.add_argument("--check", type=int, nargs=4, action="append", default=[],
                    metavar=("X0", "Y0", "X1", "Y1"),
                    help="an unchanged feature, to report registration on")
    args = ap.parse_args()

    scene = Path(args.scene_dir)
    A = np.asarray(Image.open(scene / "original.png").convert("RGB")).astype(np.float64)
    H, W = A.shape[:2]
    G = Image.open(args.raw).convert("RGB")
    if G.size != (W, H):
        print(f"plate is {G.size}, resizing to {(W, H)}")
        G = G.resize((W, H), Image.LANCZOS)
    G.save(scene / "gemini_resized.png")
    G = np.asarray(G).astype(np.float64)

    fit = np.zeros((H, W), bool)
    sx0, sy0, sx1, sy1 = args.surface
    fit[sy0:sy1, sx0:sx1] = True
    for x0, y0, x1, y1 in args.edit:
        fit[y0:y1, x0:x1] = False
    print(f"tone fit on {int(fit.sum())} px of unchanged surface")

    M = G.copy()
    coeffs = {}
    for c, name in enumerate("RGB"):
        a, b = np.polyfit(G[..., c][fit], A[..., c][fit], 1)
        coeffs[name] = {"gain": round(float(a), 4), "offset": round(float(b), 2)}
        print(f"  {name} gain {a:.4f} offset {b:+7.2f}")
        M[..., c] = a * G[..., c] + b
    M = np.clip(M, 0, 255)

    paper = (A.mean(axis=2) > 140) & (M.mean(axis=2) > 140) & fit
    if paper.any():
        print("  surface delta after matching:",
              (M[paper] - A[paper]).mean(axis=0).round(2))

    for x0, y0, x1, y1, dy in args.erase:
        m = smoothstep_mask((H, W), (x0, y0, x1, y1), 14)[..., None]
        src = np.roll(M, -dy, axis=0)
        M = M * (1 - m) + src * m
        print(f"  erased {[x0, y0, x1, y1]} using surface from {dy:+d} px away")

    Image.fromarray(M.astype(np.uint8)).save(scene / "edited_matched.png")

    if args.check:
        print("\nregistration on features that should not have moved:")
        ga = np.asarray(Image.open(scene / "original.png").convert("L")).astype(np.float32)
        gm = M.mean(axis=2).astype(np.float32)

        def grad(a):
            a = a.astype(np.float32)
            gx = np.zeros_like(a); gy = np.zeros_like(a)
            gx[:, 1:-1] = a[:, 2:] - a[:, :-2]
            gy[1:-1, :] = a[2:, :] - a[:-2, :]
            return np.sqrt(gx * gx + gy * gy)

        GA, GM = grad(ga), grad(gm)

        def shift_of(b, search=8):
            """the offset that best lines the plate up with the original here.

            Centroids do not answer this: weighted by darkness they track the
            background of a bright feature, and weighted by edge strength over a
            box with several structures in it they track whichever edge is
            strongest. Correlation over the whole box does."""
            x0, y0, x1, y1 = b
            x0, y0 = max(x0, search), max(y0, search)
            x1, y1 = min(x1, W - search), min(y1, H - search)
            ref = GA[y0:y1, x0:x1]
            ref = ref - ref.mean()
            best = None
            for dy in range(-search, search + 1):
                for dx in range(-search, search + 1):
                    cmp_ = GM[y0 + dy:y1 + dy, x0 + dx:x1 + dx]
                    cmp_ = cmp_ - cmp_.mean()
                    den = np.sqrt((ref * ref).sum() * (cmp_ * cmp_).sum())
                    c = float((ref * cmp_).sum() / den) if den > 1e-9 else -1
                    if best is None or c > best[0]:
                        best = (c, dx, dy)
            return best

        worst = 0.0
        for b in args.check:
            c, dx, dy = shift_of(b)
            d = float(np.hypot(dx, dy))
            worst = max(worst, d)
            print(f"   {str(b):28s} {dx:+3d},{dy:+3d}  = {d:.1f} px   (corr {c:.2f})")
        print(f"   worst {worst:.1f} px", end="")
        print("  - fine" if worst < 2.5 else "  - too much to patch over safely")

    print("\n->", scene / "edited_matched.png")
    print(json.dumps({"tone_match": {"coefficients": coeffs}}, indent=2))


if __name__ == "__main__":
    main()

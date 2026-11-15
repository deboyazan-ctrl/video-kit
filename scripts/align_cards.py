#!/usr/bin/env python3
"""
align_cards.py <from_scene> <to_scene> --region X0 Y0 X1 Y1
               [--seed a b c d e f] [--out edited_matched.png]

Carry an edit from one shot to another shot of the same flat object.

The same card appears twice in this video: lying on the table in scene 3, and
propped upright and cut off by the frame in scene 7. A plate was already made
and accepted for scene 3, and asking an image model for a second one produced
a card it had re-imagined rather than edited - a deckled edge invented down one
side, a tone fit with negative gain, 10 px of registration error.

Because the card is flat, one homography relates the two views exactly. This
solves for it directly on the picture, then carries scene 3's CHANGE across:

    target_edited = target_original + warp(source_edited - source_original)

Only the difference travels, so the target keeps its own paper, its own
lighting and its own grain, and only the beadwork and the date come over.

Scored on image gradient, which survives the two shots being lit differently,
and sampled bilinearly - with nearest-neighbour the score is flat between whole
pixels and the sub-pixel half of the search silently does nothing.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def homography(src, dst):
    A = np.zeros((8, 8)); b = np.zeros(8)
    for i in range(4):
        x, y = src[i]; u, v = dst[i]
        A[i * 2] = [x, y, 1, 0, 0, 0, -u * x, -u * y]; b[i * 2] = u
        A[i * 2 + 1] = [0, 0, 0, x, y, 1, -v * x, -v * y]; b[i * 2 + 1] = v
    h = np.linalg.solve(A, b)
    return np.array([[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], 1.0]])


def sample(img, xs, ys):
    """bilinear, with out-of-frame reads returned as NaN"""
    H, W = img.shape[:2]
    x0 = np.floor(xs).astype(int); y0 = np.floor(ys).astype(int)
    ok = (x0 >= 0) & (y0 >= 0) & (x0 < W - 1) & (y0 < H - 1)
    x0c = np.clip(x0, 0, W - 2); y0c = np.clip(y0, 0, H - 2)
    fx = (xs - x0c)[..., None] if img.ndim == 3 else (xs - x0c)
    fy = (ys - y0c)[..., None] if img.ndim == 3 else (ys - y0c)
    a = img[y0c, x0c]; b = img[y0c, x0c + 1]
    c = img[y0c + 1, x0c]; d = img[y0c + 1, x0c + 1]
    out = (a * (1 - fx) * (1 - fy) + b * fx * (1 - fy)
           + c * (1 - fx) * fy + d * fx * fy)
    out = np.where(ok[..., None] if img.ndim == 3 else ok, out, np.nan)
    return out


def grad(a):
    gx = np.zeros_like(a); gy = np.zeros_like(a)
    gx[:, 1:-1] = a[:, 2:] - a[:, :-2]
    gy[1:-1, :] = a[2:, :] - a[:-2, :]
    return np.sqrt(gx * gx + gy * gy)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("from_scene"); ap.add_argument("to_scene")
    ap.add_argument("--region", type=int, nargs=4, required=True,
                    metavar=("X0", "Y0", "X1", "Y1"),
                    help="the area in the TARGET frame the object covers")
    ap.add_argument("--seed-homography",
                    help="an align.json from an earlier, wider solve. Narrowing the "
                         "region to the part of the object that is genuinely flat and "
                         "genuinely the same in both shots - here the paper and its "
                         "beadwork, not the fabric or the raised wax seal - is what "
                         "turns a near miss into a fit, and it needs the wide solve "
                         "as its starting point.")
    ap.add_argument("--seed", type=float, nargs=6,
                    metavar=("A", "B", "C", "D", "E", "F"),
                    help="affine from source to target: x'=Ax+By+C, y'=Dx+Ey+F")
    ap.add_argument("--replace", action="store_true",
                    help="carry the source's EDITED picture into the region instead of "
                         "carrying the difference. Differencing cancels the old lettering "
                         "by subtracting it, so it needs the two views aligned to the "
                         "pixel or the old and new both show. A replacement buries the old "
                         "lettering under the new surface, so it only needs the geometry "
                         "to look right - which is reachable here where pixel alignment "
                         "was not. The carried surface is tone-fitted to the target's own "
                         "paper first, on the pixels where neither picture has lettering.")
    ap.add_argument("--only-edited", type=float, metavar="THRESHOLD",
                    help="with --replace, carry only the pixels the SOURCE plate "
                         "actually changed, warped into place. The region passed to "
                         "--region is a rectangle, but the thing being carried rarely "
                         "is: the framed photograph sits in an arched opening, and a "
                         "rectangle painted the wooden surround as well and squared off "
                         "the arch. The source's own edit is the right shape by "
                         "construction.")
    ap.add_argument("--tone", choices=["moments", "mean"], default="moments",
                    help="moments matches mean and spread, and is right when the two "
                         "shots show the SAME surface - the card. mean matches "
                         "brightness only and leaves contrast alone, which is what a "
                         "replaced picture needs: forcing a colour photograph of faces "
                         "to the contrast statistics of the black and white photograph "
                         "it replaces is matching two different images, and it came out "
                         "at gain 0.39 - a pale ghost of the picture being carried.")
    ap.add_argument("--tone-from", type=int, nargs=8,
                    metavar=("SX0", "SY0", "SX1", "SY1", "TX0", "TY0", "TX1", "TY1"),
                    help="two boxes on the SAME unchanged material, one in each shot - "
                         "the wooden frame here. The per-channel gain between their "
                         "means is the exposure difference between the two shots, which "
                         "is the only thing that should be corrected when the picture "
                         "itself is being replaced. Matching the carried picture's own "
                         "statistics to the picture it replaces compares two different "
                         "images: colour faces against a black and white photograph of "
                         "hands, which came out either flattened to 39% contrast or "
                         "pulled 35 levels of red out of the skin.")
    ap.add_argument("--trim-bottom", type=int, default=0, metavar="N",
                    help="drop N rows from the bottom of the carried area before "
                         "extending it. What ends the source's edit is usually the "
                         "thing that stood in front of it, and the last rows carry that "
                         "thing's own edge - scene 5's white card printed a torn white "
                         "line across the picture here.")
    ap.add_argument("--extend-down", type=float, metavar="SATURATION",
                    help="fill below the carried area by repeating its last row, "
                         "stopping where the target stops being grey. The source's "
                         "edit ends where something stood in front of it in the source "
                         "shot - the white card across the bottom of the framed "
                         "photograph in scene 5 - and that torn boundary carries over "
                         "into a shot where nothing stands there, leaving a strip of "
                         "the old picture below it. The saturation test stops the fill "
                         "at the wooden surround, which must not be painted over.")
    ap.add_argument("--no-refine", action="store_true",
                    help="take the seed as the answer. On the card the gradient score "
                         "is not measuring the lettering at all - it is dominated by "
                         "paper texture and lighting, which are not the same in the two "
                         "shots, and it preferred a fit that stretched the printed date "
                         "by 18%% over one that put its ends within a pixel. Where "
                         "landmarks can be measured, they are the better evidence.")
    ap.add_argument("--out", default="edited_matched.png")
    args = ap.parse_args()

    src_dir, dst_dir = Path(args.from_scene), Path(args.to_scene)
    A3 = np.asarray(Image.open(src_dir / "original.png").convert("RGB")).astype(np.float32)
    P3 = np.asarray(Image.open(src_dir / "edited_matched.png").convert("RGB")).astype(np.float32)
    A7 = np.asarray(Image.open(dst_dir / "original.png").convert("RGB")).astype(np.float32)
    H_, W_ = A7.shape[:2]

    x0, y0, x1, y1 = args.region
    ys, xs = np.mgrid[y0:y1, x0:x1]
    xs = xs.astype(np.float64); ys = ys.astype(np.float64)

    g3 = grad(A3.mean(axis=2))
    tgt = grad(A7.mean(axis=2))[y0:y1, x0:x1]
    tgt_c = tgt - tgt.mean()
    tgt_n = np.sqrt((tgt_c * tgt_c).sum())

    box = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], float)
    if args.seed_homography:
        Minv = np.array(json.loads(Path(args.seed_homography).read_text())
                        ["homography_target_to_source"])
    else:
        a, b, c, d, e, f = args.seed
        Minv = np.linalg.inv(np.array([[a, b, c], [d, e, f], [0, 0, 1]]))
    p = Minv @ np.vstack([box.T, np.ones(4)])
    corners = (p[:2] / p[2]).T.copy()          # the box, back in source coordinates
    print("seed corners in the source frame:")
    print("  " + "  ".join(f"({u:.0f},{v:.0f})" for u, v in corners))

    def score(cor):
        Hm = homography(box, cor)              # target pixel -> source pixel
        q = Hm @ np.vstack([xs.ravel(), ys.ravel(), np.ones(xs.size)])
        u = (q[0] / q[2]).reshape(xs.shape); v = (q[1] / q[2]).reshape(xs.shape)
        s = sample(g3, u, v)
        m = np.isfinite(s)
        if m.sum() < 0.5 * s.size:
            return -1.0
        s = np.where(m, s, 0.0)
        sc = s - s[m].mean()
        den = np.sqrt((sc[m] * sc[m]).sum()) * tgt_n
        return float((sc[m] * tgt_c[m]).sum() / den) if den > 1e-9 else -1.0

    best = score(corners)
    print(f"seed score {best:.4f}")
    for step in ([] if args.no_refine else (16, 8, 4, 2, 1, 0.5, 0.25, 0.125)):
        improved = True
        while improved:
            improved = False
            for i in range(4):
                for j in (0, 1):
                    for s in (step, -step):
                        trial = corners.copy(); trial[i, j] += s
                        v = score(trial)
                        if v > best:
                            best, corners, improved = v, trial, True
        print(f"  step {step:<6} score {best:.4f}")

    Hm = homography(box, corners)
    print("\nsolved corners in the source frame:")
    print("  " + "  ".join(f"({u:.1f},{v:.1f})" for u, v in corners))
    print(f"final gradient correlation {best:.4f}")

    q = Hm @ np.vstack([xs.ravel(), ys.ravel(), np.ones(xs.size)])
    u = (q[0] / q[2]).reshape(xs.shape); v = (q[1] / q[2]).reshape(xs.shape)
    tgt_rgb = A7[y0:y1, x0:x1]
    out = A7.copy()

    if args.replace:
        w = sample(P3, u, v)
        ok = np.isfinite(w).all(axis=2)
        print(f"{100 * ok.mean():.1f}% of the region has source pixels behind it")
        if ok.mean() < 0.999:
            print("  the rest keeps the target untouched")
        w = np.where(np.isfinite(w), w, tgt_rgb)

        if args.only_edited is not None:
            em = (np.abs(P3 - A3).mean(axis=2) > args.only_edited).astype(np.float32)
            carried = sample(em[..., None], u, v)[..., 0]
            carried = np.where(np.isfinite(carried), carried, 0.0)
            em2 = carried > 0.5
            # Close the mask. Thresholding a difference leaves holes wherever the
            # new picture happened to match the old one's brightness - along the
            # hair here - and those holes print the old photograph back through
            # the new one.
            r = 10
            d_ = em2.copy()
            for _ in range(r):
                q = d_.copy()
                q[1:, :] |= d_[:-1, :]; q[:-1, :] |= d_[1:, :]
                q[:, 1:] |= d_[:, :-1]; q[:, :-1] |= d_[:, 1:]
                d_ = q
            for _ in range(r):
                q = d_.copy()
                q[1:, :] &= d_[:-1, :]; q[:-1, :] &= d_[1:, :]
                q[:, 1:] &= d_[:, :-1]; q[:, :-1] &= d_[:, 1:]
                d_ = q
            ok &= d_
            print(f"carrying {int((carried > 0.5).sum())} px "
                  f"({100 * (carried > 0.5).mean():.1f}% of the region)")

        # Tone-fit the carried surface to the target's own paper, on the pixels
        # where neither picture carries lettering - otherwise the beads, which
        # are the brightest thing in both, drag the fit.
        wl = w.mean(axis=2); tl = tgt_rgb.mean(axis=2)
        sel = wl[ok] if ok.any() else wl.ravel()
        tsel = tl[ok] if ok.any() else tl.ravel()
        plain = ((wl > np.percentile(sel, 25)) & (wl < np.percentile(sel, 88))
                 & (tl > np.percentile(tsel, 25)) & (tl < np.percentile(tsel, 88)) & ok)
        # Match mean and spread, not a least-squares line. A regression
        # between two pictures assumes they are aligned pixel for pixel, and
        # these are two different views that only roughly correspond - run on
        # this paper, whose tonal range is narrow, it returned gains near zero,
        # which maps every pixel to the mean and erases the beadwork it was
        # supposed to carry.
        # One gain for all three channels, offsets per channel. Fitting a gain
        # per channel looks more careful and is worse: under this warm light the
        # target's blue barely varies (std 3.3 against the carried 8.3), so blue
        # alone gets scaled by 0.39 - which flattens the beadwork's own blue and
        # changes the colour of the thing being carried. The gain is taken from
        # red and green, which carry the real texture.
        if args.trim_bottom:
            for col in range(ok.shape[1]):
                rows = np.nonzero(ok[:, col])[0]
                if len(rows):
                    ok[max(rows.max() - args.trim_bottom + 1, 0):rows.max() + 1, col] = False

        if args.extend_down is not None:
            mx = tgt_rgb.max(axis=2); mn = tgt_rgb.min(axis=2)
            grey = ((mx - mn) / np.maximum(mx, 1.0)) < args.extend_down
            added = 0
            ext = np.zeros(ok.shape, bool)
            for col in range(ok.shape[1]):
                rows = np.nonzero(ok[:, col])[0]
                if not len(rows):
                    continue
                last = rows.max()
                r_ = last + 1
                while r_ < ok.shape[0] and grey[r_, col]:
                    w[r_, col] = w[last, col]
                    ok[r_, col] = True
                    ext[r_, col] = True
                    added += 1
                    r_ += 1
            print(f"extended {added} px below the carried area")
            if added:
                # Repeating a row leaves vertical streaks: every column holds its
                # own last pixel and nothing varies down the band, so it reads as
                # a glitch rather than as more picture. A wide horizontal blur
                # removes exactly that variation.
                r_ = 20
                q = np.pad(w, ((0, 0), (r_, r_), (0, 0)), mode="edge")
                c_ = np.cumsum(q, axis=1)
                c_ = np.concatenate([np.zeros_like(c_[:, :1]), c_], axis=1)
                sm = (c_[:, 2 * r_ + 1:] - c_[:, :-(2 * r_ + 1)]) / (2 * r_ + 1)
                w[ext] = sm[ext]

        if args.tone_from:
            sx0, sy0, sx1, sy1, tx0, ty0, tx1, ty1 = args.tone_from
            def warm_mean(img, b):
                q = img[b[1]:b[3], b[0]:b[2]].reshape(-1, 3)
                sel = (q.max(axis=1) - q.min(axis=1)) > 25
                return (q[sel] if sel.sum() > 200 else q).mean(axis=0)
            ms = warm_mean(A3, (sx0, sy0, sx1, sy1))
            mt = warm_mean(A7, (tx0, ty0, tx1, ty1))
            print(f"exposure from the reference material: {(mt / ms).round(4)}")
            for c in range(3):
                w[..., c] *= float(mt[c] / ms[c])
            print("  surface delta after matching:",
                  (w - tgt_rgb)[plain].mean(axis=0).round(2))
            out[y0:y1, x0:x1] = np.clip(
                tgt_rgb * (1 - ok[..., None]) + w * ok[..., None], 0, 255)
            Image.fromarray(out.astype(np.uint8)).save(dst_dir / args.out)
            print("->", dst_dir / args.out)
            return
        if args.tone == "mean":
            g = 1.0
        else:
            gains = []
            for c in (0, 1):
                gains.append(float(tgt_rgb[..., c][plain].std()
                                   / max(w[..., c][plain].std(), 1e-6)))
            g = float(np.mean(gains))
        print(f"tone fit on {int(plain.sum())} px of plain surface; shared gain {g:.4f}")
        for c, name in enumerate("RGB"):
            o = float(tgt_rgb[..., c][plain].mean() - g * w[..., c][plain].mean())
            print(f"  {name} offset {o:+7.2f}")
            w[..., c] = g * w[..., c] + o
        print("  surface delta after matching:",
              (w - tgt_rgb)[plain].mean(axis=0).round(2))
        m = ok[..., None].astype(np.float32)
        out[y0:y1, x0:x1] = np.clip(tgt_rgb * (1 - m) + w * m, 0, 255)
    else:
        d3 = sample(P3 - A3, u, v)
        d3 = np.where(np.isfinite(d3), d3, 0.0)
        out[y0:y1, x0:x1] = np.clip(tgt_rgb + d3, 0, 255)
    Image.fromarray(out.astype(np.uint8)).save(dst_dir / args.out)
    print("->", dst_dir / args.out)
    (dst_dir / "align.json").write_text(json.dumps(
        {"from": str(src_dir), "region": args.region,
         "corners_in_source": corners.tolist(),
         "homography_target_to_source": Hm.tolist(),
         "gradient_correlation": round(best, 4)}, indent=1))


if __name__ == "__main__":
    main()

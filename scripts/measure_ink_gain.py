#!/usr/bin/env python3
"""
measure_ink_gain.py <scene_dir> --box X0 Y0 X1 Y1 --hold-from N [--out ink_gain.json]

How strong the printed ink actually is on each frame, relative to the reference.

The replacement plate is built from one frame and therefore carries that frame's
ink contrast. Wherever the card is smaller, softer or more motion-blurred, its
real ink is weaker - and subtracting a full-strength version of it takes away
more than is there, so the old strokes come back as *bright* ghosts. Measured at
-61% over-removal on the first frame of scene 1 before this correction.

The measurement is taken on a printed element the edit did not touch, so it
reflects the frame and not the edit.

Past the point where the shot starts dissolving, the contrast drop is the
dissolve itself, which the blend curve already applies. Measuring through it
would count the fade twice, so the gain is held at its last clean value.
"""
import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image


def probe(path, entries, stream=None):
    cmd = ["ffprobe", "-v", "error"]
    if stream:
        cmd += ["-select_streams", stream]
    cmd += ["-show_entries", entries, "-of", "default=nw=1:nk=1", str(path)]
    return [l for l in subprocess.run(cmd, capture_output=True, text=True,
                                      check=True).stdout.strip().splitlines() if l]


def homography(s, d):
    A = np.zeros((8, 8)); b = np.zeros(8)
    for i in range(4):
        x, y = s[i]; u, v = d[i]
        A[i * 2] = [x, y, 1, 0, 0, 0, -u * x, -u * y]; b[i * 2] = u
        A[i * 2 + 1] = [0, 0, 0, x, y, 1, -v * x, -v * y]; b[i * 2 + 1] = v
    h = np.linalg.solve(A, b)
    return np.array([[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], 1.0]])


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
    ap.add_argument("--box", type=int, nargs=4, required=True,
                    metavar=("X0", "Y0", "X1", "Y1"),
                    help="a printed element the edit does NOT change")
    ap.add_argument("--hold-from", type=int, required=True,
                    help="first frame of the outgoing dissolve; gain is held from here")
    ap.add_argument("--hold-until", type=int,
                    help="last frame of the incoming dissolve; gain is held before here too")
    ap.add_argument("--bright", action="store_true",
                    help="the reference marks are brighter than the surface, not darker")
    ap.add_argument("--smooth", type=int, default=0,
                    help="average the curve over +-N frames")
    ap.add_argument("--out", default="ink_gain.json")
    args = ap.parse_args()

    scene = Path(args.scene_dir)
    rec = json.loads((scene / "recipe.json").read_text())
    video = Path(rec["source"])
    W, H = (int(x) for x in probe(video, "stream=width,height", "v:0")[:2])

    track = {int(k): np.array(v["corners"])
             for k, v in json.loads((scene / rec["track"]["file"]).read_text()).items()}
    sx0, sy0, sx1, sy1 = rec["track"]["solve_region"]
    SOLVE = np.array([[sx0, sy0], [sx1, sy0], [sx1, sy1], [sx0, sy1]], float)

    A = np.asarray(Image.open(scene / rec["plates"]["original"]).convert("L")).astype(np.float32)
    x0, y0, x1, y1 = args.box
    sub = A[y0:y1, x0:x1]
    if args.bright:
        thr = np.percentile(sub, 75) + 10
        mark = sub > thr
    else:
        thr = sub.min() + 0.45 * (np.median(sub) - sub.min())
        mark = sub < thr
    ink = np.zeros(A.shape, bool)
    ink[y0:y1, x0:x1] = mark
    paper = dilate(ink, 10) & ~dilate(ink, 4)
    print(f"reference element: {int(ink.sum())} ink px, {int(paper.sum())} paper px")

    iy, ix = np.nonzero(ink)
    py, px = np.nonzero(paper)
    lo, hi = min(track), max(track)
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(video),
         "-vf", f"select='between(n\\,{lo}\\,{hi})',format=gray",
         "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True).stdout
    n_px = W * H

    measured = {}
    for n in sorted(track):
        idx = n - lo
        if (idx + 1) * n_px > len(raw):
            continue
        g = np.frombuffer(raw[idx * n_px:(idx + 1) * n_px], np.uint8).reshape(H, W).astype(np.float32)
        Hi = homography(SOLVE, track[n])

        def warp(xs, ys):
            p = Hi @ np.stack([xs.astype(float), ys.astype(float), np.ones(len(xs))])
            return p[0] / p[2], p[1] / p[2]

        u1, v1 = warp(ix, iy)
        u2, v2 = warp(px, py)
        if (min(u1.min(), u2.min()) < 0 or min(v1.min(), v2.min()) < 0
                or max(u1.max(), u2.max()) > W - 1 or max(v1.max(), v2.max()) > H - 1):
            continue
        a = g[v1.astype(int), u1.astype(int)].mean()
        b = g[v2.astype(int), u2.astype(int)].mean()
        measured[n] = float(a - b) if args.bright else float(b - a)

    ref = rec["reference_frame"]["frame"] if isinstance(rec["reference_frame"], dict) \
        else rec["reference_frame"]
    base = measured[ref]
    print(f"contrast at the reference frame ({ref}): {base:.1f}")

    # Sanity check with teeth. At the reference frame the warp is the identity,
    # so re-measuring the same pixels through it must return the same contrast.
    # If a neighbouring frame reads far from 1.0 while the surface has barely
    # moved, the measurement is picking up the softening the warp introduces
    # rather than the ink - which once produced a confident 0.80 that made the
    # removal worse when it was applied.
    near = [n for n in measured if abs(n - ref) <= 3 and n != ref]
    if near:
        drift = max(abs(measured[n] / base - 1.0) for n in near)
        print(f"  within 3 frames of the reference the reading moves {drift:.1%}")
        if drift > 0.06:
            print("  that is too much for three frames - this measurement is not"
                  " reading the ink. Check the box is on a mark that the warp"
                  " lands on accurately, or leave the gain off.")

    hold_hi = args.hold_from
    hold_lo = args.hold_until
    gain = {}
    for n in sorted(measured):
        m = n
        if n > hold_hi:
            m = hold_hi
        if hold_lo is not None and n < hold_lo:
            m = hold_lo
        gain[n] = round(measured.get(m, base) / base, 4)

    # The reading wobbles a few percent frame to frame, and a wobbling gain
    # makes the removal breathe. Smooth it - what it is correcting for changes
    # slowly, so nothing real is lost.
    if args.smooth:
        ks = sorted(gain)
        r = int(args.smooth)
        sm = {}
        for i, n in enumerate(ks):
            lo, hi = max(0, i - r), min(len(ks), i + r + 1)
            sm[n] = round(float(np.mean([gain[k] for k in ks[lo:hi]])), 4)
        gain = sm

    out = scene / args.out
    out.write_text(json.dumps({str(k): v for k, v in gain.items()}))
    shown = [n for n in sorted(gain) if n % 10 == 0]
    print("gain: " + "  ".join(f"{n}:{gain[n]:.2f}" for n in shown))
    print("->", out)


if __name__ == "__main__":
    main()

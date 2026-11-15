#!/usr/bin/env python3
"""
solve_track.py <video> --ref N --range FIRST LAST --region X0 Y0 X1 Y1 --out track.json

Solves a flat surface's motion straight from the pixels, for cases where a
planar tracker is not accurate enough.

The plate we composite is a difference image: it brightens exactly where the old
ink was and darkens exactly where the new ink goes. Those two jobs cancel
against the real frame only if the transform is right to well under a pixel -
a single pixel of slip and the "remove the old ink" values, which reach the best
part of half the tonal range, land on bare paper and burn out to white. Planar
trackers on this footage give 3-9 px, and 22 px where they slip.

Per frame the unknown is where the four corners of a reference-frame rectangle
land - eight numbers, a full homography. The score is normalised correlation
between the reference's gradient image and the frame sampled through the
candidate. Gradients, so exposure and dissolves do not bias it; and the sampling
is bilinear, because with nearest-neighbour the score is flat between whole
pixels and the sub-pixel half of the search silently does nothing.

Search is a shrinking pattern search on the four corners, seeded from the
previous frame and walking outward from the reference in both directions.
"""
import argparse
import json
import subprocess
from pathlib import Path

import numpy as np


def probe(path, entries, stream=None):
    cmd = ["ffprobe", "-v", "error"]
    if stream:
        cmd += ["-select_streams", stream]
    cmd += ["-show_entries", entries, "-of", "default=nw=1:nk=1", str(path)]
    return [l for l in subprocess.run(cmd, capture_output=True, text=True,
                                      check=True).stdout.strip().splitlines() if l]


def decode_gray(video, first, last, W, H):
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(video),
         "-vf", f"select='between(n\\,{first}\\,{last})',format=gray",
         "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True).stdout
    n = W * H
    return {first + i: np.frombuffer(raw[i * n:(i + 1) * n], np.uint8).reshape(H, W)
            for i in range(len(raw) // n)}


def grad(a):
    a = a.astype(np.float32)
    gx = np.zeros_like(a); gy = np.zeros_like(a)
    gx[:, 1:-1] = a[:, 2:] - a[:, :-2]
    gy[1:-1, :] = a[2:, :] - a[:-2, :]
    return np.sqrt(gx * gx + gy * gy)


def homography(src, dst):
    A = np.zeros((8, 8)); b = np.zeros(8)
    for i in range(4):
        x, y = src[i]; u, v = dst[i]
        A[i * 2] = [x, y, 1, 0, 0, 0, -u * x, -u * y]; b[i * 2] = u
        A[i * 2 + 1] = [0, 0, 0, x, y, 1, -v * x, -v * y]; b[i * 2 + 1] = v
    h = np.linalg.solve(A, b)
    return np.array([[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], 1.0]])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--ref", type=int, required=True)
    ap.add_argument("--range", type=int, nargs=2, required=True, metavar=("FIRST", "LAST"))
    ap.add_argument("--region", type=int, nargs=4, required=True,
                    metavar=("X0", "Y0", "X1", "Y1"))
    ap.add_argument("--stride", type=int, default=2,
                    help="sample every Nth pixel of the region when scoring")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    v = Path(args.video)
    W, H = (int(x) for x in probe(v, "stream=width,height", "v:0")[:2])
    first, last = args.range
    x0, y0, x1, y1 = args.region

    frames = decode_gray(v, first, last, W, H)
    print(f"decoded {len(frames)} frames, {first}-{last}")

    gy_, gx_ = np.mgrid[y0:y1:args.stride, x0:x1:args.stride]
    GRID = np.stack([gx_.ravel().astype(np.float64),
                     gy_.ravel().astype(np.float64),
                     np.ones(gx_.size)], axis=0)
    ref_g = grad(frames[args.ref])
    REF = ref_g[y0:y1:args.stride, x0:x1:args.stride].ravel()

    CORNERS = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], float)
    print(f"scoring on {REF.size} samples (stride {args.stride})")

    def score(fg, corners):
        try:
            Hi = homography(CORNERS, corners)
        except np.linalg.LinAlgError:
            return -1.0
        p = Hi @ GRID
        w = p[2]
        if np.abs(w).min() < 1e-6:
            return -1.0
        u, v_ = p[0] / w, p[1] / w
        # Score on the samples that land inside the frame, instead of throwing
        # the candidate away when any of them do not. Where the surface runs off
        # the edge of the picture - the card in scene 7 continues past the left
        # edge - rejection pins the corners on that side at zero and the solver
        # answers by shearing the quad instead of moving it: on frames 1275-1284
        # the printed date came out 7-9 px wrong at its left end while its right
        # end stayed within 1.3 px.
        inside = ((u >= 0) & (v_ >= 0) & (u <= W - 2) & (v_ <= H - 2))
        if inside.mean() < 0.6:
            return -1.0
        u = np.clip(u, 0, W - 2); v_ = np.clip(v_, 0, H - 2)
        u0 = u.astype(np.int32); v0 = v_.astype(np.int32)
        fu = u - u0; fv = v_ - v0
        s = (fg[v0, u0] * (1 - fu) * (1 - fv) + fg[v0, u0 + 1] * fu * (1 - fv)
             + fg[v0 + 1, u0] * (1 - fu) * fv + fg[v0 + 1, u0 + 1] * fu * fv)
        # both sides of the correlation have to be restricted to the same
        # samples, or the score compares different parts of the surface
        r = REF[inside]; s = s[inside]
        r = r - r.mean(); s = s - s.mean()
        d = np.sqrt((s * s).sum()) * np.sqrt((r * r).sum())
        return float((r * s).sum() / d) if d > 1e-6 else -1.0

    def refine(fg, corners):
        best = score(fg, corners)
        cur = corners.copy()
        for st in (8, 4, 2, 1, 0.5, 0.25, 0.125):
            moved = True
            while moved:
                moved = False
                for i in range(4):
                    for ax in (0, 1):
                        for sg in (1, -1):
                            t = cur.copy(); t[i, ax] += sg * st
                            sc = score(fg, t)
                            if sc > best + 1e-6:
                                best, cur, moved = sc, t, True
        return cur, best

    solved = {args.ref: (CORNERS.copy(), 1.0)}
    for direction in (1, -1):
        cur = CORNERS.copy()
        n = args.ref
        while True:
            n += direction
            if n < first or n > last or n not in frames:
                break
            c, s = refine(grad(frames[n]), cur)
            solved[n] = (c, s)
            cur = c
            if n % 10 == 0:
                print(f"  frame {n}  corr {s:.4f}", flush=True)

    out = {str(n): {"corners": solved[n][0].tolist(), "corr": round(solved[n][1], 4)}
           for n in sorted(solved)}
    Path(args.out).write_text(json.dumps(out))
    cs = np.array([solved[n][1] for n in solved])
    print(f"\nsolved {len(solved)} frames | corr min {cs.min():.3f} median {np.median(cs):.3f}")
    weak = sorted(n for n in solved if solved[n][1] < 0.6)
    if weak:
        print(f"weak (surface too faint to lock onto): {weak}")
    print("->", args.out)


if __name__ == "__main__":
    main()

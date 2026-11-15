#!/usr/bin/env python3
"""
analyse_shot.py <video> <clean_start> <clean_end> [--pad 30]

Measures the two things every scene in this project needs before any
compositing can be planned:

  1. the cross-dissolves at each end of the shot, and the exact mix ratio on
     every frame of them. None of the shot changes here are hard cuts, so
     ffmpeg scene detection finds nothing and the boundaries have to be
     measured. The ratio is read off small patches where the outgoing and
     incoming shots differ strongly in brightness and each is steady on its own
     side; in such a patch the pixel value walks linearly from one to the other.
     Reading it off the card's own ink does not work - the incoming shot brings
     its own dark lettering into the same box and the reading folds back.

  2. which frame to build the replacement plate from. Three things matter, and
     the third is the one that is easy to get wrong:
       - the card should be large, because the plate gets warped to every other
         frame and upscaling it softens the result
       - the frame should be still, so the ink is not motion-blurred
       - it should be an I or P frame. In a GOP of 'I BBB P BBB P...' the B
         frames are given far fewer bits, so a sharpness ranking over all frames
         really ranks compression, not focus.

Prints a recipe fragment ready to paste into a scene's recipe.json.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, check=True, **kw).stdout


def probe(path, entries, stream=None):
    cmd = ["ffprobe", "-v", "error"]
    if stream:
        cmd += ["-select_streams", stream]
    cmd += ["-show_entries", entries, "-of", "default=nw=1:nk=1", str(path)]
    return [l for l in run(cmd).strip().splitlines() if l]


def frames_of(video, first, last, cache):
    """decode a frame range once into memory"""
    out = {}
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(video),
         "-vf", f"select='between(n\\,{first}\\,{last})'",
         "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True).stdout
    W, H = cache
    n = W * H * 3
    for i in range(len(raw) // n):
        out[first + i] = np.frombuffer(raw[i * n:(i + 1) * n], np.uint8).reshape(H, W, 3)
    return out


def dissolve_curve(fr, a_side, b_side, span):
    """mix ratio t on each frame of span, 0 = fully the A side, 1 = fully B"""
    A = [fr[n].astype(np.float32) for n in a_side if n in fr]
    B = [fr[n].astype(np.float32) for n in b_side if n in fr]
    if not A or not B:
        return {}
    H, W = A[0].shape[:2]
    cands = []
    for y in range(0, H - 80, 80):
        for x in range(0, W - 80, 80):
            a = np.array([f[y:y + 80, x:x + 80].mean() for f in A])
            b = np.array([f[y:y + 80, x:x + 80].mean() for f in B])
            sep = abs(a.mean() - b.mean())
            noise = max(a.std(), b.std())
            if sep > 25 and noise < sep * 0.12:
                cands.append((sep / (noise + 1e-6), x, y, a.mean(), b.mean()))
    cands.sort(reverse=True)
    cands = cands[:6]
    if len(cands) < 3:
        return {}
    curve = {}
    for n in span:
        if n not in fr:
            continue
        f = fr[n].astype(np.float32)
        ts = [(f[y:y + 80, x:x + 80].mean() - a) / (b - a) for _, x, y, a, b in cands]
        curve[n] = float(np.median(ts))

    # Where the two shots are lit alike the patches separate them only weakly and
    # the reading never quite reaches 0 or 1 - it sits on a floor of 0.1-0.2 in
    # the middle of a shot that is plainly not mixed at all. The shape is right,
    # so rescale the ends onto 0 and 1 rather than trusting the raw level.
    vals = np.array(list(curve.values()))
    lo_v, hi_v = np.percentile(vals, 3), np.percentile(vals, 97)
    if hi_v - lo_v > 0.25:
        for n in curve:
            curve[n] = float(np.clip((curve[n] - lo_v) / (hi_v - lo_v), 0.0, 1.0))
    else:
        for n in curve:
            curve[n] = float(np.clip(curve[n], 0.0, 1.0))
    return curve


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("clean_start", type=int)
    ap.add_argument("clean_end", type=int)
    ap.add_argument("--pad", type=int, default=30)
    ap.add_argument("--box", type=int, nargs=4, metavar=("X0", "Y0", "X1", "Y1"),
                    help="region holding the text, for the reference-frame pick")
    ap.add_argument("--out", help="write the recipe fragment here")
    args = ap.parse_args()

    v = Path(args.video)
    W, H = (int(x) for x in probe(v, "stream=width,height", "v:0")[:2])
    total = int(probe(v, "stream=nb_frames", "v:0")[0])

    lo = max(0, args.clean_start - args.pad)
    hi = min(total - 1, args.clean_end + args.pad)
    fr = frames_of(v, lo, hi, (W, H))
    print(f"decoded frames {lo}-{hi}")

    gop = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "frame=pict_type", "-of", "csv=p=0",
               "-read_intervals", "%+2", str(v)]).replace("\n", "")[:24]
    print(f"GOP pattern starts: {gop}")

    cs, ce = args.clean_start, args.clean_end
    head = dissolve_curve(fr, range(ce - 20, ce), range(lo, lo + 8),
                          range(lo, cs + 8))
    tail = dissolve_curve(fr, range(cs, cs + 20), range(hi - 7, hi + 1),
                          range(ce - 8, hi + 1))

    def active(curve):
        ks = [k for k in sorted(curve) if 0.004 < curve[k] < 0.996]
        return (ks[0], ks[-1]) if ks else None

    print()
    for name, c in (("dissolve IN ", head), ("dissolve OUT", tail)):
        a = active(c)
        if a:
            print(f"{name}  frames {a[0]}-{a[1]}  ({a[1]-a[0]+1} frames)")
            print("    " + "  ".join("%d:%.2f" % (k, c[k]) for k in range(a[0], a[1] + 1)
                                     if k in c))
        else:
            print(f"{name}  none found")

    # reference frame: largest, stillest, and an I/P frame
    box = args.box or [W // 4, H // 4, 3 * W // 4, 3 * H // 4]
    bx0, by0, bx1, by1 = box
    stats = {}
    prev = None
    for n in range(cs, ce + 1):
        if n not in fr:
            continue
        g = fr[n].mean(axis=2).astype(np.float32)
        sub = g[by0:by1, bx0:bx1]
        lap = np.abs(4 * sub[1:-1, 1:-1] - sub[:-2, 1:-1] - sub[2:, 1:-1]
                     - sub[1:-1, :-2] - sub[1:-1, 2:])
        motion = float(np.abs(g - prev).mean()) if prev is not None else None
        stats[n] = dict(detail=float(lap.mean()), motion=motion)
        prev = g

    ip = [n for n in stats if n % 4 == 0]
    pool = ip or list(stats)
    scored = []
    for n in pool:
        m = stats[n]["motion"]
        if m is None:
            continue
        scored.append((stats[n]["detail"] / (1.0 + m), n, stats[n]))
    scored.sort(reverse=True)
    print()
    print("reference-frame candidates (I/P frames, detail penalised by motion):")
    for s, n, st in scored[:6]:
        print("   frame %4d   detail %6.2f   motion %5.2f   score %6.2f"
              % (n, st["detail"], st["motion"], s))

    best = scored[0][1] if scored else cs
    print()
    print("suggested reference frame: %d  (t = %.3fs)" % (best, best / 30.0))

    frag = {"shot": {"clean": [cs, ce]},
            "dissolve_in": {str(k): round(1 - v_, 4) for k, v_ in sorted(head.items())} if head else {},
            "dissolve_out": {str(k): round(1 - v_, 4) for k, v_ in sorted(tail.items())} if tail else {},
            "reference_frame": best}
    dest = Path(args.out) if args.out else None
    if dest:
        dest.write_text(json.dumps(frag, indent=2))
        print(f"\nwrote {dest}")
    else:
        print()
        print(json.dumps(frag)[:600] + " ...")


if __name__ == "__main__":
    main()

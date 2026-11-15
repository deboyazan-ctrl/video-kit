#!/usr/bin/env python3
"""
review_scenes.py <render.mov> [--scene NAME] [--samples N] [--out DIR]

Hunt for the ghosts a patch leaves behind, before anyone else finds them.

Three of these shipped on this project and the client found all three. They
share one signature: somewhere the old lettering used to be, the render is now
BRIGHTER than the paper around it. That is over-removal - the plate's idea of
clean paper is lighter than the real paper underneath, so subtracting it takes
away more than was there. It cancels exactly on the reference frame and grows
with distance from it, which is why spot-checking one frame never caught it.

So: for every scene, at frames spread across its range, look only inside the
regions that scene actually patches, and count pixels that were ink in the
source and are now brighter than their surroundings. Write a contact sheet with
them marked.

It reports; it does not judge. Paper grain and specular highlights produce a
background rate, so read the numbers against each other and look at the sheet.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent


def probe(path, entries, stream=None):
    cmd = ["ffprobe", "-v", "error"]
    if stream:
        cmd += ["-select_streams", stream]
    cmd += ["-show_entries", entries, "-of", "default=nw=1:nk=1", str(path)]
    return [l for l in subprocess.run(cmd, capture_output=True, text=True,
                                      check=True).stdout.strip().splitlines() if l]


def grab(video, frames, W, H):
    """One decode pass for all the frames wanted from one file."""
    expr = "+".join(f"eq(n\\,{f})" for f in frames)
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(video),
         "-vf", f"select='{expr}'", "-fps_mode", "passthrough",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True).stdout
    n = W * H * 3
    return {f: np.frombuffer(raw[i * n:(i + 1) * n], np.uint8).reshape(H, W, 3).astype(np.float32)
            for i, f in enumerate(frames) if (i + 1) * n <= len(raw)}


def dilate(m, r):
    out = m.copy()
    for _ in range(r):
        p = np.zeros_like(out)
        p[1:, :] |= out[:-1, :]; p[:-1, :] |= out[1:, :]
        p[:, 1:] |= out[:, :-1]; p[:, :-1] |= out[:, 1:]
        out |= p
    return out


def box_blur(a, r, passes=2):
    out = a.astype(np.float32)
    for _ in range(passes):
        for ax in (0, 1):
            pad = [(0, 0), (0, 0)]
            pad[ax] = (r, r)
            q = np.pad(out, pad, mode="edge")
            c = np.cumsum(q, axis=ax)
            c = np.concatenate([np.zeros_like(np.take(c, [0], axis=ax)), c], axis=ax)
            out = (np.take(c, range(2 * r + 1, c.shape[ax]), axis=ax)
                   - np.take(c, range(0, c.shape[ax] - 2 * r - 1), axis=ax)) / (2 * r + 1)
    return out


def homography(s, d):
    A = np.zeros((8, 8)); b = np.zeros(8)
    for i in range(4):
        x, y = s[i]; u, v = d[i]
        A[i * 2] = [x, y, 1, 0, 0, 0, -u * x, -u * y]; b[i * 2] = u
        A[i * 2 + 1] = [0, 0, 0, x, y, 1, -v * x, -v * y]; b[i * 2 + 1] = v
    h = np.linalg.solve(A, b)
    return np.array([[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], 1.0]])


def region_mask(rec, W, H):
    """Everything this scene touches, in reference coordinates."""
    m = np.zeros((H, W), bool)
    patch = rec.get("patch", {})
    cores = patch.get("cores") or ([patch["core"]] if "core" in patch else [])
    for c in list(cores) + list(patch.get("fill_cores", [])):
        if isinstance(c, list):
            m[c[1]:c[3], c[0]:c[2]] = True
    for er in rec.get("erase", []):
        r = er["rect"]
        m[r[1]:r[3], r[0]:r[2]] = True
    return m


def review(name, render, out_dir, samples, ink_thr, bright_thr):
    scene = ROOT / "work" / "scenes" / name
    rec = json.loads((scene / "recipe.json").read_text())
    rng = rec.get("range")
    if not rng or not isinstance(rec.get("patch", {}).get("cores"), list):
        return None

    source = ROOT / rec["source"]
    W, H = (int(v) for v in probe(source, "stream=width,height", "v:0")[:2])
    blend = {int(k): float(v) for k, v in rec.get("blend_curve", {}).items()}
    live = [f for f in range(rng[0], rng[1] + 1) if blend.get(f, 1.0) > 0.6]
    if not live:
        return None
    step = max(1, len(live) // samples)
    frames = live[::step][:samples]

    mask_ref = region_mask(rec, W, H)
    if not mask_ref.any():
        return None

    track = {}
    tf = rec.get("track", {}).get("file")
    solve = rec.get("track", {}).get("solve_region")
    if tf and isinstance(solve, list) and (scene / tf).exists():
        track = {int(k): np.array(v["corners"])
                 for k, v in json.loads((scene / tf).read_text()).items()}
    SOLVE = (np.array([[solve[0], solve[1]], [solve[2], solve[1]],
                       [solve[2], solve[3]], [solve[0], solve[3]]], float)
             if isinstance(solve, list) else None)

    S = grab(source, frames, W, H)
    R = grab(render, frames, W, H)
    ys, xs = np.mgrid[0:H, 0:W]
    rows, worst = [], (-99.0, None, None)

    for f in frames:
        if f not in S or f not in R:
            continue
        here = mask_ref
        if track and SOLVE is not None and f in track:
            # An identity track on full-frame corners inverts to something
            # numerically awful; if the mapping does not come back finite,
            # the regions have not moved enough to matter anyway.
            try:
                with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                    Hi = np.linalg.inv(homography(SOLVE, track[f]))
                    p = Hi @ np.vstack([xs.ravel().astype(float), ys.ravel().astype(float),
                                        np.ones(xs.size)])
                    uu, vv = p[0] / p[2], p[1] / p[2]
                if np.isfinite(uu).all() and np.isfinite(vv).all():
                    u = np.clip(uu.reshape(H, W), 0, W - 1).astype(int)
                    v = np.clip(vv.reshape(H, W), 0, H - 1).astype(int)
                    here = mask_ref[v, u]
            except np.linalg.LinAlgError:
                pass

        gs = S[f].mean(axis=2)
        gr = R[f].mean(axis=2)
        was_ink = here & ((gs - box_blur(gs, 11)) < -ink_thr)
        excess = gr - box_blur(gr, 11)
        # Only where the old ink is NOT buried under new ink - those pixels are
        # covered and tell us nothing.
        open_ = was_ink & (excess > -ink_thr)
        ring = here & dilate(was_ink, 6) & ~dilate(was_ink, 2) & (excess > -ink_thr)
        if open_.sum() < 60 or ring.sum() < 60:
            continue
        # Counting pixels that merely went from ink to paper flags every honest
        # replacement. What marks a ghost is that the old shape is BRIGHTER than
        # the paper beside it, so measure the gap between the two.
        lift = float(excess[open_].mean() - excess[ring].mean())
        rows.append((f, lift, int(open_.sum()), lift))
        if lift > worst[0]:
            worst = (lift, f, open_ & (excess > bright_thr))

    if not rows:
        return None
    if worst[2] is not None and worst[2].any():
        vis = R[worst[1]].astype(np.uint8).copy()
        vis[worst[2]] = [255, 0, 0]
        im = Image.fromarray(vis)
        ImageDraw.Draw(im).text((8, 8), f"{name}  f{worst[1]}  {worst[0]:.1f}%",
                                fill=(255, 60, 60))
        im.save(out_dir / f"{name}.png")
    return rows, worst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("render")
    ap.add_argument("--scene")
    ap.add_argument("--samples", type=int, default=8)
    ap.add_argument("--ink", type=float, default=8.0, help="how dark a source pixel must be to count as ink")
    ap.add_argument("--bright", type=float, default=7.0, help="how bright the render must be to count as a ghost")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    render = ROOT / args.render
    if not render.exists():
        sys.exit(f"{render} not found")
    out_dir = Path(args.out) if args.out else (ROOT / "work" / "review")
    out_dir.mkdir(parents=True, exist_ok=True)

    chain = json.loads((ROOT / "work" / "chain.json").read_text())
    names = [args.scene] if args.scene else chain["scenes"]

    print(f"reviewing {render.name}\n")
    print(f"{'scene':24s} {'worst':>6s}   {'frame':>6s}   per-frame lift, in levels")
    flagged = []
    for name in names:
        try:
            res = review(name, render, out_dir, args.samples, args.ink, args.bright)
        except subprocess.CalledProcessError:
            print(f"{name:24s}  could not read frames")
            continue
        if not res:
            print(f"{name:24s}       -          nothing to check")
            continue
        rows, worst = res
        if worst[1] is None:
            print(f"{name:24s}      -          too little old ink to compare")
            continue
        trail = " ".join(f"{p:+.1f}" for _, _, _, p in rows)
        print(f"{name:24s} {worst[0]:+6.1f}   {worst[1]:6d}   {trail}")
        if worst[0] >= 2.5:
            flagged.append((worst[0], name))

    print()
    if flagged:
        print("worth looking at, highest first:")
        for pct, name in sorted(flagged, reverse=True):
            print(f"   {name:24s} {pct:+.1f} levels   {out_dir.relative_to(ROOT)}/{name}.png")
        print("\nA number on its own proves nothing - open the picture. Red is where the "
              "source had ink and the render is now brighter than the paper around it.")
    else:
        print("nothing above 2.5 levels. Still worth opening a sheet or two.")


if __name__ == "__main__":
    main()

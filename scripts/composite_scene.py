#!/usr/bin/env python3
"""
composite_scene.py <scene_dir> [--out work/grad/NAME.mov]

Renders one scene's text replacement without an NLE.

It takes three things the scene folder already carries:

  * a difference plate - the reference frame with the new text minus the
    reference frame with the old, so the composite is  src + blend*(A' - A).
    An over-composite cannot be used: during a cross-dissolve the source still
    holds the old text at (1-t) strength, and blending a replacement over it
    leaves t*(1-t) of the old word showing through - 25% at the midpoint.

  * a per-frame planar transform, solved by aligning the image itself rather
    than taken from a tracker, so it can be measured before it is trusted.

  * a blend curve that follows the shot's own cross-dissolve, so the new ink
    fades at exactly the rate the card it sits on does.

Frames outside the scene's range are passed through untouched. Audio is copied
from the source. The result is an intermediate: the head trim and the delivery
encode belong to an EDL through scripts/apply_edl.py.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def probe(path, entries, stream=None):
    cmd = ["ffprobe", "-v", "error"]
    if stream:
        cmd += ["-select_streams", stream]
    cmd += ["-show_entries", entries, "-of", "default=nw=1:nk=1", str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return [l for l in out.stdout.strip().splitlines() if l]


def homography(src, dst):
    """3x3 taking the four src points to the four dst points"""
    A = np.zeros((8, 8))
    b = np.zeros(8)
    for i in range(4):
        x, y = src[i]
        u, v = dst[i]
        A[i * 2] = [x, y, 1, 0, 0, 0, -u * x, -u * y]
        b[i * 2] = u
        A[i * 2 + 1] = [0, 0, 0, x, y, 1, -v * x, -v * y]
        b[i * 2 + 1] = v
    h = np.linalg.solve(A, b)
    return np.array([[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], 1.0]])


def build_diff(recipe, scene_dir, size, return_mask=False):
    """A' - A over a mask that is fully opaque across every stroke being replaced.

    The mask is grown from the ink outward. Building it the other way -- inset a
    rectangle and blur it -- looks equivalent and is not: a Gaussian spreads far
    past its radius, so the region that actually reaches full opacity ends up
    well inside the rectangle and part of the old ink survives on the ramp.
    """
    W, H = size
    A = np.asarray(Image.open(scene_dir / recipe["plates"]["original"])
                   .convert("RGB")).astype(np.float32)
    Ap = np.asarray(Image.open(scene_dir / recipe["plates"]["edited_matched"])
                    .convert("RGB")).astype(np.float32)
    if A.shape[:2] != (H, W) or Ap.shape[:2] != (H, W):
        sys.exit("plates do not match the source resolution")

    patch = recipe["patch"]
    cores = patch.get("cores") or [patch["core"]]
    ramp = float(patch["ramp"])
    xs = np.arange(W)[None, :]
    ys = np.arange(H)[:, None]

    allowed = np.zeros((H, W), bool)
    for core in list(cores) + list(patch.get("fill_cores", [])):
        allowed[core[1]:core[3], core[0]:core[2]] = True

    hug = patch.get("hug_strokes")
    if hug:
        # Where the lettering is sparse, a rectangle covers a lot of bare
        # surface as well - and the plate's grain there is regenerated, not
        # copied, so the patch prints a faint rectangle of the wrong texture
        # with a seam at its edge. Take the mask from where the two plates
        # actually differ instead, so it hugs the strokes and leaves the
        # surface between them untouched.
        extra = hug.get("old_marks")
        if hug.get("mode") == "marks":
            # Take the mask from everything that stands out from the surface on
            # EITHER plate, not from where the two differ. A faint old mark
            # whose brightness happens to be close to the new plate's bare
            # surface produces almost no difference, slips under the threshold,
            # and is left behind - which is exactly how a quarter of the old
            # beadwork survived.
            thr = float(hug["threshold"])
            r = int(hug.get("surface_radius", 14))
            sig = np.zeros((H, W), bool)
            for plate in (A, Ap):
                g = plate.mean(axis=2)
                sig |= np.abs(g - box_blur(g[..., None], r)[..., 0]) > thr
            sig &= allowed
        else:
            d = np.abs(Ap - A).mean(axis=2)
            sig = (d > float(hug["threshold"])) & allowed
        sig = drop_small(sig, int(hug.get("min_px", 60)))
        if extra:
            # The difference between the plates is small wherever a faint old
            # mark happens to sit at the brightness of the new plate's bare
            # surface, so those parts of it never enter the mask and survive
            # the patch. Add the old marks themselves, found on the original
            # plate alone, in whichever direction they stand out.
            g = A.mean(axis=2)
            base = box_blur(g[..., None], int(extra.get("surface_radius", 14)))[..., 0]
            t = float(extra["threshold"])
            om = (g > base + t) if extra.get("polarity", "bright") == "bright" \
                else (g < base - t)
            om = drop_small(om & allowed, int(extra.get("min_px", 30)))
            print(f"  plus {int(om.sum())} px of old marks the difference missed")
            sig = sig | om
        core_mask = dilate(sig, int(hug["grow"]))
        mask = 1.0 - edge_falloff(~core_mask, ramp)
        mask = np.clip(mask, 0, 1).astype(np.float32)
        # Faint marks - a small printed credit line, say - fall under the
        # threshold and would only be half removed. Those boxes are filled
        # solid instead; they are small and sit on plain surface, so a filled
        # rectangle there costs nothing.
        for fc in patch.get("fill_cores", []):
            core_mask[fc[1]:fc[3], fc[0]:fc[2]] = True
        if patch.get("fill_cores"):
            mask = 1.0 - edge_falloff(~core_mask, ramp)
            mask = np.clip(mask, 0, 1).astype(np.float32)
        frac = (mask > 0.5).sum() / max(allowed.sum(), 1)
        print(f"  mask hugs the strokes: {(mask > 0.5).sum()} px, "
              f"{100 * frac:.0f}% of the boxed area")
    else:
        mask = np.zeros((H, W), np.float32)
        for core in cores:
            dx = np.maximum(np.maximum(core[0] - xs, xs - core[2]), 0).astype(np.float32)
            dy = np.maximum(np.maximum(core[1] - ys, ys - core[3]), 0).astype(np.float32)
            t = np.clip(np.sqrt(dx ** 2 + dy ** 2) / ramp, 0, 1)
            mask = np.maximum(mask, 1.0 - (3 * t ** 2 - 2 * t ** 3))

    # Keep the patch off the card's own edges. The plate is a regeneration, so
    # its deckled border sits a pixel or so from the real one; letting the mask
    # reach it would print that disagreement along the edge.
    inset = patch.get("card_inset")
    if inset:
        # Measure the card on the real frame, not on an erased copy of it.
        # With erase_first the "original" plate is the reference frame with the
        # old lettering blurred out, and that blur moves the paper's luma and
        # warmth enough that largest_component picked a different blob - the
        # mask then missed a core entirely and the render stopped.
        src = recipe["plates"].get("original_source")
        C = (np.asarray(Image.open(scene_dir / src).convert("RGB")).astype(np.float32)
             if src else A)
        L = C.mean(axis=2)
        warm = C[:, :, 0] - C[:, :, 2]
        card = (L > inset["luma"]) & (warm < inset["warm"])
        # Ink and shadow punch holes in the paper mask, and a band of them can
        # cut the card into two blobs - keeping only the largest then throws
        # half the card away, and the patch silently does nothing there. Close
        # the holes before picking the component.
        r = int(inset.get("close", 12))
        card = erode(dilate(card, r), r)
        card = largest_component(card)
        mask *= edge_falloff(card, float(inset["margin"]))

    # A core the mask does not reach is a region where the old text simply
    # stays. Fail loudly instead of rendering a frame that still says the
    # wrong thing.
    if patch.get("hug_strokes"):
        hg = patch["hug_strokes"]
        if hg.get("mode") == "marks":
            thr = float(hg["threshold"]) * 1.5
            r = int(hg.get("surface_radius", 14))
            strokes = np.zeros((H, W), bool)
            for plate in (A, Ap):
                g = plate.mean(axis=2)
                strokes |= np.abs(g - box_blur(g[..., None], r)[..., 0]) > thr
            strokes &= allowed
        else:
            d = np.abs(Ap - A).mean(axis=2)
            strokes = (d > float(hg["threshold"]) * 1.5) & allowed
        if strokes.any():
            bad = 100 * float((mask[strokes] < 0.9).mean())
            print(f"  {bad:.1f}% of the changed pixels sit on the mask's ramp "
                  f"rather than its core")
            if bad > 5.0:
                sys.exit("  too many - grow the mask or lower the threshold")
    else:
        for core in cores:
            cx, cy = (core[0] + core[2]) // 2, (core[1] + core[3]) // 2
            if mask[cy, cx] < 0.9:
                sys.exit(f"patch core {core} is not covered (mask={mask[cy, cx]:.2f} at "
                         f"its centre). The card mask or the core is wrong.")

    if return_mask:
        return (Ap - A) * mask[..., None], mask
    return (Ap - A) * mask[..., None]


def drop_small(m, minpx):
    from collections import deque
    seen = np.zeros(m.shape, bool)
    out = np.zeros(m.shape, bool)
    for sy, sx in zip(*np.nonzero(m)):
        if seen[sy, sx]:
            continue
        q = deque([(sy, sx)]); seen[sy, sx] = True; pts = []
        while q:
            y, x = q.popleft(); pts.append((y, x))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < m.shape[0] and 0 <= nx < m.shape[1] \
                            and m[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True; q.append((ny, nx))
        if len(pts) >= minpx:
            p = np.array(pts)
            out[p[:, 0], p[:, 1]] = True
    return out


def dilate(m, r):
    out = m.copy()
    for _ in range(r):
        p = np.zeros_like(out)
        p[1:, :] |= out[:-1, :]; p[:-1, :] |= out[1:, :]
        p[:, 1:] |= out[:, :-1]; p[:, :-1] |= out[:, 1:]
        out |= p
    return out


def erode(m, r):
    return ~dilate(~m, r)


def largest_component(m):
    from collections import deque
    seen = np.zeros(m.shape, bool)
    best = None
    for sy, sx in zip(*np.nonzero(m)):
        if seen[sy, sx]:
            continue
        q = deque([(sy, sx)]); seen[sy, sx] = True; pts = []
        while q:
            y, x = q.popleft(); pts.append((y, x))
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < m.shape[0] and 0 <= nx < m.shape[1] \
                        and m[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True; q.append((ny, nx))
        if best is None or len(pts) > len(best):
            best = pts
    out = np.zeros(m.shape, bool)
    if best:
        p = np.array(best)
        out[p[:, 0], p[:, 1]] = True
    return out


def edge_falloff(mask_bool, margin):
    """1 well inside the region, ramping to 0 at its boundary"""
    d = np.full(mask_bool.shape, np.inf, np.float32)
    d[~mask_bool] = 0.0
    # Each pass propagates the distance by one pixel, so the loop has to run at
    # least as many times as the margin is wide. Running it fewer times caps the
    # distance below the margin and silently scales the whole patch down.
    for _ in range(int(np.ceil(margin)) + 2):
        for ax, step in ((0, 1), (0, -1), (1, 1), (1, -1)):
            rolled = np.roll(d, step, axis=ax)
            if ax == 0:
                rolled[0 if step == 1 else -1, :] = 0
            else:
                rolled[:, 0 if step == 1 else -1] = 0
            d = np.minimum(d, rolled + 1.0)
        if d[mask_bool].min() >= margin:
            break
    t = np.clip(d / margin, 0, 1)
    return (3 * t ** 2 - 2 * t ** 3).astype(np.float32)


def box_blur(a, r, passes=3):
    """repeated box blur - a cheap Gaussian, enough to destroy small detail"""
    k = 2 * r + 1
    out = a.astype(np.float32)
    for _ in range(passes):
        for ax in (0, 1):
            pad = [(0, 0), (0, 0), (0, 0)][:out.ndim]
            pad[ax] = (r, r)
            q = np.pad(out, pad, mode="edge")
            c = np.cumsum(q, axis=ax)
            zero = np.zeros_like(np.take(c, [0], axis=ax))
            c = np.concatenate([zero, c], axis=ax)
            hi = np.take(c, range(k, c.shape[ax]), axis=ax)
            lo = np.take(c, range(0, c.shape[ax] - k), axis=ax)
            out = (hi - lo) / k
    return out


def sample_bilinear(img, u, v):
    H, W = img.shape[:2]
    u = np.clip(u, 0, W - 1.001)
    v = np.clip(v, 0, H - 1.001)
    u0 = np.floor(u).astype(np.int32)
    v0 = np.floor(v).astype(np.int32)
    fu = (u - u0)[..., None]
    fv = (v - v0)[..., None]
    a = img[v0, u0]
    b = img[v0, u0 + 1]
    c = img[v0 + 1, u0]
    d = img[v0 + 1, u0 + 1]
    return (a * (1 - fu) * (1 - fv) + b * fu * (1 - fv)
            + c * (1 - fu) * fv + d * fu * fv)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene_dir")
    ap.add_argument("--out")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dump-mask", help="write the patch mask here and stop")
    args = ap.parse_args()

    scene = Path(args.scene_dir)
    recipe = json.loads((scene / "recipe.json").read_text())
    # "source" is what the scene was measured against; "input" is what this pass
    # composites onto, so scenes can be chained - each one builds on the last
    # rather than each starting from the untouched original.
    src = Path(recipe["source"])
    inp = Path(recipe["render"].get("input", recipe["source"]))
    out = Path(args.out or recipe["render"]["intermediate"])
    if out.exists() and not args.dump_mask:
        sys.exit(f"{out} exists - bump the version")

    W, H = (int(v) for v in probe(inp, "stream=width,height", "v:0")[:2])
    total = int(probe(inp, "stream=nb_frames", "v:0")[0])
    has_audio = bool(probe(inp, "stream=index", "a"))

    diff = build_diff(recipe, scene, (W, H))
    if args.dump_mask:
        _, m = build_diff(recipe, scene, (W, H), return_mask=True)
        print(f"  mask max {m.max():.3f}  >0.99 on {(m > 0.99).sum()} px"
              f"  >0.5 on {(m > 0.5).sum()} px")
        Image.fromarray((np.clip(m, 0, 1) * 255).astype(np.uint8)).save(args.dump_mask)
        print("  ->", args.dump_mask)
        return

    track = json.loads((scene / recipe["track"]["file"]).read_text())
    corners = {int(k): np.array(v["corners"]) for k, v in track.items()}
    solve_region = recipe["track"]["solve_region"]
    sx0, sy0, sx1, sy1 = solve_region
    SOLVE = np.array([[sx0, sy0], [sx1, sy0], [sx1, sy1], [sx0, sy1]], float)

    # Regions wiped using the frame's own surface rather than the plate's.
    # Removing a mark through the difference plate only works if the transform
    # is right to a pixel or two there: the difference is a picture of "take the
    # ink away from exactly here", so a transform that is out by 15 px leaves
    # the mark and brightens a patch of blank surface beside it. Copying clean
    # surface from elsewhere in the same frame needs no registration at all.
    erases = recipe.get("erase", [])

    blend = {int(k): float(v) for k, v in recipe["blend_curve"].items()}
    first, last = recipe["range"]

    # The plate was made from one frame, so it carries that frame's ink
    # contrast. Where the card is smaller or softer its real ink is weaker, and
    # removing a full-strength version of it over-corrects: the old strokes come
    # back as bright ghosts. This scales the whole difference by the contrast the
    # card's own unchanged line actually has on each frame.
    gain = {}
    gf = recipe.get("ink_gain", {}).get("file")
    if gf:
        gain = {int(k): float(v) for k, v in json.loads((scene / gf).read_text()).items()}

    def blend_at(n):
        if n < first:
            return 0.0
        if n in blend:
            return blend[n]
        keys = sorted(blend)
        if n < keys[0]:
            return blend[keys[0]]
        if n > keys[-1]:
            return blend[keys[-1]]
        lo = max(k for k in keys if k <= n)
        hi = min(k for k in keys if k >= n)
        if lo == hi:
            return blend[lo]
        f = (n - lo) / (hi - lo)
        return blend[lo] * (1 - f) + blend[hi] * f

    def gain_at(n):
        if not gain:
            return 1.0
        keys = sorted(gain)
        n = min(max(n, keys[0]), keys[-1])
        if n in gain:
            return gain[n]
        lo = max(k for k in keys if k <= n)
        hi = min(k for k in keys if k >= n)
        f = (n - lo) / (hi - lo)
        return gain[lo] * (1 - f) + gain[hi] * f

    active = [n for n in range(first, last + 1)
              if blend_at(n) > 0.001 and n in corners]
    print(f"  measured on   {src}")
    print(f"  compositing   onto {inp}  {W}x{H}  {total} frames")
    print(f"  scene range   {first}-{last}")
    print(f"  compositing   {len(active)} frames ({active[0]}-{active[-1]})")
    print(f"  output        {out}")
    if args.dry_run:
        return

    dec = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(inp),
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE)
    enc_cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
               "-r", probe(inp, "stream=r_frame_rate", "v:0")[0], "-i", "-"]
    if has_audio:
        enc_cmd += ["-i", str(inp), "-map", "0:v", "-map", "1:a",
                    "-c:a", "pcm_s24le"]
    enc_cmd += ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le",
                str(out)]
    out.parent.mkdir(parents=True, exist_ok=True)
    enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE)

    nbytes = W * H * 3
    yy, xx = np.mgrid[0:H, 0:W]
    touched = 0
    for n in range(total):
        buf = dec.stdout.read(nbytes)
        if len(buf) < nbytes:
            break
        frame = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
        b = blend_at(n)
        if b > 0.001 and n in corners:
            Hinv = homography(SOLVE, corners[n])       # reference -> this frame
            Hn = np.linalg.inv(Hinv)                   # this frame -> reference
            p = Hn @ np.stack([xx.ravel().astype(np.float64),
                               yy.ravel().astype(np.float64),
                               np.ones(W * H)], axis=0)
            u = (p[0] / p[2]).reshape(H, W)
            v = (p[1] / p[2]).reshape(H, W)
            inside = (u >= 0) & (u < W - 1) & (v >= 0) & (v < H - 1)
            d = sample_bilinear(diff, u, v) * inside[..., None]

            # Things in front of the surface have to stay in front of it. Here
            # the typebars swing across the line being replaced; patch over them
            # and the new letters print on the metal. They are far darker than
            # the paper, so a threshold on the frame itself separates them, and
            # it needs no rotoscoping and no tracking.
            sheet = recipe["patch"].get("occluder_sheet_edge")
            if sheet:
                # A sheet of paper rises through the shot and covers the surface
                # being patched. A brightness threshold alone cannot separate it:
                # the white bouquet in the photograph behind reaches 221 and the
                # paper sits at 205. What does separate them is that the paper is
                # a straight edge reaching the bottom of the region - so find,
                # per column, where the bright run from the bottom ends, and fit
                # a line through those points by the median of pairwise slopes,
                # which ignores the columns where the bouquet has joined the run.
                gmean = frame.astype(np.float32).mean(axis=2)
                sx0, sx1 = sheet["cols"]
                ybot = int(sheet["bottom"])
                thr = float(sheet["brighter_than"])
                pts = []
                for c in range(sx0, sx1, int(sheet.get("step", 4))):
                    col = gmean[:ybot, c]
                    r = ybot - 1
                    while r >= 0 and col[r] > thr:
                        r -= 1
                    if ybot - 1 - r >= int(sheet.get("min_run", 6)):
                        pts.append((c, r + 1))
                if len(pts) >= 8:
                    P_ = np.array(pts, float)
                    sl = []
                    for i in range(0, len(P_), 3):
                        for j in range(i + 3, len(P_), 3):
                            if P_[j, 0] != P_[i, 0]:
                                sl.append((P_[j, 1] - P_[i, 1]) / (P_[j, 0] - P_[i, 0]))
                    if sl:
                        m_ = float(np.median(sl))
                        b_ = float(np.median(P_[:, 1] - m_ * P_[:, 0]))
                        cols_ = np.arange(W)[None, :]
                        rows_ = np.arange(H)[:, None]
                        below = rows_ >= (m_ * cols_ + b_)
                        d = d * (~below)[..., None]

            occ = recipe["patch"].get("occluder_darker_than")
            if occ is not None:
                keep = (frame.astype(np.float32).mean(axis=2) > float(occ))
                # A threshold alone stops at the occluder's own edge, and the
                # soft rim just outside it is still bright enough to be
                # patched - which printed white blobs along the typebars, where
                # the plate's bar sits a pixel or two off. Pull the kept area
                # back from the edge.
                for _ in range(int(recipe["patch"].get("occluder_grow", 0))):
                    k = keep.copy()
                    k[1:, :] &= keep[:-1, :]; k[:-1, :] &= keep[1:, :]
                    k[:, 1:] &= keep[:, :-1]; k[:, :-1] &= keep[:, 1:]
                    keep = k
                d = d * keep[..., None]

            out_f = frame.astype(np.float32) + b * gain_at(n) * d

            # With erase_first the blur runs on the frame BEFORE the patch, and
            # the plate's baseline is the erased reference frame, so the
            # difference carries only the new lettering and has nothing to
            # remove. Removing old text by subtracting it cancels exactly on the
            # reference frame and nowhere else - a little too much comes off and
            # the old words print back bright, between the new ones. A blur
            # works off each frame's own paper, so it is exact on every frame.
            if recipe.get("erase_first"):
                base_f = frame.astype(np.float32)
                for er in erases:
                    rx0, ry0, rx1, ry1 = er["rect"]
                    soft = float(er.get("ramp", 14))
                    ddx = np.maximum(np.maximum(rx0 - u, u - rx1), 0)
                    ddy = np.maximum(np.maximum(ry0 - v, v - ry1), 0)
                    t = np.clip(np.sqrt(ddx ** 2 + ddy ** 2) / soft, 0, 1)
                    # Fade the blur with the shot. An erase at full strength
                    # while the patch is at 20% strips the old text out and
                    # leaves almost nothing in its place - grey smudges through
                    # the dissolve - and once the shot has gone the blur is
                    # still scrubbing whatever the NEXT shot happens to put
                    # under those coordinates.
                    em = ((1.0 - (3 * t ** 2 - 2 * t ** 3)) * inside * b)[..., None]
                    ys_, xs_ = np.nonzero(em[..., 0] > 0.01)
                    # As the shot fades the mask scales with it, and the two
                    # thresholds have to agree: a mask peaking at 0.005 passed
                    # a > 0.001 check and then handed an empty array to min().
                    if not len(ys_):
                        continue
                    y0b, y1b = max(ys_.min() - 4, 0), min(ys_.max() + 5, H)
                    x0b, x1b = max(xs_.min() - 4, 0), min(xs_.max() + 5, W)
                    src = base_f.copy()
                    src[y0b:y1b, x0b:x1b] = box_blur(
                        base_f[y0b:y1b, x0b:x1b], int(er.get("radius", 14)))
                    base_f = base_f * (1 - em) + src * em
                out_f = base_f + b * gain_at(n) * d

            for er in ([] if recipe.get("erase_first") else erases):
                rx0, ry0, rx1, ry1 = er["rect"]
                dy = float(er.get("source_dy", -60))
                dx = float(er.get("source_dx", 0.0))
                soft = float(er.get("ramp", 14))
                # mask of the region, in this frame's view
                ddx = np.maximum(np.maximum(rx0 - u, u - rx1), 0)
                ddy = np.maximum(np.maximum(ry0 - v, v - ry1), 0)
                t = np.clip(np.sqrt(ddx ** 2 + ddy ** 2) / soft, 0, 1)
                # same reasoning as above: the blur belongs to this shot and
                # has to leave with it
                em = ((1.0 - (3 * t ** 2 - 2 * t ** 3)) * inside * b)[..., None]
                if not (em[..., 0] > 0.01).any():
                    continue
                # where each pixel should copy from: shift in reference space,
                # then carry that point back into this frame
                if er.get("mode", "blur") == "blur":
                    # Smear the mark out of its own surroundings. Copying
                    # surface from elsewhere needs somewhere to copy from that
                    # is the same material and the same brightness, and next to
                    # a ribbon and a seal there may be nowhere; blurring keeps
                    # the local shading exactly and only destroys the detail,
                    # which is all the mark is.
                    ys_, xs_ = np.nonzero(em[..., 0] > 0.01)
                    if ys_.size == 0:
                        continue
                    y0b, y1b = max(ys_.min() - 4, 0), min(ys_.max() + 5, H)
                    x0b, x1b = max(xs_.min() - 4, 0), min(xs_.max() + 5, W)
                    patch_px = out_f[y0b:y1b, x0b:x1b]
                    src = out_f.copy()
                    src[y0b:y1b, x0b:x1b] = box_blur(patch_px, int(er.get("radius", 14)))
                else:
                    sp = Hinv @ np.stack([(u + dx).ravel(), (v + dy).ravel(),
                                          np.ones(u.size)], axis=0)
                    su = (sp[0] / sp[2]).reshape(H, W)
                    sv = (sp[1] / sp[2]).reshape(H, W)
                    src = sample_bilinear(frame.astype(np.float32), su, sv)
                    ring = ((em[..., 0] > 0.15) & (em[..., 0] < 0.7))
                    if ring.sum() > 50:
                        src = src + (out_f[ring].mean(axis=0) - src[ring].mean(axis=0))
                out_f = out_f * (1 - em) + src * em

            frame = np.clip(out_f, 0, 255).astype(np.uint8)
            touched += 1
        enc.stdin.write(frame.tobytes())
        if n % 200 == 0:
            print(f"    frame {n}/{total}", flush=True)

    enc.stdin.close()
    enc.wait()
    dec.wait()

    # Read the reference frame back out and check it against the composite we
    # meant to make. This catches the whole class of faults where the pipeline
    # ran happily on the wrong thing - wrong input file, mask that never
    # reached the text, a blend curve inverted end for end.
    ref = recipe["reference_frame"]
    ref = ref["frame"] if isinstance(ref, dict) else ref
    got = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(out),
         "-vf", f"select=eq(n\\,{ref})", "-fps_mode", "passthrough",
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True).stdout
    if len(got) == W * H * 3:
        got = np.frombuffer(got, np.uint8).reshape(H, W, 3).astype(np.float32)
        base = np.asarray(Image.open(scene / recipe["plates"]["original"])
                          .convert("RGB")).astype(np.float32)
        # The occluder test has to be applied here too. Without it the check
        # compares the render against a composite that paints straight over
        # whatever is in front of the surface - on the typewriter shot the
        # typebars cross the line on the reference frame, and the render was
        # failing its own check by 8.9 levels for being right.
        want_diff = diff
        occ_chk = recipe["patch"].get("occluder_darker_than")
        if occ_chk is not None:
            want_diff = want_diff * (base.mean(axis=2) > float(occ_chk))[..., None]
        want = np.clip(base + blend_at(ref) * gain_at(ref) * want_diff, 0, 255)
        m = np.zeros((H, W), bool)
        for core in (recipe["patch"].get("cores") or [recipe["patch"]["core"]]):
            m[core[1]:core[3], core[0]:core[2]] = True
        # Measure the codec drift first, otherwise it is charged to the edit.
        # Every scene in a chain re-encodes the whole video, and five ProRes
        # generations put 7 levels between the typewriter shot's lettering and
        # the PNG it was measured on - which read as a failed composite on a
        # render that was correct.
        had = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(inp),
             "-vf", f"select=eq(n\\,{ref})", "-fps_mode", "passthrough",
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, check=True).stdout
        drift = 0.0
        if len(had) == W * H * 3:
            had = np.frombuffer(had, np.uint8).reshape(H, W, 3).astype(np.float32)
            drift = float(np.abs(had - base).mean(axis=2)[m].mean())
        err = float(np.abs(got - want).mean(axis=2)[m].mean())
        print(f"  check: reference frame {ref} differs from the intended "
              f"composite by {err:.2f} levels inside the patch"
              f"  ({drift:.2f} of that is already in the input)")
        if drift > 25.0:
            sys.exit(f"  the input does not look like {recipe['source']} at frame {ref}"
                     " - wrong file, or the reference frame is not where it is said to be")
        if err - drift > 6.0:
            sys.exit("  that is too much - the render does not match the recipe")

    print(f"  composited {touched} frames")
    print(f"  -> {out}")


if __name__ == "__main__":
    main()

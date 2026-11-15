#!/usr/bin/env python3
"""
new_scene.py <name> <clean_start> <clean_end> [--box X0 Y0 X1 Y1] [--ref N]
             [--crop X0 Y0 X1 Y1] [--upscale N]

Open a scene folder: find the shot's edges, pull the reference frame, cut the
crop for the image model, and write a recipe with the measurements already in
it and the decisions left blank.

This is the part that was retyped fourteen times. What it does NOT do is
choose: the reference frame it suggests is scored on detail against motion,
which knows nothing about whether the lettering is clear of the typebars or
whether the frame sits inside a dissolve. Both of those picked the wrong frame
on this video. Read the candidates and pass --ref.

Check the dissolve curves it prints. A curve that never reaches 1 has not
measured a dissolve - see LESSONS.md - and the blend has to be measured by
hand before anything is composited.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def probe(path, entries, stream=None):
    cmd = ["ffprobe", "-v", "error"]
    if stream:
        cmd += ["-select_streams", stream]
    cmd += ["-show_entries", entries, "-of", "default=nw=1:nk=1", str(path)]
    return [l for l in subprocess.run(cmd, capture_output=True, text=True,
                                      check=True).stdout.strip().splitlines() if l]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="folder name, e.g. 10_place_card")
    ap.add_argument("clean_start", type=int)
    ap.add_argument("clean_end", type=int)
    ap.add_argument("--source", default=None, help="defaults to the source in work/chain.json")
    ap.add_argument("--box", type=int, nargs=4, metavar=("X0", "Y0", "X1", "Y1"),
                    help="where the text is, for scoring reference-frame candidates")
    ap.add_argument("--ref", type=int, help="reference frame, if you have already chosen one")
    ap.add_argument("--crop", type=int, nargs=4, metavar=("X0", "Y0", "X1", "Y1"),
                    help="the region to send to the image model")
    ap.add_argument("--upscale", type=int, default=5)
    args = ap.parse_args()

    chain = json.loads((ROOT / "work" / "chain.json").read_text())
    source = args.source or chain["source"]
    scene = ROOT / "work" / "scenes" / args.name
    if scene.exists():
        sys.exit(f"{scene} already exists")
    scene.mkdir(parents=True)

    cmd = [sys.executable, str(ROOT / "scripts" / "analyse_shot.py"), source,
           str(args.clean_start), str(args.clean_end), "--pad", "60",
           "--out", str(scene / "shot.json")]
    if args.box:
        cmd += ["--box"] + [str(v) for v in args.box]
    print("measuring the shot ...\n")
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr)
        sys.exit("analyse_shot failed")

    sh = json.loads((scene / "shot.json").read_text())
    for key in ("dissolve_in", "dissolve_out"):
        curve = sh.get(key) or {}
        if curve:
            top = max(float(v) for v in curve.values())
            if top < 0.9:
                print(f"!! {key} peaks at {top:.2f} and never reaches 1 - that curve has not "
                      f"measured a dissolve. Measure it by hand before compositing.")

    ref = args.ref or (sh.get("reference_frame") or {}).get("frame")
    if ref is None:
        sys.exit("no reference frame suggested - pass --ref")
    W, H = (int(v) for v in probe(ROOT / source, "stream=width,height", "v:0")[:2])
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(ROOT / source),
         "-vf", f"select='eq(n\\,{ref})'", "-fps_mode", "passthrough",
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True).stdout
    img = Image.fromarray(np.frombuffer(raw[:W * H * 3], np.uint8).reshape(H, W, 3))
    img.save(scene / "original.png")
    print(f"\nreference frame {ref} -> original.png")

    crops = {}
    if args.crop:
        x0, y0, x1, y1 = args.crop
        s = args.upscale
        img.crop((x0, y0, x1, y1)).resize(((x1 - x0) * s, (y1 - y0) * s),
                                          Image.LANCZOS).save(scene / "crop.png")
        crops["crop"] = {"rect": list(args.crop), "upscale": s}
        (scene / "crops.json").write_text(json.dumps(crops, indent=1))
        print(f"crop {args.crop} at {s}x -> crop.png   (send this, with a prompt from PROMPTS.md)")

    recipe = {
        "scene": args.name,
        "source": source,
        "status": "measuring",
        "shot": {"clean": [args.clean_start, args.clean_end],
                 "dissolve_in": "FILL IN - and check the curve reached 1",
                 "dissolve_out": "FILL IN"},
        "reference_frame": {"frame": ref, "why": "FILL IN - why this frame and not the next one"},
        "text_change": {"FILL IN": "old -> new"},
        "plates": {"original": "original.png", "edited_matched": "edited_matched.png"},
        "crops_sent_to_gemini": crops,
        "track": {"file": "track.json", "solve_region": "FILL IN",
                  "method": "FILL IN", "quality": "FILL IN - correlation AND pixels"},
        "patch": {"cores": "FILL IN", "ramp": 14},
        "ink_gain": {"disabled": True},
        "blend_curve": {},
        "range": [args.clean_start, args.clean_end],
        "render": {"input": "FILL IN - the previous scene's intermediate",
                   "intermediate": f"work/grad/{args.name}_v001.mov"},
    }
    (scene / "recipe.json").write_text(json.dumps(recipe, indent=2, ensure_ascii=False))
    (scene / "notes.md").write_text(f"# {args.name}\n\nWhat was tried and what failed.\n")
    print(f"\n-> {scene.relative_to(ROOT)}/  (recipe.json, notes.md)")
    print("\nnext: choose the reference frame deliberately, measure the dissolves, "
          "then solve the track.")


if __name__ == "__main__":
    main()

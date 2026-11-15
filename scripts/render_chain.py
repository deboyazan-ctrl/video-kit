#!/usr/bin/env python3
"""
render_chain.py [--from SCENE] [--force] [--dry-run]

Composite every scene in order, wiring each one's input to the one before it.

Doing this by hand is what went wrong on this project. Each scene's recipe
names its own input and output file, and re-rendering one scene means editing
the next scene's input, and the next, and bumping a version number in each -
twelve edits to change one thing. It is easy to get right nine times and wrong
once, and the once is expensive: a render failed mid-stream, left a file with
289 frames in it instead of 2089, and the loop carried on and built eleven
scenes on top of it. Nothing complained until the finished video was watched.

So: the order lives in work/chain.json, the wiring is derived, and every
output is counted before the next scene is allowed to read it.

A scene is skipped when its output already exists, has the right number of
frames, and is newer than both its recipe and its input - so re-running after
changing one scene only re-renders that scene and the ones after it.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def frame_count(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True)
    try:
        return int(out.stdout.strip())
    except ValueError:
        return None


def bump(name):
    m = re.match(r"(.*_v)(\d+)(\.mov)$", name)
    if not m:
        sys.exit(f"cannot read a version number out of {name}")
    return f"{m.group(1)}{int(m.group(2)) + 1:03d}{m.group(3)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", help="begin at this scene, keeping earlier outputs")
    ap.add_argument("--force", action="store_true", help="re-render even when the output looks fresh")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    chain = json.loads((ROOT / "work" / "chain.json").read_text())
    scenes = chain["scenes"]
    expected = frame_count(ROOT / chain["source"])
    if not expected:
        sys.exit(f"cannot count frames in {chain['source']}")
    print(f"source {chain['source']}: {expected} frames\n")

    if args.start:
        if args.start not in scenes:
            sys.exit(f"{args.start} is not in the chain")
        first = scenes.index(args.start)
    else:
        first = 0

    prev_out = None
    for i, name in enumerate(scenes):
        rp = ROOT / "work" / "scenes" / name / "recipe.json"
        rec = json.loads(rp.read_text())
        render = rec.setdefault("render", {})

        if i < first:
            prev_out = render.get("intermediate")
            print(f"{name:22s} kept   {prev_out}")
            continue

        if prev_out is not None:
            render["input"] = prev_out
        out = render.get("intermediate")
        inp = render.get("input")

        fresh = False
        if out and not args.force:
            op = ROOT / out
            if op.exists() and frame_count(op) == expected:
                newer_than_recipe = op.stat().st_mtime > rp.stat().st_mtime
                newer_than_input = inp is None or (
                    (ROOT / inp).exists() and op.stat().st_mtime > (ROOT / inp).stat().st_mtime)
                fresh = newer_than_recipe and newer_than_input
        if fresh:
            print(f"{name:22s} fresh  {out}")
            prev_out = out
            continue

        if (ROOT / out).exists():
            out = bump(out)
        render["intermediate"] = out
        if args.dry_run:
            print(f"{name:22s} would render {inp or '(source)'} -> {out}")
            prev_out = out
            continue

        rp.write_text(json.dumps(rec, indent=2, ensure_ascii=False))
        print(f"{name:22s} render {out} ...", flush=True)
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "composite_scene.py"),
                            str(ROOT / "work" / "scenes" / name)],
                           cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout[-2000:]); print(r.stderr[-2000:])
            sys.exit(f"\n{name} failed - stopping. Nothing after it has been touched.")

        got = frame_count(ROOT / out)
        if got != expected:
            # The failure this whole script exists to prevent. A composite can
            # die partway and still leave a playable file behind.
            sys.exit(f"\n{name} wrote {got} frames, expected {expected}. "
                     f"Treat {out} as rubbish and delete it - stopping.")
        print(f"{'':22s}       {got} frames, ok")
        prev_out = out

    print(f"\nchain complete -> {prev_out}")
    print("point the EDL's \"source\" at that file.")


if __name__ == "__main__":
    main()

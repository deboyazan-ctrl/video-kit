#!/usr/bin/env python3
"""
burn_lyrics.py <base.mp4> <cues_dir> <out.mp4> [--crf N] [--dry-run]

Lay each rendered cue over the video for its own stretch of time.

One overlay per cue, each gated by enable='between(t,start,end)'. The audio is
copied through untouched - it was cut and faded when the base was built, and
re-encoding it here would cost quality for nothing.

Refuses to overwrite: bump the version instead.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base"); ap.add_argument("cues"); ap.add_argument("out")
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    base, cues, out = Path(args.base), Path(args.cues), Path(args.out)
    if out.exists():
        sys.exit(f"{out} exists - bump the version, never overwrite a render")
    index = json.loads((cues / "index.json").read_text())

    cmd = ["ffmpeg", "-hide_banner", "-i", str(base)]
    for c in index:
        cmd += ["-i", str(cues / c["file"])]

    chains, cur = [], "[0:v]"
    for i, c in enumerate(index, start=1):
        nxt = f"[v{i}]"
        chains.append(f"{cur}[{i}:v]overlay=0:0:"
                      f"enable='between(t,{c['start']:.3f},{c['end']:.3f})'{nxt}")
        cur = nxt
    graph = ";".join(chains)

    cmd += ["-filter_complex", graph, "-map", cur, "-map", "0:a?",
            "-c:v", "libx264", "-preset", "slow", "-crf", str(args.crf),
            "-pix_fmt", "yuv420p", "-c:a", "copy", str(out)]

    print(" ".join(cmd[:6]), "...\n")
    print(graph.replace(";", ";\n"), "\n")
    if args.dry_run:
        return
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        sys.exit("ffmpeg failed")
    print("->", out)


if __name__ == "__main__":
    main()

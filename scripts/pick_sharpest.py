#!/usr/bin/env python3
"""
pick_sharpest.py <frames_dir>

Ranks frames by sharpness so you edit the cleanest one. A motion-blurred
reference frame produces a blurry composite no amount of tracking will fix.

Uses variance of the Laplacian - the standard blur metric. No extra deps:
shells out to ffmpeg rather than requiring OpenCV.
"""
import subprocess, sys, json
from pathlib import Path

d = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
frames = sorted(d.glob("*.png"))
if not frames:
    sys.exit(f"no PNGs in {d}")

scores = []
for f in frames:
    # convolution with a Laplacian kernel; stddev of the result = sharpness
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(f),
         "-vf", "format=gray,convolution='0 -1 0 -1 4 -1 0 -1 0',signalstats,"
                "metadata=print:file=-",
         "-f", "null", "-"],
        capture_output=True, text=True)
    val = 0.0
    for line in out.stdout.splitlines():
        if "YAVG" in line:
            val = float(line.split("=")[-1])
    scores.append((val, f.name))

scores.sort(reverse=True)
print(f"{len(scores)} frames ranked by sharpness (higher = sharper)\n")
for v, n in scores[:8]:
    print(f"  {v:8.3f}  {n}")
print("\n  ... worst:")
for v, n in scores[-3:]:
    print(f"  {v:8.3f}  {n}")
print(f"\n  EDIT THIS ONE: {scores[0][1]}")

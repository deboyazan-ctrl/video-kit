#!/usr/bin/env bash
# extract_shot.sh <video> <start> <end>
# Pulls every frame of a shot as lossless PNG, ready for editing + tracking.
set -euo pipefail
SRC="${1:?usage: extract_shot.sh <video> <start> <end>}"
START="${2:?}"
END="${3:?}"
OUT="work/shot_${START}_${END}"

mkdir -p "$OUT"
rm -f "$OUT"/*.png

ffmpeg -nostdin -v error -ss "$START" -to "$END" -i "$SRC" \
  -start_number 0 -q:v 1 "$OUT/%05d.png"

N=$(ls "$OUT"/*.png | wc -l | tr -d ' ')
FPS=$(ffprobe -v error -select_streams v -show_entries stream=r_frame_rate \
      -of csv=p=0 "$SRC" | head -1)
echo "  $N frames -> $OUT/  (fps $FPS)"
echo "  frame N is at t = $START + N/${FPS%%/*}"

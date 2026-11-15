#!/usr/bin/env bash
# rebuild_shot.sh <original> <frames_dir> <start> <end> <output>
#
# Splices an edited PNG sequence back into the original video, keeping the
# untouched parts and the original audio bit-for-bit where possible.
set -euo pipefail
SRC="${1:?usage: rebuild_shot.sh <original> <frames_dir> <start> <end> <output>}"
FRAMES="${2:?}"
START="${3:?}"
END="${4:?}"
OUT="${5:?}"

[ -e "$OUT" ] && { echo "$OUT exists - bump the version"; exit 1; }

FPS=$(ffprobe -v error -select_streams v -show_entries stream=r_frame_rate \
      -of csv=p=0 "$SRC" | head -1)

ffmpeg -nostdin -hide_banner \
  -i "$SRC" \
  -framerate "$FPS" -i "$FRAMES/%05d.png" \
  -filter_complex "\
[0:v]trim=start=0:end=$START,setpts=PTS-STARTPTS[pre]; \
[1:v]format=yuv420p,setpts=PTS-STARTPTS[mid]; \
[0:v]trim=start=$END,setpts=PTS-STARTPTS[post]; \
[pre][mid][post]concat=n=3:v=1:a=0[v]" \
  -map "[v]" -map 0:a? \
  -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p \
  -c:a copy \
  "$OUT"

echo
echo "  -> $OUT"
ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT"

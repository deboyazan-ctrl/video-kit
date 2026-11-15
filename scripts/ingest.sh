#!/usr/bin/env bash
# ingest.sh - Turn a raw video into artifacts Claude can actually read.
#
#   ./scripts/ingest.sh raw/interview.mp4
#
# Produces:
#   work/probe.json      technical metadata
#   work/scenes.txt      shot-boundary timestamps
#   work/thumbs/*.jpg    one frame per shot (Claude CAN look at these)
#   work/transcript.json word-level timestamps  (via transcribe.py)

set -euo pipefail

SRC="${1:?usage: ingest.sh <video>}"
SCENE_THRESHOLD="${SCENE_THRESHOLD:-0.3}"

mkdir -p work/thumbs
rm -f work/thumbs/*.jpg

echo "==> probing"
ffprobe -v error -print_format json -show_format -show_streams "$SRC" \
  > work/probe.json
python3 - "$SRC" <<'PY'
import json, sys
d = json.load(open("work/probe.json"))
v = next((s for s in d["streams"] if s["codec_type"] == "video"), {})
a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
print(f"    {v.get('width')}x{v.get('height')} @ {v.get('r_frame_rate')} "
      f"{v.get('codec_name')}")
print(f"    duration {float(d['format']['duration']):.1f}s")
print(f"    audio    {a['codec_name'] if a else 'NONE'}")
PY

echo "==> detecting scene boundaries (threshold $SCENE_THRESHOLD)"
# ffmpeg >=6 renamed pkt_pts_time -> pts_time; try the modern name first.
scene_times() {
  ffprobe -v error -of csv=p=0 \
    -f lavfi "movie=$SRC,select=gt(scene\,$SCENE_THRESHOLD)" \
    -show_entries "frame=$1" 2>/dev/null | grep -E '^[0-9]' || true
}
scene_times pts_time > work/scenes.txt
if [ ! -s work/scenes.txt ]; then
  scene_times pkt_pts_time > work/scenes.txt
fi
echo "0.0" | cat - work/scenes.txt | sort -n -u > work/scenes.tmp
mv work/scenes.tmp work/scenes.txt
echo "    $(wc -l < work/scenes.txt) shots"

echo "==> extracting thumbnails"
i=0
while read -r t; do
  i=$((i+1))
  printf -v n "%03d" "$i"
  ffmpeg -nostdin -v error -ss "$t" -i "$SRC" -frames:v 1 \
    -vf scale=480:-1 "work/thumbs/${n}_at_${t}s.jpg" -y 2>/dev/null || true
done < work/scenes.txt
echo "    $(ls work/thumbs | wc -l) thumbnails -> work/thumbs/"

echo "==> transcribing"
if python3 -c "import faster_whisper" 2>/dev/null; then
  python3 scripts/transcribe.py "$SRC"
else
  echo "    SKIPPED - run: pip install faster-whisper"
fi

echo
echo "Ingest complete. Point Claude at work/ and describe the edit you want."

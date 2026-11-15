#!/usr/bin/env bash
# ./scripts/get_model.sh small     (tiny|base|small|medium|large-v3)
set -euo pipefail
M="${1:?usage: get_model.sh <size>}"
D="models/faster-whisper-$M"
BASE="https://huggingface.co/Systran/faster-whisper-$M/resolve/main"
mkdir -p "$D"; cd "$D"
for f in config.json model.bin tokenizer.json vocabulary.txt preprocessor_config.json; do
  echo "--> $f"
  curl -fL --http1.1 --retry 100 --retry-all-errors --retry-delay 3 \
       -C - --progress-bar -O "$BASE/$f" || echo "    (skipped - not in this repo)"
done
echo; ls -lh

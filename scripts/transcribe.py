#!/usr/bin/env python3
"""
transcribe.py - Word-level transcript. The highest-value artifact in the kit.

Most edit instructions are semantic ("cut where I stumble over the intro").
A word-level transcript is what turns that sentence into a timestamp.

    pip install faster-whisper
    python3 scripts/transcribe.py raw/interview.mp4 [--model large-v3] [--lang ar]

Outputs:
    work/transcript.json   segments + per-word timings  (for Claude)
    work/transcript.txt    plain reading copy           (for you)
    work/captions.srt      burn-in ready                (for apply_edl.py)
"""

import argparse
import json
from pathlib import Path

from faster_whisper import WhisperModel


def ts(seconds):
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--model", default="medium",
                    help="tiny|base|small|medium|large-v3")
    ap.add_argument("--lang", default=None,
                    help="ISO code, e.g. en or ar. Omit to auto-detect.")
    args = ap.parse_args()

    Path("work").mkdir(exist_ok=True)

    model = WhisperModel(args.model, device="auto", compute_type="auto")
    segments, info = model.transcribe(
        args.source, language=args.lang, word_timestamps=True, vad_filter=True
    )

    data, lines, srt = [], [], []
    for i, seg in enumerate(segments, 1):
        words = [
            {"w": w.word.strip(), "start": round(w.start, 3),
             "end": round(w.end, 3), "conf": round(w.probability, 3)}
            for w in (seg.words or [])
        ]
        data.append({
            "id": i,
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "words": words,
        })
        lines.append(f"[{seg.start:7.2f}] {seg.text.strip()}")
        srt.append(f"{i}\n{ts(seg.start)} --> {ts(seg.end)}\n{seg.text.strip()}\n")
        print(f"    [{seg.start:7.2f}] {seg.text.strip()[:70]}")

    Path("work/transcript.json").write_text(json.dumps(
        {"language": info.language, "duration": info.duration,
         "segments": data}, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("work/transcript.txt").write_text("\n".join(lines), encoding="utf-8")
    Path("work/captions.srt").write_text("\n".join(srt), encoding="utf-8")

    print(f"\n    language={info.language}  segments={len(data)}")
    print("    -> work/transcript.json / .txt / captions.srt")


if __name__ == "__main__":
    main()

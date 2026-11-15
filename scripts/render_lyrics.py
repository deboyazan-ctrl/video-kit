#!/usr/bin/env python3
"""
render_lyrics.py <lyrics.json> --out DIR [--size W H] [--preview FRAME.png]

Draw each lyric cue to a transparent PNG, ready to be overlaid on the video.

This exists because the ffmpeg on this machine was built without libass and
without freetype: no subtitles filter, no ass filter, no drawtext. Only overlay
survives, so the type has to be set here and handed over as pictures.

Arabic will not render correctly by simply passing the string to a font. Its
letters change shape by position and the line runs right to left, and a plain
draw call produces disconnected letters in the wrong order. arabic_reshaper
picks the contextual forms and python-bidi puts them in visual order; both have
to run, in that order, before anything is drawn.
"""
import argparse
import json
from pathlib import Path

import arabic_reshaper
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont

AR_FONT = "/System/Library/Fonts/GeezaPro.ttc"
FR_FONT = "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf"


def shape(text):
    return get_display(arabic_reshaper.reshape(text))


def fit(text, path, size, max_w, floor=0.62):
    """Shrink until the line fits the frame.

    A long line set at the same size as a short one runs off both sides, and
    the two worst offenders here are the fifth French line and the first Arabic
    one. Shrinking is less ugly than wrapping: two lines of French under two of
    Arabic crowds the bottom of a portrait frame.
    """
    f = ImageFont.truetype(path, size)
    d = ImageDraw.Draw(Image.new("L", (10, 10)))
    while d.textbbox((0, 0), text, font=f)[2] > max_w and size > int(size * floor):
        size -= 2
        f = ImageFont.truetype(path, size)
    return f


def draw_line(img, text, font, cy, fill, shadow):
    d = ImageDraw.Draw(img)
    w = d.textbbox((0, 0), text, font=font)[2]
    x = (img.width - w) // 2
    # A soft drop shadow, not an outline: the reference has the type sitting on
    # a dark photograph and an outline reads as a sticker.
    for dx, dy, a in shadow:
        d.text((x + dx, cy + dy), text, font=font, fill=(0, 0, 0, a))
    d.text((x, cy), text, font=font, fill=fill)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lyrics")
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", type=int, nargs=2, default=[1080, 1920])
    ap.add_argument("--ar-size", type=int, default=50)
    ap.add_argument("--fr-size", type=int, default=72)
    ap.add_argument("--baseline", type=int, default=1660,
                    help="top of the Arabic line, in pixels from the top")
    ap.add_argument("--gap", type=int, default=74, help="Arabic top to French top")
    ap.add_argument("--margin", type=int, default=60)
    ap.add_argument("--preview", help="a frame to composite the first cue onto")
    args = ap.parse_args()

    W, H = args.size
    cues = json.loads(Path(args.lyrics).read_text())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    max_w = W - 2 * args.margin
    shadow = [(0, 3, 160), (2, 2, 110), (-2, 2, 110), (0, 0, 90)]

    index = []
    for i, cue in enumerate(cues):
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if cue.get("ar"):
            t = shape(cue["ar"])
            draw_line(img, t, fit(t, AR_FONT, args.ar_size, max_w), args.baseline,
                      (255, 255, 255, 255), shadow)
        if cue.get("fr"):
            draw_line(img, cue["fr"], fit(cue["fr"], FR_FONT, args.fr_size, max_w),
                      args.baseline + args.gap, (255, 255, 255, 255), shadow)
        p = out / f"cue{i:03d}.png"
        img.save(p)
        index.append({"file": p.name, "start": cue["start"], "end": cue["end"]})
        print(f"  {p.name}  {cue['start']:.2f}-{cue['end']:.2f}  {cue.get('fr','')[:40]}")

    (out / "index.json").write_text(json.dumps(index, indent=1))
    print(f"\n-> {out}/  ({len(index)} cues)")

    if args.preview:
        base = Image.open(args.preview).convert("RGBA")
        first = Image.open(out / index[0]["file"])
        Image.alpha_composite(base, first).convert("RGB").save(out / "preview.png")
        print(f"-> {out}/preview.png")


if __name__ == "__main__":
    main()

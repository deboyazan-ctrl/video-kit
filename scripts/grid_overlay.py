#!/usr/bin/env python3
import sys
from PIL import Image, ImageDraw, ImageFont

src = sys.argv[1]
out = sys.argv[2] if len(sys.argv) > 2 else "work/edit/grid.png"

im = Image.open(src).convert("RGB")
w, h = im.size
d = ImageDraw.Draw(im, "RGBA")

try:
    font = ImageFont.load_default(size=22)
except TypeError:
    font = ImageFont.load_default()

STEP, MAJOR = 50, 100

for x in range(0, w, STEP):
    major = x % MAJOR == 0
    d.line([(x, 0), (x, h)], fill=(255, 0, 0, 200) if major else (255, 150, 150, 110),
           width=2 if major else 1)

for y in range(0, h, STEP):
    major = y % MAJOR == 0
    d.line([(0, y), (w, y)], fill=(0, 190, 255, 200) if major else (150, 210, 255, 110),
           width=2 if major else 1)

def label(x, y, text, color):
    bb = d.textbbox((x, y), text, font=font)
    d.rectangle([bb[0] - 3, bb[1] - 2, bb[2] + 3, bb[3] + 2], fill=(0, 0, 0, 190))
    d.text((x, y), text, fill=color, font=font)

for x in range(0, w, MAJOR):
    label(min(x + 4, w - 55), 4, f"X{x}", (255, 80, 80))
for y in range(0, h, MAJOR):
    label(4, y + 4, f"Y{y}", (90, 220, 255))

im.save(out)
print(f"{w}x{h} -> {out}")

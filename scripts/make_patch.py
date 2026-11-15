#!/usr/bin/env python3
"""
make_patch.py <edited.png> <x1> <y1> <x2> <y2> [out]

Keeps only the given rectangle from the edited frame; everything else becomes
transparent. Canvas size is preserved so the patch stays aligned with the
reference frame when fed into Fusion's PlanarTransform.

A soft feathered edge is applied so the patch blends into the paper instead of
showing a hard rectangular seam.
"""
import sys
from PIL import Image, ImageDraw, ImageFilter

src = sys.argv[1]
x1, y1, x2, y2 = (int(v) for v in sys.argv[2:6])
out = sys.argv[6] if len(sys.argv) > 6 else "work/resolve/patch.png"
FEATHER = 18

im = Image.open(src).convert("RGBA")
w, h = im.size

mask = Image.new("L", (w, h), 0)
d = ImageDraw.Draw(mask)
d.rectangle([x1 + FEATHER, y1 + FEATHER, x2 - FEATHER, y2 - FEATHER], fill=255)
mask = mask.filter(ImageFilter.GaussianBlur(FEATHER))

im.putalpha(mask)
im.save(out)
print(f"patch {x2-x1}x{y2-y1} at ({x1},{y1}) on {w}x{h} canvas -> {out}")

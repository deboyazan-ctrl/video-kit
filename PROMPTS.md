# Image-model prompts that worked

Every clause here is in because leaving it out cost a round. Gemini was used
throughout; the behaviour notes are about it specifically.

Send a tight upscaled crop, never the whole frame — `new_scene.py` writes one.
Expect to check the result against `prepare_plate.py` before believing it.

## The opening every prompt needs

```
This image is a CLOSE-UP DETAIL cropped out of a larger photograph. The <paper
/ surface> fills the whole image and continues past all four edges. There is no
edge, border, corner or background in it, and you must not draw one.
```

Without this it draws a deckled border down one side of a crop that was all
paper. With it, that stopped happening.

## The closing every prompt needs

```
Everything else stays exactly as it is: <name the things - the paper and its
texture, the lighting, the shadows, the objects in front>.

Do not move, crop, rotate, zoom or reframe anything. Return the image at the
same size, with no added text, watermark or border.
```

It will still rescale and shift by a few pixels. `align_cards.py` solves that
out; the instruction reduces how often you need to.

## Replacing printed text

```
Replace the line  <old>  with:  <new>

Keep the same font, size, weight and letter spacing, the same line position and
the same baseline.

Write it WITHOUT diacritics or vowel marks. Copy the text exactly as given,
adding no word that is not written above.
```

The diacritics clause is not optional for Arabic — it added full tashkeel to a
card that had none. The "adding no word" clause is because it invented one.

When a line is long, do not ask for it "smaller". Say how to break it:

```
...with these FOUR lines, each centred:
  <line 1>
  <line 2>
  ...
Every one of these lines must stay within the same left and right margins as
<some other block on the card>. Do NOT let any line run wider than that block.
```

A line run to the full width of the crop has its ends outside the plate, where
no mask can reach them.

## Deleting a line

```
Delete this line completely, leaving clean paper where it was:
  <line>
Do not move the lines above or below it to close the gap.
```

Then delete it again in the composite with a blur erase, and keep the patch off
that band — see `erase_first` in LESSONS.md. The plate's idea of clean paper is
brighter than the real paper, so relying on the plate alone prints the line
back as a bright ghost.

## Handwriting and beadwork

```
Keep the same handwritten calligraphy style, the same ink colour and weight,
the same baseline and the same slant.
```

For beads, say what they are made of or it will print them:

```
The new words must be made of the SAME crystal beads: the same individual bead
shapes, the same size, the same sewn-on relief with its own small shadows, the
same warm gold highlights. Not printed, not painted, not embroidered thread —
beads.
```

Even so, beaded Arabic failed twice on one card. Carrying an approved plate
from another shot with `align_cards.py` worked where a new round did not.

## Replacing an object

A first attempt returns a 3D render pasted on: flat, hard-edged, sharp against
a soft background, unlit. Everything below is there to prevent that.

```
FIRST: completely remove the <old object>. No part of it, and no shadow or dark
smudge where it was, may remain.

THEN: place <new object> resting on <surface>.

This must look like an object PHOTOGRAPHED in this scene, not a 3D model
dropped onto it. That means:
  - the same shallow focus as the rest of the frame: its far edge should fall
    slightly out of focus exactly as <something behind it> does
  - lit from <direction> by <the light source>, so one side is warm and the
    other falls into shadow, never flat grey
  - real material: <describe the weave, the grain, the creases>
  - its own contact shadow pressing into <the surface>
  - the same film grain and slight softness as the surrounding photograph

Size it so it sits <where> the way <the old object> did, no larger.
```

The second attempt with this was convincing. It also re-rendered the whole crop
sharper than the footage, so the patch had to be confined to the object's own
footprint and the surroundings left as filmed.

## Correcting a round

Name the mistake. Vague re-asks repeat it.

```
The first word of the first line begins with BAA, one dot below, not YAA
with two. Your last attempt used the wrong letter, which is a spelling
mistake. Name the letter, not just the word - "check the spelling" gets the
same mistake back.
```

```
A previous attempt ran one line the full width of the image and both its ends
were lost.
```

```
Your last attempt moved the paper by 4 to 8 pixels; it has to land back
precisely, because this is composited over the original footage.
```

Re-send the ORIGINAL crop, not the model's own output — editing its output
compounds whatever it got wrong.

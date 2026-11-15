# What this project learned the hard way

Read this before starting another video. Every entry cost at least an hour,
most cost a day, and several were found by the client rather than by us.

<!-- index:start -->

## Index, by what you see

- [A dissolve curve that never reaches 1 has not measured a dissolve](#a-dissolve-curve-that-never-reaches-1-has-not-measured-a-dis) — *Measuring*
- [Correlation does not prove geometry](#correlation-does-not-prove-geometry) — *Measuring*
- [A residual in levels is not a geometry test](#a-residual-in-levels-is-not-a-geometry-test) — *Measuring*
- [A registration number without its correlation beside it means nothing](#a-registration-number-without-its-correlation-beside-it-mean) — *Measuring*
- [Deleting text leaves a BRIGHT ghost; replacing it usually does not](#deleting-text-leaves-a-bright-ghost-replacing-it-usually-doe) — *Patching*
- [Subtraction is only exact on the reference frame](#subtraction-is-only-exact-on-the-reference-frame) — *Patching*
- [A blur removes detail, not a brightness offset](#a-blur-removes-detail-not-a-brightness-offset) — *Patching*
- [Erase rects belong to the plate they were measured on](#erase-rects-belong-to-the-plate-they-were-measured-on) — *Patching*
- [An erase has to fade with the shot](#an-erase-has-to-fade-with-the-shot) — *Patching*
- [A mask grown after it is clipped to its box reaches past the box](#a-mask-grown-after-it-is-clipped-to-its-box-reaches-past-the) — *Patching*
- ["Hugging the strokes" can quietly be a rectangle](#hugging-the-strokes-can-quietly-be-a-rectangle) — *Patching*
- [One homography does not fit a sheet that bends](#one-homography-does-not-fit-a-sheet-that-bends) — *Patching*
- [Given a whole frame it re-typesets; given a tight crop it reproduces](#given-a-whole-frame-it-re-typesets-given-a-tight-crop-it-rep) — *Image models*
- [It will not stay inside the frame unless told, and often not then](#it-will-not-stay-inside-the-frame-unless-told-and-often-not-) — *Image models*
- [A line set to the full width of the crop cannot be patched](#a-line-set-to-the-full-width-of-the-crop-cannot-be-patched) — *Image models*
- [Check the spelling every round](#check-the-spelling-every-round) — *Image models*
- [Not every fix needs a round](#not-every-fix-needs-a-round) — *Image models*
- [A git checkout or merge makes render_chain re-render everything](#a-git-checkout-or-merge-makes-renderchain-re-render-everythi) — *Working*
- [Verify every render's frame count before anything reads it](#verify-every-renders-frame-count-before-anything-reads-it) — *Working*
- [Two guards with different thresholds will eventually disagree](#two-guards-with-different-thresholds-will-eventually-disagre) — *Working*
- [The picture is the judge, not the percentage](#the-picture-is-the-judge-not-the-percentage) — *Working*
- [Carry an accepted edit across shots instead of regenerating it](#carry-an-accepted-edit-across-shots-instead-of-regenerating-) — *Working*

<!-- index:end -->

## Measuring

**A dissolve curve that never reaches 1 has not measured a dissolve.**
`analyse_shot.py` mis-read the incoming dissolve on two separate shots, once
returning 844-907 for a transition that is really 826-850, with a curve that
peaked at 0.15. It happens when the two shots do not differ strongly enough in
the patches it picks. Measure by hand against a frame deep inside each shot,
on whatever actually differs - the typewriter's orange body, the invitation's
white paper - and rescale the ends onto 0 and 1.

**Correlation does not prove geometry.** A tracker reported 0.95-0.97 through
frames where the printed date was 9 px out at one end and 1.3 px at the other.
A shear preserves most of the structure, so it correlates well. Measure the
error in PIXELS, on a feature you can find independently, before trusting a
track.

**A residual in levels is not a geometry test.** The "warp a frame back onto
the reference and difference it" test grew smoothly with distance from the
reference on two different tracks, which looked exactly like accumulating
drift. It was candle flicker. Pixels settled it in one run.

**Any correction measured against the reference frame must return 1.000 at the
reference frame.** The warp is the identity there. `measure_ink_gain.py` warns
when a neighbouring frame drifts more than 6% because a confident 0.80, which
was really warp softening, once made a ghost worse when applied.

**A registration number without its correlation beside it means nothing.** A
check box reading "10.6 px" at correlation 0.17 is sitting on blurred
background and measuring noise. Three boxes, and discard the ones that do not
correlate.

## Patching

**Deleting text leaves a BRIGHT ghost; replacing it usually does not.** Where
new ink covers old, a small error is hidden. Where text is removed outright -
or where the new line is shorter than the old and its ends are exposed - the
plate's paper is brighter than the real paper underneath, so subtracting takes
away more than is there and the old words print back lighter than the page.
The client found three of these in one pass.

**Subtraction is only exact on the reference frame.** That is the root of the
ghosting above. The cure is `erase_first`: blur the old lettering away per
frame, which works off each frame's own paper and is therefore exact
everywhere, and give the patch a baseline that already has it blurred
(`erase_plate.py`) so it has nothing left to remove.

**A blur removes detail, not a brightness offset.** Blurring over an existing
bright ghost spreads it into a soft smudge and does not clear it. The blur has
to be the thing that removes the original ink, not a patch over the damage.

**Erase rects belong to the plate they were measured on.** Reusing rects from a
previous plate cut the first two letters off a word, because that round set the line at
217-476 where the previous one had 256-466. Re-measure the old line and the new
line every time a plate changes.

**An erase has to fade with the shot.** At full strength through a dissolve it
strips the old text out while the patch is still at 20%, leaving grey smudges;
after the shot ends it keeps scrubbing whatever the next shot puts under those
coordinates. Scale it by the blend curve.

**A mask grown after it is clipped to its box reaches past the box.** Measured
at 1.00 nine pixels below a box ending at y=385. If the plate stops at the box
edge, the patch ends under a mask still at half strength - a step in the
difference, which no amount of feathering the mask will soften. Make the
carried region larger than the core box.

**"Hugging the strokes" can quietly be a rectangle.** At threshold 10 the mask
covered 94% of its box: regenerated paper grain stands out from its own local
blur nearly everywhere. Check the percentage, not the name of the option.

**One homography does not fit a sheet that bends.** Solving on a whole envelope
gave 0-1.4 px at the wax seal and 20-30 px at the lettering. Solve on the part
being patched.

**A scorer that rejects out-of-frame samples will shear the quad instead of
moving it.** Where the surface runs off the edge of the picture, score on the
samples that do land inside. This was worth 9 px.

## Image models

**Given a whole frame it re-typesets; given a tight crop it reproduces.** But a
crop that is all paper with no edge in it reads as a photograph of a card, and
it will draw an edge. Say "this is a close-up detail cropped from a larger
photograph, the paper continues past all four edges, do not draw one".

**It will not stay inside the frame unless told, and often not then.** Two
plates came back rescaled and shifted by 4-9 px despite explicit instructions.
`align_cards.py` can solve that out afterwards - a scale-and-shift search
scored on features that are supposed to be unchanged, excluding the lettering.

**A line set to the full width of the crop cannot be patched.** Its ends sit
outside the plate, where no mask can reach. Ask for the text to be split across
more lines, within the card's own margins - not merely "smaller".

**Check the spelling every round.** Over four rounds on one card it invented a
word that was in no brief, added diacritics the original had none of,
misspelt another word by a single letter, and printed the date twice. Each
round fixed one thing and broke
another. Decide what the plate must contain, then verify it against that list.

**Not every fix needs a round.** A duplicated line that lands on blank paper
can be erased. A plate that is geometrically wrong can be realigned. Ask for a
new plate only for what is actually in the lettering.

## Working

**A git checkout or merge makes render_chain re-render everything**  
  Looks like: Something in the recipes changed and the outputs are stale  
  Settled by: Compare the recipe's mtime against the render's - git rewrites file times on checkout, so every recipe looks newer than its output even when its contents are identical  
  Do: Nothing. The freshness check errs towards re-rendering, which is the safe direction. If the renders are known good, pass --from to start past them.  
  Seen in: video-editing-kit, after merging save-thesis-project  
  *2026-11-15*


**Verify every render's frame count before anything reads it.** A composite can
die partway and leave a playable file. One did, with 289 frames instead of
2089, and eleven scenes were built on top of it before anyone noticed.
`render_chain.py` now counts.

**Two guards with different thresholds will eventually disagree.** A mask
peaking at 0.005 passed a `> 0.001` check and handed an empty array to the
code behind it.

**The picture is the judge, not the percentage.** `check_coverage.py` reports a
share of uncovered ink, and paper grain inflates it; the red-marked image it
writes is what finds the stroke that fell between two boxes.

**Carry an accepted edit across shots instead of regenerating it.** The same
card appears in two shots; `align_cards.py --replace` moved the approved plate
into the second one and kept them identical. Replacing a region needs far less
precision than differencing it, because the old content is buried rather than
cancelled - but it still needs an accurate track.
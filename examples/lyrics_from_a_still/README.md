# A lyric video from one still photograph

A worked example, kept for the method rather than the result. The media is not
here; the two small files beside this are the whole record of how it was cut.

## The job

A five-second WhatsApp video of a single photograph, no audio, and a song. The
client wanted the photograph held still with the song over it and the words
appearing underneath, in French with an Arabic translation beneath.

## What it needed

**Freeze, do not loop.** The source was a Live Photo with about 0.8 levels of
drift across its 150 frames. Looping it would have shown a jump; holding one
frame for the whole duration shows nothing.

**The ffmpeg on that machine had no libass and no freetype.** No `subtitles`,
no `ass`, no `drawtext` - and `-h filter=subtitles` reported the filter as
present right up until it was run. So the type is set in Pillow by
`scripts/render_lyrics.py`, one transparent PNG per cue, and
`scripts/burn_lyrics.py` overlays each one for its own stretch of time.

**Arabic will not draw correctly from a plain string.** arabic-reshaper picks
the contextual letterforms and python-bidi puts them in visual order. Both,
in that order, before the font is asked for anything.

**Cuts belong on silence, not on commas.** The client asked to cut "after the
first Oui, around second 15". The gap after the first oui is 40 ms and
shallow; the gap after the second is 100 ms with the level down to 0.08. The
cut went there - `edit.json` records both and why. The same measurement, an
RMS envelope at 20 ms, placed every phrase break in the subtitles: the breaks
are where the singer breathes, which is not always where the punctuation is.

**Timings came from transcription, not from counting.** faster-whisper on the
cut segment returns a start time per sung phrase; the client's own lines map
onto those in order. The word-timestamp stage crashes on short musical
excerpts, after the segment times have already been printed - which is all
that is needed.

## The files

- `lyrics_v003.json` - the cues, each with its own start, end, French and
  Arabic. This is the only file that changes when a line moves.
- `edit.json` - the two pieces of song taken, with the evidence for each cut
  point and the crossfade between them.

# Video editing kit

## What never goes in this repository

This repository holds the method. It does not hold anybody's work.

No footage, no photographs, no audio, no image-model plates. No names, no
dates, no venues, no phone numbers - not in a recipe, not in a note, not in an
example, not in a lesson. If a lesson needs an example to make sense, describe
the shape of the problem: "the plate misspelt a word by one letter", not the
word. Every lesson in `LESSONS.md` survives that treatment and none of them is
weaker for it.

A job lives in its own folder, outside this one, with its own `raw/`, `work/`
and `out/`, and its own git if it wants one. `jobs/`, `raw/`, `work/` and
`out/` are ignored here so that a job opened inside this folder by mistake
cannot be committed by accident.

The first version of this repository had sixteen of a client's cards in it,
with her full name, the date of her defence and the hall it was in, because
the plates were irreplaceable and keeping them seemed obviously right. They
were irreplaceable and it was still wrong. Knowledge is shared; the work is
not.


## The loop

This kit is meant to get better with each video, and that only happens if both
ends of the loop are actually done.

**At the start of a job**, read `LESSONS.md`. It opens with an index by
symptom - by what you SEE - because that is all you have when you are stuck.
Search it before diagnosing anything. Most of the time lost on the first
project was spent inside a plausible wrong diagnosis that was already written
down by the time it happened again.

**At the end of a job, and after any argument that a measurement settled**,
write what was learned:

```bash
.venv/bin/python3 scripts/capture_lesson.py
```

It asks four questions and appends the answer. The symptom, the wrong
conclusion you would reach first, the number that settles it, and what to do.
A lesson without a measurement in it is an anecdote and will not help anybody.

Do not wait until the end to capture something that cost an hour. The first
project produced twenty-eight lessons and all of them were written from memory
on the last day, which is why the index had to be built afterwards.

## Read these first

- `LESSONS.md` - what this kit learned the hard way. Every entry cost at least
  an hour and several were found by the client rather than by us. Read it
  before touching a new video; it will save you a day.
- `SETUP.md` - what the kit needs to run, and how to tell whether this
  machine's ffmpeg can burn subtitles (several builds say they can and cannot).
- `PROMPTS.md` - the image-model wording that worked. Every clause in it is
  there because leaving it out cost a round.

## The tools

Measuring a shot: `analyse_shot.py` (boundaries, dissolve curves, reference
frame candidates), `transcribe.py` (phrase timings).

Preparing a plate: `new_scene.py` opens a scene folder and cuts the crop,
`prepare_plate.py` tone-fits what comes back and reports registration,
`align_cards.py` carries an accepted edit from one shot into another.

Solving the surface: `solve_track.py` (4-corner homography),
`track_sheet.py` (translation, from an ink mask, where there is nothing else
to lock onto).

Compositing: `composite_scene.py` renders one scene from its recipe,
`render_chain.py` renders them all in order and verifies every output,
`erase_plate.py` builds the baseline for an erase-first scene.

Checking: `check_coverage.py` (is every old mark inside a patch box),
`measure_ink_gain.py` (is the removal the right strength),
`review_scenes.py` (ghosts in a finished render - run this before delivering).

Cutting: `apply_edl.py`, with `tests/test_apply_edl.py`.

Housekeeping: `prune_grad.py` clears intermediates nothing points at.

Lyrics and subtitles: `render_lyrics.py` sets type in Pillow (including
correctly shaped Arabic) and `burn_lyrics.py` overlays it - for machines whose
ffmpeg has no libass.

## How a scene is recorded

One folder per scene under `work/scenes/`, holding `recipe.json` with every
measured number and the reasoning for each decision, and `notes.md` with what
was tried and failed. `work/chain.json` holds the order they composite in.
The recipes are the record: the renders are rebuilt from them, not kept.


## What you can and cannot do

You **cannot watch video**. Never claim to have viewed footage, and never
guess at a timestamp. Everything you know about this video comes from:

- `work/transcript.json` - word-level timings. Your primary source.
- `work/scenes.txt` - shot boundaries in seconds.
- `work/thumbs/*.jpg` - one frame per shot. You **can** read these images.
- `work/probe.json` - resolution, fps, codecs, duration.

If the user asks for something that needs visual judgement you can't get from
a thumbnail ("is this shot in focus", "did the cut land on the beat"), say so
and ask them to check the proxy render.

## The workflow

1. Read the artifacts in `work/`.
2. Write or amend an EDL at `edl/vNNN.json`. **This is your only output.**
3. Run `python3 scripts/apply_edl.py edl/vNNN.json --proxy`.
4. Tell the user which proxy to review. Wait for feedback.
5. On feedback, copy to the next version and amend. Never edit a rendered EDL
   in place - version history is the undo mechanism.
6. Final render (no `--proxy`) only when the user explicitly approves.

## Hard rules

- `raw/` is **read-only**. Never modify, move, or delete a source file.
- Never overwrite a render. Bump the version number.
- **Do not rewrite `scripts/apply_edl.py`.** It is tested. If an edit seems
  impossible to express in the EDL schema, say so and propose a schema
  extension - don't route around it with an ad-hoc ffmpeg command.
- Never run a raw `ffmpeg` command that writes into `out/`. Renders go through
  the executor so every output is reproducible from a committed EDL.
- Cite a timestamp for every cut you propose, and quote the transcript text at
  that timestamp so the user can verify you targeted the right moment.

## EDL schema

```json
{
  "source": "raw/input.mp4",
  "output": "out/v001.mp4",
  "operations": [
    {"type": "cut",     "start": 12.4, "end": 18.9, "reason": "false start"},
    {"type": "speed",   "start": 60.0, "end": 90.0, "factor": 1.5},
    {"type": "overlay", "asset": "assets/lower_third.mov",
                        "start": 30.0, "end": 35.0, "x": 40, "y": "H-h-80"},
    {"type": "captions","file": "work/captions.srt"},
    {"type": "audio_gain", "db": -3}
  ]
}
```

Timing reference frames - this trips people up:

- `cut` and `speed` timestamps refer to the **source** timeline.
- `overlay` timestamps refer to the **output** timeline (after cuts applied).

## Encoding defaults

Final: H.264, CRF 18, preset slow, yuv420p, AAC 192k.
Proxy: 720p, CRF 28, preset veryfast.
Change these only if the user asks.

## Transcription

Models are stored locally. Always pass an explicit path:

    python3 scripts/transcribe.py <video> --model models/faster-whisper-tiny

Never pass a bare model name like "medium" - that triggers a Hub download
which hangs on this machine. Fetch new sizes with ./scripts/get_model.sh <size>.

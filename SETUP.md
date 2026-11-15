# Setting this up somewhere else

Everything the kit knows lives in this repository: the scripts, the per-scene
recipes, `LESSONS.md`, `PROMPTS.md`. What does NOT live here is the media and
the speech models, both too large for git and both reproducible.

## What you need

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./scripts/get_model.sh small        # ~460 MB, into models/
```

And ffmpeg, from anywhere - homebrew, apt, a static build.

## Check your ffmpeg before you trust it

The build on the machine this was written on reports the `subtitles`, `ass`
and `drawtext` filters in `-h filter=...` and then fails with "No such filter"
when you actually run them. It was compiled without libass and without
freetype. Test by running, not by asking:

```bash
ffmpeg -v error -f lavfi -i color=c=black:s=64x64:d=0.1 \
       -vf "drawtext=text=x:fontsize=40" -frames:v 1 -f null -
```

If that fails, you cannot burn subtitles with ffmpeg and you need
`scripts/render_lyrics.py`, which sets the type in Pillow and hands it over as
transparent PNGs for `scripts/burn_lyrics.py` to overlay. If it succeeds, an
ASS file through the `subtitles` filter is simpler and you can skip both.

## Fonts

`render_lyrics.py` points at macOS system fonts:

- Arabic: `/System/Library/Fonts/GeezaPro.ttc`
- Latin: `/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf`

On anything else, edit the two constants at the top of that file. Any font
with Arabic coverage will do - the shaping is done before the font is asked to
draw, by arabic-reshaper and python-bidi.

## Where the project's own media goes

`raw/` is read-only source. `work/grad/` holds the chain's intermediate
renders - it reached 80 GB on one project; `scripts/prune_grad.py` clears what
nothing points at. `out/` holds finished renders, which are never overwritten.
All three are outside git.

## Picking it up again

Read `LESSONS.md` first. Then `work/chain.json` for the order scenes composite
in, and any `work/scenes/*/recipe.json` for how one was actually done - every
measured number is in there with the reasoning beside it.

```bash
.venv/bin/python3 scripts/render_chain.py --dry-run
```

tells you what the chain would do without touching anything.

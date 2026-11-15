#!/usr/bin/env python3
"""Tests for the EDL executor's timeline model and filtergraph.

No ffmpeg is needed: these check the graph the executor builds, which is where
the timeline reasoning lives. Run with  python3 tests/test_apply_edl.py
"""
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "apply_edl", Path(__file__).resolve().parent.parent / "scripts" / "apply_edl.py")
edl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edl)

FAILED = []


def check(name, got, want):
    if got != want:
        FAILED.append(f"{name}\n     got  {got!r}\n     want {want!r}")


def check_in(name, needle, haystack):
    if needle not in haystack:
        FAILED.append(f"{name}\n     missing {needle!r}\n     in {haystack!r}")


def exits(name, fn):
    try:
        fn()
    except SystemExit:
        return
    FAILED.append(f"{name}\n     expected it to stop, it did not")


# --- the timeline model ------------------------------------------------
cut = [{"type": "cut", "start": 0.0, "end": 1.0},
       {"type": "cut", "start": 54.0, "end": 63.0}]
segs = edl.build_segments(69.66, cut)
check("two cuts leave two pieces", segs, [(1.0, 54.0, 1.0), (63.0, 69.66, 1.0)])

sp = [{"type": "speed", "start": 10.0, "end": 20.0, "factor": 2.0}]
check("a speed op splits without dropping",
      edl.build_segments(30.0, sp), [(0.0, 10.0, 1.0), (10.0, 20.0, 2.0), (20.0, 30.0, 1.0)])

check("speed shortens its piece",
      [round(d, 4) for d in edl.segment_durations(edl.build_segments(30.0, sp))],
      [10.0, 5.0, 10.0])

# --- the old path is untouched -----------------------------------------
g, v, a, _ = edl.build_filtergraph(segs, cut, has_audio=True)
check_in("picture and sound still concat together", "concat=n=2:v=1:a=1[vcat][acat]", g)
check("video label unchanged", v, "[vcat]")
check("audio label unchanged", a, "[acat]")
check_in("audio is still cut with the picture", "atrim=start=63.0000:end=69.6600", g)

# --- audio: keep --------------------------------------------------------
keep = [{"type": "cut", "start": 0.0, "end": 1.0},
        {"type": "cut", "start": 54.0, "end": 63.0, "audio": "keep"}]
g, v, a, _ = edl.build_filtergraph(segs, keep, has_audio=True)
# 53.00 + 6.66 of picture, and the sound runs that long from the first frame
check_in("sound is one unbroken run", "[0:a]atrim=start=1.0000:end=60.6600", g)
check_in("picture concats on its own", "concat=n=2:v=1:a=0[vj1]", g)
check("audio comes from the single run", a, "[acat]")
if "atrim=start=63.0000" in g:
    FAILED.append("audio: keep should not cut the sound at the join")

# --- dissolve -----------------------------------------------------------
dis = cut + [{"type": "dissolve", "at": 53.0, "duration": 0.8}]
g, v, a, _ = edl.build_filtergraph(segs, dis, has_audio=True)
check_in("the join cross-fades",
         "xfade=transition=fade:duration=0.8000:offset=52.2000", g)
check_in("sound cross-fades with it", "acrossfade=d=0.8000", g)

# a dissolve costs its own length
both = [{"type": "cut", "start": 0.0, "end": 1.0},
        {"type": "cut", "start": 54.0, "end": 63.0, "audio": "keep"},
        {"type": "dissolve", "at": 53.0, "duration": 0.8}]
g, v, a, _ = edl.build_filtergraph(segs, both, has_audio=True)
check_in("with a dissolve the sound run is shorter by its length",
         "[0:a]atrim=start=1.0000:end=59.8600", g)

# --- dissolves that should stop the render ------------------------------
exits("a dissolve in the middle of a shot stops",
      lambda: edl.resolve_dissolves(segs, [{"type": "dissolve", "at": 20.0}]))
exits("a dissolve longer than its piece stops",
      lambda: edl.resolve_dissolves(segs, [{"type": "dissolve", "at": 53.0,
                                            "duration": 9.0}]))

check("no dissolve means no joins keyed", edl.resolve_dissolves(segs, cut), {})

# --- report -------------------------------------------------------------
if FAILED:
    print(f"{len(FAILED)} failed:\n")
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("all checks passed")

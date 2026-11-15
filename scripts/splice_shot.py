#!/usr/bin/env python3
"""
splice_shot.py <original> <edited_shot> <start_frame> <output>

Splices an edited opening shot back into the full video and trims the head.
Measures the edited clip rather than assuming its length, so video and audio
stay locked even if the NLE export lost a few frames.
"""
import subprocess
import sys
from fractions import Fraction
from pathlib import Path


def probe(path, stream, entries):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream,
         "-show_entries", entries, "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True)
    return [l for l in out.stdout.strip().splitlines() if l]


def frame_count(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1",
         str(path)], capture_output=True, text=True, check=True)
    # ffprobe 9 can emit the value more than once; take the first line.
    return int(out.stdout.strip().splitlines()[0])


def main():
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    orig, edited, start_frame, out = (
        Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), Path(sys.argv[4]))

    for p in (orig, edited):
        if not p.exists():
            sys.exit(f"not found: {p}")
    if out.exists():
        sys.exit(f"{out} exists - bump the version")

    fps = float(Fraction(probe(orig, "v:0", "stream=r_frame_rate")[0]))
    total = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(orig)],
        capture_output=True, text=True, check=True).stdout.strip())

    n_edit = frame_count(edited)
    head = start_frame / fps
    join = n_edit / fps
    has_audio = bool(probe(orig, "a", "stream=index"))

    print(f"  fps            {fps:.3f}")
    print(f"  original       {total:.3f}s")
    print(f"  edited frames  {n_edit}  ({join:.3f}s of coverage)")
    print(f"  trim head      {start_frame} frames ({head:.3f}s)")
    print(f"  expected out   {total - head:.3f}s")
    print()

    if join <= head:
        sys.exit("edited clip is shorter than the head trim - nothing to splice")

    graph = (
        f"[1:v]trim=start={head:.5f}:end={join:.5f},setpts=PTS-STARTPTS[a];"
        f"[0:v]trim=start={join:.5f},setpts=PTS-STARTPTS[b];"
        f"[a][b]concat=n=2:v=1:a=0[v]"
    )
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-stats",
           "-i", str(orig), "-i", str(edited)]
    if has_audio:
        graph += f";[0:a]atrim=start={head:.5f},asetpts=PTS-STARTPTS[aud]"
    cmd += ["-filter_complex", graph, "-map", "[v]"]
    if has_audio:
        cmd += ["-map", "[aud]", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", "18",
            "-pix_fmt", "yuv420p", str(out)]

    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True)
    print(f"\n  -> {out}")


if __name__ == "__main__":
    main()

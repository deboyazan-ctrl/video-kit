#!/usr/bin/env python3
"""
apply_edl.py - Deterministic EDL executor.

Reads a JSON Edit Decision List and renders it with a single ffmpeg pass.
Claude edits the EDL. This script is tested code that Claude should not rewrite.

Usage:
    python3 scripts/apply_edl.py edl/v001.json
    python3 scripts/apply_edl.py edl/v001.json --proxy      # fast 720p preview
    python3 scripts/apply_edl.py edl/v001.json --dry-run    # print cmd, don't run
"""

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Timeline model
# --------------------------------------------------------------------------
# The source is a line from 0..duration. "cut" ops remove spans. "speed" ops
# retime spans. We slice the line at every op boundary, drop cut segments, and
# concat what survives. Overlay/caption timings refer to the OUTPUT timeline.
#
# Two options change how the pieces are joined back together:
#
#   {"type": "cut", ..., "audio": "keep"}   the cut applies to picture only and
#       the sound runs on unbroken. Taking a span out of a continuous music bed
#       breaks the phrase however carefully the two ends are faded, so the cure
#       is not to cut the sound at all: it is read as one run the length of the
#       finished picture. The tail of the track past that length is simply never
#       heard. Default is "cut", which is the old behaviour.
#
#   {"type": "dissolve", "at": <output time>, "duration": <seconds>}
#       cross-fades a join instead of butting the two pieces together. "at" is
#       on the OUTPUT timeline, like overlay and captions, and must land on a
#       join. A dissolve eats its own duration out of the running time.


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def build_segments(duration, operations):
    """Slice the source timeline into (start, end, rate) keep-segments."""
    cuts = [(o["start"], o["end"]) for o in operations if o["type"] == "cut"]
    speeds = [(o["start"], o["end"], float(o["factor"]))
              for o in operations if o["type"] == "speed"]

    # Every boundary that matters.
    marks = {0.0, duration}
    for s, e in cuts:
        marks.update((s, e))
    for s, e, _ in speeds:
        marks.update((s, e))
    marks = sorted(m for m in marks if 0.0 <= m <= duration)

    segments = []
    for a, b in zip(marks, marks[1:]):
        if b - a < 0.001:
            continue
        mid = (a + b) / 2
        if any(s <= mid < e for s, e in cuts):
            continue  # removed
        rate = 1.0
        for s, e, f in speeds:
            if s <= mid < e:
                rate = f
        segments.append((a, b, rate))
    return segments


def atempo_chain(rate):
    """atempo only accepts 0.5-2.0, so chain filters for anything outside."""
    if abs(rate - 1.0) < 1e-6:
        return []
    parts, r = [], rate
    while r > 2.0:
        parts.append("atempo=2.0")
        r /= 2.0
    while r < 0.5:
        parts.append("atempo=0.5")
        r /= 0.5
    parts.append(f"atempo={r:.6f}")
    return parts


# --------------------------------------------------------------------------
# Filtergraph
# --------------------------------------------------------------------------

def segment_durations(segments):
    return [(end - start) / rate for start, end, rate in segments]


def resolve_dissolves(segments, operations, tol=0.5):
    """Map each dissolve's OUTPUT time onto the join it belongs to.

    Returns {segment index: duration}, keyed by the segment a join precedes.
    A dissolve that does not land on a join is an error worth stopping for -
    silently sliding it to the nearest one would put a fade in the middle of a
    shot and look like a fault in the footage.
    """
    durs = segment_durations(segments)
    joins = {}
    t = 0.0
    for i, d in enumerate(durs[:-1]):
        t += d
        joins[i + 1] = t

    out = {}
    for op in operations:
        if op["type"] != "dissolve":
            continue
        at = float(op["at"])
        d = float(op.get("duration", 0.8))
        near = min(joins, key=lambda k: abs(joins[k] - at)) if joins else None
        if near is None or abs(joins[near] - at) > tol:
            have = ", ".join(f"{v:.2f}s" for v in joins.values()) or "none"
            sys.exit(f"dissolve at {at:.2f}s does not land on a join "
                     f"(joins are at {have})")
        if d >= min(durs[near - 1], durs[near]):
            sys.exit(f"dissolve at {at:.2f}s is {d:.2f}s long, which is longer "
                     "than one of the pieces it joins")
        out[near] = d
    return out


def build_filtergraph(segments, operations, has_audio):
    chains = []
    vlabels, alabels = [], []
    n = len(segments)
    dissolves = resolve_dissolves(segments, operations)
    keep_audio = any(o["type"] == "cut" and o.get("audio") == "keep"
                     for o in operations)
    # With the sound kept whole there are no per-piece audio chains to build,
    # and building them anyway leaves filter outputs with nothing on the other
    # end, which ffmpeg refuses to run.
    per_piece_audio = has_audio and not keep_audio

    for i, (start, end, rate) in enumerate(segments):
        v_in, a_in = "[0:v]", "[0:a]"
        chains.append(
            f"{v_in}trim=start={start:.4f}:end={end:.4f},"
            f"setpts=(PTS-STARTPTS)/{rate:.6f}[v{i}]"
        )
        vlabels.append(f"[v{i}]")
        if per_piece_audio:
            af = [f"atrim=start={start:.4f}:end={end:.4f}", "asetpts=PTS-STARTPTS"]
            af += atempo_chain(rate)
            chains.append(f"{a_in}{','.join(af)}[a{i}]")
            alabels.append(f"[a{i}]")

    if not dissolves and not keep_audio:
        # unchanged path: every EDL written before these options renders
        # byte for byte as it did
        if has_audio:
            pairs = "".join(v + a for v, a in zip(vlabels, alabels))
            chains.append(f"{pairs}concat=n={n}:v=1:a=1[vcat][acat]")
            vcur, acur = "[vcat]", "[acat]"
        else:
            chains.append(f"{''.join(vlabels)}concat=n={n}:v=1:a=0[vcat]")
            vcur, acur = "[vcat]", None
        return _finish(chains, vcur, acur, operations)

    durs = segment_durations(segments)
    vcur = vlabels[0]
    acc = durs[0]
    for i in range(1, n):
        nxt = f"[vj{i}]"
        if i in dissolves:
            d = dissolves[i]
            chains.append(f"{vcur}{vlabels[i]}xfade=transition=fade:"
                          f"duration={d:.4f}:offset={acc - d:.4f}{nxt}")
            acc += durs[i] - d
        else:
            chains.append(f"{vcur}{vlabels[i]}concat=n=2:v=1:a=0{nxt}")
            acc += durs[i]
        vcur = nxt

    acur = None
    if has_audio:
        if keep_audio:
            # one unbroken run, exactly as long as the finished picture
            s0 = segments[0][0]
            chains.append(f"[0:a]atrim=start={s0:.4f}:end={s0 + acc:.4f},"
                          f"asetpts=PTS-STARTPTS[acat]")
            acur = "[acat]"
        else:
            acur = alabels[0]
            for i in range(1, n):
                nxt = f"[aj{i}]"
                if i in dissolves:
                    chains.append(f"{acur}{alabels[i]}"
                                  f"acrossfade=d={dissolves[i]:.4f}{nxt}")
                else:
                    chains.append(f"{acur}{alabels[i]}concat=n=2:v=0:a=1{nxt}")
                acur = nxt
    return _finish(chains, vcur, acur, operations)


def _finish(chains, vcur, acur, operations):

    # --- overlays (timed against the OUTPUT timeline) ---
    extra_inputs = []
    idx = 1
    for op in operations:
        if op["type"] != "overlay":
            continue
        extra_inputs.append(op["asset"])
        x, y = op.get("x", 0), op.get("y", 0)
        enable = ""
        if "start" in op and "end" in op:
            enable = f":enable='between(t,{op['start']},{op['end']})'"
        nxt = f"[vov{idx}]"
        chains.append(f"{vcur}[{idx}:v]overlay={x}:{y}{enable}{nxt}")
        vcur = nxt
        idx += 1

    # --- burned-in captions (must come after concat) ---
    for op in operations:
        if op["type"] != "captions":
            continue
        nxt = "[vsub]"
        chains.append(f"{vcur}subtitles='{op['file']}'{nxt}")
        vcur = nxt

    # --- audio gain ---
    if acur:
        for op in operations:
            if op["type"] == "audio_gain":
                chains.append(f"{acur}volume={op['db']}dB[again]")
                acur = "[again]"

    return ";".join(chains), vcur, acur, extra_inputs


def probe_dimensions(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        capture_output=True, text=True, check=True,
    )
    w, h = out.stdout.strip().split("x")
    return int(w), int(h)


def proxy_size(w, h, long_edge=1280):
    """Scale so the longest edge is at most long_edge. Keeps portrait portrait."""
    if max(w, h) <= long_edge:
        return w, h
    scale = long_edge / max(w, h)
    return (int(w * scale) // 2) * 2, (int(h * scale) // 2) * 2


def has_audio_stream(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return bool(out.stdout.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("edl")
    ap.add_argument("--proxy", action="store_true",
                    help="fast 720p preview render")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    edl = json.loads(Path(args.edl).read_text())
    source = Path(edl["source"])
    if not source.exists():
        sys.exit(f"source not found: {source}")

    ops = edl.get("operations", [])
    duration = probe_duration(source)
    audio = has_audio_stream(source)
    segments = build_segments(duration, ops)
    if not segments:
        sys.exit("every frame was cut - nothing to render")

    graph, vout, aout, extras = build_filtergraph(segments, ops, audio)

    # Proxy downscale must live INSIDE the filtergraph - -vf cannot be combined
    # with -filter_complex for the same output stream.
    if args.proxy:
        sw, sh = probe_dimensions(source)
        pw, ph = proxy_size(sw, sh)
        if (pw, ph) != (sw, sh):
            graph += f";{vout}scale={pw}:{ph}[vproxy]"
            vout = "[vproxy]"

    out = Path(edl.get("output", "out/render.mp4"))
    if args.proxy:
        out = out.with_name(out.stem + "_proxy.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        sys.exit(f"{out} exists - bump the version, never overwrite a render")

    cmd = ["ffmpeg", "-hide_banner", "-i", str(source)]
    for e in extras:
        cmd += ["-i", str(e)]
    cmd += ["-filter_complex", graph, "-map", vout]
    if aout:
        cmd += ["-map", aout]

    if args.proxy:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", "18",
                "-pix_fmt", "yuv420p"]
    if aout:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd.append(str(out))

    kept = sum(e - s for s, e, _ in segments)
    print(f"source   {source}  ({duration:.2f}s)")
    print(f"segments {len(segments)} kept, {duration - kept:.2f}s removed")
    print(f"output   {out}")
    print()
    print(" ".join(shlex.quote(c) for c in cmd))

    if args.dry_run:
        return
    print()
    subprocess.run(cmd, check=True)
    print(f"\ndone -> {out}")


if __name__ == "__main__":
    main()

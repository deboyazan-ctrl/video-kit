#!/usr/bin/env python3
"""
prune_grad.py [--delete]

Throw away the intermediate renders the chain no longer points at.

Every scene writes a new file each time it is composited, and a full pass over
this video is about 6 GB. Nothing ever cleaned up, so work/grad reached 80 GB
of which all but thirteen files were dead ends.

What is kept: the file each scene's recipe currently names, and anything the
EDLs read. Everything else is reproducible - scripts/render_chain.py rebuilds
it from the recipes, which are the actual record.

Prints what it would remove and removes nothing unless --delete is passed.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delete", action="store_true")
    args = ap.parse_args()

    keep = set()
    chain = json.loads((ROOT / "work" / "chain.json").read_text())
    for name in chain["scenes"]:
        rec = json.loads((ROOT / "work" / "scenes" / name / "recipe.json").read_text())
        for k in ("input", "intermediate"):
            v = rec.get("render", {}).get(k)
            if v:
                keep.add((ROOT / v).resolve())
    for edl in sorted((ROOT / "edl").glob("*.json")):
        src = json.loads(edl.read_text()).get("source")
        if src:
            keep.add((ROOT / src).resolve())

    grad = ROOT / "work" / "grad"
    files = sorted(p for p in grad.rglob("*") if p.is_file())
    dead = [p for p in files if p.resolve() not in keep]
    kept = [p for p in files if p.resolve() in keep]

    print(f"keeping {len(kept)} files, {human(sum(p.stat().st_size for p in kept))}")
    for p in kept:
        print(f"   {p.relative_to(ROOT)}")
    total = sum(p.stat().st_size for p in dead)
    print(f"\n{'removing' if args.delete else 'would remove'} {len(dead)} files, {human(total)}")
    if not args.delete:
        print("\nnothing deleted. Pass --delete to do it.")
        return
    for p in dead:
        p.unlink()
    print("done.")


if __name__ == "__main__":
    main()

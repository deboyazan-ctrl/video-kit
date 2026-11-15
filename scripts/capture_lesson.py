#!/usr/bin/env python3
"""
capture_lesson.py            interactive
capture_lesson.py --symptom "..." --wrong "..." --measure "..." --fix "..." \
                  [--from "project/scene"] [--section "Patching"]

Append a lesson to LESSONS.md and rebuild its index.

A lesson that is only remembered is not a lesson. This project produced
twenty-eight of them and every one was written down at the end, from memory,
because somebody thought to - which is exactly the part that will not happen
next time.

The four fields are the ones that turned out to matter. SYMPTOM is what you
actually see, because that is all you have when you are stuck, and it is how
you will search. WRONG is the conclusion a reasonable person draws first -
most of the time lost on this project was spent inside a plausible wrong
diagnosis. MEASURE is the number that settles it, because on every single one
of these the argument ended when something got measured. FIX is what to do.
"""
import argparse
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LESSONS = ROOT / "LESSONS.md"
INDEX_START = "<!-- index:start -->"
INDEX_END = "<!-- index:end -->"


def slug(text):
    s = re.sub(r"[^a-z0-9 -]", "", text.lower()).strip()
    return re.sub(r"\s+", "-", s)[:60]


def build_index(body):
    """One line per lesson, by symptom - that is what you search on."""
    rows = []
    section = None
    for line in body.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        m = re.match(r"\*\*(.+?)\*\*", line.strip())
        if m and section:
            sym = m.group(1).rstrip(".")
            rows.append(f"- [{sym}](#{slug(sym)}) — *{section}*")
    return "\n".join(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symptom"); ap.add_argument("--wrong")
    ap.add_argument("--measure"); ap.add_argument("--fix")
    ap.add_argument("--from", dest="origin", default="")
    ap.add_argument("--section", default="Caught later")
    ap.add_argument("--reindex-only", action="store_true")
    args = ap.parse_args()

    text = LESSONS.read_text()

    if not args.reindex_only:
        fields = {}
        for k in ("symptom", "wrong", "measure", "fix"):
            v = getattr(args, k)
            if not v:
                prompt = {"symptom": "What do you SEE when this goes wrong?",
                          "wrong": "What would you conclude first, and be wrong?",
                          "measure": "What number settles it?",
                          "fix": "What do you do about it?"}[k]
                v = input(f"{prompt}\n> ").strip()
            if not v:
                sys.exit("all four fields are needed - a lesson without a "
                         "measurement is an anecdote")
            fields[k] = v

        origin = f"  \n  Seen in: {args.origin}" if args.origin else ""
        entry = (f"\n**{fields['symptom'].rstrip('.')}**  \n"
                 f"  Looks like: {fields['wrong']}  \n"
                 f"  Settled by: {fields['measure']}  \n"
                 f"  Do: {fields['fix']}{origin}  \n"
                 f"  *{date.today().isoformat()}*\n")

        head = f"## {args.section}"
        if head in text:
            i = text.index(head) + len(head)
            text = text[:i] + "\n" + entry + text[i:]
        else:
            text = text.rstrip() + f"\n\n{head}\n{entry}"
        print(f"added under '{args.section}':\n{entry}")

    body = text.split(INDEX_END)[-1] if INDEX_END in text else text
    index = build_index(body)
    block = f"{INDEX_START}\n\n## Index, by what you see\n\n{index}\n\n{INDEX_END}"
    if INDEX_START in text:
        text = re.sub(re.escape(INDEX_START) + r".*?" + re.escape(INDEX_END),
                      block, text, flags=re.S)
    else:
        lines = text.splitlines()
        cut = next((i for i, l in enumerate(lines) if l.startswith("## ")), 1)
        text = "\n".join(lines[:cut]) + f"\n{block}\n\n" + "\n".join(lines[cut:])

    LESSONS.write_text(text)
    print(f"index rebuilt: {len(index.splitlines())} lessons")


if __name__ == "__main__":
    main()

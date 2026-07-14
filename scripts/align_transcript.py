#!/usr/bin/env python3
"""
align_transcript.py — pair each extracted page with the words spoken while it
was on screen. Produces pages_context.md, the single file the lecture-processor
subagent reads alongside the page images.

Requires a *timestamped* transcript (SRT or VTT). If your online tool only gave
plain text with no timestamps, re-run Whisper with `-f srt` (see CLAUDE.md) —
without timestamps there is nothing to align to.

Usage
-----
  python align_transcript.py --pages work/L01/pages.json \
                             --srt   work/L01/L01.srt \
                             --out   work/L01/pages_context.md \
                             --pad 8
"""
from __future__ import annotations
import argparse, json, re
from pathlib import Path

# Matches SRT (HH:MM:SS,mmm) and both WebVTT forms: HH:MM:SS.mmm and the
# sub-hour short form MM:SS.mmm that some tools emit (HH group optional).
TS = re.compile(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")


def _to_s(m) -> float:
    hh, mm, ss, ms = m.groups()
    hh = hh or "0"
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms.ljust(3, "0")) / 1000


def parse_cues(text: str):
    """Very small SRT/VTT parser -> list of (start_s, end_s, text)."""
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        stamps = [m for m in TS.finditer(block)]
        if len(stamps) < 2:
            continue
        start, end = _to_s(stamps[0]), _to_s(stamps[1])
        # everything after the timestamp line is the caption text
        lines = block.splitlines()
        body = [ln for ln in lines if "-->" not in ln and not ln.strip().isdigit()]
        body = [ln for ln in body if not ln.strip().upper().startswith("WEBVTT")]
        cue = " ".join(ln.strip() for ln in body if ln.strip())
        cue = TS.sub("", cue).strip()
        if cue:
            cues.append((start, end, cue))
    return cues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", required=True)
    ap.add_argument("--srt", required=True, help="SRT or VTT transcript")
    ap.add_argument("--out", required=True)
    ap.add_argument("--pad", type=float, default=8.0,
                    help="seconds of spoken context to include before/after each page window")
    args = ap.parse_args()

    pages = json.loads(Path(args.pages).read_text())
    cues = parse_cues(Path(args.srt).read_text(encoding="utf-8", errors="replace"))

    lines = ["# Page ⇄ transcript alignment",
             f"\n{len(pages)} pages, {len(cues)} transcript cues.\n"]
    for p in pages:
        lo, hi = p["t_start"] - args.pad, p["t_end"] + args.pad
        spoken = " ".join(c for (s, e, c) in cues if e >= lo and s <= hi)
        lines += [
            f"\n## Page {p['index']:03d}  [{p['hms_start']}–{p['hms_end']}]",
            f"image: `{p['frame']}`\n",
            "> " + (spoken if spoken else "_(no transcript in this window)_"),
        ]
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print(f"[✓] wrote {args.out}")


if __name__ == "__main__":
    main()

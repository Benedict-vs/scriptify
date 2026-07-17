#!/usr/bin/env python3
"""
peek.py — look at the VIDEO at a given timestamp, not at the extracted pages.

Why this exists
---------------
Every `% TODO(L03 @ 01:04:42): ... check the video` in the corpus was written by an
agent that could only see `work/L##/frames/page_###.png` — the pages the extractor
chose to emit. But the extractor emits a page when the canvas has *settled*: after a
scroll comes to rest, on a slide change, at a clear. That makes it blind, by
construction, to exactly the things the TODOs ask about:

  * content written and then scrolled past before the next emit,
  * content below the visible edge of the page that was emitted,
  * a sketch drawn and erased between two emits,
  * a figure the lecturer builds up incrementally (only the final state is a page).

None of that is a bug — a page is a still, and these are motion. The video still has
every frame of it. So "check the video" is not a note to a human; it is an executable
instruction, and this is the executable.

Two modes, because handwriting legibility is the whole constraint:

  sheet  (default) — a labelled contact sheet across a time window. Each cell is
                     downscaled, so you can see LAYOUT (is there a sketch? where?)
                     but not read it. This is for LOCALISING the moment.
  frame            — one frame at full 1440x1080, optionally cropped and upscaled.
                     This is for READING it.

The workflow is always sheet -> pick a timestamp -> frame. Skipping the sheet and
guessing a timestamp wastes a full-res read on the wrong moment.

  python scripts/peek.py L03 01:04:42                     # sheet, +-30s
  python scripts/peek.py L03 01:04:42 --window 90 --step 10
  python scripts/peek.py L03 01:05:07 --frame            # full-res single frame
  python scripts/peek.py L03 01:05:07 --frame --crop 0,0.4,0.6,1.0   # x0,y0,x1,y1 fractions
"""
from __future__ import annotations
import argparse, os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Repo-relative, not tied to a session: the earlier hardcoded scratchpad path pointed at a
# dead session directory and was silently recreated by mkdir().
OUT = Path(os.environ.get("PEEK_OUT") or ROOT / "work" / "_peek")


def parse_ts(s: str) -> float:
    if ":" not in s:
        return float(s)
    parts = [float(p) for p in s.split(":")]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def fmt_ts(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"


def source_for(lecture: str) -> Path:
    tsv = ROOT / "work" / "lectures.tsv"
    if not tsv.is_file():
        sys.exit(f"[!] {tsv} not found — the manifest lives with the content, in work/. "
                 f"Generate it with `python scripts/build_manifest.py`.")
    for line in tsv.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        cols = line.split("\t")
        if cols[0] == lecture:
            return ROOT / cols[1]
    sys.exit(f"{lecture} not in {tsv}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("lecture")
    ap.add_argument("timestamp")
    ap.add_argument("--window", type=float, default=30.0, help="+- seconds around t (sheet)")
    ap.add_argument("--step", type=float, default=6.0, help="seconds between cells (sheet)")
    ap.add_argument("--frame", action="store_true", help="single full-res frame instead")
    ap.add_argument("--crop", help="x0,y0,x1,y1 as fractions of the frame (with --frame)")
    ap.add_argument("--scale", type=float, default=1.0, help="upscale factor for a crop")
    args = ap.parse_args()

    src = source_for(args.lecture)
    if not src.exists():
        sys.exit(f"missing source: {src}")
    t = parse_ts(args.timestamp)
    OUT.mkdir(parents=True, exist_ok=True)

    if args.frame:
        out = OUT / f"{args.lecture}_{fmt_ts(t).replace(':', '')}.png"
        vf = []
        if args.crop:
            x0, y0, x1, y1 = (float(v) for v in args.crop.split(","))
            vf.append(f"crop=iw*{x1 - x0:.4f}:ih*{y1 - y0:.4f}:iw*{x0:.4f}:ih*{y0:.4f}")
        if args.scale != 1.0:
            vf.append(f"scale=iw*{args.scale}:ih*{args.scale}:flags=lanczos")
        cmd = ["ffmpeg", "-y", "-ss", str(t), "-i", str(src), "-frames:v", "1"]
        if vf:
            cmd += ["-vf", ",".join(vf)]
        cmd += [str(out)]
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"{out}   [{args.lecture} @ {fmt_ts(t)}]")
        return

    # Contact sheet. The local ffmpeg has no drawtext (built without libfreetype), so the
    # cells carry no burned-in timestamp and the printed index below IS the legend: tile
    # fills row-major, left to right, top to bottom. Read a cell's time off that.
    n = int(2 * args.window / args.step) + 1
    t0 = max(0.0, t - args.window)
    cols = 4
    rows = (n + cols - 1) // cols
    sheet = OUT / f"{args.lecture}_{fmt_ts(t).replace(':', '')}_sheet.png"
    vf = (f"fps=1/{args.step},scale=640:-1,"
          f"tile={cols}x{rows}:margin=6:padding=4:color=0x303030")
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(t0), "-i", str(src), "-t", str(2 * args.window),
         "-vf", vf, "-frames:v", "1", str(sheet)],
        check=True, capture_output=True)
    print(f"{sheet}")
    print(f"[{args.lecture} @ {fmt_ts(t)}]  window {fmt_ts(t0)}–{fmt_ts(t0 + 2*args.window)}, "
          f"{n} cells, {args.step:g}s apart. Cell labels are ABSOLUTE seconds.")
    for i in range(n):
        if i % cols == 0:
            print()
        print(f"  {fmt_ts(t0 + i*args.step)} ({int(t0 + i*args.step)}s)", end="")
    print()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
plan_splits.py — cut a long lecture into subagent-sized page ranges.

A lecture-processor reads every frame as an image (~1000 tokens each at
--frame-width 1100) plus the transcript, so a 90-page lecture does not fit one
subagent's context. Split it, run one subagent per range, concatenate.

Two things make a split safe:
  * OVERLAP — each range repeats the last few pages of the previous one, so the
    argument that straddles the seam is visible whole to both subagents.
  * A NATURAL seam — cutting mid-derivation forces both subagents to guess at the
    other half. The lecturer scrolls to fresh canvas when starting a new topic, so
    the largest scroll step in the neighbourhood of the target is a good boundary;
    a slide (canvas_y stagnant) is an even better one.

  python scripts/plan_splits.py work/L03                 # plan one lecture
  python scripts/plan_splits.py                          # plan all of work/L##
  python scripts/plan_splits.py work/L03 --json          # machine-readable
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

SINGLE_MAX = 50     # a lecture up to this many pages goes to one subagent (L01: 53 pages
                    # cost ~157k tokens, so this is about where one agent tops out)
TARGET     = 43     # aim for ranges no larger than this
OVERLAP    = 4      # pages repeated at each seam
SEARCH     = 5      # look this far either side of the target for a natural boundary


def seam_score(pages: list[dict], i: int) -> float:
    """How good a boundary is 'range ends at page i' (1-based i)? Higher = better.
    A big scroll step into page i+1 means the lecturer moved to fresh canvas."""
    a, b = pages[i - 1], pages[i]         # page i and the page after it
    if "canvas_y" not in a:               # PDF lecture: every page break is equal
        return 0.0
    scroll = b["canvas_y"] - a["canvas_y"]
    pause = b["t_start"] - a["t_start"] if "t_start" in a else 0.0
    # A slide / page-clear (no scroll at all between two kept pages) is the cleanest
    # break there is; otherwise prefer a large scroll and a long dwell.
    return 1e6 if scroll <= 0 else scroll + 0.5 * pause


def plan(pages: list[dict]) -> list[tuple[int, int]]:
    n = len(pages)
    if n <= SINGLE_MAX:
        return [(1, n)]

    # k ranges of <= TARGET pages, overlapping by OVERLAP, must cover n pages:
    #   k*TARGET - (k-1)*OVERLAP >= n. The overlap is re-read work, so it has to be
    #   paid for in the sizing or the last range silently absorbs it and blows up.
    k = max(2, -(-(n - OVERLAP) // (TARGET - OVERLAP)))
    size = -(-(n + (k - 1) * OVERLAP) // k)   # balanced range size, overlap included
    cuts: list[int] = []                      # last page of each range but the final one
    for r in range(1, k):
        target = min(cuts[-1] - OVERLAP + size if cuts else size, n - 1)
        lo, hi = max(1, target - SEARCH), min(n - 1, target + SEARCH)
        best = max(range(lo, hi + 1), key=lambda i: seam_score(pages, i))
        if not cuts or best > cuts[-1]:
            cuts.append(best)

    ranges, start = [], 1
    for c in cuts:
        ranges.append((start, c))
        start = max(1, c - OVERLAP + 1)    # repeat OVERLAP pages across the seam
    ranges.append((start, n))
    return ranges


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*", help="work/L## directories (default: all)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    dirs = [Path(d) for d in args.dirs] or sorted(
        p for p in Path("work").glob("L*") if (p / "pages.json").exists())

    out = {}
    for d in dirs:
        pages = json.loads((d / "pages.json").read_text())
        ranges = plan(pages)
        out[d.name] = ranges
        if args.json:
            continue
        if len(ranges) == 1:
            print(f"{d.name}: {len(pages):3d} pages -> 1 subagent")
            continue
        print(f"{d.name}: {len(pages):3d} pages -> {len(ranges)} subagents")
        prev_hi = 0
        for (lo, hi), suffix in zip(ranges, "abcdefgh"):
            # READ includes the overlap so the subagent sees the run-up to its first
            # page; WRITE is the disjoint part, so concatenating the fragments cannot
            # duplicate the material that straddles a seam.
            own_lo = prev_hi + 1
            where = pages[hi - 1].get("hms_end", f"p.{hi}")
            ctx = f", ctx p{lo:03d}-{own_lo-1:03d}" if lo < own_lo else ""
            print(f"      {d.name}_{suffix}  read p{lo:03d}-{hi:03d}{ctx}  "
                  f"write p{own_lo:03d}-{hi:03d} ({hi-own_lo+1:2d}p, ends {where})")
            prev_hi = hi

    if args.json:
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()

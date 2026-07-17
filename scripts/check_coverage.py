#!/usr/bin/env python3
"""
check_coverage.py — did the scroll extractor actually see the whole canvas?

Consecutive snapshots of the scrolling canvas must OVERLAP. If the canvas advanced
by more than one viewport height between two kept pages, the strip in between was
never in any frame and that content is gone — silently. This is not hypothetical:
the first L01 run had four such gaps (steps up to 407px at a 360px viewport) and
lost the sigma-algebra axioms, the probability-measure axioms and the whole
conditional-probability block. The subagent "reconstructed" them from the audio,
which is exactly the kind of quiet fabrication we do not want.

So: run this after every extraction. It is a few milliseconds and it is the one
failure mode you cannot see by eyeballing the frames.

  python scripts/check_coverage.py work/L01     # one lecture
  python scripts/check_coverage.py              # every work/L##
Exit code 1 if any lecture has a gap.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

# Lectures whose manifest predates the temporal fix: the standing-canvas invariant is
# UNVERIFIED for them, not satisfied. Collected here and reported once at the end so the
# distinction cannot be mistaken for a clean bill of health.
temporal_unverified: list[str] = []


def check(dir_: Path) -> bool:
    manifest = dir_ / "pages.json"
    if not manifest.exists():
        print(f"[ ] {dir_.name}: no pages.json — not extracted yet")
        return True

    pages = json.loads(manifest.read_text())
    if not pages:
        print(f"[!] {dir_.name}: pages.json is empty")
        return False

    if "canvas_y" not in pages[0]:      # PDF lecture (pdf_to_pages.py): no scroll, no gaps
        print(f"[✓] {dir_.name}: {len(pages):3d} PDF pages — no scrolling canvas, coverage n/a")
        return True

    ah = pages[0].get("viewport_h")
    if ah is None:
        print(f"[!] {dir_.name}: pages.json has no viewport_h — extracted by the OLD, "
              f"gap-prone extractor. Re-extract before Stage 4.")
        return False

    if "regime" not in pages[0]:
        print(f"[!] {dir_.name}: pages.json has no regime — extracted BEFORE the letterbox "
              f"slide fix, so slide changes were caught only by luck (they cluster right on "
              f"--transition-threshold). Re-extract before Stage 4.")
        return False

    # --- Slide invariant --------------------------------------------------------
    # Slides never move the canvas, so the scroll check below is blind to them: it
    # skips every non-advancing step. That blind spot is what let L05 lose the slide
    # with the final weighted beta_N and nobody noticed until the .tex was written.
    # The extractor numbers each DETECTED slide change (slide_seq); if the kept pages
    # do not carry a contiguous run of those numbers, something downstream (dedup, a
    # manual edit, a "simplification") dropped a slide that the extractor did see.
    seqs = sorted(p["slide_seq"] for p in pages if "slide_seq" in p)
    expected = pages[0].get("n_slide_changes", len(seqs))
    if seqs != list(range(expected)):
        missing = sorted(set(range(expected)) - set(seqs))
        print(f"[!] {dir_.name}: {len(missing)} of {expected} detected slide changes never "
              f"reached a page — slide_seq {missing} missing. Content on those slides exists "
              f"in NO frame. Do not run Stage 4 on this.")
        return False

    # --- Terminal-frame invariant -----------------------------------------------
    # Whatever is on screen when the recording stops is captured by exactly ONE emit:
    # no later change re-captures it. A lecture ending on a held slide therefore has a
    # single sighting of that slide — and the dedup used to delete it (same template as
    # the slide before it => near-identical row profile). That is how L02 lost its
    # closing Gamma slide. Neither the scroll rule nor slide_seq covers the last frame,
    # so it gets its own check: the final page must reach the end of the video.
    duration = pages[0].get("duration")
    if duration is None:
        print(f"[!] {dir_.name}: pages.json has no duration — extracted before the "
              f"terminal-frame fix, when the last slide of a video could be deduped away. "
              f"Re-extract before Stage 4.")
        return False
    last_t = pages[-1]["t_end"]
    if duration and duration - last_t > 5.0:
        print(f"[!] {dir_.name}: last page ends at {last_t:.0f}s but the video runs to "
              f"{duration:.0f}s — the final {duration-last_t:.0f}s are in no frame. "
              f"Whatever is on screen at the end exists nowhere.")
        return False

    # --- Temporal invariant (the sixth loss channel) -----------------------------
    # Every invariant above is SPATIAL: it asserts that every strip of canvas was
    # photographed once. None of them says anything about WHEN. So content written on a
    # standing canvas and erased or overwritten in place before the next emit is in no
    # frame at all — and every check above stays green. That is not hypothetical: it is
    # why 9 of the 21 original TODOs asserted "never written" for material that was on
    # the board the whole time (L11's bias-variance figure, L02's covariance-matrix
    # theorem, L10's XOR weights, L08's hidden-layer sketch). The agents saw a half-empty
    # frame and drew a conclusion about the LECTURE from a fact about the EXTRACTOR.
    #
    # The extractor now bounds that blind window: once writing has accumulated past
    # ink_threshold, it forces a "settle" snapshot within max_static_seconds. Assert it.
    # ink_since_prev carries the ink of any deduped page forward, so a dedup that merges
    # two windows cannot hide writing from this check.
    # A manifest without the key predates the fix. That is a WARNING, not a failure, and the
    # distinction is deliberate: the 13 shipped lectures were extracted before it, and their
    # blind windows were closed out-of-band instead — by the video pass (scripts/peek.py), which
    # is why all 21 TODOs could be resolved and the 6 empty frames cleared. Hard-failing them
    # would paint a finished, verified corpus red and push the next session into a pointless
    # re-extraction. But it must never pass in SILENCE either: anything extracted from here on
    # carries the guarantee, so a missing key can only mean an old manifest, and the operator
    # has to know the temporal invariant is unverified rather than satisfied.
    max_static = pages[0].get("max_static_seconds")
    ink_thr = pages[0].get("ink_threshold")
    if max_static is None or ink_thr is None:
        # WARN ONLY — and fall through to the spatial check below. Do NOT return here:
        # an early return would skip the overlap invariant as well, quietly disabling a
        # guard that already works. (Nearly did exactly that while writing this.)
        temporal_unverified.append(dir_.name)
    else:
        # Assert EXPOSURE, not the raw gap between pages. The gap is the wrong quantity: a
        # 190s window in which the lecturer wrote nothing for the first three minutes is
        # perfectly safe, and an earlier cut of this check failed exactly those, because it
        # asked "was this window long AND did it contain ink?" instead of the thing that
        # matters. exposure_s is how long above-threshold writing actually sat on the canvas
        # with no frame showing it — the true width of the window in which something can be
        # written and erased unseen. The extractor forces a "settle" snapshot once that
        # reaches max_static_seconds, so exposure can never legitimately exceed it.
        blind = [p for p in pages if p.get("exposure_s", 0.0) > max_static + 2.0]
        if blind:
            print(f"[!] {dir_.name}: temporal coverage broken — writing sat on a standing "
                  f"canvas for longer than {max_static:.0f}s with no frame showing it:")
            for p in blind:
                print(f"      page {p['index']:03d}  exposure {p['exposure_s']:.0f}s "
                      f"(ink {p.get('ink_since_prev', 0):.3f})  [{p['hms_start']} - "
                      f"{p['hms_end']}] — anything drawn AND erased in there exists in no frame")
            return False

    # The extractor's invariant: consecutive pages advance by < step_px, so they
    # overlap by (viewport_h - step_px). Assert THAT, not merely "no outright hole" —
    # a step between step_px and viewport_h captures every row but leaves the seams
    # so thin that content written late near a seam is still lost. Checking only for
    # holes is what let the dedup bug pass as green on L07/L10/L12.
    limit = pages[0].get("step_px", ah)   # older manifests: fall back to the hole check
    gaps, holes = [], []
    for a, b in zip(pages, pages[1:]):
        step = b["canvas_y"] - a["canvas_y"]
        if step <= 0:                     # slide stretch / page clear: no scroll
            continue
        if step >= ah:
            holes.append((a["index"], b["index"], step, a["hms_end"], b["hms_end"]))
        elif step > limit:
            gaps.append((a["index"], b["index"], step, a["hms_end"], b["hms_end"]))

    if holes or gaps:
        print(f"[!] {dir_.name}: coverage broken (viewport {ah:.0f}px, "
              f"pages must advance < {limit:.0f}px):")
        for i, j, step, t_i, t_j in holes:
            print(f"      page {i:03d} -> {j:03d}  step {step:.0f}px  HOLE — "
                  f"{step - ah:.0f}px never captured  [{t_i} - {t_j}]")
        for i, j, step, t_i, t_j in gaps:
            print(f"      page {i:03d} -> {j:03d}  step {step:.0f}px  overlap down to "
                  f"{100*(ah-step)/ah:.0f}%  [{t_i} - {t_j}]")
        return False

    worst = max((b["canvas_y"] - a["canvas_y"] for a, b in zip(pages, pages[1:])
                 if b["canvas_y"] - a["canvas_y"] > 0), default=0.0)
    overlap = 100 * (ah - worst) / ah if worst else 100.0
    n_slides = sum(1 for p in pages if p.get("regime") == "slide")
    print(f"[✓] {dir_.name}: {len(pages):3d} pages ({n_slides:2d} slide / "
          f"{len(pages)-n_slides:3d} canvas), min overlap {overlap:.0f}% "
          f"(worst step {worst:.0f}/{ah:.0f}px), all {expected} slide changes captured")
    return True


def main() -> None:
    if len(sys.argv) > 1:
        dirs = [Path(a) for a in sys.argv[1:]]
    else:
        dirs = sorted(p for p in Path("work").glob("L*") if p.is_dir())
    if not dirs:
        sys.exit("[!] nothing to check — pass a work/L## dir or run from the project root")

    ok = all([check(d) for d in dirs])      # list, not generator: check every lecture

    if temporal_unverified:
        print(f"\n[~] Temporal invariant UNVERIFIED for: {', '.join(temporal_unverified)}")
        print( "    These manifests predate the --max-static-seconds fix. The spatial coverage")
        print( "    above is green and holds; it says nothing about content written on a STANDING")
        print( "    canvas and erased there again before the next emit.")
        print( "    Re-extract these lectures before sending them through Stage 4 — only then does")
        print( "    the manifest carry the temporal guarantee as well.")

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()

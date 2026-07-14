#!/usr/bin/env python3
"""
extract_pages_scroll.py — PRIMARY page extractor for the CSDA recordings.

The recordings are mostly a CONTINUOUSLY SCROLLING handwriting canvas, with the
occasional presentation slide (and, in the L01 intro, some write-then-clear pages).
Inspection of L01 nailed the two signals down:

  * Scrolling shows up as a clean staircase in the vertical-scroll trajectory
    (flat = writing, sharp step = a scroll burst). ~3.4 screens per 13 min.
  * A scroll burst is a COHERENT vertical shift: the row profiles translate, so
    the 1-D cross-correlation confidence is high and |dy| > 0. A discrete event
    (slide change / page clear) replaces content WITHOUT a coherent shift. So
    scroll vs. discrete is told apart by the SHIFT, not by raw magnitude (slide-to-
    slide changes overlap scroll bursts in magnitude, so a threshold alone fails).
  * A slide is told apart from the canvas by its LETTERBOX, not by brightness.
    The slide app renders 4:3 content with black bars top and bottom; the canvas
    fills the screen. Measured over all 12 recordings the black-row fraction is
    binary: the canvas is EXACTLY 0.000 (never a single dark row — a row through the
    densest handwriting still averages ~0.85), slides sit at 0.033-0.056 depending on
    the deck's aspect ratio. Nothing lands in between, so this is a structural bit of
    the recording setup, not a tuned threshold.
    It replaces the old brightness rule, which assumed slides were DARK: they are
    not, they are white slides with black text (mean brightness ~0.92 against ~0.98
    for blank canvas). `--brightness-threshold 0.72` therefore never fired in
    L02-L12 (global min brightness 0.763) and slide mode was dead code in eleven of
    twelve lectures. The slides then fell through to the canvas path, where a slide
    change is only caught if it clears --transition-threshold (0.04) — but real
    slide-to-slide diffs cluster at 0.017-0.041, i.e. right ON that threshold, so
    roughly every second slide was silently dropped. That is how L05 lost the slide
    carrying the final weighted form of beta_N. Brightness is kept as an OR-fallback
    (it is a strict subset of the letterbox signal here: all 963 dark L01 frames
    also carry bars) in case a future recording has dark full-bleed slides.

So this extractor does BOTH:
  (1) integrate the vertical scroll (1-D row-profile cross-correlation) and emit
      snapshots of the canvas under two rules, both of which capture frame i-1 (the
      settled frame, before the scroll of frame i has been applied):
        COVERAGE — emit before the scroll would carry us more than (1-overlap) screens
          past the last snapshot, so consecutive snapshots always overlap by `overlap`.
        PRE-BURST — emit before a scroll burst that follows real writing activity
          (> --ink-threshold). The canvas is WRITTEN, not revealed: spatial overlap
          alone guarantees only that every canvas row appears in some frame, NOT that
          it appears there already written. Content leaves the canvas only by scrolling
          off the top, so the settled frame before a burst is the last chance to see
          that viewport complete. Most bursts are the lecturer nudging the canvas
          without writing — those are skipped, the overlap already covers them.
  (2) on a discrete transition (d > --transition-threshold) emit the frame JUST
      BEFORE it (the complete slide / full page) and reset the scroll baseline.

Both rules are needed. With only COVERAGE (and it must be tested BEFORE applying the
scroll, or the discrete jump overshoots the threshold), L01 lost its whole
conditional-probability block: the rows were spatially covered, but the frame that
covered them was taken 3.5 min earlier, when that part of the canvas was still blank.

Output (pages.json + frames/) is drop-in compatible with align_transcript.py.
Deps: numpy, ffmpeg/ffprobe. matplotlib only for --diagnostic.

  # Look at the trajectory + transition marks + suggested page count:
  python extract_pages_scroll.py L01.mp4 --out work/L01 --diagnostic
  # Run for real:
  python extract_pages_scroll.py L01.mp4 --out work/L01
"""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path
import numpy as np


def ffprobe_dims(video: str):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", video],
        capture_output=True, text=True, check=True)
    w, h = out.stdout.strip().split("x")[:2]
    return int(w), int(h)


def ffprobe_duration(video: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def iter_gray_small(video: str, fps: float, w: int, h: int,
                    limit: float | None, start: float = 0.0):
    """Stream downscaled gray frames as HxW float32 arrays via an ffmpeg pipe."""
    cmd = ["ffmpeg", "-v", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if limit:
        cmd += ["-t", f"{limit:.3f}"]
    cmd += ["-i", video, "-vf", f"fps={fps},scale={w}:{h},format=gray",
            "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    # stdin=DEVNULL: keep ffmpeg from reading our stdin (it has an interactive
    # keyboard-command reader that would otherwise eat data piped to the caller,
    # e.g. the lectures.tsv lines when run inside a `while read ... done < tsv`).
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, bufsize=10**8)
    n = w * h
    try:
        while True:
            buf = proc.stdout.read(n)
            if len(buf) < n:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(h, w).astype(np.float32)
    finally:
        proc.stdout.close()
        rc = proc.wait()
    if rc not in (0, None):
        raise RuntimeError(
            f"ffmpeg exited with code {rc} while decoding {video!r} — "
            "file truncated/corrupt or unsupported codec?")


def estimate_shift(p0: np.ndarray, p1: np.ndarray, max_shift: int):
    """Vertical shift (rows) aligning p1 onto p0 via 1-D cross-correlation,
    restricted to |shift| <= max_shift. Returns (shift, confidence in [0,1])."""
    a = p0 - p0.mean()
    b = p1 - p1.mean()
    n = len(a)
    cc = np.correlate(b, a, mode="full")
    lags = np.arange(-(n - 1), n)
    m = np.abs(lags) <= max_shift
    cc, lags = cc[m], lags[m]
    k = int(np.argmax(cc))
    denom = np.sqrt((a * a).sum() * (b * b).sum()) + 1e-9
    return int(lags[k]), float(cc[k] / denom)


def hms(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def extract_full_frame(video: str, t: float, out_png: Path, width: int = 0):
    vf = ["-vf", f"scale={width}:-1"] if width else []
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", video,
         "-frames:v", "1", *vf, "-q:v", "2", str(out_png)],
        check=True, stdin=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--fps", type=float, default=2.0,
                    help="analysis sampling rate (default 2/s)")
    ap.add_argument("--awidth", type=int, default=480,
                    help="analysis width in px (height derived from aspect)")
    ap.add_argument("--overlap", type=float, default=0.50,
                    help="fraction of viewport height shared between consecutive scroll snapshots. "
                         "Content is WRITTEN onto the canvas over time, not revealed, so a row needs "
                         "to be seen by a snapshot taken AFTER it was written: >=0.5 gives every row "
                         "two snapshots at different times. Below ~0.35 the lecturer's freshly written "
                         "bottom lines get one chance and are lost if the emit fires just before them.")
    ap.add_argument("--transition-threshold", type=float, default=0.04,
                    help="mean-abs-diff above which a NON-scroll frame pair is a discrete "
                         "transition (slide change / page clear). Must sit above the writing "
                         "noise floor (~0.01); coherent scrolls are classified first, by shift.")
    ap.add_argument("--letterbox-threshold", type=float, default=0.02,
                    help="fraction of near-black full-width rows above which a frame is a SLIDE. "
                         "The slide app letterboxes 4:3 content, the canvas is full-bleed. Measured "
                         "over all 12 recordings this is perfectly binary (0.000 vs 0.056, no frame "
                         "in between), so anything in 0.005..0.05 works and 0.02 sits in the middle. "
                         "This is the PRIMARY slide signal — see the module docstring for why "
                         "brightness was not.")
    ap.add_argument("--brightness-threshold", type=float, default=0.72,
                    help="OR-fallback: mean frame brightness (0-1) below which a frame is a DARK "
                         "slide. Only L01's intro deck is dark; every frame it catches also carries "
                         "the letterbox, so this is redundant today and kept only so a future dark "
                         "full-bleed deck cannot go undetected. Do NOT rely on it alone.")
    ap.add_argument("--slide-threshold", type=float, default=0.008,
                    help="within slide mode, mean-abs-diff above which the slide has changed "
                         "(slides share a template, so slide-to-slide change is small)")
    ap.add_argument("--max-static-seconds", type=float, default=120.0,
                    help="TEMPORAL invariant. Every other emit reason is triggered by the canvas "
                         "MOVING (scroll) or being REPLACED (clear/slide/regime). If the lecturer "
                         "writes on a STANDING canvas and then erases or overwrites it before any "
                         "of those fire, that content is in no frame — and the spatial coverage "
                         "gate stays green, because coverage is about WHERE the canvas was "
                         "photographed, never WHEN. That is the sixth loss channel, and it is why "
                         "9 of the 21 original TODOs claimed 'never written' for content that was "
                         "on the board the whole time (L11's bias-variance figure, L10's XOR "
                         "weights, L08's hidden-layer sketch). So: once real writing has "
                         "accumulated (> --ink-threshold), force a snapshot at least this often, "
                         "even if nothing moves. Bounds the blind window instead of hoping a scroll "
                         "comes along. A pure 'canvas went quiet' detector was rejected: writing "
                         "(d ~ 0.0005-0.003) sits too close to the static floor (d < 0.0005) for "
                         "that threshold to be anything but a guess.")
    ap.add_argument("--ink-threshold", type=float, default=0.01,
                    help="writing activity (summed frame-diff over static canvas frames) that must "
                         "have accumulated since the last snapshot for the pre-scroll-burst capture "
                         "to fire. Most bursts are the lecturer nudging the canvas without writing — "
                         "those need no snapshot, the overlap already covers them.")
    ap.add_argument("--conf-scroll", type=float, default=0.5,
                    help="minimum cross-correlation confidence to accept a shift as real scroll")
    ap.add_argument("--max-shift-frac", type=float, default=0.5,
                    help="max plausible per-frame scroll as a fraction of viewport height")
    ap.add_argument("--dedup", type=float, default=0.02,
                    help="drop a page whose row profile is nearly identical (normalized) to the last kept")
    ap.add_argument("--frame-width", type=int, default=1100,
                    help="width of the written page PNGs. The subagent reads every frame as an image "
                         "and pays ~w*h/750 tokens for it, so full 1440px frames blow the context on a "
                         "long lecture (L03: 81 pages). 1100px keeps the handwriting fully legible "
                         "(verified on L01) at about half the tokens. 0 = keep native resolution.")
    ap.add_argument("--limit-seconds", type=float, default=None,
                    help="only analyse N seconds (with --start-seconds: a middle window)")
    ap.add_argument("--start-seconds", type=float, default=0.0,
                    help="begin analysis at this offset (seek)")
    ap.add_argument("--diagnostic", action="store_true",
                    help="write scroll_curve.{csv,png}, print stats, and stop")
    args = ap.parse_args()

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    frames_dir = out / "frames"; frames_dir.mkdir(exist_ok=True)

    try:
        W, H = ffprobe_dims(args.video)
    except FileNotFoundError:
        sys.exit("[!] ffprobe/ffmpeg not found on PATH — install ffmpeg (brew install ffmpeg).")
    aw = args.awidth
    ah = max(2, round(aw * H / W))
    max_shift = max(1, int(args.max_shift_frac * ah))
    step = (1.0 - args.overlap) * ah
    trans = args.transition_threshold
    try:
        duration = ffprobe_duration(args.video)
        dur_str = hms(duration)
    except (subprocess.CalledProcessError, ValueError):
        duration, dur_str = 0.0, "unknown"
    print(f"[i] {args.video}  {W}x{H}  dur {dur_str}  analysis {aw}x{ah} @ {args.fps} fps")

    # --- Pass 1: stream frames -> (brightness b, letterbox l, diff d, shift dy, conf, profile) -
    times, barr, larr, darr, dyarr, confarr, profiles = [], [], [], [], [], [], []
    prev_frame = prev_prof = None
    for i, frame in enumerate(iter_gray_small(args.video, args.fps, aw, ah,
                                              args.limit_seconds, args.start_seconds)):
        prof = (255.0 - frame).sum(axis=1)
        if prev_frame is None:
            d, dy, conf = 0.0, 0, 1.0
        else:
            d = float(np.abs(frame - prev_frame).mean() / 255.0)
            dy, conf = estimate_shift(prev_prof, prof, max_shift)
        times.append(args.start_seconds + i / args.fps)
        barr.append(float(frame.mean() / 255.0))
        # Letterbox: rows that are near-black across their whole width. Handwriting
        # cannot produce one (a row through the densest ink still averages ~0.85),
        # so this does not false-positive on the canvas.
        larr.append(float(((frame / 255.0).mean(axis=1) < 0.25).mean()))
        darr.append(d); dyarr.append(dy); confarr.append(conf)
        profiles.append(prof.astype(np.float32))
        prev_frame, prev_prof = frame, prof
    N = len(times)
    if N < 2:
        sys.exit("[!] too few frames — is ffmpeg working / video valid?")
    barr = np.asarray(barr); larr = np.asarray(larr); darr = np.asarray(darr)
    dyarr = np.asarray(dyarr); confarr = np.asarray(confarr)
    profiles = np.stack(profiles)

    # Two visual regimes: full-bleed handwriting canvas vs. letterboxed slide.
    # The LETTERBOX separates them (see docstring: brightness does not — slides are
    # white, not dark); then each regime gets its own change detection.
    slide_mask = (larr >= args.letterbox_threshold) | (barr < args.brightness_threshold)
    # A coherent vertical shift on the canvas = scroll (told apart from a slide/clear
    # by the shift, not by magnitude). Slides never count as scroll.
    scroll_mask = (confarr >= args.conf_scroll) & (np.abs(dyarr) >= 1) & (~slide_mask)
    if float((dyarr * scroll_mask).sum()) < 0:      # net scroll should be forward (downward)
        dyarr = -dyarr

    n_slide = int(slide_mask.sum())
    Y_glob = np.cumsum(np.where(scroll_mask, dyarr, 0.0))   # for diagnostic only
    total_screens = (Y_glob.max() - Y_glob.min()) / ah if N else 0.0

    if args.diagnostic:
        csv = out / "scroll_curve.csv"
        np.savetxt(csv, np.column_stack([times, Y_glob, darr, barr, larr]), delimiter=",",
                   header="t_seconds,canvas_y_px,frame_diff,brightness,letterbox", comments="")
        est = int(np.ceil(max(total_screens, 0.0) / (1 - args.overlap))) + 1
        print(f"[i] ~{total_screens:.1f} screens scrolled  +  {n_slide} slide frames "
              f"({100*n_slide/N:.0f}%)  ->  ~{est}+ pages")
        # The letterbox must be BINARY: the canvas is full-bleed (exactly 0.000 black
        # rows), a slide is letterboxed. Slide bar sizes vary with the slide's aspect
        # ratio — 0.056 for the 4:3 decks, 0.036 for L05's taller one — and that is
        # fine, they are all far above the threshold. What must NOT exist is a frame
        # with a FEW dark rows: that would mean the canvas can produce dark rows after
        # all, and the whole regime split would rest on sand.
        grey = int(((larr > 0.002) & (larr < args.letterbox_threshold)).sum())
        print(f"[i] letterbox: {int((larr >= args.letterbox_threshold).sum())} slide frames, "
              f"{grey} frames with partial bars (0.002–{args.letterbox_threshold})"
              + ("  <-- EXPECT 0; investigate before trusting the split" if grey else "  (clean split)"))
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            plt.figure(figsize=(14, 4))
            plt.plot(times, Y_glob, lw=1.0, label="cumulative scroll [px]")
            t_arr, in_slide, s0 = np.asarray(times), False, 0.0
            for i in range(N):                        # shade slide stretches
                if slide_mask[i] and not in_slide:
                    s0, in_slide = t_arr[i], True
                elif not slide_mask[i] and in_slide:
                    plt.axvspan(s0, t_arr[i], color="orange", alpha=0.2); in_slide = False
            if in_slide:
                plt.axvspan(s0, t_arr[-1], color="orange", alpha=0.2)
            plt.xlabel("t [s]"); plt.ylabel("cumulative scroll [px]")
            plt.title("Scroll staircase (blue) + slide stretches (orange)")
            plt.legend(loc="upper left")
            plt.tight_layout(); plt.savefig(out / "scroll_curve.png", dpi=120)
            print(f"[i] wrote {out/'scroll_curve.png'} — sanity-check before the batch")
        except ImportError:
            print(f"[i] wrote {csv} (matplotlib not installed, skipped plot)")
        return

    # --- Select representatives: scroll snapshots + pre-transition captures ------
    # Every emit carries the REASON it fired. Dedup below needs it: a page emitted
    # because a slide actually changed must never be dropped as "redundant".
    emits = []
    Y = last_emit_Y = ink = 0.0
    last_emit_i = 0                                  # for the temporal cap below
    ink_carry = 0.0                                  # ink since the last emit, for the manifest
    inks = {}                                        # emit index -> ink accumulated before it
    # EXPOSURE: the moment un-snapshotted writing first passed --ink-threshold. This, not
    # "time since the last emit", is the quantity that matters: it is how long content has
    # existed on the canvas without any frame showing it, i.e. exactly the window in which
    # something can be written AND erased unseen. Bounding it bounds the sixth loss channel.
    t_cross = None
    exposure = {}                                    # emit index -> seconds of exposure it ended

    def emit(idx: int, reason: str) -> None:
        # One place owns the bookkeeping. Threading `inks`/`t_cross`/`last_emit_i` through
        # six separate branches by hand is how the first cut of this silently disarmed the
        # pre-burst rule; a single helper makes the accounting impossible to get out of step.
        nonlocal ink_carry, t_cross, last_emit_i
        emits.append((idx, reason))
        inks[idx] = ink_carry
        exposure[idx] = (times[idx] - t_cross) if t_cross is not None else 0.0
        ink_carry = 0.0
        t_cross = None
        last_emit_i = idx

    for i in range(1, N):
        if slide_mask[i] != slide_mask[i - 1]:       # canvas <-> slide switch
            emit(i - 1, "regime")                     # complete last frame of the outgoing regime
            Y = last_emit_Y = ink = 0.0
        elif slide_mask[i]:                          # within slide mode
            if darr[i] > args.slide_threshold:        # slide-to-slide content change
                emit(i - 1, "slide")
        else:                                        # on the handwriting canvas
            if not scroll_mask[i]:
                ink += darr[i]                        # writing activity since the last snapshot
                ink_carry += darr[i]
                if t_cross is None and ink_carry > args.ink_threshold:
                    t_cross = times[i]                # un-snapshotted writing now exists
            if scroll_mask[i]:                        # coherent vertical scroll
                # Frame i-1 sits at canvas position Y (the scroll of frame i has not
                # been applied yet). Two reasons to keep it:
                #
                # (a) BURST START — content only ever leaves the canvas by scrolling
                #     off the top, so the settled frame right before a scroll burst is
                #     the last chance to see that viewport fully written. Spatial
                #     overlap alone does NOT cover this: the canvas is WRITTEN, not
                #     revealed, so an earlier snapshot of the same rows can simply show
                #     blank paper (this is what dropped L01's conditional-probability
                #     block — the region was still empty 3.5 min earlier).
                # (b) COVERAGE — a single burst can run over several frames, so keep
                #     emitting so that consecutive emitted positions stay < step apart.
                #     Testing AFTER `Y += dy` instead lets the discrete jump overshoot
                #     the threshold, which on L01 opened real gaps (spacing up to
                #     407px > ah = 360px).
                burst_start = not scroll_mask[i - 1] and ink > args.ink_threshold
                if burst_start or (Y + dyarr[i]) - last_emit_Y >= step:
                    emit(i - 1, "burst" if burst_start else "coverage")
                    last_emit_Y = Y
                    ink = 0.0
                Y += dyarr[i]
            elif darr[i] > trans:                     # page clear (big change, no shift)
                emit(i - 1, "clear")
                Y = last_emit_Y = ink = 0.0
            elif (t_cross is not None
                  and times[i] - t_cross >= args.max_static_seconds):
                # SETTLE — the sixth loss channel. Every branch above is triggered by the
                # canvas MOVING or being REPLACED. Content written on a canvas that then
                # just sits there, and is erased or overwritten in place before any of
                # those fire, is seen by no emit at all. Nothing above can catch it: a
                # gradual erase never clears --transition-threshold, and with no scroll
                # there is no burst. The spatial gate stays green throughout, because it
                # asserts WHERE the canvas was photographed and says nothing about WHEN.
                # So once real writing has accumulated, take a snapshot on the clock.
                #
                # `ink` is deliberately NOT reset here, and `ink_carry` is the trigger rather
                # than `ink`. The two accumulators exist for different rules and must not be
                # crossed: `ink` arms the PRE-BURST emit (fix (a) above) and may only be
                # cleared by an emit that actually snapshots the pre-scroll canvas. Resetting
                # it here disarmed exactly that — the first cut of this settle branch turned
                # 7 of L03's 66 burst emits into plain coverage emits, i.e. it was quietly
                # dismantling the rule whose absence once cost L01 its whole conditional-
                # probability block. A settle must only ever ADD a page, never suppress one.
                emit(i, "settle")
        # else: static writing -> captured at the next scroll step / transition / settle
    if not emits or emits[-1][0] != N - 1:
        emit(N - 1, "final")                          # always keep the final state

    # --- Dedup near-identical pages --------------------------------------------
    # ONLY where the canvas has not scrolled since the last kept page — i.e. a slide
    # held on screen, or a static canvas. Never on a moving canvas.
    #
    # The row profile (ink per row) is far too crude a signature to decide "same page"
    # while the canvas moves: two entirely different pages of handwriting have similar
    # per-row ink sums. A dedup that trusted it deleted three consecutive L05 pages
    # mid-scroll and took the whole beta_1 = Cov(x,y)/Var(x) derivation with them —
    # 165px of canvas that then existed in no frame at all.
    #
    # Bounding the drop by "candidate is < step from the last kept page" is NOT enough
    # either: the NEXT kept page can then sit up to 2*step away, which is how L05 still
    # ended up at 7% overlap. Tying the drop to zero scroll is the only form of this
    # that cannot degrade coverage at all, and it still does the job it exists for.
    #
    # SLIDES ARE EXEMPT, and that exemption is load-bearing. A slide stretch has no
    # scroll at all, so "canvas has not moved" is true for every frame of it and the
    # dedup is armed on exactly the content it is least able to judge: slides share a
    # template, so two slides differing by one added line of maths have nearly identical
    # ROW PROFILES. That is the same signature-too-crude bug that once deleted L05's OLS
    # derivation, just aimed at slides instead of handwriting — and it would eat the very
    # frames the letterbox fix exists to recover. A page emitted because a slide CHANGED
    # (d > --slide-threshold) is by construction new content and is always kept.
    # The costs are wildly asymmetric: an extra near-duplicate frame is a few tokens in
    # a subagent prompt, a deleted slide is content that exists nowhere and that nobody
    # will notice is missing. If slide pages ever get too numerous, raise
    # --slide-threshold (an honest detection knob) — do not start deleting detections.
    pages, prev_p, last_idx, last_y = [], None, None, None
    norm = ah * 255.0
    n_slide_changes = 0
    ink_dropped = 0.0
    exp_dropped = 0.0
    for rep, reason in emits:
        p = profiles[rep]
        y = float(Y_glob[rep])
        # "final" is protected for the same reason as "slide": the last frame of the
        # video is the ONLY sighting of whatever is on screen when the recording stops.
        # A lecture that ends on a slide held to EOF triggers no further slide change,
        # so that slide exists in exactly one emit — and because it shares its template
        # with the slide before it, the row profile is nearly identical and the dedup
        # happily threw it away. That silently deleted L02's closing Gamma-distribution
        # slide, which the OLD extractor still had. Found by the L02 patch agent, not by
        # the gate: no coverage rule covered the terminal frame. Keeping a genuinely
        # redundant last frame costs one PNG; dropping a real one costs content.
        # "clear" and "settle" are protected for exactly the reason "final" is: each is the
        # ONLY sighting of that canvas. A page emitted just before a clear is the last frame
        # in which the about-to-be-wiped content exists at all; a settle page is the only
        # frame of a standing canvas that may be erased in place. Both sit on a canvas that
        # has NOT scrolled (y <= last_y holds by construction), so the dedup is armed on
        # them — and it judges by the row profile, which this file has already twice
        # recorded as far too crude to trust (it ate L05's OLS derivation and L02's closing
        # Gamma slide). Leaving them unprotected would rebuild that bug on the very frames
        # the temporal fix exists to add. Costs are asymmetric: a redundant PNG is a few
        # tokens; a deleted one is content that exists nowhere and nobody notices is gone.
        # "burst" joins them, and that is not belt-and-braces. A settle page now often sits a
        # few seconds BEFORE the pre-burst page on an unscrolled canvas, so the dedup compares
        # the two and calls the burst redundant — it dropped 6 of L03's 66 burst emits the
        # first time round. Those are fix (a)'s frames: the last sighting of a viewport before
        # it scrolls away. And the row profile cannot tell "nothing changed" from "erased and
        # rewritten in the same rows" — same ink per row, different content — which is the very
        # thing a standing canvas invites. Protecting it costs a few near-duplicate PNGs.
        protected = reason in ("slide", "regime", "final", "clear", "settle", "burst")
        redundant = prev_p is not None and np.abs(p - prev_p).mean() / norm < args.dedup
        if redundant and not protected and last_y is not None and y <= last_y:
            ink_dropped += inks.get(rep, 0.0)   # keep the ink accounting honest for the gate
            exp_dropped = max(exp_dropped, exposure.get(rep, 0.0))
            continue
        t_start = times[last_idx] if last_idx is not None else times[0]
        prev_p, last_idx, last_y = p, rep, y
        is_slide = bool(slide_mask[rep])
        page = {"t_start": float(t_start), "t_end": float(times[rep]),
                "timestamp": float(times[rep]), "canvas_y": y,
                "regime": "slide" if is_slide else "canvas", "reason": reason,
                # Ink written since the previous KEPT page (a dropped page hands its ink on,
                # so a dedup that merges two windows cannot hide writing from the gate).
                "ink_since_prev": float(inks.get(rep, 0.0) + ink_dropped),
                # Longest stretch of above-threshold, un-snapshotted writing that this page
                # ended. THE temporal invariant: it must never exceed max_static_seconds.
                "exposure_s": float(max(exposure.get(rep, 0.0), exp_dropped))}
        ink_dropped = exp_dropped = 0.0
        if reason == "slide":
            # Sequence number over DETECTED slide changes, assigned here so that
            # check_coverage.py can assert the kept pages are contiguous — i.e. that
            # nothing downstream silently dropped one. Slides have no canvas_y motion,
            # so the scroll invariant cannot see them; this is their coverage invariant.
            page["slide_seq"] = n_slide_changes
            n_slide_changes += 1
        pages.append(page)

    print(f"[i] {total_screens:.1f} screens scroll + {n_slide} slide frames -> {len(pages)} pages")

    # --- Pass 2: re-extract representatives at full resolution ------------------
    manifest = []
    for idx, p in enumerate(pages, 1):
        name = f"page_{idx:03d}.png"
        extract_full_frame(args.video, p["timestamp"], frames_dir / name, args.frame_width)
        entry = {
            "index": idx, "frame": f"frames/{name}",
            "t_start": p["t_start"], "t_end": p["t_end"],
            "hms_start": hms(p["t_start"]), "hms_end": hms(p["t_end"]),
            "canvas_y": p["canvas_y"],
            "regime": p["regime"], "reason": p["reason"],
            # The coverage invariant, recorded so check_coverage.py can assert it instead
            # of guessing: canvas_y must advance by < step_px between consecutive pages.
            # Slides never move the canvas, so they get their own invariant: slide_seq
            # must come out contiguous (see the dedup note above).
            "viewport_h": float(ah),
            "step_px": float(step),
            "n_slide_changes": n_slide_changes,
            # TEMPORAL invariant (the sixth loss channel). The spatial rule above says
            # WHERE the canvas was photographed and nothing about WHEN, so content written
            # and erased in place between two emits fell through it with the gate green.
            # The extractor now forces a "settle" snapshot once writing has accumulated and
            # max_static_seconds have passed. Recorded here so check_coverage.py can ASSERT
            # that guarantee rather than trust it — and so a manifest from the old extractor
            # (no key) is a hard fail instead of a silent pass.
            "max_static_seconds": float(args.max_static_seconds),
            "ink_threshold": float(args.ink_threshold),
            "ink_since_prev": p["ink_since_prev"],
            "exposure_s": p["exposure_s"],
            # Terminal-frame invariant: the last page must reach the end of the video.
            # Whatever is on screen when the recording stops is seen by exactly ONE emit
            # (the "final" one) — no later change re-captures it. The dedup used to eat
            # that frame on a slide ending, which cost L02 its closing Gamma slide and
            # was found by a subagent, not by this gate, because NO invariant covered the
            # terminal frame. Now one does.
            "duration": float(duration),
        }
        if "slide_seq" in p:
            entry["slide_seq"] = p["slide_seq"]
        manifest.append(entry)
        tag = "slide" if p["regime"] == "slide" else "canvas"
        print(f"    page {idx:03d}  [{hms(p['t_start'])}–{hms(p['t_end'])}]  {tag}/{p['reason']}")

    (out / "pages.json").write_text(json.dumps(manifest, indent=2))
    print(f"[✓] {len(manifest)} pages -> {out/'pages.json'}")


if __name__ == "__main__":
    main()

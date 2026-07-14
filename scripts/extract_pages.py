#!/usr/bin/env python3
"""
extract_pages.py — turn an iPad lecture recording into a set of "complete page"
snapshots, ready for a vision model to transcribe.

NOTE: the CSDA recordings are mostly a SCROLLING canvas with occasional slides —
the primary extractor for them is extract_pages_scroll.py. This diff-spike script
(+ --change-budget) is the FALLBACK for genuinely discrete, page-flip recordings.

Idea
----
The professor writes incrementally on an iPad, then flips/scrolls to a new page.
  * while writing  -> small frame-to-frame difference
  * on a page flip -> large difference "spike"
We sample at low fps, find the spikes (= page transitions), and keep the frame
*just before* each spike, i.e. the page in its most complete state. Those
representative timestamps are then re-extracted at full resolution.

Deps: numpy, ffmpeg/ffprobe on PATH. matplotlib only for --diagnostic.

Typical use
-----------
  # 1) First, calibrate the threshold on ONE lecture:
  python extract_pages.py L01.mp4 --out work/L01 --diagnostic
  #    -> inspect work/L01/diff_curve.png, pick a threshold at the gap between
  #       the "writing" noise floor and the transition spikes.

  # 2) Run for real:
  python extract_pages.py L01.mp4 --out work/L01 --threshold 0.05
"""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path
import numpy as np


def ffprobe_duration(video: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def iter_gray_frames(video: str, fps: float, w: int, h: int,
                     limit: float | None = None, start: float = 0.0):
    """Yield downscaled grayscale frames as HxW uint8 arrays via an ffmpeg pipe."""
    cmd = ["ffmpeg", "-v", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if limit:
        cmd += ["-t", f"{limit:.3f}"]
    cmd += ["-i", video,
            "-vf", f"fps={fps},scale={w}:{h},format=gray",
            "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    # stdin=DEVNULL: keep ffmpeg from reading our stdin (its interactive keyboard
    # reader would otherwise eat data piped to the caller, e.g. lectures.tsv lines
    # when run inside a `while read ... done < tsv` loop).
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, bufsize=10**8)
    n = w * h
    try:
        while True:
            buf = proc.stdout.read(n)
            if len(buf) < n:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(h, w)
    finally:
        proc.stdout.close()
        rc = proc.wait()
    # If ffmpeg died mid-decode (truncated/corrupt mp4, unsupported codec, disk
    # error) the pipe just ends short — without this check we'd silently get a
    # truncated frame set and too few / wrong pages. Fail loudly instead.
    if rc not in (0, None):
        raise RuntimeError(
            f"ffmpeg exited with code {rc} while decoding {video!r} — "
            "file truncated/corrupt or unsupported codec?")


def hms(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def extract_full_frame(video: str, t: float, out_png: Path):
    # -ss before -i => fast keyframe seek; accurate enough for a static page.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", video,
         "-frames:v", "1", "-q:v", "2", str(out_png)],
        check=True, stdin=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--fps", type=float, default=1.0,
                    help="analysis sampling rate (default 1/s)")
    ap.add_argument("--dsize", default="320x180",
                    help="downscaled WxH used for the difference metric")
    ap.add_argument("--threshold", type=float, default=0.01,
                    help="normalized mean-abs-diff above which a frame is a page "
                         "transition. ALWAYS calibrate via --diagnostic first: the "
                         "right value depends on ink density and can be ~0.005–0.05.")
    ap.add_argument("--min-gap", type=int, default=3,
                    help="merge transitions closer than this many samples into one")
    ap.add_argument("--min-seg-seconds", type=float, default=6.0,
                    help="drop pages shown for less time than this (transient scrolls)")
    ap.add_argument("--dedup", type=float, default=0.02,
                    help="drop a page nearly identical (MAD<this) to the previous kept one")
    ap.add_argument("--change-budget", type=float, default=0.06,
                    help="emit an extra keyframe once ACCUMULATED frame-to-frame change "
                         "since the last one exceeds this — captures content that slowly "
                         "scrolls off the top of a page before it is cleared. 0 = disable "
                         "(pure discrete-transition mode). Calibrate from diff_curve.png.")
    ap.add_argument("--limit-seconds", type=float, default=None,
                    help="only analyse the first N seconds (quick test)")
    ap.add_argument("--start-seconds", type=float, default=0.0,
                    help="begin analysis at this offset (seek); with --limit-seconds a middle window")
    ap.add_argument("--diagnostic", action="store_true",
                    help="write diff_curve.{csv,png} and stop before full-res extraction")
    args = ap.parse_args()

    try:
        w, h = (int(x) for x in args.dsize.lower().split("x"))
    except ValueError:
        sys.exit(f"[!] --dsize must be WxH like 320x180, got {args.dsize!r}")
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    frames_dir = out / "frames"; frames_dir.mkdir(exist_ok=True)

    try:
        dur_str = hms(ffprobe_duration(args.video))
    except FileNotFoundError:
        sys.exit("[!] ffprobe/ffmpeg not found on PATH — install ffmpeg (brew install ffmpeg).")
    except (subprocess.CalledProcessError, ValueError):
        dur_str = "unknown"      # non-fatal: duration is only informational
    print(f"[i] duration {dur_str}  sampling @ {args.fps} fps  ({w}x{h} gray)")

    # --- Pass 1: read all downscaled frames, compute consecutive difference ----
    frames = list(iter_gray_frames(args.video, args.fps, w, h, args.limit_seconds, args.start_seconds))
    if len(frames) < 2:
        sys.exit("[!] too few frames — is ffmpeg working / video valid?")
    n_frames = len(frames)
    stack = np.stack(frames).astype(np.int16)          # (N, h, w)
    del frames                                         # free ~N*h*w bytes before diff temporaries
    diff = np.abs(np.diff(stack, axis=0)).mean(axis=(1, 2)) / 255.0  # (N-1,)
    times = np.arange(n_frames) / args.fps + args.start_seconds   # absolute time of each frame

    if args.diagnostic:
        csv = out / "diff_curve.csv"
        np.savetxt(csv, np.column_stack([times[1:], diff]),
                   delimiter=",", header="t_seconds,mad", comments="")
        # Robust heuristic: the "writing floor" ≈ median; transitions are the tail.
        floor = float(np.median(diff))
        suggested = round(max(8 * floor, 0.5 * float(np.percentile(diff, 99))), 4)
        n_hits = int((diff > suggested).sum())
        print(f"[i] writing floor≈{floor:.4f}  spike p99≈{np.percentile(diff,99):.4f}")
        print(f"[i] SUGGESTED --threshold {suggested}  (~{n_hits} transitions) "
              f"— verify against the plot before trusting it")
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            plt.figure(figsize=(14, 4))
            plt.plot(times[1:], diff, lw=0.8)
            plt.axhline(args.threshold, color="r", ls="--",
                        label=f"threshold={args.threshold}")
            plt.xlabel("t [s]"); plt.ylabel("mean abs diff"); plt.legend()
            plt.title("Frame-to-frame difference — spikes = page transitions")
            plt.tight_layout(); plt.savefig(out / "diff_curve.png", dpi=120)
            print(f"[i] wrote {out/'diff_curve.png'} — pick --threshold from it")
        except ImportError:
            print(f"[i] wrote {csv} (matplotlib not installed, skipped plot)")
        return

    # --- Select representative frames (hybrid) ---------------------------------
    # Emit a keyframe (a) just BEFORE each discrete transition (slide change /
    # page clear = diff spike) AND (b) whenever ACCUMULATED change since the last
    # keyframe exceeds --change-budget (captures content that slowly scrolls off
    # the top of a page before it is cleared). Pure difference signal — no scroll
    # estimation. Then merge flicker (min-gap) and drop near-duplicates (dedup).
    budget = args.change_budget if args.change_budget > 0 else float("inf")
    raw_reps, cum, n_cuts = [], 0.0, 0
    for i in range(1, n_frames):
        d = float(diff[i - 1])                           # change from frame i-1 -> i
        if d > args.threshold:                           # discrete transition
            raw_reps.append(i - 1)                        # most complete state before it
            cum = 0.0; n_cuts += 1
        else:
            cum += d
            if cum >= budget:                            # gradual accumulation / slow scroll
                raw_reps.append(i)
                cum = 0.0
    if not raw_reps or raw_reps[-1] != n_frames - 1:
        raw_reps.append(n_frames - 1)                    # always keep the final state

    pages, prev_small, last_idx = [], None, None
    for rep in raw_reps:
        if last_idx is not None and rep - last_idx <= args.min_gap:
            continue                                     # merge flicker / too-close reps
        small = stack[rep]
        if prev_small is not None and np.abs(small - prev_small).mean() / 255.0 < args.dedup:
            continue                                     # near-duplicate (static / scrolled back)
        t_start = times[last_idx] if last_idx is not None else 0.0
        prev_small, last_idx = small, rep
        pages.append({"t_start": float(t_start), "t_end": float(times[rep]),
                      "timestamp": float(times[rep])})

    if n_cuts == 0 and budget == float("inf"):
        print("[!] no transitions detected — lower --threshold or set --change-budget > 0.")
    print(f"[i] {n_cuts} discrete transitions (+budget emits) -> {len(pages)} candidate pages")

    # --- Pass 2: re-extract representatives at full resolution ------------------
    manifest = []
    for i, p in enumerate(pages, 1):
        name = f"page_{i:03d}.png"
        extract_full_frame(args.video, p["timestamp"], frames_dir / name)
        manifest.append({
            "index": i, "frame": f"frames/{name}",
            "t_start": p["t_start"], "t_end": p["t_end"],
            "hms_start": hms(p["t_start"]), "hms_end": hms(p["t_end"]),
        })
        print(f"    page {i:03d}  [{hms(p['t_start'])}–{hms(p['t_end'])}]")

    (out / "pages.json").write_text(json.dumps(manifest, indent=2))
    print(f"[✓] {len(manifest)} pages -> {out/'pages.json'}")


if __name__ == "__main__":
    main()

# scriptify

Turns a semester of lecture recordings into a LaTeX script: prose, theorems, derivations and
redrawn figures, with each block annotated with the timestamp of the video passage it came from.

```
videos/L03.mp4
   │  whisper            → work/L03/L03.srt            timestamped transcript
   │  page extraction    → work/L03/frames/*.png       the "finished pages" of the board
   │  alignment          → work/L03/pages_context.md   what was said while each page was up
   │  coverage gate      ✓ nothing was silently lost
   │  LLM (Claude Code)  → work/L03/L03.tex            the script
   └─ latexmk            → work/main.pdf
```

Transcription, page extraction, transcript alignment and the coverage check (stages 1–3) are
deterministic Python. Stage 4 — reading the lecturer's handwriting off the extracted frames and
writing it up as mathematics — is done by the `lecture-processor` subagent defined in
[`.claude/agents/lecture-processor.md`](.claude/agents/lecture-processor.md), one instance per
lecture. It requires a vision-capable model: the frames are the primary source and the transcript
serves to disambiguate them.

Lecture material stays out of this repo. `work/` and `videos/` are gitignored and a pre-commit
hook refuses to commit their contents; see
[Keeping lecture content out of git](#keeping-lecture-content-out-of-git).

## Requirements

- `ffmpeg`; `poppler` additionally for lectures delivered as PDFs
- Python 3 with NumPy; matplotlib for the `--diagnostic` plots
- A Whisper implementation that produces SRT with timestamps. `transcribe.sh` uses
  [mlx-whisper](https://github.com/ml-explore/mlx-examples) (Apple Silicon only); on other
  hardware use [whisper.cpp](https://github.com/ggml-org/whisper.cpp) or `openai-whisper` — the
  exact commands are at the bottom of [`scripts/transcribe.sh`](scripts/transcribe.sh).
  Plain-text output is not sufficient, since the alignment step needs the timestamps.
- TeX: `latexmk` plus `amsmath`/`amssymb`/`amsthm`, `mathtools`, `physics`, `bbm`, `tikz`
- Claude Code (or an equivalent agent runner) for stage 4

## Setup

```bash
brew install ffmpeg poppler
pip install mlx-whisper numpy matplotlib
scripts/install-hooks.sh     # pre-commit hook that blocks lecture content; run this first
```

## Usage

**1. Copy the recordings in and build the manifest.**

```bash
mkdir -p videos work
cp ~/Downloads/CSDA26_*.mp4 videos/
python scripts/build_manifest.py          # videos/ -> work/lectures.tsv
```

The lecture number is taken from the last digit run in the filename (`CSDA2023-5.mp4` → `L05`),
so recordings from mixed years form one series. Edit `work/lectures.tsv` if a mapping comes out
wrong.

**2. Check the diagnostics before running the full batch** (optional, recommended for a new
recording setup):

```bash
scripts/prepare_all.sh --diagnostic       # -> work/L##/scroll_curve.png
```

Blue is the cumulative scroll staircase, orange marks the stretches classified as slides. If the
orange bands don't line up with the actual slides, or the staircase is flat where the lecturer
clearly scrolled, adjust the flags (see [Configuration](#configuration)) before running the batch.

**3. Run stages 1–3.**

```bash
scripts/prepare_all.sh                    # all lectures
scripts/prepare_all.sh L03 L05            # or a subset
```

Transcription dominates the runtime: a 90-minute lecture takes roughly 15–25 minutes on an M2
with `large-v3`, so a full course is an overnight job. Transcription is idempotent — existing
`.srt` files are kept. Each lecture ends with `check_coverage.py`; a red gate means content is
missing from the frames and has to be fixed before stage 4.

**4. Dispatch one subagent per prepared lecture** (in Claude Code, from the project root). The
folder path has to be in the prompt, since it is the agent's only input:

```
> Process the lecture in work/L03. Read pages.json, all frames/*.png and
> pages_context.md. Write work/L03/L03.tex according to CLAUDE.md.
```

Lectures are independent and can run in parallel. Process a single lecture first, compile it and
read it before dispatching the rest: that is where the style, notation and language conventions
in [`CLAUDE.md`](CLAUDE.md) get settled, and changing them later means a consistency pass over
the whole corpus.

**5. Consistency pass, then build.** This pass is serial, since it concerns the document as a
whole: unify symbols across lectures, resolve `\label`/`\ref`, and work through the `% TODO(...)`
markers with [`peek.py`](scripts/peek.py). Then:

```bash
scripts/build.sh                          # -> work/main.pdf
```

## How it works

The difficult part of the extraction is completeness. A recording of a scrolling handwriting
canvas has no page boundaries, so "which frames are the pages?" has no obvious answer, and a
wrong answer removes content from the finished script without leaving a visible gap in the prose.
Six distinct bugs of this kind have occurred in this project (see
[Known failure modes](#known-failure-modes)); the extractor's guards exist because of them.

[`extract_pages_scroll.py`](scripts/extract_pages_scroll.py) classifies frames using two signals:

- **Scroll vs. discrete change.** A scroll is a coherent vertical shift: the row profiles
  translate, so a 1-D cross-correlation detects it with high confidence. A slide change or a page
  clear replaces content without a coherent shift. The two are distinguished by the presence of
  the shift rather than the magnitude of the change, because the magnitudes overlap.
- **Canvas vs. slide.** The slide app renders 4:3 content with black letterbox bars; the
  handwriting canvas is full-bleed. Across 12 test recordings the fraction of near-black rows was
  exactly 0.000 for the canvas and 0.033–0.056 for slides, with nothing in between — a structural
  property of the recording setup rather than a tuned threshold.

On the canvas, snapshots are emitted under four rules — coverage, pre-burst, clear and settle —
each added in response to a specific content loss (see the failure-mode table).
[`check_coverage.py`](scripts/check_coverage.py) then verifies from the manifest that consecutive
pages overlap, that every detected slide change reached a page, that the last page extends to the
end of the video, and that no writing sat unphotographed on a static canvas for longer than
`--max-static-seconds`.

## Configuration

The defaults are tuned for an iPad recording of a scrolling, light-background handwriting canvas
with occasional light 4:3 slides. Recordings of that kind should work without changes; the
defaults processed a full 13-lecture course. Per-lecture flag overrides go in column 3 of
`work/lectures.tsv` and are passed directly to the extractor.

### Choosing an extractor

| Recording | Use |
|---|---|
| Scrolling canvas (Notability/GoodNotes/OneNote), with or without slides | `extract_pages_scroll.py` — the default |
| Purely discrete: slide deck, blackboard photos, whiteboard wiped per page — no scrolling at all | `extract_pages.py` (diff-spike fallback) |
| Lecture handed out as a PDF, never recorded | `pdf_to_pages.py` — same `work/L##/` layout, page anchors instead of timestamps |

### Extractor flags

| Flag | Default | Notes |
|---|---|---|
| `--overlap` | `0.50` | Guaranteed overlap between consecutive snapshots. At 0.5 or above, every canvas row is photographed twice at different times; below about 0.35, freshly written bottom lines get a single sighting and are lost if an emit fires just before them. Raise it when the lecturer writes fast and scrolls in big jumps. Page count grows linearly with it. |
| `--letterbox-threshold` | `0.02` | Fraction of near-black full-width rows above which a frame counts as a slide; the primary canvas/slide signal. Adjust it when the slides are not letterboxed (full-bleed deck) or the canvas is dark. The `letterbox` column of `scroll_curve.csv` shows the measured values: canvas should be ≈0.000, slides clearly above. |
| `--brightness-threshold` | `0.72` | OR-fallback for dark full-bleed slides. Raise it to catch them; `0` disables it. |
| `--transition-threshold` | `0.04` | Page-clear detection. Lower it if clears are missed, raise it if normal writing is mistaken for a clear. Has to stay above the writing noise floor of about 0.01. |
| `--slide-threshold` | `0.008` | Slide-to-slide change detection. Slides share a template, so the diffs are small. If many near-identical slide pages come out, raise this rather than deleting detections. |
| `--max-static-seconds` | `120` | The temporal guarantee (failure mode 6): once real writing has accumulated, a snapshot is forced at least this often even if nothing moves. Lower is safer and produces more pages. |
| `--ink-threshold` | `0.01` | How much writing has to accumulate to arm the pre-burst snapshot. This is not a page budget: raising it far enough to reduce the page count tears coverage gaps. To reduce agent load, split long lectures with `plan_splits.py` instead. |
| `--frame-width` | `1100` | Output PNG width, and the main token-cost lever: the agent pays about w·h/750 tokens per frame. 1100 keeps handwriting legible; `0` means native resolution. |
| `--dedup` | `0.02` | Threshold for dropping near-identical pages. Applies only to a canvas that has not scrolled, and never to slide/clear/settle/burst/final pages; both restrictions guard against failure modes 3 and 5. |
| `--fps`, `--awidth` | `2.0`, `480` | Analysis sampling rate and width. Scrolling faster than about half a screen per 0.5 s needs a higher `--fps`. |

### Dark-mode recordings

Both slide signals assume a light canvas. In a dark-mode recording nearly every row is
near-black, so `letterbox ≈ 1.0` and the brightness falls below 0.72: the extractor classifies
the entire lecture as slides and the scroll path never runs. The diagnostic plot makes this
obvious (no blue staircase, everything orange). Disable both slide signals and run canvas-only:

```
L07  videos/L07.mp4  --letterbox-threshold 1.1 --brightness-threshold 0  dark mode
```

A dark-mode recording that also contains slides needs the row-darkness cutoff (hardcoded `0.25`
in `extract_pages_scroll.py`) inverted for that setup — a code change, not a flag.

## Known failure modes

All six of the following have actually occurred in this project, and all caused the same damage:
content silently missing from the finished `.tex`, with no visible gap in the prose. The guards
that fix them are why the extractor looks over-careful. Before simplifying any of them, read the
docstring of [`extract_pages_scroll.py`](scripts/extract_pages_scroll.py) — and any change to the
extractor has to be purely additive: no existing emit reason may stop firing.

| # | What went wrong | What it cost | Fix / guard |
|---|---|---|---|
| 1 | Coverage was checked after applying a scroll instead of before emitting the preceding frame | Four canvas strips were never photographed; the σ-algebra and probability axioms were missing | Test before applying; `check_coverage.py` asserts overlap |
| 2 | Overlap alone was assumed sufficient | The conditional-probability block: the region was covered, but by a frame taken 3.5 minutes earlier, while it was still blank | Pre-burst rule: snapshot before a scroll burst that follows real writing |
| 3 | Dedup compared row profiles across a moving canvas | Three consecutive pages, including the entire OLS derivation | Dedup only on a canvas that has not scrolled; slide/clear/settle/burst/final pages exempt |
| 4 | Slide detection keyed on brightness, but the slides are white | The rule never fired in 11 of 12 lectures; slide changes fell into the canvas path, close to `--transition-threshold`, and about every second slide was dropped | Letterbox signal (structural, binary); `slide_seq` has to come out contiguous |
| 5 | Dedup deleted the last frame of the video | The closing slide, seen by exactly one emit and nearly identical to its predecessor | Pages with `reason="final"` are dedup-protected; the last page has to reach the video's end |
| 6 | Every invariant was spatial; none constrained time | Content written on a standing canvas and erased before the next emit appears in no frame, while the gate stays green | The settle emit and the `exposure_s` invariant (`--max-static-seconds`) |

Failure mode 6 has a corollary for stage 4: an empty or half-empty frame says something about the
extractor, not about the lecture. Of the first 21 "the lecturer never wrote this down" claims in
this project, nine were false — the material had been on the board the whole time. When a frame
looks incomplete, check the video:

```bash
python scripts/peek.py L03 01:04:42                 # contact sheet ±30 s, to locate the moment
python scripts/peek.py L03 01:05:07 --frame         # full-res single frame, to read it
python scripts/peek.py L03 01:05:07 --frame --crop 0,0.4,0.6,1.0 --scale 2
```

### Other pitfalls

- Whisper can fall into hallucination loops on files longer than about 20 minutes, repeating one
  sentence for minutes. `transcribe.sh` passes `--condition-on-previous-text False` (ml-explore's
  fix for this) so the model stops feeding its previous window back as a prompt. Keep the flag.
- Around 80 pages of frames exceed a single agent's context. Split long lectures with
  `plan_splits.py`, give each range its own agent, and merge with `merge_fragments.py`. The merge
  refuses fragments with duplicate `\label`s or repeated headings, the symptom of two agents both
  writing the seam. The inverse also holds: once `L##.tex` has been edited directly, it is the
  source of truth, and a later `merge_fragments.py` run would silently roll those edits back.
- The mathematics needs proofreading. The agent is instructed to flag illegible handwriting as
  `% TODO(L03 @ 00:41:02): exponent unclear` rather than guess, but a confident misreading is
  possible. The `% [L03 @ hh:mm:ss]` anchor on each block lets you check any formula against the
  video in seconds.
- Only vertical scrolling is detected; horizontal panning is not handled.

## Keeping lecture content out of git

Recordings, transcripts, frames and the generated script are derivative works of the lecturer's
teaching and must not end up in a public repo. The split is structural: `work/` and `videos/`
hold all course content, everything outside them is the tool.

- The `.gitignore` excludes both directories wholesale and additionally blocks the content file
  types by name.
- `scripts/install-hooks.sh` installs a pre-commit hook that rejects content files even when
  staged with `git add -f`, which `.gitignore` alone does not prevent.
- Everything course-specific — `main.tex`, `lectures.tsv`, notes — lives inside `work/`, so
  nothing course-shaped exists outside it.

To version and back up the script without publishing it, make `work/` its own repository on a
private remote. The outer repo ignores `work/`, so it never sees the inner one:

```bash
gh repo create <you>/my-course-content --private
cd work
git init && git add . && git commit -m "lecture content"
git remote add origin git@github.com:<you>/my-course-content.git
git push -u origin main
```

`work/.gitignore` excludes `frames/` from the inner repo as well: roughly 200 MB of PNGs that are
a deterministic function of (video, extractor, flags) and are regenerated by
`scripts/prepare_all.sh`. The recordings are the one input that cannot be regenerated; back them
up outside git. Note that a branch of a public repository is public — a private remote is the
only private option.

## License

The tool is MIT-licensed (see [LICENSE](LICENSE)). The lecture material processed with it is not
covered by that license: recordings of a lecture, transcripts of them, frames from them and a
LaTeX script derived from them are all derivative works of someone else's teaching. Making them
for personal use is often permitted (in Germany, § 60a UrhG covers a fair amount of personal and
teaching use); publishing them usually requires the lecturer's permission. The repo defaults to
keeping the output private for that reason.

Dependencies are installed by the user, none are bundled or redistributed here:

| Dependency | Licence | Notes |
|---|---|---|
| [Whisper](https://github.com/openai/whisper) (OpenAI) | MIT | Code and model weights; no restriction on using the transcripts. |
| [mlx-whisper](https://github.com/ml-explore/mlx-examples) | MIT | The Apple-Silicon runtime `transcribe.sh` calls. |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | MIT | Documented alternative. |
| FFmpeg | LGPL-2.1+ (GPL with some build flags) | Called as a subprocess, never linked; no copyleft obligations. |
| poppler (`pdftoppm`) | GPL-2.0 | Also a subprocess; the default PDF renderer for that reason. |
| NumPy, matplotlib | BSD / PSF-style | Permissive. |
| PyMuPDF | AGPL-3.0 or paid commercial | Opt-in only (`pdf_to_pages.py --renderer pymupdf`). It is imported rather than run as a subprocess, so the AGPL would attach to a distributed combined work; the default poppler path avoids this entirely. |

## Repo layout

```
scripts/
  prepare_all.sh            stages 1–3 for every lecture in work/lectures.tsv
  transcribe.sh             Whisper (mlx) -> work/L##/L##.srt
  extract_pages_scroll.py   primary extractor (scroll + slide hybrid); see its docstring
  extract_pages.py          fallback for purely discrete recordings
  pdf_to_pages.py           PDF-delivered lecture -> the same work/L##/ layout
  align_transcript.py       pages.json + .srt -> pages_context.md
  check_coverage.py         the coverage gate: asserts nothing was silently lost
  peek.py                   inspect the video at a timestamp (contact sheet / full frame)
  build_manifest.py         videos/ -> work/lectures.tsv
  plan_splits.py            split a long lecture across several agents
  merge_fragments.py        merge the fragments back, refusing unsafe seams
  build.sh                  latexmk -> work/main.pdf
  install-hooks.sh          pre-commit guard against committing content
templates/                  main.tex, lectures.tsv — copy into work/ and edit
preamble_stats_addon.tex    theorem environments and statistics macros
CLAUDE.md                   conventions binding every subagent; edit for your course
.claude/agents/             the lecture-processor subagent definition
work/                       course content (gitignored; see above)
videos/                     recordings (gitignored)
```

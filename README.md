# scriptify

Turn a semester of lecture recordings into a real LaTeX script — not a transcript dump, but
prose, theorems, derivations and redrawn figures, with every block anchored back to the
timestamp it came from.

The pipeline is deterministic where it can be (transcription, page extraction, alignment,
coverage checks) and uses an LLM only where judgement is genuinely needed: reading the
lecturer's handwriting off the frames and writing it up as mathematics.

```
videos/L03.mp4
   │  whisper            → work/L03/L03.srt            timestamped transcript
   │  page extraction    → work/L03/frames/*.png       the "finished pages" of the board
   │  alignment          → work/L03/pages_context.md   page ⇄ what was said while it was up
   │  coverage gate      ✓ nothing was silently lost
   │  LLM (Claude Code)  → work/L03/L03.tex            the actual script
   └─ latexmk            → work/main.pdf
```

**An LLM is required for the last step.** Stages 1–3 are plain Python and produce the
*evidence*; turning that evidence into mathematics is what the `lecture-processor` subagent in
[`.claude/agents/`](.claude/agents/lecture-processor.md) does. It needs a vision-capable model —
it reads the frames as images, and the transcript only to disambiguate what it sees.

> **Your lecture material never belongs in this repo.** Recordings, transcripts, frames and the
> finished script are the lecturer's intellectual property. `work/` and `videos/` are gitignored
> wholesale and a pre-commit hook refuses to commit them. See
> [Keeping your content out of git](#keeping-your-content-out-of-git).

---

## What it actually does

The hard part is not OCR, it is **not losing anything** — silently. A recording of a scrolling
handwriting canvas has no page boundaries, so "which frames are the pages?" is a real question,
and every wrong answer deletes mathematics that then simply *is not in the script*, looking like
perfectly normal prose. This project has lost content six different ways (see
[Known failure modes](#known-failure-modes)); the extractor and its guards are shaped by those
six, and the comments explaining why are the point, not clutter.

Two signals do the work ([`scripts/extract_pages_scroll.py`](scripts/extract_pages_scroll.py)):

* **Scroll vs. discrete change.** A scroll is a *coherent* vertical shift — the row profiles
  translate, so a 1-D cross-correlation finds it with high confidence. A slide change or a page
  clear replaces the content *without* a coherent shift. The two are told apart by the shift, not
  by magnitude (their magnitudes overlap, so a threshold alone cannot do it).
* **Canvas vs. slide: the letterbox.** The slide app renders 4:3 content with black bars; the
  handwriting canvas is full-bleed. Measured across 12 recordings this is binary — the canvas has
  *exactly* 0.000 near-black rows, slides sit at 0.033–0.056, nothing in between. It is a
  structural fact of the recording setup, not a tuned threshold.

On the canvas, snapshots are emitted under four rules — coverage, pre-burst, clear and settle —
each of which exists because its absence cost real content. Then
[`check_coverage.py`](scripts/check_coverage.py) asserts, from the manifest, that nothing fell
through: consecutive pages must overlap, every detected slide change must reach a page, the last
page must reach the end of the video, and no writing may have sat on a standing canvas
unphotographed for longer than `--max-static-seconds`.

---

## Setup

```bash
brew install ffmpeg poppler          # poppler only for PDF-delivered lectures
pip install mlx-whisper numpy        # matplotlib too, if you want --diagnostic plots
chmod +x scripts/*.sh
scripts/install-hooks.sh             # refuses to commit lecture content. Do this first.
```

`mlx-whisper` is Apple-Silicon only. On other hardware use
[whisper.cpp](https://github.com/ggml-org/whisper.cpp) or `openai-whisper` — the exact commands
are at the bottom of [`scripts/transcribe.sh`](scripts/transcribe.sh). Anything that emits **SRT
with timestamps** works; plain text does not, there is nothing to align to.

TeX: `latexmk` plus `amsmath/amssymb/amsthm`, `mathtools`, `physics`, `bbm`, `tikz`.

---

## Typical workflow

**1. Drop the recordings in and build the manifest.**

```bash
mkdir -p videos work
cp ~/Downloads/CSDA26_*.mp4 videos/
python scripts/build_manifest.py          # videos/ -> work/lectures.tsv
```

The lecture number is the last digit-run in the filename (`CSDA2023-5.mp4` → `L05`), so mixed
years still form one series. Edit `work/lectures.tsv` if a mapping is wrong.

**2. Look before you batch** (optional, but it is two minutes and it is how you find out your
recording is not like mine):

```bash
scripts/prepare_all.sh --diagnostic       # -> work/L##/scroll_curve.png
```

Blue is the cumulative scroll staircase, orange the slide stretches. If the orange bands don't
line up with the actual slides, or the staircase is flat when the lecturer clearly scrolled, go
to [Knobs](#knobs) — do not run the batch yet.

**3. Run the batch** (transcription dominates: a 90-min lecture is ~15–25 min on an M2 with
`large-v3`, so a full course is an overnight job).

```bash
scripts/prepare_all.sh                    # all lectures
scripts/prepare_all.sh L03 L05            # or just these
```

Per lecture this transcribes (idempotent — an existing `.srt` is kept), extracts pages, aligns
the transcript, and **gates on `check_coverage.py`**. A red gate means content is missing from
the frames; fix that before spending tokens on it.

**4. Hand each prepared lecture to a subagent** (in Claude Code, from the project root). The
folder path is the only channel to the agent, so it must be in the prompt:

```
> Process the lecture in work/L03. Read pages.json, all frames/*.png and
> pages_context.md. Write work/L03/L03.tex according to CLAUDE.md.
```

Lectures are independent, so these run in parallel — but do **one first, alone**, compile it, and
read it. That is where you settle the style, the notation and the language conventions in
[`CLAUDE.md`](CLAUDE.md); every later agent inherits them, and fixing them afterwards is a
consistency pass over the whole corpus.

**5. Consistency pass, then build** (serial, not parallel — it is about the document as a whole):
unify symbols across lectures, resolve `\label`/`\ref`, work through the `% TODO(...)` markers
with [`peek.py`](scripts/peek.py), then

```bash
scripts/build.sh                          # -> work/main.pdf
```

---

## Knobs

Defaults are tuned for **an iPad recording of a scrolling, light-background handwriting canvas
with occasional light 4:3 slides**. If your recordings look like that, change nothing — the
defaults ran a whole 13-lecture course. Per-lecture overrides go in column 3 of
`work/lectures.tsv`; they are passed straight to the extractor.

### Which extractor

| Recording | Use |
|---|---|
| Scrolling canvas (Notability/GoodNotes/OneNote), with or without slides | `extract_pages_scroll.py` — the default |
| Purely discrete: slide deck, blackboard photos, whiteboard wiped per page — no scrolling at all | `extract_pages.py` (diff-spike fallback) |
| Lecture handed out as a PDF, never recorded | `pdf_to_pages.py` — same `work/L##/` layout, page anchors instead of timestamps |

### The knobs that matter

| Flag | Default | Change it when |
|---|---|---|
| `--overlap` | `0.50` | The lecturer writes fast and scrolls in big jumps. This is the *guaranteed* overlap between consecutive snapshots; ≥0.5 gives every canvas row two sightings at different times. Below ~0.35 freshly written bottom lines get one chance and are lost if the emit fires just before them. Costs pages linearly. |
| `--letterbox-threshold` | `0.02` | **Your slides are not letterboxed** (full-bleed deck), or your **canvas is dark**. This is the primary canvas/slide signal: the fraction of near-black full-width rows. Check the `letterbox` column of `scroll_curve.csv` — canvas must be ≈0.000, slides clearly above. |
| `--brightness-threshold` | `0.72` | Only an OR-fallback for **dark full-bleed slides**. Raise it to catch them; set it to `0` to disable. |
| `--transition-threshold` | `0.04` | Page clears are missed (lower it) or normal writing is mistaken for a clear (raise it). Must stay above the writing noise floor, ~0.01. |
| `--slide-threshold` | `0.008` | Slide-to-slide change detection. Slides share a template, so the diff is small. Too many near-identical slide pages? Raise *this* — do not start deleting detections. |
| `--max-static-seconds` | `120` | The temporal guarantee (loss channel #6). Once real writing has accumulated, force a snapshot at least this often even if nothing moves. Lower = safer, more pages. |
| `--ink-threshold` | `0.01` | How much writing must accumulate to arm the pre-burst snapshot. **Not a page budget.** Raising it far enough to reduce pages tears coverage gaps — split long lectures instead (`plan_splits.py`). |
| `--frame-width` | `1100` | Output PNG width. The agent pays ~`w·h/750` tokens per frame, so this is the main cost lever. 1100 keeps handwriting legible; `0` = native. |
| `--dedup` | `0.02` | Near-identical pages are dropped — but only on a canvas that has **not scrolled**, and never for slide/clear/settle/burst/final pages. Both restrictions are load-bearing (see below). |
| `--fps`, `--awidth` | `2.0`, `480` | Analysis sampling rate/width. Faster scrolling than ~half a screen per 0.5 s needs a higher `--fps`. |

### Dark-mode canvas — the one case the defaults get wrong

Both slide signals assume a **light** canvas. If you record in dark mode, nearly every row is
near-black, so `letterbox ≈ 1.0` and `brightness < 0.72`: the extractor classifies the *entire
lecture* as slides, and the scroll path never runs. The diagnostic shows it instantly (no blue
staircase, everything orange). Disable both slide signals and run canvas-only:

```
L07  videos/L07.mp4  --letterbox-threshold 1.1 --brightness-threshold 0  dark mode
```

If a dark-mode recording *also* has slides, the row-darkness cutoff (hardcoded `0.25` in
`extract_pages_scroll.py`) has to be inverted for your setup — that is a code change, not a flag.

---

## Known failure modes

Every one of these has actually happened here, and every one did the same damage: **silent
content loss** that looked like normal prose in the finished `.tex`. All six are fixed; the
guards that keep them fixed are why the code looks over-careful. Do not "simplify" them without
reading [`extract_pages_scroll.py`](scripts/extract_pages_scroll.py)'s docstring first — and when
you change the extractor, check that your change is **purely additive**: no existing emit reason
may stop firing.

| # | What went wrong | What it cost | Fix / guard |
|---|---|---|---|
| 1 | Coverage was checked *after* applying the scroll, but the frame *before* it was emitted | 4 canvas strips never photographed; the σ-algebra and probability axioms gone | Test before applying; `check_coverage.py` asserts overlap |
| 2 | Overlap alone was assumed sufficient | The whole conditional-probability block — the region *was* covered, but by a frame taken 3.5 min earlier, when it was still blank | **Pre-burst rule**: the canvas is *written*, not *revealed*, so snapshot before a scroll burst that follows real writing |
| 3 | Dedup compared row profiles across a *moving* canvas | Three consecutive pages and the entire OLS derivation | Dedup only on a canvas that has not scrolled; slide/clear/settle/burst/final pages exempt |
| 4 | Slide detection keyed on brightness — the slides are *white*, not dark | The rule never fired in 11 of 12 lectures; slide changes fell into the canvas path, where they land *right on* `--transition-threshold` → roughly every second slide silently dropped | **Letterbox** signal (structural, binary); `slide_seq` must come out contiguous |
| 5 | Dedup deleted the *last* frame of the video | The closing slide, seen by exactly one emit and sharing its template with the one before it | `reason="final"` is dedup-protected; the last page must reach the video's end |
| 6 | Every invariant was **spatial** — none said anything about *time* | Content written on a standing canvas and erased before the next emit is in *no frame*, with the gate green. **9 of 21 "never written on the board" TODOs were false** — the material had been there the whole time | `settle` emit + the `exposure_s` invariant (`--max-static-seconds`) |

**The meta-lesson (#6 is the expensive one).** An empty or half-empty frame is a fact about the
*extractor*, not about the *lecture*. An agent that concludes "the lecturer never wrote this down"
from a blank frame is fabricating. "Check the video" is not a note to a human — it is executable:

```bash
python scripts/peek.py L03 01:04:42                 # contact sheet ±30 s → locate the moment
python scripts/peek.py L03 01:05:07 --frame         # full-res single frame → read it
python scripts/peek.py L03 01:05:07 --frame --crop 0,0.4,0.6,1.0 --scale 2
```

### Other things that will bite you

* **Whisper hallucination loops on long files.** Files over ~20 min can make Whisper repeat a
  sentence for minutes. `transcribe.sh` passes `--condition-on-previous-text False` (ml-explore's
  own fix): the model stops feeding its previous window back as a prompt. Do not remove it.
* **Context limits on long lectures.** ~80 pages of frames will not fit one agent's context.
  Split the lecture (`plan_splits.py`), let one agent take each range, and merge with
  `merge_fragments.py` — which *refuses* to merge fragments with duplicate `\label`s or repeated
  headings, because that is what "both agents wrote the seam" looks like. Beware the opposite
  failure, too: if you later edit `L##.tex` directly, a stray `merge_fragments.py` run will
  silently roll that work back. `L##.tex` is the source of truth.
* **The maths still needs proofreading.** The agent reads handwriting; handwriting is ambiguous.
  It is instructed to flag uncertainty as `% TODO(L03 @ 00:41:02): exponent unclear` rather than
  guess — but a confident misreading is possible, and the `% [L03 @ hh:mm:ss]` anchors on every
  block exist precisely so you can check one in seconds.
* **Only vertical scrolling is handled.** Horizontal panning is not detected.

---

## Keeping your content out of git

The recordings are someone else's work. Treat the split as structural rather than as something
you have to remember:

**`work/` and `videos/` are yours. Everything else is the tool.**

* the public repo `.gitignore`s both, wholesale, and re-blocks every content file type by name;
* `scripts/install-hooks.sh` installs a pre-commit hook that refuses to commit them anyway
  (`.gitignore` is silent about already-staged files and does nothing against `git add -f`);
* everything course-specific — `main.tex`, `lectures.tsv`, your notes — lives *inside* `work/`,
  so there is nothing course-shaped left outside it to leak.

To version and back up your script without publishing it, make `work/` its own repo pushed to a
**private** remote. The outer repo ignores `work/`, so it never sees the inner one:

```bash
gh repo create <you>/my-course-content --private
cd work
git init && git add . && git commit -m "lecture content"
git remote add origin git@github.com:<you>/my-course-content.git
git push -u origin main
```

`work/.gitignore` keeps `frames/` out of even that repo: ~200 MB of PNGs that are a deterministic
function of (video, extractor, flags) and are regenerated by `scripts/prepare_all.sh`. The
recordings themselves are the one thing you cannot regenerate — back those up outside git.

A *branch* of a public repo is public. There is no such thing as a private branch.

---

## Licensing and copyright

**This tool** is MIT (see [LICENSE](LICENSE)).

**Your lecture material is not.** Recordings of a lecture, transcripts of them, frames from them
and a LaTeX script derived from them are all derivative works of someone else's teaching. Whether
you may make them for yourself is one question (in Germany, § 60a UrhG covers a fair amount of
personal/teaching use); whether you may *publish* them is a different one, and the answer is
usually no without the lecturer's permission. Ask. Keep the output private by default — this repo
is built so that is the path of least resistance.

**Dependencies** — none of them are bundled or redistributed here; you install them yourself.

| Dependency | Licence | Notes |
|---|---|---|
| [Whisper](https://github.com/openai/whisper) (OpenAI) | MIT | Model weights and code are MIT — no restriction on using the transcripts. |
| [mlx-whisper](https://github.com/ml-explore/mlx-examples) | MIT | The Apple-Silicon runtime we call. |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | MIT | Documented alternative. |
| FFmpeg | LGPL-2.1+ (GPL with some build flags) | Called as a **subprocess**, never linked — no copyleft reaches your code. |
| poppler (`pdftoppm`) | GPL-2.0 | Also a subprocess. Default PDF renderer for that reason. |
| NumPy, matplotlib | BSD / PSF-style | Permissive. |
| **PyMuPDF** | **AGPL-3.0** or paid commercial | **Opt-in only** (`pdf_to_pages.py --renderer pymupdf`). It is *imported*, so the AGPL would attach to a distributed combined work. The default poppler path avoids this entirely; you never need PyMuPDF. |

---

## Repo layout

```
scripts/
  prepare_all.sh            Stages 1–3 over every lecture in work/lectures.tsv
  transcribe.sh             Whisper (mlx) -> work/L##/L##.srt
  extract_pages_scroll.py   PRIMARY extractor: scroll + slide hybrid   ← read the docstring
  extract_pages.py          fallback for purely discrete recordings
  pdf_to_pages.py           PDF-delivered lecture -> same work/L##/ layout
  align_transcript.py       pages.json + .srt -> pages_context.md
  check_coverage.py         THE GATE: asserts nothing was silently lost
  peek.py                   look at the VIDEO at a timestamp (contact sheet / full frame)
  build_manifest.py         videos/ -> work/lectures.tsv
  plan_splits.py            split a long lecture across several agents
  merge_fragments.py        merge them back, refusing unsafe seams
  build.sh                  latexmk -> work/main.pdf
  install-hooks.sh          pre-commit guard against committing content
templates/                  main.tex, lectures.tsv — copy into work/ and edit
preamble_stats_addon.tex    theorem environments + statistics macros
CLAUDE.md                   the conventions every subagent is bound by  ← edit for your course
.claude/agents/             the lecture-processor subagent definition
work/                       YOUR CONTENT. gitignored. private repo of its own.
videos/                     YOUR RECORDINGS. gitignored.
```

# scriptify — conventions for the lecture-processing agents

This file is loaded automatically by every `lecture-processor` subagent. The **Language &
notation** and **LaTeX conventions** sections below are the *binding shared basis* — they are what
keeps independently-processed lectures reading like one script. Do not deviate from them per
lecture; change them here, once, before the fan-out.

Operating the pipeline (setup, knobs, failure modes) is the [README](README.md). This file is
about the **output**.

> **Edit this file for your course.** The conventions below are a working profile, not law: they
> produce **German prose with English technical terms**, statistics notation, `article` class.
> Change them to suit — but change them *before* Stage 4, because afterwards it is a consistency
> pass over the whole corpus.

## Layout

```
videos/            L01.mp4 …                          (gitignored — your recordings)
work/              YOUR CONTENT (gitignored; keep it in a PRIVATE repo)
  lectures.tsv       lecture ⇄ video mapping + per-lecture extractor flags
  main.tex           binds ../preamble_stats_addon.tex + every L##.tex
  L##/
    L##.srt              Whisper transcript (timestamps!)
    frames/page_###.png  the extracted "finished pages"
    pages.json           manifest (page ⇄ time window ⇄ why it was emitted)
    pages_context.md     page ⇄ what was said while it was on screen   ← subagent input
    L##.tex              the subagent's output — THE SOURCE OF TRUTH
scripts/           the pipeline (see README)
preamble_stats_addon.tex   theorem environments + statistics macros
```

## Pipeline

**Stages 1–3 (deterministic, no LLM):** `scripts/prepare_all.sh` → transcript, extracted pages,
aligned context, and the `check_coverage.py` gate. **A lecture whose gate is red does not go to
Stage 4** — content is missing from the frames, and no amount of LLM will invent it honestly.

**Stage 4 (LLM, one subagent per lecture, parallel):** the folder path is the only channel to the
agent, so it must be in the dispatch prompt:

> "Process the lecture in `work/L03`. Read pages.json, all frames/*.png and pages_context.md.
> Write `work/L03/L03.tex` according to CLAUDE.md."

**Stage 5 (serial, NOT parallel):** one pass over the whole document — unify symbols across
lectures, smooth duplicates and forward references, check `\label`/`\ref`, then `scripts/build.sh`.

---

## Language & notation (binding)

- **Prose German, technical terms English.** Pattern: „Der *maximum likelihood estimator* (MLE)
  $\est{\theta}$ maximiert die *log-likelihood*." Italicise a term on first occurrence (with its
  abbreviation if it has one), plain afterwards. Do not Germanise the terminology (never
  „Schätzfunktion" for *estimator*), but verbs and sentence structure are German.
- **Standard symbols** (do not deviate): expectation `\E`, variance `\Var`, covariance `\Cov`,
  probability `\Prob`, estimator `\est{\theta}` (= $\hat\theta$), transpose `A\T`, i.i.d. `\iid`,
  independence `\indep`, conditioning `\given`, convergence `\convp`/`\convd`, indicator
  `\indicator`. Sample $X_1,\dots,X_n$, size $n$, parameter $\theta\in\Theta$, design matrix $X$,
  response $y$.
- **Vectors: always `\vek{x}`** — never `\underline`, `\mathbf`, `\vec` or `\boldsymbol` directly.
  The macro is the *single* place where the rendering is fixed (currently bold, via
  `\boldsymbol`); formatting around it makes the notation unswitchable again. **Matrices stay
  upright** ($X$, $\Sigma$) — `\vek{}` marks vectors, not "everything non-scalar".
- All macros are defined in `preamble_stats_addon.tex`.

## LaTeX conventions (binding)

- Theorem environments: `definition`, `satz`, `theorem`, `lemma`, `korollar`, `proposition`,
  `beispiel`, `bemerkung`, `algorithmus` (shared counter).
- **Anchor every page with its timestamp**, as a comment, so anyone can jump back into the video:
  `% [L03 @ 00:23:15]` before the block that came from that page. PDF lectures (no audio) are
  anchored by page: `% [L13 p.5]`. These anchors are the audit trail — they are how a reader
  checks a suspicious formula in seconds. They are not clutter and must not be stripped.
- **Flag uncertainty, do not guess:** if a formula or symbol is not legible in the frame, put in
  your best reading and flag it:
  `% TODO(L03 @ 00:41:02): exponent unclear — check the video`.
- Label important results: `\label{eq:L03:mle-var}` (scheme `type:L##:short`).
- **Omit:** organisational talk (exercise sheets, exam dates, small talk, pure admin recap).
  Recap of earlier lectures: one sentence with a cross-reference.
- **Slides vs. handwriting:** some pages are presentation slides, others handwritten derivations.
  Treat both the same: **put the content into LaTeX, never embed a screenshot.** Definitions,
  formulas and diagrams from slides become prose/equations; sketches and plots are rebuilt as
  `tikzpicture` (or described precisely). Drop pure agenda/admin slides. (`pages.json` does mark
  the regime — `"regime": "slide"` — but trust the image.)
- Output is a **fragment** (sections/subsections), with **no** preamble and no `\begin{document}`.

---

## Two rules that are worth more than they look

**1. The image is the truth; the audio disambiguates it.** The mathematics comes from the
*frames*. The transcript resolves ambiguity — "theta hat" ⇒ $\est{\theta}$, "sigma squared over n"
⇒ $\sigma^2/n$, the name of a distribution. Where the two disagree, **the frame wins**. Use the
audio as the primary source only where the frame is unreadable, and say so.

**2. An empty frame proves nothing about the lecture.** It is a fact about the *extractor*. The
extractor photographs the canvas when it *settles*; content that was written and erased in place,
or built up incrementally, or scrolled past between two emits, is in no frame — while the coverage
gate stays green, because the gate is spatial and says nothing about *time*. **Never write
"[not written on the board]" or conclude the lecturer skipped something because a frame is blank.**
Of the first 21 such claims in this project, **nine were false** — the material had been on the
board the whole time.

"Check the video" is executable:

```bash
python scripts/peek.py L03 01:04:42          # contact sheet ±30 s → locate the moment
python scripts/peek.py L03 01:05:07 --frame  # full-res frame      → read it
```

---

## Do not "clean up" these things

- **The `%` comments in the `.tex` are the product, not debris.** The timestamp anchors and the
  provenance notes ("the board writes X here, the script deviates because …") are the audit trail
  against exactly the failure class this project has paid for. Finished work, not leftovers.
- **The extractor's guards are load-bearing.** `check_coverage.py` and the emit rules in
  `extract_pages_scroll.py` look over-careful because each one is a fix for a bug that silently
  deleted mathematics (six of them; see the README table and the extractor docstring). If you
  change the extractor, verify your change is **purely additive** — no existing emit reason may
  stop firing. The first attempt at the sixth fix silently disarmed the *second* one.
- **`work/L##/L##.tex` is the source of truth.** Stage-5 passes write straight into it. Old split
  fragments (`L##_a.tex`, …) go stale the moment that happens, and a `merge_fragments.py` run
  would roll the work back with a still-clean compile. That has happened once.

#!/usr/bin/env python3
"""
pdf_to_pages.py — render a PDF lecture (a lecture handed out as a document rather
than recorded) into the SAME work/<ID>/ layout the lecture-processor consumes, so
it flows through Stage 4 exactly like a video one:
    work/<ID>/frames/page_###.png   one image per PDF page (full resolution)
    work/<ID>/pages.json            manifest (index, frame, page number)
    work/<ID>/pages_context.md      minimal (no audio for a PDF)

No video / transcript / scroll involved. Anchors for a PDF lecture are page-based
(% [L13 p.N]) rather than time-based.

RENDERERS — two, and which one you get is a LICENSING question, not a quality one.
  * pdftoppm (poppler-utils): the DEFAULT. Invoked as a subprocess, so its GPL
    licence does not reach your code. `brew install poppler`.
  * PyMuPDF: used only with --renderer pymupdf. It is AGPL-3.0 (or a paid Artifex
    licence) and you IMPORT it, so the AGPL's copyleft would attach to a
    distributed combined work. This project is MIT and ships neither, but the
    default stays on poppler so nobody inherits an AGPL obligation by accident.
Output is identical either way.

  python scripts/pdf_to_pages.py path/to/L13.pdf --out work/L13 --dpi 200
"""
from __future__ import annotations
import argparse, json, re, shutil, subprocess, sys
from pathlib import Path


def render_pdftoppm(pdf: str, frames: Path, dpi: int) -> int:
    """Render via poppler's pdftoppm (subprocess -> no licence entanglement)."""
    if not shutil.which("pdftoppm"):
        sys.exit("[!] pdftoppm not found — `brew install poppler` "
                 "(or use --renderer pymupdf, but see the licence note in this file).")
    # pdftoppm names its output <prefix>-<n>.png, zero-padded to the page count's
    # width. Render into a temp prefix, then rename to our page_###.png scheme.
    subprocess.run(["pdftoppm", "-png", "-r", str(dpi), pdf, str(frames / "_raw")],
                   check=True, stdin=subprocess.DEVNULL)
    raw = sorted(frames.glob("_raw-*.png"),
                 key=lambda p: int(re.search(r"-(\d+)\.png$", p.name).group(1)))
    if not raw:
        sys.exit(f"[!] pdftoppm produced no pages from {pdf}")
    for i, src in enumerate(raw, 1):
        src.rename(frames / f"page_{i:03d}.png")
    return len(raw)


def render_pymupdf(pdf: str, frames: Path, dpi: int) -> int:
    """Render via PyMuPDF. AGPL-3.0 — opt-in only (see module docstring)."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        sys.exit("[!] PyMuPDF missing — `pip install pymupdf` (AGPL-3.0), or drop "
                 "--renderer and use the default poppler path instead.")
    doc = fitz.open(pdf)
    for i, page in enumerate(doc, 1):
        page.get_pixmap(dpi=dpi).save(str(frames / f"page_{i:03d}.png"))
    return doc.page_count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", required=True, help="output directory, e.g. work/L13")
    ap.add_argument("--dpi", type=int, default=200,
                    help="render resolution (200 is crisp for a vision model)")
    ap.add_argument("--renderer", choices=("pdftoppm", "pymupdf"), default="pdftoppm",
                    help="pdftoppm (default, poppler, no licence entanglement) or "
                         "pymupdf (AGPL-3.0 — see the licence note in this file)")
    args = ap.parse_args()

    if not Path(args.pdf).is_file():
        sys.exit(f"[!] no such PDF: {args.pdf}")

    out = Path(args.out)
    frames = out / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    for stale in frames.glob("page_*.png"):
        stale.unlink()          # a shorter re-render must not leave old pages behind

    render = render_pdftoppm if args.renderer == "pdftoppm" else render_pymupdf
    n = render(args.pdf, frames, args.dpi)

    manifest, ctx = [], ["# Page ⇄ transcript alignment",
                         f"\n{n} PDF pages (document lecture — no audio).\n"]
    for i in range(1, n + 1):
        name = f"page_{i:03d}.png"
        manifest.append({"index": i, "frame": f"frames/{name}", "page": i,
                         "hms_start": f"p.{i}", "hms_end": f"p.{i}"})
        ctx += [f"\n## Page {i:03d}  [p.{i}]", f"image: `frames/{name}`\n",
                "> _(PDF page, no audio)_"]

    (out / "pages.json").write_text(json.dumps(manifest, indent=2))
    (out / "pages_context.md").write_text("\n".join(ctx), encoding="utf-8")
    print(f"[✓] {n} pages -> {out/'pages.json'}  (frames in {frames}, via {args.renderer})")


if __name__ == "__main__":
    main()

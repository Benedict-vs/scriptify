#!/usr/bin/env bash
# build.sh — compile the script.
#
# main.tex lives in work/ (with the lecture fragments it \inputs), NOT at the repo
# root: everything course-specific is confined to work/, which is the private half
# of this project. -cd makes latexmk chdir there, so \input{L01/L01.tex} and
# \input{../preamble_stats_addon.tex} both resolve.
#
#   scripts/build.sh              # -> work/main.pdf
#   scripts/build.sh --clean      # remove build artifacts, then build
set -euo pipefail
cd "$(dirname "$0")/.."

MAIN=${MAIN_TEX:-work/main.tex}
[[ -f "$MAIN" ]] || { echo "[!] $MAIN not found — copy templates/main.tex to work/main.tex" >&2; exit 1; }

[[ "${1:-}" == "--clean" ]] && latexmk -C -cd "$MAIN"

latexmk -pdf -cd -interaction=nonstopmode "$MAIN"
echo "[✓] ${MAIN%.tex}.pdf"

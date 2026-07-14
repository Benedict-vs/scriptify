#!/usr/bin/env bash
# prepare_all.sh — Stage 1–3 batch (deterministic, no LLM) over every lecture in
# lectures.tsv. Run it overnight; the lectures are independent.
#
# TWO PHASES
#   1) Sanity-check the extractor against your recordings (optional — the global
#      defaults fit a whole course of identically-produced recordings):
#        scripts/prepare_all.sh --diagnostic
#      Writes work/<ID>/scroll_curve.png per lecture (blue = scroll staircase,
#      orange = slide stretches) without transcribing. Look at it; if the split
#      is wrong, put per-lecture flags in column 3 of work/lectures.tsv.
#
#   2) Extract + align:
#        scripts/prepare_all.sh
#      Per lecture: transcribe (idempotent) -> extract full-res pages -> align the
#      transcript into work/<ID>/pages_context.md -> check_coverage.py gate.
#      That folder is then ready for a Stage-4 lecture-processor subagent.
#
# work/lectures.tsv columns (TAB-separated; lines starting with # are ignored):
#   lecture_id <TAB> source_video <TAB> params <TAB> note
#
# `params` is '-' for "global defaults fit" (the normal case), otherwise a list of
# extra extract_pages_scroll.py flags for THAT lecture, e.g.
#   L05  videos/CSDA2023-5.mp4  --overlap 0.6  recorded in another year, faster scrolling
# Use it when a lecture's recording differs (other year, other iPad theme). Do NOT
# use it to shrink the page count — --ink-threshold is a redundancy filter, not a
# budget knob, and raising it far enough to drop pages tears coverage gaps.
#
# USAGE
#   scripts/prepare_all.sh                 # every lecture in lectures.tsv
#   scripts/prepare_all.sh L03 L05         # only these (re-run after calibrating)
#   scripts/prepare_all.sh --diagnostic    # scroll_curve.png per lecture, then stop
set -euo pipefail
cd "$(dirname "$0")/.."          # project root
MODE=extract
[[ "${1:-}" == "--diagnostic" ]] && { MODE=--diagnostic; shift; }
ONLY=("$@")                      # empty = all lectures
TSV=${LECTURES_TSV:-work/lectures.tsv}
[[ -f "$TSV" ]] || { echo "[!] $TSV not found in $(pwd) — copy templates/lectures.tsv to work/ and edit, or run scripts/build_manifest.py" >&2; exit 1; }

wanted() {                       # $1 = lecture id
    [[ ${#ONLY[@]} -eq 0 ]] && return 0
    local id
    for id in "${ONLY[@]}"; do [[ "$id" == "$1" ]] && return 0; done
    return 1
}

fail=0
while IFS=$'\t' read -r ID VIDEO PARAM NOTE || [[ -n "${ID:-}" ]]; do
    [[ -z "${ID// }" || "$ID" == \#* ]] && continue
    wanted "$ID" || continue
    OUT="work/$ID"
    echo "=== $ID  ($VIDEO) ==="
    if [[ ! -f "$VIDEO" ]]; then echo "[!] missing source, skipping"; fail=1; continue; fi

    # '-' = global defaults; anything else is extra flags for this lecture only.
    EXTRA=()
    [[ "${PARAM:-}" != "-" && -n "${PARAM// }" ]] && read -r -a EXTRA <<< "$PARAM"

    # PDF lecture (e.g. L13): render pages directly, no transcript/extract/align.
    case "$VIDEO" in
        *.pdf|*.PDF)
            if [[ "$MODE" == "--diagnostic" ]]; then
                echo "[i] $ID is a PDF — no diagnostic needed."
            else
                python scripts/pdf_to_pages.py "$VIDEO" --out "$OUT"
            fi
            continue ;;
    esac

    # Stage 1 — transcript (skipped in --diagnostic mode; idempotent otherwise).
    # The extractor diagnostic only needs the video, so don't waste hours
    # transcribing just to draw the scroll curves.
    if [[ "$MODE" != "--diagnostic" ]]; then
        if [[ -f "$OUT/$ID.srt" ]]; then
            echo "[skip] transcript exists: $OUT/$ID.srt"
        else
            scripts/transcribe.sh "$VIDEO" "$ID" </dev/null || { echo "[!] transcribe failed for $ID"; fail=1; continue; }
        fi
    fi

    # Stage 2/3 — page extraction (scroll+slide hybrid: overlapping snapshots while
    # the handwriting canvas scrolls + one capture per slide / page-clear), then
    # transcript alignment. Global defaults fit the whole course (same setup).
    if [[ "$MODE" == "--diagnostic" ]]; then
        python scripts/extract_pages_scroll.py "$VIDEO" --out "$OUT" ${EXTRA+"${EXTRA[@]}"} --diagnostic
        echo "[i] inspect $OUT/scroll_curve.png (blue = scroll staircase, orange = slide stretches)"
    else
        rm -rf "$OUT/frames"        # stale frames from an earlier run would survive a
                                    # shorter page list and be read as real pages
        python scripts/extract_pages_scroll.py "$VIDEO" --out "$OUT" ${EXTRA+"${EXTRA[@]}"}
        python scripts/align_transcript.py \
            --pages "$OUT/pages.json" --srt "$OUT/$ID.srt" --out "$OUT/pages_context.md"
        python scripts/check_coverage.py "$OUT" || fail=1   # gate: no gaps -> Stage 4
    fi
done < "$TSV"

echo
[[ $fail -eq 0 ]] && echo "[✓] prepare_all ($MODE) complete." \
                  || echo "[!] prepare_all ($MODE) finished with warnings — see above."
exit $fail

#!/usr/bin/env bash
# transcribe.sh — long-file transcription for the CSDA pipeline via the official
# mlx_whisper CLI (fast on Apple Silicon, GPU via MLX).
#
# WHY THIS FLAG SET
#   Plain mlx_whisper on 1.5–2 h files can hit a hallucination loop (it repeats
#   the same sentence for minutes). ml-explore's own fix is
#   --condition-on-previous-text False: the model no longer feeds its previous
#   window back as a prompt, so it can't get stuck in the loop. Accuracy stays
#   excellent. The default no-speech / compression-ratio thresholds additionally
#   drop failed (hallucinated) segments. Output goes straight to
#   work/<ID>/<ID>.srt — exactly what align_transcript.py consumes.
#
# SETUP (once):
#   brew install ffmpeg
#   uv sync        # from the repo root; then run this script via `uv run`
#   (the large-v3 model ~3 GB downloads automatically from Hugging Face on first run)
#
# USAGE:
#   scripts/transcribe.sh <video> <lecture_id> [work_root]
#   scripts/transcribe.sh videos/CSDA26_1.mp4 L01
#
# Env override:
#   WHISPER_MODEL   default mlx-community/whisper-large-v3-mlx (max accuracy).
#                   Use mlx-community/whisper-large-v3-turbo for ~3-4x speed.
#
# A faster, pure-C++ alternative (whisper.cpp) is documented at the bottom.
set -euo pipefail

VIDEO=${1:?usage: transcribe.sh <video> <lecture_id> [work_root]}
ID=${2:?usage: transcribe.sh <video> <lecture_id> [work_root]}
WORK_ROOT=${3:-work}
OUT_DIR="$WORK_ROOT/$ID"
MODEL=${WHISPER_MODEL:-mlx-community/whisper-large-v3-mlx}

# Domain hint -> fewer proper-noun / terminology errors. Keep in sync with CLAUDE.md.
PROMPT="Lecture on computational statistics and data analysis: estimator, bias, variance, \
maximum likelihood, MLE, bootstrap, Monte Carlo, MCMC, Metropolis-Hastings, Gibbs sampling, \
cross-validation, regularization, gradient descent, EM algorithm, posterior, prior, likelihood, \
kernel density estimation, resampling, confidence interval, hypothesis test, p-value, \
sufficient statistic, Fisher information, Cramer-Rao bound."

[[ -f "$VIDEO" ]] || { echo "[!] no such video: $VIDEO" >&2; exit 1; }
mkdir -p "$OUT_DIR"
echo "[transcribe] $VIDEO -> $OUT_DIR/$ID.srt  (model=$MODEL)"

# mlx_whisper reads the mp4 directly (extracts audio via ffmpeg) and writes
# <output-dir>/<output-name>.srt.
mlx_whisper "$VIDEO" \
    --model "$MODEL" \
    --task transcribe \
    --language en \
    --output-format srt \
    --output-dir "$OUT_DIR" \
    --output-name "$ID" \
    --condition-on-previous-text False \
    --initial-prompt "$PROMPT"

[[ -f "$OUT_DIR/$ID.srt" ]] || { echo "[!] expected $OUT_DIR/$ID.srt not produced" >&2; exit 1; }
echo "[transcribe] done: $OUT_DIR/$ID.srt"

# -----------------------------------------------------------------------------
# ALTERNATIVE ENGINE — whisper.cpp (fastest on Apple Silicon, Metal, no Python).
# Handles long files natively and emits SRT with -osrt. One-time build:
#
#   git clone https://github.com/ggml-org/whisper.cpp && cd whisper.cpp
#   cmake -B build && cmake --build build -j --config Release
#   sh ./models/download-ggml-model.sh large-v3
#   sh ./models/download-vad-model.sh silero-v6.2.0
#
# Then per lecture (needs 16 kHz mono WAV input):
#   ffmpeg -i "$VIDEO" -ar 16000 -ac 1 -c:a pcm_s16le "$OUT_DIR/$ID.wav"
#   ./build/bin/whisper-cli -m models/ggml-large-v3.bin -f "$OUT_DIR/$ID.wav" \
#       -l en --vad --vad-model models/ggml-silero-v6.2.0.bin \
#       --prompt "$PROMPT" -osrt -of "$OUT_DIR/$ID"   # writes $OUT_DIR/$ID.srt
# -----------------------------------------------------------------------------

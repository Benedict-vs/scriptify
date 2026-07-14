#!/usr/bin/env bash
# install-hooks.sh — point git at .githooks/ (tracked, so the guard travels with
# the repo instead of living in a .git/ dir nobody clones).
#
#   scripts/install-hooks.sh
set -euo pipefail
cd "$(dirname "$0")/.."
git config core.hooksPath .githooks
chmod +x .githooks/* 2>/dev/null || true
echo "[✓] core.hooksPath = .githooks — lecture content can no longer be committed here."

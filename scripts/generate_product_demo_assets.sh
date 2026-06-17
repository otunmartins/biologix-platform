#!/usr/bin/env bash
# Regenerate README demo assets from docs/assets/product_use.mp4 (source screen recording).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${ROOT}/docs/assets/product_use.mp4"
OUT_DIR="${ROOT}/docs/assets"
GIF="${OUT_DIR}/product_demo.gif"
POSTER="${OUT_DIR}/product_demo_poster.png"

if [[ ! -f "$SRC" ]]; then
  echo "Missing source video: $SRC" >&2
  echo "Place product_use.mp4 there (or copy from repo root) and re-run." >&2
  exit 1
fi

command -v ffmpeg >/dev/null || { echo "ffmpeg required" >&2; exit 1; }

echo "Poster frame → $POSTER"
ffmpeg -y -i "$SRC" -ss 8 -vframes 1 "$POSTER" -loglevel error

echo "Preview GIF (22s @ 10fps, 960px) → $GIF"
ffmpeg -y -i "$SRC" -ss 2 -t 22 \
  -vf "fps=10,scale=960:-1:flags=lanczos,split[s0][s1];[s0]palettegen=stats_mode=diff:max_colors=256[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5" \
  "$GIF" -loglevel error

ls -lh "$GIF" "$POSTER" "$SRC"

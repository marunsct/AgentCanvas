#!/usr/bin/env bash
# Turns the demo video that `make demo` recorded (apps/web/e2e/demo.spec.ts) into the README's GIF and an MP4 for
# posts: docs/assets/demo.gif (1120 px wide, 8 frames a second, one palette for the whole clip) and
# docs/assets/demo.mp4 (H.264, plays inline on GitHub, X, LinkedIn and Product Hunt).
set -euo pipefail
video=${1:?usage: scripts/demo-gif.sh path/to/video.webm}
out=$(cd "$(dirname "$0")/.." && pwd)/docs/assets
command -v ffmpeg >/dev/null || { echo "needs ffmpeg" >&2; exit 1; }
mkdir -p "$out"
filters="fps=8,scale=1120:-1:flags=lanczos"
ffmpeg -v error -y -i "$video" -vf "$filters,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" "$out/demo.gif"
ffmpeg -v error -y -i "$video" -vf "scale=1600:-2" -c:v libx264 -pix_fmt yuv420p -crf 26 -preset slow -movflags +faststart -an "$out/demo.mp4"
ls -la "$out/demo.gif" "$out/demo.mp4"

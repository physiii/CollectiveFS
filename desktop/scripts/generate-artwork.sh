#!/usr/bin/env bash
# Generate the full CollectiveFS desktop icon set from the single source SVG.
# Tauri's `tauri icon` produces every platform size from one 1024x1024 PNG.
set -euo pipefail
DESKTOP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_SVG="$DESKTOP_ROOT/art/collectivefs-app-icon.svg"
RASTER="$DESKTOP_ROOT/art/collectivefs-app-icon.png"
test -f "$SRC_SVG" || { echo "missing $SRC_SVG" >&2; exit 1; }

echo "==> Rasterizing -> $RASTER (1024x1024)"
if command -v rsvg-convert >/dev/null 2>&1; then
  rsvg-convert -w 1024 -h 1024 "$SRC_SVG" -o "$RASTER"
elif command -v convert >/dev/null 2>&1; then
  convert -background none "$SRC_SVG" -resize 1024x1024 "$RASTER"
else
  echo "need rsvg-convert or imagemagick to rasterize" >&2; exit 1
fi

echo "==> tauri icon"
cd "$DESKTOP_ROOT"
npx --yes @tauri-apps/cli icon "$RASTER"
echo "Done -> src-tauri/icons"

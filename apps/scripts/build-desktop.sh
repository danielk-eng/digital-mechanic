#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/desktop"
if [ ! -d node_modules/electron-builder ]; then
  npm install
fi
# Never discover a Windows cert on this Linux box
export CSC_IDENTITY_AUTO_DISCOVERY=false
# Prefer zip (no wine) always; try nsis/portable if wine is present
if command -v wine >/dev/null 2>&1; then
  npx electron-builder --linux AppImage --win nsis portable zip
else
  echo "wine not found: building Linux AppImage + Windows zip (unpacked payload). NSIS/portable skipped."
  npx electron-builder --linux AppImage --win zip
fi
OUT="${1:-/workspace/iikuznitsa/apps-out}"
mkdir -p "$OUT"
# copy release artifacts only; win-unpacked/linux-unpacked contain loose executables, not distributables
find artifacts -maxdepth 1 -type f \( -name '*.AppImage' -o -name '*.exe' -o -name '*.zip' \) -print -exec cp -f {} "$OUT/" \;
# if unpacked dir exists, zip it ourselves with a stable name if builder zip is missing
VERSION=$(node -e 'console.log(require(process.argv[1]).version)' "$ROOT/desktop/package.json")
if [ ! -e "$OUT/DigitalMechanic-win-${VERSION}.zip" ] && [ -d artifacts/win-unpacked ]; then
  (cd artifacts && zip -r -q "$OUT/DigitalMechanic-win-unpacked-${VERSION}.zip" win-unpacked)
fi
echo "Desktop artifacts copied to $OUT"

#!/usr/bin/env bash
set -euo pipefail
APK="${1:?apk path}"
AAPT="${AAPT:-/workspace/android-sdk/build-tools/35.0.0/aapt}"
APKSIGNER="${APKSIGNER:-/workspace/android-sdk/build-tools/35.0.0/apksigner}"
echo "=== $APK ==="
ls -lh "$APK"
echo "--- permissions ---"
"$AAPT" dump permissions "$APK"
echo "--- badging ---"
"$AAPT" dump badging "$APK" | head -20
echo "--- manifest (selected) ---"
"$AAPT" dump xmltree "$APK" AndroidManifest.xml | rg -i "permission|RECORD_AUDIO|CAMERA|INTERNET|usesCleartext|launchMode|package=|activity |networkSecurity" || true
echo "--- signer ---"
"$APKSIGNER" verify --verbose "$APK"
echo "OK"

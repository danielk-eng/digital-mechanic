#!/usr/bin/env bash
set -euo pipefail
APK="${1:?apk path}"
SDK="${ANDROID_SDK_ROOT:-$HOME/android-sdk}"
AAPT="${AAPT:-$SDK/build-tools/35.0.0/aapt}"
APKSIGNER="${APKSIGNER:-$SDK/build-tools/35.0.0/apksigner}"
echo "=== $APK ==="
ls -lh "$APK"
echo "--- permissions ---"
"$AAPT" dump permissions "$APK"
echo "--- badging ---"
"$AAPT" dump badging "$APK" | sed -n '1,20p'
echo "--- manifest (selected) ---"
"$AAPT" dump xmltree "$APK" AndroidManifest.xml | rg -i "permission|RECORD_AUDIO|CAMERA|INTERNET|usesCleartext|launchMode|package=|activity |networkSecurity" || true
echo "--- signer ---"
"$APKSIGNER" verify --verbose "$APK"
echo "OK"

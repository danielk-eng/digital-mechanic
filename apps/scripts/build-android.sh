#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export JAVA_HOME="${JAVA_HOME:-/workspace/jdk-21}"
export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-/workspace/android-sdk}"
export ANDROID_HOME="$ANDROID_SDK_ROOT"
export PATH="$JAVA_HOME/bin:$ANDROID_SDK_ROOT/platform-tools:$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:$PATH"
export MECHANIC_KEYSTORE="${MECHANIC_KEYSTORE:-/workspace/android-sdk/keystore/mechanic-release.jks}"
export MECHANIC_KEYSTORE_PASSFILE="${MECHANIC_KEYSTORE_PASSFILE:-/workspace/android-sdk/keystore/keystore.pass}"

cd "$ROOT/android"
if [ ! -d node_modules/@capacitor/android ]; then
  npm install
fi
if [ ! -d android ]; then
  npx cap add android
fi
npx cap sync android
python3 "$ROOT/scripts/patch-android.py"
python3 "$ROOT/scripts/make-android-res.py"
# cap sync may restore MainActivity; patch again
python3 "$ROOT/scripts/patch-android.py"

cd "$ROOT/android/android"
chmod +x gradlew
./gradlew --no-daemon assembleRelease assembleDebug

OUT="${1:-/workspace/iikuznitsa/apps-out}"
mkdir -p "$OUT"
REL="$(find app/build/outputs/apk/release -name '*.apk' | head -1)"
DBG="$(find app/build/outputs/apk/debug -name '*.apk' | head -1)"
cp -f "$REL" "$OUT/DigitalMechanic-1.0.0.apk"
cp -f "$DBG" "$OUT/DigitalMechanic-1.0.0-debug.apk"
echo "APKs copied to $OUT"

#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -z "${JAVA_HOME:-}" ]; then
  if [ -d "$HOME/jdk-21" ]; then export JAVA_HOME="$HOME/jdk-21"; else export JAVA_HOME="/workspace/jdk-21"; fi
fi
if [ -z "${ANDROID_SDK_ROOT:-}" ]; then
  if [ -d "$HOME/android-sdk" ]; then export ANDROID_SDK_ROOT="$HOME/android-sdk"; else export ANDROID_SDK_ROOT="/workspace/android-sdk"; fi
fi
export ANDROID_HOME="$ANDROID_SDK_ROOT"
export PATH="$JAVA_HOME/bin:$ANDROID_SDK_ROOT/platform-tools:$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:$PATH"
if [ -z "${MECHANIC_KEYSTORE:-}" ]; then
  if [ -f "$HOME/android-sdk/keystore/mechanic-release.jks" ]; then export MECHANIC_KEYSTORE="$HOME/android-sdk/keystore/mechanic-release.jks"; else export MECHANIC_KEYSTORE="/workspace/android-sdk/keystore/mechanic-release.jks"; fi
fi
if [ -z "${MECHANIC_KEYSTORE_PASSFILE:-}" ]; then
  if [ -f "$HOME/android-sdk/keystore/keystore.pass" ]; then export MECHANIC_KEYSTORE_PASSFILE="$HOME/android-sdk/keystore/keystore.pass"; else export MECHANIC_KEYSTORE_PASSFILE="/workspace/android-sdk/keystore/keystore.pass"; fi
fi

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
VERSION=$(node -e 'console.log(require(process.argv[1]).version)' "$ROOT/android/package.json")
cp -f "$REL" "$OUT/DigitalMechanic-${VERSION}.apk"
cp -f "$DBG" "$OUT/DigitalMechanic-${VERSION}-debug.apk"
echo "APKs copied to $OUT"

#!/bin/sh
# Builds injector.dex from Injector.java. Needs a JDK and the Android SDK.
set -e
cd "$(dirname "$0")"
SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-/opt/android-sdk}}"
ANDROID_JAR="$(ls -d "$SDK"/platforms/android-*/android.jar | sort -V | tail -n 1)"
D8="$(ls -d "$SDK"/build-tools/*/d8 "$SDK"/cmdline-tools/latest/bin/d8 2>/dev/null | head -n 1)"

rm -rf build && mkdir build
javac --release 11 -cp "$ANDROID_JAR" -d build Injector.java
"$D8" --min-api 26 --lib "$ANDROID_JAR" --output build build/*.class
mv build/classes.dex injector.dex
rm -rf build
echo "built injector/injector.dex"

#!/bin/sh
# Mac side: puts the voice model on the phone (first time) and starts the speech server on port 8100.
# usage: sh tts/start_tts.sh   (run from the incident_assistant folder)
set -e
cd "$(dirname "$0")"
D=/data/local/tmp/tts
if ! adb shell "[ -f $D/m/tokens.txt ]" 2>/dev/null; then
  echo "first time: copying the voice model to the phone (about 60 MB)…"
  adb shell "mkdir -p $D/lib"
  adb push sherpa-onnx-offline-tts $D/
  adb push vits-piper-en_US-lessac-medium-int8 $D/m
  adb push serve.sh $D/
  NDK=$(ls -d "${ANDROID_NDK_HOME:-}" "${ANDROID_HOME:-$HOME/Library/Android/sdk}"/ndk/* 2>/dev/null | tail -1)
  LIBCXX=$(find "$NDK" -path "*aarch64-linux-android/libc++_shared.so" 2>/dev/null | head -1)
  [ -n "$LIBCXX" ] || { echo "could not find libc++_shared.so in the Android NDK ($NDK)"; exit 1; }
  adb push "$LIBCXX" $D/lib/
  adb shell "chmod 755 $D/sherpa-onnx-offline-tts $D/serve.sh"
fi
adb push serve.sh $D/ >/dev/null
adb shell "chmod 755 $D/serve.sh"
adb shell "pkill -f 'nc -L' " 2>/dev/null || true
sleep 1
adb shell "nohup sh -c 'while true; do toybox nc -L -p 8100 $D/serve.sh; done' >/dev/null 2>&1 &"
adb forward tcp:8100 tcp:8100
sleep 2
echo "testing…"
curl -s -m 60 -o /tmp/tts_test.wav "http://127.0.0.1:8100/?t=Speech%20is%20ready." && ls -la /tmp/tts_test.wav && echo "speech server ready on the phone (port 8100)"

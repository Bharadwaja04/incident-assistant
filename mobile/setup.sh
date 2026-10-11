#!/bin/sh
# usage: sh mobile/setup.sh ~/Documents/incident_app   (run from the incident_assistant folder)
set -e
APP="${1:-$HOME/Documents/incident_app}"
mkdir -p "$APP/src/core"
cp app_core/src/*.ts "$APP/src/core/"
rm -f "$APP/src/core/onecall.ts" "$APP/src/core/severity.ts"
cp mobile/App.tsx mobile/Home.tsx mobile/Record.tsx mobile/Fields.tsx mobile/ui.tsx mobile/core.ts "$APP/"
if [ ! -d "$APP/node_modules/expo-audio" ]; then
  (cd "$APP" && npx expo install expo-audio)
  echo "NEW NATIVE MODULE (audio playback): rebuild once with:  cd $APP && npx expo run:android"
fi
M="$APP/android/app/src/main/AndroidManifest.xml"
if ! grep -q RECORD_AUDIO "$M"; then
  perl -0pi -e 's#<application#<uses-permission android:name="android.permission.RECORD_AUDIO"/>\n  <application#' "$M"
  echo "added microphone permission"
fi
echo "copied core + screens into $APP"

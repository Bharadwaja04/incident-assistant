# AI Incident Report Assistant: setup and run

Setup from a clean computer to the app running on the phone. Ports used: 8080 (language model), 8090 (speech-to-text), 8100 (text-to-speech), 8081 (app dev server).

## 0. What you need
- Mac (or Linux) computer, Android phone (we used a Qualcomm HDK8650 / Snapdragon 8 Gen 3) with USB debugging on, USB cable
- Homebrew packages (Mac): `brew install node git cmake openjdk@17 android-platform-tools`
- Android Studio, or the command-line SDK, with **Android SDK Platform 35 or newer** and **NDK (side by side)**; set
  ```
  export ANDROID_HOME=$HOME/Library/Android/sdk
  export JAVA_HOME=$(/usr/libexec/java_home -v 17)
  export PATH=$PATH:$ANDROID_HOME/platform-tools
  ```
- Check the phone is seen: `adb devices` shows one device

## 1. Get the code
```
git clone https://github.com/Bharadwaja04/incident-assistant.git
cd incident-assistant
```

## 2. Model files (hosted externally, not in this repo)
| What | Link |
|---|---|
| Speech-to-text: `ggml-base.bin` | https://huggingface.co/ggerganov/whisper.cpp/tree/main |
| Language model: `Qwen3-1.7B-Q4_K_M.gguf` | https://huggingface.co/Qwen/Qwen3-1.7B-GGUF |
| Text-to-speech voice: `vits-piper-en_US-lessac-medium-int8.tar.bz2` | https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models |
| Text-to-speech runtime: `sherpa-onnx-v1.13.8-android-aarch64-termux-static.tar.bz2` | https://github.com/k2-fsa/sherpa-onnx/releases/tag/v1.13.8 |
| llama.cpp source (for `llama-server`) | https://github.com/ggml-org/llama.cpp |
| whisper.cpp source (for `whisper-server`) | https://github.com/ggml-org/whisper.cpp |

## 3. Put the models on the phone
```
adb shell mkdir -p /data/local/tmp/llm /data/local/tmp/stt
adb push Qwen3-1.7B-Q4_K_M.gguf /data/local/tmp/llm/
adb push ggml-base.bin /data/local/tmp/stt/
```

## 4. Build the two servers for the phone and push them
Build each with the NDK (arm64, with the `i8mm` and `dotprod` instructions):
```
cmake -B build-android -DCMAKE_TOOLCHAIN_FILE=$ANDROID_HOME/ndk/<version>/build/cmake/android.toolchain.cmake \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-28 \
  -DCMAKE_C_FLAGS="-march=armv8.6-a+i8mm+dotprod" -DCMAKE_CXX_FLAGS="-march=armv8.6-a+i8mm+dotprod" \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build-android --config Release -j 8 --target llama-server      # in the llama.cpp folder
cmake --build build-android --config Release -j 8 --target whisper-server    # in the whisper.cpp folder
```
Push each server with the `.so` files it needs from its build `bin` folder:
```
adb shell mkdir -p /data/local/tmp/llm2 /data/local/tmp/stt2
adb push <llama.cpp>/build-android/bin/. /data/local/tmp/llm2/
adb push <whisper.cpp>/build-android/bin/. /data/local/tmp/stt2/
adb shell chmod 755 /data/local/tmp/llm2/llama-server /data/local/tmp/stt2/whisper-server
```

## 5. Start the language and speech-to-text servers
From the repo folder:
```
sh mobile/reset_board.sh
```
Wait for `ready: open the app now`. Check:
```
curl localhost:8080/health      # {"status":"ok"}
```

## 6. Text-to-speech server (for "Read report aloud")
Unpack the two TTS archives from step 2 so `tts/` contains `sherpa-onnx-offline-tts` (from the runtime archive, `bin/`) and the folder `vits-piper-en_US-lessac-medium-int8/`. Then:
```
sh tts/start_tts.sh
```
It copies the voice to the phone the first time, starts the server on port 8100 and tests it.

## 7. Create the app project (once)
```
cd ..
npx create-expo-app@latest incident_app --template blank-typescript
cd incident_app
npm install react-native-audio-record
npx expo install expo-audio
cd ../incident-assistant
```

## 8. Copy the app code into the project
```
sh mobile/setup.sh ../incident_app
```
This copies the screens and the on-device logic into the project and adds the microphone permission.

## 9. Build and install the app on the phone (once, and after adding any native package)
```
cd ../incident_app
npx expo run:android
```

## 10. Run
Every time you work on the app:
```
cd incident_app
npx expo start --dev-client          # leave running
adb reverse tcp:8081 tcp:8081        # in another terminal; repeat after every reinstall
```
Open the app on the phone. If the phone was restarted, run `sh mobile/reset_board.sh` and `sh tts/start_tts.sh` again from the repo folder.

## Checks (optional)
```
cd app_core && npm test
```

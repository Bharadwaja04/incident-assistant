#!/bin/sh
# One command: stop both servers on the phone, start them again with the original settings, wait until they answer.
# usage (from the incident_assistant folder): sh mobile/reset_board.sh
adb shell "kill \$(pidof llama-server) \$(pidof whisper-server) 2>/dev/null; sleep 3"
adb shell "cd /data/local/tmp/llm2 && (LD_LIBRARY_PATH=. nohup ./llama-server -m /data/local/tmp/llm/Qwen3-1.7B-Q4_K_M.gguf --host 127.0.0.1 --port 8080 --jinja --reasoning-budget 0 -t 6 -tb 6 > srv.log 2>&1 < /dev/null &)"
adb shell "D=/data/local/tmp/stt2; [ -x \$D/whisper-server ] || D=/data/local/tmp/stt; cd \$D && (LD_LIBRARY_PATH=. nohup ./whisper-server -m /data/local/tmp/stt/ggml-base.bin --port 8090 -t 6 > srv.log 2>&1 < /dev/null &)"
adb forward tcp:8080 tcp:8080 >/dev/null; adb forward tcp:8090 tcp:8090 >/dev/null
printf "waiting for the AI model to load"
for i in $(seq 1 45); do curl -s localhost:8080/health | grep -q ok && break; printf "."; sleep 2; done; echo
echo "llama health: $(curl -s localhost:8080/health)"
echo "servers on the phone:"; adb shell "ps -A | grep -E 'whisper|llama'"
echo "warming up the AI (reads its prompts once, about 30 s)..."
node --experimental-strip-types mobile/board_check.ts | grep -E "TOTAL|ERROR"
echo "ready: open the app now"

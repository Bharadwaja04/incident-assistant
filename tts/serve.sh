#!/system/bin/sh
# Runs on the phone, once per request (started by toybox nc -L). GET /?t=<url-encoded text>  ->  WAV audio made by the Piper model.
D=/data/local/tmp/tts
export LD_LIBRARY_PATH=$D/lib:$LD_LIBRARY_PATH
read -r line                                   # GET /?t=... HTTP/1.1
while read -r h; do [ -z "${h%$(printf '\r')}" ] && break; done
q=${line#*t=}; q=${q%% HTTP*}
text=$(printf '%b' "$(echo "$q" | sed 's/+/ /g; s/%\(..\)/\\x\1/g')")
out=$D/out.$$.wav
$D/sherpa-onnx-offline-tts --num-threads=4 --vits-model=$D/m/en_US-lessac-medium.onnx --vits-tokens=$D/m/tokens.txt \
  --vits-data-dir=$D/m/espeak-ng-data --output-filename=$out "$text" >/dev/null 2>&1
if [ -s "$out" ]; then
  n=$(wc -c < "$out")
  printf 'HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\nContent-Length: %s\r\nConnection: close\r\n\r\n' "$n"
  cat "$out"
else
  printf 'HTTP/1.1 500 Error\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'
fi
rm -f "$out"

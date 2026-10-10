"""Record the 10 Phase-2 test clips from the Mac microphone.
Usage:  python record_clips.py            (default mic 0)
        python record_clips.py --mic 1    (if the wrong mic is used)
        python record_clips.py --list     (show microphones)
Clips 1-8 normal voice. Clips 9-10: make some background noise (music, talking, fan)."""
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "eval", "audio")
os.makedirs(OUT, exist_ok=True)

if "--list" in sys.argv:
    subprocess.run(["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""])
    sys.exit()
mic = sys.argv[sys.argv.index("--mic") + 1] if "--mic" in sys.argv else "0"
redo = "--redo" in sys.argv
items = [json.loads(l) for l in open(os.path.join(ROOT, "eval", "utterances.jsonl")) if l.strip()]

for i in range(10):
    n = f"clip{i + 1:02d}"
    wav, txt = os.path.join(OUT, n + ".wav"), os.path.join(OUT, n + ".txt")
    if os.path.exists(wav) and os.path.exists(txt) and not redo:
        print(f"{n}: already recorded, skipping (use --redo to redo)"); continue
    text = items[i]["text"]
    print(f"\n=== {n}{'  (NOISY: add background noise)' if i >= 8 else ''} ===\nSay:  {text}")
    input("Press Enter, then start speaking... ")
    p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "avfoundation", "-i", f":{mic}",
                          "-ar", "16000", "-ac", "1", wav], stdin=subprocess.PIPE)
    input("Recording. Press Enter when you finish the sentence... ")
    p.stdin.write(b"q"); p.stdin.flush(); p.wait()
    open(txt, "w").write(text + "\n")
    print(f"saved {n}")
print("\nDone. Next: python -m incident.gates --phase 0 && python -m incident.gates --phase 2 --whisper-model base")

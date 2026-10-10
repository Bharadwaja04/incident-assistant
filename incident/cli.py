"""Interactive demo. Type what you would say (or give .wav paths with --asr whisper)."""
import argparse
from .asr import TextASR, WhisperASR
from .llm import OpenAICompatExtractor, RuleExtractor
from .pipeline import Session
from .store import Store


def build(args):
    asr = WhisperASR(args.whisper_model) if args.asr == "whisper" else TextASR()
    rules = RuleExtractor()
    ex = OpenAICompatExtractor(args.base_url, args.model, mode=args.mode) if args.extractor == "llm" else rules
    return asr, ex, rules


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asr", choices=["text", "whisper"], default="text")
    ap.add_argument("--whisper-model", default="base")
    ap.add_argument("--extractor", choices=["rules", "llm"], default="rules")
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--model", default="qwen3:1.7b")
    ap.add_argument("--mode", choices=["json", "tools"], default="json")
    ap.add_argument("--db", default="incidents.db")
    a = ap.parse_args()
    asr, ex, rules = build(a)
    s = Session(asr, ex, Store(a.db), fallback=rules if ex is not rules else None)
    print("Say the incident (Ctrl-C to quit).")
    while True:
        x = input("you> ").strip()
        if x.lower() == "submit" and not s.expected:
            print("saved id", s.submit()); s = Session(asr, ex, Store(a.db), fallback=s.fallback); continue
        r = s.turn(x)
        print(f"bot> {r.message}\n     [{r.total_s*1000:.0f} ms | asr {r.asr_s*1000:.0f} | extract {r.extract_s*1000:.0f}]")

if __name__ == "__main__":
    main()

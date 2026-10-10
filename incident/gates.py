"""Phase gates: GO / NO-GO checks. Run:  python -m incident.gates --phase 1
Phases 0-3 measure on the laptop. Phases 4-5 read a JSON report produced ON THE PHONE (see PLAN.md)."""
from __future__ import annotations
import argparse, glob, json, os, sys
from datetime import datetime
from .asr import TextASR
from .llm import OpenAICompatExtractor, RuleExtractor
from .metrics import peak_rss_mb, pct, wer
from .pipeline import Session
from .schema import REQUIRED, missing

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
NOW = datetime(2026, 10, 10, 21, 0)   # 9 PM: so every spoken time in the tests is in the past
ASR_MB = {"tiny": 75, "tiny.en": 75, "base": 145, "base.en": 145, "small": 480, "small.en": 480}


def jl(name):
    return [json.loads(l) for l in open(os.path.join(ROOT, "eval", name)) if l.strip()]


# ---- scoring helpers ------------------------------------------------------------------
def _ok(key, gold, pred):
    if gold is True:
        return bool(pred)
    if key == "location":
        p, g = (pred or "").lower(), gold.lower()
        return bool(p) and (g in p or p in g)
    if key == "time":
        return bool(pred) and pred[11:16] == gold
    return (pred or "") == gold


def score_extraction(ex, items=None):
    items = items or jl("utterances.jsonl")
    hits = tot = halluc = absent_tot = valid = 0
    lat, per_field, first_err, hal_fields = [], {}, [], {}
    hh = ht = 0
    for i, it in enumerate(items, 1):
        if os.environ.get("INCIDENT_DEBUG"):
            print(f"[{i}/{len(items)}] {it['text'][:70]}", file=sys.stderr, flush=True)
        e = ex.extract(it["text"], {}, NOW)
        if os.environ.get("INCIDENT_DEBUG"):
            print(f"   -> {e.seconds:.1f}s valid={e.valid} attempts={e.attempts} fields={e.fields}", file=sys.stderr, flush=True)
        valid += e.valid
        lat.append(e.seconds)
        if not e.valid and not first_err:
            first_err.append((it["text"][:60], e.error, e.raw[:300]))
        f = e.fields
        for k, g in it["gold"].items():
            pred = f.get("datetime") if k == "time" else f.get(k)
            ok = _ok(k, g, pred)
            hits += ok; tot += 1
            if it.get('hard'): hh += ok; ht += 1          # hard = natural speech, corrections, relative times
            per_field.setdefault(k, [0, 0]); per_field[k][0] += ok; per_field[k][1] += 1
        for k in it.get("absent", []):
            absent_tot += 1
            bad = bool(f.get("datetime" if k == "time" else k)); halluc += bad
            if bad: hal_fields[k] = hal_fields.get(k, 0) + 1
    if first_err:
        print("FIRST FAILURE:\n  sentence:", first_err[0][0], "\n  error:   ", first_err[0][1], "\n  raw model output:", repr(first_err[0][2]))
    return dict(n=len(items), json_valid=valid / len(items), field_acc=hits / tot,
                halluc=halluc / max(1, absent_tot), hard=hh / max(1, ht), hal_fields=hal_fields, p50=pct(lat, 50), p95=pct(lat, 95),
                per_field={k: v[0] / v[1] for k, v in per_field.items()})


def run_dialogues(ex, fb):
    res, lat, done, ok_final, bad = [], [], 0, 0, []
    ds = jl("dialogues.jsonl")
    for d in ds:
        s = Session(TextASR(), ex, None, now_fn=lambda: NOW, fallback=fb)
        r = None
        for t in d["turns"]:
            r = s.turn(t); lat.append(r.total_s)
        done += r.ready
        g = d["final"]
        good = all(_ok(k, v, s.draft.get("datetime" if k == "time" else k)) for k, v in g.items())
        ok_final += good
        if not good or not r.ready:
            bad.append(f"  dialogue '{d['name']}': ready={r.ready} wanted={g}\n     got={ {k: v for k, v in s.draft.items() if k != 'description'} }\n     last bot message: {r.message.splitlines()[0]}")
    crashes = 0
    for g in json.load(open(os.path.join(ROOT, "eval", "garbage.json"))):
        try:
            s = Session(TextASR(), ex, None, now_fn=lambda: NOW, fallback=fb)
            r = s.turn(g)
            if not r.needs_repeat and r.ready:
                crashes += 1  # garbage must never produce a "ready" report
        except Exception:
            crashes += 1
    if bad: print("DIALOGUE FAILURES:\n" + "\n".join(bad))
    return dict(n=len(ds), completed=done / len(ds), final_correct=ok_final / len(ds), p50=pct(lat, 50), p95=pct(lat, 95), garbage_failures=crashes)


class ReplayASR:
    """Hands back an ASR result we already have, so the real Session logic can run on it."""
    def __init__(self, r): self.r = r
    def transcribe(self, _): return self.r


class FakeASR:
    """Pretends to be Whisper: input is (text, {word: probability}); unlisted words get 0.95."""
    def transcribe(self, x):
        from .asr import ASRResult
        text, probs = x if isinstance(x, tuple) else (x, {})
        return ASRResult(text, 0.0, 0.0, 0.9, [(w, probs.get(w.lower().strip(".,"), 0.95)) for w in text.split()])


def run_uncertainty(ex, fb):
    """Shaky words, garbled times, stutters, corrections. Returns (passed, total, failures)."""
    def sess(): return Session(FakeASR(), ex, None, now_fn=lambda: NOW, fallback=fb)
    fails, n = [], 0
    def check(name, ok):
        nonlocal n; n += 1
        if not ok: fails.append(name)

    # S1: Whisper unsure about "pantry" -> asks, "yes" keeps it
    s = sess(); r = s.turn(("Small fire in the pantry at 6:15 PM, minor.", {"pantry": 0.3}))
    check("S1 asks to confirm location", r.confirming == "location" and "pantry" in r.message and "location" not in r.draft)
    r = s.turn("yes")
    check("S1 yes keeps it", s.draft.get("location") == "pantry" and r.ready)
    # S2: ... "no" drops it and asks properly, then accepts the new answer
    s = sess(); s.turn(("Small fire in the pantry at 6:15 PM, minor.", {"pantry": 0.3}))
    r = s.turn("no")
    check("S2 no -> asks where", "location" not in s.draft and "Where" in r.message)
    r = s.turn("kitchen")
    check("S2 new answer used", s.draft.get("location") == "kitchen" and r.ready)
    # S3: time garbled by ASR ('650 pm'): never silently accept a wrong time
    s = sess(); r = s.turn("The lift on block B is stuck at 16, 650 pm, serious.")
    check("S3 does not guess the time", s.draft.get("datetime") in (None,) or r.confirming == "datetime")
    r = s.turn("6:15 PM")
    check("S3 corrected time accepted", s.draft.get("datetime", "")[11:16] == "18:15" and not s.queue)
    # S4: stutter + filler
    s = sess(); r = s.turn("Uh, small fire in the in the kitchen at 6:15 6:15 PM, minor.")
    check("S4 stutter cleaned", s.draft.get("location") == "kitchen" and s.draft.get("datetime", "")[11:16] == "18:15")
    # S5: shaky time word -> asks to confirm the time
    s = sess(); r = s.turn(("Fire in the kitchen at 6:15 PM, minor.", {"6:15": 0.2}))
    check("S5 shaky time -> confirm", r.confirming == "datetime" and "6:15 PM" in r.message)
    r = s.turn("yeah")
    check("S5 yes keeps time", s.draft.get("datetime", "")[11:16] == "18:15" and r.ready)
    # S6: "this morning" but the time came out as PM -> must ask
    s = sess(); r = s.turn("A worker slipped near the loading dock this morning at 9 PM, minor.")
    check("S6 morning vs PM contradiction", r.confirming == "datetime")
    # S7: relative time + names, severity derived with the floor (plan walk-through)
    s = sess(); s.turn("Someone slipped near the cafeteria and hurt their ankle.")
    r = s.turn("About twenty minutes ago. It was Priya from accounts, and Ravi saw it.")
    check("S7 relative time -> 15:40", s.draft.get("datetime", "")[11:16] == "20:40" and r.ready)
    check("S7 severity derived high + flagged", s.draft.get("severity") == "high" and s.flags().get("severity", {}).get("status") == "derived")
    # S8: future time is rejected and asked again
    s = sess(); r = s.turn("Fire in the kitchen at 11 PM")
    check("S8 future time rejected", "datetime" not in s.draft and "future" in r.message)
    # S9: room name repaired from the transcript
    s = sess(); s.turn("Water leak near conference room too around 10 AM")
    check("S9 room fixed", s.draft.get("location") == "Conference Room 2")
    # S10: stated severity below the floor is kept but flagged
    s = sess(); s.turn("Small fire in the kitchen at 3 PM, minor.")
    check("S10 stated low kept, flagged", s.draft.get("severity") == "low" and s.flags().get("severity", {}).get("status") == "check")
    # S11: voice corrections on the review screen
    s = sess(); s.turn("Water leak near conference room 2 around 10 AM, maintenance isolated the area.")
    s.voice_edit("change severity to high"); s.voice_edit("add Priya as a witness"); s.voice_edit("it was room 3, not room 2"); s.voice_edit("add that the floor was wet")
    check("S11 voice edits", s.draft.get("severity") == "high" and "Priya" in s.draft.get("witnesses", [])
          and s.draft.get("location") == "Conference Room 3" and "floor was wet" in s.draft.get("description", "") and s.voice_edit("submit").intent == "submit")
    # S12: combined question when location and time are both missing, max 3 questions
    s = sess(); r = s.turn("A pipe burst")
    check("S12 combined question", "When and where" in r.message)
    # S14: compact spoken time ("847") is read as 8:47 and confirmed once, never invented from the clock
    s = sess(); s.turn("A light is not working in the railway station")
    r = s.turn("So the time out, 847.")
    check("S14 compact time asks to confirm 8:47", r.confirming == "datetime" and "8:47" in r.message)
    r = s.turn("yes")
    check("S14 yes keeps 8:47 PM (latest past reading)", s.draft.get("datetime", "")[11:16] == "20:47" and r.ready)
    # S15: Whisper noise text is never a report or an answer; the question is asked again
    s = sess(); s.turn("A light is not working in the railway station")
    r = s.turn("Thank you. Thank you. Look.")
    check("S15 noise text re-asks", r.needs_repeat and "When did it happen" in r.message and "datetime" not in s.draft)
    # S16: after 3 misses in a row hand over to the form
    s = sess(); s.turn("A light is not working in the railway station")
    rs = [s.turn(x) for x in ["Thank you.", "you", "Thanks for watching"]]
    check("S16 hands over to the form", rs[-1].state == "REVIEW" and not rs[-1].ready)
    # S17: spoken words and half past
    s = sess(); s.turn("Fire in the kitchen"); s.turn("half past six")
    check("S17 half past six", s.draft.get("datetime", "")[11:16] == "18:30")
    s = sess(); s.turn("Fire in the kitchen"); r = s.turn("eight forty seven pm")
    check("S17 eight forty seven pm", s.draft.get("datetime", "")[11:16] == "20:47")
    # S13: severity is never asked
    s = sess(); r = s.turn("Smoke in the kitchen at 3 PM")
    check("S13 ready without asking severity", r.ready)
    return n - len(fails), n, fails


def run_disfluency():
    """The cleaning layer (normalize.py): raw Whisper-style text -> expected clean text, incl. cases that must NOT change."""
    from .normalize import normalize
    cases, bad = jl("disfluency.jsonl"), []
    for c in cases:
        got = normalize(c["raw"]).text
        if got != c["clean"]:
            bad.append(f"  cleaning: {c['raw']!r}\n     got  {got!r}\n     want {c['clean']!r}")
    if bad: print("DISFLUENCY FAILURES:\n" + "\n".join(bad))
    return 1 - len(bad) / len(cases), len(cases)


def run_cleaner_guard():
    """The safety guard around the AI cleaner: accepts pure clean-ups, rejects invented / dropped / flipped facts."""
    from .cleaner import accept_clean
    cases, bad = jl("cleaner_guard.jsonl"), []
    for c in cases:
        if accept_clean(c["raw"], c["cleaned"]) != c["accept"]:
            bad.append(f"  guard: {c['raw']!r} -> {c['cleaned']!r} should be {'accepted' if c['accept'] else 'rejected'}")
    if bad: print("CLEANER GUARD FAILURES:\n" + "\n".join(bad))
    return 1 - len(bad) / len(cases), len(cases)


def run_cleaner_llm(ex):
    """AI cleaner on the disfluency set (needs the LLM): how often does it produce text the guard accepts that is also clean?"""
    import re
    from .cleaner import LLMCleaner
    from .normalize import normalize
    cl, ok, n, rows = LLMCleaner(ex), 0, 0, []
    for c in jl("disfluency.jsonl"):
        if len(c["raw"].split()) <= 3:
            continue
        out, how = cl.clean(c["raw"])
        n += 1
        clean_ok = how == "llm" and not re.search(r"\b(uh|um|umm|hmm)\b", out, re.I) and not re.search(r"\b(\w+)\s+\1\b", out, re.I)
        ok += clean_ok
        rows.append(f"  [{how:5}] {'ok ' if clean_ok else 'BAD'} {c['raw']!r}\n          -> {out!r}")
    print("AI cleaner results:\n" + "\n".join(rows))
    return ok / max(1, n), n


# ---- gate table -----------------------------------------------------------------------
class Table:
    def __init__(self, title): self.rows, self.title = [], title
    def add(self, name, val, op, thr, unit=""):
        if val is None: st = "NOT RUN"
        else: st = "PASS" if (val >= thr if op == ">=" else val <= thr) else "FAIL"
        self.rows.append((name, val, op, thr, unit, st))
    def show(self):
        print(f"\n=== {self.title} ===")
        for n, v, op, t, u, st in self.rows:
            vs = "-" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))
            print(f"  [{st:7}] {n:<42} {vs:>9} {u:<4} (need {op} {t}{u})")
        bad = [r for r in self.rows if r[5] == "FAIL"]
        nr = [r for r in self.rows if r[5] == "NOT RUN"]
        verdict = "NO-GO" if bad else ("INCOMPLETE" if nr else "GO")
        print(f"  --> {verdict}")
        return verdict


def build_extractor(a):
    rules = RuleExtractor()
    return (OpenAICompatExtractor(a.base_url, a.model, mode=a.mode), rules) if a.extractor == "llm" else (rules, None)


def phase0(a):
    t = Table("Phase 0: contract + test set")
    items = jl("utterances.jsonl")
    t.add("labelled utterances", len(items), ">=", 20)
    t.add("scripted dialogues", len(jl("dialogues.jsonl")), ">=", 6)
    t.add("rule baseline runs end to end", int(score_extraction(RuleExtractor(), items)["json_valid"] == 1), ">=", 1)
    t.add("recorded WAV clips with transcripts", len(glob.glob(os.path.join(ROOT, "eval/audio/*.wav"))), ">=", 10)
    return t


def phase1(a):
    ex, fb = build_extractor(a)
    base = score_extraction(RuleExtractor())
    s = score_extraction(ex)
    print("rule baseline: field_acc=%.2f hard-sentences=%.2f halluc=%.2f" % (base["field_acc"], base["hard"], base["halluc"]))
    print("this model:    field_acc=%.2f hard-sentences=%.2f | invented fields: %s" % (s["field_acc"], s["hard"], s["hal_fields"] or "none"))
    print("per-field:", {k: round(v, 2) for k, v in s["per_field"].items()})
    t = Table(f"Phase 1: text -> structured fields ({ex.name})")
    t.add("JSON valid rate", s["json_valid"], ">=", 0.95)
    t.add("field accuracy", s["field_acc"], ">=", 0.85)
    if a.extractor == "llm":   # comparing the rules against themselves would be meaningless
        t.add("beats rules on the 5 hard sentences by", s["hard"] - base["hard"], ">=", 0.05)
    t.add("hallucinated absent fields (plan target <2%)", s["halluc"], "<=", 0.05)
    t.add("extract latency p95 (laptop, 4 threads)", s["p95"], "<=", 4.0, "s")
    if a.llm_size_gb is not None:
        t.add("LLM file size", a.llm_size_gb, "<=", a.device_ram_gb * 0.35, "GB")
    else:
        t.add("LLM file size", None, "<=", a.device_ram_gb * 0.35, "GB")
    t.add("peak RAM of this process", peak_rss_mb() / 1000, "<=", a.device_ram_gb * 0.5, "GB")
    return t


def phase2(a):
    from .asr import WhisperASR
    wavs = sorted(glob.glob(os.path.join(ROOT, "eval/audio/*.wav")))
    t = Table(f"Phase 2: speech -> text (whisper {a.whisper_model})")
    if len(wavs) < 5:
        t.add("WER", None, "<=", 0.15); t.add("real-time factor", None, "<=", 0.5); return t
    from .asr import DOMAIN_PROMPT
    asr = WhisperASR(a.whisper_model, prompt=DOMAIN_PROMPT if a.bias else None)
    ex, _ = build_extractor(a)
    items = jl("utterances.jsonl")
    w, rtf, hs, hp, tot, silent, asked = [], [], 0, 0, 0, 0, 0
    for p in wavs:
        ref = open(p[:-4] + ".txt").read().strip()
        r = asr.transcribe(p); w.append(wer(ref, r.text)); rtf.append(r.rtf)
        lw = [f"{wd}:{pr:.2f}" for wd, pr in r.words if pr < 0.6]
        if lw: print(f"{os.path.basename(p)} low-confidence words: " + " ".join(lw))
        idx = int(os.path.basename(p)[4:6]) - 1            # clipNN <-> utterance NN
        if idx < len(items):
            gold = items[idx]["gold"]
            fp = ex.extract(ref, {}, NOW).fields
            for k, g in gold.items():
                hp += _ok(k, g, fp.get("datetime" if k == "time" else k))
            sess = Session(ReplayASR(r), ex, None, now_fn=lambda: NOW, fallback=RuleExtractor())
            sess.turn(None)                                  # the REAL pipeline: cleaning, cross-checks, confirm questions
            flagged = {f for f, _ in sess.queue}
            notes = []
            for k, g in gold.items():
                key = "datetime" if k == "time" else k
                pred = sess.draft.get(key)
                ok = _ok(k, g, pred); hs += ok; tot += 1
                if ok: continue
                if key in flagged: asked += 1; notes.append(f"{k}: app asks 'is that right?'")
                elif not pred and key in REQUIRED: asked += 1; notes.append(f"{k} missing: app asks")
                elif not pred: notes.append(f"{k} not captured (optional field)")
                else: silent += 1; notes.append(f"{k}={pred!r} WRONG and not flagged (wanted {g!r})")
            if notes: print(f"clip{idx+1:02d}: " + "; ".join(notes))
        print(f"{os.path.basename(p)}  WER={w[-1]:.2f}  conf={r.confidence:.2f}\n   said : {ref}\n   heard: {r.text}")
    t.add("clips tested", len(wavs), ">=", 5)
    t.add("mean WER (rough proxy, 10 clips)", sum(w) / len(w), "<=", 0.20)
    t.add("real-time factor p95 (laptop)", pct(rtf, 95), "<=", 0.5)
    if tot:
        print(f"\nfields right from a PERFECT transcript: {hp/tot:.2f} | right from speech: {hs/tot:.2f} | app asks/confirms: {asked/tot:.2f} | silently wrong: {silent/tot:.2f}   (extractor: {ex.name})")
        t.add("fields right, or the app asks (from speech)", (hs + asked) / tot, ">=", 0.90)
        t.add("fields WRONG but not flagged (silent errors)", silent / tot, "<=", 0.10)
        t.add("fields the app asks about (annoyance)", asked / tot, "<=", 0.30)
    t.add("ASR model size", ASR_MB.get(a.whisper_model, 999) / 1000, "<=", 0.2, "GB")
    return t


def phase3(a):
    ex, fb = build_extractor(a)
    d = run_dialogues(ex, fb)
    t = Table(f"Phase 3: full conversation loop ({ex.name})")
    t.add("dialogues reaching review screen", d["completed"], ">=", 1.0)
    t.add("final fields correct", d["final_correct"], ">=", 0.9)
    t.add("garbage inputs wrongly accepted", d["garbage_failures"], "<=", 0)
    ok, tot, fails = run_uncertainty(ex, fb)
    if fails: print("uncertainty scenarios failed:", fails)
    t.add("shaky-speech scenarios passed", ok / tot, ">=", 1.0)
    gs, gn = run_cleaner_guard()
    t.add(f"AI-cleaner safety guard ({gn} cases)", gs, ">=", 1.0)
    dscore, dn = run_disfluency()
    t.add(f"cleaning layer ({dn} disfluency cases)", dscore, ">=", 1.0)
    if a.cleaner == "llm" and a.extractor == "llm":
        cs, cn = run_cleaner_llm(ex)
        t.add(f"AI cleaner accepted and clean ({cn} cases)", cs, ">=", 0.8)
    t.add("turn latency p50 (text only)", d["p50"], "<=", 3.0, "s")
    t.add("turn latency p95 (text only)", d["p95"], "<=", 6.0, "s")
    return t


def phase45(a, phase):
    t = Table(f"Phase {phase}: ON-PHONE report ({a.report})")
    if not a.report or not os.path.exists(a.report):
        print("No report file. Produce one on the phone (see PLAN.md, 'device report'), then pass --report file.json")
        t.add("device report present", None, ">=", 1); return t
    r = json.load(open(a.report))
    g = lambda k: r.get(k)
    ram = g("device_ram_mb") or a.device_ram_gb * 1000
    t.add("ASR real-time factor", g("asr_rtf"), "<=", 1.0)
    t.add("LLM time to first token", g("llm_ttft_s"), "<=", 2.5, "s")
    t.add("LLM tokens / second", g("llm_tokens_per_s"), ">=", 8.0)
    t.add("end-to-end p50 (speech end -> form)", g("e2e_p50_s"), "<=", 8.0, "s")
    t.add("end-to-end p95", g("e2e_p95_s"), "<=", 12.0, "s")
    t.add("peak RAM (fraction of device)", None if g("peak_ram_mb") is None else g("peak_ram_mb") / ram, "<=", 0.6)
    t.add("JSON valid rate on device", g("json_valid_rate"), ">=", 0.95)
    t.add("field accuracy on device", g("field_accuracy"), ">=", 0.80)
    t.add("works in airplane mode (1=yes)", None if g("airplane_mode_ok") is None else int(g("airplane_mode_ok")), ">=", 1)
    if phase == 5:
        t.add("live demo success over 20 runs", g("demo_success_rate"), ">=", 0.9)
        t.add("slowdown after 10 back-to-back runs", g("thermal_slowdown_pct"), "<=", 30, "%")
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", type=int, required=True)
    ap.add_argument("--extractor", choices=["rules", "llm"], default="rules")
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--model", default="qwen3:1.7b")
    ap.add_argument("--mode", choices=["json", "tools"], default="json", help="tools = native tool calls via tools.json")
    ap.add_argument("--whisper-model", default="base")
    ap.add_argument("--device-ram-gb", type=float, default=8.0, help="RAM of the Snapdragon device")
    ap.add_argument("--llm-size-gb", type=float, default=None, help="size of the quantised LLM file")
    ap.add_argument("--cleaner", choices=["llm", "rules"], default="rules", help="llm: also score the AI cleaning layer in phase 3")
    ap.add_argument("--report", default=None)
    ap.add_argument("--bias", action="store_true", help="give Whisper a vocabulary hint (initial prompt)")
    ap.add_argument("--verbose", action="store_true", help="print each call, timing and raw model output")
    a = ap.parse_args()
    if a.verbose:
        os.environ["INCIDENT_DEBUG"] = "1"
    t = {0: phase0, 1: phase1, 2: phase2, 3: phase3}.get(a.phase) or (lambda x: phase45(x, a.phase))
    v = t(a).show()
    sys.exit(0 if v == "GO" else 1)

if __name__ == "__main__":
    main()

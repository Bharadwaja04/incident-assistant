"""Local demo app: the three screens of the plan (Record, Review, Done) + History, wired to the real pipeline.
It is the working prototype AND the spec for the Android screens (see UI_SPEC.md).
Run:  python -m incident.app --asr whisper --extractor llm --model qwen3:1.7b        then open http://localhost:8000
      python -m incident.app                                      (typing only, rule-based: works with nothing installed)"""
from __future__ import annotations
import argparse, json, os, subprocess, tempfile, threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .asr import TextASR
from .cleaner import LLMCleaner
from .llm import OpenAICompatExtractor, RuleExtractor
from .pipeline import Session
from .rooms import ROOMS
from .schema import ENUMS, SCHEMA
from .store import Store

HERE = os.path.dirname(os.path.abspath(__file__))


class MixedASR:
    """Text typed in the box, or a wav file path from the mic: same Session either way."""
    def __init__(self, whisper=None):
        self.text, self.w = TextASR(), whisper

    def transcribe(self, x):
        if isinstance(x, dict) and "asr" in x:
            return x["asr"]
        if isinstance(x, dict) and "wav" in x:
            return self.w.transcribe(x["wav"])
        return self.text.transcribe(x)


class App:
    def __init__(self, a):
        whisper = None
        if a.asr == "whisper":
            from .asr import WhisperASR, DOMAIN_PROMPT
            whisper = WhisperASR(a.whisper_model, prompt=DOMAIN_PROMPT, denoise=a.denoise == "on")
        self.whisper = whisper
        self.ex = OpenAICompatExtractor(a.base_url, a.model, mode=a.mode)          # AI only: no rule-based extractor in the app
        self.cleaner = LLMCleaner(self.ex, rules_fallback=False) if a.cleaner == "llm" else None
        self.store = Store(a.db, a.reports)
        self.pending = {}                       # audio transcribed but not yet sent: id -> ASRResult (keeps word confidences)
        self.asr = MixedASR(whisper)
        self.lock = threading.Lock()
        self.new()

    def new(self):
        self.s = Session(self.asr, self.ex, self.store, fallback=None, cleaner=self.cleaner, rule_cleaning=False)

    def state(self, extra=None):
        s = self.s
        d = {"draft": s.draft, "flags": s.flags(), "full_text": s.full_text, "missing": [f for f in SCHEMA["required"] if not s.draft.get(f)],
             "pending": [{"field": f, "value": v} for f, v in s.queue]}
        d.update(extra or {})
        return d

    def wav_from(self, blob: bytes) -> str:
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            f.write(blob)
        out = f.name + ".wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", f.name, "-ar", "16000", "-ac", "1", out], check=True)
        os.unlink(f.name)
        return out

    def _need_whisper(self):
        if not self.whisper:
            raise ValueError("Voice needs the server started with --asr whisper. Type instead.")

    def transcribe_blob(self, blob: bytes, words: bool):
        self._need_whisper()
        wav = self.wav_from(blob)
        try:
            return self.whisper.transcribe(wav, words=words)
        finally:
            os.unlink(wav)

    def turn(self, body: dict, blob: bytes = None):
        speech = body.get("text", "")
        pend = self.pending.pop(body.get("asr_id"), None)
        norm = lambda t: " ".join(str(t).lower().split())
        if pend is not None and blob is None and norm(pend.text) == norm(speech):
            speech = {"asr": pend}              # text unchanged by the person: keep Whisper's word confidences
        if blob is not None:
            if not self.whisper:
                raise ValueError("Voice needs the server started with --asr whisper. Type instead.")
            speech = {"wav": self.wav_from(blob)}
        r = self.s.turn(speech)
        return self.state({"transcript": r.transcript, "message": r.message, "state": r.state, "ready": r.ready,
                           "confirming": r.confirming, "needs_repeat": r.needs_repeat, "raw": r.raw, "clean_how": r.clean_how, "ai_missing": r.ai_missing, "ai_trace": r.ai_trace,
                           "timings": {"asr": round(r.asr_s, 2), "extract": round(r.extract_s, 2), "total": round(r.total_s, 2)}})

    def voice_edit(self, body: dict, blob: bytes = None):
        text = body.get("text", "")
        if blob is not None:
            if not self.whisper:
                raise ValueError("Voice needs the server started with --asr whisper. Type instead.")
            text = self.whisper.transcribe(self.wav_from(blob)).text
        r = self.s.voice_edit(text)
        return self.state({"heard": text, "ok": r.ok, "message": r.message, "intent": r.intent})

    def handle(self, path, body, blob):
        if path == "/api/partial":                  # live text: never queue behind other work, skip if the model is busy
            if not self.lock.acquire(blocking=False):
                return {"text": "", "busy": True}
            try:
                return {"text": self.transcribe_blob(blob, words=False).text}
            finally:
                self.lock.release()
        with self.lock:
            if path == "/api/partial":              # live text while the person is still speaking
                return {"text": self.transcribe_blob(blob, words=False).text}
            if path == "/api/transcribe":           # final text for the whole recording, shown in the box to edit before sending
                r = self.transcribe_blob(blob, words=True)
                rid = str(len(self.pending) + int(datetime.now().timestamp() * 1000))
                self.pending[rid] = r
                for k in list(self.pending)[:-8]:
                    self.pending.pop(k, None)
                from .fluency import analyze, note
                from .normalize import normalize
                rep = analyze(r.timings)
                rep["removed"] = [c for c in normalize(r.text).changes][:6] if hasattr(normalize(r.text), "changes") else []
                return {"id": rid, "text": r.text, "confidence": round(r.confidence, 2), "fluency": rep,
                        "fluency_note": note(rep, [str(c) for c in rep["removed"]]), "audio": r.audio_stats}
            if path == "/api/turn": return self.turn(body, blob)
            if path == "/api/voice_edit": return self.voice_edit(body, blob)
            if path == "/api/edit":
                self.s.edit(body["field"], body.get("value"))
                return self.state({"notice": self.s.notice})
            if path == "/api/submit":
                rid = self.s.submit()
                return {"id": rid, "summary": self.s.summary(), "draft": self.s.draft}
            if path == "/api/new":
                self.new()
                return self.state()
        raise KeyError(path)


def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _send(self, code, obj, ctype="application/json"):
            data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
            try:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass                                        # the page cancelled this request (e.g. a live-text update when you tapped stop): not an error

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                return self._send(200, open(os.path.join(HERE, "static", "index.html"), "rb").read(), "text/html; charset=utf-8")
            if self.path == "/api/status":
                return self._send(200, {"enums": ENUMS, "rooms": ROOMS, "asr": "whisper" if app.whisper else "typed text",
                                        "llm": getattr(app.ex, "name", "llm"), "mic": bool(app.whisper),
                                        "now": datetime.now().isoformat(timespec="minutes")})
            if self.path == "/api/history":
                return self._send(200, app.store.all())
            self._send(404, {"error": "not found"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            ctype = self.headers.get("Content-Type", "")
            try:
                if ctype.startswith("audio") or ctype.startswith("video"):
                    out = app.handle(self.path, {}, raw)
                else:
                    out = app.handle(self.path, json.loads(raw or b"{}"), None)
                self._send(200, out)
            except KeyError:
                self._send(404, {"error": "not found"})
            except Exception as e:
                self._send(400, {"error": str(e)})
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asr", choices=["text", "whisper"], default="text")
    ap.add_argument("--whisper-model", default="base")
    ap.add_argument("--extractor", choices=["llm"], default="llm", help="kept for old commands; the app is AI-only")
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--model", default="qwen3:1.7b")
    ap.add_argument("--mode", choices=["json", "tools"], default="tools", help="tools = native tool calls (llama-server --jinja)")
    ap.add_argument("--cleaner", choices=["llm", "off"], default="llm", help="llm = AI cleaning layer (guarded); off = send the text as heard")
    ap.add_argument("--denoise", choices=["on", "off"], default="on", help="amplitude (noise) gate before speech to text")
    ap.add_argument("--db", default="incidents.db")
    ap.add_argument("--reports", default="reports")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    app = App(a)
    print(f"Incident Assistant on http://localhost:{a.port}   asr={'whisper ' + a.whisper_model if app.whisper else 'typed text'}  llm={getattr(app.ex, 'name', 'llm')}  cleaner={'AI (guarded)' if app.cleaner else 'off'}")
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(app)).serve_forever()


if __name__ == "__main__":
    main()

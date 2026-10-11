"""ASR backends. Same interface everywhere: transcribe(path) -> ASRResult."""
from __future__ import annotations
import re, time, wave
from dataclasses import dataclass, field


@dataclass
class ASRResult:
    text: str
    seconds: float          # wall time spent transcribing
    audio_seconds: float    # length of the audio
    confidence: float = 1.0  # 0..1, 1.0 when unknown
    words: list = field(default_factory=list)  # [(word, probability)], empty when the backend can't tell
    timings: list = field(default_factory=list)  # [(word, start, end)] seconds
    audio_stats: dict = field(default_factory=dict)  # magnitude filter: threshold, noise floor, % gated

    @property
    def rtf(self):  # real-time factor: <1.0 means faster than real time
        return self.seconds / self.audio_seconds if self.audio_seconds else 0.0


def wav_seconds(path):
    try:
        with wave.open(path) as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        return 0.0


def load_wav(path):
    """Read a 16-bit mono WAV into float32 samples (what Whisper wants). Returns (samples, sample_rate)."""
    import numpy as np
    with wave.open(path) as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"{path}: need 16-bit WAV, got {8*width}-bit")
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    peak = float(np.abs(x).max()) if len(x) else 0.0
    if 0 < peak < 0.5:                     # quiet voice: raise the level (capped so noise isn't blown up)
        x = np.clip(x * min(0.7 / peak, 8.0), -1.0, 1.0).astype(np.float32)
    if rate != 16000:                      # simple resample, fine for speech
        n = int(len(x) * 16000 / rate)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    return x, 16000


DOMAIN_PROMPT = ("Incident report. Fire, smoke, small fire, water leak, pipe burst, sparks, short circuit, electrical panel, "
                 "slipped, injured, fainted, ambulance, first aid, theft, security, suspicious person, chemical spill, "
                 "lift stuck, conveyor belt, machine, motor, hydraulic press, forklift, assembly line, shop floor, pump, valve, boiler, compressor, server room, loading dock, pantry, cafeteria, conference room, warehouse, lab, block B, "
                 "corridor, washroom, 10 AM, 2:30 PM, 6:15 PM, critical, serious, minor, moderate.")


def clean_transcript(t: str) -> str:
    """Disfluency cleaning (see normalize.py): 'uh the the fire' -> 'the fire', '6:15 6:15 PM' -> '6:15 PM', 's-s-small fire' -> 'small fire'."""
    from .normalize import normalize
    return normalize(t).text


class TextASR:
    """Typed text pretending to be speech. For tests and laptop dev with no mic."""
    def transcribe(self, text: str) -> ASRResult:
        return ASRResult(text=text, seconds=0.0, audio_seconds=0.0)


class WhisperASR:
    """Laptop stand-in for on-device Whisper. pip install faster-whisper.
    On the phone this is replaced by Whisper from Qualcomm AI Hub run through QAIRT."""
    def __init__(self, model="base", threads=4, language="en", prompt=None, denoise=True):
        from faster_whisper import WhisperModel
        self.m = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=threads)
        self.language, self.prompt, self.denoise = language, prompt, denoise

    def transcribe(self, path: str, words: bool = True) -> ASRResult:
        import math
        t0 = time.perf_counter()
        audio, stats = (load_wav(path)[0] if str(path).lower().endswith(".wav") else path), {}   # avoids a PyAV version bug
        if self.denoise and not isinstance(audio, str):
            from .audio import high_pass, magnitude_filter
            audio, stats = magnitude_filter(high_pass(audio))           # stage B: amplitude gate before speech to text
        segs, info = self.m.transcribe(audio, language=self.language, beam_size=1, vad_filter=True,
                                       initial_prompt=self.prompt, word_timestamps=words,
                                       condition_on_previous_text=False)
        segs = [sg for sg in segs if not (sg.no_speech_prob > 0.6 and sg.avg_logprob < -1.0)]   # Whisper's 'Thank you.' on noise
        text = " ".join(s.text.strip() for s in segs).strip()
        conf = 1.0
        if segs:
            conf = sum(math.exp(s.avg_logprob) for s in segs) / len(segs)
        words = [(w.word.strip(), w.probability) for sg in segs for w in (sg.words or [])]
        timings = [(w.word.strip(), w.start, w.end) for sg in segs for w in (sg.words or [])]
        return ASRResult(text, time.perf_counter() - t0, info.duration, conf, words, timings, stats)


class BoardWhisperASR:
    """Speech to text on the DEVICE: posts the recording to whisper-server running on the board (reached through adb forward).
    The audio is transcribed by the board; the laptop only moves bytes. Same interface as WhisperASR."""
    def __init__(self, url="http://localhost:8081/inference", language="en", timeout=120):
        self.url, self.language, self.timeout = url, language, timeout

    def transcribe(self, path: str, words: bool = True) -> ASRResult:
        import json, urllib.request, uuid
        t0 = time.perf_counter()
        b = uuid.uuid4().hex
        data = open(path, "rb").read()
        parts = []
        for k, v in (("response_format", "json"), ("language", self.language), ("temperature", "0.0")):
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode() + data + b"\r\n")
        parts.append(f"--{b}--\r\n".encode())
        req = urllib.request.Request(self.url, b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={b}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            text = (json.load(r).get("text") or "").strip()
        text = re.sub(r"\s+", " ", re.sub(r"\[[A-Z _]+\]|\([a-z ]+\)", " ", text)).strip()      # drop [BLANK_AUDIO] style tags
        return ASRResult(text, time.perf_counter() - t0, wav_seconds(path))

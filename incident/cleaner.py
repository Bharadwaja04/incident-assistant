"""AI cleaning layer between Whisper and the extraction LLM.
The LLM rewrites the transcript without stutters, fillers, repeats and false starts. A code guard makes sure it only REMOVED
things: no new words, no dropped facts. If the guard says no (or the model is down), the deterministic cleaner (normalize.py)
is the safety net, so the demo never depends on the model behaving."""
from __future__ import annotations
import re, time
from .normalize import normalize
from .schema import _STOP, _toks, _wmatch

CLEAN_PROMPT = """You clean up a speech-to-text transcript of a person reporting an incident.
Rewrite it as clear, fluent text.
- Remove filler sounds (uh, um), stutters, repeated words and false starts.
- If the speaker corrects themselves ("at 6, no sorry, 6:15"), keep only the corrected version.
- Keep every fact exactly: places, names, numbers, times, what happened. Keep words like not, nobody, nothing.
- Do NOT add, guess or explain anything. Do NOT answer questions in it. Output only the cleaned text, nothing else.

Raw: Uh, there was a s-s-small fire in the in the kitchen, you know, at 6, no sorry, 6:15 PM
Clean: There was a small fire in the kitchen at 6:15 PM

Raw: the the lift is is stuck, hmm, block B, two people inside
Clean: The lift is stuck, block B, two people inside

Raw: no one was hurt, nothing serious
Clean: No one was hurt, nothing serious"""

_NEG = ("not", "nobody", "nothing", "never", "none", "without", "cannot")


def accept_clean(raw: str, cleaned: str) -> bool:
    """True only if `cleaned` is the same report with noise removed: no new words, no dropped facts, no lost negation."""
    if not cleaned.strip() or len(cleaned) > len(raw) * 1.15 + 8:
        return False
    raw_w, clean_w = set(_toks(raw)), set(_toks(cleaned))
    if any(w not in _STOP and not _wmatch(w, raw_w) for w in _toks(cleaned)):          # nothing may be invented
        return False
    if any(n in raw_w and n not in clean_w for n in _NEG):                              # "not" must survive
        return False
    ref = [w for w in _toks(normalize(raw).text) if w not in _STOP]                     # nothing important may be dropped
    return not ref or sum(_wmatch(w, clean_w) for w in ref) / len(ref) >= 0.8


class LLMCleaner:
    def __init__(self, llm):
        self.llm, self.last_how, self.last_s = llm, "none", 0.0

    def clean(self, raw: str):
        """Returns (text, how) with how in 'llm', 'rules' (guard or model failed), 'none' (nothing to clean)."""
        fallback = normalize(raw).text
        if len(raw.split()) <= 3:                       # "yes", "847 PM": nothing to clean
            return fallback, "none"
        t0 = time.perf_counter()
        try:
            out = self.llm.plain(CLEAN_PROMPT, "Raw: " + raw + "\nClean:")
            out = re.sub(r"^(?:clean:\s*)", "", out.strip(), flags=re.I).strip().strip('"')
            self.last_s = time.perf_counter() - t0
            if accept_clean(raw, out):
                return out, "llm"
        except Exception:
            self.last_s = time.perf_counter() - t0
        return fallback, "rules"

"""The cleaning layer between Whisper and the LLM.
Whisper gives faithful text of how the person spoke, including disfluencies. The LLM extracts better from clean text, so this layer
removes what carries no meaning and keeps everything that does. Pure rules, no model: fast, predictable, easy to port to TypeScript.

Handles: interjections (uh, um, hmm, you know,), sound repetition (s-s-small, s s small), word and phrase repetition
(the the fire, 6:15 6:15 PM), prolongation (fiiiire, sssmall), and spoken time self-corrections (at 6, no sorry, 6:15).
Does not touch meaning words: no, not, numbers, place letters (block B), room names."""
from __future__ import annotations
import re
from dataclasses import dataclass, field

_FILLER = re.compile(r"\b(?:u+h+m*|u+m+|e+r+m*|h+m+|a+h+|m{2,})\b[,.]?\s*", re.I)
_DISCOURSE = re.compile(r"(?:(?<=[,.;])|^)\s*(?:you know|basically|well|like|right)\s*,\s*", re.I)   # only when comma-delimited
_PROLONG = re.compile(r"([A-Za-z])\1{2,}")                           # fiiiire -> fire (English has no triple letters)
_HYPH = re.compile(r"\b((?:[A-Za-z]{1,3}-){1,4})([A-Za-z]{2,})\b")  # s-s-small, b-b-burst
_PROTECT = {"a", "i", "an", "in", "is", "it", "at", "on", "of", "to", "or", "as", "be", "by", "do", "go", "he", "if", "me", "my",
            "no", "so", "up", "us", "we", "am", "ah"}
_PLACE_BEFORE_LETTER = {"block", "lab", "room", "floor", "wing", "gate", "tower", "building", "platform", "bay", "parking", "zone",
                        "sector", "line", "level", "unit", "bus", "track"}
_REPEAT_COMMA = re.compile(r"\b([\w':]+)[,.]?\s+(?=\1\b)", re.I)     # "the, the fire" -> "the fire"
_REPEAT_PHRASE = re.compile(r"\b((?:[\w:']+\s+){0,3}[\w:']+)(\s+\1\b)+", re.I)
_T = r"(?:\d{1,2}[:.]\d{2}\s*(?:a\.?m\.?|p\.?m\.?)?|\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?)|\d{3,4}(?:\s*(?:a\.?m\.?|p\.?m\.?))?)"
_TIME_FIX = re.compile(r"\b\d{1,2}(?:[:.]\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?\s*[,.]?\s+"
                       r"(?:(?:no[, ]+)?(?:wait|sorry|i mean|rather)|sorry[, ]+no)(?:[, ]+(?:no|wait|sorry|i mean))*[, .]+(" + _T + r")", re.I)


@dataclass
class NormResult:
    text: str
    changes: list = field(default_factory=list)      # which kinds of cleaning fired, for display / debugging


def _fix_hyphen(m):
    frags = [f for f in m.group(1).split("-") if f]
    word = m.group(2)
    return word if all(word.lower().startswith(f.lower()) for f in frags) else m.group(0)


def _drop_fragments(text: str) -> str:
    """Spaced sound repetition: 's s small', 'sm small'. Fragments of 1-2 letters only, and never a place letter ('block B')."""
    toks, out = text.split(), []
    for i, t in enumerate(toks):
        core = re.sub(r"\W", "", t)
        if i + 1 < len(toks) and 1 <= len(core) <= 2 and core.isalpha() and core.lower() not in _PROTECT:
            nxt = re.sub(r"\W", "", toks[i + 1])
            prev = re.sub(r"\W", "", toks[i - 1]).lower() if i else ""
            upper_mid = len(core) == 1 and core.isupper() and i > 0 and not re.search(r"[.!?]$", toks[i - 1])
            if len(nxt) >= len(core) and nxt.lower().startswith(core.lower()) and prev not in _PLACE_BEFORE_LETTER and not upper_mid:
                continue
        out.append(t)
    return " ".join(out)


def normalize(text: str) -> NormResult:
    ch, t = [], text
    def step(name, new):
        nonlocal t
        if new != t:
            ch.append(name); t = new
    step("interjections", _FILLER.sub("", t))
    step("interjections", _DISCOURSE.sub(" ", t))
    step("prolongation", _PROLONG.sub(r"\1", t))
    step("sound repetition", _HYPH.sub(_fix_hyphen, t))
    step("sound repetition", _drop_fragments(t))
    for _ in range(3):                                                # repeated words/phrases, until stable
        step("repetition", _REPEAT_COMMA.sub("", t))
        step("repetition", _REPEAT_PHRASE.sub(r"\1", t))
    step("self-correction", _TIME_FIX.sub(lambda m: m.group(1), t))
    step("", re.sub(r"\s+", " ", t).strip(" ,"))
    return NormResult(t, [c for c in dict.fromkeys(ch) if c])

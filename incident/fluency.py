"""Stages D and E, timing side: what the clock says about stuttering. Whisper gives a start and end time for every word.
Long gaps between words = silent pauses / blocks, one word stretched over a long time = prolongation.
Pauses leave no text behind, so removing them is automatic once words are joined; what we keep is a short report
that the review screen can show, and the cleaning layer (normalize.py / AI cleaner) removes repeats and fillers in the text."""
from __future__ import annotations
import re

PAUSE_S = 0.6        # silence between two words longer than this counts as a pause / block
PROLONG_MIN_S = 0.9  # a word must last at least this long ...
PROLONG_PER_CHAR = 0.22   # ... and longer than this many seconds per letter, to count as stretched


def analyze(timings: list) -> dict:
    """timings = [(word, start, end)]. Returns counts and the affected words."""
    pauses, prolonged = [], []
    for i, (w, s, e) in enumerate(timings):
        if i and s - timings[i - 1][2] >= PAUSE_S:
            pauses.append({"after": timings[i - 1][0], "seconds": round(s - timings[i - 1][2], 1)})
        letters = len(re.findall(r"[A-Za-z0-9]", w))
        if letters and (e - s) >= max(PROLONG_MIN_S, PROLONG_PER_CHAR * letters + 0.5):
            prolonged.append({"word": w, "seconds": round(e - s, 1)})
    return {"pauses": pauses, "prolonged": prolonged}


def note(rep: dict, removed: list | None = None) -> str:
    bits = []
    if rep.get("pauses"):
        bits.append(f"{len(rep['pauses'])} long pause(s) skipped")
    if rep.get("prolonged"):
        bits.append("stretched: " + ", ".join(p["word"] for p in rep["prolonged"][:3]))
    if removed:
        bits.append("removed: " + ", ".join(removed[:4]))
    return "; ".join(bits)

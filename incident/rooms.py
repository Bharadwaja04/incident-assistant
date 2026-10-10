"""Room gazetteer: repair ASR slips in the transcript ("conference room too" -> "Conference Room 2")
and snap a location to the exact room name. Port to TypeScript with the same regex idea (+ fuzzywuzzy for the fuzzy part)."""
from __future__ import annotations
import difflib, os, re
from .schema import CFG


def load_rooms(path=None) -> list:
    p = path or os.path.join(CFG, "rooms.txt")
    try:
        return [l.strip() for l in open(p) if l.strip() and not l.lstrip().startswith("#")]
    except OSError:
        return []


ROOMS = load_rooms()
_DIG = {"0": r"(?:0|zero|oh|o)", "1": r"(?:1|one)", "2": r"(?:2|two)", "3": r"(?:3|three)", "4": r"(?:4|four)",
        "5": r"(?:5|five)", "6": r"(?:6|six)", "7": r"(?:7|seven)", "8": r"(?:8|eight)", "9": r"(?:9|nine)"}
_HOMO = {"1": "won", "2": "too|to", "3": "tree", "4": "for|fore"}      # only accepted when the sentence clearly ends or moves on
_GUARD = r"(?=\s*(?:[,.;:!?]|$)|\s+(?:around|at|about|when|and|but|since|this|today|yesterday|is|was|are|were|has|have|with|near|on|in|after|before)\b)"
_cache: dict = {}


def _num_pattern(num: str) -> str:
    p = r"\s*".join(_DIG[c] for c in num)
    if len(num) == 1 and num in _HOMO:
        p = f"(?:{p}|(?:{_HOMO[num]}){_GUARD})"
    return p


def _room_regex(room: str):
    parts = []
    for t in room.split():
        if t.isdigit():
            parts.append(_num_pattern(t))
        elif re.fullmatch(r"[A-Za-z]\d+", t):
            parts.append(re.escape(t[0]) + r"\s*" + _num_pattern(t[1:]))
        else:
            parts.append(re.escape(t))
    return re.compile(r"\b" + r"\s+".join(parts) + r"\b", re.I)


def _rx(room):
    if room not in _cache:
        _cache[room] = _room_regex(room)
    return _cache[room]


def fix_rooms(text: str, rooms=None):
    """Return (new_text, [(heard, room), ...]). Exact/spoken-number matches first, then fuzzy for un-numbered rooms."""
    rooms = ROOMS if rooms is None else rooms
    fixes = []
    for room in rooms:
        def sub(m, room=room):
            if m.group(0) != room:
                fixes.append((m.group(0), room))
            return room
        text = _rx(room).sub(sub, text)
    for room in rooms:                          # fuzzy: "caffeteria", "server rum"
        if any(c.isdigit() for c in room) or len(room) < 5:
            continue
        n = len(room.split())
        ms = list(re.finditer(r"[A-Za-z]+", text))
        for i in range(len(ms) - n, -1, -1):
            a, b = ms[i].start(), ms[i + n - 1].end()
            seg = text[a:b]
            if seg != room and difflib.SequenceMatcher(None, seg.lower(), room.lower()).ratio() >= 0.85:
                text = text[:a] + room + text[b:]
                fixes.append((seg, room))
    return text, fixes


def snap_location(loc: str, rooms=None) -> str:
    rooms = ROOMS if rooms is None else rooms
    best, br = loc, 0.0
    for room in rooms:
        r = difflib.SequenceMatcher(None, loc.lower().strip(), room.lower()).ratio()
        if r > br:
            best, br = room, r
    return best if br >= 0.85 else loc

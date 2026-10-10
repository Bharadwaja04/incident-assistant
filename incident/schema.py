"""Schema, normalisation, time parsing, severity rules, evidence check.
Pure logic with no I/O besides reading the config: easy to port to TypeScript (see HANDOFF.md)."""
from __future__ import annotations
import difflib, json, os, re
from datetime import datetime, timedelta
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "..", "config")
SCHEMA = json.load(open(os.path.join(CFG, "incident_schema.json")))
PROPS = SCHEMA["properties"]
FIELDS = list(PROPS)
REQUIRED = SCHEMA["required"]
ENUMS = {k: v["enum"] for k, v in PROPS.items() if "enum" in v}
LIST_FIELDS = [k for k, v in PROPS.items() if v.get("type") == "array"]
QUESTIONS = SCHEMA["x-questions"]
# literal values: must be backed by words in the transcript. Derived fields (type, severity, summary, follow-up) are exempt.
EVIDENCE_FIELDS = ["location", "persons_involved", "equipment", "witnesses", "immediate_actions"]
ASK_ORDER = ["incident_type", "location", "datetime"]       # description comes from the first utterance
RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}

ALIASES = {"occurred_at": "datetime", "time": "datetime", "date_time": "datetime",
           "people_involved": "persons_involved", "people": "persons_involved", "persons": "persons_involved",
           "action_taken": "immediate_actions", "actions": "immediate_actions", "actions_taken": "immediate_actions",
           "immediate_action": "immediate_actions", "witness": "witnesses"}
TYPE_SYNONYMS = {"spill": "chemical_spill", "chemical": "chemical_spill", "gas_leak": "chemical_spill", "leak": "water_leak",
                 "flood": "water_leak", "outage": "it_outage", "network": "it_outage", "network_outage": "it_outage",
                 "theft": "security", "break_in": "security", "short_circuit": "electrical", "accident": "injury",
                 "nearmiss": "near_miss", "equipment": "equipment_failure", "malfunction": "equipment_failure"}
SEVERITY_SYNONYMS = {"major": "high", "serious": "high", "severe": "high", "significant": "high", "urgent": "high",
                     "minor": "low", "small": "low", "slight": "low", "negligible": "low",
                     "moderate": "medium", "mild": "low", "emergency": "critical", "fatal": "critical",
                     "life_threatening": "critical"}
SEV_KW = [
    ("critical", r"\b(critical|life[- ]threatening|fatal)\b"),
    ("high", r"\b(major|serious|severe|badly|ambulance|massive|significant)\b"),
    ("medium", r"\b(moderate|medium)\b"),
    ("low", r"\b(minor|small|slight|negligible|low)\b"),
]
_NEG_SEV = re.compile(r"\b(nothing|not|no|never|isn'?t|wasn'?t)\s+(?:very\s+|too\s+|that\s+)?(serious|severe|major|critical|bad)\b", re.I)
_NULLS = ("null", "none", "unknown", "n/a", "not specified", "not mentioned", "nil")


# ---- severity ---------------------------------------------------------------------
def severity_from_text(text: str):
    """Explicit severity words decide severity ('nothing serious' -> low). None if the person did not say."""
    if _NEG_SEV.search(text):
        return "low"
    for name, pat in SEV_KW:
        if re.search(pat, text, re.I):
            return name
    return None


_HURT_TEXT = re.compile(r"\b(hurt|injur\w*|bleeding|bled|fractur\w*|burn(?:s|ed|t)? (?:his|her|their|a)\b|fainted|unconscious|crushed|amputat\w*)\b", re.I)
_FLOOR_TEXT = re.compile(r"\b(smoke|smoking|gas leak|gas smell|smell of gas|smells? (?:of )?gas|electric(?:al)? shock|shocked|electrocut\w*)\b", re.I)
_NO_HARM = re.compile(r"\b(no ?one|nobody|no|nothing|not|without)\s+(?:\w+\s+){0,2}(?:hurt|injur\w*|harm\w*)\b", re.I)
_DEFAULT_SEV = {"near_miss": "low", "equipment_failure": "low", "other": "low", "it_outage": "medium", "water_leak": "medium",
                "security": "medium", "electrical": "medium", "chemical_spill": "high", "injury": "high", "fire": "high"}


def severity_floor(fields: dict, text: str):
    """Plan rule: injury, fire, smoke, gas or electric shock means at least High."""
    t = text or ""
    ty = fields.get("incident_type")
    if ty == "fire" or (ty == "injury" and not _NO_HARM.search(t)) or _FLOOR_TEXT.search(t) \
            or (_HURT_TEXT.search(t) and not _NO_HARM.search(t)):
        return "high"
    return None


def derive_severity(fields: dict, text: str, proposed: Optional[str] = None) -> str:
    """When severity was not stated: take the model's rubric answer (or a per-type default), then apply the floor."""
    sev = proposed if proposed in RANK else _DEFAULT_SEV.get(fields.get("incident_type"), "medium")
    fl = severity_floor(fields, text)
    return fl if fl and RANK[sev] < RANK[fl] else sev


# ---- time -------------------------------------------------------------------------
_MERID = re.compile(r"\b(\d{1,2})(?:[:.]([0-5]\d))?\s*(a\.?m\.?|p\.?m\.?)(?![a-z])", re.I)
_COMPACT_M = re.compile(r"\b(\d{1,2})(\d{2})\s*(a\.?m\.?|p\.?m\.?)(?![a-z])", re.I)      # "847 pm", "1030 am"
_COMPACT = re.compile(r"(?<![\d:])(?<!\d\.)(\d{1,2})(\d{2})(?!\d)(?![:.]\d)")                          # "847", "1752"
_COMPACT_CUE = re.compile(r"\b(?:at|around|about|by|since)\s+\d{3,4}\b|\b\d{3,4}\s*(?:a\.?m\.?|p\.?m\.?)(?![a-z])", re.I)
_DOT = re.compile(r"\b(?:at|around|about|by)\s+(\d{1,2})\.([0-5]\d)\b", re.I)
_H24 = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_BARE = re.compile(r"\b(?:at|around|about|by)\s+(\d{1,2})\b(?!\s*(?:people|persons?|workers?))", re.I)
_ANYNUM = re.compile(r"\b(\d{1,2})\b")
_UNITS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
          "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60}
_NUMRE = (r"\d{1,3}|(?:twenty|thirty|forty|fifty|sixty)(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?"
          r"|a couple of|a few|couple of|few|an?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen")
_REL = re.compile(r"\b(" + _NUMRE + r")\s+(minutes?|mins?|hours?|hrs?)\s+ago\b", re.I)
_REL_HALF = re.compile(r"\b(half an hour|half hour|a quarter of an hour|quarter of an hour|an hour and a half)\s+ago\b", re.I)
_HALF_MIN = {"half an hour": 30, "half hour": 30, "a quarter of an hour": 15, "quarter of an hour": 15, "an hour and a half": 90}
_JUST_NOW = re.compile(r"\b(just now|right now)\b", re.I)
_HW = "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
_MW = (r"(?:oh|o)\s+(?:one|two|three|four|five|six|seven|eight|nine)|(?:ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen)"
       r"|(?:twenty|thirty|forty|fifty)(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?")
_WORD_HM = re.compile(r"\b(" + _HW + r")\s+(" + _MW + r")\b", re.I)                       # "eight forty seven"
_HALF_PAST = re.compile(r"\bhalf past (" + _HW + r")\b", re.I)
_QTR_PAST = re.compile(r"\bquarter past (" + _HW + r")\b", re.I)
_QTR_TO = re.compile(r"\bquarter to (" + _HW + r")\b", re.I)
_OCLOCK = re.compile(r"\b(" + _HW + r")\s+o'?\s?clock\b", re.I)
_PM = re.compile(r"(?<![a-z])(?:p\.?m\.?)(?![a-z])|\b(?:evening|afternoon|tonight)\b", re.I)
_AM = re.compile(r"(?<![a-z])(?:a\.?m\.?)(?![a-z])|\bmorning\b", re.I)


def _num(s: str) -> int:
    s = s.lower().strip()
    if s.isdigit():
        return int(s)
    if s in ("a couple of", "couple of"):
        return 2
    if s in ("a few", "few"):
        return 3
    parts = re.split(r"[- ]", s)
    if parts[0] in _TENS:
        return _TENS[parts[0]] + (_UNITS.get(parts[1], 0) if len(parts) > 1 else 0)
    return _UNITS.get(s, 0)


def _hm_iso(h: int, mi: int, t: str, now: datetime) -> Optional[str]:
    """Hour + minute with the AM/PM left open (compact '847' or spoken 'eight forty seven').
    Use pm/am/morning/evening words if there are any, otherwise the latest of the two readings that is not in the future."""
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    day = now.date() - timedelta(days=1) if "yesterday" in t else now.date()
    if 1 <= h <= 12:
        pm, am = _PM.search(t), _AM.search(t)
        if pm and not am:
            h = 12 if h == 12 else h + 12
        elif am and not pm:
            h = 0 if h == 12 else h
        else:
            cands = [h % 12, h % 12 + 12]
            past = [c for c in cands if datetime(day.year, day.month, day.day, c, mi) <= now + timedelta(minutes=5)]
            h = max(past) if past else cands[0]
    return datetime(day.year, day.month, day.day, h, mi).isoformat(timespec="minutes")


def is_compact_time(text: str, bare_ok: bool = False) -> bool:
    """True when the time came from digits without a colon ("847", "650 pm"): worth one "is that right?"."""
    t = text.lower()
    if _H24.search(t) or _MERID.search(t):
        return False
    return bool(_COMPACT_CUE.search(t) or (bare_ok and _COMPACT.search(t)))


def parse_time(text: str, now: datetime, bare_ok: bool = False) -> Optional[str]:
    """Return 'YYYY-MM-DDTHH:MM' or None. bare_ok lets a lone number like '10' or '847' count (used when we just asked for the time).
    Handles: '10 AM', '2:30 PM', '11.45 am', 'at 8.30', '8:30 in the evening', '847', '650 pm', 'eight forty seven',
    'half past six', 'quarter to seven', 'yesterday', '20 minutes ago', 'half an hour ago', 'just now'."""
    t = text.lower()
    m = _REL_HALF.search(t)
    if m:
        return (now - timedelta(minutes=_HALF_MIN[m.group(1)])).replace(second=0, microsecond=0).isoformat(timespec="minutes")
    m = _REL.search(t)
    if m and _num(m.group(1)) > 0:
        n = _num(m.group(1))
        mins = n * 60 if m.group(2).lower().startswith("h") else n
        return (now - timedelta(minutes=mins)).replace(second=0, microsecond=0).isoformat(timespec="minutes")
    if _JUST_NOW.search(t) and (mm := re.search(r"\b([01]?\d|2[0-3])([0-5]\d)\b", t)) and not re.search(r"\d{5}|[:.]", t):
        return _hm_iso(int(mm.group(1)), int(mm.group(2)), t, now)
    if _JUST_NOW.search(t) and not _COMPACT_M.search(t):      # an explicit clock time beats 'right now'
        return now.replace(second=0, microsecond=0).isoformat(timespec="minutes")
    m = _COMPACT_M.search(t)
    if m:
        return _hm_iso(int(m.group(1)), int(m.group(2)), t, now)
    m = _MERID.search(t)
    if not m and re.search(r"\d\s*[ap]\.?m\b\.?(?![a-z])", t):
        return None                        # digits + am/pm that we cannot read (e.g. '15 pm'): ask, don't guess
    if m:
        h, mi, pm = int(m.group(1)), int(m.group(2) or 0), m.group(3).startswith("p")
        if h > 12:
            return None
        h = (12 if pm else 0) if h == 12 else (h + 12 if pm else h)
    else:
        w = _HALF_PAST.search(t)
        if w: return _hm_iso(_num(w.group(1)), 30, t, now)
        w = _QTR_PAST.search(t)
        if w: return _hm_iso(_num(w.group(1)), 15, t, now)
        w = _QTR_TO.search(t)
        if w: return _hm_iso((_num(w.group(1)) - 1) or 12, 45, t, now)
        w = _OCLOCK.search(t)
        if w: return _hm_iso(_num(w.group(1)), 0, t, now)
        w = _WORD_HM.search(t)
        if w and (bare_ok or re.search(r"\b(?:at|around|about|by|since)\s+" + re.escape(w.group(0)), t) or _PM.search(t) or _AM.search(t)):
            return _hm_iso(_num(w.group(1)), _num(re.sub(r"^(?:oh|o)\s+", "", w.group(2))), t, now)
        m = _H24.search(t)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
        else:
            m = _DOT.search(t)
            if m:
                h, mi = int(m.group(1)), int(m.group(2))
            else:
                c = _COMPACT.search(t) if (bare_ok or _COMPACT_CUE.search(t)) else None
                if c:
                    return _hm_iso(int(c.group(1)), int(c.group(2)), t, now)
                m = _BARE.search(t) or (_ANYNUM.search(t) if bare_ok else None)
                if not m:
                    return None
                h, mi = int(m.group(1)), 0
        if h < 12 and re.search(r"\b(evening|afternoon|tonight)\b", t):
            h += 12
        elif 6 <= h < 12 and re.search(r"\bnight\b", t):
            h += 12
    if h > 23:
        return None
    day = now.date() - timedelta(days=1) if "yesterday" in t else now.date()
    return datetime(day.year, day.month, day.day, h, mi).isoformat(timespec="minutes")


# ---- normalisation ----------------------------------------------------------------
def as_list(v) -> list:
    """'Priya and Ravi' / ['Priya', 'Ravi'] / 'Priya' -> clean, de-duplicated list."""
    if isinstance(v, str):
        v = re.split(r",|;|\band\b", v)
    if not isinstance(v, (list, tuple)):
        v = [v]
    out, seen = [], set()
    for x in v:
        x = str(x).strip(" .\"'")
        if x and x.lower() not in _NULLS and x.lower() not in seen:
            out.append(x); seen.add(x.lower())
    return out


def normalise(raw: dict, now: datetime) -> dict:
    """Keep only known keys (aliases accepted), drop empties/nulls, snap enums, make lists, ISO time. Never raises."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for key, v in raw.items():
        k = ALIASES.get(key, key)
        if k not in FIELDS or v is None:
            continue
        if k in LIST_FIELDS:
            v = as_list(v)
            if v:
                out[k] = v
            continue
        if isinstance(v, (list, tuple)):
            v = ", ".join(str(x) for x in v)
        v = str(v).strip()
        if not v or v.lower() in _NULLS:
            continue
        if k in ENUMS:
            v = v.lower().replace(" ", "_").replace("-", "_")
            if k == "severity":
                v = SEVERITY_SYNONYMS.get(v, v)
            else:
                v = TYPE_SYNONYMS.get(v, v)
            if v not in ENUMS[k]:
                if k == "incident_type":
                    v = "other"
                else:
                    continue
        if k == "location":
            v = re.sub(r"\s*,\s*", " ", v).strip()
        if k == "datetime":
            iso = parse_time(v, now) if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", v) else v[:16]
            if not iso:
                continue
            v = iso
        out[k] = v
    return out


def missing(draft: dict) -> list:
    return [f for f in REQUIRED if not draft.get(f)]


# ---- evidence check ---------------------------------------------------------------
_TIME_EVIDENCE = re.compile(r"\d|noon|midnight|o'clock|quarter|half past|\bago\b|yesterday|\bjust now\b|\bearlier\b|\bhalf an hour\b", re.I)
_STOP = {"the", "a", "an", "of", "in", "at", "on", "and", "has", "been", "was", "were", "is", "to", "by", "it"}


def _toks(text: str) -> list:
    return re.findall(r"[a-z0-9]+", text.lower())


def _wmatch(w: str, words) -> bool:
    """Word is in the transcript, or nearly (fuzzy ratio >= 0.85, like token-set ratio in the plan)."""
    return w in words or any(len(w) > 3 and difflib.SequenceMatcher(None, w, x).ratio() >= 0.85 for x in words)


def _coverage(value: str, words) -> float:
    content = [w for w in _toks(value) if w not in _STOP]
    return 1.0 if not content else sum(_wmatch(w, words) for w in content) / len(content)


def ground(fields: dict, text: str, ai: bool = False) -> dict:
    """Model proposes, rules verify: drop any literal value the person's words do not support."""
    out = dict(fields)
    words = set(_toks(text))
    if "datetime" in out and not _TIME_EVIDENCE.search(text):
        del out["datetime"]
    # a fire "department/brigade/extinguisher" is not a fire
    if not ai and out.get("incident_type") == "fire":
        t2 = re.sub(r"fire\s+(department|dept|brigade|station|truck|engines?|fighters?|extinguishers?|exit|drill|alarm|marshal|warden|safety)\b", " ", text.lower())
        if not re.search(r"\b(fire|smoke|flames?|burning|burnt)\b", t2):
            del out["incident_type"]
    sev = None if ai else severity_from_text(text)        # rule mode only: explicit severity words beat the model's guess
    if sev:
        out["severity"] = sev
    # a location must come after a place word, otherwise "a pipe burst" gives location "a pipe"
    if "location" in out and not ai:
        cw = [w for w in _toks(out["location"]) if w not in _STOP]
        if cw:
            pat = (r"\b(?:in|at|near|on|inside|outside|behind|beside|by|under|above|over|next to|opposite|from|within|across|towards?)"
                   r"\s+(?:the\s+|a\s+|an\s+|our\s+|my\s+)?(?:\w+\s+){0,3}?" + re.escape(cw[0]) + r"\b")
            if not re.search(pat, text.lower()):
                del out["location"]
    for k in EVIDENCE_FIELDS + ["follow_up"]:       # follow_up too: a recommendation the person never said is dropped
        v = out.get(k)
        if not v:
            continue
        if k in LIST_FIELDS:
            kept = [x for x in v if _coverage(x, words) >= 0.5]
            if kept:
                out[k] = kept
            else:
                del out[k]
        elif _coverage(v, words) < 0.5:
            del out[k]
    return out


def evidence(fields: dict, text: str) -> dict:
    """For each literal field: status 'ok' / 'unverified' and the character spans of the transcript it came from
    (the review screen highlights them). Derived fields are exempt."""
    toks = [(m.group(0).lower(), m.start(), m.end()) for m in re.finditer(r"[A-Za-z0-9]+", text)]
    words = {t for t, _, _ in toks}
    res = {}
    for k in EVIDENCE_FIELDS:
        v = fields.get(k)
        if not v:
            continue
        spans, worst = [], 1.0
        for x in (v if isinstance(v, list) else [v]):
            content = [w for w in _toks(x) if w not in _STOP]
            worst = min(worst, _coverage(x, words))
            pos = [(s, e) for t, s, e in toks if t in content or
                   any(len(t) > 3 and difflib.SequenceMatcher(None, t, c).ratio() >= 0.85 for c in content)]
            if pos:
                spans.append([min(s for s, _ in pos), max(e for _, e in pos)])
        res[k] = {"status": "ok" if worst >= 0.85 else "unverified", "spans": spans}
    return res

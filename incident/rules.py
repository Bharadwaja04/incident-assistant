"""Rule-based extractor: the baseline AND the offline fallback if the LLM misbehaves."""
from __future__ import annotations
import re
from datetime import datetime
from .schema import SEV_KW, parse_time, severity_from_text

TYPE_KW = [
    ("near_miss", r"\b(near[- ]miss|almost (?:fell|hit|collided|slipped)|narrowly)\b"),
    ("fire", r"\b(fire|smoke|burning|flames?)\b"),
    ("electrical", r"\b(short circuit|sparks?|sparking|electric\w*|power outage|shock)\b"),
    ("injury", r"\b(injur\w*|hurt|fell|fall|bleed\w*|fainted|accident|slipped)\b"),
    ("security", r"\b(theft|stolen|intruder|trespass\w*|unauthori[sz]ed|break-?in|suspicious)\b"),
    ("chemical_spill", r"\b(spill\w*|chemical|gas leak|gas smell|smell of gas|smells? (?:of )?gas)\b"),
    ("it_outage", r"\b(network|wi-?fi|internet|server|system|vpn)\s+(?:is\s+|was\s+)?(?:down|not working|offline)\b|\bno internet\b"),
    ("water_leak", r"\b(leak\w*|flood\w*|burst|water)\b"),
    ("equipment_failure", r"\b(broke\w*|malfunction\w*|not working|failed|failing|failure|stuck|jammed|hardware|device|board|machine|laptop|not responding|crash\w*)\b"),
]
LOC = re.compile(
    r"\b(?:near|in|at|inside|outside|beside|by|on|behind)\s+(?:the\s+)?([A-Za-z0-9][\w\- ]{1,40}?)"
    r"(?=\s+(?:around|at|about|when|while|and|but|since|this|today|yesterday|is|was|are|were|has|have|with)\b|[,.;!?]|$)",
    re.I)
BAD_LOC = re.compile(r"^(\d|morning$|evening$|afternoon$|night$|am$|pm$|yesterday$|today$)", re.I)
ACTION = re.compile(
    r"\b(already|isolated|called|informed|notified|evacuated|contained|cleaned|switched off|shut off|unplugged|"
    r"put (?:it )?out|first aid|given|stopped|working on|ambulance|reported to|extinguish\w*|cleared|opened the)\b", re.I)
PEOPLE = re.compile(
    r"\b(?:a|an|one|two|three|four|five|several|\d+)\s+(?:people|persons?|workers?|employees?|visitors?|"
    r"students?|staff|guards?|technicians?|contractors?)\b", re.I)
NAME = r"([A-Z][a-z]{2,})"
WITNESS = re.compile(NAME + r"\s+(?:saw|witnessed|watched|noticed)\b")
PERSON = re.compile(r"\b(?i:it was|that was|victim was|affected was)\s+" + NAME + r"\b|\b" + NAME + r"\s+(?:slipped|fell|got hurt|was hurt|fainted)\b")
NOT_NAMES = {"Someone", "Somebody", "Anyone", "Everyone", "Nobody", "There", "Then", "This", "That", "The"}
EQUIP = re.compile(r"\b(projector|laptop|printer|lift|elevator|air conditioner|server|router|panel|socket|generator|"
                   r"computer|monitor|cable|battery|machine|pipe|ups|switchboard|extension board)s?\b", re.I)


def first_match(table, text):
    for name, pat in table:
        if re.search(pat, text, re.I):
            return name
    return None


ROOMCODE = re.compile(r"\b(?:location|place|room|venue)\s*(?:is|was|would be|will be|like|:)?\s*(?:the\s+)?([A-Za-z]{1,3}[- ]?\d{2,4})\b|\b([A-Za-z]{1,2}[- ]?\d{3,4})\b", re.I)


def extract_location(text):
    m = ROOMCODE.search(text)
    if m and not re.fullmatch(r"\d+", (m.group(1) or m.group(2))):
        code = re.sub(r"[- ]", "", m.group(1) or m.group(2)).upper()
        if not re.fullmatch(r"(?:AM|PM)\d+", code):
            return code
    return _extract_location(text)


def _extract_location(text):
    for m in LOC.finditer(text):
        cand = m.group(1).strip()
        if not BAD_LOC.match(cand):
            return cand
    return None


def extract_rules(text: str, now: datetime) -> dict:
    out = {}
    t = first_match(TYPE_KW, text)
    if t: out["incident_type"] = t
    loc = extract_location(text)
    if loc: out["location"] = loc
    ts = parse_time(text, now)
    if ts: out["datetime"] = ts
    s = severity_from_text(text)
    if s: out["severity"] = s
    people = [m.group(0) for m in PEOPLE.finditer(text)]
    for m in PERSON.finditer(text):
        n = m.group(1) or m.group(2)
        if n not in NOT_NAMES:
            people.append(n)
    if people: out["persons_involved"] = people
    w = [m.group(1) for m in WITNESS.finditer(text)]
    if w: out["witnesses"] = w
    eq = sorted({m.group(1).lower() for m in EQUIP.finditer(text)})
    if eq: out["equipment"] = eq
    clauses = [c.strip() for c in re.split(r"[.!?;,]| and ", text) if c.strip()]
    acts = [c for c in clauses if ACTION.search(c)]
    if acts: out["immediate_actions"] = "; ".join(acts)
    out["description"] = text.strip()
    return out

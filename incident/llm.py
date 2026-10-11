"""Extractors: take (utterance, current report) -> new fields. All return the same Extraction.

Two ways to talk to the model, same result:
  mode="json"  : ask for one JSON object (works on any runtime; used for the laptop gates).
  mode="tools" : send config/tools.json and read the tool call (what llama-server --jinja gives on the board).
In both, the code validates and the model never decides what is missing."""
from __future__ import annotations
import json, os, sys, time, urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from .rooms import ROOMS
from .schema import CFG, ground, normalise, parse_time
from .rules import extract_rules

import re
_wtoks = lambda t: re.findall(r"[a-z0-9]+", t.lower())
def _iso_from_model(out, now):
    """The model's time answer, reshaped to YYYY-MM-DDTHH:MM. Accepts '2026-10-10T20:35', '2026-10-10 20:35' or just '20:35' (today)."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})[T ](\d{1,2}):(\d{2})", out)
    if m:
        return f"{m.group(1)}T{int(m.group(2)):02d}:{m.group(3)}"
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", out)
    if m and int(m.group(1)) < 24:
        return f"{now.strftime('%Y-%m-%d')}T{int(m.group(1)):02d}:{m.group(2)}"
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*([ap])\.?m\.?\s*", out, re.I)       # '8:35 PM': the model's answer, only reshaped
    if m and 1 <= int(m.group(1)) <= 12:
        h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
        return f"{now.strftime('%Y-%m-%d')}T{h:02d}:{m.group(2)}"
    return None


DEBUG = lambda: bool(os.environ.get("INCIDENT_DEBUG"))
JSON_TAIL = "\n\nOutput: reply with ONE JSON object and nothing else."
TOOL_TAIL = ("\n\nOutput: call exactly one tool. create_incident_report when incident_type, location, datetime and description are known; "
             "request_missing_info otherwise; update_report_field only when the person is correcting the report. "
             "The tool arguments use the same fields and rules as above.")


@dataclass
class Extraction:
    fields: dict
    valid: bool = True          # model produced parseable output
    attempts: int = 1
    seconds: float = 0.0
    completion_tokens: int = 0
    raw: str = ""
    error: str = ""
    tool: str = ""              # tool name in tools mode
    type_reason: Optional[str] = None
    vague_location: Optional[str] = None  # a place the model judged too general ('my room')
    trace: Optional[dict] = None         # what the model answered to each focused question (for the 'AI check' line)
    sev_factors: Optional[list] = None   # the reasons the model gave for its severity judgement
    missing: Optional[list] = None   # mandatory fields the MODEL says are still missing (tools mode); None = model did not say

    @property
    def tokens_per_s(self):
        return self.completion_tokens / self.seconds if self.seconds and self.completion_tokens else 0.0


class RuleExtractor:
    name = "rules"
    def extract(self, text: str, draft: dict, now: datetime, expected: Optional[str] = None, context: str = "") -> Extraction:
        t0 = time.perf_counter()
        f = normalise(extract_rules(text, now), now)
        return Extraction(f, seconds=time.perf_counter() - t0)


def _first_json(text):
    """The first complete {...} object in a text (a model may write the same call twice or add a stray tag after it)."""
    a = text.find("{")
    if a < 0: raise ValueError("no JSON object found")
    depth = 0; in_str = False; esc = False
    for i in range(a, len(text)):
        c = text[i]
        if in_str:
            esc = (c == "\\" and not esc)
            if c == '"' and not esc: in_str = False
            continue
        if c == '"': in_str = True
        elif c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0: return json.loads(text[a:i + 1])
    raise ValueError("unfinished JSON object")


def _json_from(text: str):
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON object found")
    return json.loads(text[a:b + 1])


_STOP = set("a an the of in on at to and or is was were are be been has have had with by for from that this it its as not no one some people person there their they he she we i you me my our his her".split())

def _supported(phrase: str, text: str, need: float = 0.5) -> bool:
    """A stated reason must rest on the person's own words: at least half of its meaningful words must appear in what was said."""
    stem = lambda w: w[:5]
    have = {stem(w) for w in re.findall(r"[a-z0-9]+", text.lower())}
    ws = [w for w in re.findall(r"[a-z0-9]+", phrase.lower()) if w not in _STOP and len(w) > 2]
    return bool(ws) and sum(stem(w) in have for w in ws) / len(ws) >= need


def fields_from_tool_call(name: str, args) -> dict:
    """Map a tool call to plain fields. The app, not the model, decides what happens next."""
    if isinstance(args, str):
        args = json.loads(args)
    if name == "request_missing_info":
        return dict(args.get("partial_report") or {})
    if name == "update_report_field":
        return {args.get("field", ""): args.get("value")}
    if name == "submit_report":
        return {}
    return dict(args)


class OpenAICompatExtractor:
    """Talks to any OpenAI-compatible server: Ollama (http://localhost:11434/v1), llama-server on the board
    (http://127.0.0.1:8080/v1), LM Studio. The system prompt is identical on every call so llama-server's
    prompt cache is reused; the clock goes in the user message."""
    def __init__(self, base_url="http://localhost:11434/v1", model="qwen3:1.7b", max_tokens=300, timeout=60, mode="json"):
        self.base, self.model, self.max_tokens, self.timeout, self.mode = base_url.rstrip("/"), model, max_tokens, timeout, mode
        self.name = f"llm:{model}" + ("+tools" if mode == "tools" else "")
        pf = "system_prompt_extract.txt" if mode == "tools" else "system_prompt.txt"      # tools mode = extraction only, no generation
        self.prompt = open(os.path.join(CFG, pf)).read().replace("{rooms}", ", ".join(ROOMS))
        self.prompt += TOOL_TAIL if mode == "tools" else JSON_TAIL
        if "qwen3" in model:
            self.prompt = "/no_think\n" + self.prompt
        self.tools = json.load(open(os.path.join(CFG, "tools.json"))) if mode == "tools" else None

    def _chat(self, messages):
        body = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": self.max_tokens,
                "stream": False, "reasoning_effort": "none"}   # "none" turns off hidden thinking (Qwen3); other servers ignore it
        if self.tools:
            body["tools"] = self.tools
        req = urllib.request.Request(self.base + "/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"})
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            d = json.load(r)
        msg = d["choices"][0]["message"]
        n = d.get("usage", {}).get("completion_tokens", 0)
        if DEBUG():
            dt = time.perf_counter() - t0
            print(f"      [llm call] {dt:.1f}s, {n} tokens, {n/dt if dt else 0:.0f} tok/s, finish={d['choices'][0].get('finish_reason')}\n"
                  f"      raw: {(msg.get('content') or json.dumps(msg.get('tool_calls')))[:300]!r}", file=sys.stderr, flush=True)
        return msg, n

    def plain(self, system: str, user: str, max_tokens: int = 160) -> str:
        """One plain text-in / text-out call (no tools, no JSON). Used by the cleaning layer."""
        body = {"model": self.model, "messages": [{"role": "system", "content": ("/no_think\n" if "qwen3" in self.model else "") + system},
                                                    {"role": "user", "content": user}],
                "temperature": 0, "max_tokens": max_tokens, "stream": False, "reasoning_effort": "none"}
        req = urllib.request.Request(self.base + "/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"})
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            d = json.load(r)
        out = (d["choices"][0]["message"].get("content") or "").strip()
        if DEBUG():
            print(f"      [clean call] {time.perf_counter()-t0:.1f}s raw: {out[:200]!r}", file=sys.stderr, flush=True)
        return out

    OPTIONAL = ("persons_involved", "witnesses", "equipment", "immediate_actions", "follow_up")
    DETAILS_PROMPT = (
        "Read an incident description and pick out the optional details that are EXPLICITLY said. Reply with one JSON object and nothing else. "
        "Keys (leave a key out if it was not said): "
        "persons_involved (PEOPLE only, hurt, affected or involved, e.g. [\"two operators\"]; a machine, vehicle or object is never a person, put it under equipment); "
        "witnesses (people who saw or noticed it, by name or role, e.g. [\"Ravi\"]); "
        "equipment (machines or tools, short names, e.g. [\"boiler\", \"gas line valve\"]); "
        "immediate_actions (things people ALREADY DID in response, in the speaker's words, e.g. \"Switched off the boiler, evacuated the area, called the safety officer\"); "
        "follow_up (what the speaker says STILL NEEDS to happen, in their words, e.g. \"Valve to be checked by an engineer before restart\"). "
        "Something that needs to happen is follow_up, not an immediate action. Use only the speaker's own words. Never invent or add advice.")

    def details(self, text: str) -> dict:
        """Second, small AI pass. A 1.7B model often skips the optional fields in the big pass, so ask for just those."""
        try:
            d = _json_from(self.plain(self.DETAILS_PROMPT, text, max_tokens=220))
            out = {}
            for k in self.OPTIONAL:
                v = d.get(k)
                if isinstance(v, list) and k not in ("persons_involved", "witnesses", "equipment"):
                    v = ", ".join(map(str, v))
                if v:
                    out[k] = v
            return out
        except Exception:
            return {}

    FOCUS = {}
    TYPE_PROMPT = (
        "Decide which ONE category best fits the incident, using only what the person said. Reply with ONE JSON object only: "
        '{"type": one of water_leak, fire, electrical, injury, security, equipment_failure, chemical_spill, near_miss, it_outage, other, '
        '"reason": one short sentence saying which words in the description led you to this}. '
        "What each category means: water_leak = water where it should not be; fire = fire, smoke, burning; electrical = power, wiring, shock, sparks; "
        "injury = a person hurt or ill; security = theft, break-in, unauthorised entry, threats; "
        "equipment_failure = any machine, vehicle, lift, motor, crane, conveyor, building structure or tool that broke, stuck, fell, collapsed or stopped working; "
        "chemical_spill = chemical, oil or fuel spill, gas smell or leak; near_miss = something that almost caused harm but did not; "
        "it_outage = ONLY computers, network, internet, servers or software not working (never use it for machines, vehicles or buildings); "
        "other = only if none of the above fit. Pick the category for the main thing that went wrong.")

    LOCATION_PROMPT = (
        "Find WHERE the incident happened, using only what the person said, across the whole conversation. Later details add to or correct earlier ones: give the most complete place the person has described, joining the parts in their own words (a site plus a block plus a floor), and if they correct themselves use the corrected place. Reply with ONE JSON object only: "
        '{"place": the place in the speaker\'s own words, or null if no place is said, "specific": true or false, "reason": one short sentence}. '
        'Set "specific" to false when the place is too general for someone to go and find it (for example just "my room", "the building", "outside", "here", "home", '
        '"the office"). Set it to true when it names something findable such as a room number, a block, a floor, a named room, an area of a site, or a named place together with its city.')
    TIME_PROMPT = (
        "Find WHEN the incident happened, using only what the person said, by calling exactly ONE function. If the person corrects themselves ('it was 8, no, 10 p.m.'), use the corrected time, the last one said. "
        "Never work out dates or subtract anything yourself, the functions do that. Copy numbers exactly as spoken and do NOT convert to 24-hour. "
        "at_clock_time: a time of day and/or a day was said (10:35 p.m. = hour 10, minute 35, period pm; half past six in the morning = hour 6, minute 30, period am; noon = hour 12, minute 0, period pm; "
        "midnight = hour 12, minute 0, period am; 14:05 = hour 14, minute 5). If a day was said with it, add days_back: 'yesterday at 8:39 pm' = hour 8, minute 39, period pm, days_back 1; "
        "'yesterday morning at 9' = hour 9, minute 0, period am, days_back 1; 'two days ago at 3 pm' = hour 3, minute 0, period pm, days_back 2. "
        "time_ago: only how long ago it was (two hours ago = 120 minutes, ten minutes ago = 10, half an hour ago = 30, an hour and a half ago = 90). "
        "For a day with no time of day, call at_clock_time with only days_back ('yesterday' = days_back 1, 'the day before yesterday' = 2, 'three days ago' = 3). "
        "just_now: it is happening right now or just happened. no_time_said: the person gave no time at all.")
    TIME_SHIFT_PROMPT = (
        "The message shows a time that is ALREADY in the report. When the person moves THAT time with words like before, earlier, later, after or push back "
        "('two hours before', 'an hour later', 'half an hour earlier'), call move_report_time with the minutes and the direction. "
        "Never use time_ago for these; time_ago is only for the word 'ago' counted from now.")
    @staticmethod
    def _fn(name, desc, props, req):
        return {"type": "function", "function": {"name": name, "description": desc, "parameters": {"type": "object", "properties": props, "required": req}}}
    TIME_TOOL = [
        _fn.__func__("at_clock_time", "A time of day and/or a day was said: 'at 9', 'morning at 9', 'noon', 'yesterday at 8:39 pm', or only 'yesterday'.", {
            "hour": {"type": "number", "description": "Hour exactly as said, 1 to 12 (or 0 to 23 if said on a 24-hour clock). Leave out if only a day was said."},
            "minute": {"type": "number", "description": "Minutes exactly as said, 0 if none. Leave out if only a day was said."},
            "period": {"type": "string", "enum": ["am", "pm"], "description": "am or pm, only if the person said it."},
            "days_back": {"type": "number", "description": "Only if a day was said: yesterday = 1, the day before yesterday = 2, three days ago = 3. Leave out for today."}}, []),
        _fn.__func__("time_ago", "Only how long ago it happened, counting from now.", {
            "minutes": {"type": "number", "description": "Minutes ago: two hours = 120, half an hour = 30, ten minutes = 10."}}, ["minutes"]),
        _fn.__func__("just_now", "It is happening right now or just happened.", {"confirm": {"type": "boolean", "description": "Always true."}}, ["confirm"]),
        _fn.__func__("no_time_said", "The person gave no time at all.", {"note": {"type": "string", "description": "Two or three words, e.g. 'no time'."}}, ["note"]),
        _fn.__func__("move_report_time", "Move the time that is ALREADY IN THE REPORT earlier or later.", {
            "minutes": {"type": "number", "description": "How many minutes: two hours = 120, half an hour = 30."},
            "direction": {"type": "string", "enum": ["earlier", "later"], "description": "earlier for before/earlier, later for later/after."}}, ["minutes", "direction"]),
    ]

    @staticmethod
    def time_args(name, a):
        """What the model chose -> the inputs of resolve_time (plain mapping, no judgement)."""
        a = a or {}
        if name == "at_clock_time": return {"hour": a.get("hour"), "minute": a.get("minute"), "period": a.get("period"), "days_ago": a.get("days_back")}
        if name == "time_ago": return {"minutes_ago": a.get("minutes")}
        if name == "days_ago_only": return {"days_ago": a.get("days")}
        if name == "just_now": return {"just_now": True}
        if name == "no_time_said": return {}
        if name == "move_report_time":
            return {"minutes_before": a.get("minutes")} if str(a.get("direction", "")).lower().startswith("earl") else {"minutes_after": a.get("minutes")}
        return {k: a.get(k) for k in ("minutes_ago", "hour", "minute", "period", "clock", "day", "just_now", "minutes_before", "minutes_after", "days_ago")}   # an older-style call

    @classmethod
    def time_tool_plain(cls):
        """The tool without the 'move the time already in the report' inputs, offered only when the report has no time yet."""
        import copy
        t = copy.deepcopy(cls.TIME_TOOL)
        t = [x for x in t if x["function"]["name"] != "move_report_time"]
        return t

    @staticmethod
    def resolve_time(now: datetime, minutes_ago=None, hour=None, minute=None, period=None, day=None, just_now=None, clock=None, minutes_before=None, minutes_after=None, reported=None, days_ago=None):
        """The function the model calls. Plain date arithmetic, the one thing a small language model cannot do reliably."""
        base = now.replace(second=0, microsecond=0)
        try:
            num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v < 60 * 24 * 14
            if reported and (num(minutes_before) or num(minutes_after)):   # moving the time already in the report: plain subtraction or addition
                r = datetime.fromisoformat(str(reported)[:16])
                return (r - timedelta(minutes=float(minutes_before)) if num(minutes_before) else r + timedelta(minutes=float(minutes_after))).isoformat(timespec="minutes")
            if isinstance(minutes_ago, (int, float)) and not isinstance(minutes_ago, bool) and 0 < minutes_ago < 60 * 24 * 14:
                return (base - timedelta(minutes=float(minutes_ago))).isoformat(timespec="minutes")
            if hour is None and clock:                                   # an "HH:MM" string is still accepted
                m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", str(clock))
                if m: hour, minute = int(m.group(1)), int(m.group(2))
            if isinstance(hour, (int, float)) and not isinstance(hour, bool) and 0 <= hour < 24:
                h, mi = int(hour), int(minute or 0)
                if not 0 <= mi < 60: return None
                per = str(period or "").lower().replace(".", "")[:2]
                if per in ("am", "pm") and 1 <= h <= 12:
                    cands = [h % 12 + (12 if per == "pm" else 0)]       # the code, not the model, does the 12-hour to 24-hour conversion
                elif 1 <= h <= 12:
                    cands = [h % 12, h % 12 + 12]                          # no am/pm said: take the most recent one that has already happened
                else:
                    cands = [h]
                if num(days_ago):                                          # a clock time on a day N days back
                    d = base - timedelta(days=int(days_ago))
                    return d.replace(hour=max(cands), minute=mi).isoformat(timespec="minutes")
                times = [base.replace(hour=c, minute=mi) for c in cands]
                past = [t for t in times if t <= base + timedelta(minutes=5)]
                t = max(past) if past else max(times) - timedelta(days=1)
                if str(day).lower() == "yesterday" and t.date() == base.date():
                    t -= timedelta(days=1)
                return t.isoformat(timespec="minutes")
            if num(days_ago):                                              # "two days ago" with no clock time: same time of day, N days back
                return (base - timedelta(days=int(days_ago))).isoformat(timespec="minutes")
            if just_now is True:
                return base.isoformat(timespec="minutes")
        except Exception:
            pass
        return None

    SEVERITY_PROMPT = (
        "You judge how severe an incident is, like an experienced safety officer. Weigh these factors TOGETHER; no single factor decides, and you interpret them, "
        "you do not count words: (1) harm to people: anyone hurt, dizzy, trapped or at risk; (2) the kind of hazard: fire, smoke, gas, chemical, electric shock, "
        "explosion, falling or moving machinery; (3) spread and exposure: how many people, contained or spreading; (4) whether the danger is still ongoing or already controlled; "
        "(5) impact: production stopped, damage, safety systems affected; (6) how seriously the speaker themself treats it. "
        "Severity levels: low (minor, nobody at risk), medium (a contained problem to fix today), high (people hurt or at real risk), critical (life-threatening, a collapse, explosion or very large and still happening). "
        "Use ONLY facts the person actually said: never assume anyone is hurt, trapped or in danger unless they said so, and say 'nobody reported hurt' when that is the case. "
        'Reply with ONE JSON object only: {"factors": [1 to 4 short phrases, each stating a fact from the description that drove your judgement], "severity": "low|medium|high|critical"}.')

    def judge_severity(self, context: str, now: datetime):
        """The model judges severity by interpreting factors (no keyword rules). Returns (severity, factors) or (None, [])."""
        try:
            d = _json_from(self.plain(self.SEVERITY_PROMPT, "Incident description: " + context, max_tokens=120))
            sev = normalise({"severity": str(d.get("severity", "")).lower().strip()}, now).get("severity")
            fac = [str(x).strip() for x in (d.get("factors") or []) if str(x).strip()][:4]
            fac = [x for x in fac if _supported(x, context)]          # keep only reasons that rest on words the person actually said
            return sev, fac
        except Exception:
            return None, []

    def focus_location(self, context: str):
        """(place, specific): the model finds the place and judges whether it is findable. Returns (None, True) if no place is said."""
        self.trace = getattr(self, 'trace', {})
        try:
            raw = self.plain(self.LOCATION_PROMPT, "Incident description: " + context, max_tokens=60)
            d = _json_from(raw)
        except Exception:
            return None, True
        self.trace["location"] = f"{d.get('place')} (specific={d.get('specific')}: {d.get('reason')})"
        place = d.get("place")
        place = str(place).strip().strip('"') if place else None
        if not place or place.lower() in ("null", "none"):
            return None, True
        return place, d.get("specific") is not False

    def focus_time(self, context: str, now: datetime, reported=None):
        """Returns (iso_or_None, understood). The model calls resolve_time with what was said; the function does the arithmetic."""
        self.trace = getattr(self, 'trace', {})
        body = {"model": self.model, "temperature": 0, "max_tokens": 80, "stream": False, "reasoning_effort": "none",
                "messages": [{"role": "system", "content": ("/no_think\n" if "qwen3" in self.model else "") + (self.TIME_SHIFT_PROMPT + " " if reported else "") + self.TIME_PROMPT},
                             {"role": "user", "content": (f"Time already in the report: {str(reported).replace('T', ' ')[:16]}\n" if reported else "") + "Incident description: " + context}],
                "tools": self.TIME_TOOL if reported else self.time_tool_plain(), "tool_choice": "required"}
        try:
            req = urllib.request.Request(self.base + "/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                msg = json.load(r)["choices"][0]["message"]
        except Exception as e:
            self.trace["time"] = "time call failed: " + str(e)[:100]
            return None, False
        name, args = None, None
        for tc in msg.get("tool_calls") or []:
            name = tc.get("function", {}).get("name"); args = tc["function"].get("arguments") or {}
            break
        if name is None:                                       # some servers answer in text: accept a JSON object there too
            try:
                obj = _first_json(msg.get("content") or "")                      # the model wrote the call as text: take the first complete one
                if isinstance(obj, dict) and obj.get("name") and "arguments" in obj: name, args = obj["name"], obj["arguments"]
                else: name, args = "resolve_time", obj
            except Exception:
                self.trace["time"] = "no function was called; the model said: " + str(msg.get("content") or "")[:100]
                return None, False
        if isinstance(args, str):
            try: args = json.loads(args)
            except Exception: return None, False
        args = self.time_args(name, args if isinstance(args, dict) else {})
        iso = self.resolve_time(now, reported=reported, **args)
        self.trace["time"] = name + "(" + ", ".join(f"{k}={v}" for k, v in args.items() if v not in (None, "")) + ")" + (f" → {iso}" if iso else "")
        return iso, True

    def focus_type(self, context: str):
        """(type, reason): the model picks the category and says why."""
        self.trace = getattr(self, 'trace', {})
        try:
            d = _json_from(self.plain(self.TYPE_PROMPT, "Incident description: " + context, max_tokens=90))
        except Exception:
            return None, None
        t = normalise({"incident_type": str(d.get("type", "")).lower().replace(" ", "_")}, datetime.now()).get("incident_type")
        reason = str(d.get("reason") or "").strip()
        what = str(d.get("what_happened") or "").strip()
        self.trace["incident_type"] = f"{d.get('type')} ({reason})"
        note = what if _supported(what, context) else reason if _supported(reason, context) else ""
        return t, note

    def focus(self, field: str, context: str, now: datetime) -> Optional[str]:
        """One tiny, single-question AI call for a mandatory field the big pass missed. Easy for a small model, and it reads
        everything said so far, not just the last sentence."""
        self.trace = getattr(self, 'trace', {})
        try:
            out = self.plain(self.FOCUS[field] + " Reply with nothing else.",
                             f"Now: {now.strftime('%Y-%m-%d %H:%M')}\nIncident description: {context}", max_tokens=40)
            out = out.strip().strip('"').strip("'").splitlines()[0].strip().rstrip(".") if out.strip() else ""
        except Exception:
            return None
        self.trace[field] = out
        if not out or out.upper().startswith("NONE"):
            return None
        if field == "datetime":
            return _iso_from_model(out, now)                  # the model's own answer, only reshaped into one format
        if field == "incident_type":
            out = out.lower().replace(" ", "_")
            return normalise({field: out}, now).get(field)
        return normalise({field: out}, now).get(field)

    def extract(self, text: str, draft: dict, now: datetime, expected: Optional[str] = None, context: str = "") -> Extraction:
        user = (f"Now: {now.strftime('%Y-%m-%d %H:%M')}\nCurrent report: {json.dumps(draft)}\n"
                + (f"(We just asked about: {expected})\n" if expected else "") + f"New utterance: {text}")
        msgs = [{"role": "system", "content": self.prompt}, {"role": "user", "content": user}]
        t0, toks, raw, err = time.perf_counter(), 0, "", ""
        self.trace = {}
        for attempt in (1, 2):
            try:
                msg, n = self._chat(msgs)
                toks += n
                tool, ai_missing = "", None
                calls = msg.get("tool_calls") or []
                if calls:                                    # tools mode (or a model that used a tool anyway)
                    tool = calls[0]["function"]["name"]
                    raw = json.dumps(calls[0]["function"])
                    args = calls[0]["function"].get("arguments", {})
                    args = json.loads(args) if isinstance(args, str) else (args or {})
                    fields = fields_from_tool_call(tool, args)
                    if tool == "create_incident_report":
                        ai_missing = []
                    elif tool == "request_missing_info":
                        ai_missing = [m for m in (args.get("missing_fields") or []) if m in ("incident_type", "location", "datetime", "description")]
                else:                                        # JSON mode, or a tools-mode model that answered in text
                    raw = msg.get("content") or ""
                    fields = _json_from(raw)
                f = ground(normalise(fields, now), text, ai=True)      # AI mode: only 'is it in what was said' checks, no keyword rules
                if self.mode == "tools" and f.get("description"):         # extraction only: the description must be the person's own words
                    dw, tw = set(_wtoks(f["description"])), set(_wtoks(text))
                    if dw and len(dw & tw) / len(dw) < 0.8:
                        f["description"] = text.strip()
                if len(text.split()) >= 10 and not all(f.get(k) for k in self.OPTIONAL):
                    extra = ground(normalise(self.details(text), now), text, ai=True)      # optional details were skipped: ask again, just for them
                    for k in self.OPTIONAL:
                        if k in extra and not f.get(k):          # never overwrite what the main pass already found
                            f[k] = extra[k]
                if True:
                    ctx = context or text
                    type_reason = None
                    weak = f.get("incident_type") == "other"                  # "other" is a weak answer: get a second opinion
                    if weak or not (f.get("incident_type") or draft.get("incident_type")):
                        v, type_reason = self.focus_type(ctx)
                        if v and not (weak and v == "other"):
                            f["incident_type"] = v
                    vague = None
                    # Time and place are read from what was just said, so a correction ("it was 10 p.m., not 8:35") replaces the old value.
                    # Only if the report still lacks them is everything said so far searched.
                    scopes = list(dict.fromkeys([text, ctx]))
                    for scope in scopes:
                        if scope is text or not (f.get("location") or draft.get("location") or vague):
                            place, specific = self.focus_location(ctx)       # the place is read from everything said, so a follow-up adds to it
                            if place and not specific:
                                vague = place
                                f.pop("location", None)                       # too general to store: ask for something narrower
                            elif place:
                                got = ground({"location": place}, ctx, ai=True)
                                if got.get("location"):
                                    f["location"] = got["location"]
                            elif scope is text and f.get("location") and not draft.get("location"):
                                f.pop("location", None)                       # the main pass named a place the focused read could not find
                        if scope is text or not (f.get("datetime") or draft.get("datetime")):
                            iso, understood = self.focus_time(scope, now, draft.get("datetime"))
                            if iso:
                                f["datetime"] = iso
                            elif understood:
                                f.pop("datetime", None)                       # no time in these words: keep what the report already has
                    sev_factors = None
                    redo = getattr(self, "rejudge", ()) or ()
                    if "incident_type" in redo and f.get("incident_type") is None:
                        v, type_reason = self.focus_type(ctx)             # new details may change what kind of incident this is
                        if v and v != "other":
                            f["incident_type"] = v
                    if "severity" in redo or not (f.get("severity") or draft.get("severity")):
                        sv, sev_factors = self.judge_severity(ctx, now)
                        if sv:
                            f["severity"] = sv
                    if not (f.get("description") or draft.get("description")):
                        f["description"] = text.strip()          # extraction only: the description is the person's own words
                    if ai_missing is not None:
                        ai_missing = [k for k in ("incident_type", "location", "datetime", "description") if not (f.get(k) or draft.get(k))]
                return Extraction(f, True, attempt, time.perf_counter() - t0, toks, raw, tool=tool, missing=ai_missing, sev_factors=sev_factors, trace=dict(self.trace), vague_location=vague, type_reason=type_reason)
            except Exception as e:  # bad JSON or server hiccup
                err = str(e)
                msgs = msgs + [{"role": "assistant", "content": raw or "(no output)"},
                               {"role": "user", "content": "That was not valid. Reply again in the required format."}]
        return Extraction({}, False, 2, time.perf_counter() - t0, toks, raw, err)

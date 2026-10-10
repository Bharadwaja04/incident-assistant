"""Session = one incident report conversation (the DialogueManager of the plan).
speak -> transcript -> room-name fix -> fields -> (confirm shaky values) -> follow-up question(s) -> review
      -> voice/touch corrections -> submit.
States: IDLE -> LISTENING -> TRANSCRIBING -> EXTRACTING -> ASKING | CONFIRMING | REVIEW -> SUBMITTED.
The app code decides what is missing and what to ask; the model only extracts."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from .asr import clean_transcript
from .metrics import Timer
from .rooms import fix_rooms, snap_location
from .rules import TYPE_KW, extract_location, first_match
from .schema import (ASK_ORDER, ENUMS, FIELDS, LIST_FIELDS, QUESTIONS, RANK, SEV_KW, as_list, derive_severity, evidence,
                     is_compact_time, missing, normalise, parse_time, severity_floor, severity_from_text)

class ModelUnavailable(Exception):
    """The LLM gave no usable answer and no rule-based fallback is configured."""


MAX_FOLLOWUPS = 3   # plan: after 3 questions stop asking and let the person fill the rest on screen
LOW_CONF = 0.45     # whole utterance: below this we ask the person to repeat
WORD_LOW = 0.4      # single word: below this, and the word matters, we ask to confirm
MAX_CONFIRMS = 2    # never ask more than this many 'is that right?' per turn; the rest show on the review screen
CONFIRM_ORDER = ['datetime', 'incident_type', 'location', 'severity']
LABEL = {"datetime": "the time", "location": "the location", "severity": "the severity", "incident_type": "the incident type",
         "persons_involved": "who was involved", "immediate_actions": "what was done", "witnesses": "the witnesses",
         "equipment": "the equipment", "description": "the description", "follow_up": "the follow-up"}
TYPE_LABEL = {"water_leak": "Water leak", "fire": "Fire", "electrical": "Electrical incident", "injury": "Injury",
              "security": "Security incident", "equipment_failure": "Equipment failure", "chemical_spill": "Chemical spill",
              "near_miss": "Near miss", "it_outage": "IT outage", "other": "Incident"}
YES = re.compile(r"^\W*(yes|yeah|yep|yup|correct|right|exactly|sure|ok|okay|that'?s right|that is right)\b", re.I)
NO = re.compile(r"^\W*(no|nope|nah|wrong|incorrect|not (?:really|right))\b", re.I)
COMBINED = "location+datetime"
MAX_REPEATS = 3     # after this many "didn't catch that" in a row, hand over to the form
# Whisper invents these when it only hears noise or silence
_NOISE_TEXT = re.compile(r"^(?:\W*(?:thank you|thanks for watching|thanks|bye|you|look)\W*)+$", re.I)


def _tok(w: str) -> str:
    return re.sub(r"[^a-z0-9]", "", w.lower())


def show_value(field_name: str, value) -> str:
    if field_name == "datetime":
        d = datetime.fromisoformat(value)
        return f"{d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"
    return ", ".join(value) if isinstance(value, list) else str(value)


def _bare_location(text: str) -> str:
    loc = re.sub(r"^(it was|it's|its|at|in|near|inside|outside|the)\s+", "", text.strip(" .,"), flags=re.I)
    return re.sub(r"^(the)\s+", "", loc, flags=re.I)


@dataclass
class TurnResult:
    transcript: str
    draft: dict
    missing: list
    message: str                 # what the assistant says/shows next
    ready: bool                  # all required fields present -> go to review screen
    asr_s: float = 0.0
    extract_s: float = 0.0
    total_s: float = 0.0
    llm_valid: bool = True
    used_shortcut: bool = False
    needs_repeat: bool = False
    confirming: Optional[str] = None   # field we are asking "is that right?" about
    raw: str = ""                       # what Whisper heard, before the cleaning layer
    clean_how: str = "rules"            # llm | rules | none: who cleaned it
    ai_trace: Optional[dict] = None     # raw answers of the model's focused questions
    ai_missing: Optional[list] = None   # mandatory fields the model says are missing (None = it did not say)
    state: str = "ASKING"
    flags: dict = field(default_factory=dict)


@dataclass
class EditResult:
    ok: bool
    message: str
    changed: dict
    intent: Optional[str] = None       # "submit" when the person said "submit"


_SUBMIT = re.compile(r"^\W*(?:ok(?:ay)?[, ]+)?(?:please\s+)?(submit|send it|send|save it|save|file it|looks good|that'?s (?:all|it|correct))\b", re.I)
_SET = re.compile(r"\b(?:change|set|make|update|correct)\s+(?:the\s+)?(severity|incident type|type|location|time|date ?time|description)\s+(?:to|as|into)\s+(.+?)\W*$", re.I)
_MAKE_SEV = re.compile(r"\bmake it\s+(?:a\s+)?(low|medium|moderate|high|critical)(?:\s+severity)?\W*$", re.I)
_ADD_WHO = re.compile(r"\badd\s+(.+?)\s+as\s+(?:a\s+|an\s+|the\s+)?(witness(?:es)?|person|victim|involved(?: person)?)\W*$", re.I)
_ADD_DESC = re.compile(r"\badd\s+(?:that\s+)?(.+?)\W*$", re.I)
_NOT = re.compile(r"\b(?:no,?\s+)?(?:it was|actually|it's)\s+(.+?),?\s+not\s+(.+?)\W*$", re.I)
_SET_KEY = {"severity": "severity", "incident type": "incident_type", "type": "incident_type", "location": "location",
            "time": "datetime", "date time": "datetime", "datetime": "datetime", "description": "description"}


class Session:
    def __init__(self, asr, extractor, store=None, now_fn=datetime.now, fallback=None, cleaner=None, rule_cleaning=True):
        self.asr, self.ex, self.store, self.now_fn, self.fallback = asr, extractor, store, now_fn, fallback
        self.ai_missing = None
        self.ai_mode = not extractor.__class__.__name__.startswith('Rule')
        self.cleaner, self.clean_how, self.rule_cleaning = cleaner, "rules", rule_cleaning
        self.draft: dict = {}
        self.expected: Optional[str] = None
        self.followups = 0
        self.transcripts: list = []
        self.queue: list = []          # [(field, value)] held back until the person confirms
        self.locked: set = set()       # fields the person set by hand or voice: never overwritten
        self.sev_note: Optional[str] = None
        self.notice = ""
        self.edits: list = []
        self.repeats = 0

    @property
    def full_text(self) -> str:
        return " ".join(self.transcripts)

    @property
    def evidence_text(self) -> str:
        """What backs a value: the person's speech, plus anything they typed or corrected themselves."""
        return " ".join(self.transcripts + self.edits)

    # ---- one conversational turn -------------------------------------------------
    def turn(self, speech) -> TurnResult:
        """speech = audio path (WhisperASR) or text (TextASR)."""
        with Timer() as total:
            a = self.asr.transcribe(speech)
            r, ex_s, valid, shortcut = self._core(a)
        r.asr_s, r.extract_s, r.total_s, r.llm_valid, r.used_shortcut = a.seconds, ex_s, total.s + a.seconds, valid, shortcut
        r.flags, r.raw, r.clean_how, r.ai_missing = self.flags(), a.text, self.clean_how, self.ai_missing
        r.ai_trace = getattr(self, 'ai_trace', None)
        return r

    def _core(self, a):
        if self.cleaner:                                      # AI cleaning layer (guarded; falls back to the deterministic cleaner)
            cleaned, self.clean_how = self.cleaner.clean(a.text)
        elif self.rule_cleaning:
            cleaned, self.clean_how = clean_transcript(a.text), "rules"
        else:                                                 # AI-only mode with the AI cleaner off: the text goes to the model as heard
            cleaned, self.clean_how = a.text, "raw"
        text = cleaned if self.ai_mode else fix_rooms(cleaned)[0]      # AI-only: the model reads the room list itself
        now = self.now_fn()
        if len(re.findall(r"[A-Za-z0-9]", text)) < 2 or (not self.ai_mode and _NOISE_TEXT.match(text)):   # graceful failure 1: nothing heard / only fillers / noise
            return self._repeat(text), 0.0, True, False
        if self.queue:                                        # answering "is that right?"
            ans = "yes" if YES.match(text) else "no" if NO.match(text) else None
            if ans:
                f, v = self.queue.pop(0)
                if ans == "yes":
                    self.draft[f] = v
                self.transcripts.append(text)
                self.repeats = 0
                self._refresh()
                return self._respond(text), 0.0, True, True
            self.expected = self.queue[0][0]                  # they restated it instead: treat as the answer to that field
            self.queue.clear()
        low = a.confidence < LOW_CONF
        quick = self._shortcut(text, now) if low else None
        if low and not quick:                                 # graceful failure 2: ASR unsure and nothing usable in it
            return self._repeat(text), 0.0, True, False
        self.transcripts.append(text)
        first = len(self.transcripts) == 1
        self.repeats = 0
        try:
            new, shortcut, ex_s, valid = self._interpret(text, now)
        except ModelUnavailable:
            self.transcripts.pop()                            # nothing was understood: leave the report exactly as it was
            raise
        held = self._uncertain(new, text, a.words, now, shortcut)
        if low:                                               # ASR was unsure of the whole thing: confirm what we took from it
            held = sorted(set(held) | set(new), key=lambda k: CONFIRM_ORDER.index(k) if k in CONFIRM_ORDER else 9)[:MAX_CONFIRMS]
            held = [k for k in held if k in new]
        for k in held:
            self.queue.append((k, new.pop(k)))
        self._merge(new)
        if first and not self.draft.get("description"):       # description is required: fall back to what was said
            self.draft["description"] = text
        self._refresh()
        return self._respond(text), ex_s, valid, shortcut

    # ---- interpretation ----------------------------------------------------------
    def _shortcut(self, text, now):
        """Cheap parse of a short answer to the question we just asked. Returns fields or None."""
        exp = self.expected
        if not exp:
            return None
        short = len(text.split()) <= 8
        if exp in ENUMS and short:               # the person just said an enum word: "fire", ...
            w = text.lower().strip(" .,!").replace(" ", "_")
            if w in ENUMS[exp]:
                return {exp: w}
        if exp == "incident_type" and short and (t := first_match(TYPE_KW, text)):
            return {"incident_type": t}
        if exp == "datetime" and (short or len(text.split()) <= 12) and (ts := parse_time(text, now, bare_ok=True)):
            return {"datetime": ts}
        if exp == "location" and short and (loc := (extract_location(text) or _bare_location(text))):
            return {"location": snap_location(loc)}
        if exp == COMBINED and len(text.split()) <= 10:      # "second floor washroom at 7 AM", or just one of the two
            out, ts = {}, parse_time(text, now)
            if ts:
                out["datetime"] = ts
            loc = extract_location(text) or (None if ts else _bare_location(text))
            if loc:
                out["location"] = snap_location(loc)
            return out or None
        return None

    def _interpret(self, text, now):
        """Shortcut for short answers to a question we just asked; otherwise full extraction."""
        exp = self.expected
        quick = self._shortcut(text, now) if not self.ai_mode else None      # with a real LLM the model reads short answers too
        if quick:
            return quick, True, 0.0, True
        self.ex.rejudge = tuple(k for k, on in (("severity", self.sev_note == "derived"), ("incident_type", bool(getattr(self, "type_reason", None)))) if on and k not in self.locked)
        e = self.ex.extract(text, self.draft, now, exp.replace("+", " and ") if exp else exp, context=self.full_text)
        fields, valid = e.fields, e.valid
        if not valid and not self.fallback:
            raise ModelUnavailable("The AI model did not answer (is Ollama running?). Nothing was changed. Please try again.")
        self.ai_missing = e.missing            # what the model itself says is still missing (tools mode)
        self.ai_trace = getattr(e, 'trace', None)
        vl = getattr(e, 'vague_location', None)
        if vl:
            self.vague_loc = vl
        if getattr(e, 'type_reason', None):
            self.type_reason = e.type_reason
        if getattr(e, 'sev_factors', None):
            self.sev_factors = e.sev_factors
        if not valid and self.fallback:          # LLM failed twice -> rules keep the demo alive
            fe = self.fallback.extract(text, self.draft, now, exp)
            fields, valid = fe.fields, True
            e.seconds += fe.seconds
        return dict(fields), False, e.seconds, valid

    def _uncertain(self, new, text, words, now, shortcut) -> list:
        """Values we should not trust yet: Whisper was unsure of the words behind them, or the
        LLM's time disagrees with the plain time parser."""
        lowset = {_tok(w) for w, p in words if p < WORD_LOW and _tok(w)}
        if self.ai_mode:                                      # AI-only: confirm only when Whisper itself was unsure of the words behind a value
            out = [k for k, v in new.items() if k in ('datetime', 'location') and lowset & set(re.findall(r'[a-z0-9]+', (v[11:16].replace(':', ' ') + ' ' + v) .lower() if k == 'datetime' else v.lower()))]
            return [k for k in sorted(out, key=CONFIRM_ORDER.index)][:MAX_CONFIRMS]
        out = []
        for k, v in new.items():
            bad = False
            if k == "datetime":
                if not shortcut:
                    rp = parse_time(text, now)
                    bad = rp is None or rp[11:16] != v[11:16]
                d = datetime.fromisoformat(v)
                tl = text.lower()
                if (re.search(r"\bmorning\b", tl) and d.hour >= 12) or (re.search(r"\b(afternoon|evening)\b", tl) and d.hour < 12):
                    bad = True                    # "this morning at 9 PM" cannot be right
                h12 = d.hour % 12 or 12
                nums = {str(d.hour), str(h12), f"{d.minute:02d}", str(d.minute),
                        f"{h12}{d.minute:02d}", f"{d.hour}{d.minute:02d}"}   # Whisper may give "6:15" as one token
                bad = bad or bool(lowset & nums)
                bad = bad or is_compact_time(text, bare_ok=self.expected in ("datetime", COMBINED))   # "847" -> confirm once
            elif k == "severity":
                bad = any(re.search(p, t) for t in lowset for _, p in SEV_KW)
            elif k == "incident_type":
                bad = any(re.search(p, t) for t in lowset for _, p in TYPE_KW)
            elif k == "location":
                bad = bool(lowset & set(re.findall(r"[a-z0-9]+", v.lower())))
            if bad:
                out.append(k)
        out.sort(key=CONFIRM_ORDER.index)
        return out[:MAX_CONFIRMS]

    def _merge(self, new: dict):
        for k, v in new.items():
            if k == "description" and self.draft.get("description"):
                continue                          # keep the original summary
            if k in self.locked:
                continue
            if k in LIST_FIELDS:                  # lists grow: new people / equipment are added, nothing is lost
                cur = self.draft.get(k, [])
                have = {c.lower() for c in cur}
                self.draft[k] = cur + [x for x in as_list(v) if x.lower() not in have]
            elif k == "location":
                self.draft[k] = v if self.ai_mode else snap_location(v)
            else:
                self.draft[k] = v

    def _refresh(self):
        """Checks that run after every change: reject future times, derive severity, apply the severity floor."""
        now, d = self.now_fn(), self.draft
        self.notice = ""
        dt = d.get("datetime")
        if dt:
            try:
                if datetime.fromisoformat(dt) > now + timedelta(minutes=5):
                    del d["datetime"]
                    self.notice = "That time is in the future."
            except ValueError:
                del d["datetime"]
        self.type_guess = False
        if d.get("location"):
            self.vague_loc = None
        if not d.get("incident_type") and d.get("description") and len(d["description"].split()) >= 4:
            d["incident_type"] = "other"              # never keep asking: the person can change it on the review screen
            self.type_guess = True
        elif d.get("incident_type") == "other" and getattr(self, "_guessed", False):
            self.type_guess = True
        self._guessed = self.type_guess
        if self.ai_mode:                                      # AI-only: the model judged severity; code only warns if it looks too low
            if d.get("severity") in RANK and "severity" not in self.locked:
                self.sev_note = "derived"                       # judged by the model; no keyword floor
            elif "severity" in self.locked:
                self.sev_note = None
        elif d.get("incident_type") and "severity" not in self.locked:
            text = self.full_text
            stated, floor = severity_from_text(text), severity_floor(d, text)
            if stated:
                d["severity"] = stated
                self.sev_note = "check" if floor and RANK[stated] < RANK[floor] else None
            else:
                d["severity"] = derive_severity(d, text, d.get("severity"))
                self.sev_note = "derived"
        elif "severity" in self.locked:
            self.sev_note = None

    def flags(self) -> dict:
        """Per field, for the review screen: unverified (red outline), derived / check (yellow badge), plus evidence spans."""
        n = len(self.full_text)
        f = {}
        for k, v in evidence(self.draft, self.evidence_text).items():
            v["spans"] = [sp for sp in v["spans"] if sp[1] <= n]          # highlight only inside the shown transcript
            f[k] = v
        if getattr(self, "type_guess", False) and self.draft.get("incident_type") == "other":
            f["incident_type"] = {"status": "derived", "spans": [], "note": ("Marked Other because none of the types fit" + (". What you described: " + self.type_reason if getattr(self, "type_reason", None) else "") + ". Tap to change.")}
        elif getattr(self, "type_reason", None) and self.draft.get("incident_type") and "incident_type" not in f:
            f["incident_type"] = {"status": "derived", "spans": [], "note": "Judged by the AI from: " + self.type_reason + ". Tap to change."}
        if self.sev_note == "derived" and "severity" in self.draft:
            f["severity"] = {"status": "derived", "spans": [], "note": (("Judged by the AI" + (": " + ", ".join(self.sev_factors) if getattr(self, "sev_factors", None) else " from what you said") + ". Tap to change.") if self.ai_mode else "Worked out from the incident type. Tap to change.")}
        elif self.sev_note == "check" and "severity" in self.draft:
            f["severity"] = {"status": "check", "spans": [], "note": "Lower than usual for this kind of incident. Please check."}
        return f

    # ---- what to say next --------------------------------------------------------
    def _respond(self, text) -> TurnResult:
        if self.queue:
            f, v = self.queue[0]
            return TurnResult(text, dict(self.draft), missing(self.draft),
                              f'I think I heard {LABEL.get(f, f)} as "{show_value(f, v)}". Is that right? (yes / no)',
                              False, confirming=f, state="CONFIRMING")
        miss = missing(self.draft)
        if "location" in miss and "datetime" in miss and "incident_type" not in miss:
            ask = COMBINED                       # plan: combine questions when two fields are missing
        else:
            ask = next((f for f in ASK_ORDER if f in miss), None)
            if ask is None and "description" in miss:
                ask = "description"
        if ask is None:
            self.expected = None
            return TurnResult(text, dict(self.draft), [], self.review_text(), True, state="REVIEW")
        if self.followups >= MAX_FOLLOWUPS:       # don't interrogate forever: hand over to manual edit
            self.expected = None
            return TurnResult(text, dict(self.draft), miss,
                              "I still need: " + ", ".join(LABEL[m].replace("the ", "") for m in miss) + ". Please fill these in on the form.",
                              False, state="REVIEW")
        self.followups += 1
        self.expected = ask
        q = QUESTIONS[ask]
        if ask in ("location", COMBINED) and getattr(self, "vague_loc", None):
            q = f'You said "{self.vague_loc}". Which room number, block or floor is that exactly?'
        return TurnResult(text, dict(self.draft), miss, (self.notice + " " if self.notice else "") + q, False, state="ASKING")

    def _repeat(self, text):
        """Nothing usable heard. Re-ask the pending question (never echo junk back); after MAX_REPEATS hand over to the form."""
        self.repeats += 1
        if self.repeats >= MAX_REPEATS:
            self.repeats = 0
            return TurnResult(text, dict(self.draft), missing(self.draft),
                              "It is hard to hear here. Please fill in the form on screen.", False, needs_repeat=True, state="REVIEW")
        q = QUESTIONS.get(self.expected) if self.expected else None
        msg = "Sorry, I didn't catch that. " + (q or "Please say it again.")
        return TurnResult(text, dict(self.draft), missing(self.draft), msg, False, needs_repeat=True, state="ASKING")

    # ---- review / corrections / submit -------------------------------------------
    def review_text(self) -> str:
        d = self.draft
        keys = ["incident_type", "severity", "location", "datetime", "description", "persons_involved", "equipment",
                "witnesses", "immediate_actions", "follow_up"]
        lines = [f"{k.replace('_', ' ').title()}: {show_value(k, d[k]) if k == 'datetime' else (', '.join(d[k]) if isinstance(d[k], list) else d[k])}"
                 for k in keys if d.get(k)]
        return "Please review:\n" + "\n".join(lines) + "\nSay 'submit' to send, or tell me what to change."

    def edit(self, field_name: str, value) -> dict:
        """Manual edit from the review screen. Empty value clears the field. The field is then locked."""
        if field_name not in FIELDS:
            return self.draft
        if value in (None, "", []):
            self.draft.pop(field_name, None)
        else:
            v = normalise({field_name: value}, self.now_fn())
            if field_name in v:
                self.draft[field_name] = snap_location(v[field_name]) if field_name == "location" else v[field_name]
        self.locked.add(field_name)
        if value not in (None, "", []):
            self.edits.append(", ".join(value) if isinstance(value, list) else str(value))   # their own words count as evidence
        self._refresh()
        return self.draft

    def voice_edit(self, text: str) -> EditResult:
        """Review-screen mic. Plain patterns first ("change severity to high", "add Priya as a witness",
        "it was room 3, not room 2", "add that the floor was wet"); the model handles anything else."""
        text = text if self.ai_mode else fix_rooms(clean_transcript(text))[0]
        now = self.now_fn()
        if len(re.findall(r"[A-Za-z0-9]", text)) < 2:
            return EditResult(False, "Sorry, I didn't catch that.", {})
        if not self.ai_mode and _SUBMIT.match(text):
            return EditResult(True, "", {}, "submit")
        self.edits.append(text)
        if self.ai_mode:                                      # AI-only: no pattern commands, the model reads the correction
            return self._llm_edit(text, now)
        m = _MAKE_SEV.search(text)
        ms = _SET.search(text)
        if m or ms:
            key, raw = ("severity", m.group(1)) if m else (_SET_KEY[re.sub(r"\s+", " ", ms.group(1).lower())], ms.group(2))
            if key == "datetime":
                raw = parse_time(raw, now, bare_ok=True) or ""
            v = normalise({key: raw}, now)
            if key not in v:
                return EditResult(False, "Sorry, I couldn't read that value. Tap the field to edit it.", {})
            self.edit(key, snap_location(v[key]) if key == "location" else v[key])
            return EditResult(True, f"Done. {LABEL[key].replace('the ', '').capitalize()} is now {show_value(key, self.draft[key])}.", {key: self.draft[key]})
        m = _ADD_WHO.search(text)
        if m:
            key = "witnesses" if m.group(2).lower().startswith("witness") else "persons_involved"
            names = as_list(m.group(1))
            self._merge({key: names})
            self.locked.add(key)
            return EditResult(True, f"Added {', '.join(names)}.", {key: self.draft[key]})
        m = _NOT.search(text)
        if m:
            new, old = m.group(1).strip(), m.group(2).strip()
            loc = self.draft.get("location", "")
            if loc and re.search(re.escape(old), loc, re.I):
                nl = snap_location(re.sub(re.escape(old), new, loc, flags=re.I))
            else:
                nl = snap_location(new)
            self.edit("location", nl)
            desc = self.draft.get("description", "")
            if desc and re.search(re.escape(old), desc, re.I):
                self.draft["description"] = re.sub(re.escape(old), new, desc, flags=re.I)
            return EditResult(True, f"Done. Location is now {nl}.", {"location": nl})
        m = _ADD_DESC.search(text)
        if m:
            add = m.group(1).strip().rstrip(".")
            self.draft["description"] = (self.draft.get("description", "").rstrip(". ") + ". " + add[0].upper() + add[1:] + ".").lstrip(". ")
            self.locked.add("description")
            return EditResult(True, "Added to the description.", {"description": self.draft["description"]})
        return self._llm_edit(text, now)

    def _llm_edit(self, text, now) -> EditResult:
        """Corrections understood by the model (the only path in AI-only mode)."""
        e = self.ex.extract(text, self.draft, now, "a correction to the finished report")
        if not e.valid and not self.fallback:
            raise ModelUnavailable("The AI model did not answer (is Ollama running?). Nothing was changed. Please try again.")
        fields = e.fields if e.valid else self.fallback.extract(text, self.draft, now).fields
        if e.tool == "submit_report":
            return EditResult(True, "", {}, "submit")
        fields.pop("description", None)
        changed = {k: v for k, v in fields.items() if self.draft.get(k) != v}
        if not changed:
            return EditResult(False, "Sorry, I didn't understand that change. Tap a field to edit it.", {})
        self._merge(changed)
        for k in changed:
            self.locked.add(k)
        self._refresh()
        return EditResult(True, "Done. Updated " + ", ".join(LABEL[k].replace("the ", "") for k in changed) + ".", changed)

    def summary(self) -> str:
        """Spoken confirmation after submit (plan: short, under 15 seconds)."""
        d = self.draft
        parts = [f"{TYPE_LABEL.get(d.get('incident_type'), 'Incident')} at {d.get('location', 'unknown location')}"]
        if d.get("datetime"):
            parts.append(show_value("datetime", d["datetime"]))
        if d.get("severity"):
            parts.append(f"severity {d['severity']}")
        return ", ".join(parts) + ". Report saved."

    def submit(self) -> int:
        m = missing(self.draft)
        if m or self.queue:
            raise ValueError(f"cannot submit, missing: {m or [f for f, _ in self.queue]}")
        return self.store.save(self.draft, self.full_text, flags=self.flags()) if self.store else -1

"""Extractors: take (utterance, current report) -> new fields. All return the same Extraction.

Two ways to talk to the model, same result:
  mode="json"  : ask for one JSON object (works on any runtime; used for the laptop gates).
  mode="tools" : send config/tools.json and read the tool call (what llama-server --jinja gives on the board).
In both, the code validates and the model never decides what is missing."""
from __future__ import annotations
import json, os, sys, time, urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from .rooms import ROOMS
from .schema import CFG, ground, normalise, parse_time
from .rules import extract_rules

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

    @property
    def tokens_per_s(self):
        return self.completion_tokens / self.seconds if self.seconds and self.completion_tokens else 0.0


class RuleExtractor:
    name = "rules"
    def extract(self, text: str, draft: dict, now: datetime, expected: Optional[str] = None) -> Extraction:
        t0 = time.perf_counter()
        f = normalise(extract_rules(text, now), now)
        return Extraction(f, seconds=time.perf_counter() - t0)


def _json_from(text: str):
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON object found")
    return json.loads(text[a:b + 1])


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
        self.prompt = open(os.path.join(CFG, "system_prompt.txt")).read().replace("{rooms}", ", ".join(ROOMS))
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
        "persons_involved (people hurt, affected or involved, e.g. [\"two operators\"]); "
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

    def extract(self, text: str, draft: dict, now: datetime, expected: Optional[str] = None) -> Extraction:
        user = (f"Now: {now.strftime('%Y-%m-%d %H:%M')}\nCurrent report: {json.dumps(draft)}\n"
                + (f"(We just asked about: {expected})\n" if expected else "") + f"New utterance: {text}")
        msgs = [{"role": "system", "content": self.prompt}, {"role": "user", "content": user}]
        t0, toks, raw, err = time.perf_counter(), 0, "", ""
        for attempt in (1, 2):
            try:
                msg, n = self._chat(msgs)
                toks += n
                tool = ""
                calls = msg.get("tool_calls") or []
                if calls:                                    # tools mode (or a model that used a tool anyway)
                    tool = calls[0]["function"]["name"]
                    raw = json.dumps(calls[0]["function"])
                    fields = fields_from_tool_call(tool, calls[0]["function"].get("arguments", {}))
                else:                                        # JSON mode, or a tools-mode model that answered in text
                    raw = msg.get("content") or ""
                    fields = _json_from(raw)
                f = ground(normalise(fields, now), text, ai=True)      # AI mode: only 'is it in what was said' checks, no keyword rules
                rp = parse_time(text, now) if "datetime" in f else None
                if rp and rp != f["datetime"]:
                    f["datetime"] = rp                  # a clock time the person actually said beats the model's arithmetic ("8.35 PM" is 20:35, not 21:35)
                if len(text.split()) >= 10 and not all(f.get(k) for k in self.OPTIONAL):
                    extra = ground(normalise(self.details(text), now), text, ai=True)      # optional details were skipped: ask again, just for them
                    for k in self.OPTIONAL:
                        if k in extra and not f.get(k):          # never overwrite what the main pass already found
                            f[k] = extra[k]
                return Extraction(f, True, attempt, time.perf_counter() - t0, toks, raw, tool=tool)
            except Exception as e:  # bad JSON or server hiccup
                err = str(e)
                msgs = msgs + [{"role": "assistant", "content": raw or "(no output)"},
                               {"role": "user", "content": "That was not valid. Reply again in the required format."}]
        return Extraction({}, False, 2, time.perf_counter() - t0, toks, raw, err)

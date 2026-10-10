# Handoff: laptop pipeline -> React Native (Android) app on the HDK8650 (Snapdragon 8 Gen 3)

Matches `hackathon_plan.pdf` (owner C "Understanding" + the logic D and A need). Python in `incident/` is the **reference**; the TypeScript (React Native) port must reproduce `eval/golden_cases.json`.

## What exists (all runs on a laptop, nothing needs the board)
| Plan item | File | Status |
|---|---|---|
| `prompts/schema.json` | `config/incident_schema.json` | plan schema: datetime, persons_involved, equipment, witnesses, immediate_actions, follow_up. Required: incident_type, location, datetime, description. Severity derived. |
| `prompts/rooms.txt` | `config/rooms.txt` | sample site (replace with the real venue's rooms) |
| `prompts/system_prompt.txt` | `config/system_prompt.txt` | fixed text (cache-friendly), `{rooms}` filled at load, severity rubric, 4 examples incl. self-correction. Time goes in the user message. |
| `prompts/tools.json` | `config/tools.json` | create_incident_report, request_missing_info, update_report_field, submit_report. Generated: `python -m incident.tools` |
| Validator | `schema.py`: `normalise`, `ground`, `evidence`, `missing`; `pipeline._refresh` | evidence check (fuzzy >= 0.85), future-time rejection, severity derive + floor |
| Location gazetteer | `rooms.py`: `fix_rooms`, `snap_location` | "conference room too" -> "Conference Room 2" before the LLM sees it |
| Relative times | `schema.parse_time` | 10 AM, 2:30 PM, 11.45 am, "20 minutes ago", "half an hour ago", yesterday, this evening at 6 |
| LlmClient | `llm.py` (`OpenAICompatExtractor`) | json mode or tools mode, retry once, rule fallback |
| ReportState.merge | `pipeline.Session._merge` | lists grow, description kept, user-edited fields locked |
| FollowUps | `schema.QUESTIONS` + `Session._respond` | one question per field, "When and where did this happen?" when both missing, max 3 |
| DialogueManager | `pipeline.Session.turn` | states below |
| Voice corrections | `Session.voice_edit` | "change severity to high", "add Priya as a witness", "it was room 3, not room 2", "add that the floor was wet", "submit" |
| Eval | `incident/gates.py`, `eval/*` | 25 sentences, 6 dialogues, 10 clips, 13 shaky-speech scenarios |
| Screens | `incident/app.py` + `static/index.html` | Record, Review, Done, History: see `UI_SPEC.md` |

## DialogueManager states (plan)
IDLE -> LISTENING -> TRANSCRIBING -> EXTRACTING -> ASKING | CONFIRMING | REVIEW -> SUBMITTED.
`onTranscript(text)` returns the next action: ask a question, ask "is that right?", or show the form.

## Rules the app code enforces (the model can never skip them)
1. Missing required field -> ask (template question). Never trust the model's "missing" list.
2. Literal values (location, people, equipment, witnesses, actions) must appear in the transcript; weak ones are marked `unverified` (red outline). Derived fields are exempt.
3. Severity: stated words win (minor/serious/critical, "nothing serious" = low). Otherwise the model's rubric answer, raised to the **floor**: injury, fire, smoke, gas, electric shock = at least High. Flag `derived` (yellow badge). Stated but below the floor = kept, flag `check`.
4. Times: ISO only, future times rejected (we ask again), relative phrases resolved from the device clock. Code wins over the model; if they disagree we ask "is that right?".
4b. Spoken numbers: "847" = 8:47, "650 pm" = 6:50 PM, "eight forty seven", "half past six", "just now" are all read. With no AM/PM the latest reading that is not in the future wins. A time that came from bare digits is confirmed once ("is that right?").
5. Whisper unsure about a word that matters (WORD_LOW 0.4, max 2 per turn) -> "is that right?" with big Yes/No.
6. Garbage / silence / Whisper noise text ("Thank you.", "you", "Thanks for watching") / very low ASR confidence (LOW_CONF 0.45) never produces a report: the pending question is asked again (junk is never echoed back). Low confidence but a readable answer: it is used and confirmed. After 3 misses in a row the app hands over to the form (state REVIEW, `ready` false).
7. After 3 questions stop asking; the person fills the rest on the review screen.

## Porting list (pure functions, ~450 lines of TypeScript)
`normalize.normalize` (the cleaning layer between Whisper and the LLM; cases in `eval/disfluency.jsonl`), `schema.parse_time / normalise / ground / evidence / severity_from_text / severity_floor / derive_severity / missing`, `rooms.fix_rooms / snap_location`, `pipeline.Session` (turn, _interpret shortcuts, _uncertain, _merge, _refresh, flags, _respond, edit, voice_edit, summary, submit), `llm.fields_from_tool_call`.
Fuzzy matching: Python uses difflib ratio >= 0.85; in TypeScript use the `fuzzball` npm package (token set ratio >= 85), as the plan says. Tiny differences are fine; re-check against the golden file and fix any case that changes.
Unit test: load `eval/golden_cases.json` in JUnit; sections: parse_time, parse_time_bare_ok, clean_transcript, severity_from_text, severity_floor, derive_severity, normalise, ground, fix_rooms, snap_location, evidence, flows (whole conversations), voice_edits.
Regenerate after any change: `python -m incident.make_golden`.

## LLM on the board
- `llama-server --jinja -c 4096 --device HTP0 -ngl 99` with the same `config/tools.json` and `config/system_prompt.txt` (`{rooms}` replaced by `rooms.txt`, joined with ", "). For Qwen3 put `/no_think` first in the system prompt. `temperature: 0`, `max_tokens: 300`.
- Request = system prompt (never changes -> prompt cache) + user message exactly as `llm.py` builds it:
  `Now: 2026-10-10 18:30\nCurrent report: {json}\n(We just asked about: ...)\nNew utterance: <text>`
- Response: a tool call (`create_incident_report` / `request_missing_info` / `update_report_field`). Map it with `fields_from_tool_call`, then `normalise` -> `ground`. If no tool call, parse the first `{` to last `}` of the text. Invalid twice -> rule fallback (`rules.py`), so the demo never dies.
- Send one warm-up request at app start so the system prompt is cached before the first report.
- Test from the PC through `adb forward tcp:8080 tcp:8080`: `python -m incident.gates --phase 1 --extractor llm --base-url http://localhost:8080/v1 --model qwen3-1.7b --mode tools`.

## ASR / TTS needs from teammate B
- Transcript text + whole-utterance confidence (0-1). Per-word probabilities if the decoder gives them; if not, pass 1.0 for every word (the time cross-check and review screen still catch most errors).
- Stop recording after ~800 ms of silence; mute the mic while TTS speaks; spoken questions < 8 words (they already are).

## Device report (for `python -m incident.gates --phase 4 --report device_report.json`)
```
{"asr_rtf":0.4,"llm_ttft_s":1.2,"llm_tokens_per_s":14,"e2e_p50_s":5.5,"e2e_p95_s":8,"peak_ram_mb":3100,"device_ram_mb":<adb shell cat /proc/meminfo>,
 "json_valid_rate":0.97,"field_accuracy":0.86,"airplane_mode_ok":true}
```
Phase 5 adds `demo_success_rate` and `thermal_slowdown_pct`.

## Deliberate differences from the plan (tell us if you want them changed)
1. "this morning/afternoon/evening" alone does **not** create a time (we ask); with a clock time it fixes AM/PM. A guessed time would be a hallucination.
2. A stated severity below the floor ("small fire, minor") is kept but flagged `check`, not overridden.
3. Evidence check **drops** values with < 50% of their words in the transcript and flags 50-85% as `unverified`; the plan only flags. Lower hallucination, same UI.
4. Hallucination gate is 5% on 25 sentences (one miss = 4%); the plan's 2% needs the 60-case set.
5. Spoken question for time is "When did it happen? Approximate is fine." (7 words).

## Questions for the Qualcomm mentors
1. Can the Hexagon backend of llama.cpp be considered stable on the 8 Gen 3 HDK (v75)? Any known bad ops?
2. Is using AI Hub cloud compile and Hugging Face downloads at build time allowed? (Plan says to ask.)
3. Can we read per-token probabilities from the Whisper decoder in the VoiceAI SDK?
4. RAM of this HDK, and Android version.

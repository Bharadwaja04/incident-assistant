# App screens (spec for the React Native (Android) build; working prototype = `python -m incident.app`)

Prototype: `python -m incident.app --asr whisper --extractor llm --model qwen3:1.7b`, open http://localhost:8000. Without `--asr whisper` you can type instead of speaking. Look at it before building the Android screens; it is the reference for layout, states and wording.

## Screens
### 1. Record
- Big mic button (tap to talk; stops after ~800 ms of silence; second tap stops early). Status line: Ready / Listening / Transcribing / Thinking / Asking / Confirming / Speaking.
- Conversation: assistant bubbles on the left, what the person said on the right.
- Yes / No bar (big, 56 dp) only while the assistant asks "is that right?".
- "Report so far": 4 chips for the required fields (type, location, time, description) tick as they are filled, plus the values.
- Footer: stage latency of the last turn (speech to text, understanding, total).
- Header badge: "On-device, no cloud calls" (turns "Offline" when the network is off: shows the airplane-mode proof).
- Typed input is a debug fallback; keep it behind a long-press on the mic if you want a clean demo.

### 2. Review (opens when all required fields are present)
One row per field, in this order: incident type, severity, location, date & time, description, people involved, equipment, witnesses, immediate actions, follow-up.
| Row state | Look | When |
|---|---|---|
| normal | white | value present and backed by the transcript |
| `unverified` | red outline + red badge "Not heard clearly" | a literal value only partly matches what was said |
| `derived` | yellow badge "Worked out, check" + note | severity not stated: derived from the type / rubric / floor |
| `check` | yellow badge "Please check" | stated severity is below the usual minimum |
| required & empty | grey, dashed, badge "Required" | only after 3 questions without an answer |
- Tap a row: the words it came from are highlighted in "What you said". Tap again: edit (dropdown for type and severity, date-time picker, text for the rest, comma-separated chips for lists). Editing locks the field (nothing overwrites it later) and counts as evidence.
- Mic "Say a correction": "change severity to high", "add Priya as a witness", "it was room 3, not room 2", "add that the floor was wet", "submit". The result line shows what was heard and what changed.
- Buttons: Submit (disabled while a required field is empty), Add details by voice (back to Record).

### 3. Done
Tick, one spoken summary ("Injury at Cafeteria, 6:10 PM, severity high. Report saved."), New report button. TTS speaks the summary; the mic is off while it speaks.

### 4. History
List of saved reports (type, location, time, severity); tap opens it read-only with the transcript. Needs "Clear history" before the demo.

## Behaviour that must match
- Mute the mic while TTS plays; reopen it in the completion callback (hands-free mode). Push-to-talk stays as the fallback.
- Run ASR, LLM and TTS on a background thread; update the UI on the main thread.
- Never show a half-finished report as final: the Review screen opens only when `ready` is true.
- Android manifest (in the React Native android/ folder): `INTERNET` + `usesCleartextTraffic="true"` for `http://127.0.0.1:8080`.

## Data contract between engine and screens (same JSON as the prototype's /api/turn)
```
{ "transcript": "...", "message": "When did it happen? Approximate is fine.",
  "state": "ASKING | CONFIRMING | REVIEW", "ready": false, "confirming": null | "datetime",
  "draft": {"incident_type":"injury","severity":"high","location":"Cafeteria", ...},
  "flags": {"severity":{"status":"derived","spans":[],"note":"..."}, "witnesses":{"status":"ok","spans":[[63,67]]}},
  "full_text": "everything said so far", "missing": ["datetime"],
  "timings": {"asr":0.4,"extract":3.1,"total":3.6} }
```
`spans` are character ranges inside `full_text`. For mock screens (hour 3 to 8 of the plan) paste the `draft` and `flags` of any flow in `eval/golden_cases.json` into the Review screen.

## Build order for D
1. Review screen on hard-coded JSON (flow 1 in the golden file). 2. Record screen with the mic button and a fake engine that returns the golden flow steps. 3. Done + History. 4. Swap the fake engine for the TypeScript ReportEngine. 5. Hook ASR/TTS.

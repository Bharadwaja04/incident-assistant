# AI Incident Report Assistant: laptop gates (the team's hour-by-hour plan is hackathon_plan.pdf)

These laptop gates predict the plan's Gate 1 (model choice) and Gate 3 (speak -> filled form). Plan metrics (hallucination < 2%, WER < 10% clean, field F1 >= 0.90) need the 60-case set and the board; our thresholds are the proxies below.

Pipeline: mic -> ASR (Whisper) -> LLM extracts JSON -> validate/normalise -> ask follow-up for missing fields -> review screen -> submit (SQLite).
Everything runs on the phone. The laptop prototype is the same logic with swappable backends.

| File | Role | On the phone |
|---|---|---|
| config/incident_schema.json, system_prompt.txt | the contract: fields, enums, questions, prompt | load as-is (assets) |
| eval/*.jsonl | golden tests | run the same cases against the TypeScript port |
| incident/schema.py, rules.py, pipeline.py | normalise, time parsing, dialogue | port to TypeScript (~300 lines) |
| incident/asr.py (WhisperASR) | laptop stand-in | Whisper from Qualcomm AI Hub via QAIRT |
| incident/llm.py (OpenAICompat) | laptop stand-in (Ollama / llama.cpp) | small LLM via Genie |

Run: `python -m incident.app` (screens), `python -m incident.cli` (typed), `python -m incident.gates --phase N [--extractor llm --model qwen3:1.7b [--mode tools]]`, `python -m incident.make_golden` (regenerate TypeScript test cases).

## Phases (move on only when the gate says GO)
| # | Goal | Measured on | Gate (all must pass) | If NO-GO |
|---|---|---|---|---|
| 0 | Freeze contract, test set, record 10+ WAV clips (eval/audio/x.wav + x.txt) | laptop | >=14 utterances, >=4 dialogues, >=10 clips | add data; teammates record clips in a noisy room too |
| 1 | Text -> fields with a real small LLM | laptop, 4 threads | JSON valid >=95%, field acc >=85%, beats rules by 3 pts, hallucinated absent fields <=5%, p95 <=4s, model file <=35% of device RAM | smaller/different model, shorter prompt, keep rules as fallback |
| 2 | Speech -> text | laptop | WER <=15%, real-time factor <=0.5, ASR model <=200MB | tiny.en instead of base, VAD trimming |
| 3 | Full voice loop + follow-ups + failure handling | laptop | 100% dialogues reach review, >=90% final fields right, garbage never accepted, turn p95 <=6s | fix dialogue logic |
| 4 | Same pipeline on the QIDK, models on device | phone | ASR RTF <=1.0, TTFT <=2.5s, >=8 tok/s, e2e p50 <=8s, p95 <=12s, peak RAM <=60% of device, JSON valid >=95%, field acc >=80%, works in airplane mode | NPU->CPU runtime, 1B model, int4, shorter outputs |
| 5 | Demo hardening | phone | 20 live runs >=90% success, <=30% slowdown after 10 back-to-back runs | cache models warm, cool device, fall back to recorded demo |

Laptop speed is only a proxy for the phone. Phases 0-3 prove the logic and that the models are the right size. Phase 4 is the real test.

## Device report (phase 4/5): the phone app writes this JSON, then `python3 -m incident.gates --phase 4 --report device_report.json`
{"asr_rtf":0.4,"llm_ttft_s":1.2,"llm_tokens_per_s":14,"e2e_p50_s":5.5,"e2e_p95_s":8,"peak_ram_mb":3100,"device_ram_mb":8000,
 "json_valid_rate":0.97,"field_accuracy":0.86,"airplane_mode_ok":true,"demo_success_rate":0.95,"thermal_slowdown_pct":12}

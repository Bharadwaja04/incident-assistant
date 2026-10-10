"""Writes eval/golden_cases.json: exact input -> output pairs produced by THIS Python code (the reference).
The TypeScript port (ReportEngine) must reproduce every case. Re-run after any logic change:  python -m incident.make_golden"""
import json, os
from datetime import datetime
from .asr import TextASR, clean_transcript
from .llm import RuleExtractor
from .pipeline import Session
from .rooms import fix_rooms, snap_location
from .schema import derive_severity, evidence, ground, normalise, parse_time, severity_floor, severity_from_text

NOW = datetime(2026, 10, 10, 21, 0)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "eval", "golden_cases.json")


def flow(turns):
    """Whole conversations through the real Session (typed text, rule extractor): what the TypeScript DialogueManager must match."""
    s = Session(TextASR(), RuleExtractor(), None, now_fn=lambda: NOW)
    steps = []
    for t in turns:
        r = s.turn(t)
        steps.append({"say": t, "state": r.state, "message": r.message.splitlines()[0], "ready": r.ready, "draft": r.draft})
    return {"turns": steps, "final_flags": {k: v["status"] for k, v in s.flags().items()}}


def edits(first, cmds):
    s = Session(TextASR(), RuleExtractor(), None, now_fn=lambda: NOW)
    s.turn(first)
    out = []
    for c in cmds:
        r = s.voice_edit(c)
        out.append({"say": c, "ok": r.ok, "intent": r.intent, "message": r.message, "draft": dict(s.draft)})
    return {"first": first, "edits": out}


def main():
    g = {"now": NOW.isoformat(timespec="minutes")}
    g["parse_time"] = [{"in": i, "out": parse_time(i, NOW)} for i in [
        "at 10 AM", "around 2:30 PM", "at 11.45 am", "8:30 in the evening", "at 4pm yesterday", "this morning at 9", "at 16, 6, 15 PM",
        "at 650 pm", "3 AM", "12 PM", "12 AM", "at 9", "no time here", "14:05", "20 minutes ago", "about twenty minutes ago", "half an hour ago",
        "an hour ago", "2 hours ago", "a couple of hours ago", "an hour and a half ago", "forty five minutes ago"]]
    g["parse_time_bare_ok"] = [{"in": i, "out": parse_time(i, NOW, True)} for i in ["10", "7 AM", "it was around 6", "second floor"]]
    g["clean_transcript"] = [{"in": i, "out": clean_transcript(i)} for i in [
        "Uh, small fire in the in the kitchen at 6:15 6:15 PM", "hmm uh", "the the fire", "Fire at the lab, umm, around 2 pm", "ok ok ok so there was a leak"]]
    g["clean_transcript"] += [{"in": json.loads(l)["raw"], "out": clean_transcript(json.loads(l)["raw"])}
                              for l in open(os.path.join(os.path.dirname(OUT), "disfluency.jsonl")) if l.strip()]
    g["severity_from_text"] = [{"in": i, "out": severity_from_text(i)} for i in [
        "nothing serious", "it was minor", "badly hurt", "critical", "no severity here", "small fire", "an ambulance was called"]]
    g["severity_floor"] = [{"fields": f, "text": t, "out": severity_floor(f, t)} for f, t in [
        ({"incident_type": "fire"}, "fire in the kitchen"), ({"incident_type": "injury"}, "he hurt his leg"),
        ({"incident_type": "injury"}, "nobody was hurt"), ({"incident_type": "water_leak"}, "smoke and a leak"),
        ({"incident_type": "water_leak"}, "pipe leak"), ({"incident_type": "other"}, "gas smell in the lab")]]
    g["derive_severity"] = [{"fields": f, "text": t, "proposed": p, "out": derive_severity(f, t, p)} for f, t, p in [
        ({"incident_type": "water_leak"}, "pipe leak", None), ({"incident_type": "near_miss"}, "a box almost fell", None),
        ({"incident_type": "injury"}, "slipped", "low"), ({"incident_type": "equipment_failure"}, "printer broke", "medium")]]
    g["normalise"] = [{"in": i, "out": normalise(i, NOW)} for i in [
        {"severity": "major"}, {"incident_type": "Water Leak"}, {"incident_type": "spill"}, {"incident_type": "banana"}, {"severity": "banana"},
        {"occurred_at": "10 AM"}, {"datetime": "2026-10-10T10:00:00"}, {"witnesses": "Priya and Ravi"}, {"people_involved": ["A", "a", " B "]},
        {"location": "kitchen, second floor", "description": None}, {"action_taken": "called security", "foo": "x"}]]
    g["ground"] = [{"fields": f, "text": t, "out": ground(f, t)} for f, t in [
        ({"incident_type": "fire", "location": "a pipe", "severity": "critical"}, "A pipe burst. Fire department called, minor."),
        ({"location": "kitchen", "datetime": "2026-10-10T18:00"}, "smoke in the kitchen"),
        ({"witnesses": ["Ravi", "Zed"]}, "Ravi saw it"), ({"equipment": ["projector"]}, "the lift is stuck")]]
    g["fix_rooms"] = [{"in": i, "out": fix_rooms(i)[0]} for i in [
        "water leak near conference room too around 10 AM", "fire in lab one oh two at 3 pm", "send them to lab to see",
        "smoke in the caffeteria", "gas in the server rum", "cars in parking b one", "He went to conference room for."]]
    g["snap_location"] = [{"in": i, "out": snap_location(i)} for i in ["conference room 2", "Conferance Room 2", "pantry", "lobby"]]
    g["evidence"] = [{"fields": f, "text": t, "out": evidence(f, t)} for f, t in [
        ({"location": "Cafeteria", "witnesses": ["Ravi"]}, "Someone slipped near the Cafeteria. Ravi saw it."),
        ({"witnesses": ["Dev"]}, "Ravi saw it")]]
    g["flows"] = [flow(t) for t in [
        ["Someone slipped near the cafeteria and hurt their ankle.", "About twenty minutes ago. It was Priya from accounts, and Ravi saw it."],
        ["A pipe burst", "second floor washroom", "7 AM"],
        ["There was a water leak near conference room too around 10 AM. Maintenance has already isolated the area."],
        ["Fire in the kitchen at 11 PM", "6 PM"],
        ["Small fire in the kitchen at 3 PM, minor."],
        ["hmm uh", "Printer not working in finance department", "10 in the morning"]]]
    g["voice_edits"] = [edits("Water leak near conference room 2 around 10 AM, maintenance isolated the area.", [
        "change severity to high", "add Priya as a witness", "it was room 3, not room 2", "add that the floor was wet", "change the time to 9 AM", "submit", "blah blah"])]
    json.dump(g, open(OUT, "w"), indent=1)
    print("wrote", os.path.abspath(OUT), {k: len(v) for k, v in g.items() if isinstance(v, list)})


if __name__ == "__main__":
    main()

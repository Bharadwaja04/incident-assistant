// Whole conversations through Session with a scripted model: asking, narrower location, corrections, hand-over, review.
import { Session, MAX_FOLLOWUPS } from "../src/session.ts";
import type { Extraction } from "../src/extractor.ts";
import { fromIso } from "../src/schema.ts";
let pass = 0, fail = 0;
const ok = (n: string, c: boolean, i?: any) => { if (c) pass++; else { fail++; console.log("FAIL", n, i ?? ""); } };
const NOW = fromIso("2026-10-11T00:47");
const ext = (fields: any, extra: Partial<Extraction> = {}): Extraction => ({ fields, valid: true, attempts: 1, seconds: 0, raw: "", error: "", tool: "request_missing_info", missing: [], trace: {}, ...extra });
const script = (...answers: Extraction[]) => { const q = [...answers]; return { rejudge: [], extract: async () => q.shift() ?? ext({}) } as any; };
// 1. everything but time and place -> combined question, then answer completes it
{
  const s = new Session(script(ext({ incident_type: "fire", description: "fire in the lab" }), ext({ location: "lab 2", datetime: "2026-10-11T00:40" })), null, () => NOW);
  let r = await s.turn({ text: "There is a fire in the lab" });
  ok("asks combined", r.message === "When and where did this happen?" && !r.ready, r.message);
  r = await s.turn({ text: "lab 2, seven minutes ago" });
  ok("ready for review", r.ready && r.state === "REVIEW" && r.draft.location === "lab 2", r);
  const id = await s.submit(); ok("submit without store", id === -1);
}
// 2. vague place -> narrower question, then specific
{
  const s = new Session(script(ext({ incident_type: "fire", description: "fire in my room", datetime: "2026-10-11T00:30" }, { vagueLocation: "my room" }), ext({ location: "room 204" })), null, () => NOW);
  let r = await s.turn({ text: "fire in my room" });
  ok("narrower question", r.message.includes('You said "my room"') && r.message.includes("room number"), r.message);
  r = await s.turn({ text: "room 204" });
  ok("accepted specific place", r.ready && r.draft.location === "room 204", r);
}
// 3. hand over after MAX_FOLLOWUPS
{
  const s = new Session(script(...Array(6).fill(ext({ description: "something happened here today" }))), null, () => NOW);
  let r; for (let i = 0; i < MAX_FOLLOWUPS + 1; i++) r = await s.turn({ text: "something happened here today" });
  ok("hand over to form", r!.state === "REVIEW" && r!.message.startsWith("I still need"), r!.message);
}
// 4. correction replaces time; locked field is protected; list fields grow
{
  const s = new Session(script(ext({ incident_type: "security", location: "H304", datetime: "2026-10-10T20:35", description: "attack at 8:35 pm", witnesses: ["Ravi"] }),
    ext({ datetime: "2026-10-10T22:35", witnesses: ["Dev"] })), null, () => NOW);
  await s.turn({ text: "attack at 8:35 pm in H304, Ravi saw it" });
  s.edit("severity", "critical");
  const r = await s.turn({ text: "sorry it was 10:35 pm, Dev saw it too" });
  ok("time corrected", r.draft.datetime === "2026-10-10T22:35", r.draft);
  ok("witnesses grew", JSON.stringify(r.draft.witnesses) === JSON.stringify(["Ravi", "Dev"]), r.draft.witnesses);
  ok("edited severity locked", s.draft.severity === "critical" && s.sevNote === null);
}
// 5. future time rejected, low confidence asks again, model failure leaves report unchanged
{
  const s = new Session(script(ext({ incident_type: "fire", location: "lab", datetime: "2026-10-11T09:00", description: "fire in the lab now" })), null, () => NOW);
  const r = await s.turn({ text: "fire in the lab" });
  ok("future time dropped", !r.draft.datetime && r.message.startsWith("That time is in the future"), r.message);
  const r2 = await s.turn({ text: "uh", confidence: 0.2 }); ok("repeat on low confidence", r2.needsRepeat === true);
  const bad = new Session({ rejudge: [], extract: async () => ({ ...ext({}), valid: false }) } as any, null, () => NOW);
  let threw = false; try { await bad.turn({ text: "fire in the lab" }); } catch { threw = true; }
  ok("model unavailable throws, transcript popped", threw && bad.transcripts.length === 0);
}
// 6. voice edit: submit intent and field change
{
  const s = new Session(script(ext({ incident_type: "fire", location: "lab", datetime: "2026-10-11T00:00", description: "fire in the lab" })), null, () => NOW);
  await s.turn({ text: "fire in the lab at midnight" });
  s.ex = { rejudge: [], edit: async () => ({ valid: true, submit: false, changes: { severity: "high" } }) } as any;
  const e1 = await s.voiceEdit("make it high"); ok("voice edit changes", e1.ok && s.draft.severity === "high" && s.locked.has("severity"), e1);
  s.ex = { rejudge: [], edit: async () => ({ valid: true, submit: true, changes: {} }) } as any;
  const e2 = await s.voiceEdit("looks good submit"); ok("submit intent", e2.intent === "submit");
}
console.log(`session: ${pass} passed, ${fail} failed`); process.exit(fail ? 1 : 0);

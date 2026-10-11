// Runs the extractor against a scripted fake model server: same checks as the Python mocks.
import { Extractor } from "../src/extractor.ts";
import { fromIso } from "../src/schema.ts";
let pass = 0, fail = 0;
const ok = (name: string, cond: boolean, info?: any) => { if (cond) pass++; else { fail++; console.log("FAIL", name, info ?? ""); } };
const NOW = fromIso("2026-10-11T00:47");
function fake(handlers: { main?: any; type?: any; loc?: any; time?: any; sev?: any; det?: any }) {
  return async (_u: string, init: any) => {
    const b = JSON.parse(init.body); const sys: string = b.messages[0].content;
    let message: any;
    if (b.tools && ["at_clock_time", "resolve_time"].includes(b.tools[0].function.name)) message = { tool_calls: [{ function: { name: "resolve_time", arguments: JSON.stringify(handlers.time ?? {}) } }] };
    else if (b.tools) message = { tool_calls: [{ function: { name: handlers.main.tool, arguments: handlers.main.args } }] };
    else if (sys.includes("WHERE")) message = { content: JSON.stringify(handlers.loc ?? { place: null, specific: true }) };
    else if (sys.includes("category")) message = { content: JSON.stringify(handlers.type ?? { type: "other", what_happened: "", reason: "" }) };
    else if (sys.includes("severe")) message = { content: JSON.stringify(handlers.sev ?? { severity: "high", factors: [] }) };
    else message = { content: JSON.stringify(handlers.det ?? {}) };
    return { ok: true, status: 200, json: async () => ({ choices: [{ message }] }) };
  };
}
// 1. correction: report has 8:35 PM, user says it was 10:35 PM
{
  const x = new Extractor({ fetch: fake({ main: { tool: "create_incident_report", args: { datetime: "2026-10-10T20:35" } }, time: { hour: 10, minute: 35, period: "pm" },
    loc: { place: "H304", specific: true, reason: "room" } }) as any });
  const draft = { datetime: "2026-10-10T20:35", location: "H304", incident_type: "security", description: "attack at 8:35 pm", severity: "high" };
  const e = await x.extract("Sorry, it was actually 10:35 p.m., not 8:35.", draft, NOW, null, "attack at 8:35 pm in H304. Sorry, it was actually 10:35 p.m., not 8:35.");
  ok("correction replaces time", e.fields.datetime === "2026-10-10T22:35", e.fields);
  ok("trace shows resolve_time", /resolve_time\(hour=10, minute=35, period=pm\)/.test(e.trace.time), e.trace);
}
// 2. relative time
{
  const x = new Extractor({ fetch: fake({ main: { tool: "request_missing_info", args: JSON.stringify({ partial_report: { description: "lift stuck for two hours on the third floor", location: "third floor" }, missing_fields: ["datetime"] }) },
    time: { minutes_ago: 120 }, loc: { place: "third floor", specific: true, reason: "floor" }, type: { type: "equipment_failure", what_happened: "lift stuck", reason: "lift stuck" } }) as any });
  const t = "The lift has been stuck for two hours on the third floor";
  const e = await x.extract(t, {}, NOW, null, t);
  ok("two hours ago", e.fields.datetime === "2026-10-10T22:47", e.fields);
  ok("location kept", e.fields.location === "third floor", e.fields);
  ok("type from focused call", e.fields.incident_type === "equipment_failure", e.fields);
  ok("model said nothing missing now", (e.missing ?? ["x"]).length === 0, e.missing);
}
// 3. a general place is used as the main call gave it (one call fewer)
{
  let n = 0;
  const base: any = fake({ main: { tool: "request_missing_info", args: JSON.stringify({ partial_report: { location: "my room", description: "fire in my room" }, missing_fields: ["datetime"] }) } });
  const x = new Extractor({ fetch: (async (u: string, i: any) => { n++; return base(u, i); }) as any });
  const t = "There is a fire in my room";
  const e = await x.extract(t, {}, NOW, null, t);
  ok("general place kept as said", e.fields.location === "my room", e.fields);
  ok("no separate location call", !Object.values(e.trace).some((v) => String(v).includes("specific=")), e.trace);
}
// 4. invented severity reason is dropped
{
  const x = new Extractor({ fetch: fake({ main: { tool: "create_incident_report", args: JSON.stringify({}) }, sev: { severity: "high", factors: ["bulldozer fell on the building", "one friend is injured and at risk"] } }) as any });
  const [sv, fac] = await x.judgeSeverity("a bulldozer lost its balance and fell on the building", NOW);
  ok("severity kept", sv === "high"); ok("only supported factor kept", fac.length === 1 && fac[0].includes("bulldozer"), fac);
}
console.log(`extractor: ${pass} passed, ${fail} failed`); process.exit(fail ? 1 : 0);

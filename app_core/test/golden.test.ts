// Checks the TypeScript port against eval/golden_cases.json (produced by the Python reference).
import { readFileSync } from "node:fs";
import { parseTime, normalise, ground, evidence, severityFromText, severityFloor, deriveSeverity, fromIso } from "../src/schema.ts";
const g = JSON.parse(readFileSync(new URL("../../eval/golden_cases.json", import.meta.url), "utf8"));
const NOW = fromIso(g.now);
let pass = 0, fail = 0;
const eq = (a: any, b: any) => JSON.stringify(a) === JSON.stringify(b);
const check = (group: string, input: any, got: any, want: any) => { if (eq(got, want)) pass++; else { fail++; console.log(`FAIL ${group}:`, JSON.stringify(input), "\n  got ", JSON.stringify(got), "\n  want", JSON.stringify(want)); } };
for (const c of g.parse_time) check("parse_time", c.in, parseTime(c.in, NOW), c.out);
for (const c of g.parse_time_bare_ok) check("parse_time_bare_ok", c.in, parseTime(c.in, NOW, true), c.out);
for (const c of g.severity_from_text) check("severity_from_text", c.in, severityFromText(c.in), c.out);
for (const c of g.severity_floor) check("severity_floor", c, severityFloor(c.fields, c.text), c.out);
for (const c of g.derive_severity) check("derive_severity", c, deriveSeverity(c.fields, c.text, c.proposed), c.out);
for (const c of g.normalise) check("normalise", c.in, normalise(c.in, NOW), c.out);
for (const c of g.ground) check("ground", c, ground(c.fields, c.text), c.out);
for (const c of g.evidence) check("evidence", c, evidence(c.fields, c.text), c.out);
console.log(`golden: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

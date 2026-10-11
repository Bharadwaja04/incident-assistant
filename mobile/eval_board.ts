// Scores the real extraction (same code as the app) against the phone's Qwen on eval/utterances.jsonl.
// usage (phone servers running): node --experimental-strip-types mobile/eval_board.ts
import { readFileSync } from "node:fs";
import { Extractor } from "../app_core/src/extractor.ts";
import { Session } from "../app_core/src/session.ts";
const ex = new Extractor({ baseUrl: "http://127.0.0.1:8080/v1", model: "qwen3", mode: "tools" });
const rows = readFileSync(new URL("../eval/utterances.jsonl", import.meta.url), "utf8").trim().split("\n").map((l) => JSON.parse(l));
const words = (s: string) => String(s ?? "").toLowerCase().match(/[a-z0-9]+/g) ?? [];
const hit: Record<string, number> = {}, tot: Record<string, number> = {}; const secs: number[] = []; const misses: string[] = [];
const score = (k: string, ok: boolean, why: string, i: number) => { tot[k] = (tot[k] ?? 0) + 1; if (ok) hit[k] = (hit[k] ?? 0) + 1; else misses.push(`#${i + 1} ${k}: ${why}`); };
for (const [i, r] of rows.entries()) {
  const s = new Session(ex, null, () => new Date()), t0 = Date.now(); let d: any = {};
  try { d = (await s.turn({ text: r.text })).draft; } catch (e: any) { misses.push(`#${i + 1} ERROR ${e.message}`); }
  secs.push((Date.now() - t0) / 1000); const g = r.gold;
  if (g.incident_type) score("incident_type", d.incident_type === g.incident_type, `got ${d.incident_type}, want ${g.incident_type}`, i);
  if (g.location) { const w = words(g.location), have = new Set(words(d.location)); score("location", w.length > 0 && w.filter((x) => have.has(x)).length / w.length >= 0.6, `got "${d.location}", want "${g.location}"`, i); }
  if (g.time) score("time", String(d.datetime ?? "").slice(11, 16) === g.time, `got ${d.datetime}, want ${g.time}`, i);
  if (g.severity) score("severity", d.severity === g.severity, `got ${d.severity}, want ${g.severity}`, i);
  if (g.immediate_actions) score("immediate_actions", !!d.immediate_actions, "not filled", i);
  console.log(`${i + 1}/${rows.length} ${secs[i].toFixed(1)} s`);
}
secs.sort((a, b) => a - b); const q = (p: number) => secs[Math.min(secs.length - 1, Math.floor(p * secs.length))];
console.log("\n=== RESULTS ===");
let H = 0, T = 0; for (const k of Object.keys(tot)) { console.log(`${k.padEnd(18)} ${hit[k] ?? 0}/${tot[k]}  ${(((hit[k] ?? 0) / tot[k]) * 100).toFixed(0)}%`); H += hit[k] ?? 0; T += tot[k]; }
console.log(`ALL FIELDS         ${H}/${T}  ${((H / T) * 100).toFixed(0)}%`);
console.log(`turn latency       p50 ${q(0.5).toFixed(1)} s | p95 ${q(0.95).toFixed(1)} s | n=${secs.length}`);
console.log("\nMISSES:"); console.log(misses.join("\n") || "none");

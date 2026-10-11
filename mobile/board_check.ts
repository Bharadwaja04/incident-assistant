// Runs the real extraction (the same code the app uses) against the phone's Qwen, from the Mac.
// Prints one line per model call (what it was, seconds, tokens read, tokens written) and the real error if there is one.
// usage: node --experimental-strip-types mobile/board_check.ts "optional sentence"
import { Extractor } from "../app_core/src/extractor.ts";
import { Session } from "../app_core/src/session.ts";
const names: [string, string][] = [["information EXTRACTION", "main"], ["WHERE", "location"], ["WHEN", "time"], ["category", "type"], ["severe", "severity"], ["optional details", "details"]];
let n = 0, totalIn = 0, totalOut = 0; void totalIn; void totalOut;
const timed = async (url: string, init: any) => {
  const b = JSON.parse(init.body), sys: string = b.messages[0].content, t0 = Date.now();
  const r: any = await fetch(url, init), j = await r.clone().json();
  const nm = names.find(([k]) => sys.includes(k))?.[1] ?? "other", u = j.usage ?? {}, tm = j.timings ?? {};
  totalIn += u.prompt_tokens ?? 0; totalOut += u.completion_tokens ?? 0;
  console.log(`call ${++n}: ${nm.padEnd(8)} ${((Date.now() - t0) / 1000).toFixed(1)} s | read ${u.prompt_tokens ?? "?"} tokens (${tm.cache_n ?? 0} cached) | wrote ${u.completion_tokens ?? "?"} tokens`);
  return r;
};
const ex = new Extractor({ baseUrl: "http://127.0.0.1:8080/v1", model: "qwen3", mode: "tools", fetch: timed as any });
const s = new Session(ex, null);
const texts = process.argv.slice(2).length ? process.argv.slice(2) : ["Two hours ago a bulldozer fell near the Satva construction site in Bangalore. Two workers were hurt."];
for (const text of texts) {                              // several sentences = one conversation, like the app (corrections included)
  console.log("\n>>> " + text); n = 0;
  const t0 = Date.now();
  try {
    const r: any = await s.turn({ text });
    console.log(JSON.stringify(r.draft, null, 1)); console.log("message:", r.message); console.log("trace:", JSON.stringify(r.aiTrace, null, 1));
  } catch (e: any) { console.log("ERROR:", e.message); }
  console.log(`TOTAL ${((Date.now() - t0) / 1000).toFixed(1)} s | ${n} calls`);
}

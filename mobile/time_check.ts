// Tries phrases on the phone's Qwen through the real resolve_time tool, one by one (each on its own).
// usage: node --experimental-strip-types mobile/time_check.ts "8:30 pm" "yesterday at 8:39 pm" "two hours ago" ...
// A time already in the report (for "two hours later") can be given as first argument: reported=2026-10-10T20:35
import { Extractor } from "../app_core/src/extractor.ts";
const ex = new Extractor({ baseUrl: "http://127.0.0.1:8080/v1", model: "qwen3", mode: "tools" });
let args = process.argv.slice(2), reported: string | null = null;
if (args[0]?.startsWith("reported=")) { reported = args[0].slice(9); args = args.slice(1); }
const now = new Date();
console.log("now:", now.toString().slice(0, 21), reported ? "| report already says " + reported : "");
for (const phrase of args) {
  ex.trace = {};
  const [iso] = await ex.focusTime("The incident happened " + phrase, now, reported);
  console.log(phrase.padEnd(34), "=>", iso ?? "(nothing understood)", "   ", ex.trace.time ?? "");
}

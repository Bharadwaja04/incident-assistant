// Runs the voice-edit step against the phone's Qwen: node --experimental-strip-types mobile/edit_check.ts "change the incident type to chemical factory"
import { Extractor } from "../app_core/src/extractor.ts";
const ex = new Extractor({ baseUrl: "http://127.0.0.1:8080/v1", model: "qwen3", mode: "tools" });
const draft = { incident_type: "equipment_failure", severity: "medium", location: "Block A", description: "The lift is not working.", datetime: "2026-10-10T20:35" };
for (const t of process.argv.slice(2)) { const r = await ex.edit(t, draft as any, new Date()); console.log(JSON.stringify(t), "->", JSON.stringify(r)); }

// Port of incident/llm.py (AI path). Talks to any OpenAI-compatible server (llama-server on the board: http://127.0.0.1:8080/v1).
// The model understands (tool calls, focused questions); this code only validates, reshapes and does date arithmetic.
import { PROMPT_EXTRACT, PROMPT_JSON, PROMPTS, TIME_TOOL, TOOLS, TOOL_TAIL, JSON_TAIL, ROOMS } from "./generated.ts";
import { type Fields, ground, normalise, resolveTime, toks } from "./schema.ts";

export interface Extraction {
  fields: Fields; valid: boolean; attempts: number; seconds: number; raw: string; error: string; tool: string;
  typeReason?: string | null; vagueLocation?: string | null; trace: Record<string, string>; sevFactors?: string[] | null;
  missing: string[] | null;     // mandatory fields the MODEL says are missing (tools mode); null = not said
}
const MANDATORY = ["incident_type", "location", "datetime", "description"];
const OPTIONAL = ["persons_involved", "witnesses", "equipment", "immediate_actions", "follow_up"];
const STOPW = new Set("a an the of in on at to and or is was were are be been has have had with by for from that this it its as not no one some people person there their they he she we i you me my our his her".split(" "));

export function jsonFrom(text: string): any {
  const a = text.indexOf("{"), b = text.lastIndexOf("}");
  if (a < 0 || b <= a) throw new Error("no JSON object found");
  return JSON.parse(text.slice(a, b + 1));
}
/** A stated reason must rest on the speaker's own words: at least half of its meaningful words appear in what was said. */
export function supported(phrase: string, text: string, need = 0.5): boolean {
  const stem = (w: string) => w.slice(0, 5);
  const have = new Set(toks(text).map(stem));
  const ws = toks(phrase).filter((w) => !STOPW.has(w) && w.length > 2);
  return ws.length > 0 && ws.filter((w) => have.has(stem(w))).length / ws.length >= need;
}
/** What the model chose -> the inputs of resolveTime (a plain mapping, no judgement). */
export function timeArgs(name: string, a: any): Record<string, any> {
  if (name === "at_clock_time") return { hour: a.hour, minute: a.minute, period: a.period, days_ago: a.days_back };
  if (name === "time_ago") return { minutes_ago: a.minutes };
  if (name === "days_ago_only") return { days_ago: a.days };
  if (name === "just_now") return { just_now: true };
  if (name === "no_time_said") return {};
  if (name === "move_report_time") return String(a.direction ?? "").toLowerCase().startsWith("earl") ? { minutes_before: a.minutes } : { minutes_after: a.minutes };
  const o: Record<string, any> = {};                                                        // an older-style resolve_time call
  for (const k of ["minutes_ago", "hour", "minute", "period", "clock", "day", "just_now", "minutes_before", "minutes_after", "days_ago"]) o[k] = a[k];
  return o;
}
/** The first complete {...} object in a text (a model may write the same call twice or add a stray tag after it). */
export function firstJson(text: string): any {
  const a = text.indexOf("{"); if (a < 0) throw new Error("no JSON object found");
  let depth = 0, inStr = false, esc = false;
  for (let i = a; i < text.length; i++) {
    const c = text[i];
    if (inStr) { esc = c === "\\" && !esc; if (c === '"' && !esc) inStr = false; continue; }
    if (c === '"') inStr = true; else if (c === "{") depth++; else if (c === "}" && --depth === 0) return JSON.parse(text.slice(a, i + 1));
  }
  throw new Error("unfinished JSON object");
}
export function fieldsFromToolCall(name: string, args: any): Fields {
  if (typeof args === "string") args = JSON.parse(args);
  if (name === "request_missing_info") return { ...(args.partial_report ?? {}) };
  if (name === "update_report_field") return { [args.field ?? ""]: args.value };
  if (name === "submit_report") return {};
  return { ...args };
}

export type Fetch = (url: string, init: any) => Promise<{ ok: boolean; status: number; json(): Promise<any> }>;

const EDIT_FIELDS = ["incident_type", "severity", "location", "description", "persons_involved", "equipment", "witnesses", "immediate_actions", "follow_up"];
const EDIT_PROMPT = `/no_think
The person is correcting a finished incident report. Reply with JSON only: {"submit": false, "changes": {"field": "new value"}, "time": null}
Fields: incident_type, severity, location, description, persons_involved, equipment, witnesses, immediate_actions, follow_up.
Put only the fields they asked to change. Copy the new value word for word from what they said; never replace it with a different or "similar" word, and do not map it to a fixed list.
Example: "change the incident type to chemical factory" -> {"submit": false, "changes": {"incident_type": "chemical factory"}, "time": null}
Example: "the location is block 5 canteen" -> {"submit": false, "changes": {"location": "block 5 canteen"}, "time": null}
For people, equipment or witnesses give a list.
If they change the time or date, put their exact time words in "time". "submit" is true only if they say to submit or send the report.`;
export class Extractor {
  base: string; model: string; mode: "tools" | "json"; maxTokens: number; trace: Record<string, string> = {}; rejudge: string[] = [];
  prompt: string; fetchFn: Fetch;
  constructor(opts: { baseUrl?: string; model?: string; mode?: "tools" | "json"; maxTokens?: number; fetch?: Fetch } = {}) {
    this.base = (opts.baseUrl ?? "http://127.0.0.1:8080/v1").replace(/\/$/, "");
    this.model = opts.model ?? "qwen3-1.7b"; this.mode = opts.mode ?? "tools"; this.maxTokens = opts.maxTokens ?? 300;
    this.fetchFn = opts.fetch ?? ((u, i) => fetch(u, i) as any);
    const nt = this.model.includes("qwen3") ? "/no_think\n" : "";
    this.prompt = nt + (this.mode === "tools" ? PROMPT_EXTRACT : PROMPT_JSON).replace("{rooms}", ROOMS.join(", ")) + (this.mode === "tools" ? TOOL_TAIL : JSON_TAIL);
  }
  private async post(body: any): Promise<any> {
    const r = await this.fetchFn(this.base + "/chat/completions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error("model server answered " + r.status);
    return (await r.json()).choices[0].message;
  }
  private chat(messages: any[]) {
    const body: any = { model: this.model, messages, temperature: 0, max_tokens: this.maxTokens, stream: false, reasoning_effort: "none" };
    if (this.mode === "tools") body.tools = TOOLS;
    return this.post(body);
  }
  async plain(system: string, user: string, maxTokens = 160): Promise<string> {
    const nt = this.model.includes("qwen3") ? "/no_think\n" : "";
    const m = await this.post({ model: this.model, messages: [{ role: "system", content: nt + system }, { role: "user", content: user }],
      temperature: 0, max_tokens: maxTokens, stream: false, reasoning_effort: "none" });
    return (m.content ?? "").trim();
  }
  async details(text: string): Promise<Fields> {
    try {
      const d = jsonFrom(await this.plain(PROMPTS.DETAILS_PROMPT, text, 220)); const out: Fields = {};
      for (const k of OPTIONAL) {
        let v = d[k];
        if (Array.isArray(v) && !["persons_involved", "witnesses", "equipment"].includes(k)) v = v.map(String).join(", ");
        if (v) out[k] = v;
      }
      return out;
    } catch { return {}; }
  }
  async focusType(context: string): Promise<[string | null, string | null]> {
    try {
      const d = jsonFrom(await this.plain(PROMPTS.TYPE_PROMPT, "Incident description: " + context, 90));
      const t = normalise({ incident_type: String(d.type ?? "").toLowerCase().replace(/ /g, "_") }, new Date()).incident_type ?? null;
      const reason = String(d.reason ?? "").trim(), what = String(d.what_happened ?? "").trim();
      this.trace.incident_type = `${d.type} (${reason})`;
      return [t, supported(what, context) ? what : supported(reason, context) ? reason : ""];
    } catch { return [null, null]; }
  }
  /** Voice correction of a finished report: Qwen says which fields change and to what, in the person's own words (no fixed list). */
  async edit(text: string, draft: Fields, now: Date): Promise<{ valid: boolean; submit: boolean; changes: Fields }> {
    try {
      const cur: any = {}; for (const [k, v] of Object.entries(draft)) if (v && k !== "datetime") cur[k] = v;
      const d = jsonFrom(await this.plain(EDIT_PROMPT, "Current report: " + JSON.stringify(cur) + "\nWhat the person said: " + text, 200));
      const changes: Fields = {};
      for (const [k, v] of Object.entries<any>(d.changes ?? {})) {
        if (!EDIT_FIELDS.includes(k) || v == null || String(v).trim() === "") continue;
        const have = new Set(toks(text)), want = toks(String(Array.isArray(v) ? v.join(" ") : v).replace(/_/g, " "));
        if (want.length && want.filter((w) => have.has(w)).length / want.length < 0.6) continue;     // the new value must be the person's own words
        if (Array.isArray(v) || ["persons_involved", "equipment", "witnesses"].includes(k)) changes[k] = (Array.isArray(v) ? v : String(v).split(/\s*,\s*/)).map(String).filter(Boolean);
        else if (k === "incident_type" || k === "severity") changes[k] = String(v).trim().toLowerCase().replace(/\s+/g, "_");
        else changes[k] = String(v).trim();
      }
      if (d.time && String(d.time).trim()) {
        const [iso] = await this.focusTime(String(d.time), now, draft.datetime ? String(draft.datetime) : null);
        if (iso) changes.datetime = iso;
      }
      return { valid: true, submit: d.submit === true, changes };
    } catch { return { valid: false, submit: false, changes: {} }; }
  }
  async focusLocation(context: string): Promise<[string | null, boolean]> {
    try {
      const d = jsonFrom(await this.plain(PROMPTS.LOCATION_PROMPT, "Incident description: " + context, 60));
      this.trace.location = `${d.place} (specific=${d.specific}: ${d.reason})`;
      let place: string | null = d.place ? String(d.place).trim().replace(/^"+|"+$/g, "") : null;
      if (!place || ["null", "none"].includes(place.toLowerCase())) return [null, true];
      return [place, d.specific !== false];
    } catch { return [null, true]; }
  }
  /** The model calls resolve_time with what was said; resolveTime() does the arithmetic. [iso, understood] */
  async focusTime(context: string, now: Date, reported: string | null = null): Promise<[string | null, boolean]> {
    const nt = this.model.includes("qwen3") ? "/no_think\n" : "";
    let msg: any;
    try {
      const tools = reported ? TIME_TOOL : TIME_TOOL.filter((t: any) => t.function.name !== "move_report_time");   // "move the report's time" only exists when the report has a time
      msg = await this.post({ model: this.model, temperature: 0, max_tokens: 80, stream: false, reasoning_effort: "none", tools, tool_choice: "required",    // the model must call one function, it cannot answer in plain words
        messages: [{ role: "system", content: nt + (reported ? PROMPTS.TIME_SHIFT_PROMPT + " " : "") + PROMPTS.TIME_PROMPT },
                   { role: "user", content: (reported ? `Time already in the report: ${reported.replace("T", " ").slice(0, 16)}\n` : "") + "Incident description: " + context }] });
    } catch (e: any) { this.trace.time = "time call failed: " + String(e?.message ?? e).slice(0, 100); return [null, false]; }
    let name = "", args: any = null;
    const tc = (msg.tool_calls ?? [])[0];
    if (tc) { name = tc.function?.name ?? ""; args = tc.function?.arguments ?? {}; }
    try { if (!name) { const o = firstJson(msg.content ?? ""); if (o?.name && "arguments" in o) { name = o.name; args = o.arguments; } else { name = "resolve_time"; args = o; } } if (typeof args === "string") args = JSON.parse(args); } catch { this.trace.time = "no function was called; the model said: " + String(msg.content ?? "").slice(0, 100); return [null, false]; }
    const a = timeArgs(name, args ?? {});
    const iso = resolveTime(now, a, reported);
    this.trace.time = name + "(" + Object.entries(a).filter(([, v]) => v != null && v !== "").map(([k, v]) => `${k}=${v}`).join(", ") + ")" + (iso ? ` → ${iso}` : "");
    return [iso, true];
  }
  async judgeSeverity(context: string, now: Date): Promise<[string | null, string[]]> {
    try {
      const d = jsonFrom(await this.plain(PROMPTS.SEVERITY_PROMPT, "Incident description: " + context, 120));
      const sev = normalise({ severity: String(d.severity ?? "").toLowerCase().trim() }, now).severity ?? null;
      const fac = (d.factors ?? []).map((x: any) => String(x).trim()).filter(Boolean).slice(0, 4).filter((x: string) => supported(x, context));
      return [sev, fac];
    } catch { return [null, []]; }
  }

  async extract(text: string, draft: Fields, now: Date, expected: string | null = null, context = ""): Promise<Extraction> {
    const pad = (n: number) => String(n).padStart(2, "0");
    const nowS = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}`;
    const user = `Now: ${nowS}\nCurrent report: ${JSON.stringify(draft)}\n` + (expected ? `(We just asked about: ${expected})\n` : "") + `New utterance: ${text}`;
    let msgs: any[] = [{ role: "system", content: this.prompt }, { role: "user", content: user }];
    const t0 = Date.now(); let raw = "", err = "";
    this.trace = {};
    for (const attempt of [1, 2]) {
      try {
        const msg = await this.chat(msgs);
        let tool = "", aiMissing: string[] | null = null, fields: any;
        const calls = msg.tool_calls ?? [];
        if (calls.length) {
          tool = calls[0].function.name; raw = JSON.stringify(calls[0].function);
          let args = calls[0].function.arguments ?? {}; if (typeof args === "string") args = JSON.parse(args);
          fields = fieldsFromToolCall(tool, args);
          if (tool === "create_incident_report") aiMissing = [];
          else if (tool === "request_missing_info") aiMissing = (args.missing_fields ?? []).filter((m: string) => MANDATORY.includes(m));
        } else { raw = msg.content ?? ""; fields = jsonFrom(raw); }
        let f = ground(normalise(fields, now), text, true);
        if (this.mode === "tools" && f.description) {                 // extraction only: the description is the person's own words
          const dw = new Set(toks(f.description)), tw = new Set(toks(text));
          if (dw.size && [...dw].filter((w) => tw.has(w)).length / dw.size < 0.8) f.description = text.trim();
        }
        const ctx = context || text; let typeReason: string | null = null, vague: string | null = null;
        if (text.split(/\s+/).length >= 10 && !OPTIONAL.every((k) => f[k])) {
          const extra = ground(normalise(await this.details(text), now), text, true);
          for (const k of OPTIONAL) if (k in extra && !f[k]) f[k] = extra[k];
        }
        const weak = f.incident_type === "other";
        if (weak || !(f.incident_type || draft.incident_type)) {
          const [v, r] = await this.focusType(ctx); typeReason = r;
          if (v && !(weak && v === "other")) f.incident_type = v;
        }
        // the place comes from the main call as it is (general is fine, no extra call); the time is read by the resolve_time tool from what was just said, so a correction replaces the old value
        const scopes = [...new Set([text, ctx])];
        for (const scope of scopes) {
          if (scope === text || !(f.datetime || draft.datetime)) {
            const [iso, understood] = await this.focusTime(scope, now, draft.datetime ? String(draft.datetime) : null);
            if (iso) f.datetime = iso; else if (understood) delete f.datetime;
          }
        }
        let sevFactors: string[] | null = null;
        if (this.rejudge.includes("incident_type") && f.incident_type == null) {
          const [v, r] = await this.focusType(ctx); typeReason = r; if (v && v !== "other") f.incident_type = v;
        }
        if (this.rejudge.includes("severity") || !(f.severity || draft.severity)) {
          const [sv, fac] = await this.judgeSeverity(ctx, now); sevFactors = fac; if (sv) f.severity = sv;
        }
        if (!(f.description || draft.description)) f.description = text.trim();
        if (aiMissing !== null) aiMissing = MANDATORY.filter((k) => !(f[k] || draft[k]));
        return { fields: f, valid: true, attempts: attempt, seconds: (Date.now() - t0) / 1000, raw, error: "", tool, missing: aiMissing, sevFactors,
          trace: { ...this.trace }, vagueLocation: vague, typeReason };
      } catch (e: any) {
        err = String(e?.message ?? e);
        msgs = [...msgs, { role: "assistant", content: raw || "(no output)" }, { role: "user", content: "That was not valid. Reply again in the required format." }];
      }
    }
    return { fields: {}, valid: false, attempts: 2, seconds: (Date.now() - t0) / 1000, raw, error: err, tool: "", missing: null, trace: {} };
  }
}

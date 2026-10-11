// Port of incident/pipeline.py (AI mode only): one incident-report conversation.
// speak -> transcript -> model extraction -> (confirm shaky values) -> follow-up question(s) -> review -> corrections -> submit.
// The app decides what to ask; the model only extracts. No keyword rules: understanding is the model's job.
import { type Extraction, Extractor } from "./extractor.ts";
import { ENUMS, type Fields, FIELDS, LIST_FIELDS, QUESTIONS, RANK, asList, evidence, fromIso, missing, normalise } from "./schema.ts";

export class ModelUnavailable extends Error {}
export const MAX_FOLLOWUPS = 3, LOW_CONF = 0.45, WORD_LOW = 0.4, MAX_CONFIRMS = 2, MAX_REPEATS = 3;
const ASK_ORDER = ["incident_type", "location", "datetime"];
const CONFIRM_ORDER = ["datetime", "incident_type", "location", "severity"];
export const COMBINED = "location+datetime";
export const LABEL: Record<string, string> = { datetime: "the time", location: "the location", severity: "the severity", incident_type: "the incident type",
  persons_involved: "who was involved", immediate_actions: "what was done", witnesses: "the witnesses", equipment: "the equipment", description: "the description", follow_up: "the follow-up" };
const TYPE_LABEL: Record<string, string> = { water_leak: "Water leak", fire: "Fire", electrical: "Electrical incident", injury: "Injury", security: "Security incident",
  equipment_failure: "Equipment failure", chemical_spill: "Chemical spill", near_miss: "Near miss", it_outage: "IT outage", other: "Incident" };
const YES = /^\W*(yes|yeah|yep|yup|correct|right|exactly|sure|ok|okay|that'?s right|that is right)\b/i;
const NO = /^\W*(no|nope|nah|wrong|incorrect|not (?:really|right))\b/i;

export interface Heard { text: string; confidence?: number; words?: [string, number][] }   // what the speech model returned
export interface Flag { status: string; spans: number[][]; note?: string }
export interface TurnResult {
  transcript: string; draft: Fields; missing: string[]; message: string; ready: boolean; state: string;
  needsRepeat?: boolean; confirming?: string | null; aiMissing?: string[] | null; aiTrace?: Record<string, string> | null; flags?: Record<string, Flag>; seconds?: number;
}
export interface EditResult { ok: boolean; message: string; changed: Fields; intent?: "submit" }
export interface Store { save(fields: Fields, transcript: string, flags: Record<string, Flag>): Promise<number> }
export type Cleaner = (text: string) => Promise<{ text: string; how: string }>;

const tok = (w: string) => w.toLowerCase().replace(/[^a-z0-9]/g, "");
export function showValue(field: string, value: any): string {
  if (field === "datetime") { const d = fromIso(value); return `${d.getHours() % 12 || 12}:${String(d.getMinutes()).padStart(2, "0")} ${d.getHours() < 12 ? "AM" : "PM"}`; }
  return Array.isArray(value) ? value.join(", ") : String(value);
}

export class Session {
  draft: Fields = {}; expected: string | null = null; followups = 0; transcripts: string[] = []; queue: [string, any][] = [];
  locked = new Set<string>(); sevNote: string | null = null; notice = ""; edits: string[] = []; repeats = 0;
  vagueLoc: string | null = null; typeReason: string | null = null; sevFactors: string[] | null = null; typeGuess = false; private guessed = false;
  aiMissing: string[] | null = null; aiTrace: Record<string, string> | null = null; cleanHow = "raw";
  ex: Extractor; store: Store | null; nowFn: () => Date; cleaner: Cleaner | null;
  constructor(ex: Extractor, store: Store | null = null, nowFn: () => Date = () => new Date(), cleaner: Cleaner | null = null) {
    this.ex = ex; this.store = store; this.nowFn = nowFn; this.cleaner = cleaner;
  }

  get fullText() { return this.transcripts.join(" "); }
  get evidenceText() { return [...this.transcripts, ...this.edits].join(" "); }

  async turn(heard: Heard): Promise<TurnResult> {
    const t0 = Date.now(); const r = await this.core(heard);
    r.flags = this.flags(); r.aiMissing = this.aiMissing; r.aiTrace = this.aiTrace; r.seconds = (Date.now() - t0) / 1000; return r;
  }

  private async core(a: Heard): Promise<TurnResult> {
    let text = a.text;
    if (this.cleaner) { const c = await this.cleaner(a.text); text = c.text; this.cleanHow = c.how; }
    const now = this.nowFn();
    if ((text.match(/[A-Za-z0-9]/g) ?? []).length < 2) return this.repeat(text);              // nothing heard
    if (this.queue.length) {                                                                    // answering "is that right?"
      const ans = YES.test(text) ? "yes" : NO.test(text) ? "no" : null;
      if (ans) {
        const [f, v] = this.queue.shift()!;
        if (ans === "yes") this.draft[f] = v;
        this.transcripts.push(text); this.repeats = 0; this.refresh();
        return this.respond(text);
      }
      this.expected = this.queue[0][0]; this.queue = [];                                        // they restated it: treat as the answer
    }
    const conf = a.confidence ?? 1, low = conf < LOW_CONF;
    if (low) return this.repeat(text);                                                          // ASR unsure of the whole thing: ask again
    this.transcripts.push(text); const first = this.transcripts.length === 1; this.repeats = 0;
    let e: Extraction;
    try { e = await this.interpret(text, now); } catch (err) { this.transcripts.pop(); throw err; }     // nothing understood: report unchanged
    const nw: Fields = { ...e.fields };
    for (const k of this.uncertain(nw, a.words ?? [])) { this.queue.push([k, nw[k]]); delete nw[k]; }
    this.merge(nw);
    if (first && !this.draft.description) this.draft.description = text;
    this.refresh();
    return this.respond(text);
  }

  private async interpret(text: string, now: Date): Promise<Extraction> {
    const exp = this.expected;
    this.ex.rejudge = [["severity", this.sevNote === "derived"], ["incident_type", !!this.typeReason]].filter(([k, on]) => on && !this.locked.has(k as string)).map(([k]) => k as string);
    const e = await this.ex.extract(text, this.draft, now, exp ? exp.replace("+", " and ") : null, this.fullText);
    if (!e.valid) throw new ModelUnavailable("The AI model did not answer" + (e.error ? " (" + String(e.error).slice(0, 140) + ")" : "") + ". Nothing was changed. Please try again.");
    this.aiMissing = e.missing; this.aiTrace = e.trace;
    if (e.vagueLocation) this.vagueLoc = e.vagueLocation;
    if (e.typeReason) this.typeReason = e.typeReason;
    if (e.sevFactors?.length) this.sevFactors = e.sevFactors;
    return e;
  }

  /** Confirm only when the speech model itself was unsure of the words behind a time or place. */
  private uncertain(nw: Fields, words: [string, number][]): string[] {
    const low = new Set(words.filter(([w, p]) => p < WORD_LOW && tok(w)).map(([w]) => tok(w)));
    const out = Object.entries(nw).filter(([k, v]) => (k === "datetime" || k === "location") &&
      (((k === "datetime" ? v.slice(11, 16).replace(":", " ") + " " + v : String(v)).toLowerCase().match(/[a-z0-9]+/g) ?? []).some((t: string) => low.has(t)))).map(([k]) => k);
    return out.sort((a, b) => CONFIRM_ORDER.indexOf(a) - CONFIRM_ORDER.indexOf(b)).slice(0, MAX_CONFIRMS);
  }

  private merge(nw: Fields) {
    for (const [k, v] of Object.entries(nw)) {
      if (k === "description" && this.draft.description) continue;                // keep the original summary
      if (this.locked.has(k)) continue;
      if (LIST_FIELDS.includes(k)) {                                                // lists grow
        const cur: string[] = this.draft[k] ?? []; const have = new Set(cur.map((c) => c.toLowerCase()));
        this.draft[k] = [...cur, ...asList(v).filter((x) => !have.has(x.toLowerCase()))];
      } else this.draft[k] = v;
    }
  }

  private refresh() {
    const now = this.nowFn(), d = this.draft; this.notice = "";
    if (d.datetime) {
      try { if (fromIso(d.datetime).getTime() > now.getTime() + 5 * 60000) { delete d.datetime; this.notice = "That time is in the future."; } } catch { delete d.datetime; }
    }
    this.typeGuess = false;
    if (d.location) this.vagueLoc = null;
    if (!d.incident_type && d.description && d.description.split(/\s+/).length >= 4) { d.incident_type = "other"; this.typeGuess = true; }
    else if (d.incident_type === "other" && this.guessed) this.typeGuess = true;
    this.guessed = this.typeGuess;
    if (d.severity in RANK && !this.locked.has("severity")) this.sevNote = "derived";
    else if (this.locked.has("severity")) this.sevNote = null;
  }

  flags(): Record<string, Flag> {
    const n = this.fullText.length, f: Record<string, Flag> = {};
    for (const [k, v] of Object.entries(evidence(this.draft, this.evidenceText))) f[k] = { status: v.status, spans: v.spans.filter((s) => s[1] <= n) };
    if (this.typeGuess && this.draft.incident_type === "other")
      f.incident_type = { status: "derived", spans: [], note: "Marked Other because none of the types fit" + (this.typeReason ? ". What you described: " + this.typeReason : "") + ". Tap to change." };
    else if (this.typeReason && this.draft.incident_type && !("incident_type" in f))
      f.incident_type = { status: "derived", spans: [], note: "Judged by the AI from: " + this.typeReason + ". Tap to change." };
    if (this.sevNote === "derived" && this.draft.severity)
      f.severity = { status: "derived", spans: [], note: "Judged by the AI" + (this.sevFactors?.length ? ": " + this.sevFactors.join(", ") : " from what you said") + ". Tap to change." };
    return f;
  }

  private respond(text: string): TurnResult {
    const base = (message: string, ready: boolean, state: string, extra: Partial<TurnResult> = {}): TurnResult =>
      ({ transcript: text, draft: { ...this.draft }, missing: missing(this.draft), message, ready, state, ...extra });
    if (this.queue.length) {
      const [f, v] = this.queue[0];
      return base(`I think I heard ${LABEL[f] ?? f} as "${showValue(f, v)}". Is that right? (yes / no)`, false, "CONFIRMING", { confirming: f });
    }
    const miss = missing(this.draft); let ask: string | null;
    if (miss.includes("location") && miss.includes("datetime") && !miss.includes("incident_type")) ask = COMBINED;
    else { ask = ASK_ORDER.find((f) => miss.includes(f)) ?? null; if (ask === null && miss.includes("description")) ask = "description"; }
    if (ask === null) { this.expected = null; return base(this.reviewText(), true, "REVIEW", { missing: [] }); }
    if (this.followups >= MAX_FOLLOWUPS) {
      this.expected = null;
      return base("I still need: " + miss.map((m) => LABEL[m].replace("the ", "")).join(", ") + ". Please fill these in on the form.", false, "REVIEW");
    }
    this.followups++; this.expected = ask;
    let q = QUESTIONS[ask];
    if ((ask === "location" || ask === COMBINED) && this.vagueLoc) q = `You said "${this.vagueLoc}". Which room number, block or floor is that exactly?`;
    return base((this.notice ? this.notice + " " : "") + q, false, "ASKING");
  }

  private repeat(text: string): TurnResult {
    this.repeats++;
    const miss = missing(this.draft);
    if (this.repeats >= MAX_REPEATS) { this.repeats = 0; return { transcript: text, draft: { ...this.draft }, missing: miss, message: "It is hard to hear here. Please fill in the form on screen.", ready: false, needsRepeat: true, state: "REVIEW" }; }
    const q = this.expected ? QUESTIONS[this.expected] : null;
    return { transcript: text, draft: { ...this.draft }, missing: miss, message: "Sorry, I didn't catch that. " + (q ?? "Please say it again."), ready: false, needsRepeat: true, state: "ASKING" };
  }

  reviewText(): string {
    const keys = ["incident_type", "severity", "location", "datetime", "description", "persons_involved", "equipment", "witnesses", "immediate_actions", "follow_up"];
    const lines = keys.filter((k) => this.draft[k]).map((k) => `${k.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}: ${showValue(k, this.draft[k])}`);
    return "Please review:\n" + lines.join("\n") + "\nSay 'submit' to send, or tell me what to change.";
  }

  /** Manual edit from the review screen. Empty value clears the field. The field is then locked. */
  edit(field: string, value: any): Fields {
    if (!FIELDS.includes(field)) return this.draft;
    const empty = value == null || value === "" || (Array.isArray(value) && !value.length);
    if (empty) delete this.draft[field];
    else { const v = normalise({ [field]: value }, this.nowFn()); if (field in v) this.draft[field] = v[field]; }
    this.locked.add(field);
    if (!empty) this.edits.push(Array.isArray(value) ? value.join(", ") : String(value));
    this.refresh(); return this.draft;
  }

  /** Review-screen microphone: the model reads the correction (or the submit command). */
  async voiceEdit(text: string): Promise<EditResult> {
    const now = this.nowFn();
    if ((text.match(/[A-Za-z0-9]/g) ?? []).length < 2) return { ok: false, message: "Sorry, I didn't catch that.", changed: {} };
    this.edits.push(text);
    const e = await this.ex.edit(text, this.draft, now);
    if (!e.valid) throw new ModelUnavailable("The AI model did not answer. Nothing was changed. Please try again.");
    if (e.submit) return { ok: true, message: "", changed: {}, intent: "submit" };
    const fields = e.changes;
    const changed = Object.fromEntries(Object.entries(fields).filter(([k, v]) => JSON.stringify(this.draft[k]) !== JSON.stringify(v)));
    const same = Object.keys(fields).filter((k) => JSON.stringify(this.draft[k]) === JSON.stringify(fields[k]));
    if (!Object.keys(changed).length && same.length) return { ok: true, message: "Already set: " + same.map((k) => showValue(k, this.draft[k])).join(", ") + ".", changed: {} };
    if (!Object.keys(changed).length) return { ok: false, message: "Sorry, I didn't understand that change. Tap a field to edit it.", changed: {} };
    for (const [k, v] of Object.entries(changed)) { this.draft[k] = v; this.locked.add(k); } this.refresh();
    return { ok: true, message: "Done. Updated " + Object.keys(changed).map((k) => (LABEL[k] ?? k.replace(/_/g, " ")).replace("the ", "")).join(", ") + ".", changed };
  }

  summary(): string {
    const d = this.draft; const parts = [`${TYPE_LABEL[d.incident_type] ?? "Incident"} at ${d.location ?? "unknown location"}`];
    if (d.datetime) parts.push(showValue("datetime", d.datetime));
    if (d.severity) parts.push(`severity ${d.severity}`);
    return parts.join(", ") + ". Report saved.";
  }

  async submit(): Promise<number> {
    const m = missing(this.draft);
    if (m.length || this.queue.length) throw new Error("cannot submit, missing: " + (m.length ? m : this.queue.map(([f]) => f)).join(", "));
    return this.store ? this.store.save(this.draft, this.fullText, this.flags()) : -1;
  }
}

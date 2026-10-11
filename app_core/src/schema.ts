// Port of incident/schema.py: normalisation, time handling, grounding (does the speaker's text support the value?), evidence spans.
// Pure logic, no I/O. Dates are local wall-clock strings "YYYY-MM-DDTHH:MM" (no time zones).
import { SCHEMA } from "./generated.ts";

export type Fields = Record<string, any>;
export const PROPS = SCHEMA.properties;
export const FIELDS: string[] = Object.keys(PROPS);
export const REQUIRED: string[] = SCHEMA.required;
export const ENUMS: Record<string, string[]> = Object.fromEntries(Object.entries<any>(PROPS).filter(([, v]) => v.enum).map(([k, v]) => [k, v.enum]));
export const LIST_FIELDS: string[] = Object.entries<any>(PROPS).filter(([, v]) => v.type === "array").map(([k]) => k);
export const QUESTIONS: Record<string, string> = SCHEMA["x-questions"];
export const EVIDENCE_FIELDS = ["location", "persons_involved", "equipment", "witnesses", "immediate_actions"];
export const RANK: Record<string, number> = { low: 0, medium: 1, high: 2, critical: 3 };

const ALIASES: Record<string, string> = { occurred_at: "datetime", time: "datetime", date_time: "datetime", people_involved: "persons_involved",
  people: "persons_involved", persons: "persons_involved", action_taken: "immediate_actions", actions: "immediate_actions",
  actions_taken: "immediate_actions", immediate_action: "immediate_actions", witness: "witnesses" };
const TYPE_SYNONYMS: Record<string, string> = { spill: "chemical_spill", chemical: "chemical_spill", gas_leak: "chemical_spill", leak: "water_leak",
  flood: "water_leak", outage: "it_outage", network: "it_outage", network_outage: "it_outage", theft: "security", break_in: "security",
  short_circuit: "electrical", accident: "injury", nearmiss: "near_miss", equipment: "equipment_failure", malfunction: "equipment_failure" };
const SEVERITY_SYNONYMS: Record<string, string> = { major: "high", serious: "high", severe: "high", significant: "high", urgent: "high",
  minor: "low", small: "low", slight: "low", negligible: "low", moderate: "medium", mild: "low", emergency: "critical", fatal: "critical", life_threatening: "critical" };
const SEV_KW: [string, RegExp][] = [
  ["critical", /\b(critical|life[- ]threatening|fatal)\b/i], ["high", /\b(major|serious|severe|badly|ambulance|massive|significant)\b/i],
  ["medium", /\b(moderate|medium)\b/i], ["low", /\b(minor|small|slight|negligible|low)\b/i]];
const NEG_SEV = /\b(nothing|not|no|never|isn'?t|wasn'?t)\s+(?:very\s+|too\s+|that\s+)?(serious|severe|major|critical|bad)\b/i;
const NULLS = ["null", "none", "unknown", "n/a", "not specified", "not mentioned", "nil"];

// ---- date helpers (local wall clock) ----
const p2 = (n: number) => String(n).padStart(2, "0");
export const isoOf = (d: Date) => `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}T${p2(d.getHours())}:${p2(d.getMinutes())}`;
export const fromIso = (s: string) => { const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(s)!; return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]); };
const addMin = (d: Date, n: number) => new Date(d.getTime() + n * 60000);
const dayOf = (now: Date, yesterday: boolean) => new Date(now.getFullYear(), now.getMonth(), now.getDate() - (yesterday ? 1 : 0));
const mk = (day: Date, h: number, mi: number) => new Date(day.getFullYear(), day.getMonth(), day.getDate(), h, mi);

// ---- severity (rule paths: used by tests and the non-AI mode only) ----
export function severityFromText(text: string): string | null {
  if (NEG_SEV.test(text)) return "low";
  for (const [n, p] of SEV_KW) if (p.test(text)) return n;
  return null;
}
const HURT = /\b(hurt|injur\w*|bleeding|bled|fractur\w*|burn(?:s|ed|t)? (?:his|her|their|a)\b|fainted|unconscious|crushed|amputat\w*)\b/i;
const FLOOR = /\b(smoke|smoking|gas leak|gas smell|smell of gas|smells? (?:of )?gas|electric(?:al)? shock|shocked|electrocut\w*)\b/i;
const NO_HARM = /\b(no ?one|nobody|no|nothing|not|without)\s+(?:\w+\s+){0,2}(?:hurt|injur\w*|harm\w*)\b/i;
const DEFAULT_SEV: Record<string, string> = { near_miss: "low", equipment_failure: "low", other: "low", it_outage: "medium", water_leak: "medium",
  security: "medium", electrical: "medium", chemical_spill: "high", injury: "high", fire: "high" };
export function severityFloor(f: Fields, text: string): string | null {
  const ty = f.incident_type;
  return ty === "fire" || (ty === "injury" && !NO_HARM.test(text)) || FLOOR.test(text) || (HURT.test(text) && !NO_HARM.test(text)) ? "high" : null;
}
export function deriveSeverity(f: Fields, text: string, proposed?: string | null): string {
  const sev = proposed && proposed in RANK ? proposed : DEFAULT_SEV[f.incident_type] ?? "medium";
  const fl = severityFloor(f, text);
  return fl && RANK[sev] < RANK[fl] ? fl : sev;
}

// ---- time ----
const MERID = /\b(\d{1,2})(?:[:.]([0-5]\d))?\s*(a\.?m\.?|p\.?m\.?)(?![a-z])/i;
const COMPACT_M = /\b(\d{1,2})(\d{2})\s*(a\.?m\.?|p\.?m\.?)(?![a-z])/i;
const COMPACT = /(?<![\d:])(?<!\d\.)(\d{1,2})(\d{2})(?!\d)(?![:.]\d)/;
const COMPACT_CUE = /\b(?:at|around|about|by|since)\s+\d{3,4}\b|\b\d{3,4}\s*(?:a\.?m\.?|p\.?m\.?)(?![a-z])/i;
const DOT = /\b(?:at|around|about|by)\s+(\d{1,2})\.([0-5]\d)\b/i;
const H24 = /\b([01]?\d|2[0-3]):([0-5]\d)\b/;
const BARE = /\b(?:at|around|about|by)\s+(\d{1,2})\b(?!\s*(?:people|persons?|workers?))/i;
const ANYNUM = /\b(\d{1,2})\b/;
const UNITS: Record<string, number> = { a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, eleven: 11,
  twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18, nineteen: 19 };
const TENS: Record<string, number> = { twenty: 20, thirty: 30, forty: 40, fifty: 50, sixty: 60 };
const NUMRE = String.raw`\d{1,3}|(?:twenty|thirty|forty|fifty|sixty)(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?|a couple of|a few|couple of|few|an?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen`;
const REL = new RegExp(String.raw`\b(` + NUMRE + String.raw`)\s+(minutes?|mins?|hours?|hrs?)\s+ago\b`, "i");
const REL_HALF = /\b(half an hour|half hour|a quarter of an hour|quarter of an hour|an hour and a half)\s+ago\b/i;
const HALF_MIN: Record<string, number> = { "half an hour": 30, "half hour": 30, "a quarter of an hour": 15, "quarter of an hour": 15, "an hour and a half": 90 };
const JUST_NOW = /\b(just now|right now)\b/i;
const HW = "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve";
const MW = String.raw`(?:oh|o)\s+(?:one|two|three|four|five|six|seven|eight|nine)|(?:ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen)|(?:twenty|thirty|forty|fifty)(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?`;
const WORD_HM = new RegExp(String.raw`\b(` + HW + String.raw`)\s+(` + MW + String.raw`)\b`, "i");
const HALF_PAST = new RegExp(String.raw`\bhalf past (` + HW + String.raw`)\b`, "i");
const QTR_PAST = new RegExp(String.raw`\bquarter past (` + HW + String.raw`)\b`, "i");
const QTR_TO = new RegExp(String.raw`\bquarter to (` + HW + String.raw`)\b`, "i");
const OCLOCK = new RegExp(String.raw`\b(` + HW + String.raw`)\s+o'?\s?clock\b`, "i");
const PM = /(?<![a-z])(?:p\.?m\.?)(?![a-z])|\b(?:evening|afternoon|tonight)\b/i;
const AM = /(?<![a-z])(?:a\.?m\.?)(?![a-z])|\bmorning\b/i;

function num(s: string): number {
  s = s.toLowerCase().trim();
  if (/^\d+$/.test(s)) return parseInt(s, 10);
  if (s === "a couple of" || s === "couple of") return 2;
  if (s === "a few" || s === "few") return 3;
  const parts = s.split(/[- ]/);
  if (parts[0] in TENS) return TENS[parts[0]] + (parts.length > 1 ? UNITS[parts[1]] ?? 0 : 0);
  return UNITS[s] ?? 0;
}

function hmIso(h: number, mi: number, t: string, now: Date): string | null {
  if (!(h >= 0 && h <= 23 && mi >= 0 && mi <= 59)) return null;
  const day = dayOf(now, t.includes("yesterday"));
  if (h >= 1 && h <= 12) {
    const pm = PM.test(t), am = AM.test(t);
    if (pm && !am) h = h === 12 ? 12 : h + 12;
    else if (am && !pm) h = h === 12 ? 0 : h;
    else {
      const cands = [h % 12, (h % 12) + 12];
      const past = cands.filter((c) => mk(day, c, mi).getTime() <= addMin(now, 5).getTime());
      h = past.length ? Math.max(...past) : cands[0];
    }
  }
  return isoOf(mk(day, h, mi));
}

export function isCompactTime(text: string, bareOk = false): boolean {
  const t = text.toLowerCase();
  if (H24.test(t) || MERID.test(t)) return false;
  return COMPACT_CUE.test(t) || (bareOk && COMPACT.test(t));
}

export function parseTime(text: string, now: Date, bareOk = false): string | null {
  const t = text.toLowerCase();
  const floor = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate(), d.getHours(), d.getMinutes());
  let m = REL_HALF.exec(t);
  if (m) return isoOf(addMin(floor(now), -HALF_MIN[m[1]]));
  m = REL.exec(t);
  if (m && num(m[1]) > 0) { const n = num(m[1]); return isoOf(addMin(floor(now), -(m[2].toLowerCase().startsWith("h") ? n * 60 : n))); }
  let mm: RegExpExecArray | null;
  if (JUST_NOW.test(t) && (mm = /\b([01]?\d|2[0-3])([0-5]\d)\b/.exec(t)) && !/\d{5}|[:.]/.test(t)) return hmIso(+mm[1], +mm[2], t, now);
  if (JUST_NOW.test(t) && !COMPACT_M.test(t)) return isoOf(floor(now));
  m = COMPACT_M.exec(t);
  if (m) return hmIso(+m[1], +m[2], t, now);
  m = MERID.exec(t);
  if (!m && /\d\s*[ap]\.?m\b\.?(?![a-z])/.test(t)) return null;
  let h: number, mi: number;
  if (m) {
    h = +m[1]; mi = +(m[2] ?? 0); const pm = m[3].toLowerCase().startsWith("p");
    if (h > 12) return null;
    h = h === 12 ? (pm ? 12 : 0) : pm ? h + 12 : h;
  } else {
    let w = HALF_PAST.exec(t); if (w) return hmIso(num(w[1]), 30, t, now);
    w = QTR_PAST.exec(t); if (w) return hmIso(num(w[1]), 15, t, now);
    w = QTR_TO.exec(t); if (w) return hmIso(num(w[1]) - 1 || 12, 45, t, now);
    w = OCLOCK.exec(t); if (w) return hmIso(num(w[1]), 0, t, now);
    w = WORD_HM.exec(t);
    const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (w && (bareOk || new RegExp(String.raw`\b(?:at|around|about|by|since)\s+` + esc(w[0])).test(t) || PM.test(t) || AM.test(t)))
      return hmIso(num(w[1]), num(w[2].replace(/^(?:oh|o)\s+/, "")), t, now);
    let x = H24.exec(t);
    if (x) { h = +x[1]; mi = +x[2]; }
    else {
      x = DOT.exec(t);
      if (x) { h = +x[1]; mi = +x[2]; }
      else {
        const c = bareOk || COMPACT_CUE.test(t) ? COMPACT.exec(t) : null;
        if (c) return hmIso(+c[1], +c[2], t, now);
        x = BARE.exec(t) ?? (bareOk ? ANYNUM.exec(t) : null);
        if (!x) return null;
        h = +x[1]; mi = 0;
      }
    }
    if (h < 12 && /\b(evening|afternoon|tonight)\b/.test(t)) h += 12;
    else if (h >= 6 && h < 12 && /\bnight\b/.test(t)) h += 12;
  }
  if (h > 23) return null;
  return isoOf(mk(dayOf(now, t.includes("yesterday")), h, mi));
}

/** The function the model calls (resolve_time). Plain date arithmetic: small models cannot subtract reliably.
 *  Model gives only what was said: minutes_ago, or hour/minute/period (copied as said), or just_now. */
export function resolveTime(now: Date, a: { minutes_ago?: any; hour?: any; minute?: any; period?: any; day?: any; just_now?: any; clock?: any; minutes_before?: any; minutes_after?: any; days_ago?: any }, reported?: string | null): string | null {
  const base = new Date(now.getFullYear(), now.getMonth(), now.getDate(), now.getHours(), now.getMinutes());
  const isNum = (v: any) => typeof v === "number" && isFinite(v);
  const ok = (v: any) => isNum(v) && v > 0 && v < 60 * 24 * 14;
  if (reported && (ok(a.minutes_before) || ok(a.minutes_after))) {              // moving the time already in the report: plain subtraction or addition
    const r = fromIso(String(reported).slice(0, 16));
    return isoOf(addMin(r, ok(a.minutes_before) ? -a.minutes_before : a.minutes_after));
  }
  if (isNum(a.minutes_ago) && a.minutes_ago > 0 && a.minutes_ago < 60 * 24 * 14) return isoOf(addMin(base, -a.minutes_ago));
  let hour = a.hour, minute = a.minute;
  if (hour == null && a.clock) { const m = /^\s*(\d{1,2})[:.](\d{2})\s*$/.exec(String(a.clock)); if (m) { hour = +m[1]; minute = +m[2]; } }
  if (isNum(hour) && hour >= 0 && hour < 24) {
    const h = Math.trunc(hour), mi = Math.trunc(isNum(minute) ? minute : 0);
    if (!(mi >= 0 && mi < 60)) return null;
    const per = String(a.period ?? "").toLowerCase().replace(/\./g, "").slice(0, 2);
    let cands: number[];
    if ((per === "am" || per === "pm") && h >= 1 && h <= 12) cands = [(h % 12) + (per === "pm" ? 12 : 0)];
    else if (h >= 1 && h <= 12) cands = [h % 12, (h % 12) + 12];
    else cands = [h];
    if (ok(a.days_ago)) {                                                         // a clock time on a day N days back
      const d = new Date(base.getFullYear(), base.getMonth(), base.getDate() - Math.trunc(a.days_ago));
      return isoOf(mk(d, Math.max(...cands), mi));
    }
    const day = new Date(base.getFullYear(), base.getMonth(), base.getDate());
    const times = cands.map((c) => mk(day, c, mi));
    const past = times.filter((t) => t.getTime() <= addMin(base, 5).getTime());
    let t = past.length ? new Date(Math.max(...past.map((x) => x.getTime()))) : addMin(new Date(Math.max(...times.map((x) => x.getTime()))), -1440);
    if (String(a.day).toLowerCase() === "yesterday" && t.getDate() === base.getDate() && t.getMonth() === base.getMonth()) t = addMin(t, -1440);
    return isoOf(t);
  }
  if (ok(a.days_ago)) return isoOf(new Date(base.getFullYear(), base.getMonth(), base.getDate() - Math.trunc(a.days_ago), base.getHours(), base.getMinutes()));   // "two days ago", no clock time
  if (a.just_now === true) return isoOf(base);
  return null;
}

// ---- normalisation ----
export function asList(v: any): string[] {
  if (typeof v === "string") v = v.split(/,|;|\band\b/);
  if (!Array.isArray(v)) v = [v];
  const out: string[] = [], seen = new Set<string>();
  for (let x of v) {
    x = String(x).replace(/^[ ."']+|[ ."']+$/g, "");
    if (x && !NULLS.includes(x.toLowerCase()) && !seen.has(x.toLowerCase())) { out.push(x); seen.add(x.toLowerCase()); }
  }
  return out;
}

export function normalise(raw: any, now: Date): Fields {
  const out: Fields = {};
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return out;
  for (const [key, v0] of Object.entries<any>(raw)) {
    const k = ALIASES[key] ?? key;
    if (!FIELDS.includes(k) || v0 == null) continue;
    if (LIST_FIELDS.includes(k)) { const l = asList(v0); if (l.length) out[k] = l; continue; }
    let v: string = Array.isArray(v0) ? v0.map(String).join(", ") : String(v0);
    v = v.trim();
    if (!v || NULLS.includes(v.toLowerCase())) continue;
    if (k in ENUMS) {
      v = v.toLowerCase().replace(/ /g, "_").replace(/-/g, "_");
      v = k === "severity" ? SEVERITY_SYNONYMS[v] ?? v : TYPE_SYNONYMS[v] ?? v;
      if (!ENUMS[k].includes(v)) { if (k === "incident_type") v = "other"; else continue; }
    }
    if (k === "location") v = v.replace(/\s*,\s*/g, " ").trim();
    if (k === "datetime") {
      const iso = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(v) ? v.slice(0, 16) : parseTime(v, now);
      if (!iso) continue;
      v = iso;
    }
    out[k] = v;
  }
  return out;
}

export const missing = (draft: Fields) => REQUIRED.filter((f) => !draft[f]);

// ---- grounding / evidence ----
const TIME_EVIDENCE = /\d|noon|midnight|o'clock|quarter|half past|\bago\b|yesterday|\bjust now\b|\bearlier\b|\bhalf an hour\b/i;
const STOP = new Set(["the", "a", "an", "of", "in", "at", "on", "and", "has", "been", "was", "were", "is", "to", "by", "it"]);
export const toks = (text: string): string[] => text.toLowerCase().match(/[a-z0-9]+/g) ?? [];

/** Python difflib.SequenceMatcher(None, a, b).ratio() (Ratcliff/Obershelp). */
export function ratio(a: string, b: string): number {
  const matched = (alo: number, ahi: number, blo: number, bhi: number): number => {
    let best = 0, bi = alo, bj = blo;
    for (let i = alo; i < ahi; i++) for (let j = blo; j < bhi; j++) {
      let k = 0; while (i + k < ahi && j + k < bhi && a[i + k] === b[j + k]) k++;
      if (k > best) { best = k; bi = i; bj = j; }
    }
    if (!best) return 0;
    return best + matched(alo, bi, blo, bj) + matched(bi + best, ahi, bj + best, bhi);
  };
  return a.length + b.length === 0 ? 1 : (2 * matched(0, a.length, 0, b.length)) / (a.length + b.length);
}
const wmatch = (w: string, words: Set<string>) => words.has(w) || (w.length > 3 && [...words].some((x) => ratio(w, x) >= 0.85));
const coverage = (value: string, words: Set<string>) => {
  const content = toks(value).filter((w) => !STOP.has(w));
  return content.length === 0 ? 1 : content.filter((w) => wmatch(w, words)).length / content.length;
};

/** The model proposes, the code verifies: drop any literal value the person's words do not support. */
export function ground(fields: Fields, text: string, ai = false): Fields {
  const out: Fields = { ...fields };
  const words = new Set(toks(text));
  if ("datetime" in out && !TIME_EVIDENCE.test(text)) delete out.datetime;
  if (!ai && out.incident_type === "fire") {
    const t2 = text.toLowerCase().replace(/fire\s+(department|dept|brigade|station|truck|engines?|fighters?|extinguishers?|exit|drill|alarm|marshal|warden|safety)\b/g, " ");
    if (!/\b(fire|smoke|flames?|burning|burnt)\b/.test(t2)) delete out.incident_type;
  }
  const sev = ai ? null : severityFromText(text);
  if (sev) out.severity = sev;
  for (const k of [...EVIDENCE_FIELDS, "follow_up"]) {
    const v = out[k];
    if (!v) continue;
    if (LIST_FIELDS.includes(k)) {
      const kept = (v as string[]).filter((x) => coverage(x, words) >= 0.5);
      if (kept.length) out[k] = kept; else delete out[k];
    } else if (coverage(v, words) < 0.5) delete out[k];
  }
  return out;
}

export function evidence(fields: Fields, text: string): Record<string, { status: string; spans: number[][] }> {
  const tk = [...text.matchAll(/[A-Za-z0-9]+/g)].map((m) => ({ t: m[0].toLowerCase(), s: m.index!, e: m.index! + m[0].length }));
  const words = new Set(tk.map((x) => x.t));
  const res: Record<string, { status: string; spans: number[][] }> = {};
  for (const k of EVIDENCE_FIELDS) {
    const v = fields[k];
    if (!v) continue;
    const spans: number[][] = []; let worst = 1;
    for (const x of Array.isArray(v) ? v : [v]) {
      const content = toks(x).filter((w) => !STOP.has(w));
      worst = Math.min(worst, coverage(x, words));
      const pos = tk.filter((o) => content.includes(o.t) || (o.t.length > 3 && content.some((c) => ratio(o.t, c) >= 0.85)));
      if (pos.length) spans.push([Math.min(...pos.map((p) => p.s)), Math.max(...pos.map((p) => p.e))]);
    }
    res[k] = { status: worst >= 0.85 ? "ok" : "unverified", spans };
  }
  return res;
}

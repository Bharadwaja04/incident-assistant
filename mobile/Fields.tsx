// The form page: the fields the AI extracted. Opens after "Stop & Process" (runs the AI) or from a Recent Incident (saved fields).
import React, { useEffect, useRef, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";
import { Session } from "./src/core/session.ts";
import { ex, startRec, stopAndTranscribe, speak, stopSpeaking } from "./core.ts";
import { GREEN, GRAY, INK, LINE, RED, BackBar, Header, formatWhen, prettyType } from "./ui.tsx";

const ROWS: [string, string][] = [["incident_type", "Incident Type"], ["severity", "Severity"], ["location", "Location"], ["datetime", "Date & Time"],
  ["description", "Description"], ["persons_involved", "People Involved"], ["equipment", "Equipment"], ["witnesses", "Witnesses"],
  ["immediate_actions", "Immediate Actions"], ["follow_up", "Follow-up"]];
const whenText = (v: any) => (v ? String(v).replace("T", " ").slice(0, 16) : "");
const show = (k: string, v: any) => (v == null ? "" : k === "datetime" ? formatWhen(v) : k === "incident_type" || k === "severity" ? prettyType(v) : Array.isArray(v) ? v.join(", ") : String(v));

export default function Fields({ heard, saved, readOnly, onBack, onDone }: {
  heard?: string; saved?: any; readOnly?: boolean; onBack: () => void; onDone: (fields: any, status: "Draft" | "Submitted", heard?: string) => void }) {
  const session = useRef(new Session(ex, null)).current;
  const [draft, setDraft] = useState<any>(saved || {});
  const [ask, setAsk] = useState("");                  // the AI's follow-up question
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(!!heard);
  const [rec, setRec] = useState(false);
  const [err, setErr] = useState("");
  const [said, setSaid] = useState(heard || "");
  const [tmp, setTmp] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [secs, setSecs] = useState<number | null>(null);

  const run = async (text: string) => {
    setBusy(true); setErr(""); const t0 = Date.now();
    try {
      const r: any = await session.turn({ text });
      setDraft(r.draft || {}); setAsk(r.ready ? "" : r.message || ""); setAnswer("");
      setSaid((x) => (x ? x + " " : "") + text);
    } catch (e: any) { setErr("Error: " + String(e?.message || e)); }
    setSecs((Date.now() - t0) / 1000); setBusy(false);
  };
  useEffect(() => { if (heard) run(heard); else if (saved) session.draft = { ...saved }; }, []);
  const change = async (text: string) => {                      // a spoken or typed correction to the report
    setBusy(true); setErr(""); setNote("");
    try {
      const r: any = await session.voiceEdit(text);
      setDraft({ ...session.draft }); setTxt({}); setNote(r.message || ""); setAnswer(""); setTimeout(() => setNote(""), 4000);
    } catch (e: any) { setErr("Error: " + String(e?.message || e)); }
    setBusy(false);
  };
  const send = () => (ask ? run(answer) : change(answer));

  const mic = async () => {
    try {
      if (!rec) { await startRec(); setRec(true); return; }
      setRec(false); setBusy(true); const t = await stopAndTranscribe(); setBusy(false); setAnswer((a) => (a ? a + " " : "") + t);
    } catch (e: any) { setErr("Mic error: " + String(e?.message || e)); setRec(false); setBusy(false); }
  };
  const edit = (k: string, v: string) => { session.draft = { ...session.draft, [k]: v }; setDraft((d: any) => ({ ...d, [k]: v })); };
  const editable = !readOnly;
  const [talking, setTalking] = useState(false);
  useEffect(() => () => { stopSpeaking(); }, []);
  const readAloud = async () => {                                    // the Piper model on the phone turns each field into speech
    if (talking) { stopSpeaking(); setTalking(false); return; }
    const parts = ROWS.map(([k, label]) => (show(k, draft[k]) ? `${label}: ${show(k, draft[k])}.` : "")).filter(Boolean);
    if (!parts.length) return;
    setTalking(true); setErr("");
    try { await speak(["Incident report.", ...parts]); }
    catch (e: any) { setErr("Speech error: " + String(e?.message || e)); }
    setTalking(false);
  };
  const pick = (k: string, v: string) => { session.draft = { ...session.draft, [k]: v }; setDraft((d: any) => ({ ...d, [k]: v })); };
  const [txt, setTxt] = useState<Record<string, string>>({});       // what is typed in the box right now
  const DT = /^(\d{4}-\d{2}-\d{2})[ T](([01]\d|2[0-3]):[0-5]\d)$/;
  const finish = async (status: "Draft" | "Submitted") => {         // apply any typed-but-unapplied change first, then hand over the latest fields
    if (txt.datetime !== undefined && txt.datetime.trim() && !DT.test(txt.datetime.trim())) { setErr("Date & Time should look like 2026-10-10 20:35."); return; }
    onDone({ ...session.draft }, status, said);
  };

  return (
    <View style={s.root}>
      <Header />
      <BackBar title={saved ? "Incident Details" : "Review Report"} onBack={onBack} />
      <ScrollView contentContainerStyle={{ padding: 18, paddingBottom: 40 }} keyboardShouldPersistTaps="handled">
        {!!err && <Text selectable style={s.err}>{err}</Text>}
        {!!ask && <View style={s.ask}><Text style={s.askT}>{ask}</Text></View>}
        {editable && (
          <View style={s.box}>
            <Text style={s.label}>{ask ? "Your answer" : "Change something? Speak or type it"}</Text>
            <View style={{ flexDirection: "row" }}>
              <TextInput style={[s.in, { flex: 1 }]} value={answer} onChangeText={setAnswer} placeholder={ask ? "Type or speak your answer" : "e.g. the location is Block 5"} placeholderTextColor="#9ca3af" />
              <TouchableOpacity onPress={mic} style={[s.mic, rec && { backgroundColor: RED }]}><Text style={s.micT}>{rec ? "■" : "🎤"}</Text></TouchableOpacity>
            </View>
            <TouchableOpacity style={[s.btn, !answer.trim() && { opacity: 0.5 }]} disabled={!answer.trim()} onPress={send}><Text style={s.btnT}>{ask ? "Send answer" : "Apply change"}</Text></TouchableOpacity>
          </View>
        )}
        <TouchableOpacity activeOpacity={0.85} onPress={readAloud} style={[s.read, talking && { backgroundColor: RED }]}>
          <Text style={s.readT}>{talking ? "■  Stop reading" : "🔊  Read report aloud"}</Text>
        </TouchableOpacity>
        {ROWS.map(([k, label]) => (
          <View key={k} style={s.field}>
            <Text style={s.label}>{label}</Text>
            {!editable ? <Text style={s.val}>{show(k, draft[k]) || "Not given"}</Text>
              : k === "incident_type" || k === "severity" ? (
                <TextInput style={s.in} value={txt[k] ?? show(k, draft[k])} placeholder="Not given" placeholderTextColor="#9ca3af"
                  onChangeText={(v) => { setTxt((t) => ({ ...t, [k]: v })); pick(k, v.trim().toLowerCase().replace(/\s+/g, "_")); }} />)
              : k === "datetime" ? (
                <View>
                  <TextInput style={s.in} value={txt.datetime ?? whenText(draft.datetime)} placeholder="YYYY-MM-DD HH:MM  (24-hour)" placeholderTextColor="#9ca3af"
                    keyboardType="numbers-and-punctuation" onChangeText={(v) => { setTxt((t) => ({ ...t, datetime: v })); const m = DT.exec(v.trim()); if (m) pick("datetime", `${m[1]}T${m[2]}`); else if (!v.trim()) pick("datetime", ""); }} />
                  {!!draft.datetime && <Text style={s.small}>{formatWhen(draft.datetime)}</Text>}
                </View>)
              : <TextInput style={s.in} multiline={k === "description"} value={show(k, draft[k])} onChangeText={(v) => edit(k, v)} placeholder="Not given" placeholderTextColor="#9ca3af" />}
          </View>
        ))}
        {secs !== null && <Text style={s.small}>AI time: {secs.toFixed(1)} s</Text>}
        {!readOnly && (
          <View style={{ flexDirection: "row", marginTop: 18 }}>
            <TouchableOpacity style={[s.btn, s.ghost, { flex: 1, marginRight: 8 }]} onPress={() => finish("Draft")}><Text style={[s.btnT, { color: INK }]}>Save Draft</Text></TouchableOpacity>
            <TouchableOpacity style={[s.btn, { flex: 1 }, busy && { opacity: 0.5 }]} disabled={busy} onPress={() => finish("Submitted")}><Text style={s.btnT}>Submit</Text></TouchableOpacity>
          </View>
        )}
      </ScrollView>
      {(busy || !!note) && (
        <View pointerEvents="none" style={s.toast}>
          {busy ? <><ActivityIndicator color="#fff" /><Text style={s.toastT}>  The AI is working…</Text></> : <Text style={s.toastT}>{note}</Text>}
        </View>
      )}
    </View>
  );
}
const s = StyleSheet.create({
  toast: { position: "absolute", bottom: 24, left: 20, right: 20, backgroundColor: "#0F172A", borderRadius: 14, paddingVertical: 12, paddingHorizontal: 16, flexDirection: "row", alignItems: "center", justifyContent: "center" }, toastT: { color: "#fff", fontSize: 15, fontWeight: "600" },
  root: { flex: 1, backgroundColor: "#fff" },
  row0: { flexDirection: "row", alignItems: "center", marginBottom: 12 }, busy: { color: GRAY, fontSize: 15 },
  err: { color: RED, marginBottom: 10 },
  ask: { backgroundColor: "#ECFDF5", borderWidth: 1, borderColor: "#A7E3C0", borderRadius: 14, padding: 14, marginBottom: 14 }, askT: { color: INK, fontSize: 16, fontWeight: "600" },
  box: { backgroundColor: "#F8FAFC", borderWidth: 1, borderColor: LINE, borderRadius: 14, padding: 12, marginBottom: 14 }, note: { color: GREEN, marginTop: 8, fontWeight: "600" },
  chips: { flexDirection: "row", flexWrap: "wrap" }, opt: { borderWidth: 1, borderColor: LINE, borderRadius: 18, paddingHorizontal: 12, paddingVertical: 7, marginRight: 8, marginBottom: 8, backgroundColor: "#fff" }, optOn: { backgroundColor: GREEN, borderColor: GREEN }, optT: { color: INK, fontSize: 14 },
  read: { backgroundColor: "#0F172A", borderRadius: 12, paddingVertical: 12, alignItems: "center", marginBottom: 14 }, readT: { color: "#fff", fontWeight: "700", fontSize: 15 },
  field: { marginBottom: 12 }, label: { fontSize: 13, color: GRAY, marginBottom: 4 },
  in: { color: INK, backgroundColor: "#fff", borderWidth: 1, borderColor: LINE, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 9, fontSize: 16, textAlignVertical: "top" },
  val: { color: INK, fontSize: 16, backgroundColor: "#F8FAFC", borderWidth: 1, borderColor: LINE, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10 },
  mic: { width: 44, height: 44, borderRadius: 22, backgroundColor: GREEN, alignItems: "center", justifyContent: "center", marginLeft: 8 }, micT: { color: "#fff", fontSize: 18 },
  btn: { backgroundColor: GREEN, borderRadius: 12, paddingVertical: 13, alignItems: "center", marginTop: 10 }, btnT: { color: "#fff", fontWeight: "700", fontSize: 16 },
  ghost: { backgroundColor: "#fff", borderWidth: 1, borderColor: LINE }, small: { color: GRAY, fontSize: 12, marginTop: 4 },
});

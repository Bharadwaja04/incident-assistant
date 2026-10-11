// The report screen: editable transcript, Send to the on-device AI, the draft report and what the AI decided.
import React, { useRef, useState } from "react";
import { ScrollView, StatusBar, StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";
import { Session } from "./src/core/session.ts";
import { ex, startRec, stopAndTranscribe } from "./core.ts";

export default function Report({ initialText, onBack }: { initialText: string; onBack: () => void }) {
  const session = useRef(new Session(ex, null)).current;
  const [text, setText] = useState(initialText);
  const [msg, setMsg] = useState("Check the text below, fix any word, then tap Send.");
  const [draft, setDraft] = useState<any>({});
  const [dbg, setDbg] = useState("");
  const [busy, setBusy] = useState(false);
  const [rec, setRec] = useState(false);
  const [secs, setSecs] = useState<number | null>(null);
  const [asrSecs, setAsrSecs] = useState<number | null>(null);

  const mic = async () => {                         // tap to start, tap again to stop (it never stops by itself)
    try {
      if (!rec) { await startRec(); setRec(true); return; }
      setRec(false); setBusy(true); const t0 = Date.now();
      const heard = await stopAndTranscribe();
      setAsrSecs((Date.now() - t0) / 1000);
      setText((old) => (old ? old + " " : "") + heard);
    } catch (e: any) { setMsg("Mic error: " + String(e?.message || e)); setRec(false); }
    setBusy(false);
  };
  const send = async () => {
    if (!text.trim() || busy) return;
    setBusy(true); const t0 = Date.now();
    try {
      const r: any = await session.turn({ text });
      setMsg(r.message || (r.ready ? "Ready for review." : ""));
      setDraft(r.draft || {});
      setDbg(JSON.stringify({ heard: text, ai: r.aiTrace, missing: r.aiMissing }, null, 1));
      setText("");
    } catch (e: any) { setMsg("Error: " + String(e?.message || e)); }
    setSecs((Date.now() - t0) / 1000); setBusy(false);
  };

  return (
    <View style={s.root}>
      <ScrollView contentContainerStyle={{ padding: 16 }}>
        <TouchableOpacity onPress={onBack}><Text style={s.back}>‹ Home</Text></TouchableOpacity>
        <Text style={s.h}>New incident report</Text>
        <Text style={s.msg}>{msg}</Text>
        <TextInput style={s.input} value={text} onChangeText={setText} multiline placeholderTextColor="#9ca3af" placeholder="What happened?" />
        <TouchableOpacity style={[s.btn, { backgroundColor: rec ? "#dc2626" : "#047857", marginBottom: 8 }, busy && { opacity: 0.5 }]} onPress={mic} disabled={busy}>
          <Text style={s.bt}>{rec ? "Recording… tap to stop" : "Tap to speak"}</Text></TouchableOpacity>
        {asrSecs !== null && <Text style={s.t}>Speech to text: {asrSecs.toFixed(1)} s</Text>}
        <TouchableOpacity style={[s.btn, busy && { opacity: 0.5 }]} onPress={send}><Text style={s.bt}>{busy ? "Thinking…" : "Send"}</Text></TouchableOpacity>
        {secs !== null && <Text style={s.t}>Last turn: {secs.toFixed(1)} s</Text>}
        <View style={s.box}><Text style={s.mono}>{JSON.stringify(draft, null, 2)}</Text></View>
        <Text style={[s.t, { marginTop: 12 }]}>What the AI decided (long-press to copy):</Text>
        <View style={s.box}><Text selectable style={s.mono}>{dbg}</Text></View>
      </ScrollView>
    </View>
  );
}
const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#fff", paddingTop: (StatusBar.currentHeight ?? 24) + 6 },
  back: { color: "#047857", fontSize: 16, fontWeight: "600", marginBottom: 8 },
  h: { color: "#111827", fontSize: 20, fontWeight: "700", marginBottom: 10 }, msg: { color: "#111827", fontSize: 16, marginBottom: 10 },
  input: { color: "#111827", backgroundColor: "#ffffff", textAlignVertical: "top", borderWidth: 1, borderColor: "#999", borderRadius: 8, padding: 10, minHeight: 80, marginBottom: 10 },
  btn: { backgroundColor: "#1a56db", padding: 12, borderRadius: 8, alignItems: "center" }, bt: { color: "#fff", fontWeight: "700" },
  t: { marginTop: 8, color: "#4b5563" }, box: { marginTop: 12, backgroundColor: "#f3f4f6", padding: 10, borderRadius: 8 }, mono: { color: "#111827", fontFamily: "monospace", fontSize: 12 },
});

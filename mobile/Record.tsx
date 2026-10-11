// Recording screen: starts recording as soon as it opens, shows the timer, and "Stop & Process" turns the speech into text.
import React, { useEffect, useRef, useState } from "react";
import { Animated, Easing, StatusBar, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import { GREEN, GRAY, INK, RED, BackBar, Header, MicIcon } from "./ui.tsx";
import { cancelRec, startRec, stopAndTranscribe } from "./core.ts";

export default function Record({ onBack, onStop }: { onBack: () => void; onStop: (text: string) => void }) {
  const [state, setState] = useState<"starting" | "recording" | "processing">("starting");
  const [secs, setSecs] = useState(0);
  const [err, setErr] = useState("");
  const started = useRef(false), alive = useRef(true);
  const pulse = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    alive.current = true;
    if (!started.current) { started.current = true; startRec().then(() => alive.current && setState("recording")).catch((e) => setErr(String(e?.message || e))); }
    return () => { alive.current = false; };
  }, []);
  useEffect(() => {
    if (state !== "recording") return;
    const t = setInterval(() => setSecs((x) => x + 1), 1000);
    const loop = Animated.loop(Animated.timing(pulse, { toValue: 1, duration: 1600, easing: Easing.out(Easing.quad), useNativeDriver: true }));
    loop.start();
    return () => { clearInterval(t); loop.stop(); };
  }, [state]);

  const stop = async () => {                                     // recording stops only when the person taps this
    if (state !== "recording") return;
    setState("processing");
    try { onStop(await stopAndTranscribe()); }
    catch (e: any) { setErr(String(e?.message || e)); setState("recording"); }
  };
  const back = async () => { if (state === "recording") await cancelRec(); onBack(); };
  const grow = pulse.interpolate({ inputRange: [0, 1], outputRange: [1, 1.35] }), fade = pulse.interpolate({ inputRange: [0, 1], outputRange: [0.35, 0] });
  const mm = String(Math.floor(secs / 60)).padStart(2, "0"), ss = String(secs % 60).padStart(2, "0");

  return (
    <View style={s.root}>
      <StatusBar barStyle="dark-content" backgroundColor="#fff" />
      <Header />
      <BackBar title="Report an Incident" onBack={back} />
      <View style={s.center}>
        <View style={s.micWrap}>
          <Animated.View style={[s.ring, { width: 250, height: 250, borderRadius: 125, backgroundColor: "#BFE9D0", opacity: fade, transform: [{ scale: grow }] }]} />
          <View style={[s.ring, { width: 250, height: 250, borderRadius: 125, backgroundColor: "#EAF8F0" }]} />
          <View style={[s.ring, { width: 200, height: 200, borderRadius: 100, backgroundColor: "#D8F2E3" }]} />
          <View style={[s.ring, { width: 160, height: 160, borderRadius: 80, backgroundColor: "#BFE9D0" }]} />
          <View style={s.mic}><MicIcon size={58} /></View>
        </View>
        <Text style={s.timer}>{mm}:{ss}</Text>
        <Text style={s.hint}>{state === "processing" ? "Converting your speech to text…" : state === "starting" ? "Getting the microphone ready…" : "Speak clearly and describe what happened, where it happened, and any danger you noticed."}</Text>
        {!!err && <Text style={s.err}>{err}</Text>}
      </View>
      <View style={s.bottom}>
        <TouchableOpacity activeOpacity={0.85} onPress={stop} disabled={state !== "recording"} style={[s.pill, state !== "recording" && { opacity: 0.55 }]}>
          <View style={s.stopDot}><View style={s.stopSq} /></View>
          <Text style={s.pillT}>{state === "processing" ? "Processing…" : "Stop & Process"}</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}
const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#fff" },
  center: { flex: 1, alignItems: "center", paddingHorizontal: 28 },
  micWrap: { height: 290, width: 290, alignItems: "center", justifyContent: "center", marginTop: 14 },
  ring: { position: "absolute" },
  mic: { width: 124, height: 124, borderRadius: 62, backgroundColor: GREEN, alignItems: "center", justifyContent: "center", borderWidth: 4, borderColor: "#fff", elevation: 6 },
  timer: { fontSize: 44, fontWeight: "800", color: INK, marginTop: 4, letterSpacing: 1 },
  hint: { fontSize: 16, color: GRAY, textAlign: "center", marginTop: 14, lineHeight: 24 },
  err: { color: RED, textAlign: "center", marginTop: 10 },
  bottom: { paddingHorizontal: 40, paddingBottom: 56, alignItems: "center" },
  pill: { flexDirection: "row", alignItems: "center", backgroundColor: "#FDECEC", borderRadius: 40, paddingVertical: 14, paddingHorizontal: 24, minWidth: 250, justifyContent: "center" },
  stopDot: { width: 50, height: 50, borderRadius: 25, backgroundColor: "#E53935", alignItems: "center", justifyContent: "center", marginRight: 18 },
  stopSq: { width: 20, height: 20, borderRadius: 4, backgroundColor: "#fff" }, pillT: { fontSize: 20, fontWeight: "700", color: INK },
});

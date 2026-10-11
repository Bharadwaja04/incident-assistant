// Landing screen: header, current site, the big record button, and the recent incidents.
import React from "react";
import { ScrollView, StatusBar, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import { GREEN, GRAY, INK, LINE, Header, MicIcon } from "./ui.tsx";

export interface Incident { id: number; title: string; place: string; status: "Draft" | "Submitted"; when: string; fields: any; heard?: string }
const CHIP: Record<string, { bg: string; fg: string }> = {
  Draft: { bg: "#FEF3C7", fg: "#B7791F" }, Submitted: { bg: "#FEE2E2", fg: "#DC2626" },
};

export default function Home({ recent, onRecord, onOpen }: { recent: Incident[]; onRecord: () => void; onOpen: (i: Incident) => void }) {
  return (
    <View style={s.root}>
      <StatusBar barStyle="dark-content" backgroundColor="#fff" />
      <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 30 }}>
        <Header />
        <View style={{ paddingHorizontal: 18 }}>
          <View style={s.micWrap}>
            <View style={[s.halo, { backgroundColor: "#E9F8EF", width: 190, height: 190, borderRadius: 95 }]} />
            <View style={[s.halo, { backgroundColor: "#D3F1DF", width: 150, height: 150, borderRadius: 75 }]} />
            <TouchableOpacity activeOpacity={0.85} onPress={onRecord} style={s.mic}><MicIcon size={62} /></TouchableOpacity>
          </View>
          <Text style={s.title}>Report an Incident</Text>
          <Text style={s.sub}>Tap the microphone to describe what happened</Text>

          <Text style={s.h2}>Recent Incidents</Text>
          {recent.map((r) => (
            <TouchableOpacity key={r.id} activeOpacity={0.8} style={s.card} onPress={() => onOpen(r)}>
              <View style={{ flex: 1 }}>
                <Text style={s.cardT}>{r.title}</Text>
                <Text style={s.cardP}>{r.place}</Text>
              </View>
              <View style={{ alignItems: "flex-end", marginRight: 8 }}>
                <View style={[s.chip, { backgroundColor: (CHIP[r.status] ?? CHIP.Draft).bg }]}><Text style={[s.chipT, { color: (CHIP[r.status] ?? CHIP.Draft).fg }]}>{r.status}</Text></View>
                <Text style={s.cardW}>{r.when}</Text>
              </View>
              <Text style={s.chev}>›</Text>
            </TouchableOpacity>
          ))}
        </View>
      </ScrollView>
    </View>
  );
}
const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#fff" },
  site: { flexDirection: "row", alignItems: "center", backgroundColor: "#F8FAFC", borderWidth: 1, borderColor: LINE, borderRadius: 14, paddingVertical: 12, paddingHorizontal: 16, marginTop: 18 },
  siteL: { fontSize: 13, color: GRAY }, siteV: { fontSize: 17, fontWeight: "600", color: INK, marginTop: 2 },
  chev: { fontSize: 28, color: GRAY, marginLeft: 6, lineHeight: 30 },
  micWrap: { height: 250, alignItems: "center", justifyContent: "center", marginTop: 18 },
  halo: { position: "absolute", width: 120, height: 120, borderRadius: 60 },
  mic: { width: 112, height: 112, borderRadius: 56, alignItems: "center", justifyContent: "center", backgroundColor: GREEN, elevation: 8, shadowColor: GREEN, shadowOpacity: 0.4, shadowRadius: 14 },
  title: { fontSize: 24, fontWeight: "700", color: INK, textAlign: "center", marginTop: 4 },
  sub: { fontSize: 15, color: GRAY, textAlign: "center", marginTop: 6 },
  h2: { fontSize: 21, fontWeight: "700", color: INK, marginTop: 34, marginBottom: 10 },
  card: { flexDirection: "row", alignItems: "center", borderWidth: 1, borderColor: LINE, borderRadius: 14, padding: 14, marginBottom: 10, backgroundColor: "#fff" },
  cardT: { fontSize: 17, fontWeight: "600", color: INK }, cardP: { fontSize: 14, color: GRAY, marginTop: 3 },
  chip: { paddingHorizontal: 12, paddingVertical: 4, borderRadius: 10 }, chipT: { fontSize: 14, fontWeight: "500" }, cardW: { fontSize: 13, color: GRAY, marginTop: 6 },
});

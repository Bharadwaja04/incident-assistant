// Shared look: colours, the logo, the mic icon, the header and the back bar.
import React from "react";
import { StatusBar, StyleSheet, Text, TouchableOpacity, View } from "react-native";

export const GREEN = "#0F9D58", INK = "#0F172A", GRAY = "#64748B", LINE = "#E5E7EB", RED = "#DC2626";
export const TOP = (StatusBar.currentHeight ?? 24) + 4;

export function Shield({ scale = 1 }: { scale?: number }) {       // the logo: a green shield with a white ring and slash
  return (
    <View style={{ width: 44 * scale, height: 50 * scale, alignItems: "center", justifyContent: "center" }}>
      <View style={{ position: "absolute", width: 44 * scale, height: 50 * scale, backgroundColor: GREEN, borderTopLeftRadius: 8 * scale, borderTopRightRadius: 8 * scale, borderBottomLeftRadius: 24 * scale, borderBottomRightRadius: 24 * scale }} />
      <View style={{ width: 22 * scale, height: 22 * scale, borderRadius: 11 * scale, borderWidth: 3 * scale, borderColor: "#fff" }} />
      <View style={{ position: "absolute", width: 4 * scale, height: 34 * scale, backgroundColor: "#fff", borderRadius: 2, transform: [{ rotate: "40deg" }] }} />
    </View>
  );
}
export function MicIcon({ size = 64, color = "#fff" }: { size?: number; color?: string }) {
  const w = size * 0.36, h = size * 0.58, t = size * 0.07;
  return (
    <View style={{ alignItems: "center", height: size }}>
      <View style={{ width: w, height: h, borderRadius: w / 2, backgroundColor: color }} />
      <View style={{ position: "absolute", top: h * 0.45, width: size * 0.62, height: size * 0.42, borderWidth: t, borderTopWidth: 0, borderColor: color, borderBottomLeftRadius: size * 0.31, borderBottomRightRadius: size * 0.31 }} />
      <View style={{ position: "absolute", top: h * 0.45 + size * 0.42, width: t, height: size * 0.1, backgroundColor: color }} />
      <View style={{ position: "absolute", top: h * 0.45 + size * 0.52, width: size * 0.32, height: t, borderRadius: t / 2, backgroundColor: color }} />
    </View>
  );
}
export function Header() {
  return (
    <View style={u.header}>
      <Shield />
      <View style={{ marginLeft: 10, flex: 1 }}>
        <Text style={u.brand}>PlantGuard <Text style={{ color: GREEN }}>AI</Text></Text>
        <Text style={u.tag}>Your Safety. Our Priority.</Text>
      </View>
    </View>
  );
}
export function BackBar({ title, onBack }: { title: string; onBack: () => void }) {
  return (
    <TouchableOpacity activeOpacity={0.7} onPress={onBack} style={u.back}>
      <Text style={u.backArrow}>‹</Text><Text style={u.backT}>{title}</Text>
    </TouchableOpacity>
  );
}
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** "2026-10-10T20:35" -> "Yesterday, 8:35 PM" (formatting only). */
export function formatWhen(iso?: string | null, now = new Date()): string {
  const m = iso ? /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(iso)) : null;
  if (!m) return "Time not given";
  const d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
  const day0 = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day0(now) - day0(d)) / 86400000);
  const h = d.getHours() % 12 || 12, mm = String(d.getMinutes()).padStart(2, "0"), ap = d.getHours() >= 12 ? "PM" : "AM";
  const date = diff === 0 ? "Today" : diff === 1 ? "Yesterday" : `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
  return `${date}, ${h}:${mm} ${ap}`;
}
export const prettyType = (t?: string | null) => (t ? String(t).split("_").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ") : "");
const u = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: 18, paddingTop: TOP + 6 },
  brand: { fontSize: 28, fontWeight: "800", color: INK, letterSpacing: -0.5 }, tag: { fontSize: 13, color: GRAY, marginTop: 1 },
  avatar: { width: 50, height: 50, borderRadius: 25, backgroundColor: "#DCFCE7", alignItems: "center", justifyContent: "center" },
  avatarT: { fontSize: 17, fontWeight: "600", color: INK }, user: { fontSize: 12, color: INK, marginTop: 3 },
  back: { flexDirection: "row", alignItems: "center", paddingHorizontal: 18, paddingVertical: 8, marginTop: 14 },
  backArrow: { fontSize: 34, color: GRAY, marginRight: 14, lineHeight: 36 }, backT: { fontSize: 19, fontWeight: "700", color: INK },
});

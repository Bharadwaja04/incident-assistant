// Measures speech-to-text (word error rate, speed) on eval/audio clips and text-to-speech (time to audio, real-time factor), on the phone.
// usage (phone servers running, after reset_board.sh and tts/start_tts.sh): node --experimental-strip-types mobile/speech_eval.ts
import { readFileSync, readdirSync } from "node:fs";
const dir = new URL("../eval/audio/", import.meta.url);
const words = (t: string) => t.toLowerCase().replace(/a\.m\./g, "am").replace(/p\.m\./g, "pm").replace(/(\d)\s+(am|pm)\b/g, "$1$2").replace(/[^a-z0-9: ]/g, " ").replace(/(?<![0-9]):|:(?![0-9])/g, " ").split(/\s+/).filter(Boolean);
const wer = (ref: string, hyp: string) => {
  const r = words(ref), h = words(hyp); let d = Array.from({ length: h.length + 1 }, (_, j) => j);
  for (let i = 1; i <= r.length; i++) { let prev = d[0]; d[0] = i; for (let j = 1; j <= h.length; j++) { const t = d[j]; d[j] = Math.min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] === h[j - 1] ? 0 : 1)); prev = t; } }
  return r.length ? d[h.length] / r.length : 0;
};
const tags = (t: string) => t.replace(/\[[^\]]*\]|\([^)]*\)/g, " ").replace(/\s+/g, " ").trim();      // what the app strips (blank-audio and noise tags)
const wavSecs = (b: Buffer) => (b.length - 44) / (b.readUInt32LE(24) * 2);
console.log("=== SPEECH TO TEXT (Whisper base) ===");
let rawSum = 0, cleanSum = 0, n = 0, audio = 0, took = 0;
for (const f of readdirSync(dir).filter((x) => x.endsWith(".wav")).sort()) {
  const wav = readFileSync(new URL(f, dir)), ref = readFileSync(new URL(f.replace(".wav", ".txt"), dir), "utf8").trim();
  const fd = new FormData(); fd.append("file", new Blob([wav], { type: "audio/wav" }), f); fd.append("response_format", "json"); fd.append("temperature", "0");
  const t0 = Date.now(); const r: any = await (await fetch("http://127.0.0.1:8090/inference", { method: "POST", body: fd })).json(); const s = (Date.now() - t0) / 1000;
  const raw = String(r.text ?? "").trim(), clean = tags(raw), a = wavSecs(wav);
  const w1 = wer(ref, raw), w2 = wer(ref, clean); rawSum += w1; cleanSum += w2; n++; audio += a; took += s;
  console.log(`${f}  ${a.toFixed(1)} s audio -> ${s.toFixed(2)} s | WER raw ${(w1 * 100).toFixed(0)}%  after tag filter ${(w2 * 100).toFixed(0)}%`);
  if (w2 > 0) console.log(`   want: ${ref}\n   got : ${clean}`);
}
console.log(`MEAN WER  raw ${((rawSum / n) * 100).toFixed(1)}%  |  after filter ${((cleanSum / n) * 100).toFixed(1)}%   | speed ${(audio / took).toFixed(1)}x real time (${took.toFixed(1)} s for ${audio.toFixed(1)} s of audio)`);
console.log("\n=== TEXT TO SPEECH (Piper lessac medium int8) ===");
const lines = ["Incident report.", "Incident type: Fire. Severity: High.", "Location: Block B second floor near motor M12. Description: Smoke was coming from motor M12 and two workers left the area."];
for (const t of lines) {
  const t0 = Date.now(); const b = Buffer.from(await (await fetch("http://127.0.0.1:8100/?t=" + encodeURIComponent(t))).arrayBuffer()); const s = (Date.now() - t0) / 1000, a = wavSecs(b);
  console.log(`${t.length} chars -> ${a.toFixed(1)} s of speech in ${s.toFixed(2)} s | real-time factor ${(s / a).toFixed(2)} (below 1 is faster than real time)`);
}

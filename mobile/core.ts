// Shared by the screens: the on-device AI (Qwen on 127.0.0.1:8080) and speech to text (Whisper on 127.0.0.1:8090). Nothing leaves the phone.
import { PermissionsAndroid } from "react-native";
import AudioRecord from "react-native-audio-record";
import { Extractor } from "./src/core/extractor.ts";

export const ex = new Extractor({ baseUrl: "http://127.0.0.1:8080/v1", model: "qwen3", mode: "tools" });

const WHISPER = "http://127.0.0.1:8090/inference";
let audioReady = false;
export async function startRec() {
  const ok = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.RECORD_AUDIO);
  if (ok !== PermissionsAndroid.RESULTS.GRANTED) throw new Error("Microphone permission denied");
  if (!audioReady) { AudioRecord.init({ sampleRate: 16000, channels: 1, bitsPerSample: 16, audioSource: 6, wavFile: "report.wav" }); audioReady = true; }
  AudioRecord.start();
}
export async function stopAndTranscribe(): Promise<string> {
  const path = await AudioRecord.stop();            // 16 kHz mono WAV
  const form = new FormData();
  form.append("file", { uri: "file://" + path, type: "audio/wav", name: "report.wav" } as any);
  form.append("response_format", "json"); form.append("language", "en"); form.append("temperature", "0.0");
  const j: any = await new Promise((resolve, reject) => {       // XMLHttpRequest accepts a file part; Expo's fetch does not
    const x = new XMLHttpRequest();
    x.open("POST", WHISPER);
    x.onload = () => { try { resolve(JSON.parse(x.responseText)); } catch { reject(new Error("Whisper replied: " + x.responseText.slice(0, 80))); } };
    x.onerror = () => reject(new Error("Could not reach Whisper on the phone (port 8090)"));
    x.timeout = 120000; x.ontimeout = () => reject(new Error("Whisper timed out"));
    x.send(form);
  });
  return String(j.text || "").replace(/\[[A-Z _]+\]|\([a-z ]+\)/g, " ").replace(/\s+/g, " ").trim();
}
export async function cancelRec() { try { await AudioRecord.stop(); } catch {} }

// ---- Text to speech: the Piper voice model runs on the phone (tts/start_tts.sh), the app plays what it returns ----
let speakId = 0, current: any = null;
export function stopSpeaking() { speakId++; try { current?.pause(); current?.remove(); } catch {} current = null; }
/** Says each piece of text in turn (one request per piece so the first words start quickly). Resolves when finished or stopped. */
export async function speak(pieces: string[]): Promise<void> {
  stopSpeaking(); const id = speakId;
  const { createAudioPlayer } = require("expo-audio");          // loaded only here, so the rest of the app works even before the rebuild
  for (const p of pieces) {
    if (id !== speakId) return;
    await new Promise<void>((done, fail) => {
      const player = createAudioPlayer({ uri: "http://127.0.0.1:8100/?t=" + encodeURIComponent(p) });
      current = player;
      let over = false;
      const end = (err?: any) => { if (over) return; over = true; try { sub.remove(); player.remove(); } catch {} err ? fail(err) : done(); };
      const sub = player.addListener("playbackStatusUpdate", (st: any) => { if (st.didJustFinish) end(); });
      setTimeout(() => end(new Error("The speech server did not answer (is tts/start_tts.sh running?)")), 90000);
      player.play();
    });
  }
}

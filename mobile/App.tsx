import React, { useState } from "react";
import Home, { type Incident } from "./Home.tsx";
import Record from "./Record.tsx";
import Fields from "./Fields.tsx";
import { formatWhen, prettyType } from "./ui.tsx";

// Sample incidents so the list is not empty; new reports are added at the top (kept in memory for now).
const SAMPLE: Incident[] = [
  { id: 1, title: "Lift Not Working", place: "Block A - 1st Floor", status: "Draft", when: "Today, 10:15 AM",
    fields: { incident_type: "equipment_failure", severity: "medium", location: "Block A - 1st Floor", datetime: null, description: "The lift in Block A is not working." } },
  { id: 2, title: "Smoke from Motor M12", place: "Block B - 2nd Floor", status: "Submitted", when: "Yesterday, 2:30 PM",
    fields: { incident_type: "fire", severity: "high", location: "Block B - 2nd Floor", description: "Smoke was coming from motor M12.", equipment: "Motor M12" } },
];
type Screen = { n: "home" } | { n: "record" } | { n: "new"; heard: string; k: number } | { n: "open"; item: Incident };

export default function App() {
  const [list, setList] = useState<Incident[]>(SAMPLE);
  const [scr, setScr] = useState<Screen>({ n: "home" });
  const [k, setK] = useState(0);
  if (scr.n === "record") return <Record onBack={() => setScr({ n: "home" })} onStop={(t) => { setK((x) => x + 1); setScr({ n: "new", heard: t, k }); }} />;
  if (scr.n === "new") return <Fields key={scr.k} heard={scr.heard} onBack={() => setScr({ n: "home" })}
    onDone={(f, status) => {
      const item: Incident = { id: Date.now(), title: f.description ? (prettyType(f.incident_type) || "New Incident") : "New Incident", place: f.location || "Location not given", status, when: formatWhen(f.datetime || new Date().toISOString().slice(0, 16)), fields: f };
      setList((l) => [item, ...l]); setScr({ n: "home" });
    }} />;
  if (scr.n === "open") return <Fields key={scr.item.id} saved={scr.item.fields} readOnly={scr.item.status !== "Draft"} onBack={() => setScr({ n: "home" })}
    onDone={(f, status) => { setList((l) => l.map((x) => (x.id === scr.item.id ? { ...x, status, fields: f, title: prettyType(f.incident_type) || x.title, place: f.location || x.place, when: f.datetime ? formatWhen(f.datetime) : x.when } : x))); setScr({ n: "home" }); }} />;
  return <Home recent={list} onRecord={() => setScr({ n: "record" })} onOpen={(item) => setScr({ n: "open", item })} />;
}

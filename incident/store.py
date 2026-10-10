import json, os, sqlite3, time


class Store:
    """SQLite (history list) + one JSON file per report (plan: files/reports/<timestamp>.json)."""
    def __init__(self, path="incidents.db", reports_dir="reports"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.reports_dir = reports_dir
        self.db.execute("""CREATE TABLE IF NOT EXISTS incidents(
            id INTEGER PRIMARY KEY, created_at TEXT, fields TEXT, transcript TEXT, status TEXT)""")

    def save(self, fields: dict, transcript: str, status="submitted", flags=None) -> int:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        c = self.db.execute("INSERT INTO incidents(created_at,fields,transcript,status) VALUES(?,?,?,?)",
                            (now, json.dumps(fields), transcript, status))
        self.db.commit()
        if self.reports_dir:
            os.makedirs(self.reports_dir, exist_ok=True)
            with open(os.path.join(self.reports_dir, time.strftime("%Y%m%d-%H%M%S") + f"-{c.lastrowid}.json"), "w") as f:
                json.dump({"id": c.lastrowid, "created_at": now, "report": fields, "flags": flags or {}, "transcript": transcript}, f, indent=1)
        return c.lastrowid

    def all(self):
        return [dict(id=r[0], created_at=r[1], fields=json.loads(r[2]), transcript=r[3], status=r[4])
                for r in self.db.execute("SELECT * FROM incidents ORDER BY id DESC")]

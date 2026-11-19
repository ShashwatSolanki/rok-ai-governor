from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TaskStore:
    """Persistent queue for Gemini recommendations; stores tasks, never executes them."""

    def __init__(self, database: str | Path = "data/account_history.sqlite3"):
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS governor_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL UNIQUE,
                action TEXT NOT NULL,
                category TEXT NOT NULL,
                priority TEXT NOT NULL,
                why TEXT NOT NULL,
                visual_evidence TEXT NOT NULL,
                confidence REAL,
                uncertainties_json TEXT NOT NULL,
                manual_steps_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                seen_count INTEGER NOT NULL DEFAULT 1,
                source_screenshot TEXT
            )""")

    def ingest(self, result: dict[str, Any]) -> int:
        advice = result.get("advice") or {}
        now = result.get("generated_at") or datetime.now(timezone.utc).isoformat()
        inserted = 0
        with sqlite3.connect(self.path) as db:
            for item in advice.get("recommended_actions") or []:
                action = str(item.get("action") or "").strip()
                if not action:
                    continue
                category = str(item.get("category") or "other")
                fingerprint = hashlib.sha256((category.casefold() + "::" + action.casefold()).encode()).hexdigest()
                cur = db.execute("""INSERT INTO governor_tasks
                    (fingerprint, action, category, priority, why, visual_evidence,
                     confidence, uncertainties_json, manual_steps_json, first_seen,
                     last_seen, source_screenshot)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(fingerprint) DO UPDATE SET
                      priority=excluded.priority, why=excluded.why,
                      visual_evidence=excluded.visual_evidence,
                      confidence=excluded.confidence,
                      uncertainties_json=excluded.uncertainties_json,
                      manual_steps_json=excluded.manual_steps_json,
                      last_seen=excluded.last_seen,
                      seen_count=governor_tasks.seen_count + 1,
                      source_screenshot=excluded.source_screenshot""",
                    (fingerprint, action, category, str(item.get("priority") or "normal"),
                     str(item.get("why") or ""), str(item.get("visual_evidence") or ""),
                     item.get("confidence"), json.dumps(item.get("uncertainties") or []),
                     json.dumps(item.get("manual_steps") or []), now, now,
                     str(result.get("source_screenshot") or "")))
                if cur.rowcount == 1:
                    inserted += 1
        return inserted

    def list(self, status: str = "pending") -> list[dict[str, Any]]:
        if status not in {"pending", "completed", "dismissed", "all"}:
            raise ValueError("Invalid task status")
        query = "SELECT * FROM governor_tasks"
        args: tuple[Any, ...] = ()
        if status != "all":
            query += " WHERE status = ?"
            args = (status,)
        query += " ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, last_seen DESC"
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(query, args).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["uncertainties"] = json.loads(item.pop("uncertainties_json"))
            item["manual_steps"] = json.loads(item.pop("manual_steps_json"))
            output.append(item)
        return output

    def set_status(self, task_id: int, status: str) -> bool:
        if status not in {"pending", "completed", "dismissed"}:
            raise ValueError("Invalid task status")
        with sqlite3.connect(self.path) as db:
            cur = db.execute("UPDATE governor_tasks SET status = ? WHERE id = ?", (status, task_id))
            return cur.rowcount > 0

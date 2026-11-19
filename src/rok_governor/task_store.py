from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
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
            db.execute("""CREATE TABLE IF NOT EXISTS task_reviews (
                task_id INTEGER PRIMARY KEY,
                assessment TEXT NOT NULL,
                visual_evidence TEXT NOT NULL,
                confidence REAL,
                reviewed_at TEXT NOT NULL,
                source_screenshot TEXT,
                FOREIGN KEY(task_id) REFERENCES governor_tasks(id)
            )""")

    def ingest(self, result: dict[str, Any]) -> int:
        advice = result.get("advice") or {}
        now = result.get("generated_at") or datetime.now(timezone.utc).isoformat()
        processed = 0
        with sqlite3.connect(self.path) as db:
            for item in advice.get("recommended_actions") or []:
                action = str(item.get("action") or "").strip()
                if not action:
                    continue
                category = str(item.get("category") or "other").strip().casefold()
                canonical_action = " ".join(unicodedata.normalize("NFKC", action).casefold().split())
                fingerprint = hashlib.sha256((category + "::" + canonical_action).encode("utf-8")).hexdigest()
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
                processed += 1
        return processed

    def record_reviews(self, result: dict[str, Any]) -> int:
        """Store Gemini's visual assessments without changing task status."""
        advice = result.get("advice") or {}
        now = result.get("generated_at") or datetime.now(timezone.utc).isoformat()
        saved = 0
        with sqlite3.connect(self.path) as db:
            for review in advice.get("task_reviews") or []:
                task_id = review.get("task_id")
                assessment = str(review.get("assessment") or "uncertain")
                if not isinstance(task_id, int) or assessment not in {
                    "likely_completed", "still_pending", "uncertain"
                }:
                    continue
                cursor = db.execute(
                    """INSERT INTO task_reviews
                       (task_id, assessment, visual_evidence, confidence,
                        reviewed_at, source_screenshot)
                       SELECT id, ?, ?, ?, ?, ?
                       FROM governor_tasks WHERE id = ? AND status = 'pending'
                       ON CONFLICT(task_id) DO UPDATE SET
                         assessment=excluded.assessment,
                         visual_evidence=excluded.visual_evidence,
                         confidence=excluded.confidence,
                         reviewed_at=excluded.reviewed_at,
                         source_screenshot=excluded.source_screenshot""",
                    (
                        assessment,
                        str(review.get("visual_evidence") or ""),
                        review.get("confidence"),
                        now,
                        str(result.get("source_screenshot") or ""),
                        task_id,
                    ),
                )
                saved += cursor.rowcount
        return saved

    def list_reviews(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """SELECT t.id, t.action, t.priority, t.status,
                          r.assessment, r.visual_evidence, r.confidence,
                          r.reviewed_at, r.source_screenshot
                   FROM task_reviews r
                   JOIN governor_tasks t ON t.id = r.task_id
                   WHERE t.status = 'pending'
                   ORDER BY CASE r.assessment
                       WHEN 'likely_completed' THEN 0
                       WHEN 'still_pending' THEN 1 ELSE 2 END,
                       r.reviewed_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

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

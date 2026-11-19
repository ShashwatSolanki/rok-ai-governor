"""Persistent idempotency, execution audit, and visual verification state."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ExecutionStore:
    def __init__(self, database: str | Path = "data/account_history.sqlite3"):
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS automation_executions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT NOT NULL UNIQUE,
                    command_title TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                )"""
            )

    @staticmethod
    def fingerprint(command: dict[str, Any]) -> str:
        canonical = json.dumps(
            {
                "action_type": command.get("action_type"),
                "title": command.get("title"),
                "target": command.get("target"),
                "input_actions": command.get("input_actions"),
                "screen_fingerprint": command.get("screen_fingerprint"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def claim(self, command: dict[str, Any]) -> bool:
        """Atomically reserve a command; an identical command is never replayed."""
        with sqlite3.connect(self.path) as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO automation_executions
                   (fingerprint, command_title, action_type, status, details_json, started_at)
                   VALUES (?, ?, ?, 'started', '{}', ?)""",
                (
                    self.fingerprint(command),
                    str(command.get("title") or "Untitled command"),
                    str(command.get("action_type") or "other"),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cursor.rowcount == 1

    def finish(self, command: dict[str, Any], status: str, details: dict[str, Any]) -> None:
        if status not in {"posted", "blocked", "error"}:
            raise ValueError("Invalid automation execution status.")
        with sqlite3.connect(self.path) as db:
            db.execute(
                """UPDATE automation_executions
                   SET status = ?, details_json = ?, finished_at = ?
                   WHERE fingerprint = ?""",
                (
                    status,
                    json.dumps(details, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                    self.fingerprint(command),
                ),
            )

    def pending_verification(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return posted actions whose result has not yet been assessed by Gemini."""
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """SELECT id, command_title, action_type, details_json, started_at
                   FROM automation_executions
                   WHERE status = 'posted'
                   ORDER BY id ASC LIMIT ?""",
                (max(1, min(limit, 50)),),
            ).fetchall()
        result = []
        for row in rows:
            details = json.loads(row["details_json"])
            result.append({
                "execution_id": row["id"],
                "command_title": row["command_title"],
                "action_type": row["action_type"],
                "verification_criteria": details.get("verification_criteria", ""),
                "posted_at": row["started_at"],
                "post_action_screenshot": details.get("post_action_screenshot"),
            })
        return result

    def record_reviews(self, result: dict[str, Any]) -> int:
        reviews = (result.get("advice") or {}).get("execution_reviews") or []
        saved = 0
        with sqlite3.connect(self.path) as db:
            for review in reviews:
                execution_id = review.get("execution_id")
                assessment = str(review.get("assessment") or "uncertain").casefold()
                if not isinstance(execution_id, int) or assessment not in {"verified", "failed", "uncertain"}:
                    continue
                row = db.execute(
                    "SELECT details_json, status FROM automation_executions WHERE id = ?",
                    (execution_id,),
                ).fetchone()
                if row is None or row[1] != "posted":
                    continue
                details = json.loads(row[0])
                details["visual_review"] = {
                    "assessment": assessment,
                    "evidence": str(review.get("visual_evidence") or ""),
                    "reviewed_at": datetime.now(timezone.utc).isoformat(),
                }
                db.execute(
                    """UPDATE automation_executions
                       SET status = ?, details_json = ?, finished_at = ?
                       WHERE id = ? AND status = 'posted'""",
                    (
                        assessment,
                        json.dumps(details, ensure_ascii=False),
                        datetime.now(timezone.utc).isoformat(),
                        execution_id,
                    ),
                )
                saved += 1
        return saved

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT * FROM automation_executions ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            items.append(item)
        return items

"""Persistent idempotency and audit log for unattended game input."""
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
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def claim(self, command: dict[str, Any]) -> bool:
        """Atomically reserve a command; a command is never auto-replayed."""
        fingerprint = self.fingerprint(command)
        with sqlite3.connect(self.path) as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO automation_executions
                   (fingerprint, command_title, action_type, status, details_json, started_at)
                   VALUES (?, ?, ?, 'started', '{}', ?)""",
                (
                    fingerprint,
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

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT * FROM automation_executions ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            items.append(item)
        return items

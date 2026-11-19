"""Persistent declarative in-game action proposals returned by Gemini.

Only the separate input controller can dispatch its narrow allow-listed actions.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ALLOWED_ACTION_TYPES = {
    "inspect_screen", "open_menu", "select_tab", "start_gathering",
    "start_research", "upgrade_building", "train_troops",
    "claim_reward", "manage_commander", "other",
}
ALLOWED_STATUSES = {"proposed", "completed", "dismissed"}


class CommandStore:
    """Persistent queue of structured Gemini action proposals."""

    def __init__(self, database: str | Path = "data/account_history.sqlite3"):
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS governor_commands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT NOT NULL UNIQUE,
                    action_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    target TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    preconditions_json TEXT NOT NULL,
                    steps_json TEXT NOT NULL,
                    verification TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    confidence REAL,
                    status TEXT NOT NULL DEFAULT 'proposed',
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    seen_count INTEGER NOT NULL DEFAULT 1,
                    source_screenshot TEXT
                )"""
            )

    def ingest(self, result: dict[str, Any]) -> int:
        advice = result.get("advice") or {}
        now = result.get("generated_at") or datetime.now(timezone.utc).isoformat()
        saved = 0
        with sqlite3.connect(self.path) as db:
            for item in advice.get("commands") or []:
                action_type = str(item.get("action_type") or "other").strip().casefold()
                if action_type not in ALLOWED_ACTION_TYPES:
                    action_type = "other"
                title = str(item.get("title") or "").strip()
                if not title:
                    continue
                target = str(item.get("target") or "").strip()
                canonical = json.dumps(
                    [action_type, title.casefold(), target.casefold()],
                    ensure_ascii=False, separators=(",", ":"),
                )
                fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                db.execute(
                    """INSERT INTO governor_commands
                       (fingerprint, action_type, title, target, parameters_json,
                        preconditions_json, steps_json, verification, rationale,
                        risk, confidence, first_seen, last_seen, source_screenshot)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(fingerprint) DO UPDATE SET
                         parameters_json=excluded.parameters_json,
                         preconditions_json=excluded.preconditions_json,
                         steps_json=excluded.steps_json,
                         verification=excluded.verification,
                         rationale=excluded.rationale,
                         risk=excluded.risk,
                         confidence=excluded.confidence,
                         last_seen=excluded.last_seen,
                         seen_count=governor_commands.seen_count + 1,
                         source_screenshot=excluded.source_screenshot""",
                    (
                        fingerprint, action_type, title, target,
                        json.dumps(item.get("parameters") or {}, ensure_ascii=False),
                        json.dumps(item.get("preconditions") or [], ensure_ascii=False),
                        json.dumps(item.get("steps") or [], ensure_ascii=False),
                        str(item.get("verification") or ""),
                        str(item.get("rationale") or ""),
                        str(item.get("risk") or "unknown"),
                        item.get("confidence"), now, now,
                        str(result.get("source_screenshot") or ""),
                    ),
                )
                saved += 1
        return saved

    def list(self, status: str = "proposed") -> list[dict[str, Any]]:
        if status not in {"proposed", "completed", "dismissed", "all"}:
            raise ValueError("Invalid command status")
        query = "SELECT * FROM governor_commands"
        args: tuple[Any, ...] = ()
        if status != "all":
            query += " WHERE status = ?"
            args = (status,)
        query += " ORDER BY last_seen DESC, id DESC"
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(query, args).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for field in ("parameters_json", "preconditions_json", "steps_json"):
                item[field.removesuffix("_json")] = json.loads(item.pop(field))
            result.append(item)
        return result

    def set_status(self, command_id: int, status: str) -> bool:
        if status not in ALLOWED_STATUSES:
            raise ValueError("Invalid command status")
        with sqlite3.connect(self.path) as db:
            cursor = db.execute(
                "UPDATE governor_commands SET status = ? WHERE id = ?",
                (status, command_id),
            )
            return cursor.rowcount > 0

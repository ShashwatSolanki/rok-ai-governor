"""Persistent, local history for read-only RoK observations."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AccountStore:
    """Store every scan and maintain a best-known, non-destructive state."""

    def __init__(self, database: str | Path = "data/account_history.sqlite3"):
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observed_at TEXT NOT NULL,
                    screen_type TEXT,
                    source TEXT,
                    payload_json TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS account_state (
                    state_key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    confidence REAL
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
        flattened: dict[str, Any] = {}
        if isinstance(value, dict):
            for key, child in value.items():
                path = f"{prefix}.{key}" if prefix else key
                flattened.update(AccountStore._flatten(child, path))
        elif value is not None and prefix:
            flattened[prefix] = value
        return flattened

    def record(self, observation: dict[str, Any]) -> int:
        observed_at = observation.get("captured_or_analysed_at") or datetime.now(
            timezone.utc
        ).isoformat()
        screen_type = (observation.get("screen") or {}).get("type")
        flattened = self._flatten(observation.get("account_state") or {})

        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO observations
                   (observed_at, screen_type, source, payload_json)
                   VALUES (?, ?, ?, ?)""",
                (
                    observed_at,
                    screen_type,
                    observation.get("source"),
                    json.dumps(observation, ensure_ascii=False),
                ),
            )
            for key, value in flattened.items():
                # Never replace a known value with null. Unknown/low-confidence
                # values remain in the raw observation for later review.
                confidence = value.get("confidence") if isinstance(value, dict) else None
                connection.execute(
                    """INSERT INTO account_state
                       (state_key, value_json, observed_at, confidence)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(state_key) DO UPDATE SET
                         value_json=excluded.value_json,
                         observed_at=excluded.observed_at,
                         confidence=excluded.confidence""",
                    (key, json.dumps(value, ensure_ascii=False), observed_at, confidence),
                )
            return int(cursor.lastrowid)

    def latest_state(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT state_key, value_json, observed_at, confidence
                   FROM account_state ORDER BY state_key"""
            ).fetchall()
        return [
            {
                "key": row["state_key"],
                "value": json.loads(row["value_json"]),
                "observed_at": row["observed_at"],
                "confidence": row["confidence"],
            }
            for row in rows
        ]

    def observation_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS total FROM observations").fetchone()
        return int(row["total"])

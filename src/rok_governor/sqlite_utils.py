"""SQLite helpers with explicit connection cleanup for Windows compatibility."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def connect_database(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Commit or roll back a transaction, then always release its file handle."""
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()

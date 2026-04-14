from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from ..models import AppState


def table_has_rows(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(f"SELECT 1 FROM {table_name} LIMIT 1").fetchone()
    return row is not None


def database_has_data(connection: sqlite3.Connection) -> bool:
    return any(
        table_has_rows(connection, table)
        for table in ("media_items", "schedule_entries", "cron_entries", "queue_items")
    )


def is_legacy_migration_done(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT value FROM app_meta WHERE key = 'legacy_json_migrated'"
    ).fetchone()
    return row is not None and row["value"] == "1"


def mark_legacy_migration_done(connection: sqlite3.Connection) -> None:
    with connection:
        connection.execute(
            """
            INSERT INTO app_meta(key, value)
            VALUES('legacy_json_migrated', '1')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )


def load_legacy_json_state(path: Path) -> AppState:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return AppState.from_dict(data)

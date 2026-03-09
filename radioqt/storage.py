from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from .models import AppState


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS media_items (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS schedule_entries (
            id TEXT PRIMARY KEY,
            media_id TEXT NOT NULL,
            start_at TEXT NOT NULL,
            duration INTEGER,
            hard_sync INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            one_shot INTEGER NOT NULL DEFAULT 1,
            fired INTEGER NOT NULL DEFAULT 0,
            position INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_schedule_entries_position
            ON schedule_entries(position);

        CREATE TABLE IF NOT EXISTS queue_items (
            position INTEGER PRIMARY KEY,
            media_id TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def _table_has_rows(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(f"SELECT 1 FROM {table_name} LIMIT 1").fetchone()
    return row is not None


def _database_has_data(connection: sqlite3.Connection) -> bool:
    return any(
        _table_has_rows(connection, table)
        for table in ("media_items", "schedule_entries", "queue_items")
    )


def _is_legacy_migration_done(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT value FROM app_meta WHERE key = 'legacy_json_migrated'"
    ).fetchone()
    return row is not None and row["value"] == "1"


def _mark_legacy_migration_done(connection: sqlite3.Connection) -> None:
    with connection:
        connection.execute(
            """
            INSERT INTO app_meta(key, value)
            VALUES('legacy_json_migrated', '1')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )


def _load_legacy_json_state(path: Path) -> AppState:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return AppState.from_dict(data)


def _read_state(connection: sqlite3.Connection) -> AppState:
    media_items = [
        {
            "id": row["id"],
            "title": row["title"],
            "source": row["source"],
            "created_at": row["created_at"],
        }
        for row in connection.execute(
            "SELECT id, title, source, created_at FROM media_items ORDER BY created_at, id"
        ).fetchall()
    ]
    schedule_entries = [
        {
            "id": row["id"],
            "media_id": row["media_id"],
            "start_at": row["start_at"],
            "duration": row["duration"],
            "hard_sync": bool(row["hard_sync"]),
            "enabled": bool(row["enabled"]),
            "one_shot": bool(row["one_shot"]),
            "fired": bool(row["fired"]),
        }
        for row in connection.execute(
            """
            SELECT id, media_id, start_at, duration, hard_sync, enabled, one_shot, fired
            FROM schedule_entries
            ORDER BY position
            """
        ).fetchall()
    ]
    queue = [
        row["media_id"]
        for row in connection.execute(
            "SELECT media_id FROM queue_items ORDER BY position"
        ).fetchall()
    ]
    return AppState.from_dict(
        {
            "media_items": media_items,
            "schedule_entries": schedule_entries,
            "queue": queue,
        }
    )


def _write_state(connection: sqlite3.Connection, state: AppState) -> None:
    with connection:
        connection.execute("DELETE FROM queue_items")
        connection.execute("DELETE FROM schedule_entries")
        connection.execute("DELETE FROM media_items")

        connection.executemany(
            """
            INSERT INTO media_items (id, title, source, created_at)
            VALUES (?, ?, ?, ?)
            """,
            [
                (item.id, item.title, item.source, item.created_at.isoformat())
                for item in state.media_items
            ],
        )
        connection.executemany(
            """
            INSERT INTO schedule_entries (
                id, media_id, start_at, duration, hard_sync, enabled, one_shot, fired, position
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    entry.id,
                    entry.media_id,
                    entry.start_at.isoformat(),
                    entry.duration,
                    int(entry.hard_sync),
                    int(entry.enabled),
                    int(entry.one_shot),
                    int(entry.fired),
                    index,
                )
                for index, entry in enumerate(state.schedule_entries)
            ],
        )
        connection.executemany(
            "INSERT INTO queue_items (position, media_id) VALUES (?, ?)",
            [(index, media_id) for index, media_id in enumerate(state.queue)],
        )


def load_state(path: Path) -> AppState:
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_json_path = path.with_suffix(".json")

    with _connect(path) as connection:
        _ensure_schema(connection)
        if not _is_legacy_migration_done(connection):
            if not _database_has_data(connection) and legacy_json_path.exists():
                legacy_state = _load_legacy_json_state(legacy_json_path)
                _write_state(connection, legacy_state)
            _mark_legacy_migration_done(connection)
        return _read_state(connection)


def save_state(path: Path, state: AppState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as connection:
        _ensure_schema(connection)
        _write_state(connection, state)

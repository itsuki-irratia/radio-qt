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
            status TEXT NOT NULL DEFAULT 'pending',
            one_shot INTEGER NOT NULL DEFAULT 1,
            cron_id TEXT,
            cron_status_override TEXT,
            cron_hard_sync_override INTEGER,
            position INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_schedule_entries_position
            ON schedule_entries(position);

        CREATE TABLE IF NOT EXISTS cron_entries (
            id TEXT PRIMARY KEY,
            media_id TEXT NOT NULL,
            expression TEXT NOT NULL,
            hard_sync INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            position INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_cron_entries_position
            ON cron_entries(position);

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
        for table in ("media_items", "schedule_entries", "cron_entries", "queue_items")
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
    schedule_auto_focus_row = connection.execute(
        "SELECT value FROM app_meta WHERE key = 'schedule_auto_focus'"
    ).fetchone()
    schedule_auto_focus = (
        schedule_auto_focus_row is not None and schedule_auto_focus_row["value"] == "1"
    )
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
            "status": row["status"],
            "one_shot": bool(row["one_shot"]),
            "cron_id": row["cron_id"],
            "cron_status_override": row["cron_status_override"],
            "cron_hard_sync_override": (
                None if row["cron_hard_sync_override"] is None else bool(row["cron_hard_sync_override"])
            ),
        }
        for row in connection.execute(
            """
            SELECT
                id,
                media_id,
                start_at,
                duration,
                hard_sync,
                status,
                one_shot,
                cron_id,
                cron_status_override,
                cron_hard_sync_override
            FROM schedule_entries
            ORDER BY position
            """
        ).fetchall()
    ]
    cron_entries = [
        {
            "id": row["id"],
            "media_id": row["media_id"],
            "expression": row["expression"],
            "hard_sync": bool(row["hard_sync"]),
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
        }
        for row in connection.execute(
            """
            SELECT id, media_id, expression, hard_sync, enabled, created_at
            FROM cron_entries
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
            "cron_entries": cron_entries,
            "queue": queue,
            "schedule_auto_focus": schedule_auto_focus,
        }
    )


def _write_state(connection: sqlite3.Connection, state: AppState) -> None:
    with connection:
        connection.execute("DELETE FROM queue_items")
        connection.execute("DELETE FROM cron_entries")
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
                id,
                media_id,
                start_at,
                duration,
                hard_sync,
                status,
                one_shot,
                cron_id,
                cron_status_override,
                cron_hard_sync_override,
                position
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    entry.id,
                    entry.media_id,
                    entry.start_at.isoformat(),
                    entry.duration,
                    int(entry.hard_sync),
                    entry.status,
                    int(entry.one_shot),
                    entry.cron_id,
                    entry.cron_status_override,
                    (
                        None
                        if entry.cron_hard_sync_override is None
                        else int(entry.cron_hard_sync_override)
                    ),
                    index,
                )
                for index, entry in enumerate(state.schedule_entries)
            ],
        )
        connection.executemany(
            """
            INSERT INTO cron_entries (
                id, media_id, expression, hard_sync, enabled, created_at, position
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    entry.id,
                    entry.media_id,
                    entry.expression,
                    int(entry.hard_sync),
                    int(entry.enabled),
                    entry.created_at.isoformat(),
                    index,
                )
                for index, entry in enumerate(state.cron_entries)
            ],
        )
        connection.executemany(
            "INSERT INTO queue_items (position, media_id) VALUES (?, ?)",
            [(index, media_id) for index, media_id in enumerate(state.queue)],
        )
        connection.execute(
            """
            INSERT INTO app_meta(key, value)
            VALUES('schedule_auto_focus', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            ("1" if state.schedule_auto_focus else "0",),
        )


def _migrate_enabled_fired_to_status(connection: sqlite3.Connection) -> None:
    """Migrate old enabled/fired columns to the new status column."""
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(schedule_entries)").fetchall()
    }
    if "fired" not in columns and "enabled" not in columns:
        return

    if "status" not in columns:
        connection.execute(
            "ALTER TABLE schedule_entries ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'"
        )

    if "fired" in columns and "enabled" in columns:
        connection.execute(
            """
            UPDATE schedule_entries
            SET status = CASE
                WHEN fired = 1 THEN 'fired'
                WHEN enabled = 0 THEN 'disabled'
                ELSE 'pending'
            END
            """
        )

    for col in ("fired", "enabled"):
        if col in columns:
            connection.executescript(
                f"""
                CREATE TABLE schedule_entries_backup AS SELECT
                    id, media_id, start_at, duration, hard_sync, status, one_shot, position
                FROM schedule_entries;
                DROP TABLE schedule_entries;
                ALTER TABLE schedule_entries_backup RENAME TO schedule_entries;
                CREATE INDEX IF NOT EXISTS idx_schedule_entries_position
                    ON schedule_entries(position);
                """
            )
            break

    connection.commit()


def _migrate_schedule_entries_for_cron(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(schedule_entries)").fetchall()
    }
    if "cron_id" not in columns:
        connection.execute("ALTER TABLE schedule_entries ADD COLUMN cron_id TEXT")
    if "cron_status_override" not in columns:
        connection.execute("ALTER TABLE schedule_entries ADD COLUMN cron_status_override TEXT")
    if "cron_hard_sync_override" not in columns:
        connection.execute("ALTER TABLE schedule_entries ADD COLUMN cron_hard_sync_override INTEGER")
    connection.commit()


def load_state(path: Path) -> AppState:
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_json_path = path.with_suffix(".json")

    with _connect(path) as connection:
        _ensure_schema(connection)
        _migrate_enabled_fired_to_status(connection)
        _migrate_schedule_entries_for_cron(connection)
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
        _migrate_schedule_entries_for_cron(connection)
        _write_state(connection, state)

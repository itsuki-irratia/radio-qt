from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from .models import AppState


def _db_bool_to_python(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off", ""}:
            return False
    return default


def _db_optional_bool_to_python(value: object) -> bool | None:
    if value is None:
        return None
    return _db_bool_to_python(value, default=False)


def _python_bool_to_db(value: bool) -> str:
    return "True" if value else "False"


def _python_optional_bool_to_db(value: bool | None) -> str | None:
    if value is None:
        return None
    return _python_bool_to_db(value)


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
            hard_sync TEXT NOT NULL DEFAULT 'False',
            fade_in TEXT NOT NULL DEFAULT 'False',
            fade_out TEXT NOT NULL DEFAULT 'False',
            status TEXT NOT NULL DEFAULT 'pending',
            one_shot INTEGER NOT NULL DEFAULT 1,
            cron_id TEXT,
            cron_status_override TEXT,
            cron_hard_sync_override TEXT,
            position INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_schedule_entries_position
            ON schedule_entries(position);

        CREATE TABLE IF NOT EXISTS cron_entries (
            id TEXT PRIMARY KEY,
            media_id TEXT NOT NULL,
            expression TEXT NOT NULL,
            hard_sync TEXT NOT NULL DEFAULT 'False',
            fade_in TEXT NOT NULL DEFAULT 'False',
            fade_out TEXT NOT NULL DEFAULT 'False',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            position INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_cron_entries_position
            ON cron_entries(position);

        CREATE TABLE IF NOT EXISTS queue_items (
            position INTEGER PRIMARY KEY,
            media_id TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual',
            schedule_entry_id TEXT
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
    logs_visible_row = connection.execute(
        "SELECT value FROM app_meta WHERE key = 'logs_visible'"
    ).fetchone()
    logs_visible = logs_visible_row is None or logs_visible_row["value"] == "1"
    fade_in_duration_row = connection.execute(
        "SELECT value FROM app_meta WHERE key = 'fade_in_duration_seconds'"
    ).fetchone()
    fade_out_duration_row = connection.execute(
        "SELECT value FROM app_meta WHERE key = 'fade_out_duration_seconds'"
    ).fetchone()
    duration_probe_cache_row = connection.execute(
        "SELECT value FROM app_meta WHERE key = 'duration_probe_cache'"
    ).fetchone()
    try:
        fade_in_duration_seconds = max(
            1, int(fade_in_duration_row["value"] if fade_in_duration_row is not None else 5)
        )
    except (TypeError, ValueError):
        fade_in_duration_seconds = 5
    try:
        fade_out_duration_seconds = max(
            1, int(fade_out_duration_row["value"] if fade_out_duration_row is not None else 5)
        )
    except (TypeError, ValueError):
        fade_out_duration_seconds = 5
    try:
        duration_probe_cache = json.loads(
            duration_probe_cache_row["value"] if duration_probe_cache_row is not None else "{}"
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        duration_probe_cache = {}
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
            "hard_sync": _db_bool_to_python(row["hard_sync"]),
            "fade_in": _db_bool_to_python(row["fade_in"]),
            "fade_out": _db_bool_to_python(row["fade_out"]),
            "status": row["status"],
            "one_shot": bool(row["one_shot"]),
            "cron_id": row["cron_id"],
            "cron_status_override": row["cron_status_override"],
            "cron_hard_sync_override": _db_optional_bool_to_python(row["cron_hard_sync_override"]),
            "cron_fade_in_override": _db_optional_bool_to_python(row["cron_fade_in_override"]),
            "cron_fade_out_override": _db_optional_bool_to_python(row["cron_fade_out_override"]),
        }
        for row in connection.execute(
            """
            SELECT
                id,
                media_id,
                start_at,
                duration,
                hard_sync,
                fade_in,
                fade_out,
                status,
                one_shot,
                cron_id,
                cron_status_override,
                cron_hard_sync_override,
                cron_fade_in_override,
                cron_fade_out_override
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
            "hard_sync": _db_bool_to_python(row["hard_sync"]),
            "fade_in": _db_bool_to_python(row["fade_in"]),
            "fade_out": _db_bool_to_python(row["fade_out"]),
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
        }
        for row in connection.execute(
            """
            SELECT id, media_id, expression, hard_sync, fade_in, fade_out, enabled, created_at
            FROM cron_entries
            ORDER BY position
            """
        ).fetchall()
    ]
    queue = [
        {
            "media_id": row["media_id"],
            "source": row["source"],
            "schedule_entry_id": row["schedule_entry_id"],
        }
        for row in connection.execute(
            "SELECT media_id, source, schedule_entry_id FROM queue_items ORDER BY position"
        ).fetchall()
    ]
    return AppState.from_dict(
        {
            "media_items": media_items,
            "schedule_entries": schedule_entries,
            "cron_entries": cron_entries,
            "queue": queue,
            "schedule_auto_focus": schedule_auto_focus,
            "logs_visible": logs_visible,
            "fade_in_duration_seconds": fade_in_duration_seconds,
            "fade_out_duration_seconds": fade_out_duration_seconds,
            "duration_probe_cache": duration_probe_cache,
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
                fade_in,
                fade_out,
                status,
                one_shot,
                cron_id,
                cron_status_override,
                cron_hard_sync_override,
                cron_fade_in_override,
                cron_fade_out_override,
                position
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    entry.id,
                    entry.media_id,
                    entry.start_at.isoformat(),
                    entry.duration,
                    _python_bool_to_db(entry.hard_sync),
                    _python_bool_to_db(entry.fade_in),
                    _python_bool_to_db(entry.fade_out),
                    entry.status,
                    int(entry.one_shot),
                    entry.cron_id,
                    entry.cron_status_override,
                    _python_optional_bool_to_db(entry.cron_hard_sync_override),
                    _python_optional_bool_to_db(entry.cron_fade_in_override),
                    _python_optional_bool_to_db(entry.cron_fade_out_override),
                    index,
                )
                for index, entry in enumerate(state.schedule_entries)
            ],
        )
        connection.executemany(
            """
            INSERT INTO cron_entries (
                id, media_id, expression, hard_sync, fade_in, fade_out, enabled, created_at, position
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    entry.id,
                    entry.media_id,
                    entry.expression,
                    _python_bool_to_db(entry.hard_sync),
                    _python_bool_to_db(entry.fade_in),
                    _python_bool_to_db(entry.fade_out),
                    int(entry.enabled),
                    entry.created_at.isoformat(),
                    index,
                )
                for index, entry in enumerate(state.cron_entries)
            ],
        )
        connection.executemany(
            """
            INSERT INTO queue_items (position, media_id, source, schedule_entry_id)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    index,
                    item.media_id,
                    item.source,
                    item.schedule_entry_id,
                )
                for index, item in enumerate(state.queue)
            ],
        )
        connection.execute(
            """
            INSERT INTO app_meta(key, value)
            VALUES('schedule_auto_focus', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            ("1" if state.schedule_auto_focus else "0",),
        )
        connection.execute(
            """
            INSERT INTO app_meta(key, value)
            VALUES('logs_visible', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            ("1" if state.logs_visible else "0",),
        )
        connection.execute(
            """
            INSERT INTO app_meta(key, value)
            VALUES('fade_in_duration_seconds', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(max(1, state.fade_in_duration_seconds)),),
        )
        connection.execute(
            """
            INSERT INTO app_meta(key, value)
            VALUES('fade_out_duration_seconds', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(max(1, state.fade_out_duration_seconds)),),
        )
        connection.execute(
            """
            INSERT INTO app_meta(key, value)
            VALUES('duration_probe_cache', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (json.dumps(state.duration_probe_cache, separators=(",", ":")),),
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
        connection.execute("ALTER TABLE schedule_entries ADD COLUMN cron_hard_sync_override TEXT")
    if "cron_fade_in_override" not in columns:
        connection.execute("ALTER TABLE schedule_entries ADD COLUMN cron_fade_in_override TEXT")
    if "cron_fade_out_override" not in columns:
        connection.execute("ALTER TABLE schedule_entries ADD COLUMN cron_fade_out_override TEXT")
    connection.commit()


def _migrate_schedule_entries_fade_flags(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(schedule_entries)").fetchall()
    }
    if "fade_in" not in columns:
        connection.execute(
            "ALTER TABLE schedule_entries ADD COLUMN fade_in TEXT NOT NULL DEFAULT 'False'"
        )
    if "fade_out" not in columns:
        connection.execute(
            "ALTER TABLE schedule_entries ADD COLUMN fade_out TEXT NOT NULL DEFAULT 'False'"
        )
    connection.commit()


def _migrate_cron_entries_fade_flags(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(cron_entries)").fetchall()
    }
    if "fade_in" not in columns:
        connection.execute(
            "ALTER TABLE cron_entries ADD COLUMN fade_in TEXT NOT NULL DEFAULT 'False'"
        )
    if "fade_out" not in columns:
        connection.execute(
            "ALTER TABLE cron_entries ADD COLUMN fade_out TEXT NOT NULL DEFAULT 'False'"
        )
    connection.commit()


def _migrate_boolean_storage_to_text(connection: sqlite3.Connection) -> None:
    truthy = "1, '1', 'true', 'TRUE', 'True', 'yes', 'YES', 'Yes', 'on', 'ON', 'On'"
    with connection:
        connection.execute(
            f"""
            UPDATE schedule_entries
            SET hard_sync = CASE
                WHEN hard_sync IN ({truthy}) THEN 'True'
                ELSE 'False'
            END
            """
        )
        connection.execute(
            f"""
            UPDATE schedule_entries
            SET fade_in = CASE
                WHEN fade_in IN ({truthy}) THEN 'True'
                ELSE 'False'
            END
            """
        )
        connection.execute(
            f"""
            UPDATE schedule_entries
            SET fade_out = CASE
                WHEN fade_out IN ({truthy}) THEN 'True'
                ELSE 'False'
            END
            """
        )
        connection.execute(
            f"""
            UPDATE schedule_entries
            SET cron_hard_sync_override = CASE
                WHEN cron_hard_sync_override IS NULL THEN NULL
                WHEN cron_hard_sync_override IN ({truthy}) THEN 'True'
                ELSE 'False'
            END
            """
        )
        connection.execute(
            f"""
            UPDATE schedule_entries
            SET cron_fade_in_override = CASE
                WHEN cron_fade_in_override IS NULL THEN NULL
                WHEN cron_fade_in_override IN ({truthy}) THEN 'True'
                ELSE 'False'
            END
            """
        )
        connection.execute(
            f"""
            UPDATE schedule_entries
            SET cron_fade_out_override = CASE
                WHEN cron_fade_out_override IS NULL THEN NULL
                WHEN cron_fade_out_override IN ({truthy}) THEN 'True'
                ELSE 'False'
            END
            """
        )
        connection.execute(
            f"""
            UPDATE cron_entries
            SET hard_sync = CASE
                WHEN hard_sync IN ({truthy}) THEN 'True'
                ELSE 'False'
            END
            """
        )
        connection.execute(
            f"""
            UPDATE cron_entries
            SET fade_in = CASE
                WHEN fade_in IN ({truthy}) THEN 'True'
                ELSE 'False'
            END
            """
        )
        connection.execute(
            f"""
            UPDATE cron_entries
            SET fade_out = CASE
                WHEN fade_out IN ({truthy}) THEN 'True'
                ELSE 'False'
            END
            """
        )


def _migrate_boolean_column_types_to_text(connection: sqlite3.Connection) -> None:
    schedule_columns = {
        row[1]: str(row[2] or "").upper()
        for row in connection.execute("PRAGMA table_info(schedule_entries)").fetchall()
    }
    cron_columns = {
        row[1]: str(row[2] or "").upper()
        for row in connection.execute("PRAGMA table_info(cron_entries)").fetchall()
    }

    schedule_target_columns = {
        "hard_sync",
        "fade_in",
        "fade_out",
        "cron_hard_sync_override",
        "cron_fade_in_override",
        "cron_fade_out_override",
    }
    cron_target_columns = {"hard_sync", "fade_in", "fade_out"}

    needs_schedule_rebuild = any(
        column in schedule_columns and "TEXT" not in schedule_columns[column]
        for column in schedule_target_columns
    )
    needs_cron_rebuild = any(
        column in cron_columns and "TEXT" not in cron_columns[column]
        for column in cron_target_columns
    )

    if not needs_schedule_rebuild and not needs_cron_rebuild:
        return

    truthy = "1, '1', 'true', 'TRUE', 'True', 'yes', 'YES', 'Yes', 'on', 'ON', 'On'"
    with connection:
        if needs_schedule_rebuild:
            connection.executescript(
                f"""
                CREATE TABLE schedule_entries_new (
                    id TEXT PRIMARY KEY,
                    media_id TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    duration INTEGER,
                    hard_sync TEXT NOT NULL DEFAULT 'False',
                    fade_in TEXT NOT NULL DEFAULT 'False',
                    fade_out TEXT NOT NULL DEFAULT 'False',
                    status TEXT NOT NULL DEFAULT 'pending',
                    one_shot INTEGER NOT NULL DEFAULT 1,
                    cron_id TEXT,
                    cron_status_override TEXT,
                    cron_hard_sync_override TEXT,
                    cron_fade_in_override TEXT,
                    cron_fade_out_override TEXT,
                    position INTEGER NOT NULL
                );

                INSERT INTO schedule_entries_new (
                    id,
                    media_id,
                    start_at,
                    duration,
                    hard_sync,
                    fade_in,
                    fade_out,
                    status,
                    one_shot,
                    cron_id,
                    cron_status_override,
                    cron_hard_sync_override,
                    cron_fade_in_override,
                    cron_fade_out_override,
                    position
                )
                SELECT
                    id,
                    media_id,
                    start_at,
                    duration,
                    CASE WHEN hard_sync IN ({truthy}) THEN 'True' ELSE 'False' END,
                    CASE WHEN fade_in IN ({truthy}) THEN 'True' ELSE 'False' END,
                    CASE WHEN fade_out IN ({truthy}) THEN 'True' ELSE 'False' END,
                    COALESCE(status, 'pending'),
                    COALESCE(one_shot, 1),
                    cron_id,
                    cron_status_override,
                    CASE
                        WHEN cron_hard_sync_override IS NULL THEN NULL
                        WHEN cron_hard_sync_override IN ({truthy}) THEN 'True'
                        ELSE 'False'
                    END,
                    CASE
                        WHEN cron_fade_in_override IS NULL THEN NULL
                        WHEN cron_fade_in_override IN ({truthy}) THEN 'True'
                        ELSE 'False'
                    END,
                    CASE
                        WHEN cron_fade_out_override IS NULL THEN NULL
                        WHEN cron_fade_out_override IN ({truthy}) THEN 'True'
                        ELSE 'False'
                    END,
                    position
                FROM schedule_entries;

                DROP TABLE schedule_entries;
                ALTER TABLE schedule_entries_new RENAME TO schedule_entries;
                CREATE INDEX IF NOT EXISTS idx_schedule_entries_position
                    ON schedule_entries(position);
                """
            )

        if needs_cron_rebuild:
            connection.executescript(
                f"""
                CREATE TABLE cron_entries_new (
                    id TEXT PRIMARY KEY,
                    media_id TEXT NOT NULL,
                    expression TEXT NOT NULL,
                    hard_sync TEXT NOT NULL DEFAULT 'False',
                    fade_in TEXT NOT NULL DEFAULT 'False',
                    fade_out TEXT NOT NULL DEFAULT 'False',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    position INTEGER NOT NULL
                );

                INSERT INTO cron_entries_new (
                    id,
                    media_id,
                    expression,
                    hard_sync,
                    fade_in,
                    fade_out,
                    enabled,
                    created_at,
                    position
                )
                SELECT
                    id,
                    media_id,
                    expression,
                    CASE WHEN hard_sync IN ({truthy}) THEN 'True' ELSE 'False' END,
                    CASE WHEN fade_in IN ({truthy}) THEN 'True' ELSE 'False' END,
                    CASE WHEN fade_out IN ({truthy}) THEN 'True' ELSE 'False' END,
                    COALESCE(enabled, 1),
                    created_at,
                    position
                FROM cron_entries;

                DROP TABLE cron_entries;
                ALTER TABLE cron_entries_new RENAME TO cron_entries;
                CREATE INDEX IF NOT EXISTS idx_cron_entries_position
                    ON cron_entries(position);
                """
            )


def _migrate_queue_items_metadata(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(queue_items)").fetchall()
    }
    if "source" not in columns:
        connection.execute(
            "ALTER TABLE queue_items ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'"
        )
    if "schedule_entry_id" not in columns:
        connection.execute("ALTER TABLE queue_items ADD COLUMN schedule_entry_id TEXT")
    connection.commit()


def load_state(path: Path) -> AppState:
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_json_path = path.with_suffix(".json")

    with _connect(path) as connection:
        _ensure_schema(connection)
        _migrate_enabled_fired_to_status(connection)
        _migrate_schedule_entries_for_cron(connection)
        _migrate_schedule_entries_fade_flags(connection)
        _migrate_cron_entries_fade_flags(connection)
        _migrate_boolean_column_types_to_text(connection)
        _migrate_queue_items_metadata(connection)
        _migrate_boolean_storage_to_text(connection)
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
        _migrate_schedule_entries_fade_flags(connection)
        _migrate_cron_entries_fade_flags(connection)
        _migrate_boolean_column_types_to_text(connection)
        _migrate_queue_items_metadata(connection)
        _migrate_boolean_storage_to_text(connection)
        _write_state(connection, state)

from __future__ import annotations

from pathlib import Path
import sqlite3


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS media_items (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source TEXT NOT NULL,
            greenwich_time_signal_enabled TEXT NOT NULL DEFAULT 'False',
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

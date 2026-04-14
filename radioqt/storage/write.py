from __future__ import annotations

import json
import sqlite3

from ..models import AppState
from .helpers import python_bool_to_db, python_optional_bool_to_db


def write_state(connection: sqlite3.Connection, state: AppState) -> None:
    with connection:
        connection.execute("DELETE FROM queue_items")
        connection.execute("DELETE FROM cron_entries")
        connection.execute("DELETE FROM schedule_entries")
        connection.execute("DELETE FROM media_items")

        connection.executemany(
            """
            INSERT INTO media_items (
                id,
                title,
                source,
                greenwich_time_signal_enabled,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    item.id,
                    item.title,
                    item.source,
                    python_bool_to_db(item.greenwich_time_signal_enabled),
                    item.created_at.isoformat(),
                )
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
                    python_bool_to_db(entry.hard_sync),
                    python_bool_to_db(entry.fade_in),
                    python_bool_to_db(entry.fade_out),
                    entry.status,
                    int(entry.one_shot),
                    entry.cron_id,
                    entry.cron_status_override,
                    python_optional_bool_to_db(entry.cron_hard_sync_override),
                    python_optional_bool_to_db(entry.cron_fade_in_override),
                    python_optional_bool_to_db(entry.cron_fade_out_override),
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
                    python_bool_to_db(entry.hard_sync),
                    python_bool_to_db(entry.fade_in),
                    python_bool_to_db(entry.fade_out),
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
            VALUES('duration_probe_cache', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (json.dumps(state.duration_probe_cache, separators=(",", ":")),),
        )
        connection.execute(
            """
            DELETE FROM app_meta
            WHERE key IN (
                'library_tabs',
                'supported_extensions',
                'fade_in_duration_seconds',
                'fade_out_duration_seconds'
            )
            """
        )

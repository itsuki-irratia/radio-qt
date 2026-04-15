from __future__ import annotations

import json
import sqlite3

from ..models import AppState
from .helpers import db_bool_to_python, db_optional_bool_to_python


def read_state(connection: sqlite3.Connection) -> AppState:
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
    library_tabs_row = connection.execute(
        "SELECT value FROM app_meta WHERE key = 'library_tabs'"
    ).fetchone()
    supported_extensions_row = connection.execute(
        "SELECT value FROM app_meta WHERE key = 'supported_extensions'"
    ).fetchone()
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
    try:
        library_tabs = json.loads(
            library_tabs_row["value"] if library_tabs_row is not None else "[]"
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        library_tabs = []
    try:
        supported_extensions = json.loads(
            supported_extensions_row["value"] if supported_extensions_row is not None else "[]"
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        supported_extensions = []
    media_items = [
        {
            "id": row["id"],
            "title": row["title"],
            "source": row["source"],
            "greenwich_time_signal_enabled": db_bool_to_python(row["greenwich_time_signal_enabled"]),
            "created_at": row["created_at"],
        }
        for row in connection.execute(
            """
            SELECT
                id,
                title,
                source,
                greenwich_time_signal_enabled,
                created_at
            FROM media_items
            ORDER BY created_at, id
            """
        ).fetchall()
    ]
    schedule_entries = [
        {
            "id": row["id"],
            "media_id": row["media_id"],
            "start_at": row["start_at"],
            "duration": row["duration"],
            "hard_sync": db_bool_to_python(row["hard_sync"]),
            "fade_in": db_bool_to_python(row["fade_in"]),
            "fade_out": db_bool_to_python(row["fade_out"]),
            "status": row["status"],
            "one_shot": bool(row["one_shot"]),
            "cron_id": row["cron_id"],
            "cron_status_override": row["cron_status_override"],
            "cron_hard_sync_override": db_optional_bool_to_python(row["cron_hard_sync_override"]),
            "cron_fade_in_override": db_optional_bool_to_python(row["cron_fade_in_override"]),
            "cron_fade_out_override": db_optional_bool_to_python(row["cron_fade_out_override"]),
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
            "hard_sync": db_bool_to_python(row["hard_sync"]),
            "fade_in": db_bool_to_python(row["fade_in"]),
            "fade_out": db_bool_to_python(row["fade_out"]),
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
            "library_tabs": library_tabs,
            "supported_extensions": supported_extensions,
            "schedule_auto_focus": schedule_auto_focus,
            "logs_visible": logs_visible,
            "fade_in_duration_seconds": fade_in_duration_seconds,
            "fade_out_duration_seconds": fade_out_duration_seconds,
            "duration_probe_cache": duration_probe_cache,
        }
    )

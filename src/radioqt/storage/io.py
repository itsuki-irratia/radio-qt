from __future__ import annotations

from pathlib import Path

from ..models import AppState
from .legacy import (
    database_has_data,
    is_legacy_migration_done,
    load_legacy_json_state,
    mark_legacy_migration_done,
)
from .migrations import (
    migrate_boolean_column_types_to_text,
    migrate_boolean_storage_to_text,
    migrate_cron_entries_fade_flags,
    migrate_enabled_fired_to_status,
    migrate_media_items_greenwich_time_signal,
    migrate_queue_items_metadata,
    migrate_schedule_entries_fade_flags,
    migrate_schedule_entries_for_cron,
)
from .read import read_state
from .schema import connect, ensure_schema
from .write import write_state


def load_state(path: Path) -> AppState:
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_json_path = path.with_suffix(".json")

    with connect(path) as connection:
        ensure_schema(connection)
        migrate_media_items_greenwich_time_signal(connection)
        migrate_enabled_fired_to_status(connection)
        migrate_schedule_entries_for_cron(connection)
        migrate_schedule_entries_fade_flags(connection)
        migrate_cron_entries_fade_flags(connection)
        migrate_boolean_column_types_to_text(connection)
        migrate_queue_items_metadata(connection)
        migrate_boolean_storage_to_text(connection)
        if not is_legacy_migration_done(connection):
            if not database_has_data(connection) and legacy_json_path.exists():
                legacy_state = load_legacy_json_state(legacy_json_path)
                write_state(connection, legacy_state)
            mark_legacy_migration_done(connection)
        return read_state(connection)


def save_state(path: Path, state: AppState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        ensure_schema(connection)
        migrate_media_items_greenwich_time_signal(connection)
        migrate_schedule_entries_for_cron(connection)
        migrate_schedule_entries_fade_flags(connection)
        migrate_cron_entries_fade_flags(connection)
        migrate_boolean_column_types_to_text(connection)
        migrate_queue_items_metadata(connection)
        migrate_boolean_storage_to_text(connection)
        write_state(connection, state)

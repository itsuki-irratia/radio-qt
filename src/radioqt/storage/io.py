from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

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
from .schedule_export import export_schedule_incremental
from .schema import connect, ensure_schema
from .write import write_state

STATE_VERSION_META_KEY = "state_version"


class StateVersionConflictError(RuntimeError):
    def __init__(self, *, expected_version: int, current_version: int) -> None:
        self.expected_version = expected_version
        self.current_version = current_version
        super().__init__(
            (
                f"State version conflict: expected version {expected_version}, "
                f"but current version is {current_version}"
            )
        )


@dataclass(slots=True)
class LoadedState:
    state: AppState
    version: int


def _state_version(connection) -> int:
    row = connection.execute(
        "SELECT value FROM app_meta WHERE key = ?",
        (STATE_VERSION_META_KEY,),
    ).fetchone()
    if row is None:
        return 0
    try:
        return max(0, int(row["value"]))
    except (TypeError, ValueError):
        return 0


def _set_state_version(connection, version: int) -> None:
    connection.execute(
        """
        INSERT INTO app_meta(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (STATE_VERSION_META_KEY, str(max(0, int(version)))),
    )


def _prepare_state_connection(connection, *, legacy_json_path: Path) -> None:
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


def load_state_with_version(path: Path) -> LoadedState:
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_json_path = path.with_suffix(".json")

    with connect(path) as connection:
        _prepare_state_connection(connection, legacy_json_path=legacy_json_path)
        return LoadedState(
            state=read_state(connection),
            version=_state_version(connection),
        )


def load_state(path: Path) -> AppState:
    return load_state_with_version(path).state


def state_version(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        ensure_schema(connection)
        return _state_version(connection)


def save_state(
    path: Path,
    state: AppState,
    *,
    expected_version: int | None = None,
    on_schedule_export: Callable[[Path, AppState, AppState], None] | None = None,
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_json_path = path.with_suffix(".json")
    new_version: int
    previous_state: AppState
    with connect(path) as connection:
        _prepare_state_connection(connection, legacy_json_path=legacy_json_path)
        previous_state = read_state(connection)
        current_version = _state_version(connection)
        if expected_version is not None and current_version != expected_version:
            raise StateVersionConflictError(
                expected_version=expected_version,
                current_version=current_version,
            )
        write_state(connection, state)
        new_version = current_version + 1
        _set_state_version(connection, new_version)
    if on_schedule_export is not None:
        try:
            on_schedule_export(
                path.parent,
                AppState.from_dict(previous_state.to_dict()),
                AppState.from_dict(state.to_dict()),
            )
        except Exception:
            # Schedule export should never block normal state persistence.
            pass
        return new_version
    try:
        export_schedule_incremental(
            path.parent,
            previous_state=previous_state,
            current_state=state,
        )
    except Exception:
        # Schedule export should never block normal state persistence.
        pass
    return new_version

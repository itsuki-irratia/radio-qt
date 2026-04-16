from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime
import json
import os
from pathlib import Path, PurePath
import signal
import sys
import time

from ..app_config import load_app_config, save_app_config
from ..cron import CronExpression, CronParseError
from ..library import (
    add_stream_media_item,
    is_stream_source,
    local_media_path_from_source,
    remove_media_from_library,
    update_stream_greenwich_time_signal,
    update_stream_media_item,
)
from ..models import AppState, CronEntry, LibraryTab, MediaItem, ScheduleEntry
from ..scheduling.cron_runtime import next_cron_occurrence
from ..scheduling.logic import normalized_start, sort_schedule_entries
from ..scheduling.mutations import (
    create_cron_entry,
    create_schedule_entry,
    remove_cron_and_generated_schedule_entries,
    remove_schedule_entries_by_ids,
    select_schedule_entries_for_removal,
    update_cron_enabled,
    update_cron_expression,
    update_cron_fade_in,
    update_cron_fade_out,
    update_schedule_fade_in,
    update_schedule_fade_out,
    update_schedule_status,
)
from ..scheduling.presentation import runtime_cron_dates, visible_schedule_entries
from ..scheduling.state import prepare_schedule_entries_for_startup
from ..scheduling.workflows import (
    enforce_hard_sync_always,
    is_schedule_entry_protected_from_removal,
    sync_cron_runtime_window,
)
from ..runtime_status import (
    delete_runtime_lock,
    is_pid_running,
    read_runtime_status,
    resolve_runtime_status,
    RUNTIME_STATUS_OFFLINE,
    RUNTIME_STATUS_ONLINE,
    runtime_status_file_path,
    VALID_RUNTIME_STATUSES,
    write_runtime_status,
)
from ..runtime_control import (
    enqueue_runtime_control_command,
    runtime_control_file_path,
    RUNTIME_CONTROL_ACTION_FADE_IN,
    RUNTIME_CONTROL_ACTION_FADE_OUT,
    RUNTIME_CONTROL_ACTION_SET_VOLUME,
    RUNTIME_CONTROL_ACTION_START_AUTOMATION,
    RUNTIME_CONTROL_ACTION_STOP_AUTOMATION,
)
from ..storage.io import (
    load_state_with_version,
    save_state,
    StateVersionConflictError,
)

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "radioqt"
SUPPORTED_SETTINGS_KEYS = (
    "fade_seconds",
    "filesystem_default_fade_in",
    "filesystem_default_fade_out",
    "streams_default_fade_in",
    "streams_default_fade_out",
    "default_volume_percent",
    "font_size",
    "media_library_width_percent",
    "schedule_width_percent",
    "greenwich_time_signal_enabled",
    "greenwich_time_signal_path",
    "supported_extensions",
    "library_tabs",
)


class CliError(ValueError):
    pass


@dataclass(slots=True)
class StateContext:
    config_dir: Path
    state_path: Path
    state: AppState
    state_version: int


def _config_dir_from_args(raw_config_dir: str) -> Path:
    return Path(raw_config_dir).expanduser()


def _load_state_context(raw_config_dir: str) -> StateContext:
    config_dir = _config_dir_from_args(raw_config_dir)
    state_path = config_dir / "db.sqlite"
    loaded = load_state_with_version(state_path)
    return StateContext(
        config_dir=config_dir,
        state_path=state_path,
        state=loaded.state,
        state_version=loaded.version,
    )


def _settings_path(config_dir: Path) -> Path:
    return config_dir / "settings.yaml"


def _load_app_config_context(raw_config_dir: str) -> tuple[Path, Path, object]:
    config_dir = _config_dir_from_args(raw_config_dir)
    settings_path = _settings_path(config_dir)
    app_config = load_app_config(settings_path)
    return config_dir, settings_path, app_config


def _ensure_media_exists(state: AppState, media_id: str) -> MediaItem:
    media_by_id = {item.id: item for item in state.media_items}
    media = media_by_id.get(media_id)
    if media is None:
        raise CliError(f"Media id '{media_id}' does not exist")
    return media


def _find_schedule_entry(state: AppState, entry_id: str) -> ScheduleEntry:
    for entry in state.schedule_entries:
        if entry.id == entry_id:
            return entry
    raise CliError(f"Schedule entry '{entry_id}' not found")


def _find_cron_entry(state: AppState, cron_id: str) -> CronEntry:
    for entry in state.cron_entries:
        if entry.id == cron_id:
            return entry
    raise CliError(f"CRON entry '{cron_id}' not found")


def _parse_datetime(raw_value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw_value)
    except ValueError as exc:
        raise CliError(
            "Invalid datetime. Use ISO format, for example: 2026-04-16T12:30:00+02:00"
        ) from exc
    if parsed.tzinfo is not None:
        return parsed
    return parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)


def _parse_date(raw_value: str) -> date:
    try:
        return date.fromisoformat(raw_value)
    except ValueError as exc:
        raise CliError("Invalid date. Use YYYY-MM-DD format.") from exc


def _bool_from_token(raw_value: str) -> bool:
    return raw_value.strip().lower() == "true"


def _format_datetime(value: datetime, reference_time: datetime) -> str:
    normalized = normalized_start(value, reference_time)
    return normalized.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _json_enabled(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json_output", False))


def _print_success(
    args: argparse.Namespace,
    *,
    text: str,
    payload: dict[str, object],
) -> None:
    if _json_enabled(args):
        print(json.dumps(payload, separators=(",", ":"), ensure_ascii=True))
        return
    print(text)


def _print_warning(args: argparse.Namespace, text: str) -> None:
    if _json_enabled(args):
        return
    print(text)


def _settings_to_dict(app_config: object) -> dict[str, object]:
    media_width = max(10, min(90, int(app_config.media_library_width_percent)))
    schedule_width = 100 - media_width
    supported_extensions: list[str] = []
    for raw_extension in list(app_config.supported_extensions):
        token = str(raw_extension).strip().lower().lstrip(".")
        if not token:
            continue
        if not all(char.isalnum() for char in token):
            continue
        if token in supported_extensions:
            continue
        supported_extensions.append(token)
    library_tabs = [
        {
            "title": str(tab.title).strip(),
            "path": str(tab.path).strip(),
        }
        for tab in list(app_config.library_tabs)
    ]
    return {
        "fade_seconds": max(
            1,
            int(app_config.fade_in_duration_seconds),
            int(app_config.fade_out_duration_seconds),
        ),
        "filesystem_default_fade_in": bool(app_config.filesystem_default_fade_in),
        "filesystem_default_fade_out": bool(app_config.filesystem_default_fade_out),
        "streams_default_fade_in": bool(app_config.streams_default_fade_in),
        "streams_default_fade_out": bool(app_config.streams_default_fade_out),
        "default_volume_percent": _validate_volume_percent(int(app_config.default_volume_percent)),
        "font_size": int(app_config.font_size) if app_config.font_size is not None else None,
        "media_library_width_percent": media_width,
        "schedule_width_percent": schedule_width,
        "greenwich_time_signal_enabled": bool(app_config.greenwich_time_signal_enabled),
        "greenwich_time_signal_path": str(app_config.greenwich_time_signal_path).strip(),
        "supported_extensions": supported_extensions,
        "library_tabs": library_tabs,
    }


def _normalize_settings_key(raw_key: str) -> str:
    normalized = raw_key.strip().lower().replace("-", "_").replace(".", "_")
    aliases = {
        "fade": "fade_seconds",
        "fade_seconds": "fade_seconds",
        "fade_in_seconds": "fade_seconds",
        "fade_out_seconds": "fade_seconds",
        "fade_in_duration_seconds": "fade_seconds",
        "fade_out_duration_seconds": "fade_seconds",
        "filesystem_default_fade_in": "filesystem_default_fade_in",
        "filesystem_default_fade_out": "filesystem_default_fade_out",
        "streams_default_fade_in": "streams_default_fade_in",
        "streams_default_fade_out": "streams_default_fade_out",
        "default_volume_percent": "default_volume_percent",
        "audio_default_volume_percent": "default_volume_percent",
        "volume": "default_volume_percent",
        "volume_percent": "default_volume_percent",
        "font_size": "font_size",
        "media_library_width_percent": "media_library_width_percent",
        "schedule_width_percent": "schedule_width_percent",
        "greenwich_time_signal_enabled": "greenwich_time_signal_enabled",
        "greenwich_time_signal_path": "greenwich_time_signal_path",
        "supported_extensions": "supported_extensions",
        "extensions_supported": "supported_extensions",
        "library_tabs": "library_tabs",
        "custom_paths_tabs": "library_tabs",
    }
    key = aliases.get(normalized, normalized)
    if key not in SUPPORTED_SETTINGS_KEYS:
        allowed = ", ".join(SUPPORTED_SETTINGS_KEYS)
        raise CliError(f"Unknown settings key '{raw_key}'. Allowed keys: {allowed}")
    return key


def _parse_settings_bool(raw_value: str, key: str) -> bool:
    normalized = raw_value.strip().lower()
    if normalized in {"true", "1", "yes", "y", "on"}:
        return True
    if normalized in {"false", "0", "no", "n", "off"}:
        return False
    raise CliError(f"Invalid boolean for {key}. Use true/false.")


def _parse_settings_positive_int(raw_value: str, key: str) -> int:
    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise CliError(f"Invalid integer for {key}.") from exc
    if parsed <= 0:
        raise CliError(f"{key} must be greater than zero.")
    return parsed


def _parse_settings_volume_percent(raw_value: str, key: str) -> int:
    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise CliError(f"Invalid integer for {key}.") from exc
    return _validate_volume_percent(parsed)


def _parse_settings_panel_percent(raw_value: str, key: str) -> int:
    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise CliError(f"Invalid integer for {key}.") from exc
    if parsed < 10 or parsed > 90:
        raise CliError(f"{key} must be between 10 and 90.")
    return parsed


def _parse_settings_supported_extensions(raw_value: str, key: str) -> list[str]:
    raw = raw_value.strip()
    if not raw:
        raise CliError(f"{key} cannot be empty.")
    values: list[object]
    if raw.startswith("["):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CliError(f"Invalid JSON list for {key}.") from exc
        if not isinstance(decoded, list):
            raise CliError(f"{key} JSON value must be an array.")
        values = decoded
    else:
        values = [token.strip() for token in raw.split(",")]

    normalized: list[str] = []
    for item in values:
        token = str(item).strip().lower().lstrip(".")
        if not token:
            continue
        if not all(char.isalnum() for char in token):
            raise CliError(
                f"Invalid extension '{item}' for {key}. Use alphanumeric tokens like mp3, ogg."
            )
        if token in normalized:
            continue
        normalized.append(token)
    if not normalized:
        raise CliError(f"{key} cannot be empty.")
    return normalized


def _parse_settings_library_tabs(raw_value: str, key: str) -> list[LibraryTab]:
    raw = raw_value.strip()
    if not raw:
        raise CliError(f"{key} cannot be empty.")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CliError(
            f"Invalid JSON for {key}. Expected an array of objects with title/path."
        ) from exc
    if not isinstance(decoded, list):
        raise CliError(f"{key} JSON value must be an array.")
    normalized_tabs: list[LibraryTab] = []
    for item in decoded:
        if not isinstance(item, dict):
            raise CliError(f"{key} entries must be objects with title/path.")
        tab = LibraryTab.from_dict(item)
        if not tab.title or not tab.path:
            raise CliError(
                f"{key} entries require non-empty title and path. Invalid entry: {item}"
            )
        normalized_tabs.append(tab)
    return normalized_tabs


def _apply_setting_value(app_config: object, *, key: str, raw_value: str) -> None:
    if key == "fade_seconds":
        fade_seconds = _parse_settings_positive_int(raw_value, key)
        app_config.fade_in_duration_seconds = fade_seconds
        app_config.fade_out_duration_seconds = fade_seconds
        return
    if key == "filesystem_default_fade_in":
        app_config.filesystem_default_fade_in = _parse_settings_bool(raw_value, key)
        return
    if key == "filesystem_default_fade_out":
        app_config.filesystem_default_fade_out = _parse_settings_bool(raw_value, key)
        return
    if key == "streams_default_fade_in":
        app_config.streams_default_fade_in = _parse_settings_bool(raw_value, key)
        return
    if key == "streams_default_fade_out":
        app_config.streams_default_fade_out = _parse_settings_bool(raw_value, key)
        return
    if key == "default_volume_percent":
        app_config.default_volume_percent = _parse_settings_volume_percent(raw_value, key)
        return
    if key == "font_size":
        normalized = raw_value.strip().lower()
        if normalized in {"none", "null", "auto"}:
            app_config.font_size = None
            return
        app_config.font_size = _parse_settings_positive_int(raw_value, key)
        return
    if key == "media_library_width_percent":
        media_width = _parse_settings_panel_percent(raw_value, key)
        app_config.media_library_width_percent = media_width
        app_config.schedule_width_percent = 100 - media_width
        return
    if key == "schedule_width_percent":
        schedule_width = _parse_settings_panel_percent(raw_value, key)
        app_config.schedule_width_percent = schedule_width
        app_config.media_library_width_percent = 100 - schedule_width
        return
    if key == "greenwich_time_signal_enabled":
        app_config.greenwich_time_signal_enabled = _parse_settings_bool(raw_value, key)
        return
    if key == "greenwich_time_signal_path":
        app_config.greenwich_time_signal_path = raw_value.strip()
        return
    if key == "supported_extensions":
        app_config.supported_extensions = _parse_settings_supported_extensions(raw_value, key)
        return
    if key == "library_tabs":
        app_config.library_tabs = _parse_settings_library_tabs(raw_value, key)
        return
    raise CliError(f"Unsupported settings key: {key}")


def _setting_value_to_text(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    if value is None:
        return "null"
    return str(value)


def _media_item_to_dict(media_item: MediaItem) -> dict[str, object]:
    return {
        "id": media_item.id,
        "title": media_item.title,
        "source": media_item.source,
        "greenwich_time_signal_enabled": bool(media_item.greenwich_time_signal_enabled),
        "created_at": media_item.created_at.isoformat(),
    }


def _schedule_entry_to_dict(
    entry: ScheduleEntry,
    reference_time: datetime,
    media_by_id: dict[str, MediaItem] | None = None,
) -> dict[str, object]:
    media_title: str | None = None
    if media_by_id is not None:
        media = media_by_id.get(entry.media_id)
        media_title = media.title if media is not None else None
    return {
        "id": entry.id,
        "media_id": entry.media_id,
        "media_title": media_title,
        "start_at": normalized_start(entry.start_at, reference_time).isoformat(),
        "status": entry.status,
        "cron_id": entry.cron_id,
        "fade_in": bool(entry.fade_in),
        "fade_out": bool(entry.fade_out),
        "one_shot": bool(entry.one_shot),
    }


def _cron_entry_to_dict(
    entry: CronEntry,
    reference_time: datetime,
    media_by_id: dict[str, MediaItem] | None = None,
) -> dict[str, object]:
    media_title: str | None = None
    if media_by_id is not None:
        media = media_by_id.get(entry.media_id)
        media_title = media.title if media is not None else None
    next_occurrence = next_cron_occurrence(entry, reference_time)
    return {
        "id": entry.id,
        "media_id": entry.media_id,
        "media_title": media_title,
        "expression": entry.expression,
        "enabled": bool(entry.enabled),
        "fade_in": bool(entry.fade_in),
        "fade_out": bool(entry.fade_out),
        "created_at": entry.created_at.isoformat(),
        "next_occurrence": next_occurrence.isoformat() if next_occurrence is not None else None,
    }


def _sync_runtime_state(state: AppState, now: datetime) -> None:
    enforce_hard_sync_always(state.cron_entries, state.schedule_entries)
    state.schedule_entries = sync_cron_runtime_window(
        state.schedule_entries,
        state.cron_entries,
        target_dates=runtime_cron_dates(now),
        now=now,
    )
    prepare_schedule_entries_for_startup(state.schedule_entries, now)


def _save_runtime_state(context: StateContext, now: datetime) -> None:
    _sync_runtime_state(context.state, now)
    try:
        context.state_version = save_state(
            context.state_path,
            context.state,
            expected_version=context.state_version,
        )
    except StateVersionConflictError as exc:
        raise CliError(
            (
                "State changed in another process while this command was running "
                f"(expected version {exc.expected_version}, current {exc.current_version}). "
                "Please run the command again."
            )
        ) from exc


def _cmd_media_list(args: argparse.Namespace) -> int:
    context = _load_state_context(args.config)
    media_items = sorted(
        context.state.media_items,
        key=lambda item: (item.created_at, item.title.lower(), item.id),
    )
    if not media_items:
        _print_success(
            args,
            text="No media items found.",
            payload={
                "ok": True,
                "count": 0,
                "media": [],
            },
        )
        return 0
    if _json_enabled(args):
        _print_success(
            args,
            text="",
            payload={
                "ok": True,
                "count": len(media_items),
                "media": [_media_item_to_dict(item) for item in media_items],
            },
        )
        return 0
    print("MEDIA_ID\tTITLE\tSOURCE")
    for item in media_items:
        print(f"{item.id}\t{item.title}\t{item.source}")
    return 0


def _normalize_media_source(raw_source: str) -> str:
    source = raw_source.strip()
    if not source:
        raise CliError("Media source cannot be empty")
    if "://" in source:
        return source
    source_path = Path(source).expanduser()
    try:
        source_path = source_path.resolve()
    except OSError:
        pass
    if not source_path.exists():
        raise CliError(f"Source path does not exist: {source_path}")
    return str(source_path)


def _is_remote_stream_source(source: str) -> bool:
    return is_stream_source(source) and local_media_path_from_source(source) is None


def _normalize_stream_source(raw_source: str) -> str:
    source = raw_source.strip()
    if not source:
        raise CliError("Stream source cannot be empty")
    if not _is_remote_stream_source(source):
        raise CliError("Stream source must be a URL (http/https/rtsp/etc).")
    return source


def _stream_media_items(state: AppState) -> list[MediaItem]:
    return sorted(
        [item for item in state.media_items if _is_remote_stream_source(item.source)],
        key=lambda item: (item.created_at, item.title.lower(), item.id),
    )


def _cmd_media_add(args: argparse.Namespace) -> int:
    context = _load_state_context(args.config)
    source = _normalize_media_source(args.source)
    for existing in context.state.media_items:
        if existing.source == source:
            _print_success(
                args,
                text=f"Media already exists: {existing.id}",
                payload={
                    "ok": True,
                    "created": False,
                    "media": _media_item_to_dict(existing),
                },
            )
            return 0

    title = args.title.strip() if args.title else ""
    if not title:
        title = PurePath(source).name or source
    media_item = MediaItem.create(
        title=title,
        source=source,
    )
    context.state.media_items.append(media_item)
    try:
        context.state_version = save_state(
            context.state_path,
            context.state,
            expected_version=context.state_version,
        )
    except StateVersionConflictError as exc:
        raise CliError(
            (
                "State changed in another process while this command was running "
                f"(expected version {exc.expected_version}, current {exc.current_version}). "
                "Please run the command again."
            )
        ) from exc
    _print_success(
        args,
        text=f"Created media item: {media_item.id}",
        payload={
            "ok": True,
            "created": True,
            "media": _media_item_to_dict(media_item),
        },
    )
    return 0


def _cmd_streams_list(args: argparse.Namespace) -> int:
    context = _load_state_context(args.config)
    streams = _stream_media_items(context.state)
    if not streams:
        _print_success(
            args,
            text="No streams found.",
            payload={
                "ok": True,
                "count": 0,
                "streams": [],
            },
        )
        return 0
    if _json_enabled(args):
        _print_success(
            args,
            text="",
            payload={
                "ok": True,
                "count": len(streams),
                "streams": [_media_item_to_dict(item) for item in streams],
            },
        )
        return 0
    print("STREAM_ID\tTITLE\tURL\tGREENWICH_TIME_SIGNAL")
    for item in streams:
        print(
            f"{item.id}\t{item.title}\t{item.source}\t"
            f"{'true' if item.greenwich_time_signal_enabled else 'false'}"
        )
    return 0


def _cmd_streams_add(args: argparse.Namespace) -> int:
    context = _load_state_context(args.config)
    source = _normalize_stream_source(args.source)
    existing_stream = next(
        (item for item in context.state.media_items if item.source == source and _is_remote_stream_source(item.source)),
        None,
    )
    if existing_stream is not None:
        _print_success(
            args,
            text=f"Stream already exists: {existing_stream.id}",
            payload={
                "ok": True,
                "created": False,
                "stream": _media_item_to_dict(existing_stream),
            },
        )
        return 0
    title = args.title.strip() if args.title else source
    media_by_id = {item.id: item for item in context.state.media_items}
    created_stream = add_stream_media_item(
        media_by_id,
        {},
        title,
        source,
    )
    created_stream.greenwich_time_signal_enabled = _bool_from_token(args.greenwich_time_signal)
    context.state.media_items = list(media_by_id.values())
    try:
        context.state_version = save_state(
            context.state_path,
            context.state,
            expected_version=context.state_version,
        )
    except StateVersionConflictError as exc:
        raise CliError(
            (
                "State changed in another process while this command was running "
                f"(expected version {exc.expected_version}, current {exc.current_version}). "
                "Please run the command again."
            )
        ) from exc
    _print_success(
        args,
        text=f"Created stream: {created_stream.id}",
        payload={
            "ok": True,
            "created": True,
            "stream": _media_item_to_dict(created_stream),
        },
    )
    return 0


def _cmd_streams_edit(args: argparse.Namespace) -> int:
    context = _load_state_context(args.config)
    media_by_id = {item.id: item for item in context.state.media_items}
    stream = media_by_id.get(args.stream_id)
    if stream is None or not _is_remote_stream_source(stream.source):
        raise CliError(f"Stream '{args.stream_id}' not found")

    has_changes = False
    if args.source is not None:
        normalized_source = _normalize_stream_source(args.source)
        duplicate = next(
            (
                item
                for item in context.state.media_items
                if item.id != stream.id and item.source == normalized_source and _is_remote_stream_source(item.source)
            ),
            None,
        )
        if duplicate is not None:
            raise CliError(f"Another stream already uses this URL: {duplicate.id}")
        next_title = args.title if args.title is not None else stream.title
        updated = update_stream_media_item(
            media_by_id,
            {},
            stream.id,
            next_title,
            normalized_source,
        )
        if updated is not None:
            stream = updated
            has_changes = True
    elif args.title is not None:
        updated = update_stream_media_item(
            media_by_id,
            {},
            stream.id,
            args.title,
            stream.source,
        )
        if updated is not None:
            stream = updated
            has_changes = True

    if args.greenwich_time_signal is not None:
        next_enabled = _bool_from_token(args.greenwich_time_signal)
        if bool(stream.greenwich_time_signal_enabled) != next_enabled:
            updated_signal_stream = update_stream_greenwich_time_signal(
                media_by_id,
                stream.id,
                enabled=next_enabled,
            )
            if updated_signal_stream is not None:
                stream = updated_signal_stream
                has_changes = True

    if not has_changes:
        raise CliError("No changes were applied")

    context.state.media_items = list(media_by_id.values())
    try:
        context.state_version = save_state(
            context.state_path,
            context.state,
            expected_version=context.state_version,
        )
    except StateVersionConflictError as exc:
        raise CliError(
            (
                "State changed in another process while this command was running "
                f"(expected version {exc.expected_version}, current {exc.current_version}). "
                "Please run the command again."
            )
        ) from exc
    _print_success(
        args,
        text=f"Updated stream: {stream.id}",
        payload={
            "ok": True,
            "updated": True,
            "stream": _media_item_to_dict(stream),
        },
    )
    return 0


def _cmd_streams_remove(args: argparse.Namespace) -> int:
    context = _load_state_context(args.config)
    media_by_id = {item.id: item for item in context.state.media_items}
    stream = media_by_id.get(args.stream_id)
    if stream is None or not _is_remote_stream_source(stream.source):
        raise CliError(f"Stream '{args.stream_id}' not found")

    removal = remove_media_from_library(
        media_by_id,
        {},
        context.state.cron_entries,
        context.state.schedule_entries,
        deque(context.state.queue),
        args.stream_id,
    )
    if removal.removed_media is None:
        raise CliError(f"Stream '{args.stream_id}' was not removed")

    context.state.media_items = list(media_by_id.values())
    context.state.cron_entries = removal.cron_entries
    context.state.schedule_entries = removal.schedule_entries
    context.state.queue = list(removal.play_queue)
    _save_runtime_state(context, datetime.now().astimezone())
    _print_success(
        args,
        text=(
            f"Removed stream: {args.stream_id} "
            f"(removed_cron={removal.removed_cron_count}, removed_schedule={removal.removed_schedule_count})"
        ),
        payload={
            "ok": True,
            "removed": True,
            "stream_id": args.stream_id,
            "removed_cron_count": removal.removed_cron_count,
            "removed_schedule_count": removal.removed_schedule_count,
            "removed_queue_count": removal.removed_queue_count,
        },
    )
    return 0


def _cmd_schedule_list(args: argparse.Namespace) -> int:
    context = _load_state_context(args.config)
    now = datetime.now().astimezone()
    runtime_entries = sync_cron_runtime_window(
        context.state.schedule_entries,
        context.state.cron_entries,
        target_dates=runtime_cron_dates(now),
        now=now,
    )
    media_by_id = {item.id: item for item in context.state.media_items}
    if args.all:
        entries = sort_schedule_entries(runtime_entries, now)
    else:
        target_date = _parse_date(args.date) if args.date else now.date()
        entries = visible_schedule_entries(runtime_entries, target_date, now)
    if not entries:
        _print_success(
            args,
            text="No schedule entries found.",
            payload={
                "ok": True,
                "count": 0,
                "entries": [],
            },
        )
        return 0
    if _json_enabled(args):
        _print_success(
            args,
            text="",
            payload={
                "ok": True,
                "count": len(entries),
                "entries": [
                    _schedule_entry_to_dict(entry, now, media_by_id=media_by_id)
                    for entry in entries
                ],
            },
        )
        return 0
    print("ENTRY_ID\tSTART\tSTATUS\tMEDIA\tCRON_ID\tFADE_IN\tFADE_OUT")
    for entry in entries:
        media = media_by_id.get(entry.media_id)
        media_label = media.title if media is not None else f"<missing:{entry.media_id}>"
        print(
            f"{entry.id}\t{_format_datetime(entry.start_at, now)}\t{entry.status}\t{media_label}"
            f"\t{entry.cron_id or '-'}\t{entry.fade_in}\t{entry.fade_out}"
        )
    return 0


def _cmd_schedule_add(args: argparse.Namespace) -> int:
    context = _load_state_context(args.config)
    _ensure_media_exists(context.state, args.media_id)
    now = datetime.now().astimezone()
    entry = create_schedule_entry(
        media_id=args.media_id,
        start_at=_parse_datetime(args.start),
        reference_time=now,
        fade_in=args.fade_in,
        fade_out=args.fade_out,
    )
    context.state.schedule_entries.append(entry)
    _save_runtime_state(context, now)
    _print_success(
        args,
        text=f"Created schedule entry: {entry.id} (status={entry.status})",
        payload={
            "ok": True,
            "created": True,
            "entry": _schedule_entry_to_dict(entry, now),
        },
    )
    return 0


def _status_cli_value_to_mutation_value(status: str) -> str:
    return "Pending" if status == "pending" else "Disabled"


def _cmd_schedule_bulk_add(args: argparse.Namespace) -> int:
    context = _load_state_context(args.config)
    _ensure_media_exists(context.state, args.media_id)
    starts = [_parse_datetime(raw_start) for raw_start in args.start]
    if not starts:
        raise CliError("Provide at least one --start value")
    now = datetime.now().astimezone()
    created_entries: list[ScheduleEntry] = []
    for start_at in starts:
        entry = create_schedule_entry(
            media_id=args.media_id,
            start_at=start_at,
            reference_time=now,
            fade_in=args.fade_in,
            fade_out=args.fade_out,
        )
        context.state.schedule_entries.append(entry)
        created_entries.append(entry)
    _save_runtime_state(context, now)
    _print_success(
        args,
        text=f"Created {len(created_entries)} schedule entries.",
        payload={
            "ok": True,
            "created_count": len(created_entries),
            "entries": [_schedule_entry_to_dict(entry, now) for entry in created_entries],
        },
    )
    return 0


def _cmd_schedule_bulk_status(args: argparse.Namespace) -> int:
    context = _load_state_context(args.config)
    now = datetime.now().astimezone()
    _sync_runtime_state(context.state, now)
    status_value = _status_cli_value_to_mutation_value(args.status)
    cron_entries_by_id = {cron_entry.id: cron_entry for cron_entry in context.state.cron_entries}

    matching_entries: list[ScheduleEntry] = []
    if args.entry_id:
        selected_entry_ids = set(args.entry_id)
        matching_entries = [
            entry
            for entry in context.state.schedule_entries
            if entry.id in selected_entry_ids
            and (args.media_id is None or entry.media_id == args.media_id)
        ]
        missing_ids = sorted(selected_entry_ids - {entry.id for entry in matching_entries})
        if missing_ids:
            raise CliError(f"Some entry ids were not found: {', '.join(missing_ids)}")
    else:
        target_date = _parse_date(args.date)
        matching_entries = [
            entry
            for entry in context.state.schedule_entries
            if normalized_start(entry.start_at, now).date() == target_date
            and (args.media_id is None or entry.media_id == args.media_id)
        ]

    if not matching_entries:
        raise CliError("No schedule entries matched the bulk filter")

    updated_entries: list[ScheduleEntry] = []
    unchanged_count = 0
    blocked_count = 0
    for entry in matching_entries:
        mutation_result = update_schedule_status(
            context.state.schedule_entries,
            entry.id,
            value=status_value,
            reference_time=now,
            cron_entry_by_id=lambda cron_id: cron_entries_by_id.get(cron_id or ""),
        )
        if mutation_result.refresh_only:
            blocked_count += 1
            continue
        if mutation_result.updated_entry is None:
            unchanged_count += 1
            continue
        updated_entries.append(mutation_result.updated_entry)

    if not updated_entries:
        if blocked_count > 0:
            raise CliError(
                "No entries were updated because matching CRON parent rules are disabled or protected"
            )
        raise CliError("No changes were applied")

    _save_runtime_state(context, now)
    _print_success(
        args,
        text=(
            f"Bulk status update complete: matched={len(matching_entries)}, "
            f"updated={len(updated_entries)}, unchanged={unchanged_count}, blocked={blocked_count}"
        ),
        payload={
            "ok": True,
            "matched_count": len(matching_entries),
            "updated_count": len(updated_entries),
            "unchanged_count": unchanged_count,
            "blocked_count": blocked_count,
            "updated_entries": [
                _schedule_entry_to_dict(entry, now)
                for entry in updated_entries
            ],
        },
    )
    return 0


def _cmd_schedule_edit(args: argparse.Namespace) -> int:
    context = _load_state_context(args.config)
    entry = _find_schedule_entry(context.state, args.entry_id)
    has_changes = False
    now = datetime.now().astimezone()

    if args.start is not None:
        if entry.cron_id is not None:
            raise CliError("Cannot change start time on a CRON-generated schedule entry")
        next_start = _parse_datetime(args.start)
        if entry.start_at != next_start:
            entry.start_at = next_start
            has_changes = True

    if args.media_id is not None:
        if entry.cron_id is not None:
            raise CliError("Cannot change media on a CRON-generated schedule entry")
        _ensure_media_exists(context.state, args.media_id)
        if entry.media_id != args.media_id:
            entry.media_id = args.media_id
            has_changes = True

    cron_entries_by_id = {cron_entry.id: cron_entry for cron_entry in context.state.cron_entries}
    if args.fade_in is not None:
        if update_schedule_fade_in(
            context.state.schedule_entries,
            entry.id,
            fade_in_enabled=_bool_from_token(args.fade_in),
            cron_entry_by_id=lambda cron_id: cron_entries_by_id.get(cron_id or ""),
        ):
            has_changes = True
    if args.fade_out is not None:
        if update_schedule_fade_out(
            context.state.schedule_entries,
            entry.id,
            fade_out_enabled=_bool_from_token(args.fade_out),
            cron_entry_by_id=lambda cron_id: cron_entries_by_id.get(cron_id or ""),
        ):
            has_changes = True
    if args.status is not None:
        status_value = _status_cli_value_to_mutation_value(args.status)
        status_result = update_schedule_status(
            context.state.schedule_entries,
            entry.id,
            value=status_value,
            reference_time=now,
            cron_entry_by_id=lambda cron_id: cron_entries_by_id.get(cron_id or ""),
        )
        if status_result.refresh_only:
            raise CliError("Cannot edit status while parent CRON rule is disabled")
        if status_result.updated_entry is not None:
            has_changes = True

    if not has_changes:
        raise CliError("No changes were applied")

    _save_runtime_state(context, now)
    _print_success(
        args,
        text=f"Updated schedule entry: {entry.id}",
        payload={
            "ok": True,
            "updated": True,
            "entry": _schedule_entry_to_dict(entry, now),
        },
    )
    return 0


def _cmd_schedule_remove(args: argparse.Namespace) -> int:
    context = _load_state_context(args.config)
    entry_ids = set(args.entry_ids)
    if not entry_ids:
        raise CliError("Provide at least one schedule entry id")

    cron_entries_by_id = {entry.id: entry for entry in context.state.cron_entries}
    selection = select_schedule_entries_for_removal(
        context.state.schedule_entries,
        entry_ids=entry_ids,
        is_protected=lambda entry: is_schedule_entry_protected_from_removal(entry, cron_entries_by_id),
    )
    if not selection.entries_to_remove:
        raise CliError("No matching schedule entries found")
    if selection.protected_entries and not args.force:
        protected_ids = ", ".join(sorted(entry.id for entry in selection.protected_entries))
        raise CliError(
            "Some entries are CRON-managed and protected from direct removal. "
            f"Use --force or disable/remove the CRON rule first. ({protected_ids})"
        )

    context.state.schedule_entries = remove_schedule_entries_by_ids(
        context.state.schedule_entries,
        entry_ids=entry_ids,
    )
    _save_runtime_state(context, datetime.now().astimezone())
    removed_count = len(selection.entries_to_remove)
    _print_success(
        args,
        text=f"Removed {removed_count} schedule entr{'y' if removed_count == 1 else 'ies'}.",
        payload={
            "ok": True,
            "removed_count": removed_count,
            "removed_entry_ids": sorted(entry.id for entry in selection.entries_to_remove),
        },
    )
    if args.force and selection.protected_entries:
        _print_warning(
            args,
            "Warning: forced CRON-managed entries can be regenerated while the CRON rule is enabled.",
        )
    return 0


def _cmd_cron_list(args: argparse.Namespace) -> int:
    context = _load_state_context(args.config)
    media_by_id = {item.id: item for item in context.state.media_items}
    now = datetime.now().astimezone()
    cron_entries = sorted(
        context.state.cron_entries,
        key=lambda entry: (entry.created_at, entry.id),
    )
    if not cron_entries:
        _print_success(
            args,
            text="No CRON entries found.",
            payload={
                "ok": True,
                "count": 0,
                "entries": [],
            },
        )
        return 0
    if _json_enabled(args):
        _print_success(
            args,
            text="",
            payload={
                "ok": True,
                "count": len(cron_entries),
                "entries": [
                    _cron_entry_to_dict(entry, now, media_by_id=media_by_id)
                    for entry in cron_entries
                ],
            },
        )
        return 0
    print("CRON_ID\tEXPRESSION\tMEDIA\tENABLED\tFADE_IN\tFADE_OUT\tNEXT_OCCURRENCE")
    for entry in cron_entries:
        media = media_by_id.get(entry.media_id)
        media_label = media.title if media is not None else f"<missing:{entry.media_id}>"
        next_occurrence = next_cron_occurrence(entry, now)
        next_label = _format_datetime(next_occurrence, now) if next_occurrence is not None else "-"
        print(
            f"{entry.id}\t{entry.expression}\t{media_label}\t{entry.enabled}\t"
            f"{entry.fade_in}\t{entry.fade_out}\t{next_label}"
        )
    return 0


def _cmd_cron_add(args: argparse.Namespace) -> int:
    context = _load_state_context(args.config)
    _ensure_media_exists(context.state, args.media_id)
    try:
        CronExpression.parse(args.expression)
    except CronParseError as exc:
        raise CliError(str(exc)) from exc

    entry = create_cron_entry(
        media_id=args.media_id,
        expression=args.expression,
        fade_in=args.fade_in,
        fade_out=args.fade_out,
    )
    if args.enabled is not None:
        entry.enabled = _bool_from_token(args.enabled)
    context.state.cron_entries.append(entry)
    now = datetime.now().astimezone()
    _save_runtime_state(context, now)
    _print_success(
        args,
        text=f"Created CRON entry: {entry.id}",
        payload={
            "ok": True,
            "created": True,
            "entry": _cron_entry_to_dict(entry, now),
        },
    )
    return 0


def _cmd_cron_edit(args: argparse.Namespace) -> int:
    context = _load_state_context(args.config)
    entry = _find_cron_entry(context.state, args.cron_id)
    has_changes = False

    if args.expression is not None:
        try:
            CronExpression.parse(args.expression)
        except CronParseError as exc:
            raise CliError(str(exc)) from exc
        if update_cron_expression(entry, expression=args.expression):
            has_changes = True
    if args.media_id is not None:
        _ensure_media_exists(context.state, args.media_id)
        if entry.media_id != args.media_id:
            entry.media_id = args.media_id
            has_changes = True
    if args.fade_in is not None:
        if update_cron_fade_in(
            context.state.cron_entries,
            entry.id,
            fade_in_enabled=_bool_from_token(args.fade_in),
        ):
            has_changes = True
    if args.fade_out is not None:
        if update_cron_fade_out(
            context.state.cron_entries,
            entry.id,
            fade_out_enabled=_bool_from_token(args.fade_out),
        ):
            has_changes = True
    if args.enabled is not None:
        if update_cron_enabled(
            context.state.cron_entries,
            entry.id,
            enabled=_bool_from_token(args.enabled),
        ):
            has_changes = True

    if not has_changes:
        raise CliError("No changes were applied")

    now = datetime.now().astimezone()
    _save_runtime_state(context, now)
    _print_success(
        args,
        text=f"Updated CRON entry: {entry.id}",
        payload={
            "ok": True,
            "updated": True,
            "entry": _cron_entry_to_dict(entry, now),
        },
    )
    return 0


def _cmd_cron_remove(args: argparse.Namespace) -> int:
    context = _load_state_context(args.config)
    _find_cron_entry(context.state, args.cron_id)
    previous_count = len(context.state.cron_entries)
    context.state.cron_entries, context.state.schedule_entries = remove_cron_and_generated_schedule_entries(
        context.state.cron_entries,
        context.state.schedule_entries,
        cron_id=args.cron_id,
    )
    if len(context.state.cron_entries) == previous_count:
        raise CliError("CRON entry was not removed")
    _save_runtime_state(context, datetime.now().astimezone())
    _print_success(
        args,
        text=f"Removed CRON entry: {args.cron_id}",
        payload={
            "ok": True,
            "removed": True,
            "cron_id": args.cron_id,
        },
    )
    return 0


def _cmd_settings_get(args: argparse.Namespace) -> int:
    config_dir, settings_path, app_config = _load_app_config_context(args.config)
    del config_dir
    settings_data = _settings_to_dict(app_config)
    if args.key:
        key = _normalize_settings_key(args.key)
        value = settings_data[key]
        _print_success(
            args,
            text=f"{key}={_setting_value_to_text(value)}",
            payload={
                "ok": True,
                "key": key,
                "value": value,
                "settings_path": str(settings_path),
            },
        )
        return 0

    if _json_enabled(args):
        _print_success(
            args,
            text="",
            payload={
                "ok": True,
                "settings": settings_data,
                "settings_path": str(settings_path),
            },
        )
        return 0

    print("KEY\tVALUE")
    for key in SUPPORTED_SETTINGS_KEYS:
        print(f"{key}\t{_setting_value_to_text(settings_data[key])}")
    return 0


def _cmd_settings_set(args: argparse.Namespace) -> int:
    config_dir, settings_path, app_config = _load_app_config_context(args.config)
    del config_dir
    key = _normalize_settings_key(args.key)
    before_data = _settings_to_dict(app_config)
    before_value = before_data[key]
    _apply_setting_value(app_config, key=key, raw_value=args.value)
    after_data = _settings_to_dict(app_config)
    after_value = after_data[key]
    changed = before_value != after_value
    if changed:
        save_app_config(settings_path, app_config)

    _print_success(
        args,
        text=(
            f"{'Updated' if changed else 'No change for'} setting {key}: "
            f"{_setting_value_to_text(after_value)}"
        ),
        payload={
            "ok": True,
            "changed": changed,
            "key": key,
            "value": after_value,
            "settings_path": str(settings_path),
        },
    )
    return 0


def _validate_positive_pid(raw_pid: int | None) -> int | None:
    if raw_pid is None:
        return None
    if raw_pid <= 0:
        raise CliError("PID must be a positive integer")
    return raw_pid


def _validate_volume_percent(raw_value: int) -> int:
    if raw_value < 0 or raw_value > 100:
        raise CliError("Volume must be between 0 and 100")
    return int(raw_value)


def _runtime_status_payload(config_dir: Path) -> dict[str, object]:
    status_path = runtime_status_file_path(config_dir)
    view = resolve_runtime_status(config_dir)
    if view.stale:
        # Auto-heal stale lock files so "offline" is represented by lock absence.
        delete_runtime_lock(config_dir)
        view = resolve_runtime_status(config_dir)
    return {
        "ok": True,
        "status": view.status,
        "effective_status": view.effective_status,
        "pid": view.pid,
        "process_running": view.process_running,
        "stale": view.stale,
        "lock_exists": status_path.is_file(),
        "status_path": str(status_path),
    }


def _runtime_status_text(payload: dict[str, object]) -> str:
    pid_label = payload["pid"] if payload["pid"] is not None else "-"
    return (
        f"Runtime status: {payload['effective_status']} "
        f"(pid={pid_label}, lock_exists={payload['lock_exists']}, "
        f"process_running={payload['process_running']})"
    )


def _cmd_runtime_status(args: argparse.Namespace) -> int:
    config_dir = _config_dir_from_args(args.config)
    payload = _runtime_status_payload(config_dir)
    _print_success(
        args,
        text=_runtime_status_text(payload),
        payload=payload,
    )
    return 0


def _cmd_runtime_watch(args: argparse.Namespace) -> int:
    config_dir = _config_dir_from_args(args.config)
    if args.interval <= 0:
        raise CliError("Interval must be greater than zero")
    if args.timeout is not None and args.timeout < 0:
        raise CliError("Timeout must be zero or greater")

    started_at = time.monotonic()
    last_snapshot: str | None = None
    emitted_events = 0
    max_events = args.max_events
    if max_events is not None and max_events <= 0:
        raise CliError("max-events must be greater than zero")

    try:
        while True:
            payload = _runtime_status_payload(config_dir)
            snapshot = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=True)
            if snapshot != last_snapshot:
                if _json_enabled(args):
                    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=True))
                else:
                    print(_runtime_status_text(payload))
                last_snapshot = snapshot
                emitted_events += 1
                if max_events is not None and emitted_events >= max_events:
                    return 0

            if args.once:
                return 0
            if args.timeout is not None and (time.monotonic() - started_at) >= args.timeout:
                return 0

            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 130


def _cmd_runtime_control_action(
    args: argparse.Namespace,
    *,
    action: str,
    action_label: str,
    value: int | None = None,
) -> int:
    config_dir = _config_dir_from_args(args.config)
    status = resolve_runtime_status(config_dir)
    if not status.process_running:
        raise CliError(
            "GUI runtime is not running. Start radioqt first and retry the command."
        )
    command = enqueue_runtime_control_command(config_dir, action=action, value=value)
    control_path = runtime_control_file_path(config_dir)
    command_details = (
        f"id={command.command_id}, pid={status.pid if status.pid is not None else '-'}"
    )
    if value is not None:
        command_details += f", value={value}"
    _print_success(
        args,
        text=(
            f"Queued runtime command: {action_label} "
            f"({command_details})"
        ),
        payload={
            "ok": True,
            "queued": True,
            "command_id": command.command_id,
            "action": command.action,
            "value": command.value,
            "pid": status.pid,
            "control_path": str(control_path),
        },
    )
    return 0


def _cmd_runtime_fade_in(args: argparse.Namespace) -> int:
    return _cmd_runtime_control_action(
        args,
        action=RUNTIME_CONTROL_ACTION_FADE_IN,
        action_label="fade-in",
    )


def _cmd_runtime_fade_out(args: argparse.Namespace) -> int:
    return _cmd_runtime_control_action(
        args,
        action=RUNTIME_CONTROL_ACTION_FADE_OUT,
        action_label="fade-out",
    )


def _cmd_runtime_online(args: argparse.Namespace) -> int:
    return _cmd_runtime_control_action(
        args,
        action=RUNTIME_CONTROL_ACTION_START_AUTOMATION,
        action_label="online",
    )


def _cmd_runtime_offline(args: argparse.Namespace) -> int:
    return _cmd_runtime_control_action(
        args,
        action=RUNTIME_CONTROL_ACTION_STOP_AUTOMATION,
        action_label="offline",
    )


def _cmd_runtime_volume(args: argparse.Namespace) -> int:
    value = _validate_volume_percent(args.value)
    return _cmd_runtime_control_action(
        args,
        action=RUNTIME_CONTROL_ACTION_SET_VOLUME,
        action_label="set-volume",
        value=value,
    )


def _cmd_runtime_mute(args: argparse.Namespace) -> int:
    return _cmd_runtime_control_action(
        args,
        action=RUNTIME_CONTROL_ACTION_SET_VOLUME,
        action_label="mute",
        value=0,
    )


def _cmd_runtime_set_status(args: argparse.Namespace) -> int:
    config_dir = _config_dir_from_args(args.config)
    status_path = runtime_status_file_path(config_dir)
    pid = _validate_positive_pid(args.pid)
    status = args.value
    if status == RUNTIME_STATUS_ONLINE and pid is None:
        raise CliError("When setting status to online, provide --pid")
    if status == RUNTIME_STATUS_OFFLINE:
        if pid is None:
            pid = read_runtime_status(config_dir).pid

    record = write_runtime_status(config_dir, status=status, pid=pid)
    _print_success(
        args,
        text=(
            f"Runtime status updated: {record.status}"
            f" (pid={record.pid if record.pid is not None else '-'})"
        ),
        payload={
            "ok": True,
            "status": record.status,
            "pid": record.pid,
            "lock_exists": status_path.is_file(),
            "status_path": str(status_path),
        },
    )
    return 0


def _wait_for_process_shutdown(pid: int, timeout_seconds: float) -> bool:
    if timeout_seconds <= 0:
        return not is_pid_running(pid)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() <= deadline:
        if not is_pid_running(pid):
            return True
        time.sleep(0.1)
    return not is_pid_running(pid)


def _cmd_runtime_stop(args: argparse.Namespace) -> int:
    config_dir = _config_dir_from_args(args.config)
    explicit_pid = _validate_positive_pid(args.pid)
    status_view = resolve_runtime_status(config_dir)
    target_pid = explicit_pid if explicit_pid is not None else status_view.pid
    timeout_seconds = float(args.timeout)
    if timeout_seconds < 0:
        raise CliError("Timeout must be zero or greater")
    if target_pid is None:
        raise CliError("No runtime PID is available. Start the GUI first or pass --pid.")
    if target_pid == os.getpid():
        raise CliError("Refusing to stop the current CLI process")

    already_stopped = not is_pid_running(target_pid)
    sent_signal = "none"
    if not already_stopped:
        try:
            os.kill(target_pid, signal.SIGTERM)
            sent_signal = "SIGTERM"
        except ProcessLookupError:
            already_stopped = True
        except PermissionError as exc:
            raise CliError(f"Permission denied when stopping PID {target_pid}") from exc
        except OSError as exc:
            raise CliError(f"Failed to send SIGTERM to PID {target_pid}: {exc}") from exc

    stopped = already_stopped or _wait_for_process_shutdown(target_pid, timeout_seconds)
    if not stopped:
        # Give Qt/Python a short post-TERM grace window to complete shutdown
        # and avoid stale "online" lock states due to close races.
        stopped = _wait_for_process_shutdown(target_pid, 1.5)
    if not stopped and args.force:
        try:
            os.kill(target_pid, signal.SIGKILL)
            sent_signal = "SIGKILL"
        except ProcessLookupError:
            stopped = True
        except PermissionError as exc:
            raise CliError(f"Permission denied when force stopping PID {target_pid}") from exc
        except OSError as exc:
            raise CliError(f"Failed to send SIGKILL to PID {target_pid}: {exc}") from exc
        if not stopped:
            force_timeout = max(1.0, min(timeout_seconds, 3.0))
            stopped = _wait_for_process_shutdown(target_pid, force_timeout)

    if not stopped:
        raise CliError(
            (
                f"PID {target_pid} is still running after {timeout_seconds:.1f}s. "
                "Use --force to send SIGKILL or increase --timeout."
            )
        )

    delete_runtime_lock(config_dir)
    _print_success(
        args,
        text=f"Runtime stopped (pid={target_pid}, signal={sent_signal}).",
        payload={
            "ok": True,
            "stopped": True,
            "pid": target_pid,
            "signal": sent_signal,
            "status": RUNTIME_STATUS_OFFLINE,
            "lock_exists": runtime_status_file_path(config_dir).is_file(),
            "status_path": str(runtime_status_file_path(config_dir)),
        },
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="radioqt-cli",
        description="CLI for managing RadioQt schedules and CRON rules.",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_DIR),
        help=f"Configuration directory (default: {DEFAULT_CONFIG_DIR})",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit machine-readable JSON output",
    )

    top_level_subparsers = parser.add_subparsers(dest="resource", required=True)

    settings_parser = top_level_subparsers.add_parser("settings", help="Application settings commands")
    settings_subparsers = settings_parser.add_subparsers(dest="settings_command", required=True)

    settings_get_parser = settings_subparsers.add_parser(
        "get",
        help="Show one setting value or all settings",
    )
    settings_get_parser.add_argument(
        "key",
        nargs="?",
        help="Optional setting key",
    )
    settings_get_parser.set_defaults(handler=_cmd_settings_get)

    settings_set_parser = settings_subparsers.add_parser(
        "set",
        help="Set one setting value",
    )
    settings_set_parser.add_argument("key", help="Setting key")
    settings_set_parser.add_argument("value", help="Setting value")
    settings_set_parser.set_defaults(handler=_cmd_settings_set)

    media_parser = top_level_subparsers.add_parser("media", help="Media library commands")
    media_subparsers = media_parser.add_subparsers(dest="media_command", required=True)
    media_list_parser = media_subparsers.add_parser("list", help="List media items")
    media_list_parser.set_defaults(handler=_cmd_media_list)
    media_add_parser = media_subparsers.add_parser("add", help="Add a media item")
    media_add_parser.add_argument("--source", required=True, help="Local file path or stream URL")
    media_add_parser.add_argument("--title", help="Optional display title")
    media_add_parser.set_defaults(handler=_cmd_media_add)

    streams_parser = top_level_subparsers.add_parser("streams", help="Streaming media commands")
    streams_subparsers = streams_parser.add_subparsers(dest="streams_command", required=True)
    streams_list_parser = streams_subparsers.add_parser("list", help="List stream entries")
    streams_list_parser.set_defaults(handler=_cmd_streams_list)
    streams_add_parser = streams_subparsers.add_parser("add", help="Add a stream URL")
    streams_add_parser.add_argument("--source", required=True, help="Stream URL (http/https/rtsp/etc)")
    streams_add_parser.add_argument("--title", help="Optional display title")
    streams_add_parser.add_argument(
        "--greenwich-time-signal",
        choices=("true", "false"),
        default="false",
        help="Enable/disable Greenwich time signal for this stream (default: false)",
    )
    streams_add_parser.set_defaults(handler=_cmd_streams_add)
    streams_edit_parser = streams_subparsers.add_parser("edit", help="Edit a stream")
    streams_edit_parser.add_argument("stream_id", help="Stream media id")
    streams_edit_parser.add_argument("--source", help="New stream URL")
    streams_edit_parser.add_argument("--title", help="New display title")
    streams_edit_parser.add_argument(
        "--greenwich-time-signal",
        choices=("true", "false"),
        help="Set Greenwich time signal for this stream",
    )
    streams_edit_parser.set_defaults(handler=_cmd_streams_edit)
    streams_remove_parser = streams_subparsers.add_parser("remove", help="Remove a stream")
    streams_remove_parser.add_argument("stream_id", help="Stream media id")
    streams_remove_parser.set_defaults(handler=_cmd_streams_remove)

    schedule_parser = top_level_subparsers.add_parser("schedule", help="Schedule commands")
    schedule_subparsers = schedule_parser.add_subparsers(dest="schedule_command", required=True)

    schedule_list_parser = schedule_subparsers.add_parser("list", help="List schedule entries")
    schedule_list_parser.add_argument("--date", help="Filter by date (YYYY-MM-DD)")
    schedule_list_parser.add_argument("--all", action="store_true", help="List all dates")
    schedule_list_parser.set_defaults(handler=_cmd_schedule_list)

    schedule_add_parser = schedule_subparsers.add_parser("add", help="Create a schedule entry")
    schedule_add_parser.add_argument("--media-id", required=True, help="Media id to schedule")
    schedule_add_parser.add_argument("--start", required=True, help="Start datetime (ISO format)")
    schedule_add_parser.add_argument("--fade-in", action="store_true", help="Enable fade in")
    schedule_add_parser.add_argument("--fade-out", action="store_true", help="Enable fade out")
    schedule_add_parser.set_defaults(handler=_cmd_schedule_add)

    schedule_bulk_add_parser = schedule_subparsers.add_parser(
        "bulk-add",
        help="Create multiple schedule entries",
    )
    schedule_bulk_add_parser.add_argument("--media-id", required=True, help="Media id to schedule")
    schedule_bulk_add_parser.add_argument(
        "--start",
        required=True,
        action="append",
        help="Start datetime in ISO format. Repeat --start for multiple entries.",
    )
    schedule_bulk_add_parser.add_argument("--fade-in", action="store_true", help="Enable fade in")
    schedule_bulk_add_parser.add_argument("--fade-out", action="store_true", help="Enable fade out")
    schedule_bulk_add_parser.set_defaults(handler=_cmd_schedule_bulk_add)

    schedule_edit_parser = schedule_subparsers.add_parser("edit", help="Edit a schedule entry")
    schedule_edit_parser.add_argument("entry_id", help="Schedule entry id")
    schedule_edit_parser.add_argument("--start", help="New start datetime (ISO format)")
    schedule_edit_parser.add_argument("--media-id", help="New media id")
    schedule_edit_parser.add_argument("--fade-in", choices=("true", "false"), help="Set fade in")
    schedule_edit_parser.add_argument("--fade-out", choices=("true", "false"), help="Set fade out")
    schedule_edit_parser.add_argument(
        "--status",
        choices=("pending", "disabled"),
        help="Set status",
    )
    schedule_edit_parser.set_defaults(handler=_cmd_schedule_edit)

    schedule_remove_parser = schedule_subparsers.add_parser("remove", help="Remove schedule entries")
    schedule_remove_parser.add_argument("entry_ids", nargs="+", help="Schedule entry ids")
    schedule_remove_parser.add_argument(
        "--force",
        action="store_true",
        help="Force removal of CRON-managed runtime entries",
    )
    schedule_remove_parser.set_defaults(handler=_cmd_schedule_remove)

    schedule_bulk_status_parser = schedule_subparsers.add_parser(
        "bulk-status",
        help="Bulk update schedule entry status",
    )
    schedule_bulk_status_group = schedule_bulk_status_parser.add_mutually_exclusive_group(required=True)
    schedule_bulk_status_group.add_argument(
        "--date",
        help="Filter by date (YYYY-MM-DD)",
    )
    schedule_bulk_status_group.add_argument(
        "--entry-id",
        action="append",
        help="Specific entry id. Repeat for multiple entries.",
    )
    schedule_bulk_status_parser.add_argument(
        "--media-id",
        help="Optional media id filter",
    )
    schedule_bulk_status_parser.add_argument(
        "--status",
        required=True,
        choices=("pending", "disabled"),
        help="Target status",
    )
    schedule_bulk_status_parser.set_defaults(handler=_cmd_schedule_bulk_status)

    cron_parser = top_level_subparsers.add_parser("cron", help="CRON commands")
    cron_subparsers = cron_parser.add_subparsers(dest="cron_command", required=True)

    cron_list_parser = cron_subparsers.add_parser("list", help="List CRON entries")
    cron_list_parser.set_defaults(handler=_cmd_cron_list)

    cron_add_parser = cron_subparsers.add_parser("add", help="Create a CRON entry")
    cron_add_parser.add_argument("--media-id", required=True, help="Media id")
    cron_add_parser.add_argument("--expression", required=True, help="CRON expression with seconds")
    cron_add_parser.add_argument("--fade-in", action="store_true", help="Enable fade in")
    cron_add_parser.add_argument("--fade-out", action="store_true", help="Enable fade out")
    cron_add_parser.add_argument(
        "--enabled",
        choices=("true", "false"),
        help="Enable or disable the rule after creation (default: true)",
    )
    cron_add_parser.set_defaults(handler=_cmd_cron_add)

    cron_edit_parser = cron_subparsers.add_parser("edit", help="Edit a CRON entry")
    cron_edit_parser.add_argument("cron_id", help="CRON id")
    cron_edit_parser.add_argument("--expression", help="CRON expression with seconds")
    cron_edit_parser.add_argument("--media-id", help="New media id")
    cron_edit_parser.add_argument("--fade-in", choices=("true", "false"), help="Set fade in")
    cron_edit_parser.add_argument("--fade-out", choices=("true", "false"), help="Set fade out")
    cron_edit_parser.add_argument("--enabled", choices=("true", "false"), help="Set enabled status")
    cron_edit_parser.set_defaults(handler=_cmd_cron_edit)

    cron_remove_parser = cron_subparsers.add_parser("remove", help="Remove a CRON entry")
    cron_remove_parser.add_argument("cron_id", help="CRON id")
    cron_remove_parser.set_defaults(handler=_cmd_cron_remove)

    runtime_parser = top_level_subparsers.add_parser("runtime", help="Runtime process commands")
    runtime_subparsers = runtime_parser.add_subparsers(dest="runtime_command", required=True)

    runtime_status_parser = runtime_subparsers.add_parser(
        "status",
        help="Show GUI runtime status and PID",
    )
    runtime_status_parser.set_defaults(handler=_cmd_runtime_status)

    runtime_set_status_parser = runtime_subparsers.add_parser(
        "set-status",
        help="Write runtime status file",
    )
    runtime_set_status_parser.add_argument(
        "--value",
        required=True,
        choices=tuple(sorted(VALID_RUNTIME_STATUSES)),
        help="Runtime status value",
    )
    runtime_set_status_parser.add_argument(
        "--pid",
        type=int,
        help="Process ID (required when --value=online)",
    )
    runtime_set_status_parser.set_defaults(handler=_cmd_runtime_set_status)

    runtime_stop_parser = runtime_subparsers.add_parser(
        "stop",
        help="Stop the running GUI process from the lock file",
    )
    runtime_stop_parser.add_argument("--pid", type=int, help="Optional PID override")
    runtime_stop_parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Seconds to wait after SIGTERM before failing (default: 10)",
    )
    runtime_stop_parser.add_argument(
        "--force",
        action="store_true",
        help="Send SIGKILL if SIGTERM does not stop the process",
    )
    runtime_stop_parser.set_defaults(handler=_cmd_runtime_stop)

    runtime_watch_parser = runtime_subparsers.add_parser(
        "watch",
        help="Watch runtime lock/status changes in real time",
    )
    runtime_watch_parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds (default: 1.0)",
    )
    runtime_watch_parser.add_argument(
        "--timeout",
        type=float,
        help="Stop watching after this many seconds",
    )
    runtime_watch_parser.add_argument(
        "--once",
        action="store_true",
        help="Print current status once and exit",
    )
    runtime_watch_parser.add_argument(
        "--max-events",
        type=int,
        help="Stop after emitting this many status changes",
    )
    runtime_watch_parser.set_defaults(handler=_cmd_runtime_watch)

    runtime_fade_in_parser = runtime_subparsers.add_parser(
        "fade-in",
        help="Trigger immediate live fade-in on the running GUI",
    )
    runtime_fade_in_parser.set_defaults(handler=_cmd_runtime_fade_in)

    runtime_fade_out_parser = runtime_subparsers.add_parser(
        "fade-out",
        help="Trigger immediate live fade-out on the running GUI",
    )
    runtime_fade_out_parser.set_defaults(handler=_cmd_runtime_fade_out)

    runtime_online_parser = runtime_subparsers.add_parser(
        "online",
        help="Set automation online (same as GUI Play button)",
    )
    runtime_online_parser.set_defaults(handler=_cmd_runtime_online)

    runtime_offline_parser = runtime_subparsers.add_parser(
        "offline",
        help="Set automation offline (same as GUI Stop button)",
    )
    runtime_offline_parser.set_defaults(handler=_cmd_runtime_offline)

    runtime_volume_parser = runtime_subparsers.add_parser(
        "volume",
        help="Set live GUI volume (0-100)",
    )
    runtime_volume_parser.add_argument(
        "--value",
        type=int,
        required=True,
        help="Target volume percent (0-100)",
    )
    runtime_volume_parser.set_defaults(handler=_cmd_runtime_volume)

    runtime_mute_parser = runtime_subparsers.add_parser(
        "mute",
        help="Alias for runtime volume --value 0",
    )
    runtime_mute_parser.set_defaults(handler=_cmd_runtime_mute)

    return parser


def run(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2

    try:
        return int(handler(args))
    except CliError as exc:
        if _json_enabled(args):
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": str(exc),
                    },
                    separators=(",", ":"),
                    ensure_ascii=True,
                ),
                file=sys.stderr,
            )
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run())

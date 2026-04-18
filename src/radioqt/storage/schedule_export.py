from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import subprocess
import tempfile
import threading

from ..library import local_media_path_from_source
from ..models import AppState, MediaItem, ScheduleEntry

_EXPORT_DIR_NAME = "export"
_FFPROBE_TIMEOUT_SECONDS = 3.0
_MAX_METADATA_WORKERS = 4
_MAX_METADATA_CACHE_ENTRIES = 2048
_METADATA_CACHE_LOCK = threading.Lock()
_METADATA_CACHE: dict[tuple[str, int, int], dict[str, object]] = {}


@dataclass(slots=True)
class ScheduleExportResult:
    updated_paths: list[Path] = field(default_factory=list)
    removed_paths: list[Path] = field(default_factory=list)
    unchanged_count: int = 0

    @property
    def updated_count(self) -> int:
        return len(self.updated_paths)

    @property
    def removed_count(self) -> int:
        return len(self.removed_paths)


def _entry_day_key(entry: ScheduleEntry) -> str:
    return entry.start_at.astimezone().date().isoformat()


def _entry_export_signature(entry: ScheduleEntry) -> tuple[object, ...]:
    return (
        entry.id,
        entry.media_id,
        entry.start_at.astimezone().isoformat(),
        entry.status,
        entry.duration,
        bool(entry.hard_sync),
        bool(entry.fade_in),
        bool(entry.fade_out),
        bool(entry.one_shot),
        entry.cron_id,
    )


def _media_export_signature(media: MediaItem | None) -> tuple[object, ...] | None:
    if media is None:
        return None
    return (
        media.id,
        media.title,
        media.source,
        bool(media.greenwich_time_signal_enabled),
        media.created_at.astimezone().isoformat(),
    )


def _schedule_entries_by_day(entries: list[ScheduleEntry]) -> dict[str, list[ScheduleEntry]]:
    grouped: dict[str, list[ScheduleEntry]] = defaultdict(list)
    sorted_entries = sorted(entries, key=lambda entry: (entry.start_at.astimezone(), entry.id))
    for entry in sorted_entries:
        grouped[_entry_day_key(entry)].append(entry)
    return dict(grouped)


def _serialize_media(
    media: MediaItem | None,
    media_id: str,
    local_file_metadata_by_media_id: dict[str, dict[str, object]],
) -> dict[str, object]:
    if media is None:
        return {
            "missing": True,
            "id": media_id,
        }
    return {
        "missing": False,
        "id": media.id,
        "title": media.title,
        "source": media.source,
        "greenwich_time_signal_enabled": bool(media.greenwich_time_signal_enabled),
        "created_at": media.created_at.astimezone().isoformat(),
        "local_file_metadata": local_file_metadata_by_media_id.get(
            media.id, {"available": False}
        ),
    }


def _serialize_schedule_entry(
    entry: ScheduleEntry,
    media_by_id: dict[str, MediaItem],
    local_file_metadata_by_media_id: dict[str, dict[str, object]],
) -> dict[str, object]:
    media = media_by_id.get(entry.media_id)
    return {
        "id": entry.id,
        "start_at": entry.start_at.astimezone().isoformat(),
        "status": entry.status,
        "duration_seconds": entry.duration,
        "hard_sync": bool(entry.hard_sync),
        "fade_in": bool(entry.fade_in),
        "fade_out": bool(entry.fade_out),
        "one_shot": bool(entry.one_shot),
        "cron_id": entry.cron_id,
        "media": _serialize_media(
            media,
            entry.media_id,
            local_file_metadata_by_media_id,
        ),
    }


def _payload_for_day(
    *,
    day_key: str,
    entries: list[ScheduleEntry],
    media_by_id: dict[str, MediaItem],
    local_file_metadata_by_media_id: dict[str, dict[str, object]],
) -> dict[str, object]:
    return {
        "date": day_key,
        "entry_count": len(entries),
        "entries": [
            _serialize_schedule_entry(
                entry,
                media_by_id,
                local_file_metadata_by_media_id,
            )
            for entry in entries
        ],
    }


def _daily_export_path(export_root: Path, day_key: str) -> Path:
    year = day_key[:4]
    return export_root / year / f"{day_key}.json"


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=path.parent,
        prefix=f".{path.stem}.tmp-",
        suffix=path.suffix,
    ) as handle:
        handle.write(text)
        temporary_path = Path(handle.name)
    temporary_path.replace(path)


def _safe_resolve_file_path(source: str) -> Path | None:
    raw_path = local_media_path_from_source(source)
    if raw_path is None:
        return None
    try:
        resolved = raw_path.expanduser().resolve()
    except OSError:
        resolved = raw_path.expanduser()
    return resolved


def _serialize_ffprobe_payload(path: Path) -> dict[str, object] | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_entries",
                "format=duration,bit_rate,tags:stream=index,codec_type,codec_name,sample_rate,channels,bit_rate,tags",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=_FFPROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "")
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _build_local_file_metadata(path: Path) -> dict[str, object]:
    try:
        stat = path.stat()
    except OSError:
        return {"available": False}
    if not path.is_file():
        return {"available": False}
    modified_at = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat()
    signature = (str(path), int(stat.st_size), int(stat.st_mtime_ns))
    with _METADATA_CACHE_LOCK:
        cached = _METADATA_CACHE.get(signature)
    if cached is not None:
        return dict(cached)

    payload: dict[str, object] = {
        "available": True,
        "path": str(path),
        "size_bytes": int(stat.st_size),
        "modified_at": modified_at,
    }
    ffprobe_payload = _serialize_ffprobe_payload(path)
    if ffprobe_payload is not None:
        payload["probe"] = ffprobe_payload

    with _METADATA_CACHE_LOCK:
        if len(_METADATA_CACHE) >= _MAX_METADATA_CACHE_ENTRIES:
            _METADATA_CACHE.clear()
        _METADATA_CACHE[signature] = dict(payload)
    return payload


def _build_local_file_metadata_by_media_id(
    media_by_id: dict[str, MediaItem],
) -> dict[str, dict[str, object]]:
    local_path_by_media_id: dict[str, Path] = {}
    unique_paths: set[Path] = set()
    for media in media_by_id.values():
        resolved_path = _safe_resolve_file_path(media.source)
        if resolved_path is None:
            continue
        local_path_by_media_id[media.id] = resolved_path
        unique_paths.add(resolved_path)
    if not unique_paths:
        return {}

    path_payload: dict[Path, dict[str, object]] = {}
    workers = max(1, min(_MAX_METADATA_WORKERS, len(unique_paths)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="schedule-export-meta") as executor:
        future_by_path = {
            executor.submit(_build_local_file_metadata, path): path for path in unique_paths
        }
        for future in as_completed(future_by_path):
            path = future_by_path[future]
            try:
                path_payload[path] = future.result()
            except Exception:
                path_payload[path] = {"available": False}
    return {
        media_id: path_payload.get(path, {"available": False})
        for media_id, path in local_path_by_media_id.items()
    }


def _cleanup_year_dirs_for_removed_paths(removed_paths: list[Path]) -> None:
    year_dirs = sorted({path.parent for path in removed_paths})
    for year_dir in year_dirs:
        try:
            if any(year_dir.iterdir()):
                continue
            year_dir.rmdir()
        except OSError:
            continue


def _write_day_exports(
    *,
    config_dir: Path,
    state: AppState,
    upsert_day_keys: set[str],
    remove_day_keys: set[str],
) -> ScheduleExportResult:
    result = ScheduleExportResult()
    export_root = config_dir.expanduser() / _EXPORT_DIR_NAME
    entries_by_day = _schedule_entries_by_day(state.schedule_entries)
    media_by_id_all = {item.id: item for item in state.media_items}
    relevant_media_ids = {
        entry.media_id
        for day_key in upsert_day_keys
        for entry in entries_by_day.get(day_key, [])
    }
    relevant_media_by_id = {
        media_id: media
        for media_id, media in media_by_id_all.items()
        if media_id in relevant_media_ids
    }
    local_file_metadata_by_media_id = _build_local_file_metadata_by_media_id(relevant_media_by_id)

    for day_key in sorted(upsert_day_keys):
        entries = entries_by_day.get(day_key, [])
        if not entries:
            continue
        payload = _payload_for_day(
            day_key=day_key,
            entries=entries,
            media_by_id=media_by_id_all,
            local_file_metadata_by_media_id=local_file_metadata_by_media_id,
        )
        output_path = _daily_export_path(export_root, day_key)
        payload_text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        if output_path.is_file():
            try:
                existing_text = output_path.read_text(encoding="utf-8")
            except OSError:
                existing_text = None
            if existing_text == payload_text:
                result.unchanged_count += 1
                continue
        _write_text_atomic(output_path, payload_text)
        result.updated_paths.append(output_path)

    for day_key in sorted(remove_day_keys):
        output_path = _daily_export_path(export_root, day_key)
        if not output_path.exists():
            continue
        try:
            output_path.unlink()
        except OSError:
            continue
        result.removed_paths.append(output_path)
    _cleanup_year_dirs_for_removed_paths(result.removed_paths)
    return result


def _changed_day_keys_for_incremental_export(
    previous_state: AppState,
    current_state: AppState,
) -> tuple[set[str], set[str]]:
    previous_entries_by_id = {entry.id: entry for entry in previous_state.schedule_entries}
    current_entries_by_id = {entry.id: entry for entry in current_state.schedule_entries}

    changed_day_keys: set[str] = set()
    for entry_id in set(previous_entries_by_id) | set(current_entries_by_id):
        previous_entry = previous_entries_by_id.get(entry_id)
        current_entry = current_entries_by_id.get(entry_id)
        if previous_entry is None:
            if current_entry is not None:
                changed_day_keys.add(_entry_day_key(current_entry))
            continue
        if current_entry is None:
            changed_day_keys.add(_entry_day_key(previous_entry))
            continue
        if _entry_export_signature(previous_entry) == _entry_export_signature(current_entry):
            continue
        changed_day_keys.add(_entry_day_key(previous_entry))
        changed_day_keys.add(_entry_day_key(current_entry))

    previous_media_by_id = {item.id: item for item in previous_state.media_items}
    current_media_by_id = {item.id: item for item in current_state.media_items}
    changed_media_ids = {
        media_id
        for media_id in set(previous_media_by_id) | set(current_media_by_id)
        if _media_export_signature(previous_media_by_id.get(media_id))
        != _media_export_signature(current_media_by_id.get(media_id))
    }
    if changed_media_ids:
        for entry in previous_state.schedule_entries:
            if entry.media_id in changed_media_ids:
                changed_day_keys.add(_entry_day_key(entry))
        for entry in current_state.schedule_entries:
            if entry.media_id in changed_media_ids:
                changed_day_keys.add(_entry_day_key(entry))

    current_day_keys = {_entry_day_key(entry) for entry in current_state.schedule_entries}
    upsert_day_keys = {day_key for day_key in changed_day_keys if day_key in current_day_keys}
    remove_day_keys = changed_day_keys - current_day_keys
    return upsert_day_keys, remove_day_keys


def export_schedule_incremental(
    config_dir: Path,
    *,
    previous_state: AppState,
    current_state: AppState,
) -> ScheduleExportResult:
    upsert_day_keys, remove_day_keys = _changed_day_keys_for_incremental_export(
        previous_state,
        current_state,
    )
    if not upsert_day_keys and not remove_day_keys:
        return ScheduleExportResult()
    return _write_day_exports(
        config_dir=config_dir,
        state=current_state,
        upsert_day_keys=upsert_day_keys,
        remove_day_keys=remove_day_keys,
    )


def _day_keys_in_range(start_date: date, end_date: date) -> set[str]:
    day_keys: set[str] = set()
    current_day = start_date
    while current_day <= end_date:
        day_keys.add(current_day.isoformat())
        current_day += timedelta(days=1)
    return day_keys


def export_schedule_range(
    config_dir: Path,
    *,
    state: AppState,
    start_date: date,
    end_date: date,
) -> ScheduleExportResult:
    if end_date < start_date:
        raise ValueError("end_date cannot be before start_date")
    target_day_keys = _day_keys_in_range(start_date, end_date)
    schedule_day_keys = {_entry_day_key(entry) for entry in state.schedule_entries}
    upsert_day_keys = target_day_keys & schedule_day_keys
    remove_day_keys = target_day_keys - schedule_day_keys
    return _write_day_exports(
        config_dir=config_dir,
        state=state,
        upsert_day_keys=upsert_day_keys,
        remove_day_keys=remove_day_keys,
    )

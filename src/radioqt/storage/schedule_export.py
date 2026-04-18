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

from ..app_config import load_app_config
from ..library.sources import local_media_path_from_source
from ..models import AppState, MediaItem, ScheduleEntry

_EXPORT_DIR_NAME = "export"
_FFPROBE_TIMEOUT_SECONDS = 3.0
_MAX_METADATA_WORKERS = 4
_MAX_METADATA_CACHE_ENTRIES = 2048
_METADATA_CACHE_LOCK = threading.Lock()
_METADATA_CACHE: dict[tuple[str, int, int], dict[str, object]] = {}
_TEXT_METADATA_FIELDS = (
    "title",
    "artist",
    "album",
    "genre",
    "track",
    "date",
    "comment",
    "copyright",
)
_TAG_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("title",),
    "artist": ("artist", "album_artist", "albumartist"),
    "album": ("album",),
    "genre": ("genre",),
    "track": ("track", "tracknumber", "track_number"),
    "date": ("date", "year", "creation_time"),
    "comment": ("comment", "description"),
    "copyright": ("copyright",),
}


@dataclass(slots=True, frozen=True)
class _CompiledPathMapping:
    from_prefix: Path
    to_prefix: str


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


def _today_day_key() -> str:
    return datetime.now().astimezone().date().isoformat()


def _day_keys_from_today(day_keys: set[str]) -> set[str]:
    today_day_key = _today_day_key()
    return {day_key for day_key in day_keys if day_key >= today_day_key}


def _schedule_entries_by_day(entries: list[ScheduleEntry]) -> dict[str, list[ScheduleEntry]]:
    grouped: dict[str, list[ScheduleEntry]] = defaultdict(list)
    sorted_entries = sorted(entries, key=lambda entry: (entry.start_at.astimezone(), entry.id))
    for entry in sorted_entries:
        grouped[_entry_day_key(entry)].append(entry)
    return dict(grouped)


def _empty_local_file_metadata() -> dict[str, object]:
    payload: dict[str, object] = {
        "path": "",
        "size_bytes": 0,
        "duration_seconds": None,
        "audio": {
            "channels": None,
            "codec": "",
            "sample_rate": None,
            "bit_rate": None,
        },
        "video": {
            "codec": "",
            "bit_rate": None,
        },
        "metadata": {
            field_name: ""
            for field_name in _TEXT_METADATA_FIELDS
        },
    }
    return payload


def _normalize_public_prefix(value: object) -> str:
    token = str(value).strip()
    if not token:
        return ""
    if token == "/":
        return token
    return token.rstrip("/\\")


def _load_compiled_path_mappings(config_dir: Path) -> list[_CompiledPathMapping]:
    settings_path = config_dir.expanduser() / "settings.yaml"
    try:
        app_config = load_app_config(settings_path)
    except Exception:
        return []
    compiled: list[_CompiledPathMapping] = []
    for mapping in list(getattr(app_config, "export_path_mappings", [])):
        from_prefix = str(getattr(mapping, "from_prefix", "")).strip()
        to_prefix = _normalize_public_prefix(getattr(mapping, "to_prefix", ""))
        if not from_prefix or not to_prefix:
            continue
        from_path = Path(from_prefix).expanduser()
        try:
            resolved_from_path = from_path.resolve()
        except OSError:
            resolved_from_path = from_path
        compiled.append(
            _CompiledPathMapping(
                from_prefix=resolved_from_path,
                to_prefix=to_prefix,
            )
        )
    compiled.sort(key=lambda item: len(str(item.from_prefix)), reverse=True)
    return compiled


def _map_local_path_to_public(
    local_path: Path,
    path_mappings: list[_CompiledPathMapping],
) -> str:
    for mapping in path_mappings:
        try:
            relative_path = local_path.relative_to(mapping.from_prefix)
        except ValueError:
            continue
        relative_path_text = relative_path.as_posix().lstrip("/")
        if not relative_path_text:
            return mapping.to_prefix
        return f"{mapping.to_prefix}/{relative_path_text}"
    return local_path.as_posix()


def _export_media_source(
    source: str,
    path_mappings: list[_CompiledPathMapping],
) -> str:
    local_path = _safe_resolve_file_path(source)
    if local_path is None:
        return source
    return _map_local_path_to_public(local_path, path_mappings)


def _clone_local_file_metadata(payload: dict[str, object]) -> dict[str, object]:
    cloned = dict(payload)
    metadata = cloned.get("metadata")
    if isinstance(metadata, dict):
        cloned["metadata"] = dict(metadata)
    audio = cloned.get("audio")
    if isinstance(audio, dict):
        cloned["audio"] = dict(audio)
    video = cloned.get("video")
    if isinstance(video, dict):
        cloned["video"] = dict(video)
    return cloned


def _with_media_title(local_file_metadata: dict[str, object], media_title: str) -> dict[str, object]:
    payload = _clone_local_file_metadata(local_file_metadata)
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {
            field_name: ""
            for field_name in _TEXT_METADATA_FIELDS
        }
        payload["metadata"] = metadata
    if not str(metadata.get("title", "")).strip():
        metadata["title"] = str(media_title).strip()
    return payload


def _normalize_audio_info(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raw = {}
    codec = str(raw.get("codec", "")).strip()
    return {
        "channels": _safe_int_or_none(raw.get("channels")),
        "codec": codec,
        "sample_rate": _safe_int_or_none(raw.get("sample_rate")),
        "bit_rate": _safe_int_or_none(raw.get("bit_rate")),
    }


def _normalize_video_info(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raw = {}
    codec = str(raw.get("codec", "")).strip()
    return {
        "codec": codec,
        "bit_rate": _safe_int_or_none(raw.get("bit_rate")),
    }


def _empty_metadata() -> dict[str, str]:
    return {
        field_name: ""
        for field_name in _TEXT_METADATA_FIELDS
    }


def _media_export_details(local_file_metadata: dict[str, object], media_title: str) -> dict[str, object]:
    payload = _with_media_title(local_file_metadata, media_title)
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = _empty_metadata()
    metadata_payload = _empty_metadata()
    for key, value in metadata.items():
        key_text = str(key).strip()
        if key_text in metadata_payload:
            metadata_payload[key_text] = str(value)
    if not metadata_payload["title"].strip():
        metadata_payload["title"] = media_title

    size_bytes = _safe_int_or_none(payload.get("size_bytes"))
    duration_seconds = _safe_float_or_none(payload.get("duration_seconds"))
    return {
        "metadata": metadata_payload,
        "file_info": {
            "path": str(payload.get("path", "")),
            "size_bytes": 0 if size_bytes is None else size_bytes,
            "duration_seconds": duration_seconds,
            "audio": _normalize_audio_info(payload.get("audio")),
            "video": _normalize_video_info(payload.get("video")),
        },
    }


def _serialize_media(
    media: MediaItem | None,
    media_id: str,
    exported_source_by_media_id: dict[str, str],
    local_file_metadata_by_media_id: dict[str, dict[str, object]],
) -> dict[str, object]:
    if media is None:
        return {
            "id": media_id,
            **_media_export_details(_empty_local_file_metadata(), ""),
        }
    local_file_metadata = local_file_metadata_by_media_id.get(
        media.id,
        _empty_local_file_metadata(),
    )
    local_file_metadata = _clone_local_file_metadata(local_file_metadata)
    local_file_metadata["path"] = exported_source_by_media_id.get(media.id, media.source)
    return {
        "id": media.id,
        **_media_export_details(local_file_metadata, media.title),
    }


def _serialize_schedule_entry(
    entry: ScheduleEntry,
    media_by_id: dict[str, MediaItem],
    exported_source_by_media_id: dict[str, str],
    local_file_metadata_by_media_id: dict[str, dict[str, object]],
) -> dict[str, object]:
    media = media_by_id.get(entry.media_id)
    return {
        "id": entry.id,
        "start_at": entry.start_at.astimezone().isoformat(),
        "status": entry.status,
        "media": _serialize_media(
            media,
            entry.media_id,
            exported_source_by_media_id,
            local_file_metadata_by_media_id,
        ),
    }


def _payload_for_day(
    *,
    day_key: str,
    entries: list[ScheduleEntry],
    media_by_id: dict[str, MediaItem],
    exported_source_by_media_id: dict[str, str],
    local_file_metadata_by_media_id: dict[str, dict[str, object]],
) -> dict[str, object]:
    return {
        "date": day_key,
        "entry_count": len(entries),
        "entries": [
            _serialize_schedule_entry(
                entry,
                media_by_id,
                exported_source_by_media_id,
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
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
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


def _safe_float_or_none(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def _safe_int_or_none(value: object) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def _normalize_tag_map(raw_tags: object) -> dict[str, str]:
    if not isinstance(raw_tags, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in raw_tags.items():
        normalized_key = str(key).strip().lower()
        if not normalized_key:
            continue
        normalized_value = str(value).strip()
        if not normalized_value:
            continue
        normalized[normalized_key] = normalized_value
    return normalized


def _extract_text_metadata(ffprobe_payload: dict[str, object] | None) -> dict[str, str]:
    extracted = {field_name: "" for field_name in _TEXT_METADATA_FIELDS}
    if ffprobe_payload is None:
        return extracted

    candidates: list[dict[str, str]] = []
    format_payload = ffprobe_payload.get("format")
    if isinstance(format_payload, dict):
        candidates.append(_normalize_tag_map(format_payload.get("tags")))
    streams_payload = ffprobe_payload.get("streams")
    if isinstance(streams_payload, list):
        for stream_payload in streams_payload:
            if not isinstance(stream_payload, dict):
                continue
            candidates.append(_normalize_tag_map(stream_payload.get("tags")))

    for field_name in _TEXT_METADATA_FIELDS:
        aliases = _TAG_ALIASES.get(field_name, (field_name,))
        for candidate in candidates:
            for alias in aliases:
                token = candidate.get(alias)
                if token:
                    extracted[field_name] = token
                    break
            if extracted[field_name]:
                break
    return extracted


def _probe_summary(ffprobe_payload: dict[str, object] | None) -> dict[str, object]:
    summary: dict[str, object] = {
        "duration_seconds": None,
        "audio": {
            "channels": None,
            "codec": "",
            "sample_rate": None,
            "bit_rate": None,
        },
        "video": {
            "codec": "",
            "bit_rate": None,
        },
    }
    if ffprobe_payload is None:
        return summary

    format_bit_rate: int | None = None
    format_payload = ffprobe_payload.get("format")
    if isinstance(format_payload, dict):
        summary["duration_seconds"] = _safe_float_or_none(format_payload.get("duration"))
        format_bit_rate = _safe_int_or_none(format_payload.get("bit_rate"))

    streams_payload = ffprobe_payload.get("streams")
    if not isinstance(streams_payload, list):
        audio_payload = summary.get("audio")
        if isinstance(audio_payload, dict) and audio_payload.get("bit_rate") is None:
            audio_payload["bit_rate"] = format_bit_rate
        video_payload = summary.get("video")
        if isinstance(video_payload, dict) and video_payload.get("bit_rate") is None:
            video_payload["bit_rate"] = format_bit_rate
        return summary

    for stream_payload in streams_payload:
        if not isinstance(stream_payload, dict):
            continue
        codec_type = str(stream_payload.get("codec_type", "")).strip().lower()
        codec_name = str(stream_payload.get("codec_name", "")).strip()
        stream_bit_rate = _safe_int_or_none(stream_payload.get("bit_rate"))
        if codec_type == "audio":
            audio_payload = summary.get("audio")
            if not isinstance(audio_payload, dict):
                continue
            if not str(audio_payload.get("codec", "")).strip() and codec_name:
                audio_payload["codec"] = codec_name
            if audio_payload.get("channels") is None:
                audio_payload["channels"] = _safe_int_or_none(stream_payload.get("channels"))
            if audio_payload.get("sample_rate") is None:
                audio_payload["sample_rate"] = _safe_int_or_none(stream_payload.get("sample_rate"))
            if audio_payload.get("bit_rate") is None:
                audio_payload["bit_rate"] = stream_bit_rate
        if codec_type == "video":
            video_payload = summary.get("video")
            if not isinstance(video_payload, dict):
                continue
            if not str(video_payload.get("codec", "")).strip() and codec_name:
                video_payload["codec"] = codec_name
            if video_payload.get("bit_rate") is None:
                video_payload["bit_rate"] = stream_bit_rate

    audio_payload = summary.get("audio")
    if isinstance(audio_payload, dict) and audio_payload.get("bit_rate") is None:
        audio_payload["bit_rate"] = format_bit_rate
    video_payload = summary.get("video")
    if isinstance(video_payload, dict) and video_payload.get("bit_rate") is None:
        video_payload["bit_rate"] = format_bit_rate
    return summary


def _build_local_file_metadata(
    path: Path,
    *,
    public_path: str,
) -> dict[str, object]:
    payload = _empty_local_file_metadata()
    payload["path"] = public_path
    try:
        stat = path.stat()
    except OSError:
        return payload
    if not path.is_file():
        return payload
    signature = (str(path), int(stat.st_size), int(stat.st_mtime_ns))
    with _METADATA_CACHE_LOCK:
        cached = _METADATA_CACHE.get(signature)
    if cached is not None:
        cached_payload = _clone_local_file_metadata(cached)
        cached_payload["path"] = public_path
        return cached_payload

    payload["size_bytes"] = int(stat.st_size)
    ffprobe_payload = _serialize_ffprobe_payload(path)
    probe_summary = _probe_summary(ffprobe_payload)
    payload["duration_seconds"] = _safe_float_or_none(probe_summary.get("duration_seconds"))
    audio_payload = payload.get("audio")
    if isinstance(audio_payload, dict):
        audio_payload.update(_normalize_audio_info(probe_summary.get("audio")))
    video_payload = payload.get("video")
    if isinstance(video_payload, dict):
        video_payload.update(_normalize_video_info(probe_summary.get("video")))
    metadata_payload = payload.get("metadata")
    if isinstance(metadata_payload, dict):
        metadata_payload.update(_extract_text_metadata(ffprobe_payload))

    with _METADATA_CACHE_LOCK:
        if len(_METADATA_CACHE) >= _MAX_METADATA_CACHE_ENTRIES:
            _METADATA_CACHE.clear()
        _METADATA_CACHE[signature] = _clone_local_file_metadata(payload)
    return payload


def _build_local_file_metadata_by_media_id(
    media_by_id: dict[str, MediaItem],
    *,
    path_mappings: list[_CompiledPathMapping],
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
    public_path_by_path: dict[Path, str] = {}
    workers = max(1, min(_MAX_METADATA_WORKERS, len(unique_paths)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="schedule-export-meta") as executor:
        future_by_path = {}
        for path in unique_paths:
            public_path = _map_local_path_to_public(path, path_mappings)
            public_path_by_path[path] = public_path
            future = executor.submit(
                _build_local_file_metadata,
                path,
                public_path=public_path,
            )
            future_by_path[future] = path
        for future in as_completed(future_by_path):
            path = future_by_path[future]
            try:
                path_payload[path] = future.result()
            except Exception:
                public_path = public_path_by_path.get(path, "")
                fallback_payload = _empty_local_file_metadata()
                fallback_payload["path"] = public_path
                path_payload[path] = fallback_payload
    return {
        media_id: path_payload.get(path, _empty_local_file_metadata())
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
    path_mappings = _load_compiled_path_mappings(config_dir)
    entries_by_day = _schedule_entries_by_day(state.schedule_entries)
    media_by_id_all = {item.id: item for item in state.media_items}
    exported_source_by_media_id = {
        media.id: _export_media_source(media.source, path_mappings)
        for media in media_by_id_all.values()
    }
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
    local_file_metadata_by_media_id = _build_local_file_metadata_by_media_id(
        relevant_media_by_id,
        path_mappings=path_mappings,
    )

    for day_key in sorted(upsert_day_keys):
        entries = entries_by_day.get(day_key, [])
        if not entries:
            continue
        payload = _payload_for_day(
            day_key=day_key,
            entries=entries,
            media_by_id=media_by_id_all,
            exported_source_by_media_id=exported_source_by_media_id,
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


def export_schedule_incremental(
    config_dir: Path,
    *,
    previous_state: AppState,
    current_state: AppState,
) -> ScheduleExportResult:
    upsert_day_keys = _day_keys_from_today(
        {_entry_day_key(entry) for entry in current_state.schedule_entries}
    )
    previous_day_keys = _day_keys_from_today(
        {_entry_day_key(entry) for entry in previous_state.schedule_entries}
    )
    remove_day_keys = previous_day_keys - upsert_day_keys
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
    today_local_date = datetime.now().astimezone().date()
    effective_start_date = max(start_date, today_local_date)
    if end_date < effective_start_date:
        return ScheduleExportResult()
    target_day_keys = _day_keys_in_range(effective_start_date, end_date)
    schedule_day_keys = {_entry_day_key(entry) for entry in state.schedule_entries}
    upsert_day_keys = target_day_keys & schedule_day_keys
    remove_day_keys = target_day_keys - schedule_day_keys
    return _write_day_exports(
        config_dir=config_dir,
        state=state,
        upsert_day_keys=upsert_day_keys,
        remove_day_keys=remove_day_keys,
    )


def export_schedule_day_keys(
    config_dir: Path,
    *,
    state: AppState,
    day_keys: set[str],
) -> ScheduleExportResult:
    target_day_keys = _day_keys_from_today(set(day_keys))
    if not target_day_keys:
        return ScheduleExportResult()
    schedule_day_keys = {_entry_day_key(entry) for entry in state.schedule_entries}
    upsert_day_keys = target_day_keys & schedule_day_keys
    remove_day_keys = target_day_keys - schedule_day_keys
    if not upsert_day_keys and not remove_day_keys:
        return ScheduleExportResult()
    return _write_day_exports(
        config_dir=config_dir,
        state=state,
        upsert_day_keys=upsert_day_keys,
        remove_day_keys=remove_day_keys,
    )

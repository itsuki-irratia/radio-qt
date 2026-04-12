from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


DEFAULT_SUPPORTED_EXTENSIONS = [
    "mp3",
    "ogg",
    "opus",
    "mp4",
    "webm",
    "aac",
    "mkv",
    "mpg",
    "mpeg",
    "m3u",
    "m3u8",
    "pls",
    "xspf",
    "mov",
    "wav",
    "flac",
    "m4a",
    "avi",
    "flv",
]


def _normalize_extension(value: object) -> str:
    token = str(value).strip().lower().lstrip(".")
    if not token:
        return ""
    if not all(char.isalnum() for char in token):
        return ""
    return token


def _normalize_extensions_list(raw_values: object) -> list[str]:
    if not isinstance(raw_values, list):
        return list(DEFAULT_SUPPORTED_EXTENSIONS)
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        token = _normalize_extension(raw_value)
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized or list(DEFAULT_SUPPORTED_EXTENSIONS)


@dataclass(slots=True)
class MediaItem:
    id: str
    title: str
    source: str
    created_at: datetime = field(default_factory=lambda: datetime.now().astimezone())

    @classmethod
    def create(cls, title: str, source: str) -> "MediaItem":
        return cls(id=str(uuid4()), title=title, source=source)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MediaItem":
        created_at_raw = data.get("created_at", datetime.now().astimezone().isoformat())
        return cls(
            id=data.get("id", str(uuid4())),
            title=data.get("title", "Untitled"),
            source=data.get("source", ""),
            created_at=_parse_datetime(created_at_raw),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "source": self.source,
            "created_at": self.created_at.isoformat(),
        }


SCHEDULE_STATUS_PENDING = "pending"
SCHEDULE_STATUS_DISABLED = "disabled"
SCHEDULE_STATUS_FIRED = "fired"
SCHEDULE_STATUS_MISSED = "missed"

VALID_SCHEDULE_STATUSES = {
    SCHEDULE_STATUS_PENDING,
    SCHEDULE_STATUS_DISABLED,
    SCHEDULE_STATUS_FIRED,
    SCHEDULE_STATUS_MISSED,
}


@dataclass(slots=True)
class CronEntry:
    id: str
    media_id: str
    expression: str
    hard_sync: bool = True
    fade_in: bool = False
    fade_out: bool = False
    enabled: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now().astimezone())

    @classmethod
    def create(
        cls,
        media_id: str,
        expression: str,
        hard_sync: bool = True,
        fade_in: bool = False,
        fade_out: bool = False,
    ) -> "CronEntry":
        return cls(
            id=str(uuid4()),
            media_id=media_id,
            expression=expression,
            hard_sync=hard_sync,
            fade_in=fade_in,
            fade_out=fade_out,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CronEntry":
        created_at_raw = data.get("created_at", datetime.now().astimezone().isoformat())
        return cls(
            id=data.get("id", str(uuid4())),
            media_id=data["media_id"],
            expression=data.get("expression", "").strip(),
            hard_sync=bool(data.get("hard_sync", True)),
            fade_in=bool(data.get("fade_in", False)),
            fade_out=bool(data.get("fade_out", False)),
            enabled=bool(data.get("enabled", True)),
            created_at=_parse_datetime(created_at_raw),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "media_id": self.media_id,
            "expression": self.expression,
            "hard_sync": self.hard_sync,
            "fade_in": self.fade_in,
            "fade_out": self.fade_out,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(slots=True)
class ScheduleEntry:
    id: str
    media_id: str
    start_at: datetime
    duration: int | None = None
    hard_sync: bool = True
    fade_in: bool = False
    fade_out: bool = False
    status: str = SCHEDULE_STATUS_PENDING
    one_shot: bool = True
    cron_id: str | None = None
    cron_status_override: str | None = None
    cron_hard_sync_override: bool | None = None
    cron_fade_in_override: bool | None = None
    cron_fade_out_override: bool | None = None

    @classmethod
    def create(cls, media_id: str, start_at: datetime, hard_sync: bool = True) -> "ScheduleEntry":
        return cls(id=str(uuid4()), media_id=media_id, start_at=start_at, hard_sync=hard_sync)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScheduleEntry":
        def _optional_bool(value: Any) -> bool | None:
            if value is None:
                return None
            return bool(value)

        duration = None
        duration_raw = data.get("duration")
        if duration_raw is not None:
            try:
                duration = int(duration_raw)
            except (TypeError, ValueError):
                duration = None
        status = data.get("status")
        if status is None:
            fired = data.get("fired", False)
            enabled = data.get("enabled", True)
            if fired:
                status = SCHEDULE_STATUS_FIRED
            elif not enabled:
                status = SCHEDULE_STATUS_DISABLED
            else:
                status = SCHEDULE_STATUS_PENDING
        if status not in VALID_SCHEDULE_STATUSES:
            status = SCHEDULE_STATUS_PENDING
        return cls(
            id=data["id"],
            media_id=data["media_id"],
            start_at=_parse_datetime(data["start_at"]),
            duration=duration,
            hard_sync=bool(data.get("hard_sync", True)),
            fade_in=bool(data.get("fade_in", False)),
            fade_out=bool(data.get("fade_out", False)),
            status=status,
            one_shot=data.get("one_shot", True),
            cron_id=data.get("cron_id"),
            cron_status_override=data.get("cron_status_override"),
            cron_hard_sync_override=_optional_bool(data.get("cron_hard_sync_override")),
            cron_fade_in_override=_optional_bool(data.get("cron_fade_in_override")),
            cron_fade_out_override=_optional_bool(data.get("cron_fade_out_override")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "media_id": self.media_id,
            "start_at": self.start_at.isoformat(),
            "duration": self.duration,
            "hard_sync": self.hard_sync,
            "fade_in": self.fade_in,
            "fade_out": self.fade_out,
            "status": self.status,
            "one_shot": self.one_shot,
            "cron_id": self.cron_id,
            "cron_status_override": self.cron_status_override,
            "cron_hard_sync_override": self.cron_hard_sync_override,
            "cron_fade_in_override": self.cron_fade_in_override,
            "cron_fade_out_override": self.cron_fade_out_override,
        }


@dataclass(slots=True)
class QueueItem:
    media_id: str
    source: str = "manual"
    schedule_entry_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | str) -> "QueueItem":
        if isinstance(data, str):
            return cls(media_id=data)
        return cls(
            media_id=data["media_id"],
            source=data.get("source", "manual"),
            schedule_entry_id=data.get("schedule_entry_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "media_id": self.media_id,
            "source": self.source,
            "schedule_entry_id": self.schedule_entry_id,
        }


@dataclass(slots=True)
class LibraryTab:
    title: str
    path: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LibraryTab":
        return cls(
            title=str(data.get("title", "")).strip(),
            path=str(data.get("path", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "path": self.path,
        }


@dataclass(slots=True)
class AppState:
    media_items: list[MediaItem] = field(default_factory=list)
    schedule_entries: list[ScheduleEntry] = field(default_factory=list)
    cron_entries: list[CronEntry] = field(default_factory=list)
    queue: list[QueueItem] = field(default_factory=list)
    library_tabs: list[LibraryTab] = field(default_factory=list)
    supported_extensions: list[str] = field(default_factory=lambda: list(DEFAULT_SUPPORTED_EXTENSIONS))
    schedule_auto_focus: bool = False
    logs_visible: bool = True
    fade_in_duration_seconds: int = 5
    fade_out_duration_seconds: int = 5
    duration_probe_cache: dict[str, int | None] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppState":
        def _safe_positive_int(value: Any, default: int) -> int:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return default
            return max(1, parsed)

        media_items = [MediaItem.from_dict(item) for item in data.get("media_items", [])]
        schedule_entries = [ScheduleEntry.from_dict(item) for item in data.get("schedule_entries", [])]
        cron_entries = [CronEntry.from_dict(item) for item in data.get("cron_entries", [])]
        queue = [QueueItem.from_dict(item) for item in data.get("queue", [])]
        library_tabs = [
            LibraryTab.from_dict(item)
            for item in data.get("library_tabs", [])
            if isinstance(item, dict)
        ]
        duration_probe_cache_raw = data.get("duration_probe_cache", {})
        duration_probe_cache: dict[str, int | None] = {}
        if isinstance(duration_probe_cache_raw, dict):
            for key, raw_value in duration_probe_cache_raw.items():
                if not isinstance(key, str) or not key:
                    continue
                if raw_value is None:
                    duration_probe_cache[key] = None
                    continue
                try:
                    duration_probe_cache[key] = max(0, int(raw_value))
                except (TypeError, ValueError):
                    continue
        return cls(
            media_items=media_items,
            schedule_entries=schedule_entries,
            cron_entries=cron_entries,
            queue=queue,
            library_tabs=library_tabs,
            supported_extensions=_normalize_extensions_list(data.get("supported_extensions")),
            schedule_auto_focus=bool(data.get("schedule_auto_focus", False)),
            logs_visible=bool(data.get("logs_visible", True)),
            fade_in_duration_seconds=_safe_positive_int(data.get("fade_in_duration_seconds"), 5),
            fade_out_duration_seconds=_safe_positive_int(data.get("fade_out_duration_seconds"), 5),
            duration_probe_cache=duration_probe_cache,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "media_items": [item.to_dict() for item in self.media_items],
            "schedule_entries": [entry.to_dict() for entry in self.schedule_entries],
            "cron_entries": [entry.to_dict() for entry in self.cron_entries],
            "queue": [item.to_dict() for item in self.queue],
            "library_tabs": [tab.to_dict() for tab in self.library_tabs],
            "supported_extensions": _normalize_extensions_list(self.supported_extensions),
            "schedule_auto_focus": self.schedule_auto_focus,
            "logs_visible": self.logs_visible,
            "fade_in_duration_seconds": self.fade_in_duration_seconds,
            "fade_out_duration_seconds": self.fade_out_duration_seconds,
            "duration_probe_cache": self.duration_probe_cache,
        }

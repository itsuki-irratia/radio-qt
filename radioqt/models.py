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
    hard_sync: bool = False
    enabled: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now().astimezone())

    @classmethod
    def create(
        cls,
        media_id: str,
        expression: str,
        hard_sync: bool = False,
    ) -> "CronEntry":
        return cls(
            id=str(uuid4()),
            media_id=media_id,
            expression=expression,
            hard_sync=hard_sync,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CronEntry":
        created_at_raw = data.get("created_at", datetime.now().astimezone().isoformat())
        return cls(
            id=data.get("id", str(uuid4())),
            media_id=data["media_id"],
            expression=data.get("expression", "").strip(),
            hard_sync=data.get("hard_sync", False),
            enabled=data.get("enabled", True),
            created_at=_parse_datetime(created_at_raw),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "media_id": self.media_id,
            "expression": self.expression,
            "hard_sync": self.hard_sync,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(slots=True)
class ScheduleEntry:
    id: str
    media_id: str
    start_at: datetime
    duration: int | None = None
    hard_sync: bool = False
    status: str = SCHEDULE_STATUS_PENDING
    one_shot: bool = True
    cron_id: str | None = None
    cron_status_override: str | None = None
    cron_hard_sync_override: bool | None = None

    @classmethod
    def create(cls, media_id: str, start_at: datetime, hard_sync: bool = False) -> "ScheduleEntry":
        return cls(id=str(uuid4()), media_id=media_id, start_at=start_at, hard_sync=hard_sync)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScheduleEntry":
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
            hard_sync=data.get("hard_sync", False),
            status=status,
            one_shot=data.get("one_shot", True),
            cron_id=data.get("cron_id"),
            cron_status_override=data.get("cron_status_override"),
            cron_hard_sync_override=data.get("cron_hard_sync_override"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "media_id": self.media_id,
            "start_at": self.start_at.isoformat(),
            "duration": self.duration,
            "hard_sync": self.hard_sync,
            "status": self.status,
            "one_shot": self.one_shot,
            "cron_id": self.cron_id,
            "cron_status_override": self.cron_status_override,
            "cron_hard_sync_override": self.cron_hard_sync_override,
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
class AppState:
    media_items: list[MediaItem] = field(default_factory=list)
    schedule_entries: list[ScheduleEntry] = field(default_factory=list)
    cron_entries: list[CronEntry] = field(default_factory=list)
    queue: list[QueueItem] = field(default_factory=list)
    schedule_auto_focus: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppState":
        media_items = [MediaItem.from_dict(item) for item in data.get("media_items", [])]
        schedule_entries = [ScheduleEntry.from_dict(item) for item in data.get("schedule_entries", [])]
        cron_entries = [CronEntry.from_dict(item) for item in data.get("cron_entries", [])]
        queue = [QueueItem.from_dict(item) for item in data.get("queue", [])]
        return cls(
            media_items=media_items,
            schedule_entries=schedule_entries,
            cron_entries=cron_entries,
            queue=queue,
            schedule_auto_focus=bool(data.get("schedule_auto_focus", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "media_items": [item.to_dict() for item in self.media_items],
            "schedule_entries": [entry.to_dict() for entry in self.schedule_entries],
            "cron_entries": [entry.to_dict() for entry in self.cron_entries],
            "queue": [item.to_dict() for item in self.queue],
            "schedule_auto_focus": self.schedule_auto_focus,
        }

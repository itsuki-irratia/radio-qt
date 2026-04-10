from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..models import (
    MediaItem,
    QueueItem,
    ScheduleEntry,
    SCHEDULE_STATUS_FIRED,
    SCHEDULE_STATUS_MISSED,
    SCHEDULE_STATUS_PENDING,
)
from ..scheduling import active_schedule_entry_at, schedule_entry_window_details
from .actions import enqueue_scheduled_media, resolve_media_by_id


@dataclass(slots=True)
class ScheduleTriggerOutcome:
    kind: str
    media: MediaItem | None = None
    interrupted_media_name: str | None = None


@dataclass(slots=True)
class ActiveSchedulePlayOutcome:
    kind: str
    entry: ScheduleEntry
    media: MediaItem | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    end_reason: str | None = None
    offset_ms: int = 0


@dataclass(slots=True)
class PlayRequestOutcome:
    kind: str
    active_schedule: ActiveSchedulePlayOutcome | None = None


def process_schedule_trigger(
    entry: ScheduleEntry,
    media_items: dict[str, MediaItem],
    play_queue: "deque[QueueItem]",
    *,
    automation_playing: bool,
    player_is_playing: bool,
    current_media_name: str | None,
) -> ScheduleTriggerOutcome:
    if not automation_playing:
        if entry.one_shot and entry.status == SCHEDULE_STATUS_PENDING:
            entry.status = SCHEDULE_STATUS_MISSED
        return ScheduleTriggerOutcome(kind="ignored_stopped")

    media = resolve_media_by_id(media_items, entry.media_id)
    if media is None:
        if entry.one_shot:
            entry.status = SCHEDULE_STATUS_MISSED
        return ScheduleTriggerOutcome(kind="missing_media")

    if entry.one_shot:
        entry.status = SCHEDULE_STATUS_FIRED

    if entry.hard_sync or not player_is_playing:
        interrupted_media_name = current_media_name if entry.hard_sync and player_is_playing else None
        return ScheduleTriggerOutcome(
            kind="play_now",
            media=media,
            interrupted_media_name=interrupted_media_name,
        )

    enqueue_scheduled_media(play_queue, media.id, entry.id)
    return ScheduleTriggerOutcome(kind="queued", media=media)


def resolve_active_schedule_play(
    schedule_entries: list[ScheduleEntry],
    media_items: dict[str, MediaItem],
    now: datetime,
) -> ActiveSchedulePlayOutcome | None:
    active_entry = active_schedule_entry_at(schedule_entries, now)
    if active_entry is None:
        return None

    entry, start_at = active_entry
    if entry.status not in {SCHEDULE_STATUS_PENDING, SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED}:
        return ActiveSchedulePlayOutcome(
            kind="unsupported_status",
            entry=entry,
            start_at=start_at,
        )

    media = resolve_media_by_id(media_items, entry.media_id)
    if media is None:
        if entry.one_shot:
            entry.status = SCHEDULE_STATUS_MISSED
        return ActiveSchedulePlayOutcome(
            kind="missing_media",
            entry=entry,
            start_at=start_at,
        )

    if entry.one_shot and entry.status in {SCHEDULE_STATUS_PENDING, SCHEDULE_STATUS_MISSED}:
        entry.status = SCHEDULE_STATUS_FIRED

    _, end_at, end_reason = schedule_entry_window_details(schedule_entries, entry.id)
    return ActiveSchedulePlayOutcome(
        kind="play_active",
        entry=entry,
        media=media,
        start_at=start_at,
        end_at=end_at,
        end_reason=end_reason,
        offset_ms=max(0, int((now - start_at).total_seconds() * 1000)),
    )


def resolve_play_request(
    schedule_entries: list[ScheduleEntry],
    media_items: dict[str, MediaItem],
    now: datetime,
    *,
    player_is_playing: bool,
    player_has_active_media: bool,
    queue_has_items: bool,
) -> PlayRequestOutcome:
    if player_is_playing:
        return PlayRequestOutcome(kind="already_playing")

    active_schedule = resolve_active_schedule_play(schedule_entries, media_items, now)
    if active_schedule is not None:
        return PlayRequestOutcome(kind="active_schedule", active_schedule=active_schedule)

    if player_has_active_media:
        return PlayRequestOutcome(kind="resume_loaded_media")

    if queue_has_items:
        return PlayRequestOutcome(kind="play_queue")

    return PlayRequestOutcome(kind="idle_no_media")

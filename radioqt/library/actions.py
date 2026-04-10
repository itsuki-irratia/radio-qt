from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from ..models import CronEntry, MediaItem, QueueItem, ScheduleEntry


@dataclass(slots=True)
class MediaRemovalResult:
    removed_media: MediaItem | None
    cron_entries: list[CronEntry]
    schedule_entries: list[ScheduleEntry]
    play_queue: deque[QueueItem]
    removed_cron_count: int
    removed_schedule_count: int
    removed_queue_count: int


def add_stream_media_item(
    media_items: dict[str, MediaItem],
    media_duration_cache: dict[str, int | None],
    title: str,
    source: str,
) -> MediaItem:
    media = MediaItem.create(title=title.strip(), source=source.strip())
    media_items[media.id] = media
    media_duration_cache.pop(media.id, None)
    return media


def update_stream_media_item(
    media_items: dict[str, MediaItem],
    media_duration_cache: dict[str, int | None],
    media_id: str,
    title: str,
    source: str,
) -> MediaItem | None:
    media = media_items.get(media_id)
    if media is None:
        return None

    media.title = title.strip()
    media.source = source.strip()
    media_duration_cache.pop(media_id, None)
    return media


def remove_media_from_library(
    media_items: dict[str, MediaItem],
    media_duration_cache: dict[str, int | None],
    cron_entries: list[CronEntry],
    schedule_entries: list[ScheduleEntry],
    play_queue: deque[QueueItem],
    media_id: str,
) -> MediaRemovalResult:
    removed_media = media_items.pop(media_id, None)
    if removed_media is None:
        return MediaRemovalResult(
            removed_media=None,
            cron_entries=cron_entries,
            schedule_entries=schedule_entries,
            play_queue=play_queue,
            removed_cron_count=0,
            removed_schedule_count=0,
            removed_queue_count=0,
        )

    media_duration_cache.pop(media_id, None)

    next_cron_entries = [entry for entry in cron_entries if entry.media_id != media_id]
    next_schedule_entries = [entry for entry in schedule_entries if entry.media_id != media_id]
    next_queue_items = deque(item for item in play_queue if item.media_id != media_id)

    return MediaRemovalResult(
        removed_media=removed_media,
        cron_entries=next_cron_entries,
        schedule_entries=next_schedule_entries,
        play_queue=next_queue_items,
        removed_cron_count=len(cron_entries) - len(next_cron_entries),
        removed_schedule_count=len(schedule_entries) - len(next_schedule_entries),
        removed_queue_count=len(play_queue) - len(next_queue_items),
    )

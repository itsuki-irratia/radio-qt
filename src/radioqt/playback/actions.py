from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from ..models import MediaItem, QueueItem


@dataclass(slots=True)
class DequeueResult:
    queue_item: QueueItem
    media: MediaItem
    skipped_missing_count: int = 0


def resolve_media_by_id(
    media_items: dict[str, MediaItem],
    media_id: str | None,
) -> MediaItem | None:
    if media_id is None:
        return None
    return media_items.get(media_id)


def enqueue_manual_media(play_queue: deque[QueueItem], media_id: str) -> QueueItem:
    item = QueueItem(media_id=media_id, source="manual")
    play_queue.append(item)
    return item


def enqueue_scheduled_media(
    play_queue: deque[QueueItem],
    media_id: str,
    schedule_entry_id: str,
) -> QueueItem:
    item = QueueItem(
        media_id=media_id,
        source="schedule",
        schedule_entry_id=schedule_entry_id,
    )
    play_queue.append(item)
    return item


def dequeue_next_playable_media(
    play_queue: deque[QueueItem],
    media_items: dict[str, MediaItem],
) -> DequeueResult | None:
    skipped_missing_count = 0
    while play_queue:
        next_item = play_queue.popleft()
        next_media = media_items.get(next_item.media_id)
        if next_media is None:
            skipped_missing_count += 1
            continue
        return DequeueResult(
            queue_item=next_item,
            media=next_media,
            skipped_missing_count=skipped_missing_count,
        )
    return None

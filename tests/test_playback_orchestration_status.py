from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone

from radioqt.models import MediaItem, SCHEDULE_STATUS_MISSED, SCHEDULE_STATUS_PENDING, ScheduleEntry
from radioqt.playback.orchestration import process_schedule_trigger, resolve_active_schedule_play


def test_process_schedule_trigger_keeps_one_shot_pending_until_media_starts() -> None:
    now = datetime.now(timezone.utc)
    media = MediaItem.create(title="track", source="/tmp/track.mp3")
    entry = ScheduleEntry.create(media_id=media.id, start_at=now)
    entry.one_shot = True
    entry.status = SCHEDULE_STATUS_PENDING

    outcome = process_schedule_trigger(
        entry,
        {media.id: media},
        deque(),
        automation_playing=True,
        player_is_playing=False,
        current_media_name=None,
    )

    assert outcome.kind == "play_now"
    assert entry.status == SCHEDULE_STATUS_PENDING


def test_resolve_active_schedule_play_does_not_mark_fired_early() -> None:
    now = datetime.now(timezone.utc)
    media = MediaItem.create(title="track", source="/tmp/track.mp3")
    entry = ScheduleEntry.create(media_id=media.id, start_at=now - timedelta(minutes=1))
    entry.one_shot = True
    entry.status = SCHEDULE_STATUS_PENDING

    outcome = resolve_active_schedule_play(
        [entry],
        {media.id: media},
        now,
    )

    assert outcome is not None
    assert outcome.kind == "play_active"
    assert entry.status == SCHEDULE_STATUS_PENDING


def test_resolve_active_schedule_play_keeps_missed_until_start_confirmed() -> None:
    now = datetime.now(timezone.utc)
    media = MediaItem.create(title="track", source="/tmp/track.mp3")
    entry = ScheduleEntry.create(media_id=media.id, start_at=now - timedelta(minutes=1))
    entry.one_shot = True
    entry.status = SCHEDULE_STATUS_MISSED

    outcome = resolve_active_schedule_play(
        [entry],
        {media.id: media},
        now,
    )

    assert outcome is not None
    assert outcome.kind == "play_active"
    assert entry.status == SCHEDULE_STATUS_MISSED

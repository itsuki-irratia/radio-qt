from __future__ import annotations

from datetime import datetime, timedelta, timezone

from radioqt.models import MediaItem, SCHEDULE_STATUS_DISABLED, SCHEDULE_STATUS_PENDING, ScheduleEntry
from radioqt.ui.main_window import MainWindow


class _LabelStub:
    def __init__(self) -> None:
        self.text = ""

    def setText(self, value: str) -> None:
        self.text = value


class _PlayerStub:
    def __init__(self, current_media: MediaItem | None) -> None:
        self.current_media = current_media


class _Harness:
    def __init__(self, *, current_media: MediaItem | None, schedule_entries: list[ScheduleEntry], media_items: dict[str, MediaItem]) -> None:
        self._player = _PlayerStub(current_media)
        self._schedule_entries = schedule_entries
        self._media_items = media_items
        self._now_playing_label = _LabelStub()
        self._current_playback_position_ms = 0

    def _format_duration(self, _seconds: int) -> str:
        return "00:00:00"

    def _normalized_start(self, value: datetime) -> datetime:
        return value.astimezone()


def test_now_playing_label_uses_file_name_for_local_media() -> None:
    media = MediaItem.create(title="Track title", source="/tmp/music/playlist.m3u")
    harness = _Harness(current_media=media, schedule_entries=[], media_items={media.id: media})

    MainWindow._update_now_playing_label(harness)

    assert harness._now_playing_label.text == "playlist.m3u - 00:00:00"


def test_now_playing_label_shows_coming_soon_for_next_scheduled_file() -> None:
    media = MediaItem.create(title="Track title", source="/tmp/music/next-track.mp3")
    now = datetime.now(timezone.utc)
    entry = ScheduleEntry.create(media_id=media.id, start_at=now + timedelta(minutes=15))
    entry.status = SCHEDULE_STATUS_PENDING
    harness = _Harness(current_media=None, schedule_entries=[entry], media_items={media.id: media})

    MainWindow._update_now_playing_label(harness)

    assert "COMING SOON: next-track.mp3 - " in harness._now_playing_label.text


def test_now_playing_label_handles_missing_upcoming_media() -> None:
    now = datetime.now(timezone.utc)
    entry = ScheduleEntry.create(media_id="missing-media", start_at=now + timedelta(minutes=15))
    entry.status = SCHEDULE_STATUS_PENDING
    disabled_entry = ScheduleEntry.create(media_id="other", start_at=now + timedelta(minutes=5))
    disabled_entry.status = SCHEDULE_STATUS_DISABLED
    harness = _Harness(current_media=None, schedule_entries=[entry, disabled_entry], media_items={})

    MainWindow._update_now_playing_label(harness)

    assert harness._now_playing_label.text == "COMING SOON: No file scheduled"

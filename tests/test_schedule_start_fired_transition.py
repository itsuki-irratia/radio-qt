from __future__ import annotations

from datetime import datetime, timezone

from radioqt.models import MediaItem, SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_PENDING, ScheduleEntry
from radioqt.ui.playback_handlers import MainWindowPlaybackHandlersMixin


class _PlayerStub:
    def __init__(self) -> None:
        self.current_media = None

    @staticmethod
    def current_position_ms() -> int:
        return 0


class _StartedHarness(MainWindowPlaybackHandlersMixin):
    def __init__(self, entry: ScheduleEntry | None, pending_entry_id: str | None) -> None:
        self._player = _PlayerStub()
        self._schedule_entries = [entry] if entry is not None else []
        self._pending_schedule_start_entry_id = pending_entry_id
        self._current_playback_position_ms = 0
        self.refresh_calls = 0
        self.save_calls = 0
        self.logs: list[str] = []

    def _schedule_entry_by_id(self, entry_id: str | None) -> ScheduleEntry | None:
        if entry_id is None:
            return None
        for entry in self._schedule_entries:
            if entry.id == entry_id:
                return entry
        return None

    def _refresh_schedule_table(self) -> None:
        self.refresh_calls += 1

    def _save_state(self) -> None:
        self.save_calls += 1

    def _update_now_playing_label(self) -> None:
        pass

    def _update_player_visual_state(self) -> None:
        pass

    def _append_log(self, message: str) -> None:
        self.logs.append(message)


def test_on_media_started_marks_pending_one_shot_as_fired() -> None:
    entry = ScheduleEntry.create(
        media_id="media-1",
        start_at=datetime.now(timezone.utc),
    )
    entry.one_shot = True
    entry.status = SCHEDULE_STATUS_PENDING
    window = _StartedHarness(entry, pending_entry_id=entry.id)
    media = MediaItem.create(title="song", source="/tmp/song.mp3")

    window._on_media_started(media)

    assert entry.status == SCHEDULE_STATUS_FIRED
    assert window.refresh_calls == 1
    assert window.save_calls == 1
    assert window._pending_schedule_start_entry_id is None


def test_on_media_started_without_pending_schedule_does_not_touch_state() -> None:
    entry = ScheduleEntry.create(
        media_id="media-1",
        start_at=datetime.now(timezone.utc),
    )
    entry.one_shot = True
    entry.status = SCHEDULE_STATUS_PENDING
    window = _StartedHarness(entry, pending_entry_id=None)
    media = MediaItem.create(title="song", source="/tmp/song.mp3")

    window._on_media_started(media)

    assert entry.status == SCHEDULE_STATUS_PENDING
    assert window.refresh_calls == 0
    assert window.save_calls == 0

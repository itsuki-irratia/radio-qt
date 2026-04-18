from __future__ import annotations

from collections import deque
from datetime import datetime, timezone

from radioqt.models import SCHEDULE_STATUS_MISSED, SCHEDULE_STATUS_PENDING, MediaItem, ScheduleEntry
from radioqt.player.controller import MediaPlayerController
from radioqt.ui.playback_handlers import MainWindowPlaybackHandlersMixin


class _PlayerStub:
    def __init__(self) -> None:
        self.current_media = None
        self.play_calls: list[tuple[MediaItem, dict[str, object]]] = []

    def is_playing(self) -> bool:
        return False

    def play_media(self, media: MediaItem, **kwargs: object) -> None:
        self.play_calls.append((media, kwargs))

    def clear_current_media(self) -> None:
        self.current_media = None


class _E2EHarness(MainWindowPlaybackHandlersMixin):
    def __init__(self, *, media: MediaItem, entry: ScheduleEntry) -> None:
        self._player = _PlayerStub()
        self._media_items = {media.id: media}
        self._schedule_entries = [entry]
        self._play_queue = deque()
        self._automation_playing = True
        self._pending_schedule_start_entry_id = None
        self._current_playback_position_ms = 0
        self._fade_in_duration_seconds = 5
        self._fade_out_duration_seconds = 5
        self._shutting_down = False
        self.logs: list[str] = []
        self.refresh_calls = 0
        self.save_calls = 0

    def _normalized_start(self, value: datetime) -> datetime:
        return value.astimezone()

    def _entry_duration_ms(self, _entry: ScheduleEntry | None) -> int | None:
        return None

    def _fade_in_duration_ms(self) -> int:
        return self._fade_in_duration_seconds * 1000

    def _fade_out_duration_ms(self) -> int:
        return self._fade_out_duration_seconds * 1000

    def _media_log_name(self, media_id: str) -> str:
        media = self._media_items.get(media_id)
        return media.title if media is not None else media_id

    def _refresh_schedule_table(self) -> None:
        self.refresh_calls += 1

    def _save_state(self) -> None:
        self.save_calls += 1

    def _append_log(self, message: str) -> None:
        self.logs.append(message)

    def _update_now_playing_label(self) -> None:
        pass

    def _update_player_visual_state(self) -> None:
        pass

    def _schedule_entry_by_id(self, entry_id: str | None) -> ScheduleEntry | None:
        if entry_id is None:
            return None
        for entry in self._schedule_entries:
            if entry.id == entry_id:
                return entry
        return None


def test_schedule_trigger_then_play_rejection_marks_one_shot_as_missed() -> None:
    media = MediaItem.create(title="broken", source="/tmp/missing.mp3")
    entry = ScheduleEntry.create(
        media_id=media.id,
        start_at=datetime.now(timezone.utc),
    )
    entry.one_shot = True
    entry.status = SCHEDULE_STATUS_PENDING
    window = _E2EHarness(media=media, entry=entry)

    window._on_schedule_triggered(entry)

    assert len(window._player.play_calls) == 1
    assert window._pending_schedule_start_entry_id == entry.id
    assert entry.status == SCHEDULE_STATUS_PENDING

    window._on_player_error(
        (
            f"{MediaPlayerController._PLAY_REQUEST_REJECTED_PREFIX}"
            "Local media file does not exist: /tmp/missing.mp3"
        )
    )

    assert entry.status == SCHEDULE_STATUS_MISSED
    assert window._pending_schedule_start_entry_id is None
    assert window.refresh_calls == 2
    assert window.save_calls == 2
    assert any("Marked scheduled one-shot as missed" in line for line in window.logs)

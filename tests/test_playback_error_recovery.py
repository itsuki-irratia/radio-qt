from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from radioqt.models import SCHEDULE_STATUS_MISSED, SCHEDULE_STATUS_PENDING, ScheduleEntry
from radioqt.player.controller import MediaPlayerController
from radioqt.ui.playback_handlers import MainWindowPlaybackHandlersMixin


@dataclass(slots=True)
class _Media:
    title: str


class _PlayerStub:
    def __init__(self, *, playing: bool = True) -> None:
        self.current_media = _Media(title="broken-media")
        self.clear_calls = 0
        self.playing = playing

    def clear_current_media(self) -> None:
        self.clear_calls += 1
        self.current_media = None

    def is_playing(self) -> bool:
        return self.playing


class _PlaybackHarness(MainWindowPlaybackHandlersMixin):
    def __init__(
        self,
        *,
        queued_items: int = 0,
        shutting_down: bool = False,
        player_is_playing: bool = True,
        pending_schedule_entry: ScheduleEntry | None = None,
    ) -> None:
        self._player = _PlayerStub(playing=player_is_playing)
        self._play_queue = deque(range(queued_items))
        self._shutting_down = shutting_down
        self._current_playback_position_ms = 1234
        self._pending_schedule_start_entry_id = (
            pending_schedule_entry.id if pending_schedule_entry is not None else None
        )
        self._schedule_entries = [pending_schedule_entry] if pending_schedule_entry is not None else []
        self.logs: list[str] = []
        self.play_next_calls = 0
        self.save_state_calls = 0
        self.update_now_playing_calls = 0
        self.update_visual_calls = 0
        self.refresh_calls = 0

    def _append_log(self, message: str) -> None:
        self.logs.append(message)

    def _play_next_from_queue(self) -> None:
        self.play_next_calls += 1

    def _save_state(self) -> None:
        self.save_state_calls += 1

    def _update_now_playing_label(self) -> None:
        self.update_now_playing_calls += 1

    def _update_player_visual_state(self) -> None:
        self.update_visual_calls += 1

    def _refresh_schedule_table(self) -> None:
        self.refresh_calls += 1

    def _schedule_entry_by_id(self, entry_id: str | None) -> ScheduleEntry | None:
        if entry_id is None:
            return None
        for entry in self._schedule_entries:
            if entry.id == entry_id:
                return entry
        return None


def test_on_player_error_recovers_with_queued_media() -> None:
    window = _PlaybackHarness(queued_items=1)

    window._on_player_error("decode failed")

    assert window._player.clear_calls == 1
    assert window._current_playback_position_ms == 0
    assert window.play_next_calls == 1
    assert window.save_state_calls == 0
    assert window.update_now_playing_calls == 1
    assert window.update_visual_calls == 1
    assert any("Playback recovery: trying next queued media item" in line for line in window.logs)


def test_on_player_error_clears_player_and_saves_when_queue_empty() -> None:
    window = _PlaybackHarness()

    window._on_player_error("permission denied")

    assert window._player.clear_calls == 1
    assert window._current_playback_position_ms == 0
    assert window.play_next_calls == 0
    assert window.save_state_calls == 1
    assert window.update_now_playing_calls == 1
    assert window.update_visual_calls == 1


def test_on_player_error_no_recovery_when_shutting_down() -> None:
    window = _PlaybackHarness(queued_items=1, shutting_down=True)

    window._on_player_error("ignored on shutdown")

    assert window._player.clear_calls == 0
    assert window._current_playback_position_ms == 1234
    assert window.play_next_calls == 0
    assert window.save_state_calls == 0
    assert window.update_now_playing_calls == 0
    assert window.update_visual_calls == 0


def test_on_player_error_play_request_rejection_keeps_current_media_and_marks_pending_schedule_missed() -> None:
    pending_entry = ScheduleEntry.create(
        media_id="media-1",
        start_at=datetime.now(timezone.utc),
    )
    pending_entry.one_shot = True
    pending_entry.status = SCHEDULE_STATUS_PENDING
    window = _PlaybackHarness(pending_schedule_entry=pending_entry, player_is_playing=True)
    rejection_message = (
        f"{MediaPlayerController._PLAY_REQUEST_REJECTED_PREFIX}Local media file does not exist: /tmp/missing.mp3"
    )

    window._on_player_error(rejection_message)

    assert window._player.clear_calls == 0
    assert pending_entry.status == SCHEDULE_STATUS_MISSED
    assert window.refresh_calls == 1
    assert window.save_state_calls == 1
    assert window.play_next_calls == 0


def test_on_player_error_play_request_rejection_advances_queue_when_idle() -> None:
    window = _PlaybackHarness(queued_items=1, player_is_playing=False)
    rejection_message = (
        f"{MediaPlayerController._PLAY_REQUEST_REJECTED_PREFIX}Local media source is not a file: /tmp"
    )

    window._on_player_error(rejection_message)

    assert window._player.clear_calls == 0
    assert window.play_next_calls == 1
    assert window.update_now_playing_calls == 0
    assert window.update_visual_calls == 0

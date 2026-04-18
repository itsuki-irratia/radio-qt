from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from radioqt.ui.playback_handlers import MainWindowPlaybackHandlersMixin


@dataclass(slots=True)
class _Media:
    title: str


class _PlayerStub:
    def __init__(self) -> None:
        self.current_media = _Media(title="broken-media")
        self.clear_calls = 0

    def clear_current_media(self) -> None:
        self.clear_calls += 1
        self.current_media = None


class _PlaybackHarness(MainWindowPlaybackHandlersMixin):
    def __init__(self, *, queued_items: int = 0, shutting_down: bool = False) -> None:
        self._player = _PlayerStub()
        self._play_queue = deque(range(queued_items))
        self._shutting_down = shutting_down
        self._current_playback_position_ms = 1234
        self.logs: list[str] = []
        self.play_next_calls = 0
        self.save_state_calls = 0
        self.update_now_playing_calls = 0
        self.update_visual_calls = 0

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

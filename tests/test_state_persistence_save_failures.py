from __future__ import annotations

from pathlib import Path

from radioqt.models import AppState
import radioqt.ui.state_persistence as state_persistence_module
from radioqt.ui.state_persistence import MainWindowStatePersistenceMixin


class _SaveHarness(MainWindowStatePersistenceMixin):
    def __init__(self, state_path: Path, settings_path: Path) -> None:
        self._state_path = state_path
        self._settings_path = settings_path
        self._state_version = 7
        self.logs: list[str] = []

        self._fade_in_duration_seconds = 5
        self._fade_out_duration_seconds = 5
        self._filesystem_default_fade_in = False
        self._filesystem_default_fade_out = False
        self._streams_default_fade_in = False
        self._streams_default_fade_out = False
        self._media_library_width_percent = 35
        self._schedule_width_percent = 65
        self._font_size_points = 10
        self._library_tab_configs = []
        self._export_path_mappings = []
        self._supported_extensions = [".mp3"]
        self._greenwich_time_signal_enabled = False
        self._greenwich_time_signal_path = ""
        self._icecast_status = False
        self._icecast_run_in_background = False
        self._icecast_command = ""
        self._icecast_input_format = "alsa"
        self._icecast_thread_queue_size = 64
        self._icecast_device = "default"
        self._icecast_audio_channels = 2
        self._icecast_audio_rate = 44100
        self._icecast_audio_codec = "libmp3lame"
        self._icecast_audio_bitrate = 192
        self._icecast_content_type = "audio/mpeg"
        self._icecast_output_format = "mp3"
        self._icecast_url = "icecast://source:pass@127.0.0.1:8000/mount"
        self._volume_slider = type("_SliderStub", (), {"value": lambda _self: 100})()

    def _append_log(self, message: str) -> None:
        self.logs.append(message)

    def _build_app_state_snapshot(self) -> AppState:
        return AppState()

    def _queue_incremental_schedule_export(self, *_args, **_kwargs) -> None:
        return None

    def _reload_runtime_state_after_conflict(self) -> None:
        return None


def test_save_state_logs_and_keeps_running_when_persistence_fails(monkeypatch, tmp_path) -> None:
    def _raise(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(state_persistence_module, "save_state", _raise)
    harness = _SaveHarness(tmp_path / "db.sqlite", tmp_path / "settings.yaml")

    harness._save_state()

    assert harness._state_version == 7
    assert any("Failed to persist runtime state" in line for line in harness.logs)


def test_save_settings_logs_when_write_fails(monkeypatch, tmp_path) -> None:
    def _raise(*_args, **_kwargs):
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(state_persistence_module, "save_app_config", _raise)
    harness = _SaveHarness(tmp_path / "db.sqlite", tmp_path / "settings.yaml")

    harness._save_settings()

    assert any("Failed to persist settings" in line for line in harness.logs)

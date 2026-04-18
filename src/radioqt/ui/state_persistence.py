from __future__ import annotations

from collections import deque
from datetime import datetime
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import time

from PySide6.QtWidgets import QApplication

from ..app_config import AppConfig, load_app_config, save_app_config
from ..duration_probe import sanitize_duration_probe_cache
from ..models import AppState
from ..runtime_control import (
    drain_runtime_control_commands,
    RUNTIME_CONTROL_ACTION_FADE_IN,
    RUNTIME_CONTROL_ACTION_FADE_OUT,
    RUNTIME_CONTROL_ACTION_SET_VOLUME,
    RUNTIME_CONTROL_ACTION_START_AUTOMATION,
    RUNTIME_CONTROL_ACTION_STOP_AUTOMATION,
)
from ..runtime_status import is_pid_running
from ..stream_relay import (
    build_icecast_ffmpeg_command,
    delete_stream_relay_pid,
    DEFAULT_ICECAST_AUDIO_BITRATE,
    DEFAULT_ICECAST_AUDIO_CHANNELS,
    DEFAULT_ICECAST_AUDIO_CODEC,
    DEFAULT_ICECAST_AUDIO_RATE,
    DEFAULT_ICECAST_CONTENT_TYPE,
    DEFAULT_ICECAST_DEVICE,
    DEFAULT_ICECAST_INPUT_FORMAT,
    DEFAULT_ICECAST_OUTPUT_FORMAT,
    DEFAULT_ICECAST_THREAD_QUEUE_SIZE,
    DEFAULT_ICECAST_URL,
    IcecastFfmpegConfig,
    read_stream_relay_pid,
    stream_relay_stderr_file_path,
    stream_relay_stdout_file_path,
    write_stream_relay_pid,
)
from ..scheduling import initial_schedule_filter_date, prepare_schedule_entries_for_startup
from ..storage import (
    load_state_with_version,
    save_state,
    state_version,
    StateVersionConflictError,
)


class MainWindowStatePersistenceMixin:
    _ICECAST_LOG_MASK_PATTERN = re.compile(r"(icecast://[^:/@\s]+:)([^@/\s]+)(@)")

    def _mask_icecast_command_for_log(self, value: str) -> str:
        return self._ICECAST_LOG_MASK_PATTERN.sub(r"\1***\3", value)

    @staticmethod
    def _tail_text_file(path: Path, *, max_lines: int = 6) -> list[str]:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        if max_lines <= 0:
            return []
        return [line.strip() for line in lines[-max_lines:] if line.strip()]

    @staticmethod
    def _wait_for_pid_shutdown(pid: int, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while time.monotonic() < deadline:
            if not is_pid_running(pid):
                return True
            time.sleep(0.1)
        return not is_pid_running(pid)

    def _resolved_icecast_command(self) -> str:
        manual_command = str(self._icecast_command or "").strip()
        if manual_command:
            return manual_command
        return build_icecast_ffmpeg_command(
            IcecastFfmpegConfig(
                input_format=str(self._icecast_input_format or "").strip()
                or DEFAULT_ICECAST_INPUT_FORMAT,
                thread_queue_size=max(1, int(self._icecast_thread_queue_size)),
                device=str(self._icecast_device or "").strip() or DEFAULT_ICECAST_DEVICE,
                audio_channels=max(1, int(self._icecast_audio_channels)),
                audio_rate=max(1, int(self._icecast_audio_rate)),
                audio_codec=str(self._icecast_audio_codec or "").strip() or DEFAULT_ICECAST_AUDIO_CODEC,
                audio_bitrate=max(1, int(self._icecast_audio_bitrate)),
                content_type=str(self._icecast_content_type or "").strip()
                or DEFAULT_ICECAST_CONTENT_TYPE,
                output_format=str(self._icecast_output_format or "").strip()
                or DEFAULT_ICECAST_OUTPUT_FORMAT,
                icecast_url=str(self._icecast_url or "").strip() or DEFAULT_ICECAST_URL,
            )
        )

    def _synchronize_icecast_runtime(self, *, reason: str) -> None:
        configured_command = self._resolved_icecast_command()
        enabled = bool(self._icecast_status)
        tracked_pid = read_stream_relay_pid(self._config_dir)
        running = is_pid_running(tracked_pid)
        if tracked_pid is not None and not running:
            delete_stream_relay_pid(self._config_dir)
            tracked_pid = None

        if enabled:
            if running and tracked_pid is not None:
                self._append_log(
                    f"Icecast already running (pid={tracked_pid}) [{reason}]"
                )
                return
            if not configured_command:
                self._append_log(
                    f"Icecast is enabled but command is empty; not starting [{reason}]"
                )
                return
            stdout_path = stream_relay_stdout_file_path(self._config_dir)
            stderr_path = stream_relay_stderr_file_path(self._config_dir)
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                stdout_handle = stdout_path.open("a", encoding="utf-8")
                stderr_handle = stderr_path.open("a", encoding="utf-8")
            except OSError as exc:
                self._append_log(
                    f"Icecast start failed opening log files [{reason}]: {exc}"
                )
                return

            try:
                process = subprocess.Popen(
                    ["/bin/bash", "-lc", f"exec {configured_command}"],
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    start_new_session=True,
                )
            except OSError as exc:
                self._append_log(f"Icecast start failed [{reason}]: {exc}")
                try:
                    stdout_handle.close()
                except Exception:
                    pass
                try:
                    stderr_handle.close()
                except Exception:
                    pass
                return
            finally:
                try:
                    stdout_handle.close()
                except Exception:
                    pass
                try:
                    stderr_handle.close()
                except Exception:
                    pass

            time.sleep(0.35)
            immediate_exit_code = process.poll()
            if immediate_exit_code is not None:
                stderr_tail = self._tail_text_file(stderr_path, max_lines=6)
                detail = f"exit={immediate_exit_code}"
                if stderr_tail:
                    tail_text = " | ".join(stderr_tail[-2:])
                    detail += f", stderr={self._mask_icecast_command_for_log(tail_text)}"
                self._append_log(
                    f"Icecast failed to stay running after start [{reason}]: {detail}"
                )
                return

            write_stream_relay_pid(self._config_dir, process.pid)
            masked_command = self._mask_icecast_command_for_log(configured_command)
            self._append_log(
                (
                    f"Icecast started from GUI (pid={process.pid}) [{reason}], "
                    f"command={masked_command}, stdout={stdout_path}, stderr={stderr_path}"
                )
            )
            return

        if tracked_pid is None or not is_pid_running(tracked_pid):
            return
        try:
            os.killpg(tracked_pid, signal.SIGTERM)
        except ProcessLookupError:
            delete_stream_relay_pid(self._config_dir)
            return
        except PermissionError as exc:
            self._append_log(
                f"Icecast stop failed (permission denied) [{reason}] pid={tracked_pid}: {exc}"
            )
            return
        except OSError as exc:
            self._append_log(
                f"Icecast stop failed [{reason}] pid={tracked_pid}: {exc}"
            )
            return

        if not self._wait_for_pid_shutdown(tracked_pid, 3.0):
            self._append_log(
                (
                    f"Icecast did not stop within timeout [{reason}] pid={tracked_pid}; "
                    "use CLI stop --force if needed"
                )
            )
            return
        delete_stream_relay_pid(self._config_dir)
        self._append_log(f"Icecast stopped from GUI [{reason}] pid={tracked_pid}")

    def _load_initial_state(self) -> None:
        app_started_at = datetime.now().astimezone()
        self._migrate_legacy_state_location_if_needed()
        loaded_state = load_state_with_version(self._state_path)
        self._state_version = loaded_state.version
        state = loaded_state.state
        app_config = self._load_or_initialize_app_config(state)
        self._media_items = {item.id: item for item in state.media_items}
        self._media_duration_cache.clear()
        self._duration_probe_cache = sanitize_duration_probe_cache(
            state.duration_probe_cache,
            max_entries=self._DURATION_PROBE_CACHE_MAX_ENTRIES,
        )
        self._media_duration_pending.clear()
        self._schedule_entries = state.schedule_entries
        self._cron_entries = state.cron_entries
        self._play_queue = deque(state.queue)
        hard_sync_normalized = self._enforce_hard_sync_always()
        self._library_tab_configs = list(app_config.library_tabs)
        self._supported_extensions = self._normalize_supported_extensions(app_config.supported_extensions)
        self._schedule_auto_focus_enabled = state.schedule_auto_focus
        self._logs_visible = state.logs_visible
        self._apply_panel_width_split(app_config.media_library_width_percent)
        shared_fade_duration_seconds = max(
            1,
            max(app_config.fade_in_duration_seconds, app_config.fade_out_duration_seconds),
        )
        self._fade_in_duration_seconds = shared_fade_duration_seconds
        self._fade_out_duration_seconds = shared_fade_duration_seconds
        self._filesystem_default_fade_in = bool(app_config.filesystem_default_fade_in)
        self._filesystem_default_fade_out = bool(app_config.filesystem_default_fade_out)
        self._streams_default_fade_in = bool(app_config.streams_default_fade_in)
        self._streams_default_fade_out = bool(app_config.streams_default_fade_out)
        self._greenwich_time_signal_enabled = bool(app_config.greenwich_time_signal_enabled)
        self._greenwich_time_signal_path = str(app_config.greenwich_time_signal_path).strip()
        self._icecast_status = bool(app_config.icecast_status)
        self._icecast_run_in_background = bool(app_config.icecast_run_in_background)
        self._icecast_command = str(app_config.icecast_command).strip()
        self._icecast_input_format = str(app_config.icecast_input_format).strip()
        self._icecast_thread_queue_size = max(1, int(app_config.icecast_thread_queue_size))
        self._icecast_device = str(app_config.icecast_device).strip()
        self._icecast_audio_channels = max(1, int(app_config.icecast_audio_channels))
        self._icecast_audio_rate = max(1, int(app_config.icecast_audio_rate))
        self._icecast_audio_codec = str(app_config.icecast_audio_codec).strip()
        self._icecast_audio_bitrate = max(1, int(app_config.icecast_audio_bitrate))
        self._icecast_content_type = str(app_config.icecast_content_type).strip()
        self._icecast_output_format = str(app_config.icecast_output_format).strip()
        self._icecast_url = str(app_config.icecast_url).strip()
        if not self._icecast_command:
            self._icecast_command = self._resolved_icecast_command()
        self._volume_slider.setValue(max(0, min(100, int(app_config.default_volume_percent))))
        if app_config.font_size is not None:
            self._font_size_points = max(1, app_config.font_size)
        self._apply_global_font_size(self._font_size_points)
        loaded_schedule_count = len(self._schedule_entries)
        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_schedule_durations()
        startup_preparation = prepare_schedule_entries_for_startup(
            self._schedule_entries,
            app_started_at,
        )
        normalized_details = self._normalized_missed_details(
            app_started_at,
            startup_preparation.normalized_entries,
        )
        self._schedule_filter_date = initial_schedule_filter_date(
            self._schedule_entries,
            self._cron_entries,
            datetime.now().astimezone(),
        )
        self._set_schedule_filter_date(self._schedule_filter_date)
        self._schedule_focus_checkbox.blockSignals(True)
        self._schedule_focus_checkbox.setChecked(self._schedule_auto_focus_enabled)
        self._schedule_focus_checkbox.blockSignals(False)
        self._toggle_logs_action.blockSignals(True)
        self._toggle_logs_action.setChecked(self._logs_visible)
        self._toggle_logs_action.blockSignals(False)
        self._set_logs_visible(self._logs_visible)
        self._apply_supported_extensions_to_filesystem_models()
        self._rebuild_custom_library_tabs()
        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_schedule_durations()
        runtime_pruned_count = max(0, loaded_schedule_count - len(self._schedule_entries))

        self._refresh_urls_list()
        self._refresh_cron_table()
        self._refresh_schedule_table()
        self._apply_schedule_auto_focus(force=True)
        self._scheduler.set_entries(self._schedule_entries)
        self._player.set_volume(self._volume_slider.value())
        self._update_player_visual_state()
        if runtime_pruned_count:
            self._append_log(
                f"Pruned {runtime_pruned_count} CRON occurrence(s) outside runtime window (today/tomorrow)"
            )
        if startup_preparation.normalized_entries:
            self._append_log(
                f"Normalized {len(startup_preparation.normalized_entries)} past one-shot schedule item(s) to missed on startup"
            )
            self._append_normalized_missed_logs(
                len(startup_preparation.normalized_entries),
                normalized_details,
            )
            self._save_state()
        elif startup_preparation.restored_count:
            self._append_log(
                f"Restored {startup_preparation.restored_count} active one-shot schedule item(s) from missed on startup"
            )
            self._save_state()
        elif runtime_pruned_count or hard_sync_normalized:
            self._save_state()
        if hard_sync_normalized:
            self._append_log("Hard sync is now always active for all schedule/CRON entries")
        self._append_log(f"Loaded state from {self._state_path}")
        self._synchronize_icecast_runtime(reason="startup")

    def _save_state(self) -> None:
        state = AppState(
            media_items=list(self._media_items.values()),
            schedule_entries=self._schedule_entries,
            cron_entries=self._cron_entries,
            queue=list(self._play_queue),
            library_tabs=self._library_tab_configs,
            supported_extensions=self._supported_extensions,
            schedule_auto_focus=self._schedule_auto_focus_enabled,
            logs_visible=self._logs_visible,
            fade_in_duration_seconds=self._fade_in_duration_seconds,
            fade_out_duration_seconds=self._fade_out_duration_seconds,
            duration_probe_cache=dict(self._duration_probe_cache),
        )
        try:
            self._state_version = save_state(
                self._state_path,
                state,
                expected_version=self._state_version,
            )
        except StateVersionConflictError as conflict_error:
            self._append_log(
                (
                    "Detected external state changes; reloading latest data from disk "
                    f"(local expected version {conflict_error.expected_version}, "
                    f"current version {conflict_error.current_version})"
                )
            )
            self._reload_runtime_state_after_conflict()

    def _reload_runtime_state_after_conflict(self) -> None:
        loaded_state = load_state_with_version(self._state_path)
        self._state_version = loaded_state.version
        state = loaded_state.state
        self._media_items = {item.id: item for item in state.media_items}
        self._media_duration_cache.clear()
        self._duration_probe_cache = sanitize_duration_probe_cache(
            state.duration_probe_cache,
            max_entries=self._DURATION_PROBE_CACHE_MAX_ENTRIES,
        )
        self._media_duration_pending.clear()
        self._schedule_entries = state.schedule_entries
        self._cron_entries = state.cron_entries
        self._play_queue = deque(state.queue)
        self._schedule_auto_focus_enabled = state.schedule_auto_focus
        self._logs_visible = state.logs_visible
        self._schedule_focus_checkbox.blockSignals(True)
        self._schedule_focus_checkbox.setChecked(self._schedule_auto_focus_enabled)
        self._schedule_focus_checkbox.blockSignals(False)
        self._toggle_logs_action.blockSignals(True)
        self._toggle_logs_action.setChecked(self._logs_visible)
        self._toggle_logs_action.blockSignals(False)
        self._set_logs_visible(self._logs_visible)
        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_schedule_durations()
        self._refresh_urls_list()
        self._refresh_cron_table()
        self._refresh_schedule_table()
        self._scheduler.set_entries(self._schedule_entries)

    def _sync_external_state_if_needed(self) -> None:
        if self._shutting_down:
            return
        try:
            current_external_version = state_version(self._state_path)
        except Exception:
            return
        if current_external_version <= self._state_version:
            return
        self._append_log(
            (
                "Detected external state update from another process "
                f"(local version {self._state_version}, external version {current_external_version}); "
                "reloading runtime state"
            )
        )
        self._reload_runtime_state_after_conflict()

    def _process_runtime_control_commands(self) -> None:
        commands = drain_runtime_control_commands(self._config_dir)
        if not commands:
            return
        for command in commands:
            if command.action == RUNTIME_CONTROL_ACTION_FADE_IN:
                self._on_volume_fade_in_clicked()
                self._append_log(f"Runtime CLI command executed: fade-in ({command.command_id})")
                continue
            if command.action == RUNTIME_CONTROL_ACTION_FADE_OUT:
                self._on_volume_fade_out_clicked()
                self._append_log(f"Runtime CLI command executed: fade-out ({command.command_id})")
                continue
            if command.action == RUNTIME_CONTROL_ACTION_SET_VOLUME:
                if command.value is None:
                    continue
                self._apply_runtime_volume_value(command.value)
                self._append_log(
                    f"Runtime CLI command executed: set-volume {command.value}% ({command.command_id})"
                )
                continue
            if command.action == RUNTIME_CONTROL_ACTION_START_AUTOMATION:
                self._on_play_clicked()
                self._append_log(f"Runtime CLI command executed: online ({command.command_id})")
                continue
            if command.action == RUNTIME_CONTROL_ACTION_STOP_AUTOMATION:
                self._on_stop_clicked()
                self._append_log(f"Runtime CLI command executed: offline ({command.command_id})")

    def _apply_runtime_volume_value(self, value: int) -> None:
        normalized_value = max(0, min(100, int(value)))
        if normalized_value <= 0:
            if not self._mute_button.isChecked():
                self._mute_button.setChecked(True)
            else:
                self._volume_slider.setValue(0)
            return
        if self._mute_button.isChecked():
            self._last_nonzero_volume = normalized_value
            self._mute_button.setChecked(False)
            return
        self._volume_slider.setValue(normalized_value)

    def _save_settings(self) -> None:
        shared_fade_duration_seconds = max(
            1,
            max(self._fade_in_duration_seconds, self._fade_out_duration_seconds),
        )
        app_config = AppConfig(
            fade_in_duration_seconds=shared_fade_duration_seconds,
            fade_out_duration_seconds=shared_fade_duration_seconds,
            filesystem_default_fade_in=self._filesystem_default_fade_in,
            filesystem_default_fade_out=self._filesystem_default_fade_out,
            streams_default_fade_in=self._streams_default_fade_in,
            streams_default_fade_out=self._streams_default_fade_out,
            media_library_width_percent=self._media_library_width_percent,
            schedule_width_percent=self._schedule_width_percent,
            font_size=self._font_size_points,
            library_tabs=list(self._library_tab_configs),
            supported_extensions=list(self._supported_extensions),
            greenwich_time_signal_enabled=self._greenwich_time_signal_enabled,
            greenwich_time_signal_path=self._greenwich_time_signal_path,
            default_volume_percent=self._volume_slider.value(),
            icecast_status=self._icecast_status,
            icecast_run_in_background=self._icecast_run_in_background,
            icecast_command=self._icecast_command,
            icecast_input_format=self._icecast_input_format,
            icecast_thread_queue_size=self._icecast_thread_queue_size,
            icecast_device=self._icecast_device,
            icecast_audio_channels=self._icecast_audio_channels,
            icecast_audio_rate=self._icecast_audio_rate,
            icecast_audio_codec=self._icecast_audio_codec,
            icecast_audio_bitrate=self._icecast_audio_bitrate,
            icecast_content_type=self._icecast_content_type,
            icecast_output_format=self._icecast_output_format,
            icecast_url=self._icecast_url,
        )
        save_app_config(self._settings_path, app_config)

    def _load_or_initialize_app_config(self, state: AppState) -> AppConfig:
        if self._settings_path.exists():
            config = load_app_config(self._settings_path)
            if config.font_size is None:
                config.font_size = self._font_size_points
                save_app_config(self._settings_path, config)
            return config

        seeded_config = AppConfig(
            fade_in_duration_seconds=max(1, state.fade_in_duration_seconds),
            fade_out_duration_seconds=max(1, state.fade_out_duration_seconds),
            filesystem_default_fade_in=False,
            filesystem_default_fade_out=False,
            streams_default_fade_in=False,
            streams_default_fade_out=False,
            media_library_width_percent=35,
            schedule_width_percent=65,
            font_size=self._font_size_points,
            library_tabs=list(state.library_tabs),
            supported_extensions=self._normalize_supported_extensions(state.supported_extensions),
            greenwich_time_signal_enabled=False,
            greenwich_time_signal_path="",
            default_volume_percent=100,
            icecast_status=False,
            icecast_run_in_background=False,
            icecast_command="",
            icecast_input_format=DEFAULT_ICECAST_INPUT_FORMAT,
            icecast_thread_queue_size=DEFAULT_ICECAST_THREAD_QUEUE_SIZE,
            icecast_device=DEFAULT_ICECAST_DEVICE,
            icecast_audio_channels=DEFAULT_ICECAST_AUDIO_CHANNELS,
            icecast_audio_rate=DEFAULT_ICECAST_AUDIO_RATE,
            icecast_audio_codec=DEFAULT_ICECAST_AUDIO_CODEC,
            icecast_audio_bitrate=DEFAULT_ICECAST_AUDIO_BITRATE,
            icecast_content_type=DEFAULT_ICECAST_CONTENT_TYPE,
            icecast_output_format=DEFAULT_ICECAST_OUTPUT_FORMAT,
            icecast_url=DEFAULT_ICECAST_URL,
        )
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        save_app_config(self._settings_path, seeded_config)
        return seeded_config

    def _migrate_legacy_state_location_if_needed(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        # Legacy auto-migration is only enabled for the historical local config dir.
        # With the default config moved to ~/.config/radioqt, silently importing
        # from ./state would repopulate data after manual cleanup.
        legacy_local_config_dir = (Path.cwd() / "config").expanduser()
        if self._config_dir != legacy_local_config_dir:
            return
        if not self._state_path.exists() and self._legacy_state_path.exists():
            try:
                shutil.copy2(self._legacy_state_path, self._state_path)
            except OSError:
                pass
        target_legacy_json_path = self._state_path.with_suffix(".json")
        if (
            not self._state_path.exists()
            and not target_legacy_json_path.exists()
            and self._legacy_state_json_path.exists()
        ):
            try:
                shutil.copy2(self._legacy_state_json_path, target_legacy_json_path)
            except OSError:
                pass

    @staticmethod
    def _default_font_size_points() -> int:
        app = QApplication.instance()
        if app is None:
            return 10
        point_size = app.font().pointSize()
        if point_size <= 0:
            return 10
        return int(point_size)

    def _apply_panel_width_split(self, media_library_width_percent: int) -> None:
        normalized_media_library_width_percent = max(10, min(90, int(media_library_width_percent)))
        normalized_schedule_width_percent = 100 - normalized_media_library_width_percent
        self._media_library_width_percent = normalized_media_library_width_percent
        self._schedule_width_percent = normalized_schedule_width_percent
        if self._panels_layout is None:
            return
        self._panels_layout.setStretch(0, self._media_library_width_percent)
        self._panels_layout.setStretch(1, self._schedule_width_percent)

    def _apply_global_font_size(self, font_size_points: int) -> None:
        normalized_size = max(1, int(font_size_points))
        app = QApplication.instance()
        if app is None:
            self._font_size_points = normalized_size
            return
        font = app.font()
        if font.pointSize() != normalized_size:
            font.setPointSize(normalized_size)
            app.setFont(font)
        self._font_size_points = normalized_size

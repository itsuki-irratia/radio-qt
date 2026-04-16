from __future__ import annotations

from collections import deque
from datetime import datetime
from pathlib import Path
import shutil

from PySide6.QtWidgets import QApplication

from ..app_config import AppConfig, load_app_config, save_app_config
from ..duration_probe import sanitize_duration_probe_cache
from ..models import AppState
from ..scheduling import initial_schedule_filter_date, prepare_schedule_entries_for_startup
from ..storage import (
    load_state_with_version,
    save_state,
    state_version,
    StateVersionConflictError,
)


class MainWindowStatePersistenceMixin:
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

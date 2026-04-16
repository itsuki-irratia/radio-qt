from __future__ import annotations

from PySide6.QtCore import QEvent, Qt


class MainWindowInteractionRuntimeMixin:
    def _wire_signals(self) -> None:
        self._filesystem_view.clicked.connect(self._on_filesystem_selected)
        self._urls_table.itemSelectionChanged.connect(self._on_urls_selection_changed)
        self._library_tabs.currentChanged.connect(self._on_library_tab_changed)
        self._add_stream_button.clicked.connect(self._add_media_url)

        self._add_schedule_button.clicked.connect(self._add_schedule_entry)
        self._add_cron_button.clicked.connect(self._add_cron_schedule)
        self._schedule_date_selector.dateChanged.connect(self._on_schedule_filter_date_changed)
        self._schedule_focus_checkbox.toggled.connect(self._on_schedule_auto_focus_toggled)
        self._schedule_table.cellPressed.connect(self._on_schedule_table_cell_pressed)

        self._automation_status_button.clicked.connect(self._on_play_stop_clicked)
        self._mute_button.toggled.connect(self._on_mute_toggled)
        self._fade_in_button.clicked.connect(self._on_volume_fade_in_clicked)
        self._fade_out_button.clicked.connect(self._on_volume_fade_out_clicked)
        self._volume_slider.valueChanged.connect(self._player.set_volume)
        self._volume_fade_timer.timeout.connect(self._on_volume_fade_tick)

        self._player.media_started.connect(self._on_media_started)
        self._player.media_finished.connect(self._on_media_finished)
        self._player.playback_state_changed.connect(self._on_playback_state_changed)
        self._player.playback_position_changed.connect(self._on_playback_position_changed)
        self._player.playback_error.connect(self._on_player_error)
        self._player.audio_levels_changed.connect(self._on_audio_levels_changed)
        self._duration_probe_dispatcher.probe_finished.connect(self._on_media_duration_probed)

        self._scheduler.schedule_triggered.connect(self._on_schedule_triggered)
        self._scheduler.log.connect(self._append_log)
        self._cron_refresh_timer.timeout.connect(self._refresh_cron_runtime_window)
        self._schedule_focus_timer.timeout.connect(self._refresh_schedule_auto_focus)
        self._external_state_sync_timer.timeout.connect(self._sync_external_state_if_needed)
        self._runtime_control_timer.timeout.connect(self._process_runtime_control_commands)
        self._greenwich_time_signal_timer.timeout.connect(self._on_greenwich_time_signal_timer)
        self._configuration_action.triggered.connect(self._open_configuration_dialog)
        self._toggle_logs_action.toggled.connect(self._on_logs_visibility_toggled)
        self._export_logs_action.triggered.connect(self._export_logs)
        self._cron_help_action.triggered.connect(self._show_cron_help)
        # Sync fullscreen button with video widget state
        try:
            self._video_widget.fullScreenChanged.connect(self._on_video_fullscreen_changed)
        except Exception:
            # Some platforms/versions may differ; ignore if not present
            pass
        # Install event filters so Escape key will reliably exit fullscreen
        self.installEventFilter(self)
        try:
            self._video_widget.installEventFilter(self)
        except Exception:
            pass
        try:
            self._waveform_widget.installEventFilter(self)
        except Exception:
            pass
        try:
            self._fullscreen_overlay.installEventFilter(self)
        except Exception:
            pass

    def eventFilter(self, obj: object, event: object) -> bool:
        # Catch keyboard/mouse shortcuts for fullscreen handling.
        try:
            if isinstance(event, QEvent) and event.type() == QEvent.KeyPress:
                from PySide6.QtGui import QKeyEvent

                if isinstance(event, QKeyEvent) and event.key() == Qt.Key_Escape:
                    self._ensure_exit_fullscreen()
                    return True
            if (
                obj in (self._video_widget, self._waveform_widget, self._fullscreen_overlay)
                and isinstance(event, QEvent)
                and event.type() == QEvent.MouseButtonDblClick
            ):
                self._toggle_fullscreen()
                return True
        except Exception:
            pass
        return super().eventFilter(obj, event)

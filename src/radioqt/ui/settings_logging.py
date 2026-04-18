from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox

from ..runtime_logs import append_runtime_log_line, format_runtime_log_line
from ..storage.schedule_export import export_schedule_range
from ..stream_relay import (
    build_icecast_ffmpeg_command,
    IcecastFfmpegConfig,
    sync_icecast_command_with_generated,
)
from ..ui_components import ConfigurationDialog, CronHelpDialog


class MainWindowSettingsLoggingMixin:
    @Slot(str)
    def _append_log(self, message: str) -> None:
        line = format_runtime_log_line(message, timestamp=datetime.now().astimezone())
        self._log_view.appendPlainText(line)
        try:
            append_runtime_log_line(self._config_dir, line)
        except OSError:
            # Runtime log persistence is best-effort and should not block UI updates.
            pass

    @Slot(bool)
    def _set_logs_visible(self, visible: bool) -> None:
        self._logs_group.setVisible(bool(visible))

    @Slot(bool)
    def _on_logs_visibility_toggled(self, checked: bool) -> None:
        self._logs_visible = bool(checked)
        self._set_logs_visible(self._logs_visible)
        self._save_state()

    @Slot()
    def _export_logs(self) -> None:
        default_name = f"radioqt-log-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}.log"
        target_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Logs",
            str(Path.cwd() / default_name),
            "Log Files (*.log);;Text Files (*.txt);;All Files (*)",
        )
        if not target_path:
            return

        try:
            Path(target_path).write_text(self._log_view.toPlainText(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Export Failed", f"Could not export logs:\n{exc}")
            return

        self._append_log(f"Exported logs to {target_path}")

    @Slot()
    def _export_schedule_range(self) -> None:
        if self._schedule_entries:
            schedule_days = sorted({entry.start_at.astimezone().date() for entry in self._schedule_entries})
            default_start_date = schedule_days[0]
            default_end_date = schedule_days[-1]
        else:
            today = datetime.now().astimezone().date()
            default_start_date = today
            default_end_date = today

        from_text, from_ok = QInputDialog.getText(
            self,
            "Export Schedule (From)",
            "Start date (YYYY-MM-DD):",
            text=default_start_date.isoformat(),
        )
        if not from_ok:
            return
        to_text, to_ok = QInputDialog.getText(
            self,
            "Export Schedule (To)",
            "End date (YYYY-MM-DD):",
            text=default_end_date.isoformat(),
        )
        if not to_ok:
            return

        try:
            start_date = date.fromisoformat(from_text.strip())
            end_date = date.fromisoformat(to_text.strip())
        except ValueError:
            QMessageBox.warning(
                self,
                "Invalid Date",
                "Use YYYY-MM-DD format for both start and end date.",
            )
            return
        if end_date < start_date:
            QMessageBox.warning(
                self,
                "Invalid Range",
                "End date cannot be before start date.",
            )
            return

        state_snapshot = self._build_app_state_snapshot()
        try:
            result = export_schedule_range(
                self._config_dir,
                state=state_snapshot,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Export Failed", f"Could not export schedule JSON:\n{exc}")
            return

        export_root = self._config_dir / "export"
        self._append_log(
            (
                f"Exported schedule JSON range {start_date.isoformat()}..{end_date.isoformat()} "
                f"(updated={result.updated_count}, removed={result.removed_count}) to {export_root}"
            )
        )
        QMessageBox.information(
            self,
            "Export Complete",
            (
                f"Schedule export completed.\n\n"
                f"Range: {start_date.isoformat()} to {end_date.isoformat()}\n"
                f"Updated files: {result.updated_count}\n"
                f"Removed files: {result.removed_count}\n"
                f"Unchanged files: {result.unchanged_count}\n"
                f"Path: {export_root}"
            ),
        )

    @Slot()
    def _show_cron_help(self) -> None:
        dialog = CronHelpDialog(self)
        dialog.exec()

    @staticmethod
    def _generated_icecast_command(
        *,
        input_format: str,
        thread_queue_size: int,
        device: str,
        audio_channels: int,
        audio_rate: int,
        audio_codec: str,
        audio_bitrate: int,
        content_type: str,
        output_format: str,
        icecast_url: str,
    ) -> str:
        return build_icecast_ffmpeg_command(
            IcecastFfmpegConfig(
                input_format=input_format,
                thread_queue_size=thread_queue_size,
                device=device,
                audio_channels=audio_channels,
                audio_rate=audio_rate,
                audio_codec=audio_codec,
                audio_bitrate=audio_bitrate,
                content_type=content_type,
                output_format=output_format,
                icecast_url=icecast_url,
            )
        )

    @Slot()
    def _open_configuration_dialog(self) -> None:
        previous_generated_command = self._generated_icecast_command(
            input_format=self._icecast_input_format,
            thread_queue_size=self._icecast_thread_queue_size,
            device=self._icecast_device,
            audio_channels=self._icecast_audio_channels,
            audio_rate=self._icecast_audio_rate,
            audio_codec=self._icecast_audio_codec,
            audio_bitrate=self._icecast_audio_bitrate,
            content_type=self._icecast_content_type,
            output_format=self._icecast_output_format,
            icecast_url=self._icecast_url,
        )
        dialog = ConfigurationDialog(
            self,
            fade_in_duration_seconds=self._fade_in_duration_seconds,
            fade_out_duration_seconds=self._fade_out_duration_seconds,
            filesystem_default_fade_in=self._filesystem_default_fade_in,
            filesystem_default_fade_out=self._filesystem_default_fade_out,
            streams_default_fade_in=self._streams_default_fade_in,
            streams_default_fade_out=self._streams_default_fade_out,
            media_library_width_percent=self._media_library_width_percent,
            schedule_width_percent=self._schedule_width_percent,
            font_size_points=self._font_size_points,
            greenwich_time_signal_enabled=self._greenwich_time_signal_enabled,
            greenwich_time_signal_path=self._greenwich_time_signal_path,
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
            library_tabs=self._library_tab_configs,
            supported_extensions=self._supported_extensions,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        next_shared_fade_duration_seconds = max(1, dialog.fade_duration_seconds())
        next_filesystem_default_fade_in = bool(dialog.filesystem_default_fade_in())
        next_filesystem_default_fade_out = bool(dialog.filesystem_default_fade_out())
        next_streams_default_fade_in = bool(dialog.streams_default_fade_in())
        next_streams_default_fade_out = bool(dialog.streams_default_fade_out())
        next_media_library_width_percent = max(10, min(90, dialog.media_library_width_percent()))
        next_schedule_width_percent = 100 - next_media_library_width_percent
        next_font_size_points = max(1, dialog.font_size_points())
        next_greenwich_time_signal_enabled = bool(dialog.greenwich_time_signal_enabled())
        next_greenwich_time_signal_path = dialog.greenwich_time_signal_path()
        next_icecast_status = bool(dialog.icecast_status())
        next_icecast_run_in_background = bool(dialog.icecast_run_in_background())
        next_icecast_command = dialog.icecast_command()
        next_icecast_input_format = dialog.icecast_input_format()
        next_icecast_thread_queue_size = dialog.icecast_thread_queue_size()
        next_icecast_device = dialog.icecast_device()
        next_icecast_audio_channels = dialog.icecast_audio_channels()
        next_icecast_audio_rate = dialog.icecast_audio_rate()
        next_icecast_audio_codec = dialog.icecast_audio_codec()
        next_icecast_audio_bitrate = dialog.icecast_audio_bitrate()
        next_icecast_content_type = dialog.icecast_content_type()
        next_icecast_output_format = dialog.icecast_output_format()
        next_icecast_url = dialog.icecast_url()
        next_generated_command = self._generated_icecast_command(
            input_format=next_icecast_input_format,
            thread_queue_size=next_icecast_thread_queue_size,
            device=next_icecast_device,
            audio_channels=next_icecast_audio_channels,
            audio_rate=next_icecast_audio_rate,
            audio_codec=next_icecast_audio_codec,
            audio_bitrate=next_icecast_audio_bitrate,
            content_type=next_icecast_content_type,
            output_format=next_icecast_output_format,
            icecast_url=next_icecast_url,
        )
        synchronized_icecast_command = sync_icecast_command_with_generated(
            current_command=next_icecast_command,
            previous_generated_command=previous_generated_command,
            next_generated_command=next_generated_command,
        )
        next_library_tabs = dialog.library_tabs()
        next_supported_extensions = self._normalize_supported_extensions(dialog.supported_extensions())
        fade_changed = not (
            next_shared_fade_duration_seconds == self._fade_in_duration_seconds
            and next_shared_fade_duration_seconds == self._fade_out_duration_seconds
            and next_filesystem_default_fade_in == self._filesystem_default_fade_in
            and next_filesystem_default_fade_out == self._filesystem_default_fade_out
            and next_streams_default_fade_in == self._streams_default_fade_in
            and next_streams_default_fade_out == self._streams_default_fade_out
        )
        font_size_changed = next_font_size_points != self._font_size_points
        panel_width_changed = not (
            next_media_library_width_percent == self._media_library_width_percent
            and next_schedule_width_percent == self._schedule_width_percent
        )
        greenwich_time_signal_changed = (
            next_greenwich_time_signal_enabled != self._greenwich_time_signal_enabled
            or next_greenwich_time_signal_path != self._greenwich_time_signal_path
        )
        icecast_changed = (
            next_icecast_status != self._icecast_status
            or next_icecast_run_in_background != self._icecast_run_in_background
            or synchronized_icecast_command != self._icecast_command
            or next_icecast_input_format != self._icecast_input_format
            or next_icecast_thread_queue_size != self._icecast_thread_queue_size
            or next_icecast_device != self._icecast_device
            or next_icecast_audio_channels != self._icecast_audio_channels
            or next_icecast_audio_rate != self._icecast_audio_rate
            or next_icecast_audio_codec != self._icecast_audio_codec
            or next_icecast_audio_bitrate != self._icecast_audio_bitrate
            or next_icecast_content_type != self._icecast_content_type
            or next_icecast_output_format != self._icecast_output_format
            or next_icecast_url != self._icecast_url
        )
        library_tabs_changed = next_library_tabs != self._library_tab_configs
        supported_extensions_changed = next_supported_extensions != self._supported_extensions

        if (
            not fade_changed
            and not font_size_changed
            and not panel_width_changed
            and not greenwich_time_signal_changed
            and not icecast_changed
            and not library_tabs_changed
            and not supported_extensions_changed
        ):
            return

        if fade_changed:
            self._fade_in_duration_seconds = next_shared_fade_duration_seconds
            self._fade_out_duration_seconds = next_shared_fade_duration_seconds
            self._filesystem_default_fade_in = next_filesystem_default_fade_in
            self._filesystem_default_fade_out = next_filesystem_default_fade_out
            self._streams_default_fade_in = next_streams_default_fade_in
            self._streams_default_fade_out = next_streams_default_fade_out
        if font_size_changed:
            self._apply_global_font_size(next_font_size_points)
        if panel_width_changed:
            self._apply_panel_width_split(next_media_library_width_percent)
        if greenwich_time_signal_changed:
            self._greenwich_time_signal_enabled = next_greenwich_time_signal_enabled
            self._greenwich_time_signal_path = next_greenwich_time_signal_path
        if icecast_changed:
            self._icecast_status = next_icecast_status
            self._icecast_run_in_background = next_icecast_run_in_background
            self._icecast_command = synchronized_icecast_command
            self._icecast_input_format = next_icecast_input_format
            self._icecast_thread_queue_size = next_icecast_thread_queue_size
            self._icecast_device = next_icecast_device
            self._icecast_audio_channels = next_icecast_audio_channels
            self._icecast_audio_rate = next_icecast_audio_rate
            self._icecast_audio_codec = next_icecast_audio_codec
            self._icecast_audio_bitrate = next_icecast_audio_bitrate
            self._icecast_content_type = next_icecast_content_type
            self._icecast_output_format = next_icecast_output_format
            self._icecast_url = next_icecast_url
        if supported_extensions_changed:
            self._supported_extensions = next_supported_extensions
            self._apply_supported_extensions_to_filesystem_models()
        if library_tabs_changed:
            self._library_tab_configs = next_library_tabs
            self._rebuild_custom_library_tabs()
        self._save_settings()
        self._append_log(
            f"Updated settings: fade in={self._fade_in_duration_seconds}s, "
            f"fade out={self._fade_out_duration_seconds}s, "
            f"filesystem_fade_in={'True' if self._filesystem_default_fade_in else 'False'}, "
            f"filesystem_fade_out={'True' if self._filesystem_default_fade_out else 'False'}, "
            f"streams_fade_in={'True' if self._streams_default_fade_in else 'False'}, "
            f"streams_fade_out={'True' if self._streams_default_fade_out else 'False'}, "
            f"greenwich_time_signal={'True' if self._greenwich_time_signal_enabled else 'False'}, "
            f"icecast_status={'True' if self._icecast_status else 'False'}, "
            f"icecast_run_in_background={'True' if self._icecast_run_in_background else 'False'}, "
            f"icecast_command={'set' if self._icecast_command else 'empty'}, "
            f"icecast_device={self._icecast_device}, "
            f"icecast_audio={self._icecast_audio_codec}/{self._icecast_audio_bitrate}k/{self._icecast_audio_rate}Hz, "
            f"media_library_width={self._media_library_width_percent}%, "
            f"schedule_width={self._schedule_width_percent}%, "
            f"font={self._font_size_points}pt, "
            f"custom library tabs={len(self._library_tab_configs)}, "
            f"extensions={','.join(self._supported_extensions)}"
        )
        if icecast_changed:
            self._synchronize_icecast_runtime(reason="settings-update")

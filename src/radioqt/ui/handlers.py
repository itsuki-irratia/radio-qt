from __future__ import annotations

from concurrent.futures import Future
import json
import os
from datetime import datetime
from pathlib import Path
import subprocess
import tempfile
from typing import Callable

from PySide6.QtCore import QDate, QModelIndex, Qt, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from ..library import (
    add_stream_media_item,
    is_stream_source,
    local_media_path_from_source,
    remove_media_from_library,
    selected_url_media_id,
    update_stream_greenwich_time_signal,
    update_stream_media_item,
)
from ..models import SCHEDULE_STATUS_MISSED
from ..playback import enqueue_manual_media, resolve_media_by_id
from ..scheduling import (
    create_cron_entry,
    create_schedule_entry,
    remove_cron_and_generated_schedule_entries,
    remove_schedule_entries_by_ids,
    schedule_entry_at_exact_start,
    select_schedule_entries_for_removal,
    update_cron_enabled,
    update_cron_expression,
    update_cron_fade_in,
    update_cron_fade_out,
    update_schedule_fade_in,
    update_schedule_fade_out,
    update_schedule_status,
)
from ..storage.schedule_export import export_schedule_day_keys
from ..ui_components import CronDialog, ScheduleDialog


class MainWindowHandlersMixin:
    _LOCAL_FILE_METADATA_FIELDS: tuple[tuple[str, str], ...] = (
        ("title", "Title"),
        ("artist", "Artist"),
        ("album", "Album"),
        ("copyright", "Copyright"),
        ("genre", "Genre"),
        ("date", "Date/Year"),
        ("track", "Track"),
        ("comment", "Comment"),
    )
    _LOCAL_FILE_METADATA_READ_TAG_ALIASES: dict[str, tuple[str, ...]] = {
        "title": ("title",),
        "artist": ("artist", "album_artist", "albumartist"),
        "album": ("album",),
        "copyright": ("copyright",),
        "genre": ("genre",),
        "date": ("date", "year", "creation_time"),
        "track": ("track", "tracknumber", "track_number"),
        "comment": ("comment", "description"),
    }
    _LOCAL_FILE_METADATA_WRITE_TAG_ALIASES: dict[str, tuple[str, ...]] = {
        "title": ("title",),
        "artist": ("artist", "album_artist", "albumartist"),
        "album": ("album",),
        "copyright": ("copyright",),
        "genre": ("genre",),
        "date": ("date", "year"),
        "track": ("track", "tracknumber", "track_number"),
        "comment": ("comment", "description"),
    }

    @staticmethod
    def _local_media_path_if_file(source: str) -> Path | None:
        path = local_media_path_from_source(source)
        if path is None:
            return None
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            resolved = path.expanduser()
        return resolved if resolved.is_file() else None

    @staticmethod
    def _ffprobe_metadata(path: Path) -> dict[str, object] | None:
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=3.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        try:
            payload = json.loads(result.stdout or "")
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _schedule_entry_metadata_payload(self, entry_id: str) -> dict[str, object] | None:
        entry = self._schedule_entry_by_id(entry_id)
        if entry is None:
            return None
        media = self._media_items.get(entry.media_id)
        payload: dict[str, object] = {
            "schedule_entry": {
                "id": entry.id,
                "start_at": entry.start_at.astimezone().isoformat(),
                "status": entry.status,
                "cron_id": entry.cron_id,
                "fade_in": bool(entry.fade_in),
                "fade_out": bool(entry.fade_out),
                "one_shot": bool(entry.one_shot),
                "duration_seconds": entry.duration,
            },
        }
        if media is None:
            payload["media"] = {"missing": True, "media_id": entry.media_id}
            return payload

        payload["media"] = {
            "id": media.id,
            "title": media.title,
            "source": media.source,
            "greenwich_time_signal_enabled": bool(media.greenwich_time_signal_enabled),
            "created_at": media.created_at.astimezone().isoformat(),
            "is_stream_source": bool(is_stream_source(media.source)),
        }

        local_file_path = self._local_media_path_if_file(media.source)
        if local_file_path is None:
            payload["local_file"] = {"available": False}
            return payload

        try:
            stat = local_file_path.stat()
            file_size = int(stat.st_size)
            modified_at = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat()
        except OSError:
            file_size = None
            modified_at = None
        payload["local_file"] = {
            "available": True,
            "path": str(local_file_path),
            "size_bytes": file_size,
            "modified_at": modified_at,
        }
        ffprobe_payload = self._ffprobe_metadata(local_file_path)
        if ffprobe_payload is not None:
            payload["ffprobe"] = ffprobe_payload
        return payload

    def _show_metadata_viewer(self, title: str, metadata: dict[str, object]) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(860, 560)
        viewer = QPlainTextEdit(dialog)
        viewer.setReadOnly(True)
        viewer.setPlainText(json.dumps(metadata, indent=2, ensure_ascii=False, default=str))
        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout = QVBoxLayout(dialog)
        layout.addWidget(viewer, 1)
        layout.addWidget(buttons)
        dialog.exec()

    def _show_selected_schedule_metadata(self) -> None:
        entry_id = self._selected_schedule_entry_id()
        if entry_id is None:
            QMessageBox.information(self, "No Selection", "Select a schedule row first.")
            return
        payload = self._schedule_entry_metadata_payload(entry_id)
        if payload is None:
            QMessageBox.warning(self, "Missing Entry", "Could not load metadata for this entry.")
            return
        self._show_metadata_viewer("Schedule Entry Metadata", payload)

    def _export_schedule_days_for_media(self, media_id: str) -> None:
        target_day_keys = {
            entry.start_at.astimezone().date().isoformat()
            for entry in self._schedule_entries
            if entry.media_id == media_id
        }
        if not target_day_keys or self._shutting_down:
            return
        state_snapshot = self._build_app_state_snapshot()
        requested_day_keys = set(target_day_keys)
        future = self._schedule_export_executor.submit(
            export_schedule_day_keys,
            self._config_dir,
            state=state_snapshot,
            day_keys=requested_day_keys,
        )
        future.add_done_callback(
            lambda task, keys=requested_day_keys: self._emit_schedule_export_days_result(
                keys,
                task,
            )
        )

    def _emit_schedule_export_days_result(
        self,
        requested_day_keys: set[str],
        task: Future[object],
    ) -> None:
        if self._shutting_down:
            return
        updated_count = 0
        removed_count = 0
        error_message = ""
        try:
            result = task.result()
            updated_count = int(getattr(result, "updated_count", 0) or 0)
            removed_count = int(getattr(result, "removed_count", 0) or 0)
        except Exception as exc:
            error_message = str(exc)
        try:
            self._schedule_export_dispatcher.export_finished.emit(
                sorted(requested_day_keys),
                updated_count,
                removed_count,
                error_message,
            )
        except RuntimeError:
            return

    @Slot(object, int, int, str)
    def _on_schedule_export_days_finished(
        self,
        requested_day_keys: object,
        updated_count: int,
        removed_count: int,
        error_message: str,
    ) -> None:
        if self._shutting_down:
            return
        if error_message:
            self._append_log(f"Schedule export failed after metadata edit: {error_message}")
            return
        if updated_count or removed_count:
            days = (
                requested_day_keys
                if isinstance(requested_day_keys, list)
                else []
            )
            day_summary = ", ".join(days[:3])
            if len(days) > 3:
                day_summary = f"{day_summary} (+{len(days) - 3} more)"
            self._append_log(
                (
                    "Schedule export refreshed affected days after metadata edit: "
                    f"updated={updated_count}, removed={removed_count}, days=[{day_summary}]"
                )
            )

    @classmethod
    def _editable_local_file_tags_from_ffprobe(
        cls, ffprobe_payload: dict[str, object] | None
    ) -> dict[str, str]:
        candidates: list[dict[str, str]] = []
        if ffprobe_payload is not None:
            format_payload = ffprobe_payload.get("format")
            if isinstance(format_payload, dict):
                candidates.append(cls._normalize_ffprobe_tags(format_payload.get("tags")))
            streams_payload = ffprobe_payload.get("streams")
            if isinstance(streams_payload, list):
                for stream_payload in streams_payload:
                    if not isinstance(stream_payload, dict):
                        continue
                    candidates.append(cls._normalize_ffprobe_tags(stream_payload.get("tags")))

        extracted: dict[str, str] = {}
        for key, _ in cls._LOCAL_FILE_METADATA_FIELDS:
            extracted[key] = cls._first_tag_match(
                key,
                candidates,
            )
        return extracted

    @classmethod
    def _first_tag_match(cls, key: str, candidates: list[dict[str, str]]) -> str:
        aliases = cls._LOCAL_FILE_METADATA_READ_TAG_ALIASES.get(key, (key,))
        for candidate in candidates:
            for alias in aliases:
                value = candidate.get(alias)
                if value:
                    return value
        return ""

    @staticmethod
    def _normalize_ffprobe_tags(raw_tags: object) -> dict[str, str]:
        if not isinstance(raw_tags, dict):
            return {}
        normalized: dict[str, str] = {}
        for raw_key, raw_value in raw_tags.items():
            key = str(raw_key).strip().lower()
            if not key:
                continue
            value = "" if raw_value is None else str(raw_value).strip()
            if not value:
                continue
            normalized[key] = value
        return normalized

    def _show_local_file_metadata_editor(
        self,
        *,
        initial_file_tags: dict[str, str],
        on_submit: Callable[[dict[str, str]], tuple[bool, str | None]],
    ) -> dict[str, str] | None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Edit Local Metadata")
        dialog.resize(560, 360)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()

        tag_inputs: dict[str, QLineEdit] = {}
        for key, label in self._LOCAL_FILE_METADATA_FIELDS:
            line_edit = QLineEdit(dialog)
            line_edit.setText(initial_file_tags.get(key, ""))
            form.addRow(f"{label}:", line_edit)
            tag_inputs[key] = line_edit

        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        submitted_values: dict[str, dict[str, str]] = {}

        def _attempt_submit() -> None:
            updated_file_tags = {
                key: input_box.text().strip() for key, input_box in tag_inputs.items()
            }
            buttons.setEnabled(False)
            dialog.setCursor(Qt.WaitCursor)
            QApplication.processEvents()
            try:
                ok, error_message = on_submit(updated_file_tags)
            finally:
                dialog.unsetCursor()
                buttons.setEnabled(True)
            if not ok:
                QMessageBox.warning(
                    dialog,
                    "Metadata Update Failed",
                    error_message or "Could not save metadata.",
                )
                return
            submitted_values["value"] = updated_file_tags
            dialog.accept()

        buttons.accepted.connect(_attempt_submit)

        if dialog.exec() != QDialog.Accepted:
            return None

        return submitted_values.get("value")

    @staticmethod
    def _write_local_file_metadata(path: Path, tags: dict[str, str]) -> tuple[bool, str | None]:
        descriptor, temporary_path_raw = tempfile.mkstemp(
            prefix=f".{path.stem}.radioqt-meta-",
            suffix=path.suffix,
            dir=str(path.parent),
        )
        os.close(descriptor)
        temporary_path = Path(temporary_path_raw)
        command = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0",
            "-c",
            "copy",
            "-map_metadata",
            "0",
        ]
        expanded_tag_items: list[tuple[str, str]] = []
        seen_aliases: set[str] = set()
        for key, value in tags.items():
            aliases = MainWindowHandlersMixin._LOCAL_FILE_METADATA_WRITE_TAG_ALIASES.get(
                key, (key,)
            )
            for alias in aliases:
                normalized_alias = str(alias).strip().lower()
                if not normalized_alias or normalized_alias in seen_aliases:
                    continue
                seen_aliases.add(normalized_alias)
                expanded_tag_items.append((normalized_alias, value))
        for key, value in expanded_tag_items:
            command.extend(["-metadata", f"{key}={value}"])
        command.append(str(temporary_path))
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=30.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            temporary_path.unlink(missing_ok=True)
            return False, str(exc)
        if result.returncode != 0:
            temporary_path.unlink(missing_ok=True)
            detail = (result.stderr or result.stdout or "ffmpeg metadata update failed").strip()
            return False, detail.splitlines()[-1] if detail else "Unknown ffmpeg error"
        try:
            os.replace(temporary_path, path)
        except OSError as exc:
            temporary_path.unlink(missing_ok=True)
            return False, str(exc)
        return True, None

    def _edit_selected_schedule_local_metadata(self) -> None:
        entry_id = self._selected_schedule_entry_id()
        if entry_id is None:
            QMessageBox.information(self, "No Selection", "Select a schedule row first.")
            return
        entry = self._schedule_entry_by_id(entry_id)
        if entry is None:
            QMessageBox.warning(self, "Missing Entry", "Could not find schedule entry.")
            return
        media = self._media_items.get(entry.media_id)
        if media is None:
            QMessageBox.warning(self, "Missing Media", "Could not find media for this entry.")
            return
        local_file_path = self._local_media_path_if_file(media.source)
        if local_file_path is None:
            QMessageBox.information(
                self,
                "Read-only for Streams",
                "Editing metadata from schedule is only available for local files.",
            )
            return
        ffprobe_payload = self._ffprobe_metadata(local_file_path)
        initial_file_tags = self._editable_local_file_tags_from_ffprobe(ffprobe_payload)

        def _submit_local_metadata(
            updated_file_tags: dict[str, str],
        ) -> tuple[bool, str | None]:
            file_metadata_changed = updated_file_tags != initial_file_tags
            if not file_metadata_changed:
                return True, None

            if file_metadata_changed:
                succeeded, error_message = self._write_local_file_metadata(
                    local_file_path, updated_file_tags
                )
                if not succeeded:
                    return False, error_message or "Could not update embedded file metadata."

            self._refresh_urls_list()
            self._refresh_cron_table()
            self._refresh_schedule_table()
            self._export_schedule_days_for_media(media.id)
            self._append_log(f"Updated embedded local file metadata for {local_file_path}")
            return True, None

        self._show_local_file_metadata_editor(
            initial_file_tags=initial_file_tags,
            on_submit=_submit_local_metadata,
        )

    def _disable_schedule_auto_focus(self, *, reason: str) -> None:
        if not self._schedule_auto_focus_enabled:
            return
        self._schedule_auto_focus_enabled = False
        self._schedule_focus_checkbox.blockSignals(True)
        self._schedule_focus_checkbox.setChecked(False)
        self._schedule_focus_checkbox.blockSignals(False)
        self._save_state()
        self._append_log(reason)

    @Slot(QDate)
    def _on_schedule_filter_date_changed(self, selected_date: QDate) -> None:
        self._disable_schedule_auto_focus(
            reason="Focus current program disabled by manual date change"
        )
        self._schedule_filter_date = selected_date.toPython()
        self._resync_schedule_runtime(refresh_table=True)

    @Slot(bool)
    def _on_schedule_auto_focus_toggled(self, checked: bool) -> None:
        self._schedule_auto_focus_enabled = checked
        self._save_state()
        if checked:
            self._apply_schedule_auto_focus(force=True)

    @Slot(int, int)
    def _on_schedule_table_cell_pressed(self, row: int, column: int) -> None:
        del column
        if row < 0:
            return
        self._disable_schedule_auto_focus(
            reason="Focus current program disabled by manual timeline selection"
        )

    @Slot(QModelIndex)
    def _on_filesystem_selected(self, _: QModelIndex) -> None:
        self._last_source_panel = "filesystem"

    @Slot()
    def _on_urls_selection_changed(self) -> None:
        if self._urls_table.currentRow() >= 0:
            self._last_source_panel = "urls"

    @Slot(int)
    def _on_library_tab_changed(self, index: int) -> None:
        tab_widget = self._library_tabs.widget(index) if index >= 0 else None
        if tab_widget is None:
            self._last_source_panel = "filesystem"
            return
        panel_kind, _, _ = self._library_tab_sources.get(tab_widget, ("filesystem", None, None))
        self._last_source_panel = "urls" if panel_kind == "urls" else "filesystem"

    def _default_fade_flags_for_media(self, media_id: str) -> tuple[bool, bool]:
        panel_kind, _, _ = self._current_library_tab_descriptor()
        if panel_kind == "urls":
            return bool(self._streams_default_fade_in), bool(self._streams_default_fade_out)
        media = self._media_items.get(media_id)
        if media is not None and is_stream_source(media.source):
            return bool(self._streams_default_fade_in), bool(self._streams_default_fade_out)
        return bool(self._filesystem_default_fade_in), bool(self._filesystem_default_fade_out)

    @Slot()
    def _add_media_url(self) -> None:
        url, ok = QInputDialog.getText(self, "Add Stream URL", "URL (http/https/rtsp/etc):")
        if not ok or not url.strip():
            return

        title, ok_title = QInputDialog.getText(self, "Display Name", "Title:", text=url.strip())
        if not ok_title or not title.strip():
            title = url.strip()

        media = add_stream_media_item(
            self._media_items,
            self._media_duration_cache,
            title,
            url,
        )
        self._refresh_urls_list()
        self._save_state()
        self._append_log(f"Added stream: {title.strip()}")

    def _remove_media_by_id(self, media_id: str) -> None:
        result = remove_media_from_library(
            self._media_items,
            self._media_duration_cache,
            self._cron_entries,
            self._schedule_entries,
            self._play_queue,
            media_id,
        )
        if result.removed_media is None:
            return

        self._cron_entries = result.cron_entries
        self._schedule_entries = result.schedule_entries
        self._play_queue = result.play_queue
        self._media_duration_pending.discard(media_id)
        if self._player.current_media is not None and self._player.current_media.id == media_id:
            self._player.clear_current_media()
            self._now_playing_label.setText("None")
            self._update_player_visual_state()

        self._resync_schedule_runtime()
        self._refresh_urls_list()
        self._refresh_cron_table()
        self._refresh_schedule_table()
        self._save_state()
        self._append_log(f"Removed media: {result.removed_media.title}")

    @Slot()
    def _remove_selected_url(self) -> None:
        media_id = selected_url_media_id(self._urls_table)
        if media_id is None:
            QMessageBox.information(self, "No Selection", "Select a URL first.")
            return
        media = self._media_items.get(media_id)
        title = media.title if media else "this stream"
        result = QMessageBox.question(
            self,
            "Confirm Removal",
            f"Are you sure you want to remove '{title}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if result != QMessageBox.Yes:
            return
        self._remove_media_by_id(media_id)

    @Slot()
    def _edit_selected_url(self) -> None:
        media_id = selected_url_media_id(self._urls_table)
        if media_id is None:
            QMessageBox.information(self, "No Selection", "Select a URL first.")
            return

        media = self._media_items.get(media_id)
        if media is None:
            return

        updated_url, ok = QInputDialog.getText(
            self,
            "Edit Stream URL",
            "URL (http/https/rtsp/etc):",
            text=media.source,
        )
        if not ok or not updated_url.strip():
            return

        updated_title, ok_title = QInputDialog.getText(
            self,
            "Edit Display Name",
            "Title:",
            text=media.title,
        )
        if not ok_title or not updated_title.strip():
            updated_title = updated_url.strip()

        media = update_stream_media_item(
            self._media_items,
            self._media_duration_cache,
            media_id,
            updated_title,
            updated_url,
        )
        if media is None:
            return
        self._refresh_urls_list()
        self._refresh_schedule_table()
        self._save_state()
        self._append_log(f"Updated stream: {media.title}")

    def _on_stream_greenwich_time_signal_changed(self, media_id: str, value: str) -> None:
        enabled = value == "True"
        media = update_stream_greenwich_time_signal(
            self._media_items,
            media_id,
            enabled=enabled,
        )
        if media is None:
            return
        self._save_state()
        self._append_log(
            f"Set Greenwich Time Signal for stream '{media.title}' to {value}"
        )

    @Slot()
    def _remove_selected_media(self) -> None:
        media_id = self._selected_media_id()
        if media_id is None:
            QMessageBox.information(self, "No Selection", "Select a media item first.")
            return
        self._remove_media_by_id(media_id)

    @Slot()
    def _play_selected_media(self) -> None:
        media_id = self._selected_media_id()
        if media_id is None:
            QMessageBox.information(self, "No Selection", "Select a media item first.")
            return
        media = resolve_media_by_id(self._media_items, media_id)
        if media is None:
            return
        self._set_pending_schedule_start_entry_id(None)
        self._player.play_media(media)

    @Slot()
    def _queue_selected_media(self) -> None:
        media_id = self._selected_media_id()
        if media_id is None:
            QMessageBox.information(self, "No Selection", "Select a media item first.")
            return
        media = resolve_media_by_id(self._media_items, media_id)
        if media is None:
            return
        enqueue_manual_media(self._play_queue, media_id)
        self._save_state()
        self._append_log(
            f"Queued media '{media.title}' ({len(self._play_queue)} item(s) pending)"
        )

    @Slot()
    def _add_schedule_entry(self) -> None:
        media_id = self._selected_media_id()
        if media_id is None:
            QMessageBox.information(self, "No Selection", "Select a media item from library first.")
            return

        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_schedule_durations()
        default_fade_in, default_fade_out = self._default_fade_flags_for_media(media_id)
        dialog = ScheduleDialog(self, initial_start_at=self._default_next_schedule_start())
        if dialog.exec() != QDialog.Accepted:
            return

        selected_start_at = dialog.selected_datetime()
        reference_time = datetime.now().astimezone()
        conflicting_entry = schedule_entry_at_exact_start(
            self._schedule_entries,
            selected_start_at,
            reference_time,
        )
        if conflicting_entry is not None:
            QMessageBox.warning(
                self,
                "Schedule Conflict",
                (
                    "There is already a schedule entry at "
                    f"{conflicting_entry.start_at.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}."
                ),
            )
            return

        entry = create_schedule_entry(
            media_id=media_id,
            start_at=selected_start_at,
            reference_time=reference_time,
            fade_in=default_fade_in,
            fade_out=default_fade_out,
        )
        self._schedule_entries.append(entry)

        self._recalculate_and_apply_schedule_entries()
        self._set_schedule_filter_date(self._normalized_start(entry.start_at).date())
        self._refresh_schedule_table()
        self._save_state()
        media_name = self._media_log_name(entry.media_id)
        if entry.status == SCHEDULE_STATUS_MISSED:
            self._append_log(
                f"Scheduled media '{media_name}' in the past; entry was marked as missed"
            )
        self._append_log(
            f"Scheduled media '{media_name}' for {entry.start_at.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}"
        )

    @Slot()
    def _remove_schedule_entry(self) -> None:
        entry_ids = self._selected_schedule_entry_ids()
        if not entry_ids:
            QMessageBox.information(self, "No Selection", "Select a schedule row first.")
            return

        entry_ids_set = set(entry_ids)
        selection = select_schedule_entries_for_removal(
            self._schedule_entries,
            entry_ids=entry_ids_set,
            is_protected=self._is_schedule_entry_protected_from_removal,
        )
        entries_to_remove = selection.entries_to_remove
        if not entries_to_remove:
            return
        if selection.protected_entries:
            QMessageBox.information(
                self,
                "CRON-managed Entries",
                "Active CRON-generated rows cannot be removed from Date Time. Disable the CRON rule first if you want to remove them here.",
            )
            return

        if len(entries_to_remove) == 1:
            entry = entries_to_remove[0]
            media_name = self._media_log_name(entry.media_id)
            start_label = entry.start_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            message = f"Are you sure you want to remove the schedule entry for '{media_name}' at {start_label}?"
        else:
            lines = []
            for entry in sorted(entries_to_remove, key=lambda e: e.start_at):
                media_name = self._media_log_name(entry.media_id)
                start_label = entry.start_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                lines.append(f"  - '{media_name}' at {start_label}")
            message = f"Are you sure you want to remove {len(entries_to_remove)} schedule entries?\n" + "\n".join(lines)

        result = QMessageBox.question(
            self,
            "Confirm Removal",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if result != QMessageBox.Yes:
            return

        self._schedule_entries = remove_schedule_entries_by_ids(
            self._schedule_entries,
            entry_ids=entry_ids_set,
        )
        self._recalculate_and_apply_schedule_entries()
        self._refresh_schedule_table()
        self._save_state()
        if len(entries_to_remove) == 1:
            self._append_log(f"Removed schedule entry for media '{self._media_log_name(entries_to_remove[0].media_id)}'")
        else:
            self._append_log(f"Removed {len(entries_to_remove)} schedule entries")

    @Slot("QPoint")
    def _on_schedule_context_menu(self, position) -> None:
        index = self._schedule_table.indexAt(position)
        if not index.isValid():
            return
        row = index.row()
        self._schedule_table.setCurrentCell(row, 0)
        entry_item = self._schedule_table.item(row, 0)
        entry_id = entry_item.data(Qt.UserRole) if entry_item is not None else None
        entry = self._schedule_entry_by_id(entry_id) if isinstance(entry_id, str) else None
        entry_media = self._media_items.get(entry.media_id) if entry is not None else None
        can_edit_local_metadata = (
            entry is not None
            and entry_media is not None
            and self._local_media_path_if_file(entry_media.source) is not None
        )
        selected_count = len(self._selected_schedule_entry_ids())
        selected_ids = set(self._selected_schedule_entry_ids())
        has_cron_generated = any(
            entry.id in selected_ids and self._is_schedule_entry_protected_from_removal(entry)
            for entry in self._schedule_entries
        )
        menu = QMenu(self._schedule_table)
        edit_metadata_action = QAction("Edit Local Metadata", menu)
        edit_metadata_action.setEnabled(can_edit_local_metadata)
        edit_metadata_action.triggered.connect(self._edit_selected_schedule_local_metadata)
        menu.addAction(edit_metadata_action)
        menu.addSeparator()
        label = (
            "CRON-managed Entries Cannot Be Removed"
            if has_cron_generated
            else f"Remove {selected_count} Entries" if selected_count > 1 else "Remove Entry"
        )
        remove_action = QAction(label, menu)
        remove_action.setEnabled(not has_cron_generated)
        remove_action.triggered.connect(self._remove_schedule_entry)
        menu.addAction(remove_action)
        menu.exec(self._schedule_table.viewport().mapToGlobal(position))

    @Slot("QPoint")
    def _on_cron_context_menu(self, position) -> None:
        item = self._cron_table.itemAt(position)
        if item is None:
            return
        self._cron_table.selectRow(item.row())
        menu = QMenu(self._cron_table)
        edit_action = QAction("Edit CRON Entry", menu)
        edit_action.triggered.connect(self._edit_selected_cron)
        menu.addAction(edit_action)
        remove_action = QAction("Remove CRON Entry", menu)
        remove_action.triggered.connect(self._remove_selected_cron)
        menu.addAction(remove_action)
        menu.exec(self._cron_table.viewport().mapToGlobal(position))

    @Slot()
    def _add_cron_schedule(self) -> None:
        media_id = self._selected_media_id()
        if media_id is None:
            QMessageBox.information(self, "No Selection", "Select a media item from library first.")
            return

        default_fade_in, default_fade_out = self._default_fade_flags_for_media(media_id)
        dialog = CronDialog(
            self,
            initial_fade_in=default_fade_in,
            initial_fade_out=default_fade_out,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        entry = create_cron_entry(
            media_id=media_id,
            expression=dialog.expression(),
            fade_in=dialog.fade_in(),
            fade_out=dialog.fade_out(),
        )
        self._cron_entries.append(entry)
        self._sync_after_cron_rule_change(focus_entry=entry)
        self._append_log(
            f"Added CRON schedule '{entry.expression}' for media '{self._media_log_name(entry.media_id)}'"
        )

    @Slot()
    def _edit_selected_cron(self) -> None:
        cron_id = self._selected_cron_entry_id()
        if cron_id is None:
            QMessageBox.information(self, "No Selection", "Select a CRON row first.")
            return

        cron_entry = self._cron_entry_by_id(cron_id)
        if cron_entry is None:
            return

        previous_expression = cron_entry.expression

        dialog = CronDialog(
            self,
            dialog_title="Edit CRON Entry",
            initial_expression=cron_entry.expression,
            initial_fade_in=cron_entry.fade_in,
            initial_fade_out=cron_entry.fade_out,
            expression_only=True,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        if not update_cron_expression(cron_entry, expression=dialog.expression()):
            return
        self._sync_after_cron_rule_change(focus_entry=cron_entry)
        self._append_log(
            (
                f"Updated CRON schedule '{previous_expression}' -> '{cron_entry.expression}' "
                f"for media '{self._media_log_name(cron_entry.media_id)}'"
            )
        )

    def _remove_selected_cron(self) -> None:
        cron_id = self._selected_cron_entry_id()
        if cron_id is None:
            QMessageBox.information(self, "No Selection", "Select a CRON row first.")
            return

        cron_entry = self._cron_entry_by_id(cron_id)
        if cron_entry is None:
            return

        result = QMessageBox.question(
            self,
            "Confirm Removal",
            (
                f"Are you sure you want to remove the CRON rule '{cron_entry.expression}' "
                f"for '{self._media_log_name(cron_entry.media_id)}'?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if result != QMessageBox.Yes:
            return

        self._cron_entries, self._schedule_entries = remove_cron_and_generated_schedule_entries(
            self._cron_entries,
            self._schedule_entries,
            cron_id=cron_id,
        )
        self._sync_after_cron_rule_change()
        self._append_log(f"Removed CRON schedule '{cron_entry.expression}'")

    @Slot("QPoint")
    def _on_urls_context_menu(self, position) -> None:
        item = self._urls_table.itemAt(position)
        menu = QMenu(self._urls_table)
        add_action = QAction("Add Streaming...", menu)
        add_action.triggered.connect(self._add_media_url)
        menu.addAction(add_action)
        if item is None:
            menu.exec(self._urls_table.viewport().mapToGlobal(position))
            return
        self._urls_table.selectRow(item.row())
        menu.addSeparator()
        edit_action = QAction("Edit Entry", menu)
        edit_action.triggered.connect(self._edit_selected_url)
        menu.addAction(edit_action)
        remove_action = QAction("Remove URL", menu)
        remove_action.triggered.connect(self._remove_selected_url)
        menu.addAction(remove_action)
        menu.exec(self._urls_table.viewport().mapToGlobal(position))

    def _on_schedule_fade_in_changed(self, entry_id: str, value: str) -> None:
        updated_entry = update_schedule_fade_in(
            self._schedule_entries,
            entry_id,
            fade_in_enabled=value == "True",
            cron_entry_by_id=self._cron_entry_by_id,
        )
        if updated_entry is None:
            return

        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()
        self._save_state()
        state = "enabled" if updated_entry.fade_in else "disabled"
        self._append_log(
            f"Set fade in for media '{self._media_log_name(updated_entry.media_id)}' to {state}"
        )

    def _on_schedule_fade_out_changed(self, entry_id: str, value: str) -> None:
        updated_entry = update_schedule_fade_out(
            self._schedule_entries,
            entry_id,
            fade_out_enabled=value == "True",
            cron_entry_by_id=self._cron_entry_by_id,
        )
        if updated_entry is None:
            return

        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()
        self._save_state()
        state = "enabled" if updated_entry.fade_out else "disabled"
        self._append_log(
            f"Set fade out for media '{self._media_log_name(updated_entry.media_id)}' to {state}"
        )

    def _on_schedule_status_changed(self, entry_id: str, value: str) -> None:
        result = update_schedule_status(
            self._schedule_entries,
            entry_id,
            value=value,
            reference_time=datetime.now().astimezone(),
            cron_entry_by_id=self._cron_entry_by_id,
        )
        if result.refresh_only:
            self._refresh_schedule_table()
            return
        if result.updated_entry is None:
            return
        applied_value = result.applied_value or value

        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()
        self._save_state()
        self._append_log(
            f"Set status for media '{self._media_log_name(result.updated_entry.media_id)}' to {applied_value}"
        )

    def _on_cron_fade_in_changed(self, cron_id: str, value: str) -> None:
        updated_entry = update_cron_fade_in(
            self._cron_entries,
            cron_id,
            fade_in_enabled=value == "True",
        )
        if updated_entry is None:
            return

        self._resync_schedule_runtime(refresh_table=True, save_state=True)
        self._append_log(
            f"Set CRON fade in for media '{self._media_log_name(updated_entry.media_id)}' to {value}"
        )

    def _on_cron_fade_out_changed(self, cron_id: str, value: str) -> None:
        updated_entry = update_cron_fade_out(
            self._cron_entries,
            cron_id,
            fade_out_enabled=value == "True",
        )
        if updated_entry is None:
            return

        self._resync_schedule_runtime(refresh_table=True, save_state=True)
        self._append_log(
            f"Set CRON fade out for media '{self._media_log_name(updated_entry.media_id)}' to {value}"
        )

    def _on_cron_status_changed(self, cron_id: str, value: str) -> None:
        updated_entry = update_cron_enabled(
            self._cron_entries,
            cron_id,
            enabled=value == "Enabled",
        )
        if updated_entry is None:
            return

        self._resync_schedule_runtime(refresh_table=True, save_state=True)
        self._append_log(
            f"Set CRON status for media '{self._media_log_name(updated_entry.media_id)}' to {value}"
        )

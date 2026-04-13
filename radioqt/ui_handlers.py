from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QDate, QModelIndex, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDialog, QInputDialog, QMenu, QMessageBox

from .library import (
    add_stream_media_item,
    remove_media_from_library,
    selected_url_media_id,
    update_stream_greenwich_time_signal,
    update_stream_media_item,
)
from .models import SCHEDULE_STATUS_MISSED
from .playback import enqueue_manual_media, resolve_media_by_id
from .scheduling import (
    create_cron_entry,
    create_schedule_entry,
    remove_cron_and_generated_schedule_entries,
    remove_schedule_entries_by_ids,
    select_schedule_entries_for_removal,
    update_cron_enabled,
    update_cron_expression,
    update_cron_fade_in,
    update_cron_fade_out,
    update_schedule_fade_in,
    update_schedule_fade_out,
    update_schedule_status,
)
from .ui_components import CronDialog, ScheduleDialog


class MainWindowHandlersMixin:
    @Slot(QDate)
    def _on_schedule_filter_date_changed(self, selected_date: QDate) -> None:
        self._schedule_filter_date = selected_date.toPython()
        self._resync_schedule_runtime(refresh_table=True)

    @Slot(bool)
    def _on_schedule_auto_focus_toggled(self, checked: bool) -> None:
        self._schedule_auto_focus_enabled = checked
        self._save_state()
        if checked:
            self._apply_schedule_auto_focus(force=True)

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
        dialog = ScheduleDialog(self, initial_start_at=self._default_next_schedule_start())
        if dialog.exec() != QDialog.Accepted:
            return

        entry = create_schedule_entry(
            media_id=media_id,
            start_at=dialog.selected_datetime(),
            reference_time=datetime.now().astimezone(),
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
        item = self._schedule_table.itemAt(position)
        if item is None:
            return
        selected_count = len(self._selected_schedule_entry_ids())
        selected_ids = set(self._selected_schedule_entry_ids())
        has_cron_generated = any(
            entry.id in selected_ids and self._is_schedule_entry_protected_from_removal(entry)
            for entry in self._schedule_entries
        )
        menu = QMenu(self._schedule_table)
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

        dialog = CronDialog(self)
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
        if item is None:
            return
        self._urls_table.selectRow(item.row())
        menu = QMenu(self._urls_table)
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

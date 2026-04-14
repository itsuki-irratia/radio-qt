from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileSystemModel, QTreeView

from ..library import is_stream_source, selected_filesystem_media_id, selected_url_media_id
from ..ui_components import refresh_cron_table, refresh_urls_table


class MainWindowLibrarySelectionMixin:
    def _refresh_urls_list(self) -> None:
        refresh_urls_table(
            self._urls_table,
            self._media_items,
            is_stream_source=is_stream_source,
            on_greenwich_time_signal_changed=self._on_stream_greenwich_time_signal_changed,
        )

    def _refresh_cron_table(self) -> None:
        refresh_cron_table(
            self._cron_table,
            self._cron_entries,
            self._media_items,
            on_fade_in_changed=self._on_cron_fade_in_changed,
            on_fade_out_changed=self._on_cron_fade_out_changed,
            on_status_changed=self._on_cron_status_changed,
        )

    def _media_log_name(self, media_id: str) -> str:
        media = self._media_items.get(media_id)
        if media is None:
            return f"missing:{media_id[:8]}"
        return media.title

    def _current_library_tab_descriptor(
        self,
    ) -> tuple[str, QTreeView | None, QFileSystemModel | None]:
        current_widget = self._library_tabs.currentWidget()
        if current_widget is None:
            return "filesystem", self._filesystem_view, self._filesystem_model
        return self._library_tab_sources.get(
            current_widget,
            ("filesystem", self._filesystem_view, self._filesystem_model),
        )

    def _selected_media_id(self) -> str | None:
        panel_kind, filesystem_view, filesystem_model = self._current_library_tab_descriptor()
        if panel_kind == "urls":
            return selected_url_media_id(self._urls_table)

        if filesystem_view is None or filesystem_model is None:
            return None
        media_id, created = selected_filesystem_media_id(
            filesystem_view,
            filesystem_model,
            self._media_items,
            self._media_duration_cache,
            supported_extensions=self._supported_extension_suffixes(),
        )
        if created:
            self._save_state()
        return media_id

    def _selected_schedule_entry_id(self) -> str | None:
        row = self._schedule_table.currentRow()
        if row < 0:
            return None
        item = self._schedule_table.item(row, 0)
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def _selected_schedule_entry_ids(self) -> list[str]:
        rows = sorted({index.row() for index in self._schedule_table.selectedIndexes()})
        entry_ids = []
        for row in rows:
            item = self._schedule_table.item(row, 0)
            if item is not None:
                entry_id = item.data(Qt.UserRole)
                if entry_id is not None:
                    entry_ids.append(entry_id)
        return entry_ids

    def _selected_cron_entry_id(self) -> str | None:
        row = self._cron_table.currentRow()
        if row < 0:
            return None
        item = self._cron_table.item(row, 0)
        if item is None:
            return None
        return item.data(Qt.UserRole)

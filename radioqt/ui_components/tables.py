from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QTableWidget, QTableWidgetItem, QWidget

from ..models import CronEntry, MediaItem, ScheduleEntry, SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED


def refresh_urls_table(
    urls_table: QTableWidget,
    media_items: dict[str, MediaItem],
    *,
    is_stream_source: Callable[[str], bool],
) -> None:
    urls_table.setRowCount(0)
    items = sorted(media_items.values(), key=lambda item: item.created_at)
    for media in items:
        if not is_stream_source(media.source):
            continue
        row = urls_table.rowCount()
        urls_table.insertRow(row)
        title_item = QTableWidgetItem(media.title)
        title_item.setData(Qt.UserRole, media.id)
        urls_table.setItem(row, 0, title_item)
        urls_table.setItem(row, 1, QTableWidgetItem(media.source))
    urls_table.resizeColumnsToContents()


def refresh_cron_table(
    cron_table: QTableWidget,
    entries: list[CronEntry],
    media_items: dict[str, MediaItem],
    *,
    on_hard_sync_changed: Callable[[str, str], None],
    on_status_changed: Callable[[str, str], None],
) -> None:
    ordered_entries = sorted(entries, key=lambda entry: entry.created_at)
    cron_table.setRowCount(len(ordered_entries))
    for row, entry in enumerate(ordered_entries):
        media = media_items.get(entry.media_id)
        media_name = media.title if media else f"Missing ({entry.media_id[:8]})"
        media_source = media.source if media else f"Missing media ID: {entry.media_id}"

        expression_item = QTableWidgetItem(entry.expression)
        expression_item.setData(Qt.UserRole, entry.id)
        expression_item.setToolTip(media_source)
        cron_table.setItem(row, 0, expression_item)

        media_item = QTableWidgetItem(media_name)
        media_item.setToolTip(media_source)
        cron_table.setItem(row, 1, media_item)

        hard_sync_selector = QComboBox(cron_table)
        hard_sync_selector.addItems(["Yes", "No"])
        hard_sync_selector.setCurrentText("Yes" if entry.hard_sync else "No")
        hard_sync_selector.setToolTip(media_source)
        hard_sync_selector.currentTextChanged.connect(
            lambda value, entry_id=entry.id: on_hard_sync_changed(entry_id, value)
        )
        cron_table.setCellWidget(row, 2, hard_sync_selector)

        status_selector = QComboBox(cron_table)
        status_selector.addItems(["Enabled", "Disabled"])
        status_selector.setCurrentText("Enabled" if entry.enabled else "Disabled")
        status_selector.setToolTip(media_source)
        status_selector.currentTextChanged.connect(
            lambda value, entry_id=entry.id: on_status_changed(entry_id, value)
        )
        cron_table.setCellWidget(row, 3, status_selector)

    cron_table.resizeColumnsToContents()


def refresh_schedule_table(
    schedule_table: QTableWidget,
    entries: list[ScheduleEntry],
    media_items: dict[str, MediaItem],
    reference_time: datetime,
    *,
    cron_entry_by_id: Callable[[str | None], CronEntry | None],
    duration_display_details: Callable[[MediaItem | None, int | None], tuple[str, str]],
    schedule_window_tooltip: Callable[[ScheduleEntry], str],
    schedule_entry_palette: Callable[[ScheduleEntry, datetime], tuple | None],
    apply_item_palette: Callable[[QTableWidgetItem, tuple | None], None],
    apply_widget_palette: Callable[[QWidget, tuple | None], None],
    on_hard_sync_changed: Callable[[str, str], None],
    on_status_changed: Callable[[str, str], None],
) -> None:
    schedule_table.setRowCount(len(entries))
    for row, entry in enumerate(entries):
        media = media_items.get(entry.media_id)
        media_name = media.title if media else f"Missing ({entry.media_id[:8]})"
        media_source = media.source if media else f"Missing media ID: {entry.media_id}"
        status = entry.status.capitalize()
        start_label = entry.start_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        duration_label, duration_tooltip = duration_display_details(media, entry.duration)
        cron_entry = cron_entry_by_id(entry.cron_id)
        origin_label = (
            f"Generated from CRON: {cron_entry.expression}"
            if cron_entry is not None
            else "Manual schedule"
        )
        window_tooltip = schedule_window_tooltip(entry)
        tooltip = f"{media_source}\n{origin_label}\n{duration_tooltip}\n{window_tooltip}"
        palette = schedule_entry_palette(entry, reference_time)

        start_item = QTableWidgetItem(start_label)
        start_item.setData(Qt.UserRole, entry.id)
        start_item.setToolTip(tooltip)
        apply_item_palette(start_item, palette)
        schedule_table.setItem(row, 0, start_item)

        duration_item = QTableWidgetItem(duration_label)
        duration_item.setToolTip(tooltip)
        apply_item_palette(duration_item, palette)
        schedule_table.setItem(row, 1, duration_item)

        media_item = QTableWidgetItem(media_name)
        media_item.setToolTip(tooltip)
        apply_item_palette(media_item, palette)
        schedule_table.setItem(row, 2, media_item)

        cron_globally_disabled = cron_entry is not None and not cron_entry.enabled
        is_locked = entry.status in {SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED}

        hard_sync_selector = QComboBox(schedule_table)
        hard_sync_selector.addItems(["Yes", "No"])
        hard_sync_selector.setCurrentText("Yes" if entry.hard_sync else "No")
        hard_sync_selector.setEnabled(not is_locked)
        hard_sync_selector.setToolTip(tooltip)
        hard_sync_selector.currentTextChanged.connect(
            lambda value, entry_id=entry.id: on_hard_sync_changed(entry_id, value)
        )
        apply_widget_palette(hard_sync_selector, palette)
        schedule_table.setCellWidget(row, 3, hard_sync_selector)

        status_selector = QComboBox(schedule_table)
        if cron_globally_disabled:
            status_selector.addItem("Disabled")
        else:
            status_selector.addItems(["Pending", "Disabled"])
        if entry.status == SCHEDULE_STATUS_FIRED:
            status_selector.addItem("Fired")
        if entry.status == SCHEDULE_STATUS_MISSED:
            status_selector.addItem("Missed")
        status_selector.setCurrentText(status)
        status_selector.setEnabled(not is_locked and not cron_globally_disabled)
        status_selector.setToolTip(tooltip)
        status_selector.currentTextChanged.connect(
            lambda value, entry_id=entry.id: on_status_changed(entry_id, value)
        )
        apply_widget_palette(status_selector, palette)
        schedule_table.setCellWidget(row, 4, status_selector)

    schedule_table.resizeColumnsToContents()

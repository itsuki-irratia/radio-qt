from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QTableWidget, QTableWidgetItem, QWidget

from ..models import CronEntry, MediaItem, ScheduleEntry, SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED
from .boolean_selectors import _configure_boolean_selector


class NoScrollComboBox(QComboBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


def _apply_comfortable_column_widths(
    table: QTableWidget,
    minimum_widths: list[int],
    *,
    extra_padding: int = 24,
) -> None:
    for column, min_width in enumerate(minimum_widths):
        if column >= table.columnCount():
            break
        content_width = table.columnWidth(column)
        table.setColumnWidth(column, max(min_width, content_width + extra_padding))


def refresh_urls_table(
    urls_table: QTableWidget,
    media_items: dict[str, MediaItem],
    *,
    is_stream_source: Callable[[str], bool],
    on_greenwich_time_signal_changed: Callable[[str, str], None],
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
        source_item = QTableWidgetItem(media.source)
        source_item.setToolTip(media.source)
        urls_table.setItem(row, 1, source_item)

        signal_selector = NoScrollComboBox(urls_table)
        signal_selector.addItems(["True", "False"])
        signal_selector.setCurrentText("True" if media.greenwich_time_signal_enabled else "False")
        signal_selector.setToolTip("Allow Greenwich Time Signal while this stream is active")
        _configure_boolean_selector(signal_selector)
        signal_selector.currentTextChanged.connect(
            lambda value, media_id=media.id: on_greenwich_time_signal_changed(media_id, value)
        )
        urls_table.setCellWidget(row, 2, signal_selector)
    urls_table.resizeColumnsToContents()
    _apply_comfortable_column_widths(
        urls_table,
        [220, 420, 220],
    )


def refresh_cron_table(
    cron_table: QTableWidget,
    entries: list[CronEntry],
    media_items: dict[str, MediaItem],
    *,
    on_fade_in_changed: Callable[[str, str], None],
    on_fade_out_changed: Callable[[str, str], None],
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

        fade_in_selector = NoScrollComboBox(cron_table)
        fade_in_selector.addItems(["True", "False"])
        fade_in_selector.setCurrentText("True" if entry.fade_in else "False")
        fade_in_selector.setToolTip(media_source)
        _configure_boolean_selector(fade_in_selector)
        fade_in_selector.currentTextChanged.connect(
            lambda value, entry_id=entry.id: on_fade_in_changed(entry_id, value)
        )
        cron_table.setCellWidget(row, 2, fade_in_selector)

        fade_out_selector = NoScrollComboBox(cron_table)
        fade_out_selector.addItems(["True", "False"])
        fade_out_selector.setCurrentText("True" if entry.fade_out else "False")
        fade_out_selector.setToolTip(media_source)
        _configure_boolean_selector(fade_out_selector)
        fade_out_selector.currentTextChanged.connect(
            lambda value, entry_id=entry.id: on_fade_out_changed(entry_id, value)
        )
        cron_table.setCellWidget(row, 3, fade_out_selector)

        status_selector = NoScrollComboBox(cron_table)
        status_selector.addItems(["Enabled", "Disabled"])
        status_selector.setCurrentText("Enabled" if entry.enabled else "Disabled")
        status_selector.setToolTip(media_source)
        status_selector.currentTextChanged.connect(
            lambda value, entry_id=entry.id: on_status_changed(entry_id, value)
        )
        cron_table.setCellWidget(row, 4, status_selector)

    cron_table.resizeColumnsToContents()
    _apply_comfortable_column_widths(
        cron_table,
        [220, 260, 110, 110, 130],
    )


def refresh_schedule_table(
    schedule_table: QTableWidget,
    entries: list[ScheduleEntry],
    media_items: dict[str, MediaItem],
    reference_time: datetime,
    *,
    cron_entry_by_id: Callable[[str | None], CronEntry | None],
    duration_display_details: Callable[[ScheduleEntry, MediaItem | None, int | None], tuple[str, str]],
    schedule_window_tooltip: Callable[[ScheduleEntry], str],
    schedule_entry_palette: Callable[[ScheduleEntry, datetime], tuple | None],
    apply_item_palette: Callable[[QTableWidgetItem, tuple | None], None],
    apply_widget_palette: Callable[[QWidget, tuple | None], None],
    schedule_entry_can_edit_fade_in: Callable[[ScheduleEntry, datetime], bool],
    schedule_entry_can_edit_fade_out: Callable[[ScheduleEntry, datetime], bool],
    schedule_entry_can_edit_status: Callable[[ScheduleEntry, datetime], bool],
    on_fade_in_changed: Callable[[str, str], None],
    on_fade_out_changed: Callable[[str, str], None],
    on_status_changed: Callable[[str, str], None],
) -> None:
    schedule_table.setRowCount(len(entries))
    for row, entry in enumerate(entries):
        media = media_items.get(entry.media_id)
        media_name = media.title if media else f"Missing ({entry.media_id[:8]})"
        media_source = media.source if media else f"Missing media ID: {entry.media_id}"
        status = entry.status.capitalize()
        start_label = entry.start_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        duration_label, duration_tooltip = duration_display_details(entry, media, entry.duration)
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
        can_edit_fade_in = not is_locked and schedule_entry_can_edit_fade_in(entry, reference_time)
        can_edit_fade_out = not is_locked and schedule_entry_can_edit_fade_out(entry, reference_time)
        can_edit_status = (
            not is_locked
            and not cron_globally_disabled
            and schedule_entry_can_edit_status(entry, reference_time)
        )

        fade_in_selector = NoScrollComboBox(schedule_table)
        fade_in_selector.addItems(["True", "False"])
        fade_in_selector.setCurrentText("True" if entry.fade_in else "False")
        fade_in_selector.setEnabled(can_edit_fade_in)
        fade_in_selector.setToolTip(tooltip)
        _configure_boolean_selector(fade_in_selector)
        fade_in_selector.currentTextChanged.connect(
            lambda value, entry_id=entry.id: on_fade_in_changed(entry_id, value)
        )
        schedule_table.setCellWidget(row, 3, fade_in_selector)

        fade_out_selector = NoScrollComboBox(schedule_table)
        fade_out_selector.addItems(["True", "False"])
        fade_out_selector.setCurrentText("True" if entry.fade_out else "False")
        fade_out_selector.setEnabled(can_edit_fade_out)
        fade_out_selector.setToolTip(tooltip)
        _configure_boolean_selector(fade_out_selector)
        fade_out_selector.currentTextChanged.connect(
            lambda value, entry_id=entry.id: on_fade_out_changed(entry_id, value)
        )
        schedule_table.setCellWidget(row, 4, fade_out_selector)

        status_selector = NoScrollComboBox(schedule_table)
        if cron_globally_disabled:
            status_selector.addItem("Disabled")
        else:
            status_selector.addItems(["Pending", "Disabled"])
        if entry.status == SCHEDULE_STATUS_FIRED:
            status_selector.addItem("Fired")
        if entry.status == SCHEDULE_STATUS_MISSED:
            status_selector.addItem("Missed")
        status_selector.setCurrentText(status)
        status_selector.setEnabled(can_edit_status)
        status_selector.setToolTip(tooltip)
        status_selector.currentTextChanged.connect(
            lambda value, entry_id=entry.id: on_status_changed(entry_id, value)
        )
        apply_widget_palette(status_selector, palette)
        schedule_table.setCellWidget(row, 5, status_selector)

    schedule_table.resizeColumnsToContents()
    _apply_comfortable_column_widths(
        schedule_table,
        [220, 140, 280, 110, 110, 130],
    )

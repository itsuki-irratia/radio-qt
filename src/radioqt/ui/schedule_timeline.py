from __future__ import annotations

from concurrent.futures import Future
from datetime import date, datetime, timedelta

from PySide6.QtCore import QDate, Qt, Slot
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QAbstractItemView, QTableWidgetItem, QWidget

from ..duration_probe import (
    duration_probe_cache_key_from_path,
    duration_probe_cache_key_from_source,
    duration_probe_cache_lookup,
    normalize_probe_duration,
    probe_media_duration_seconds,
    store_duration_probe_cache,
)
from ..library import is_stream_source, local_media_path_from_source
from ..models import CronEntry, MediaItem, SCHEDULE_STATUS_PENDING, ScheduleEntry
from ..scheduling import (
    active_schedule_entry_at,
    current_schedule_entry_for_playback,
    enforce_hard_sync_always,
    is_schedule_entry_protected_from_removal,
    next_cron_occurrence,
    normalized_start,
    runtime_cron_dates,
    schedule_entry_end_at,
    schedule_entry_palette_tokens,
    schedule_entry_window_details,
    sync_cron_runtime_window,
    visible_schedule_entries,
)
from ..ui_components import ScheduleDialog, refresh_schedule_table


class MainWindowScheduleTimelineMixin:
    def _cron_entry_by_id(self, cron_id: str | None) -> CronEntry | None:
        if cron_id is None:
            return None
        for entry in self._cron_entries:
            if entry.id == cron_id:
                return entry
        return None

    def _schedule_entry_by_id(self, entry_id: str | None) -> ScheduleEntry | None:
        if entry_id is None:
            return None
        for entry in self._schedule_entries:
            if entry.id == entry_id:
                return entry
        return None

    def _next_scheduled_start(self, entry: ScheduleEntry) -> datetime | None:
        ordered = sorted(
            self._schedule_entries,
            key=lambda candidate: self._normalized_start(candidate.start_at),
        )
        for index, candidate in enumerate(ordered):
            if candidate.id != entry.id:
                continue
            if index + 1 >= len(ordered):
                return None
            return self._normalized_start(ordered[index + 1].start_at)
        return None

    def _next_scheduled_gap_ms(self, entry: ScheduleEntry) -> int | None:
        next_start = self._next_scheduled_start(entry)
        if next_start is None:
            return None
        start_at = self._normalized_start(entry.start_at)
        gap_ms = max(0, int((next_start - start_at).total_seconds() * 1000))
        if gap_ms <= 0:
            return None
        return gap_ms

    def _is_open_ended_stream_entry(
        self,
        entry: ScheduleEntry,
        media: MediaItem | None = None,
    ) -> bool:
        resolved_media = media or self._media_items.get(entry.media_id)
        if resolved_media is None:
            return False
        return (
            is_stream_source(resolved_media.source)
            and local_media_path_from_source(resolved_media.source) is None
        )

    def _entry_duration_ms(self, entry: ScheduleEntry | None) -> int | None:
        if entry is None:
            return None

        media_duration_ms: int | None = None
        if entry.duration is not None and entry.duration > 0:
            media_duration_ms = entry.duration * 1000

        next_gap_ms = self._next_scheduled_gap_ms(entry)
        if media_duration_ms is not None and next_gap_ms is not None:
            return min(media_duration_ms, next_gap_ms)

        if media_duration_ms is not None:
            return media_duration_ms

        if next_gap_ms is not None:
            return next_gap_ms

        if self._is_open_ended_stream_entry(entry):
            return None
        return None

    def _enforce_hard_sync_always(self) -> bool:
        return enforce_hard_sync_always(
            self._cron_entries,
            self._schedule_entries,
        )

    def _is_schedule_entry_protected_from_removal(self, entry: ScheduleEntry) -> bool:
        return is_schedule_entry_protected_from_removal(
            entry,
            {cron_entry.id: cron_entry for cron_entry in self._cron_entries},
        )

    @staticmethod
    def _runtime_cron_dates() -> set[date]:
        return runtime_cron_dates(datetime.now().astimezone())

    def _refresh_cron_schedule_entries(self, target_dates: set[date] | None = None) -> None:
        self._schedule_entries = sync_cron_runtime_window(
            self._schedule_entries,
            self._cron_entries,
            target_dates=target_dates,
            now=datetime.now().astimezone(),
            runtime_lookback=self._CRON_RUNTIME_LOOKBACK,
            max_occurrences=self._CRON_RUNTIME_MAX_OCCURRENCES,
            max_recent_occurrences=self._CRON_RUNTIME_MAX_RECENT_OCCURRENCES,
        )

    def _resync_schedule_runtime(self, *, refresh_table: bool = False, save_state: bool = False) -> None:
        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_and_apply_schedule_entries()
        if refresh_table:
            self._refresh_schedule_table()
        if save_state:
            self._save_state()

    def _recalculate_and_apply_schedule_entries(self) -> None:
        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)

    def _sync_after_cron_rule_change(self, *, focus_entry: CronEntry | None = None) -> None:
        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        if focus_entry is not None:
            next_occurrence = next_cron_occurrence(focus_entry, datetime.now().astimezone())
            if next_occurrence is not None:
                self._set_schedule_filter_date(next_occurrence.date())
                self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_and_apply_schedule_entries()
        self._refresh_cron_table()
        self._refresh_schedule_table()
        self._save_state()

    def _refresh_cron_runtime_window(self) -> None:
        self._resync_schedule_runtime()
        normalized_entries, normalized_details = self._normalize_overdue_one_shots(
            datetime.now().astimezone(),
            {SCHEDULE_STATUS_PENDING},
        )
        if normalized_entries:
            self._refresh_schedule_table()
            self._save_state()
            self._append_log(
                f"Marked {normalized_entries} overdue one-shot schedule item(s) as missed"
            )
            self._append_normalized_missed_logs(normalized_entries, normalized_details)

    def _schedule_entry_palette(self, entry: ScheduleEntry, reference_time: datetime) -> tuple[QColor, QColor] | None:
        current_media_id = self._player.current_media.id if self._player.current_media is not None else None
        current_entry = current_schedule_entry_for_playback(
            self._schedule_entries,
            reference_time,
            player_is_playing=self._player.is_playing(),
            current_media_id=current_media_id,
        )
        palette_tokens = schedule_entry_palette_tokens(
            entry,
            reference_time,
            current_entry_id=current_entry.id if current_entry is not None else None,
        )
        if palette_tokens is None:
            return None
        background_hex, foreground_hex = palette_tokens
        return QColor(background_hex), QColor(foreground_hex)

    @staticmethod
    def _apply_item_palette(item: QTableWidgetItem, palette: tuple[QColor, QColor] | None) -> None:
        if palette is None:
            return
        background, foreground = palette
        item.setBackground(QBrush(background))
        item.setForeground(QBrush(foreground))

    @staticmethod
    def _apply_widget_palette(widget: QWidget, palette: tuple[QColor, QColor] | None) -> None:
        if palette is None:
            widget.setStyleSheet("")
            return
        background, foreground = palette
        widget.setStyleSheet(
            "QComboBox {"
            f"background-color: {background.name()};"
            f"color: {foreground.name()};"
            "}"
        )

    def _refresh_schedule_table(self) -> None:
        entries = visible_schedule_entries(
            self._schedule_entries,
            self._schedule_filter_date,
            datetime.now().astimezone(),
        )
        now = datetime.now().astimezone()
        refresh_schedule_table(
            self._schedule_table,
            entries,
            self._media_items,
            now,
            cron_entry_by_id=self._cron_entry_by_id,
            duration_display_details=self._duration_display_details,
            schedule_window_tooltip=self._schedule_window_tooltip,
            schedule_entry_palette=self._schedule_entry_palette,
            apply_item_palette=self._apply_item_palette,
            apply_widget_palette=self._apply_widget_palette,
            on_fade_in_changed=self._on_schedule_fade_in_changed,
            on_fade_out_changed=self._on_schedule_fade_out_changed,
            on_status_changed=self._on_schedule_status_changed,
        )

    def _set_schedule_filter_date(self, target_date: date) -> None:
        self._schedule_filter_date = target_date
        self._schedule_date_selector.blockSignals(True)
        self._schedule_date_selector.setDate(
            QDate(target_date.year, target_date.month, target_date.day)
        )
        self._schedule_date_selector.blockSignals(False)

    def _recalculate_schedule_durations(self) -> None:
        entries = sorted(self._schedule_entries, key=lambda entry: self._normalized_start(entry.start_at))
        for entry in entries:
            entry.duration = self._media_duration_seconds(entry.media_id)

    def _default_next_schedule_start(self) -> datetime:
        if not self._schedule_entries:
            return ScheduleDialog._default_start_datetime()

        entries = sorted(self._schedule_entries, key=lambda entry: self._normalized_start(entry.start_at))
        previous = entries[-1]
        previous_start = self._normalized_start(previous.start_at)
        now = datetime.now().astimezone()
        if previous_start <= now:
            return ScheduleDialog._default_start_datetime()
        if previous.duration is None:
            return previous_start
        return previous_start + timedelta(seconds=max(0, previous.duration))

    def _media_duration_seconds(self, media_id: str) -> int | None:
        if media_id in self._media_duration_cache:
            return self._media_duration_cache[media_id]

        media = self._media_items.get(media_id)
        if media is None:
            self._media_duration_cache[media_id] = None
            return None

        local_path = local_media_path_from_source(media.source)
        if local_path is None or not local_path.is_file():
            self._media_duration_cache[media_id] = None
            return None

        probe_key = duration_probe_cache_key_from_path(local_path)
        if probe_key is not None:
            cached, cached_duration = duration_probe_cache_lookup(self._duration_probe_cache, probe_key)
            if cached:
                self._media_duration_cache[media_id] = cached_duration
                return cached_duration

        self._request_media_duration_probe(media_id, media.source, probe_key=probe_key)
        return None

    def _request_media_duration_probe(
        self,
        media_id: str,
        source: str,
        *,
        probe_key: str | None = None,
    ) -> None:
        if media_id in self._media_duration_pending or self._shutting_down:
            return
        requested_probe_key = probe_key or duration_probe_cache_key_from_source(source)
        self._media_duration_pending.add(media_id)
        future = self._duration_probe_executor.submit(
            probe_media_duration_seconds,
            source,
        )
        future.add_done_callback(
            lambda task, requested_media_id=media_id, requested_source=source, requested_key=requested_probe_key: (
                self._emit_duration_probe_result(
                    requested_media_id,
                    requested_source,
                    requested_key,
                    task,
                )
            )
        )

    def _emit_duration_probe_result(
        self,
        media_id: str,
        source: str,
        probe_key: str | None,
        task: Future[int | None],
    ) -> None:
        if self._shutting_down:
            return
        try:
            duration = task.result()
        except Exception:
            duration = None
        try:
            self._duration_probe_dispatcher.probe_finished.emit(media_id, source, probe_key, duration)
        except RuntimeError:
            return

    @Slot(str, str, object, object)
    def _on_media_duration_probed(
        self,
        media_id: str,
        source: str,
        probe_key: object,
        duration: object,
    ) -> None:
        self._media_duration_pending.discard(media_id)
        if self._shutting_down:
            return

        media = self._media_items.get(media_id)
        if media is None:
            self._media_duration_cache.pop(media_id, None)
            return

        if media.source != source:
            self._media_duration_cache.pop(media_id, None)
            self._media_duration_seconds(media_id)
            return

        requested_probe_key = probe_key if isinstance(probe_key, str) and probe_key else None
        current_probe_key = duration_probe_cache_key_from_source(media.source)
        if (
            requested_probe_key is not None
            and current_probe_key is not None
            and requested_probe_key != current_probe_key
        ):
            self._media_duration_cache.pop(media_id, None)
            self._media_duration_seconds(media_id)
            return

        resolved_duration = normalize_probe_duration(duration)

        effective_probe_key = requested_probe_key or current_probe_key
        if effective_probe_key is not None:
            store_duration_probe_cache(
                self._duration_probe_cache,
                effective_probe_key,
                resolved_duration,
                max_entries=self._DURATION_PROBE_CACHE_MAX_ENTRIES,
            )

        previous_duration = self._media_duration_cache.get(media_id, object())
        self._media_duration_cache[media_id] = resolved_duration
        if previous_duration == resolved_duration:
            return

        updated = False
        for entry in self._schedule_entries:
            if entry.media_id != media_id:
                continue
            if entry.duration == resolved_duration:
                continue
            entry.duration = resolved_duration
            updated = True

        if not updated:
            return

        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()
        self._save_state()

    @staticmethod
    def _normalized_start(start_at: datetime) -> datetime:
        return normalized_start(start_at)

    @staticmethod
    def _format_duration(duration_seconds: int | None) -> str:
        if duration_seconds is None:
            return "-"
        hours, remainder = divmod(duration_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _duration_display_details(
        self,
        entry: ScheduleEntry,
        media: MediaItem | None,
        duration_seconds: int | None,
    ) -> tuple[str, str]:
        effective_duration_ms = self._entry_duration_ms(entry)
        if effective_duration_ms is not None and effective_duration_ms > 0:
            effective_seconds = max(0, effective_duration_ms // 1000)
            formatted = self._format_duration(effective_seconds)
            media_duration_ms = entry.duration * 1000 if entry.duration is not None and entry.duration > 0 else None
            next_gap_ms = self._next_scheduled_gap_ms(entry)
            if media_duration_ms is not None and next_gap_ms is not None:
                media_formatted = self._format_duration(media_duration_ms // 1000)
                gap_formatted = self._format_duration(next_gap_ms // 1000)
                if media_duration_ms < next_gap_ms:
                    return (
                        formatted,
                        "Duration read from media file: "
                        f"{media_formatted} (next scheduled gap: {gap_formatted})",
                    )
                if next_gap_ms < media_duration_ms:
                    return (
                        formatted,
                        "Duration limited by next scheduled item: "
                        f"{gap_formatted} (media duration: {media_formatted})",
                    )
                return formatted, f"Duration from media and schedule boundary: {formatted}"

            if media_duration_ms is not None:
                return formatted, f"Duration read from media file: {formatted}"
            if next_gap_ms is not None:
                next_start = self._next_scheduled_start(entry)
                next_label = (
                    next_start.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                    if next_start is not None
                    else "unknown"
                )
                return (
                    formatted,
                    "Duration computed from next scheduled item: "
                    f"{formatted} (next start at {next_label})",
                )
            return formatted, f"Effective duration: {formatted}"

        if effective_duration_ms is None and self._is_open_ended_stream_entry(entry, media):
            return "-", "Open-ended stream: no media duration and no next scheduled item"

        if media is not None and media.id in self._media_duration_pending:
            return "Loading", "Duration is being analyzed in background"
        if duration_seconds is not None:
            formatted = self._format_duration(duration_seconds)
            return formatted, f"Duration read from media file: {formatted}"
        if media is None:
            return "Missing", "Duration unavailable: media item is missing"

        if is_stream_source(media.source) and local_media_path_from_source(media.source) is None:
            return "Stream", "Duration unavailable for remote streams/URLs"
        local_path = local_media_path_from_source(media.source)
        if local_path is None or not local_path.exists():
            return "Missing", "Duration unavailable: local file is missing"
        return "Unknown", "Duration unavailable: ffprobe could not read this file"

    def _schedule_window_tooltip(self, entry: ScheduleEntry) -> str:
        start_at, end_at, end_reason = self._schedule_entry_window_details(entry)
        start_label = start_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        if end_at is None:
            end_label = "Open-ended"
        else:
            end_label = end_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        return (
            f"Computed start: {start_label}\n"
            f"Computed end: {end_label}\n"
            f"End reason: {end_reason}"
        )

    def _schedule_entry_window_details(
        self,
        entry: ScheduleEntry,
    ) -> tuple[datetime, datetime | None, str]:
        return schedule_entry_window_details(self._schedule_entries, entry.id)

    def _schedule_log_summary(self, reference_time: datetime) -> str:
        entries = sorted(
            self._schedule_entries,
            key=lambda entry: self._normalized_start(entry.start_at),
        )
        if not entries:
            return "schedule: empty"

        upcoming_entry = next(
            (
                entry
                for entry in entries
                if self._normalized_start(entry.start_at) >= reference_time
            ),
            None,
        )
        target_entry = upcoming_entry if upcoming_entry is not None else entries[-1]
        prefix = "next" if upcoming_entry is not None else "recent"
        start_label = self._normalized_start(target_entry.start_at).strftime("%H:%M:%S")
        duration_label = (
            str(target_entry.duration)
            if target_entry.duration is not None
            else "-"
        )
        return (
            f"schedule {prefix}: "
            f"{self._media_log_name(target_entry.media_id)}@{start_label}/"
            f"{target_entry.status}/dur={duration_label}"
        )

    def _focus_schedule_entry(self, entry_id: str, force: bool = False) -> None:
        selected_entry_ids = self._selected_schedule_entry_ids()
        if not force and selected_entry_ids == [entry_id]:
            return

        for row in range(self._schedule_table.rowCount()):
            item = self._schedule_table.item(row, 0)
            if item is None or item.data(Qt.UserRole) != entry_id:
                continue
            self._schedule_table.clearSelection()
            self._schedule_table.setCurrentCell(row, 0)
            self._schedule_table.selectRow(row)
            self._schedule_table.scrollToItem(
                item,
                QAbstractItemView.ScrollHint.PositionAtCenter,
            )
            return

    def _schedule_entry_to_focus(self, reference_time: datetime) -> tuple[ScheduleEntry, date] | None:
        active_entry = self._active_schedule_entry_at(reference_time)
        if active_entry is not None:
            entry, start_at = active_entry
            return entry, start_at.date()

        entries = sorted(
            self._schedule_entries,
            key=lambda entry: self._normalized_start(entry.start_at),
        )
        if not entries:
            return None

        for index, entry in enumerate(entries):
            start_at = self._normalized_start(entry.start_at)
            if start_at >= reference_time:
                target_entry = entries[index - 1] if index > 0 else entry
                return target_entry, self._normalized_start(target_entry.start_at).date()

        last_entry = entries[-1]
        return last_entry, self._normalized_start(last_entry.start_at).date()

    def _apply_schedule_auto_focus(self, force: bool = False) -> None:
        if not self._schedule_auto_focus_enabled:
            return

        target_entry = self._schedule_entry_to_focus(datetime.now().astimezone())
        if target_entry is None:
            return

        entry, active_date = target_entry
        if active_date != self._schedule_filter_date:
            self._set_schedule_filter_date(active_date)
            self._resync_schedule_runtime(refresh_table=True)

        self._focus_schedule_entry(entry.id, force=force)

    @Slot()
    def _refresh_schedule_auto_focus(self) -> None:
        self._apply_schedule_auto_focus()

    def _active_schedule_entry_at(self, now: datetime) -> tuple[ScheduleEntry, datetime] | None:
        return active_schedule_entry_at(self._schedule_entries, now)

    def _schedule_entry_end_at(
        self,
        entries: list[ScheduleEntry],
        index: int,
    ) -> datetime | None:
        return schedule_entry_end_at(entries, index)

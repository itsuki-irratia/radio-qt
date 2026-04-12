from __future__ import annotations

from datetime import date, datetime, timedelta

from ..models import (
    CronEntry,
    SCHEDULE_STATUS_DISABLED,
    SCHEDULE_STATUS_FIRED,
    SCHEDULE_STATUS_MISSED,
    ScheduleEntry,
)
from .cron_runtime import next_cron_occurrence
from .logic import active_schedule_entry_at, normalized_start


def runtime_cron_dates(reference_time: datetime) -> set[date]:
    today = reference_time.date()
    return {today, today + timedelta(days=1)}


def visible_schedule_entries(
    entries: list[ScheduleEntry],
    filter_date: date,
    reference_time: datetime,
) -> list[ScheduleEntry]:
    return [
        entry
        for entry in sorted(
            entries,
            key=lambda current_entry: normalized_start(current_entry.start_at, reference_time),
        )
        if normalized_start(entry.start_at, reference_time).date() == filter_date
    ]


def initial_schedule_filter_date(
    schedule_entries: list[ScheduleEntry],
    cron_entries: list[CronEntry],
    reference_time: datetime,
) -> date:
    today = reference_time.date()
    upcoming_dates = [
        normalized_start(entry.start_at, reference_time).date()
        for entry in sorted(
            schedule_entries,
            key=lambda entry: normalized_start(entry.start_at, reference_time),
        )
    ]
    for entry_date in upcoming_dates:
        if entry_date >= today:
            return entry_date
    if upcoming_dates:
        return upcoming_dates[0]

    cron_dates = []
    for cron_entry in cron_entries:
        next_occurrence = next_cron_occurrence(cron_entry, reference_time)
        if next_occurrence is not None:
            cron_dates.append(next_occurrence.date())
    if cron_dates:
        return min(cron_dates)
    return today


def current_schedule_entry_for_playback(
    schedule_entries: list[ScheduleEntry],
    reference_time: datetime,
    *,
    player_is_playing: bool,
    current_media_id: str | None,
) -> ScheduleEntry | None:
    if not player_is_playing or current_media_id is None:
        return None
    active_entry = active_schedule_entry_at(schedule_entries, reference_time)
    if active_entry is None:
        return None
    entry, _ = active_entry
    if entry.media_id != current_media_id:
        return None
    return entry


def schedule_entry_palette_tokens(
    entry: ScheduleEntry,
    reference_time: datetime,
    *,
    current_entry_id: str | None,
) -> tuple[str, str] | None:
    if current_entry_id is not None and current_entry_id == entry.id:
        return "#2d6a4f", "#ffffff"
    if entry.status == SCHEDULE_STATUS_DISABLED:
        return "#f8d7da", "#842029"
    if entry.status == SCHEDULE_STATUS_FIRED and normalized_start(entry.start_at, reference_time) < reference_time:
        return "#d8f3dc", "#1b4332"
    if entry.status == SCHEDULE_STATUS_MISSED:
        return "#fff3cd", "#664d03"
    if entry.cron_id is not None:
        return "#ffd166", "#5f4b00"
    return None

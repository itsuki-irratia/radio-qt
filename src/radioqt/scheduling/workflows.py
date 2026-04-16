from __future__ import annotations

from datetime import date, datetime, timedelta

from ..models import CronEntry, ScheduleEntry
from .cron_runtime import refresh_cron_schedule_entries

DEFAULT_CRON_RUNTIME_LOOKBACK = timedelta(hours=1)
DEFAULT_CRON_RUNTIME_MAX_OCCURRENCES = 100
DEFAULT_CRON_RUNTIME_MAX_RECENT_OCCURRENCES = 20


def enforce_hard_sync_always(
    cron_entries: list[CronEntry],
    schedule_entries: list[ScheduleEntry],
) -> bool:
    changed = False
    for cron_entry in cron_entries:
        if cron_entry.hard_sync:
            continue
        cron_entry.hard_sync = True
        changed = True

    for schedule_entry in schedule_entries:
        if schedule_entry.hard_sync and schedule_entry.cron_hard_sync_override is None:
            continue
        schedule_entry.hard_sync = True
        schedule_entry.cron_hard_sync_override = None
        changed = True
    return changed


def is_schedule_entry_protected_from_removal(
    entry: ScheduleEntry,
    cron_entries_by_id: dict[str, CronEntry],
) -> bool:
    if entry.cron_id is None:
        return False
    cron_entry = cron_entries_by_id.get(entry.cron_id)
    return cron_entry is not None and cron_entry.enabled


def sync_cron_runtime_window(
    schedule_entries: list[ScheduleEntry],
    cron_entries: list[CronEntry],
    *,
    target_dates: set[date] | None,
    now: datetime,
    runtime_lookback: timedelta = DEFAULT_CRON_RUNTIME_LOOKBACK,
    max_occurrences: int = DEFAULT_CRON_RUNTIME_MAX_OCCURRENCES,
    max_recent_occurrences: int = DEFAULT_CRON_RUNTIME_MAX_RECENT_OCCURRENCES,
) -> list[ScheduleEntry]:
    return refresh_cron_schedule_entries(
        schedule_entries,
        cron_entries,
        target_dates=target_dates,
        now=now,
        runtime_lookback=runtime_lookback,
        max_occurrences=max_occurrences,
        max_recent_occurrences=max_recent_occurrences,
    )

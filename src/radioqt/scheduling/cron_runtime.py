from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from ..cron import CronExpression, CronParseError
from ..models import (
    CronEntry,
    ScheduleEntry,
    SCHEDULE_STATUS_DISABLED,
    SCHEDULE_STATUS_FIRED,
    SCHEDULE_STATUS_MISSED,
    SCHEDULE_STATUS_PENDING,
)
from .logic import normalized_start


def cron_occurrence_entry_id(cron_id: str, start_at: datetime) -> str:
    return str(uuid5(NAMESPACE_URL, f"radioqt-cron:{cron_id}:{start_at.isoformat()}"))


def next_cron_occurrence(cron_entry: CronEntry, start: datetime) -> datetime | None:
    try:
        expression = CronExpression.parse(cron_entry.expression)
    except CronParseError:
        return None
    return expression.next_at_or_after(start)


def apply_cron_entry_defaults(entry: ScheduleEntry, cron_entry: CronEntry) -> None:
    entry.media_id = cron_entry.media_id
    entry.one_shot = True
    entry.cron_id = cron_entry.id
    entry.hard_sync = True
    entry.cron_hard_sync_override = None
    if entry.cron_fade_in_override is None:
        entry.fade_in = cron_entry.fade_in
    else:
        entry.fade_in = entry.cron_fade_in_override
    if entry.cron_fade_out_override is None:
        entry.fade_out = cron_entry.fade_out
    else:
        entry.fade_out = entry.cron_fade_out_override

    if entry.status in {SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED}:
        return
    if not cron_entry.enabled:
        entry.status = SCHEDULE_STATUS_DISABLED
        return
    entry.status = entry.cron_status_override or SCHEDULE_STATUS_PENDING


def refresh_cron_schedule_entries(
    schedule_entries: list[ScheduleEntry],
    cron_entries: list[CronEntry],
    *,
    target_dates: set[date] | None,
    now: datetime,
    runtime_lookback: timedelta,
    max_occurrences: int,
    max_recent_occurrences: int,
) -> list[ScheduleEntry]:
    runtime_dates = set(target_dates) if target_dates else None
    cron_entries_by_id = {entry.id: entry for entry in cron_entries}
    refreshed_entries: list[ScheduleEntry] = []
    for entry in schedule_entries:
        if entry.cron_id is None:
            refreshed_entries.append(entry)
            continue

        if runtime_dates is not None:
            entry_date = normalized_start(entry.start_at, now).date()
            if entry_date not in runtime_dates:
                # Keep runtime memory bounded to the configured CRON window.
                continue

        cron_entry = cron_entries_by_id.get(entry.cron_id)
        if cron_entry is None:
            if entry.status in {SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED}:
                refreshed_entries.append(entry)
            continue
        apply_cron_entry_defaults(entry, cron_entry)
        refreshed_entries.append(entry)

    if not runtime_dates:
        return refreshed_entries

    existing_by_id = {entry.id: entry for entry in refreshed_entries if entry.cron_id is not None}
    lookback_start = now - runtime_lookback
    timezone = now.tzinfo
    occurrence_candidates: list[tuple[datetime, CronEntry]] = []
    for cron_entry in cron_entries:
        if not cron_entry.enabled:
            continue
        try:
            expression = CronExpression.parse(cron_entry.expression)
        except CronParseError:
            continue
        for target_date in sorted(runtime_dates):
            for start_at in expression.iter_datetimes_on_date(target_date, timezone):
                if start_at < lookback_start:
                    continue
                occurrence_candidates.append((start_at, cron_entry))

    occurrence_candidates.sort(key=lambda item: item[0])
    past_occurrences = [item for item in occurrence_candidates if item[0] <= now]
    future_occurrences = [item for item in occurrence_candidates if item[0] > now]

    selected_recent = past_occurrences[-max_recent_occurrences:]
    remaining_capacity = max(0, max_occurrences - len(selected_recent))
    selected_occurrences = selected_recent + future_occurrences[:remaining_capacity]

    if len(selected_occurrences) < max_occurrences:
        extra_needed = max_occurrences - len(selected_occurrences)
        older_past = past_occurrences[: max(0, len(past_occurrences) - len(selected_recent))]
        selected_occurrences = older_past[-extra_needed:] + selected_occurrences

    selected_occurrences.sort(key=lambda item: item[0])
    selected_entry_ids: set[str] = set()
    for start_at, cron_entry in selected_occurrences:
        entry_id = cron_occurrence_entry_id(cron_entry.id, start_at)
        selected_entry_ids.add(entry_id)
        entry = existing_by_id.get(entry_id)
        if entry is None:
            entry = ScheduleEntry(
                id=entry_id,
                media_id=cron_entry.media_id,
                start_at=start_at,
                hard_sync=True,
                fade_in=cron_entry.fade_in,
                fade_out=cron_entry.fade_out,
                status=SCHEDULE_STATUS_PENDING,
                one_shot=True,
                cron_id=cron_entry.id,
            )
            apply_cron_entry_defaults(entry, cron_entry)
            refreshed_entries.append(entry)
            existing_by_id[entry_id] = entry
            continue

        entry.start_at = start_at
        apply_cron_entry_defaults(entry, cron_entry)

    if selected_entry_ids:
        return [
            entry
            for entry in refreshed_entries
            if entry.cron_id is None or entry.id in selected_entry_ids
        ]
    return [entry for entry in refreshed_entries if entry.cron_id is None]

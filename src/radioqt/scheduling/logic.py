from __future__ import annotations

from datetime import datetime, timedelta

from ..models import (
    SCHEDULE_STATUS_DISABLED,
    SCHEDULE_STATUS_MISSED,
    SCHEDULE_STATUS_PENDING,
    ScheduleEntry,
)


def normalized_start(start_at: datetime, reference_time: datetime | None = None) -> datetime:
    if start_at.tzinfo is not None:
        return start_at
    if reference_time is None:
        reference_time = datetime.now().astimezone()
    return start_at.replace(tzinfo=reference_time.tzinfo)


def sort_schedule_entries(
    entries: list[ScheduleEntry],
    reference_time: datetime | None = None,
) -> list[ScheduleEntry]:
    return sorted(
        entries,
        key=lambda entry: normalized_start(entry.start_at, reference_time),
    )


def schedule_entry_at_exact_start(
    entries: list[ScheduleEntry],
    start_at: datetime,
    reference_time: datetime | None = None,
    *,
    exclude_entry_id: str | None = None,
) -> ScheduleEntry | None:
    normalized_target = normalized_start(start_at, reference_time)
    for entry in entries:
        if exclude_entry_id is not None and entry.id == exclude_entry_id:
            continue
        if normalized_start(entry.start_at, reference_time) == normalized_target:
            return entry
    return None


def schedule_entry_started_in_past(
    entry: ScheduleEntry,
    reference_time: datetime,
) -> bool:
    return normalized_start(entry.start_at, reference_time) < reference_time


def schedule_entry_end_at(
    entries: list[ScheduleEntry],
    index: int,
    reference_time: datetime | None = None,
) -> datetime | None:
    entry = entries[index]
    start_at = normalized_start(entry.start_at, reference_time)
    end_candidates: list[datetime] = []
    if entry.duration is not None:
        end_candidates.append(start_at + timedelta(seconds=max(0, entry.duration)))
    if index + 1 < len(entries):
        end_candidates.append(normalized_start(entries[index + 1].start_at, reference_time))
    if not end_candidates:
        return None
    return min(end_candidates)


def active_schedule_entry_at(
    entries: list[ScheduleEntry],
    now: datetime,
) -> tuple[ScheduleEntry, datetime] | None:
    sorted_entries = sort_schedule_entries(entries, now)
    for index, entry in enumerate(sorted_entries):
        start_at = normalized_start(entry.start_at, now)
        if now < start_at:
            break

        end_at = schedule_entry_end_at(sorted_entries, index, now)
        if end_at is not None and now >= end_at:
            continue
        if entry.status == SCHEDULE_STATUS_DISABLED:
            return None
        return entry, start_at
    return None


def schedule_entry_window_details(
    entries: list[ScheduleEntry],
    entry_id: str,
    reference_time: datetime | None = None,
) -> tuple[datetime, datetime | None, str]:
    sorted_entries = sort_schedule_entries(entries, reference_time)
    target_index: int | None = None
    target_entry: ScheduleEntry | None = None
    for index, current_entry in enumerate(sorted_entries):
        if current_entry.id == entry_id:
            target_index = index
            target_entry = current_entry
            break

    if target_entry is None or target_index is None:
        fallback_start = normalized_start(
            next(entry.start_at for entry in entries if entry.id == entry_id),
            reference_time,
        )
        return fallback_start, None, "Computed window unavailable"

    start_at = normalized_start(target_entry.start_at, reference_time)
    duration_end_at: datetime | None = None
    next_entry_start_at: datetime | None = None
    if target_entry.duration is not None:
        duration_end_at = start_at + timedelta(seconds=max(0, target_entry.duration))
    if target_index + 1 < len(sorted_entries):
        next_entry_start_at = normalized_start(sorted_entries[target_index + 1].start_at, reference_time)

    end_at = schedule_entry_end_at(sorted_entries, target_index, reference_time)
    if end_at is None:
        return start_at, None, "No duration and no next scheduled item"

    reason_parts = []
    if duration_end_at is not None and end_at == duration_end_at:
        reason_parts.append("media duration")
    if next_entry_start_at is not None and end_at == next_entry_start_at:
        reason_parts.append("next scheduled item")
    end_reason = " and ".join(reason_parts) if reason_parts else "computed schedule boundary"
    return start_at, end_at, end_reason


def normalize_overdue_one_shots(
    entries: list[ScheduleEntry],
    reference_time: datetime,
    eligible_statuses: set[str],
) -> list[tuple[ScheduleEntry, datetime, datetime]]:
    normalized_entries: list[tuple[ScheduleEntry, datetime, datetime]] = []
    sorted_entries = sort_schedule_entries(entries, reference_time)
    for index, entry in enumerate(sorted_entries):
        if not entry.one_shot:
            continue
        start_at = normalized_start(entry.start_at, reference_time)
        if start_at >= reference_time:
            continue
        if entry.status not in eligible_statuses:
            continue
        end_at = schedule_entry_end_at(sorted_entries, index, reference_time)
        if end_at is None or reference_time < end_at:
            continue
        entry.status = SCHEDULE_STATUS_MISSED
        normalized_entries.append((entry, start_at, end_at))
    return normalized_entries


def restore_active_missed_one_shots(
    entries: list[ScheduleEntry],
    reference_time: datetime,
) -> list[ScheduleEntry]:
    restored_entries: list[ScheduleEntry] = []
    sorted_entries = sort_schedule_entries(entries, reference_time)
    for index, entry in enumerate(sorted_entries):
        if not entry.one_shot or entry.status != SCHEDULE_STATUS_MISSED:
            continue
        start_at = normalized_start(entry.start_at, reference_time)
        if start_at > reference_time:
            continue
        end_at = schedule_entry_end_at(sorted_entries, index, reference_time)
        if end_at is None or reference_time >= end_at:
            continue
        entry.status = SCHEDULE_STATUS_PENDING
        restored_entries.append(entry)
    return restored_entries

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from ..models import (
    CronEntry,
    SCHEDULE_STATUS_DISABLED,
    SCHEDULE_STATUS_FIRED,
    SCHEDULE_STATUS_MISSED,
    SCHEDULE_STATUS_PENDING,
    ScheduleEntry,
)
from .logic import normalized_start


class ScheduleMutationError(ValueError):
    pass


@dataclass(slots=True)
class ScheduleStatusMutationResult:
    updated_entry: ScheduleEntry | None = None
    applied_value: str | None = None
    refresh_only: bool = False


@dataclass(slots=True)
class ScheduleRemovalSelection:
    entries_to_remove: list[ScheduleEntry]
    protected_entries: list[ScheduleEntry]


def create_schedule_entry(
    *,
    media_id: str,
    start_at: datetime,
    reference_time: datetime,
    fade_in: bool = False,
    fade_out: bool = False,
) -> ScheduleEntry:
    if normalized_start(start_at, reference_time) < reference_time:
        raise ScheduleMutationError("Cannot create a schedule entry in the past")
    entry = ScheduleEntry.create(
        media_id=media_id,
        start_at=start_at,
        hard_sync=True,
        fade_in=fade_in,
        fade_out=fade_out,
    )
    return entry


def schedule_entry_is_in_past(entry: ScheduleEntry, reference_time: datetime) -> bool:
    return normalized_start(entry.start_at, reference_time) < reference_time


def select_schedule_entries_for_removal(
    entries: list[ScheduleEntry],
    *,
    entry_ids: set[str],
    is_protected: Callable[[ScheduleEntry], bool],
) -> ScheduleRemovalSelection:
    entries_to_remove = [entry for entry in entries if entry.id in entry_ids]
    protected_entries = [entry for entry in entries_to_remove if is_protected(entry)]
    return ScheduleRemovalSelection(
        entries_to_remove=entries_to_remove,
        protected_entries=protected_entries,
    )


def remove_schedule_entries_by_ids(entries: list[ScheduleEntry], *, entry_ids: set[str]) -> list[ScheduleEntry]:
    return [entry for entry in entries if entry.id not in entry_ids]


def create_cron_entry(
    *,
    media_id: str,
    expression: str,
    fade_in: bool,
    fade_out: bool,
) -> CronEntry:
    return CronEntry.create(
        media_id=media_id,
        expression=expression,
        hard_sync=True,
        fade_in=fade_in,
        fade_out=fade_out,
    )


def update_cron_expression(cron_entry: CronEntry, *, expression: str) -> bool:
    updated_expression = expression.strip()
    if updated_expression == cron_entry.expression:
        return False
    cron_entry.expression = updated_expression
    return True


def remove_cron_and_generated_schedule_entries(
    cron_entries: list[CronEntry],
    schedule_entries: list[ScheduleEntry],
    *,
    cron_id: str,
) -> tuple[list[CronEntry], list[ScheduleEntry]]:
    remaining_cron_entries = [entry for entry in cron_entries if entry.id != cron_id]
    remaining_schedule_entries = [
        entry
        for entry in schedule_entries
        if entry.cron_id != cron_id or entry.status in {SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED}
    ]
    return remaining_cron_entries, remaining_schedule_entries


def update_schedule_fade_in(
    entries: list[ScheduleEntry],
    entry_id: str,
    *,
    fade_in_enabled: bool,
    reference_time: datetime,
    cron_entry_by_id: Callable[[str | None], CronEntry | None],
) -> ScheduleEntry | None:
    for entry in entries:
        if entry.id != entry_id:
            continue
        if schedule_entry_is_in_past(entry, reference_time):
            return None
        if entry.status in {SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED}:
            return None
        cron_entry = cron_entry_by_id(entry.cron_id)
        if cron_entry is not None:
            override_value = None if cron_entry.fade_in == fade_in_enabled else fade_in_enabled
            if entry.cron_fade_in_override == override_value and entry.fade_in == fade_in_enabled:
                return None
            entry.cron_fade_in_override = override_value
            entry.fade_in = fade_in_enabled
            return entry
        if entry.fade_in == fade_in_enabled:
            return None
        entry.fade_in = fade_in_enabled
        return entry
    return None


def update_schedule_fade_out(
    entries: list[ScheduleEntry],
    entry_id: str,
    *,
    fade_out_enabled: bool,
    reference_time: datetime,
    allow_past_entry: bool = False,
    cron_entry_by_id: Callable[[str | None], CronEntry | None],
) -> ScheduleEntry | None:
    for entry in entries:
        if entry.id != entry_id:
            continue
        if schedule_entry_is_in_past(entry, reference_time) and not allow_past_entry:
            return None
        if entry.status in {SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED}:
            return None
        cron_entry = cron_entry_by_id(entry.cron_id)
        if cron_entry is not None:
            override_value = None if cron_entry.fade_out == fade_out_enabled else fade_out_enabled
            if entry.cron_fade_out_override == override_value and entry.fade_out == fade_out_enabled:
                return None
            entry.cron_fade_out_override = override_value
            entry.fade_out = fade_out_enabled
            return entry
        if entry.fade_out == fade_out_enabled:
            return None
        entry.fade_out = fade_out_enabled
        return entry
    return None


def update_schedule_status(
    entries: list[ScheduleEntry],
    entry_id: str,
    *,
    value: str,
    reference_time: datetime,
    cron_entry_by_id: Callable[[str | None], CronEntry | None],
) -> ScheduleStatusMutationResult:
    applied_value = value
    for entry in entries:
        if entry.id != entry_id:
            continue
        if schedule_entry_is_in_past(entry, reference_time):
            return ScheduleStatusMutationResult()
        if entry.status in {SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED}:
            return ScheduleStatusMutationResult()
        cron_entry = cron_entry_by_id(entry.cron_id)
        if cron_entry is not None and not cron_entry.enabled:
            return ScheduleStatusMutationResult(refresh_only=True)
        new_status = SCHEDULE_STATUS_PENDING if value == "Pending" else SCHEDULE_STATUS_DISABLED
        if (
            new_status == SCHEDULE_STATUS_PENDING
            and entry.one_shot
            and normalized_start(entry.start_at, reference_time) < reference_time
        ):
            new_status = SCHEDULE_STATUS_MISSED
            applied_value = "Missed"
        if entry.status == new_status:
            return ScheduleStatusMutationResult()
        if cron_entry is not None:
            entry.cron_status_override = (
                SCHEDULE_STATUS_DISABLED if new_status == SCHEDULE_STATUS_DISABLED else None
            )
        entry.status = new_status
        return ScheduleStatusMutationResult(updated_entry=entry, applied_value=applied_value)
    return ScheduleStatusMutationResult()


def update_cron_fade_in(
    entries: list[CronEntry],
    cron_id: str,
    *,
    fade_in_enabled: bool,
) -> CronEntry | None:
    for entry in entries:
        if entry.id != cron_id:
            continue
        if entry.fade_in == fade_in_enabled:
            return None
        entry.fade_in = fade_in_enabled
        return entry
    return None


def update_cron_fade_out(
    entries: list[CronEntry],
    cron_id: str,
    *,
    fade_out_enabled: bool,
) -> CronEntry | None:
    for entry in entries:
        if entry.id != cron_id:
            continue
        if entry.fade_out == fade_out_enabled:
            return None
        entry.fade_out = fade_out_enabled
        return entry
    return None


def update_cron_enabled(
    entries: list[CronEntry],
    cron_id: str,
    *,
    enabled: bool,
) -> CronEntry | None:
    for entry in entries:
        if entry.id != cron_id:
            continue
        if entry.enabled == enabled:
            return None
        entry.enabled = enabled
        return entry
    return None

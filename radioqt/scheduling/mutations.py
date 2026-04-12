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


@dataclass(slots=True)
class ScheduleStatusMutationResult:
    updated_entry: ScheduleEntry | None = None
    applied_value: str | None = None
    refresh_only: bool = False


def update_schedule_fade_in(
    entries: list[ScheduleEntry],
    entry_id: str,
    *,
    fade_in_enabled: bool,
    cron_entry_by_id: Callable[[str | None], CronEntry | None],
) -> ScheduleEntry | None:
    for entry in entries:
        if entry.id != entry_id:
            continue
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
    cron_entry_by_id: Callable[[str | None], CronEntry | None],
) -> ScheduleEntry | None:
    for entry in entries:
        if entry.id != entry_id:
            continue
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

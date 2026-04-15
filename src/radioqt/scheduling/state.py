from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..models import SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_PENDING, ScheduleEntry
from .logic import normalize_overdue_one_shots, restore_active_missed_one_shots


@dataclass(slots=True)
class StartupSchedulePreparation:
    restored_count: int
    normalized_entries: list[tuple[ScheduleEntry, datetime, datetime]]


@dataclass(slots=True)
class PlaySchedulePreparation:
    restored_count: int
    normalized_entries: list[tuple[ScheduleEntry, datetime, datetime]]
    started_automation: bool


def prepare_schedule_entries_for_startup(
    entries: list[ScheduleEntry],
    reference_time: datetime,
) -> StartupSchedulePreparation:
    restored_entries = restore_active_missed_one_shots(entries, reference_time)
    normalized_entries = normalize_overdue_one_shots(
        entries,
        reference_time,
        {SCHEDULE_STATUS_PENDING, SCHEDULE_STATUS_FIRED},
    )
    return StartupSchedulePreparation(
        restored_count=len(restored_entries),
        normalized_entries=normalized_entries,
    )


def prepare_schedule_entries_for_play(
    entries: list[ScheduleEntry],
    reference_time: datetime,
    *,
    automation_playing: bool,
) -> PlaySchedulePreparation:
    restored_entries = restore_active_missed_one_shots(entries, reference_time)
    started_automation = not automation_playing
    normalized_entries: list[tuple[ScheduleEntry, datetime, datetime]] = []
    if started_automation:
        normalized_entries = normalize_overdue_one_shots(
            entries,
            reference_time,
            {SCHEDULE_STATUS_PENDING},
        )
    return PlaySchedulePreparation(
        restored_count=len(restored_entries),
        normalized_entries=normalized_entries,
        started_automation=started_automation,
    )

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from radioqt.models import CronEntry, ScheduleEntry
from radioqt.scheduling.workflows import (
    enforce_hard_sync_always,
    is_schedule_entry_protected_from_removal,
    sync_cron_runtime_window,
)


def test_enforce_hard_sync_always_sets_cron_and_schedule_flags() -> None:
    cron_entry = CronEntry.create(
        media_id="media-1",
        expression="0 */10 * * * *",
        hard_sync=False,
    )
    schedule_entry = ScheduleEntry.create(
        media_id="media-1",
        start_at=datetime.now(timezone.utc) + timedelta(hours=1),
        hard_sync=False,
    )
    schedule_entry.cron_hard_sync_override = False

    changed = enforce_hard_sync_always([cron_entry], [schedule_entry])

    assert changed is True
    assert cron_entry.hard_sync is True
    assert schedule_entry.hard_sync is True
    assert schedule_entry.cron_hard_sync_override is None


def test_is_schedule_entry_protected_from_removal_only_when_cron_enabled() -> None:
    cron_enabled = CronEntry.create(media_id="media-1", expression="0 */5 * * * *")
    cron_disabled = CronEntry.create(media_id="media-1", expression="0 */5 * * * *")
    cron_disabled.enabled = False

    schedule_enabled = ScheduleEntry.create(
        media_id="media-1",
        start_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    schedule_enabled.cron_id = cron_enabled.id

    schedule_disabled = ScheduleEntry.create(
        media_id="media-1",
        start_at=datetime.now(timezone.utc) + timedelta(minutes=20),
    )
    schedule_disabled.cron_id = cron_disabled.id

    assert (
        is_schedule_entry_protected_from_removal(
            schedule_enabled,
            {cron_enabled.id: cron_enabled},
        )
        is True
    )
    assert (
        is_schedule_entry_protected_from_removal(
            schedule_disabled,
            {cron_disabled.id: cron_disabled},
        )
        is False
    )


def test_sync_cron_runtime_window_keeps_regular_schedule_entries() -> None:
    now = datetime.now(timezone.utc)
    schedule_entry = ScheduleEntry.create(
        media_id="media-regular",
        start_at=now + timedelta(days=7),
    )

    synced_entries = sync_cron_runtime_window(
        [schedule_entry],
        [],
        target_dates={now.date(), (now + timedelta(days=1)).date()},
        now=now,
    )

    assert len(synced_entries) == 1
    assert synced_entries[0].id == schedule_entry.id

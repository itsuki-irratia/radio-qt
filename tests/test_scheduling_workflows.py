from __future__ import annotations

from datetime import datetime, timedelta, timezone

from radioqt.models import CronEntry, ScheduleEntry
from radioqt.scheduling.mutations import (
    ScheduleMutationError,
    create_schedule_entry,
    update_schedule_fade_in,
    update_schedule_fade_out,
    update_schedule_status,
)
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


def test_create_schedule_entry_rejects_past_start() -> None:
    now = datetime.now(timezone.utc)

    try:
        create_schedule_entry(
            media_id="media-1",
            start_at=now - timedelta(minutes=1),
            reference_time=now,
        )
    except ScheduleMutationError as exc:
        assert "past" in str(exc)
    else:
        raise AssertionError("Expected past schedule creation to be rejected")


def test_past_schedule_entry_cannot_be_changed_except_allowed_fade_out() -> None:
    now = datetime.now(timezone.utc)
    entry = ScheduleEntry.create(
        media_id="media-1",
        start_at=now - timedelta(minutes=1),
    )

    assert (
        update_schedule_fade_in(
            [entry],
            entry.id,
            fade_in_enabled=True,
            reference_time=now,
            cron_entry_by_id=lambda _cron_id: None,
        )
        is None
    )
    assert entry.fade_in is False

    assert (
        update_schedule_status(
            [entry],
            entry.id,
            value="Disabled",
            reference_time=now,
            cron_entry_by_id=lambda _cron_id: None,
        ).updated_entry
        is None
    )
    assert entry.status == "pending"

    assert (
        update_schedule_fade_out(
            [entry],
            entry.id,
            fade_out_enabled=True,
            reference_time=now,
            cron_entry_by_id=lambda _cron_id: None,
        )
        is None
    )
    assert entry.fade_out is False

    assert (
        update_schedule_fade_out(
            [entry],
            entry.id,
            fade_out_enabled=True,
            reference_time=now,
            allow_past_entry=True,
            cron_entry_by_id=lambda _cron_id: None,
        )
        is entry
    )
    assert entry.fade_out is True

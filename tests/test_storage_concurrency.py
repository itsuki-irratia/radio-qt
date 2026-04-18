from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from radioqt.models import AppState, MediaItem, ScheduleEntry
from radioqt.storage import (
    load_state,
    load_state_with_version,
    save_state,
    state_version,
    StateVersionConflictError,
)


def test_save_state_rejects_stale_expected_version(tmp_path) -> None:
    state_path = tmp_path / "db.sqlite"
    save_state(state_path, AppState())

    snapshot_a = load_state_with_version(state_path)
    snapshot_b = load_state_with_version(state_path)

    snapshot_a.state.media_items.append(MediaItem.create(title="A", source="/tmp/a.mp4"))
    save_state(
        state_path,
        snapshot_a.state,
        expected_version=snapshot_a.version,
    )

    snapshot_b.state.media_items.append(MediaItem.create(title="B", source="/tmp/b.mp4"))
    with pytest.raises(StateVersionConflictError):
        save_state(
            state_path,
            snapshot_b.state,
            expected_version=snapshot_b.version,
        )

    latest_state = load_state(state_path)
    assert len(latest_state.media_items) == 1
    assert latest_state.media_items[0].title == "A"


def test_state_version_increments_on_successful_save(tmp_path) -> None:
    state_path = tmp_path / "db.sqlite"
    assert state_version(state_path) == 0

    version_after_first_save = save_state(state_path, AppState())
    assert version_after_first_save == 1
    assert state_version(state_path) == 1

    snapshot = load_state_with_version(state_path)
    snapshot.state.media_items.append(MediaItem.create(title="Track", source="/tmp/track.mp3"))
    version_after_second_save = save_state(
        state_path,
        snapshot.state,
        expected_version=snapshot.version,
    )
    assert version_after_second_save == 2
    assert state_version(state_path) == 2


def test_save_state_exports_schedule_by_day_files(tmp_path) -> None:
    state_path = tmp_path / "db.sqlite"
    local_tz = datetime.now().astimezone().tzinfo
    assert local_tz is not None
    day_one = datetime(2026, 4, 18, 9, 0, tzinfo=local_tz)
    day_two = day_one + timedelta(days=1)

    media = MediaItem.create(title="Morning Show", source="/tmp/morning.mp3")
    state = AppState(
        media_items=[media],
        schedule_entries=[
            ScheduleEntry.create(media_id=media.id, start_at=day_one),
            ScheduleEntry.create(media_id=media.id, start_at=day_two),
        ],
    )

    save_state(state_path, state)

    export_day_one_path = tmp_path / "export" / "2026" / "2026-04-18.json"
    export_day_two_path = tmp_path / "export" / "2026" / "2026-04-19.json"
    assert export_day_one_path.is_file()
    assert export_day_two_path.is_file()

    day_one_payload = json.loads(export_day_one_path.read_text(encoding="utf-8"))
    day_two_payload = json.loads(export_day_two_path.read_text(encoding="utf-8"))
    assert day_one_payload["date"] == "2026-04-18"
    assert day_two_payload["date"] == "2026-04-19"
    assert day_one_payload["entry_count"] == 1
    assert day_one_payload["entries"][0]["media"]["title"] == "Morning Show"


def test_save_state_schedule_export_updates_metadata_and_removes_stale_day_file(tmp_path) -> None:
    state_path = tmp_path / "db.sqlite"
    local_tz = datetime.now().astimezone().tzinfo
    assert local_tz is not None
    day_one = datetime(2026, 4, 18, 10, 0, tzinfo=local_tz)
    day_two = day_one + timedelta(days=1)

    media = MediaItem.create(title="Original Title", source="/tmp/show.mp3")
    entry_day_one = ScheduleEntry.create(media_id=media.id, start_at=day_one)
    entry_day_two = ScheduleEntry.create(media_id=media.id, start_at=day_two)
    save_state(
        state_path,
        AppState(media_items=[media], schedule_entries=[entry_day_one, entry_day_two]),
    )

    media.title = "Updated Title"
    save_state(
        state_path,
        AppState(media_items=[media], schedule_entries=[entry_day_one]),
    )

    export_day_one_path = tmp_path / "export" / "2026" / "2026-04-18.json"
    export_day_two_path = tmp_path / "export" / "2026" / "2026-04-19.json"
    assert export_day_one_path.is_file()
    assert not export_day_two_path.exists()

    day_one_payload = json.loads(export_day_one_path.read_text(encoding="utf-8"))
    assert day_one_payload["entries"][0]["media"]["title"] == "Updated Title"


def test_save_state_schedule_export_includes_local_file_metadata(tmp_path) -> None:
    state_path = tmp_path / "db.sqlite"
    local_tz = datetime.now().astimezone().tzinfo
    assert local_tz is not None
    start_at = datetime(2026, 4, 18, 11, 0, tzinfo=local_tz)
    media_path = tmp_path / "audio.mp3"
    media_path.write_bytes(b"ID3")

    media = MediaItem.create(title="Local File", source=str(media_path))
    save_state(
        state_path,
        AppState(
            media_items=[media],
            schedule_entries=[ScheduleEntry.create(media_id=media.id, start_at=start_at)],
        ),
    )

    export_path = tmp_path / "export" / "2026" / "2026-04-18.json"
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    file_metadata = payload["entries"][0]["media"]["local_file_metadata"]
    assert file_metadata["available"] is True
    assert file_metadata["path"] == str(media_path.resolve())
    assert file_metadata["size_bytes"] == 3
    assert isinstance(file_metadata["modified_at"], str)

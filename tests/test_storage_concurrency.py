from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from radioqt.app_config import AppConfig, ExportPathMapping, save_app_config
from radioqt.models import AppState, MediaItem, ScheduleEntry
from radioqt.storage import schedule_export
from radioqt.storage import (
    load_state,
    load_state_with_version,
    save_state,
    state_version,
    StateVersionConflictError,
)
from radioqt.storage import io as storage_io


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


def test_save_state_allows_custom_schedule_export_handler(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_path = tmp_path / "db.sqlite"
    media = MediaItem.create(title="Callback", source="/tmp/callback.mp3")
    state = AppState(media_items=[media], schedule_entries=[])

    sync_export_called = False
    callback_payload: dict[str, object] = {}

    def _fake_sync_export(*args, **kwargs):
        del args, kwargs
        nonlocal sync_export_called
        sync_export_called = True
        return schedule_export.ScheduleExportResult()

    def _capture_export(config_dir, previous_state, current_state):
        callback_payload["config_dir"] = config_dir
        callback_payload["previous_state"] = previous_state
        callback_payload["current_state"] = current_state

    monkeypatch.setattr(storage_io, "export_schedule_incremental", _fake_sync_export)

    save_state(
        state_path,
        state,
        on_schedule_export=_capture_export,
    )

    assert sync_export_called is False
    assert callback_payload["config_dir"] == tmp_path
    previous_state = callback_payload["previous_state"]
    current_state = callback_payload["current_state"]
    assert isinstance(previous_state, AppState)
    assert isinstance(current_state, AppState)
    assert current_state is not state
    assert len(previous_state.media_items) == 0
    assert [item.title for item in current_state.media_items] == ["Callback"]


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
    assert day_one_payload["entries"][0]["media"]["metadata"]["title"] == "Morning Show"


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
    assert day_one_payload["entries"][0]["media"]["metadata"]["title"] == "Updated Title"


def test_save_state_schedule_export_includes_media_metadata_and_file_info(tmp_path) -> None:
    state_path = tmp_path / "db.sqlite"
    local_tz = datetime.now().astimezone().tzinfo
    assert local_tz is not None
    start_at = datetime(2026, 4, 18, 11, 0, tzinfo=local_tz)
    media_path = tmp_path / "audio.mp3"
    media_path.write_bytes(b"ID3")
    save_app_config(
        tmp_path / "settings.yaml",
        AppConfig(
            export_path_mappings=[
                ExportPathMapping(
                    from_prefix=str(tmp_path),
                    to_prefix="/media",
                )
            ]
        ),
    )

    media = MediaItem.create(title="Local File", source=str(media_path))
    save_state(
        state_path,
        AppState(
            media_items=[media],
            schedule_entries=[ScheduleEntry.create(media_id=media.id, start_at=start_at)],
        ),
    )

    export_path = tmp_path / "export" / "2026" / "2026-04-18.json"
    payload_text = export_path.read_text(encoding="utf-8")
    payload = json.loads(payload_text)
    entry_payload = payload["entries"][0]
    media_payload = payload["entries"][0]["media"]
    metadata = media_payload["metadata"]
    file_info = media_payload["file_info"]
    audio_info = file_info["audio"]
    video_info = file_info["video"]
    expected_public_path = "/media/audio.mp3"
    assert "local_file_metadata" not in media_payload
    assert "source" not in media_payload
    assert "missing" not in media_payload
    assert "created_at" not in media_payload
    assert "greenwich_time_signal_enabled" not in media_payload
    assert "title" not in media_payload
    assert "path" not in media_payload
    assert "cron_id" not in entry_payload
    assert "duration_seconds" not in entry_payload
    assert "fade_in" not in entry_payload
    assert "fade_out" not in entry_payload
    assert "hard_sync" not in entry_payload
    assert "one_shot" not in entry_payload
    assert file_info["path"] == expected_public_path
    assert file_info["size_bytes"] == 3
    assert isinstance(file_info["duration_seconds"], (float, type(None)))
    assert metadata["title"] == "Local File"
    assert metadata["artist"] == ""
    assert metadata["album"] == ""
    assert metadata["genre"] == ""
    assert metadata["track"] == ""
    assert metadata["date"] == ""
    assert metadata["comment"] == ""
    assert metadata["copyright"] == ""
    assert "album_artist" not in metadata
    assert "composer" not in metadata
    assert "description" not in metadata
    assert "disc" not in metadata
    assert "encoder" not in metadata
    assert "language" not in metadata
    assert "lyrics" not in metadata
    assert "publisher" not in metadata
    assert "technical_metadata" not in media_payload.get("metadata", {})
    assert "editable_metadata" not in media_payload.get("metadata", {})
    assert set(audio_info.keys()) == {"channels", "codec", "sample_rate", "bit_rate"}
    assert set(video_info.keys()) == {"codec", "bit_rate"}
    assert isinstance(audio_info["codec"], str)
    assert isinstance(video_info["codec"], str)
    assert str(media_path.resolve()) not in payload_text


def test_save_state_schedule_export_uses_original_path_without_mapping(tmp_path) -> None:
    state_path = tmp_path / "db.sqlite"
    local_tz = datetime.now().astimezone().tzinfo
    assert local_tz is not None
    start_at = datetime(2026, 4, 18, 11, 30, tzinfo=local_tz)
    media_path = tmp_path / "no-mapping.mp3"
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
    media_payload = payload["entries"][0]["media"]
    file_info = media_payload["file_info"]
    expected_original_path = str(media_path.resolve())
    assert "path" not in media_payload
    assert file_info["path"] == expected_original_path
    assert file_info["size_bytes"] == 3
    assert "duration_seconds" in file_info
    assert "audio" in file_info
    assert "video" in file_info
    assert "title" not in media_payload


def test_save_state_schedule_export_prefers_embedded_title_over_item_title(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "db.sqlite"
    local_tz = datetime.now().astimezone().tzinfo
    assert local_tz is not None
    start_at = datetime(2026, 4, 18, 12, 0, tzinfo=local_tz)
    media_path = tmp_path / "title-conflict.mp3"
    media_path.write_bytes(b"ID3")

    def _fake_ffprobe_payload(path):
        del path
        return {
            "format": {
                "duration": "42.5",
                "bit_rate": "128000",
                "tags": {
                    "title": "Embedded File Title",
                },
            },
            "streams": [],
        }

    monkeypatch.setattr(schedule_export, "_serialize_ffprobe_payload", _fake_ffprobe_payload)

    media = MediaItem.create(title="Library Item Title", source=str(media_path))
    save_state(
        state_path,
        AppState(
            media_items=[media],
            schedule_entries=[ScheduleEntry.create(media_id=media.id, start_at=start_at)],
        ),
    )

    export_path = tmp_path / "export" / "2026" / "2026-04-18.json"
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    metadata = payload["entries"][0]["media"]["metadata"]
    assert metadata["title"] == "Embedded File Title"


def test_save_state_schedule_export_starts_from_today(tmp_path) -> None:
    state_path = tmp_path / "db.sqlite"
    local_tz = datetime.now().astimezone().tzinfo
    assert local_tz is not None
    today_local = datetime.now().astimezone().date()
    day_past = datetime(
        today_local.year, today_local.month, today_local.day, 9, 0, tzinfo=local_tz
    ) - timedelta(days=1)
    day_today = day_past + timedelta(days=1)
    day_future = day_today + timedelta(days=1)

    media = MediaItem.create(title="Today Forward", source="/tmp/today-forward.mp3")
    state = AppState(
        media_items=[media],
        schedule_entries=[
            ScheduleEntry.create(media_id=media.id, start_at=day_past),
            ScheduleEntry.create(media_id=media.id, start_at=day_today),
            ScheduleEntry.create(media_id=media.id, start_at=day_future),
        ],
    )

    save_state(state_path, state)

    past_day_key = day_past.astimezone().date().isoformat()
    today_day_key = day_today.astimezone().date().isoformat()
    future_day_key = day_future.astimezone().date().isoformat()
    past_export_path = tmp_path / "export" / past_day_key[:4] / f"{past_day_key}.json"
    today_export_path = tmp_path / "export" / today_day_key[:4] / f"{today_day_key}.json"
    future_export_path = tmp_path / "export" / future_day_key[:4] / f"{future_day_key}.json"

    assert not past_export_path.exists()
    assert today_export_path.is_file()
    assert future_export_path.is_file()


def test_export_schedule_day_keys_refreshes_only_target_days(tmp_path) -> None:
    local_tz = datetime.now().astimezone().tzinfo
    assert local_tz is not None
    today_local = datetime.now().astimezone().date()
    day_today = datetime(today_local.year, today_local.month, today_local.day, 9, 0, tzinfo=local_tz)
    day_tomorrow = day_today + timedelta(days=1)
    day_plus_two = day_today + timedelta(days=2)

    media_a = MediaItem.create(title="A", source="/tmp/a.mp3")
    media_b = MediaItem.create(title="B", source="/tmp/b.mp3")
    state = AppState(
        media_items=[media_a, media_b],
        schedule_entries=[
            ScheduleEntry.create(media_id=media_a.id, start_at=day_today),
            ScheduleEntry.create(media_id=media_b.id, start_at=day_tomorrow),
            ScheduleEntry.create(media_id=media_a.id, start_at=day_plus_two),
        ],
    )

    target_day_keys = {
        day_today.astimezone().date().isoformat(),
        day_plus_two.astimezone().date().isoformat(),
    }
    result = schedule_export.export_schedule_day_keys(
        tmp_path,
        state=state,
        day_keys=target_day_keys,
    )

    today_export_path = tmp_path / "export" / str(today_local.year) / f"{day_today.date().isoformat()}.json"
    tomorrow_export_path = tmp_path / "export" / str(today_local.year) / f"{day_tomorrow.date().isoformat()}.json"
    plus_two_export_path = tmp_path / "export" / str(today_local.year) / f"{day_plus_two.date().isoformat()}.json"
    assert result.updated_count == 2
    assert today_export_path.is_file()
    assert not tomorrow_export_path.exists()
    assert plus_two_export_path.is_file()

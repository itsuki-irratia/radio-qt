from __future__ import annotations

import json

from radioqt.cli.app import run
from radioqt.models import AppState, MediaItem
from radioqt.storage.io import load_state, save_state


def test_cli_help_returns_zero(capsys) -> None:
    try:
        run(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("Expected SystemExit(0) when requesting --help")
    captured = capsys.readouterr()
    assert "radioqt-cli" in captured.out


def test_media_list_empty_state(tmp_path, capsys) -> None:
    exit_code = run(["--config", str(tmp_path), "media", "list"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "No media items found." in captured.out


def test_media_list_json_output(tmp_path, capsys) -> None:
    state_path = tmp_path / "db.sqlite"
    media = MediaItem.create(title="Test Media", source="/tmp/test.mp4")
    save_state(
        state_path,
        AppState(
            media_items=[media],
        ),
    )
    exit_code = run(["--json", "--config", str(tmp_path), "media", "list"])
    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["media"][0]["id"] == media.id


def test_media_add_persists_item(tmp_path) -> None:
    state_path = tmp_path / "db.sqlite"
    save_state(state_path, AppState())

    exit_code = run(
        [
            "--config",
            str(tmp_path),
            "media",
            "add",
            "--source",
            "/tmp/test-file.mp4",
            "--title",
            "My Test File",
        ]
    )
    assert exit_code == 2

    test_media_file = tmp_path / "test-file.mp4"
    test_media_file.write_text("placeholder", encoding="utf-8")
    exit_code = run(
        [
            "--config",
            str(tmp_path),
            "media",
            "add",
            "--source",
            str(test_media_file),
            "--title",
            "My Test File",
        ]
    )
    assert exit_code == 0

    loaded = load_state(state_path)
    assert len(loaded.media_items) == 1
    media_item = loaded.media_items[0]
    assert media_item.title == "My Test File"
    assert media_item.source == str(test_media_file.resolve())


def test_schedule_add_requires_existing_media(tmp_path, capsys) -> None:
    exit_code = run(
        [
            "--config",
            str(tmp_path),
            "schedule",
            "add",
            "--media-id",
            "missing-media-id",
            "--start",
            "2099-01-01T10:00:00+00:00",
        ]
    )
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "does not exist" in captured.err


def test_schedule_add_persists_entry(tmp_path) -> None:
    state_path = tmp_path / "db.sqlite"
    media = MediaItem.create(title="Test Media", source="file:///tmp/test.mp3")
    save_state(
        state_path,
        AppState(
            media_items=[media],
        ),
    )

    exit_code = run(
        [
            "--config",
            str(tmp_path),
            "schedule",
            "add",
            "--media-id",
            media.id,
            "--start",
            "2099-01-01T10:00:00+00:00",
            "--fade-in",
        ]
    )
    assert exit_code == 0

    loaded = load_state(state_path)
    assert len(loaded.schedule_entries) == 1
    entry = loaded.schedule_entries[0]
    assert entry.media_id == media.id
    assert entry.fade_in is True


def test_schedule_bulk_add_persists_entries(tmp_path) -> None:
    state_path = tmp_path / "db.sqlite"
    media = MediaItem.create(title="Test Media", source="file:///tmp/test.mp3")
    save_state(
        state_path,
        AppState(
            media_items=[media],
        ),
    )

    exit_code = run(
        [
            "--config",
            str(tmp_path),
            "schedule",
            "bulk-add",
            "--media-id",
            media.id,
            "--start",
            "2099-01-01T10:00:00+00:00",
            "--start",
            "2099-01-01T10:05:00+00:00",
        ]
    )
    assert exit_code == 0

    loaded = load_state(state_path)
    assert len(loaded.schedule_entries) == 2
    assert {entry.media_id for entry in loaded.schedule_entries} == {media.id}


def test_schedule_bulk_status_updates_entries_by_date(tmp_path) -> None:
    state_path = tmp_path / "db.sqlite"
    media = MediaItem.create(title="Test Media", source="file:///tmp/test.mp3")
    save_state(
        state_path,
        AppState(
            media_items=[media],
        ),
    )

    run(
        [
            "--config",
            str(tmp_path),
            "schedule",
            "bulk-add",
            "--media-id",
            media.id,
            "--start",
            "2099-01-01T10:00:00+00:00",
            "--start",
            "2099-01-01T10:05:00+00:00",
        ]
    )
    exit_code = run(
        [
            "--config",
            str(tmp_path),
            "schedule",
            "bulk-status",
            "--date",
            "2099-01-01",
            "--status",
            "disabled",
        ]
    )
    assert exit_code == 0

    loaded = load_state(state_path)
    assert len(loaded.schedule_entries) == 2
    assert {entry.status for entry in loaded.schedule_entries} == {"disabled"}


def test_cron_add_invalid_expression(tmp_path, capsys) -> None:
    state_path = tmp_path / "db.sqlite"
    media = MediaItem.create(title="Test Media", source="file:///tmp/test.mp3")
    save_state(
        state_path,
        AppState(
            media_items=[media],
        ),
    )

    exit_code = run(
        [
            "--config",
            str(tmp_path),
            "cron",
            "add",
            "--media-id",
            media.id,
            "--expression",
            "bad-cron",
        ]
    )
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "Cron expression must have 6 fields" in captured.err


def test_cron_add_persists_entry(tmp_path) -> None:
    state_path = tmp_path / "db.sqlite"
    media = MediaItem.create(title="Test Media", source="file:///tmp/test.mp3")
    save_state(
        state_path,
        AppState(
            media_items=[media],
        ),
    )

    exit_code = run(
        [
            "--config",
            str(tmp_path),
            "cron",
            "add",
            "--media-id",
            media.id,
            "--expression",
            "0 */15 * * * *",
            "--enabled",
            "false",
        ]
    )
    assert exit_code == 0

    loaded = load_state(state_path)
    assert len(loaded.cron_entries) == 1
    cron_entry = loaded.cron_entries[0]
    assert cron_entry.media_id == media.id
    assert cron_entry.enabled is False

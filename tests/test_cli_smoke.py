from __future__ import annotations

import json
import subprocess

from radioqt.app_config import load_app_config
from radioqt.cli.app import run
from radioqt.models import AppState, MediaItem
from radioqt.runtime_control import drain_runtime_control_commands
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


def test_settings_get_json_defaults(tmp_path, capsys) -> None:
    exit_code = run(["--json", "--config", str(tmp_path), "settings", "get"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["settings"]["fade_seconds"] == 5
    assert payload["settings"]["default_volume_percent"] == 100


def test_settings_set_persists_fade_and_volume(tmp_path, capsys) -> None:
    fade_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "fade-seconds",
            "9",
        ]
    )
    assert fade_exit == 0
    volume_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "audio.default-volume-percent",
            "73",
        ]
    )
    assert volume_exit == 0
    capsys.readouterr()

    get_exit = run(
        [
            "--json",
            "--config",
            str(tmp_path),
            "settings",
            "get",
            "fade_seconds",
        ]
    )
    assert get_exit == 0
    get_payload = json.loads(capsys.readouterr().out)
    assert get_payload["value"] == 9

    app_config = load_app_config(tmp_path / "settings.yaml")
    assert app_config.fade_in_duration_seconds == 9
    assert app_config.fade_out_duration_seconds == 9
    assert app_config.default_volume_percent == 73


def test_settings_set_rejects_unknown_key(tmp_path, capsys) -> None:
    exit_code = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "unknown_key",
            "value",
        ]
    )
    assert exit_code == 2
    assert "Unknown settings key" in capsys.readouterr().err


def test_settings_set_supported_extensions_and_library_tabs(tmp_path) -> None:
    extensions_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "supported_extensions",
            "mp3,ogg,webm",
        ]
    )
    assert extensions_exit == 0
    tabs_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "library_tabs",
            '[{"title":"Studio","path":"/tmp/studio"},{"title":"Ads","path":"/tmp/ads"}]',
        ]
    )
    assert tabs_exit == 0

    app_config = load_app_config(tmp_path / "settings.yaml")
    assert app_config.supported_extensions == ["mp3", "ogg", "webm"]
    assert [tab.title for tab in app_config.library_tabs] == ["Studio", "Ads"]
    assert [tab.path for tab in app_config.library_tabs] == ["/tmp/studio", "/tmp/ads"]


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


def test_streams_add_edit_remove_workflow(tmp_path) -> None:
    state_path = tmp_path / "db.sqlite"
    save_state(state_path, AppState())

    add_exit = run(
        [
            "--config",
            str(tmp_path),
            "streams",
            "add",
            "--source",
            "https://example.com/live.m3u8",
            "--title",
            "Live One",
            "--greenwich-time-signal",
            "true",
        ]
    )
    assert add_exit == 0
    loaded = load_state(state_path)
    assert len(loaded.media_items) == 1
    stream = loaded.media_items[0]
    assert stream.source == "https://example.com/live.m3u8"
    assert stream.greenwich_time_signal_enabled is True

    edit_exit = run(
        [
            "--config",
            str(tmp_path),
            "streams",
            "edit",
            stream.id,
            "--source",
            "https://example.com/live-hq.m3u8",
            "--title",
            "Live HQ",
            "--greenwich-time-signal",
            "false",
        ]
    )
    assert edit_exit == 0
    loaded_after_edit = load_state(state_path)
    assert loaded_after_edit.media_items[0].title == "Live HQ"
    assert loaded_after_edit.media_items[0].source == "https://example.com/live-hq.m3u8"
    assert loaded_after_edit.media_items[0].greenwich_time_signal_enabled is False

    remove_exit = run(
        [
            "--config",
            str(tmp_path),
            "streams",
            "remove",
            stream.id,
        ]
    )
    assert remove_exit == 0
    loaded_after_remove = load_state(state_path)
    assert loaded_after_remove.media_items == []


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


def test_runtime_status_json_defaults_offline(tmp_path, capsys) -> None:
    exit_code = run(["--json", "--config", str(tmp_path), "runtime", "status"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["effective_status"] == "offline"
    assert payload["pid"] is None


def test_runtime_set_status_requires_pid_when_online(tmp_path, capsys) -> None:
    exit_code = run(
        [
            "--config",
            str(tmp_path),
            "runtime",
            "set-status",
            "--value",
            "online",
        ]
    )
    assert exit_code == 2
    assert "provide --pid" in capsys.readouterr().err


def test_runtime_watch_once_json(tmp_path, capsys) -> None:
    exit_code = run(["--json", "--config", str(tmp_path), "runtime", "watch", "--once"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["effective_status"] == "offline"
    assert payload["lock_exists"] is False


def test_runtime_watch_rejects_invalid_interval(tmp_path, capsys) -> None:
    exit_code = run(
        [
            "--config",
            str(tmp_path),
            "runtime",
            "watch",
            "--interval",
            "0",
            "--once",
        ]
    )
    assert exit_code == 2
    assert "Interval must be greater than zero" in capsys.readouterr().err


def test_runtime_fade_in_requires_running_runtime(tmp_path, capsys) -> None:
    exit_code = run(["--config", str(tmp_path), "runtime", "fade-in"])
    assert exit_code == 2
    assert "GUI runtime is not running" in capsys.readouterr().err


def test_runtime_fade_commands_enqueue_control_messages(tmp_path, capsys) -> None:
    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        set_status_exit = run(
            [
                "--config",
                str(tmp_path),
                "runtime",
                "set-status",
                "--value",
                "online",
                "--pid",
                str(sleeper.pid),
            ]
        )
        assert set_status_exit == 0
        capsys.readouterr()

        fade_in_exit = run(
            ["--json", "--config", str(tmp_path), "runtime", "fade-in"]
        )
        assert fade_in_exit == 0
        fade_in_payload = json.loads(capsys.readouterr().out)
        assert fade_in_payload["queued"] is True
        assert fade_in_payload["action"] == "fade_in"

        fade_out_exit = run(
            ["--json", "--config", str(tmp_path), "runtime", "fade-out"]
        )
        assert fade_out_exit == 0
        fade_out_payload = json.loads(capsys.readouterr().out)
        assert fade_out_payload["queued"] is True
        assert fade_out_payload["action"] == "fade_out"

        commands = drain_runtime_control_commands(tmp_path)
        assert [command.action for command in commands] == ["fade_in", "fade_out"]
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait(timeout=5)


def test_runtime_online_offline_enqueue_control_messages(tmp_path, capsys) -> None:
    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        set_status_exit = run(
            [
                "--config",
                str(tmp_path),
                "runtime",
                "set-status",
                "--value",
                "online",
                "--pid",
                str(sleeper.pid),
            ]
        )
        assert set_status_exit == 0
        capsys.readouterr()

        online_exit = run(
            ["--json", "--config", str(tmp_path), "runtime", "online"]
        )
        assert online_exit == 0
        online_payload = json.loads(capsys.readouterr().out)
        assert online_payload["queued"] is True
        assert online_payload["action"] == "start_automation"

        offline_exit = run(
            ["--json", "--config", str(tmp_path), "runtime", "offline"]
        )
        assert offline_exit == 0
        offline_payload = json.loads(capsys.readouterr().out)
        assert offline_payload["queued"] is True
        assert offline_payload["action"] == "stop_automation"

        commands = drain_runtime_control_commands(tmp_path)
        assert [command.action for command in commands] == [
            "start_automation",
            "stop_automation",
        ]
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait(timeout=5)


def test_runtime_volume_rejects_out_of_range(tmp_path, capsys) -> None:
    exit_code = run(
        [
            "--config",
            str(tmp_path),
            "runtime",
            "volume",
            "--value",
            "101",
        ]
    )
    assert exit_code == 2
    assert "Volume must be between 0 and 100" in capsys.readouterr().err


def test_runtime_volume_and_mute_enqueue_set_volume_commands(tmp_path, capsys) -> None:
    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        set_status_exit = run(
            [
                "--config",
                str(tmp_path),
                "runtime",
                "set-status",
                "--value",
                "online",
                "--pid",
                str(sleeper.pid),
            ]
        )
        assert set_status_exit == 0
        capsys.readouterr()

        volume_exit = run(
            ["--json", "--config", str(tmp_path), "runtime", "volume", "--value", "65"]
        )
        assert volume_exit == 0
        volume_payload = json.loads(capsys.readouterr().out)
        assert volume_payload["queued"] is True
        assert volume_payload["action"] == "set_volume"
        assert volume_payload["value"] == 65

        mute_exit = run(
            ["--json", "--config", str(tmp_path), "runtime", "mute"]
        )
        assert mute_exit == 0
        mute_payload = json.loads(capsys.readouterr().out)
        assert mute_payload["queued"] is True
        assert mute_payload["action"] == "set_volume"
        assert mute_payload["value"] == 0

        commands = drain_runtime_control_commands(tmp_path)
        assert [command.action for command in commands] == ["set_volume", "set_volume"]
        assert [command.value for command in commands] == [65, 0]
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait(timeout=5)


def test_runtime_set_status_offline_keeps_lock_and_pid(tmp_path, capsys) -> None:
    run(
        [
            "--config",
            str(tmp_path),
            "runtime",
            "set-status",
            "--value",
            "online",
            "--pid",
            "1234",
        ]
    )
    capsys.readouterr()
    exit_code = run(
        [
            "--json",
            "--config",
            str(tmp_path),
            "runtime",
            "set-status",
            "--value",
            "offline",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "offline"
    assert payload["pid"] == 1234
    assert payload["lock_exists"] is True


def test_runtime_stop_terminates_pid_from_status_file(tmp_path, capsys) -> None:
    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        set_status_exit = run(
            [
                "--config",
                str(tmp_path),
                "runtime",
                "set-status",
                "--value",
                "online",
                "--pid",
                str(sleeper.pid),
            ]
        )
        assert set_status_exit == 0

        stop_exit = run(
            [
                "--config",
                str(tmp_path),
                "runtime",
                "stop",
                "--timeout",
                "2",
            ]
        )
        assert stop_exit == 0
        sleeper.wait(timeout=5)
        capsys.readouterr()

        status_exit = run(
            [
                "--json",
                "--config",
                str(tmp_path),
                "runtime",
                "status",
            ]
        )
        assert status_exit == 0
        status_payload = json.loads(capsys.readouterr().out)
        assert status_payload["effective_status"] == "offline"
        assert status_payload["lock_exists"] is False
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait(timeout=5)

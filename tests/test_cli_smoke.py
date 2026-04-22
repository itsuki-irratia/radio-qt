from __future__ import annotations

import json
from datetime import datetime, timedelta
import subprocess

from radioqt.app_config import load_app_config
from radioqt.cli.app import run
from radioqt.models import AppState, MediaItem, ScheduleEntry
from radioqt.runtime_control import drain_runtime_control_commands
from radioqt.runtime_logs import runtime_log_file_path
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
    assert payload["settings"]["icecast_status"] is False
    assert payload["settings"]["icecast_run_in_background"] is False
    assert payload["settings"]["icecast_command"].startswith("ffmpeg ")
    assert payload["settings"]["icecast_input_format"] == "pulse"
    assert payload["settings"]["icecast_thread_queue_size"] == 4096
    assert payload["settings"]["icecast_device"] != ""
    assert payload["settings"]["icecast_audio_channels"] == 2
    assert payload["settings"]["icecast_audio_rate"] == 48000
    assert payload["settings"]["icecast_audio_codec"] == "libmp3lame"
    assert payload["settings"]["icecast_audio_bitrate"] == 128
    assert payload["settings"]["icecast_content_type"] == "audio/mpeg"
    assert payload["settings"]["icecast_output_format"] == "mp3"
    assert payload["settings"]["icecast_url"].startswith("icecast://")
    assert payload["settings"]["export_path_mappings"] == []


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


def test_settings_set_export_path_mappings(tmp_path, capsys) -> None:
    set_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "export_path_mappings",
            '[{"from":"/home/zital","to":"/media"},{"from":"/mnt/radio","to":"https://example.com/media"}]',
        ]
    )
    assert set_exit == 0
    capsys.readouterr()

    get_exit = run(
        [
            "--json",
            "--config",
            str(tmp_path),
            "settings",
            "get",
            "export_path_mappings",
        ]
    )
    assert get_exit == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["value"] == [
        {"from": "/home/zital", "to": "/media"},
        {"from": "/mnt/radio", "to": "https://example.com/media"},
    ]

    app_config = load_app_config(tmp_path / "settings.yaml")
    assert [mapping.from_prefix for mapping in app_config.export_path_mappings] == [
        "/home/zital",
        "/mnt/radio",
    ]
    assert [mapping.to_prefix for mapping in app_config.export_path_mappings] == [
        "/media",
        "https://example.com/media",
    ]

    clear_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "export_path_mappings",
            "[]",
        ]
    )
    assert clear_exit == 0
    capsys.readouterr()

    get_cleared_exit = run(
        [
            "--json",
            "--config",
            str(tmp_path),
            "settings",
            "get",
            "export_path_mappings",
        ]
    )
    assert get_cleared_exit == 0
    cleared_payload = json.loads(capsys.readouterr().out)
    assert cleared_payload["value"] == []


def test_settings_set_icecast_command_and_status(tmp_path, capsys) -> None:
    set_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "icecast_command",
            "ffmpeg -f pulse -i monitor",
        ]
    )
    assert set_exit == 0
    status_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "icecast_status",
            "true",
        ]
    )
    assert status_exit == 0
    capsys.readouterr()

    get_exit = run(
        [
            "--json",
            "--config",
            str(tmp_path),
            "settings",
            "get",
            "icecast_command",
        ]
    )
    assert get_exit == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["value"] == "ffmpeg -f pulse -i monitor"

    get_status_exit = run(
        [
            "--json",
            "--config",
            str(tmp_path),
            "settings",
            "get",
            "icecast_status",
        ]
    )
    assert get_status_exit == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["value"] is True


def test_settings_set_icecast_run_in_background(tmp_path, capsys) -> None:
    set_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "icecast_run_in_background",
            "true",
        ]
    )
    assert set_exit == 0
    capsys.readouterr()

    get_exit = run(
        [
            "--json",
            "--config",
            str(tmp_path),
            "settings",
            "get",
            "icecast_run_in_background",
        ]
    )
    assert get_exit == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["value"] is True

    app_config = load_app_config(tmp_path / "settings.yaml")
    assert app_config.icecast_run_in_background is True


def test_settings_set_icecast_ffmpeg_params(tmp_path, capsys) -> None:
    queue_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "thread_queue_size",
            "8192",
        ]
    )
    assert queue_exit == 0
    bitrate_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "audio-bitrate",
            "192",
        ]
    )
    assert bitrate_exit == 0
    device_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "device",
            "alsa_output.test.monitor",
        ]
    )
    assert device_exit == 0
    capsys.readouterr()

    get_exit = run(
        [
            "--json",
            "--config",
            str(tmp_path),
            "settings",
            "get",
            "icecast_audio_bitrate",
        ]
    )
    assert get_exit == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["value"] == 192

    app_config = load_app_config(tmp_path / "settings.yaml")
    assert app_config.icecast_thread_queue_size == 8192
    assert app_config.icecast_audio_bitrate == 192
    assert app_config.icecast_device == "alsa_output.test.monitor"
    assert "-thread_queue_size 8192" in app_config.icecast_command
    assert "-b:a 192k" in app_config.icecast_command
    assert "alsa_output.test.monitor" in app_config.icecast_command


def test_settings_set_icecast_params_preserve_command_suffix(tmp_path, capsys) -> None:
    bitrate_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "icecast_audio_bitrate",
            "160",
        ]
    )
    assert bitrate_exit == 0
    capsys.readouterr()

    get_base_exit = run(
        [
            "--json",
            "--config",
            str(tmp_path),
            "settings",
            "get",
            "icecast_command",
        ]
    )
    assert get_base_exit == 0
    base_payload = json.loads(capsys.readouterr().out)
    base_command = str(base_payload["value"])
    assert base_command.startswith("ffmpeg ")

    set_suffix_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "icecast_command",
            f"{base_command} -af loudnorm",
        ]
    )
    assert set_suffix_exit == 0
    capsys.readouterr()

    rate_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "icecast_audio_rate",
            "44100",
        ]
    )
    assert rate_exit == 0
    capsys.readouterr()

    get_exit = run(
        [
            "--json",
            "--config",
            str(tmp_path),
            "settings",
            "get",
            "icecast_command",
        ]
    )
    assert get_exit == 0
    payload = json.loads(capsys.readouterr().out)
    command = str(payload["value"])
    assert "-ar 44100" in command
    assert command.endswith("-af loudnorm")


def test_settings_set_icecast_params_updates_quoted_equivalent_command(
    tmp_path, capsys
) -> None:
    old_device = "alsa_output.usb-Generic_KM_B2_USB_Audio_20210726905926-00.analog-stereo.monitor"
    new_device = "bluez_output.F8_DF_15_C7_1C_3D.a2dp-sink.monitor"

    rate_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "icecast_audio_rate",
            "44100",
        ]
    )
    assert rate_exit == 0
    capsys.readouterr()

    quoted_command = (
        'ffmpeg -f pulse -thread_queue_size 4096 -i '
        f'"{old_device}" -ac 2 -ar 44100 -c:a libmp3lame -b:a 128k '
        '-content_type audio/mpeg -f mp3 '
        '"icecast://source:hackme@localhost:8000/radio.mp3"'
    )
    set_command_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "icecast_command",
            quoted_command,
        ]
    )
    assert set_command_exit == 0
    capsys.readouterr()

    set_device_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "icecast_device",
            new_device,
        ]
    )
    assert set_device_exit == 0
    capsys.readouterr()

    get_exit = run(
        [
            "--json",
            "--config",
            str(tmp_path),
            "settings",
            "get",
            "icecast_command",
        ]
    )
    assert get_exit == 0
    payload = json.loads(capsys.readouterr().out)
    command = str(payload["value"])
    assert new_device in command
    assert old_device not in command
    assert "-ar 44100" in command


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


def test_schedule_list_range_filters_and_materializes_cron(tmp_path, capsys) -> None:
    state_path = tmp_path / "db.sqlite"
    media = MediaItem.create(title="Test Media", source="file:///tmp/test.mp3")
    in_range = ScheduleEntry.create(
        media_id=media.id,
        start_at=datetime.fromisoformat("2099-01-01T10:00:00+00:00"),
    )
    out_of_range = ScheduleEntry.create(
        media_id=media.id,
        start_at=datetime.fromisoformat("2099-01-03T10:00:00+00:00"),
    )
    save_state(
        state_path,
        AppState(
            media_items=[media],
            schedule_entries=[in_range, out_of_range],
        ),
    )

    cron_exit = run(
        [
            "--config",
            str(tmp_path),
            "cron",
            "add",
            "--media-id",
            media.id,
            "--expression",
            "0 0 13 * * *",
        ]
    )
    assert cron_exit == 0
    capsys.readouterr()

    list_exit = run(
        [
            "--json",
            "--config",
            str(tmp_path),
            "schedule",
            "list",
            "--from",
            "2099-01-01",
            "--to",
            "2099-01-02",
        ]
    )
    assert list_exit == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["count"] >= 3
    assert all(
        "2099-01-01" <= entry["start_at"][:10] <= "2099-01-02"
        for entry in payload["entries"]
    )
    assert any(entry["cron_id"] is not None for entry in payload["entries"])


def test_schedule_list_range_requires_from_and_to(tmp_path, capsys) -> None:
    exit_code = run(
        [
            "--config",
            str(tmp_path),
            "schedule",
            "list",
            "--from",
            "2099-01-01",
        ]
    )
    assert exit_code == 2
    assert "Provide both --from and --to for range filtering." in capsys.readouterr().err


def test_schedule_export_range_json_updates_and_removes_files(tmp_path, capsys) -> None:
    state_path = tmp_path / "db.sqlite"
    local_tz = datetime.now().astimezone().tzinfo
    assert local_tz is not None
    today_local = datetime.now().astimezone().date()
    day_one = datetime(today_local.year, today_local.month, today_local.day, 10, 0, tzinfo=local_tz)
    day_two = day_one + timedelta(days=1)
    day_one_key = day_one.astimezone().date().isoformat()
    day_two_key = day_two.astimezone().date().isoformat()
    media = MediaItem.create(title="Test Media", source="file:///tmp/test.mp3")
    entry = ScheduleEntry.create(
        media_id=media.id,
        start_at=day_one,
    )
    save_state(
        state_path,
        AppState(
            media_items=[media],
            schedule_entries=[entry],
        ),
    )

    stale_path = tmp_path / "export" / day_two_key[:4] / f"{day_two_key}.json"
    stale_path.parent.mkdir(parents=True, exist_ok=True)
    stale_path.write_text("{}", encoding="utf-8")

    exit_code = run(
        [
            "--json",
            "--config",
            str(tmp_path),
            "schedule",
            "export",
            "--from",
            day_one_key,
            "--to",
            day_two_key,
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["from"] == day_one_key
    assert payload["to"] == day_two_key
    assert payload["removed_count"] == 1
    assert not stale_path.exists()
    assert (tmp_path / "export" / day_one_key[:4] / f"{day_one_key}.json").is_file()


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


def test_schedule_list_date_materializes_cron_for_requested_day(tmp_path, capsys) -> None:
    state_path = tmp_path / "db.sqlite"
    media = MediaItem.create(title="Test Media", source="file:///tmp/test.mp3")
    save_state(
        state_path,
        AppState(
            media_items=[media],
        ),
    )

    add_exit = run(
        [
            "--config",
            str(tmp_path),
            "cron",
            "add",
            "--media-id",
            media.id,
            "--expression",
            "0 0 13 * * *",
        ]
    )
    assert add_exit == 0
    capsys.readouterr()

    target_date = (datetime.now().astimezone().date() + timedelta(days=7)).isoformat()
    list_exit = run(
        [
            "--json",
            "--config",
            str(tmp_path),
            "schedule",
            "list",
            "--date",
            target_date,
        ]
    )
    assert list_exit == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["count"] >= 1
    assert any(
        entry["cron_id"] is not None and entry["start_at"].startswith(target_date)
        for entry in payload["entries"]
    )


def test_runtime_status_json_defaults_offline(tmp_path, capsys) -> None:
    exit_code = run(["--json", "--config", str(tmp_path), "runtime", "status"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["effective_status"] == "offline"
    assert payload["pid"] is None


def test_icecast_status_uses_generated_command_when_manual_command_is_empty(
    tmp_path, capsys
) -> None:
    status_exit = run(["--json", "--config", str(tmp_path), "icecast", "status"])
    assert status_exit == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["configured_command"].startswith("ffmpeg ")


def test_icecast_start_status_stop_with_configured_command(tmp_path, capsys) -> None:
    set_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "icecast_command",
            "sleep 60",
        ]
    )
    assert set_exit == 0
    capsys.readouterr()

    start_exit = run(["--json", "--config", str(tmp_path), "icecast", "start"])
    assert start_exit == 0
    start_payload = json.loads(capsys.readouterr().out)
    assert start_payload["started"] is True
    assert start_payload["status"] is True
    assert start_payload["pid"] > 0
    pid = int(start_payload["pid"])

    try:
        status_exit = run(["--json", "--config", str(tmp_path), "icecast", "status"])
        assert status_exit == 0
        status_payload = json.loads(capsys.readouterr().out)
        assert status_payload["status"] is True
        assert status_payload["running"] is True
        assert status_payload["pid"] == pid
        assert status_payload["configured_command"] == "sleep 60"
    finally:
        stop_exit = run(
            [
                "--json",
                "--config",
                str(tmp_path),
                "icecast",
                "stop",
                "--timeout",
                "2",
            ]
        )
        assert stop_exit == 0
        stop_payload = json.loads(capsys.readouterr().out)
        assert stop_payload["stopped"] is True
        assert stop_payload["status"] is False
    log_text = runtime_log_file_path(tmp_path).read_text(encoding="utf-8")
    assert "[icecast] start requested" in log_text
    assert "[icecast] started pid=" in log_text
    assert "[icecast] process confirmed running after startup check:" in log_text
    assert "[icecast] stop requested" in log_text
    assert "[icecast] stopped pid=" in log_text


def test_icecast_start_accepts_wrapped_command_from_settings(tmp_path, capsys) -> None:
    set_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "icecast_command",
            '"sleep 60"',
        ]
    )
    assert set_exit == 0
    capsys.readouterr()

    start_exit = run(["--json", "--config", str(tmp_path), "icecast", "start"])
    assert start_exit == 0
    start_payload = json.loads(capsys.readouterr().out)
    assert start_payload["started"] is True
    assert start_payload["status"] is True
    assert start_payload["command"] == "sleep 60"
    pid = int(start_payload["pid"])

    try:
        status_exit = run(["--json", "--config", str(tmp_path), "icecast", "status"])
        assert status_exit == 0
        status_payload = json.loads(capsys.readouterr().out)
        assert status_payload["status"] is True
        assert status_payload["running"] is True
        assert status_payload["pid"] == pid
        assert status_payload["configured_command"] == "sleep 60"
    finally:
        stop_exit = run(
            [
                "--json",
                "--config",
                str(tmp_path),
                "icecast",
                "stop",
                "--timeout",
                "2",
            ]
        )
        assert stop_exit == 0
        stop_payload = json.loads(capsys.readouterr().out)
        assert stop_payload["stopped"] is True
        assert stop_payload["status"] is False


def test_icecast_start_reports_immediate_exit(tmp_path, capsys) -> None:
    set_exit = run(
        [
            "--config",
            str(tmp_path),
            "settings",
            "set",
            "icecast_command",
            "false",
        ]
    )
    assert set_exit == 0
    capsys.readouterr()

    start_exit = run(["--json", "--config", str(tmp_path), "icecast", "start"])
    assert start_exit == 2
    error_payload = json.loads(capsys.readouterr().err)
    assert error_payload["ok"] is False
    assert "exited immediately" in error_payload["error"]
    assert "Exit code:" in error_payload["error"]
    log_text = runtime_log_file_path(tmp_path).read_text(encoding="utf-8")
    assert "[icecast] start requested" in log_text
    assert "[icecast] start failed:" in log_text
    assert "[cli] error (icecast start):" in log_text


def test_logs_show_json_empty(tmp_path, capsys) -> None:
    exit_code = run(["--json", "--config", str(tmp_path), "logs", "show", "--all"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["count"] == 0
    assert payload["lines"] == []
    assert payload["log_path"] == str(runtime_log_file_path(tmp_path))


def test_logs_show_tail_and_export(tmp_path, capsys) -> None:
    log_path = runtime_log_file_path(tmp_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        (
            "[10:00:00] first\n"
            "[10:00:01] second\n"
            "[10:00:02] third\n"
        ),
        encoding="utf-8",
    )

    show_exit = run(
        ["--json", "--config", str(tmp_path), "logs", "show", "--lines", "2"]
    )
    assert show_exit == 0
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["count"] == 2
    assert show_payload["lines"] == ["[10:00:01] second", "[10:00:02] third"]

    output_path = tmp_path / "exported-runtime.log"
    export_exit = run(
        [
            "--json",
            "--config",
            str(tmp_path),
            "logs",
            "export",
            "--output",
            str(output_path),
            "--lines",
            "2",
        ]
    )
    assert export_exit == 0
    export_payload = json.loads(capsys.readouterr().out)
    assert export_payload["count"] == 2
    assert export_payload["output_path"] == str(output_path)
    assert output_path.read_text(encoding="utf-8") == (
        "[10:00:01] second\n"
        "[10:00:02] third\n"
    )


def test_logs_show_rejects_non_positive_lines(tmp_path, capsys) -> None:
    exit_code = run(["--config", str(tmp_path), "logs", "show", "--lines", "0"])
    assert exit_code == 2
    assert "lines must be greater than zero" in capsys.readouterr().err


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

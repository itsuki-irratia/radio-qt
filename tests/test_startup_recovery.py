from __future__ import annotations

from datetime import datetime, timezone

from radioqt.startup_recovery import backup_file_for_recovery, recovery_timestamp


def test_recovery_timestamp_uses_expected_shape() -> None:
    stamp = recovery_timestamp(now=datetime(2026, 4, 19, 8, 7, 6, tzinfo=timezone.utc))
    assert stamp == "20260419T080706"


def test_backup_file_for_recovery_moves_original(tmp_path) -> None:
    source = tmp_path / "settings.yaml"
    source.write_text("x: 1\n", encoding="utf-8")

    backup_path = backup_file_for_recovery(source, timestamp="20260419T010203")

    assert backup_path == tmp_path / "settings.yaml.corrupt-20260419T010203"
    assert source.exists() is False
    assert backup_path is not None
    assert backup_path.read_text(encoding="utf-8") == "x: 1\n"


def test_backup_file_for_recovery_adds_incremental_suffix_when_needed(tmp_path) -> None:
    source = tmp_path / "db.sqlite"
    source.write_bytes(b"state")
    existing_backup = tmp_path / "db.sqlite.corrupt-20260419T010203"
    existing_backup.write_bytes(b"old")

    backup_path = backup_file_for_recovery(source, timestamp="20260419T010203")

    assert backup_path == tmp_path / "db.sqlite.corrupt-20260419T010203-1"
    assert source.exists() is False
    assert backup_path is not None
    assert backup_path.read_bytes() == b"state"


def test_backup_file_for_recovery_returns_none_when_source_does_not_exist(tmp_path) -> None:
    source = tmp_path / "missing.log"

    backup_path = backup_file_for_recovery(source, timestamp="20260419T010203")

    assert backup_path is None

from __future__ import annotations

from datetime import datetime, timezone

import radioqt.runtime_logs as runtime_logs
from radioqt.runtime_logs import (
    append_runtime_log_line,
    format_runtime_log_line,
    read_runtime_log_lines,
    runtime_log_file_path,
)


def test_format_runtime_log_line_uses_expected_shape() -> None:
    line = format_runtime_log_line(
        "hello",
        timestamp=datetime(2026, 4, 17, 12, 34, 56, tzinfo=timezone.utc),
    )
    assert line == "[12:34:56] hello"


def test_append_and_read_runtime_logs_roundtrip(tmp_path) -> None:
    append_runtime_log_line(tmp_path, "[10:00:00] first")
    append_runtime_log_line(tmp_path, "[10:00:01] second")
    append_runtime_log_line(tmp_path, "[10:00:02] third")

    assert runtime_log_file_path(tmp_path).is_file() is True
    assert read_runtime_log_lines(tmp_path, limit=None) == [
        "[10:00:00] first",
        "[10:00:01] second",
        "[10:00:02] third",
    ]
    assert read_runtime_log_lines(tmp_path, limit=2) == [
        "[10:00:01] second",
        "[10:00:02] third",
    ]


def test_append_runtime_log_line_rotates_large_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_logs, "RUNTIME_LOG_MAX_BYTES", 70)
    monkeypatch.setattr(runtime_logs, "RUNTIME_LOG_ROTATE_KEEP_LINES", 2)

    append_runtime_log_line(tmp_path, "[10:00:00] first-0123456789")
    append_runtime_log_line(tmp_path, "[10:00:01] second-0123456789")
    append_runtime_log_line(tmp_path, "[10:00:02] third-0123456789")
    append_runtime_log_line(tmp_path, "[10:00:03] fourth-0123456789")

    assert read_runtime_log_lines(tmp_path, limit=None) == [
        "[10:00:02] third-0123456789",
        "[10:00:03] fourth-0123456789",
    ]

from __future__ import annotations

from pathlib import Path

STREAM_RELAY_PID_FILE_NAME = "icecast.pid"
STREAM_RELAY_STDOUT_FILE_NAME = "icecast.stdout.log"
STREAM_RELAY_STDERR_FILE_NAME = "icecast.stderr.log"
LEGACY_STREAM_RELAY_PID_FILE_NAME = "stream_relay.pid"


def stream_relay_pid_file_path(config_dir: Path) -> Path:
    return config_dir.expanduser() / STREAM_RELAY_PID_FILE_NAME


def stream_relay_stdout_file_path(config_dir: Path) -> Path:
    return config_dir.expanduser() / STREAM_RELAY_STDOUT_FILE_NAME


def stream_relay_stderr_file_path(config_dir: Path) -> Path:
    return config_dir.expanduser() / STREAM_RELAY_STDERR_FILE_NAME


def read_stream_relay_pid(config_dir: Path) -> int | None:
    for pid_path in (
        stream_relay_pid_file_path(config_dir),
        config_dir.expanduser() / LEGACY_STREAM_RELAY_PID_FILE_NAME,
    ):
        if not pid_path.is_file():
            continue
        try:
            raw_pid = pid_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        try:
            parsed_pid = int(raw_pid)
        except (TypeError, ValueError):
            continue
        if parsed_pid <= 0:
            continue
        return parsed_pid
    return None


def write_stream_relay_pid(config_dir: Path, pid: int) -> None:
    pid_path = stream_relay_pid_file_path(config_dir)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(f"{int(pid)}\n", encoding="utf-8")


def delete_stream_relay_pid(config_dir: Path) -> bool:
    removed = False
    for pid_path in (
        stream_relay_pid_file_path(config_dir),
        config_dir.expanduser() / LEGACY_STREAM_RELAY_PID_FILE_NAME,
    ):
        if not pid_path.is_file():
            continue
        try:
            pid_path.unlink()
            removed = True
        except OSError:
            continue
    return removed

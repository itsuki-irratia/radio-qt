from __future__ import annotations

from collections import deque
from datetime import datetime
from pathlib import Path

RUNTIME_LOG_FILE_NAME = "runtime.log"
RUNTIME_LOG_MAX_BYTES = 512 * 1024
RUNTIME_LOG_ROTATE_KEEP_LINES = 2000


def runtime_log_file_path(config_dir: Path) -> Path:
    return config_dir.expanduser() / RUNTIME_LOG_FILE_NAME


def format_runtime_log_line(message: str, *, timestamp: datetime | None = None) -> str:
    if timestamp is None:
        timestamp = datetime.now().astimezone()
    return f"[{timestamp.strftime('%H:%M:%S')}] {message}"


def _rotate_runtime_log_if_needed(
    log_path: Path,
    *,
    max_bytes: int | None = None,
    keep_lines: int | None = None,
) -> None:
    normalized_max_bytes = RUNTIME_LOG_MAX_BYTES if max_bytes is None else int(max_bytes)
    normalized_keep_lines = (
        RUNTIME_LOG_ROTATE_KEEP_LINES if keep_lines is None else int(keep_lines)
    )
    if normalized_max_bytes <= 0 or normalized_keep_lines <= 0:
        return
    try:
        if log_path.stat().st_size <= normalized_max_bytes:
            return
    except OSError:
        return

    tail: deque[str] = deque(maxlen=max(1, normalized_keep_lines))
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                tail.append(line.rstrip("\n"))
    except OSError:
        return

    temporary_path = log_path.with_name(f"{log_path.name}.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as handle:
            for line in tail:
                handle.write(line)
                handle.write("\n")
        temporary_path.replace(log_path)
    except OSError:
        try:
            temporary_path.unlink()
        except OSError:
            pass


def append_runtime_log_line(config_dir: Path, line: str) -> None:
    log_path = runtime_log_file_path(config_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_line = line.rstrip("\n")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(normalized_line)
        handle.write("\n")
    _rotate_runtime_log_if_needed(log_path)


def read_runtime_log_lines(config_dir: Path, *, limit: int | None = None) -> list[str]:
    log_path = runtime_log_file_path(config_dir)
    if not log_path.is_file():
        return []
    if limit is not None and limit <= 0:
        return []
    if limit is None:
        return [line.rstrip("\n") for line in log_path.read_text(encoding="utf-8").splitlines()]
    tail: deque[str] = deque(maxlen=limit)
    with log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            tail.append(line.rstrip("\n"))
    return list(tail)

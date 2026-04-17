from __future__ import annotations

from collections import deque
from datetime import datetime
from pathlib import Path

RUNTIME_LOG_FILE_NAME = "runtime.log"


def runtime_log_file_path(config_dir: Path) -> Path:
    return config_dir.expanduser() / RUNTIME_LOG_FILE_NAME


def format_runtime_log_line(message: str, *, timestamp: datetime | None = None) -> str:
    if timestamp is None:
        timestamp = datetime.now().astimezone()
    return f"[{timestamp.strftime('%H:%M:%S')}] {message}"


def append_runtime_log_line(config_dir: Path, line: str) -> None:
    log_path = runtime_log_file_path(config_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_line = line.rstrip("\n")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(normalized_line)
        handle.write("\n")


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

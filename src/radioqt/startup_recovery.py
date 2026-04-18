from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil


def recovery_timestamp(*, now: datetime | None = None) -> str:
    current = now or datetime.now().astimezone()
    return current.strftime("%Y%m%dT%H%M%S")


def _recovery_backup_path(path: Path, *, timestamp: str, attempt: int) -> Path:
    suffix = f".corrupt-{timestamp}"
    if attempt > 0:
        suffix = f"{suffix}-{attempt}"
    return path.with_name(f"{path.name}{suffix}")


def backup_file_for_recovery(path: Path, *, timestamp: str | None = None) -> Path | None:
    if not path.exists():
        return None

    resolved_timestamp = timestamp or recovery_timestamp()
    attempt = 0
    backup_path = _recovery_backup_path(path, timestamp=resolved_timestamp, attempt=attempt)
    while backup_path.exists():
        attempt += 1
        backup_path = _recovery_backup_path(path, timestamp=resolved_timestamp, attempt=attempt)

    try:
        path.rename(backup_path)
        return backup_path
    except OSError:
        pass

    try:
        shutil.copy2(path, backup_path)
    except OSError:
        return None
    try:
        path.unlink()
    except OSError:
        # Best effort only; if unlink fails the backup still exists.
        pass
    return backup_path

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

RUNTIME_STATUS_FILE_NAME = "radioqt.lock"
LEGACY_RUNTIME_STATUS_FILE_NAME = "runtime_status.json"
RUNTIME_STATUS_ONLINE = "online"
RUNTIME_STATUS_OFFLINE = "offline"
VALID_RUNTIME_STATUSES = {RUNTIME_STATUS_ONLINE, RUNTIME_STATUS_OFFLINE}


@dataclass(slots=True)
class RuntimeStatusRecord:
    status: str
    pid: int | None


@dataclass(slots=True)
class RuntimeStatusView:
    status: str
    pid: int | None
    process_running: bool
    effective_status: str
    stale: bool


def runtime_status_file_path(config_dir: Path) -> Path:
    return config_dir.expanduser() / RUNTIME_STATUS_FILE_NAME


def _legacy_runtime_status_file_path(config_dir: Path) -> Path:
    return config_dir.expanduser() / LEGACY_RUNTIME_STATUS_FILE_NAME


def _normalize_status(raw_status: Any) -> str:
    status = str(raw_status).strip().lower()
    if status not in VALID_RUNTIME_STATUSES:
        return RUNTIME_STATUS_OFFLINE
    return status


def _normalize_pid(raw_pid: Any) -> int | None:
    try:
        parsed = int(raw_pid)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _default_runtime_status_record() -> RuntimeStatusRecord:
    return RuntimeStatusRecord(
        status=RUNTIME_STATUS_OFFLINE,
        pid=None,
    )


def read_runtime_status(config_dir: Path) -> RuntimeStatusRecord:
    for status_path in (
        runtime_status_file_path(config_dir),
        _legacy_runtime_status_file_path(config_dir),
    ):
        if not status_path.is_file():
            continue
        try:
            raw_data = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw_data, dict):
            continue
        return RuntimeStatusRecord(
            status=_normalize_status(raw_data.get("status")),
            pid=_normalize_pid(raw_data.get("pid")),
        )
    return _default_runtime_status_record()


def is_pid_running(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    if _is_linux_zombie_process(pid):
        return False
    return True


def _is_linux_zombie_process(pid: int) -> bool:
    stat_path = Path("/proc") / str(pid) / "stat"
    if not stat_path.is_file():
        return False
    try:
        stat_content = stat_path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    closing_paren_index = stat_content.rfind(")")
    if closing_paren_index < 0 or closing_paren_index + 2 >= len(stat_content):
        return False
    state_chunk = stat_content[closing_paren_index + 2 :]
    if not state_chunk:
        return False
    process_state = state_chunk.split(" ", 1)[0].strip()
    return process_state == "Z"


def resolve_runtime_status(config_dir: Path) -> RuntimeStatusView:
    record = read_runtime_status(config_dir)
    process_running = is_pid_running(record.pid)
    stale = record.pid is not None and not process_running
    effective_status = RUNTIME_STATUS_OFFLINE if stale else record.status
    return RuntimeStatusView(
        status=record.status,
        pid=record.pid,
        process_running=process_running,
        effective_status=effective_status,
        stale=stale,
    )


def write_runtime_status(
    config_dir: Path,
    *,
    status: str,
    pid: int | None = None,
) -> RuntimeStatusRecord:
    normalized_status = _normalize_status(status)
    normalized_pid = _normalize_pid(pid)
    status_path = runtime_status_file_path(config_dir)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": normalized_status,
        "pid": normalized_pid,
    }
    status_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    legacy_status_path = _legacy_runtime_status_file_path(config_dir)
    if legacy_status_path != status_path and legacy_status_path.is_file():
        try:
            legacy_status_path.unlink()
        except OSError:
            pass
    return RuntimeStatusRecord(
        status=normalized_status,
        pid=normalized_pid,
    )


def mark_runtime_online(config_dir: Path, *, pid: int | None = None) -> RuntimeStatusRecord:
    runtime_pid = os.getpid() if pid is None else pid
    return write_runtime_status(
        config_dir,
        status=RUNTIME_STATUS_ONLINE,
        pid=runtime_pid,
    )


def mark_runtime_offline(config_dir: Path, *, pid: int | None = None) -> RuntimeStatusRecord:
    runtime_pid = os.getpid() if pid is None else pid
    return write_runtime_status(
        config_dir,
        status=RUNTIME_STATUS_OFFLINE,
        pid=runtime_pid,
    )


def delete_runtime_lock(config_dir: Path) -> bool:
    removed = False
    for status_path in (
        runtime_status_file_path(config_dir),
        _legacy_runtime_status_file_path(config_dir),
    ):
        if not status_path.is_file():
            continue
        try:
            status_path.unlink()
            removed = True
        except OSError:
            continue
    return removed

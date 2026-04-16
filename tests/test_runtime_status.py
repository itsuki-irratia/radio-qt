from __future__ import annotations

import json
import os

from radioqt.runtime_status import (
    delete_runtime_lock,
    LEGACY_RUNTIME_STATUS_FILE_NAME,
    RUNTIME_STATUS_OFFLINE,
    RUNTIME_STATUS_FILE_NAME,
    RUNTIME_STATUS_ONLINE,
    mark_runtime_offline,
    read_runtime_status,
    resolve_runtime_status,
    runtime_status_file_path,
    write_runtime_status,
)


def test_runtime_status_defaults_to_offline(tmp_path) -> None:
    record = read_runtime_status(tmp_path)
    assert record.status == RUNTIME_STATUS_OFFLINE
    assert record.pid is None
    assert runtime_status_file_path(tmp_path).name == RUNTIME_STATUS_FILE_NAME


def test_runtime_status_write_online_then_offline(tmp_path) -> None:
    online = write_runtime_status(
        tmp_path,
        status=RUNTIME_STATUS_ONLINE,
        pid=1234,
    )
    assert online.status == RUNTIME_STATUS_ONLINE
    assert online.pid == 1234

    offline = mark_runtime_offline(tmp_path)
    assert offline.status == RUNTIME_STATUS_OFFLINE
    assert offline.pid == os.getpid()
    payload = json.loads(runtime_status_file_path(tmp_path).read_text(encoding="utf-8"))
    assert payload == {"pid": os.getpid(), "status": RUNTIME_STATUS_OFFLINE}


def test_runtime_status_resolves_stale_online_pid(monkeypatch, tmp_path) -> None:
    write_runtime_status(
        tmp_path,
        status=RUNTIME_STATUS_ONLINE,
        pid=2222,
    )
    monkeypatch.setattr("radioqt.runtime_status.is_pid_running", lambda pid: False)
    view = resolve_runtime_status(tmp_path)
    assert view.status == RUNTIME_STATUS_ONLINE
    assert view.effective_status == RUNTIME_STATUS_OFFLINE
    assert view.process_running is False
    assert view.stale is True


def test_runtime_status_invalid_file_falls_back_to_offline(tmp_path) -> None:
    status_path = runtime_status_file_path(tmp_path)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(["invalid"]), encoding="utf-8")
    record = read_runtime_status(tmp_path)
    assert record.status == RUNTIME_STATUS_OFFLINE
    assert record.pid is None


def test_runtime_status_reads_legacy_file_when_new_one_missing(tmp_path) -> None:
    legacy_status_path = tmp_path / LEGACY_RUNTIME_STATUS_FILE_NAME
    legacy_status_path.write_text(
        json.dumps(
            {
                "status": RUNTIME_STATUS_ONLINE,
                "pid": 7777,
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    record = read_runtime_status(tmp_path)
    assert record.status == RUNTIME_STATUS_ONLINE
    assert record.pid == 7777


def test_delete_runtime_lock_removes_new_and_legacy_files(tmp_path) -> None:
    runtime_status_file_path(tmp_path).write_text(
        json.dumps({"status": RUNTIME_STATUS_OFFLINE, "pid": None}, ensure_ascii=True),
        encoding="utf-8",
    )
    legacy_status_path = tmp_path / LEGACY_RUNTIME_STATUS_FILE_NAME
    legacy_status_path.write_text(
        json.dumps({"status": RUNTIME_STATUS_ONLINE, "pid": 999}, ensure_ascii=True),
        encoding="utf-8",
    )
    removed = delete_runtime_lock(tmp_path)
    assert removed is True
    assert runtime_status_file_path(tmp_path).exists() is False
    assert legacy_status_path.exists() is False

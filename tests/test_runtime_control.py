from __future__ import annotations

from radioqt.runtime_control import (
    drain_runtime_control_commands,
    enqueue_runtime_control_command,
    runtime_control_file_path,
    RUNTIME_CONTROL_ACTION_FADE_IN,
    RUNTIME_CONTROL_ACTION_FADE_OUT,
    RUNTIME_CONTROL_ACTION_SET_VOLUME,
)


def test_runtime_control_enqueue_and_drain_roundtrip(tmp_path) -> None:
    first = enqueue_runtime_control_command(
        tmp_path,
        action=RUNTIME_CONTROL_ACTION_FADE_IN,
    )
    second = enqueue_runtime_control_command(
        tmp_path,
        action=RUNTIME_CONTROL_ACTION_FADE_OUT,
    )
    drained = drain_runtime_control_commands(tmp_path)
    assert [command.command_id for command in drained] == [first.command_id, second.command_id]
    assert [command.action for command in drained] == [
        RUNTIME_CONTROL_ACTION_FADE_IN,
        RUNTIME_CONTROL_ACTION_FADE_OUT,
    ]
    assert runtime_control_file_path(tmp_path).exists() is False


def test_runtime_control_drain_missing_file_returns_empty(tmp_path) -> None:
    assert drain_runtime_control_commands(tmp_path) == []


def test_runtime_control_set_volume_roundtrip(tmp_path) -> None:
    queued = enqueue_runtime_control_command(
        tmp_path,
        action=RUNTIME_CONTROL_ACTION_SET_VOLUME,
        value=37,
    )
    drained = drain_runtime_control_commands(tmp_path)
    assert len(drained) == 1
    assert drained[0].command_id == queued.command_id
    assert drained[0].action == RUNTIME_CONTROL_ACTION_SET_VOLUME
    assert drained[0].value == 37

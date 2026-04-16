from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import uuid

RUNTIME_CONTROL_FILE_NAME = "runtime_control.jsonl"
RUNTIME_CONTROL_ACTION_FADE_IN = "fade_in"
RUNTIME_CONTROL_ACTION_FADE_OUT = "fade_out"
RUNTIME_CONTROL_ACTION_SET_VOLUME = "set_volume"
RUNTIME_CONTROL_ACTION_START_AUTOMATION = "start_automation"
RUNTIME_CONTROL_ACTION_STOP_AUTOMATION = "stop_automation"
VALID_RUNTIME_CONTROL_ACTIONS = {
    RUNTIME_CONTROL_ACTION_FADE_IN,
    RUNTIME_CONTROL_ACTION_FADE_OUT,
    RUNTIME_CONTROL_ACTION_SET_VOLUME,
    RUNTIME_CONTROL_ACTION_START_AUTOMATION,
    RUNTIME_CONTROL_ACTION_STOP_AUTOMATION,
}


@dataclass(slots=True)
class RuntimeControlCommand:
    command_id: str
    action: str
    value: int | None = None


def runtime_control_file_path(config_dir: Path) -> Path:
    return config_dir.expanduser() / RUNTIME_CONTROL_FILE_NAME


def _normalize_volume_value(raw_value: object) -> int | None:
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return None
    if parsed < 0 or parsed > 100:
        return None
    return parsed


def enqueue_runtime_control_command(
    config_dir: Path,
    *,
    action: str,
    value: int | None = None,
) -> RuntimeControlCommand:
    normalized_action = str(action).strip().lower()
    if normalized_action not in VALID_RUNTIME_CONTROL_ACTIONS:
        raise ValueError(f"Unsupported runtime control action: {action}")
    normalized_value = _normalize_volume_value(value)
    if normalized_action == RUNTIME_CONTROL_ACTION_SET_VOLUME and normalized_value is None:
        raise ValueError("set_volume requires value between 0 and 100")
    if normalized_action != RUNTIME_CONTROL_ACTION_SET_VOLUME:
        normalized_value = None
    command = RuntimeControlCommand(
        command_id=uuid.uuid4().hex,
        action=normalized_action,
        value=normalized_value,
    )
    control_path = runtime_control_file_path(config_dir)
    control_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": command.command_id,
        "action": command.action,
        "value": command.value,
    }
    with control_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, separators=(",", ":"), ensure_ascii=True))
        handle.write("\n")
    return command


def drain_runtime_control_commands(config_dir: Path) -> list[RuntimeControlCommand]:
    control_path = runtime_control_file_path(config_dir)
    processing_path = control_path.with_suffix(control_path.suffix + ".processing")
    try:
        os.replace(control_path, processing_path)
    except FileNotFoundError:
        return []
    except OSError:
        return []

    commands: list[RuntimeControlCommand] = []
    try:
        for raw_line in processing_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            raw_action = str(payload.get("action", "")).strip().lower()
            if raw_action not in VALID_RUNTIME_CONTROL_ACTIONS:
                continue
            parsed_value = _normalize_volume_value(payload.get("value"))
            if raw_action == RUNTIME_CONTROL_ACTION_SET_VOLUME and parsed_value is None:
                continue
            if raw_action != RUNTIME_CONTROL_ACTION_SET_VOLUME:
                parsed_value = None
            raw_id = str(payload.get("id", "")).strip()
            command_id = raw_id if raw_id else uuid.uuid4().hex
            commands.append(
                RuntimeControlCommand(
                    command_id=command_id,
                    action=raw_action,
                    value=parsed_value,
                )
            )
    finally:
        try:
            processing_path.unlink()
        except OSError:
            pass
    return commands

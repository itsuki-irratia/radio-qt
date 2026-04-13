from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from .models import DEFAULT_SUPPORTED_EXTENSIONS, LibraryTab


def _safe_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, parsed)


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off", ""}:
            return False
    return default


def _normalize_extensions(raw_values: Any) -> list[str]:
    if not isinstance(raw_values, list):
        return list(DEFAULT_SUPPORTED_EXTENSIONS)
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        token = str(raw).strip().lower().lstrip(".")
        if not token:
            continue
        if not all(char.isalnum() for char in token):
            continue
        if token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized or list(DEFAULT_SUPPORTED_EXTENSIONS)


@dataclass(slots=True)
class AppConfig:
    fade_duration_seconds: int = 5
    font_size: int | None = None
    library_tabs: list[LibraryTab] = field(default_factory=list)
    supported_extensions: list[str] = field(default_factory=lambda: list(DEFAULT_SUPPORTED_EXTENSIONS))
    greenwich_time_signal_enabled: bool = False
    greenwich_time_signal_path: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        fade_duration_seconds = _safe_positive_int(data.get("fade"), 5)
        if "fade" not in data:
            fade_in_legacy = _safe_positive_int(data.get("fade_in_duration_seconds"), 5)
            fade_out_legacy = _safe_positive_int(data.get("fade_out_duration_seconds"), 5)
            fade_duration_seconds = max(fade_in_legacy, fade_out_legacy)

        font_size: int | None = None
        font_payload = data.get("font")
        if isinstance(font_payload, dict) and "size" in font_payload:
            font_size = _safe_positive_int(font_payload.get("size"), 10)
        elif "font_size" in data:
            # Backward-compatible support for legacy flat key.
            font_size = _safe_positive_int(data.get("font_size"), 10)

        greenwich_time_signal_enabled = _safe_bool(
            data.get("greenwich_time_signal_enabled"),
            False,
        )
        greenwich_time_signal_path = str(
            data.get("greenwich_time_signal_path", "") or ""
        ).strip()
        signal_payload = data.get("greenwich_time_signal")
        if isinstance(signal_payload, dict):
            greenwich_time_signal_enabled = _safe_bool(
                signal_payload.get("enabled"),
                greenwich_time_signal_enabled,
            )
            greenwich_time_signal_path = str(
                signal_payload.get("path", greenwich_time_signal_path) or ""
            ).strip()

        return cls(
            fade_duration_seconds=fade_duration_seconds,
            font_size=font_size,
            library_tabs=[
                LibraryTab.from_dict(item)
                for item in data.get("library_tabs", [])
                if isinstance(item, dict)
            ],
            supported_extensions=_normalize_extensions(data.get("supported_extensions")),
            greenwich_time_signal_enabled=greenwich_time_signal_enabled,
            greenwich_time_signal_path=greenwich_time_signal_path,
        )

    def to_dict(self) -> dict[str, Any]:
        normalized_font_size = max(1, self.font_size if self.font_size is not None else 10)
        return {
            "fade": max(1, self.fade_duration_seconds),
            "font": {
                "size": normalized_font_size,
            },
            "library_tabs": [tab.to_dict() for tab in self.library_tabs],
            "supported_extensions": _normalize_extensions(self.supported_extensions),
            "greenwich_time_signal_enabled": bool(self.greenwich_time_signal_enabled),
            "greenwich_time_signal_path": str(self.greenwich_time_signal_path).strip(),
        }


def _parse_scalar(token: str) -> str:
    value = token.strip()
    if not value:
        return ""
    if value.startswith('"') and value.endswith('"'):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, str) else value[1:-1]
        except json.JSONDecodeError:
            return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return value


def _parse_settings_yaml(raw_text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    lines = raw_text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        index += 1
        if not line.strip():
            continue
        if line.startswith("fade:"):
            raw_value = line.split(":", 1)[1].strip()
            try:
                data["fade"] = int(raw_value)
            except ValueError:
                pass
            continue
        if line.startswith("fade_in_duration_seconds:"):
            raw_value = line.split(":", 1)[1].strip()
            try:
                data["fade_in_duration_seconds"] = int(raw_value)
            except ValueError:
                pass
            continue
        if line.startswith("fade_out_duration_seconds:"):
            raw_value = line.split(":", 1)[1].strip()
            try:
                data["fade_out_duration_seconds"] = int(raw_value)
            except ValueError:
                pass
            continue
        if line.startswith("font_size:"):
            raw_value = line.split(":", 1)[1].strip()
            try:
                data["font_size"] = int(raw_value)
            except ValueError:
                pass
            continue
        if line.startswith("greenwich_time_signal_enabled:"):
            raw_value = line.split(":", 1)[1].strip()
            data["greenwich_time_signal_enabled"] = _safe_bool(raw_value, False)
            continue
        if line.startswith("greenwich_time_signal_path:"):
            raw_value = line.split(":", 1)[1].strip()
            data["greenwich_time_signal_path"] = _parse_scalar(raw_value)
            continue
        if line.startswith("greenwich_time_signal:"):
            signal_data: dict[str, Any] = {}
            while index < len(lines):
                detail_line = lines[index].rstrip()
                if not detail_line.startswith("  "):
                    break
                detail = detail_line[2:]
                index += 1
                if ":" not in detail:
                    continue
                key, value = detail.split(":", 1)
                normalized_key = key.strip()
                raw_value = value.strip()
                if normalized_key == "enabled":
                    signal_data["enabled"] = _safe_bool(raw_value, False)
                elif normalized_key == "path":
                    signal_data["path"] = _parse_scalar(raw_value)
            if signal_data:
                data["greenwich_time_signal"] = signal_data
            continue
        if line.startswith("font:"):
            font_data: dict[str, Any] = {}
            while index < len(lines):
                detail_line = lines[index].rstrip()
                if not detail_line.startswith("  "):
                    break
                detail = detail_line[2:]
                index += 1
                if ":" not in detail:
                    continue
                key, value = detail.split(":", 1)
                normalized_key = key.strip()
                raw_value = value.strip()
                if normalized_key == "size":
                    try:
                        font_data["size"] = int(raw_value)
                    except ValueError:
                        continue
            if font_data:
                data["font"] = font_data
            continue
        if line.startswith("supported_extensions:"):
            extensions: list[str] = []
            while index < len(lines):
                item_line = lines[index].rstrip()
                if not item_line.startswith("  - "):
                    break
                token = item_line[4:]
                extensions.append(_parse_scalar(token))
                index += 1
            data["supported_extensions"] = extensions
            continue
        if line.startswith("library_tabs:"):
            tabs: list[dict[str, str]] = []
            while index < len(lines):
                item_line = lines[index].rstrip()
                if not item_line.startswith("  - "):
                    break
                first = item_line[4:]
                tab: dict[str, str] = {}
                if ":" in first:
                    key, value = first.split(":", 1)
                    tab[key.strip()] = _parse_scalar(value)
                index += 1
                while index < len(lines):
                    detail_line = lines[index].rstrip()
                    if not detail_line.startswith("    "):
                        break
                    detail = detail_line[4:]
                    if ":" in detail:
                        key, value = detail.split(":", 1)
                        tab[key.strip()] = _parse_scalar(value)
                    index += 1
                tabs.append(tab)
            data["library_tabs"] = tabs
            continue
    return data


def _string_as_yaml(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _dump_settings_yaml(config: AppConfig) -> str:
    payload = config.to_dict()
    lines: list[str] = []
    lines.append(f"fade: {int(payload['fade'])}")
    font_payload = payload.get("font", {})
    lines.append("font:")
    lines.append(f"  size: {int(font_payload.get('size', 10))}")
    lines.append("library_tabs:")
    for tab in payload["library_tabs"]:
        title = _string_as_yaml(str(tab.get("title", "")))
        path = _string_as_yaml(str(tab.get("path", "")))
        lines.append(f"  - title: {title}")
        lines.append(f"    path: {path}")
    lines.append("supported_extensions:")
    for extension in payload["supported_extensions"]:
        lines.append(f"  - {_string_as_yaml(str(extension))}")
    lines.append(
        "greenwich_time_signal_enabled: "
        f"{'true' if payload['greenwich_time_signal_enabled'] else 'false'}"
    )
    lines.append(
        "greenwich_time_signal_path: "
        f"{_string_as_yaml(str(payload['greenwich_time_signal_path']))}"
    )
    lines.append("")
    return "\n".join(lines)


def load_app_config(path: Path) -> AppConfig:
    if not path.exists():
        return AppConfig()
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError:
        return AppConfig()
    data = _parse_settings_yaml(raw_text)
    if not data:
        return AppConfig()
    return AppConfig.from_dict(data)


def save_app_config(path: Path, config: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump_settings_yaml(config), encoding="utf-8")

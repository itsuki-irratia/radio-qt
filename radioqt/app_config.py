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
    fade_in_duration_seconds: int = 5
    fade_out_duration_seconds: int = 5
    library_tabs: list[LibraryTab] = field(default_factory=list)
    supported_extensions: list[str] = field(default_factory=lambda: list(DEFAULT_SUPPORTED_EXTENSIONS))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        return cls(
            fade_in_duration_seconds=_safe_positive_int(data.get("fade_in_duration_seconds"), 5),
            fade_out_duration_seconds=_safe_positive_int(data.get("fade_out_duration_seconds"), 5),
            library_tabs=[
                LibraryTab.from_dict(item)
                for item in data.get("library_tabs", [])
                if isinstance(item, dict)
            ],
            supported_extensions=_normalize_extensions(data.get("supported_extensions")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fade_in_duration_seconds": max(1, self.fade_in_duration_seconds),
            "fade_out_duration_seconds": max(1, self.fade_out_duration_seconds),
            "library_tabs": [tab.to_dict() for tab in self.library_tabs],
            "supported_extensions": _normalize_extensions(self.supported_extensions),
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
    lines.append(f"fade_in_duration_seconds: {int(payload['fade_in_duration_seconds'])}")
    lines.append(f"fade_out_duration_seconds: {int(payload['fade_out_duration_seconds'])}")
    lines.append("library_tabs:")
    for tab in payload["library_tabs"]:
        title = _string_as_yaml(str(tab.get("title", "")))
        path = _string_as_yaml(str(tab.get("path", "")))
        lines.append(f"  - title: {title}")
        lines.append(f"    path: {path}")
    lines.append("supported_extensions:")
    for extension in payload["supported_extensions"]:
        lines.append(f"  - {_string_as_yaml(str(extension))}")
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

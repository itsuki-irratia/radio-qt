from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from ..models import DEFAULT_SUPPORTED_EXTENSIONS, LibraryTab


def _safe_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, parsed)


def _safe_panel_percent(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(10, min(90, parsed))


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
    fade_in_duration_seconds: int = 5
    fade_out_duration_seconds: int = 5
    filesystem_default_fade_in: bool = False
    filesystem_default_fade_out: bool = False
    streams_default_fade_in: bool = False
    streams_default_fade_out: bool = False
    media_library_width_percent: int = 35
    schedule_width_percent: int = 65
    font_size: int | None = None
    library_tabs: list[LibraryTab] = field(default_factory=list)
    supported_extensions: list[str] = field(default_factory=lambda: list(DEFAULT_SUPPORTED_EXTENSIONS))
    greenwich_time_signal_enabled: bool = False
    greenwich_time_signal_path: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        fade_payload = data.get("fade")
        if isinstance(fade_payload, dict):
            shared_fade_duration_seconds = _safe_positive_int(fade_payload.get("seconds"), 5)
        else:
            shared_fade_duration_seconds = _safe_positive_int(fade_payload, 5)
        parsed_fade_in_duration_seconds = _safe_positive_int(
            data.get("fade_in_seconds"),
            _safe_positive_int(data.get("fade_in_duration_seconds"), shared_fade_duration_seconds),
        )
        parsed_fade_out_duration_seconds = _safe_positive_int(
            data.get("fade_out_seconds"),
            _safe_positive_int(data.get("fade_out_duration_seconds"), shared_fade_duration_seconds),
        )
        normalized_shared_fade_duration_seconds = max(
            shared_fade_duration_seconds,
            parsed_fade_in_duration_seconds,
            parsed_fade_out_duration_seconds,
        )
        fade_filesystem_payload = (
            fade_payload.get("filesystem", {}) if isinstance(fade_payload, dict) else {}
        )
        fade_streams_payload = (
            fade_payload.get("streams", {}) if isinstance(fade_payload, dict) else {}
        )
        filesystem_default_fade_in = _safe_bool(
            fade_filesystem_payload.get("default_fade_in")
            if isinstance(fade_filesystem_payload, dict)
            else None,
            _safe_bool(data.get("filesystem_default_fade_in"), False),
        )
        filesystem_default_fade_out = _safe_bool(
            fade_filesystem_payload.get("default_fade_out")
            if isinstance(fade_filesystem_payload, dict)
            else None,
            _safe_bool(data.get("filesystem_default_fade_out"), False),
        )
        streams_default_fade_in = _safe_bool(
            fade_streams_payload.get("default_fade_in")
            if isinstance(fade_streams_payload, dict)
            else None,
            _safe_bool(data.get("streams_default_fade_in"), False),
        )
        streams_default_fade_out = _safe_bool(
            fade_streams_payload.get("default_fade_out")
            if isinstance(fade_streams_payload, dict)
            else None,
            _safe_bool(data.get("streams_default_fade_out"), False),
        )

        view_payload = data.get("view")
        media_library_raw = (
            view_payload.get("media_library_width_percent")
            if isinstance(view_payload, dict)
            else data.get("media_library_width_percent")
        )
        schedule_raw = (
            view_payload.get("schedule_width_percent")
            if isinstance(view_payload, dict)
            else data.get("schedule_width_percent")
        )
        if media_library_raw is None and schedule_raw is not None:
            schedule_width_percent = _safe_panel_percent(schedule_raw, 65)
            media_library_width_percent = 100 - schedule_width_percent
        else:
            media_library_width_percent = _safe_panel_percent(media_library_raw, 35)
        schedule_width_percent = 100 - media_library_width_percent

        font_size: int | None = None
        font_payload = data.get("font")
        if isinstance(view_payload, dict) and "font_size" in view_payload:
            font_size = _safe_positive_int(view_payload.get("font_size"), 10)
        elif isinstance(view_payload, dict) and "font" in view_payload:
            font_size = _safe_positive_int(view_payload.get("font"), 10)
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

        custom_paths_payload = data.get("custom_paths")
        tabs_raw = (
            custom_paths_payload.get("tabs")
            if isinstance(custom_paths_payload, dict)
            else data.get("library_tabs", [])
        )
        extensions_payload = data.get("extensions")
        supported_extensions_raw = (
            extensions_payload.get("supported")
            if isinstance(extensions_payload, dict)
            else data.get("supported_extensions")
        )

        return cls(
            fade_in_duration_seconds=normalized_shared_fade_duration_seconds,
            fade_out_duration_seconds=normalized_shared_fade_duration_seconds,
            filesystem_default_fade_in=filesystem_default_fade_in,
            filesystem_default_fade_out=filesystem_default_fade_out,
            streams_default_fade_in=streams_default_fade_in,
            streams_default_fade_out=streams_default_fade_out,
            media_library_width_percent=media_library_width_percent,
            schedule_width_percent=schedule_width_percent,
            font_size=font_size,
            library_tabs=[
                LibraryTab.from_dict(item)
                for item in tabs_raw
                if isinstance(item, dict)
            ],
            supported_extensions=_normalize_extensions(supported_extensions_raw),
            greenwich_time_signal_enabled=greenwich_time_signal_enabled,
            greenwich_time_signal_path=greenwich_time_signal_path,
        )

    def to_dict(self) -> dict[str, Any]:
        normalized_font_size = max(1, self.font_size if self.font_size is not None else 10)
        normalized_shared_fade_duration_seconds = max(
            1,
            int(self.fade_in_duration_seconds),
            int(self.fade_out_duration_seconds),
        )
        normalized_media_library_width_percent = _safe_panel_percent(
            self.media_library_width_percent,
            35,
        )
        normalized_schedule_width_percent = 100 - normalized_media_library_width_percent
        return {
            "view": {
                "font_size": normalized_font_size,
                "media_library_width_percent": normalized_media_library_width_percent,
                "schedule_width_percent": normalized_schedule_width_percent,
            },
            "fade": {
                "seconds": normalized_shared_fade_duration_seconds,
                "filesystem": {
                    "default_fade_in": bool(self.filesystem_default_fade_in),
                    "default_fade_out": bool(self.filesystem_default_fade_out),
                },
                "streams": {
                    "default_fade_in": bool(self.streams_default_fade_in),
                    "default_fade_out": bool(self.streams_default_fade_out),
                },
            },
            "greenwich_time_signal": {
                "enabled": bool(self.greenwich_time_signal_enabled),
                "path": str(self.greenwich_time_signal_path).strip(),
            },
            "custom_paths": {
                "tabs": [tab.to_dict() for tab in self.library_tabs],
            },
            "extensions": {
                "supported": _normalize_extensions(self.supported_extensions),
            },
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
        if line.startswith("view:"):
            view_data: dict[str, Any] = {}
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
                if normalized_key in {
                    "font_size",
                    "font",
                    "media_library_width_percent",
                    "schedule_width_percent",
                }:
                    try:
                        view_data[normalized_key] = int(raw_value)
                    except ValueError:
                        continue
            if view_data:
                data["view"] = view_data
            continue
        if line.startswith("fade:"):
            raw_value = line.split(":", 1)[1].strip()
            if raw_value:
                try:
                    data["fade"] = int(raw_value)
                except ValueError:
                    pass
                continue
            fade_data: dict[str, Any] = {}
            while index < len(lines):
                detail_line = lines[index].rstrip()
                if not detail_line.startswith("  "):
                    break
                detail = detail_line[2:]
                index += 1
                if detail.startswith("filesystem:"):
                    filesystem_data: dict[str, Any] = {}
                    while index < len(lines):
                        section_line = lines[index].rstrip()
                        if not section_line.startswith("    "):
                            break
                        section_detail = section_line[4:]
                        index += 1
                        if ":" not in section_detail:
                            continue
                        key, value = section_detail.split(":", 1)
                        normalized_key = key.strip()
                        raw_section_value = value.strip()
                        if normalized_key in {"default_fade_in", "default_fade_out"}:
                            filesystem_data[normalized_key] = _safe_bool(raw_section_value, False)
                    if filesystem_data:
                        fade_data["filesystem"] = filesystem_data
                    continue
                if detail.startswith("streams:"):
                    streams_data: dict[str, Any] = {}
                    while index < len(lines):
                        section_line = lines[index].rstrip()
                        if not section_line.startswith("    "):
                            break
                        section_detail = section_line[4:]
                        index += 1
                        if ":" not in section_detail:
                            continue
                        key, value = section_detail.split(":", 1)
                        normalized_key = key.strip()
                        raw_section_value = value.strip()
                        if normalized_key in {"default_fade_in", "default_fade_out"}:
                            streams_data[normalized_key] = _safe_bool(raw_section_value, False)
                    if streams_data:
                        fade_data["streams"] = streams_data
                    continue
                if ":" not in detail:
                    continue
                key, value = detail.split(":", 1)
                normalized_key = key.strip()
                raw_detail_value = value.strip()
                if normalized_key == "seconds":
                    try:
                        fade_data["seconds"] = int(raw_detail_value)
                    except ValueError:
                        continue
            if fade_data:
                data["fade"] = fade_data
            continue
        if line.startswith("fade_in_duration_seconds:"):
            raw_value = line.split(":", 1)[1].strip()
            try:
                data["fade_in_duration_seconds"] = int(raw_value)
            except ValueError:
                pass
            continue
        if line.startswith("fade_in_seconds:"):
            raw_value = line.split(":", 1)[1].strip()
            try:
                data["fade_in_seconds"] = int(raw_value)
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
        if line.startswith("fade_out_seconds:"):
            raw_value = line.split(":", 1)[1].strip()
            try:
                data["fade_out_seconds"] = int(raw_value)
            except ValueError:
                pass
            continue
        if line.startswith("filesystem_default_fade_in:"):
            raw_value = line.split(":", 1)[1].strip()
            data["filesystem_default_fade_in"] = _safe_bool(raw_value, False)
            continue
        if line.startswith("filesystem_default_fade_out:"):
            raw_value = line.split(":", 1)[1].strip()
            data["filesystem_default_fade_out"] = _safe_bool(raw_value, False)
            continue
        if line.startswith("streams_default_fade_in:"):
            raw_value = line.split(":", 1)[1].strip()
            data["streams_default_fade_in"] = _safe_bool(raw_value, False)
            continue
        if line.startswith("streams_default_fade_out:"):
            raw_value = line.split(":", 1)[1].strip()
            data["streams_default_fade_out"] = _safe_bool(raw_value, False)
            continue
        if line.startswith("media_library_width_percent:"):
            raw_value = line.split(":", 1)[1].strip()
            try:
                data["media_library_width_percent"] = int(raw_value)
            except ValueError:
                pass
            continue
        if line.startswith("schedule_width_percent:"):
            raw_value = line.split(":", 1)[1].strip()
            try:
                data["schedule_width_percent"] = int(raw_value)
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
        if line.startswith("custom_paths:"):
            custom_paths_data: dict[str, Any] = {}
            while index < len(lines):
                detail_line = lines[index].rstrip()
                if not detail_line.startswith("  "):
                    break
                detail = detail_line[2:]
                if not detail.startswith("tabs:"):
                    index += 1
                    continue
                index += 1
                tabs: list[dict[str, str]] = []
                while index < len(lines):
                    item_line = lines[index].rstrip()
                    if not item_line.startswith("    - "):
                        break
                    first = item_line[6:]
                    tab: dict[str, str] = {}
                    if ":" in first:
                        key, value = first.split(":", 1)
                        tab[key.strip()] = _parse_scalar(value)
                    index += 1
                    while index < len(lines):
                        sub_line = lines[index].rstrip()
                        if not sub_line.startswith("      "):
                            break
                        sub_detail = sub_line[6:]
                        if ":" in sub_detail:
                            key, value = sub_detail.split(":", 1)
                            tab[key.strip()] = _parse_scalar(value)
                        index += 1
                    tabs.append(tab)
                custom_paths_data["tabs"] = tabs
            if custom_paths_data:
                data["custom_paths"] = custom_paths_data
            continue
        if line.startswith("extensions:"):
            extensions_data: dict[str, Any] = {}
            while index < len(lines):
                detail_line = lines[index].rstrip()
                if not detail_line.startswith("  "):
                    break
                detail = detail_line[2:]
                if not detail.startswith("supported:"):
                    index += 1
                    continue
                index += 1
                supported: list[str] = []
                while index < len(lines):
                    item_line = lines[index].rstrip()
                    if not item_line.startswith("    - "):
                        break
                    token = item_line[6:]
                    supported.append(_parse_scalar(token))
                    index += 1
                extensions_data["supported"] = supported
            if extensions_data:
                data["extensions"] = extensions_data
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
    view_payload = payload.get("view", {})
    fade_payload = payload.get("fade", {})
    filesystem_fade_payload = (
        fade_payload.get("filesystem", {}) if isinstance(fade_payload, dict) else {}
    )
    streams_fade_payload = (
        fade_payload.get("streams", {}) if isinstance(fade_payload, dict) else {}
    )
    greenwich_payload = payload.get("greenwich_time_signal", {})
    custom_paths_payload = payload.get("custom_paths", {})
    extensions_payload = payload.get("extensions", {})

    lines.append("view:")
    lines.append(f"  font_size: {int(view_payload.get('font_size', 10))}")
    lines.append(
        f"  media_library_width_percent: {int(view_payload.get('media_library_width_percent', 35))}"
    )
    lines.append(
        f"  schedule_width_percent: {int(view_payload.get('schedule_width_percent', 65))}"
    )

    lines.append("fade:")
    lines.append(f"  seconds: {int(fade_payload.get('seconds', 5))}")
    lines.append("  filesystem:")
    lines.append(
        "    default_fade_in: "
        f"{'true' if filesystem_fade_payload.get('default_fade_in', False) else 'false'}"
    )
    lines.append(
        "    default_fade_out: "
        f"{'true' if filesystem_fade_payload.get('default_fade_out', False) else 'false'}"
    )
    lines.append("  streams:")
    lines.append(
        "    default_fade_in: "
        f"{'true' if streams_fade_payload.get('default_fade_in', False) else 'false'}"
    )
    lines.append(
        "    default_fade_out: "
        f"{'true' if streams_fade_payload.get('default_fade_out', False) else 'false'}"
    )

    lines.append("greenwich_time_signal:")
    lines.append(
        "  enabled: "
        f"{'true' if greenwich_payload.get('enabled', False) else 'false'}"
    )
    lines.append(
        "  path: "
        f"{_string_as_yaml(str(greenwich_payload.get('path', '')))}"
    )

    lines.append("custom_paths:")
    lines.append("  tabs:")
    for tab in custom_paths_payload.get("tabs", []):
        title = _string_as_yaml(str(tab.get("title", "")))
        path = _string_as_yaml(str(tab.get("path", "")))
        lines.append(f"    - title: {title}")
        lines.append(f"      path: {path}")

    lines.append("extensions:")
    lines.append("  supported:")
    for extension in extensions_payload.get("supported", []):
        lines.append(f"    - {_string_as_yaml(str(extension))}")
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

from __future__ import annotations

import json
from typing import Any

from ._shared import safe_bool


def parse_scalar(token: str) -> str:
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


def parse_settings_yaml(raw_text: str) -> dict[str, Any]:
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
                            filesystem_data[normalized_key] = safe_bool(raw_section_value, False)
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
                            streams_data[normalized_key] = safe_bool(raw_section_value, False)
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
            data["filesystem_default_fade_in"] = safe_bool(raw_value, False)
            continue
        if line.startswith("filesystem_default_fade_out:"):
            raw_value = line.split(":", 1)[1].strip()
            data["filesystem_default_fade_out"] = safe_bool(raw_value, False)
            continue
        if line.startswith("streams_default_fade_in:"):
            raw_value = line.split(":", 1)[1].strip()
            data["streams_default_fade_in"] = safe_bool(raw_value, False)
            continue
        if line.startswith("streams_default_fade_out:"):
            raw_value = line.split(":", 1)[1].strip()
            data["streams_default_fade_out"] = safe_bool(raw_value, False)
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
            data["greenwich_time_signal_enabled"] = safe_bool(raw_value, False)
            continue
        if line.startswith("greenwich_time_signal_path:"):
            raw_value = line.split(":", 1)[1].strip()
            data["greenwich_time_signal_path"] = parse_scalar(raw_value)
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
                    signal_data["enabled"] = safe_bool(raw_value, False)
                elif normalized_key == "path":
                    signal_data["path"] = parse_scalar(raw_value)
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
                        tab[key.strip()] = parse_scalar(value)
                    index += 1
                    while index < len(lines):
                        sub_line = lines[index].rstrip()
                        if not sub_line.startswith("      "):
                            break
                        sub_detail = sub_line[6:]
                        if ":" in sub_detail:
                            key, value = sub_detail.split(":", 1)
                            tab[key.strip()] = parse_scalar(value)
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
                    supported.append(parse_scalar(token))
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
                extensions.append(parse_scalar(token))
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
                    tab[key.strip()] = parse_scalar(value)
                index += 1
                while index < len(lines):
                    detail_line = lines[index].rstrip()
                    if not detail_line.startswith("    "):
                        break
                    detail = detail_line[4:]
                    if ":" in detail:
                        key, value = detail.split(":", 1)
                        tab[key.strip()] = parse_scalar(value)
                    index += 1
                tabs.append(tab)
            data["library_tabs"] = tabs
            continue
    return data

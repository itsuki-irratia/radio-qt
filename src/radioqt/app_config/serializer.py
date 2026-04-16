from __future__ import annotations

import json

from .schema import AppConfig


def string_as_yaml(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def dump_settings_yaml(config: AppConfig) -> str:
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
    audio_payload = payload.get("audio", {})
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
        f"{string_as_yaml(str(greenwich_payload.get('path', '')))}"
    )

    lines.append("audio:")
    lines.append(
        f"  default_volume_percent: {int(audio_payload.get('default_volume_percent', 100))}"
    )

    lines.append("custom_paths:")
    lines.append("  tabs:")
    for tab in custom_paths_payload.get("tabs", []):
        title = string_as_yaml(str(tab.get("title", "")))
        path = string_as_yaml(str(tab.get("path", "")))
        lines.append(f"    - title: {title}")
        lines.append(f"      path: {path}")

    lines.append("extensions:")
    lines.append("  supported:")
    for extension in extensions_payload.get("supported", []):
        lines.append(f"    - {string_as_yaml(str(extension))}")
    lines.append("")
    return "\n".join(lines)

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import DEFAULT_SUPPORTED_EXTENSIONS, LibraryTab
from ..stream_relay import (
    DEFAULT_ICECAST_AUDIO_BITRATE,
    DEFAULT_ICECAST_AUDIO_CHANNELS,
    DEFAULT_ICECAST_AUDIO_CODEC,
    DEFAULT_ICECAST_AUDIO_RATE,
    DEFAULT_ICECAST_CONTENT_TYPE,
    DEFAULT_ICECAST_DEVICE,
    DEFAULT_ICECAST_INPUT_FORMAT,
    DEFAULT_ICECAST_OUTPUT_FORMAT,
    DEFAULT_ICECAST_THREAD_QUEUE_SIZE,
    DEFAULT_ICECAST_URL,
)
from ._shared import normalize_extensions, safe_bool, safe_panel_percent, safe_positive_int


def _safe_volume_percent(value: Any, default: int = 100) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0, min(100, parsed))


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
    default_volume_percent: int = 100
    icecast_status: bool = False
    icecast_run_in_background: bool = False
    icecast_command: str = ""
    icecast_input_format: str = DEFAULT_ICECAST_INPUT_FORMAT
    icecast_thread_queue_size: int = DEFAULT_ICECAST_THREAD_QUEUE_SIZE
    icecast_device: str = DEFAULT_ICECAST_DEVICE
    icecast_audio_channels: int = DEFAULT_ICECAST_AUDIO_CHANNELS
    icecast_audio_rate: int = DEFAULT_ICECAST_AUDIO_RATE
    icecast_audio_codec: str = DEFAULT_ICECAST_AUDIO_CODEC
    icecast_audio_bitrate: int = DEFAULT_ICECAST_AUDIO_BITRATE
    icecast_content_type: str = DEFAULT_ICECAST_CONTENT_TYPE
    icecast_output_format: str = DEFAULT_ICECAST_OUTPUT_FORMAT
    icecast_url: str = DEFAULT_ICECAST_URL

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        fade_payload = data.get("fade")
        if isinstance(fade_payload, dict):
            shared_fade_duration_seconds = safe_positive_int(fade_payload.get("seconds"), 5)
        else:
            shared_fade_duration_seconds = safe_positive_int(fade_payload, 5)
        parsed_fade_in_duration_seconds = safe_positive_int(
            data.get("fade_in_seconds"),
            safe_positive_int(data.get("fade_in_duration_seconds"), shared_fade_duration_seconds),
        )
        parsed_fade_out_duration_seconds = safe_positive_int(
            data.get("fade_out_seconds"),
            safe_positive_int(data.get("fade_out_duration_seconds"), shared_fade_duration_seconds),
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
        filesystem_default_fade_in = safe_bool(
            fade_filesystem_payload.get("default_fade_in")
            if isinstance(fade_filesystem_payload, dict)
            else None,
            safe_bool(data.get("filesystem_default_fade_in"), False),
        )
        filesystem_default_fade_out = safe_bool(
            fade_filesystem_payload.get("default_fade_out")
            if isinstance(fade_filesystem_payload, dict)
            else None,
            safe_bool(data.get("filesystem_default_fade_out"), False),
        )
        streams_default_fade_in = safe_bool(
            fade_streams_payload.get("default_fade_in")
            if isinstance(fade_streams_payload, dict)
            else None,
            safe_bool(data.get("streams_default_fade_in"), False),
        )
        streams_default_fade_out = safe_bool(
            fade_streams_payload.get("default_fade_out")
            if isinstance(fade_streams_payload, dict)
            else None,
            safe_bool(data.get("streams_default_fade_out"), False),
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
            schedule_width_percent = safe_panel_percent(schedule_raw, 65)
            media_library_width_percent = 100 - schedule_width_percent
        else:
            media_library_width_percent = safe_panel_percent(media_library_raw, 35)
        schedule_width_percent = 100 - media_library_width_percent

        font_size: int | None = None
        font_payload = data.get("font")
        if isinstance(view_payload, dict) and "font_size" in view_payload:
            font_size = safe_positive_int(view_payload.get("font_size"), 10)
        elif isinstance(view_payload, dict) and "font" in view_payload:
            font_size = safe_positive_int(view_payload.get("font"), 10)
        if isinstance(font_payload, dict) and "size" in font_payload:
            font_size = safe_positive_int(font_payload.get("size"), 10)
        elif "font_size" in data:
            font_size = safe_positive_int(data.get("font_size"), 10)

        greenwich_time_signal_enabled = safe_bool(
            data.get("greenwich_time_signal_enabled"),
            False,
        )
        greenwich_time_signal_path = str(
            data.get("greenwich_time_signal_path", "") or ""
        ).strip()
        default_volume_percent = _safe_volume_percent(
            data.get("default_volume_percent"),
            100,
        )
        signal_payload = data.get("greenwich_time_signal")
        if isinstance(signal_payload, dict):
            greenwich_time_signal_enabled = safe_bool(
                signal_payload.get("enabled"),
                greenwich_time_signal_enabled,
            )
            greenwich_time_signal_path = str(
                signal_payload.get("path", greenwich_time_signal_path) or ""
            ).strip()
        audio_payload = data.get("audio")
        if isinstance(audio_payload, dict):
            default_volume_percent = _safe_volume_percent(
                audio_payload.get("default_volume_percent"),
                default_volume_percent,
            )

        icecast_status = safe_bool(
            data.get("icecast_status"),
            False,
        )
        icecast_run_in_background = safe_bool(
            data.get("icecast_run_in_background"),
            False,
        )
        icecast_command = str(
            data.get("icecast_command", data.get("stream_relay_command", "")) or ""
        ).strip()
        icecast_input_format = str(
            data.get("icecast_input_format", DEFAULT_ICECAST_INPUT_FORMAT) or ""
        ).strip()
        icecast_thread_queue_size = safe_positive_int(
            data.get("icecast_thread_queue_size"),
            DEFAULT_ICECAST_THREAD_QUEUE_SIZE,
        )
        icecast_device = str(
            data.get("icecast_device", DEFAULT_ICECAST_DEVICE) or ""
        ).strip()
        icecast_audio_channels = safe_positive_int(
            data.get("icecast_audio_channels"),
            DEFAULT_ICECAST_AUDIO_CHANNELS,
        )
        icecast_audio_rate = safe_positive_int(
            data.get("icecast_audio_rate"),
            DEFAULT_ICECAST_AUDIO_RATE,
        )
        icecast_audio_codec = str(
            data.get("icecast_audio_codec", DEFAULT_ICECAST_AUDIO_CODEC) or ""
        ).strip()
        icecast_audio_bitrate = safe_positive_int(
            data.get("icecast_audio_bitrate"),
            DEFAULT_ICECAST_AUDIO_BITRATE,
        )
        icecast_content_type = str(
            data.get("icecast_content_type", DEFAULT_ICECAST_CONTENT_TYPE) or ""
        ).strip()
        icecast_output_format = str(
            data.get("icecast_output_format", DEFAULT_ICECAST_OUTPUT_FORMAT) or ""
        ).strip()
        icecast_url = str(
            data.get("icecast_url", DEFAULT_ICECAST_URL) or ""
        ).strip()
        icecast_payload = data.get("icecast")
        if isinstance(icecast_payload, dict):
            icecast_status = safe_bool(
                icecast_payload.get("status"),
                icecast_status,
            )
            icecast_run_in_background = safe_bool(
                icecast_payload.get("run_in_background"),
                icecast_run_in_background,
            )
            icecast_command = str(
                icecast_payload.get("command", icecast_command) or ""
            ).strip()
            icecast_input_format = str(
                icecast_payload.get("input_format", icecast_input_format) or ""
            ).strip()
            icecast_thread_queue_size = safe_positive_int(
                icecast_payload.get("thread_queue_size"),
                icecast_thread_queue_size,
            )
            icecast_device = str(
                icecast_payload.get("device", icecast_device) or ""
            ).strip()
            icecast_audio_channels = safe_positive_int(
                icecast_payload.get("audio_channels"),
                icecast_audio_channels,
            )
            icecast_audio_rate = safe_positive_int(
                icecast_payload.get("audio_rate"),
                icecast_audio_rate,
            )
            icecast_audio_codec = str(
                icecast_payload.get("audio_codec", icecast_audio_codec) or ""
            ).strip()
            icecast_audio_bitrate = safe_positive_int(
                icecast_payload.get("audio_bitrate"),
                icecast_audio_bitrate,
            )
            icecast_content_type = str(
                icecast_payload.get("content_type", icecast_content_type) or ""
            ).strip()
            icecast_output_format = str(
                icecast_payload.get("output_format", icecast_output_format) or ""
            ).strip()
            icecast_url = str(
                icecast_payload.get("url", icecast_url) or ""
            ).strip()
        stream_relay_payload = data.get("stream_relay")
        if isinstance(stream_relay_payload, dict):
            icecast_command = str(
                stream_relay_payload.get("command", icecast_command) or ""
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
            supported_extensions=normalize_extensions(supported_extensions_raw),
            greenwich_time_signal_enabled=greenwich_time_signal_enabled,
            greenwich_time_signal_path=greenwich_time_signal_path,
            default_volume_percent=default_volume_percent,
            icecast_status=icecast_status,
            icecast_run_in_background=icecast_run_in_background,
            icecast_command=icecast_command,
            icecast_input_format=icecast_input_format or DEFAULT_ICECAST_INPUT_FORMAT,
            icecast_thread_queue_size=max(1, int(icecast_thread_queue_size)),
            icecast_device=icecast_device or DEFAULT_ICECAST_DEVICE,
            icecast_audio_channels=max(1, int(icecast_audio_channels)),
            icecast_audio_rate=max(1, int(icecast_audio_rate)),
            icecast_audio_codec=icecast_audio_codec or DEFAULT_ICECAST_AUDIO_CODEC,
            icecast_audio_bitrate=max(1, int(icecast_audio_bitrate)),
            icecast_content_type=icecast_content_type or DEFAULT_ICECAST_CONTENT_TYPE,
            icecast_output_format=icecast_output_format or DEFAULT_ICECAST_OUTPUT_FORMAT,
            icecast_url=icecast_url or DEFAULT_ICECAST_URL,
        )

    def to_dict(self) -> dict[str, Any]:
        normalized_font_size = max(1, self.font_size if self.font_size is not None else 10)
        normalized_shared_fade_duration_seconds = max(
            1,
            int(self.fade_in_duration_seconds),
            int(self.fade_out_duration_seconds),
        )
        normalized_media_library_width_percent = safe_panel_percent(
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
            "audio": {
                "default_volume_percent": _safe_volume_percent(
                    self.default_volume_percent,
                    100,
                ),
            },
            "icecast": {
                "status": bool(self.icecast_status),
                "run_in_background": bool(self.icecast_run_in_background),
                "command": str(self.icecast_command).strip(),
                "input_format": str(self.icecast_input_format).strip()
                or DEFAULT_ICECAST_INPUT_FORMAT,
                "thread_queue_size": max(
                    1,
                    int(self.icecast_thread_queue_size),
                ),
                "device": str(self.icecast_device).strip() or DEFAULT_ICECAST_DEVICE,
                "audio_channels": max(
                    1,
                    int(self.icecast_audio_channels),
                ),
                "audio_rate": max(
                    1,
                    int(self.icecast_audio_rate),
                ),
                "audio_codec": str(self.icecast_audio_codec).strip()
                or DEFAULT_ICECAST_AUDIO_CODEC,
                "audio_bitrate": max(
                    1,
                    int(self.icecast_audio_bitrate),
                ),
                "content_type": str(self.icecast_content_type).strip()
                or DEFAULT_ICECAST_CONTENT_TYPE,
                "output_format": str(self.icecast_output_format).strip()
                or DEFAULT_ICECAST_OUTPUT_FORMAT,
                "url": str(self.icecast_url).strip() or DEFAULT_ICECAST_URL,
            },
            "custom_paths": {
                "tabs": [tab.to_dict() for tab in self.library_tabs],
            },
            "extensions": {
                "supported": normalize_extensions(self.supported_extensions),
            },
        }

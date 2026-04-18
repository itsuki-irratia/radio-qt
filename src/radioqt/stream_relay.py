from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

STREAM_RELAY_PID_FILE_NAME = "icecast.pid"
STREAM_RELAY_STDOUT_FILE_NAME = "icecast.stdout.log"
STREAM_RELAY_STDERR_FILE_NAME = "icecast.stderr.log"
LEGACY_STREAM_RELAY_PID_FILE_NAME = "stream_relay.pid"
DEFAULT_ICECAST_INPUT_FORMAT = "pulse"
DEFAULT_ICECAST_THREAD_QUEUE_SIZE = 4096
DEFAULT_ICECAST_DEVICE = (
    "alsa_output.usb-Generic_KM_B2_USB_Audio_20210726905926-00.analog-stereo.monitor"
)
DEFAULT_ICECAST_AUDIO_CHANNELS = 2
DEFAULT_ICECAST_AUDIO_RATE = 48000
DEFAULT_ICECAST_AUDIO_CODEC = "libmp3lame"
DEFAULT_ICECAST_AUDIO_BITRATE = 128
DEFAULT_ICECAST_CONTENT_TYPE = "audio/mpeg"
DEFAULT_ICECAST_OUTPUT_FORMAT = "mp3"
DEFAULT_ICECAST_URL = "icecast://source:hackme@localhost:8000/radio.mp3"


@dataclass(slots=True)
class IcecastFfmpegConfig:
    input_format: str = DEFAULT_ICECAST_INPUT_FORMAT
    thread_queue_size: int = DEFAULT_ICECAST_THREAD_QUEUE_SIZE
    device: str = DEFAULT_ICECAST_DEVICE
    audio_channels: int = DEFAULT_ICECAST_AUDIO_CHANNELS
    audio_rate: int = DEFAULT_ICECAST_AUDIO_RATE
    audio_codec: str = DEFAULT_ICECAST_AUDIO_CODEC
    audio_bitrate: int = DEFAULT_ICECAST_AUDIO_BITRATE
    content_type: str = DEFAULT_ICECAST_CONTENT_TYPE
    output_format: str = DEFAULT_ICECAST_OUTPUT_FORMAT
    icecast_url: str = DEFAULT_ICECAST_URL


def _normalized_token(raw_value: object, default: str) -> str:
    token = str(raw_value).strip()
    return token or default


def _normalized_positive_int(raw_value: object, default: int) -> int:
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return int(default)
    return parsed if parsed > 0 else int(default)


def normalized_icecast_ffmpeg_config(config: IcecastFfmpegConfig) -> IcecastFfmpegConfig:
    return IcecastFfmpegConfig(
        input_format=_normalized_token(config.input_format, DEFAULT_ICECAST_INPUT_FORMAT),
        thread_queue_size=_normalized_positive_int(
            config.thread_queue_size,
            DEFAULT_ICECAST_THREAD_QUEUE_SIZE,
        ),
        device=_normalized_token(config.device, DEFAULT_ICECAST_DEVICE),
        audio_channels=_normalized_positive_int(
            config.audio_channels,
            DEFAULT_ICECAST_AUDIO_CHANNELS,
        ),
        audio_rate=_normalized_positive_int(
            config.audio_rate,
            DEFAULT_ICECAST_AUDIO_RATE,
        ),
        audio_codec=_normalized_token(config.audio_codec, DEFAULT_ICECAST_AUDIO_CODEC),
        audio_bitrate=_normalized_positive_int(
            config.audio_bitrate,
            DEFAULT_ICECAST_AUDIO_BITRATE,
        ),
        content_type=_normalized_token(config.content_type, DEFAULT_ICECAST_CONTENT_TYPE),
        output_format=_normalized_token(config.output_format, DEFAULT_ICECAST_OUTPUT_FORMAT),
        icecast_url=_normalized_token(config.icecast_url, DEFAULT_ICECAST_URL),
    )


def build_icecast_ffmpeg_command(config: IcecastFfmpegConfig) -> str:
    normalized = normalized_icecast_ffmpeg_config(config)
    args = [
        "ffmpeg",
        "-f",
        normalized.input_format,
        "-thread_queue_size",
        str(normalized.thread_queue_size),
        "-i",
        normalized.device,
        "-ac",
        str(normalized.audio_channels),
        "-ar",
        str(normalized.audio_rate),
        "-c:a",
        normalized.audio_codec,
        "-b:a",
        f"{int(normalized.audio_bitrate)}k",
        "-content_type",
        normalized.content_type,
        "-f",
        normalized.output_format,
        normalized.icecast_url,
    ]
    return " ".join(shlex.quote(token) for token in args)


def sync_icecast_command_with_generated(
    *,
    current_command: str,
    previous_generated_command: str,
    next_generated_command: str,
) -> str:
    current = str(current_command or "").strip()
    previous_generated = str(previous_generated_command or "").strip()
    next_generated = str(next_generated_command or "").strip()
    if not next_generated:
        return current
    if not current:
        return next_generated
    if not previous_generated:
        return current
    if current == previous_generated:
        return next_generated
    if current.startswith(previous_generated):
        suffix = current[len(previous_generated):]
        if not suffix.strip():
            return next_generated
        if suffix.startswith(" "):
            return f"{next_generated}{suffix}"
        return f"{next_generated} {suffix.lstrip()}"
    return current


def list_pulse_source_devices(*, monitors_only: bool = True) -> list[str]:
    try:
        result = subprocess.run(
            ["pactl", "list", "short", "sources"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    devices: list[str] = []
    for line in result.stdout.splitlines():
        row = line.strip()
        if not row:
            continue
        columns = row.split("\t")
        if len(columns) < 2:
            continue
        device_name = columns[1].strip()
        if not device_name:
            continue
        if monitors_only and not device_name.endswith(".monitor"):
            continue
        if device_name in devices:
            continue
        devices.append(device_name)
    return devices


def stream_relay_pid_file_path(config_dir: Path) -> Path:
    return config_dir.expanduser() / STREAM_RELAY_PID_FILE_NAME


def stream_relay_stdout_file_path(config_dir: Path) -> Path:
    return config_dir.expanduser() / STREAM_RELAY_STDOUT_FILE_NAME


def stream_relay_stderr_file_path(config_dir: Path) -> Path:
    return config_dir.expanduser() / STREAM_RELAY_STDERR_FILE_NAME


def read_stream_relay_pid(config_dir: Path) -> int | None:
    for pid_path in (
        stream_relay_pid_file_path(config_dir),
        config_dir.expanduser() / LEGACY_STREAM_RELAY_PID_FILE_NAME,
    ):
        if not pid_path.is_file():
            continue
        try:
            raw_pid = pid_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        try:
            parsed_pid = int(raw_pid)
        except (TypeError, ValueError):
            continue
        if parsed_pid <= 0:
            continue
        return parsed_pid
    return None


def write_stream_relay_pid(config_dir: Path, pid: int) -> None:
    pid_path = stream_relay_pid_file_path(config_dir)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(f"{int(pid)}\n", encoding="utf-8")


def delete_stream_relay_pid(config_dir: Path) -> bool:
    removed = False
    for pid_path in (
        stream_relay_pid_file_path(config_dir),
        config_dir.expanduser() / LEGACY_STREAM_RELAY_PID_FILE_NAME,
    ):
        if not pid_path.is_file():
            continue
        try:
            pid_path.unlink()
            removed = True
        except OSError:
            continue
    return removed

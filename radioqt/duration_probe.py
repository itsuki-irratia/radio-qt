from __future__ import annotations

import math
from pathlib import Path
import subprocess
from typing import Any

from .library import local_media_path_from_source


def sanitize_duration_probe_cache(
    raw_cache: dict[str, int | None] | None,
    *,
    max_entries: int,
) -> dict[str, int | None]:
    if not isinstance(raw_cache, dict):
        return {}

    normalized: dict[str, int | None] = {}
    for key, value in raw_cache.items():
        if not isinstance(key, str) or not key:
            continue
        if value is None:
            normalized[key] = None
            continue
        try:
            normalized[key] = max(0, int(value))
        except (TypeError, ValueError):
            continue

    while len(normalized) > max_entries:
        oldest_key = next(iter(normalized))
        normalized.pop(oldest_key, None)
    return normalized


def duration_probe_cache_lookup(
    cache: dict[str, int | None],
    probe_key: str,
) -> tuple[bool, int | None]:
    if probe_key not in cache:
        return False, None
    duration = cache.pop(probe_key)
    cache[probe_key] = duration
    return True, duration


def store_duration_probe_cache(
    cache: dict[str, int | None],
    probe_key: str,
    duration: int | None,
    *,
    max_entries: int,
) -> None:
    if probe_key in cache:
        current = cache.pop(probe_key)
        if current == duration:
            cache[probe_key] = current
            return
    cache[probe_key] = duration
    while len(cache) > max_entries:
        oldest_key = next(iter(cache))
        cache.pop(oldest_key, None)


def duration_probe_cache_key_from_path(path: Path) -> str | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = str(path)
    return f"{resolved}|{stat.st_mtime_ns}|{stat.st_size}"


def duration_probe_cache_key_from_source(source: str) -> str | None:
    path = local_media_path_from_source(source)
    if path is None or not path.is_file():
        return None
    return duration_probe_cache_key_from_path(path)


def normalize_probe_duration(duration: Any) -> int | None:
    if isinstance(duration, int):
        return max(0, duration)
    return None


def probe_media_duration_seconds(source: str) -> int | None:
    path = local_media_path_from_source(source)
    if path is None or not path.is_file():
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    value = result.stdout.strip()
    if not value:
        return None
    try:
        return max(0, math.ceil(float(value)))
    except ValueError:
        return None

from __future__ import annotations

import math
import subprocess

from ..library import local_media_path_from_source


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

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl

SUPPORTED_MEDIA_EXTENSIONS = {
    ".aac",
    ".avi",
    ".flac",
    ".flv",
    ".m4a",
    ".m3u",
    ".m3u8",
    ".mkv",
    ".mpg",
    ".mpeg",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".pls",
    ".wav",
    ".webm",
    ".xspf",
}

VIDEO_EXTENSIONS = {".avi", ".mkv", ".mov", ".mp4", ".webm", ".flv"}


def normalize_supported_extensions(extensions: set[str] | list[str] | tuple[str, ...] | None) -> set[str]:
    if extensions is None:
        return set(SUPPORTED_MEDIA_EXTENSIONS)
    normalized: set[str] = set()
    for extension in extensions:
        token = str(extension).strip().lower()
        if not token:
            continue
        if not token.startswith("."):
            token = f".{token}"
        normalized.add(token)
    return normalized or set(SUPPORTED_MEDIA_EXTENSIONS)


def is_supported_media_file(path: Path, *, supported_extensions: set[str] | None = None) -> bool:
    allowed_extensions = normalize_supported_extensions(supported_extensions)
    return path.suffix.lower() in allowed_extensions


def is_stream_source(source: str) -> bool:
    url = QUrl(source)
    return url.isValid() and bool(url.scheme())


def media_source_suffix(source: str) -> str:
    normalized_source = source.strip()
    if not normalized_source:
        return ""

    url = QUrl(normalized_source)
    if url.isValid() and url.scheme():
        if url.scheme().lower() == "file":
            return Path(url.toLocalFile()).suffix.lower()
        return Path(url.path()).suffix.lower()
    return Path(normalized_source).expanduser().suffix.lower()


def media_looks_like_video_source(source: str) -> bool:
    return media_source_suffix(source) in VIDEO_EXTENSIONS


def local_media_path_from_source(source: str) -> Path | None:
    url = QUrl(source)
    if url.isValid() and url.scheme():
        if url.scheme().lower() != "file":
            return None
        local_path = url.toLocalFile()
        return Path(local_path) if local_path else None
    return Path(source).expanduser()

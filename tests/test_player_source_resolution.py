from __future__ import annotations

import os
from pathlib import Path

import pytest

from radioqt.player.controller import MediaPlayerController


def test_resolve_source_accepts_remote_url() -> None:
    source_url, error_message = MediaPlayerController._resolve_source("https://example.com/live")

    assert source_url is not None
    assert error_message is None
    assert source_url.toString().startswith("https://")


def test_resolve_source_rejects_missing_local_file(tmp_path) -> None:
    missing_file = tmp_path / "missing.mp3"

    source_url, error_message = MediaPlayerController._resolve_source(str(missing_file))

    assert source_url is None
    assert isinstance(error_message, str)
    assert "does not exist" in error_message


def test_resolve_source_rejects_directory_path(tmp_path) -> None:
    source_url, error_message = MediaPlayerController._resolve_source(str(tmp_path))

    assert source_url is None
    assert isinstance(error_message, str)
    assert "not a file" in error_message


def test_resolve_source_accepts_existing_local_file(tmp_path) -> None:
    media_file = tmp_path / "clip.mp3"
    media_file.write_bytes(b"ID3")

    source_url, error_message = MediaPlayerController._resolve_source(str(media_file))

    assert source_url is not None
    assert error_message is None
    assert source_url.isLocalFile()
    assert Path(source_url.toLocalFile()).resolve() == media_file.resolve()


def test_resolve_source_rejects_unreadable_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    media_file = tmp_path / "no-read.mp3"
    media_file.write_bytes(b"ID3")

    monkeypatch.setattr(os, "access", lambda *_args, **_kwargs: False)
    source_url, error_message = MediaPlayerController._resolve_source(str(media_file))

    assert source_url is None
    assert isinstance(error_message, str)
    assert "not readable" in error_message


def test_resolve_source_rejects_invalid_file_url() -> None:
    source_url, error_message = MediaPlayerController._resolve_source("file://")

    assert source_url is None
    assert isinstance(error_message, str)
    assert "invalid file URL" in error_message

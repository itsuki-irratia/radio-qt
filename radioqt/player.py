from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget

from .models import MediaItem


class MediaPlayerController(QObject):
    media_started = Signal(object)
    media_finished = Signal()
    playback_state_changed = Signal(object)
    playback_position_changed = Signal(int)
    playback_error = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._audio_output = QAudioOutput(self)
        self._media_player = QMediaPlayer(self)
        self._media_player.setAudioOutput(self._audio_output)
        self._media_player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._media_player.positionChanged.connect(self.playback_position_changed.emit)
        self._media_player.errorOccurred.connect(self._on_error)
        self._media_player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.current_media: MediaItem | None = None
        self._pending_seek_ms: int | None = None

    def set_video_output(self, widget: QVideoWidget) -> None:
        self._media_player.setVideoOutput(widget)

    def play_media(self, media: MediaItem, start_position_ms: int = 0) -> None:
        source_url = self._resolve_source(media.source)
        if source_url is None:
            self.playback_error.emit(f"Cannot resolve media source: {media.source}")
            return
        self._pending_seek_ms = max(0, start_position_ms)
        self.current_media = media
        self._media_player.setSource(source_url)
        if self._pending_seek_ms > 0:
            # Try immediately; some backends need an additional seek after load.
            self._media_player.setPosition(self._pending_seek_ms)
        self._media_player.play()
        self.media_started.emit(media)

    def play(self) -> None:
        self._media_player.play()

    def stop(self) -> None:
        self._media_player.stop()

    def clear_current_media(self) -> None:
        self._media_player.stop()
        self._media_player.setSource(QUrl())
        self.current_media = None
        self._pending_seek_ms = None

    def set_volume(self, volume: int) -> None:
        self._audio_output.setVolume(max(0, min(volume, 100)) / 100.0)

    def is_playing(self) -> bool:
        return self._media_player.playbackState() == QMediaPlayer.PlayingState

    def has_active_media(self) -> bool:
        return self.current_media is not None

    def current_position_ms(self) -> int:
        return max(0, self._media_player.position())

    @staticmethod
    def _resolve_source(source: str) -> QUrl | None:
        source = source.strip()
        if not source:
            return None

        url = QUrl(source)
        if url.isValid() and url.scheme():
            return url

        path = Path(source).expanduser()
        if path.exists():
            return QUrl.fromLocalFile(str(path.resolve()))
        return None

    @Slot(QMediaPlayer.MediaStatus)
    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if self._pending_seek_ms and status in (
            QMediaPlayer.LoadedMedia,
            QMediaPlayer.BufferedMedia,
        ):
            self._media_player.setPosition(self._pending_seek_ms)
            self._pending_seek_ms = None
        if status == QMediaPlayer.EndOfMedia:
            self.media_finished.emit()

    @Slot(QMediaPlayer.PlaybackState)
    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self.playback_state_changed.emit(state)

    def _on_error(self, *_: object) -> None:
        self.playback_error.emit(self._media_player.errorString())

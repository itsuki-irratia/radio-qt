from __future__ import annotations

from array import array
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtMultimedia import QAudioBuffer, QAudioBufferOutput, QAudioFormat, QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget

from .models import MediaItem


class MediaPlayerController(QObject):
    media_started = Signal(object)
    media_finished = Signal()
    playback_state_changed = Signal(object)
    playback_position_changed = Signal(int)
    playback_error = Signal(str)
    audio_levels_changed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._audio_output = QAudioOutput(self)
        self._audio_buffer_output = QAudioBufferOutput(self)
        self._media_player = QMediaPlayer(self)
        self._media_player.setAudioOutput(self._audio_output)
        self._media_player.setAudioBufferOutput(self._audio_buffer_output)
        self._media_player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._media_player.positionChanged.connect(self.playback_position_changed.emit)
        self._media_player.errorOccurred.connect(self._on_error)
        self._media_player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._audio_buffer_output.audioBufferReceived.connect(self._on_audio_buffer_received)
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
        self.audio_levels_changed.emit(None)

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

    @Slot(QAudioBuffer)
    def _on_audio_buffer_received(self, audio_buffer: QAudioBuffer) -> None:
        if not audio_buffer.isValid() or audio_buffer.frameCount() <= 0:
            return
        levels = self._audio_levels_from_buffer(audio_buffer)
        if levels is not None:
            self.audio_levels_changed.emit(levels)

    @staticmethod
    def _audio_levels_from_buffer(audio_buffer: QAudioBuffer, bar_count: int = 36) -> list[float] | None:
        audio_format = audio_buffer.format()
        channel_count = audio_format.channelCount()
        bytes_per_sample = audio_format.bytesPerSample()
        sample_format = audio_format.sampleFormat()
        if channel_count <= 0 or bytes_per_sample <= 0:
            return None

        raw_data = audio_buffer.constData()
        try:
            payload = raw_data.tobytes()
        except AttributeError:
            payload = bytes(raw_data)
        if not payload:
            return None

        samples = MediaPlayerController._normalized_samples_from_payload(
            payload,
            sample_format,
            bytes_per_sample,
            channel_count,
        )
        if not samples:
            return None

        chunk_size = max(1, len(samples) // bar_count)
        levels: list[float] = []
        for index in range(bar_count):
            start = index * chunk_size
            end = len(samples) if index == bar_count - 1 else min(len(samples), start + chunk_size)
            if start >= len(samples):
                levels.append(0.0)
                continue
            chunk = samples[start:end]
            peak = max(abs(value) for value in chunk) if chunk else 0.0
            levels.append(max(0.0, min(1.0, peak)))
        return levels

    @staticmethod
    def _normalized_samples_from_payload(
        payload: bytes,
        sample_format: QAudioFormat.SampleFormat,
        bytes_per_sample: int,
        channel_count: int,
    ) -> list[float]:
        if sample_format == QAudioFormat.UInt8:
            interleaved = [(value - 128) / 128.0 for value in payload]
        elif sample_format == QAudioFormat.Int16 and bytes_per_sample == 2:
            ints = array("h")
            ints.frombytes(payload[: len(payload) - (len(payload) % 2)])
            interleaved = [value / 32768.0 for value in ints]
        elif sample_format == QAudioFormat.Int32 and bytes_per_sample == 4:
            ints = array("i")
            ints.frombytes(payload[: len(payload) - (len(payload) % 4)])
            interleaved = [value / 2147483648.0 for value in ints]
        elif sample_format == QAudioFormat.Float and bytes_per_sample == 4:
            floats = array("f")
            floats.frombytes(payload[: len(payload) - (len(payload) % 4)])
            interleaved = [max(-1.0, min(1.0, value)) for value in floats]
        else:
            return []

        if channel_count == 1:
            return interleaved

        mono_samples: list[float] = []
        for index in range(0, len(interleaved) - channel_count + 1, channel_count):
            frame = interleaved[index:index + channel_count]
            mono_samples.append(sum(frame) / channel_count)
        return mono_samples

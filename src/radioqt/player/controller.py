from __future__ import annotations

from array import array
import os
from pathlib import Path
import shutil
import time

from PySide6.QtCore import QProcess, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtMultimedia import (
    QAudioBuffer,
    QAudioBufferOutput,
    QAudioFormat,
    QAudioOutput,
    QMediaDevices,
    QMediaPlayer,
)
from PySide6.QtMultimediaWidgets import QVideoWidget

from ..models import MediaItem


class MediaPlayerController(QObject):
    _DEFAULT_FADE_DURATION_MS = 5000
    _PLAY_REQUEST_REJECTED_PREFIX = "Play request rejected: "

    media_started = Signal(object)
    media_finished = Signal()
    playback_state_changed = Signal(object)
    playback_position_changed = Signal(int)
    playback_error = Signal(str)
    audio_levels_changed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._media_devices = QMediaDevices(self)
        self._audio_output = self._new_audio_output()
        self._audio_buffer_output = QAudioBufferOutput(self)
        self._media_player = QMediaPlayer(self)
        self._external_audio_process: QProcess | None = None
        self._external_audio_position_timer = QTimer(self)
        self._external_audio_position_timer.setInterval(250)
        self._external_audio_position_timer.timeout.connect(self._on_external_audio_position_tick)
        self._external_audio_started_at_monotonic: float | None = None
        self._external_audio_start_position_ms = 0
        self._external_audio_stop_requested = False
        self._media_player.setAudioOutput(self._audio_output)
        self._media_player.setAudioBufferOutput(self._audio_buffer_output)
        self._media_player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._media_player.positionChanged.connect(self._on_position_changed)
        self._media_player.errorOccurred.connect(self._on_error)
        self._media_player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._audio_buffer_output.audioBufferReceived.connect(self._on_audio_buffer_received)
        self._fade_tick_timer = QTimer(self)
        self._fade_tick_timer.setInterval(100)
        self._fade_tick_timer.timeout.connect(self._on_fade_tick)
        self.current_media: MediaItem | None = None
        self._pending_seek_ms: int | None = None
        self._base_volume_percent = 100
        self._fade_multiplier = 1.0
        self._fade_in_enabled = False
        self._fade_out_enabled = False
        self._fade_session_start_position_ms = 0
        self._expected_duration_ms: int | None = None
        self._fade_in_duration_ms = self._DEFAULT_FADE_DURATION_MS
        self._fade_out_duration_ms = self._DEFAULT_FADE_DURATION_MS
        self._fade_timeline_position_ms = 0
        self._fade_timeline_last_tick_monotonic: float | None = None
        self._media_devices.audioOutputsChanged.connect(self._on_audio_outputs_changed)
        self._apply_effective_volume()

    def set_video_output(self, widget: QVideoWidget) -> None:
        self._media_player.setVideoOutput(widget)

    def play_media(
        self,
        media: MediaItem,
        start_position_ms: int | None = 0,
        *,
        fade_in: bool = False,
        fade_out: bool = False,
        expected_duration_ms: int | None = None,
        fade_in_duration_ms: int = _DEFAULT_FADE_DURATION_MS,
        fade_out_duration_ms: int = _DEFAULT_FADE_DURATION_MS,
    ) -> None:
        source_url, resolve_error = self._resolve_source(media.source)
        if source_url is None:
            self.playback_error.emit(
                f"{self._PLAY_REQUEST_REJECTED_PREFIX}"
                f"{resolve_error or f'Cannot resolve media source: {media.source}'}"
            )
            return
        normalized_start_position_ms = self._normalize_start_position_ms(start_position_ms)
        seek_start_position_ms = normalized_start_position_ms if source_url.isLocalFile() else 0
        current_source = self._media_player.source()
        if current_source.isValid() or self._external_audio_process is not None:
            # Always force a fresh pipeline when changing scheduled media.
            # This avoids backend reuse edge cases (especially with streams).
            self._media_player.stop()
            self._media_player.setSource(QUrl())
            self._stop_external_audio()
        self._configure_fade_for_new_media(
            start_position_ms=seek_start_position_ms,
            fade_in=fade_in,
            fade_out=fade_out,
            expected_duration_ms=expected_duration_ms,
            fade_in_duration_ms=fade_in_duration_ms,
            fade_out_duration_ms=fade_out_duration_ms,
        )
        if self._should_use_external_audio_backend():
            if self._start_external_audio(
                source_url,
                start_position_ms=seek_start_position_ms,
                expected_duration_ms=expected_duration_ms,
            ):
                self._start_qt_media_pipeline(
                    source_url,
                    pending_seek_ms=seek_start_position_ms,
                    video_only=True,
                )
                self.current_media = media
                self.media_started.emit(media)
            return
        self.current_media = media
        self._start_qt_media_pipeline(source_url, pending_seek_ms=seek_start_position_ms)
        self.media_started.emit(media)

    def play(self) -> None:
        if self._external_audio_process is not None:
            return
        self._media_player.play()

    def stop(self) -> None:
        self._media_player.stop()
        self._stop_external_audio()
        self._reset_fade_state()

    def clear_current_media(self) -> None:
        self._media_player.stop()
        self._media_player.setSource(QUrl())
        self._stop_external_audio()
        self.current_media = None
        self._pending_seek_ms = None
        self._reset_fade_state()
        self.audio_levels_changed.emit(None)

    def set_volume(self, volume: int) -> None:
        self._base_volume_percent = max(0, min(volume, 100))
        self._apply_effective_volume()

    def is_playing(self) -> bool:
        if self._external_audio_process is not None:
            return self._external_audio_process.state() == QProcess.Running
        return self._media_player.playbackState() == QMediaPlayer.PlayingState

    def has_active_media(self) -> bool:
        return self.current_media is not None

    def current_position_ms(self) -> int:
        external_position_ms = self._external_audio_current_position_ms()
        if external_position_ms is not None:
            return external_position_ms
        return max(0, self._media_player.position())

    def set_live_fade_out(
        self,
        fade_out: bool,
        *,
        fade_out_duration_ms: int = _DEFAULT_FADE_DURATION_MS,
    ) -> None:
        self._fade_out_enabled = bool(fade_out) and self._expected_duration_ms is not None
        self._fade_out_duration_ms = max(1, int(fade_out_duration_ms))
        if self._fade_in_enabled or self._fade_out_enabled:
            self._fade_timeline_last_tick_monotonic = time.monotonic()
            self._fade_tick_timer.start()
        else:
            self._fade_tick_timer.stop()
            self._fade_timeline_last_tick_monotonic = None
        self._update_fade_multiplier_for_position(
            self.current_position_ms(),
            force_apply=True,
        )

    def set_live_schedule_fade_window(
        self,
        *,
        expected_duration_ms: int | None,
        fade_out: bool,
        fade_out_duration_ms: int = _DEFAULT_FADE_DURATION_MS,
    ) -> None:
        normalized_duration_ms = (
            int(expected_duration_ms) if expected_duration_ms is not None and expected_duration_ms > 0 else None
        )
        self._expected_duration_ms = normalized_duration_ms
        self._fade_out_enabled = bool(fade_out) and normalized_duration_ms is not None
        self._fade_out_duration_ms = max(1, int(fade_out_duration_ms))
        if self._fade_in_enabled or self._fade_out_enabled:
            self._fade_timeline_last_tick_monotonic = time.monotonic()
            self._fade_tick_timer.start()
        else:
            self._fade_tick_timer.stop()
            self._fade_timeline_last_tick_monotonic = None
        self._update_fade_multiplier_for_position(
            self.current_position_ms(),
            force_apply=True,
        )

    def _configure_fade_for_new_media(
        self,
        *,
        start_position_ms: int,
        fade_in: bool,
        fade_out: bool,
        expected_duration_ms: int | None,
        fade_in_duration_ms: int,
        fade_out_duration_ms: int,
    ) -> None:
        normalized_duration_ms = None
        if expected_duration_ms is not None and expected_duration_ms > 0:
            normalized_duration_ms = expected_duration_ms
        normalized_fade_in_duration_ms = max(1, int(fade_in_duration_ms))
        normalized_fade_out_duration_ms = max(1, int(fade_out_duration_ms))

        self._fade_in_enabled = bool(fade_in)
        self._fade_out_enabled = bool(fade_out) and normalized_duration_ms is not None
        self._fade_session_start_position_ms = max(0, start_position_ms)
        self._expected_duration_ms = normalized_duration_ms
        self._fade_in_duration_ms = normalized_fade_in_duration_ms
        self._fade_out_duration_ms = normalized_fade_out_duration_ms
        self._fade_timeline_position_ms = self._fade_session_start_position_ms
        self._fade_timeline_last_tick_monotonic = time.monotonic()
        if self._fade_in_enabled or self._fade_out_enabled:
            self._fade_tick_timer.start()
        else:
            self._fade_tick_timer.stop()
        self._update_fade_multiplier_for_position(
            self._fade_session_start_position_ms,
            force_apply=True,
        )

    def _reset_fade_state(self) -> None:
        self._fade_in_enabled = False
        self._fade_out_enabled = False
        self._fade_session_start_position_ms = 0
        self._expected_duration_ms = None
        self._fade_in_duration_ms = self._DEFAULT_FADE_DURATION_MS
        self._fade_out_duration_ms = self._DEFAULT_FADE_DURATION_MS
        self._fade_timeline_position_ms = 0
        self._fade_timeline_last_tick_monotonic = None
        self._fade_tick_timer.stop()
        self._fade_multiplier = 1.0
        self._apply_effective_volume()

    def _update_fade_multiplier_for_position(
        self,
        position_ms: int,
        *,
        force_apply: bool = False,
    ) -> None:
        multiplier = 1.0
        current_position_ms = max(0, position_ms)
        if self._fade_in_enabled:
            elapsed_ms = max(0, current_position_ms - self._fade_session_start_position_ms)
            multiplier = min(multiplier, elapsed_ms / self._fade_in_duration_ms)
        if self._fade_out_enabled and self._expected_duration_ms is not None:
            remaining_ms = max(0, self._expected_duration_ms - current_position_ms)
            if remaining_ms <= self._fade_out_duration_ms:
                multiplier = min(multiplier, remaining_ms / self._fade_out_duration_ms)
        multiplier = max(0.0, min(1.0, multiplier))
        if not force_apply and abs(multiplier - self._fade_multiplier) < 1e-6:
            return
        self._fade_multiplier = multiplier
        self._apply_effective_volume()

    def _apply_effective_volume(self) -> None:
        effective = (self._base_volume_percent / 100.0) * self._fade_multiplier
        self._audio_output.setVolume(max(0.0, min(1.0, effective)))

    def _start_qt_media_pipeline(
        self,
        source_url: QUrl,
        *,
        pending_seek_ms: int,
        video_only: bool = False,
    ) -> None:
        self._pending_seek_ms = pending_seek_ms
        self._media_player.setAudioOutput(None if video_only else self._audio_output)
        self._media_player.setSource(source_url)
        if pending_seek_ms > 0:
            # Try immediately; some backends need an additional seek after load.
            self._media_player.setPosition(pending_seek_ms)
        self._media_player.play()

    def _new_audio_output(self) -> QAudioOutput:
        default_device = QMediaDevices.defaultAudioOutput()
        if default_device.isNull():
            return QAudioOutput(self)
        return QAudioOutput(default_device, self)

    @staticmethod
    def _qt_audio_output_count() -> int:
        return len(QMediaDevices.audioOutputs())

    @classmethod
    def _should_use_external_audio_backend(cls) -> bool:
        # Keep playback in Qt so runtime fade multipliers always affect
        # the active audio pipeline.
        return False

    @Slot()
    def _on_audio_outputs_changed(self) -> None:
        previous_audio_output = self._audio_output
        self._audio_output = self._new_audio_output()
        self._media_player.setAudioOutput(self._audio_output)
        self._apply_effective_volume()
        previous_audio_output.deleteLater()

    def _start_external_audio(
        self,
        source_url: QUrl,
        *,
        start_position_ms: int,
        expected_duration_ms: int | None,
    ) -> bool:
        self._stop_external_audio()
        source = source_url.toLocalFile() if source_url.isLocalFile() else source_url.toString()
        if not source:
            self.playback_error.emit(f"{self._PLAY_REQUEST_REJECTED_PREFIX}Cannot resolve media source")
            return False

        args = [
            "--no-video",
            "--force-window=no",
            "--input-terminal=no",
            "--audio-client-name=RadioQt",
            f"--volume={max(0, min(100, int(round(self._base_volume_percent))))}",
        ]
        if start_position_ms > 0:
            args.append(f"--start={start_position_ms / 1000:.3f}")
        if expected_duration_ms is not None and expected_duration_ms > 0:
            args.append(f"--length={expected_duration_ms / 1000:.3f}")
        args.append(source)

        process = QProcess(self)
        process.setProgram("mpv")
        process.setArguments(args)
        process.setStandardOutputFile(QProcess.nullDevice())
        process.setStandardErrorFile(QProcess.nullDevice())
        process.finished.connect(self._on_external_audio_finished)
        process.errorOccurred.connect(self._on_external_audio_error)
        self._external_audio_process = process
        self._external_audio_start_position_ms = max(0, start_position_ms)
        self._external_audio_started_at_monotonic = time.monotonic()
        self._external_audio_stop_requested = False
        process.start()
        if not process.waitForStarted(1000):
            self._external_audio_process = None
            self._external_audio_started_at_monotonic = None
            self.playback_error.emit(
                f"{self._PLAY_REQUEST_REJECTED_PREFIX}Could not start external audio backend"
            )
            return False
        self._external_audio_position_timer.start()
        self.playback_state_changed.emit(QMediaPlayer.PlayingState)
        return True

    def _stop_external_audio(self) -> None:
        process = self._external_audio_process
        if process is None:
            return
        self._external_audio_stop_requested = True
        self._external_audio_position_timer.stop()
        if process.state() != QProcess.NotRunning:
            process.terminate()
            if not process.waitForFinished(700):
                process.kill()
                process.waitForFinished(700)
        process.deleteLater()
        self._external_audio_process = None
        self._external_audio_started_at_monotonic = None
        self.playback_state_changed.emit(QMediaPlayer.StoppedState)

    def _external_audio_current_position_ms(self) -> int | None:
        if self._external_audio_process is None or self._external_audio_started_at_monotonic is None:
            return None
        elapsed_ms = int((time.monotonic() - self._external_audio_started_at_monotonic) * 1000)
        return max(0, self._external_audio_start_position_ms + elapsed_ms)

    @Slot()
    def _on_external_audio_position_tick(self) -> None:
        position_ms = self._external_audio_current_position_ms()
        if position_ms is not None:
            self.playback_position_changed.emit(position_ms)

    @Slot(int, QProcess.ExitStatus)
    def _on_external_audio_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        process = self._external_audio_process
        if process is not None:
            process.deleteLater()
        self._external_audio_process = None
        self._external_audio_position_timer.stop()
        self._external_audio_started_at_monotonic = None
        stop_requested = self._external_audio_stop_requested
        self._external_audio_stop_requested = False
        self.playback_state_changed.emit(QMediaPlayer.StoppedState)
        if not stop_requested and exit_code != 0:
            self.playback_error.emit(
                f"External audio backend stopped unexpectedly: exit code {exit_code}"
            )
            return
        if not stop_requested:
            self.media_finished.emit()

    @Slot(QProcess.ProcessError)
    def _on_external_audio_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.FailedToStart:
            self.playback_error.emit(
                f"{self._PLAY_REQUEST_REJECTED_PREFIX}Could not start external audio backend"
            )

    @staticmethod
    def _normalize_start_position_ms(start_position_ms: int | None) -> int:
        try:
            parsed = int(start_position_ms) if start_position_ms is not None else 0
        except (TypeError, ValueError):
            return 0
        return max(0, parsed)

    @staticmethod
    def _resolve_source(source: str) -> tuple[QUrl | None, str | None]:
        source = source.strip()
        if not source:
            return None, "Cannot resolve media source: source is empty"

        url = QUrl(source)
        if url.isValid() and url.scheme():
            if url.scheme().lower() != "file":
                return url, None
            local_file = url.toLocalFile().strip()
            if not local_file:
                return None, f"Cannot resolve media source: invalid file URL '{source}'"
            return MediaPlayerController._resolve_local_file_path(Path(local_file))

        path = Path(source).expanduser()
        return MediaPlayerController._resolve_local_file_path(path)

    @classmethod
    def is_play_request_rejection(cls, message: str) -> bool:
        return str(message).startswith(cls._PLAY_REQUEST_REJECTED_PREFIX)

    @staticmethod
    def _resolve_local_file_path(path: Path) -> tuple[QUrl | None, str | None]:
        try:
            resolved_path = path.resolve()
        except OSError:
            resolved_path = path
        if not resolved_path.exists():
            return None, f"Local media file does not exist: {resolved_path}"
        if not resolved_path.is_file():
            return None, f"Local media source is not a file: {resolved_path}"
        if not os.access(resolved_path, os.R_OK):
            return None, f"Local media file is not readable: {resolved_path}"
        return QUrl.fromLocalFile(str(resolved_path)), None

    @Slot(QMediaPlayer.MediaStatus)
    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status in (
            QMediaPlayer.LoadedMedia,
            QMediaPlayer.BufferedMedia,
        ):
            pending_seek_ms = self._pending_seek_ms
            if pending_seek_ms is not None and pending_seek_ms > 0:
                self._media_player.setPosition(pending_seek_ms)
            self._pending_seek_ms = None
        if status == QMediaPlayer.EndOfMedia and self._external_audio_process is None:
            self.media_finished.emit()

    @Slot(QMediaPlayer.PlaybackState)
    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlayingState and (self._fade_in_enabled or self._fade_out_enabled):
            self._fade_timeline_last_tick_monotonic = time.monotonic()
            if not self._fade_tick_timer.isActive():
                self._fade_tick_timer.start()
        elif state != QMediaPlayer.PlayingState:
            self._fade_tick_timer.stop()
            self._fade_timeline_last_tick_monotonic = None
        self.playback_state_changed.emit(state)

    @Slot(int)
    def _on_position_changed(self, position_ms: int) -> None:
        normalized_position_ms = max(0, int(position_ms))
        if normalized_position_ms > self._fade_timeline_position_ms:
            self._fade_timeline_position_ms = normalized_position_ms
        self._update_fade_multiplier_for_position(self._fade_timeline_position_ms)
        self.playback_position_changed.emit(position_ms)

    @Slot()
    def _on_fade_tick(self) -> None:
        if not (self._fade_in_enabled or self._fade_out_enabled):
            self._fade_tick_timer.stop()
            return
        if self._media_player.playbackState() != QMediaPlayer.PlayingState:
            self._fade_timeline_last_tick_monotonic = time.monotonic()
            return

        now = time.monotonic()
        previous_tick = self._fade_timeline_last_tick_monotonic
        self._fade_timeline_last_tick_monotonic = now
        if previous_tick is None:
            return

        elapsed_ms = max(0, int((now - previous_tick) * 1000))
        if elapsed_ms <= 0:
            return

        reported_position_ms = max(0, self._media_player.position())
        if reported_position_ms > self._fade_timeline_position_ms:
            self._fade_timeline_position_ms = reported_position_ms
        else:
            # Streams can report a static position; keep fade progression aligned with wall time.
            self._fade_timeline_position_ms += min(elapsed_ms, 500)

        effective_position_ms = max(reported_position_ms, self._fade_timeline_position_ms)
        self._update_fade_multiplier_for_position(effective_position_ms)

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

from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QDateTime, QObject, Qt, QTimer, Signal, Slot, QUrl
from PySide6.QtGui import QCloseEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QFileSystemModel,
    QHBoxLayout,
    QMainWindow,
    QTreeView,
    QWidget,
)

from ..library import (
    is_stream_source,
    local_media_path_from_source,
    media_looks_like_video_source,
)
from ..models import (
    AppState,
    CronEntry,
    DEFAULT_SUPPORTED_EXTENSIONS,
    LibraryTab,
    MediaItem,
    QueueItem,
    SCHEDULE_STATUS_DISABLED,
    SCHEDULE_STATUS_FIRED,
    SCHEDULE_STATUS_MISSED,
    ScheduleEntry,
)
from ..player import MediaPlayerController
from ..runtime_status import delete_runtime_lock, mark_runtime_offline
from ..scheduling import (
    DEFAULT_CRON_RUNTIME_LOOKBACK,
    DEFAULT_CRON_RUNTIME_MAX_OCCURRENCES,
    DEFAULT_CRON_RUNTIME_MAX_RECENT_OCCURRENCES,
    RadioScheduler,
    normalize_overdue_one_shots,
)
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
from .handlers import MainWindowHandlersMixin
from .fullscreen_visuals import MainWindowFullscreenVisualsMixin
from .interaction_runtime import MainWindowInteractionRuntimeMixin
from .library_selection import MainWindowLibrarySelectionMixin
from .layout_builders import MainWindowLayoutBuildersMixin
from .playback_handlers import MainWindowPlaybackHandlersMixin
from .schedule_timeline import MainWindowScheduleTimelineMixin
from .settings_logging import MainWindowSettingsLoggingMixin
from .state_persistence import MainWindowStatePersistenceMixin

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "radioqt"


class _DurationProbeDispatcher(QObject):
    probe_finished = Signal(str, str, object, object)


class MainWindow(
    MainWindowLayoutBuildersMixin,
    MainWindowInteractionRuntimeMixin,
    MainWindowFullscreenVisualsMixin,
    MainWindowLibrarySelectionMixin,
    MainWindowScheduleTimelineMixin,
    MainWindowStatePersistenceMixin,
    MainWindowSettingsLoggingMixin,
    MainWindowHandlersMixin,
    MainWindowPlaybackHandlersMixin,
    QMainWindow,
):
    _CRON_RUNTIME_MAX_OCCURRENCES = DEFAULT_CRON_RUNTIME_MAX_OCCURRENCES
    _CRON_RUNTIME_MAX_RECENT_OCCURRENCES = DEFAULT_CRON_RUNTIME_MAX_RECENT_OCCURRENCES
    _CRON_RUNTIME_LOOKBACK = DEFAULT_CRON_RUNTIME_LOOKBACK
    _DURATION_PROBE_CACHE_MAX_ENTRIES = 2000

    def __init__(self, config_dir: Path | None = None) -> None:
        super().__init__()
        self.setWindowFlag(Qt.Window, True)
        self.setWindowFlag(Qt.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.setWindowFlag(Qt.WindowCloseButtonHint, True)
        self.setWindowTitle("RadioQt - Scheduled Multimedia Player")
        self.resize(1280, 820)
        self.setMinimumSize(960, 760)

        self._config_dir = (config_dir or DEFAULT_CONFIG_DIR).expanduser()
        self._state_path = self._config_dir / "db.sqlite"
        self._settings_path = self._config_dir / "settings.yaml"
        self._state_version = 0
        self._legacy_state_path = Path.cwd() / "state" / "radio_state.db"
        self._legacy_state_json_path = Path.cwd() / "state" / "radio_state.json"
        self._media_items: dict[str, MediaItem] = {}
        self._media_duration_cache: dict[str, int | None] = {}
        self._duration_probe_cache: dict[str, int | None] = {}
        self._media_duration_pending: set[str] = set()
        self._schedule_entries: list[ScheduleEntry] = []
        self._cron_entries: list[CronEntry] = []
        self._play_queue: deque[QueueItem] = deque()
        self._library_tab_configs: list[LibraryTab] = []
        self._supported_extensions: list[str] = list(DEFAULT_SUPPORTED_EXTENSIONS)
        self._library_tab_sources: dict[QWidget, tuple[str, QTreeView | None, QFileSystemModel | None]] = {}
        self._custom_library_tab_widgets: list[QWidget] = []
        self._last_source_panel = "filesystem"
        self._automation_playing = False
        self._schedule_auto_focus_enabled = False
        self._logs_visible = True
        self._fade_in_duration_seconds = 5
        self._fade_out_duration_seconds = 5
        self._filesystem_default_fade_in = False
        self._filesystem_default_fade_out = False
        self._streams_default_fade_in = False
        self._streams_default_fade_out = False
        self._greenwich_time_signal_enabled = False
        self._greenwich_time_signal_path = ""
        self._icecast_status = False
        self._icecast_run_in_background = False
        self._icecast_command = ""
        self._icecast_input_format = DEFAULT_ICECAST_INPUT_FORMAT
        self._icecast_thread_queue_size = DEFAULT_ICECAST_THREAD_QUEUE_SIZE
        self._icecast_device = DEFAULT_ICECAST_DEVICE
        self._icecast_audio_channels = DEFAULT_ICECAST_AUDIO_CHANNELS
        self._icecast_audio_rate = DEFAULT_ICECAST_AUDIO_RATE
        self._icecast_audio_codec = DEFAULT_ICECAST_AUDIO_CODEC
        self._icecast_audio_bitrate = DEFAULT_ICECAST_AUDIO_BITRATE
        self._icecast_content_type = DEFAULT_ICECAST_CONTENT_TYPE
        self._icecast_output_format = DEFAULT_ICECAST_OUTPUT_FORMAT
        self._icecast_url = DEFAULT_ICECAST_URL
        self._fullscreen_active = False
        self._schedule_filter_date = datetime.now().astimezone().date()
        self._current_playback_position_ms = 0
        self._shutting_down = False
        self._font_size_points = self._default_font_size_points()
        self._media_library_width_percent = 35
        self._schedule_width_percent = 65
        self._panels_layout: QHBoxLayout | None = None

        self._player = MediaPlayerController(self)
        self._greenwich_time_signal_audio_output = QAudioOutput(self)
        self._greenwich_time_signal_audio_output.setVolume(1.0)
        self._greenwich_time_signal_player = QMediaPlayer(self)
        self._greenwich_time_signal_player.setAudioOutput(
            self._greenwich_time_signal_audio_output
        )
        self._scheduler = RadioScheduler(parent=self)
        self._duration_probe_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="radioqt-duration",
        )
        self._duration_probe_dispatcher = _DurationProbeDispatcher(self)
        self._cron_refresh_timer = QTimer(self)
        self._cron_refresh_timer.setInterval(30000)
        self._schedule_focus_timer = QTimer(self)
        self._schedule_focus_timer.setInterval(1000)
        self._external_state_sync_timer = QTimer(self)
        self._external_state_sync_timer.setInterval(2000)
        self._runtime_control_timer = QTimer(self)
        self._runtime_control_timer.setInterval(250)
        self._greenwich_time_signal_timer = QTimer(self)
        self._greenwich_time_signal_timer.setSingleShot(True)
        self._volume_fade_timer = QTimer(self)
        self._volume_fade_timer.setInterval(40)
        self._volume_fade_started_at = 0.0
        self._volume_fade_duration_ms = 0
        self._volume_fade_start_volume = 0
        self._volume_fade_target_volume = 0
        self._last_nonzero_volume = 100

        self._build_ui()
        self._build_menu_bar()
        self._wire_signals()
        QTimer.singleShot(0, self._finish_startup_load)

    @Slot()
    def _finish_startup_load(self) -> None:
        self._load_initial_state()
        try:
            # At startup we create the lock in offline mode.
            mark_runtime_offline(self._config_dir)
        except OSError as exc:
            self._append_log(f"Failed to write runtime lock file: {exc}")
        self._cron_refresh_timer.start()
        self._schedule_focus_timer.start()
        self._external_state_sync_timer.start()
        self._runtime_control_timer.start()
        self._schedule_next_greenwich_time_signal()

    def _schedule_next_greenwich_time_signal(self) -> None:
        self._greenwich_time_signal_timer.stop()
        now = datetime.now().astimezone()
        next_minute = (now + timedelta(minutes=1)).replace(
            second=0,
            microsecond=0,
        )
        delay_ms = max(
            1000,
            int((next_minute - now).total_seconds() * 1000),
        )
        self._greenwich_time_signal_timer.start(delay_ms)

    def _resolved_greenwich_time_signal_audio_path(self) -> Path | None:
        raw_path = self._greenwich_time_signal_path.strip()
        if not raw_path:
            return None
        path = Path(raw_path).expanduser()
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if not resolved.is_file():
            return None
        return resolved

    @Slot()
    def _on_greenwich_time_signal_timer(self) -> None:
        self._try_play_greenwich_time_signal()
        self._schedule_next_greenwich_time_signal()

    def _try_play_greenwich_time_signal(self) -> None:
        if not self._automation_playing:
            return
        if not self._greenwich_time_signal_enabled:
            return
        audio_path = self._resolved_greenwich_time_signal_audio_path()
        if audio_path is None:
            self._append_log(
                "Greenwich Time Signal is enabled, but the configured audio path is missing or invalid"
            )
            return

        if self._player.is_playing():
            current_media = self._player.current_media
            if (
                current_media is not None
                and is_stream_source(current_media.source)
                and not current_media.greenwich_time_signal_enabled
            ):
                self._append_log(
                    (
                        "Skipped Greenwich Time Signal: active stream "
                        f"'{current_media.title}' has Greenwich Time Signal disabled"
                    )
                )
                return
        try:
            self._greenwich_time_signal_player.stop()
            self._greenwich_time_signal_player.setSource(
                QUrl.fromLocalFile(str(audio_path))
            )
            self._greenwich_time_signal_player.play()
            self._append_log(
                f"Played Greenwich Time Signal from '{audio_path}'"
            )
        except Exception as exc:
            self._append_log(
                f"Failed to play Greenwich Time Signal: {exc}"
            )

    @staticmethod
    def _media_looks_like_video(media: MediaItem | None) -> bool:
        if media is None:
            return False
        return media_looks_like_video_source(media.source)

    @staticmethod
    def _player_media_label(media: MediaItem) -> str:
        local_path = local_media_path_from_source(media.source)
        if local_path is not None:
            expanded = local_path.expanduser()
            try:
                return str(expanded.resolve())
            except OSError:
                return str(expanded)
        return media.title

    def _update_player_visual_state(self) -> None:
        media = self._player.current_media
        if self._media_looks_like_video(media):
            self._player_display_layout.setCurrentWidget(self._video_widget)
            return
        title = self._player_media_label(media) if media is not None else "No media"
        self._waveform_widget.set_media_state(title, self._player.is_playing())
        self._player_display_layout.setCurrentWidget(self._waveform_widget)

    def _fade_in_duration_ms(self) -> int:
        return max(1, self._fade_in_duration_seconds) * 1000

    def _fade_out_duration_ms(self) -> int:
        return max(1, self._fade_out_duration_seconds) * 1000

    def _normalize_overdue_one_shots(
        self,
        reference_time: datetime,
        eligible_statuses: set[str],
    ) -> tuple[int, list[str]]:
        normalized_entries = normalize_overdue_one_shots(
            self._schedule_entries,
            reference_time,
            eligible_statuses,
        )
        details = self._normalized_missed_details(reference_time, normalized_entries)
        return len(normalized_entries), details

    def _normalized_missed_details(
        self,
        reference_time: datetime,
        normalized_entries: list[tuple[ScheduleEntry, datetime, datetime]],
    ) -> list[str]:
        details: list[str] = []
        for entry, start_at, end_at in normalized_entries[:5]:
            start_label = start_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            end_label = end_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            now_label = reference_time.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            details.append(
                f"Marked missed '{self._media_log_name(entry.media_id)}': "
                f"start={start_label}, end={end_label}, checked_at={now_label}"
            )
        return details

    def _append_normalized_missed_logs(self, normalized: int, details: list[str]) -> None:
        for detail in details:
            self._append_log(detail)
        remaining = normalized - len(details)
        if remaining > 0:
            self._append_log(f"Marked missed details omitted for {remaining} additional item(s)")

    def _update_now_playing_label(self) -> None:
        media = self._player.current_media
        if media is None:
            self._now_playing_label.setText("None")
            return
        elapsed_seconds = max(0, self._current_playback_position_ms // 1000)
        media_label = self._player_media_label(media)
        self._now_playing_label.setText(
            f"{media_label} - {self._format_duration(elapsed_seconds)}"
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        self._shutting_down = True
        self._scheduler.stop()
        self._greenwich_time_signal_timer.stop()
        self._external_state_sync_timer.stop()
        self._runtime_control_timer.stop()
        self._greenwich_time_signal_player.stop()
        self._volume_fade_timer.stop()
        self._duration_probe_executor.shutdown(wait=False, cancel_futures=True)
        try:
            delete_runtime_lock(self._config_dir)
        except OSError:
            pass
        if not self._icecast_run_in_background:
            # On GUI shutdown, stop any running Icecast relay process so ffmpeg
            # does not continue detached after the app closes.
            previous_icecast_status = bool(self._icecast_status)
            self._icecast_status = False
            self._synchronize_icecast_runtime(reason="shutdown")
            self._icecast_status = previous_icecast_status
        self._save_settings()
        self._save_state()
        super().closeEvent(event)

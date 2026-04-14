from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, QDateTime, QObject, QSize, Qt, QTimer, Signal, Slot, QEvent, QUrl
from PySide6.QtGui import QAction, QBrush, QCloseEvent, QColor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDateEdit,
    QFileSystemModel,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QSlider,
    QStyle,
    QStackedLayout,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..duration_probe import (
    duration_probe_cache_key_from_path,
    duration_probe_cache_key_from_source,
    duration_probe_cache_lookup,
    normalize_probe_duration,
    probe_media_duration_seconds,
    store_duration_probe_cache,
)
from ..library import (
    is_stream_source,
    local_media_path_from_source,
    media_looks_like_video_source,
    selected_filesystem_media_id,
    selected_url_media_id,
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
    SCHEDULE_STATUS_PENDING,
    ScheduleEntry,
)
from ..player import MediaPlayerController
from ..scheduling import (
    RadioScheduler,
    active_schedule_entry_at,
    current_schedule_entry_for_playback,
    normalize_overdue_one_shots,
    normalized_start,
    next_cron_occurrence,
    refresh_cron_schedule_entries,
    runtime_cron_dates,
    schedule_entry_palette_tokens,
    schedule_entry_end_at,
    schedule_entry_window_details,
    visible_schedule_entries,
)
from ..ui_components import (
    FullscreenOverlay,
    ScheduleDialog,
    WaveformWidget,
    refresh_cron_table,
    refresh_schedule_table,
    refresh_urls_table,
)
from .handlers import MainWindowHandlersMixin
from .fullscreen_visuals import MainWindowFullscreenVisualsMixin
from .playback_handlers import MainWindowPlaybackHandlersMixin
from .settings_logging import MainWindowSettingsLoggingMixin
from .state_persistence import MainWindowStatePersistenceMixin


class _DurationProbeDispatcher(QObject):
    probe_finished = Signal(str, str, object, object)


class MainWindow(
    MainWindowFullscreenVisualsMixin,
    MainWindowStatePersistenceMixin,
    MainWindowSettingsLoggingMixin,
    MainWindowHandlersMixin,
    MainWindowPlaybackHandlersMixin,
    QMainWindow,
):
    _CRON_RUNTIME_MAX_OCCURRENCES = 100
    _CRON_RUNTIME_MAX_RECENT_OCCURRENCES = 20
    _CRON_RUNTIME_LOOKBACK = timedelta(hours=1)
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

        self._config_dir = (config_dir or (Path.cwd() / "config")).expanduser()
        self._state_path = self._config_dir / "db.sqlite"
        self._settings_path = self._config_dir / "settings.yaml"
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
        self._cron_refresh_timer.start()
        self._schedule_focus_timer.start()
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
    def _make_tab_marker(marker_color: str) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        marker = QLabel(container)
        marker.setFixedSize(QSize(8, 8))
        marker.setStyleSheet(
            f"background-color: {marker_color}; border: 1px solid rgba(0, 0, 0, 0.25);"
        )
        layout.addWidget(marker)
        return container

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

    def _build_ui(self) -> None:
        root = QWidget(self)
        root_layout = QVBoxLayout(root)

        self._player_display = QWidget(root)
        self._player_display_layout = QStackedLayout(self._player_display)
        self._player_display_layout.setContentsMargins(0, 0, 0, 0)

        self._video_widget = QVideoWidget(self._player_display)
        self._video_widget.setMinimumHeight(180)
        self._player.set_video_output(self._video_widget)
        self._waveform_widget = WaveformWidget(self._player_display)
        self._player_display_layout.addWidget(self._video_widget)
        self._player_display_layout.addWidget(self._waveform_widget)
        self._player_display_layout.setCurrentWidget(self._waveform_widget)

        self._now_playing_label = QLabel("None", root)

        now_playing_layout = QHBoxLayout()
        now_playing_layout.addWidget(self._now_playing_label)
        now_playing_layout.addStretch()

        playback_controls_layout = QHBoxLayout()
        control_button_size = QSize(36, 36)
        control_button_icon_size = QSize(20, 20)
        self._play_button = QPushButton("")
        self._play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self._play_button.setToolTip("Play")
        self._stop_button = QPushButton("")
        self._stop_button.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        self._stop_button.setToolTip("Stop")
        self._set_automation_status(self._automation_playing)
        self._mute_button = QPushButton("")
        self._mute_button.setCheckable(True)
        self._mute_button.setToolTip("Mute")
        self._mute_button.setIcon(self.style().standardIcon(QStyle.SP_MediaVolume))
        self._fade_in_button = QPushButton("")
        self._fade_in_button.setToolTip("Fade In")
        self._fade_in_button.setIcon(self.style().standardIcon(QStyle.SP_ArrowUp))
        self._fade_out_button = QPushButton("")
        self._fade_out_button.setToolTip("Fade Out")
        self._fade_out_button.setIcon(self.style().standardIcon(QStyle.SP_ArrowDown))
        for button in (
            self._play_button,
            self._stop_button,
            self._mute_button,
            self._fade_in_button,
            self._fade_out_button,
        ):
            button.setFixedSize(control_button_size)
            button.setIconSize(control_button_icon_size)
        playback_controls_layout.addWidget(self._play_button)
        playback_controls_layout.addWidget(self._stop_button)
        playback_controls_layout.addSpacing(control_button_size.width())
        playback_controls_layout.addWidget(self._mute_button)
        playback_controls_layout.addWidget(self._fade_in_button)
        playback_controls_layout.addWidget(self._fade_out_button)
        playback_controls_layout.addWidget(QLabel("Volume"))
        self._volume_slider = QSlider(Qt.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(100)
        self._volume_slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        playback_controls_layout.addWidget(self._volume_slider, 1)
        self._volume_label = QLabel("100%")
        playback_controls_layout.addWidget(self._volume_label)
        self._volume_slider.valueChanged.connect(self._on_volume_slider_value_changed)

        self._panels_layout = QHBoxLayout()
        self._panels_layout.addWidget(self._build_library_panel(), self._media_library_width_percent)
        self._panels_layout.addWidget(self._build_schedule_panel(), self._schedule_width_percent)

        self._logs_group = QGroupBox("Logs", root)
        logs_layout = QVBoxLayout(self._logs_group)
        logs_layout.setContentsMargins(8, 8, 8, 8)

        self._log_view = QPlainTextEdit(self._logs_group)
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(2000)
        self._log_view.setPlaceholderText("Runtime events...")
        self._log_view.setMinimumHeight(80)
        logs_layout.addWidget(self._log_view)

        root_layout.addWidget(self._player_display, 2)
        root_layout.addLayout(now_playing_layout)
        root_layout.addLayout(playback_controls_layout)
        root_layout.addLayout(self._panels_layout, 7)
        root_layout.addWidget(self._logs_group)

        self.setCentralWidget(root)
        # Fullscreen overlay for audio-only playback
        self._fullscreen_overlay = FullscreenOverlay(self)

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("&File")
        self._configuration_action = QAction("&Settings...", self)
        file_menu.addAction(self._configuration_action)
        view_menu = menu_bar.addMenu("&View")
        self._toggle_logs_action = QAction("&Logs", self)
        self._toggle_logs_action.setCheckable(True)
        self._toggle_logs_action.setChecked(self._logs_visible)
        view_menu.addAction(self._toggle_logs_action)
        help_menu = menu_bar.addMenu("&Help")
        self._cron_help_action = QAction("&CRON", self)
        self._export_logs_action = QAction("Export &Logs...", self)
        help_menu.addAction(self._export_logs_action)
        help_menu.addAction(self._cron_help_action)

    def _build_library_panel(self) -> QWidget:
        group = QGroupBox("Media Library")
        layout = QVBoxLayout(group)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 8, 8, 8)

        self._library_tabs = QTabWidget(group)
        self._library_tab_sources = {}
        self._custom_library_tab_widgets = []

        # --- Filesystem tab ---
        self._filesystem_tab_widget, self._filesystem_view, self._filesystem_model = (
            self._create_filesystem_tab_widget("/", self._library_tabs)
        )
        self._library_tabs.addTab(self._filesystem_tab_widget, "Filesystem")
        self._library_tab_sources[self._filesystem_tab_widget] = (
            "filesystem",
            self._filesystem_view,
            self._filesystem_model,
        )

        # --- Streams tab ---
        self._streamings_tab_widget = QWidget()
        streamings_layout = QVBoxLayout(self._streamings_tab_widget)
        streamings_layout.setContentsMargins(8, 8, 8, 8)

        streamings_header_row = QHBoxLayout()
        streamings_header_row.addWidget(QLabel("Streams", self._streamings_tab_widget))
        streamings_header_row.addStretch()
        self._add_stream_button = QPushButton("+", self._streamings_tab_widget)
        self._add_stream_button.setToolTip("Add Streaming")
        self._add_stream_button.setFixedSize(QSize(30, 30))
        streamings_header_row.addWidget(self._add_stream_button)
        streamings_layout.addLayout(streamings_header_row)

        self._urls_table = QTableWidget(self._streamings_tab_widget)
        self._urls_table.setColumnCount(3)
        self._urls_table.setHorizontalHeaderLabels(["Title", "URL", "Greenwich Time Signal"])
        self._urls_table.horizontalHeader().setStretchLastSection(False)
        self._urls_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._urls_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._urls_table.setAlternatingRowColors(True)
        self._urls_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._urls_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self._urls_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._urls_table.customContextMenuRequested.connect(self._on_urls_context_menu)
        streamings_layout.addWidget(self._urls_table)

        self._library_tabs.addTab(self._streamings_tab_widget, "Streams")
        self._library_tab_sources[self._streamings_tab_widget] = ("urls", None, None)

        self._add_schedule_button = QPushButton("Schedule Selected Media")
        self._add_schedule_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout.addWidget(self._library_tabs)
        layout.addWidget(self._add_schedule_button)
        return group

    @staticmethod
    def _normalize_library_tab_path(path: str) -> str:
        expanded = Path(path).expanduser()
        try:
            return str(expanded.resolve())
        except OSError:
            return str(expanded)

    @staticmethod
    def _normalize_supported_extensions(raw_extensions: list[str]) -> list[str]:
        normalized_extensions: list[str] = []
        seen: set[str] = set()
        for raw_extension in raw_extensions:
            token = str(raw_extension).strip().lower().lstrip(".")
            if not token or not all(char.isalnum() for char in token):
                continue
            if token in seen:
                continue
            seen.add(token)
            normalized_extensions.append(token)
        return normalized_extensions or list(DEFAULT_SUPPORTED_EXTENSIONS)

    def _supported_extension_suffixes(self) -> set[str]:
        return {f".{extension}" for extension in self._supported_extensions}

    def _filesystem_name_filters(self) -> list[str]:
        filters: list[str] = []
        for extension in self._supported_extensions:
            case_insensitive_extension = "".join(
                f"[{char.lower()}{char.upper()}]" if char.isalpha() else char
                for char in extension
            )
            filters.append(f"*.{case_insensitive_extension}")
        return filters or ["*"]

    def _apply_supported_extensions_to_model(self, filesystem_model: QFileSystemModel) -> None:
        filesystem_model.setNameFilterDisables(False)
        filesystem_model.setNameFilters(self._filesystem_name_filters())

    def _apply_supported_extensions_to_filesystem_models(self) -> None:
        applied_models: set[int] = set()
        for panel_kind, _, filesystem_model in self._library_tab_sources.values():
            if panel_kind != "filesystem" or filesystem_model is None:
                continue
            model_id = id(filesystem_model)
            if model_id in applied_models:
                continue
            self._apply_supported_extensions_to_model(filesystem_model)
            applied_models.add(model_id)

    def _create_filesystem_tab_widget(
        self,
        root_path: str,
        parent: QWidget,
    ) -> tuple[QWidget, QTreeView, QFileSystemModel]:
        filesystem_tab = QWidget(parent)
        filesystem_layout = QVBoxLayout(filesystem_tab)
        filesystem_layout.setContentsMargins(8, 8, 8, 8)

        normalized_root = self._normalize_library_tab_path(root_path)
        filesystem_model = QFileSystemModel(filesystem_tab)
        self._apply_supported_extensions_to_model(filesystem_model)
        root_index = filesystem_model.setRootPath(normalized_root)

        filesystem_view = QTreeView(filesystem_tab)
        filesystem_view.setModel(filesystem_model)
        if not root_index.isValid():
            root_index = filesystem_model.setRootPath("/")
        filesystem_view.setRootIndex(root_index)
        filesystem_view.setSelectionMode(QAbstractItemView.SingleSelection)
        filesystem_view.setAlternatingRowColors(True)
        filesystem_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        for column in (1, 2, 3):
            filesystem_view.hideColumn(column)
        filesystem_layout.addWidget(filesystem_view)

        return filesystem_tab, filesystem_view, filesystem_model

    def _rebuild_custom_library_tabs(self) -> None:
        current_widget = self._library_tabs.currentWidget()

        for custom_tab in self._custom_library_tab_widgets:
            tab_index = self._library_tabs.indexOf(custom_tab)
            if tab_index >= 0:
                self._library_tabs.removeTab(tab_index)
            self._library_tab_sources.pop(custom_tab, None)
            custom_tab.deleteLater()
        self._custom_library_tab_widgets = []

        valid_custom_tabs: list[LibraryTab] = []

        for tab_config in self._library_tab_configs:
            title = tab_config.title.strip()
            path = self._normalize_library_tab_path(tab_config.path)
            if not title:
                continue
            if not Path(path).is_dir():
                continue

            tab_widget, filesystem_view, filesystem_model = self._create_filesystem_tab_widget(
                path,
                self._library_tabs,
            )
            self._library_tabs.addTab(tab_widget, title)
            self._custom_library_tab_widgets.append(tab_widget)
            self._library_tab_sources[tab_widget] = ("filesystem", filesystem_view, filesystem_model)
            valid_custom_tabs.append(LibraryTab(title=title, path=path))

        self._library_tab_configs = valid_custom_tabs
        if current_widget is not None:
            current_index = self._library_tabs.indexOf(current_widget)
            if current_index >= 0:
                self._library_tabs.setCurrentIndex(current_index)

        self._on_library_tab_changed(self._library_tabs.currentIndex())

    def _build_schedule_panel(self) -> QWidget:
        group = QGroupBox("Schedule")
        layout = QVBoxLayout(group)

        # Tabs for different scheduling types
        self._schedule_tabs = QTabWidget(group)

        # --- Datetime tab (existing UI) ---
        datetime_tab = QWidget()
        datetime_layout = QVBoxLayout(datetime_tab)

        filter_row = QHBoxLayout()
        self._schedule_date_selector = QDateEdit(datetime_tab)
        self._schedule_date_selector.setCalendarPopup(True)
        self._schedule_date_selector.setDisplayFormat("yyyy-MM-dd")
        self._schedule_date_selector.setDate(
            QDate(
                self._schedule_filter_date.year,
                self._schedule_filter_date.month,
                self._schedule_filter_date.day,
            )
        )
        filter_row.addWidget(QLabel("Date"))
        filter_row.addWidget(self._schedule_date_selector)
        self._schedule_focus_checkbox = QCheckBox("Focus current program")
        self._schedule_focus_checkbox.setToolTip(
            "Automatically keep the current schedule entry selected in the table."
        )
        filter_row.addWidget(self._schedule_focus_checkbox)
        filter_row.addStretch()

        self._schedule_table = QTableWidget(datetime_tab)
        self._schedule_table.setColumnCount(6)
        self._schedule_table.setHorizontalHeaderLabels(
            ["Start Time", "Duration", "Media", "Fade In", "Fade Out", "Status"]
        )
        self._schedule_table.horizontalHeader().setStretchLastSection(False)
        self._schedule_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._schedule_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._schedule_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._schedule_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._schedule_table.customContextMenuRequested.connect(self._on_schedule_context_menu)

        datetime_layout.addLayout(filter_row)
        datetime_layout.addWidget(self._schedule_table)
        self._schedule_tabs.addTab(datetime_tab, "Date Time")

        # --- CRON tab (placeholder for CRON-based scheduling) ---
        cron_tab = QWidget()
        cron_layout = QVBoxLayout(cron_tab)

        self._cron_table = QTableWidget(cron_tab)
        self._cron_table.setColumnCount(5)
        self._cron_table.setHorizontalHeaderLabels(
            ["CRON", "Media", "Fade In", "Fade Out", "Status"]
        )
        self._cron_table.horizontalHeader().setStretchLastSection(False)
        self._cron_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._cron_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._cron_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._cron_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._cron_table.customContextMenuRequested.connect(self._on_cron_context_menu)

        cron_buttons_row = QHBoxLayout()
        self._add_cron_button = QPushButton("Add CRON Schedule")
        cron_buttons_row.addWidget(self._add_cron_button)

        cron_layout.addWidget(self._cron_table)
        cron_layout.addLayout(cron_buttons_row)
        cron_tab_index = self._schedule_tabs.addTab(cron_tab, "CRON")
        self._schedule_tabs.tabBar().setTabButton(
            cron_tab_index,
            self._schedule_tabs.tabBar().ButtonPosition.RightSide,
            self._make_tab_marker("#ffd166"),
        )

        layout.addWidget(self._schedule_tabs)
        return group

    def _wire_signals(self) -> None:
        self._filesystem_view.clicked.connect(self._on_filesystem_selected)
        self._urls_table.itemSelectionChanged.connect(self._on_urls_selection_changed)
        self._library_tabs.currentChanged.connect(self._on_library_tab_changed)
        self._add_stream_button.clicked.connect(self._add_media_url)

        self._add_schedule_button.clicked.connect(self._add_schedule_entry)
        self._add_cron_button.clicked.connect(self._add_cron_schedule)
        self._schedule_date_selector.dateChanged.connect(self._on_schedule_filter_date_changed)
        self._schedule_focus_checkbox.toggled.connect(self._on_schedule_auto_focus_toggled)
        self._schedule_table.cellPressed.connect(self._on_schedule_table_cell_pressed)

        self._play_button.clicked.connect(self._on_play_clicked)
        self._stop_button.clicked.connect(self._on_stop_clicked)
        self._mute_button.toggled.connect(self._on_mute_toggled)
        self._fade_in_button.clicked.connect(self._on_volume_fade_in_clicked)
        self._fade_out_button.clicked.connect(self._on_volume_fade_out_clicked)
        self._volume_slider.valueChanged.connect(self._player.set_volume)
        self._volume_fade_timer.timeout.connect(self._on_volume_fade_tick)

        self._player.media_started.connect(self._on_media_started)
        self._player.media_finished.connect(self._on_media_finished)
        self._player.playback_state_changed.connect(self._on_playback_state_changed)
        self._player.playback_position_changed.connect(self._on_playback_position_changed)
        self._player.playback_error.connect(self._on_player_error)
        self._player.audio_levels_changed.connect(self._on_audio_levels_changed)
        self._duration_probe_dispatcher.probe_finished.connect(self._on_media_duration_probed)

        self._scheduler.schedule_triggered.connect(self._on_schedule_triggered)
        self._scheduler.log.connect(self._append_log)
        self._cron_refresh_timer.timeout.connect(self._refresh_cron_runtime_window)
        self._schedule_focus_timer.timeout.connect(self._refresh_schedule_auto_focus)
        self._greenwich_time_signal_timer.timeout.connect(self._on_greenwich_time_signal_timer)
        self._configuration_action.triggered.connect(self._open_configuration_dialog)
        self._toggle_logs_action.toggled.connect(self._on_logs_visibility_toggled)
        self._export_logs_action.triggered.connect(self._export_logs)
        self._cron_help_action.triggered.connect(self._show_cron_help)
        # Sync fullscreen button with video widget state
        try:
            self._video_widget.fullScreenChanged.connect(self._on_video_fullscreen_changed)
        except Exception:
            # Some platforms/versions may differ; ignore if not present
            pass
        # Install event filters so Escape key will reliably exit fullscreen
        self.installEventFilter(self)
        try:
            self._video_widget.installEventFilter(self)
        except Exception:
            pass
        try:
            self._waveform_widget.installEventFilter(self)
        except Exception:
            pass
        try:
            self._fullscreen_overlay.installEventFilter(self)
        except Exception:
            pass

    def eventFilter(self, obj: object, event: object) -> bool:
        # Catch keyboard/mouse shortcuts for fullscreen handling.
        try:
            if isinstance(event, QEvent) and event.type() == QEvent.KeyPress:
                from PySide6.QtGui import QKeyEvent

                if isinstance(event, QKeyEvent) and event.key() == Qt.Key_Escape:
                    self._ensure_exit_fullscreen()
                    return True
            if (
                obj in (self._video_widget, self._waveform_widget, self._fullscreen_overlay)
                and isinstance(event, QEvent)
                and event.type() == QEvent.MouseButtonDblClick
            ):
                self._toggle_fullscreen()
                return True
        except Exception:
            pass
        return super().eventFilter(obj, event)

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

    def _refresh_urls_list(self) -> None:
        refresh_urls_table(
            self._urls_table,
            self._media_items,
            is_stream_source=is_stream_source,
            on_greenwich_time_signal_changed=self._on_stream_greenwich_time_signal_changed,
        )

    def _refresh_cron_table(self) -> None:
        refresh_cron_table(
            self._cron_table,
            self._cron_entries,
            self._media_items,
            on_fade_in_changed=self._on_cron_fade_in_changed,
            on_fade_out_changed=self._on_cron_fade_out_changed,
            on_status_changed=self._on_cron_status_changed,
        )

    def _cron_entry_by_id(self, cron_id: str | None) -> CronEntry | None:
        if cron_id is None:
            return None
        for entry in self._cron_entries:
            if entry.id == cron_id:
                return entry
        return None

    def _schedule_entry_by_id(self, entry_id: str | None) -> ScheduleEntry | None:
        if entry_id is None:
            return None
        for entry in self._schedule_entries:
            if entry.id == entry_id:
                return entry
        return None

    def _next_scheduled_start(self, entry: ScheduleEntry) -> datetime | None:
        ordered = sorted(
            self._schedule_entries,
            key=lambda candidate: self._normalized_start(candidate.start_at),
        )
        for index, candidate in enumerate(ordered):
            if candidate.id != entry.id:
                continue
            if index + 1 >= len(ordered):
                return None
            return self._normalized_start(ordered[index + 1].start_at)
        return None

    def _next_scheduled_gap_ms(self, entry: ScheduleEntry) -> int | None:
        next_start = self._next_scheduled_start(entry)
        if next_start is None:
            return None
        start_at = self._normalized_start(entry.start_at)
        gap_ms = max(0, int((next_start - start_at).total_seconds() * 1000))
        if gap_ms <= 0:
            return None
        return gap_ms

    def _is_open_ended_stream_entry(
        self,
        entry: ScheduleEntry,
        media: MediaItem | None = None,
    ) -> bool:
        resolved_media = media or self._media_items.get(entry.media_id)
        if resolved_media is None:
            return False
        return (
            is_stream_source(resolved_media.source)
            and local_media_path_from_source(resolved_media.source) is None
        )

    def _entry_duration_ms(self, entry: ScheduleEntry | None) -> int | None:
        if entry is None:
            return None

        media_duration_ms: int | None = None
        if entry.duration is not None and entry.duration > 0:
            media_duration_ms = entry.duration * 1000

        next_gap_ms = self._next_scheduled_gap_ms(entry)
        if media_duration_ms is not None and next_gap_ms is not None:
            return min(media_duration_ms, next_gap_ms)

        if media_duration_ms is not None:
            return media_duration_ms

        if next_gap_ms is not None:
            return next_gap_ms

        if self._is_open_ended_stream_entry(entry):
            return None
        return None

    def _enforce_hard_sync_always(self) -> bool:
        changed = False
        for cron_entry in self._cron_entries:
            if cron_entry.hard_sync:
                continue
            cron_entry.hard_sync = True
            changed = True
        for schedule_entry in self._schedule_entries:
            if schedule_entry.hard_sync and schedule_entry.cron_hard_sync_override is None:
                continue
            schedule_entry.hard_sync = True
            schedule_entry.cron_hard_sync_override = None
            changed = True
        return changed

    def _is_schedule_entry_protected_from_removal(self, entry: ScheduleEntry) -> bool:
        cron_entry = self._cron_entry_by_id(entry.cron_id)
        return cron_entry is not None and cron_entry.enabled

    @staticmethod
    def _runtime_cron_dates() -> set[date]:
        return runtime_cron_dates(datetime.now().astimezone())

    def _refresh_cron_schedule_entries(self, target_dates: set[date] | None = None) -> None:
        self._schedule_entries = refresh_cron_schedule_entries(
            self._schedule_entries,
            self._cron_entries,
            target_dates=target_dates,
            now=datetime.now().astimezone(),
            runtime_lookback=self._CRON_RUNTIME_LOOKBACK,
            max_occurrences=self._CRON_RUNTIME_MAX_OCCURRENCES,
            max_recent_occurrences=self._CRON_RUNTIME_MAX_RECENT_OCCURRENCES,
        )

    def _resync_schedule_runtime(self, *, refresh_table: bool = False, save_state: bool = False) -> None:
        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_and_apply_schedule_entries()
        if refresh_table:
            self._refresh_schedule_table()
        if save_state:
            self._save_state()

    def _recalculate_and_apply_schedule_entries(self) -> None:
        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)

    def _sync_after_cron_rule_change(self, *, focus_entry: CronEntry | None = None) -> None:
        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        if focus_entry is not None:
            next_occurrence = next_cron_occurrence(focus_entry, datetime.now().astimezone())
            if next_occurrence is not None:
                self._set_schedule_filter_date(next_occurrence.date())
                self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_and_apply_schedule_entries()
        self._refresh_cron_table()
        self._refresh_schedule_table()
        self._save_state()

    def _refresh_cron_runtime_window(self) -> None:
        self._resync_schedule_runtime()
        normalized_entries, normalized_details = self._normalize_overdue_one_shots(
            datetime.now().astimezone(),
            {SCHEDULE_STATUS_PENDING},
        )
        if normalized_entries:
            self._refresh_schedule_table()
            self._save_state()
            self._append_log(
                f"Marked {normalized_entries} overdue one-shot schedule item(s) as missed"
            )
            self._append_normalized_missed_logs(normalized_entries, normalized_details)

    def _schedule_entry_palette(self, entry: ScheduleEntry, reference_time: datetime) -> tuple[QColor, QColor] | None:
        current_media_id = self._player.current_media.id if self._player.current_media is not None else None
        current_entry = current_schedule_entry_for_playback(
            self._schedule_entries,
            reference_time,
            player_is_playing=self._player.is_playing(),
            current_media_id=current_media_id,
        )
        palette_tokens = schedule_entry_palette_tokens(
            entry,
            reference_time,
            current_entry_id=current_entry.id if current_entry is not None else None,
        )
        if palette_tokens is None:
            return None
        background_hex, foreground_hex = palette_tokens
        return QColor(background_hex), QColor(foreground_hex)

    @staticmethod
    def _apply_item_palette(item: QTableWidgetItem, palette: tuple[QColor, QColor] | None) -> None:
        if palette is None:
            return
        background, foreground = palette
        item.setBackground(QBrush(background))
        item.setForeground(QBrush(foreground))

    @staticmethod
    def _apply_widget_palette(widget: QWidget, palette: tuple[QColor, QColor] | None) -> None:
        if palette is None:
            widget.setStyleSheet("")
            return
        background, foreground = palette
        widget.setStyleSheet(
            "QComboBox {"
            f"background-color: {background.name()};"
            f"color: {foreground.name()};"
            "}"
        )

    def _refresh_schedule_table(self) -> None:
        entries = visible_schedule_entries(
            self._schedule_entries,
            self._schedule_filter_date,
            datetime.now().astimezone(),
        )
        now = datetime.now().astimezone()
        refresh_schedule_table(
            self._schedule_table,
            entries,
            self._media_items,
            now,
            cron_entry_by_id=self._cron_entry_by_id,
            duration_display_details=self._duration_display_details,
            schedule_window_tooltip=self._schedule_window_tooltip,
            schedule_entry_palette=self._schedule_entry_palette,
            apply_item_palette=self._apply_item_palette,
            apply_widget_palette=self._apply_widget_palette,
            on_fade_in_changed=self._on_schedule_fade_in_changed,
            on_fade_out_changed=self._on_schedule_fade_out_changed,
            on_status_changed=self._on_schedule_status_changed,
        )

    def _set_schedule_filter_date(self, target_date: date) -> None:
        self._schedule_filter_date = target_date
        self._schedule_date_selector.blockSignals(True)
        self._schedule_date_selector.setDate(
            QDate(target_date.year, target_date.month, target_date.day)
        )
        self._schedule_date_selector.blockSignals(False)

    def _recalculate_schedule_durations(self) -> None:
        entries = sorted(self._schedule_entries, key=lambda entry: self._normalized_start(entry.start_at))
        for entry in entries:
            entry.duration = self._media_duration_seconds(entry.media_id)

    def _default_next_schedule_start(self) -> datetime:
        if not self._schedule_entries:
            return ScheduleDialog._default_start_datetime()

        entries = sorted(self._schedule_entries, key=lambda entry: self._normalized_start(entry.start_at))
        previous = entries[-1]
        previous_start = self._normalized_start(previous.start_at)
        now = datetime.now().astimezone()
        if previous_start <= now:
            return ScheduleDialog._default_start_datetime()
        if previous.duration is None:
            return previous_start
        return previous_start + timedelta(seconds=max(0, previous.duration))

    def _media_duration_seconds(self, media_id: str) -> int | None:
        if media_id in self._media_duration_cache:
            return self._media_duration_cache[media_id]

        media = self._media_items.get(media_id)
        if media is None:
            self._media_duration_cache[media_id] = None
            return None

        local_path = local_media_path_from_source(media.source)
        if local_path is None or not local_path.is_file():
            self._media_duration_cache[media_id] = None
            return None

        probe_key = duration_probe_cache_key_from_path(local_path)
        if probe_key is not None:
            cached, cached_duration = duration_probe_cache_lookup(self._duration_probe_cache, probe_key)
            if cached:
                self._media_duration_cache[media_id] = cached_duration
                return cached_duration

        self._request_media_duration_probe(media_id, media.source, probe_key=probe_key)
        return None

    def _request_media_duration_probe(
        self,
        media_id: str,
        source: str,
        *,
        probe_key: str | None = None,
    ) -> None:
        if media_id in self._media_duration_pending or self._shutting_down:
            return
        requested_probe_key = probe_key or duration_probe_cache_key_from_source(source)
        self._media_duration_pending.add(media_id)
        future = self._duration_probe_executor.submit(
            probe_media_duration_seconds,
            source,
        )
        future.add_done_callback(
            lambda task, requested_media_id=media_id, requested_source=source, requested_key=requested_probe_key: (
                self._emit_duration_probe_result(
                    requested_media_id,
                    requested_source,
                    requested_key,
                    task,
                )
            )
        )

    def _emit_duration_probe_result(
        self,
        media_id: str,
        source: str,
        probe_key: str | None,
        task: Future[int | None],
    ) -> None:
        if self._shutting_down:
            return
        try:
            duration = task.result()
        except Exception:
            duration = None
        try:
            self._duration_probe_dispatcher.probe_finished.emit(media_id, source, probe_key, duration)
        except RuntimeError:
            return

    @Slot(str, str, object, object)
    def _on_media_duration_probed(
        self,
        media_id: str,
        source: str,
        probe_key: object,
        duration: object,
    ) -> None:
        self._media_duration_pending.discard(media_id)
        if self._shutting_down:
            return

        media = self._media_items.get(media_id)
        if media is None:
            self._media_duration_cache.pop(media_id, None)
            return

        if media.source != source:
            self._media_duration_cache.pop(media_id, None)
            self._media_duration_seconds(media_id)
            return

        requested_probe_key = probe_key if isinstance(probe_key, str) and probe_key else None
        current_probe_key = duration_probe_cache_key_from_source(media.source)
        if (
            requested_probe_key is not None
            and current_probe_key is not None
            and requested_probe_key != current_probe_key
        ):
            self._media_duration_cache.pop(media_id, None)
            self._media_duration_seconds(media_id)
            return

        resolved_duration = normalize_probe_duration(duration)

        effective_probe_key = requested_probe_key or current_probe_key
        if effective_probe_key is not None:
            store_duration_probe_cache(
                self._duration_probe_cache,
                effective_probe_key,
                resolved_duration,
                max_entries=self._DURATION_PROBE_CACHE_MAX_ENTRIES,
            )

        previous_duration = self._media_duration_cache.get(media_id, object())
        self._media_duration_cache[media_id] = resolved_duration
        if previous_duration == resolved_duration:
            return

        updated = False
        for entry in self._schedule_entries:
            if entry.media_id != media_id:
                continue
            if entry.duration == resolved_duration:
                continue
            entry.duration = resolved_duration
            updated = True

        if not updated:
            return

        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()
        self._save_state()

    @staticmethod
    def _normalized_start(start_at: datetime) -> datetime:
        return normalized_start(start_at)

    @staticmethod
    def _format_duration(duration_seconds: int | None) -> str:
        if duration_seconds is None:
            return "-"
        hours, remainder = divmod(duration_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _duration_display_details(
        self,
        entry: ScheduleEntry,
        media: MediaItem | None,
        duration_seconds: int | None,
    ) -> tuple[str, str]:
        effective_duration_ms = self._entry_duration_ms(entry)
        if effective_duration_ms is not None and effective_duration_ms > 0:
            effective_seconds = max(0, effective_duration_ms // 1000)
            formatted = MainWindow._format_duration(effective_seconds)
            media_duration_ms = entry.duration * 1000 if entry.duration is not None and entry.duration > 0 else None
            next_gap_ms = self._next_scheduled_gap_ms(entry)
            if media_duration_ms is not None and next_gap_ms is not None:
                media_formatted = MainWindow._format_duration(media_duration_ms // 1000)
                gap_formatted = MainWindow._format_duration(next_gap_ms // 1000)
                if media_duration_ms < next_gap_ms:
                    return (
                        formatted,
                        "Duration read from media file: "
                        f"{media_formatted} (next scheduled gap: {gap_formatted})",
                    )
                if next_gap_ms < media_duration_ms:
                    return (
                        formatted,
                        "Duration limited by next scheduled item: "
                        f"{gap_formatted} (media duration: {media_formatted})",
                    )
                return formatted, f"Duration from media and schedule boundary: {formatted}"

            if media_duration_ms is not None:
                return formatted, f"Duration read from media file: {formatted}"
            if next_gap_ms is not None:
                next_start = self._next_scheduled_start(entry)
                next_label = (
                    next_start.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                    if next_start is not None
                    else "unknown"
                )
                return (
                    formatted,
                    "Duration computed from next scheduled item: "
                    f"{formatted} (next start at {next_label})",
                )
            return formatted, f"Effective duration: {formatted}"

        if effective_duration_ms is None and self._is_open_ended_stream_entry(entry, media):
            return "-", "Open-ended stream: no media duration and no next scheduled item"

        if media is not None and media.id in self._media_duration_pending:
            return "Loading", "Duration is being analyzed in background"
        if duration_seconds is not None:
            formatted = MainWindow._format_duration(duration_seconds)
            return formatted, f"Duration read from media file: {formatted}"
        if media is None:
            return "Missing", "Duration unavailable: media item is missing"

        if is_stream_source(media.source) and local_media_path_from_source(media.source) is None:
            return "Stream", "Duration unavailable for remote streams/URLs"
        local_path = local_media_path_from_source(media.source)
        if local_path is None or not local_path.exists():
            return "Missing", "Duration unavailable: local file is missing"
        return "Unknown", "Duration unavailable: ffprobe could not read this file"

    def _schedule_window_tooltip(self, entry: ScheduleEntry) -> str:
        start_at, end_at, end_reason = self._schedule_entry_window_details(entry)
        start_label = start_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        if end_at is None:
            end_label = "Open-ended"
        else:
            end_label = end_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        return (
            f"Computed start: {start_label}\n"
            f"Computed end: {end_label}\n"
            f"End reason: {end_reason}"
        )

    def _schedule_entry_window_details(
        self,
        entry: ScheduleEntry,
    ) -> tuple[datetime, datetime | None, str]:
        return schedule_entry_window_details(self._schedule_entries, entry.id)

    def _schedule_log_summary(self, reference_time: datetime) -> str:
        entries = sorted(
            self._schedule_entries,
            key=lambda entry: self._normalized_start(entry.start_at),
        )
        if not entries:
            return "schedule: empty"

        upcoming_entry = next(
            (
                entry
                for entry in entries
                if self._normalized_start(entry.start_at) >= reference_time
            ),
            None,
        )
        target_entry = upcoming_entry if upcoming_entry is not None else entries[-1]
        prefix = "next" if upcoming_entry is not None else "recent"
        start_label = self._normalized_start(target_entry.start_at).strftime("%H:%M:%S")
        duration_label = (
            str(target_entry.duration)
            if target_entry.duration is not None
            else "-"
        )
        return (
            f"schedule {prefix}: "
            f"{self._media_log_name(target_entry.media_id)}@{start_label}/"
            f"{target_entry.status}/dur={duration_label}"
        )

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

    def _focus_schedule_entry(self, entry_id: str, force: bool = False) -> None:
        selected_entry_ids = self._selected_schedule_entry_ids()
        if not force and selected_entry_ids == [entry_id]:
            return

        for row in range(self._schedule_table.rowCount()):
            item = self._schedule_table.item(row, 0)
            if item is None or item.data(Qt.UserRole) != entry_id:
                continue
            self._schedule_table.clearSelection()
            self._schedule_table.setCurrentCell(row, 0)
            self._schedule_table.selectRow(row)
            self._schedule_table.scrollToItem(
                item,
                QAbstractItemView.ScrollHint.PositionAtCenter,
            )
            return

    def _schedule_entry_to_focus(self, reference_time: datetime) -> tuple[ScheduleEntry, date] | None:
        active_entry = self._active_schedule_entry_at(reference_time)
        if active_entry is not None:
            entry, start_at = active_entry
            return entry, start_at.date()

        entries = sorted(
            self._schedule_entries,
            key=lambda entry: self._normalized_start(entry.start_at),
        )
        if not entries:
            return None

        for index, entry in enumerate(entries):
            start_at = self._normalized_start(entry.start_at)
            if start_at >= reference_time:
                target_entry = entries[index - 1] if index > 0 else entry
                return target_entry, self._normalized_start(target_entry.start_at).date()

        last_entry = entries[-1]
        return last_entry, self._normalized_start(last_entry.start_at).date()

    def _apply_schedule_auto_focus(self, force: bool = False) -> None:
        if not self._schedule_auto_focus_enabled:
            return

        target_entry = self._schedule_entry_to_focus(datetime.now().astimezone())
        if target_entry is None:
            return

        entry, active_date = target_entry
        if active_date != self._schedule_filter_date:
            self._set_schedule_filter_date(active_date)
            self._resync_schedule_runtime(refresh_table=True)

        self._focus_schedule_entry(entry.id, force=force)

    @Slot()
    def _refresh_schedule_auto_focus(self) -> None:
        self._apply_schedule_auto_focus()

    def _media_log_name(self, media_id: str) -> str:
        media = self._media_items.get(media_id)
        if media is None:
            return f"missing:{media_id[:8]}"
        return media.title

    def _current_library_tab_descriptor(
        self,
    ) -> tuple[str, QTreeView | None, QFileSystemModel | None]:
        current_widget = self._library_tabs.currentWidget()
        if current_widget is None:
            return "filesystem", self._filesystem_view, self._filesystem_model
        return self._library_tab_sources.get(
            current_widget,
            ("filesystem", self._filesystem_view, self._filesystem_model),
        )

    def _selected_media_id(self) -> str | None:
        panel_kind, filesystem_view, filesystem_model = self._current_library_tab_descriptor()
        if panel_kind == "urls":
            return selected_url_media_id(self._urls_table)

        if filesystem_view is None or filesystem_model is None:
            return None
        media_id, created = selected_filesystem_media_id(
            filesystem_view,
            filesystem_model,
            self._media_items,
            self._media_duration_cache,
            supported_extensions=self._supported_extension_suffixes(),
        )
        if created:
            self._save_state()
        return media_id

    def _selected_schedule_entry_id(self) -> str | None:
        row = self._schedule_table.currentRow()
        if row < 0:
            return None
        item = self._schedule_table.item(row, 0)
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def _selected_schedule_entry_ids(self) -> list[str]:
        rows = sorted({index.row() for index in self._schedule_table.selectedIndexes()})
        entry_ids = []
        for row in rows:
            item = self._schedule_table.item(row, 0)
            if item is not None:
                entry_id = item.data(Qt.UserRole)
                if entry_id is not None:
                    entry_ids.append(entry_id)
        return entry_ids

    def _selected_cron_entry_id(self) -> str | None:
        row = self._cron_table.currentRow()
        if row < 0:
            return None
        item = self._cron_table.item(row, 0)
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def _active_schedule_entry_at(self, now: datetime) -> tuple[ScheduleEntry, datetime] | None:
        return active_schedule_entry_at(self._schedule_entries, now)

    def _schedule_entry_end_at(
        self,
        entries: list[ScheduleEntry],
        index: int,
    ) -> datetime | None:
        return schedule_entry_end_at(entries, index)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._shutting_down = True
        self._scheduler.stop()
        self._greenwich_time_signal_timer.stop()
        self._greenwich_time_signal_player.stop()
        self._volume_fade_timer.stop()
        self._duration_probe_executor.shutdown(wait=False, cancel_futures=True)
        self._save_settings()
        self._save_state()
        super().closeEvent(event)

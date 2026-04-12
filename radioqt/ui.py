from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
import shutil

from PySide6.QtCore import QDate, QDateTime, QObject, QSize, Qt, QTimer, Signal, Slot, QEvent
from PySide6.QtGui import QAction, QBrush, QCloseEvent, QColor, QIcon, QPainter, QPixmap
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFileSystemModel,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
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

from .app_config import AppConfig, load_app_config, save_app_config
from .duration_probe import (
    duration_probe_cache_key_from_path,
    duration_probe_cache_key_from_source,
    duration_probe_cache_lookup,
    normalize_probe_duration,
    probe_media_duration_seconds,
    sanitize_duration_probe_cache,
    store_duration_probe_cache,
)
from .library import (
    VIDEO_EXTENSIONS,
    is_stream_source,
    local_media_path_from_source,
    media_looks_like_video_source,
    media_source_suffix,
    selected_filesystem_media_id,
    selected_url_media_id,
)
from .models import (
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
from .player import MediaPlayerController
from .scheduling import (
    RadioScheduler,
    active_schedule_entry_at,
    current_schedule_entry_for_playback,
    initial_schedule_filter_date,
    normalize_overdue_one_shots,
    normalized_start,
    next_cron_occurrence,
    prepare_schedule_entries_for_startup,
    refresh_cron_schedule_entries,
    runtime_cron_dates,
    schedule_entry_palette_tokens,
    schedule_entry_end_at,
    schedule_entry_window_details,
    visible_schedule_entries,
)
from .storage import load_state, save_state
from .ui_handlers import MainWindowHandlersMixin
from .ui_playback_handlers import MainWindowPlaybackHandlersMixin
from .ui_components import (
    ConfigurationDialog,
    CronHelpDialog,
    FullscreenOverlay,
    ScheduleDialog,
    WaveformWidget,
    refresh_cron_table,
    refresh_schedule_table,
    refresh_urls_table,
)


class _DurationProbeDispatcher(QObject):
    probe_finished = Signal(str, str, object, object)


class MainWindow(MainWindowHandlersMixin, MainWindowPlaybackHandlersMixin, QMainWindow):
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
        self._fullscreen_active = False
        self._schedule_filter_date = datetime.now().astimezone().date()
        self._current_playback_position_ms = 0
        self._shutting_down = False
        self._font_size_points = self._default_font_size_points()

        self._player = MediaPlayerController(self)
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

        panels_layout = QHBoxLayout()
        panels_layout.addWidget(self._build_library_panel(), 7)
        panels_layout.addWidget(self._build_schedule_panel(), 13)

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
        root_layout.addLayout(panels_layout, 7)
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

        # --- Streamings tab ---
        self._streamings_tab_widget = QWidget()
        streamings_layout = QVBoxLayout(self._streamings_tab_widget)
        streamings_layout.setContentsMargins(8, 8, 8, 8)

        self._urls_table = QTableWidget(self._streamings_tab_widget)
        self._urls_table.setColumnCount(2)
        self._urls_table.setHorizontalHeaderLabels(["Title", "URL"])
        self._urls_table.horizontalHeader().setStretchLastSection(True)
        self._urls_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._urls_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._urls_table.setAlternatingRowColors(True)
        self._urls_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._urls_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self._urls_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._urls_table.customContextMenuRequested.connect(self._on_urls_context_menu)
        streamings_layout.addWidget(self._urls_table)

        buttons_row = QHBoxLayout()
        self._add_url_button = QPushButton("Add Streaming")
        buttons_row.addWidget(self._add_url_button)
        streamings_layout.addLayout(buttons_row)

        self._library_tabs.addTab(self._streamings_tab_widget, "Streamings")
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
        self._add_url_button.clicked.connect(self._add_media_url)

        self._add_schedule_button.clicked.connect(self._add_schedule_entry)
        self._add_cron_button.clicked.connect(self._add_cron_schedule)
        self._schedule_date_selector.dateChanged.connect(self._on_schedule_filter_date_changed)
        self._schedule_focus_checkbox.toggled.connect(self._on_schedule_auto_focus_toggled)

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

    def _toggle_fullscreen(self) -> None:
        is_fullscreen = (
            getattr(self._video_widget, "isFullScreen", lambda: False)()
            or self._fullscreen_overlay.isVisible()
            or self.isFullScreen()
        )
        self._on_fullscreen_toggled(not is_fullscreen)

    def _ensure_exit_fullscreen(self) -> None:
        # Centralized exit fullscreen: video widget, overlay, main window
        try:
            # Exit video widget fullscreen if active
            if getattr(self._video_widget, "isFullScreen", lambda: False)():
                try:
                    self._video_widget.setFullScreen(False)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            if self._fullscreen_overlay.isVisible():
                self._fullscreen_overlay.hide()
        except Exception:
            pass

        try:
            if self.isFullScreen():
                self.showNormal()
        except Exception:
            pass

        self._fullscreen_active = False

    def _load_initial_state(self) -> None:
        app_started_at = datetime.now().astimezone()
        self._migrate_legacy_state_location_if_needed()
        state = load_state(self._state_path)
        app_config = self._load_or_initialize_app_config(state)
        self._media_items = {item.id: item for item in state.media_items}
        self._media_duration_cache.clear()
        self._duration_probe_cache = sanitize_duration_probe_cache(
            state.duration_probe_cache,
            max_entries=self._DURATION_PROBE_CACHE_MAX_ENTRIES,
        )
        self._media_duration_pending.clear()
        self._schedule_entries = state.schedule_entries
        self._cron_entries = state.cron_entries
        self._play_queue = deque(state.queue)
        hard_sync_normalized = self._enforce_hard_sync_always()
        self._library_tab_configs = list(app_config.library_tabs)
        self._supported_extensions = self._normalize_supported_extensions(app_config.supported_extensions)
        self._schedule_auto_focus_enabled = state.schedule_auto_focus
        self._logs_visible = state.logs_visible
        shared_fade_seconds = max(1, app_config.fade_duration_seconds)
        self._fade_in_duration_seconds = shared_fade_seconds
        self._fade_out_duration_seconds = shared_fade_seconds
        if app_config.font_size is not None:
            self._font_size_points = max(1, app_config.font_size)
        self._apply_global_font_size(self._font_size_points)
        loaded_schedule_count = len(self._schedule_entries)
        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_schedule_durations()
        startup_preparation = prepare_schedule_entries_for_startup(
            self._schedule_entries,
            app_started_at,
        )
        normalized_details = self._normalized_missed_details(
            app_started_at,
            startup_preparation.normalized_entries,
        )
        self._schedule_filter_date = initial_schedule_filter_date(
            self._schedule_entries,
            self._cron_entries,
            datetime.now().astimezone(),
        )
        self._set_schedule_filter_date(self._schedule_filter_date)
        self._schedule_focus_checkbox.blockSignals(True)
        self._schedule_focus_checkbox.setChecked(self._schedule_auto_focus_enabled)
        self._schedule_focus_checkbox.blockSignals(False)
        self._toggle_logs_action.blockSignals(True)
        self._toggle_logs_action.setChecked(self._logs_visible)
        self._toggle_logs_action.blockSignals(False)
        self._set_logs_visible(self._logs_visible)
        self._apply_supported_extensions_to_filesystem_models()
        self._rebuild_custom_library_tabs()
        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_schedule_durations()
        runtime_pruned_count = max(0, loaded_schedule_count - len(self._schedule_entries))

        self._refresh_urls_list()
        self._refresh_cron_table()
        self._refresh_schedule_table()
        self._apply_schedule_auto_focus(force=True)
        self._scheduler.set_entries(self._schedule_entries)
        self._player.set_volume(self._volume_slider.value())
        self._update_player_visual_state()
        if runtime_pruned_count:
            self._append_log(
                f"Pruned {runtime_pruned_count} CRON occurrence(s) outside runtime window (today/tomorrow)"
            )
        if startup_preparation.normalized_entries:
            self._append_log(
                f"Normalized {len(startup_preparation.normalized_entries)} past one-shot schedule item(s) to missed on startup"
            )
            self._append_normalized_missed_logs(
                len(startup_preparation.normalized_entries),
                normalized_details,
            )
            self._save_state()
        elif startup_preparation.restored_count:
            self._append_log(
                f"Restored {startup_preparation.restored_count} active one-shot schedule item(s) from missed on startup"
            )
            self._save_state()
        elif runtime_pruned_count or hard_sync_normalized:
            self._save_state()
        if hard_sync_normalized:
            self._append_log("Hard sync is now always active for all schedule/CRON entries")
        self._append_log(f"Loaded state from {self._state_path}")

    def _save_state(self) -> None:
        state = AppState(
            media_items=list(self._media_items.values()),
            schedule_entries=self._schedule_entries,
            cron_entries=self._cron_entries,
            queue=list(self._play_queue),
            library_tabs=self._library_tab_configs,
            supported_extensions=self._supported_extensions,
            schedule_auto_focus=self._schedule_auto_focus_enabled,
            logs_visible=self._logs_visible,
            fade_in_duration_seconds=self._fade_in_duration_seconds,
            fade_out_duration_seconds=self._fade_out_duration_seconds,
            duration_probe_cache=dict(self._duration_probe_cache),
        )
        save_state(self._state_path, state)

    def _save_settings(self) -> None:
        app_config = AppConfig(
            fade_duration_seconds=max(self._fade_in_duration_seconds, self._fade_out_duration_seconds),
            font_size=self._font_size_points,
            library_tabs=list(self._library_tab_configs),
            supported_extensions=list(self._supported_extensions),
        )
        save_app_config(self._settings_path, app_config)

    def _load_or_initialize_app_config(self, state: AppState) -> AppConfig:
        if self._settings_path.exists():
            config = load_app_config(self._settings_path)
            if config.font_size is None:
                config.font_size = self._font_size_points
                save_app_config(self._settings_path, config)
            return config

        # Seed initial YAML config from legacy DB settings if available.
        seeded_config = AppConfig(
            fade_duration_seconds=max(1, state.fade_in_duration_seconds, state.fade_out_duration_seconds),
            font_size=self._font_size_points,
            library_tabs=list(state.library_tabs),
            supported_extensions=self._normalize_supported_extensions(state.supported_extensions),
        )
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        save_app_config(self._settings_path, seeded_config)
        return seeded_config

    def _migrate_legacy_state_location_if_needed(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._state_path.exists() and self._legacy_state_path.exists():
            try:
                shutil.copy2(self._legacy_state_path, self._state_path)
            except OSError:
                pass
        target_legacy_json_path = self._state_path.with_suffix(".json")
        if (
            not self._state_path.exists()
            and not target_legacy_json_path.exists()
            and self._legacy_state_json_path.exists()
        ):
            try:
                shutil.copy2(self._legacy_state_json_path, target_legacy_json_path)
            except OSError:
                pass

    @staticmethod
    def _default_font_size_points() -> int:
        app = QApplication.instance()
        if app is None:
            return 10
        point_size = app.font().pointSize()
        if point_size <= 0:
            return 10
        return int(point_size)

    def _apply_global_font_size(self, font_size_points: int) -> None:
        normalized_size = max(1, int(font_size_points))
        app = QApplication.instance()
        if app is None:
            self._font_size_points = normalized_size
            return
        font = app.font()
        if font.pointSize() != normalized_size:
            font.setPointSize(normalized_size)
            app.setFont(font)
        self._font_size_points = normalized_size

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

    def _entry_duration_ms(self, entry: ScheduleEntry | None) -> int | None:
        if entry is None:
            return None

        start_at, end_at, _ = self._schedule_entry_window_details(entry)
        if end_at is not None:
            computed_ms = max(0, int((end_at - start_at).total_seconds() * 1000))
            if computed_ms > 0:
                return computed_ms

        if entry.duration is None or entry.duration <= 0:
            return None
        return entry.duration * 1000

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
        media: MediaItem | None,
        duration_seconds: int | None,
    ) -> tuple[str, str]:
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

    @Slot(str)
    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
        self._log_view.appendPlainText(f"[{timestamp}] {message}")

    @Slot(bool)
    def _set_logs_visible(self, visible: bool) -> None:
        self._logs_group.setVisible(bool(visible))

    @Slot(bool)
    def _on_logs_visibility_toggled(self, checked: bool) -> None:
        self._logs_visible = bool(checked)
        self._set_logs_visible(self._logs_visible)
        self._save_state()

    @Slot()
    def _export_logs(self) -> None:
        default_name = f"radioqt-log-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}.log"
        target_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Logs",
            str(Path.cwd() / default_name),
            "Log Files (*.log);;Text Files (*.txt);;All Files (*)",
        )
        if not target_path:
            return

        try:
            Path(target_path).write_text(self._log_view.toPlainText(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Export Failed", f"Could not export logs:\n{exc}")
            return

        self._append_log(f"Exported logs to {target_path}")

    @Slot()
    def _show_cron_help(self) -> None:
        dialog = CronHelpDialog(self)
        dialog.exec()

    @Slot()
    def _open_configuration_dialog(self) -> None:
        dialog = ConfigurationDialog(
            self,
            fade_in_duration_seconds=self._fade_in_duration_seconds,
            fade_out_duration_seconds=self._fade_out_duration_seconds,
            font_size_points=self._font_size_points,
            library_tabs=self._library_tab_configs,
            supported_extensions=self._supported_extensions,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        next_shared_fade_duration = max(1, dialog.fade_duration_seconds())
        next_font_size_points = max(1, dialog.font_size_points())
        next_library_tabs = dialog.library_tabs()
        next_supported_extensions = self._normalize_supported_extensions(dialog.supported_extensions())
        fade_changed = not (
            next_shared_fade_duration == self._fade_in_duration_seconds
            and next_shared_fade_duration == self._fade_out_duration_seconds
        )
        font_size_changed = next_font_size_points != self._font_size_points
        library_tabs_changed = next_library_tabs != self._library_tab_configs
        supported_extensions_changed = next_supported_extensions != self._supported_extensions

        if not fade_changed and not font_size_changed and not library_tabs_changed and not supported_extensions_changed:
            return

        if fade_changed:
            self._fade_in_duration_seconds = next_shared_fade_duration
            self._fade_out_duration_seconds = next_shared_fade_duration
        if font_size_changed:
            self._apply_global_font_size(next_font_size_points)
        if supported_extensions_changed:
            self._supported_extensions = next_supported_extensions
            self._apply_supported_extensions_to_filesystem_models()
        if library_tabs_changed:
            self._library_tab_configs = next_library_tabs
            self._rebuild_custom_library_tabs()
        self._save_settings()
        self._append_log(
            f"Updated settings: fade in={self._fade_in_duration_seconds}s, "
            f"fade out={self._fade_out_duration_seconds}s, "
            f"font={self._font_size_points}pt, "
            f"custom library tabs={len(self._library_tab_configs)}, "
            f"extensions={','.join(self._supported_extensions)}"
        )

    def _set_automation_status(self, is_playing: bool) -> None:
        if is_playing:
            self._play_button.setIcon(
                self._tinted_standard_icon(QStyle.SP_MediaPlay, QColor("#198754"))
            )
            self._stop_button.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
            return
        self._play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self._stop_button.setIcon(
            self._tinted_standard_icon(QStyle.SP_MediaStop, QColor("#dc3545"))
        )

    def _tinted_standard_icon(
        self,
        standard_pixmap: QStyle.StandardPixmap,
        color: QColor,
    ) -> QIcon:
        base_icon = self.style().standardIcon(standard_pixmap)
        base_pixmap = base_icon.pixmap(20, 20)
        if base_pixmap.isNull():
            return base_icon
        tinted = QPixmap(base_pixmap.size())
        tinted.fill(Qt.GlobalColor.transparent)
        painter = QPainter(tinted)
        painter.drawPixmap(0, 0, base_pixmap)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), color)
        painter.end()
        return QIcon(tinted)

    @Slot(bool)
    def _on_fullscreen_toggled(self, checked: bool) -> None:
        media = self._player.current_media
        ext = ""
        if media is not None:
            try:
                ext = media_source_suffix(media.source)
            except Exception:
                ext = ""

        if checked:
            # If media looks like video, use the video widget fullscreen; otherwise show overlay
            if ext in VIDEO_EXTENSIONS:
                try:
                    self._video_widget.setFullScreen(True)
                except Exception:
                    # fallback to making the main window fullscreen
                    self.showFullScreen()
            else:
                title = self._player_media_label(media) if media is not None else "Now Playing"
                self._fullscreen_overlay.set_text(title)
                self._fullscreen_overlay.showFullScreen()
        else:
            # Exit fullscreen modes
            try:
                if getattr(self._video_widget, "isFullScreen", lambda: False)():
                    self._video_widget.setFullScreen(False)
            except Exception:
                pass
            if self._fullscreen_overlay.isVisible():
                self._fullscreen_overlay.hide()

    def _on_video_fullscreen_changed(self, is_fullscreen: bool) -> None:
        self._fullscreen_active = bool(is_fullscreen)

    def _exit_fullscreen_overlay(self) -> None:
        # Called by overlay when it is dismissed (ESC)
        self._fullscreen_active = False

    def closeEvent(self, event: QCloseEvent) -> None:
        self._shutting_down = True
        self._scheduler.stop()
        self._volume_fade_timer.stop()
        self._duration_probe_executor.shutdown(wait=False, cancel_futures=True)
        self._save_settings()
        self._save_state()
        super().closeEvent(event)

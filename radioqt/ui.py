from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timedelta
import math
from pathlib import Path
import subprocess
import time
from uuid import NAMESPACE_URL, uuid5

from PySide6.QtCore import QDate, QDateTime, QModelIndex, QObject, QSize, Qt, QTimer, Signal, Slot, QEvent
from PySide6.QtGui import QAction, QBrush, QCloseEvent, QColor, QIcon, QPainter, QPixmap
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    QInputDialog,
)

from .cron import CronExpression, CronParseError
from .library import (
    VIDEO_EXTENSIONS,
    add_stream_media_item,
    is_stream_source,
    local_media_path_from_source,
    media_looks_like_video_source,
    media_source_suffix,
    remove_media_from_library,
    selected_filesystem_media_id,
    selected_url_media_id,
    update_stream_media_item,
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
from .playback import (
    dequeue_next_playable_media,
    enqueue_manual_media,
    process_schedule_trigger,
    resolve_media_by_id,
    resolve_play_request,
)
from .player import MediaPlayerController
from .scheduling import (
    RadioScheduler,
    active_schedule_entry_at,
    normalize_overdue_one_shots,
    normalized_start,
    prepare_schedule_entries_for_play,
    prepare_schedule_entries_for_startup,
    schedule_entry_end_at,
    schedule_entry_window_details,
)
from .storage import load_state, save_state
from .ui_components import (
    ConfigurationDialog,
    CronDialog,
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


class MainWindow(QMainWindow):
    _CRON_RUNTIME_MAX_OCCURRENCES = 100
    _CRON_RUNTIME_MAX_RECENT_OCCURRENCES = 20
    _CRON_RUNTIME_LOOKBACK = timedelta(hours=1)
    _DURATION_PROBE_CACHE_MAX_ENTRIES = 2000

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlag(Qt.Window, True)
        self.setWindowFlag(Qt.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.setWindowFlag(Qt.WindowCloseButtonHint, True)
        self.setWindowTitle("RadioQt - Scheduled Multimedia Player")
        self.resize(1280, 820)
        self.setMinimumSize(960, 760)

        self._state_path = Path.cwd() / "state" / "radio_state.db"
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
    def _make_tab_label(text: str, marker_color: str) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        text_label = QLabel(text, container)
        marker = QLabel(container)
        marker.setFixedSize(QSize(10, 10))
        marker.setStyleSheet(
            f"background-color: {marker_color}; border: 1px solid rgba(0, 0, 0, 0.25);"
        )

        layout.addWidget(text_label)
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

        layout.addWidget(self._library_tabs)
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
        self._schedule_table.setColumnCount(7)
        self._schedule_table.setHorizontalHeaderLabels(
            ["Start Time", "Duration", "Media", "Hard Sync", "Fade In", "Fade Out", "Status"]
        )
        self._schedule_table.horizontalHeader().setStretchLastSection(False)
        self._schedule_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._schedule_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._schedule_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._schedule_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._schedule_table.customContextMenuRequested.connect(self._on_schedule_context_menu)

        self._add_schedule_button = QPushButton("Schedule Selected Media")
        self._add_schedule_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        datetime_layout.addLayout(filter_row)
        datetime_layout.addWidget(self._schedule_table)
        datetime_layout.addWidget(self._add_schedule_button)
        self._schedule_tabs.addTab(datetime_tab, "Date Time")

        # --- CRON tab (placeholder for CRON-based scheduling) ---
        cron_tab = QWidget()
        cron_layout = QVBoxLayout(cron_tab)

        self._cron_table = QTableWidget(cron_tab)
        self._cron_table.setColumnCount(6)
        self._cron_table.setHorizontalHeaderLabels(
            ["CRON", "Media", "Hard Sync", "Fade In", "Fade Out", "Status"]
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
        cron_tab_index = self._schedule_tabs.addTab(cron_tab, "")
        self._schedule_tabs.tabBar().setTabButton(
            cron_tab_index,
            self._schedule_tabs.tabBar().ButtonPosition.RightSide,
            self._make_tab_label("CRON", "#ffd166"),
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
        state = load_state(self._state_path)
        self._media_items = {item.id: item for item in state.media_items}
        self._media_duration_cache.clear()
        self._duration_probe_cache = self._sanitize_duration_probe_cache(state.duration_probe_cache)
        self._media_duration_pending.clear()
        self._schedule_entries = state.schedule_entries
        self._cron_entries = state.cron_entries
        self._play_queue = deque(state.queue)
        self._library_tab_configs = list(state.library_tabs)
        self._supported_extensions = self._normalize_supported_extensions(state.supported_extensions)
        self._schedule_auto_focus_enabled = state.schedule_auto_focus
        self._logs_visible = state.logs_visible
        self._fade_in_duration_seconds = max(1, state.fade_in_duration_seconds)
        self._fade_out_duration_seconds = max(1, state.fade_out_duration_seconds)
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
        self._schedule_filter_date = self._initial_schedule_filter_date()
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
        elif runtime_pruned_count:
            self._save_state()
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
            duration_probe_cache=self._duration_probe_cache,
        )
        save_state(self._state_path, state)

    def _fade_in_duration_ms(self) -> int:
        return max(1, self._fade_in_duration_seconds) * 1000

    def _fade_out_duration_ms(self) -> int:
        return max(1, self._fade_out_duration_seconds) * 1000

    @classmethod
    def _sanitize_duration_probe_cache(
        cls,
        raw_cache: dict[str, int | None] | None,
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

        while len(normalized) > cls._DURATION_PROBE_CACHE_MAX_ENTRIES:
            oldest_key = next(iter(normalized))
            normalized.pop(oldest_key, None)
        return normalized

    def _duration_probe_cache_lookup(self, probe_key: str) -> tuple[bool, int | None]:
        if probe_key not in self._duration_probe_cache:
            return False, None
        duration = self._duration_probe_cache.pop(probe_key)
        self._duration_probe_cache[probe_key] = duration
        return True, duration

    def _store_duration_probe_cache(self, probe_key: str, duration: int | None) -> None:
        if probe_key in self._duration_probe_cache:
            current = self._duration_probe_cache.pop(probe_key)
            if current == duration:
                self._duration_probe_cache[probe_key] = current
                return
        self._duration_probe_cache[probe_key] = duration
        while len(self._duration_probe_cache) > self._DURATION_PROBE_CACHE_MAX_ENTRIES:
            oldest_key = next(iter(self._duration_probe_cache))
            self._duration_probe_cache.pop(oldest_key, None)

    @classmethod
    def _duration_probe_cache_key_from_path(cls, path: Path) -> str | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        try:
            resolved = str(path.resolve())
        except OSError:
            resolved = str(path)
        return f"{resolved}|{stat.st_mtime_ns}|{stat.st_size}"

    @classmethod
    def _duration_probe_cache_key_from_source(cls, source: str) -> str | None:
        path = local_media_path_from_source(source)
        if path is None or not path.is_file():
            return None
        return cls._duration_probe_cache_key_from_path(path)

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
            on_hard_sync_changed=self._on_cron_hard_sync_changed,
            on_fade_in_changed=self._on_cron_fade_in_changed,
            on_fade_out_changed=self._on_cron_fade_out_changed,
            on_status_changed=self._on_cron_status_changed,
        )

    @staticmethod
    def _cron_occurrence_entry_id(cron_id: str, start_at: datetime) -> str:
        return str(uuid5(NAMESPACE_URL, f"radioqt-cron:{cron_id}:{start_at.isoformat()}"))

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

    @staticmethod
    def _entry_duration_ms(entry: ScheduleEntry | None) -> int | None:
        if entry is None or entry.duration is None or entry.duration <= 0:
            return None
        return entry.duration * 1000

    def _is_schedule_entry_protected_from_removal(self, entry: ScheduleEntry) -> bool:
        cron_entry = self._cron_entry_by_id(entry.cron_id)
        return cron_entry is not None and cron_entry.enabled

    @staticmethod
    def _runtime_cron_dates() -> set[date]:
        today = datetime.now().astimezone().date()
        return {today, today + timedelta(days=1)}

    def _next_cron_occurrence(self, cron_entry: CronEntry, start: datetime) -> datetime | None:
        try:
            expression = CronExpression.parse(cron_entry.expression)
        except CronParseError:
            return None
        return expression.next_at_or_after(start)

    def _apply_cron_entry_defaults(self, entry: ScheduleEntry, cron_entry: CronEntry) -> None:
        entry.media_id = cron_entry.media_id
        entry.one_shot = True
        entry.cron_id = cron_entry.id
        if entry.cron_hard_sync_override is None:
            entry.hard_sync = cron_entry.hard_sync
        else:
            entry.hard_sync = entry.cron_hard_sync_override
        if entry.cron_fade_in_override is None:
            entry.fade_in = cron_entry.fade_in
        else:
            entry.fade_in = entry.cron_fade_in_override
        if entry.cron_fade_out_override is None:
            entry.fade_out = cron_entry.fade_out
        else:
            entry.fade_out = entry.cron_fade_out_override

        if entry.status in {SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED}:
            return
        if not cron_entry.enabled:
            entry.status = SCHEDULE_STATUS_DISABLED
            return
        entry.status = entry.cron_status_override or SCHEDULE_STATUS_PENDING

    def _refresh_cron_schedule_entries(self, target_dates: set[date] | None = None) -> None:
        runtime_dates = set(target_dates) if target_dates else None
        now = datetime.now().astimezone()
        refreshed_entries: list[ScheduleEntry] = []
        for entry in self._schedule_entries:
            if entry.cron_id is None:
                refreshed_entries.append(entry)
                continue

            if runtime_dates is not None:
                entry_date = self._normalized_start(entry.start_at).date()
                if entry_date not in runtime_dates:
                    # Keep runtime memory bounded to the configured CRON window.
                    continue

            cron_entry = self._cron_entry_by_id(entry.cron_id)
            if cron_entry is None:
                if entry.status in {SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED}:
                    refreshed_entries.append(entry)
                continue
            self._apply_cron_entry_defaults(entry, cron_entry)
            refreshed_entries.append(entry)

        self._schedule_entries = refreshed_entries
        existing_by_id = {
            entry.id: entry
            for entry in self._schedule_entries
            if entry.cron_id is not None
        }
        if not runtime_dates:
            return

        lookback_start = now - self._CRON_RUNTIME_LOOKBACK
        timezone = datetime.now().astimezone().tzinfo
        occurrence_candidates: list[tuple[datetime, CronEntry]] = []
        for cron_entry in self._cron_entries:
            if not cron_entry.enabled:
                continue
            try:
                expression = CronExpression.parse(cron_entry.expression)
            except CronParseError:
                continue
            for target_date in sorted(runtime_dates):
                for start_at in expression.iter_datetimes_on_date(target_date, timezone):
                    if start_at < lookback_start:
                        continue
                    occurrence_candidates.append((start_at, cron_entry))

        occurrence_candidates.sort(key=lambda item: item[0])
        past_occurrences = [item for item in occurrence_candidates if item[0] <= now]
        future_occurrences = [item for item in occurrence_candidates if item[0] > now]

        selected_recent = past_occurrences[-self._CRON_RUNTIME_MAX_RECENT_OCCURRENCES:]
        remaining_capacity = max(0, self._CRON_RUNTIME_MAX_OCCURRENCES - len(selected_recent))
        selected_occurrences = selected_recent + future_occurrences[:remaining_capacity]

        if len(selected_occurrences) < self._CRON_RUNTIME_MAX_OCCURRENCES:
            extra_needed = self._CRON_RUNTIME_MAX_OCCURRENCES - len(selected_occurrences)
            older_past = past_occurrences[: max(0, len(past_occurrences) - len(selected_recent))]
            selected_occurrences = older_past[-extra_needed:] + selected_occurrences

        selected_occurrences.sort(key=lambda item: item[0])
        selected_entry_ids: set[str] = set()
        for start_at, cron_entry in selected_occurrences:
            entry_id = self._cron_occurrence_entry_id(cron_entry.id, start_at)
            selected_entry_ids.add(entry_id)
            entry = existing_by_id.get(entry_id)
            if entry is None:
                entry = ScheduleEntry(
                    id=entry_id,
                    media_id=cron_entry.media_id,
                    start_at=start_at,
                    hard_sync=cron_entry.hard_sync,
                    fade_in=cron_entry.fade_in,
                    fade_out=cron_entry.fade_out,
                    status=SCHEDULE_STATUS_PENDING,
                    one_shot=True,
                    cron_id=cron_entry.id,
                )
                self._apply_cron_entry_defaults(entry, cron_entry)
                self._schedule_entries.append(entry)
                existing_by_id[entry_id] = entry
                continue

            entry.start_at = start_at
            self._apply_cron_entry_defaults(entry, cron_entry)

        if selected_entry_ids:
            self._schedule_entries = [
                entry
                for entry in self._schedule_entries
                if entry.cron_id is None or entry.id in selected_entry_ids
            ]
        else:
            self._schedule_entries = [entry for entry in self._schedule_entries if entry.cron_id is None]

    def _refresh_cron_runtime_window(self) -> None:
        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_schedule_durations()
        normalized_entries, normalized_details = self._normalize_overdue_one_shots(
            datetime.now().astimezone(),
            {SCHEDULE_STATUS_PENDING},
        )
        self._scheduler.set_entries(self._schedule_entries)
        if normalized_entries:
            self._refresh_schedule_table()
            self._save_state()
            self._append_log(
                f"Marked {normalized_entries} overdue one-shot schedule item(s) as missed"
            )
            self._append_normalized_missed_logs(normalized_entries, normalized_details)

    def _current_schedule_entry_for_playback(self, reference_time: datetime) -> ScheduleEntry | None:
        if not self._player.is_playing() or self._player.current_media is None:
            return None
        active_entry = self._active_schedule_entry_at(reference_time)
        if active_entry is None:
            return None
        entry, _ = active_entry
        if entry.media_id != self._player.current_media.id:
            return None
        return entry

    def _schedule_entry_palette(self, entry: ScheduleEntry, reference_time: datetime) -> tuple[QColor, QColor] | None:
        current_entry = self._current_schedule_entry_for_playback(reference_time)
        if current_entry is not None and current_entry.id == entry.id:
            return QColor("#2d6a4f"), QColor("#ffffff")
        if entry.status == SCHEDULE_STATUS_DISABLED:
            return QColor("#f8d7da"), QColor("#842029")
        if entry.status == SCHEDULE_STATUS_FIRED and self._normalized_start(entry.start_at) < reference_time:
            return QColor("#d8f3dc"), QColor("#1b4332")
        if entry.status == SCHEDULE_STATUS_MISSED:
            return QColor("#fff3cd"), QColor("#664d03")
        if entry.cron_id is not None:
            return QColor("#ffd166"), QColor("#5f4b00")
        return None

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
        entries = self._visible_schedule_entries()
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
            on_hard_sync_changed=self._on_schedule_hard_sync_changed,
            on_fade_in_changed=self._on_schedule_fade_in_changed,
            on_fade_out_changed=self._on_schedule_fade_out_changed,
            on_status_changed=self._on_schedule_status_changed,
        )

    def _visible_schedule_entries(self) -> list[ScheduleEntry]:
        return [
            entry
            for entry in sorted(
                self._schedule_entries,
                key=lambda current_entry: self._normalized_start(current_entry.start_at),
            )
            if self._normalized_start(entry.start_at).date() == self._schedule_filter_date
        ]

    def _initial_schedule_filter_date(self) -> date:
        today = datetime.now().astimezone().date()
        upcoming_dates = [
            self._normalized_start(entry.start_at).date()
            for entry in sorted(
                self._schedule_entries,
                key=lambda entry: self._normalized_start(entry.start_at),
            )
        ]
        for entry_date in upcoming_dates:
            if entry_date >= today:
                return entry_date
        if upcoming_dates:
            return upcoming_dates[0]

        now = datetime.now().astimezone()
        cron_dates = []
        for cron_entry in self._cron_entries:
            next_occurrence = self._next_cron_occurrence(cron_entry, now)
            if next_occurrence is not None:
                cron_dates.append(next_occurrence.date())
        if cron_dates:
            return min(cron_dates)
        return today

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

        probe_key = self._duration_probe_cache_key_from_path(local_path)
        if probe_key is not None:
            cached, cached_duration = self._duration_probe_cache_lookup(probe_key)
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
        requested_probe_key = probe_key or self._duration_probe_cache_key_from_source(source)
        self._media_duration_pending.add(media_id)
        future = self._duration_probe_executor.submit(
            self._probe_media_duration_seconds,
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
        current_probe_key = self._duration_probe_cache_key_from_source(media.source)
        if (
            requested_probe_key is not None
            and current_probe_key is not None
            and requested_probe_key != current_probe_key
        ):
            self._media_duration_cache.pop(media_id, None)
            self._media_duration_seconds(media_id)
            return

        resolved_duration: int | None
        if isinstance(duration, int):
            resolved_duration = max(0, duration)
        else:
            resolved_duration = None

        effective_probe_key = requested_probe_key or current_probe_key
        if effective_probe_key is not None:
            self._store_duration_probe_cache(effective_probe_key, resolved_duration)

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
    def _probe_media_duration_seconds(source: str) -> int | None:
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
            self._refresh_cron_schedule_entries(self._runtime_cron_dates())
            self._recalculate_schedule_durations()
            self._scheduler.set_entries(self._schedule_entries)
            self._refresh_schedule_table()

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

    @Slot(QDate)
    def _on_schedule_filter_date_changed(self, selected_date: QDate) -> None:
        self._schedule_filter_date = selected_date.toPython()
        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()

    @Slot(bool)
    def _on_schedule_auto_focus_toggled(self, checked: bool) -> None:
        self._schedule_auto_focus_enabled = checked
        self._save_state()
        if checked:
            self._apply_schedule_auto_focus(force=True)

    @Slot(QModelIndex)
    def _on_filesystem_selected(self, _: QModelIndex) -> None:
        self._last_source_panel = "filesystem"

    @Slot()
    def _on_urls_selection_changed(self) -> None:
        if self._urls_table.currentRow() >= 0:
            self._last_source_panel = "urls"

    @Slot(int)
    def _on_library_tab_changed(self, index: int) -> None:
        tab_widget = self._library_tabs.widget(index) if index >= 0 else None
        if tab_widget is None:
            self._last_source_panel = "filesystem"
            return
        panel_kind, _, _ = self._library_tab_sources.get(tab_widget, ("filesystem", None, None))
        self._last_source_panel = "urls" if panel_kind == "urls" else "filesystem"

    @Slot()
    def _add_media_url(self) -> None:
        url, ok = QInputDialog.getText(self, "Add Stream URL", "URL (http/https/rtsp/etc):")
        if not ok or not url.strip():
            return

        title, ok_title = QInputDialog.getText(self, "Display Name", "Title:", text=url.strip())
        if not ok_title or not title.strip():
            title = url.strip()

        media = add_stream_media_item(
            self._media_items,
            self._media_duration_cache,
            title,
            url,
        )
        self._refresh_urls_list()
        self._save_state()
        self._append_log(f"Added stream: {title.strip()}")

    def _remove_media_by_id(self, media_id: str) -> None:
        result = remove_media_from_library(
            self._media_items,
            self._media_duration_cache,
            self._cron_entries,
            self._schedule_entries,
            self._play_queue,
            media_id,
        )
        if result.removed_media is None:
            return

        self._cron_entries = result.cron_entries
        self._schedule_entries = result.schedule_entries
        self._play_queue = result.play_queue
        self._media_duration_pending.discard(media_id)
        if self._player.current_media is not None and self._player.current_media.id == media_id:
            self._player.clear_current_media()
            self._now_playing_label.setText("None")
            self._update_player_visual_state()

        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_urls_list()
        self._refresh_cron_table()
        self._refresh_schedule_table()
        self._save_state()
        self._append_log(f"Removed media: {result.removed_media.title}")

    @Slot()
    def _remove_selected_url(self) -> None:
        media_id = selected_url_media_id(self._urls_table)
        if media_id is None:
            QMessageBox.information(self, "No Selection", "Select a URL first.")
            return
        media = self._media_items.get(media_id)
        title = media.title if media else "this stream"
        result = QMessageBox.question(
            self,
            "Confirm Removal",
            f"Are you sure you want to remove '{title}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if result != QMessageBox.Yes:
            return
        self._remove_media_by_id(media_id)

    @Slot()
    def _edit_selected_url(self) -> None:
        media_id = selected_url_media_id(self._urls_table)
        if media_id is None:
            QMessageBox.information(self, "No Selection", "Select a URL first.")
            return

        media = self._media_items.get(media_id)
        if media is None:
            return

        updated_url, ok = QInputDialog.getText(
            self,
            "Edit Stream URL",
            "URL (http/https/rtsp/etc):",
            text=media.source,
        )
        if not ok or not updated_url.strip():
            return

        updated_title, ok_title = QInputDialog.getText(
            self,
            "Edit Display Name",
            "Title:",
            text=media.title,
        )
        if not ok_title or not updated_title.strip():
            updated_title = updated_url.strip()

        media = update_stream_media_item(
            self._media_items,
            self._media_duration_cache,
            media_id,
            updated_title,
            updated_url,
        )
        if media is None:
            return
        self._refresh_urls_list()
        self._refresh_schedule_table()
        self._save_state()
        self._append_log(f"Updated stream: {media.title}")

    @Slot()
    def _remove_selected_media(self) -> None:
        media_id = self._selected_media_id()
        if media_id is None:
            QMessageBox.information(self, "No Selection", "Select a media item first.")
            return
        self._remove_media_by_id(media_id)

    @Slot()
    def _play_selected_media(self) -> None:
        media_id = self._selected_media_id()
        if media_id is None:
            QMessageBox.information(self, "No Selection", "Select a media item first.")
            return
        media = resolve_media_by_id(self._media_items, media_id)
        if media is None:
            return
        self._player.play_media(media)

    @Slot()
    def _queue_selected_media(self) -> None:
        media_id = self._selected_media_id()
        if media_id is None:
            QMessageBox.information(self, "No Selection", "Select a media item first.")
            return
        media = resolve_media_by_id(self._media_items, media_id)
        if media is None:
            return
        enqueue_manual_media(self._play_queue, media_id)
        self._save_state()
        self._append_log(
            f"Queued media '{media.title}' ({len(self._play_queue)} item(s) pending)"
        )

    @Slot()
    def _add_schedule_entry(self) -> None:
        media_id = self._selected_media_id()
        if media_id is None:
            QMessageBox.information(self, "No Selection", "Select a media item from library first.")
            return

        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_schedule_durations()
        dialog = ScheduleDialog(self, initial_start_at=self._default_next_schedule_start())
        if dialog.exec() != QDialog.Accepted:
            return

        entry = ScheduleEntry.create(
            media_id=media_id,
            start_at=dialog.selected_datetime(),
            hard_sync=dialog.hard_sync(),
        )
        if entry.one_shot and self._normalized_start(entry.start_at) <= datetime.now().astimezone():
            entry.status = SCHEDULE_STATUS_MISSED
        self._schedule_entries.append(entry)

        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)
        self._set_schedule_filter_date(self._normalized_start(entry.start_at).date())
        self._refresh_schedule_table()
        self._save_state()
        media_name = self._media_log_name(entry.media_id)
        if entry.status == SCHEDULE_STATUS_MISSED:
            self._append_log(
                f"Scheduled media '{media_name}' in the past; entry was marked as missed"
            )
        self._append_log(
            f"Scheduled media '{media_name}' for {entry.start_at.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}"
        )

    @Slot()
    def _remove_schedule_entry(self) -> None:
        entry_ids = self._selected_schedule_entry_ids()
        if not entry_ids:
            QMessageBox.information(self, "No Selection", "Select a schedule row first.")
            return

        entry_ids_set = set(entry_ids)
        entries_to_remove = [e for e in self._schedule_entries if e.id in entry_ids_set]
        if not entries_to_remove:
            return
        cron_generated_entries = [
            entry for entry in entries_to_remove if self._is_schedule_entry_protected_from_removal(entry)
        ]
        if cron_generated_entries:
            QMessageBox.information(
                self,
                "CRON-managed Entries",
                "Active CRON-generated rows cannot be removed from Date Time. Disable the CRON rule first if you want to remove them here.",
            )
            return

        if len(entries_to_remove) == 1:
            entry = entries_to_remove[0]
            media_name = self._media_log_name(entry.media_id)
            start_label = entry.start_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            message = f"Are you sure you want to remove the schedule entry for '{media_name}' at {start_label}?"
        else:
            lines = []
            for entry in sorted(entries_to_remove, key=lambda e: e.start_at):
                media_name = self._media_log_name(entry.media_id)
                start_label = entry.start_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                lines.append(f"  - '{media_name}' at {start_label}")
            message = f"Are you sure you want to remove {len(entries_to_remove)} schedule entries?\n" + "\n".join(lines)

        result = QMessageBox.question(
            self,
            "Confirm Removal",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if result != QMessageBox.Yes:
            return

        self._schedule_entries = [e for e in self._schedule_entries if e.id not in entry_ids_set]
        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()
        self._save_state()
        if len(entries_to_remove) == 1:
            self._append_log(f"Removed schedule entry for media '{self._media_log_name(entries_to_remove[0].media_id)}'")
        else:
            self._append_log(f"Removed {len(entries_to_remove)} schedule entries")

    @Slot("QPoint")
    def _on_schedule_context_menu(self, position) -> None:
        item = self._schedule_table.itemAt(position)
        if item is None:
            return
        selected_count = len(self._selected_schedule_entry_ids())
        selected_ids = set(self._selected_schedule_entry_ids())
        has_cron_generated = any(
            entry.id in selected_ids and self._is_schedule_entry_protected_from_removal(entry)
            for entry in self._schedule_entries
        )
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self._schedule_table)
        label = (
            "CRON-managed Entries Cannot Be Removed"
            if has_cron_generated
            else f"Remove {selected_count} Entries" if selected_count > 1 else "Remove Entry"
        )
        remove_action = QAction(label, menu)
        remove_action.setEnabled(not has_cron_generated)
        remove_action.triggered.connect(self._remove_schedule_entry)
        menu.addAction(remove_action)
        menu.exec(self._schedule_table.viewport().mapToGlobal(position))

    @Slot("QPoint")
    def _on_cron_context_menu(self, position) -> None:
        item = self._cron_table.itemAt(position)
        if item is None:
            return
        self._cron_table.selectRow(item.row())
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self._cron_table)
        edit_action = QAction("Edit CRON Entry", menu)
        edit_action.triggered.connect(self._edit_selected_cron)
        menu.addAction(edit_action)
        remove_action = QAction("Remove CRON Entry", menu)
        remove_action.triggered.connect(self._remove_selected_cron)
        menu.addAction(remove_action)
        menu.exec(self._cron_table.viewport().mapToGlobal(position))

    @Slot()
    def _add_cron_schedule(self) -> None:
        media_id = self._selected_media_id()
        if media_id is None:
            QMessageBox.information(self, "No Selection", "Select a media item from library first.")
            return

        dialog = CronDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return

        entry = CronEntry.create(
            media_id=media_id,
            expression=dialog.expression(),
            hard_sync=dialog.hard_sync(),
            fade_in=dialog.fade_in(),
            fade_out=dialog.fade_out(),
        )
        self._cron_entries.append(entry)
        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        next_occurrence = self._next_cron_occurrence(entry, datetime.now().astimezone())
        if next_occurrence is not None:
            self._set_schedule_filter_date(next_occurrence.date())
            self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_cron_table()
        self._refresh_schedule_table()
        self._save_state()
        self._append_log(
            f"Added CRON schedule '{entry.expression}' for media '{self._media_log_name(entry.media_id)}'"
        )

    @Slot()
    def _edit_selected_cron(self) -> None:
        cron_id = self._selected_cron_entry_id()
        if cron_id is None:
            QMessageBox.information(self, "No Selection", "Select a CRON row first.")
            return

        cron_entry = self._cron_entry_by_id(cron_id)
        if cron_entry is None:
            return

        previous_expression = cron_entry.expression

        dialog = CronDialog(
            self,
            dialog_title="Edit CRON Entry",
            initial_expression=cron_entry.expression,
            initial_hard_sync=cron_entry.hard_sync,
            initial_fade_in=cron_entry.fade_in,
            initial_fade_out=cron_entry.fade_out,
            expression_only=True,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        updated_expression = dialog.expression()
        if updated_expression == previous_expression:
            return

        cron_entry.expression = updated_expression

        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        next_occurrence = self._next_cron_occurrence(cron_entry, datetime.now().astimezone())
        if next_occurrence is not None:
            self._set_schedule_filter_date(next_occurrence.date())
            self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_cron_table()
        self._refresh_schedule_table()
        self._save_state()
        self._append_log(
            (
                f"Updated CRON schedule '{previous_expression}' -> '{cron_entry.expression}' "
                f"for media '{self._media_log_name(cron_entry.media_id)}'"
            )
        )

    def _remove_selected_cron(self) -> None:
        cron_id = self._selected_cron_entry_id()
        if cron_id is None:
            QMessageBox.information(self, "No Selection", "Select a CRON row first.")
            return

        cron_entry = self._cron_entry_by_id(cron_id)
        if cron_entry is None:
            return

        result = QMessageBox.question(
            self,
            "Confirm Removal",
            (
                f"Are you sure you want to remove the CRON rule '{cron_entry.expression}' "
                f"for '{self._media_log_name(cron_entry.media_id)}'?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if result != QMessageBox.Yes:
            return

        self._cron_entries = [entry for entry in self._cron_entries if entry.id != cron_id]
        self._schedule_entries = [
            entry
            for entry in self._schedule_entries
            if entry.cron_id != cron_id or entry.status in {SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED}
        ]
        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_cron_table()
        self._refresh_schedule_table()
        self._save_state()
        self._append_log(f"Removed CRON schedule '{cron_entry.expression}'")

    @Slot("QPoint")
    def _on_urls_context_menu(self, position) -> None:
        item = self._urls_table.itemAt(position)
        if item is None:
            return
        self._urls_table.selectRow(item.row())
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self._urls_table)
        edit_action = QAction("Edit Entry", menu)
        edit_action.triggered.connect(self._edit_selected_url)
        menu.addAction(edit_action)
        remove_action = QAction("Remove URL", menu)
        remove_action.triggered.connect(self._remove_selected_url)
        menu.addAction(remove_action)
        menu.exec(self._urls_table.viewport().mapToGlobal(position))

    def _on_schedule_hard_sync_changed(self, entry_id: str, value: str) -> None:
        updated_entry: ScheduleEntry | None = None
        new_hard_sync = value == "True"
        for entry in self._schedule_entries:
            if entry.id == entry_id:
                if entry.status in {SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED}:
                    return
                cron_entry = self._cron_entry_by_id(entry.cron_id)
                if cron_entry is not None:
                    override_value = None if cron_entry.hard_sync == new_hard_sync else new_hard_sync
                    if entry.cron_hard_sync_override == override_value and entry.hard_sync == new_hard_sync:
                        return
                    entry.cron_hard_sync_override = override_value
                    entry.hard_sync = new_hard_sync
                    updated_entry = entry
                    break
                if entry.hard_sync == new_hard_sync:
                    return
                entry.hard_sync = new_hard_sync
                updated_entry = entry
                break

        if updated_entry is None:
            return

        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()
        self._save_state()
        state = "enabled" if updated_entry.hard_sync else "disabled"
        self._append_log(
            f"Set hard sync for media '{self._media_log_name(updated_entry.media_id)}' to {state}"
        )

    def _on_schedule_fade_in_changed(self, entry_id: str, value: str) -> None:
        updated_entry: ScheduleEntry | None = None
        fade_in_enabled = value == "True"
        for entry in self._schedule_entries:
            if entry.id != entry_id:
                continue
            if entry.status in {SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED}:
                return
            cron_entry = self._cron_entry_by_id(entry.cron_id)
            if cron_entry is not None:
                override_value = None if cron_entry.fade_in == fade_in_enabled else fade_in_enabled
                if entry.cron_fade_in_override == override_value and entry.fade_in == fade_in_enabled:
                    return
                entry.cron_fade_in_override = override_value
                entry.fade_in = fade_in_enabled
                updated_entry = entry
                break
            if entry.fade_in == fade_in_enabled:
                return
            entry.fade_in = fade_in_enabled
            updated_entry = entry
            break

        if updated_entry is None:
            return

        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()
        self._save_state()
        state = "enabled" if updated_entry.fade_in else "disabled"
        self._append_log(
            f"Set fade in for media '{self._media_log_name(updated_entry.media_id)}' to {state}"
        )

    def _on_schedule_fade_out_changed(self, entry_id: str, value: str) -> None:
        updated_entry: ScheduleEntry | None = None
        fade_out_enabled = value == "True"
        for entry in self._schedule_entries:
            if entry.id != entry_id:
                continue
            if entry.status in {SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED}:
                return
            cron_entry = self._cron_entry_by_id(entry.cron_id)
            if cron_entry is not None:
                override_value = None if cron_entry.fade_out == fade_out_enabled else fade_out_enabled
                if entry.cron_fade_out_override == override_value and entry.fade_out == fade_out_enabled:
                    return
                entry.cron_fade_out_override = override_value
                entry.fade_out = fade_out_enabled
                updated_entry = entry
                break
            if entry.fade_out == fade_out_enabled:
                return
            entry.fade_out = fade_out_enabled
            updated_entry = entry
            break

        if updated_entry is None:
            return

        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()
        self._save_state()
        state = "enabled" if updated_entry.fade_out else "disabled"
        self._append_log(
            f"Set fade out for media '{self._media_log_name(updated_entry.media_id)}' to {state}"
        )

    def _on_schedule_status_changed(self, entry_id: str, value: str) -> None:
        updated_entry: ScheduleEntry | None = None
        applied_value = value
        for entry in self._schedule_entries:
            if entry.id == entry_id:
                if entry.status in {SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED}:
                    return
                cron_entry = self._cron_entry_by_id(entry.cron_id)
                if cron_entry is not None and not cron_entry.enabled:
                    self._refresh_schedule_table()
                    return
                new_status = SCHEDULE_STATUS_PENDING if value == "Pending" else SCHEDULE_STATUS_DISABLED
                if (
                    new_status == SCHEDULE_STATUS_PENDING
                    and entry.one_shot
                    and self._normalized_start(entry.start_at) < datetime.now().astimezone()
                ):
                    new_status = SCHEDULE_STATUS_MISSED
                    applied_value = "Missed"
                if entry.status == new_status:
                    return
                if cron_entry is not None:
                    entry.cron_status_override = (
                        SCHEDULE_STATUS_DISABLED if new_status == SCHEDULE_STATUS_DISABLED else None
                    )
                entry.status = new_status
                updated_entry = entry
                break

        if updated_entry is None:
            return

        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()
        self._save_state()
        self._append_log(
            f"Set status for media '{self._media_log_name(updated_entry.media_id)}' to {applied_value}"
        )

    def _on_cron_hard_sync_changed(self, cron_id: str, value: str) -> None:
        updated_entry: CronEntry | None = None
        new_hard_sync = value == "True"
        for entry in self._cron_entries:
            if entry.id != cron_id:
                continue
            if entry.hard_sync == new_hard_sync:
                return
            entry.hard_sync = new_hard_sync
            updated_entry = entry
            break

        if updated_entry is None:
            return

        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()
        self._save_state()
        self._append_log(
            f"Set CRON hard sync for media '{self._media_log_name(updated_entry.media_id)}' to {value}"
        )

    def _on_cron_fade_in_changed(self, cron_id: str, value: str) -> None:
        updated_entry: CronEntry | None = None
        fade_in_enabled = value == "True"
        for entry in self._cron_entries:
            if entry.id != cron_id:
                continue
            if entry.fade_in == fade_in_enabled:
                return
            entry.fade_in = fade_in_enabled
            updated_entry = entry
            break

        if updated_entry is None:
            return

        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()
        self._save_state()
        self._append_log(
            f"Set CRON fade in for media '{self._media_log_name(updated_entry.media_id)}' to {value}"
        )

    def _on_cron_fade_out_changed(self, cron_id: str, value: str) -> None:
        updated_entry: CronEntry | None = None
        fade_out_enabled = value == "True"
        for entry in self._cron_entries:
            if entry.id != cron_id:
                continue
            if entry.fade_out == fade_out_enabled:
                return
            entry.fade_out = fade_out_enabled
            updated_entry = entry
            break

        if updated_entry is None:
            return

        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()
        self._save_state()
        self._append_log(
            f"Set CRON fade out for media '{self._media_log_name(updated_entry.media_id)}' to {value}"
        )

    def _on_cron_status_changed(self, cron_id: str, value: str) -> None:
        updated_entry: CronEntry | None = None
        enabled = value == "Enabled"
        for entry in self._cron_entries:
            if entry.id != cron_id:
                continue
            if entry.enabled == enabled:
                return
            entry.enabled = enabled
            updated_entry = entry
            break

        if updated_entry is None:
            return

        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()
        self._save_state()
        self._append_log(
            f"Set CRON status for media '{self._media_log_name(updated_entry.media_id)}' to {value}"
        )

    @Slot(object)
    def _on_schedule_triggered(self, entry: ScheduleEntry) -> None:
        current_media_name = (
            self._player.current_media.title
            if self._player.current_media is not None
            else "nothing"
        )
        outcome = process_schedule_trigger(
            entry,
            self._media_items,
            self._play_queue,
            automation_playing=self._automation_playing,
            player_is_playing=self._player.is_playing(),
            current_media_name=current_media_name,
        )
        if outcome.kind == "ignored_stopped":
            self._append_log(f"Ignoring schedule {entry.id}: automation is stopped")
            self._refresh_schedule_table()
            self._save_state()
            return
        if outcome.kind == "missing_media":
            self._append_log(
                f"Skipping schedule {entry.id}: media '{self._media_log_name(entry.media_id)}' not found"
            )
            self._refresh_schedule_table()
            self._save_state()
            return
        if outcome.kind == "play_now" and outcome.media is not None:
            if outcome.interrupted_media_name is not None:
                self._append_log(
                    f"Hard sync active for '{outcome.media.title}': interrupting '{outcome.interrupted_media_name}'"
                )
            scheduled_start_at = self._normalized_start(entry.start_at)
            offset_ms = max(0, int((datetime.now().astimezone() - scheduled_start_at).total_seconds() * 1000))
            self._player.play_media(
                outcome.media,
                start_position_ms=offset_ms,
                fade_in=entry.fade_in,
                fade_out=entry.fade_out,
                expected_duration_ms=self._entry_duration_ms(entry),
                fade_in_duration_ms=self._fade_in_duration_ms(),
                fade_out_duration_ms=self._fade_out_duration_ms(),
            )
        elif outcome.kind == "queued" and outcome.media is not None:
            self._append_log(f"Player busy; queued scheduled media '{outcome.media.title}'")

        self._refresh_schedule_table()
        self._save_state()

    @Slot(object)
    def _on_media_started(self, media: MediaItem) -> None:
        self._current_playback_position_ms = self._player.current_position_ms()
        self._update_now_playing_label()
        self._update_player_visual_state()
        self._append_log(f"Now playing '{media.title}'")

    @Slot()
    def _on_media_finished(self) -> None:
        current_media_name = (
            self._player.current_media.title
            if self._player.current_media is not None
            else "unknown"
        )
        self._append_log(f"Media finished '{current_media_name}'")
        self._update_player_visual_state()
        self._play_next_from_queue()

    def _play_next_from_queue(self) -> None:
        result = dequeue_next_playable_media(self._play_queue, self._media_items)
        if result is not None:
            self._save_state()
            if result.skipped_missing_count:
                self._append_log(
                    f"Skipped {result.skipped_missing_count} missing queued media item(s)"
                )
            if result.queue_item.source == "schedule":
                self._append_log(
                    f"Playing queued scheduled media '{result.media.title}'"
                )
            else:
                self._append_log(
                    f"Playing queued manual media '{result.media.title}'"
                )
            queued_schedule_entry = (
                self._schedule_entry_by_id(result.queue_item.schedule_entry_id)
                if result.queue_item.source == "schedule"
                else None
            )
            self._player.play_media(
                result.media,
                fade_in=queued_schedule_entry.fade_in if queued_schedule_entry is not None else False,
                fade_out=queued_schedule_entry.fade_out if queued_schedule_entry is not None else False,
                expected_duration_ms=self._entry_duration_ms(queued_schedule_entry),
                fade_in_duration_ms=self._fade_in_duration_ms(),
                fade_out_duration_ms=self._fade_out_duration_ms(),
            )
            return
        self._player.clear_current_media()
        self._current_playback_position_ms = 0
        self._save_state()
        self._update_now_playing_label()
        self._update_player_visual_state()

    def _active_schedule_entry_at(self, now: datetime) -> tuple[ScheduleEntry, datetime] | None:
        return active_schedule_entry_at(self._schedule_entries, now)

    def _schedule_entry_end_at(
        self,
        entries: list[ScheduleEntry],
        index: int,
    ) -> datetime | None:
        return schedule_entry_end_at(entries, index)

    @Slot(object)
    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self._update_player_visual_state()
        if state == QMediaPlayer.StoppedState and not self._play_queue:
            self._current_playback_position_ms = 0
            self._update_now_playing_label()

    @Slot(int)
    def _on_playback_position_changed(self, position_ms: int) -> None:
        self._current_playback_position_ms = max(0, position_ms)
        self._update_now_playing_label()

    @Slot(int)
    def _on_volume_slider_value_changed(self, value: int) -> None:
        self._volume_label.setText(f"{value}%")
        if value > 0:
            # Keep the "intended" non-zero volume stable while fading.
            # Otherwise transitional values (1%, 2%, ...) can overwrite it.
            if (
                not self._volume_fade_timer.isActive()
                or value == self._volume_fade_target_volume
            ):
                self._last_nonzero_volume = value
            if self._mute_button.isChecked():
                self._mute_button.blockSignals(True)
                self._mute_button.setChecked(False)
                self._mute_button.blockSignals(False)

    def _start_volume_fade(
        self,
        *,
        start_volume: int,
        target_volume: int,
        duration_ms: int,
    ) -> None:
        normalized_start = max(0, min(100, start_volume))
        normalized_target = max(0, min(100, target_volume))
        self._volume_fade_timer.stop()
        self._volume_fade_start_volume = normalized_start
        self._volume_fade_target_volume = normalized_target
        self._volume_fade_duration_ms = max(1, duration_ms)
        self._volume_slider.setValue(normalized_start)
        if normalized_start == normalized_target:
            return
        self._volume_fade_started_at = time.monotonic()
        self._volume_fade_timer.start()

    @Slot()
    def _on_volume_fade_tick(self) -> None:
        elapsed_ms = max(0, int((time.monotonic() - self._volume_fade_started_at) * 1000))
        progress = min(1.0, elapsed_ms / self._volume_fade_duration_ms)
        next_value = int(
            round(
                self._volume_fade_start_volume
                + (self._volume_fade_target_volume - self._volume_fade_start_volume) * progress
            )
        )
        if self._volume_slider.value() != next_value:
            self._volume_slider.setValue(next_value)
        if progress < 1.0:
            return
        self._volume_fade_timer.stop()
        if self._volume_fade_target_volume <= 0:
            self._mute_button.blockSignals(True)
            self._mute_button.setChecked(True)
            self._mute_button.blockSignals(False)
        elif self._mute_button.isChecked():
            self._mute_button.blockSignals(True)
            self._mute_button.setChecked(False)
            self._mute_button.blockSignals(False)

    @Slot(bool)
    def _on_mute_toggled(self, checked: bool) -> None:
        self._volume_fade_timer.stop()
        self._mute_button.setIcon(
            self.style().standardIcon(
                QStyle.SP_MediaVolumeMuted if checked else QStyle.SP_MediaVolume
            )
        )
        if checked:
            current = self._volume_slider.value()
            if current > 0:
                self._last_nonzero_volume = current
            self._volume_slider.setValue(0)
            return
        restore_volume = self._last_nonzero_volume if self._last_nonzero_volume > 0 else 100
        self._volume_slider.setValue(restore_volume)

    @Slot()
    def _on_volume_fade_in_clicked(self) -> None:
        current_volume = self._volume_slider.value()
        target_volume = current_volume
        if current_volume <= 0:
            target_volume = self._last_nonzero_volume if self._last_nonzero_volume > 0 else 100
        elif current_volume <= 1 and self._last_nonzero_volume > current_volume:
            # Recover from edge cases where slider got stuck near zero.
            target_volume = self._last_nonzero_volume
        if self._mute_button.isChecked():
            self._mute_button.blockSignals(True)
            self._mute_button.setChecked(False)
            self._mute_button.blockSignals(False)
        self._start_volume_fade(
            start_volume=0,
            target_volume=target_volume,
            duration_ms=self._fade_in_duration_ms(),
        )

    @Slot()
    def _on_volume_fade_out_clicked(self) -> None:
        current_volume = self._volume_slider.value()
        if current_volume <= 0:
            self._mute_button.blockSignals(True)
            self._mute_button.setChecked(True)
            self._mute_button.blockSignals(False)
            return
        self._start_volume_fade(
            start_volume=current_volume,
            target_volume=0,
            duration_ms=self._fade_out_duration_ms(),
        )

    @Slot()
    def _on_play_clicked(self) -> None:
        now = datetime.now().astimezone()
        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_schedule_durations()
        play_preparation = prepare_schedule_entries_for_play(
            self._schedule_entries,
            now,
            automation_playing=self._automation_playing,
        )
        self._scheduler.set_entries(self._schedule_entries)
        if play_preparation.started_automation:
            self._automation_playing = True
            self._set_automation_status(True)
            self._scheduler.start()
            self._append_log("Automation status changed to Playing")
        if play_preparation.normalized_entries:
            normalized_details = self._normalized_missed_details(
                now,
                play_preparation.normalized_entries,
            )
            self._refresh_schedule_table()
            self._save_state()
            self._append_log(
                f"Marked {len(play_preparation.normalized_entries)} missed one-shot schedule item(s) as missed"
            )
            self._append_normalized_missed_logs(
                len(play_preparation.normalized_entries),
                normalized_details,
            )
        if play_preparation.restored_count:
            self._append_log(
                f"Restored {play_preparation.restored_count} active one-shot schedule item(s) from missed"
            )

        play_request = resolve_play_request(
            self._schedule_entries,
            self._media_items,
            now,
            player_is_playing=self._player.is_playing(),
            player_has_active_media=self._player.has_active_media(),
            queue_has_items=bool(self._play_queue),
        )
        if play_request.kind == "already_playing":
            return

        if play_request.kind == "active_schedule" and play_request.active_schedule is not None:
            active_play = play_request.active_schedule
            if active_play.kind == "unsupported_status":
                self._refresh_schedule_table()
                self._save_state()
                return
            if active_play.kind == "missing_media":
                self._append_log(
                    f"Play ignored: scheduled media '{self._media_log_name(active_play.entry.media_id)}' not found"
                )
                self._refresh_schedule_table()
                self._save_state()
                return
            if active_play.kind != "play_active" or active_play.media is None or active_play.start_at is None:
                self._refresh_schedule_table()
                self._save_state()
                return
            end_label = (
                active_play.end_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                if active_play.end_at is not None
                else "Open-ended"
            )
            self._append_log(
                f"Active schedule entry '{active_play.media.title}': "
                f"start={active_play.start_at.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}, "
                f"end={end_label}, end_reason={active_play.end_reason}, "
                f"offset_ms={active_play.offset_ms}"
            )
            self._player.play_media(
                active_play.media,
                start_position_ms=active_play.offset_ms,
                fade_in=active_play.entry.fade_in,
                fade_out=active_play.entry.fade_out,
                expected_duration_ms=self._entry_duration_ms(active_play.entry),
                fade_in_duration_ms=self._fade_in_duration_ms(),
                fade_out_duration_ms=self._fade_out_duration_ms(),
            )
            self._append_log(
                f"Started scheduled media '{active_play.media.title}' from {self._format_duration(active_play.offset_ms // 1000)}"
            )
            self._refresh_schedule_table()
            self._save_state()
            return
        if play_request.kind == "resume_loaded_media":
            self._player.play()
            return
        if play_request.kind == "play_queue":
            self._play_next_from_queue()
            return
        self._append_log(
            f"Play ignored: no active or queued media at {now.strftime('%H:%M:%S')} "
            f"— {self._schedule_log_summary(now)}"
        )

    @Slot()
    def _on_stop_clicked(self) -> None:
        current_media_name = (
            self._player.current_media.title
            if self._player.current_media is not None
            else "nothing"
        )
        if self._automation_playing:
            self._automation_playing = False
            self._set_automation_status(False)
            self._scheduler.stop()
            self._append_log("Automation status changed to Stopped")
        self._player.clear_current_media()
        self._current_playback_position_ms = 0
        self._update_now_playing_label()
        self._update_player_visual_state()
        self._append_log(f"Playback stopped and media cleared ('{current_media_name}')")

    @Slot(str)
    def _on_player_error(self, message: str) -> None:
        current_media_name = (
            self._player.current_media.title
            if self._player.current_media is not None
            else "unknown"
        )
        self._append_log(f"Player error on '{current_media_name}': {message}")

    @Slot(object)
    def _on_audio_levels_changed(self, levels: object) -> None:
        if self._media_looks_like_video(self._player.current_media):
            return
        self._waveform_widget.set_levels(levels if isinstance(levels, list) else None)

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
            library_tabs=self._library_tab_configs,
            supported_extensions=self._supported_extensions,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        next_shared_fade_duration = max(1, dialog.fade_duration_seconds())
        next_library_tabs = dialog.library_tabs()
        next_supported_extensions = self._normalize_supported_extensions(dialog.supported_extensions())
        fade_changed = not (
            next_shared_fade_duration == self._fade_in_duration_seconds
            and next_shared_fade_duration == self._fade_out_duration_seconds
        )
        library_tabs_changed = next_library_tabs != self._library_tab_configs
        supported_extensions_changed = next_supported_extensions != self._supported_extensions

        if not fade_changed and not library_tabs_changed and not supported_extensions_changed:
            return

        if fade_changed:
            self._fade_in_duration_seconds = next_shared_fade_duration
            self._fade_out_duration_seconds = next_shared_fade_duration
        if supported_extensions_changed:
            self._supported_extensions = next_supported_extensions
            self._apply_supported_extensions_to_filesystem_models()
        if library_tabs_changed:
            self._library_tab_configs = next_library_tabs
            self._rebuild_custom_library_tabs()
        self._save_state()
        self._append_log(
            f"Updated settings: fade in={self._fade_in_duration_seconds}s, "
            f"fade out={self._fade_out_duration_seconds}s, "
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
        self._save_state()
        super().closeEvent(event)

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDate, QSize, Qt
from PySide6.QtGui import QAction
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDateEdit,
    QFileSystemModel,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QSlider,
    QStyle,
    QStackedLayout,
    QTabWidget,
    QTableWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..models import DEFAULT_SUPPORTED_EXTENSIONS, LibraryTab
from ..ui_components import FullscreenOverlay, WaveformWidget


class MainWindowLayoutBuildersMixin:
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

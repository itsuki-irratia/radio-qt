from __future__ import annotations

from collections import deque
from datetime import date, datetime, timedelta
import math
from pathlib import Path
import re
import subprocess
from uuid import NAMESPACE_URL, uuid5

from PySide6.QtCore import QDate, QDateTime, QModelIndex, QSize, Qt, QTimer, QUrl, Slot, QEvent
from PySide6.QtGui import QAction, QBrush, QCloseEvent, QColor, QPainter
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
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
    QStackedLayout,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
    QInputDialog,
    QLineEdit,
)

from .cron import CronExpression, CronParseError
from .models import (
    AppState,
    CronEntry,
    MediaItem,
    SCHEDULE_STATUS_DISABLED,
    SCHEDULE_STATUS_FIRED,
    SCHEDULE_STATUS_MISSED,
    SCHEDULE_STATUS_PENDING,
    ScheduleEntry,
)
from .player import MediaPlayerController
from .scheduler import RadioScheduler
from .storage import load_state, save_state

SUPPORTED_MEDIA_EXTENSIONS = {
    ".aac",
    ".avi",
    ".flac",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}

VIDEO_EXTENSIONS = {".avi", ".mkv", ".mov", ".mp4", ".webm", ".flv"}


class FullscreenOverlay(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.Window, True)
        self._label = QLabel("", self)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet("color: white; background-color: #222; font-size: 36px; padding: 40px;")
        layout = QVBoxLayout(self)
        layout.addWidget(self._label)

    def set_text(self, text: str) -> None:
        self._label.setText(text)

    def keyPressEvent(self, event) -> None:  # allow Esc to close overlay
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import Qt as _Qt

        try:
            if isinstance(event, QKeyEvent) and event.key() == _Qt.Key_Escape:
                self.hide()
                # notify parent to sync fullscreen state
                if isinstance(self.parent(), MainWindow):
                    self.parent()._exit_fullscreen_overlay()
                return
        except Exception:
            pass
        super().keyPressEvent(event)


class WaveformWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = "No media"
        self._active = False
        self._levels = [0.0] * 36
        self.setMinimumHeight(180)

        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._decay_levels)
        self._timer.start()

    def set_media_state(self, title: str, active: bool) -> None:
        self._title = title
        self._active = active
        self.update()

    def set_levels(self, levels: list[float] | None) -> None:
        if not levels:
            self._levels = [0.0] * len(self._levels)
            self.update()
            return
        if len(levels) != len(self._levels):
            self._levels = [0.0] * len(levels)
        smoothed_levels = []
        for current, incoming in zip(self._levels, levels):
            smoothed_levels.append(max(incoming, current * 0.65))
        self._levels = smoothed_levels
        self.update()

    def clear(self) -> None:
        self._title = "No media"
        self._active = False
        self._levels = [0.0] * len(self._levels)
        self.update()

    def _decay_levels(self) -> None:
        if not any(level > 0.002 for level in self._levels):
            return
        decay_factor = 0.92 if self._active else 0.82
        self._levels = [level * decay_factor if level > 0.002 else 0.0 for level in self._levels]
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#111827"))

        title_color = QColor("#f9fafb") if self._active else QColor("#d1d5db")
        subtitle_color = QColor("#9ca3af")
        accent_color = QColor("#38bdf8") if self._active else QColor("#475569")
        baseline_color = QColor("#1f2937")

        painter.setPen(title_color)
        painter.drawText(24, 34, self._title)
        painter.setPen(subtitle_color)
        painter.drawText(24, 56, "Audio waveform")

        center_y = int(self.height() * 0.62)
        painter.setPen(baseline_color)
        painter.drawLine(24, center_y, self.width() - 24, center_y)

        bars = len(self._levels)
        gap = 4
        available_width = max(40, self.width() - 48)
        bar_width = max(4, int((available_width - gap * (bars - 1)) / bars))
        max_height = max(36, int(self.height() * 0.22))
        left = 24

        painter.setPen(Qt.NoPen)
        painter.setBrush(accent_color)
        for index, normalized in enumerate(self._levels):
            x = left + index * (bar_width + gap)
            bar_height = max(10, int(10 + normalized * max_height))
            top = center_y - bar_height
            painter.drawRoundedRect(x, top, bar_width, bar_height * 2, 2, 2)

        super().paintEvent(event)


class ScheduleDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        initial_start_at: datetime | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Schedule Entry")
        self._datetime_edit = QDateTimeEdit(self)
        self._datetime_edit.setCalendarPopup(True)
        self._datetime_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        if initial_start_at is None:
            initial_start_at = self._default_start_datetime()
        self._datetime_edit.setDateTime(QDateTime(initial_start_at))
        self._hard_sync_checkbox = QCheckBox("Hard sync (interrupt current playback)", self)
        self._hard_sync_checkbox.setChecked(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Start at:"))
        layout.addWidget(self._datetime_edit)
        layout.addWidget(self._hard_sync_checkbox)
        layout.addWidget(buttons)

    @staticmethod
    def _default_start_datetime() -> datetime:
        now = datetime.now().astimezone()
        minutes_to_add = 2 if now.second > 30 else 1
        return now.replace(second=0, microsecond=0) + timedelta(minutes=minutes_to_add)

    def selected_datetime(self) -> datetime:
        dt = self._datetime_edit.dateTime().toPython()
        if dt.tzinfo is None:
            return dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return dt

    def hard_sync(self) -> bool:
        return self._hard_sync_checkbox.isChecked()


class CronDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add CRON Entry")
        self._expression_edit = QLineEdit(self)
        self._expression_edit.setPlaceholderText("sec min hour day month weekday")
        self._hard_sync_checkbox = QCheckBox("Hard sync (interrupt current playback)", self)
        self._hard_sync_checkbox.setChecked(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("CRON expression (with seconds):"))
        layout.addWidget(self._expression_edit)
        layout.addWidget(QLabel("Example: 0 */15 * * * *"))
        layout.addWidget(QLabel("Use numeric values only. Month: 1-12. Weekday starts on Monday: 1-7."))
        layout.addWidget(self._hard_sync_checkbox)
        layout.addWidget(buttons)

    def expression(self) -> str:
        return self._expression_edit.text().strip()

    def hard_sync(self) -> bool:
        return self._hard_sync_checkbox.isChecked()

    def _validate_and_accept(self) -> None:
        try:
            CronExpression.parse(self.expression())
        except CronParseError as exc:
            QMessageBox.warning(self, "Invalid CRON", str(exc))
            return
        self.accept()


class CronHelpDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("CRON Help")
        self.resize(760, 420)

        help_text = (
            "CRON format in RadioQt uses 6 fields:\n"
            "second minute hour day-of-month month day-of-week\n\n"
            "Supported syntax:\n"
            "*  any value\n"
            ",  list of values\n"
            "-  range of values\n"
            "/  step values\n"
            "Use numeric values only\n"
            "Month: 1-12\n"
            "Day-of-week starts on Monday:\n"
            "  1 = Monday\n"
            "  2 = Tuesday\n"
            "  3 = Wednesday\n"
            "  4 = Thursday\n"
            "  5 = Friday\n"
            "  6 = Saturday\n"
            "  7 = Sunday\n\n"
            "Examples:\n"
            "0 * * * * *\n"
            "  Every minute, at second 0\n\n"
            "0 */15 * * * *\n"
            "  Every 15 minutes\n\n"
            "0 30 8 * * *\n"
            "  Every day at 08:30:00\n\n"
            "0 0 9 * * 1-5\n"
            "  Monday to Friday at 09:00:00\n\n"
            "30 0 12 1 * *\n"
            "  On day 1 of every month at 12:00:30\n\n"
            "0 0 18 * 1,6,12 *\n"
            "  Every day at 18:00:00, only in months 1, 6 and 12\n\n"
            "0 0 6 * * 7\n"
            "  Every Sunday at 06:00:00\n"
        )

        text = QPlainTextEdit(self)
        text.setReadOnly(True)
        text.setPlainText(help_text)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
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
        self._schedule_entries: list[ScheduleEntry] = []
        self._cron_entries: list[CronEntry] = []
        self._play_queue: deque[str] = deque()
        self._last_source_panel = "filesystem"
        self._automation_playing = False
        self._fullscreen_active = False
        self._schedule_filter_date = datetime.now().astimezone().date()
        self._current_playback_position_ms = 0

        self._player = MediaPlayerController(self)
        self._scheduler = RadioScheduler(parent=self)
        self._cron_refresh_timer = QTimer(self)
        self._cron_refresh_timer.setInterval(30000)

        self._build_ui()
        self._build_menu_bar()
        self._wire_signals()
        self._load_initial_state()
        self._cron_refresh_timer.start()

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
        source = media.source.strip()
        if not source:
            return False
        url = QUrl(source)
        if url.isValid() and url.scheme():
            if url.scheme().lower() == "file":
                suffix = Path(url.toLocalFile()).suffix.lower()
            else:
                suffix = Path(url.path()).suffix.lower()
        else:
            suffix = Path(source).expanduser().suffix.lower()
        return suffix in VIDEO_EXTENSIONS

    def _update_player_visual_state(self) -> None:
        media = self._player.current_media
        if self._media_looks_like_video(media):
            self._player_display_layout.setCurrentWidget(self._video_widget)
            return
        title = media.title if media is not None else "No media"
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
        self._automation_status_label = QLabel(root)
        self._set_automation_status(self._automation_playing)

        now_playing_layout = QHBoxLayout()
        now_playing_layout.addWidget(self._automation_status_label)
        now_playing_layout.addWidget(self._now_playing_label)
        now_playing_layout.addStretch()

        controls_layout = QHBoxLayout()
        self._play_button = QPushButton("Play")
        self._stop_button = QPushButton("Stop")
        self._volume_slider = QSlider(Qt.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(100)
        controls_layout.addWidget(self._play_button)
        controls_layout.addWidget(self._stop_button)
        controls_layout.addWidget(QLabel("Volume"))
        controls_layout.addWidget(self._volume_slider)
        self._volume_label = QLabel("100%")
        controls_layout.addWidget(self._volume_label)
        self._volume_slider.valueChanged.connect(
            lambda v: self._volume_label.setText(f"{v}%")
        )

        panels_layout = QHBoxLayout()
        panels_layout.addWidget(self._build_library_panel(), 1)
        panels_layout.addWidget(self._build_schedule_panel(), 1)

        self._log_view = QPlainTextEdit(root)
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(2000)
        self._log_view.setPlaceholderText("Runtime events...")
        self._log_view.setMinimumHeight(80)

        root_layout.addWidget(self._player_display, 2)
        root_layout.addLayout(now_playing_layout)
        root_layout.addLayout(controls_layout)
        root_layout.addLayout(panels_layout, 7)
        root_layout.addWidget(self._log_view)

        self.setCentralWidget(root)
        # Fullscreen overlay for audio-only playback
        self._fullscreen_overlay = FullscreenOverlay(self)

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()
        help_menu = menu_bar.addMenu("&Help")
        self._cron_help_action = QAction("&CRON", self)
        help_menu.addAction(self._cron_help_action)

    def _build_library_panel(self) -> QWidget:
        group = QGroupBox("Media Library")
        layout = QVBoxLayout(group)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 8, 8, 8)

        self._library_tabs = QTabWidget(group)

        # --- Filesystem tab ---
        filesystem_tab = QWidget()
        filesystem_layout = QVBoxLayout(filesystem_tab)
        filesystem_layout.setContentsMargins(8, 8, 8, 8)

        root_path = "/"
        self._filesystem_model = QFileSystemModel(group)
        self._filesystem_model.setRootPath(root_path)

        self._filesystem_view = QTreeView(filesystem_tab)
        self._filesystem_view.setModel(self._filesystem_model)
        self._filesystem_view.setRootIndex(self._filesystem_model.index(root_path))
        self._filesystem_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self._filesystem_view.setAlternatingRowColors(True)
        self._filesystem_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        for column in (1, 2, 3):
            self._filesystem_view.hideColumn(column)
        filesystem_layout.addWidget(self._filesystem_view)

        self._library_tabs.addTab(filesystem_tab, "Filesystem")

        # --- Streamings tab ---
        streamings_tab = QWidget()
        streamings_layout = QVBoxLayout(streamings_tab)
        streamings_layout.setContentsMargins(8, 8, 8, 8)

        self._urls_table = QTableWidget(streamings_tab)
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

        self._library_tabs.addTab(streamings_tab, "Streamings")

        layout.addWidget(self._library_tabs)
        return group

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
        filter_row.addStretch()

        self._schedule_table = QTableWidget(datetime_tab)
        self._schedule_table.setColumnCount(5)
        self._schedule_table.setHorizontalHeaderLabels(
            ["Start Time", "Duration", "Media", "Hard Sync", "Status"]
        )
        self._schedule_table.horizontalHeader().setStretchLastSection(True)
        self._schedule_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._schedule_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._schedule_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._schedule_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._schedule_table.customContextMenuRequested.connect(self._on_schedule_context_menu)

        buttons_row = QHBoxLayout()
        self._add_schedule_button = QPushButton("Schedule Selected Media")
        buttons_row.addWidget(self._add_schedule_button)

        datetime_layout.addLayout(filter_row)
        datetime_layout.addWidget(self._schedule_table)
        datetime_layout.addLayout(buttons_row)
        self._schedule_tabs.addTab(datetime_tab, "Date Time")

        # --- CRON tab (placeholder for CRON-based scheduling) ---
        cron_tab = QWidget()
        cron_layout = QVBoxLayout(cron_tab)

        self._cron_table = QTableWidget(cron_tab)
        self._cron_table.setColumnCount(4)
        self._cron_table.setHorizontalHeaderLabels(["CRON", "Media", "Hard Sync", "Status"])
        self._cron_table.horizontalHeader().setStretchLastSection(True)
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

        self._play_button.clicked.connect(self._on_play_clicked)
        self._stop_button.clicked.connect(self._on_stop_clicked)
        self._volume_slider.valueChanged.connect(self._player.set_volume)

        self._player.media_started.connect(self._on_media_started)
        self._player.media_finished.connect(self._on_media_finished)
        self._player.playback_state_changed.connect(self._on_playback_state_changed)
        self._player.playback_position_changed.connect(self._on_playback_position_changed)
        self._player.playback_error.connect(self._on_player_error)
        self._player.audio_levels_changed.connect(self._on_audio_levels_changed)

        self._scheduler.schedule_triggered.connect(self._on_schedule_triggered)
        self._scheduler.log.connect(self._append_log)
        self._cron_refresh_timer.timeout.connect(self._refresh_cron_runtime_window)
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
        self._schedule_entries = state.schedule_entries
        self._cron_entries = state.cron_entries
        self._play_queue = deque(state.queue)
        self._refresh_cron_schedule_entries(self._runtime_cron_dates() | {self._schedule_filter_date})
        self._recalculate_schedule_durations()
        expired_entries = self._expire_missed_one_shots(app_started_at)
        self._schedule_filter_date = self._initial_schedule_filter_date()
        self._set_schedule_filter_date(self._schedule_filter_date)
        self._refresh_cron_schedule_entries({self._schedule_filter_date})
        self._recalculate_schedule_durations()

        self._refresh_urls_list()
        self._refresh_cron_table()
        self._refresh_schedule_table()
        self._scheduler.set_entries(self._schedule_entries)
        self._player.set_volume(self._volume_slider.value())
        self._update_player_visual_state()
        if expired_entries:
            self._append_log(
                f"Marked {expired_entries} missed one-shot schedule item(s) as missed on startup"
            )
            self._save_state()
        self._append_log(f"Loaded state from {self._state_path}")

    def _save_state(self) -> None:
        state = AppState(
            media_items=list(self._media_items.values()),
            schedule_entries=self._schedule_entries,
            cron_entries=self._cron_entries,
            queue=list(self._play_queue),
        )
        save_state(self._state_path, state)

    def _expire_missed_one_shots(self, reference_time: datetime) -> int:
        active_entry = self._active_schedule_entry_at(reference_time)
        active_entry_id = active_entry[0].id if active_entry is not None else None
        expired = 0
        for entry in self._schedule_entries:
            if entry.status != SCHEDULE_STATUS_PENDING or not entry.one_shot:
                continue
            if entry.id == active_entry_id:
                continue
            start_at = entry.start_at
            if start_at.tzinfo is None:
                start_at = start_at.replace(tzinfo=reference_time.tzinfo)
            if start_at < reference_time:
                entry.status = SCHEDULE_STATUS_MISSED
                expired += 1
        return expired

    def _refresh_urls_list(self) -> None:
        self._urls_table.setRowCount(0)
        items = sorted(self._media_items.values(), key=lambda item: item.created_at)
        for media in items:
            if not self._is_stream_source(media.source):
                continue
            row = self._urls_table.rowCount()
            self._urls_table.insertRow(row)
            title_item = QTableWidgetItem(media.title)
            title_item.setData(Qt.UserRole, media.id)
            self._urls_table.setItem(row, 0, title_item)
            self._urls_table.setItem(row, 1, QTableWidgetItem(media.source))
        self._urls_table.resizeColumnsToContents()

    def _refresh_cron_table(self) -> None:
        entries = sorted(self._cron_entries, key=lambda entry: entry.created_at)
        self._cron_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            media = self._media_items.get(entry.media_id)
            media_name = media.title if media else f"Missing ({entry.media_id[:8]})"
            media_source = media.source if media else f"Missing media ID: {entry.media_id}"

            expression_item = QTableWidgetItem(entry.expression)
            expression_item.setData(Qt.UserRole, entry.id)
            expression_item.setToolTip(media_source)
            self._cron_table.setItem(row, 0, expression_item)

            media_item = QTableWidgetItem(media_name)
            media_item.setToolTip(media_source)
            self._cron_table.setItem(row, 1, media_item)

            hard_sync_selector = QComboBox(self._cron_table)
            hard_sync_selector.addItems(["Yes", "No"])
            hard_sync_selector.setCurrentText("Yes" if entry.hard_sync else "No")
            hard_sync_selector.setToolTip(media_source)
            hard_sync_selector.currentTextChanged.connect(
                lambda value, entry_id=entry.id: self._on_cron_hard_sync_changed(entry_id, value)
            )
            self._cron_table.setCellWidget(row, 2, hard_sync_selector)

            status_selector = QComboBox(self._cron_table)
            status_selector.addItems(["Enabled", "Disabled"])
            status_selector.setCurrentText("Enabled" if entry.enabled else "Disabled")
            status_selector.setToolTip(media_source)
            status_selector.currentTextChanged.connect(
                lambda value, entry_id=entry.id: self._on_cron_status_changed(entry_id, value)
            )
            self._cron_table.setCellWidget(row, 3, status_selector)

        self._cron_table.resizeColumnsToContents()

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

    def _is_schedule_entry_protected_from_removal(self, entry: ScheduleEntry) -> bool:
        cron_entry = self._cron_entry_by_id(entry.cron_id)
        return cron_entry is not None and cron_entry.enabled

    @staticmethod
    def _runtime_cron_dates() -> set[date]:
        today = datetime.now().astimezone().date()
        return {today - timedelta(days=1), today, today + timedelta(days=1)}

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

        if entry.status in {SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED}:
            return
        if not cron_entry.enabled:
            entry.status = SCHEDULE_STATUS_DISABLED
            return
        entry.status = entry.cron_status_override or SCHEDULE_STATUS_PENDING

    def _refresh_cron_schedule_entries(self, target_dates: set[date] | None = None) -> None:
        refreshed_entries: list[ScheduleEntry] = []
        for entry in self._schedule_entries:
            if entry.cron_id is None:
                refreshed_entries.append(entry)
                continue
            cron_entry = self._cron_entry_by_id(entry.cron_id)
            if cron_entry is None:
                if entry.status in {SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED}:
                    refreshed_entries.append(entry)
                continue
            self._apply_cron_entry_defaults(entry, cron_entry)
            refreshed_entries.append(entry)

        self._schedule_entries = refreshed_entries
        existing_by_id = {entry.id: entry for entry in self._schedule_entries}
        if not target_dates:
            return

        timezone = datetime.now().astimezone().tzinfo
        for cron_entry in self._cron_entries:
            if not cron_entry.enabled:
                continue
            try:
                expression = CronExpression.parse(cron_entry.expression)
            except CronParseError:
                continue
            for target_date in sorted(target_dates):
                for start_at in expression.iter_datetimes_on_date(target_date, timezone):
                    entry_id = self._cron_occurrence_entry_id(cron_entry.id, start_at)
                    entry = existing_by_id.get(entry_id)
                    if entry is None:
                        entry = ScheduleEntry(
                            id=entry_id,
                            media_id=cron_entry.media_id,
                            start_at=start_at,
                            hard_sync=cron_entry.hard_sync,
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

    def _refresh_cron_runtime_window(self) -> None:
        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)

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
        self._schedule_table.setRowCount(len(entries))
        now = datetime.now().astimezone()
        for row, entry in enumerate(entries):
            media = self._media_items.get(entry.media_id)
            media_name = media.title if media else f"Missing ({entry.media_id[:8]})"
            media_source = media.source if media else f"Missing media ID: {entry.media_id}"
            status = entry.status.capitalize()
            start_label = entry.start_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            duration_label = self._format_duration(entry.duration)
            cron_entry = self._cron_entry_by_id(entry.cron_id)
            origin_label = f"Generated from CRON: {cron_entry.expression}" if cron_entry is not None else "Manual schedule"
            tooltip = f"{media_source}\n{origin_label}"
            palette = self._schedule_entry_palette(entry, now)

            start_item = QTableWidgetItem(start_label)
            start_item.setData(Qt.UserRole, entry.id)
            start_item.setToolTip(tooltip)
            self._apply_item_palette(start_item, palette)
            self._schedule_table.setItem(row, 0, start_item)

            duration_item = QTableWidgetItem(duration_label)
            duration_item.setToolTip(tooltip)
            self._apply_item_palette(duration_item, palette)
            self._schedule_table.setItem(row, 1, duration_item)

            media_item = QTableWidgetItem(media_name)
            media_item.setToolTip(tooltip)
            self._apply_item_palette(media_item, palette)
            self._schedule_table.setItem(row, 2, media_item)

            entry_expired = entry.status == SCHEDULE_STATUS_DISABLED and self._normalized_start(entry.start_at) < now
            cron_globally_disabled = cron_entry is not None and not cron_entry.enabled
            is_locked = entry.status in {SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED} or entry_expired

            hard_sync_selector = QComboBox(self._schedule_table)
            hard_sync_selector.addItems(["Yes", "No"])
            hard_sync_selector.setCurrentText("Yes" if entry.hard_sync else "No")
            hard_sync_selector.setEnabled(not is_locked)
            hard_sync_selector.setToolTip(tooltip)
            hard_sync_selector.currentTextChanged.connect(
                lambda value, entry_id=entry.id: self._on_schedule_hard_sync_changed(entry_id, value)
            )
            self._apply_widget_palette(hard_sync_selector, palette)
            self._schedule_table.setCellWidget(row, 3, hard_sync_selector)

            status_selector = QComboBox(self._schedule_table)
            if cron_globally_disabled:
                status_selector.addItem("Disabled")
            else:
                status_selector.addItems(["Pending", "Disabled"])
            if entry.status == SCHEDULE_STATUS_FIRED:
                status_selector.addItem("Fired")
            if entry.status == SCHEDULE_STATUS_MISSED:
                status_selector.addItem("Missed")
            status_selector.setCurrentText(status)
            status_selector.setEnabled(not is_locked and not cron_globally_disabled)
            status_selector.setToolTip(tooltip)
            status_selector.currentTextChanged.connect(
                lambda value, entry_id=entry.id: self._on_schedule_status_changed(entry_id, value)
            )
            self._apply_widget_palette(status_selector, palette)
            self._schedule_table.setCellWidget(row, 4, status_selector)

        self._schedule_table.resizeColumnsToContents()

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
        for current, next_entry in zip(entries, entries[1:]):
            current_start = self._normalized_start(current.start_at)
            next_start = self._normalized_start(next_entry.start_at)
            current.duration = max(0, int((next_start - current_start).total_seconds()))

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

        duration = self._probe_media_duration_seconds(media.source)
        self._media_duration_cache[media_id] = duration
        return duration

    @staticmethod
    def _probe_media_duration_seconds(source: str) -> int | None:
        path: Path | None = None
        url = QUrl(source)
        if url.isValid() and url.scheme():
            if url.scheme().lower() == "file":
                local_path = url.toLocalFile()
                if local_path:
                    path = Path(local_path)
            else:
                return None
        else:
            path = Path(source).expanduser()

        if path is None or not path.is_file():
            return None

        duration = MainWindow._probe_with_ffprobe(path)
        if duration is not None:
            return duration
        return MainWindow._probe_with_ffmpeg(path)

    @staticmethod
    def _probe_with_ffprobe(path: Path) -> int | None:
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
                timeout=10,
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
    def _probe_with_ffmpeg(path: Path) -> int | None:
        try:
            result = subprocess.run(
                ["ffmpeg", "-i", str(path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

        output = f"{result.stdout}\n{result.stderr}"
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
        if match is None:
            return None

        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = float(match.group(3))
        return max(0, math.ceil(hours * 3600 + minutes * 60 + seconds))

    @staticmethod
    def _normalized_start(start_at: datetime) -> datetime:
        if start_at.tzinfo is None:
            return start_at.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return start_at

    @staticmethod
    def _format_duration(duration_seconds: int | None) -> str:
        if duration_seconds is None:
            return "-"
        hours, remainder = divmod(duration_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _update_now_playing_label(self) -> None:
        media = self._player.current_media
        if media is None:
            self._now_playing_label.setText("None")
            return
        elapsed_seconds = max(0, self._current_playback_position_ms // 1000)
        self._now_playing_label.setText(
            f"{media.title} - {self._format_duration(elapsed_seconds)}"
        )

    def _media_log_name(self, media_id: str) -> str:
        media = self._media_items.get(media_id)
        if media is None:
            return f"missing:{media_id[:8]}"
        return media.title

    def _selected_media_id(self) -> str | None:
        if self._last_source_panel == "urls":
            return self._selected_url_media_id()

        return self._selected_filesystem_media_id()

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
        self._refresh_cron_schedule_entries({self._schedule_filter_date})
        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()

    @Slot(QModelIndex)
    def _on_filesystem_selected(self, _: QModelIndex) -> None:
        self._last_source_panel = "filesystem"

    @Slot()
    def _on_urls_selection_changed(self) -> None:
        if self._urls_table.currentRow() >= 0:
            self._last_source_panel = "urls"

    @Slot(int)
    def _on_library_tab_changed(self, index: int) -> None:
        self._last_source_panel = "urls" if index == 1 else "filesystem"

    @staticmethod
    def _is_supported_media_file(path: Path) -> bool:
        return path.suffix.lower() in SUPPORTED_MEDIA_EXTENSIONS

    @staticmethod
    def _is_stream_source(source: str) -> bool:
        url = QUrl(source)
        return url.isValid() and bool(url.scheme())

    def _selected_url_media_id(self) -> str | None:
        row = self._urls_table.currentRow()
        if row < 0:
            return None
        item = self._urls_table.item(row, 0)
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def _selected_filesystem_media_id(self) -> str | None:
        index = self._filesystem_view.currentIndex()
        if not index.isValid():
            return None
        path = Path(self._filesystem_model.filePath(index))
        if not path.is_file() or not self._is_supported_media_file(path):
            return None
        media = self._ensure_file_media_item(path)
        return media.id

    def _ensure_file_media_item(self, file_path: Path) -> MediaItem:
        resolved = str(file_path.resolve())
        for item in self._media_items.values():
            if item.source == resolved:
                return item
        media = MediaItem.create(title=file_path.name, source=resolved)
        self._media_items[media.id] = media
        self._media_duration_cache.pop(media.id, None)
        self._save_state()
        return media

    @Slot()
    def _add_media_url(self) -> None:
        url, ok = QInputDialog.getText(self, "Add Stream URL", "URL (http/https/rtsp/etc):")
        if not ok or not url.strip():
            return

        title, ok_title = QInputDialog.getText(self, "Display Name", "Title:", text=url.strip())
        if not ok_title or not title.strip():
            title = url.strip()

        media = MediaItem.create(title=title.strip(), source=url.strip())
        self._media_items[media.id] = media
        self._media_duration_cache.pop(media.id, None)
        self._refresh_urls_list()
        self._save_state()
        self._append_log(f"Added stream: {title.strip()}")

    def _remove_media_by_id(self, media_id: str) -> None:
        removed = self._media_items.pop(media_id, None)
        if removed is None:
            return
        self._media_duration_cache.pop(media_id, None)

        self._cron_entries = [entry for entry in self._cron_entries if entry.media_id != media_id]
        self._schedule_entries = [entry for entry in self._schedule_entries if entry.media_id != media_id]
        self._play_queue = deque([item_id for item_id in self._play_queue if item_id != media_id])
        if self._player.current_media is not None and self._player.current_media.id == media_id:
            self._player.clear_current_media()
            self._now_playing_label.setText("None")
            self._update_player_visual_state()

        self._refresh_cron_schedule_entries(self._runtime_cron_dates() | {self._schedule_filter_date})
        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_urls_list()
        self._refresh_cron_table()
        self._refresh_schedule_table()
        self._save_state()
        self._append_log(f"Removed media: {removed.title}")

    @Slot()
    def _remove_selected_url(self) -> None:
        media_id = self._selected_url_media_id()
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
        media_id = self._selected_url_media_id()
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

        media.source = updated_url.strip()
        media.title = updated_title.strip()
        self._media_duration_cache.pop(media_id, None)
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
        media = self._media_items.get(media_id)
        if media is None:
            return
        self._player.play_media(media)

    @Slot()
    def _queue_selected_media(self) -> None:
        media_id = self._selected_media_id()
        if media_id is None:
            QMessageBox.information(self, "No Selection", "Select a media item first.")
            return
        media = self._media_items.get(media_id)
        if media is None:
            return
        self._play_queue.append(media_id)
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

        self._refresh_cron_schedule_entries(self._runtime_cron_dates() | {self._schedule_filter_date})
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
        )
        self._cron_entries.append(entry)
        self._refresh_cron_schedule_entries(self._runtime_cron_dates() | {self._schedule_filter_date})
        next_occurrence = self._next_cron_occurrence(entry, datetime.now().astimezone())
        if next_occurrence is not None:
            self._set_schedule_filter_date(next_occurrence.date())
            self._refresh_cron_schedule_entries({next_occurrence.date()})
        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_cron_table()
        self._refresh_schedule_table()
        self._save_state()
        self._append_log(
            f"Added CRON schedule '{entry.expression}' for media '{self._media_log_name(entry.media_id)}'"
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
        new_hard_sync = value == "Yes"
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

    def _on_schedule_status_changed(self, entry_id: str, value: str) -> None:
        updated_entry: ScheduleEntry | None = None
        for entry in self._schedule_entries:
            if entry.id == entry_id:
                if entry.status in {SCHEDULE_STATUS_FIRED, SCHEDULE_STATUS_MISSED}:
                    return
                cron_entry = self._cron_entry_by_id(entry.cron_id)
                if cron_entry is not None and not cron_entry.enabled:
                    self._refresh_schedule_table()
                    return
                new_status = SCHEDULE_STATUS_PENDING if value == "Pending" else SCHEDULE_STATUS_DISABLED
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
            f"Set status for media '{self._media_log_name(updated_entry.media_id)}' to {value}"
        )

    def _on_cron_hard_sync_changed(self, cron_id: str, value: str) -> None:
        updated_entry: CronEntry | None = None
        new_hard_sync = value == "Yes"
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

        self._refresh_cron_schedule_entries(self._runtime_cron_dates() | {self._schedule_filter_date})
        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()
        self._save_state()
        self._append_log(
            f"Set CRON hard sync for media '{self._media_log_name(updated_entry.media_id)}' to {value}"
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

        self._refresh_cron_schedule_entries(self._runtime_cron_dates() | {self._schedule_filter_date})
        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()
        self._save_state()
        self._append_log(
            f"Set CRON status for media '{self._media_log_name(updated_entry.media_id)}' to {value}"
        )

    @Slot(object)
    def _on_schedule_triggered(self, entry: ScheduleEntry) -> None:
        if not self._automation_playing:
            if entry.one_shot and entry.status == SCHEDULE_STATUS_PENDING:
                entry.status = SCHEDULE_STATUS_MISSED
            self._append_log(f"Ignoring schedule {entry.id}: automation is stopped")
            self._refresh_schedule_table()
            self._save_state()
            return
        media = self._media_items.get(entry.media_id)
        if media is None:
            if entry.one_shot:
                entry.status = SCHEDULE_STATUS_MISSED
            self._append_log(f"Skipping schedule {entry.id}: media '{self._media_log_name(entry.media_id)}' not found")
            self._refresh_schedule_table()
            self._save_state()
            return
        if entry.one_shot:
            entry.status = SCHEDULE_STATUS_FIRED

        if entry.hard_sync or not self._player.is_playing():
            if entry.hard_sync and self._player.is_playing():
                current_media_name = (
                    self._player.current_media.title
                    if self._player.current_media is not None
                    else "nothing"
                )
                self._append_log(
                    f"Hard sync active for '{media.title}': interrupting '{current_media_name}'"
                )
            self._player.play_media(media)
        else:
            self._play_queue.append(media.id)
            self._append_log(f"Player busy; queued scheduled media '{media.title}'")

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
        while self._play_queue:
            next_media_id = self._play_queue.popleft()
            next_media = self._media_items.get(next_media_id)
            if next_media is None:
                continue
            self._save_state()
            self._player.play_media(next_media)
            return
        self._player.clear_current_media()
        self._current_playback_position_ms = 0
        self._save_state()
        self._update_now_playing_label()
        self._update_player_visual_state()

    def _active_schedule_entry_at(self, now: datetime) -> tuple[ScheduleEntry, datetime] | None:
        entries = sorted(self._schedule_entries, key=lambda entry: self._normalized_start(entry.start_at))
        for index, entry in enumerate(entries):
            start_at = self._normalized_start(entry.start_at)
            if now < start_at:
                break

            end_at: datetime | None = None
            if index + 1 < len(entries):
                end_at = self._normalized_start(entries[index + 1].start_at)
            elif entry.duration is not None:
                end_at = start_at + timedelta(seconds=max(0, entry.duration))

            if end_at is not None and now >= end_at:
                continue
            if entry.status == SCHEDULE_STATUS_DISABLED:
                return None
            return entry, start_at
        return None

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

    @Slot()
    def _on_play_clicked(self) -> None:
        now = datetime.now().astimezone()
        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)
        if not self._automation_playing:
            self._automation_playing = True
            self._set_automation_status(True)
            self._scheduler.start()
            self._append_log("Automation status changed to Playing")
            self._mark_missed_entries_missed(now)

        if self._player.is_playing():
            return

        active_entry = self._active_schedule_entry_at(now)
        if active_entry is not None:
            entry, start_at = active_entry
            if entry.status != SCHEDULE_STATUS_PENDING:
                self._refresh_schedule_table()
                self._save_state()
                return
            media = self._media_items.get(entry.media_id)
            if media is None:
                if entry.one_shot:
                    entry.status = SCHEDULE_STATUS_MISSED
                self._append_log(f"Play ignored: scheduled media '{self._media_log_name(entry.media_id)}' not found")
                self._refresh_schedule_table()
                self._save_state()
                return
            if entry.one_shot:
                entry.status = SCHEDULE_STATUS_FIRED
            offset_ms = max(
                0,
                int((now - start_at).total_seconds() * 1000),
            )
            self._player.play_media(media, start_position_ms=offset_ms)
            self._append_log(
                f"Started scheduled media '{media.title}' from {self._format_duration(offset_ms // 1000)}"
            )
            self._refresh_schedule_table()
            self._save_state()
            return
        if self._player.has_active_media():
            self._player.play()
            return
        if self._play_queue:
            self._play_next_from_queue()
            return
        entries_detail = []
        for e in sorted(self._schedule_entries, key=lambda e: self._normalized_start(e.start_at)):
            s = self._normalized_start(e.start_at).strftime("%H:%M:%S")
            name = self._media_log_name(e.media_id)
            entries_detail.append(f"{name}@{s}/{e.status}/dur={e.duration}")
        self._append_log(
            f"Play ignored: no active or queued media at {now.strftime('%H:%M:%S')} "
            f"— schedule: [{', '.join(entries_detail) or 'empty'}]"
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

    @Slot()
    def _show_cron_help(self) -> None:
        dialog = CronHelpDialog(self)
        dialog.exec()

    def _set_automation_status(self, is_playing: bool) -> None:
        if is_playing:
            self._automation_status_label.setText("Automation: Playing")
            self._automation_status_label.setStyleSheet(
                "color: #0f5132; background-color: #d1e7dd; "
                "border: 1px solid #75b798; border-radius: 6px; "
                "padding: 2px 8px; font-weight: 600;"
            )
            return
        self._automation_status_label.setText("Automation: Stopped")
        self._automation_status_label.setStyleSheet(
            "color: #842029; background-color: #f8d7da; "
            "border: 1px solid #f1aeb5; border-radius: 6px; "
            "padding: 2px 8px; font-weight: 600;"
        )

    def _mark_missed_entries_missed(self, now: datetime) -> None:
        active_entry = self._active_schedule_entry_at(now)
        active_entry_id = active_entry[0].id if active_entry is not None else None
        changed = False
        skipped = 0
        for entry in self._schedule_entries:
            if entry.status != SCHEDULE_STATUS_PENDING or not entry.one_shot:
                continue
            if entry.id == active_entry_id:
                continue
            start_at = self._normalized_start(entry.start_at)
            if start_at < now:
                entry.status = SCHEDULE_STATUS_MISSED
                skipped += 1
                changed = True
        if not changed:
            return
        self._refresh_schedule_table()
        self._save_state()
        self._append_log(f"Marked {skipped} missed one-shot schedule item(s) as missed")

    @Slot(bool)
    def _on_fullscreen_toggled(self, checked: bool) -> None:
        media = self._player.current_media
        ext = ""
        if media is not None:
            try:
                ext = Path(media.source).suffix.lower()
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
                title = media.title if media is not None else "Now Playing"
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
        self._scheduler.stop()
        self._save_state()
        super().closeEvent(event)

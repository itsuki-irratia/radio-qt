from __future__ import annotations

from collections import deque
from datetime import date, datetime, timedelta
import math
from pathlib import Path
import re
import subprocess
from uuid import NAMESPACE_URL, uuid5

from PySide6.QtCore import QDate, QDateTime, QModelIndex, QSize, Qt, QTimer, Slot, QEvent
from PySide6.QtGui import QAction, QBrush, QCloseEvent, QColor
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDateEdit,
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
    CronDialog,
    CronHelpDialog,
    FullscreenOverlay,
    ScheduleDialog,
    WaveformWidget,
    refresh_cron_table,
    refresh_schedule_table,
    refresh_urls_table,
)


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
        self._play_queue: deque[QueueItem] = deque()
        self._last_source_panel = "filesystem"
        self._automation_playing = False
        self._schedule_auto_focus_enabled = False
        self._fullscreen_active = False
        self._schedule_filter_date = datetime.now().astimezone().date()
        self._current_playback_position_ms = 0

        self._player = MediaPlayerController(self)
        self._scheduler = RadioScheduler(parent=self)
        self._cron_refresh_timer = QTimer(self)
        self._cron_refresh_timer.setInterval(30000)
        self._schedule_focus_timer = QTimer(self)
        self._schedule_focus_timer.setInterval(1000)

        self._build_ui()
        self._build_menu_bar()
        self._wire_signals()
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
        self._export_logs_action = QAction("Export &Logs...", self)
        help_menu.addAction(self._export_logs_action)
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
        self._schedule_focus_checkbox = QCheckBox("Focus current program")
        self._schedule_focus_checkbox.setToolTip(
            "Automatically keep the current schedule entry selected in the table."
        )
        filter_row.addWidget(self._schedule_focus_checkbox)
        filter_row.addStretch()

        self._schedule_overlap_note = QLabel(
            "Overlap rule: the next scheduled item can cut off the current one.",
            datetime_tab,
        )
        self._schedule_overlap_note.setWordWrap(True)
        self._schedule_overlap_note.setStyleSheet("color: #6b7280; font-size: 11px;")

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
        buttons_row.addStretch()

        datetime_layout.addLayout(filter_row)
        datetime_layout.addWidget(self._schedule_overlap_note)
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
        self._schedule_focus_checkbox.toggled.connect(self._on_schedule_auto_focus_toggled)

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
        self._schedule_focus_timer.timeout.connect(self._refresh_schedule_auto_focus)
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
        self._schedule_entries = state.schedule_entries
        self._cron_entries = state.cron_entries
        self._play_queue = deque(state.queue)
        self._schedule_auto_focus_enabled = state.schedule_auto_focus
        self._refresh_cron_schedule_entries(self._runtime_cron_dates() | {self._schedule_filter_date})
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
        self._refresh_cron_schedule_entries({self._schedule_filter_date})
        self._recalculate_schedule_durations()

        self._refresh_urls_list()
        self._refresh_cron_table()
        self._refresh_schedule_table()
        self._apply_schedule_auto_focus(force=True)
        self._scheduler.set_entries(self._schedule_entries)
        self._player.set_volume(self._volume_slider.value())
        self._update_player_visual_state()
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
        self._append_log(f"Loaded state from {self._state_path}")

    def _save_state(self) -> None:
        state = AppState(
            media_items=list(self._media_items.values()),
            schedule_entries=self._schedule_entries,
            cron_entries=self._cron_entries,
            queue=list(self._play_queue),
            schedule_auto_focus=self._schedule_auto_focus_enabled,
        )
        save_state(self._state_path, state)

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

        duration = self._probe_media_duration_seconds(media.source)
        self._media_duration_cache[media_id] = duration
        return duration

    @staticmethod
    def _probe_media_duration_seconds(source: str) -> int | None:
        path = local_media_path_from_source(source)
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
        return normalized_start(start_at)

    @staticmethod
    def _format_duration(duration_seconds: int | None) -> str:
        if duration_seconds is None:
            return "-"
        hours, remainder = divmod(duration_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _duration_display_details(
        media: MediaItem | None,
        duration_seconds: int | None,
    ) -> tuple[str, str]:
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
        return "Unknown", "Duration unavailable: ffprobe/ffmpeg could not read this file"

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
        self._now_playing_label.setText(
            f"{media.title} - {self._format_duration(elapsed_seconds)}"
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
            self._refresh_cron_schedule_entries({self._schedule_filter_date})
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

    def _selected_media_id(self) -> str | None:
        if self._last_source_panel == "urls":
            return selected_url_media_id(self._urls_table)

        media_id, created = selected_filesystem_media_id(
            self._filesystem_view,
            self._filesystem_model,
            self._media_items,
            self._media_duration_cache,
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
        self._refresh_cron_schedule_entries({self._schedule_filter_date})
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
        self._last_source_panel = "urls" if index == 1 else "filesystem"

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
            self._player.play_media(outcome.media)
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
            self._player.play_media(result.media)
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
            self._player.play_media(active_play.media, start_position_ms=active_play.offset_ms)
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

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
import math
from pathlib import Path
import re
import subprocess

from PySide6.QtCore import QDateTime, QModelIndex, Qt, QUrl, Slot
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
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
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
    QInputDialog,
)

from .models import AppState, MediaItem, ScheduleEntry
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
        self._enabled_checkbox = QCheckBox("Enabled", self)
        self._enabled_checkbox.setChecked(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Start at:"))
        layout.addWidget(self._datetime_edit)
        layout.addWidget(self._hard_sync_checkbox)
        layout.addWidget(self._enabled_checkbox)
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

    def enabled(self) -> bool:
        return self._enabled_checkbox.isChecked()


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
        self._play_queue: deque[str] = deque()
        self._last_source_panel = "filesystem"
        self._automation_playing = False

        self._player = MediaPlayerController(self)
        self._scheduler = RadioScheduler(parent=self)

        self._build_ui()
        self._wire_signals()
        self._load_initial_state()

    def _build_ui(self) -> None:
        root = QWidget(self)
        root_layout = QVBoxLayout(root)

        self._video_widget = QVideoWidget(root)
        self._video_widget.setMinimumHeight(180)
        self._player.set_video_output(self._video_widget)

        self._now_playing_label = QLabel("Now playing: nothing", root)
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
        self._volume_slider.setValue(80)
        controls_layout.addWidget(self._play_button)
        controls_layout.addWidget(self._stop_button)
        controls_layout.addWidget(QLabel("Volume"))
        controls_layout.addWidget(self._volume_slider)

        panels_layout = QHBoxLayout()
        panels_layout.addWidget(self._build_library_panel(), 1)
        panels_layout.addWidget(self._build_schedule_panel(), 1)

        self._log_view = QPlainTextEdit(root)
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(2000)
        self._log_view.setPlaceholderText("Runtime events...")
        self._log_view.setMinimumHeight(80)

        root_layout.addWidget(self._video_widget, 2)
        root_layout.addLayout(now_playing_layout)
        root_layout.addLayout(controls_layout)
        root_layout.addLayout(panels_layout, 7)
        root_layout.addWidget(self._log_view)

        self.setCentralWidget(root)

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
        group = QGroupBox("Datetime Schedule")
        layout = QVBoxLayout(group)

        self._schedule_table = QTableWidget(group)
        self._schedule_table.setColumnCount(6)
        self._schedule_table.setHorizontalHeaderLabels(
            ["Start Time", "Duration", "Media", "Hard Sync", "Enabled", "Status"]
        )
        self._schedule_table.horizontalHeader().setStretchLastSection(True)
        self._schedule_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._schedule_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._schedule_table.customContextMenuRequested.connect(self._on_schedule_context_menu)

        buttons_row = QHBoxLayout()
        self._add_schedule_button = QPushButton("Schedule Selected Media")
        buttons_row.addWidget(self._add_schedule_button)

        layout.addWidget(self._schedule_table)
        layout.addLayout(buttons_row)
        return group

    def _wire_signals(self) -> None:
        self._filesystem_view.clicked.connect(self._on_filesystem_selected)
        self._urls_table.itemSelectionChanged.connect(self._on_urls_selection_changed)
        self._library_tabs.currentChanged.connect(self._on_library_tab_changed)
        self._add_url_button.clicked.connect(self._add_media_url)

        self._add_schedule_button.clicked.connect(self._add_schedule_entry)

        self._play_button.clicked.connect(self._on_play_clicked)
        self._stop_button.clicked.connect(self._on_stop_clicked)
        self._volume_slider.valueChanged.connect(self._player.set_volume)

        self._player.media_started.connect(self._on_media_started)
        self._player.media_finished.connect(self._on_media_finished)
        self._player.playback_state_changed.connect(self._on_playback_state_changed)
        self._player.playback_error.connect(self._on_player_error)

        self._scheduler.schedule_triggered.connect(self._on_schedule_triggered)
        self._scheduler.log.connect(self._append_log)

    def _load_initial_state(self) -> None:
        app_started_at = datetime.now().astimezone()
        state = load_state(self._state_path)
        self._media_items = {item.id: item for item in state.media_items}
        self._media_duration_cache.clear()
        self._schedule_entries = state.schedule_entries
        self._play_queue = deque(state.queue)
        expired_entries = self._expire_missed_one_shots(app_started_at)
        self._recalculate_schedule_durations()

        self._refresh_urls_list()
        self._refresh_schedule_table()
        self._scheduler.set_entries(self._schedule_entries)
        self._player.set_volume(self._volume_slider.value())
        if expired_entries:
            self._append_log(
                f"Marked {expired_entries} missed one-shot schedule item(s) as fired on startup"
            )
            self._save_state()
        self._append_log(f"Loaded state from {self._state_path}")

    def _save_state(self) -> None:
        state = AppState(
            media_items=list(self._media_items.values()),
            schedule_entries=self._schedule_entries,
            queue=list(self._play_queue),
        )
        save_state(self._state_path, state)

    def _expire_missed_one_shots(self, reference_time: datetime) -> int:
        expired = 0
        for entry in self._schedule_entries:
            if entry.fired or not entry.one_shot:
                continue
            start_at = entry.start_at
            if start_at.tzinfo is None:
                start_at = start_at.replace(tzinfo=reference_time.tzinfo)
            if start_at < reference_time:
                entry.fired = True
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

    def _refresh_schedule_table(self) -> None:
        entries = sorted(self._schedule_entries, key=lambda entry: entry.start_at)
        self._schedule_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            media = self._media_items.get(entry.media_id)
            media_name = media.title if media else f"Missing ({entry.media_id[:8]})"
            media_source = media.source if media else f"Missing media ID: {entry.media_id}"
            status = "Fired" if entry.fired else ("Disabled" if not entry.enabled else "Pending")
            start_label = entry.start_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            duration_label = self._format_duration(entry.duration)

            start_item = QTableWidgetItem(start_label)
            start_item.setData(Qt.UserRole, entry.id)
            start_item.setToolTip(media_source)
            self._schedule_table.setItem(row, 0, start_item)

            duration_item = QTableWidgetItem(duration_label)
            duration_item.setToolTip(media_source)
            self._schedule_table.setItem(row, 1, duration_item)

            media_item = QTableWidgetItem(media_name)
            media_item.setToolTip(media_source)
            self._schedule_table.setItem(row, 2, media_item)

            hard_sync_item = QTableWidgetItem("Yes" if entry.hard_sync else "No")
            hard_sync_item.setToolTip(media_source)
            self._schedule_table.setItem(row, 3, hard_sync_item)
            enabled_selector = QComboBox(self._schedule_table)
            enabled_selector.addItems(["Yes", "No"])
            enabled_selector.setCurrentText("Yes" if entry.enabled else "No")
            enabled_selector.setEnabled(not entry.fired)
            enabled_selector.setToolTip(media_source)
            enabled_selector.currentTextChanged.connect(
                lambda value, entry_id=entry.id: self._on_schedule_enabled_changed(entry_id, value)
            )
            self._schedule_table.setCellWidget(row, 4, enabled_selector)

            status_item = QTableWidgetItem(status)
            status_item.setToolTip(media_source)
            self._schedule_table.setItem(row, 5, status_item)

        self._schedule_table.resizeColumnsToContents()

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

        self._schedule_entries = [entry for entry in self._schedule_entries if entry.media_id != media_id]
        self._play_queue = deque([item_id for item_id in self._play_queue if item_id != media_id])
        if self._player.current_media is not None and self._player.current_media.id == media_id:
            self._player.clear_current_media()
            self._now_playing_label.setText("Now playing: nothing")

        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_urls_list()
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

        self._recalculate_schedule_durations()
        dialog = ScheduleDialog(self, initial_start_at=self._default_next_schedule_start())
        if dialog.exec() != QDialog.Accepted:
            return

        entry = ScheduleEntry.create(
            media_id=media_id,
            start_at=dialog.selected_datetime(),
            hard_sync=dialog.hard_sync(),
        )
        entry.enabled = dialog.enabled()
        if entry.one_shot and self._normalized_start(entry.start_at) <= datetime.now().astimezone():
            entry.fired = True
        self._schedule_entries.append(entry)

        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()
        self._save_state()
        media_name = self._media_log_name(entry.media_id)
        if entry.fired:
            self._append_log(
                f"Scheduled media '{media_name}' in the past; entry was marked as fired"
            )
        self._append_log(
            f"Scheduled media '{media_name}' for {entry.start_at.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}"
        )

    @Slot()
    def _remove_schedule_entry(self) -> None:
        entry_id = self._selected_schedule_entry_id()
        if entry_id is None:
            QMessageBox.information(self, "No Selection", "Select a schedule row first.")
            return

        removed_entry = next((entry for entry in self._schedule_entries if entry.id == entry_id), None)
        media_name = self._media_log_name(removed_entry.media_id) if removed_entry else "unknown"
        start_label = removed_entry.start_at.astimezone().strftime("%Y-%m-%d %H:%M:%S") if removed_entry else "unknown"
        result = QMessageBox.question(
            self,
            "Confirm Removal",
            f"Are you sure you want to remove the schedule entry for '{media_name}' at {start_label}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if result != QMessageBox.Yes:
            return

        original_count = len(self._schedule_entries)
        self._schedule_entries = [entry for entry in self._schedule_entries if entry.id != entry_id]
        if len(self._schedule_entries) == original_count:
            return

        self._recalculate_schedule_durations()
        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()
        self._save_state()
        if removed_entry is None:
            self._append_log("Removed schedule entry")
        else:
            self._append_log(f"Removed schedule entry for media '{self._media_log_name(removed_entry.media_id)}'")

    @Slot("QPoint")
    def _on_schedule_context_menu(self, position) -> None:
        item = self._schedule_table.itemAt(position)
        if item is None:
            return
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self._schedule_table)
        remove_action = QAction("Remove Entry", menu)
        remove_action.triggered.connect(self._remove_schedule_entry)
        menu.addAction(remove_action)
        menu.exec(self._schedule_table.viewport().mapToGlobal(position))

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

    @Slot()
    def _toggle_schedule_entry_enabled(self) -> None:
        entry_id = self._selected_schedule_entry_id()
        if entry_id is None:
            QMessageBox.information(self, "No Selection", "Select a schedule row first.")
            return

        toggled_entry: ScheduleEntry | None = None
        for entry in self._schedule_entries:
            if entry.id == entry_id:
                if entry.fired:
                    self._append_log(
                        f"Ignored toggle for media '{self._media_log_name(entry.media_id)}': status is Fired"
                    )
                    return
                entry.enabled = not entry.enabled
                toggled_entry = entry
                break

        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()
        self._save_state()
        if toggled_entry is None:
            self._append_log("Toggled schedule entry enabled/disabled")
        else:
            state = "enabled" if toggled_entry.enabled else "disabled"
            self._append_log(
                f"Toggled schedule entry for media '{self._media_log_name(toggled_entry.media_id)}' to {state}"
            )

    def _on_schedule_enabled_changed(self, entry_id: str, value: str) -> None:
        updated_entry: ScheduleEntry | None = None
        new_enabled = value == "Yes"
        for entry in self._schedule_entries:
            if entry.id == entry_id:
                if entry.fired:
                    return
                if entry.enabled == new_enabled:
                    return
                entry.enabled = new_enabled
                updated_entry = entry
                break

        if updated_entry is None:
            return

        self._scheduler.set_entries(self._schedule_entries)
        self._refresh_schedule_table()
        self._save_state()
        state = "enabled" if updated_entry.enabled else "disabled"
        self._append_log(
            f"Set schedule entry for media '{self._media_log_name(updated_entry.media_id)}' to {state}"
        )

    @Slot(object)
    def _on_schedule_triggered(self, entry: ScheduleEntry) -> None:
        if not self._automation_playing:
            self._append_log(f"Ignoring schedule {entry.id}: automation is stopped")
            return
        media = self._media_items.get(entry.media_id)
        if media is None:
            self._append_log(f"Skipping schedule {entry.id}: media '{self._media_log_name(entry.media_id)}' not found")
            return

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
        self._now_playing_label.setText(f"Now playing: {media.title}")
        self._append_log(f"Now playing '{media.title}'")

    @Slot()
    def _on_media_finished(self) -> None:
        current_media_name = (
            self._player.current_media.title
            if self._player.current_media is not None
            else "unknown"
        )
        self._append_log(f"Media finished '{current_media_name}'")
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
        self._save_state()
        self._now_playing_label.setText("Now playing: nothing")

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
            if not entry.enabled:
                return None
            return entry, start_at
        return None

    @Slot(object)
    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.StoppedState and not self._play_queue:
            self._now_playing_label.setText("Now playing: nothing")

    @Slot()
    def _on_play_clicked(self) -> None:
        now = datetime.now().astimezone()
        if not self._automation_playing:
            self._automation_playing = True
            self._set_automation_status(True)
            self._scheduler.start()
            self._append_log("Automation status changed to Playing")
            self._recalculate_schedule_durations()
            self._mark_missed_entries_fired(now)

        if self._player.is_playing():
            return

        self._recalculate_schedule_durations()
        active_entry = self._active_schedule_entry_at(now)
        if active_entry is not None:
            entry, start_at = active_entry
            if entry.one_shot:
                entry.fired = True
            media = self._media_items.get(entry.media_id)
            if media is None:
                self._append_log(f"Play ignored: scheduled media '{self._media_log_name(entry.media_id)}' not found")
                self._refresh_schedule_table()
                self._save_state()
                return
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
        self._append_log("Play ignored: no active or queued media")

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
        self._now_playing_label.setText("Now playing: nothing")
        self._append_log(f"Playback stopped and media cleared ('{current_media_name}')")

    @Slot(str)
    def _on_player_error(self, message: str) -> None:
        current_media_name = (
            self._player.current_media.title
            if self._player.current_media is not None
            else "unknown"
        )
        self._append_log(f"Player error on '{current_media_name}': {message}")

    @Slot(str)
    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
        self._log_view.appendPlainText(f"[{timestamp}] {message}")

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

    def _mark_missed_entries_fired(self, now: datetime) -> None:
        active_entry = self._active_schedule_entry_at(now)
        active_entry_id = active_entry[0].id if active_entry is not None else None
        changed = False
        skipped = 0
        for entry in self._schedule_entries:
            if entry.fired or not entry.one_shot:
                continue
            if entry.id == active_entry_id:
                continue
            start_at = self._normalized_start(entry.start_at)
            if start_at < now:
                entry.fired = True
                skipped += 1
                changed = True
        if not changed:
            return
        self._refresh_schedule_table()
        self._save_state()
        self._append_log(f"Marked {skipped} missed one-shot schedule item(s) as fired")

    def closeEvent(self, event: QCloseEvent) -> None:
        self._scheduler.stop()
        self._save_state()
        super().closeEvent(event)

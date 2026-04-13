from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QListWidget,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QDateTimeEdit,
    QWidget,
)

from ..cron import CronExpression, CronParseError
from ..models import LibraryTab


def _cron_help_html() -> str:
    return """
    <style>
      table {
        width: 100%;
        border-collapse: collapse;
      }
      th, td {
        padding: 6px;
      }
    </style>
    <h3>CRON format in RadioQt</h3>
    <p>RadioQt uses 6 fields:</p>
    <p><code>second minute hour day-of-month month day-of-week</code></p>

    <h4>Field order</h4>
    <table border="1" cellspacing="0" cellpadding="6">
      <tr>
        <th><code>second</code></th>
        <th><code>minute</code></th>
        <th><code>hour</code></th>
        <th><code>day-of-month</code></th>
        <th><code>month</code></th>
        <th><code>day-of-week</code></th>
      </tr>
      <tr>
        <td><code>0-59</code></td>
        <td><code>0-59</code></td>
        <td><code>0-23</code></td>
        <td><code>1-31</code></td>
        <td><code>1-12</code></td>
        <td><code>1-7</code></td>
      </tr>
    </table>

    <h4>Supported syntax</h4>
    <p><code>*</code> any value<br>
    <code>,</code> list of values<br>
    <code>-</code> range of values<br>
    <code>/</code> step values</p>

    <p>Use numeric values only.<br>
    Month: <code>1-12</code><br>
    Day-of-week starts on Monday:
    <code>1=Monday 2=Tuesday 3=Wednesday 4=Thursday 5=Friday 6=Saturday 7=Sunday</code></p>

    <h4>Examples by use</h4>
    <table border="1" cellspacing="0" cellpadding="6">
      <tr>
        <th>Use</th>
        <th>Expression</th>
        <th>Meaning</th>
      </tr>
      <tr>
        <td>Exact time</td>
        <td><code>0 30 8 * * *</code></td>
        <td>Every day at 08:30:00</td>
      </tr>
      <tr>
        <td>Wildcard <code>*</code></td>
        <td><code>0 * * * * *</code></td>
        <td>Every minute, at second 0</td>
      </tr>
      <tr>
        <td>List <code>,</code></td>
        <td><code>0 0 18 * 1,6,12 *</code></td>
        <td>Every day at 18:00:00, only in months 1, 6 and 12</td>
      </tr>
      <tr>
        <td>Range <code>-</code></td>
        <td><code>0 0 9 * * 1-5</code></td>
        <td>Monday to Friday at 09:00:00</td>
      </tr>
      <tr>
        <td>Step <code>/</code></td>
        <td><code>0 */15 * * * *</code></td>
        <td>Every 15 minutes</td>
      </tr>
      <tr>
        <td>Specific day of month</td>
        <td><code>30 0 12 1 * *</code></td>
        <td>On day 1 of every month at 12:00:30</td>
      </tr>
      <tr>
        <td>Specific weekday</td>
        <td><code>0 0 6 * * 7</code></td>
        <td>Every Sunday at 06:00:00</td>
      </tr>
      <tr>
        <td>Combined range + step</td>
        <td><code>0 0/10 9-17 * * 1-5</code></td>
        <td>Every 10 minutes between 09:00 and 17:59, Monday to Friday</td>
      </tr>
    </table>
    """


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

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Start at:"))
        layout.addWidget(self._datetime_edit)
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


class CronDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        dialog_title: str = "Add CRON Entry",
        initial_expression: str = "",
        initial_fade_in: bool = False,
        initial_fade_out: bool = False,
        expression_only: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(dialog_title)
        self.setMinimumSize(980, 640)
        self._expression_only = bool(expression_only)
        self._expression_edit = QLineEdit(self)
        self._expression_edit.setPlaceholderText("sec min hour day month weekday")
        self._expression_edit.setText(initial_expression.strip())
        self._fade_in_checkbox: QCheckBox | None = None
        self._fade_out_checkbox: QCheckBox | None = None
        if not self._expression_only:
            self._fade_in_checkbox = QCheckBox("Fade in", self)
            self._fade_in_checkbox.setChecked(bool(initial_fade_in))
            self._fade_out_checkbox = QCheckBox("Fade out", self)
            self._fade_out_checkbox.setChecked(bool(initial_fade_out))
        self._cron_examples_text = QTextBrowser(self)
        self._cron_examples_text.setReadOnly(True)
        self._cron_examples_text.setOpenExternalLinks(False)
        self._cron_examples_text.setHtml(_cron_help_html())
        self._cron_examples_text.setVisible(True)
        self._cron_examples_text.setMinimumHeight(220)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("CRON expression (with seconds):"))
        layout.addWidget(self._expression_edit)
        if self._fade_in_checkbox is not None:
            layout.addWidget(self._fade_in_checkbox)
        if self._fade_out_checkbox is not None:
            layout.addWidget(self._fade_out_checkbox)
        layout.addWidget(self._cron_examples_text)
        layout.addWidget(buttons)
        initial_width = max(self.sizeHint().width(), 980)
        initial_height = max(self.sizeHint().height(), 640)
        self.resize(initial_width, initial_height)

    def expression(self) -> str:
        return self._expression_edit.text().strip()

    def fade_in(self) -> bool:
        return self._fade_in_checkbox.isChecked() if self._fade_in_checkbox is not None else False

    def fade_out(self) -> bool:
        return self._fade_out_checkbox.isChecked() if self._fade_out_checkbox is not None else False

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

        text = QTextBrowser(self)
        text.setReadOnly(True)
        text.setOpenExternalLinks(False)
        text.setHtml(_cron_help_html())

        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addWidget(buttons)


class ConfigurationDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        fade_in_duration_seconds: int,
        fade_out_duration_seconds: int,
        font_size_points: int,
        greenwich_time_signal_enabled: bool,
        greenwich_time_signal_path: str,
        library_tabs: list[LibraryTab],
        supported_extensions: list[str],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(760, 520)

        shared_initial_duration = max(
            1,
            int(round((max(1, fade_in_duration_seconds) + max(1, fade_out_duration_seconds)) / 2)),
        )
        self._fade_duration_spinbox = QSpinBox(self)
        self._fade_duration_spinbox.setRange(1, 120)
        self._fade_duration_spinbox.setValue(shared_initial_duration)
        self._font_size_spinbox = QSpinBox(self)
        self._font_size_spinbox.setRange(6, 72)
        self._font_size_spinbox.setValue(max(6, int(font_size_points)))
        self._greenwich_time_signal_selector = QComboBox(self)
        self._greenwich_time_signal_selector.addItems(["True", "False"])
        self._greenwich_time_signal_selector.setCurrentText(
            "True" if greenwich_time_signal_enabled else "False"
        )
        self._greenwich_time_signal_path_edit = QLineEdit(self)
        self._greenwich_time_signal_path_edit.setPlaceholderText(
            "Path to Greenwich Time Signal audio"
        )
        self._greenwich_time_signal_path_edit.setText(greenwich_time_signal_path.strip())
        self._greenwich_time_signal_browse_button = QPushButton("Browse...", self)
        self._greenwich_time_signal_browse_button.clicked.connect(
            self._browse_greenwich_time_signal_path
        )
        self._greenwich_time_signal_path_widget = QWidget(self)
        self._greenwich_time_signal_path_layout = QHBoxLayout(
            self._greenwich_time_signal_path_widget
        )
        self._greenwich_time_signal_path_layout.setContentsMargins(0, 0, 0, 0)
        self._greenwich_time_signal_path_layout.setSpacing(6)
        self._greenwich_time_signal_path_layout.addWidget(
            self._greenwich_time_signal_path_edit, 1
        )
        self._greenwich_time_signal_path_layout.addWidget(
            self._greenwich_time_signal_browse_button
        )
        self._configured_library_tabs: list[LibraryTab] = list(library_tabs)
        self._configured_supported_extensions: list[str] = list(supported_extensions)

        self._settings_sections_list = QListWidget(self)
        self._settings_sections_list.addItem("General Settings")
        self._settings_sections_list.addItem("Greenwich Time Signal")
        self._settings_sections_list.addItem("Custom Paths")
        self._settings_sections_list.addItem("Extensions")
        self._settings_sections_list.setFixedWidth(190)

        self._settings_pages = QStackedWidget(self)

        general_page = QWidget(self)
        general_layout = QVBoxLayout(general_page)
        self._properties_table = QTableWidget(self)
        self._properties_table.setColumnCount(2)
        self._properties_table.setRowCount(2)
        self._properties_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._properties_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._properties_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._properties_table.setAlternatingRowColors(True)
        self._properties_table.verticalHeader().setVisible(False)
        self._properties_table.horizontalHeader().setVisible(False)
        self._properties_table.horizontalHeader().setStretchLastSection(True)

        fade_duration_item = QTableWidgetItem("Fade In / Fade Out in seconds")
        fade_duration_item.setFlags(fade_duration_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        font_size_item = QTableWidgetItem("Font size (pt)")
        font_size_item.setFlags(font_size_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

        self._properties_table.setItem(0, 0, fade_duration_item)
        self._properties_table.setCellWidget(0, 1, self._fade_duration_spinbox)
        self._properties_table.setItem(1, 0, font_size_item)
        self._properties_table.setCellWidget(1, 1, self._font_size_spinbox)
        self._properties_table.resizeColumnsToContents()
        general_layout.addWidget(self._properties_table)
        general_layout.addStretch()

        greenwich_page = QWidget(self)
        greenwich_layout = QVBoxLayout(greenwich_page)
        self._greenwich_table = QTableWidget(self)
        self._greenwich_table.setColumnCount(2)
        self._greenwich_table.setRowCount(2)
        self._greenwich_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._greenwich_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._greenwich_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._greenwich_table.setAlternatingRowColors(True)
        self._greenwich_table.verticalHeader().setVisible(False)
        self._greenwich_table.horizontalHeader().setVisible(False)
        self._greenwich_table.horizontalHeader().setStretchLastSection(True)

        signal_enabled_item = QTableWidgetItem("Enabled")
        signal_enabled_item.setFlags(signal_enabled_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        signal_path_item = QTableWidgetItem("Audio Path")
        signal_path_item.setFlags(signal_path_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

        self._greenwich_table.setItem(0, 0, signal_enabled_item)
        self._greenwich_table.setCellWidget(0, 1, self._greenwich_time_signal_selector)
        self._greenwich_table.setItem(1, 0, signal_path_item)
        self._greenwich_table.setCellWidget(1, 1, self._greenwich_time_signal_path_widget)
        self._greenwich_table.resizeColumnsToContents()
        greenwich_layout.addWidget(self._greenwich_table)
        greenwich_layout.addStretch()

        custom_paths_page = QWidget(self)
        custom_paths_layout = QVBoxLayout(custom_paths_page)
        self._library_tabs_table = QTableWidget(custom_paths_page)
        self._library_tabs_table.setColumnCount(2)
        self._library_tabs_table.setHorizontalHeaderLabels(["Title", "Path"])
        self._library_tabs_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._library_tabs_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._library_tabs_table.setAlternatingRowColors(True)
        self._library_tabs_table.verticalHeader().setVisible(False)
        self._library_tabs_table.horizontalHeader().setStretchLastSection(True)
        self._library_tabs_table.setRowCount(0)
        for tab in self._configured_library_tabs:
            self._append_library_tab_row(tab.title, tab.path)

        library_tabs_buttons = QHBoxLayout()
        self._add_library_tab_button = QPushButton("Add Tab", custom_paths_page)
        self._browse_library_path_button = QPushButton("Browse Path...", custom_paths_page)
        self._remove_library_tab_button = QPushButton(custom_paths_page)
        self._remove_library_tab_button.setToolTip("Remove selected tab")
        self._remove_library_tab_button.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        self._add_library_tab_button.clicked.connect(self._add_library_tab_row)
        self._remove_library_tab_button.clicked.connect(self._remove_selected_library_tab_row)
        self._browse_library_path_button.clicked.connect(self._browse_selected_library_tab_path)
        library_tabs_buttons.addWidget(self._add_library_tab_button)
        library_tabs_buttons.addWidget(self._browse_library_path_button)
        library_tabs_buttons.addWidget(self._remove_library_tab_button)
        library_tabs_buttons.addStretch()
        custom_paths_layout.addWidget(self._library_tabs_table, 1)
        custom_paths_layout.addLayout(library_tabs_buttons)

        extensions_page = QWidget(self)
        extensions_layout = QVBoxLayout(extensions_page)
        self._supported_extensions_table = QTableWidget(extensions_page)
        self._supported_extensions_table.setColumnCount(1)
        self._supported_extensions_table.setHorizontalHeaderLabels(["Extension"])
        self._supported_extensions_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._supported_extensions_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._supported_extensions_table.setAlternatingRowColors(True)
        self._supported_extensions_table.verticalHeader().setVisible(False)
        self._supported_extensions_table.horizontalHeader().setStretchLastSection(True)
        self._supported_extensions_table.setRowCount(0)
        for extension in self._configured_supported_extensions:
            self._append_extension_row(extension)

        extensions_buttons = QHBoxLayout()
        self._add_extension_button = QPushButton("Add Extension", extensions_page)
        self._remove_extension_button = QPushButton(extensions_page)
        self._remove_extension_button.setToolTip("Remove selected extension")
        self._remove_extension_button.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        self._add_extension_button.clicked.connect(self._add_extension_row)
        self._remove_extension_button.clicked.connect(self._remove_selected_extension_row)
        extensions_buttons.addWidget(self._add_extension_button)
        extensions_buttons.addWidget(self._remove_extension_button)
        extensions_buttons.addStretch()
        extensions_layout.addWidget(self._supported_extensions_table, 1)
        extensions_layout.addLayout(extensions_buttons)

        self._settings_pages.addWidget(general_page)
        self._settings_pages.addWidget(greenwich_page)
        self._settings_pages.addWidget(custom_paths_page)
        self._settings_pages.addWidget(extensions_page)
        self._settings_sections_list.currentRowChanged.connect(self._on_settings_section_changed)
        self._settings_sections_list.setCurrentRow(0)

        settings_layout = QHBoxLayout()
        settings_layout.addWidget(self._settings_sections_list)
        settings_layout.addWidget(self._settings_pages, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(settings_layout, 1)

    def fade_duration_seconds(self) -> int:
        return self._fade_duration_spinbox.value()

    def font_size_points(self) -> int:
        return self._font_size_spinbox.value()

    def greenwich_time_signal_enabled(self) -> bool:
        return self._greenwich_time_signal_selector.currentText() == "True"

    def greenwich_time_signal_path(self) -> str:
        return self._greenwich_time_signal_path_edit.text().strip()

    def library_tabs(self) -> list[LibraryTab]:
        collected_settings = self._collect_settings_values(show_warning=False)
        if collected_settings is None:
            return list(self._configured_library_tabs)
        configured_tabs, _ = collected_settings
        return configured_tabs

    def supported_extensions(self) -> list[str]:
        collected_settings = self._collect_settings_values(show_warning=False)
        if collected_settings is None:
            return list(self._configured_supported_extensions)
        _, configured_supported_extensions = collected_settings
        return configured_supported_extensions

    @staticmethod
    def _normalize_directory_path(raw_path: str) -> str:
        expanded = Path(raw_path).expanduser()
        try:
            return str(expanded.resolve())
        except OSError:
            return str(expanded)

    def _append_library_tab_row(self, title: str = "", path: str = "") -> None:
        row = self._library_tabs_table.rowCount()
        self._library_tabs_table.insertRow(row)
        self._library_tabs_table.setItem(row, 0, QTableWidgetItem(title))
        self._library_tabs_table.setItem(row, 1, QTableWidgetItem(path))
        self._library_tabs_table.resizeColumnsToContents()

    @staticmethod
    def _normalize_extension_token(raw_extension: str) -> str:
        token = raw_extension.strip().lower().lstrip(".")
        if not token:
            return ""
        if not all(char.isalnum() for char in token):
            return ""
        return token

    def _append_extension_row(self, extension: str = "") -> None:
        row = self._supported_extensions_table.rowCount()
        self._supported_extensions_table.insertRow(row)
        self._supported_extensions_table.setItem(row, 0, QTableWidgetItem(extension))

    def _add_library_tab_row(self) -> None:
        self._append_library_tab_row()
        new_row = self._library_tabs_table.rowCount() - 1
        self._library_tabs_table.setCurrentCell(new_row, 0)
        self._library_tabs_table.editItem(self._library_tabs_table.item(new_row, 0))

    def _add_extension_row(self) -> None:
        self._append_extension_row()
        new_row = self._supported_extensions_table.rowCount() - 1
        self._supported_extensions_table.setCurrentCell(new_row, 0)
        self._supported_extensions_table.editItem(self._supported_extensions_table.item(new_row, 0))

    def _remove_selected_library_tab_row(self) -> None:
        current_row = self._library_tabs_table.currentRow()
        if current_row < 0:
            return
        title_item = self._library_tabs_table.item(current_row, 0)
        path_item = self._library_tabs_table.item(current_row, 1)
        title = title_item.text().strip() if title_item is not None else ""
        path = path_item.text().strip() if path_item is not None else ""
        details = title or path or f"row {current_row + 1}"
        result = QMessageBox.question(
            self,
            "Confirm Removal",
            f"Remove custom path '{details}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if result != QMessageBox.Yes:
            return
        self._library_tabs_table.removeRow(current_row)

    def _remove_selected_extension_row(self) -> None:
        current_row = self._supported_extensions_table.currentRow()
        if current_row < 0:
            return
        extension_item = self._supported_extensions_table.item(current_row, 0)
        extension = extension_item.text().strip() if extension_item is not None else ""
        details = extension or f"row {current_row + 1}"
        result = QMessageBox.question(
            self,
            "Confirm Removal",
            f"Remove extension '{details}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if result != QMessageBox.Yes:
            return
        self._supported_extensions_table.removeRow(current_row)

    def _browse_selected_library_tab_path(self) -> None:
        current_row = self._library_tabs_table.currentRow()
        if current_row < 0:
            QMessageBox.information(self, "No Selection", "Select a tab row first.")
            return
        current_path_item = self._library_tabs_table.item(current_row, 1)
        current_path = current_path_item.text().strip() if current_path_item is not None else ""
        base_dir = self._normalize_directory_path(current_path) if current_path else str(Path.home())
        selected_dir = QFileDialog.getExistingDirectory(self, "Choose Library Tab Path", base_dir)
        if not selected_dir:
            return
        normalized_path = self._normalize_directory_path(selected_dir)
        if current_path_item is None:
            self._library_tabs_table.setItem(current_row, 1, QTableWidgetItem(normalized_path))
        else:
            current_path_item.setText(normalized_path)
        self._library_tabs_table.resizeColumnsToContents()

    def _browse_greenwich_time_signal_path(self) -> None:
        current_path = self._greenwich_time_signal_path_edit.text().strip()
        base_dir = str(Path(current_path).expanduser().parent) if current_path else str(Path.home())
        selected_file, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Greenwich Time Signal Audio",
            base_dir,
            "Audio Files (*.wav *.mp3 *.ogg *.opus *.m4a *.aac *.flac *.webm *.mp4);;All Files (*)",
        )
        if not selected_file:
            return
        self._greenwich_time_signal_path_edit.setText(str(Path(selected_file).expanduser()))

    def _collect_library_tabs(self, *, show_warning: bool) -> list[LibraryTab] | None:
        configured_tabs: list[LibraryTab] = []
        for row in range(self._library_tabs_table.rowCount()):
            title_item = self._library_tabs_table.item(row, 0)
            path_item = self._library_tabs_table.item(row, 1)
            title = title_item.text().strip() if title_item is not None else ""
            path = path_item.text().strip() if path_item is not None else ""
            if not title and not path:
                continue
            if not title or not path:
                if show_warning:
                    QMessageBox.warning(
                        self,
                        "Invalid Tab Configuration",
                        f"Row {row + 1}: both Title and Path are required.",
                    )
                return
            normalized_path = self._normalize_directory_path(path)
            if not Path(normalized_path).is_dir():
                if show_warning:
                    QMessageBox.warning(
                        self,
                        "Invalid Tab Path",
                        f"Row {row + 1}: path does not exist or is not a directory:\n{normalized_path}",
                    )
                return
            configured_tabs.append(LibraryTab(title=title, path=normalized_path))
        return configured_tabs

    def _collect_supported_extensions(self, *, show_warning: bool) -> list[str] | None:
        configured_supported_extensions: list[str] = []
        seen_extensions: set[str] = set()
        for row in range(self._supported_extensions_table.rowCount()):
            extension_item = self._supported_extensions_table.item(row, 0)
            raw_extension = extension_item.text() if extension_item is not None else ""
            extension = self._normalize_extension_token(raw_extension)
            if not raw_extension.strip():
                continue
            if not extension:
                if show_warning:
                    QMessageBox.warning(
                        self,
                        "Invalid Extension",
                        (
                            f"Row {row + 1}: extension '{raw_extension}' is invalid. "
                            "Use only letters and numbers."
                        ),
                    )
                return None
            if extension in seen_extensions:
                continue
            seen_extensions.add(extension)
            configured_supported_extensions.append(extension)
        if not configured_supported_extensions:
            if show_warning:
                QMessageBox.warning(
                    self,
                    "Invalid Extensions",
                    "Add at least one extension.",
                )
            return None
        return configured_supported_extensions

    def _collect_settings_values(self, *, show_warning: bool) -> tuple[list[LibraryTab], list[str]] | None:
        configured_tabs = self._collect_library_tabs(show_warning=show_warning)
        if configured_tabs is None:
            return None
        configured_supported_extensions = self._collect_supported_extensions(show_warning=show_warning)
        if configured_supported_extensions is None:
            return None
        return configured_tabs, configured_supported_extensions

    def _on_settings_section_changed(self, index: int) -> None:
        if 0 <= index < self._settings_pages.count():
            self._settings_pages.setCurrentIndex(index)

    def reject(self) -> None:
        collected_settings = self._collect_settings_values(show_warning=True)
        if collected_settings is None:
            return
        configured_tabs, configured_supported_extensions = collected_settings
        self._configured_library_tabs = configured_tabs
        self._configured_supported_extensions = configured_supported_extensions
        super().accept()

    def closeEvent(self, event) -> None:
        collected_settings = self._collect_settings_values(show_warning=True)
        if collected_settings is None:
            event.ignore()
            return
        configured_tabs, configured_supported_extensions = collected_settings
        self._configured_library_tabs = configured_tabs
        self._configured_supported_extensions = configured_supported_extensions
        self.setResult(QDialog.Accepted)
        event.accept()

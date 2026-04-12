from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        dialog_title: str = "Add CRON Entry",
        initial_expression: str = "",
        initial_hard_sync: bool = True,
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
        self._hard_sync_checkbox: QCheckBox | None = None
        self._fade_in_checkbox: QCheckBox | None = None
        self._fade_out_checkbox: QCheckBox | None = None
        if not self._expression_only:
            self._hard_sync_checkbox = QCheckBox("Hard sync (interrupt current playback)", self)
            self._hard_sync_checkbox.setChecked(bool(initial_hard_sync))
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
        if self._hard_sync_checkbox is not None:
            layout.addWidget(self._hard_sync_checkbox)
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

    def hard_sync(self) -> bool:
        return self._hard_sync_checkbox.isChecked() if self._hard_sync_checkbox is not None else False

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
        library_tabs: list[LibraryTab],
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
        self._configured_library_tabs: list[LibraryTab] = list(library_tabs)

        self._settings_sections_list = QListWidget(self)
        self._settings_sections_list.addItem("General Settings")
        self._settings_sections_list.addItem("Custom Paths")
        self._settings_sections_list.setFixedWidth(190)

        self._settings_pages = QStackedWidget(self)

        general_page = QWidget(self)
        general_layout = QVBoxLayout(general_page)
        self._properties_table = QTableWidget(self)
        self._properties_table.setColumnCount(2)
        self._properties_table.setRowCount(1)
        self._properties_table.setHorizontalHeaderLabels(["Property", "Value"])
        self._properties_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._properties_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._properties_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._properties_table.setAlternatingRowColors(True)
        self._properties_table.verticalHeader().setVisible(False)
        self._properties_table.horizontalHeader().setStretchLastSection(True)

        fade_duration_item = QTableWidgetItem("Fade In / Fade Out in seconds")
        fade_duration_item.setFlags(fade_duration_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

        self._properties_table.setItem(0, 0, fade_duration_item)
        self._properties_table.setCellWidget(0, 1, self._fade_duration_spinbox)
        self._properties_table.resizeColumnsToContents()
        general_layout.addWidget(self._properties_table)
        general_layout.addStretch()

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

        self._settings_pages.addWidget(general_page)
        self._settings_pages.addWidget(custom_paths_page)
        self._settings_sections_list.currentRowChanged.connect(self._on_settings_section_changed)
        self._settings_sections_list.setCurrentRow(0)

        settings_layout = QHBoxLayout()
        settings_layout.addWidget(self._settings_sections_list)
        settings_layout.addWidget(self._settings_pages, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(settings_layout, 1)

    def fade_duration_seconds(self) -> int:
        return self._fade_duration_spinbox.value()

    def library_tabs(self) -> list[LibraryTab]:
        configured_tabs = self._collect_library_tabs(show_warning=False)
        if configured_tabs is None:
            return list(self._configured_library_tabs)
        return configured_tabs

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

    def _add_library_tab_row(self) -> None:
        self._append_library_tab_row()
        new_row = self._library_tabs_table.rowCount() - 1
        self._library_tabs_table.setCurrentCell(new_row, 0)
        self._library_tabs_table.editItem(self._library_tabs_table.item(new_row, 0))

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

    def _on_settings_section_changed(self, index: int) -> None:
        if 0 <= index < self._settings_pages.count():
            self._settings_pages.setCurrentIndex(index)

    def reject(self) -> None:
        configured_tabs = self._collect_library_tabs(show_warning=True)
        if configured_tabs is None:
            return
        self._configured_library_tabs = configured_tabs
        super().accept()

    def closeEvent(self, event) -> None:
        configured_tabs = self._collect_library_tabs(show_warning=True)
        if configured_tabs is None:
            event.ignore()
            return
        self._configured_library_tabs = configured_tabs
        self.setResult(QDialog.Accepted)
        event.accept()

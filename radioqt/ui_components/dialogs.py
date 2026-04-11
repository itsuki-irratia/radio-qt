from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTextBrowser,
    QVBoxLayout,
    QDateTimeEdit,
    QWidget,
)

from ..cron import CronExpression, CronParseError


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
        self._fade_in_checkbox = QCheckBox("Fade in", self)
        self._fade_out_checkbox = QCheckBox("Fade out", self)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("CRON expression (with seconds):"))
        layout.addWidget(self._expression_edit)
        layout.addWidget(QLabel("Example: 0 */15 * * * *"))
        layout.addWidget(QLabel("Use numeric values only. Month: 1-12. Weekday starts on Monday: 1-7."))
        layout.addWidget(self._hard_sync_checkbox)
        layout.addWidget(self._fade_in_checkbox)
        layout.addWidget(self._fade_out_checkbox)
        layout.addWidget(buttons)

    def expression(self) -> str:
        return self._expression_edit.text().strip()

    def hard_sync(self) -> bool:
        return self._hard_sync_checkbox.isChecked()

    def fade_in(self) -> bool:
        return self._fade_in_checkbox.isChecked()

    def fade_out(self) -> bool:
        return self._fade_out_checkbox.isChecked()

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

        help_html = """
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

        text = QTextBrowser(self)
        text.setReadOnly(True)
        text.setOpenExternalLinks(False)
        text.setHtml(help_html)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addWidget(buttons)

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
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QDateTimeEdit,
    QWidget,
)

from ..cron import CronExpression, CronParseError
from ..app_config import ExportPathMapping
from ..models import LibraryTab
from ..stream_relay import (
    DEFAULT_ICECAST_AUDIO_BITRATE,
    DEFAULT_ICECAST_AUDIO_CHANNELS,
    DEFAULT_ICECAST_AUDIO_CODEC,
    DEFAULT_ICECAST_AUDIO_RATE,
    DEFAULT_ICECAST_CONTENT_TYPE,
    DEFAULT_ICECAST_DEVICE,
    DEFAULT_ICECAST_INPUT_FORMAT,
    DEFAULT_ICECAST_OUTPUT_FORMAT,
    DEFAULT_ICECAST_THREAD_QUEUE_SIZE,
    DEFAULT_ICECAST_URL,
    list_pulse_source_devices,
)
from .boolean_selectors import _configure_boolean_selector


def _make_readonly_label_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


def _configure_settings_table(table: QTableWidget, *, row_count: int) -> None:
    table.setColumnCount(2)
    table.setRowCount(row_count)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(True)
    table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)


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
        filesystem_default_fade_in: bool,
        filesystem_default_fade_out: bool,
        streams_default_fade_in: bool,
        streams_default_fade_out: bool,
        media_library_width_percent: int,
        schedule_width_percent: int,
        font_size_points: int,
        greenwich_time_signal_enabled: bool,
        greenwich_time_signal_path: str,
        icecast_status: bool,
        icecast_run_in_background: bool,
        icecast_command: str,
        icecast_input_format: str,
        icecast_thread_queue_size: int,
        icecast_device: str,
        icecast_audio_channels: int,
        icecast_audio_rate: int,
        icecast_audio_codec: str,
        icecast_audio_bitrate: int,
        icecast_content_type: str,
        icecast_output_format: str,
        icecast_url: str,
        library_tabs: list[LibraryTab],
        export_path_mappings: list[ExportPathMapping],
        supported_extensions: list[str],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(760, 520)

        shared_fade_duration_seconds = max(
            1,
            max(int(fade_in_duration_seconds), int(fade_out_duration_seconds)),
        )
        self._fade_duration_spinbox = QSpinBox(self)
        self._fade_duration_spinbox.setRange(1, 120)
        self._fade_duration_spinbox.setValue(shared_fade_duration_seconds)
        self._filesystem_default_fade_in_selector = QComboBox(self)
        self._filesystem_default_fade_in_selector.addItems(["True", "False"])
        self._filesystem_default_fade_in_selector.setCurrentText(
            "True" if filesystem_default_fade_in else "False"
        )
        _configure_boolean_selector(self._filesystem_default_fade_in_selector)
        self._filesystem_default_fade_out_selector = QComboBox(self)
        self._filesystem_default_fade_out_selector.addItems(["True", "False"])
        self._filesystem_default_fade_out_selector.setCurrentText(
            "True" if filesystem_default_fade_out else "False"
        )
        _configure_boolean_selector(self._filesystem_default_fade_out_selector)
        self._streams_default_fade_in_selector = QComboBox(self)
        self._streams_default_fade_in_selector.addItems(["True", "False"])
        self._streams_default_fade_in_selector.setCurrentText(
            "True" if streams_default_fade_in else "False"
        )
        _configure_boolean_selector(self._streams_default_fade_in_selector)
        self._streams_default_fade_out_selector = QComboBox(self)
        self._streams_default_fade_out_selector.addItems(["True", "False"])
        self._streams_default_fade_out_selector.setCurrentText(
            "True" if streams_default_fade_out else "False"
        )
        _configure_boolean_selector(self._streams_default_fade_out_selector)
        self._font_size_spinbox = QSpinBox(self)
        self._font_size_spinbox.setRange(6, 72)
        self._font_size_spinbox.setValue(max(6, int(font_size_points)))
        normalized_media_library_width_percent = max(10, min(90, int(media_library_width_percent)))
        normalized_schedule_width_percent = max(10, min(90, int(schedule_width_percent)))
        if normalized_media_library_width_percent + normalized_schedule_width_percent != 100:
            normalized_schedule_width_percent = 100 - normalized_media_library_width_percent
        self._media_library_width_spinbox = QSpinBox(self)
        self._media_library_width_spinbox.setRange(10, 90)
        self._media_library_width_spinbox.setValue(normalized_media_library_width_percent)
        self._schedule_width_spinbox = QSpinBox(self)
        self._schedule_width_spinbox.setRange(10, 90)
        self._schedule_width_spinbox.setValue(normalized_schedule_width_percent)
        self._media_library_width_spinbox.valueChanged.connect(self._on_media_library_width_changed)
        self._schedule_width_spinbox.valueChanged.connect(self._on_schedule_width_changed)
        self._panel_widths_widget = QWidget(self)
        self._panel_widths_layout = QHBoxLayout(self._panel_widths_widget)
        self._panel_widths_layout.setContentsMargins(0, 0, 0, 0)
        self._panel_widths_layout.setSpacing(6)
        self._panel_widths_layout.addWidget(QLabel("Media Library", self._panel_widths_widget))
        self._panel_widths_layout.addWidget(self._media_library_width_spinbox)
        self._panel_widths_layout.addWidget(QLabel("Schedule", self._panel_widths_widget))
        self._panel_widths_layout.addWidget(self._schedule_width_spinbox)
        self._panel_widths_layout.addStretch()
        self._greenwich_time_signal_selector = QComboBox(self)
        self._greenwich_time_signal_selector.addItems(["True", "False"])
        self._greenwich_time_signal_selector.setCurrentText(
            "True" if greenwich_time_signal_enabled else "False"
        )
        _configure_boolean_selector(self._greenwich_time_signal_selector)
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
        self._icecast_status_selector = QComboBox(self)
        self._icecast_status_selector.addItems(["True", "False"])
        self._icecast_status_selector.setCurrentText(
            "True" if icecast_status else "False"
        )
        _configure_boolean_selector(self._icecast_status_selector)
        self._icecast_run_in_background_selector = QComboBox(self)
        self._icecast_run_in_background_selector.addItems(["True", "False"])
        self._icecast_run_in_background_selector.setCurrentText(
            "True" if icecast_run_in_background else "False"
        )
        _configure_boolean_selector(self._icecast_run_in_background_selector)
        self._icecast_command_edit = QLineEdit(self)
        self._icecast_command_edit.setPlaceholderText(
            "Auto-generated. Append extra ffmpeg args at the end (kept when params change)."
        )
        self._icecast_command_edit.setText(icecast_command.strip())
        self._icecast_input_format_edit = QLineEdit(self)
        self._icecast_input_format_edit.setPlaceholderText("pulse")
        self._icecast_input_format_edit.setText(
            icecast_input_format.strip() or DEFAULT_ICECAST_INPUT_FORMAT
        )
        self._icecast_thread_queue_size_spinbox = QSpinBox(self)
        self._icecast_thread_queue_size_spinbox.setRange(1, 2000000)
        self._icecast_thread_queue_size_spinbox.setValue(
            max(1, int(icecast_thread_queue_size or DEFAULT_ICECAST_THREAD_QUEUE_SIZE))
        )
        self._icecast_device_selector = QComboBox(self)
        self._icecast_device_selector.setEditable(True)
        self._icecast_device_selector.setToolTip(
            "Pulse monitor source. Bluetooth outputs are shown as ...a2dp-sink.monitor."
        )
        self._icecast_device_refresh_button = QPushButton("Refresh", self)
        self._icecast_device_refresh_button.clicked.connect(
            lambda _checked=False: self._reload_icecast_device_options()
        )
        self._icecast_device_widget = QWidget(self)
        self._icecast_device_layout = QHBoxLayout(self._icecast_device_widget)
        self._icecast_device_layout.setContentsMargins(0, 0, 0, 0)
        self._icecast_device_layout.setSpacing(6)
        self._icecast_device_layout.addWidget(self._icecast_device_selector, 1)
        self._icecast_device_layout.addWidget(self._icecast_device_refresh_button)
        selected_device = icecast_device.strip() or DEFAULT_ICECAST_DEVICE
        self._reload_icecast_device_options(preferred_device=selected_device)
        self._icecast_audio_channels_spinbox = QSpinBox(self)
        self._icecast_audio_channels_spinbox.setRange(1, 8)
        self._icecast_audio_channels_spinbox.setValue(
            max(1, int(icecast_audio_channels or DEFAULT_ICECAST_AUDIO_CHANNELS))
        )
        self._icecast_audio_rate_spinbox = QSpinBox(self)
        self._icecast_audio_rate_spinbox.setRange(8000, 384000)
        self._icecast_audio_rate_spinbox.setSingleStep(1000)
        self._icecast_audio_rate_spinbox.setValue(
            max(1, int(icecast_audio_rate or DEFAULT_ICECAST_AUDIO_RATE))
        )
        self._icecast_audio_codec_edit = QLineEdit(self)
        self._icecast_audio_codec_edit.setPlaceholderText("libmp3lame")
        self._icecast_audio_codec_edit.setText(
            icecast_audio_codec.strip() or DEFAULT_ICECAST_AUDIO_CODEC
        )
        self._icecast_audio_bitrate_spinbox = QSpinBox(self)
        self._icecast_audio_bitrate_spinbox.setRange(8, 10000)
        self._icecast_audio_bitrate_spinbox.setValue(
            max(1, int(icecast_audio_bitrate or DEFAULT_ICECAST_AUDIO_BITRATE))
        )
        self._icecast_content_type_edit = QLineEdit(self)
        self._icecast_content_type_edit.setPlaceholderText("audio/mpeg")
        self._icecast_content_type_edit.setText(
            icecast_content_type.strip() or DEFAULT_ICECAST_CONTENT_TYPE
        )
        self._icecast_output_format_edit = QLineEdit(self)
        self._icecast_output_format_edit.setPlaceholderText("mp3")
        self._icecast_output_format_edit.setText(
            icecast_output_format.strip() or DEFAULT_ICECAST_OUTPUT_FORMAT
        )
        self._icecast_url_edit = QLineEdit(self)
        self._icecast_url_edit.setPlaceholderText(
            "icecast://source:pass@localhost:8000/radio.mp3"
        )
        self._icecast_url_edit.setText(icecast_url.strip() or DEFAULT_ICECAST_URL)
        self._configured_library_tabs: list[LibraryTab] = list(library_tabs)
        self._configured_export_path_mappings: list[ExportPathMapping] = list(
            export_path_mappings
        )
        self._configured_supported_extensions: list[str] = list(supported_extensions)

        self._settings_sections_list = QListWidget(self)
        self._settings_sections_list.addItem("Custom Paths")
        self._settings_sections_list.addItem("Export Paths")
        self._settings_sections_list.addItem("Extensions")
        self._settings_sections_list.addItem("Fade")
        self._settings_sections_list.addItem("Greenwich Time Signal")
        self._settings_sections_list.addItem("Icecast")
        self._settings_sections_list.addItem("View")
        self._settings_sections_list.setFixedWidth(190)
        self._settings_sections_list.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        self._settings_pages = QStackedWidget(self)
        self._settings_pages.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        general_page = QWidget(self)
        general_layout = QVBoxLayout(general_page)
        general_layout.setContentsMargins(0, 0, 0, 0)
        self._properties_table = QTableWidget(self)
        _configure_settings_table(self._properties_table, row_count=2)

        self._properties_table.setItem(0, 0, _make_readonly_label_item("Font size (pt)"))
        self._properties_table.setCellWidget(0, 1, self._font_size_spinbox)
        self._properties_table.setItem(1, 0, _make_readonly_label_item("Panel Widths (%)"))
        self._properties_table.setCellWidget(1, 1, self._panel_widths_widget)
        self._properties_table.resizeColumnsToContents()
        general_layout.addWidget(self._properties_table, 1)

        fade_page = QWidget(self)
        fade_layout = QVBoxLayout(fade_page)
        fade_layout.setContentsMargins(0, 0, 0, 0)
        self._fade_table = QTableWidget(self)
        _configure_settings_table(self._fade_table, row_count=5)

        self._fade_table.setItem(0, 0, _make_readonly_label_item("In / Out Seconds"))
        self._fade_table.setCellWidget(0, 1, self._fade_duration_spinbox)
        self._fade_table.setItem(1, 0, _make_readonly_label_item("Filesystem → Default Fade In"))
        self._fade_table.setCellWidget(1, 1, self._filesystem_default_fade_in_selector)
        self._fade_table.setItem(2, 0, _make_readonly_label_item("Filesystem → Default Fade Out"))
        self._fade_table.setCellWidget(2, 1, self._filesystem_default_fade_out_selector)
        self._fade_table.setItem(3, 0, _make_readonly_label_item("Streams → Default Fade In"))
        self._fade_table.setCellWidget(3, 1, self._streams_default_fade_in_selector)
        self._fade_table.setItem(4, 0, _make_readonly_label_item("Streams → Default Fade Out"))
        self._fade_table.setCellWidget(4, 1, self._streams_default_fade_out_selector)
        self._fade_table.resizeColumnsToContents()
        fade_layout.addWidget(self._fade_table, 1)

        greenwich_page = QWidget(self)
        greenwich_layout = QVBoxLayout(greenwich_page)
        greenwich_layout.setContentsMargins(0, 0, 0, 0)
        self._greenwich_table = QTableWidget(self)
        _configure_settings_table(self._greenwich_table, row_count=2)

        self._greenwich_table.setItem(0, 0, _make_readonly_label_item("Enabled"))
        self._greenwich_table.setCellWidget(0, 1, self._greenwich_time_signal_selector)
        self._greenwich_table.setItem(1, 0, _make_readonly_label_item("Audio Path"))
        self._greenwich_table.setCellWidget(1, 1, self._greenwich_time_signal_path_widget)
        self._greenwich_table.resizeColumnsToContents()
        greenwich_layout.addWidget(self._greenwich_table, 1)

        icecast_page = QWidget(self)
        icecast_layout = QVBoxLayout(icecast_page)
        icecast_layout.setContentsMargins(0, 0, 0, 0)
        self._icecast_table = QTableWidget(self)
        _configure_settings_table(self._icecast_table, row_count=13)
        self._icecast_table.setItem(0, 0, _make_readonly_label_item("Status"))
        self._icecast_table.setCellWidget(0, 1, self._icecast_status_selector)
        self._icecast_table.setItem(1, 0, _make_readonly_label_item("Run In Background"))
        self._icecast_table.setCellWidget(1, 1, self._icecast_run_in_background_selector)
        self._icecast_table.setItem(2, 0, _make_readonly_label_item("Input Format"))
        self._icecast_table.setCellWidget(2, 1, self._icecast_input_format_edit)
        self._icecast_table.setItem(3, 0, _make_readonly_label_item("Thread Queue Size"))
        self._icecast_table.setCellWidget(3, 1, self._icecast_thread_queue_size_spinbox)
        self._icecast_table.setItem(4, 0, _make_readonly_label_item("Device (Pulse Source)"))
        self._icecast_table.setCellWidget(4, 1, self._icecast_device_widget)
        self._icecast_table.setItem(5, 0, _make_readonly_label_item("Audio Channels"))
        self._icecast_table.setCellWidget(5, 1, self._icecast_audio_channels_spinbox)
        self._icecast_table.setItem(6, 0, _make_readonly_label_item("Audio Rate"))
        self._icecast_table.setCellWidget(6, 1, self._icecast_audio_rate_spinbox)
        self._icecast_table.setItem(7, 0, _make_readonly_label_item("Audio Codec"))
        self._icecast_table.setCellWidget(7, 1, self._icecast_audio_codec_edit)
        self._icecast_table.setItem(8, 0, _make_readonly_label_item("Audio Bitrate (kbps)"))
        self._icecast_table.setCellWidget(8, 1, self._icecast_audio_bitrate_spinbox)
        self._icecast_table.setItem(9, 0, _make_readonly_label_item("Content Type"))
        self._icecast_table.setCellWidget(9, 1, self._icecast_content_type_edit)
        self._icecast_table.setItem(10, 0, _make_readonly_label_item("Output Format"))
        self._icecast_table.setCellWidget(10, 1, self._icecast_output_format_edit)
        self._icecast_table.setItem(11, 0, _make_readonly_label_item("Icecast URL"))
        self._icecast_table.setCellWidget(11, 1, self._icecast_url_edit)
        self._icecast_table.setItem(12, 0, _make_readonly_label_item("FFmpeg Command (Override)"))
        self._icecast_table.setCellWidget(12, 1, self._icecast_command_edit)
        self._icecast_table.resizeColumnsToContents()
        icecast_layout.addWidget(self._icecast_table, 1)

        custom_paths_page = QWidget(self)
        custom_paths_layout = QVBoxLayout(custom_paths_page)
        custom_paths_layout.setContentsMargins(0, 0, 0, 0)
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
        square_button_size = 30
        self._add_library_tab_button = QPushButton("+", custom_paths_page)
        self._add_library_tab_button.setToolTip("Add Tab")
        self._add_library_tab_button.setFixedSize(square_button_size, square_button_size)
        self._remove_library_tab_button = QPushButton("-", custom_paths_page)
        self._remove_library_tab_button.setToolTip("Remove selected tab")
        self._remove_library_tab_button.setFixedSize(square_button_size, square_button_size)
        self._add_library_tab_button.clicked.connect(self._add_library_tab_row)
        self._remove_library_tab_button.clicked.connect(self._remove_selected_library_tab_row)
        library_tabs_buttons.addWidget(self._add_library_tab_button)
        library_tabs_buttons.addWidget(self._remove_library_tab_button)
        library_tabs_buttons.addStretch()
        custom_paths_layout.addWidget(self._library_tabs_table, 1)
        custom_paths_layout.addLayout(library_tabs_buttons)

        export_paths_page = QWidget(self)
        export_paths_layout = QVBoxLayout(export_paths_page)
        export_paths_layout.setContentsMargins(0, 0, 0, 0)
        self._export_path_mappings_table = QTableWidget(export_paths_page)
        self._export_path_mappings_table.setColumnCount(2)
        self._export_path_mappings_table.setHorizontalHeaderLabels(["From Prefix", "To Prefix"])
        self._export_path_mappings_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._export_path_mappings_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._export_path_mappings_table.setAlternatingRowColors(True)
        self._export_path_mappings_table.verticalHeader().setVisible(False)
        self._export_path_mappings_table.horizontalHeader().setStretchLastSection(True)
        self._export_path_mappings_table.setRowCount(0)
        for mapping in self._configured_export_path_mappings:
            self._append_export_path_mapping_row(mapping.from_prefix, mapping.to_prefix)

        export_paths_buttons = QHBoxLayout()
        self._add_export_path_mapping_button = QPushButton("+", export_paths_page)
        self._add_export_path_mapping_button.setToolTip("Add export path mapping")
        self._add_export_path_mapping_button.setFixedSize(square_button_size, square_button_size)
        self._remove_export_path_mapping_button = QPushButton("-", export_paths_page)
        self._remove_export_path_mapping_button.setToolTip("Remove selected export path mapping")
        self._remove_export_path_mapping_button.setFixedSize(square_button_size, square_button_size)
        self._add_export_path_mapping_button.clicked.connect(self._add_export_path_mapping_row)
        self._remove_export_path_mapping_button.clicked.connect(
            self._remove_selected_export_path_mapping_row
        )
        export_paths_buttons.addWidget(self._add_export_path_mapping_button)
        export_paths_buttons.addWidget(self._remove_export_path_mapping_button)
        export_paths_buttons.addStretch()
        export_paths_layout.addWidget(self._export_path_mappings_table, 1)
        export_paths_layout.addLayout(export_paths_buttons)

        extensions_page = QWidget(self)
        extensions_layout = QVBoxLayout(extensions_page)
        extensions_layout.setContentsMargins(0, 0, 0, 0)
        self._supported_extensions_table = QTableWidget(extensions_page)
        self._supported_extensions_table.setColumnCount(1)
        self._supported_extensions_table.setHorizontalHeaderLabels(["Extensions"])
        self._supported_extensions_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._supported_extensions_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._supported_extensions_table.setAlternatingRowColors(True)
        self._supported_extensions_table.verticalHeader().setVisible(False)
        self._supported_extensions_table.horizontalHeader().setStretchLastSection(True)
        self._supported_extensions_table.setRowCount(0)
        for extension in self._configured_supported_extensions:
            self._append_extension_row(extension)

        extensions_buttons = QHBoxLayout()
        self._add_extension_button = QPushButton("+", extensions_page)
        self._add_extension_button.setToolTip("Add Extension")
        self._add_extension_button.setFixedSize(square_button_size, square_button_size)
        self._remove_extension_button = QPushButton("-", extensions_page)
        self._remove_extension_button.setToolTip("Remove selected extension")
        self._remove_extension_button.setFixedSize(square_button_size, square_button_size)
        self._add_extension_button.clicked.connect(self._add_extension_row)
        self._remove_extension_button.clicked.connect(self._remove_selected_extension_row)
        extensions_buttons.addWidget(self._add_extension_button)
        extensions_buttons.addWidget(self._remove_extension_button)
        extensions_buttons.addStretch()
        extensions_layout.addWidget(self._supported_extensions_table, 1)
        extensions_layout.addLayout(extensions_buttons)

        self._settings_pages.addWidget(custom_paths_page)
        self._settings_pages.addWidget(export_paths_page)
        self._settings_pages.addWidget(extensions_page)
        self._settings_pages.addWidget(fade_page)
        self._settings_pages.addWidget(greenwich_page)
        self._settings_pages.addWidget(icecast_page)
        self._settings_pages.addWidget(general_page)
        self._settings_sections_list.currentRowChanged.connect(self._on_settings_section_changed)
        self._settings_sections_list.setCurrentRow(0)

        settings_layout = QHBoxLayout()
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(8)
        settings_layout.addWidget(self._settings_sections_list)
        settings_layout.addWidget(self._settings_pages, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(settings_layout, 1)

    def fade_duration_seconds(self) -> int:
        return self._fade_duration_spinbox.value()

    def fade_in_duration_seconds(self) -> int:
        return self._fade_duration_spinbox.value()

    def fade_out_duration_seconds(self) -> int:
        return self._fade_duration_spinbox.value()

    def filesystem_default_fade_in(self) -> bool:
        return self._filesystem_default_fade_in_selector.currentText() == "True"

    def filesystem_default_fade_out(self) -> bool:
        return self._filesystem_default_fade_out_selector.currentText() == "True"

    def streams_default_fade_in(self) -> bool:
        return self._streams_default_fade_in_selector.currentText() == "True"

    def streams_default_fade_out(self) -> bool:
        return self._streams_default_fade_out_selector.currentText() == "True"

    def font_size_points(self) -> int:
        return self._font_size_spinbox.value()

    def media_library_width_percent(self) -> int:
        return self._media_library_width_spinbox.value()

    def schedule_width_percent(self) -> int:
        return self._schedule_width_spinbox.value()

    def greenwich_time_signal_enabled(self) -> bool:
        return self._greenwich_time_signal_selector.currentText() == "True"

    def greenwich_time_signal_path(self) -> str:
        return self._greenwich_time_signal_path_edit.text().strip()

    def icecast_status(self) -> bool:
        return self._icecast_status_selector.currentText() == "True"

    def icecast_run_in_background(self) -> bool:
        return self._icecast_run_in_background_selector.currentText() == "True"

    def icecast_command(self) -> str:
        return self._icecast_command_edit.text().strip()

    def icecast_input_format(self) -> str:
        return self._icecast_input_format_edit.text().strip() or DEFAULT_ICECAST_INPUT_FORMAT

    def icecast_thread_queue_size(self) -> int:
        return max(1, int(self._icecast_thread_queue_size_spinbox.value()))

    def icecast_device(self) -> str:
        return self._icecast_device_selector.currentText().strip() or DEFAULT_ICECAST_DEVICE

    def _reload_icecast_device_options(self, *, preferred_device: str | None = None) -> None:
        current_device = (
            preferred_device
            if preferred_device is not None
            else self._icecast_device_selector.currentText().strip()
        )
        if not current_device:
            current_device = DEFAULT_ICECAST_DEVICE
        available_devices = list_pulse_source_devices(monitors_only=True)
        if current_device not in available_devices:
            available_devices.insert(0, current_device)
        elif available_devices and available_devices[0] != current_device:
            available_devices.remove(current_device)
            available_devices.insert(0, current_device)
        if not available_devices:
            available_devices = [current_device]
        self._icecast_device_selector.blockSignals(True)
        self._icecast_device_selector.clear()
        self._icecast_device_selector.addItems(available_devices)
        self._icecast_device_selector.setCurrentText(current_device)
        self._icecast_device_selector.blockSignals(False)

    def icecast_audio_channels(self) -> int:
        return max(1, int(self._icecast_audio_channels_spinbox.value()))

    def icecast_audio_rate(self) -> int:
        return max(1, int(self._icecast_audio_rate_spinbox.value()))

    def icecast_audio_codec(self) -> str:
        return self._icecast_audio_codec_edit.text().strip() or DEFAULT_ICECAST_AUDIO_CODEC

    def icecast_audio_bitrate(self) -> int:
        return max(1, int(self._icecast_audio_bitrate_spinbox.value()))

    def icecast_content_type(self) -> str:
        return self._icecast_content_type_edit.text().strip() or DEFAULT_ICECAST_CONTENT_TYPE

    def icecast_output_format(self) -> str:
        return self._icecast_output_format_edit.text().strip() or DEFAULT_ICECAST_OUTPUT_FORMAT

    def icecast_url(self) -> str:
        return self._icecast_url_edit.text().strip() or DEFAULT_ICECAST_URL

    def library_tabs(self) -> list[LibraryTab]:
        collected_settings = self._collect_settings_values(show_warning=False)
        if collected_settings is None:
            return list(self._configured_library_tabs)
        configured_tabs, _, _ = collected_settings
        return configured_tabs

    def export_path_mappings(self) -> list[ExportPathMapping]:
        collected_settings = self._collect_settings_values(show_warning=False)
        if collected_settings is None:
            return list(self._configured_export_path_mappings)
        _, configured_export_path_mappings, _ = collected_settings
        return configured_export_path_mappings

    def supported_extensions(self) -> list[str]:
        collected_settings = self._collect_settings_values(show_warning=False)
        if collected_settings is None:
            return list(self._configured_supported_extensions)
        _, _, configured_supported_extensions = collected_settings
        return configured_supported_extensions

    @staticmethod
    def _normalize_directory_path(raw_path: str) -> str:
        expanded = Path(raw_path).expanduser()
        try:
            return str(expanded.resolve())
        except OSError:
            return str(expanded)

    @staticmethod
    def _normalize_export_mapping_from_prefix(raw_path: str) -> str:
        trimmed = raw_path.strip()
        if not trimmed:
            return ""
        expanded = Path(trimmed).expanduser()
        expanded_text = str(expanded)
        if expanded_text in {"/", "\\"}:
            return "/"
        return expanded_text.rstrip("/\\")

    @staticmethod
    def _normalize_export_mapping_to_prefix(raw_path: str) -> str:
        trimmed = raw_path.strip()
        if not trimmed:
            return ""
        if trimmed == "/":
            return trimmed
        return trimmed.rstrip("/\\")

    def _append_library_tab_row(self, title: str = "", path: str = "") -> None:
        row = self._library_tabs_table.rowCount()
        self._library_tabs_table.insertRow(row)
        self._library_tabs_table.setItem(row, 0, QTableWidgetItem(title))
        self._library_tabs_table.setCellWidget(row, 1, self._build_library_tab_path_widget(path))
        self._library_tabs_table.resizeColumnsToContents()

    def _build_library_tab_path_widget(self, path: str = "") -> QWidget:
        path_widget = QWidget(self._library_tabs_table)
        path_layout = QHBoxLayout(path_widget)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.setSpacing(6)

        path_edit = QLineEdit(path_widget)
        path_edit.setObjectName("library_tab_path_edit")
        path_edit.setPlaceholderText("Path to directory")
        path_edit.setText(path.strip())

        browse_button = QPushButton("Browse...", path_widget)
        browse_button.clicked.connect(
            lambda: self._browse_library_tab_path_line_edit(path_edit)
        )
        path_layout.addWidget(path_edit, 1)
        path_layout.addWidget(browse_button)
        return path_widget

    @staticmethod
    def _library_tab_path_edit(path_widget: QWidget | None) -> QLineEdit | None:
        if path_widget is None:
            return None
        return path_widget.findChild(QLineEdit, "library_tab_path_edit")

    def _library_tab_path_text(self, row: int) -> str:
        path_widget = self._library_tabs_table.cellWidget(row, 1)
        path_edit = self._library_tab_path_edit(path_widget)
        if path_edit is not None:
            return path_edit.text().strip()
        path_item = self._library_tabs_table.item(row, 1)
        return path_item.text().strip() if path_item is not None else ""

    def _browse_library_tab_path_line_edit(self, path_edit: QLineEdit) -> None:
        current_path = path_edit.text().strip()
        base_dir = self._normalize_directory_path(current_path) if current_path else str(Path.home())
        selected_dir = QFileDialog.getExistingDirectory(self, "Choose Library Tab Path", base_dir)
        if not selected_dir:
            return
        path_edit.setText(self._normalize_directory_path(selected_dir))

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

    def _append_export_path_mapping_row(self, from_prefix: str = "", to_prefix: str = "") -> None:
        row = self._export_path_mappings_table.rowCount()
        self._export_path_mappings_table.insertRow(row)
        self._export_path_mappings_table.setItem(row, 0, QTableWidgetItem(from_prefix))
        self._export_path_mappings_table.setItem(row, 1, QTableWidgetItem(to_prefix))
        self._export_path_mappings_table.resizeColumnsToContents()

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

    def _add_export_path_mapping_row(self) -> None:
        self._append_export_path_mapping_row()
        new_row = self._export_path_mappings_table.rowCount() - 1
        self._export_path_mappings_table.setCurrentCell(new_row, 0)
        self._export_path_mappings_table.editItem(
            self._export_path_mappings_table.item(new_row, 0)
        )

    def _remove_selected_library_tab_row(self) -> None:
        current_row = self._library_tabs_table.currentRow()
        if current_row < 0:
            return
        title_item = self._library_tabs_table.item(current_row, 0)
        title = title_item.text().strip() if title_item is not None else ""
        path = self._library_tab_path_text(current_row)
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

    def _remove_selected_export_path_mapping_row(self) -> None:
        current_row = self._export_path_mappings_table.currentRow()
        if current_row < 0:
            return
        from_item = self._export_path_mappings_table.item(current_row, 0)
        to_item = self._export_path_mappings_table.item(current_row, 1)
        from_prefix = from_item.text().strip() if from_item is not None else ""
        to_prefix = to_item.text().strip() if to_item is not None else ""
        details = f"{from_prefix or '?'} -> {to_prefix or '?'}"
        result = QMessageBox.question(
            self,
            "Confirm Removal",
            f"Remove export mapping '{details}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if result != QMessageBox.Yes:
            return
        self._export_path_mappings_table.removeRow(current_row)

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
            title = title_item.text().strip() if title_item is not None else ""
            path = self._library_tab_path_text(row)
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

    def _collect_export_path_mappings(
        self,
        *,
        show_warning: bool,
    ) -> list[ExportPathMapping] | None:
        configured_mappings: list[ExportPathMapping] = []
        seen_mappings: set[tuple[str, str]] = set()
        for row in range(self._export_path_mappings_table.rowCount()):
            from_item = self._export_path_mappings_table.item(row, 0)
            to_item = self._export_path_mappings_table.item(row, 1)
            raw_from = from_item.text() if from_item is not None else ""
            raw_to = to_item.text() if to_item is not None else ""
            from_prefix = self._normalize_export_mapping_from_prefix(raw_from)
            to_prefix = self._normalize_export_mapping_to_prefix(raw_to)
            if not raw_from.strip() and not raw_to.strip():
                continue
            if not from_prefix or not to_prefix:
                if show_warning:
                    QMessageBox.warning(
                        self,
                        "Invalid Export Path Mapping",
                        f"Row {row + 1}: both 'From Prefix' and 'To Prefix' are required.",
                    )
                return None
            mapping_key = (from_prefix, to_prefix)
            if mapping_key in seen_mappings:
                continue
            seen_mappings.add(mapping_key)
            configured_mappings.append(
                ExportPathMapping(
                    from_prefix=from_prefix,
                    to_prefix=to_prefix,
                )
            )
        return configured_mappings

    def _collect_settings_values(
        self,
        *,
        show_warning: bool,
    ) -> tuple[list[LibraryTab], list[ExportPathMapping], list[str]] | None:
        configured_tabs = self._collect_library_tabs(show_warning=show_warning)
        if configured_tabs is None:
            return None
        configured_export_path_mappings = self._collect_export_path_mappings(
            show_warning=show_warning
        )
        if configured_export_path_mappings is None:
            return None
        configured_supported_extensions = self._collect_supported_extensions(show_warning=show_warning)
        if configured_supported_extensions is None:
            return None
        return configured_tabs, configured_export_path_mappings, configured_supported_extensions

    def _validate_greenwich_time_signal_path(self, *, show_warning: bool) -> bool:
        enabled = self.greenwich_time_signal_enabled()
        raw_path = self.greenwich_time_signal_path()
        if not raw_path:
            if enabled and show_warning:
                QMessageBox.warning(
                    self,
                    "Invalid Greenwich Time Signal",
                    "Audio Path is required when Greenwich Time Signal is enabled.",
                )
            return not enabled

        candidate = Path(raw_path).expanduser()
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if not resolved.is_file():
            if show_warning:
                QMessageBox.warning(
                    self,
                    "Invalid Greenwich Time Signal Path",
                    f"Audio file does not exist:\n{resolved}",
                )
            return False

        normalized_path = str(resolved)
        if normalized_path != raw_path:
            self._greenwich_time_signal_path_edit.setText(normalized_path)
        return True

    def _on_settings_section_changed(self, index: int) -> None:
        if 0 <= index < self._settings_pages.count():
            self._settings_pages.setCurrentIndex(index)

    def _on_media_library_width_changed(self, value: int) -> None:
        normalized_media_library_width = max(10, min(90, int(value)))
        target_schedule_width = 100 - normalized_media_library_width
        if self._schedule_width_spinbox.value() == target_schedule_width:
            return
        self._schedule_width_spinbox.blockSignals(True)
        self._schedule_width_spinbox.setValue(target_schedule_width)
        self._schedule_width_spinbox.blockSignals(False)

    def _on_schedule_width_changed(self, value: int) -> None:
        normalized_schedule_width = max(10, min(90, int(value)))
        target_media_library_width = 100 - normalized_schedule_width
        if self._media_library_width_spinbox.value() == target_media_library_width:
            return
        self._media_library_width_spinbox.blockSignals(True)
        self._media_library_width_spinbox.setValue(target_media_library_width)
        self._media_library_width_spinbox.blockSignals(False)

    def reject(self) -> None:
        if not self._validate_greenwich_time_signal_path(show_warning=True):
            return
        collected_settings = self._collect_settings_values(show_warning=True)
        if collected_settings is None:
            return
        configured_tabs, configured_export_path_mappings, configured_supported_extensions = (
            collected_settings
        )
        self._configured_library_tabs = configured_tabs
        self._configured_export_path_mappings = configured_export_path_mappings
        self._configured_supported_extensions = configured_supported_extensions
        super().accept()

    def closeEvent(self, event) -> None:
        if not self._validate_greenwich_time_signal_path(show_warning=True):
            event.ignore()
            return
        collected_settings = self._collect_settings_values(show_warning=True)
        if collected_settings is None:
            event.ignore()
            return
        configured_tabs, configured_export_path_mappings, configured_supported_extensions = (
            collected_settings
        )
        self._configured_library_tabs = configured_tabs
        self._configured_export_path_mappings = configured_export_path_mappings
        self._configured_supported_extensions = configured_supported_extensions
        self.setResult(QDialog.Accepted)
        event.accept()

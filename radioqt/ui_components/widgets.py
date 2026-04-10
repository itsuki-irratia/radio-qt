from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


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

    def keyPressEvent(self, event) -> None:
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtGui import QKeyEvent

        try:
            if isinstance(event, QKeyEvent) and event.key() == _Qt.Key_Escape:
                self.hide()
                exit_handler = getattr(self.parent(), "_exit_fullscreen_overlay", None)
                if callable(exit_handler):
                    exit_handler()
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

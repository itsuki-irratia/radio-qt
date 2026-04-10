from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ..models import SCHEDULE_STATUS_PENDING, ScheduleEntry
from .logic import normalized_start


class RadioScheduler(QObject):
    schedule_triggered = Signal(object)
    log = Signal(str)

    def __init__(self, interval_ms: int = 500, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._entries: list[ScheduleEntry] = []
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)

    def set_entries(self, entries: list[ScheduleEntry]) -> None:
        self._entries = sorted(entries, key=lambda entry: normalized_start(entry.start_at))

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    @Slot()
    def _tick(self) -> None:
        now = datetime.now().astimezone()
        for entry in self._entries:
            if entry.status != SCHEDULE_STATUS_PENDING:
                continue
            start_at = normalized_start(entry.start_at, now)

            if now >= start_at:
                self.log.emit(f"Schedule triggered at {now.isoformat(timespec='seconds')}: {entry.id}")
                self.schedule_triggered.emit(entry)

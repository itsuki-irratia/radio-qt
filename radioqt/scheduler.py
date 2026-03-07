from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from .models import ScheduleEntry


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
        self._entries = sorted(entries, key=self._normalized_start)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    @Slot()
    def _tick(self) -> None:
        now = datetime.now().astimezone()
        for entry in self._entries:
            if not entry.enabled or entry.fired:
                continue
            start_at = self._normalized_start(entry)

            if now >= start_at:
                if entry.one_shot:
                    entry.fired = True
                self.log.emit(f"Schedule triggered at {now.isoformat(timespec='seconds')}: {entry.id}")
                self.schedule_triggered.emit(entry)

    @staticmethod
    def _normalized_start(entry: ScheduleEntry) -> datetime:
        if entry.start_at.tzinfo is None:
            return entry.start_at.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return entry.start_at

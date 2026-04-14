from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QStyle

from ..library import VIDEO_EXTENSIONS, media_source_suffix


class MainWindowFullscreenVisualsMixin:
    def _toggle_fullscreen(self) -> None:
        is_fullscreen = (
            getattr(self._video_widget, "isFullScreen", lambda: False)()
            or self._fullscreen_overlay.isVisible()
            or self.isFullScreen()
        )
        self._on_fullscreen_toggled(not is_fullscreen)

    def _ensure_exit_fullscreen(self) -> None:
        try:
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

    def _set_automation_status(self, is_playing: bool) -> None:
        if is_playing:
            self._play_button.setIcon(
                self._tinted_standard_icon(QStyle.SP_MediaPlay, QColor("#198754"))
            )
            self._stop_button.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
            return
        self._play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self._stop_button.setIcon(
            self._tinted_standard_icon(QStyle.SP_MediaStop, QColor("#dc3545"))
        )

    def _tinted_standard_icon(
        self,
        standard_pixmap: QStyle.StandardPixmap,
        color: QColor,
    ) -> QIcon:
        base_icon = self.style().standardIcon(standard_pixmap)
        base_pixmap = base_icon.pixmap(20, 20)
        if base_pixmap.isNull():
            return base_icon
        tinted = QPixmap(base_pixmap.size())
        tinted.fill(Qt.GlobalColor.transparent)
        painter = QPainter(tinted)
        painter.drawPixmap(0, 0, base_pixmap)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), color)
        painter.end()
        return QIcon(tinted)

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
            if ext in VIDEO_EXTENSIONS:
                try:
                    self._video_widget.setFullScreen(True)
                except Exception:
                    self.showFullScreen()
            else:
                title = self._player_media_label(media) if media is not None else "Now Playing"
                self._fullscreen_overlay.set_text(title)
                self._fullscreen_overlay.showFullScreen()
        else:
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
        self._fullscreen_active = False

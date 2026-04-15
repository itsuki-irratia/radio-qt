from __future__ import annotations

from PySide6.QtCore import Slot

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
            self._automation_status_button.setText("ONLINE")
            self._automation_status_button.setStyleSheet(
                "QPushButton {"
                "background-color: #198754;"
                "color: #ffffff;"
                "border: 1px solid #146c43;"
                "padding: 2px 8px;"
                "font-weight: 700;"
                "}"
            )
            self._automation_status_button.setToolTip("Stop")
            return
        self._automation_status_button.setText("OFFLINE")
        self._automation_status_button.setStyleSheet(
            "QPushButton {"
            "background-color: #dc3545;"
            "color: #ffffff;"
            "border: 1px solid #b02a37;"
            "padding: 2px 8px;"
            "font-weight: 700;"
            "}"
        )
        self._automation_status_button.setToolTip("Play")

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

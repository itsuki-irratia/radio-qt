from __future__ import annotations

import os
import sys

from PySide6.QtWidgets import QApplication

from .ui import MainWindow


def _configure_multimedia_runtime() -> None:
    # Prefer the FFmpeg backend for broader codec support.
    os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")

    # On some Linux setups, VAAPI probing fails repeatedly and breaks playback.
    # Default to software decoding for stability; allow explicit override.
    if sys.platform.startswith("linux"):
        disable_hw = os.environ.get("RADIOQT_DISABLE_HW_DECODING", "1")
        if disable_hw == "1":
            os.environ.setdefault("QT_FFMPEG_DECODING_HW_DEVICE_TYPES", "")


def run() -> int:
    _configure_multimedia_runtime()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())

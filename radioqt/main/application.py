from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ..ui import MainWindow
from .cli import parse_cli_args
from .runtime import configure_multimedia_runtime


def application_icon() -> QIcon | None:
    icon_path = Path(__file__).resolve().parent.parent / "radioqt.svg"
    if not icon_path.is_file():
        return None
    icon = QIcon(str(icon_path))
    if icon.isNull():
        return None
    return icon


def run() -> int:
    config_dir, qt_argv = parse_cli_args(sys.argv)
    configure_multimedia_runtime()
    app = QApplication(qt_argv)
    app_icon = application_icon()
    if app_icon is not None:
        app.setWindowIcon(app_icon)
    window = MainWindow(config_dir=config_dir)
    if app_icon is not None:
        window.setWindowIcon(app_icon)
    window.show()
    return app.exec()

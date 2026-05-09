from __future__ import annotations

import os
from pathlib import Path
import sys

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ..ui import MainWindow
from .cli import parse_cli_args
from .runtime import configure_multimedia_runtime

APP_NAME = "RadioQt"
ORG_NAME = "RadioQt"
DESKTOP_FILE_NAME = "radioqt"


def configure_application_identity() -> None:
    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setOrganizationName(ORG_NAME)
    pulse_properties = os.environ.get("PULSE_PROP", "").strip()
    application_name_property = f"application.name={APP_NAME}"
    media_role_property = "media.role=music"
    existing_properties = pulse_properties.split() if pulse_properties else []
    for property_value in (application_name_property, media_role_property):
        if property_value not in existing_properties:
            existing_properties.append(property_value)
    os.environ["PULSE_PROP"] = " ".join(existing_properties)


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
    configure_application_identity()
    app = QApplication(qt_argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setDesktopFileName(DESKTOP_FILE_NAME)
    app_icon = application_icon()
    if app_icon is not None:
        app.setWindowIcon(app_icon)
    window = MainWindow(config_dir=config_dir)
    if app_icon is not None:
        window.setWindowIcon(app_icon)
    window.show()
    return app.exec()

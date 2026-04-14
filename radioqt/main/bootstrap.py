from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from PySide6.QtCore import QLibraryInfo
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ..ui import MainWindow


def _candidate_qt_plugin_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()

    def _add(path: Path) -> None:
        resolved = path.expanduser().resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        roots.append(resolved)

    _add(Path(QLibraryInfo.path(QLibraryInfo.PluginsPath)))

    env_plugin_path = os.environ.get("QT_PLUGIN_PATH", "")
    for raw in env_plugin_path.split(os.pathsep):
        if not raw.strip():
            continue
        _add(Path(raw.strip()))

    for fallback in ("/usr/lib/qt6/plugins", "/usr/lib/qt/plugins"):
        path = Path(fallback)
        if path.exists():
            _add(path)

    return roots


def _backend_plugin_roots() -> dict[str, Path]:
    backend_roots: dict[str, Path] = {}
    for plugins_root in _candidate_qt_plugin_roots():
        multimedia_plugins_dir = plugins_root / "multimedia"
        if not multimedia_plugins_dir.is_dir():
            continue
        for plugin in multimedia_plugins_dir.iterdir():
            plugin_name = plugin.name.lower()
            if "ffmpeg" in plugin_name and "ffmpeg" not in backend_roots:
                backend_roots["ffmpeg"] = plugins_root
            if "gstreamer" in plugin_name and "gstreamer" not in backend_roots:
                backend_roots["gstreamer"] = plugins_root
    return backend_roots


def _ensure_qt_plugin_root(plugins_root: Path) -> None:
    current = [part for part in os.environ.get("QT_PLUGIN_PATH", "").split(os.pathsep) if part]
    normalized_current = {Path(part).expanduser().resolve() for part in current}
    resolved_root = plugins_root.expanduser().resolve()
    if resolved_root in normalized_current:
        return
    os.environ["QT_PLUGIN_PATH"] = (
        os.pathsep.join([str(resolved_root), *current]) if current else str(resolved_root)
    )


def _configure_multimedia_runtime() -> None:
    # Backend selection:
    # - RADIOQT_MEDIA_BACKEND=auto   -> let Qt choose (default)
    # - RADIOQT_MEDIA_BACKEND=ffmpeg -> force FFmpeg backend
    # - RADIOQT_MEDIA_BACKEND=gstreamer (or other) -> force explicit backend
    requested_backend = os.environ.get("RADIOQT_MEDIA_BACKEND", "auto").strip().lower()
    backend_roots = _backend_plugin_roots()
    if requested_backend and requested_backend != "auto":
        requested_root = backend_roots.get(requested_backend)
        if requested_root is not None:
            _ensure_qt_plugin_root(requested_root)
            os.environ["QT_MEDIA_BACKEND"] = requested_backend
        elif "ffmpeg" in backend_roots:
            _ensure_qt_plugin_root(backend_roots["ffmpeg"])
            os.environ["QT_MEDIA_BACKEND"] = "ffmpeg"
            print(
                (
                    f"Requested backend '{requested_backend}' is not available. "
                    "Falling back to 'ffmpeg'."
                ),
                file=sys.stderr,
            )
        else:
            print(
                (
                    f"Requested backend '{requested_backend}' is not available and no known fallback "
                    "backend was detected in Qt multimedia plugins."
                ),
                file=sys.stderr,
            )

    # On some Linux setups, VAAPI probing fails repeatedly and breaks playback.
    # Default to software decoding for stability; allow explicit override.
    if sys.platform.startswith("linux"):
        disable_hw = os.environ.get("RADIOQT_DISABLE_HW_DECODING", "1")
        if disable_hw == "1":
            os.environ.setdefault("QT_FFMPEG_DECODING_HW_DEVICE_TYPES", "")


def _parse_cli_args(argv: list[str]) -> tuple[Path, list[str]]:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "--config",
        default="config",
        help="Configuration directory (default: ./config)",
    )
    parsed_args, qt_args = parser.parse_known_args(argv[1:])
    config_dir = Path(parsed_args.config).expanduser()
    return config_dir, [argv[0], *qt_args]


def _application_icon() -> QIcon | None:
    icon_path = Path(__file__).resolve().parent / "radioqt.svg"
    if not icon_path.is_file():
        return None
    icon = QIcon(str(icon_path))
    if icon.isNull():
        return None
    return icon


def run() -> int:
    config_dir, qt_argv = _parse_cli_args(sys.argv)
    _configure_multimedia_runtime()
    app = QApplication(qt_argv)
    app_icon = _application_icon()
    if app_icon is not None:
        app.setWindowIcon(app_icon)
    window = MainWindow(config_dir=config_dir)
    if app_icon is not None:
        window.setWindowIcon(app_icon)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())

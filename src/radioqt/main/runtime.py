from __future__ import annotations

import os
from pathlib import Path
import sys

from PySide6.QtCore import QLibraryInfo


def candidate_qt_plugin_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()

    def _add(path: Path) -> None:
        resolved = path.expanduser().resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        roots.append(resolved)

    bundled_plugins_root = Path(QLibraryInfo.path(QLibraryInfo.PluginsPath))
    _add(bundled_plugins_root)

    env_plugin_path = os.environ.get("QT_PLUGIN_PATH", "")
    for raw in env_plugin_path.split(os.pathsep):
        if not raw.strip():
            continue
        _add(Path(raw.strip()))

    # PySide wheels bundle their own Qt build. Loading system Qt plugins into
    # that build can leave QtMultimedia with no usable backend at all.
    if "site-packages" in bundled_plugins_root.parts:
        return roots

    for fallback in ("/usr/lib/qt6/plugins", "/usr/lib/qt/plugins"):
        path = Path(fallback)
        if path.exists():
            _add(path)

    return roots


def backend_plugin_roots() -> dict[str, Path]:
    backend_roots: dict[str, Path] = {}
    for plugins_root in candidate_qt_plugin_roots():
        multimedia_plugins_dir = plugins_root / "multimedia"
        if not multimedia_plugins_dir.is_dir():
            continue
        for plugin in multimedia_plugins_dir.iterdir():
            plugin_name = plugin.name.lower()
            if "ffmpeg" in plugin_name and "ffmpeg" not in backend_roots:
                backend_roots["ffmpeg"] = plugins_root
    return backend_roots


def ensure_qt_plugin_root(plugins_root: Path) -> None:
    current = [part for part in os.environ.get("QT_PLUGIN_PATH", "").split(os.pathsep) if part]
    normalized_current = {Path(part).expanduser().resolve() for part in current}
    resolved_root = plugins_root.expanduser().resolve()
    if resolved_root in normalized_current:
        return
    os.environ["QT_PLUGIN_PATH"] = (
        os.pathsep.join([str(resolved_root), *current]) if current else str(resolved_root)
    )


def configure_multimedia_runtime() -> None:
    available_backends = backend_plugin_roots()
    if "ffmpeg" in available_backends:
        ensure_qt_plugin_root(available_backends["ffmpeg"])
        os.environ["QT_MEDIA_BACKEND"] = "ffmpeg"
    else:
        print(
            (
                "Qt FFmpeg backend was not detected in multimedia plugins. "
                "RadioQt requires ffmpeg backend support."
            ),
            file=sys.stderr,
        )

    if sys.platform.startswith("linux"):
        disable_hw = os.environ.get("RADIOQT_DISABLE_HW_DECODING", "1")
        if disable_hw == "1":
            os.environ.setdefault("QT_FFMPEG_DECODING_HW_DEVICE_TYPES", "")

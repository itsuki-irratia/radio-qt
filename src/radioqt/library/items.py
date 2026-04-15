from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileSystemModel, QTableWidget, QTreeView

from ..models import MediaItem
from .sources import is_supported_media_file


def selected_url_media_id(urls_table: QTableWidget) -> str | None:
    row = urls_table.currentRow()
    if row < 0:
        return None
    item = urls_table.item(row, 0)
    if item is None:
        return None
    return item.data(Qt.UserRole)


def ensure_file_media_item(
    media_items: dict[str, MediaItem],
    media_duration_cache: dict[str, int | None],
    file_path: Path,
) -> tuple[MediaItem, bool]:
    resolved = str(file_path.resolve())
    for item in media_items.values():
        if item.source == resolved:
            return item, False

    media = MediaItem.create(title=file_path.name, source=resolved)
    media_items[media.id] = media
    media_duration_cache.pop(media.id, None)
    return media, True


def selected_filesystem_media_id(
    filesystem_view: QTreeView,
    filesystem_model: QFileSystemModel,
    media_items: dict[str, MediaItem],
    media_duration_cache: dict[str, int | None],
    *,
    supported_extensions: set[str] | None = None,
) -> tuple[str | None, bool]:
    index = filesystem_view.currentIndex()
    if not index.isValid():
        return None, False

    path = Path(filesystem_model.filePath(index))
    if not path.is_file() or not is_supported_media_file(path, supported_extensions=supported_extensions):
        return None, False

    media, created = ensure_file_media_item(media_items, media_duration_cache, path)
    return media.id, created

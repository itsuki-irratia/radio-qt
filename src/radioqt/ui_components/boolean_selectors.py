from __future__ import annotations

from PySide6.QtWidgets import QComboBox


def _apply_boolean_selector_color(selector: QComboBox, value: str) -> None:
    if value == "True":
        selector.setStyleSheet(
            "QComboBox {"
            "background-color: #e8f5e9;"
            "color: #1b5e20;"
            "}"
            "QComboBox QAbstractItemView {"
            "selection-background-color: #2e7d32;"
            "selection-color: #ffffff;"
            "}"
        )
        return
    if value == "False":
        selector.setStyleSheet(
            "QComboBox {"
            "background-color: #ffebee;"
            "color: #b71c1c;"
            "}"
            "QComboBox QAbstractItemView {"
            "selection-background-color: #c62828;"
            "selection-color: #ffffff;"
            "}"
        )
        return
    selector.setStyleSheet("")


def _configure_boolean_selector(selector: QComboBox) -> None:
    _apply_boolean_selector_color(selector, selector.currentText())
    selector.currentTextChanged.connect(
        lambda value, combo=selector: _apply_boolean_selector_color(combo, value)
    )

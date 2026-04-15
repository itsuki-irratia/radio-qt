from __future__ import annotations

from pathlib import Path

from .parser import parse_settings_yaml
from .schema import AppConfig
from .serializer import dump_settings_yaml


def load_app_config(path: Path) -> AppConfig:
    if not path.exists():
        return AppConfig()
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError:
        return AppConfig()
    data = parse_settings_yaml(raw_text)
    if not data:
        return AppConfig()
    return AppConfig.from_dict(data)


def save_app_config(path: Path, config: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_settings_yaml(config), encoding="utf-8")

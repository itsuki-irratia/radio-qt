from __future__ import annotations

import json
from pathlib import Path

from .models import AppState


def load_state(path: Path) -> AppState:
    if not path.exists():
        return AppState()

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return AppState.from_dict(data)


def save_state(path: Path, state: AppState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state.to_dict(), f, indent=2)


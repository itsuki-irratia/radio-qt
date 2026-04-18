from __future__ import annotations

from pathlib import Path

from radioqt.models import AppState
import radioqt.ui.state_persistence as state_persistence_module
from radioqt.ui.state_persistence import MainWindowStatePersistenceMixin


class _StateLoadHarness(MainWindowStatePersistenceMixin):
    def __init__(self, state_path: Path) -> None:
        self._state_path = state_path
        self._state_version = 0
        self.recovered_errors: list[Exception] = []

    def _recover_state_after_load_failure(self, error: Exception) -> AppState:
        self.recovered_errors.append(error)
        self._state_version = 0
        return AppState()


def test_load_state_with_recovery_uses_loaded_snapshot(monkeypatch, tmp_path) -> None:
    expected_state = AppState()

    class _LoadedState:
        def __init__(self) -> None:
            self.state = expected_state
            self.version = 12

    monkeypatch.setattr(
        state_persistence_module,
        "load_state_with_version",
        lambda _path: _LoadedState(),
    )

    harness = _StateLoadHarness(tmp_path / "db.sqlite")
    loaded = harness._load_state_with_recovery()

    assert loaded is expected_state
    assert harness._state_version == 12
    assert harness.recovered_errors == []


def test_load_state_with_recovery_falls_back_to_recovery(monkeypatch, tmp_path) -> None:
    def _raise(_path):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        state_persistence_module,
        "load_state_with_version",
        _raise,
    )

    harness = _StateLoadHarness(tmp_path / "db.sqlite")
    loaded = harness._load_state_with_recovery()

    assert isinstance(loaded, AppState)
    assert harness._state_version == 0
    assert len(harness.recovered_errors) == 1
    assert str(harness.recovered_errors[0]) == "boom"

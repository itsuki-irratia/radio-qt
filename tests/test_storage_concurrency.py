from __future__ import annotations

import pytest

from radioqt.models import AppState, MediaItem
from radioqt.storage import (
    load_state,
    load_state_with_version,
    save_state,
    state_version,
    StateVersionConflictError,
)


def test_save_state_rejects_stale_expected_version(tmp_path) -> None:
    state_path = tmp_path / "db.sqlite"
    save_state(state_path, AppState())

    snapshot_a = load_state_with_version(state_path)
    snapshot_b = load_state_with_version(state_path)

    snapshot_a.state.media_items.append(MediaItem.create(title="A", source="/tmp/a.mp4"))
    save_state(
        state_path,
        snapshot_a.state,
        expected_version=snapshot_a.version,
    )

    snapshot_b.state.media_items.append(MediaItem.create(title="B", source="/tmp/b.mp4"))
    with pytest.raises(StateVersionConflictError):
        save_state(
            state_path,
            snapshot_b.state,
            expected_version=snapshot_b.version,
        )

    latest_state = load_state(state_path)
    assert len(latest_state.media_items) == 1
    assert latest_state.media_items[0].title == "A"


def test_state_version_increments_on_successful_save(tmp_path) -> None:
    state_path = tmp_path / "db.sqlite"
    assert state_version(state_path) == 0

    version_after_first_save = save_state(state_path, AppState())
    assert version_after_first_save == 1
    assert state_version(state_path) == 1

    snapshot = load_state_with_version(state_path)
    snapshot.state.media_items.append(MediaItem.create(title="Track", source="/tmp/track.mp3"))
    version_after_second_save = save_state(
        state_path,
        snapshot.state,
        expected_version=snapshot.version,
    )
    assert version_after_second_save == 2
    assert state_version(state_path) == 2

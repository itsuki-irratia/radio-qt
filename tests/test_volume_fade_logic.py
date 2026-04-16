from __future__ import annotations

from radioqt.ui.playback_handlers import resolve_fade_in_start_and_target


def test_resolve_fade_in_reverses_active_fade_out() -> None:
    start_volume, target_volume = resolve_fade_in_start_and_target(
        current_volume=78,
        last_nonzero_volume=100,
        fade_timer_active=True,
        current_fade_target_volume=0,
    )
    assert start_volume == 78
    assert target_volume == 100


def test_resolve_fade_in_from_zero_uses_last_nonzero() -> None:
    start_volume, target_volume = resolve_fade_in_start_and_target(
        current_volume=0,
        last_nonzero_volume=65,
        fade_timer_active=False,
        current_fade_target_volume=0,
    )
    assert start_volume == 0
    assert target_volume == 65


def test_resolve_fade_in_default_behavior_kept() -> None:
    start_volume, target_volume = resolve_fade_in_start_and_target(
        current_volume=50,
        last_nonzero_volume=100,
        fade_timer_active=False,
        current_fade_target_volume=0,
    )
    assert start_volume == 0
    assert target_volume == 50

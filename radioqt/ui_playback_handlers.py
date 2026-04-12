from __future__ import annotations

from datetime import datetime
import time

from PySide6.QtCore import Slot
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QStyle

from .models import MediaItem, ScheduleEntry
from .playback import dequeue_next_playable_media, process_schedule_trigger, resolve_play_request
from .scheduling import prepare_schedule_entries_for_play


class MainWindowPlaybackHandlersMixin:
    @Slot(object)
    def _on_schedule_triggered(self, entry: ScheduleEntry) -> None:
        current_media_name = (
            self._player.current_media.title
            if self._player.current_media is not None
            else "nothing"
        )
        outcome = process_schedule_trigger(
            entry,
            self._media_items,
            self._play_queue,
            automation_playing=self._automation_playing,
            player_is_playing=self._player.is_playing(),
            current_media_name=current_media_name,
        )
        if outcome.kind == "ignored_stopped":
            self._append_log(f"Ignoring schedule {entry.id}: automation is stopped")
            self._refresh_schedule_table()
            self._save_state()
            return
        if outcome.kind == "missing_media":
            self._append_log(
                f"Skipping schedule {entry.id}: media '{self._media_log_name(entry.media_id)}' not found"
            )
            self._refresh_schedule_table()
            self._save_state()
            return
        if outcome.kind == "play_now" and outcome.media is not None:
            if outcome.interrupted_media_name is not None:
                self._append_log(
                    f"Hard sync active for '{outcome.media.title}': interrupting '{outcome.interrupted_media_name}'"
                )
            scheduled_start_at = self._normalized_start(entry.start_at)
            offset_ms = max(0, int((datetime.now().astimezone() - scheduled_start_at).total_seconds() * 1000))
            self._player.play_media(
                outcome.media,
                start_position_ms=offset_ms,
                fade_in=entry.fade_in,
                fade_out=entry.fade_out,
                expected_duration_ms=self._entry_duration_ms(entry),
                fade_in_duration_ms=self._fade_in_duration_ms(),
                fade_out_duration_ms=self._fade_out_duration_ms(),
            )
        elif outcome.kind == "queued" and outcome.media is not None:
            self._append_log(f"Player busy; queued scheduled media '{outcome.media.title}'")

        self._refresh_schedule_table()
        self._save_state()

    @Slot(object)
    def _on_media_started(self, media: MediaItem) -> None:
        self._current_playback_position_ms = self._player.current_position_ms()
        self._update_now_playing_label()
        self._update_player_visual_state()
        self._append_log(f"Now playing '{media.title}'")

    @Slot()
    def _on_media_finished(self) -> None:
        current_media_name = (
            self._player.current_media.title
            if self._player.current_media is not None
            else "unknown"
        )
        self._append_log(f"Media finished '{current_media_name}'")
        self._update_player_visual_state()
        self._play_next_from_queue()

    def _play_next_from_queue(self) -> None:
        result = dequeue_next_playable_media(self._play_queue, self._media_items)
        if result is not None:
            self._save_state()
            if result.skipped_missing_count:
                self._append_log(
                    f"Skipped {result.skipped_missing_count} missing queued media item(s)"
                )
            if result.queue_item.source == "schedule":
                self._append_log(
                    f"Playing queued scheduled media '{result.media.title}'"
                )
            else:
                self._append_log(
                    f"Playing queued manual media '{result.media.title}'"
                )
            queued_schedule_entry = (
                self._schedule_entry_by_id(result.queue_item.schedule_entry_id)
                if result.queue_item.source == "schedule"
                else None
            )
            self._player.play_media(
                result.media,
                fade_in=queued_schedule_entry.fade_in if queued_schedule_entry is not None else False,
                fade_out=queued_schedule_entry.fade_out if queued_schedule_entry is not None else False,
                expected_duration_ms=self._entry_duration_ms(queued_schedule_entry),
                fade_in_duration_ms=self._fade_in_duration_ms(),
                fade_out_duration_ms=self._fade_out_duration_ms(),
            )
            return
        self._player.clear_current_media()
        self._current_playback_position_ms = 0
        self._save_state()
        self._update_now_playing_label()
        self._update_player_visual_state()

    @Slot(object)
    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self._update_player_visual_state()
        if state == QMediaPlayer.StoppedState and not self._play_queue:
            self._current_playback_position_ms = 0
            self._update_now_playing_label()

    @Slot(int)
    def _on_playback_position_changed(self, position_ms: int) -> None:
        self._current_playback_position_ms = max(0, position_ms)
        self._update_now_playing_label()

    @Slot(int)
    def _on_volume_slider_value_changed(self, value: int) -> None:
        self._volume_label.setText(f"{value}%")
        if value > 0:
            # Keep the "intended" non-zero volume stable while fading.
            # Otherwise transitional values (1%, 2%, ...) can overwrite it.
            if (
                not self._volume_fade_timer.isActive()
                or value == self._volume_fade_target_volume
            ):
                self._last_nonzero_volume = value
            if self._mute_button.isChecked():
                self._mute_button.blockSignals(True)
                self._mute_button.setChecked(False)
                self._mute_button.blockSignals(False)

    def _start_volume_fade(
        self,
        *,
        start_volume: int,
        target_volume: int,
        duration_ms: int,
    ) -> None:
        normalized_start = max(0, min(100, start_volume))
        normalized_target = max(0, min(100, target_volume))
        self._volume_fade_timer.stop()
        self._volume_fade_start_volume = normalized_start
        self._volume_fade_target_volume = normalized_target
        self._volume_fade_duration_ms = max(1, duration_ms)
        self._volume_slider.setValue(normalized_start)
        if normalized_start == normalized_target:
            return
        self._volume_fade_started_at = time.monotonic()
        self._volume_fade_timer.start()

    @Slot()
    def _on_volume_fade_tick(self) -> None:
        elapsed_ms = max(0, int((time.monotonic() - self._volume_fade_started_at) * 1000))
        progress = min(1.0, elapsed_ms / self._volume_fade_duration_ms)
        next_value = int(
            round(
                self._volume_fade_start_volume
                + (self._volume_fade_target_volume - self._volume_fade_start_volume) * progress
            )
        )
        if self._volume_slider.value() != next_value:
            self._volume_slider.setValue(next_value)
        if progress < 1.0:
            return
        self._volume_fade_timer.stop()
        if self._volume_fade_target_volume <= 0:
            self._mute_button.blockSignals(True)
            self._mute_button.setChecked(True)
            self._mute_button.blockSignals(False)
        elif self._mute_button.isChecked():
            self._mute_button.blockSignals(True)
            self._mute_button.setChecked(False)
            self._mute_button.blockSignals(False)

    @Slot(bool)
    def _on_mute_toggled(self, checked: bool) -> None:
        self._volume_fade_timer.stop()
        self._mute_button.setIcon(
            self.style().standardIcon(
                QStyle.SP_MediaVolumeMuted if checked else QStyle.SP_MediaVolume
            )
        )
        if checked:
            current = self._volume_slider.value()
            if current > 0:
                self._last_nonzero_volume = current
            self._volume_slider.setValue(0)
            return
        restore_volume = self._last_nonzero_volume if self._last_nonzero_volume > 0 else 100
        self._volume_slider.setValue(restore_volume)

    @Slot()
    def _on_volume_fade_in_clicked(self) -> None:
        current_volume = self._volume_slider.value()
        target_volume = current_volume
        if current_volume <= 0:
            target_volume = self._last_nonzero_volume if self._last_nonzero_volume > 0 else 100
        elif current_volume <= 1 and self._last_nonzero_volume > current_volume:
            # Recover from edge cases where slider got stuck near zero.
            target_volume = self._last_nonzero_volume
        if self._mute_button.isChecked():
            self._mute_button.blockSignals(True)
            self._mute_button.setChecked(False)
            self._mute_button.blockSignals(False)
        self._start_volume_fade(
            start_volume=0,
            target_volume=target_volume,
            duration_ms=self._fade_in_duration_ms(),
        )

    @Slot()
    def _on_volume_fade_out_clicked(self) -> None:
        current_volume = self._volume_slider.value()
        if current_volume <= 0:
            self._mute_button.blockSignals(True)
            self._mute_button.setChecked(True)
            self._mute_button.blockSignals(False)
            return
        self._start_volume_fade(
            start_volume=current_volume,
            target_volume=0,
            duration_ms=self._fade_out_duration_ms(),
        )

    @Slot()
    def _on_play_clicked(self) -> None:
        now = datetime.now().astimezone()
        self._refresh_cron_schedule_entries(self._runtime_cron_dates())
        self._recalculate_schedule_durations()
        play_preparation = prepare_schedule_entries_for_play(
            self._schedule_entries,
            now,
            automation_playing=self._automation_playing,
        )
        self._scheduler.set_entries(self._schedule_entries)
        if play_preparation.started_automation:
            self._automation_playing = True
            self._set_automation_status(True)
            self._scheduler.start()
            self._append_log("Automation status changed to Playing")
        if play_preparation.normalized_entries:
            normalized_details = self._normalized_missed_details(
                now,
                play_preparation.normalized_entries,
            )
            self._refresh_schedule_table()
            self._save_state()
            self._append_log(
                f"Marked {len(play_preparation.normalized_entries)} missed one-shot schedule item(s) as missed"
            )
            self._append_normalized_missed_logs(
                len(play_preparation.normalized_entries),
                normalized_details,
            )
        if play_preparation.restored_count:
            self._append_log(
                f"Restored {play_preparation.restored_count} active one-shot schedule item(s) from missed"
            )

        play_request = resolve_play_request(
            self._schedule_entries,
            self._media_items,
            now,
            player_is_playing=self._player.is_playing(),
            player_has_active_media=self._player.has_active_media(),
            queue_has_items=bool(self._play_queue),
        )
        if play_request.kind == "already_playing":
            return

        if play_request.kind == "active_schedule" and play_request.active_schedule is not None:
            active_play = play_request.active_schedule
            if active_play.kind == "unsupported_status":
                self._refresh_schedule_table()
                self._save_state()
                return
            if active_play.kind == "missing_media":
                self._append_log(
                    f"Play ignored: scheduled media '{self._media_log_name(active_play.entry.media_id)}' not found"
                )
                self._refresh_schedule_table()
                self._save_state()
                return
            if active_play.kind != "play_active" or active_play.media is None or active_play.start_at is None:
                self._refresh_schedule_table()
                self._save_state()
                return
            end_label = (
                active_play.end_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                if active_play.end_at is not None
                else "Open-ended"
            )
            self._append_log(
                f"Active schedule entry '{active_play.media.title}': "
                f"start={active_play.start_at.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}, "
                f"end={end_label}, end_reason={active_play.end_reason}, "
                f"offset_ms={active_play.offset_ms}"
            )
            self._player.play_media(
                active_play.media,
                start_position_ms=active_play.offset_ms,
                fade_in=active_play.entry.fade_in,
                fade_out=active_play.entry.fade_out,
                expected_duration_ms=self._entry_duration_ms(active_play.entry),
                fade_in_duration_ms=self._fade_in_duration_ms(),
                fade_out_duration_ms=self._fade_out_duration_ms(),
            )
            self._append_log(
                f"Started scheduled media '{active_play.media.title}' from {self._format_duration(active_play.offset_ms // 1000)}"
            )
            self._refresh_schedule_table()
            self._save_state()
            return
        if play_request.kind == "resume_loaded_media":
            self._player.play()
            return
        if play_request.kind == "play_queue":
            self._play_next_from_queue()
            return
        self._append_log(
            f"Play ignored: no active or queued media at {now.strftime('%H:%M:%S')} "
            f"— {self._schedule_log_summary(now)}"
        )

    @Slot()
    def _on_stop_clicked(self) -> None:
        current_media_name = (
            self._player.current_media.title
            if self._player.current_media is not None
            else "nothing"
        )
        if self._automation_playing:
            self._automation_playing = False
            self._set_automation_status(False)
            self._scheduler.stop()
            self._append_log("Automation status changed to Stopped")
        self._player.clear_current_media()
        self._current_playback_position_ms = 0
        self._update_now_playing_label()
        self._update_player_visual_state()
        self._append_log(f"Playback stopped and media cleared ('{current_media_name}')")

    @Slot(str)
    def _on_player_error(self, message: str) -> None:
        current_media_name = (
            self._player.current_media.title
            if self._player.current_media is not None
            else "unknown"
        )
        self._append_log(f"Player error on '{current_media_name}': {message}")

    @Slot(object)
    def _on_audio_levels_changed(self, levels: object) -> None:
        if self._media_looks_like_video(self._player.current_media):
            return
        self._waveform_widget.set_levels(levels if isinstance(levels, list) else None)

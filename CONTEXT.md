# RadioQt Context

This file is a working memory for future sessions on this repo.
It is intentionally practical: architecture, behavior, operational rules, and the parts that are easiest to break.

## Project Summary

RadioQt is a desktop multimedia automation app built with Python and PySide6.
It combines:

- local file playback
- stream URL playback
- manual datetime scheduling
- CRON-based schedule generation
- queue fallback when playback is busy
- SQLite persistence

Primary entrypoint:

- `python -m radioqt`

Main runtime state file:

- `state/radio_state.db`

## Repo Layout

- `README.md`: user-facing setup and run notes
- `requirements.txt`: only runtime dependency is `PySide6>=6.6`
- `radioqt/main.py`: Qt app startup and multimedia environment setup
- `radioqt/__main__.py`: package entrypoint
- `radioqt/models.py`: dataclasses and schedule status constants
- `radioqt/cron.py`: custom 6-field CRON parser and iterator
- `radioqt/player.py`: wrapper around `QMediaPlayer`
- `radioqt/schedule_logic.py`: pure scheduling helpers extracted from UI logic
- `radioqt/scheduler.py`: timer-based schedule trigger engine
- `radioqt/storage.py`: SQLite persistence and legacy JSON migration
- `radioqt/ui.py`: almost all application behavior and UI
- `.codex`: currently empty

## Runtime / Startup

Startup path:

1. `radioqt.main.run()`
2. `_configure_multimedia_runtime()`
3. `QApplication(...)`
4. `MainWindow()`
5. `window.show()`

Important environment behavior in `radioqt/main.py`:

- `QT_MEDIA_BACKEND` defaults to `ffmpeg`
- on Linux, hardware decoding is disabled by default unless `RADIOQT_DISABLE_HW_DECODING=0`
- disabling hardware decode is a stability workaround for VAAPI issues

## Core Data Model

Defined in `radioqt/models.py`.

### `MediaItem`

- fields: `id`, `title`, `source`, `created_at`
- `source` is either a local filesystem path or a URL

### `ScheduleEntry`

- fields:
  - `id`
  - `media_id`
  - `start_at`
  - `duration`
  - `hard_sync`
  - `status`
  - `one_shot`
  - `cron_id`
  - `cron_status_override`
  - `cron_hard_sync_override`

Status constants:

- `pending`
- `disabled`
- `fired`
- `missed`

### `CronEntry`

- fields: `id`, `media_id`, `expression`, `hard_sync`, `enabled`, `created_at`

### `AppState`

- `media_items`
- `schedule_entries`
- `cron_entries`
- `queue`
- `schedule_auto_focus`

## Persistence

Implemented in `radioqt/storage.py`.

SQLite tables:

- `media_items`
- `schedule_entries`
- `cron_entries`
- `queue_items`
- `app_meta`

Important notes:

- save is full-rewrite style: tables are cleared and reinserted from in-memory state
- entry ordering is preserved via `position`
- `app_meta` stores:
  - `legacy_json_migrated`
  - `schedule_auto_focus`

Legacy migration:

- if `state/radio_state.json` exists and DB is empty, it is migrated automatically

Schema migration helpers:

- old `enabled` / `fired` schedule fields are migrated into `status`
- CRON-related schedule columns are added if missing

## CRON Semantics

Implemented in `radioqt/cron.py`.

This app uses a 6-field format:

- `second minute hour day-of-month month day-of-week`

Behavior:

- day-of-week accepts `1-7`, where `7` is normalized to `0` internally
- if both day-of-month and day-of-week are specific, matching uses OR semantics
- parser supports:
  - `*`
  - `,`
  - `-`
  - `/`

Main APIs:

- `CronExpression.parse(raw)`
- `iter_datetimes_on_date(target_date, tzinfo)`
- `next_at_or_after(start)`

## Playback Layer

Implemented in `radioqt/player.py`.

Controller responsibilities:

- wraps `QMediaPlayer`
- emits app-friendly signals
- sets audio output and audio buffer output
- supports seeking on start through `start_position_ms`
- supports video output with `QVideoWidget`
- computes audio levels for waveform display

Important playback detail:

- when `play_media(..., start_position_ms=...)` is used, it tries an immediate seek and then a second seek on `LoadedMedia` / `BufferedMedia`
- this is what enables resuming a scheduled item from the correct elapsed point when pressing `Play` mid-program

Limitations:

- remote streams/URLs do not provide duration probing here

## Scheduler Engine

Implemented in `radioqt/scheduler.py`.

Behavior:

- a `QTimer` ticks every 500 ms
- it iterates through sorted schedule entries
- only `pending` entries are triggerable
- if `now >= start_at`, it emits `schedule_triggered(entry)`

Important:

- the scheduler itself does not resolve collisions, end times, or queue rules
- timing normalization is shared through `radioqt/schedule_logic.py`
- all playback consequences still live in `ui.py`

## Schedule Logic Module

Implemented in `radioqt/schedule_logic.py`.

This module now contains the pure scheduling computations that were previously embedded in `ui.py`:

- `normalized_start(...)`
- `sort_schedule_entries(...)`
- `schedule_entry_end_at(...)`
- `active_schedule_entry_at(...)`
- `schedule_entry_window_details(...)`
- `normalize_overdue_one_shots(...)`
- `restore_active_missed_one_shots(...)`

This is the safest place to keep evolving schedule rules without mixing them with Qt widget code.

## UI Structure

Almost all functional behavior is in `radioqt/ui.py`.

Main UI areas:

- player area:
  - `QVideoWidget` for video
  - custom `WaveformWidget` for audio-only playback
- controls:
  - `Play`
  - `Stop`
  - volume slider
- media library:
  - filesystem tab
  - streamings tab
- schedule panel:
  - Date Time tab
  - CRON tab
- runtime log view

Dialogs:

- `ScheduleDialog`
- `CronDialog`
- `CronHelpDialog`

## Media Library Rules

Filesystem:

- only files with extensions in `SUPPORTED_MEDIA_EXTENSIONS` are accepted
- selecting a file auto-creates a `MediaItem` if needed

Streams:

- manually added via URL + title dialog
- editable and removable from context menu

Deleting media:

- removes linked CRON entries
- removes linked schedule entries
- removes queued items pointing to that media
- clears current playback if that media is playing

## Schedule Panel Rules

Date Time tab:

- shows only entries for the selected date
- columns:
  - Start Time
  - Duration
  - Media
  - Hard Sync
  - Status

CRON tab:

- lists CRON rules, not occurrences
- columns:
  - CRON
  - Media
  - Hard Sync
  - Status

Auto-focus:

- checkbox label: `Focus current program`
- when enabled, the schedule table auto-selects and scrolls to the currently active entry
- persisted in `AppState.schedule_auto_focus`

## Schedule Generation Rules

Manual schedule entries:

- created by `ScheduleEntry.create(...)`
- if created in the past, they are immediately marked `missed`

CRON-generated schedule entries:

- generated in `_refresh_cron_schedule_entries(...)`
- deterministic ID: `uuid5(NAMESPACE_URL, f"radioqt-cron:{cron_id}:{start_at.isoformat()}")`
- one generated occurrence per matching datetime

Runtime generation window:

- the app refreshes CRON entries for `yesterday`, `today`, and `tomorrow`
- the selected filter date may also trigger generation for that date

CRON overrides:

- generated occurrences inherit `media_id`, `hard_sync`, `cron_id`
- per-occurrence status/hard-sync overrides are possible through:
  - `cron_status_override`
  - `cron_hard_sync_override`

Protection:

- active CRON-managed rows cannot be removed from the Date Time tab while the CRON rule is enabled

## Duration Rules

Schedule durations are recalculated in `_recalculate_schedule_durations()`.

Current behavior:

- `duration` comes from media metadata only
- it is probed with `ffprobe`
- fallback is parsing `ffmpeg -i` output
- if both fail, duration is `None`
- remote URLs are treated as having no duration

Important recent fix:

- duration is no longer overwritten by the gap until the next scheduled item
- this matters especially for CRON rules like "every 5 minutes" with a 2:28 video

Tooltip behavior in the schedule table:

- shows source path / URL
- shows whether entry is manual or generated from CRON
- shows whether duration was read successfully or unavailable

## Active Entry Semantics

The active program is computed by `_active_schedule_entry_at(now)`.

An entry is considered active if:

- `now >= start_at`
- `now < end_at`
- its status is not `disabled`

How `end_at` is computed:

- candidate 1: `start_at + duration`
- candidate 2: next schedule entry's `start_at`
- actual end is the earliest available candidate

This means:

- a later scheduled item can cut off an earlier one
- a known media duration can also end the active window before the next item

## Play Button Behavior

`_on_play_clicked()` is one of the most important methods in the app.

Current behavior:

1. refresh CRON occurrences
2. recalculate durations
3. restore wrongly-missed active one-shots if needed
4. update scheduler entries
5. if automation was stopped:
   - mark automation as playing
   - start scheduler timer
   - mark truly overdue pending one-shots as missed
6. if player is already playing, stop there
7. if there is an active schedule entry:
   - allow statuses `pending`, `fired`, or restored `missed`
   - compute elapsed offset from `now - start_at`
   - start playback from that offset
   - mark one-shot as `fired`
8. else if player has paused/stopped media loaded:
   - resume it
9. else if queue has pending media:
   - play next queued item
10. otherwise log a compact schedule summary

Important recent fix:

- pressing `Play` after the scheduled start time now correctly resumes the current scheduled item from the proper offset
- entries wrongly stored as `missed` but still active are restored before playback decision

## Triggered Schedule Behavior

When `RadioScheduler` emits `schedule_triggered(entry)`, `_on_schedule_triggered()` decides what to do.

Rules:

- if automation is stopped:
  - pending one-shot becomes `missed`
  - event is logged and ignored
- if media is missing:
  - one-shot becomes `missed`
  - event is logged and ignored
- otherwise:
  - one-shot becomes `fired`
  - if `hard_sync` is true, current playback is interrupted
  - if `hard_sync` is false and player is busy, media is queued

## Queue Rules

Queue is a `deque` of queue items with playback context.

Used for:

- manual queueing from the library
- scheduled playback when player is busy and `hard_sync` is disabled

Current persisted queue item shape:

- `media_id`
- `source`
- `schedule_entry_id`

When current media finishes:

- `_play_next_from_queue()` pulls the next valid media
- if queue is empty, current media is cleared and UI resets

## Status Rules

`pending`

- entry can still trigger

`disabled`

- entry is visible but not active/triggerable

`fired`

- entry has already been started
- one-shot manual/CRON occurrences usually transition here when triggered or resumed

`missed`

- entry is in the past and no longer valid
- missing sources can also force this

Important subtlety:

- active one-shots that were incorrectly persisted as `missed` can now be restored to `pending` during startup or when pressing `Play`

## Startup Recovery Rules

On startup, `MainWindow._load_initial_state()` does all of this:

- loads DB state
- clears duration cache
- regenerates CRON runtime window
- recalculates durations
- restores active items that were incorrectly `missed`
- normalizes genuinely overdue one-shots to `missed`
- picks the initial filter date
- refreshes tables
- applies schedule auto-focus if enabled
- loads volume into player
- logs normalization/restoration activity

## Logging

Logs are appended to a `QPlainTextEdit` with `[HH:MM:SS]` prefix.

Typical log events:

- startup load
- manual scheduling
- CRON add/remove
- status changes
- schedule trigger
- hard sync interruption
- queueing
- playback start/finish
- player errors

Important recent fix:

- the "Play ignored: no active or queued media" log used to dump the entire schedule
- it now uses `_schedule_log_summary(...)` and only shows a compact next/recent summary

## Fullscreen

Fullscreen support is custom and somewhat defensive.

Behavior:

- double-click on player area toggles fullscreen
- `Esc` exits fullscreen
- video uses `QVideoWidget.setFullScreen(True)` when possible
- audio-only playback uses `FullscreenOverlay`

This part has broad `try/except` guards because Qt backend behavior can differ by platform.

## Known Operational Constraints

- only PySide6 is declared as a dependency; `ffprobe` / `ffmpeg` are assumed to exist if local duration probing should work
- stream URLs do not get duration metadata
- a lot of business logic lives in one large `ui.py`, so regressions often come from there
- scheduler tick logic is simple and depends on up-to-date in-memory schedule entries
- persistence is full rewrite, not incremental

## Known Recent Fixes Already Present

These are now part of the current codebase and should be preserved:

1. Schedule auto-focus checkbox in Date Time tab, persisted in SQLite.
2. Schedule duration reflects media duration, not the gap to the next item.
3. Mid-program `Play` resumes the active scheduled item from the correct offset.
4. Active items incorrectly persisted as `missed` are restored on startup / play.
5. Oversized schedule log output was reduced to a compact summary.
6. Duration tooltip explains whether metadata was read or unavailable.
7. Schedule tooltip shows computed start/end window and whether the end comes from media duration or the next scheduled item.
8. The Schedule UI includes a visible overlap note explaining that the next scheduled item can cut off the current one.
9. Queue items now persist origin context (`manual` vs `schedule`) plus optional `schedule_entry_id`.
9. Diagnostic logs now include active-entry timing/offset details on `Play` and sampled details when overdue items are normalized to `missed`.
10. The Duration column now distinguishes formatted media duration, remote streams, missing media/files, and unknown probe failures.
11. Runtime logs can now be exported from the Help menu for troubleshooting.
12. Core pure scheduling computations were extracted from `ui.py` into `radioqt/schedule_logic.py`.

## Places Most Likely To Need Care

- `_refresh_cron_schedule_entries()`
- `_normalize_overdue_one_shots()`
- `_restore_active_missed_one_shots()`
- `_active_schedule_entry_at()`
- `_schedule_entry_end_at()`
- `_on_play_clicked()`
- `_on_schedule_triggered()`
- `_recalculate_schedule_durations()`

If a future change touches any of those, re-check:

- startup normalization
- CRON regeneration
- resumed playback offset
- status transitions
- queue fallback
- duration display

## Quick Test Checklist

When changing scheduling/playback behavior, test at least:

1. Local file manual schedule in the future.
2. Local file manual schedule in the past.
3. CRON every 5 minutes with a short video.
4. `Play` pressed in the middle of an active item.
5. `Hard sync` on and off.
6. Missing media source.
7. Disabled CRON rule.
8. Stream URL item.
9. App restart with active schedule entries already in DB.
10. Schedule auto-focus enabled.

## Suggested Future Refactors

These are not implemented yet, but would improve maintainability:

- split `ui.py` into smaller modules:
  - dialogs
  - schedule logic
  - media library logic
  - playback controller UI
- add automated tests for:
  - CRON generation
  - active entry computation
  - overdue/missed restoration
  - play-from-offset behavior
- store duration probe failures separately from `None` if richer UI is needed
- add a lightweight service layer so scheduling rules are not buried in the Qt widget class

## Known Issues / TODO

This section is intentionally operational and should be updated after important bugs or feature work.

### Known Issues

- `radioqt/ui.py` is the main risk area because UI code, schedule rules, playback decisions, persistence coordination, and log behavior are all mixed in one file.
- There are no automated tests yet, so scheduling regressions are easy to introduce.
- Local duration probing depends on external `ffprobe` / `ffmpeg` binaries being available on the system.
- Remote streams/URLs do not expose duration in the current implementation, so schedule duration may remain unknown for those items.
- The scheduler is timer-driven and simple; if future logic becomes more complex, duplicate-trigger and state-transition bugs will need extra care.
- Fullscreen behavior is intentionally defensive and may behave differently across platforms/backends.
- The current CRON runtime window only materializes occurrences for yesterday, today, and tomorrow, so any future feature that expects a long visible horizon in the Date Time tab will need a wider generation strategy.
- Manual schedule entries created in the past are immediately marked `missed`; that behavior is intentional now, but it may surprise users expecting manual backfill/recovery playback.
- `QMediaPlayer` seek behavior can vary by backend/platform, so play-from-offset is implemented defensively but still depends on Qt multimedia backend reliability.
- Schedule persistence is stateful enough that an old bad status in SQLite can affect current behavior until startup/play recovery logic corrects it.
- The active-entry algorithm uses the earliest of `start + duration` and `next entry start`, so overlapping schedules are effectively truncated by the next entry even if the media file is longer.
- Editing a media item or removing one can have broad side effects because schedule rows, CRON rules, queue entries, and current playback all reference the same `media_id`.
- The UI currently exposes status and hard-sync editing directly inside tables, which is convenient but increases the chance of subtle state interactions with CRON-managed rows.
- Logging is user-friendly now, but it is still not structured; troubleshooting complex timing issues can require inspecting the SQLite database directly.
- Scheduling logic is no longer fully trapped in `ui.py`, but there is still significant schedule/UI coordination there.

### High-Value TODO

- Add automated tests for:
  - CRON parsing
  - CRON occurrence generation
  - active schedule entry detection
  - overdue/missed normalization
  - restore-from-missed behavior
  - play-from-offset behavior
- Continue extracting remaining schedule/UI coordination from `MainWindow` now that core computations live in `radioqt/schedule_logic.py`.
- Extract media library actions from `ui.py` into their own module.
- Consider adding a small diagnostics screen for troubleshooting user reports beyond raw log export.

### Session Notes

- If a user says "it should be playing now but it is not", inspect:
  - `_active_schedule_entry_at()`
  - `_schedule_entry_end_at()`
  - `_normalize_overdue_one_shots()`
  - `_restore_active_missed_one_shots()`
  - current rows in `state/radio_state.db`
- If a user says "duration is wrong", inspect:
  - `_recalculate_schedule_durations()`
  - `_media_duration_seconds()`
  - `_probe_with_ffprobe()`
  - whether the source is a local file or remote URL
- If a change seems correct in code but not in the running app, make sure the user has restarted the process so the new Python code is loaded.

## Last Notes For Future Sessions

- treat `ui.py` as the source of truth for current business behavior
- check `state/radio_state.db` when a user reports "it says missed/pending but should not"
- many bugs here are not raw playback bugs, but status-transition bugs
- if behavior looks wrong after a code change, remember that the user may need to restart the app so the new Python code is actually loaded

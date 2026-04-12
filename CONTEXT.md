# RadioQt Context

Working memory for future sessions on this repository.
This file describes the current architecture and behavior as implemented in code.

## Project Snapshot

RadioQt is a desktop automation player built with Python + PySide6.

Core capabilities:
- Local file playback and stream URL playback
- Manual datetime schedule entries
- CRON-generated schedule entries
- Queue fallback when player is busy
- SQLite persistence
- Runtime logs and log export

Primary entrypoint:
- `python -m radioqt`

Primary state file:
- `state/radio_state.db`

Legacy migration:
- If `state/radio_state.json` exists and DB has no data, state is migrated automatically.

## Runtime Entrypoint

Startup path:
1. `radioqt.__main__` -> `radioqt.main.run()`
2. `_configure_multimedia_runtime()`
3. `QApplication(...)`
4. `MainWindow()`
5. `window.show()`

`radioqt/main.py` runtime behavior:
- `RADIOQT_MEDIA_BACKEND=auto` (default): let Qt choose backend
- `RADIOQT_MEDIA_BACKEND=<backend>`: request explicit backend (e.g. `ffmpeg`, `gstreamer`)
- Backend detection scans Qt plugin roots, including common Linux fallbacks:
  - `/usr/lib/qt6/plugins`
  - `/usr/lib/qt/plugins`
- If requested backend is unavailable and FFmpeg plugin exists, fallback to FFmpeg.
- On Linux, hardware decoding is disabled by default unless `RADIOQT_DISABLE_HW_DECODING=0`.

## Repository Map

- `README.md`: setup/run and troubleshooting notes
- `requirements.txt`: runtime dependency (`PySide6>=6.6`)
- `requirements-system.txt`: system multimedia packages

- `radioqt/main.py`: app bootstrap + multimedia env setup
- `radioqt/ui.py`: main window + most runtime orchestration
- `radioqt/player.py`: `QMediaPlayer` wrapper, seek/fade/audio levels
- `radioqt/storage.py`: SQLite persistence + schema/data migrations
- `radioqt/models.py`: dataclasses and schedule status constants
- `radioqt/cron.py`: 6-field CRON parser and iterator

- `radioqt/scheduling/logic.py`: pure schedule computations
- `radioqt/scheduling/runtime.py`: timer scheduler engine
- `radioqt/scheduling/state.py`: startup/play normalization helpers
- `radioqt/scheduling/__init__.py`: exports scheduling APIs
- `radioqt/scheduler.py`: compatibility wrapper to scheduling runtime
- `radioqt/schedule_logic.py`: compatibility wrapper to scheduling logic

- `radioqt/playback/actions.py`: queue/media helper actions
- `radioqt/playback/orchestration.py`: trigger/play decision helpers

- `radioqt/library/sources.py`: source/path/extension helpers
- `radioqt/library/items.py`: selected-media helpers
- `radioqt/library/actions.py`: library state mutations

- `radioqt/ui_components/dialogs.py`: dialogs (`Schedule`, `CRON`, `Settings`, help)
- `radioqt/ui_components/widgets.py`: `WaveformWidget`, `FullscreenOverlay`
- `radioqt/ui_components/tables.py`: table render helpers

## Core Data Model

Defined in `radioqt/models.py`.

### `MediaItem`
- `id`, `title`, `source`, `created_at`

### `ScheduleEntry`
- `id`, `media_id`, `start_at`, `duration`
- `hard_sync`, `fade_in`, `fade_out`
- `status` (`pending` | `disabled` | `fired` | `missed`)
- `one_shot`
- `cron_id`
- overrides:
  - `cron_status_override`
  - `cron_hard_sync_override`
  - `cron_fade_in_override`
  - `cron_fade_out_override`

### `CronEntry`
- `id`, `media_id`, `expression`
- `hard_sync`, `fade_in`, `fade_out`
- `enabled`, `created_at`

### `QueueItem`
- `media_id`
- `source` (`manual` or `schedule`)
- `schedule_entry_id` (optional)

### `LibraryTab`
- `title`, `path`

### `AppState`
- `media_items`, `schedule_entries`, `cron_entries`, `queue`
- `library_tabs`
- `supported_extensions`
- `schedule_auto_focus`
- `logs_visible`
- `fade_in_duration_seconds`
- `fade_out_duration_seconds`
- `duration_probe_cache`

## Persistence (SQLite)

Implemented in `radioqt/storage.py`.

Tables:
- `media_items`
- `schedule_entries`
- `cron_entries`
- `queue_items`
- `app_meta`

Persistence model:
- Save is full rewrite: clear core tables, then insert in-memory state.
- Ordering preserved by `position` columns.

`app_meta` keys currently used:
- `legacy_json_migrated`
- `schedule_auto_focus`
- `logs_visible`
- `library_tabs`
- `supported_extensions`
- `fade_in_duration_seconds`
- `fade_out_duration_seconds`
- `duration_probe_cache`

Notable migrations handled:
- old `enabled` / `fired` -> `status`
- add CRON-related schedule columns and overrides
- add fade flags on schedule/CRON tables
- normalize boolean storage to text `True` / `False`
- rebuild tables if boolean column types are not `TEXT`
- add queue metadata (`source`, `schedule_entry_id`)

## CRON Semantics

Implemented in `radioqt/cron.py`.

Format:
- `second minute hour day-of-month month day-of-week`

Rules:
- 6 fields required
- Supported syntax: `*`, `,`, `-`, `/`
- Day-of-week accepts `0-7`; `7` is normalized to `0` (Sunday)
- If both day-of-month and day-of-week are specific, matching uses OR semantics

Main APIs:
- `CronExpression.parse(raw)`
- `iter_datetimes_on_date(target_date, tzinfo)`
- `next_at_or_after(start)`

## UI Structure

Main window is in `radioqt/ui.py`.

Top-level areas:
- Player display (`QVideoWidget` + waveform fallback)
- Playback controls (`Play`, `Stop`, `Mute`, manual fade in/out, volume slider)
- Media Library panel
- Schedule panel (Date Time + CRON tabs)
- Logs panel (`QPlainTextEdit`, max 2000 lines)

Menu:
- `File -> Settings...`
- `View -> Logs` (toggle visibility)
- `Help -> Export Logs...`
- `Help -> CRON`

### Media Library panel

- Built-in tabs:
  - `Filesystem`
  - `Streamings`
- Additional custom filesystem tabs are configurable in Settings.
- Filesystem filtering is driven by `supported_extensions` from persisted settings.
- Extension filters are applied as case-insensitive glob patterns.
- URL tab supports context menu edit/remove.

### Schedule panel

Date Time table columns:
- `Start Time`, `Duration`, `Media`, `Hard Sync`, `Fade In`, `Fade Out`, `Status`

CRON table columns:
- `CRON`, `Media`, `Hard Sync`, `Fade In`, `Fade Out`, `Status`

Behaviors:
- Date filter by selected day
- Multi-select removal in Date Time table
- CRON-managed rows cannot be removed from Date Time while corresponding CRON entry is enabled
- CRON context menu allows edit/remove
- CRON edit dialog currently changes expression only; hard/fade are changed via table controls
- Auto-focus checkbox: `Focus current program`

## Scheduling Runtime

`radioqt/scheduling/runtime.py`:
- `RadioScheduler` runs a `QTimer` every 500 ms
- Emits `schedule_triggered(entry)` when `now >= start_at` for `pending` entries
- Does not perform collision logic; orchestration is in UI/playback layer

`radioqt/ui.py` CRON runtime window policy:
- Runtime dates: today + tomorrow
- Refresh timer: every 30 seconds
- Lookback window for occurrences: 1 hour
- Max CRON occurrences in memory: 100
- Keep up to 20 recent past occurrences plus upcoming ones
- CRON occurrence IDs are deterministic:
  - `uuid5(NAMESPACE_URL, f"radioqt-cron:{cron_id}:{start_at.isoformat()}")`

## Duration Handling

Duration source:
- Local files only, via external `ffprobe`
- Remote streams are treated as unknown duration

Flow:
- Request from `_media_duration_seconds()`
- Async probe in single-thread `ThreadPoolExecutor`
- Completion dispatched back to UI thread via `_DurationProbeDispatcher`

Caching:
- In-memory per-media cache (`_media_duration_cache`)
- Persistent signature cache (`AppState.duration_probe_cache`)
- Probe key: `<resolved_path>|<mtime_ns>|<size>`
- Max probe cache entries: 2000
- Cache behaves as LRU-like (pop/reinsert on lookup/store)

## Play / Trigger / Queue Rules

Play button (`_on_play_clicked`) high-level flow:
1. Refresh CRON runtime entries
2. Recalculate durations
3. Prepare schedule state (`prepare_schedule_entries_for_play`)
4. If automation was stopped: start automation + scheduler
5. Resolve play request (`resolve_play_request`) with precedence:
   - already playing -> no-op
   - active schedule -> play from computed offset
   - resume loaded media
   - play queue
   - otherwise log compact schedule summary

Schedule trigger (`_on_schedule_triggered`) rules:
- If automation stopped: one-shot `pending` -> `missed`, ignore
- Missing media: one-shot -> `missed`, ignore
- Else one-shot -> `fired`
- If `hard_sync` true or player idle: play now (with start offset)
- Else queue scheduled media

Queue:
- `deque[QueueItem]`, persisted
- Stores origin (`manual` vs `schedule`) + optional `schedule_entry_id`
- On media end, queue playback continues via `_play_next_from_queue()`

Active window semantics:
- End-at is minimum of:
  - `start + duration` (if known)
  - next entry start (if exists)
- So later scheduled entries can truncate current entry window.

## Fade Behavior

Two fade systems exist:

1. Playback-entry fades in `MediaPlayerController`:
- Supports per-entry `fade_in` / `fade_out`
- Fade-in ramps from 0 to current base volume
- Fade-out ramps toward 0 near expected end
- Fade-out only active when expected duration is known

2. Manual volume fade controls in UI:
- `Fade In` and `Fade Out` buttons animate slider value
- Uses configurable durations from Settings
- Tracks last non-zero volume for mute/unmute recovery

## Startup and Shutdown

Startup in `MainWindow`:
- UI is built first; state loading deferred with `QTimer.singleShot(0, ...)`
- Load persisted state
- Rebuild custom library tabs
- Refresh CRON runtime window and durations
- Run startup normalization (`prepare_schedule_entries_for_startup`)
- Apply schedule filter date and optional auto-focus
- Start timers:
  - CRON refresh (30s)
  - schedule auto-focus refresh (1s)

Shutdown:
- set `_shutting_down`
- stop scheduler and fade timer
- shutdown duration probe executor (`cancel_futures=True`)
- save state

## Fullscreen

Handled defensively in `ui.py`:
- Double-click player area toggles fullscreen
- `Esc` exits fullscreen via event filter
- Video uses `QVideoWidget.setFullScreen(True)` when possible
- Audio-only uses `FullscreenOverlay`

## Logging

- Logs are appended as `[HH:MM:SS] message`
- Export from `Help -> Export Logs...`
- Logs visibility toggle is persisted (`logs_visible`)

## Important Operational Details

- `ui.py` remains the highest-risk file for behavior regressions.
- `ConfigurationDialog` enforces valid settings on close/cancel and currently returns accepted when validation passes.
- Missing media removal cascades:
  - removes linked CRON entries
  - removes linked schedule entries
  - removes linked queue items
  - clears current playback if it was that media
- Manual schedule entries created in the past are immediately marked `missed`.
- Active `missed` one-shots may be restored to `pending` by startup/play preparation helpers.

## Known Constraints

- No automated test suite in repo yet.
- `ffprobe` availability is required for local duration probing.
- Stream duration remains unknown in current implementation.
- Scheduler is simple timer-driven logic; state coherence depends on in-memory entries being refreshed correctly.
- Business logic is still concentrated in `ui.py` despite ongoing extraction to `library/`, `playback/`, `scheduling/`, and `ui_components/`.

## Quick Manual Regression Checklist

1. Manual schedule in future (local file)
2. Manual schedule in past (expect `missed`)
3. CRON recurring rule with enabled/disabled toggles
4. Play mid-program (resume from offset)
5. Hard sync on/off during active playback
6. Missing media behavior on trigger and queue
7. Queue fallback with busy player
8. Restart app and verify status normalization/recovery
9. Settings changes:
   - fade duration
   - custom library tabs
   - supported extensions
   - logs visibility
10. Log export and fullscreen behavior

## Next Refactor Priorities

- Continue reducing `ui.py` responsibilities.
- Add automated tests for:
  - CRON parsing and occurrence generation
  - active-entry and end-window computation
  - missed/restore normalization
  - play-from-offset behavior
  - queue and trigger orchestration

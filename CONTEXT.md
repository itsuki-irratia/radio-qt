# RadioQt Context

Working memory for future sessions on this repository.
This document reflects the code as it exists now.

## Project Snapshot

RadioQt is a desktop automation player built with Python + PySide6.

Core capabilities:
- Local file playback and stream URL playback
- Manual datetime schedule entries
- CRON-generated schedule entries
- Queue fallback when player is busy
- SQLite persistence for runtime state
- YAML persistence for app settings
- Runtime logs + log export

Primary entrypoint:
- `python -m radioqt`

Default runtime files:
- State DB: `config/db.sqlite`
- App settings: `config/settings.yaml`

Legacy migration:
- `state/radio_state.db` and `state/radio_state.json` are copied to `config/` when needed.
- Legacy JSON (`config/db.json`) can still be imported into SQLite on first run when DB is empty.

## Runtime Entrypoint

Startup path:
1. `radioqt.__main__` -> `radioqt.main.run()`
2. `_configure_multimedia_runtime()`
3. `QApplication(...)`
4. Load app icon from `radioqt/radioqt.svg` (if available)
5. `MainWindow(config_dir=...)`
6. `window.show()`

`radioqt/main.py` runtime behavior:
- `RADIOQT_MEDIA_BACKEND=auto` (default): let Qt choose backend
- `RADIOQT_MEDIA_BACKEND=<backend>`: request explicit backend (e.g. `ffmpeg`, `gstreamer`)
- Backend detection scans Qt plugin roots, including Linux fallbacks:
  - `/usr/lib/qt6/plugins`
  - `/usr/lib/qt/plugins`
- If requested backend is unavailable and FFmpeg exists, fallback to FFmpeg.
- On Linux, hardware decoding is disabled by default unless `RADIOQT_DISABLE_HW_DECODING=0`.

## Repository Map

- `README.md`: setup/run notes
- `requirements.txt`: Python deps (`PySide6>=6.6`)
- `requirements-system.txt`: system multimedia packages

- `radioqt/main.py`: bootstrap + multimedia env setup + app icon wiring
- `radioqt/radioqt.svg`: app icon asset
- `radioqt/ui.py`: main window + orchestration
- `radioqt/player.py`: `QMediaPlayer` wrapper, fades, audio levels
- `radioqt/storage.py`: SQLite persistence + migrations
- `radioqt/app_config.py`: YAML settings load/save
- `radioqt/models.py`: dataclasses + status constants
- `radioqt/cron.py`: 6-field CRON parser/iterator

- `radioqt/scheduling/logic.py`: pure schedule computations
- `radioqt/scheduling/runtime.py`: timer scheduler engine
- `radioqt/scheduling/state.py`: startup/play normalization helpers
- `radioqt/scheduling/__init__.py`: scheduling exports
- `radioqt/scheduler.py`: compatibility export for runtime scheduler
- `radioqt/schedule_logic.py`: compatibility export for schedule logic

- `radioqt/playback/actions.py`: queue/media helper actions
- `radioqt/playback/orchestration.py`: trigger/play decision logic

- `radioqt/library/sources.py`: source/path/extension helpers
- `radioqt/library/items.py`: selected-media helpers
- `radioqt/library/actions.py`: library mutations

- `radioqt/ui_components/dialogs.py`: dialogs (`Schedule`, `CRON`, `Settings`, help)
- `radioqt/ui_components/tables.py`: table render helpers
- `radioqt/ui_components/widgets.py`: `WaveformWidget`, `FullscreenOverlay`

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
- `library_tabs`, `supported_extensions`
- `schedule_auto_focus`, `logs_visible`
- `fade_in_duration_seconds`, `fade_out_duration_seconds`
- `duration_probe_cache`

Note:
- `AppState` still contains settings-like fields for compatibility/import.
- Current runtime settings source of truth is `config/settings.yaml` (`AppConfig`).

## Persistence

### SQLite (`radioqt/storage.py`)

Tables:
- `media_items`
- `schedule_entries`
- `cron_entries`
- `queue_items`
- `app_meta`

Write model:
- Full rewrite on save (`DELETE` + `INSERT`) for core tables.
- Queue and schedule order preserved by `position`.

`app_meta` keys currently used:
- `legacy_json_migrated`
- `schedule_auto_focus`
- `logs_visible`
- `duration_probe_cache`

Legacy app_meta keys deliberately removed on write:
- `library_tabs`
- `supported_extensions`
- `fade_in_duration_seconds`
- `fade_out_duration_seconds`

Handled migrations include:
- old `enabled` / `fired` -> `status`
- add CRON link/override columns to schedule entries
- add fade flags on schedule/CRON tables
- add queue metadata (`source`, `schedule_entry_id`)
- normalize/rebuild boolean-like columns as `TEXT` (`'True'/'False'`)

### YAML Settings (`radioqt/app_config.py`)

File: `config/settings.yaml`

Current keys:
- `fade` (shared fade duration in seconds)
- `library_tabs`
- `supported_extensions`

Backward compatibility:
- Can read legacy `fade_in_duration_seconds` / `fade_out_duration_seconds`.
- If `settings.yaml` does not exist, it is seeded from legacy DB values via `AppState`.

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
- `View -> Logs`
- `Help -> Export Logs...`
- `Help -> CRON`

### Media Library

Tabs:
- `Filesystem`
- `Streamings`
- Optional custom filesystem tabs from Settings

Behavior:
- Filesystem filter uses configured extensions
- Streams table supports context menu edit/remove
- Add stream via `Add Streaming` button

### Schedule Tables

Date Time columns:
- `Start Time`, `Duration`, `Media`, `Fade In`, `Fade Out`, `Status`

CRON columns:
- `CRON`, `Media`, `Fade In`, `Fade Out`, `Status`

Important:
- `hard_sync` is always enforced in code and not exposed in table columns.

Other behaviors:
- Date filter by selected day
- Multi-select remove in Date Time table
- Active CRON-generated Date Time rows cannot be removed while CRON rule is enabled
- CRON context menu supports edit/remove
- CRON edit dialog currently edits expression only (`expression_only=True`)
- Optional auto-focus (`Focus current program`) updates every second

## Scheduling Runtime

`radioqt/scheduling/runtime.py`:
- `RadioScheduler` ticks every 500 ms
- Emits `schedule_triggered(entry)` for `pending` entries where `now >= start_at`
- Collision/window logic is not in scheduler; it is handled by scheduling/playback logic

CRON runtime window in `ui.py`:
- Runtime dates: today + tomorrow
- Refresh timer: every 30 seconds
- Lookback for occurrence generation: 1 hour
- Max CRON occurrences kept in memory: 100
- Keep up to 20 recent past occurrences + upcoming ones
- Deterministic occurrence IDs:
  - `uuid5(NAMESPACE_URL, f"radioqt-cron:{cron_id}:{start_at.isoformat()}")`

## Play / Trigger / Queue Rules

Play button (`_on_play_clicked`) flow:
1. Refresh CRON runtime entries
2. Recalculate durations
3. Prepare schedule state (`prepare_schedule_entries_for_play`)
4. Start automation + scheduler if needed
5. Resolve play request (`resolve_play_request`) with precedence:
   - already playing -> no-op
   - active schedule -> play from computed offset
   - resume loaded media
   - play queue
   - else log schedule summary

Schedule trigger (`_on_schedule_triggered`) flow:
- If automation stopped: one-shot `pending` -> `missed`, ignore
- Missing media: one-shot -> `missed`, ignore
- Else one-shot -> `fired`
- If `hard_sync` true or player idle -> play now (with offset)
- Else queue scheduled media

Queue behavior:
- `deque[QueueItem]`, persisted
- Keeps source (`manual` / `schedule`) + optional `schedule_entry_id`
- On media finish, `_play_next_from_queue()` continues playback
- Missing queued media are skipped with log count

## Duration Handling

Source of duration:
- Local files only, via `ffprobe`
- Streams/remote URLs are unknown duration

Flow:
- `_media_duration_seconds()` checks cache, then async probe if needed
- Probe runs in single-thread `ThreadPoolExecutor`
- Result is marshaled to UI thread via `_DurationProbeDispatcher`

Caching:
- Per-media cache (`_media_duration_cache`)
- Persistent signature cache (`duration_probe_cache`)
- Cache key: `<resolved_path>|<mtime_ns>|<size>`
- Max entries: 2000
- LRU-like behavior via pop/reinsert

## Fade Behavior

Two separate fade systems exist:

1. Playback-entry fades (`MediaPlayerController`):
- Per-entry `fade_in` / `fade_out`
- Effective volume = slider base volume * fade multiplier
- Fade-out requires an `expected_duration_ms`
- `expected_duration_ms` comes from schedule window in UI:
  - end = min(`start + media duration`, `next schedule start`)
  - this allows fade-out on overlaps/truncations
- For streams/backends with static `QMediaPlayer.position()`, fade progression falls back to a wall-clock timeline (`QTimer` tick)

2. Manual volume fades (UI buttons):
- `Fade In` / `Fade Out` animate slider value
- Uses configured shared fade duration
- Keeps last non-zero volume for mute/unmute recovery

## Startup and Shutdown

Startup:
- Build UI first, then deferred load (`QTimer.singleShot(0, _finish_startup_load)`)
- Migrate/copy legacy state paths if needed
- Load SQLite state + YAML settings
- Enforce hard sync on all schedule/CRON entries
- Refresh CRON runtime entries and durations
- Normalize missed/restored one-shot entries (`prepare_schedule_entries_for_startup`)
- Refresh tables, scheduler entries, visuals
- Start timers:
  - CRON runtime refresh (30s)
  - schedule auto-focus refresh (1s)

Shutdown:
- Set `_shutting_down`
- Stop scheduler and UI volume fade timer
- Shutdown duration probe executor (`cancel_futures=True`)
- Save settings and state

## Fullscreen, Icon, and Visuals

Fullscreen handling:
- Double-click video/waveform/overlay toggles fullscreen
- `Esc` exits fullscreen through event filter
- Video prefers `QVideoWidget.setFullScreen(True)`
- Audio-only uses `FullscreenOverlay`

App icon:
- Loaded from `radioqt/radioqt.svg`
- Applied to both `QApplication` and `MainWindow` in `main.py`

Waveform:
- `WaveformWidget` shows bar visualization only (no title/subtitle text overlay)
- Uses decaying smoothed levels from `QAudioBufferOutput`

## Logging

- Log format: `[HH:MM:SS] message`
- Log view max: 2000 lines
- Export path chosen from `Help -> Export Logs...`
- Logs visibility persists (`logs_visible`)

## Important Operational Details

- `ui.py` is still the highest-risk file for behavior regressions.
- Hard sync is normalized/enforced to always true for all schedule/CRON entries.
- Schedule entries created in the past are immediately marked `missed`.
- Active `missed` one-shots may be restored to `pending` by preparation helpers.
- Removing media cascades to linked CRON entries, schedule entries, queue entries, and current playback.
- `ConfigurationDialog` accepts on close/reject after validation (non-standard but intentional in current code).

## Known Constraints

- No automated test suite in repo yet.
- `ffprobe` availability is required for local duration probing.
- Stream duration remains unknown.
- Most orchestration complexity is still concentrated in `ui.py`.

## Quick Manual Regression Checklist

1. Manual schedule in future (local file)
2. Manual schedule in past (`missed`)
3. CRON rule add/edit/remove + enabled/disabled toggles
4. Play mid-program (offset resume)
5. Overlap fade-out: local->local, local->stream, stream->local
6. Missing media behavior on trigger and queue
7. Queue fallback while player is busy
8. Restart app and verify normalization/recovery
9. Settings changes:
   - shared fade duration
   - custom library tabs
   - supported extensions
10. Log export + fullscreen
11. App icon visible from `radioqt.svg`

## Next Refactor Priorities

- Continue extracting orchestration logic out of `ui.py`.
- Add automated tests for:
  - CRON parsing/occurrence generation
  - active entry + end window computations
  - missed/restore normalization
  - trigger/play/queue orchestration
  - fade expected-duration calculations on overlapping schedules

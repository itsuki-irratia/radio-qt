# RadioQt Context

Working memory for future sessions on this repository.
This document reflects the current codebase after rollback.

## Project Snapshot

RadioQt is a desktop automation player built with Python + PySide6.

Current functional scope:
- Single timeline/runtime (no multi-timeline architecture)
- Local file playback and stream URL playback
- DateTime schedule entries
- CRON-generated schedule entries (6 fields, with seconds)
- Queue fallback when playback is busy
- Per-entry fade in/out support
- Shared manual volume fade controls
- SQLite persistence for runtime state
- YAML persistence for user settings
- Runtime logs + log export

Primary entrypoint:
- `python -m radioqt`

Default runtime files:
- State DB: `config/db.sqlite`
- App settings: `config/settings.yaml`

## Startup, Paths, and Auto-Recreation

Main startup path:
1. `radioqt.__main__` -> `radioqt.main.run()`
2. `_configure_multimedia_runtime()`
3. `QApplication(...)`
4. Load app icon from `radioqt/radioqt.svg` (if available)
5. `MainWindow(config_dir=...)`
6. Deferred state load via `QTimer.singleShot(0, _finish_startup_load)`

Important recreation behavior (when `config/` does not exist):
- SQLite path parent is created automatically in `load_state()` / `save_state()`.
- DB schema is auto-created (`_ensure_schema`).
- `settings.yaml` is created automatically by `_load_or_initialize_app_config()`.
- If YAML is missing, initial YAML is seeded from DB/legacy state values.

Legacy migration behavior:
- If `config/db.sqlite` is missing and `state/radio_state.db` exists, it is copied to `config/db.sqlite`.
- If DB is missing and `state/radio_state.json` exists, it is copied to `config/db.json` equivalent (`db.sqlite` sibling with `.json`).
- On first DB load, if SQLite tables are empty and legacy JSON exists, JSON is imported.

## Runtime Backend Configuration (`radioqt/main.py`)

Environment knobs:
- `RADIOQT_MEDIA_BACKEND=auto` (default): Qt chooses backend
- `RADIOQT_MEDIA_BACKEND=<backend>`: request explicit backend (`ffmpeg`, `gstreamer`, ...)
- `RADIOQT_DISABLE_HW_DECODING` (Linux):
  - default behavior disables FFmpeg HW decode (`QT_FFMPEG_DECODING_HW_DEVICE_TYPES=""`)
  - set `RADIOQT_DISABLE_HW_DECODING=0` to opt back in

Qt plugin root detection includes:
- `QLibraryInfo.PluginsPath`
- `QT_PLUGIN_PATH`
- Linux fallbacks: `/usr/lib/qt6/plugins`, `/usr/lib/qt/plugins`

If requested backend is unavailable and FFmpeg plugin exists, runtime falls back to FFmpeg.

## Repository Map

Top-level:
- `README.md`: usage and Linux troubleshooting
- `requirements.txt`: Python deps (`PySide6>=6.6`)
- `requirements-system.txt`: distro multimedia packages
- `CONTEXT.md`: this file

Core modules:
- `radioqt/main.py`: app bootstrap and multimedia env setup
- `radioqt/ui.py`: main window and orchestration (largest/highest-risk file)
- `radioqt/player.py`: media player wrapper and fade engine
- `radioqt/storage.py`: SQLite read/write + migrations
- `radioqt/app_config.py`: custom YAML parser/dumper for app settings
- `radioqt/models.py`: dataclasses and schedule status constants
- `radioqt/cron.py`: CRON parser and next-occurrence logic

Scheduling package:
- `radioqt/scheduling/logic.py`: pure schedule computations
- `radioqt/scheduling/runtime.py`: `RadioScheduler` tick engine
- `radioqt/scheduling/state.py`: startup/play normalization helpers
- `radioqt/scheduling/__init__.py`: exports
- `radioqt/scheduler.py`: compatibility re-export for `RadioScheduler`
- `radioqt/schedule_logic.py`: compatibility re-export for scheduling logic

Playback package:
- `radioqt/playback/actions.py`: queue/media helpers
- `radioqt/playback/orchestration.py`: trigger/play decision logic

Library package:
- `radioqt/library/sources.py`: media source and extension helpers
- `radioqt/library/items.py`: selected-media helpers
- `radioqt/library/actions.py`: stream add/update and media removal cascade

UI components:
- `radioqt/ui_components/dialogs.py`: Schedule/CRON/Settings dialogs
- `radioqt/ui_components/tables.py`: table rendering helpers
- `radioqt/ui_components/widgets.py`: waveform and fullscreen overlay widgets

Assets:
- `radioqt/radioqt.svg`: app icon

## Data Model (`radioqt/models.py`)

### `MediaItem`
- `id`, `title`, `source`, `created_at`

### `CronEntry`
- `id`, `media_id`, `expression`
- `hard_sync`, `fade_in`, `fade_out`
- `enabled`, `created_at`

### `ScheduleEntry`
- `id`, `media_id`, `start_at`, `duration`
- `hard_sync`, `fade_in`, `fade_out`
- `status` (`pending` | `disabled` | `fired` | `missed`)
- `one_shot`
- CRON linkage and overrides:
  - `cron_id`
  - `cron_status_override`
  - `cron_hard_sync_override`
  - `cron_fade_in_override`
  - `cron_fade_out_override`

### `QueueItem`
- `media_id`
- `source` (`manual` or `schedule`)
- `schedule_entry_id` (optional)

### `LibraryTab`
- `title`, `path`

### `AppState`
- Runtime persisted payload:
  - `media_items`, `schedule_entries`, `cron_entries`, `queue`
  - compatibility fields: `library_tabs`, `supported_extensions`
  - UI state: `schedule_auto_focus`, `logs_visible`
  - compatibility fade fields: `fade_in_duration_seconds`, `fade_out_duration_seconds`
  - `duration_probe_cache`

Note:
- Current settings source of truth is YAML (`AppConfig`), not `AppState`.

## SQLite Persistence (`radioqt/storage.py`)

Tables:
- `media_items`
- `schedule_entries`
- `cron_entries`
- `queue_items`
- `app_meta`

Write model:
- Full table rewrite on save (`DELETE` then `INSERT`) for core data tables.
- Ordering is preserved by `position` columns.

`app_meta` currently used:
- `legacy_json_migrated`
- `schedule_auto_focus`
- `logs_visible`
- `duration_probe_cache`

Deprecated app_meta keys explicitly removed on write:
- `library_tabs`
- `supported_extensions`
- `fade_in_duration_seconds`
- `fade_out_duration_seconds`

Migrations handled:
- old `enabled/fired` schedule columns -> `status`
- add CRON link/override columns on `schedule_entries`
- add fade flags on schedule/CRON tables
- add queue metadata (`source`, `schedule_entry_id`)
- normalize boolean-like values to textual `'True'/'False'`
- rebuild boolean-typed columns as `TEXT` when needed

## Settings YAML (`radioqt/app_config.py`)

Path:
- `config/settings.yaml`

Current canonical keys:
- `fade` (shared fade duration seconds for in/out)
- `font.size` (global app point size)
- `library_tabs`
- `supported_extensions`

Backward compatibility supported on load:
- `fade_in_duration_seconds` and `fade_out_duration_seconds`
- legacy flat `font_size`

Implementation details:
- Uses a custom lightweight parser/dumper (not PyYAML).
- `save_app_config()` ensures parent directory exists.

## UI Overview (`radioqt/ui.py`)

Main areas:
- Player display:
  - `QVideoWidget` when source appears video-like
  - `WaveformWidget` for audio-like/unknown sources
- Playback controls:
  - Play, Stop, Mute, manual Fade In, manual Fade Out, Volume slider
- Media Library panel:
  - `Filesystem` tab
  - `Streams` tab
  - optional custom filesystem tabs from settings
- Schedule panel (`QTabWidget`):
  - `Date Time` tab (schedule table)
  - `CRON` tab (rule table)
- Logs panel (`QPlainTextEdit`, max 2000 lines)

Menu:
- `File -> Settings...`
- `View -> Logs`
- `Help -> Export Logs...`
- `Help -> CRON`

Current visual note:
- CRON tab has a yellow square marker on the tab side (`_make_tab_marker`, 8x8, no border-radius).

Waveform note:
- Waveform widget renders bars only (title/subtitle text overlays removed).

## Schedule + CRON Behavior

### Date Time table columns
- `Start Time`, `Duration`, `Media`, `Fade In`, `Fade Out`, `Status`

### CRON table columns
- `CRON`, `Media`, `Fade In`, `Fade Out`, `Status`

### CRON runtime window
- Refresh timer: every 30 seconds
- Runtime dates: today + tomorrow
- Lookback for generated occurrences: 1 hour
- Max retained generated occurrences in memory: 100
- Keeps up to 20 recent past occurrences + upcoming ones
- Deterministic generated IDs:
  - `uuid5(NAMESPACE_URL, "radioqt-cron:{cron_id}:{start_iso}")`

### Entry status handling
- One-shot entries in the past can become `missed`.
- Startup and Play actions run normalization helpers:
  - restore active missed entries to `pending`
  - mark overdue one-shot entries as `missed` when appropriate

### Hard sync policy
- UI/runtime enforce hard sync always-on (`_enforce_hard_sync_always`).
- Hard sync controls are not exposed in schedule/cron tables.

### Removal protection
- CRON-generated schedule rows tied to enabled CRON rules cannot be deleted from Date Time tab.

## Playback / Queue / Trigger Orchestration

### Scheduler
- `RadioScheduler` ticks every 500 ms.
- Emits `schedule_triggered(entry)` when `entry.status == pending` and `now >= start_at`.

### On schedule trigger
- If automation stopped:
  - one-shot pending entry -> `missed`, no playback
- If media missing:
  - one-shot entry -> `missed`, no playback
- Else one-shot entries transition to `fired`
- If hard sync or player idle:
  - play immediately (possibly with offset)
- Else:
  - queue as scheduled item

### Queue behavior
- Queue type: `deque[QueueItem]`
- Queue entries remember source (`manual` / `schedule`) and optional `schedule_entry_id`
- On media end, next playable queue item starts automatically
- Missing queued media are skipped with log message

## Duration + Fade Details

### Duration detection
- Local files only, via `ffprobe`
- Streams/remote URLs remain unknown duration
- Probe execution:
  - single-thread `ThreadPoolExecutor`
  - async callback via `_DurationProbeDispatcher` signal into UI thread

### Duration caches
- Per-media cache: `_media_duration_cache`
- Persistent signature cache: `_duration_probe_cache`
- Persistent key format: `<resolved_path>|<mtime_ns>|<size>`
- Max persistent cache entries: 2000 (LRU-like pop/reinsert)

### Fade systems
1. Playback-entry fades in `MediaPlayerController`:
- Uses entry `fade_in` / `fade_out`
- Effective volume = slider base volume * fade multiplier
- Fade out only active when `expected_duration_ms` is known
- `expected_duration_ms` is computed from schedule window:
  - end = min(media-duration-end, next-schedule-start)
- Stream/static-position fallback:
  - fade timeline advances by wall clock when backend does not advance `position()`

2. Manual slider fades from UI buttons:
- `Fade In`/`Fade Out` animate slider value over shared configured duration
- Keeps last non-zero volume for mute recovery

## Fullscreen and Visual Handling

- Double-click on video/waveform/overlay toggles fullscreen.
- `Esc` exits fullscreen via event filter.
- Video-like media prefers `QVideoWidget` fullscreen.
- Audio-only fullscreen uses `FullscreenOverlay`.

## Configuration Dialog Behavior

`ConfigurationDialog` sections:
- `General Settings`
- `Custom Paths`
- `Extensions`

Editable values:
- Shared fade duration seconds
- Global font size (pt)
- Custom filesystem tabs
- Supported extensions

Important UX behavior currently in code:
- `reject()` validates and then calls `accept()` (non-standard cancel semantics).
- `closeEvent` also validates and accepts.

## Logging and Export

- Log format: `[HH:MM:SS] message`
- Log view max blocks: 2000
- Export via `Help -> Export Logs...`
- Log panel visibility persisted in DB

## Known Constraints / Risks

- No automated test suite in repository.
- `ui.py` remains large and central to most behavior.
- `ffprobe` is required for reliable local duration probing.
- Stream duration remains unknown.
- CRON weekday values are strict `1-7 (Mon-Sun)`; legacy expressions using `0` are invalid.

## Manual Regression Checklist

1. Start app with no `config/` directory and verify DB + YAML auto-create.
2. Add local file, schedule future DateTime entry, run automation.
3. Add past DateTime one-shot and verify `missed` normalization.
4. Add/edit/remove CRON rules and toggle enabled/disabled.
5. Validate overlap fade-out paths:
   - local -> local
   - local -> stream
   - stream -> local
6. Verify queue fallback when player is busy.
7. Remove media and confirm cascade cleanup (CRON/schedule/queue/current playback).
8. Restart app and confirm state/settings persistence + startup normalization.
9. Change settings (fade/font/tabs/extensions) and verify persistence.
10. Check fullscreen behavior (video/audio) and Esc exit.
11. Check app icon loads from `radioqt/radioqt.svg`.

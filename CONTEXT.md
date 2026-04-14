# RadioQt Context

Working memory for future sessions on this repository.
Last updated: 2026-04-14.

## Project Snapshot

RadioQt is a desktop radio automation player built with Python + PySide6.

Current scope:
- Single runtime timeline (no multi-timeline architecture)
- Local file and stream URL playback
- DateTime schedule entries
- CRON-generated entries (6 fields with seconds)
- Queue fallback when player is busy
- Per-entry fade flags (`fade_in` / `fade_out`)
- Shared fade duration (same value for fade-in and fade-out seconds)
- Greenwich Time Signal playback at minute boundaries
- SQLite persistence for runtime data
- YAML persistence for UI/settings data
- Runtime log panel + export

Entrypoint:
- `python -m radioqt`

Default runtime files:
- SQLite state: `config/db.sqlite`
- Settings YAML: `config/settings.yaml`

## Startup / Loading Flow

Main startup path:
1. `radioqt.__main__` -> `radioqt.main.run()`
2. `_configure_multimedia_runtime()`
3. `QApplication(...)`
4. Optional app icon from `radioqt/radioqt.svg`
5. `MainWindow(config_dir=...)`
6. Deferred load via `QTimer.singleShot(0, _finish_startup_load)`

Main window startup work:
- Migrates legacy DB/JSON location when needed
- Loads SQLite state (`load_state`)
- Loads/initializes YAML settings (`_load_or_initialize_app_config`)
- Applies panel split, font, fade defaults, custom tabs, extensions
- Refreshes CRON runtime window and schedule durations
- Normalizes/restores one-shot statuses for startup
- Starts timers:
  - CRON refresh (30s)
  - Schedule auto-focus timer (1s)
  - Greenwich timer (next minute boundary)

## Legacy / Migration Behavior

When `config/` is missing:
- Parent directories are created automatically
- SQLite schema is auto-created
- `settings.yaml` is auto-created

Legacy compatibility:
- If `config/db.sqlite` is missing and `state/radio_state.db` exists, DB is copied
- If DB is missing and `state/radio_state.json` exists, JSON is copied to sibling `db.json` path for import flow
- On first load, legacy JSON is imported only when DB is empty and migration flag is not set

DB migrations include:
- `enabled/fired` -> `status`
- CRON linkage/override columns on `schedule_entries`
- Fade columns on schedule/cron tables
- Greenwich stream flag on `media_items`
- Queue metadata (`source`, `schedule_entry_id`)
- Boolean normalization to textual `True`/`False`
- Rebuild of boolean columns as `TEXT` when needed

## Runtime Backend Configuration (`radioqt/main/runtime.py`)

Environment controls:
- `RADIOQT_MEDIA_BACKEND=auto` (default): Qt auto selection
- `RADIOQT_MEDIA_BACKEND=<backend>`: force backend (`ffmpeg`, `gstreamer`, ...)
- `RADIOQT_DISABLE_HW_DECODING` (Linux):
  - default disables FFmpeg HW decode (`QT_FFMPEG_DECODING_HW_DEVICE_TYPES=""`)
  - set `RADIOQT_DISABLE_HW_DECODING=0` to re-enable

Qt plugin roots considered:
- `QLibraryInfo.PluginsPath`
- `QT_PLUGIN_PATH`
- Linux fallbacks: `/usr/lib/qt6/plugins`, `/usr/lib/qt/plugins`

If requested backend is unavailable and FFmpeg plugin exists, fallback is FFmpeg.

## Repository Map

Top-level:
- `README.md`: setup/run + Linux multimedia notes
- `requirements.txt`: `PySide6>=6.6`
- `requirements-system.txt`: distro multimedia packages
- `CONTEXT.md`: this file

Core runtime:
- `radioqt/main/application.py`: app bootstrap and `run()` entry wiring
- `radioqt/main/runtime.py`: multimedia runtime env configuration
- `radioqt/main/cli.py`: CLI parsing (`--config`)
- `radioqt/ui/main_window.py`: main window + orchestration
- `radioqt/ui/handlers.py`: UI action handlers (library/schedule/CRON/settings interactions)
- `radioqt/ui/playback_handlers.py`: playback/scheduler trigger handlers
- `radioqt/ui/library_selection.py`: library/schedule/CRON table refresh and selection helpers mixin
- `radioqt/ui/layout_builders.py`: menu/layout/panel construction and filesystem-tab extension helpers mixin
- `radioqt/ui/interaction_runtime.py`: runtime signal wiring and fullscreen keyboard/mouse event-filter mixin
- `radioqt/ui/state_persistence.py`: state/settings persistence and startup load mixin
- `radioqt/ui/settings_logging.py`: settings dialog and log actions mixin
- `radioqt/ui/fullscreen_visuals.py`: fullscreen behavior and visual icon helpers mixin
- `radioqt/ui/schedule_timeline.py`: schedule/timeline runtime, coloring, focus and duration-probe mixin
- `radioqt/player/controller.py`: media player wrapper + fade engine
- `radioqt/storage/io.py`: storage load/save orchestration
- `radioqt/storage/schema.py`: SQLite connection/schema bootstrap
- `radioqt/storage/migrations.py`: schema/data migrations
- `radioqt/storage/read.py`: DB -> `AppState`
- `radioqt/storage/write.py`: `AppState` -> DB
- `radioqt/storage/sqlite_store.py`: compatibility facade/re-exports
- `radioqt/app_config/schema.py`: settings dataclass and dict conversion
- `radioqt/app_config/parser.py`: YAML parsing (with legacy key support)
- `radioqt/app_config/serializer.py`: canonical YAML dump
- `radioqt/app_config/io.py`: file load/save
- `radioqt/app_config/core.py`: compatibility facade/re-exports
- `radioqt/models/entities.py`: dataclasses/constants
- `radioqt/cron/expression.py`: CRON parser and matching
- `radioqt/duration_probe/cache.py`: probe cache helpers and cache-key generation
- `radioqt/duration_probe/ffprobe.py`: ffprobe-backed duration probing
- `radioqt/duration_probe/probe.py`: compatibility facade/re-exports

Packages:
- `radioqt/scheduling/*`: scheduling logic, runtime, mutations, presentation, CRON runtime
- `radioqt/playback/*`: queue actions + play decision orchestration
- `radioqt/library/*`: source helpers + media actions
- `radioqt/ui_components/*`: dialogs, tables, widgets

Compatibility re-exports:
- `radioqt/scheduler/__init__.py`
- `radioqt/schedule_logic/__init__.py`

## Data Models (`radioqt/models/entities.py`)

- `MediaItem`: `id`, `title`, `source`, `greenwich_time_signal_enabled`, `created_at`
- `CronEntry`: `id`, `media_id`, `expression`, `hard_sync`, `fade_in`, `fade_out`, `enabled`, `created_at`
- `ScheduleEntry`:
  - core: `id`, `media_id`, `start_at`, `duration`, `hard_sync`, `fade_in`, `fade_out`, `status`, `one_shot`
  - CRON link/overrides: `cron_id`, `cron_status_override`, `cron_hard_sync_override`, `cron_fade_in_override`, `cron_fade_out_override`
- `QueueItem`: `media_id`, `source`, `schedule_entry_id`
- `LibraryTab`: `title`, `path`
- `AppState`: runtime persistence payload (includes legacy-compatible fields like library tabs/extensions/fade durations)

## Persistence

### SQLite (`radioqt/storage/io.py`, `schema.py`, `migrations.py`, `read.py`, `write.py`)

Tables:
- `media_items`
- `schedule_entries`
- `cron_entries`
- `queue_items`
- `app_meta`

Write strategy:
- Full rewrite of core tables (`DELETE` + bulk `INSERT`)
- `app_meta` currently stores:
  - `schedule_auto_focus`
  - `logs_visible`
  - `duration_probe_cache`
  - `legacy_json_migrated`

Deprecated app_meta keys removed on write:
- `library_tabs`
- `supported_extensions`
- `fade_in_duration_seconds`
- `fade_out_duration_seconds`

### Settings YAML (`radioqt/app_config/schema.py`, `parser.py`, `serializer.py`, `io.py`)

Canonical YAML structure:
- `view.font_size`
- `view.media_library_width_percent`
- `view.schedule_width_percent`
- `fade.seconds`
- `fade.filesystem.default_fade_in`
- `fade.filesystem.default_fade_out`
- `fade.streams.default_fade_in`
- `fade.streams.default_fade_out`
- `greenwich_time_signal.enabled`
- `greenwich_time_signal.path`
- `custom_paths.tabs[]`
- `extensions.supported[]`

Backward-compatible parsing still supports legacy flat keys (`font_size`, `fade_in_duration_seconds`, etc.), but dumps only canonical structure.

## UI Overview

Main areas:
- Player display: `QVideoWidget` (video-like) or `WaveformWidget` (audio-like)
- Playback controls: Play, Stop, Mute, manual Fade In/Out, volume slider
- Media Library:
  - `Filesystem` tab
  - `Streams` tab
  - extra custom filesystem tabs from settings
- Schedule panel:
  - `Date Time`
  - `CRON`
- Logs panel (`QPlainTextEdit`, max 2000 lines)

Menu:
- `File -> Settings...`
- `View -> Logs`
- `Help -> Export Logs...`
- `Help -> CRON`

## Settings Dialog (`ConfigurationDialog`)

Sections (alphabetical and synced with pages):
- `Custom Paths`
- `Extensions`
- `Fade`
- `Greenwich Time Signal`
- `View`

Notable UX behavior:
- Boolean selectors (`True`/`False`) are color-coded:
  - `True`: green palette
  - `False`: red palette
- `Fade` uses one shared seconds value for in/out
- View panel width controls are on the same row:
  - `Media Library` and `Schedule`
  - each range 10..90
  - values auto-adjust so sum is always 100
- Custom Paths and Extensions use square `+` / `-` buttons
- Each custom path row has path editor + `Browse...`
- Greenwich audio path is validated as existing file

Important current semantics:
- `reject()` validates and then calls `accept()`
- `closeEvent` also validates and accepts

## Scheduling / CRON Behavior

Date Time table columns:
- `Start Time`, `Duration`, `Media`, `Fade In`, `Fade Out`, `Status`

CRON table columns:
- `CRON`, `Media`, `Fade In`, `Fade Out`, `Status`

Runtime CRON window:
- Dates kept in runtime: today + tomorrow
- Lookback: 1 hour
- Max occurrences in memory: 100
- Keeps up to 20 recent past occurrences + upcoming ones
- Deterministic generated IDs:
  - `uuid5(NAMESPACE_URL, "radioqt-cron:{cron_id}:{start_iso}")`

Status handling:
- Past one-shot entries can be normalized to `missed`
- Active missed one-shot entries can be restored to `pending`
- Hard sync is enforced as always-on in runtime UI logic

Protection rules:
- Enabled CRON-generated DateTime rows are protected from direct removal in DateTime tab

## Playback / Queue Behavior

- `RadioScheduler` ticks every 500 ms
- Trigger flow:
  - if automation stopped: one-shot pending -> `missed`
  - missing media: one-shot -> `missed`
  - one-shot played -> `fired`
  - if hard-sync or idle -> play now
  - else -> enqueue scheduled item
- Queue stores source metadata (`manual`/`schedule`)
- On media end, next playable queue item starts automatically
- Missing queued media are skipped and logged

Stop behavior:
- `Stop` disables automation, stops scheduler, stops Greenwich signal player, and clears active media

## Focus / Timeline Behavior

- Clicking a timeline row disables `Focus current program`
- Manual schedule date change also disables `Focus current program`
- Checkbox state is persisted in DB

## Duration + Fades

Duration probing:
- Local files only via `ffprobe`
- Streams keep unknown duration
- Probe runs in single-thread executor with UI-thread signal dispatch

Duration caches:
- In-memory media cache by media id
- Persistent signature cache in DB metadata
- Cache key: `<resolved_path>|<mtime_ns>|<size>`
- Max persistent entries: 2000 (LRU-like behavior via pop/reinsert)

Fade systems:
1. Entry playback fades in `MediaPlayerController`
- Uses per-entry fade flags
- Effective output volume = slider base volume * fade multiplier
- Fade-out applies when expected duration is known

2. Manual volume fades in UI
- Fade In / Fade Out buttons animate the volume slider over shared configured duration
- Keeps last non-zero volume for mute recovery

## Known Constraints

- No automated tests in repository
- `ui/main_window.py` is still the largest/high-risk integration file
- `ffprobe` should be available for reliable local duration probing
- Stream duration remains unknown
- CRON day-of-week is strict `1-7` (Mon-Sun); `0` is invalid

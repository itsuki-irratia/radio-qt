# RadioQt CLI

Complete documentation for the `radioqt-cli` executable.

## Requirements

- Have the project installed in editable mode:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

- Confirm that the command exists:

```bash
radioqt-cli --help
```

## Data paths and global option

By default, the CLI uses:

- SQLite database: `$HOME/.config/radioqt/db.sqlite`
- YAML config: `$HOME/.config/radioqt/settings.yaml`
- Runtime status file: `$HOME/.config/radioqt/radioqt.lock`
- Runtime log file: `$HOME/.config/radioqt/runtime.log`
- Icecast relay PID file: `$HOME/.config/radioqt/icecast.pid`
- Icecast relay stdout log: `$HOME/.config/radioqt/icecast.stdout.log`
- Icecast relay stderr log: `$HOME/.config/radioqt/icecast.stderr.log`

Global option:

```bash
radioqt-cli --config "/path/to/my-config" <command>
```

Example:

```bash
radioqt-cli --config "/home/user/radioqt-prod" schedule list --all
```

Machine-readable output for scripts/SSH automation:

```bash
radioqt-cli --json --config "/home/user/radioqt-prod" schedule list --all
```

## Important formats

### Date and time for `schedule add/edit --start`

Use ISO 8601.

With explicit timezone (recommended):

```text
2026-04-16T18:00:00+02:00
```

Without timezone:

```text
2026-04-16T18:00:00
```

If you do not include a timezone, RadioQt applies your local system timezone.

### Date for filters (`schedule list --date`)

Format:

```text
YYYY-MM-DD
```

Example:

```text
2026-04-16
```

### CRON format

RadioQt uses 6 fields:

```text
second minute hour day-of-month month day-of-week
```

Example: every 15 minutes:

```text
0 */15 * * * *
```

## Recommended quick flow

1. List media and copy `MEDIA_ID`:

```bash
radioqt-cli media list
```

2. Create a one-shot schedule entry:

```bash
radioqt-cli schedule add \
  --media-id "MEDIA_ID_HERE" \
  --start "2026-04-16T18:00:00+02:00" \
  --fade-in
```

3. Check created schedule entries:

```bash
radioqt-cli schedule list --all
```

4. Create a CRON rule:

```bash
radioqt-cli cron add \
  --media-id "MEDIA_ID_HERE" \
  --expression "0 */15 * * * *" \
  --fade-in
```

5. Check CRON rules:

```bash
radioqt-cli cron list
```

## Command reference

### `settings`

Read and update `settings.yaml` safely from CLI.

#### `settings get`

Print all supported settings:

```bash
radioqt-cli settings get
```

Print one setting:

```bash
radioqt-cli settings get fade_seconds
radioqt-cli settings get default_volume_percent
```

JSON mode:

```bash
radioqt-cli --json settings get
```

#### `settings set`

Update one setting value:

```bash
radioqt-cli settings set fade_seconds 8
radioqt-cli settings set default_volume_percent 72
radioqt-cli settings set filesystem_default_fade_in true
radioqt-cli settings set supported_extensions "mp3,ogg,webm"
radioqt-cli settings set library_tabs '[{"title":"Studio","path":"/srv/radio/studio"}]'
```

Supported setting keys:

- `fade_seconds`
- `filesystem_default_fade_in`
- `filesystem_default_fade_out`
- `streams_default_fade_in`
- `streams_default_fade_out`
- `default_volume_percent`
- `font_size` (use `none`/`null`/`auto` to reset)
- `media_library_width_percent`
- `schedule_width_percent`
- `greenwich_time_signal_enabled`
- `greenwich_time_signal_path`
- `icecast_status` (`true`/`false`)
- `icecast_run_in_background` (`true`/`false`, default: `false`)
- `icecast_command` (auto-generated command result; supports extra args suffix)
- `icecast_input_format` (default: `pulse`)
- `icecast_thread_queue_size` (default: `4096`)
- `icecast_device` (Pulse source/monitor; required for Pulse capture)
- `icecast_audio_channels` (default: `2`)
- `icecast_audio_rate` (default: `48000`)
- `icecast_audio_codec` (default: `libmp3lame`)
- `icecast_audio_bitrate` (kbps, default: `128`)
- `icecast_content_type` (default: `audio/mpeg`)
- `icecast_output_format` (default: `mp3`)
- `icecast_url` (default: `icecast://source:hackme@localhost:8000/radio.mp3`)
- `supported_extensions` (CSV or JSON list)
- `library_tabs` (JSON array of objects with `title` and `path`)

### `media`

#### `media list`

Lists library items and their IDs.

```bash
radioqt-cli media list
```

#### `media add`

Adds a media item to the library (local file path or stream URL).

```bash
radioqt-cli media add --source "/home/user/video.mp4" --title "video.mp4"
```

If `--title` is omitted, the CLI uses the source filename (or the source value).

### `streams`

#### `streams list`

Lists URL-based stream entries (not local files).

```bash
radioqt-cli streams list
```

#### `streams add`

Adds a stream URL entry.

```bash
radioqt-cli streams add \
  --source "https://example.com/live.m3u8" \
  --title "My Stream"
```

Enable Greenwich Time Signal for this stream:

```bash
radioqt-cli streams add \
  --source "https://example.com/live.m3u8" \
  --greenwich-time-signal true
```

#### `streams edit`

Edit title/URL/signal flag:

```bash
radioqt-cli streams edit "STREAM_ID" \
  --title "My Stream HQ" \
  --source "https://example.com/live-hq.m3u8" \
  --greenwich-time-signal false
```

#### `streams remove`

Remove one stream entry:

```bash
radioqt-cli streams remove "STREAM_ID"
```

### `schedule`

#### `schedule list`

Lists the timeline for the current day:

```bash
radioqt-cli schedule list
```

Lists by date:

```bash
radioqt-cli schedule list --date "2026-04-16"
```

Lists all runtime-visible dates:

```bash
radioqt-cli schedule list --all
```

#### `schedule add`

Creates a one-shot entry:

```bash
radioqt-cli schedule add \
  --media-id "MEDIA_ID_HERE" \
  --start "2026-04-16T18:00:00+02:00"
```

With fades:

```bash
radioqt-cli schedule add \
  --media-id "MEDIA_ID_HERE" \
  --start "2026-04-16T18:00:00+02:00" \
  --fade-in \
  --fade-out
```

#### `schedule bulk-add`

Creates multiple entries in one command (repeat `--start`):

```bash
radioqt-cli schedule bulk-add \
  --media-id "MEDIA_ID_HERE" \
  --start "2026-04-16T18:35:00+02:00" \
  --start "2026-04-16T18:45:00+02:00" \
  --start "2026-04-16T18:50:00+02:00"
```

#### `schedule edit`

Change start time:

```bash
radioqt-cli schedule edit "SCHEDULE_ENTRY_ID" \
  --start "2026-04-16T19:30:00+02:00"
```

Change media:

```bash
radioqt-cli schedule edit "SCHEDULE_ENTRY_ID" \
  --media-id "NEW_MEDIA_ID"
```

Change fades:

```bash
radioqt-cli schedule edit "SCHEDULE_ENTRY_ID" --fade-in true --fade-out false
```

Change status:

```bash
radioqt-cli schedule edit "SCHEDULE_ENTRY_ID" --status disabled
radioqt-cli schedule edit "SCHEDULE_ENTRY_ID" --status pending
```

#### `schedule remove`

Remove one entry:

```bash
radioqt-cli schedule remove "SCHEDULE_ENTRY_ID"
```

Remove multiple entries:

```bash
radioqt-cli schedule remove "SCHEDULE_ENTRY_ID_1" "SCHEDULE_ENTRY_ID_2"
```

Force removal of CRON-generated rows:

```bash
radioqt-cli schedule remove "SCHEDULE_ENTRY_ID" --force
```

Note: if the CRON rule is still enabled, those rows can be regenerated.

#### `schedule bulk-status`

Bulk update status by date:

```bash
radioqt-cli schedule bulk-status --date "2026-04-16" --status disabled
```

Bulk update specific entry IDs:

```bash
radioqt-cli schedule bulk-status \
  --entry-id "ENTRY_ID_1" \
  --entry-id "ENTRY_ID_2" \
  --status pending
```

Optional `--media-id` can be combined with either mode to narrow the target set.

### `cron`

#### `cron list`

Lists CRON rules with next occurrence:

```bash
radioqt-cli cron list
```

#### `cron add`

Create a CRON rule:

```bash
radioqt-cli cron add \
  --media-id "MEDIA_ID_HERE" \
  --expression "0 */15 * * * *"
```

Create disabled and with fades:

```bash
radioqt-cli cron add \
  --media-id "MEDIA_ID_HERE" \
  --expression "0 0 8 * * 1-5" \
  --fade-in \
  --fade-out \
  --enabled false
```

#### `cron edit`

Change expression:

```bash
radioqt-cli cron edit "CRON_ID" --expression "0 0/10 * * * *"
```

Change media:

```bash
radioqt-cli cron edit "CRON_ID" --media-id "NEW_MEDIA_ID"
```

Enable or disable:

```bash
radioqt-cli cron edit "CRON_ID" --enabled false
radioqt-cli cron edit "CRON_ID" --enabled true
```

Change fades:

```bash
radioqt-cli cron edit "CRON_ID" --fade-in true --fade-out false
```

#### `cron remove`

Remove a rule:

```bash
radioqt-cli cron remove "CRON_ID"
```

### `runtime`

Runtime commands let you inspect and control the GUI process state from SSH/CLI.
Lock lifecycle:

- GUI start: creates `radioqt.lock` with `status=offline`.
- GUI Play: updates lock to `status=online` with current PID.
- GUI Stop: updates lock to `status=offline` (PID is kept while GUI is open).
- GUI close: deletes `radioqt.lock`.

#### `runtime status`

Shows current runtime state, PID, and whether the PID is still alive:

```bash
radioqt-cli runtime status
```

JSON mode:

```bash
radioqt-cli --json runtime status
```

#### `runtime set-status`

Manually set runtime state in `radioqt.lock`.

Set offline:

```bash
radioqt-cli runtime set-status --value offline
```

Set online (requires PID):

```bash
radioqt-cli runtime set-status --value online --pid 12345
```

#### `runtime stop`

Stops the GUI PID stored in `radioqt.lock` using `SIGTERM` and removes the lock.

```bash
radioqt-cli runtime stop
```

Stop with custom timeout:

```bash
radioqt-cli runtime stop --timeout 10
```

Force stop (`SIGKILL`) if graceful stop fails:

```bash
radioqt-cli runtime stop --force
```

Stop a specific PID (override lock file PID):

```bash
radioqt-cli runtime stop --pid 12345 --force
```

#### `runtime watch`

Watches runtime lock/status changes in real time (good for SSH sessions).

Basic watch:

```bash
radioqt-cli runtime watch
```

Press `Ctrl+C` to stop the watcher.

One snapshot and exit:

```bash
radioqt-cli runtime watch --once
```

Watch for 30 seconds:

```bash
radioqt-cli runtime watch --timeout 30
```

Machine-readable watch stream:

```bash
radioqt-cli --json runtime watch
```

#### `runtime online`

Triggers the same action as pressing GUI `Play` (automation online).

```bash
radioqt-cli runtime online
```

#### `runtime offline`

Triggers the same action as pressing GUI `Stop` (automation offline).

```bash
radioqt-cli runtime offline
```

#### `runtime fade-in`

Triggers immediate live fade-in on the running GUI (same action as clicking the fade-in button).

```bash
radioqt-cli runtime fade-in
```

#### `runtime fade-out`

Triggers immediate live fade-out on the running GUI (same action as clicking the fade-out button).

```bash
radioqt-cli runtime fade-out
```

#### `runtime volume`

Sets live GUI volume to a value between `0` and `100`.

```bash
radioqt-cli runtime volume --value 65
```

Set volume to zero:

```bash
radioqt-cli runtime volume --value 0
```

#### `runtime mute`

Alias for setting volume to zero (same as `runtime volume --value 0`).

```bash
radioqt-cli runtime mute
```

### `logs`

Read/export runtime logs written by the GUI and runtime control handlers.

#### `logs show`

Show last 200 lines (default):

```bash
radioqt-cli logs show
```

Show all lines:

```bash
radioqt-cli logs show --all
```

Show only last 50:

```bash
radioqt-cli logs show --lines 50
```

JSON mode:

```bash
radioqt-cli --json logs show --lines 20
```

#### `logs export`

Export all lines to a file:

```bash
radioqt-cli logs export --output "/tmp/radioqt-export.log"
```

Export only last 300 lines:

```bash
radioqt-cli logs export --output "/tmp/radioqt-export.log" --lines 300
```

### `icecast`

Control an external ffmpeg relay process (typically ffmpeg -> Icecast).

`icecast_command` is regenerated from `icecast_*` parameters whenever those parameters change.
If you append extra ffmpeg args at the end of `icecast_command`, that suffix is preserved when parameters change.

Recommended setup (parameter-based):

```bash
radioqt-cli settings set icecast_status true
radioqt-cli settings set icecast_run_in_background false
radioqt-cli settings set icecast_device "alsa_output.usb-Generic_KM_B2_USB_Audio_20210726905926-00.analog-stereo.monitor"
radioqt-cli settings set icecast_audio_bitrate 128
radioqt-cli settings set icecast_audio_rate 48000
radioqt-cli settings set icecast_url "icecast://source:PASS@HOST:8000/radio.mp3"
```

Background behavior:

- If `icecast_run_in_background=false`, closing GUI will stop Icecast relay.
- If `icecast_run_in_background=true`, GUI close keeps relay running in background.

Optional extra args suffix (preserved on parameter changes):

```bash
radioqt-cli --json settings get icecast_command
# copy value and append suffix, example:
radioqt-cli settings set icecast_command '<generated-command> -af loudnorm'
```

#### `icecast status`

```bash
radioqt-cli icecast status
```

#### `icecast start`

Uses priority order:

1. `--command` (one run)
2. `settings.icecast_command` (normally generated from `icecast_*`)
3. generated command from `icecast_*` ffmpeg parameters (fallback when empty)

```bash
radioqt-cli icecast start
```

Override command for one run:

```bash
radioqt-cli icecast start --command 'ffmpeg ...'
```

#### `icecast stop`

```bash
radioqt-cli icecast stop
```

Stop with custom timeout:

```bash
radioqt-cli icecast stop --timeout 5
```

Force stop if needed:

```bash
radioqt-cli icecast stop --force
```

## JSON output mode

`--json` is a global flag. It works with all commands and returns compact JSON payloads suitable for scripting.

Example:

```bash
radioqt-cli --json schedule list --date "2026-04-16"
```

Example response:

```json
{"ok":true,"count":2,"entries":[{"id":"...","media_id":"...","media_title":"...","start_at":"2026-04-16T18:45:00+02:00","status":"pending","cron_id":null,"fade_in":false,"fade_out":false,"one_shot":true}]}
```

## Common errors

- `Media id '...' does not exist`:
  use `radioqt-cli media list` and copy a valid `MEDIA_ID`.
- `Invalid datetime`:
  check ISO format (`YYYY-MM-DDTHH:MM:SS+TZ`).
- `Invalid date`:
  check `YYYY-MM-DD` format.
- `Stream source must be a URL`:
  for `streams add/edit --source`, use a stream URL (`http/https/rtsp/...`).
- `No schedule entries matched the bulk filter`:
  adjust `--date`, `--entry-id`, or `--media-id` values.
- `State changed in another process while this command was running`:
  another GUI/CLI process saved first. Re-run your command to apply it on the latest state.
- `Some entries are CRON-managed and protected from direct removal`:
  disable or remove the CRON rule, or use `--force`.
- `No changes were applied`:
  the new value is the same as the current one.
- `No runtime PID is available`:
  there is no active/known GUI PID in the runtime status file.
- `PID ... is still running after ...`:
  graceful stop timed out; retry with `runtime stop --force`.
- `Interval must be greater than zero`:
  for `runtime watch`, use `--interval` values above `0`.
- `GUI runtime is not running`:
  start `radioqt` first (or verify `runtime status`) before sending live runtime commands.
- `Volume must be between 0 and 100`:
  for `runtime volume`, use values in the `0..100` range.
- `lines must be greater than zero`:
  for `logs show --lines` or `logs export --lines`, use values above `0`.
- `Output path is a directory`:
  for `logs export --output`, pass a file path, not a directory.
- `No icecast command configured`:
  configure `icecast_*` keys (or pass `icecast start --command`). `icecast_command` is usually generated automatically.
- `Icecast relay is already running`:
  run `icecast stop` first, then retry `icecast start`.
- `No icecast relay PID available`:
  no tracked relay process exists; start it first or pass `icecast stop --pid`.
- `Icecast relay PID ... is still running after ...`:
  graceful stop timed out; retry with `icecast stop --force`.
- `Unknown settings key`:
  run `radioqt-cli settings get` and use one of the supported setting keys.

## Exit codes

- `0`: command succeeded.
- `2`: validation or argument usage error.

## GUI and CLI share state

GUI (`radioqt`) and CLI (`radioqt-cli`) use the same SQLite database for the `--config` path you choose.
If both are open at the same time using the same path, changes are persisted to the same data source.
Both entry points also reuse shared domain modules (`library`, `scheduling`, `app_config`, `runtime_status`, `runtime_control`, `runtime_logs`, and icecast relay runtime modules) so behavior stays aligned between GUI actions and CLI commands.

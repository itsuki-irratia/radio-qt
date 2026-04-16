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
- `No schedule entries matched the bulk filter`:
  adjust `--date`, `--entry-id`, or `--media-id` values.
- `State changed in another process while this command was running`:
  another GUI/CLI process saved first. Re-run your command to apply it on the latest state.
- `Some entries are CRON-managed and protected from direct removal`:
  disable or remove the CRON rule, or use `--force`.
- `No changes were applied`:
  the new value is the same as the current one.

## Exit codes

- `0`: command succeeded.
- `2`: validation or argument usage error.

## GUI and CLI share state

GUI (`radioqt`) and CLI (`radioqt-cli`) use the same SQLite database for the `--config` path you choose.
If both are open at the same time using the same path, changes are persisted to the same data source.

# RadioQt

[![CI](https://github.com/itsuki-irratia/radio-qt/actions/workflows/ci.yml/badge.svg)](https://github.com/itsuki-irratia/radio-qt/actions/workflows/ci.yml)

RadioQt is a Python + Qt multimedia player for radio automation workflows:
- VLC-style local/stream playback
- Datetime-oriented scheduling
- Queue fallback when player is busy
- SQLite persistence for library, queue, and schedule

## Requirements

- Python 3.10+
- Qt multimedia backend available on your system
- System multimedia packages (see `requirements-system.txt`)

### Debian / Ubuntu system packages

Install the Qt 6 multimedia runtime libraries, common GStreamer codecs, and
FFmpeg tools before installing the Python package:

```bash
sudo apt update
sudo apt install \
  libqt6multimedia6 \
  libqt6multimediawidgets6 \
  qml6-module-qtmultimedia \
  ffmpeg
```

On minimal Debian installs, you may also need PulseAudio/PipeWire audio support
from your desktop environment packages.

### Arch / Manjaro system packages

```bash
sudo pacman -S --needed \
  qt6-multimedia \
  qt6-multimedia-ffmpeg \
  ffmpeg
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

For development (with test tooling):

```bash
pip install -e ".[dev]"
```

## Run

```bash
radioqt
```

CLI for schedule and CRON management:

```bash
radioqt-cli --help
```

Full CLI docs (all commands + explicit examples):

[CLI.md](CLI.md)

Default runtime paths:

- SQLite state: `$HOME/.config/radioqt/db.sqlite`
- App settings (YAML): `$HOME/.config/radioqt/settings.yaml`

You can override the config directory:

```bash
radioqt --config "/path/to/config-dir"
```

And same for CLI:

```bash
radioqt-cli --config "/path/to/config-dir" schedule list
```

In that case, paths become:
- `/path/to/config-dir/db.sqlite`
- `/path/to/config-dir/settings.yaml`

Legacy `state/radio_state.db` and `state/radio_state.json` are auto-migrated when running with the historical local config dir (`--config ./config`).

## Basic workflow

1. Add local files or stream URLs to the Media Library.
2. Select a media item and click `Schedule Selected Media`.
3. Pick an absolute start datetime.
4. Let scheduler trigger playback automatically.

## Notes

- Scheduled items always interrupt current playback at trigger time.
- If a media source is missing, the app logs the skip and continues.

## Tests

```bash
python -m pytest
```

## Linux troubleshooting

- RadioQt does not select audio devices itself. It creates a regular desktop
  audio stream and leaves output routing to PulseAudio/PipeWire. Use your Linux
  mixer, for example `pavucontrol` -> `Playback`, to move the active `RadioQt`
  stream to Bluetooth, USB, HDMI, or another sink.

- RadioQt uses the Qt FFmpeg backend.

- If you see repeated VAAPI decode errors (`invalid VAContextID`, `hardware accelerator failed to decode picture`), run with software decoding:

```bash
RADIOQT_DISABLE_HW_DECODING=1 radioqt
```

- To re-enable hardware decoding for testing:

```bash
RADIOQT_DISABLE_HW_DECODING=0 radioqt
```

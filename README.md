# RadioQt

RadioQt is a Python + Qt multimedia player for radio automation workflows:
- VLC-style local/stream playback
- Datetime-oriented scheduling
- Queue fallback when player is busy
- SQLite persistence for library, queue, and schedule

## Requirements

- Python 3.10+
- Qt multimedia backend available on your system
- System multimedia packages (see `requirements-system.txt`)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python -m radioqt
```

Default runtime paths:

- SQLite state: `config/db.sqlite`
- App settings (YAML): `config/settings.yaml`

You can override the config directory:

```bash
python -m radioqt --config "/path/to/config-dir"
```

In that case, paths become:
- `/path/to/config-dir/db.sqlite`
- `/path/to/config-dir/settings.yaml`

Legacy `state/radio_state.db` and `state/radio_state.json` are copied to the new location automatically when needed.

## Basic workflow

1. Add local files or stream URLs to the Media Library.
2. Select a media item and click `Schedule Selected Media`.
3. Pick an absolute start datetime.
4. Let scheduler trigger playback automatically.

## Notes

- Scheduled items always interrupt current playback at trigger time.
- If a media source is missing, the app logs the skip and continues.

## Linux troubleshooting

- If you want to use the GStreamer backend on Arch/Manjaro, install:

```bash
sudo pacman -S --needed qt6-multimedia qt6-multimedia-ffmpeg qt6-multimedia-gstreamer gstreamer
```

- Then run:

```bash
RADIOQT_MEDIA_BACKEND=gstreamer python -m radioqt
```

- If you see repeated VAAPI decode errors (`invalid VAContextID`, `hardware accelerator failed to decode picture`), run with software decoding:

```bash
RADIOQT_DISABLE_HW_DECODING=1 python -m radioqt
```

- To re-enable hardware decoding for testing:

```bash
RADIOQT_DISABLE_HW_DECODING=0 python -m radioqt
```

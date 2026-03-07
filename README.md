# RadioQt

RadioQt is a Python + Qt multimedia player for radio automation workflows:
- VLC-style local/stream playback
- Datetime-oriented scheduling
- Queue fallback when player is busy
- JSON persistence for library, queue, and schedule

## Requirements

- Python 3.10+
- Qt multimedia backend available on your system

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

State is saved to:

`state/radio_state.json`

## Basic workflow

1. Add local files or stream URLs to the Media Library.
2. Select a media item and click `Schedule Selected Media`.
3. Pick an absolute start datetime and optional hard sync.
4. Let scheduler trigger playback automatically.

## Notes

- `Hard sync` interrupts current playback when a scheduled item starts.
- Without hard sync, scheduled items are queued if something is already playing.
- If a media source is missing, the app logs the skip and continues.

## Linux troubleshooting

- If you see repeated VAAPI decode errors (`invalid VAContextID`, `hardware accelerator failed to decode picture`), run with software decoding:

```bash
RADIOQT_DISABLE_HW_DECODING=1 python -m radioqt
```

- To re-enable hardware decoding for testing:

```bash
RADIOQT_DISABLE_HW_DECODING=0 python -m radioqt
```

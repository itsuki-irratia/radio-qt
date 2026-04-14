from __future__ import annotations

import argparse
from pathlib import Path


def parse_cli_args(argv: list[str]) -> tuple[Path, list[str]]:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "--config",
        default="config",
        help="Configuration directory (default: ./config)",
    )
    parsed_args, qt_args = parser.parse_known_args(argv[1:])
    config_dir = Path(parsed_args.config).expanduser()
    return config_dir, [argv[0], *qt_args]

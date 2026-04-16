from __future__ import annotations


def run(argv: list[str] | None = None) -> int:
    from .app import run as _run

    return _run(argv)


__all__ = ["run"]

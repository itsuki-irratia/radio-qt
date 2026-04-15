from __future__ import annotations


def db_bool_to_python(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off", ""}:
            return False
    return default


def db_optional_bool_to_python(value: object) -> bool | None:
    if value is None:
        return None
    return db_bool_to_python(value, default=False)


def python_bool_to_db(value: bool) -> str:
    return "True" if value else "False"


def python_optional_bool_to_db(value: bool | None) -> str | None:
    if value is None:
        return None
    return python_bool_to_db(value)

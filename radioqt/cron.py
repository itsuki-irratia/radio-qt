from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterator

MONTH_NAMES = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

DOW_NAMES = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}


class CronParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CronField:
    values: tuple[int, ...]
    is_wildcard: bool


def _normalize_dow(value: int) -> int:
    return 0 if value == 7 else value


def _parse_single_value(token: str, minimum: int, maximum: int, names: dict[str, int] | None = None) -> int:
    lowered = token.lower()
    if names and lowered in names:
        return names[lowered]
    try:
        value = int(token)
    except ValueError as exc:
        raise CronParseError(f"Invalid cron value '{token}'") from exc
    if not minimum <= value <= maximum:
        raise CronParseError(f"Cron value '{token}' is outside {minimum}-{maximum}")
    return value


def _parse_field(
    raw: str,
    minimum: int,
    maximum: int,
    names: dict[str, int] | None = None,
    normalize=None,
) -> CronField:
    raw = raw.strip()
    if not raw:
        raise CronParseError("Empty cron field")

    values: set[int] = set()
    wildcard = False
    for part in raw.split(","):
        token = part.strip()
        if not token:
            raise CronParseError("Invalid empty cron list item")

        base, step_raw = (token.split("/", 1) + [None])[:2]
        step = 1
        if step_raw is not None:
            try:
                step = int(step_raw)
            except ValueError as exc:
                raise CronParseError(f"Invalid cron step '{step_raw}'") from exc
            if step <= 0:
                raise CronParseError("Cron step must be greater than zero")

        if base == "*":
            wildcard = True
            start = minimum
            end = maximum
        elif "-" in base:
            start_raw, end_raw = base.split("-", 1)
            start = _parse_single_value(start_raw, minimum, maximum, names)
            end = _parse_single_value(end_raw, minimum, maximum, names)
            if normalize is not None:
                start = normalize(start)
                end = normalize(end)
            if end < start:
                raise CronParseError(f"Invalid cron range '{base}'")
        else:
            single = _parse_single_value(base, minimum, maximum, names)
            if normalize is not None:
                single = normalize(single)
            start = single
            end = single

        if base == "*":
            range_values = range(start, end + 1, step)
        else:
            range_values = range(start, end + 1, step)

        for value in range_values:
            normalized_value = normalize(value) if normalize is not None else value
            if not minimum <= normalized_value <= maximum:
                raise CronParseError(f"Cron value '{normalized_value}' is outside {minimum}-{maximum}")
            values.add(normalized_value)

    if not values:
        raise CronParseError("Cron field does not contain any values")

    full_range = tuple(range(minimum, maximum + 1))
    return CronField(values=tuple(sorted(values)), is_wildcard=wildcard or tuple(sorted(values)) == full_range)


@dataclass(frozen=True, slots=True)
class CronExpression:
    second: CronField
    minute: CronField
    hour: CronField
    day_of_month: CronField
    month: CronField
    day_of_week: CronField

    @classmethod
    def parse(cls, raw: str) -> "CronExpression":
        parts = raw.split()
        if len(parts) != 6:
            raise CronParseError(
                "Cron expression must have 6 fields: second minute hour day-of-month month day-of-week"
            )
        return cls(
            second=_parse_field(parts[0], 0, 59),
            minute=_parse_field(parts[1], 0, 59),
            hour=_parse_field(parts[2], 0, 23),
            day_of_month=_parse_field(parts[3], 1, 31),
            month=_parse_field(parts[4], 1, 12, names=MONTH_NAMES),
            day_of_week=_parse_field(parts[5], 0, 6, names=DOW_NAMES, normalize=_normalize_dow),
        )

    def matches(self, value: datetime) -> bool:
        return (
            value.second in self.second.values
            and value.minute in self.minute.values
            and value.hour in self.hour.values
            and value.month in self.month.values
            and self._matches_day(value.date())
        )

    def iter_datetimes_on_date(self, target_date: date, tzinfo) -> Iterator[datetime]:
        if target_date.month not in self.month.values or not self._matches_day(target_date):
            return

        for hour in self.hour.values:
            for minute in self.minute.values:
                for second in self.second.values:
                    yield datetime(
                        target_date.year,
                        target_date.month,
                        target_date.day,
                        hour,
                        minute,
                        second,
                        tzinfo=tzinfo,
                    )

    def next_at_or_after(self, start: datetime, max_days: int = 366) -> datetime | None:
        tzinfo = start.tzinfo
        for day_offset in range(max_days + 1):
            target_date = start.date().fromordinal(start.date().toordinal() + day_offset)
            for candidate in self.iter_datetimes_on_date(target_date, tzinfo):
                if candidate >= start:
                    return candidate
        return None

    def _matches_day(self, target_date: date) -> bool:
        dom_match = target_date.day in self.day_of_month.values
        dow_match = (target_date.isoweekday() % 7) in self.day_of_week.values

        if self.day_of_month.is_wildcard and self.day_of_week.is_wildcard:
            return True
        if self.day_of_month.is_wildcard:
            return dow_match
        if self.day_of_week.is_wildcard:
            return dom_match
        return dom_match or dow_match

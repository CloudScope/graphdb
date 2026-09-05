"""Time handling for PaySentry.

One rule, enforced everywhere: **inside the system, a timestamp is an integer
number of milliseconds since the Unix epoch, UTC.** ISO strings exist only at
the edges — config files, log lines, human-facing output.

That rule is not stylistic. Raphtory indexes on integer timestamps, TigerGraph
wants DATETIME, the event log wants something sortable, and the generator does
arithmetic on them. Picking one canonical form at the boundary means the
conversions live here instead of being rediscovered in six modules.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

MS_PER_SECOND = 1_000
MS_PER_MINUTE = 60 * MS_PER_SECOND
MS_PER_HOUR = 60 * MS_PER_MINUTE
MS_PER_DAY = 24 * MS_PER_HOUR

_DURATION_UNITS = {
    "ms": 1,
    "s": MS_PER_SECOND,
    "m": MS_PER_MINUTE,
    "h": MS_PER_HOUR,
    "d": MS_PER_DAY,
    "w": 7 * MS_PER_DAY,
}
_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h|d|w)\s*$", re.IGNORECASE)


def to_ms(value: str | datetime | int | float) -> int:
    """Coerce an ISO 8601 string, datetime, or epoch number to epoch milliseconds.

    Naive datetimes are assumed UTC rather than local time — a laptop's timezone
    must never change what the generated data means.
    """
    if isinstance(value, bool):
        raise TypeError("bool is not a timestamp")
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    if isinstance(value, str):
        return to_ms(datetime.fromisoformat(value.replace("Z", "+00:00")))
    raise TypeError(f"cannot interpret {value!r} as a timestamp")


def from_ms(ms: int) -> datetime:
    """Epoch milliseconds -> timezone-aware UTC datetime."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def iso(ms: int) -> str:
    """Epoch milliseconds -> ISO 8601 string with a trailing Z."""
    return from_ms(ms).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_duration(spec: str | int | float) -> int:
    """Parse a human duration into milliseconds.

    Accepts the forms used throughout ``config.yaml`` — ``"24h"``, ``"6h"``,
    ``"90d"``, ``"500ms"``. Bare numbers pass through as milliseconds.
    """
    if isinstance(spec, (int, float)) and not isinstance(spec, bool):
        return int(spec)
    match = _DURATION_RE.match(str(spec))
    if not match:
        raise ValueError(
            f"cannot parse duration {spec!r}; expected forms like '24h', '90d', '500ms'"
        )
    amount, unit = match.groups()
    return int(float(amount) * _DURATION_UNITS[unit.lower()])


def days(n: float) -> int:
    """Milliseconds in ``n`` days."""
    return int(n * MS_PER_DAY)


def hours(n: float) -> int:
    """Milliseconds in ``n`` hours."""
    return int(n * MS_PER_HOUR)


def hour_of_day(ms: int) -> int:
    """UTC hour (0-23) — the index into ``generation.background.hourly_weights``."""
    return from_ms(ms).hour


def is_weekend(ms: int) -> bool:
    """True on Saturday or Sunday, UTC."""
    return from_ms(ms).weekday() >= 5


def day_bucket(ms: int) -> int:
    """Whole days since the epoch. Useful for grouping without building datetimes."""
    return ms // MS_PER_DAY


def window_bounds(end_ms: int, span: str | int) -> tuple[int, int]:
    """``(start, end)`` for a window of ``span`` ending at ``end_ms``.

    Half-open ``[start, end)``, matching Raphtory's ``window()`` semantics so the
    two never disagree about whether an edge on the boundary is included.
    """
    return end_ms - parse_duration(span), end_ms


def to_datetime_literal(ms: int) -> str:
    """Epoch milliseconds -> the ``YYYY-MM-DD HH:MM:SS`` form GSQL DATETIME wants."""
    return from_ms(ms).strftime("%Y-%m-%d %H:%M:%S")


def add(ms: int, delta: timedelta) -> int:
    """Offset a timestamp by a ``timedelta``, staying in milliseconds."""
    return ms + int(delta.total_seconds() * 1000)

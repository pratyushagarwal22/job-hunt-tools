"""Parse Serper/ATS dates into tz-aware datetimes and format them for the sheet."""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_RELATIVE = re.compile(r"^(\d+|an?|one)\s+(minute|hour|day|week|month)s?\s+ago$")
_UNIT = {
    "minute": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(days=7),
    "month": timedelta(days=30),
}
_ABSOLUTE_FORMATS = ("%b %d, %Y", "%B %d, %Y")


def parse_relative_date(date_str: str, run_start: datetime) -> datetime | None:
    """Relative results are in run_start's timezone; absolute dates are midnight there,
    so run_start should be in the configured timezone."""
    _require_aware(run_start)
    if not date_str:
        return None
    text = " ".join(date_str.split()).lower()

    if text == "just now":
        return run_start

    match = _RELATIVE.match(text)
    if match:
        count_str, unit = match.groups()
        count = int(count_str) if count_str.isdigit() else 1
        # Subtract in UTC so the offset is elapsed time even across DST changes.
        then = run_start.astimezone(timezone.utc) - count * _UNIT[unit]
        return then.astimezone(run_start.tzinfo)

    for fmt in _ABSOLUTE_FORMATS:
        try:
            day = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return day.replace(tzinfo=run_start.tzinfo)
    return None


def parse_ats_date(value: str | int, tz_name: str) -> datetime | None:
    """ISO 8601 string (naive treated as UTC) or Unix ms int (Lever createdAt)."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            parsed = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        else:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(ZoneInfo(tz_name))


def format_posted_date(dt: datetime, tz_name: str) -> str:
    _require_aware(dt)
    return dt.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d")


def format_date_found(dt: datetime, tz_name: str) -> str:
    _require_aware(dt)
    return dt.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M")


def _require_aware(dt: datetime) -> None:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")

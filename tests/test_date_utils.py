from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from job_scanner.date_utils import (
    format_date_found,
    format_posted_date,
    parse_ats_date,
    parse_relative_date,
)

CT = ZoneInfo("America/Chicago")
RUN_START = datetime(2026, 9, 29, 9, 0, tzinfo=CT)


def assert_aware(dt):
    assert dt is not None
    assert dt.tzinfo is not None and dt.utcoffset() is not None


# parse_relative_date


def test_hours_ago():
    result = parse_relative_date("21 hours ago", RUN_START)
    assert_aware(result)
    assert result == datetime(2026, 9, 28, 12, 0, tzinfo=CT)


def test_days_ago():
    assert parse_relative_date("3 days ago", RUN_START) == RUN_START - timedelta(days=3)


def test_one_week_ago():
    assert parse_relative_date("1 week ago", RUN_START) == RUN_START - timedelta(days=7)


def test_weeks_ago():
    assert parse_relative_date("2 weeks ago", RUN_START) == RUN_START - timedelta(days=14)


def test_minutes_ago():
    assert parse_relative_date("45 minutes ago", RUN_START) == RUN_START - timedelta(minutes=45)


def test_one_minute_ago():
    assert parse_relative_date("1 minute ago", RUN_START) == RUN_START - timedelta(minutes=1)


def test_an_hour_ago():
    assert parse_relative_date("an hour ago", RUN_START) == RUN_START - timedelta(hours=1)


def test_a_day_ago():
    assert parse_relative_date("a day ago", RUN_START) == RUN_START - timedelta(days=1)


def test_a_week_ago():
    assert parse_relative_date("a week ago", RUN_START) == RUN_START - timedelta(days=7)


def test_months_ago_are_30_days_each():
    assert parse_relative_date("2 months ago", RUN_START) == RUN_START - timedelta(days=60)
    assert parse_relative_date("a month ago", RUN_START) == RUN_START - timedelta(days=30)


def test_just_now():
    assert parse_relative_date("just now", RUN_START) == RUN_START


def test_relative_is_case_and_whitespace_insensitive():
    assert parse_relative_date("  3 Days Ago ", RUN_START) == RUN_START - timedelta(days=3)


def test_relative_result_in_run_start_timezone():
    result = parse_relative_date("3 days ago", RUN_START)
    assert result.tzinfo == CT


def test_relative_across_dst_change_is_elapsed_time():
    # DST ends 2026-11-01 02:00 CT; a day ago is 24 real hours, not same wall time.
    run_start = datetime(2026, 11, 1, 12, 0, tzinfo=CT)
    result = parse_relative_date("1 day ago", run_start)
    elapsed = run_start.astimezone(timezone.utc) - result.astimezone(timezone.utc)
    assert elapsed == timedelta(hours=24)
    assert result.hour == 13


def test_relative_with_utc_run_start():
    run_start = datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc)
    assert parse_relative_date("2 hours ago", run_start) == datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def test_absolute_date_is_midnight_ct():
    result = parse_relative_date("Sep 28, 2026", RUN_START)
    assert_aware(result)
    assert result == datetime(2026, 9, 28, 0, 0, tzinfo=CT)
    assert result.tzinfo == CT


def test_absolute_full_month_name():
    assert parse_relative_date("September 28, 2026", RUN_START) == datetime(2026, 9, 28, tzinfo=CT)


def test_absolute_single_digit_day():
    assert parse_relative_date("Oct 1, 2026", RUN_START) == datetime(2026, 10, 1, tzinfo=CT)


@pytest.mark.parametrize(
    "value",
    ["", "   ", "yesterday-ish", "21 fortnights ago", "Sep 31, 2026", "hours ago", "-3 days ago", None],
)
def test_unparseable_returns_none(value):
    assert parse_relative_date(value, RUN_START) is None


def test_naive_run_start_raises():
    with pytest.raises(ValueError):
        parse_relative_date("3 days ago", datetime(2026, 9, 29, 9, 0))


# parse_ats_date


def test_ats_utc_iso_string_to_ct():
    result = parse_ats_date("2026-09-29T14:00:00Z", "America/Chicago")
    assert_aware(result)
    assert result.tzinfo == CT
    assert result == datetime(2026, 9, 29, 9, 0, tzinfo=CT)
    assert (result.hour, result.minute) == (9, 0)


def test_ats_iso_with_offset():
    result = parse_ats_date("2026-09-29T10:00:00-04:00", "America/Chicago")
    assert result == datetime(2026, 9, 29, 9, 0, tzinfo=CT)
    assert result.hour == 9


def test_ats_iso_with_fractional_seconds():
    result = parse_ats_date("2026-09-29T14:00:00.123Z", "America/Chicago")
    assert result.replace(microsecond=0) == datetime(2026, 9, 29, 9, 0, tzinfo=CT)


def test_ats_naive_iso_is_treated_as_utc():
    result = parse_ats_date("2026-09-29T14:00:00", "America/Chicago")
    assert_aware(result)
    assert result == datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc)
    assert result.hour == 9


def test_ats_unix_ms_int_to_ct():
    ms = int(datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc).timestamp() * 1000)
    result = parse_ats_date(ms, "America/Chicago")
    assert_aware(result)
    assert result.tzinfo == CT
    assert (result.year, result.month, result.day, result.hour) == (2026, 9, 29, 9)


def test_ats_utc_target_timezone():
    result = parse_ats_date("2026-09-29T14:00:00Z", "UTC")
    assert result == datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc)
    assert result.hour == 14


@pytest.mark.parametrize("value", [None, "", "not a date", "2026-13-45", True])
def test_ats_unparseable_returns_none(value):
    assert parse_ats_date(value, "America/Chicago") is None


# formatters


def test_format_posted_date():
    assert format_posted_date(datetime(2026, 9, 28, 12, 0, tzinfo=CT), "America/Chicago") == "2026-09-28"


def test_format_posted_date_converts_timezone():
    # 02:00 UTC on the 30th is still the 29th in Chicago.
    dt = datetime(2026, 9, 30, 2, 0, tzinfo=timezone.utc)
    assert format_posted_date(dt, "America/Chicago") == "2026-09-29"


def test_format_date_found():
    dt = datetime(2026, 9, 30, 2, 7, tzinfo=timezone.utc)
    assert format_date_found(dt, "America/Chicago") == "2026-09-29 21:07"


def test_formatters_return_strings():
    assert isinstance(format_posted_date(RUN_START, "America/Chicago"), str)
    assert isinstance(format_date_found(RUN_START, "America/Chicago"), str)


def test_formatters_reject_naive():
    with pytest.raises(ValueError):
        format_posted_date(datetime(2026, 9, 29), "America/Chicago")
    with pytest.raises(ValueError):
        format_date_found(datetime(2026, 9, 29), "America/Chicago")

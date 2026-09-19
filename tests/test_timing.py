"""Time expressions: the thing an assistant gets asked about most."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from zoneinfo import ZoneInfo

from jarvis.timing import TimeParseError, format_when, parse_when

BERLIN = "Europe/Berlin"
# A Friday, 17:00 in Berlin.
NOW = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)


def local(moment: datetime) -> datetime:
    return moment.astimezone(ZoneInfo(BERLIN))


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("+10m", "2026-09-18 17:10"),
        ("in 10 Minuten", "2026-09-18 17:10"),
        ("in 2 hours", "2026-09-18 19:00"),
        ("in 3 Tagen", "2026-09-21 17:00"),
        ("morgen 9 Uhr", "2026-09-19 09:00"),
        ("tomorrow 9am", "2026-09-19 09:00"),
        ("übermorgen 14:30", "2026-09-20 14:30"),
        ("heute abend", "2026-09-18 20:00"),
        ("2026-12-24T18:00", "2026-12-24 18:00"),
        ("2026-12-24 18:00", "2026-12-24 18:00"),
        ("18:30", "2026-09-18 18:30"),
    ],
)
def test_expressions_resolve(expression, expected):
    assert local(parse_when(expression, NOW, BERLIN)).strftime("%Y-%m-%d %H:%M") == expected


def test_bare_clock_time_rolls_to_tomorrow_when_past():
    # 07:00 has been and gone at 17:00, so it means tomorrow morning.
    assert local(parse_when("7 Uhr", NOW, BERLIN)).strftime("%Y-%m-%d %H:%M") == "2026-09-19 07:00"


def test_named_weekday_means_the_next_one():
    # Asked on a Friday, "Monday" is three days out.
    assert local(parse_when("montag", NOW, BERLIN)).strftime("%Y-%m-%d") == "2026-09-21"


def test_same_weekday_means_next_week_not_today():
    assert local(parse_when("freitag", NOW, BERLIN)).strftime("%Y-%m-%d") == "2026-09-25"


def test_naive_iso_is_read_in_the_users_timezone():
    moment = parse_when("2026-12-24T18:00", NOW, BERLIN)
    # Berlin is UTC+1 in December.
    assert moment.astimezone(timezone.utc).strftime("%H:%M") == "17:00"


def test_nonsense_is_rejected_rather_than_guessed():
    with pytest.raises(TimeParseError):
        parse_when("irgendwann bald", NOW, BERLIN)
    with pytest.raises(TimeParseError):
        parse_when("", NOW, BERLIN)


def test_formatting_reads_like_speech():
    tomorrow = parse_when("morgen 9 Uhr", None, BERLIN)
    assert format_when(tomorrow, BERLIN, "de").startswith("morgen um")
    assert format_when(tomorrow, BERLIN, "en").startswith("tomorrow at")

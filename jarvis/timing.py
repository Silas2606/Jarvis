"""Turning what a person says about time into an actual timestamp.

Claude knows the current time from the system prompt and can do the arithmetic
itself, so tools accept plain ISO-8601. But models do slip on date maths, and
"in ten minutes" is by far the most common request a personal assistant gets --
so relative offsets are accepted too, and resolved here against a real clock.

Accepted shapes::

    2026-04-20T17:00:00        ISO-8601, naive -> interpreted in local time
    2026-04-20 17:00           ISO-ish with a space
    +10m  +2h  +3d  +1w        relative offset from now
    in 10 minutes / in 2 hours English relative
    in 10 Minuten / in 2 Stunden  German relative
    tomorrow 09:00 / morgen 9 Uhr
    17:00 / 17 Uhr             the next time the clock reads that
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

__all__ = ["parse_when", "format_when", "resolve_zone", "TimeParseError"]


class TimeParseError(ValueError):
    """The given expression is not a time we can pin down."""


_UNITS: dict[str, str] = {
    # English
    "s": "seconds", "sec": "seconds", "secs": "seconds", "second": "seconds", "seconds": "seconds",
    "m": "minutes", "min": "minutes", "mins": "minutes", "minute": "minutes", "minutes": "minutes",
    "h": "hours", "hr": "hours", "hrs": "hours", "hour": "hours", "hours": "hours",
    "d": "days", "day": "days", "days": "days",
    "w": "weeks", "week": "weeks", "weeks": "weeks",
    # German
    "sekunde": "seconds", "sekunden": "seconds",
    "minute_de": "minutes", "minuten": "minutes",
    "stunde": "hours", "stunden": "hours",
    "tag": "days", "tage": "days", "tagen": "days",
    "woche": "weeks", "wochen": "weeks",
}

_WEEKDAYS: dict[str, int] = {
    "monday": 0, "montag": 0,
    "tuesday": 1, "dienstag": 1,
    "wednesday": 2, "mittwoch": 2,
    "thursday": 3, "donnerstag": 3,
    "friday": 4, "freitag": 4,
    "saturday": 5, "samstag": 5, "sonnabend": 5,
    "sunday": 6, "sonntag": 6,
}

_TOMORROW = {"tomorrow", "morgen"}
_DAY_AFTER = {"overmorrow", "übermorgen", "uebermorgen"}
_TODAY = {"today", "heute"}
_TONIGHT = {"tonight", "heute abend"}


def resolve_zone(name: str | None) -> timezone | ZoneInfo:
    """A tzinfo for ``name``, falling back to UTC when it is unknown."""
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def _clock(text: str) -> time | None:
    """Read a clock time out of a fragment: '9', '9:30', '17 Uhr', '9am'."""
    text = text.strip().lower()
    ampm = re.search(r"\b(am|pm)\b", text)
    match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(?:uhr|h|am|pm)?", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if ampm:
        if ampm.group(1) == "pm" and hour < 12:
            hour += 12
        if ampm.group(1) == "am" and hour == 12:
            hour = 0
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def parse_when(
    expression: str,
    now: datetime | None = None,
    tz_name: str | None = None,
) -> datetime:
    """Resolve ``expression`` to a timezone-aware UTC datetime.

    Args:
        expression: What the user (or Claude) said the time was.
        now: Reference point; defaults to the real current time.
        tz_name: IANA zone the user lives in, e.g. ``Europe/Berlin``. Naive and
            relative expressions are interpreted there.

    Raises:
        TimeParseError: when nothing in the string pins down a moment.
    """
    if expression is None or not str(expression).strip():
        raise TimeParseError("No time given.")

    tz = resolve_zone(tz_name)
    reference = (now or datetime.now(timezone.utc)).astimezone(tz)
    text = str(expression).strip().lower()

    # 1. Compact relative offsets: +10m, +2h, 30m
    compact = re.fullmatch(r"\+?\s*(\d+)\s*([a-zäöü]+)", text)
    if compact and compact.group(2) in _UNITS:
        unit = _UNITS[compact.group(2)]
        return (reference + timedelta(**{unit: int(compact.group(1))})).astimezone(timezone.utc)

    # 2. Worded relative offsets: "in 10 minutes", "in 2 Stunden"
    worded = re.search(r"\b(?:in|nach)\s+(\d+)\s*([a-zäöü]+)", text)
    if worded:
        key = worded.group(2)
        unit = _UNITS.get(key) or _UNITS.get(key.rstrip("n"))
        if unit:
            return (reference + timedelta(**{unit: int(worded.group(1))})).astimezone(timezone.utc)

    # 3. ISO-8601, with or without a zone
    iso_candidate = text.replace(" ", "T", 1) if re.match(r"^\d{4}-\d{2}-\d{2}[ t]", text) else text
    try:
        parsed = datetime.fromisoformat(iso_candidate.upper().replace("Z", "+00:00").lower())
    except ValueError:
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        return parsed.astimezone(timezone.utc)

    # 4. Named days, with an optional clock time
    clock = _clock(text)
    target_date = None
    if any(word in text for word in _DAY_AFTER):
        target_date = reference.date() + timedelta(days=2)
    elif any(word in text for word in _TOMORROW):
        target_date = reference.date() + timedelta(days=1)
    elif any(word in text for word in _TONIGHT):
        target_date = reference.date()
        clock = clock or time(20, 0)
    elif any(word in text for word in _TODAY):
        target_date = reference.date()
    else:
        for name, index in _WEEKDAYS.items():
            if re.search(rf"\b{name}\b", text):
                ahead = (index - reference.weekday()) % 7
                # "on Monday" said on a Monday means the next one.
                ahead = ahead or 7
                target_date = reference.date() + timedelta(days=ahead)
                break

    if target_date is not None:
        moment = datetime.combine(target_date, clock or time(9, 0), tzinfo=tz)
        return moment.astimezone(timezone.utc)

    # 5. A bare clock time: the next time the clock reads that.
    if clock is not None and re.search(r"\d", text):
        moment = datetime.combine(reference.date(), clock, tzinfo=tz)
        if moment <= reference:
            moment += timedelta(days=1)
        return moment.astimezone(timezone.utc)

    raise TimeParseError(f"Could not work out a time from {expression!r}.")


def format_when(moment: datetime, tz_name: str | None = None, language: str = "en") -> str:
    """Render a timestamp the way it should be read aloud."""
    tz = resolve_zone(tz_name)
    local = moment.astimezone(tz)
    now = datetime.now(tz)
    today = now.date()
    delta_days = (local.date() - today).days

    if language.startswith("de"):
        clock = local.strftime("%H:%M")
        if delta_days == 0:
            return f"heute um {clock}"
        if delta_days == 1:
            return f"morgen um {clock}"
        if delta_days == -1:
            return f"gestern um {clock}"
        if 0 < delta_days < 7:
            days = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
            return f"{days[local.weekday()]} um {clock}"
        return local.strftime("%d.%m.%Y um %H:%M")

    clock = local.strftime("%H:%M")
    if delta_days == 0:
        return f"today at {clock}"
    if delta_days == 1:
        return f"tomorrow at {clock}"
    if delta_days == -1:
        return f"yesterday at {clock}"
    if 0 < delta_days < 7:
        return f"{local.strftime('%A')} at {clock}"
    return local.strftime("%d %B %Y at %H:%M")

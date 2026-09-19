"""Clock and calendar arithmetic that Jarvis should never have to guess at."""

from __future__ import annotations

from datetime import datetime, timezone

from jarvis.timing import TimeParseError, format_when, parse_when, resolve_zone
from jarvis.tools.base import Tool, ToolError, tool


@tool
def current_time(ctx, timezone_name: str = "") -> str:
    """Get the exact current date and time.

    Args:
        timezone_name: An IANA zone such as "Europe/Berlin". Defaults to the
            user's own timezone.
    """
    zone_name = timezone_name or getattr(ctx.config, "timezone", "UTC")
    zone = resolve_zone(zone_name)
    now = datetime.now(zone)
    return now.strftime(f"%A, %d %B %Y, %H:%M:%S ({zone_name})")


@tool
def time_until(ctx, when: str) -> str:
    """Work out how long it is until a given moment.

    Args:
        when: ISO-8601 or a phrase like "tomorrow 09:00" or "+3d".
    """
    tz_name = getattr(ctx.config, "timezone", "UTC")
    try:
        moment = parse_when(when, tz_name=tz_name)
    except TimeParseError as exc:
        raise ToolError(str(exc)) from exc

    delta = moment - datetime.now(timezone.utc)
    seconds = int(abs(delta.total_seconds()))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60

    parts = []
    if days:
        parts.append(f"{days} day(s)")
    if hours:
        parts.append(f"{hours} hour(s)")
    if minutes or not parts:
        parts.append(f"{minutes} minute(s)")
    span = ", ".join(parts)

    language = getattr(getattr(ctx.config, "voice", None), "language", "en")
    rendered = format_when(moment, tz_name, language)
    direction = "from now" if delta.total_seconds() >= 0 else "ago"
    return f"{rendered} -- that is {span} {direction}."


TIME_TOOLS: list[Tool] = [current_time, time_until]

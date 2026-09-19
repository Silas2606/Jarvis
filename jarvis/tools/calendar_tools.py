"""Google Calendar, as tools Jarvis can use in conversation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jarvis.timing import TimeParseError, format_when, parse_when, resolve_zone
from jarvis.tools.base import Tool, ToolError, tool
from jarvis.tools.google_auth import GoogleUnavailable, get_service


def _calendar(ctx):
    try:
        return get_service(ctx.config, "calendar", "v3")
    except GoogleUnavailable as exc:
        raise ToolError(str(exc)) from exc


def _tz(ctx) -> str:
    return getattr(ctx.config, "timezone", "UTC") or "UTC"


def _language(ctx) -> str:
    return getattr(getattr(ctx.config, "voice", None), "language", "en") or "en"


def _moment(ctx, expression: str, fallback: datetime | None = None) -> datetime:
    if not expression:
        if fallback is not None:
            return fallback
        raise ToolError("No time was given.")
    try:
        return parse_when(expression, tz_name=_tz(ctx))
    except TimeParseError as exc:
        raise ToolError(str(exc)) from exc


def _describe(ctx, event: dict) -> str:
    start = event.get("start", {})
    end = event.get("end", {})
    summary = event.get("summary", "(no title)")

    if "date" in start:  # an all-day event
        return f"{start['date']} (all day): {summary} [id {event.get('id', '')}]"

    started = datetime.fromisoformat(start.get("dateTime").replace("Z", "+00:00"))
    when = format_when(started, _tz(ctx), _language(ctx))
    duration = ""
    if end.get("dateTime"):
        finished = datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00"))
        minutes = int((finished - started).total_seconds() // 60)
        duration = f", {minutes} min"
    where = f", at {event['location']}" if event.get("location") else ""
    return f"{when}{duration}: {summary}{where} [id {event.get('id', '')}]"


@tool
def calendar_list(ctx, start: str = "", end: str = "", limit: int = 15, calendar_id: str = "primary") -> str:
    """Look at the user's calendar over a period.

    Args:
        start: Start of the window, e.g. "today", "tomorrow", ISO-8601.
            Defaults to now.
        end: End of the window, e.g. "+7d". Defaults to seven days after start.
        limit: Maximum number of events to return.
        calendar_id: Which calendar; "primary" is the user's own.
    """
    service = _calendar(ctx)
    begins = _moment(ctx, start, datetime.now(timezone.utc)) if start else datetime.now(timezone.utc)
    finishes = _moment(ctx, end, begins + timedelta(days=7)) if end else begins + timedelta(days=7)

    try:
        response = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=begins.isoformat(),
                timeMax=finishes.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=min(max(limit, 1), 50),
            )
            .execute()
        )
    except Exception as exc:
        raise ToolError(f"The calendar could not be read: {exc}") from exc

    events = response.get("items", [])
    if not events:
        return "Nothing is scheduled in that period."
    lines = [f"{len(events)} event(s):"]
    lines.extend(_describe(ctx, event) for event in events)
    return "\n".join(lines)


@tool
def calendar_create(
    ctx,
    title: str,
    start: str,
    duration_minutes: int = 60,
    end: str = "",
    description: str = "",
    location: str = "",
    attendees: list[str] = None,
    calendar_id: str = "primary",
) -> str:
    """Put a new appointment in the calendar.

    Args:
        title: What the appointment is called.
        start: When it starts: ISO-8601 or "tomorrow 14:00", "+2h".
        duration_minutes: How long it runs, if no explicit end is given.
        end: Explicit end time; overrides duration_minutes.
        description: Longer notes for the entry.
        location: Where it takes place.
        attendees: Email addresses to invite.
        calendar_id: Which calendar to add it to.
    """
    service = _calendar(ctx)
    begins = _moment(ctx, start)
    finishes = _moment(ctx, end, None) if end else begins + timedelta(minutes=max(duration_minutes, 1))
    if finishes <= begins:
        raise ToolError("The end of the appointment is not after its start.")

    zone = _tz(ctx)
    body = {
        "summary": title,
        "start": {"dateTime": begins.astimezone(resolve_zone(zone)).isoformat(), "timeZone": zone},
        "end": {"dateTime": finishes.astimezone(resolve_zone(zone)).isoformat(), "timeZone": zone},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": address} for address in attendees]

    try:
        event = service.events().insert(calendarId=calendar_id, body=body, sendUpdates="all" if attendees else "none").execute()
    except Exception as exc:
        raise ToolError(f"The appointment could not be created: {exc}") from exc

    return f"Created: {_describe(ctx, event)}"


@tool
def calendar_update(
    ctx,
    event_id: str,
    title: str = "",
    start: str = "",
    duration_minutes: int = 0,
    location: str = "",
    calendar_id: str = "primary",
) -> str:
    """Change an existing appointment. Get the id from calendar_list first.

    Args:
        event_id: The id of the event to change.
        title: New title, if it should change.
        start: New start time, if it should move.
        duration_minutes: New length in minutes.
        location: New location.
        calendar_id: Which calendar the event lives in.
    """
    service = _calendar(ctx)
    try:
        event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    except Exception as exc:
        raise ToolError(f"No such appointment: {exc}") from exc

    if title:
        event["summary"] = title
    if location:
        event["location"] = location

    zone = _tz(ctx)
    if start:
        begins = _moment(ctx, start)
        old_start = event.get("start", {}).get("dateTime")
        old_end = event.get("end", {}).get("dateTime")
        length = timedelta(minutes=duration_minutes or 60)
        if old_start and old_end and not duration_minutes:
            length = datetime.fromisoformat(old_end.replace("Z", "+00:00")) - datetime.fromisoformat(
                old_start.replace("Z", "+00:00")
            )
        event["start"] = {"dateTime": begins.astimezone(resolve_zone(zone)).isoformat(), "timeZone": zone}
        event["end"] = {"dateTime": (begins + length).astimezone(resolve_zone(zone)).isoformat(), "timeZone": zone}
    elif duration_minutes:
        old_start = event.get("start", {}).get("dateTime")
        if not old_start:
            raise ToolError("An all-day event has no length to change.")
        begins = datetime.fromisoformat(old_start.replace("Z", "+00:00"))
        event["end"] = {
            "dateTime": (begins + timedelta(minutes=duration_minutes)).astimezone(resolve_zone(zone)).isoformat(),
            "timeZone": zone,
        }

    try:
        updated = service.events().update(calendarId=calendar_id, eventId=event_id, body=event).execute()
    except Exception as exc:
        raise ToolError(f"The appointment could not be changed: {exc}") from exc
    return f"Updated: {_describe(ctx, updated)}"


@tool(confirm="Shall I delete the appointment {event_id}?")
def calendar_delete(ctx, event_id: str, calendar_id: str = "primary") -> str:
    """Delete an appointment. Asks the user for confirmation first.

    Args:
        event_id: The id of the event, as shown by calendar_list.
        calendar_id: Which calendar the event lives in.
    """
    service = _calendar(ctx)
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    except Exception as exc:
        raise ToolError(f"The appointment could not be deleted: {exc}") from exc
    return f"Appointment {event_id} deleted."


@tool
def calendar_find_slot(ctx, duration_minutes: int = 60, start: str = "", end: str = "", earliest_hour: int = 9, latest_hour: int = 18) -> str:
    """Find free slots in the calendar for a meeting of a given length.

    Args:
        duration_minutes: How much uninterrupted time is needed.
        start: Beginning of the search window. Defaults to now.
        end: End of the search window. Defaults to seven days out.
        earliest_hour: Do not suggest anything before this hour, local time.
        latest_hour: Everything must finish by this hour, local time.
    """
    service = _calendar(ctx)
    zone = resolve_zone(_tz(ctx))
    begins = _moment(ctx, start, datetime.now(timezone.utc)) if start else datetime.now(timezone.utc)
    finishes = _moment(ctx, end, begins + timedelta(days=7)) if end else begins + timedelta(days=7)

    try:
        response = service.freebusy().query(
            body={
                "timeMin": begins.isoformat(),
                "timeMax": finishes.isoformat(),
                "items": [{"id": "primary"}],
            }
        ).execute()
    except Exception as exc:
        raise ToolError(f"Availability could not be read: {exc}") from exc

    busy = [
        (
            datetime.fromisoformat(block["start"].replace("Z", "+00:00")),
            datetime.fromisoformat(block["end"].replace("Z", "+00:00")),
        )
        for block in response.get("calendars", {}).get("primary", {}).get("busy", [])
    ]
    busy.sort()

    needed = timedelta(minutes=max(duration_minutes, 1))
    slots: list[str] = []
    cursor = max(begins, datetime.now(timezone.utc))

    def within_hours(moment: datetime) -> datetime:
        """Push a moment forward to the next acceptable working hour."""
        local = moment.astimezone(zone)
        if local.hour < earliest_hour:
            local = local.replace(hour=earliest_hour, minute=0, second=0, microsecond=0)
        elif local.hour >= latest_hour:
            local = (local + timedelta(days=1)).replace(hour=earliest_hour, minute=0, second=0, microsecond=0)
        return local.astimezone(timezone.utc)

    for block_start, block_end in busy + [(finishes, finishes)]:
        cursor = within_hours(cursor)
        while cursor + needed <= min(block_start, finishes):
            local_end = (cursor + needed).astimezone(zone)
            if local_end.hour > latest_hour or (local_end.hour == latest_hour and local_end.minute > 0):
                cursor = within_hours(cursor + timedelta(hours=1))
                continue
            slots.append(format_when(cursor, _tz(ctx), _language(ctx)))
            if len(slots) >= 5:
                return "Free slots: " + "; ".join(slots)
            cursor = cursor + needed
        cursor = max(cursor, block_end)

    if not slots:
        return "No free slot of that length in the window."
    return "Free slots: " + "; ".join(slots)


CALENDAR_TOOLS: list[Tool] = [
    calendar_list,
    calendar_create,
    calendar_update,
    calendar_delete,
    calendar_find_slot,
]

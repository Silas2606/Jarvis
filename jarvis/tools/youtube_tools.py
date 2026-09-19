"""YouTube channel performance, from the Analytics API.

YouTube Studio is a JavaScript application behind a login: its numbers are not
in the page source, and scraping them would break with every redesign. The
Analytics API hands the same figures over as data -- which is also what makes
them summarisable out loud.

Read-only throughout. Nothing here can change a video, a title or a comment.
"""

from __future__ import annotations

from datetime import date, timedelta

from jarvis.tools.base import Tool, ToolError, tool
from jarvis.tools.google_auth import GoogleUnavailable, get_service

ANALYTICS_API = ("youtubeAnalytics", "v2")
DATA_API = ("youtube", "v3")

# Where each API is switched on. A 403 that says "not configured" means the
# project never enabled it, which no amount of re-authorising will fix -- so
# the message has to say which one and where.
ENABLE_LINKS = {
    "youtubeAnalytics": (
        "YouTube Analytics API",
        "https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com",
    ),
    "youtube": (
        "YouTube Data API v3",
        "https://console.cloud.google.com/apis/library/youtube.googleapis.com",
    ),
}

# The figures a person actually asks about, in the order they ask.
CHANNEL_METRICS = (
    "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
    "subscribersGained,subscribersLost,likes,comments,shares"
)
VIDEO_METRICS = "views,estimatedMinutesWatched,averageViewDuration,likes,comments"

READABLE = {
    "views": "Aufrufe",
    "estimatedMinutesWatched": "Wiedergabeminuten",
    "averageViewDuration": "Ø Wiedergabedauer (Sek.)",
    "averageViewPercentage": "Ø gesehener Anteil (%)",
    "subscribersGained": "Neue Abonnenten",
    "subscribersLost": "Verlorene Abonnenten",
    "likes": "Likes",
    "comments": "Kommentare",
    "shares": "Geteilt",
}


def _analytics(ctx):
    try:
        return get_service(ctx.config, *ANALYTICS_API)
    except GoogleUnavailable as exc:
        raise ToolError(str(exc)) from exc


def _data(ctx):
    try:
        return get_service(ctx.config, *DATA_API)
    except GoogleUnavailable as exc:
        raise ToolError(str(exc)) from exc


def _window(days: int) -> tuple[str, str]:
    """A date range ending yesterday -- today's figures are still settling."""
    days = min(max(days, 1), 365)
    end = date.today() - timedelta(days=1)
    return (end - timedelta(days=days - 1)).isoformat(), end.isoformat()


def _explain(exc: Exception, api: str) -> ToolError:
    """Turn a Google API failure into the step that fixes it.

    The three failures here need three different actions, and the raw error
    distinguishes them poorly: a missing scope needs re-authorising, a
    disabled API needs a click in the console, and a missing channel needs
    neither.
    """
    message = str(exc)
    lowered = message.lower()

    if "accessnotconfigured" in lowered or "has not been used in project" in lowered:
        name, link = ENABLE_LINKS.get(api, ("the YouTube API", ""))
        return ToolError(
            f"The {name} is not enabled in your Google Cloud project. "
            f"Enable it at {link} and try again in a minute. "
            "Re-authorising will not help."
        )
    if "403" in message and ("scope" in lowered or "insufficientpermissions" in lowered):
        return ToolError(
            "The YouTube permission is missing. Run `jarvis setup google` again "
            "to grant it."
        )
    if "quotaexceeded" in lowered or "429" in message:
        return ToolError("The YouTube API quota for today is used up.")
    if "channelnotfound" in lowered or "no channel" in lowered:
        return ToolError(
            "That Google account has no YouTube channel. Sign in with the account "
            "that owns the channel: `jarvis setup google`."
        )
    return ToolError(f"YouTube could not be read: {message}")


def _query(ctx, **params):
    service = _analytics(ctx)
    try:
        return service.reports().query(ids="channel==MINE", **params).execute()
    except Exception as exc:
        raise _explain(exc, "youtubeAnalytics") from exc


def _rows(response) -> tuple[list[str], list[list]]:
    headers = [column["name"] for column in response.get("columnHeaders", [])]
    return headers, response.get("rows", [])


def _titles(ctx, video_ids: list[str]) -> dict[str, str]:
    """Resolve video ids to titles, so a summary can name them."""
    if not video_ids:
        return {}
    try:
        response = (
            _data(ctx).videos().list(part="snippet", id=",".join(video_ids[:50])).execute()
        )
    except Exception:
        return {}  # numbers without titles still beat no answer
    return {
        item["id"]: item.get("snippet", {}).get("title", "")
        for item in response.get("items", [])
    }


def _number(value) -> str:
    """German thousands separators, so the figures read naturally aloud."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == int(number):
        return f"{int(number):,}".replace(",", ".")
    return f"{number:,.1f}".replace(",", "#").replace(".", ",").replace("#", ".")


@tool
def youtube_channel_stats(ctx, days: int = 28) -> str:
    """How the whole channel has performed over a period.

    Use this for questions like "wie läuft mein Kanal" or "wie waren die
    letzten vier Wochen".

    Args:
        days: How many days back to look. 28 is YouTube's own default.
    """
    start, end = _window(days)
    response = _query(ctx, startDate=start, endDate=end, metrics=CHANNEL_METRICS)
    headers, rows = _rows(response)
    if not rows:
        return f"No data between {start} and {end}."

    values = dict(zip(headers, rows[0]))
    gained = values.get("subscribersGained", 0)
    lost = values.get("subscribersLost", 0)

    lines = [f"Channel, {start} to {end} ({days} days):"]
    for key, label in READABLE.items():
        if key in values and key not in ("subscribersGained", "subscribersLost"):
            lines.append(f"{label}: {_number(values[key])}")
    lines.append(
        f"Abonnenten: {_number(gained)} gewonnen, {_number(lost)} verloren "
        f"(netto {_number(gained - lost)})"
    )
    return "\n".join(lines)


@tool
def youtube_top_videos(ctx, days: int = 28, limit: int = 10) -> str:
    """The best-performing videos of a period, with their figures.

    Use this for "welches Video lief am besten" or "wie liefen meine letzten
    Videos".

    Args:
        days: How many days back to look.
        limit: How many videos to list.
    """
    start, end = _window(days)
    response = _query(
        ctx,
        startDate=start,
        endDate=end,
        metrics=VIDEO_METRICS,
        dimensions="video",
        sort="-views",
        maxResults=min(max(limit, 1), 50),
    )
    headers, rows = _rows(response)
    if not rows:
        return f"No video data between {start} and {end}."

    index = {name: position for position, name in enumerate(headers)}
    ids = [row[index["video"]] for row in rows]
    titles = _titles(ctx, ids)

    lines = [f"Top {len(rows)} videos, {start} to {end}:"]
    for position, row in enumerate(rows, start=1):
        video_id = row[index["video"]]
        title = titles.get(video_id, video_id)
        views = _number(row[index["views"]])
        minutes = _number(row[index["estimatedMinutesWatched"]])
        duration = _number(row[index["averageViewDuration"]])
        lines.append(
            f"{position}. {title} — {views} Aufrufe, {minutes} Wiedergabeminuten, "
            f"Ø {duration} Sek. gesehen"
        )
    return "\n".join(lines)


@tool
def youtube_traffic_sources(ctx, days: int = 28) -> str:
    """Where the views came from: search, suggested, browse, external.

    Use this for "woher kommen meine Aufrufe".

    Args:
        days: How many days back to look.
    """
    start, end = _window(days)
    response = _query(
        ctx,
        startDate=start,
        endDate=end,
        metrics="views,estimatedMinutesWatched",
        dimensions="insightTrafficSourceType",
        sort="-views",
        maxResults=12,
    )
    headers, rows = _rows(response)
    if not rows:
        return f"No traffic data between {start} and {end}."

    index = {name: position for position, name in enumerate(headers)}
    total = sum(row[index["views"]] for row in rows) or 1
    lines = [f"Traffic sources, {start} to {end}:"]
    for row in rows:
        source = row[index["insightTrafficSourceType"]]
        views = row[index["views"]]
        lines.append(f"{source}: {_number(views)} Aufrufe ({views / total * 100:.0f} %)")
    return "\n".join(lines)


@tool
def youtube_daily(ctx, days: int = 14) -> str:
    """Views day by day, to see a trend rather than a single number.

    Args:
        days: How many days back to look.
    """
    start, end = _window(days)
    response = _query(
        ctx,
        startDate=start,
        endDate=end,
        metrics="views,subscribersGained",
        dimensions="day",
        sort="day",
    )
    headers, rows = _rows(response)
    if not rows:
        return f"No data between {start} and {end}."

    index = {name: position for position, name in enumerate(headers)}
    lines = [f"Daily views, {start} to {end}:"]
    for row in rows:
        lines.append(
            f"{row[index['day']]}: {_number(row[index['views']])} Aufrufe, "
            f"+{_number(row[index['subscribersGained']])} Abos"
        )
    return "\n".join(lines)


@tool
def youtube_recent_uploads(ctx, limit: int = 10) -> str:
    """The channel's most recent uploads, with publication dates.

    Useful for tying performance figures to what was actually posted.

    Args:
        limit: How many videos to list.
    """
    service = _data(ctx)
    try:
        channels = service.channels().list(part="contentDetails", mine=True).execute()
        items = channels.get("items", [])
        if not items:
            raise ToolError("No YouTube channel is attached to this account.")
        uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        playlist = (
            service.playlistItems()
            .list(part="snippet", playlistId=uploads, maxResults=min(max(limit, 1), 50))
            .execute()
        )
    except ToolError:
        raise
    except Exception as exc:
        raise _explain(exc, "youtube") from exc

    entries = playlist.get("items", [])
    if not entries:
        return "No uploads found."
    lines = [f"{len(entries)} most recent uploads:"]
    for entry in entries:
        snippet = entry.get("snippet", {})
        published = snippet.get("publishedAt", "")[:10]
        lines.append(f"{published}: {snippet.get('title', '?')}")
    return "\n".join(lines)


YOUTUBE_TOOLS: list[Tool] = [
    youtube_channel_stats,
    youtube_top_videos,
    youtube_traffic_sources,
    youtube_daily,
    youtube_recent_uploads,
]

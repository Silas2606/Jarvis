"""Channel analytics.

The API is mocked; what is tested is the query built for it and the wording
that comes back, since these answers are read aloud.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from jarvis.tools.base import ToolContext, ToolError
from jarvis.tools.youtube_tools import _number, _window


@pytest.fixture
def analytics(monkeypatch):
    """Records the queries and answers with a fixed report."""
    queries: list[dict] = []

    report = {
        "columnHeaders": [
            {"name": "views"},
            {"name": "estimatedMinutesWatched"},
            {"name": "averageViewDuration"},
            {"name": "averageViewPercentage"},
            {"name": "subscribersGained"},
            {"name": "subscribersLost"},
            {"name": "likes"},
            {"name": "comments"},
            {"name": "shares"},
        ],
        "rows": [[12345, 48210, 234, 41.5, 180, 23, 640, 88, 42]],
    }

    class Reports:
        def query(self, **params):
            queries.append(params)
            return self

        def execute(self):
            return report

    class Service:
        def reports(self):
            return Reports()

    monkeypatch.setattr("jarvis.tools.youtube_tools._analytics", lambda ctx: Service())
    monkeypatch.setattr("jarvis.tools.youtube_tools._titles", lambda ctx, ids: {})
    return queries


@pytest.fixture
def ctx(config):
    return ToolContext(config=config)


# -- the reporting window ----------------------------------------------------


def test_the_window_ends_yesterday():
    """Today's figures are still settling; including them misleads."""
    start, end = _window(28)
    assert end == (date.today() - timedelta(days=1)).isoformat()
    assert start == (date.today() - timedelta(days=28)).isoformat()


def test_absurd_windows_are_clamped():
    assert _window(0)[0] == _window(1)[0]
    assert _window(9999)[0] == _window(365)[0]


# -- numbers that get spoken -------------------------------------------------


def test_numbers_are_grouped_the_german_way():
    """These are read aloud; "12345" is not how a person says it."""
    assert _number(12345) == "12.345"
    assert _number(1234567) == "1.234.567"
    assert _number(45.67) == "45,7"
    assert _number(0) == "0"


# -- channel figures ---------------------------------------------------------


def test_channel_stats_ask_for_the_right_metrics(analytics, ctx):
    from jarvis.tools.youtube_tools import youtube_channel_stats

    youtube_channel_stats.func(ctx, days=28)

    query = analytics[0]
    assert query["startDate"] == _window(28)[0]
    assert "views" in query["metrics"]
    assert "subscribersGained" in query["metrics"]


def test_channel_stats_report_net_subscribers(analytics, ctx):
    """Gained and lost separately is data; the net figure is the answer."""
    from jarvis.tools.youtube_tools import youtube_channel_stats

    result = youtube_channel_stats.func(ctx, days=28)

    assert "12.345" in result           # views, grouped
    assert "180 gewonnen" in result
    assert "23 verloren" in result
    assert "netto 157" in result


def test_top_videos_are_sorted_by_views_and_named(monkeypatch, ctx):
    from jarvis.tools import youtube_tools

    captured: list[dict] = []

    class Reports:
        def query(self, **params):
            captured.append(params)
            return self

        def execute(self):
            return {
                "columnHeaders": [
                    {"name": "video"},
                    {"name": "views"},
                    {"name": "estimatedMinutesWatched"},
                    {"name": "averageViewDuration"},
                    {"name": "likes"},
                    {"name": "comments"},
                ],
                "rows": [
                    ["vid111", 9000, 32000, 210, 400, 30],
                    ["vid222", 4000, 12000, 180, 150, 12],
                ],
            }

    monkeypatch.setattr(youtube_tools, "_analytics", lambda ctx: type("S", (), {"reports": lambda self: Reports()})())
    monkeypatch.setattr(
        youtube_tools, "_titles", lambda ctx, ids: {"vid111": "Reaktor-Bau Teil 3", "vid222": "Kurzes Update"}
    )

    result = youtube_tools.youtube_top_videos.func(ctx, days=28, limit=10)

    assert captured[0]["sort"] == "-views"
    assert captured[0]["dimensions"] == "video"
    # Titles, not opaque ids, because this gets spoken.
    assert "Reaktor-Bau Teil 3" in result
    assert "vid111" not in result
    assert result.index("Reaktor-Bau Teil 3") < result.index("Kurzes Update")


def test_titles_that_cannot_be_resolved_fall_back_to_ids(monkeypatch, ctx):
    """Numbers without titles still beat no answer at all."""
    from jarvis.tools import youtube_tools

    class Reports:
        def query(self, **params):
            return self

        def execute(self):
            return {
                "columnHeaders": [
                    {"name": "video"}, {"name": "views"},
                    {"name": "estimatedMinutesWatched"}, {"name": "averageViewDuration"},
                    {"name": "likes"}, {"name": "comments"},
                ],
                "rows": [["vid111", 9000, 32000, 210, 400, 30]],
            }

    monkeypatch.setattr(youtube_tools, "_analytics", lambda ctx: type("S", (), {"reports": lambda self: Reports()})())
    monkeypatch.setattr(youtube_tools, "_titles", lambda ctx, ids: {})

    assert "vid111" in youtube_tools.youtube_top_videos.func(ctx, days=7)


def test_a_missing_permission_says_how_to_grant_it(monkeypatch, ctx):
    """The scope was added later, so an old token gives a 403."""
    from jarvis.tools import youtube_tools

    class Reports:
        def query(self, **params):
            return self

        def execute(self):
            raise RuntimeError("<HttpError 403 ... insufficient authentication scope>")

    monkeypatch.setattr(youtube_tools, "_analytics", lambda ctx: type("S", (), {"reports": lambda self: Reports()})())

    with pytest.raises(ToolError) as caught:
        youtube_tools.youtube_channel_stats.func(ctx, days=7)
    assert "jarvis setup google" in str(caught.value)


def test_an_empty_period_is_reported_not_faked(analytics, ctx, monkeypatch):
    from jarvis.tools import youtube_tools

    class Reports:
        def query(self, **params):
            return self

        def execute(self):
            return {"columnHeaders": [], "rows": []}

    monkeypatch.setattr(youtube_tools, "_analytics", lambda ctx: type("S", (), {"reports": lambda self: Reports()})())
    assert "No data" in youtube_tools.youtube_channel_stats.func(ctx, days=7)


# -- telling the three failures apart ----------------------------------------


def test_a_disabled_api_names_itself_and_the_place_to_enable_it():
    """Regression: this arrived as an opaque 403 with a bare URL.

    "accessNotConfigured" means the project never switched the API on. It
    looks like a permission error and is not one -- re-authorising forever
    would never fix it.
    """
    from jarvis.tools.youtube_tools import _explain

    failure = RuntimeError(
        '<HttpError 403 when requesting https://youtube.googleapis.com/... returned '
        '"YouTube Data API v3 has not been used in project 123 before or it is '
        'disabled". Details: "accessNotConfigured">'
    )
    problem = _explain(failure, "youtube")

    assert "not enabled" in str(problem)
    assert "console.cloud.google.com/apis/library/youtube.googleapis.com" in str(problem)
    # And it says plainly that the other remedy is the wrong one.
    assert "Re-authorising will not help" in str(problem)


def test_the_analytics_api_points_at_its_own_page():
    from jarvis.tools.youtube_tools import _explain

    problem = _explain(RuntimeError("403 accessNotConfigured"), "youtubeAnalytics")
    assert "youtubeanalytics.googleapis.com" in str(problem)


def test_a_missing_scope_still_asks_for_re_authorisation():
    from jarvis.tools.youtube_tools import _explain

    problem = _explain(RuntimeError('403 ... reason "insufficientPermissions"'), "youtube")
    assert "jarvis setup google" in str(problem)
    assert "not enabled" not in str(problem)


def test_an_account_without_a_channel_is_told_so():
    from jarvis.tools.youtube_tools import _explain

    problem = _explain(RuntimeError("channelNotFound"), "youtube")
    assert "no YouTube channel" in str(problem)


def test_an_exhausted_quota_is_not_mistaken_for_a_setup_problem():
    from jarvis.tools.youtube_tools import _explain

    assert "quota" in str(_explain(RuntimeError("quotaExceeded"), "youtube")).lower()

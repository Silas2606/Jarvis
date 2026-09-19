"""Microsoft To Do as the task backend.

Graph itself is mocked; what is tested is everything around it -- that the
tools carry the same names as the local ones, that the right list is chosen,
and that a spoken due date arrives as something To Do accepts.
"""

from __future__ import annotations

import pytest

from jarvis.tools.base import ToolContext, ToolError


@pytest.fixture
def graph(monkeypatch):
    """A recording stand-in for the Graph API."""
    calls: list[tuple[str, str, dict | None]] = []

    lists = {
        "value": [
            {"id": "list-work", "displayName": "Arbeit"},
            {"id": "list-main", "displayName": "Aufgaben", "wellknownListName": "defaultList"},
        ]
    }
    tasks = {
        "value": [
            {"id": "task-aaaa1111", "title": "Reaktor prüfen", "status": "notStarted",
             "dueDateTime": {"dateTime": "2026-09-20T09:00:00.0000000", "timeZone": "UTC"}},
            {"id": "task-bbbb2222", "title": "Espresso kaufen", "status": "notStarted"},
        ]
    }

    def fake(config, method, path, payload=None):
        calls.append((method, path, payload))
        if path == "/me/todo/lists":
            return lists
        if "/tasks" in path and method == "GET":
            return tasks
        if method == "POST":
            return {"id": "task-new00000", "title": payload.get("title"), "status": "notStarted",
                    **({"dueDateTime": payload["dueDateTime"]} if "dueDateTime" in payload else {})}
        return {}

    monkeypatch.setattr("jarvis.tools.microsoft_tools.graph", fake)
    # The default-list cache is module level; a stale id would leak between tests.
    monkeypatch.setattr("jarvis.tools.microsoft_tools._default_list", {})
    return calls


@pytest.fixture
def ctx(config):
    return ToolContext(config=config)


def test_the_tools_match_the_local_ones_by_name():
    """Switching backend must not make the model relearn anything."""
    from jarvis.tools.memory_tools import TASK_TOOLS
    from jarvis.tools.microsoft_tools import MICROSOFT_TASK_TOOLS

    local = {t.name for t in TASK_TOOLS}
    microsoft = {t.name for t in MICROSOFT_TASK_TOOLS}
    assert local <= microsoft


def test_a_task_lands_in_the_default_list(graph, ctx):
    from jarvis.tools.microsoft_tools import task_add

    result = task_add.func(ctx, title="Reaktor kalibrieren")

    posts = [c for c in graph if c[0] == "POST"]
    assert len(posts) == 1
    method, path, payload = posts[0]
    # The list marked as the well-known default, not simply the first one.
    assert path == "/me/todo/lists/list-main/tasks"
    assert payload["title"] == "Reaktor kalibrieren"
    assert "Microsoft To Do" in result


def test_a_named_list_is_used_when_given(graph, ctx):
    from jarvis.tools.microsoft_tools import task_add

    task_add.func(ctx, title="Angebot schreiben", list_name="Arbeit")
    assert any(c[1] == "/me/todo/lists/list-work/tasks" for c in graph if c[0] == "POST")


def test_an_unknown_list_says_which_ones_exist(graph, ctx):
    from jarvis.tools.microsoft_tools import task_add

    with pytest.raises(ToolError) as caught:
        task_add.func(ctx, title="x", list_name="Urlaub")
    assert "Arbeit" in str(caught.value) and "Aufgaben" in str(caught.value)


def test_a_spoken_due_date_becomes_a_graph_timestamp(graph, ctx):
    from jarvis.tools.microsoft_tools import task_add

    task_add.func(ctx, title="Zahnarzt", due="morgen 09:00")

    payload = [c[2] for c in graph if c[0] == "POST"][0]
    due = payload["dueDateTime"]
    # Graph wants a naive local timestamp plus a named zone, not an offset.
    assert due["timeZone"] == "Europe/Berlin"
    assert due["dateTime"].endswith("T09:00:00")
    assert "+" not in due["dateTime"] and "Z" not in due["dateTime"]


def test_an_unparseable_due_date_never_reaches_the_api(graph, ctx):
    from jarvis.tools.microsoft_tools import task_add

    with pytest.raises(ToolError):
        task_add.func(ctx, title="x", due="irgendwann bald")
    assert not [c for c in graph if c[0] == "POST"]


def test_open_tasks_are_listed_with_their_due_dates(graph, ctx):
    from jarvis.tools.microsoft_tools import task_list

    result = task_list.func(ctx)
    assert "Reaktor prüfen" in result and "Espresso kaufen" in result
    # Finished tasks are filtered out at the API, not after the fact.
    assert any("status ne 'completed'" in c[1] for c in graph if c[0] == "GET")


def test_a_task_can_be_completed_by_title(graph, ctx):
    """Speech gives titles, not ids -- so the title has to work."""
    from jarvis.tools.microsoft_tools import task_complete

    result = task_complete.func(ctx, task_id="Espresso")

    patches = [c for c in graph if c[0] == "PATCH"]
    assert patches and patches[0][1].endswith("/tasks/task-bbbb2222")
    assert patches[0][2] == {"status": "completed"}
    assert "Espresso kaufen" in result


def test_an_ambiguous_title_asks_rather_than_guessing(graph, ctx, monkeypatch):
    from jarvis.tools import microsoft_tools

    monkeypatch.setattr(
        microsoft_tools,
        "graph",
        lambda c, m, p, payload=None: (
            {"value": [{"id": "l1", "displayName": "Aufgaben", "wellknownListName": "defaultList"}]}
            if p == "/me/todo/lists"
            else {"value": [
                {"id": "t1", "title": "Bericht Montag", "status": "notStarted"},
                {"id": "t2", "title": "Bericht Freitag", "status": "notStarted"},
            ]}
        ),
    )
    with pytest.raises(ToolError) as caught:
        microsoft_tools.task_complete.func(ctx, task_id="Bericht")
    assert "several tasks" in str(caught.value)


def test_the_registry_swaps_backends_without_changing_names(config, store, bus):
    from jarvis.tools import build_registry

    config.tools.tasks_backend = "local"
    local = build_registry(config, store, bus)
    config.tools.tasks_backend = "microsoft"
    microsoft = build_registry(config, store, bus)

    assert "task_add" in local and "task_add" in microsoft
    # Same name, different implementation -- and never both at once.
    assert local.tools["task_add"].func.__module__.endswith("memory_tools")
    assert microsoft.tools["task_add"].func.__module__.endswith("microsoft_tools")

"""Microsoft To Do, exposed under the same tool names as the local list.

Deliberately identical in name and signature to the tools in memory_tools:
when To Do is the chosen backend, "put that on the list" should reach it
without the model needing to know that anything changed. One set of names,
two possible destinations, chosen by configuration.
"""

from __future__ import annotations

from datetime import datetime, timezone

from jarvis.timing import TimeParseError, format_when, parse_when, resolve_zone
from jarvis.tools.base import Tool, ToolError, tool
from jarvis.tools.microsoft_auth import MicrosoftUnavailable, graph

# The default list, once found. To Do always has one and it is where a task
# belongs unless the user says otherwise.
_default_list: dict[str, str] = {}


def _call(ctx, method: str, path: str, payload: dict | None = None):
    try:
        return graph(ctx.config, method, path, payload)
    except MicrosoftUnavailable as exc:
        raise ToolError(str(exc)) from exc


def _tz(ctx) -> str:
    return getattr(ctx.config, "timezone", "UTC") or "UTC"


def _language(ctx) -> str:
    return getattr(getattr(ctx.config, "voice", None), "language", "en") or "en"


def _lists(ctx) -> list[dict]:
    return _call(ctx, "GET", "/me/todo/lists").get("value", [])


def _list_id(ctx, name: str = "") -> str:
    """The id of the named list, or of the default one."""
    lists = _lists(ctx)
    if not lists:
        raise ToolError("There are no lists in Microsoft To Do.")

    if name:
        folded = name.strip().casefold()
        for entry in lists:
            if entry.get("displayName", "").casefold() == folded:
                return entry["id"]
        for entry in lists:
            if entry.get("displayName", "").casefold().startswith(folded):
                return entry["id"]
        available = ", ".join(entry.get("displayName", "?") for entry in lists[:8])
        raise ToolError(f"No list called {name!r}. Available: {available}")

    cached = _default_list.get("id")
    if cached:
        return cached
    for entry in lists:
        if entry.get("wellknownListName") == "defaultList":
            _default_list["id"] = entry["id"]
            return entry["id"]
    _default_list["id"] = lists[0]["id"]
    return lists[0]["id"]


def _due(task: dict) -> datetime | None:
    due = task.get("dueDateTime")
    if not isinstance(due, dict) or not due.get("dateTime"):
        return None
    try:
        stamp = datetime.fromisoformat(due["dateTime"].replace("Z", "").split(".")[0])
    except ValueError:
        return None
    return stamp.replace(tzinfo=timezone.utc)


def _describe(ctx, task: dict) -> str:
    state = "done" if task.get("status") == "completed" else "open"
    due = _due(task)
    when = f", due {format_when(due, _tz(ctx), _language(ctx))}" if due else ""
    importance = task.get("importance", "normal")
    flag = f" [{importance}]" if importance != "normal" else ""
    return f"#{task.get('id', '')[:8]} ({state}){flag}: {task.get('title', '?')}{when}"


def _find_task(ctx, list_id: str, needle: str) -> dict:
    """Locate a task by the short id shown to the model, or by its title."""
    tasks = _call(ctx, "GET", f"/me/todo/lists/{list_id}/tasks?$top=200").get("value", [])
    needle = needle.strip()

    for task in tasks:
        if task.get("id", "").startswith(needle) or task.get("id") == needle:
            return task
    folded = needle.casefold()
    matches = [t for t in tasks if folded in t.get("title", "").casefold()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        titles = ", ".join(t.get("title", "?") for t in matches[:5])
        raise ToolError(f"{needle!r} matches several tasks: {titles}. Be more specific.")
    raise ToolError(f"No task matching {needle!r}.")


# -- the tools ---------------------------------------------------------------


@tool
def task_add(ctx, title: str, details: str = "", due: str = "", priority: str = "normal", list_name: str = "") -> str:
    """Add something to the user's to-do list.

    Args:
        title: Short description of the task.
        details: Any extra context worth keeping.
        due: When it is due, as ISO-8601 or a phrase like "tomorrow 09:00",
            "in 2 hours", "+3d". Leave empty if there is no deadline.
        priority: One of "low", "normal", "high".
        list_name: A specific To Do list. Defaults to the main list.
    """
    payload: dict = {"title": title.strip()}
    if details:
        payload["body"] = {"content": details, "contentType": "text"}
    if priority in ("low", "high"):
        payload["importance"] = priority
    if due:
        try:
            moment = parse_when(due, tz_name=_tz(ctx))
        except TimeParseError as exc:
            raise ToolError(str(exc)) from exc
        local = moment.astimezone(resolve_zone(_tz(ctx)))
        payload["dueDateTime"] = {
            "dateTime": local.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": _tz(ctx),
        }

    list_id = _list_id(ctx, list_name)
    created = _call(ctx, "POST", f"/me/todo/lists/{list_id}/tasks", payload)
    return f"Added to Microsoft To Do: {_describe(ctx, created)}"


@tool
def task_list(ctx, include_done: bool = False, limit: int = 20, list_name: str = "") -> str:
    """List the user's tasks, the most urgent first.

    Args:
        include_done: Also show tasks that are already finished.
        limit: Maximum number of tasks to return.
        list_name: A specific To Do list. Defaults to the main list.
    """
    list_id = _list_id(ctx, list_name)
    query = f"/me/todo/lists/{list_id}/tasks?$top={min(max(limit, 1), 100)}"
    if not include_done:
        query += "&$filter=status ne 'completed'"
    tasks = _call(ctx, "GET", query).get("value", [])

    if not tasks:
        return "The to-do list is empty."
    # Due first, soonest at the top; the rest after.
    tasks.sort(key=lambda t: (_due(t) is None, _due(t) or datetime.max.replace(tzinfo=timezone.utc)))
    lines = [f"{len(tasks)} task(s) in Microsoft To Do:"]
    lines.extend(_describe(ctx, task) for task in tasks)
    return "\n".join(lines)


@tool
def task_complete(ctx, task_id: str, list_name: str = "") -> str:
    """Tick a task off as finished.

    Args:
        task_id: The id shown by task_list, or the task's title.
        list_name: A specific To Do list. Defaults to the main list.
    """
    list_id = _list_id(ctx, list_name)
    task = _find_task(ctx, list_id, task_id)
    _call(
        ctx, "PATCH", f"/me/todo/lists/{list_id}/tasks/{task['id']}", {"status": "completed"}
    )
    return f"Completed: {task.get('title', '?')}"


@tool
def task_find(ctx, query: str, list_name: str = "") -> str:
    """Find a task by words in its title, to get its id.

    Args:
        query: Part of the task title.
        list_name: A specific To Do list. Defaults to the main list.
    """
    list_id = _list_id(ctx, list_name)
    tasks = _call(ctx, "GET", f"/me/todo/lists/{list_id}/tasks?$top=200").get("value", [])
    folded = query.strip().casefold()
    matches = [t for t in tasks if folded in t.get("title", "").casefold()]
    if not matches:
        return f"No task matches {query!r}."
    return "\n".join(_describe(ctx, task) for task in matches[:15])


@tool
def task_delete(ctx, task_id: str, list_name: str = "") -> str:
    """Remove a task from the list entirely.

    Args:
        task_id: The id shown by task_list, or the task's title.
        list_name: A specific To Do list. Defaults to the main list.
    """
    list_id = _list_id(ctx, list_name)
    task = _find_task(ctx, list_id, task_id)
    _call(ctx, "DELETE", f"/me/todo/lists/{list_id}/tasks/{task['id']}")
    return f"Deleted: {task.get('title', '?')}"


@tool
def task_lists(ctx) -> str:
    """Show which To Do lists exist, so a task can be filed in the right one."""
    lists = _lists(ctx)
    if not lists:
        return "There are no lists in Microsoft To Do."
    names = []
    for entry in lists:
        mark = " (default)" if entry.get("wellknownListName") == "defaultList" else ""
        names.append(f"{entry.get('displayName', '?')}{mark}")
    return "Lists: " + "; ".join(names)


MICROSOFT_TASK_TOOLS: list[Tool] = [
    task_add,
    task_list,
    task_complete,
    task_find,
    task_delete,
    task_lists,
]

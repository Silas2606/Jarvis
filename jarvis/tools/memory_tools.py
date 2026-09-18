"""Notes, tasks, reminders and remembered facts, exposed as tools.

Results are written as short prose rather than JSON dumps: Claude has to read
them out loud, and a wall of braces makes for a poor spoken answer.
"""

from __future__ import annotations

from typing import Any

from jarvis.timing import TimeParseError, format_when, parse_when
from jarvis.tools.base import Tool, ToolContext, ToolError, tool


def _language(ctx: ToolContext) -> str:
    return getattr(getattr(ctx.config, "voice", None), "language", "en") or "en"


def _tz(ctx: ToolContext) -> str:
    return getattr(ctx.config, "timezone", "UTC") or "UTC"


def _due(ctx: ToolContext, value: Any) -> str:
    return format_when(value, _tz(ctx), _language(ctx))


# -- notes -------------------------------------------------------------------


@tool
def note_add(ctx, body: str, tags: list[str] = None) -> str:
    """Write down a note for the user so it can be recalled later.

    Args:
        body: The full text of the note, in the user's own words.
        tags: Optional short labels to file it under, e.g. ["work", "ideas"].
    """
    note = ctx.store.add_note(body, tags or [])
    label = f" filed under {', '.join(note.tags)}" if note.tags else ""
    return f"Note #{note.id} saved{label}."


@tool
def note_search(ctx, query: str = "", tag: str = "", limit: int = 10) -> str:
    """Look through saved notes.

    Args:
        query: Text to search for inside notes. Leave empty to list recent ones.
        tag: Restrict to notes filed under this label.
        limit: How many notes to return at most.
    """
    notes = ctx.store.search_notes(query, tag, min(max(limit, 1), 50))
    if not notes:
        return "No notes match that."
    lines = [f"{len(notes)} note(s):"]
    for note in notes:
        stamp = _due(ctx, note.created_at)
        lines.append(f"#{note.id} ({stamp}): {note.body}")
    return "\n".join(lines)


@tool
def note_delete(ctx, note_id: int) -> str:
    """Delete a note by its number.

    Args:
        note_id: The number shown when the note was listed.
    """
    if not ctx.store.delete_note(note_id):
        raise ToolError(f"There is no note #{note_id}.")
    return f"Note #{note_id} deleted."


# -- tasks -------------------------------------------------------------------


@tool
def task_add(ctx, title: str, details: str = "", due: str = "", priority: str = "normal") -> str:
    """Add something to the user's to-do list.

    Args:
        title: Short description of the task.
        details: Any extra context worth keeping.
        due: When it is due, as ISO-8601 or a phrase like "tomorrow 09:00",
            "in 2 hours", "+3d". Leave empty if there is no deadline.
        priority: One of "low", "normal", "high".
    """
    due_at = None
    if due:
        try:
            due_at = parse_when(due, tz_name=_tz(ctx))
        except TimeParseError as exc:
            raise ToolError(str(exc)) from exc
    task = ctx.store.add_task(title, details, due_at, priority)
    when = f", due {_due(ctx, task.due_at)}" if task.due_at else ""
    return f"Task #{task.id} added: {task.title}{when}."


@tool
def task_list(ctx, include_done: bool = False, limit: int = 20) -> str:
    """List the user's tasks, the most urgent first.

    Args:
        include_done: Also show tasks that are already finished.
        limit: Maximum number of tasks to return.
    """
    tasks = ctx.store.list_tasks(include_done, min(max(limit, 1), 100))
    if not tasks:
        return "The to-do list is empty."
    lines = [f"{len(tasks)} task(s):"]
    for task in tasks:
        state = "done" if task.done else "open"
        when = f", due {_due(ctx, task.due_at)}" if task.due_at else ""
        flag = f" [{task.priority}]" if task.priority != "normal" else ""
        lines.append(f"#{task.id} ({state}){flag}: {task.title}{when}")
    return "\n".join(lines)


@tool
def task_complete(ctx, task_id: int) -> str:
    """Tick a task off as finished.

    Args:
        task_id: The number of the task to complete.
    """
    task = ctx.store.complete_task(task_id)
    if task is None:
        raise ToolError(f"There is no open task #{task_id}.")
    return f"Task #{task.id} completed: {task.title}."


@tool
def task_find(ctx, query: str) -> str:
    """Find a task by words in its title, to get its number.

    Args:
        query: Part of the task title or details.
    """
    tasks = ctx.store.find_tasks(query)
    if not tasks:
        return f"No open task matches {query!r}."
    return "\n".join(f"#{t.id}: {t.title}" for t in tasks)


@tool
def task_delete(ctx, task_id: int) -> str:
    """Remove a task from the list entirely.

    Args:
        task_id: The number of the task to delete.
    """
    if not ctx.store.delete_task(task_id):
        raise ToolError(f"There is no task #{task_id}.")
    return f"Task #{task_id} deleted."


# -- reminders ---------------------------------------------------------------


@tool
def reminder_set(ctx, body: str, when: str) -> str:
    """Set a reminder. Jarvis will speak it aloud when it comes due.

    Args:
        body: What to remind the user about, phrased as you would say it.
        when: ISO-8601, or a phrase like "in 10 minutes", "+2h",
            "tomorrow 07:30".
    """
    try:
        due_at = parse_when(when, tz_name=_tz(ctx))
    except TimeParseError as exc:
        raise ToolError(str(exc)) from exc
    reminder = ctx.store.add_reminder(body, due_at)
    return f"Reminder #{reminder.id} set for {_due(ctx, due_at)}: {body}"


@tool
def reminder_list(ctx, include_fired: bool = False) -> str:
    """List reminders that have not gone off yet.

    Args:
        include_fired: Also include reminders that already fired.
    """
    reminders = ctx.store.list_reminders(include_fired)
    if not reminders:
        return "No reminders are pending."
    lines = [f"{len(reminders)} reminder(s):"]
    for item in reminders:
        lines.append(f"#{item.id} at {_due(ctx, item.due_at)}: {item.body}")
    return "\n".join(lines)


@tool
def reminder_cancel(ctx, reminder_id: int) -> str:
    """Cancel a pending reminder.

    Args:
        reminder_id: The number of the reminder to cancel.
    """
    if not ctx.store.cancel_reminder(reminder_id):
        raise ToolError(f"There is no reminder #{reminder_id}.")
    return f"Reminder #{reminder_id} cancelled."


# -- long-term facts ---------------------------------------------------------


@tool
def remember(ctx, key: str, value: str) -> str:
    """Remember a fact about the user, permanently, across restarts.

    Use this whenever the user says something worth keeping: preferences,
    names, addresses, how they take their coffee.

    Args:
        key: A short label for the fact, e.g. "home address", "coffee".
        value: The fact itself.
    """
    ctx.store.remember(key, value)
    return f"Remembered: {key} = {value}"


@tool
def recall(ctx, key: str = "") -> str:
    """Recall what is known about the user.

    Args:
        key: A specific label to look up. Leave empty to list everything known.
    """
    if key:
        value = ctx.store.recall(key)
        return f"{key}: {value}" if value else f"Nothing is remembered under {key!r}."
    facts = ctx.store.all_facts()
    if not facts:
        return "Nothing has been remembered yet."
    return "\n".join(f"{k}: {v}" for k, v in facts.items())


@tool
def forget(ctx, key: str) -> str:
    """Forget a remembered fact.

    Args:
        key: The label of the fact to drop.
    """
    if not ctx.store.forget(key):
        raise ToolError(f"Nothing is remembered under {key!r}.")
    return f"Forgotten: {key}"


NOTE_TOOLS: list[Tool] = [note_add, note_search, note_delete]
TASK_TOOLS: list[Tool] = [task_add, task_list, task_complete, task_find, task_delete]
REMINDER_TOOLS: list[Tool] = [reminder_set, reminder_list, reminder_cancel]
FACT_TOOLS: list[Tool] = [remember, recall, forget]

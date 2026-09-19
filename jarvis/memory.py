"""Persistent memory: notes, tasks, reminders and remembered facts.

Everything lives in one SQLite file so that Jarvis remembers across restarts.
The store is deliberately small and synchronous -- a personal assistant writes
a handful of rows a day, not a thousand a second.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    body       TEXT NOT NULL,
    tags       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    details      TEXT NOT NULL DEFAULT '',
    due_at       TEXT,
    priority     TEXT NOT NULL DEFAULT 'normal',
    done         INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    body       TEXT NOT NULL,
    due_at     TEXT NOT NULL,
    fired      INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcript (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    role       TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_open ON tasks (done, due_at);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (fired, due_at);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass
class Note:
    id: int
    body: str
    tags: list[str]
    created_at: datetime


@dataclass
class Task:
    id: int
    title: str
    details: str
    due_at: datetime | None
    priority: str
    done: bool
    created_at: datetime
    completed_at: datetime | None


@dataclass
class Reminder:
    id: int
    body: str
    due_at: datetime
    fired: bool
    created_at: datetime


class MemoryStore:
    """A thread-safe façade over the SQLite file."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cursor

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    # -- notes ---------------------------------------------------------------

    def add_note(self, body: str, tags: Iterable[str] = ()) -> Note:
        tag_text = ",".join(sorted({t.strip().lower() for t in tags if t.strip()}))
        now = utcnow()
        cursor = self._execute(
            "INSERT INTO notes (body, tags, created_at) VALUES (?, ?, ?)",
            (body.strip(), tag_text, _iso(now)),
        )
        return Note(int(cursor.lastrowid), body.strip(), _split_tags(tag_text), now)

    def search_notes(self, query: str = "", tag: str = "", limit: int = 20) -> list[Note]:
        sql = "SELECT * FROM notes WHERE 1=1"
        params: list[Any] = []
        if query:
            sql += " AND body LIKE ?"
            params.append(f"%{query}%")
        if tag:
            sql += " AND (',' || tags || ',') LIKE ?"
            params.append(f"%,{tag.strip().lower()},%")
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [_row_to_note(row) for row in self._query(sql, params)]

    def delete_note(self, note_id: int) -> bool:
        return self._execute("DELETE FROM notes WHERE id = ?", (note_id,)).rowcount > 0

    # -- tasks ---------------------------------------------------------------

    def add_task(
        self,
        title: str,
        details: str = "",
        due_at: datetime | None = None,
        priority: str = "normal",
    ) -> Task:
        now = utcnow()
        cursor = self._execute(
            "INSERT INTO tasks (title, details, due_at, priority, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (title.strip(), details.strip(), _iso(due_at), priority, _iso(now)),
        )
        return Task(int(cursor.lastrowid), title.strip(), details.strip(), due_at, priority, False, now, None)

    def list_tasks(self, include_done: bool = False, limit: int = 50) -> list[Task]:
        sql = "SELECT * FROM tasks"
        if not include_done:
            sql += " WHERE done = 0"
        # Tasks with a due date come first, soonest at the top; the rest follow.
        sql += " ORDER BY done, due_at IS NULL, due_at, id LIMIT ?"
        return [_row_to_task(row) for row in self._query(sql, (limit,))]

    def complete_task(self, task_id: int) -> Task | None:
        now = utcnow()
        changed = self._execute(
            "UPDATE tasks SET done = 1, completed_at = ? WHERE id = ? AND done = 0",
            (_iso(now), task_id),
        ).rowcount
        if not changed:
            return None
        rows = self._query("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return _row_to_task(rows[0]) if rows else None

    def delete_task(self, task_id: int) -> bool:
        return self._execute("DELETE FROM tasks WHERE id = ?", (task_id,)).rowcount > 0

    def find_tasks(self, query: str, include_done: bool = False) -> list[Task]:
        sql = "SELECT * FROM tasks WHERE (title LIKE ? OR details LIKE ?)"
        params: list[Any] = [f"%{query}%", f"%{query}%"]
        if not include_done:
            sql += " AND done = 0"
        sql += " ORDER BY done, due_at IS NULL, due_at, id LIMIT 25"
        return [_row_to_task(row) for row in self._query(sql, params)]

    # -- reminders -----------------------------------------------------------

    def add_reminder(self, body: str, due_at: datetime) -> Reminder:
        now = utcnow()
        cursor = self._execute(
            "INSERT INTO reminders (body, due_at, created_at) VALUES (?, ?, ?)",
            (body.strip(), _iso(due_at), _iso(now)),
        )
        return Reminder(int(cursor.lastrowid), body.strip(), due_at, False, now)

    def list_reminders(self, include_fired: bool = False, limit: int = 50) -> list[Reminder]:
        sql = "SELECT * FROM reminders"
        if not include_fired:
            sql += " WHERE fired = 0"
        sql += " ORDER BY due_at LIMIT ?"
        return [_row_to_reminder(row) for row in self._query(sql, (limit,))]

    def due_reminders(self, now: datetime | None = None) -> list[Reminder]:
        moment = _iso(now or utcnow())
        rows = self._query(
            "SELECT * FROM reminders WHERE fired = 0 AND due_at <= ? ORDER BY due_at",
            (moment,),
        )
        return [_row_to_reminder(row) for row in rows]

    def mark_reminder_fired(self, reminder_id: int) -> None:
        self._execute("UPDATE reminders SET fired = 1 WHERE id = ?", (reminder_id,))

    def cancel_reminder(self, reminder_id: int) -> bool:
        return self._execute("DELETE FROM reminders WHERE id = ?", (reminder_id,)).rowcount > 0

    # -- facts ---------------------------------------------------------------

    def remember(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO facts (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            (key.strip().lower(), value.strip(), _iso(utcnow())),
        )

    def recall(self, key: str) -> str | None:
        rows = self._query("SELECT value FROM facts WHERE key = ?", (key.strip().lower(),))
        return rows[0]["value"] if rows else None

    def all_facts(self) -> dict[str, str]:
        return {row["key"]: row["value"] for row in self._query("SELECT * FROM facts ORDER BY key")}

    def forget(self, key: str) -> bool:
        return self._execute("DELETE FROM facts WHERE key = ?", (key.strip().lower(),)).rowcount > 0

    # -- transcript ----------------------------------------------------------

    def log_turn(self, role: str, body: str) -> None:
        self._execute(
            "INSERT INTO transcript (role, body, created_at) VALUES (?, ?, ?)",
            (role, body, _iso(utcnow())),
        )

    def recent_turns(self, limit: int = 20) -> list[tuple[str, str, datetime]]:
        rows = self._query(
            "SELECT * FROM transcript ORDER BY id DESC LIMIT ?", (limit,)
        )
        turns = [(r["role"], r["body"], _parse(r["created_at"])) for r in rows]
        return list(reversed(turns))


def _split_tags(text: str) -> list[str]:
    return [t for t in text.split(",") if t]


def _row_to_note(row: sqlite3.Row) -> Note:
    return Note(row["id"], row["body"], _split_tags(row["tags"]), _parse(row["created_at"]))


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        row["id"],
        row["title"],
        row["details"],
        _parse(row["due_at"]),
        row["priority"],
        bool(row["done"]),
        _parse(row["created_at"]),
        _parse(row["completed_at"]),
    )


def _row_to_reminder(row: sqlite3.Row) -> Reminder:
    return Reminder(
        row["id"],
        row["body"],
        _parse(row["due_at"]),
        bool(row["fired"]),
        _parse(row["created_at"]),
    )

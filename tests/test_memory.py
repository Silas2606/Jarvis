"""The store that makes Jarvis remember across restarts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jarvis.memory import MemoryStore


def test_notes_are_searchable_by_text_and_tag(store):
    store.add_note("Reaktor-Kern kalibrieren", ["labor", "dringend"])
    store.add_note("Espresso nachkaufen", ["haushalt"])

    assert len(store.search_notes("Reaktor")) == 1
    assert len(store.search_notes(tag="haushalt")) == 1
    assert len(store.search_notes()) == 2
    # Tags are normalised, so case does not matter when recalling.
    assert len(store.search_notes(tag="LABOR")) == 1


def test_tasks_sort_by_urgency(store):
    now = datetime.now(timezone.utc)
    store.add_task("ohne Frist")
    store.add_task("übermorgen", due_at=now + timedelta(days=2))
    store.add_task("gleich", due_at=now + timedelta(hours=1))

    assert [t.title for t in store.list_tasks()] == ["gleich", "übermorgen", "ohne Frist"]


def test_completing_a_task_hides_it_and_is_not_repeatable(store):
    task = store.add_task("Mark III")
    assert store.complete_task(task.id).done is True
    assert store.list_tasks() == []
    assert len(store.list_tasks(include_done=True)) == 1
    # Completing it twice is a no-op, not an error.
    assert store.complete_task(task.id) is None


def test_due_reminders_fire_once(store):
    now = datetime.now(timezone.utc)
    store.add_reminder("jetzt", now - timedelta(minutes=1))
    store.add_reminder("später", now + timedelta(hours=2))

    due = store.due_reminders()
    assert [r.body for r in due] == ["jetzt"]
    store.mark_reminder_fired(due[0].id)
    assert store.due_reminders() == []
    assert [r.body for r in store.list_reminders()] == ["später"]


def test_facts_are_overwritten_not_duplicated(store):
    store.remember("Kaffee", "schwarz")
    store.remember("kaffee", "mit Milch")
    assert store.all_facts() == {"kaffee": "mit Milch"}
    assert store.forget("Kaffee") is True
    assert store.forget("Kaffee") is False


def test_memory_survives_reopening(tmp_path):
    path = tmp_path / "persist.db"
    first = MemoryStore(path)
    first.add_task("überlebt einen Neustart")
    first.remember("name", "Silas")
    first.close()

    second = MemoryStore(path)
    try:
        assert [t.title for t in second.list_tasks()] == ["überlebt einen Neustart"]
        assert second.recall("name") == "Silas"
    finally:
        second.close()


def test_transcript_is_kept_in_order(store):
    store.log_turn("user", "Guten Morgen")
    store.log_turn("assistant", "Guten Morgen, Sir.")
    turns = store.recent_turns()
    assert [(role, body) for role, body, _ in turns] == [
        ("user", "Guten Morgen"),
        ("assistant", "Guten Morgen, Sir."),
    ]

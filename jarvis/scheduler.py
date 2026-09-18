"""The background thread that makes reminders actually go off."""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from jarvis.events import EventBus, EventKind


class ReminderService:
    """Polls the store and announces reminders as they come due.

    A poll every few seconds is plenty for a personal assistant, and it
    survives the process being asleep or busy talking -- anything overdue is
    picked up on the next tick rather than being lost.
    """

    def __init__(self, store, bus: EventBus, interval: float = 5.0):
        self.store = store
        self.bus = bus
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="jarvis-reminders", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)

    def check_once(self, now: datetime | None = None) -> int:
        """Fire everything that is due. Returns how many went off."""
        due = self.store.due_reminders(now or datetime.now(timezone.utc))
        for reminder in due:
            self.store.mark_reminder_fired(reminder.id)
            self.bus.emit(
                EventKind.REMINDER,
                reminder.body,
                reminder_id=reminder.id,
                due_at=reminder.due_at,
            )
        return len(due)

    def _run(self) -> None:  # pragma: no cover - timing loop
        while not self._stop.wait(self.interval):
            try:
                self.check_once()
            except Exception as exc:
                self.bus.emit(EventKind.ERROR, f"The reminder service stumbled: {exc}")

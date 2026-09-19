"""End-to-end: an instruction goes in, tools run, an answer comes out."""

from __future__ import annotations

from conftest import FakeClient, FakeMessage, TextBlock, ToolUseBlock

from jarvis.events import EventKind
from jarvis.scheduler import ReminderService
from jarvis.session import ask_once, build_session, greeting_for


def test_instruction_runs_tools_and_answers(config, tmp_path):
    client = FakeClient(
        FakeMessage(
            [ToolUseBlock("reminder_set", {"body": "Kaffee holen", "when": "+10m"})],
            stop_reason="tool_use",
        ),
        FakeMessage([TextBlock("In zehn Minuten, Sir.")]),
    )
    session = build_session(config, with_console=False, client=client)
    events: list = []
    session.bus.subscribe(events.append)

    try:
        assert ask_once(session, "Erinnere mich in 10 Minuten an Kaffee") == 0

        answers = [e.text for e in events if e.kind is EventKind.ANSWER]
        assert answers == ["In zehn Minuten, Sir."]
        pending = session.store.list_reminders()
        assert len(pending) == 1 and pending[0].body == "Kaffee holen"
    finally:
        session.close()


def test_outbound_tool_asks_before_acting(config):
    """A tool marked confirm= must not run until the user agrees."""
    config.tools.mail = True
    client = FakeClient(
        FakeMessage(
            [ToolUseBlock("mail_send", {"to": ["x@y.z"], "subject": "Test", "body": "Hallo"})],
            stop_reason="tool_use",
        ),
        FakeMessage([TextBlock("Ich habe es gelassen.")]),
    )
    session = build_session(config, with_console=False, client=client)
    asked: list[str] = []

    def refuse(question: str) -> bool:
        asked.append(question)
        return False

    session.brain.registry.context.confirm = refuse
    try:
        session.brain.ask("Schick eine Mail an x@y.z")
    finally:
        session.close()

    assert asked and "x@y.z" in asked[0]
    results = [
        block
        for message in session.brain.messages
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert results and "declined" in results[0]["content"]


def test_confirmation_switch_removes_the_prompts(config):
    config.tools.mail = True
    config.tools.confirm_outbound = False
    session = build_session(config, with_console=False, client=FakeClient())
    try:
        assert session.brain.registry.tools["mail_send"].confirm is None
    finally:
        session.close()


def test_reminder_service_fires_due_reminders(config, store, bus):
    from datetime import datetime, timedelta, timezone

    events: list = []
    bus.subscribe(events.append)
    service = ReminderService(store, bus)

    store.add_reminder("Jetzt fällig", datetime.now(timezone.utc) - timedelta(seconds=1))
    store.add_reminder("Später", datetime.now(timezone.utc) + timedelta(hours=1))

    assert service.check_once() == 1
    assert [e.text for e in events if e.kind is EventKind.REMINDER] == ["Jetzt fällig"]
    # A fired reminder does not fire twice.
    assert service.check_once() == 0


def test_greeting_matches_language(config):
    config.voice.language = "de"
    german = greeting_for(config)
    assert german.endswith("Alle Systeme bereit.")
    assert config.address in german

    config.voice.language = "en"
    assert greeting_for(config).endswith("All systems ready.")


def test_api_failure_is_announced_without_crashing_the_loop(config, tmp_path):
    """The whole error path, end to end.

    Regression: the failure was reported with `kind=` as event data, which
    collides with EventBus.emit's own first parameter -- so reporting an API
    error raised a TypeError of its own and took the assistant down. Testing
    the exception alone did not catch it; only emitting does.
    """
    import anthropic
    import httpx2 as httpx

    from jarvis.session import ask_once

    body = {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": "Your credit balance is too low."},
    }
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    failure = anthropic.BadRequestError(
        "boom", response=httpx.Response(400, request=request, json=body), body=body
    )

    config.voice.language = "de"
    session = build_session(config, with_console=False, client=FakeClient(failure))
    events: list = []
    session.bus.subscribe(events.append)

    try:
        # Returns a failure code rather than raising.
        assert ask_once(session, "Wie spät ist es?") == 1
    finally:
        session.close()

    errors = [e for e in events if e.kind is EventKind.ERROR]
    assert len(errors) == 1
    assert "Guthaben" in errors[0].text
    assert errors[0].data["problem"] == "no_credit"
    assert "credit balance" in errors[0].data["detail"]

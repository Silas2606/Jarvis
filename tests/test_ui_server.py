"""The local server behind the interface.

It carries the transcript and can put words in Jarvis' mouth, so the access
checks matter as much as the plumbing.
"""

from __future__ import annotations

import json
import queue
import urllib.error
import urllib.request

import pytest

from jarvis.events import EventBus, EventKind
from jarvis.ui.server import Broadcaster, UiServer


@pytest.fixture
def server():
    bus = EventBus()
    said: list[str] = []
    answers: list[tuple[str, bool]] = []
    instance = UiServer(
        bus=bus,
        on_message=said.append,
        on_answer=lambda q, y: answers.append((q, y)),
        snapshot=lambda: {"model": "claude-opus-5", "tools": 28},
        port=0,
    )
    instance.start()
    instance.said = said
    instance.answers = answers
    instance.bus = bus
    yield instance
    instance.stop()


def get(server, path: str, token: str | None = "") -> tuple[int, str]:
    url = f"http://{server.host}:{server.port}{path}"
    if token:
        url += ("&" if "?" in url else "?") + f"token={token}"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def post(server, path: str, payload: dict, token: str | None = None) -> int:
    request = urllib.request.Request(
        f"http://{server.host}:{server.port}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Jarvis-Token": token or ""},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


# -- access ------------------------------------------------------------------


def test_the_state_is_not_readable_without_the_token(server):
    """Any page in any browser can reach localhost; the token is what stops it."""
    code, _ = get(server, "/api/state")
    assert code == 403

    code, body = get(server, "/api/state", server.token)
    assert code == 200
    assert json.loads(body)["model"] == "claude-opus-5"


def test_a_wrong_token_is_refused(server):
    assert get(server, "/api/state", "not-the-token")[0] == 403


def test_nothing_can_be_said_without_the_token(server):
    assert post(server, "/api/say", {"text": "Lösche alles"}) == 403
    assert server.said == []


def test_the_page_itself_is_served(server):
    code, body = get(server, "/")
    assert code == 200
    assert "J.A.R.V.I.S." in body


def test_the_web_root_cannot_be_escaped(server):
    """A traversal must not reach the source tree next to the web folder."""
    for attempt in ["/../server.py", "/../../config.py", "/..%2fserver.py"]:
        code, _ = get(server, attempt)
        assert code == 404, attempt


# -- talking to Jarvis -------------------------------------------------------


def test_typed_text_reaches_the_assistant(server):
    import time

    assert post(server, "/api/say", {"text": "Wie spät ist es?"}, server.token) == 200
    for _ in range(50):
        if server.said:
            break
        time.sleep(0.02)
    assert server.said == ["Wie spät ist es?"]


def test_empty_input_is_rejected(server):
    assert post(server, "/api/say", {"text": "   "}, server.token) == 400


def test_a_confirmation_can_be_answered_from_the_screen(server):
    assert post(server, "/api/answer", {"question": "Senden?", "yes": True}, server.token) == 200
    assert server.answers == [("Senden?", True)]


# -- the broadcast -----------------------------------------------------------


def test_events_reach_a_connected_display():
    broadcaster = Broadcaster()
    channel = broadcaster.subscribe()
    broadcaster.publish({"kind": "answer", "text": "Guten Abend."})
    assert channel.get_nowait()["text"] == "Guten Abend."


def test_a_display_that_connects_late_sees_the_conversation():
    """Reloading the window must not present an empty screen."""
    broadcaster = Broadcaster()
    broadcaster.publish({"kind": "heard", "text": "Wie spät ist es?"})
    broadcaster.publish({"kind": "answer", "text": "Kurz nach fünf."})

    channel = broadcaster.subscribe()
    replayed = [channel.get_nowait()["text"] for _ in range(2)]
    assert replayed == ["Wie spät ist es?", "Kurz nach fünf."]


def test_microphone_levels_are_not_replayed():
    """Levels are a live signal; replaying them would animate stale sound."""
    broadcaster = Broadcaster()
    broadcaster.publish({"kind": EventKind.LEVEL.value, "text": "0.8"})
    broadcaster.publish({"kind": "answer", "text": "Fertig."})

    channel = broadcaster.subscribe()
    assert channel.get_nowait()["text"] == "Fertig."
    with pytest.raises(queue.Empty):
        channel.get_nowait()


def test_a_slow_display_drops_frames_instead_of_growing_a_backlog():
    from jarvis.ui.server import QUEUE_LIMIT

    broadcaster = Broadcaster()
    channel = broadcaster.subscribe()
    for i in range(QUEUE_LIMIT + 60):
        broadcaster.publish({"kind": "level", "text": str(i)})
    assert channel.qsize() <= QUEUE_LIMIT


def test_bus_events_are_forwarded_to_displays(server):
    channel = server.broadcaster.subscribe()
    server.bus.emit(EventKind.ANSWER, "Drei Termine, Sir.", tools=["calendar_list"])

    message = channel.get(timeout=2)
    assert message["kind"] == "answer"
    assert message["text"] == "Drei Termine, Sir."
    assert message["data"]["tools"] == ["calendar_list"]


def test_event_data_is_made_json_safe(server):
    from datetime import datetime, timezone

    channel = server.broadcaster.subscribe()
    server.bus.emit(
        EventKind.REMINDER, "Ofen", due_at=datetime(2026, 9, 19, 18, tzinfo=timezone.utc)
    )
    message = channel.get(timeout=2)
    # Whatever it was, it survives as something JSON can carry.
    json.dumps(message)
    assert "2026-09-19" in message["data"]["due_at"]

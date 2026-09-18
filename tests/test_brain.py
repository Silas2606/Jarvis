"""The agent loop: tool rounds, streaming, cancellation, refusals."""

from __future__ import annotations

import threading

import pytest
from conftest import FakeClient, FakeMessage, TextBlock, ToolUseBlock

from jarvis.brain import Brain
from jarvis.events import EventKind
from jarvis.tools import build_registry


@pytest.fixture
def brain(config, store, bus):
    def make(*script):
        registry = build_registry(config, store, bus)
        return Brain(config, registry, bus, store, client=FakeClient(*script))

    return make


def test_plain_answer_streams_sentences(brain, recorder):
    agent = brain(FakeMessage([TextBlock("Guten Morgen, Sir. Alles bereit.")]))
    reply = agent.ask("Guten Morgen")

    assert reply.text == "Guten Morgen, Sir. Alles bereit."
    assert reply.stop_reason == "end_turn"
    spoken = [e.text for e in recorder if e.kind is EventKind.SPEECH_CHUNK]
    assert spoken == ["Guten Morgen, Sir.", "Alles bereit."]


def test_tool_round_executes_and_feeds_back(brain, store, recorder):
    agent = brain(
        FakeMessage(
            [ToolUseBlock("task_add", {"title": "Reaktor prüfen"})],
            stop_reason="tool_use",
        ),
        FakeMessage([TextBlock("Steht auf der Liste, Sir.")]),
    )
    reply = agent.ask("Setz Reaktor prüfen auf die Liste")

    assert reply.tool_calls == ["task_add"]
    assert reply.text == "Steht auf der Liste, Sir."
    assert [t.title for t in store.list_tasks()] == ["Reaktor prüfen"]

    # Shape of the transcript: ask -> tool_use -> tool_result -> answer.
    roles = [m["role"] for m in agent.messages]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert agent.messages[2]["content"][0]["type"] == "tool_result"
    started = [e for e in recorder if e.kind is EventKind.TOOL_START]
    assert started and started[0].text == "task_add"


def test_parallel_tool_results_share_one_message(brain):
    agent = brain(
        FakeMessage(
            [
                ToolUseBlock("task_add", {"title": "A"}, id="t1"),
                ToolUseBlock("task_add", {"title": "B"}, id="t2"),
            ],
            stop_reason="tool_use",
        ),
        FakeMessage([TextBlock("Beide notiert.")]),
    )
    agent.ask("Zwei Aufgaben")

    tool_messages = [
        m
        for m in agent.messages
        if m["role"] == "user"
        and isinstance(m["content"], list)
        and m["content"]
        and m["content"][0].get("type") == "tool_result"
    ]
    assert len(tool_messages) == 1
    assert len(tool_messages[0]["content"]) == 2
    assert {b["tool_use_id"] for b in tool_messages[0]["content"]} == {"t1", "t2"}


def test_failing_tool_is_reported_as_error(brain):
    agent = brain(
        FakeMessage([ToolUseBlock("task_complete", {"task_id": 999})], stop_reason="tool_use"),
        FakeMessage([TextBlock("Die Aufgabe gibt es nicht.")]),
    )
    agent.ask("Erledige Aufgabe 999")

    results = agent.messages[2]["content"]
    errors = [b for b in results if b.get("is_error")]
    assert errors and "999" in errors[0]["content"]


def test_unknown_tool_does_not_crash(brain):
    agent = brain(
        FakeMessage([ToolUseBlock("self_destruct", {})], stop_reason="tool_use"),
        FakeMessage([TextBlock("Das kann ich nicht.")]),
    )
    reply = agent.ask("Selbstzerstörung")
    assert reply.text == "Das kann ich nicht."


def test_pause_turn_resumes(brain):
    agent = brain(
        FakeMessage([TextBlock("Suche läuft")], stop_reason="pause_turn"),
        FakeMessage([TextBlock("Hier ist das Ergebnis.")]),
    )
    reply = agent.ask("Such etwas")
    assert reply.text == "Hier ist das Ergebnis."


def test_refusal_is_surfaced(brain):
    from types import SimpleNamespace

    agent = brain(
        FakeMessage(
            [TextBlock("Dabei kann ich nicht helfen.")],
            stop_reason="refusal",
            stop_details=SimpleNamespace(type="refusal", category="cyber"),
        )
    )
    reply = agent.ask("etwas heikles")
    assert reply.refused is True
    assert "nicht helfen" in reply.text


def test_cancel_abandons_the_turn(brain):
    agent = brain(FakeMessage([TextBlock("Ein langer Satz, der nie ankommt.")]))
    cancel = threading.Event()
    cancel.set()

    reply = agent.ask("Erzähl mir was", cancel=cancel)

    assert reply.cancelled is True
    # The dangling user turn is removed, so the next request stays valid.
    assert agent.messages == []


def test_history_is_trimmed_without_splitting_tool_pairs(config, store, bus):
    config.brain.history_turns = 4
    registry = build_registry(config, store, bus)
    agent = Brain(config, registry, bus, store, client=FakeClient())

    agent.messages = [
        {"role": "user", "content": "eins"},
        {"role": "assistant", "content": [ToolUseBlock("task_list", {})]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]},
        {"role": "assistant", "content": [TextBlock("fertig")]},
        {"role": "user", "content": "zwei"},
        {"role": "assistant", "content": [TextBlock("gut")]},
    ]
    agent._trim()

    # Never starts on a tool_result, which the API would reject.
    first = agent.messages[0]
    assert first["role"] == "user"
    assert isinstance(first["content"], str)


def test_request_carries_cache_breakpoint_and_effort(brain):
    agent = brain(FakeMessage([TextBlock("Ja, Sir.")]))
    agent.ask("Hallo")

    params = agent._client.calls[0]
    assert params["model"] == "claude-opus-5"
    assert params["output_config"] == {"effort": "low"}
    # Stable persona first and cached; volatile situation after it.
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in params["system"][1]
    assert "Current situation" in params["system"][1]["text"]


def test_tool_order_is_stable_across_turns(brain):
    agent = brain(FakeMessage([TextBlock("eins")]), FakeMessage([TextBlock("zwei")]))
    agent.ask("a")
    agent.ask("b")

    first, second = agent._client.calls
    assert [t["name"] for t in first["tools"]] == [t["name"] for t in second["tools"]]

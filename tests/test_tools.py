"""Tool schema generation, dispatch and the memory-backed tools."""

from __future__ import annotations

from jarvis.tools import build_registry
from jarvis.tools.base import ToolContext, ToolError, ToolRegistry, tool


def test_schema_comes_from_signature_and_docstring():
    @tool
    def example(ctx, body: str, tags: list[str] = None, count: int = 3, flag: bool = False) -> str:
        """Do a thing.

        Args:
            body: The main text.
            tags: Labels to file it under.
        """
        return "ok"

    schema = example.schema()
    assert schema["name"] == "example"
    assert schema["description"] == "Do a thing."
    properties = schema["input_schema"]["properties"]
    # ctx is injected, never exposed to the model.
    assert "ctx" not in properties
    assert properties["body"] == {"type": "string", "description": "The main text."}
    assert properties["tags"]["type"] == "array"
    assert properties["tags"]["items"] == {"type": "string"}
    assert properties["count"]["type"] == "integer"
    assert properties["flag"]["type"] == "boolean"
    # Only parameters without a default are required.
    assert schema["input_schema"]["required"] == ["body"]


def test_tool_errors_come_back_as_text_not_exceptions():
    @tool
    def explodes() -> str:
        """Always fails."""
        raise ToolError("the reactor is offline")

    registry = ToolRegistry().register(explodes)
    result, failed = registry.call("explodes", {})
    assert failed is True
    assert result == "the reactor is offline"


def test_unexpected_exception_is_contained():
    @tool
    def explodes() -> str:
        """Always fails."""
        raise ValueError("boom")

    registry = ToolRegistry().register(explodes)
    result, failed = registry.call("explodes", {})
    assert failed is True and "ValueError" in result


def test_bad_arguments_are_reported_clearly():
    @tool
    def needs_body(body: str) -> str:
        """Needs a body."""
        return body

    registry = ToolRegistry().register(needs_body)
    result, failed = registry.call("needs_body", {"wrong": 1})
    assert failed is True and "Invalid arguments" in result


def test_confirmation_template_is_filled_from_arguments():
    asked: list[str] = []

    @tool(confirm="Really send to {to}?")
    def send(to: str) -> str:
        """Send it."""
        return "sent"

    registry = ToolRegistry(context=ToolContext(confirm=lambda q: asked.append(q) or True))
    registry.register(send)
    result, _ = registry.call("send", {"to": "anna@example.com"})
    assert asked == ["Really send to anna@example.com?"]
    assert result == "sent"


def test_without_a_way_to_ask_an_outbound_tool_refuses():
    ran: list[int] = []

    @tool(confirm="Send?")
    def send() -> str:
        """Send it."""
        ran.append(1)
        return "sent"

    registry = ToolRegistry().register(send)  # no confirm callback
    result, failed = registry.call("send", {})
    assert ran == [] and failed is False and "declined" in result


def test_notes_tasks_and_facts_round_trip(config, store, bus):
    registry = build_registry(config, store, bus)

    registry.call("note_add", {"body": "Reaktor prüfen", "tags": ["labor"]})
    assert "Reaktor prüfen" in registry.call("note_search", {"query": "Reaktor"})[0]
    assert "No notes match" in registry.call("note_search", {"query": "Espresso"})[0]

    registry.call("task_add", {"title": "Mark III", "due": "morgen 9 Uhr"})
    listing = registry.call("task_list", {})[0]
    assert "Mark III" in listing and "morgen um 09:00" in listing

    found = registry.call("task_find", {"query": "Mark"})[0]
    task_id = int(found.split("#")[1].split(":")[0])
    assert "completed" in registry.call("task_complete", {"task_id": task_id})[0]
    assert "empty" in registry.call("task_list", {})[0]

    registry.call("remember", {"key": "Kaffee", "value": "schwarz"})
    assert "schwarz" in registry.call("recall", {"key": "kaffee"})[0]
    assert "Forgotten" in registry.call("forget", {"key": "kaffee"})[0]


def test_unparseable_due_date_is_an_error_not_a_silent_guess(config, store, bus):
    registry = build_registry(config, store, bus)
    result, failed = registry.call("task_add", {"title": "x", "due": "irgendwann"})
    assert failed is True and "Could not work out a time" in result
    assert store.list_tasks() == []


def test_web_tools_are_server_side_and_optional(config, store, bus):
    config.brain.web_access = True
    registry = build_registry(config, store, bus)
    types = [t["type"] for t in registry.server_tools]
    assert types == ["web_search_20260209", "web_fetch_20260209"]
    # Server tools are never dispatched locally.
    assert "web_search" not in registry.tools

    config.brain.web_access = False
    assert build_registry(config, store, bus).server_tools == []

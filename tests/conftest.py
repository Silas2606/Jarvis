"""Shared fixtures, including a Claude stand-in so tests need no API key."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.config import Config  # noqa: E402
from jarvis.events import EventBus  # noqa: E402
from jarvis.memory import MemoryStore  # noqa: E402


# -- fake content blocks -----------------------------------------------------


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    name: str
    input: dict[str, Any]
    id: str = "toolu_1"
    type: str = "tool_use"


@dataclass
class FakeMessage:
    content: list[Any]
    stop_reason: str = "end_turn"
    stop_details: Any = None
    usage: Any = field(
        default_factory=lambda: SimpleNamespace(
            input_tokens=100, output_tokens=20, cache_read_input_tokens=0
        )
    )


class FakeStream:
    """Mimics the context-manager stream the SDK hands back."""

    def __init__(self, message: FakeMessage):
        self._message = message
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def __iter__(self):
        # Emit each text block as a couple of deltas, like the real thing.
        for block in self._message.content:
            if getattr(block, "type", None) != "text":
                continue
            text = block.text
            middle = max(len(text) // 2, 1)
            for piece in (text[:middle], text[middle:]):
                if piece:
                    yield SimpleNamespace(
                        type="content_block_delta",
                        delta=SimpleNamespace(type="text_delta", text=piece),
                    )

    def get_final_message(self) -> FakeMessage:
        return self._message

    def close(self) -> None:
        self.closed = True


class FakeMessages:
    def __init__(self, owner: "FakeClient"):
        self._owner = owner

    def stream(self, **params):
        self._owner.calls.append(params)
        if not self._owner.script:
            raise AssertionError("FakeClient ran out of scripted responses")
        item = self._owner.script.pop(0)
        # A scripted exception stands in for an API failure.
        if isinstance(item, Exception):
            raise item
        return FakeStream(item)


class FakeClient:
    """A scripted Claude: hand it the messages it should return, in order.

    An ``Exception`` in the script is raised instead of returned, which is how
    API failures are exercised.
    """

    def __init__(self, *script):
        self.script: list = list(script)
        self.calls: list[dict[str, Any]] = []
        self.messages = FakeMessages(self)
        self.beta = SimpleNamespace(messages=FakeMessages(self))


# -- fixtures ----------------------------------------------------------------


@pytest.fixture
def config(tmp_path: Path) -> Config:
    cfg = Config(home=tmp_path)
    cfg.timezone = "Europe/Berlin"
    cfg.tools.calendar = False
    cfg.tools.mail = False
    cfg.brain.web_access = False
    cfg.brain.server_fallbacks = False
    return cfg


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    memory = MemoryStore(tmp_path / "test.db")
    yield memory
    memory.close()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def recorder(bus: EventBus) -> list:
    events: list = []
    bus.subscribe(events.append)
    return events

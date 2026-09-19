"""Events the assistant emits while it works.

The agent core knows nothing about consoles or loudspeakers. It publishes
events; the console HUD prints them and the voice loop speaks them. That keeps
the brain testable without a microphone anywhere near it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class EventKind(str, Enum):
    STATE = "state"                  # idle / listening / thinking / speaking
    LEVEL = "level"                  # microphone loudness, for the display
    LISTENING = "listening"          # microphone is open
    WAKE = "wake"                    # wake word detected
    HEARD = "heard"                  # an utterance was transcribed
    THINKING = "thinking"            # a request to Claude is in flight
    REASONING = "reasoning"          # a summary of Claude's reasoning
    SPEECH_CHUNK = "speech_chunk"    # a finished sentence, ready to speak
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    CONFIRM = "confirm"              # a tool needs a yes before it runs
    ANSWER = "answer"                # the complete reply
    REMINDER = "reminder"            # a reminder came due
    NOTICE = "notice"                # informational aside
    ERROR = "error"


class State(str, Enum):
    """What Jarvis is doing, as a display would put it."""

    ASLEEP = "asleep"          # waiting for the wake word
    LISTENING = "listening"    # recording an utterance
    THINKING = "thinking"      # a request is in flight
    WORKING = "working"        # running a tool
    SPEAKING = "speaking"      # saying the answer
    ASKING = "asking"          # waiting for a yes or no


@dataclass
class Event:
    kind: EventKind
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)


Listener = Callable[[Event], None]


class EventBus:
    """A synchronous fan-out. Small, predictable, easy to assert against."""

    def __init__(self) -> None:
        self._listeners: list[Listener] = []

    def subscribe(self, listener: Listener) -> Listener:
        self._listeners.append(listener)
        return listener

    def unsubscribe(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def emit(self, kind: EventKind, text: str = "", **data: Any) -> Event:
        event = Event(kind=kind, text=text, data=data)
        for listener in list(self._listeners):
            # A broken listener must never take the assistant down mid-sentence.
            try:
                listener(event)
            except Exception:  # pragma: no cover - defensive
                pass
        return event

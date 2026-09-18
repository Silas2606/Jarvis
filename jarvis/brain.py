"""The thinking part: a streaming agent loop over the Claude Messages API.

Why a manual loop rather than the SDK's tool runner? A voice assistant needs
two things the runner does not hand out: text as it is generated, so speech
synthesis can start on sentence one while sentence two is still being written;
and the ability to abandon a turn mid-sentence when the user talks over it.
Both want direct access to the stream, so the loop lives here.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from jarvis.events import EventBus, EventKind
from jarvis.persona import persona_prompt, situation_prompt
from jarvis.speech_chunks import SentenceAccumulator
from jarvis.tools.base import ToolRegistry

# Guard against a tool loop that never settles.
MAX_TOOL_ROUNDS = 12
# Server-side tools (web search) can pause a turn; each resume costs a request.
MAX_PAUSE_RESUMES = 5


class BrainError(RuntimeError):
    """Talking to the model failed in a way the user should hear about."""


@dataclass
class Reply:
    """What came back from one exchange."""

    text: str = ""
    stop_reason: str = ""
    tool_calls: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cancelled: bool = False
    refused: bool = False


def _text_of(content: list[Any]) -> str:
    parts = []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts).strip()


class Brain:
    """Holds the conversation and runs it against Claude."""

    def __init__(
        self,
        config,
        registry: ToolRegistry,
        bus: EventBus | None = None,
        store=None,
        client: Any = None,
    ):
        self.config = config
        self.registry = registry
        self.bus = bus or EventBus()
        self.store = store
        self.messages: list[dict[str, Any]] = []
        self._client = client
        # Turned off automatically if the installed SDK or endpoint rejects it.
        self._use_fallbacks = bool(config.brain.server_fallbacks)

    # -- client --------------------------------------------------------------

    @property
    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover
                raise BrainError(
                    "The anthropic package is missing. Install it with: pip install anthropic"
                ) from exc
            try:
                self._client = anthropic.Anthropic(timeout=self.config.brain.request_timeout)
            except Exception as exc:
                raise BrainError(f"No Claude client could be created: {exc}") from exc
        return self._client

    # -- conversation state --------------------------------------------------

    def reset(self) -> None:
        """Forget the current conversation, but not the long-term memory."""
        self.messages = []

    def _trim(self) -> None:
        """Keep the transcript bounded without breaking tool pairings.

        A ``tool_result`` must always follow the ``tool_use`` it answers, so the
        transcript may only be cut in front of a user message that carries no
        tool results.
        """
        limit = max(self.config.brain.history_turns, 4)
        if len(self.messages) <= limit:
            return
        for index in range(len(self.messages) - limit, len(self.messages)):
            message = self.messages[index]
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                self.messages = self.messages[index:]
                return
            if isinstance(content, list) and not any(
                isinstance(block, dict) and block.get("type") == "tool_result" for block in content
            ):
                self.messages = self.messages[index:]
                return

    # -- request shaping -----------------------------------------------------

    def _system(self) -> list[dict[str, Any]]:
        """System prompt in two blocks: stable (cached) then volatile.

        The cache breakpoint sits on the persona, which never changes during a
        session. The situational block -- clock, open tasks -- follows it, where
        changing every minute costs nothing.
        """
        return [
            {
                "type": "text",
                "text": persona_prompt(self.config),
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": situation_prompt(self.config, self.store)},
        ]

    def _params(self) -> dict[str, Any]:
        brain = self.config.brain
        params: dict[str, Any] = {
            "model": brain.model,
            "max_tokens": brain.max_tokens,
            "system": self._system(),
            "messages": self.messages,
            "output_config": {"effort": brain.effort},
        }
        tools = self.registry.schemas()
        if tools:
            params["tools"] = tools
        if brain.show_thinking:
            # Adaptive thinking is the default on Opus 5; the display opt-in is
            # what makes the reasoning visible in the HUD.
            params["thinking"] = {"type": "adaptive", "display": "summarized"}
        if self._use_fallbacks:
            # If a safety classifier declines, the server routes the request to
            # a suitable fallback model instead of failing the turn outright.
            params["betas"] = ["server-side-fallback-2026-07-01"]
            params["fallbacks"] = "default"
        return params

    def _stream_once(self, reply: Reply, cancel: threading.Event | None) -> Any:
        """Run one streaming request, emitting speech chunks as they form."""
        params = self._params()
        accumulator = SentenceAccumulator()
        endpoint = self.client.beta.messages if self._use_fallbacks else self.client.messages

        try:
            with endpoint.stream(**params) as stream:
                for event in stream:
                    if cancel is not None and cancel.is_set():
                        reply.cancelled = True
                        stream.close()
                        return None
                    if event.type != "content_block_delta":
                        continue
                    delta = event.delta
                    if delta.type == "text_delta":
                        for sentence in accumulator.feed(delta.text):
                            self.bus.emit(EventKind.SPEECH_CHUNK, sentence)
                    elif delta.type == "thinking_delta" and self.config.brain.show_thinking:
                        self.bus.emit(EventKind.REASONING, delta.thinking)
                message = stream.get_final_message()
        except TypeError as exc:
            message = str(exc)
            # The SDK raises TypeError for a missing credential too, which has
            # nothing to do with the parameters we pass.
            if "authentication" in message.lower() or "api_key" in message.lower():
                raise BrainError(
                    "No Claude credentials were found. Set ANTHROPIC_API_KEY, "
                    "or run `ant auth login`."
                ) from exc
            # An older SDK that does not know `fallbacks` -- drop it and retry.
            if self._use_fallbacks and "unexpected keyword" in message.lower():
                self._use_fallbacks = False
                self.bus.emit(
                    EventKind.NOTICE,
                    "Server-side fallbacks unavailable; continuing without them.",
                )
                return self._stream_once(reply, cancel)
            raise BrainError(f"The request was malformed: {exc}") from exc
        except Exception as exc:
            raise self._translate(exc) from exc

        remainder = accumulator.flush()
        if remainder:
            self.bus.emit(EventKind.SPEECH_CHUNK, remainder)
        return message

    def _translate(self, exc: Exception) -> BrainError:
        """Turn an SDK exception into something worth saying out loud."""
        try:
            import anthropic
        except ImportError:  # pragma: no cover
            return BrainError(str(exc))

        if isinstance(exc, anthropic.AuthenticationError):
            return BrainError(
                "The Claude API rejected the credentials. Check ANTHROPIC_API_KEY."
            )
        if isinstance(exc, anthropic.NotFoundError):
            return BrainError(f"The model {self.config.brain.model!r} is not available on this account.")
        if isinstance(exc, anthropic.RateLimitError):
            return BrainError("The rate limit is reached. Try again in a moment.")
        if isinstance(exc, anthropic.BadRequestError):
            if self._use_fallbacks:
                # Most likely the fallbacks beta is not enabled for this account.
                self._use_fallbacks = False
                return BrainError("__retry_without_fallbacks__")
            return BrainError(f"The request was rejected: {exc}")
        if isinstance(exc, anthropic.APIConnectionError):
            return BrainError("No connection to the Claude API.")
        if isinstance(exc, anthropic.APIStatusError):
            return BrainError(f"The Claude API returned an error: {exc}")
        return BrainError(f"{type(exc).__name__}: {exc}")

    # -- the loop ------------------------------------------------------------

    def ask(self, user_text: str, cancel: threading.Event | None = None) -> Reply:
        """Put one utterance to Claude and run tools until it is satisfied."""
        reply = Reply()
        self.messages.append({"role": "user", "content": user_text})
        self._trim()

        pauses = 0
        for _round in range(MAX_TOOL_ROUNDS):
            self.bus.emit(EventKind.THINKING)

            try:
                message = self._stream_once(reply, cancel)
            except BrainError as exc:
                if str(exc) == "__retry_without_fallbacks__":
                    message = self._stream_once(reply, cancel)
                else:
                    raise

            if message is None:  # cancelled mid-stream
                # Drop the dangling user turn so the transcript stays valid.
                if self.messages and self.messages[-1]["role"] == "user":
                    self.messages.pop()
                return reply

            usage = getattr(message, "usage", None)
            if usage is not None:
                reply.input_tokens += getattr(usage, "input_tokens", 0) or 0
                reply.output_tokens += getattr(usage, "output_tokens", 0) or 0
                reply.cached_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0

            reply.stop_reason = message.stop_reason or ""
            self.messages.append({"role": "assistant", "content": message.content})

            # A safety classifier declined. Say so plainly rather than inventing
            # an answer; stop_details carries the category.
            if message.stop_reason == "refusal":
                reply.refused = True
                details = getattr(message, "stop_details", None)
                category = getattr(details, "category", None) if details else None
                reply.text = _text_of(message.content) or (
                    "I cannot help with that request."
                    + (f" ({category})" if category else "")
                )
                return reply

            # A server-side tool ran out of its per-turn budget; resend to let
            # it carry on where it left off.
            if message.stop_reason == "pause_turn":
                pauses += 1
                if pauses > MAX_PAUSE_RESUMES:
                    reply.text = _text_of(message.content) or "That search went on too long; I stopped it."
                    return reply
                continue

            tool_uses = [b for b in message.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                reply.text = _text_of(message.content)
                if message.stop_reason == "max_tokens":
                    self.bus.emit(EventKind.NOTICE, "The answer hit the length limit.")
                return reply

            results = []
            for block in tool_uses:
                if cancel is not None and cancel.is_set():
                    reply.cancelled = True
                    return reply
                arguments = dict(block.input or {})
                self.bus.emit(EventKind.TOOL_START, block.name, arguments=arguments)
                output, failed = self.registry.call(block.name, arguments)
                self.bus.emit(EventKind.TOOL_END, block.name, result=output, failed=failed)
                reply.tool_calls.append(block.name)
                entry: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                }
                if failed:
                    entry["is_error"] = True
                results.append(entry)

            # All results for one assistant turn go back in a single user
            # message -- splitting them teaches Claude to stop calling tools
            # in parallel.
            self.messages.append({"role": "user", "content": results})

        reply.text = reply.text or "I went round in circles on that one and stopped."
        return reply


def build_brain(config, registry: ToolRegistry, bus: EventBus, store=None, client=None) -> Brain:
    return Brain(config=config, registry=registry, bus=bus, store=store, client=client)

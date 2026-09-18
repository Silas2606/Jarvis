"""Wiring everything into a running assistant.

Assembly order matters and is slightly circular: the tool registry needs a way
to ask the user for confirmation, but that way only exists once the voice loop
is built, and the voice loop needs the brain, which needs the registry. The
knot is untied by handing the registry its ``confirm`` callback after the loop
exists -- the tool context is mutable for exactly this reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from jarvis.brain import Brain, BrainError
from jarvis.config import Config, load_config
from jarvis.events import EventBus, EventKind
from jarvis.memory import MemoryStore
from jarvis.persona import language_name
from jarvis.scheduler import ReminderService
from jarvis.tools import build_registry
from jarvis.ui.console import Console, attach_console


@dataclass
class Session:
    """A fully assembled Jarvis."""

    config: Config
    store: MemoryStore
    bus: EventBus
    brain: Brain
    reminders: ReminderService
    console: Console | None = None

    def close(self) -> None:
        self.reminders.stop()
        self.store.close()


def build_session(
    config: Config | None = None,
    verbose: bool = False,
    with_console: bool = True,
    client: Any = None,
    confirm: Callable[[str], bool] | None = None,
) -> Session:
    """Assemble the assistant without starting to listen."""
    config = config or load_config()
    config.ensure_home()

    bus = EventBus()
    console = attach_console(bus, config.address, verbose) if with_console else None
    store = MemoryStore(config.db_path)
    registry = build_registry(config, store, bus, confirm=confirm)
    brain = Brain(config, registry, bus, store, client=client)
    reminders = ReminderService(store, bus)

    return Session(
        config=config,
        store=store,
        bus=bus,
        brain=brain,
        reminders=reminders,
        console=console,
    )


def greeting_for(config: Config) -> str:
    """What Jarvis says when he comes online."""
    from datetime import datetime

    from jarvis.timing import resolve_zone

    hour = datetime.now(resolve_zone(config.timezone)).hour
    german = config.voice.language.startswith("de")

    if hour < 11:
        opener = "Guten Morgen" if german else "Good morning"
    elif hour < 18:
        opener = "Guten Tag" if german else "Good afternoon"
    else:
        opener = "Guten Abend" if german else "Good evening"

    tail = "Alle Systeme bereit." if german else "All systems ready."
    return f"{opener}, {config.address}. {tail}"


def run_voice(session: Session, greet: bool = True) -> int:
    """Start listening. This is the main mode."""
    from jarvis.voice import AudioUnavailable
    from jarvis.voice.loop import VoiceLoop
    from jarvis.voice.stt import build_transcriber
    from jarvis.voice.tts import build_speaker
    from jarvis.voice.wakeword import build_detector

    config = session.config

    transcriber = build_transcriber(config)
    if transcriber.name == "none":
        session.bus.emit(
            EventKind.ERROR,
            "No speech recognition available. Install the voice stack with "
            'pip install "jarvis-assistant[voice]", or talk to me in text mode '
            "with: jarvis --text",
        )
        return 2

    speaker = build_speaker(config)
    if speaker.name == "none":
        session.bus.emit(
            EventKind.NOTICE,
            "No speech synthesis available -- I will answer in writing only. "
            "Install piper or edge-tts to give me a voice.",
        )

    detector = build_detector(config, transcriber)

    loop = VoiceLoop(
        config=config,
        brain=session.brain,
        bus=session.bus,
        transcriber=transcriber,
        speaker=speaker,
        detector=detector,
        reminders=session.reminders,
    )
    # Now that the loop exists, tools can ask the user out loud.
    session.brain.registry.context.confirm = loop.confirm

    if session.console is not None:
        session.console.banner(
            model=config.brain.model,
            voice=speaker.name,
            ears=transcriber.name,
            tools=len(session.brain.registry),
        )
        wake = " / ".join(config.voice.wake_words)
        session.console.notice(
            f"Say “{wake}” to wake me. Wake word detection: {detector.name}. "
            f"Language: {language_name(config.voice.language)}. Ctrl-C to stop."
        )
        session.console.rule()

    try:
        loop.run(greeting=greeting_for(config) if greet else "")
    except AudioUnavailable as exc:
        session.bus.emit(EventKind.ERROR, str(exc))
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        loop.stop()
    return 0


def run_text(session: Session, greet: bool = True) -> int:
    """Type instead of talk. For testing, or a machine with no microphone."""
    config = session.config

    def ask_confirmation(question: str) -> bool:
        from jarvis.voice.loop import interpret_yes_no

        session.bus.emit(EventKind.CONFIRM, question)
        for _ in range(2):
            try:
                answer = input("  [y/n] > ").strip()
            except (EOFError, KeyboardInterrupt):
                return False
            decision = interpret_yes_no(answer) if answer else None
            if decision is None and answer.lower() in {"y", "j"}:
                decision = True
            if decision is None and answer.lower() == "n":
                decision = False
            if decision is not None:
                return decision
        return False

    session.brain.registry.context.confirm = ask_confirmation
    session.reminders.start()

    if session.console is not None:
        session.console.banner(
            model=config.brain.model,
            voice="text",
            ears="keyboard",
            tools=len(session.brain.registry),
        )
        session.console.notice("Text mode. Type your instruction, or “exit” to stop.")
        session.console.rule()

    if greet:
        session.bus.emit(EventKind.ANSWER, greeting_for(config))

    while True:
        try:
            line = input(f"  {config.address}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() in {"exit", "quit", "ende", "tschüss", "bye"}:
            break

        session.store.log_turn("user", line)
        try:
            reply = session.brain.ask(line)
        except BrainError as exc:
            session.bus.emit(EventKind.ERROR, exc.spoken, detail=exc.detail, problem=exc.kind)
            continue
        if reply.text:
            session.bus.emit(EventKind.ANSWER, reply.text, tools=reply.tool_calls)
            session.store.log_turn("assistant", reply.text)

    session.reminders.stop()
    return 0


def ask_once(session: Session, text: str) -> int:
    """Run a single instruction and print the answer. Handy for scripts."""
    session.brain.registry.context.confirm = lambda question: False
    try:
        reply = session.brain.ask(text)
    except BrainError as exc:
        session.bus.emit(EventKind.ERROR, exc.spoken, detail=exc.detail, problem=exc.kind)
        return 1
    if reply.text:
        session.bus.emit(EventKind.ANSWER, reply.text, tools=reply.tool_calls)
    return 0

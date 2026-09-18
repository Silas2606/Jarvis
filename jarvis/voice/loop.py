"""The listening loop: wake, record, transcribe, answer, repeat.

The shape of one exchange:

    wait for the wake word
      -> record until the user stops talking
      -> transcribe
      -> put it to Claude, whose sentences are spoken as they are generated
      -> keep listening for a while without needing the wake word again

While Jarvis is speaking, a watcher keeps an eye on the microphone. If the user
starts talking over him, speech stops and the turn is abandoned -- which is the
single thing that most makes a voice assistant feel like a conversation rather
than a vending machine.
"""

from __future__ import annotations

import threading
import time

from jarvis.brain import Brain, BrainError
from jarvis.events import Event, EventBus, EventKind
from jarvis.voice.audio import (
    Microphone,
    VoiceActivityDetector,
    record_utterance,
)
from jarvis.voice.speech_queue import SpeechQueue
from jarvis.voice.wakeword import AlwaysAwakeDetector, WakeWordDetector

# Consecutive voiced frames needed to call it an interruption. Jarvis' own
# voice leaks into an open microphone, so a single frame proves nothing;
# roughly a third of a second of continuous speech does.
BARGE_IN_FRAMES = 11

_YES = {
    "ja", "jawohl", "jep", "jo", "klar", "sicher", "bitte", "genau", "mach", "machen",
    "okay", "ok", "gerne", "richtig", "korrekt", "stimmt", "los", "einverstanden",
    "yes", "yeah", "yep", "sure", "please", "go", "do", "confirm", "correct", "right",
}

_NO = {
    "nein", "ne", "nee", "nicht", "stopp", "stop", "abbrechen", "lass", "warte",
    "no", "nope", "cancel", "abort", "wait", "never",
}

# Phrases that contain a "no" word but are not an answer. "I don't know" must
# lead to another question, not to a silent refusal.
_UNSURE = (
    "weiß nicht", "weiss nicht", "keine ahnung", "vielleicht", "möglicherweise",
    "moment", "sekunde", "mal sehen", "unsicher",
    "don't know", "dont know", "not sure", "maybe", "perhaps", "hang on",
)


def interpret_yes_no(text: str) -> bool | None:
    """Read a spoken answer as yes, no, or neither."""
    lowered = text.lower()
    if any(phrase in lowered for phrase in _UNSURE):
        return None

    words = {w.strip(".,!?;:").lower() for w in lowered.split()}
    if not words:
        return None
    # "nein" beats "ja" if somehow both appear -- refusing is the safe reading.
    if words & _NO:
        return False
    if words & _YES:
        return True
    return None


class VoiceLoop:
    """Runs Jarvis' ears and mouth."""

    def __init__(
        self,
        config,
        brain: Brain,
        bus: EventBus,
        transcriber,
        speaker,
        detector: WakeWordDetector,
        reminders=None,
    ):
        self.config = config
        self.brain = brain
        self.bus = bus
        self.transcriber = transcriber
        self.detector = detector
        self.reminders = reminders

        self.speech = SpeechQueue(speaker)
        self.microphone = Microphone(config.voice.sample_rate, config.voice.input_device)
        self.vad = VoiceActivityDetector(config.voice.sample_rate)

        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._watcher: threading.Thread | None = None
        self._watching = threading.Event()
        self._interrupted = threading.Event()
        self._awake_detector = AlwaysAwakeDetector()

        self.bus.subscribe(self._on_event)

    # -- event wiring --------------------------------------------------------

    def _on_event(self, event: Event) -> None:
        """Anything meant to be heard goes into the speaking queue."""
        if event.kind is EventKind.SPEECH_CHUNK:
            self.speech.say(event.text)
        elif event.kind is EventKind.REMINDER:
            self.speech.say(event.text)

    # -- barge-in ------------------------------------------------------------

    def _watch_for_barge_in(self) -> None:
        """Listen while Jarvis talks; cancel the turn if the user cuts in."""
        detector = VoiceActivityDetector(self.config.voice.sample_rate, aggressiveness=3)
        run = 0
        while self._watching.is_set():
            frame = self.microphone.read(timeout=0.2)
            if frame is None:
                continue
            if detector.is_speech(frame):
                run += 1
                if run >= BARGE_IN_FRAMES:
                    self._interrupted.set()
                    self._cancel.set()
                    self.speech.interrupt()
                    self.bus.emit(EventKind.NOTICE, "Interrupted.")
                    return
            else:
                run = 0

    def _start_watching(self) -> None:
        if not self.config.voice.barge_in or self._watching.is_set():
            return
        self._watching.set()
        self._watcher = threading.Thread(
            target=self._watch_for_barge_in, name="jarvis-bargein", daemon=True
        )
        self._watcher.start()

    def _stop_watching(self) -> None:
        self._watching.clear()
        watcher, self._watcher = self._watcher, None
        if watcher is not None:
            watcher.join(timeout=1.0)

    # -- spoken confirmation -------------------------------------------------

    def confirm(self, question: str) -> bool:
        """Ask the user a yes/no question out loud and wait for the answer.

        This is what makes ``confirm=`` on a tool work: it is handed to the
        registry, which calls it before anything leaves the house.
        """
        self.bus.emit(EventKind.CONFIRM, question)

        # Let the queue finish so the question is not spoken over, and pause
        # the watcher so the answer is not mistaken for an interruption.
        self._stop_watching()
        self.speech.wait_until_done(timeout=30.0)
        self.speech.say(question)
        self.speech.wait_until_done(timeout=30.0)
        self.microphone.drain()

        for _attempt in range(2):
            utterance = record_utterance(
                self.microphone,
                self.vad,
                silence_timeout=self.config.voice.silence_timeout,
                max_seconds=8.0,
                start_timeout=8.0,
            )
            if utterance is None:
                break
            try:
                text = self.transcriber.transcribe(utterance.audio, utterance.sample_rate)
            except Exception:
                break
            if not text:
                continue
            self.bus.emit(EventKind.HEARD, text)
            answer = interpret_yes_no(text)
            if answer is not None:
                return answer
            self.speech.say("Ist das ein Ja oder ein Nein?" if self.config.voice.language.startswith("de") else "Is that a yes or a no?")
            self.speech.wait_until_done(timeout=20.0)

        # No clear answer: treat silence as refusal.
        return False

    # -- one exchange --------------------------------------------------------

    def handle(self, text: str) -> None:
        """Put one utterance to Claude and speak the answer."""
        if not text.strip():
            return

        self.bus.emit(EventKind.HEARD, text)
        if self.brain.store is not None:
            self.brain.store.log_turn("user", text)

        self._cancel.clear()
        self._interrupted.clear()
        self._start_watching()

        try:
            reply = self.brain.ask(text, cancel=self._cancel)
        except BrainError as exc:
            self._stop_watching()
            # The user hears the sentence; the console also gets the detail.
            self.bus.emit(EventKind.ERROR, exc.spoken, detail=exc.detail, problem=exc.kind)
            self.speech.say(exc.spoken)
            self.speech.wait_until_done(timeout=30.0)
            return

        if reply.cancelled:
            self._stop_watching()
            self.speech.interrupt()
            return

        # Wait for the last sentence to be spoken, keeping the watcher alive so
        # the user can still cut in during the tail of a long answer.
        while self.speech.is_busy and not self._interrupted.is_set():
            time.sleep(0.05)
        self._stop_watching()

        if reply.text:
            self.bus.emit(EventKind.ANSWER, reply.text, tools=reply.tool_calls)
            if self.brain.store is not None:
                self.brain.store.log_turn("assistant", reply.text)

        # Jarvis' own voice is still echoing in the buffer; drop it.
        self.microphone.drain()

    # -- the loop ------------------------------------------------------------

    def _listen_once(self, detector: WakeWordDetector) -> str | None:
        """Wait for a trigger and return what was said, if anything."""
        wake = detector.wait(self.microphone, self._stop)
        if wake is None:
            return None
        if not isinstance(detector, AlwaysAwakeDetector):
            self.bus.emit(EventKind.WAKE)

        # The transcription detector already has the words.
        if wake.command:
            return wake.command
        if wake.transcript and not wake.command:
            # The name was called with nothing after it: listen for the order.
            pass

        self.bus.emit(EventKind.LISTENING)
        utterance = record_utterance(
            self.microphone,
            self.vad,
            silence_timeout=self.config.voice.silence_timeout,
            max_seconds=self.config.voice.max_utterance_seconds,
            start_timeout=8.0,
            prefix=wake.prefix_audio,
            cancel=self._stop,
        )
        if utterance is None:
            return None

        try:
            return self.transcriber.transcribe(utterance.audio, utterance.sample_rate)
        except Exception as exc:
            self.bus.emit(EventKind.ERROR, f"I could not make that out: {exc}")
            return None

    def run(self, greeting: str = "") -> None:
        """Listen until stopped."""
        self.microphone.start()
        self.speech.start()
        if self.reminders is not None:
            self.reminders.start()

        if greeting:
            self.speech.say(greeting)
            self.speech.wait_until_done(timeout=30.0)
            self.microphone.drain()

        follow_up_until = 0.0

        try:
            while not self._stop.is_set():
                in_window = time.monotonic() < follow_up_until
                detector = self._awake_detector if in_window else self.detector

                if not in_window:
                    self.bus.emit(EventKind.LISTENING, "waiting for the wake word")

                text = self._listen_once(detector)
                if self._stop.is_set():
                    break
                if not text:
                    # Nothing said during the follow-up window: go back to sleep.
                    if in_window:
                        follow_up_until = 0.0
                    continue

                self.handle(text)
                follow_up_until = time.monotonic() + self.config.voice.followup_window
        except KeyboardInterrupt:  # pragma: no cover
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        self._stop.set()
        self._cancel.set()
        self._stop_watching()
        self.speech.shutdown()
        self.microphone.stop()
        if self.reminders is not None:
            self.reminders.stop()

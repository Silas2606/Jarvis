"""Waking Jarvis up.

Two ways to hear your own name:

``openwakeword``  A small neural spotter that runs continuously on the audio
                  stream. It ships a pre-trained "hey jarvis" model, which is
                  exactly what we want. Low CPU, no transcription.

transcription     The fallback: capture short utterances, transcribe them, and
                  look for the wake word in the text. Heavier, but it works
                  with nothing beyond the STT engine -- and it gets the command
                  for free, since "Jarvis, what time is it?" arrives as one
                  utterance and the words after the name are the instruction.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

from jarvis.voice.audio import Microphone, VoiceActivityDetector, record_utterance


@dataclass
class WakeResult:
    """What the detector heard."""

    # Audio captured around the trigger, prepended to the recording so the
    # first syllable of the instruction is not clipped.
    prefix_audio: bytes = b""
    # Already-transcribed text, when the detector had to transcribe anyway.
    transcript: str = ""
    # The instruction that followed the wake word in the same breath.
    command: str = ""


class WakeWordDetector:
    """Interface: block until the wake word is heard."""

    name = "none"

    def wait(self, microphone: Microphone, cancel: threading.Event | None = None) -> WakeResult | None:
        raise NotImplementedError  # pragma: no cover


# Words that may precede the name and still mean "I am talking to you".
# Anything else in front of it means the user is talking *about* Jarvis.
_ADDRESS_PREFIX = re.compile(
    r"^[\s,.:;!?-]*(?:(?:ok|okay|okey|hey|hi|he|hallo|hello|also|sag mal|sag)\b[\s,.:;!?-]*)*",
    re.IGNORECASE,
)


def strip_wake_word(text: str, wake_words: tuple[str, ...]) -> tuple[bool, str]:
    """Find a leading wake word and return what was said after it.

    Returns ``(heard, remainder)``. The name only counts at the very start of
    the utterance, optionally behind a greeting -- otherwise "I saw Jarvis
    yesterday" would wake him up mid-conversation.
    """
    if not text.strip():
        return False, ""

    start = _ADDRESS_PREFIX.match(text).end()

    # Longest first, so "hey jarvis" wins over "jarvis".
    for word in sorted(wake_words, key=len, reverse=True):
        pattern = re.compile(
            rf"{re.escape(word.lower())}\b[\s,.:;!?-]*", re.IGNORECASE
        )
        match = pattern.match(text, start)
        if match:
            return True, text[match.end():].strip()
    return False, ""


class OpenWakeWordDetector(WakeWordDetector):
    """The openwakeword spotter, with its pre-trained "hey jarvis" model."""

    name = "openwakeword"

    def __init__(self, model: str = "hey_jarvis", threshold: float = 0.5, sample_rate: int = 16000):
        try:
            from openwakeword.model import Model
        except ImportError as exc:
            raise RuntimeError("openwakeword is not installed.") from exc

        try:
            from openwakeword.utils import download_models

            download_models([model])
        except Exception:
            pass  # already downloaded, or offline with a cached copy

        try:
            self._model = Model(wakeword_models=[model])
        except Exception:
            # An unknown name: load the bundled set and match on prefix later.
            self._model = Model()
        self.threshold = threshold
        self.sample_rate = sample_rate
        # openwakeword wants 80 ms chunks at 16 kHz.
        self._chunk_bytes = int(sample_rate * 0.08) * 2

    def wait(self, microphone: Microphone, cancel: threading.Event | None = None) -> WakeResult | None:
        import numpy

        buffer = b""
        # A rolling half second kept as the run-up for the recording.
        history = b""

        while True:
            if cancel is not None and cancel.is_set():
                return None
            frame = microphone.read(timeout=0.5)
            if frame is None:
                continue
            buffer += frame
            history = (history + frame)[-self.sample_rate:]

            while len(buffer) >= self._chunk_bytes:
                chunk, buffer = buffer[: self._chunk_bytes], buffer[self._chunk_bytes :]
                samples = numpy.frombuffer(chunk, dtype=numpy.int16)
                scores = self._model.predict(samples)
                if any(score >= self.threshold for score in scores.values()):
                    self._model.reset()
                    return WakeResult(prefix_audio=history[-self.sample_rate // 2 :])


class TranscriptionWakeDetector(WakeWordDetector):
    """Transcribe short utterances and look for the name in the words."""

    name = "transcription"

    def __init__(self, transcriber, wake_words: tuple[str, ...], sample_rate: int = 16000, language: str = "de"):
        self.transcriber = transcriber
        self.wake_words = wake_words
        self.sample_rate = sample_rate
        self.detector = VoiceActivityDetector(sample_rate)

    def wait(self, microphone: Microphone, cancel: threading.Event | None = None) -> WakeResult | None:
        while True:
            if cancel is not None and cancel.is_set():
                return None

            utterance = record_utterance(
                microphone,
                self.detector,
                # Short windows: we only need enough to catch the name, and the
                # rest of the sentence rides along if the user kept talking.
                silence_timeout=0.9,
                max_seconds=12.0,
                start_timeout=3600.0,
                cancel=cancel,
            )
            if utterance is None:
                continue

            try:
                text = self.transcriber.transcribe(utterance.audio, utterance.sample_rate)
            except Exception:
                continue
            if not text:
                continue

            heard, remainder = strip_wake_word(text, self.wake_words)
            if heard:
                return WakeResult(transcript=text, command=remainder)


class AlwaysAwakeDetector(WakeWordDetector):
    """No wake word: every utterance counts. Used inside the follow-up window."""

    name = "always"

    def wait(self, microphone: Microphone, cancel: threading.Event | None = None) -> WakeResult | None:
        return WakeResult()


def build_detector(config, transcriber=None) -> WakeWordDetector:
    """Pick the best wake-word detector available."""
    voice = config.voice
    try:
        return OpenWakeWordDetector(voice.wake_model, sample_rate=voice.sample_rate)
    except Exception:
        pass
    if transcriber is not None and getattr(transcriber, "name", "none") != "none":
        return TranscriptionWakeDetector(
            transcriber, tuple(voice.wake_words), voice.sample_rate, voice.language
        )
    return AlwaysAwakeDetector()

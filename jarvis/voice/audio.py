"""Microphone capture and voice-activity detection.

Audio comes in as 16-bit mono PCM at the configured rate, in short frames. The
frames feed three consumers: the wake-word detector, the recorder that captures
an utterance, and the barge-in watcher that listens while Jarvis is speaking.

``sounddevice`` and ``numpy`` are optional dependencies -- importing this module
without them is fine, and only using a microphone raises.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Iterator

from jarvis.voice import AudioUnavailable

# 30 ms frames: what webrtcvad expects, and a good granularity for barge-in.
FRAME_MS = 30


def _require_audio():
    try:
        import numpy
        import sounddevice
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise AudioUnavailable(
            "The audio stack is missing. Install it with: "
            'pip install "jarvis-assistant[voice]" '
            "(on Linux you may also need the system package portaudio19-dev)."
        ) from exc
    return sounddevice, numpy


def list_devices() -> str:
    """A human-readable rundown of the audio devices, for `jarvis doctor`."""
    sounddevice, _ = _require_audio()
    return str(sounddevice.query_devices())


class Microphone:
    """An open input stream whose frames can be pulled off a queue."""

    def __init__(self, sample_rate: int = 16000, device: int | None = None, frame_ms: int = FRAME_MS):
        self.sample_rate = sample_rate
        self.device = device
        self.frame_ms = frame_ms
        self.frame_samples = int(sample_rate * frame_ms / 1000)
        self._queue: queue.Queue = queue.Queue(maxsize=200)
        self._stream = None
        self._lock = threading.Lock()

    def __enter__(self) -> "Microphone":
        self.start()
        return self

    def __exit__(self, *exc_info) -> bool:
        self.stop()
        return False

    def start(self) -> None:
        sounddevice, _ = _require_audio()
        with self._lock:
            if self._stream is not None:
                return

            def callback(indata, _frames, _time, status):  # pragma: no cover - realtime
                if status:
                    pass  # overflows are survivable; dropping a frame is fine
                try:
                    self._queue.put_nowait(bytes(indata))
                except queue.Full:
                    pass

            try:
                self._stream = sounddevice.RawInputStream(
                    samplerate=self.sample_rate,
                    blocksize=self.frame_samples,
                    device=self.device,
                    dtype="int16",
                    channels=1,
                    callback=callback,
                )
                self._stream.start()
            except Exception as exc:
                self._stream = None
                raise AudioUnavailable(f"The microphone could not be opened: {exc}") from exc

    def stop(self) -> None:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                finally:
                    self._stream = None

    def drain(self) -> None:
        """Throw away buffered audio, e.g. what Jarvis' own voice leaked in."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def frames(self, timeout: float = 1.0) -> Iterator[bytes]:
        """Yield frames as they arrive; stops when the stream goes quiet."""
        while True:
            try:
                yield self._queue.get(timeout=timeout)
            except queue.Empty:
                return

    def read(self, timeout: float = 1.0) -> bytes | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None


class VoiceActivityDetector:
    """Tells speech from silence.

    Prefers ``webrtcvad``. Without it, falls back to an energy gate whose noise
    floor adapts, so a humming fridge does not count as speech.
    """

    def __init__(self, sample_rate: int = 16000, aggressiveness: int = 2, frame_ms: int = FRAME_MS):
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self._vad = None
        try:
            import webrtcvad

            self._vad = webrtcvad.Vad(aggressiveness)
        except Exception:
            self._vad = None
        # Energy-gate state.
        self._floor: float | None = None
        self.backend = "webrtcvad" if self._vad is not None else "energy"

    @staticmethod
    def _rms(frame: bytes) -> float:
        import array
        import math

        samples = array.array("h")
        samples.frombytes(frame[: len(frame) // 2 * 2])
        if not samples:
            return 0.0
        total = sum(float(s) * float(s) for s in samples)
        return math.sqrt(total / len(samples))

    def is_speech(self, frame: bytes) -> bool:
        if self._vad is not None:
            try:
                return self._vad.is_speech(frame, self.sample_rate)
            except Exception:
                pass  # wrong frame length: fall through to the energy gate

        level = self._rms(frame)
        if self._floor is None:
            self._floor = max(level, 1.0)
        # The floor falls quickly towards quiet and rises slowly, so it tracks
        # the room rather than the speaker.
        if level < self._floor:
            self._floor = 0.9 * self._floor + 0.1 * level
        else:
            self._floor = 0.995 * self._floor + 0.005 * level
        return level > max(self._floor * 3.0, 180.0)


@dataclass
class Utterance:
    """One captured stretch of speech."""

    audio: bytes
    sample_rate: int
    seconds: float

    def __bool__(self) -> bool:
        return bool(self.audio)


def record_utterance(
    microphone: Microphone,
    detector: VoiceActivityDetector,
    silence_timeout: float = 1.1,
    max_seconds: float = 30.0,
    start_timeout: float = 8.0,
    prefix: bytes = b"",
    cancel: threading.Event | None = None,
) -> Utterance | None:
    """Record until the speaker stops talking.

    Args:
        microphone: An already-started microphone.
        detector: Voice activity detector.
        silence_timeout: Stop after this much quiet once speech has begun.
        max_seconds: Hard ceiling, so a noisy room cannot record forever.
        start_timeout: Give up if nobody says anything within this long.
        prefix: Audio to prepend -- typically the frames that triggered the
            wake word, so the first syllable is not clipped.
        cancel: Abort the recording when set.

    Returns:
        The utterance, or None if nothing was said.
    """
    frame_seconds = microphone.frame_ms / 1000.0
    silence_frames_needed = max(int(silence_timeout / frame_seconds), 1)
    max_frames = int(max_seconds / frame_seconds)
    start_frames = int(start_timeout / frame_seconds)

    collected: list[bytes] = [prefix] if prefix else []
    speaking = False
    silent_run = 0
    waited = 0

    while len(collected) < max_frames:
        if cancel is not None and cancel.is_set():
            return None
        frame = microphone.read(timeout=1.0)
        if frame is None:
            waited += 1
            if not speaking and waited > 3:
                return None
            continue

        voiced = detector.is_speech(frame)

        if not speaking:
            waited += 1
            if voiced:
                speaking = True
                collected.append(frame)
            elif waited > start_frames:
                return None
            else:
                # Keep a short run-up so the opening consonant survives.
                collected.append(frame)
                if len(collected) > 10:
                    collected.pop(0)
            continue

        collected.append(frame)
        if voiced:
            silent_run = 0
        else:
            silent_run += 1
            if silent_run >= silence_frames_needed:
                break

    if not speaking:
        return None

    audio = b"".join(collected)
    seconds = len(audio) / 2 / microphone.sample_rate
    # Anything this short is a cough, a click or a door.
    if seconds < 0.35:
        return None
    return Utterance(audio=audio, sample_rate=microphone.sample_rate, seconds=seconds)


def pcm_to_wav(audio: bytes, sample_rate: int) -> bytes:
    """Wrap raw PCM in a WAV container, which every STT engine understands."""
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(audio)
    return buffer.getvalue()

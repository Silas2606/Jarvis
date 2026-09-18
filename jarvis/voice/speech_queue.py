"""A speaking queue, so synthesis of one sentence overlaps generation of the next."""

from __future__ import annotations

import queue
import threading

from jarvis.voice.tts import Speaker

_SENTINEL = object()


class SpeechQueue:
    """Hands sentences to the speaker one at a time, on its own thread."""

    def __init__(self, speaker: Speaker):
        self.speaker = speaker
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._idle = threading.Event()
        self._idle.set()
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="jarvis-voice", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            item = self._queue.get()
            if item is _SENTINEL:
                self._queue.task_done()
                break
            self._idle.clear()
            try:
                self.speaker.say(str(item))
            except Exception:
                pass  # a mute assistant beats a crashed one
            finally:
                self._queue.task_done()
                if self._queue.empty():
                    self._idle.set()

    def say(self, text: str) -> None:
        """Queue a sentence."""
        if not text.strip():
            return
        self.start()
        self._idle.clear()
        self._queue.put(text)

    @property
    def is_busy(self) -> bool:
        return not self._idle.is_set() or self.speaker.is_speaking

    def wait_until_done(self, timeout: float | None = None) -> bool:
        """Block until everything queued has been spoken."""
        return self._idle.wait(timeout)

    def interrupt(self) -> None:
        """Stop mid-sentence and throw away whatever was still queued."""
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break
        self.speaker.stop()
        self._idle.set()

    def shutdown(self) -> None:
        self.interrupt()
        self._stop.set()
        self._queue.put(_SENTINEL)
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)

"""Cutting a stream of tokens into sentences that can be spoken.

A voice assistant must not wait for the full answer before opening its mouth.
Text arrives token by token; this turns it into whole sentences as soon as one
is complete, so synthesis of sentence one overlaps generation of sentence two.
"""

from __future__ import annotations

import re

# Terminators that reliably end a spoken sentence.
_ENDINGS = ".!?…\n"

# Things that look like an ending but are not: abbreviations, ordinals,
# decimals and clock times ("17.30", "z. B.", "Dr.", "1.", "Mr.").
_FALSE_ENDING = re.compile(
    r"(?:"
    r"\b\d+[.,]$"                                   # 17. / 3,
    r"|\b(?:z|d|u|bzw|ca|evtl|ggf|inkl|max|min|Nr|Dr|Prof|St|Mr|Mrs|Ms|vs|etc|e\.g|i\.e|approx)\.$"
    r"|\b[A-Za-zÄÖÜäöü]\.$"                         # single initial: "A."
    r")",
    re.IGNORECASE,
)

# Never emit a scrap shorter than this: too-short fragments make the voice
# sound chopped.
MIN_CHUNK = 24
# The opening fragment is the exception. Getting the voice started early is
# what the user perceives as responsiveness, so a short "Good morning, Sir."
# goes out on its own rather than waiting for the sentence after it.
FIRST_CHUNK_MIN = 10
# Speak a fragment once it is this long even without a sentence ending, so a
# long clause does not stall the voice.
MAX_CHUNK = 300


class SentenceAccumulator:
    """Feed it token deltas, take finished sentences out."""

    def __init__(
        self,
        min_chunk: int = MIN_CHUNK,
        max_chunk: int = MAX_CHUNK,
        first_chunk_min: int = FIRST_CHUNK_MIN,
    ):
        self.min_chunk = min_chunk
        self.max_chunk = max_chunk
        self.first_chunk_min = first_chunk_min
        self._buffer = ""
        self._emitted = 0

    def feed(self, delta: str) -> list[str]:
        """Add streamed text; return any sentences that are now complete."""
        self._buffer += delta
        finished: list[str] = []

        while True:
            cut = self._find_cut()
            if cut is None:
                break
            chunk, self._buffer = self._buffer[:cut].strip(), self._buffer[cut:].lstrip()
            if chunk:
                finished.append(chunk)
                self._emitted += 1
        return finished

    def _find_cut(self) -> int | None:
        """Index just past a sentence ending, or None if there is none yet."""
        floor = self.first_chunk_min if self._emitted == 0 else self.min_chunk
        for index, character in enumerate(self._buffer):
            if character not in _ENDINGS:
                continue
            head = self._buffer[: index + 1]
            if len(head.strip()) < floor and character != "\n":
                continue
            if _FALSE_ENDING.search(head.rstrip()):
                continue
            # A terminator followed by a digit is almost always a number.
            tail = self._buffer[index + 1 : index + 2]
            if character in ".," and tail.isdigit():
                continue
            return index + 1

        # No terminator, but the buffer has grown long: cut at the last comma
        # or space so the voice keeps moving.
        if len(self._buffer) > self.max_chunk:
            window = self._buffer[: self.max_chunk]
            for separator in (", ", "; ", " – ", " - ", " "):
                position = window.rfind(separator)
                if position > self.min_chunk:
                    return position + len(separator)
        return None

    def flush(self) -> str:
        """Everything still held back, e.g. at the end of an answer."""
        remainder, self._buffer = self._buffer.strip(), ""
        if remainder:
            self._emitted += 1
        return remainder

    @property
    def pending(self) -> str:
        return self._buffer


def split_sentences(text: str) -> list[str]:
    """Split a finished block of text into speakable sentences."""
    accumulator = SentenceAccumulator()
    chunks = accumulator.feed(text)
    remainder = accumulator.flush()
    if remainder:
        chunks.append(remainder)
    return chunks

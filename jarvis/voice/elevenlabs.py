"""ElevenLabs speech synthesis.

The most natural-sounding option Jarvis can use, at the cost of a second API
key and a per-character bill. Talks to the REST API directly through the
standard library -- no extra dependency for something this small.

Two things here are tuned for a voice assistant rather than for batch
narration:

* Audio is requested as raw PCM and played as it arrives, so speech starts
  before synthesis has finished. If the account's plan refuses PCM, the code
  falls back to MP3 once and remembers.
* The default model is the low-latency one. Quality per sentence matters less
  here than the gap before Jarvis starts talking.

Note: written against the documented REST shape; the API reference was not
reachable from the build environment, so `jarvis voices` and a first
`jarvis say` are the real verification.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterator

from jarvis.voice import AudioUnavailable
from jarvis.voice.tts import Speaker, _play_file, _play_pcm

API_ROOT = "https://api.elevenlabs.io/v1"

# eleven_flash_v2_5 is the fastest multilingual model; eleven_multilingual_v2
# sounds better but takes noticeably longer to first audio.
DEFAULT_MODEL = "eleven_flash_v2_5"

# Raw PCM avoids an MP3 decode and an external player, and can be streamed
# straight into the sound card.
PCM_FORMAT = "pcm_24000"
PCM_RATE = 24000
MP3_FORMAT = "mp3_44100_128"

# Read in small pieces so playback can start on the first one.
CHUNK = 4096
TIMEOUT = 60


class ElevenLabsError(AudioUnavailable):
    """The service refused, with a reason worth repeating."""


def _api_key(explicit: str = "") -> str:
    key = explicit or os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        raise ElevenLabsError(
            "No ElevenLabs API key. Create one at elevenlabs.io under Profile, "
            "then set ELEVENLABS_API_KEY."
        )
    return key


def _request(path: str, key: str, method: str = "GET", payload: dict | None = None):
    """Open a request against the API, translating failures on the way out."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{API_ROOT}{path}",
        data=data,
        method=method,
        headers={
            "xi-api-key": key,
            "Content-Type": "application/json",
            "Accept": "*/*",
        },
    )
    try:
        return urllib.request.urlopen(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as exc:
        raise _http_failure(exc) from exc
    except urllib.error.URLError as exc:
        raise ElevenLabsError(f"ElevenLabs is not reachable: {exc.reason}") from exc


def _http_failure(exc: urllib.error.HTTPError) -> ElevenLabsError:
    """Say what the service objected to, in a sentence."""
    detail = ""
    try:
        body = json.loads(exc.read().decode("utf-8", "replace"))
        error = body.get("detail", body)
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("status") or error)
        else:
            detail = str(error)
    except Exception:
        detail = exc.reason or ""

    if exc.code == 401:
        return ElevenLabsError("The ElevenLabs API key was rejected. Check ELEVENLABS_API_KEY.")
    if exc.code == 404:
        return ElevenLabsError(
            "That ElevenLabs voice does not exist. Run `jarvis voices` to see yours."
        )
    if exc.code == 429:
        # Deliberately vague: whether this is a spent month or a burst of
        # requests is decided by asking the account, not by reading a status.
        return ElevenLabsError(f"ElevenLabs declined for now (429). {detail}".strip())
    if exc.code == 422:
        return ElevenLabsError(f"ElevenLabs rejected the request: {detail}")
    return ElevenLabsError(f"ElevenLabs returned an error ({exc.code}): {detail}")


def list_voices(api_key: str = "") -> list[tuple[str, str, str]]:
    """Every voice on the account, as ``(voice_id, name, description)``."""
    key = _api_key(api_key)
    with _request("/voices", key) as response:
        payload = json.loads(response.read().decode("utf-8"))

    voices: list[tuple[str, str, str]] = []
    for voice in payload.get("voices", []):
        labels = voice.get("labels") or {}
        descriptors = [str(v) for v in labels.values() if v]
        voices.append(
            (
                voice.get("voice_id", ""),
                voice.get("name", "?"),
                ", ".join(descriptors[:4]),
            )
        )
    return voices


@dataclass
class Subscription:
    """What the account has spent and when it is refilled."""

    used: int = 0
    limit: int = 0
    reset_at: float = 0.0
    known: bool = False

    @property
    def exhausted(self) -> bool:
        """Whether the character allowance is genuinely used up."""
        return self.known and self.limit > 0 and self.used >= self.limit

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used) if self.known else 0

    def describe(self) -> str:
        if not self.known or not self.limit:
            return ""
        return f"{self.used:,} / {self.limit:,} characters used".replace(",", ".")


def subscription(api_key: str = "") -> Subscription:
    """Ask the account what it has left.

    A 429 does not say whether the month's allowance is gone or the requests
    merely came too fast. This does, and the difference is a month of waiting
    against a few seconds.
    """
    try:
        with _request("/user/subscription", _api_key(api_key)) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return Subscription()

    reset = 0.0
    for field in ("next_character_count_reset_unix", "next_invoice_time_unix"):
        value = payload.get(field)
        if isinstance(value, (int, float)) and value > 0:
            reset = float(value)
            break

    used = payload.get("character_count")
    limit = payload.get("character_limit")
    return Subscription(
        used=int(used) if isinstance(used, (int, float)) else 0,
        limit=int(limit) if isinstance(limit, (int, float)) else 0,
        reset_at=reset,
        known=isinstance(used, (int, float)) and isinstance(limit, (int, float)),
    )


def quota_reset_at(api_key: str = "") -> float:
    """When the character quota next resets, as a unix timestamp."""
    return subscription(api_key).reset_at


def first_voice_id(api_key: str = "") -> str:
    """Pick a voice when none is configured, rather than guessing at an id."""
    voices = list_voices(api_key)
    if not voices:
        raise ElevenLabsError(
            "The ElevenLabs account has no voices. Add one in the Voice Library first."
        )
    return voices[0][0]


def resolve_voice(wanted: str, api_key: str = "") -> str:
    """Turn whatever the user wrote into a voice id.

    People reach for the name they read in the listing -- "Roger" -- not the
    opaque id beside it. Both work, and so does a unique prefix.
    """
    wanted = (wanted or "").strip()
    voices = list_voices(api_key)
    if not voices:
        raise ElevenLabsError(
            "The ElevenLabs account has no voices. Add one in the Voice Library first."
        )
    if not wanted:
        return voices[0][0]

    for voice_id, _name, _labels in voices:
        if voice_id == wanted:
            return voice_id

    folded = wanted.casefold()
    for voice_id, name, _labels in voices:
        if name.casefold() == folded:
            return voice_id

    partial = [(vid, name) for vid, name, _ in voices if name.casefold().startswith(folded)]
    if len(partial) == 1:
        return partial[0][0]
    if len(partial) > 1:
        names = ", ".join(name for _vid, name in partial[:6])
        raise ElevenLabsError(f"{wanted!r} matches several voices: {names}. Be more specific.")

    available = ", ".join(name for _vid, name, _ in voices[:8])
    raise ElevenLabsError(
        f"No ElevenLabs voice called {wanted!r}. Available: {available}… "
        "Run `jarvis voices` for the full list."
    )


def _resolve(wanted: str, api_key: str) -> str:
    """Module-level shim so the speaker can resolve lazily."""
    return resolve_voice(wanted, api_key)


class ElevenLabsSpeaker(Speaker):
    """Streams synthesis from ElevenLabs and plays it as it arrives."""

    name = "elevenlabs"

    def __init__(
        self,
        api_key: str = "",
        voice: str = "",
        model: str = "",
        rate: float = 1.0,
        resolve_voice: bool = True,
    ):
        super().__init__()
        self.api_key = _api_key(api_key)
        self.model = model or DEFAULT_MODEL
        self.rate = rate
        # What the user wrote -- a name, an id, or nothing -- turned into an id
        # on first use. Resolving here would cost a request at start-up.
        self.wanted = (voice or "").strip()
        self.voice = _resolve(self.wanted, self.api_key) if resolve_voice else ""
        # Set once the account turns out not to allow raw PCM.
        self._use_mp3 = False
        self._lock = threading.Lock()

    @property
    def voice_name(self) -> str:
        return self.voice or "(resolved on first use)"

    def check(self) -> str:
        """Verify the key and voice against the service. Returns the voice name."""
        self.voice = _resolve(self.wanted or self.voice, self.api_key)
        for voice_id, name, _labels in list_voices(self.api_key):
            if voice_id == self.voice:
                return name
        return self.voice

    def _settings(self) -> dict:
        settings: dict[str, float | bool] = {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "use_speaker_boost": True,
        }
        if abs(self.rate - 1.0) > 0.01:
            settings["speed"] = round(max(0.7, min(self.rate, 1.2)), 2)
        return settings

    def _fetch(self, text: str, output_format: str):
        payload = {"text": text, "model_id": self.model, "voice_settings": self._settings()}
        path = f"/text-to-speech/{self.voice}/stream?output_format={output_format}"
        return _request(path, self.api_key, method="POST", payload=payload)

    def _chunks(self, response) -> Iterator[bytes]:
        while True:
            if self._cancel.is_set():
                return
            piece = response.read(CHUNK)
            if not piece:
                return
            yield piece

    def _speak(self, text: str) -> None:
        if not self.voice:
            self.voice = _resolve(self.wanted, self.api_key)

        with self._lock:
            use_mp3 = self._use_mp3

        if not use_mp3:
            try:
                with self._fetch(text, PCM_FORMAT) as response:
                    _play_pcm_stream(self._chunks(response), PCM_RATE, self._cancel)
                return
            except ElevenLabsError as exc:
                # Raw PCM is not on every plan. Fall back once, then remember.
                if "rejected the request" not in str(exc) and "422" not in str(exc):
                    raise
                with self._lock:
                    self._use_mp3 = True

        self._speak_mp3(text)

    def _speak_mp3(self, text: str) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
            path = handle.name
        try:
            with self._fetch(text, MP3_FORMAT) as response:
                while True:
                    piece = response.read(CHUNK)
                    if not piece:
                        break
                    with open(path, "ab") as sink:
                        sink.write(piece)
            if self._cancel.is_set():
                return
            if not _play_file(path, self._cancel):
                raise ElevenLabsError(
                    "No audio player for MP3 was found. Install ffmpeg (ffplay) or mpv."
                )
        finally:
            if os.path.exists(path):
                os.unlink(path)


def _play_pcm_stream(chunks: Iterator[bytes], sample_rate: int, cancel: threading.Event) -> None:
    """Play PCM as it arrives, so speech starts before synthesis finishes."""
    try:
        import numpy
        import sounddevice
    except ImportError:
        # No sound card binding: collect and hand the whole thing to the
        # blocking player instead.
        _play_pcm(b"".join(chunks), sample_rate, 1, cancel)
        return

    stream = sounddevice.OutputStream(samplerate=sample_rate, channels=1, dtype="int16")
    stream.start()
    remainder = b""
    try:
        for chunk in chunks:
            if cancel.is_set():
                stream.abort()
                return
            data = remainder + chunk
            # int16 frames are two bytes; keep any odd trailing byte back.
            usable = len(data) - (len(data) % 2)
            remainder = data[usable:]
            if usable:
                stream.write(numpy.frombuffer(data[:usable], dtype=numpy.int16))
    finally:
        try:
            stream.stop()
            stream.close()
        except Exception:  # pragma: no cover
            pass

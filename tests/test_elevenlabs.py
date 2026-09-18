"""The ElevenLabs client.

What is testable here is the logic around the network: how failures are
worded, how the streamed bytes are reassembled, how a plan without raw PCM is
handled. Whether the request shape matches the live API is not -- that is what
`jarvis voices` and the first `jarvis say` verify.
"""

from __future__ import annotations

import io
import json
import threading
import urllib.error

import pytest

from jarvis.voice.elevenlabs import (
    ElevenLabsError,
    ElevenLabsSpeaker,
    _http_failure,
    _play_pcm_stream,
    list_voices,
)


def http_error(code: int, payload: dict | None = None) -> urllib.error.HTTPError:
    body = json.dumps(payload or {}).encode("utf-8")
    return urllib.error.HTTPError(
        "https://api.elevenlabs.io/v1/x", code, "err", {}, io.BytesIO(body)
    )


# -- failure wording ---------------------------------------------------------


def test_rejected_key_is_named_as_such():
    problem = _http_failure(http_error(401))
    assert "key was rejected" in str(problem)
    assert "ELEVENLABS_API_KEY" in str(problem)


def test_unknown_voice_points_at_the_voices_command():
    problem = _http_failure(http_error(404))
    assert "does not exist" in str(problem)
    assert "jarvis voices" in str(problem)


def test_exhausted_quota_is_distinguished_from_a_broken_key():
    assert "quota" in str(_http_failure(http_error(429)))


def test_the_services_own_complaint_survives():
    problem = _http_failure(http_error(422, {"detail": {"message": "voice_settings.speed invalid"}}))
    assert "voice_settings.speed invalid" in str(problem)


def test_a_missing_key_is_caught_before_any_request(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(ElevenLabsError) as caught:
        ElevenLabsSpeaker()
    assert "elevenlabs.io" in str(caught.value)


# -- streamed audio ----------------------------------------------------------


class FakeStream:
    """Stands in for a sounddevice OutputStream."""

    def __init__(self, *_args, **_kwargs):
        self.written: list[bytes] = []
        self.aborted = False

    def start(self):
        pass

    def write(self, samples):
        self.written.append(samples.tobytes())

    def abort(self):
        self.aborted = True

    def stop(self):
        pass

    def close(self):
        pass


@pytest.fixture
def fake_sounddevice(monkeypatch):
    import sys
    import types

    created: list[FakeStream] = []

    def OutputStream(*args, **kwargs):
        stream = FakeStream()
        created.append(stream)
        return stream

    module = types.ModuleType("sounddevice")
    module.OutputStream = OutputStream
    monkeypatch.setitem(sys.modules, "sounddevice", module)
    return created


def test_odd_byte_counts_do_not_corrupt_the_audio(fake_sounddevice):
    """PCM frames are two bytes; a chunk boundary can fall between them.

    Splitting mid-frame and writing the halves separately would turn the tail
    of one sample and the head of the next into a click.
    """
    pytest.importorskip("numpy")
    # Deliberately odd-sized chunks, so boundaries land mid-sample.
    chunks = [b"\x01\x02\x03", b"\x04\x05", b"\x06\x07\x08"]
    _play_pcm_stream(iter(chunks), 24000, threading.Event())

    stream = fake_sounddevice[0]
    # Every byte arrives, in order, and only in whole frames.
    assert b"".join(stream.written) == b"\x01\x02\x03\x04\x05\x06\x07\x08"[:8]
    assert all(len(written) % 2 == 0 for written in stream.written)


def test_cancellation_stops_playback_immediately(fake_sounddevice):
    pytest.importorskip("numpy")
    cancel = threading.Event()

    def chunks():
        yield b"\x01\x02" * 100
        cancel.set()
        yield b"\x03\x04" * 100  # must never be played

    _play_pcm_stream(chunks(), 24000, cancel)

    stream = fake_sounddevice[0]
    assert stream.aborted is True
    assert len(b"".join(stream.written)) == 200


# -- voice listing -----------------------------------------------------------


def test_voices_are_listed_with_their_ids(monkeypatch):
    payload = {
        "voices": [
            {"voice_id": "abc123", "name": "Antoni", "labels": {"accent": "british", "age": "middle"}},
            {"voice_id": "def456", "name": "Rachel", "labels": {}},
        ]
    }

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        "jarvis.voice.elevenlabs.urllib.request.urlopen",
        lambda *a, **k: FakeResponse(json.dumps(payload).encode()),
    )
    voices = list_voices("a-key")
    assert voices[0] == ("abc123", "Antoni", "british, middle")
    assert voices[1] == ("def456", "Rachel", "")


def test_low_latency_model_is_the_default(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "a-key")
    speaker = ElevenLabsSpeaker(resolve_voice=False)
    # Perceived responsiveness beats per-sentence polish in a conversation.
    assert speaker.model == "eleven_flash_v2_5"


def test_speech_rate_is_clamped_to_what_the_api_accepts(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "a-key")
    fast = ElevenLabsSpeaker(rate=3.0, resolve_voice=False)
    slow = ElevenLabsSpeaker(rate=0.1, resolve_voice=False)
    assert fast._settings()["speed"] == 1.2
    assert slow._settings()["speed"] == 0.7

    normal = ElevenLabsSpeaker(rate=1.0, resolve_voice=False)
    # An unchanged rate is left out entirely rather than sent as 1.0.
    assert "speed" not in normal._settings()

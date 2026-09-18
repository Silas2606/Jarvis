"""Falling back to a free voice, and returning to the paid one.

The awkward parts are all about time: not retrying a dead quota on every
sentence, not waiting forever once it resets, and not forgetting across a
restart which of the two is true.
"""

from __future__ import annotations

import json
import time

import pytest

from jarvis.voice import AudioUnavailable
from jarvis.voice.fallback import DEFAULT_BENCH_SECONDS, BenchState, FallbackSpeaker
from jarvis.voice.tts import Speaker


class ScriptedSpeaker(Speaker):
    """Says everything, or raises whatever it was told to raise."""

    def __init__(self, name: str, failure: Exception | None = None):
        super().__init__()
        self._name = name
        self.failure = failure
        self.spoken: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def voice_name(self) -> str:
        return f"{self._name}-voice"

    def _speak(self, text: str) -> None:
        if self.failure is not None:
            raise self.failure
        self.spoken.append(text)


@pytest.fixture
def pair():
    return ScriptedSpeaker("elevenlabs"), ScriptedSpeaker("edge")


def quota_error():
    return AudioUnavailable("The ElevenLabs quota or rate limit is used up.")


# -- normal operation --------------------------------------------------------


def test_the_paid_voice_is_used_while_it_works(pair, tmp_path):
    primary, backup = pair
    speaker = FallbackSpeaker(primary, backup, tmp_path / "state.json")

    speaker.say("Guten Abend, Sir.")

    assert primary.spoken == ["Guten Abend, Sir."]
    assert backup.spoken == []
    assert speaker.describe() == "elevenlabs (elevenlabs-voice)"


# -- running out -------------------------------------------------------------


def test_an_exhausted_quota_hands_the_same_sentence_to_the_backup(pair, tmp_path):
    """The sentence that triggered the failure must still be spoken."""
    primary, backup = pair
    primary.failure = quota_error()
    said: list[str] = []
    speaker = FallbackSpeaker(primary, backup, tmp_path / "state.json", notify=said.append)

    speaker.say("Drei Termine, Sir.")

    assert backup.spoken == ["Drei Termine, Sir."]
    assert speaker.bench.reason == "quota exhausted"
    assert said and "switching to edge" in said[0]


def test_later_sentences_skip_the_dead_primary_entirely(pair, tmp_path):
    """Retrying every sentence would cost a failed request and a pause each time."""
    primary, backup = pair
    primary.failure = quota_error()
    speaker = FallbackSpeaker(primary, backup, tmp_path / "state.json")

    speaker.say("Erster Satz.")
    attempts_after_first = 1

    primary.failure = None  # would succeed -- but must not even be tried
    speaker.say("Zweiter Satz.")
    speaker.say("Dritter Satz.")

    assert primary.spoken == []  # never called again while benched
    assert backup.spoken == ["Erster Satz.", "Zweiter Satz.", "Dritter Satz."]
    assert attempts_after_first == 1


def test_a_failure_the_backup_cannot_help_with_is_not_swallowed(pair, tmp_path):
    """No audio player is a local problem; switching voices would not fix it."""
    primary, backup = pair
    primary.failure = AudioUnavailable("No audio player for MP3 was found.")
    speaker = FallbackSpeaker(primary, backup, tmp_path / "state.json")

    with pytest.raises(AudioUnavailable):
        speaker.say("Das geht schief.")
    assert backup.spoken == []
    assert speaker.bench.until == 0.0


# -- coming back -------------------------------------------------------------


def test_the_paid_voice_is_retried_once_the_bench_expires(pair, tmp_path):
    primary, backup = pair
    primary.failure = quota_error()
    said: list[str] = []
    speaker = FallbackSpeaker(primary, backup, tmp_path / "state.json", notify=said.append)

    speaker.say("Vor dem Reset.")
    assert backup.spoken == ["Vor dem Reset."]

    # The quota resets: the bench runs out and the voice starts working.
    speaker._bench = BenchState(time.time() - 1, "quota exhausted")
    primary.failure = None

    speaker.say("Nach dem Reset.")

    assert primary.spoken == ["Nach dem Reset."]
    assert speaker.bench.until == 0.0  # bench lifted
    assert any("available again" in message for message in said)


def test_a_failed_retry_benches_the_voice_again(pair, tmp_path):
    primary, backup = pair
    primary.failure = quota_error()
    speaker = FallbackSpeaker(primary, backup, tmp_path / "state.json")
    speaker.say("Erster.")

    speaker._bench = BenchState(time.time() - 1, "quota exhausted")
    speaker.say("Zweiter.")  # retried, fails again

    assert backup.spoken == ["Erster.", "Zweiter."]
    assert speaker.bench.active is True


def test_the_services_own_reset_time_is_preferred_over_a_guess(pair, tmp_path):
    """The account knows when its quota returns; a fixed interval is the fallback."""
    primary, backup = pair
    primary.failure = quota_error()
    reset = time.time() + 12 * 24 * 3600  # twelve days out

    speaker = FallbackSpeaker(primary, backup, tmp_path / "state.json", reset_lookup=lambda: reset)
    speaker.say("Leer.")

    assert speaker.bench.until == pytest.approx(reset, abs=2)


@pytest.mark.parametrize("reported", [0.0, time.time() - 5000, time.time() + 400 * 24 * 3600])
def test_an_unusable_reset_time_falls_back_to_a_fixed_interval(pair, tmp_path, reported):
    """Missing, already past, or implausibly distant -- none of those are usable."""
    primary, backup = pair
    primary.failure = quota_error()

    speaker = FallbackSpeaker(primary, backup, tmp_path / "state.json", reset_lookup=lambda: reported)
    speaker.say("Leer.")

    assert speaker.bench.until == pytest.approx(time.time() + DEFAULT_BENCH_SECONDS, abs=5)


def test_a_rejected_key_is_retried_sooner_than_a_dead_quota(pair, tmp_path):
    """A wrong key can be fixed any minute; a quota cannot."""
    primary, backup = pair
    primary.failure = AudioUnavailable("The ElevenLabs API key was rejected.")
    speaker = FallbackSpeaker(primary, backup, tmp_path / "state.json")

    speaker.say("Falscher Schlüssel.")

    assert speaker.bench.active is True
    assert speaker.bench.remaining < DEFAULT_BENCH_SECONDS


# -- surviving a restart -----------------------------------------------------


def test_the_bench_outlives_the_process(pair, tmp_path):
    """Otherwise every launch spends one doomed request rediscovering this."""
    state = tmp_path / "state.json"
    primary, backup = pair
    primary.failure = quota_error()

    FallbackSpeaker(primary, backup, state).say("Leer.")
    assert json.loads(state.read_text())["reason"] == "quota exhausted"

    # A fresh process, a fresh speaker, the same knowledge.
    fresh_primary, fresh_backup = ScriptedSpeaker("elevenlabs"), ScriptedSpeaker("edge")
    revived = FallbackSpeaker(fresh_primary, fresh_backup, state)
    revived.say("Nach Neustart.")

    assert fresh_primary.spoken == []
    assert fresh_backup.spoken == ["Nach Neustart."]


def test_a_corrupt_state_file_is_ignored_not_fatal(pair, tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{not json at all")
    primary, backup = pair

    speaker = FallbackSpeaker(primary, backup, state)
    speaker.say("Trotzdem sprechen.")

    assert primary.spoken == ["Trotzdem sprechen."]


def test_an_unwritable_state_path_does_not_stop_speech(pair, tmp_path):
    primary, backup = pair
    primary.failure = quota_error()
    # A directory where a file should be: writing will fail.
    blocked = tmp_path / "state.json"
    blocked.mkdir()

    speaker = FallbackSpeaker(primary, backup, blocked)
    speaker.say("Muss trotzdem rauskommen.")

    assert backup.spoken == ["Muss trotzdem rauskommen."]


# -- display -----------------------------------------------------------------


def test_the_console_shows_which_voice_is_active_and_when_it_returns(pair, tmp_path):
    primary, backup = pair
    primary.failure = quota_error()
    speaker = FallbackSpeaker(primary, backup, tmp_path / "state.json")
    speaker.say("Leer.")

    described = speaker.describe()
    assert "edge" in described
    assert "elevenlabs paused" in described
    assert "retry in" in described
    assert speaker.name == "edge"


# -- engine-specific voice ids -----------------------------------------------


def test_the_understudy_does_not_inherit_the_primarys_voice_id(monkeypatch, tmp_path):
    """Regression: edge was handed an ElevenLabs voice id.

    `tts_voice` is engine-specific. Passing the configured ElevenLabs id to
    whichever free engine stepped in produced "edge-tts failed: Invalid voice
    'CwhRBWXzGAHq8TQ4Fs17'" -- a message naming the wrong engine and the wrong
    problem.
    """
    from jarvis.config import Config
    from jarvis.voice.tts import build_speaker

    built: list[tuple[str, str]] = []

    class Recorder(Speaker):
        def __init__(self, engine: str, voice: str):
            super().__init__()
            self._engine = engine
            self._voice = voice
            built.append((engine, voice))

        @property
        def name(self) -> str:
            return self._engine

        @property
        def voice_name(self) -> str:
            return self._voice

        def _speak(self, text: str) -> None:
            pass

    monkeypatch.setenv("ELEVENLABS_API_KEY", "a-key")
    monkeypatch.setattr(
        "jarvis.voice.elevenlabs.ElevenLabsSpeaker",
        lambda **kw: Recorder("elevenlabs", kw.get("voice", "")),
    )
    monkeypatch.setattr(
        "jarvis.voice.tts.EdgeSpeaker",
        lambda voice, language, rate: Recorder("edge", voice),
    )

    config = Config(home=tmp_path)
    config.voice.tts_voice = "CwhRBWXzGAHq8TQ4Fs17"  # an ElevenLabs id
    speaker = build_speaker(config)

    assert ("elevenlabs", "CwhRBWXzGAHq8TQ4Fs17") in built
    # The understudy gets no voice id at all, so it uses its own default.
    assert ("edge", "") in built
    assert isinstance(speaker, FallbackSpeaker)

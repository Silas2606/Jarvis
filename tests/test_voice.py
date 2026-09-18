"""The voice layer: chunking, wake words, confirmations, interruption."""

from __future__ import annotations

import time

from jarvis.speech_chunks import SentenceAccumulator, split_sentences
from jarvis.voice.audio import VoiceActivityDetector, pcm_to_wav
from jarvis.voice.loop import interpret_yes_no
from jarvis.voice.speech_queue import SpeechQueue
from jarvis.voice.stt import Transcriber
from jarvis.voice.tts import Speaker
from jarvis.voice.wakeword import strip_wake_word

WAKE_WORDS = ("jarvis", "hey jarvis")


# -- chunking ----------------------------------------------------------------


def test_sentences_are_emitted_as_they_complete():
    """The voice must start on sentence one while sentence two is still coming."""
    accumulator = SentenceAccumulator()
    tokens = ["Guten ", "Morgen, ", "Sir. ", "Drei Termine ", "stehen an heute."]
    spoken: list[str] = []

    for index, token in enumerate(tokens):
        spoken.extend(accumulator.feed(token))
        if index == 2:
            # Three tokens in, the greeting is already on its way to the speaker
            # even though the rest of the answer has not been generated yet.
            assert spoken == ["Guten Morgen, Sir."]

    spoken.append(accumulator.flush())
    assert spoken == ["Guten Morgen, Sir.", "Drei Termine stehen an heute.", ""]


def test_abbreviations_and_numbers_do_not_split_sentences():
    assert split_sentences("Es ist 17.30 Uhr und Sie haben z. B. ein Meeting.") == [
        "Es ist 17.30 Uhr und Sie haben z. B. ein Meeting."
    ]


def test_a_very_long_clause_is_cut_so_the_voice_keeps_moving():
    text = "und dann " * 60
    chunks = split_sentences(text)
    assert len(chunks) > 1
    assert all(len(chunk) <= 320 for chunk in chunks)


# -- wake words --------------------------------------------------------------


def test_leading_wake_word_carries_the_command():
    assert strip_wake_word("Jarvis, wie spät ist es?", WAKE_WORDS) == (True, "wie spät ist es?")
    assert strip_wake_word("ok jarvis mach das Licht an", WAKE_WORDS) == (True, "mach das Licht an")


def test_talking_about_jarvis_does_not_wake_him():
    assert strip_wake_word("Ich habe Jarvis gestern gesehen", WAKE_WORDS) == (False, "")
    assert strip_wake_word("Frag Jarvis danach", WAKE_WORDS) == (False, "")


def test_name_alone_wakes_him_with_no_command():
    assert strip_wake_word("Jarvis", WAKE_WORDS) == (True, "")


# -- confirmations -----------------------------------------------------------


def test_yes_and_no_are_understood():
    assert interpret_yes_no("Ja, bitte") is True
    assert interpret_yes_no("Nein, lass mal") is False
    assert interpret_yes_no("yes please") is True


def test_uncertainty_is_not_taken_for_a_no():
    # These must lead to another question, not to a silent refusal.
    assert interpret_yes_no("Ich weiß nicht") is None
    assert interpret_yes_no("Vielleicht") is None
    assert interpret_yes_no("Moment mal") is None
    assert interpret_yes_no("Was hast du gesagt?") is None


# -- speaking ----------------------------------------------------------------


class RecordingSpeaker(Speaker):
    name = "recording"

    def __init__(self):
        super().__init__()
        self.spoken: list[str] = []
        self.interrupted: list[str] = []

    def _speak(self, text: str) -> None:
        for _ in range(20):
            if self._cancel.is_set():
                self.interrupted.append(text)
                return
            time.sleep(0.005)
        self.spoken.append(text)


def test_sentences_are_spoken_in_order():
    speaker = RecordingSpeaker()
    queue = SpeechQueue(speaker)
    queue.say("Erster Satz.")
    queue.say("Zweiter Satz.")
    assert queue.wait_until_done(timeout=5)
    assert speaker.spoken == ["Erster Satz.", "Zweiter Satz."]
    queue.shutdown()


def test_interruption_stops_mid_sentence_and_drops_the_queue():
    speaker = RecordingSpeaker()
    queue = SpeechQueue(speaker)
    queue.say("Ein langer Satz der unterbrochen wird.")
    queue.say("Dieser hier kommt nie dran.")
    time.sleep(0.02)
    queue.interrupt()
    time.sleep(0.15)

    assert speaker.interrupted == ["Ein langer Satz der unterbrochen wird."]
    assert "Dieser hier kommt nie dran." not in speaker.spoken
    queue.shutdown()


# -- audio -------------------------------------------------------------------


def test_energy_vad_tells_speech_from_a_quiet_room():
    import math
    import struct

    detector = VoiceActivityDetector()
    quiet = struct.pack("<480h", *([40] * 480))
    loud = struct.pack("<480h", *[int(9000 * math.sin(i / 6)) for i in range(480)])

    for _ in range(30):  # let the noise floor settle
        detector.is_speech(quiet)
    assert detector.is_speech(quiet) is False
    assert detector.is_speech(loud) is True


def test_pcm_is_wrapped_in_a_valid_wav_container():
    import io
    import wave

    audio = b"\x00\x01" * 1600
    data = pcm_to_wav(audio, 16000)
    with wave.open(io.BytesIO(data), "rb") as handle:
        assert handle.getframerate() == 16000
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2


# -- transcription -----------------------------------------------------------

def test_whisper_hallucinations_are_dropped():
    # Whisper invents these when it hears nothing; acting on them would be worse
    # than staying silent.
    assert Transcriber.clean("Vielen Dank.") == ""
    assert Transcriber.clean("Thanks for watching!") == ""
    assert Transcriber.clean("  Wie ist das Wetter?  ") == "Wie ist das Wetter?"

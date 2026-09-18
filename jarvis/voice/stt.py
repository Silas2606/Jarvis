"""Speech to text.

Two backends, both local so that nothing spoken in the room leaves the
machine: ``faster-whisper`` (a Python package) and a ``whisper.cpp`` binary.
The engine is picked automatically unless the config names one.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from jarvis.voice import AudioUnavailable
from jarvis.voice.audio import pcm_to_wav

# Whisper happily "transcribes" silence into stock phrases. These are the ones
# it hallucinates most, and they are dropped rather than acted on.
_HALLUCINATIONS = {
    "thank you.", "thanks for watching!", "you", "bye.", "so", ".",
    "untertitel im auftrag des zdf, 2017", "untertitel von stephanie geiges",
    "vielen dank.", "danke.", "amara.org", "untertitelung aufgrund der audiodeskription",
    "copyright wdr", "das war's.", "tschüss.",
}


class Transcriber:
    """Interface: audio in, text out."""

    name = "none"

    def transcribe(self, audio: bytes, sample_rate: int) -> str:  # pragma: no cover
        raise NotImplementedError

    @staticmethod
    def clean(text: str) -> str:
        """Drop the phrases Whisper invents when it hears nothing."""
        stripped = text.strip()
        if stripped.lower().strip(" .!?") in {h.strip(" .!?") for h in _HALLUCINATIONS}:
            return ""
        return stripped


class FasterWhisper(Transcriber):
    """The faster-whisper package, running a CTranslate2 Whisper locally."""

    name = "faster-whisper"

    def __init__(self, model: str = "small", language: str = "de", device: str = "auto"):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise AudioUnavailable(
                "faster-whisper is not installed. Install it with: "
                'pip install "jarvis-assistant[voice]"'
            ) from exc

        compute = "float16" if device == "cuda" else "int8"
        try:
            self._model = WhisperModel(model, device=device, compute_type=compute)
        except Exception:
            # A GPU build without a GPU, or a bad compute type -- fall back.
            self._model = WhisperModel(model, device="cpu", compute_type="int8")
        self.language = language

    def transcribe(self, audio: bytes, sample_rate: int) -> str:
        import numpy

        samples = numpy.frombuffer(audio, dtype=numpy.int16).astype(numpy.float32) / 32768.0
        segments, _info = self._model.transcribe(
            samples,
            language=self.language or None,
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return self.clean(" ".join(segment.text.strip() for segment in segments))


class WhisperCpp(Transcriber):
    """A whisper.cpp binary, for setups that already have one."""

    name = "whisper-cpp"

    def __init__(self, binary: str = "whisper-cli", model_path: str = "", language: str = "de"):
        self.binary = binary
        self.model_path = model_path
        self.language = language
        if not _which(binary):
            raise AudioUnavailable(f"The whisper.cpp binary {binary!r} was not found on PATH.")
        if model_path and not Path(model_path).exists():
            raise AudioUnavailable(f"The whisper.cpp model {model_path!r} does not exist.")

    def transcribe(self, audio: bytes, sample_rate: int) -> str:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            handle.write(pcm_to_wav(audio, sample_rate))
            path = handle.name
        try:
            command = [self.binary, "-f", path, "-nt", "-np"]
            if self.model_path:
                command += ["-m", self.model_path]
            if self.language:
                command += ["-l", self.language]
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                raise AudioUnavailable(f"whisper.cpp failed: {result.stderr.strip()[:200]}")
            return self.clean(result.stdout)
        finally:
            os.unlink(path)


class NullTranscriber(Transcriber):
    """Used when no engine is available, so the rest of the app still runs."""

    name = "none"

    def transcribe(self, audio: bytes, sample_rate: int) -> str:
        raise AudioUnavailable(
            "No speech recognition is available. Install it with: "
            'pip install "jarvis-assistant[voice]"'
        )


def _which(binary: str) -> str | None:
    from shutil import which

    return which(binary)


def available_engines() -> list[str]:
    """Which STT engines this machine could use right now."""
    found = []
    try:
        import faster_whisper  # noqa: F401

        found.append("faster-whisper")
    except ImportError:
        pass
    if _which("whisper-cli") or _which("whisper.cpp") or _which("main"):
        found.append("whisper-cpp")
    return found


def build_transcriber(config, strict: bool = False) -> Transcriber:
    """Pick a transcription engine according to the config.

    Args:
        config: The Jarvis config.
        strict: Raise if the requested engine is unavailable, instead of
            falling back to a null engine.
    """
    voice = config.voice
    wanted = (voice.stt_engine or "auto").lower()
    device = voice.stt_device
    if device == "auto":
        device = "cpu"

    def faster():
        return FasterWhisper(voice.stt_model, voice.language, device)

    def cpp():
        return WhisperCpp(voice.whisper_cpp_binary, voice.whisper_cpp_model, voice.language)

    order = {"auto": [faster, cpp], "faster-whisper": [faster], "whisper-cpp": [cpp]}.get(wanted, [faster, cpp])

    last: Exception | None = None
    for builder in order:
        try:
            return builder()
        except Exception as exc:
            last = exc
    if strict and last is not None:
        raise last if isinstance(last, AudioUnavailable) else AudioUnavailable(str(last))
    return NullTranscriber()

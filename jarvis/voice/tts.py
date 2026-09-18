"""Speech synthesis -- Jarvis' actual voice.

Several engines, picked in order of quality unless the config names one:

    piper   local neural voices, fast, no network
    edge    Microsoft's neural voices, excellent German, needs the internet
    say     macOS built-in
    espeak  espeak-ng, robotic but present on most Linux boxes

Every engine must be interruptible: when the user talks over Jarvis, speech
stops within a frame or two. Playback therefore runs in chunks with a cancel
flag checked between them, and subprocess engines are killed outright.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from jarvis.voice import AudioUnavailable

# Voices chosen to fit the part: calm, measured, a touch formal.
PIPER_VOICES = {
    "de": "de_DE-thorsten-medium",
    "en": "en_GB-alan-medium",
    "fr": "fr_FR-gilles-low",
    "es": "es_ES-davefx-medium",
    "it": "it_IT-riccardo-x_low",
}

EDGE_VOICES = {
    "de": "de-DE-ConradNeural",
    "en": "en-GB-RyanNeural",
    "fr": "fr-FR-HenriNeural",
    "es": "es-ES-AlvaroNeural",
    "it": "it-IT-DiegoNeural",
    "nl": "nl-NL-MaartenNeural",
    "pt": "pt-PT-DuarteNeural",
    "pl": "pl-PL-MarekNeural",
}

MACOS_VOICES = {"de": "Markus", "en": "Daniel", "fr": "Thomas", "es": "Jorge", "it": "Luca"}

EXTERNAL_PLAYERS = ("ffplay", "mpv", "afplay", "mpg123", "aplay", "paplay")


class Speaker:
    """Interface: text in, sound out, interruptible."""

    name = "none"

    def __init__(self) -> None:
        self._cancel = threading.Event()
        self._speaking = threading.Event()

    @property
    def is_speaking(self) -> bool:
        return self._speaking.is_set()

    def say(self, text: str) -> None:
        """Speak, blocking until finished or interrupted."""
        if not text.strip():
            return
        self._cancel.clear()
        self._speaking.set()
        try:
            self._speak(text.strip())
        finally:
            self._speaking.clear()

    def stop(self) -> None:
        """Interrupt whatever is being said."""
        self._cancel.set()

    def _speak(self, text: str) -> None:  # pragma: no cover
        raise NotImplementedError


def _play_pcm(data: bytes, sample_rate: int, channels: int, cancel: threading.Event) -> bool:
    """Play raw PCM through sounddevice, checking the cancel flag as it goes."""
    try:
        import numpy
        import sounddevice
    except ImportError:
        return False

    try:
        samples = numpy.frombuffer(data, dtype=numpy.int16)
        if channels > 1:
            samples = samples.reshape(-1, channels)
        # 100 ms blocks: small enough that an interrupt feels immediate.
        block = sample_rate // 10
        with sounddevice.OutputStream(samplerate=sample_rate, channels=channels, dtype="int16") as stream:
            for start in range(0, len(samples), block):
                if cancel.is_set():
                    stream.abort()
                    return True
                stream.write(samples[start : start + block])
        return True
    except Exception:
        return False


def _play_wav_bytes(data: bytes, cancel: threading.Event) -> bool:
    import io
    import wave

    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            frames = handle.readframes(handle.getnframes())
            return _play_pcm(frames, handle.getframerate(), handle.getnchannels(), cancel)
    except Exception:
        return False


def _external_player() -> list[str] | None:
    for binary in EXTERNAL_PLAYERS:
        if shutil.which(binary):
            if binary == "ffplay":
                return [binary, "-nodisp", "-autoexit", "-loglevel", "quiet"]
            if binary == "mpv":
                return [binary, "--really-quiet", "--no-video"]
            return [binary]
    return None


def _play_file(path: str, cancel: threading.Event) -> bool:
    """Play a file with whatever player is installed, killable on cancel."""
    player = _external_player()
    if player is None:
        return False
    process = subprocess.Popen(
        player + [path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        while process.poll() is None:
            if cancel.wait(0.05):
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    process.kill()
                return True
        return True
    finally:
        if process.poll() is None:  # pragma: no cover
            process.kill()


class PiperSpeaker(Speaker):
    """piper: local neural TTS. Writes WAV to stdout, which we play ourselves."""

    name = "piper"

    def __init__(self, binary: str = "piper", model: str = "", language: str = "de", rate: float = 1.0):
        super().__init__()
        if not shutil.which(binary):
            raise AudioUnavailable(f"The piper binary {binary!r} was not found on PATH.")
        self.binary = binary
        self.language = language
        self.rate = rate
        self.model = model or self._find_model(language)
        if not self.model:
            raise AudioUnavailable(
                "No piper voice model was found. Download one (e.g. "
                f"{PIPER_VOICES.get(language[:2], 'en_GB-alan-medium')}.onnx) and set "
                "voice.piper_model in the config."
            )

    @staticmethod
    def _find_model(language: str) -> str:
        """Look in the usual places for a voice matching the language."""
        wanted = PIPER_VOICES.get(language[:2], "")
        roots = [
            Path.home() / ".local/share/piper-voices",
            Path.home() / ".jarvis/voices",
            Path("/usr/share/piper-voices"),
            Path("/usr/local/share/piper-voices"),
        ]
        for root in roots:
            if not root.exists():
                continue
            if wanted:
                exact = list(root.rglob(f"{wanted}.onnx"))
                if exact:
                    return str(exact[0])
            for candidate in root.rglob(f"{language[:2]}_*.onnx"):
                return str(candidate)
        return ""

    def _speak(self, text: str) -> None:
        command = [self.binary, "--model", self.model, "--output_file", "-"]
        if self.rate and abs(self.rate - 1.0) > 0.01:
            # piper's length_scale is inverse to speed.
            command += ["--length_scale", f"{1.0 / self.rate:.3f}"]
        try:
            result = subprocess.run(
                command, input=text.encode("utf-8"), capture_output=True, timeout=120
            )
        except Exception as exc:
            raise AudioUnavailable(f"piper failed: {exc}") from exc
        if result.returncode != 0 or not result.stdout:
            raise AudioUnavailable(f"piper failed: {result.stderr.decode('utf-8', 'replace')[:200]}")
        if not _play_wav_bytes(result.stdout, self._cancel):
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                handle.write(result.stdout)
                path = handle.name
            try:
                _play_file(path, self._cancel)
            finally:
                os.unlink(path)


class EdgeSpeaker(Speaker):
    """Microsoft Edge neural voices. Online, but the German is excellent."""

    name = "edge"

    def __init__(self, voice: str = "", language: str = "de", rate: float = 1.0):
        super().__init__()
        try:
            import edge_tts  # noqa: F401
        except ImportError as exc:
            raise AudioUnavailable(
                'edge-tts is not installed. Install it with: pip install "jarvis-assistant[edge]"'
            ) from exc
        if _external_player() is None:
            raise AudioUnavailable(
                "edge-tts needs an audio player for MP3. Install ffmpeg (ffplay) or mpv."
            )
        self.voice = voice or EDGE_VOICES.get(language[:2], EDGE_VOICES["en"])
        self.rate = rate

    def _speak(self, text: str) -> None:
        import asyncio

        import edge_tts

        percent = int(round((self.rate - 1.0) * 100))
        rate = f"{percent:+d}%"

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
            path = handle.name
        try:
            async def synthesise() -> None:
                communicate = edge_tts.Communicate(text, self.voice, rate=rate)
                await communicate.save(path)

            asyncio.run(synthesise())
            if self._cancel.is_set():
                return
            _play_file(path, self._cancel)
        except Exception as exc:
            raise AudioUnavailable(f"edge-tts failed: {exc}") from exc
        finally:
            if os.path.exists(path):
                os.unlink(path)


class CommandSpeaker(Speaker):
    """A speaking subprocess -- macOS `say` or `espeak-ng`."""

    def __init__(self, name: str, command: list[str]):
        super().__init__()
        self.name = name
        self.command = command

    def _speak(self, text: str) -> None:
        process = subprocess.Popen(
            self.command + [text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        while process.poll() is None:
            if self._cancel.wait(0.05):
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    process.kill()
                return


class NullSpeaker(Speaker):
    """No voice available; the console still shows what would have been said."""

    name = "none"

    def _speak(self, text: str) -> None:
        return


def _macos_speaker(language: str, rate: float) -> CommandSpeaker:
    if not shutil.which("say"):
        raise AudioUnavailable("The `say` command is only on macOS.")
    command = ["say"]
    voice = MACOS_VOICES.get(language[:2])
    if voice:
        command += ["-v", voice]
    if rate and abs(rate - 1.0) > 0.01:
        command += ["-r", str(int(180 * rate))]
    return CommandSpeaker("say", command)


def _espeak_speaker(language: str, rate: float) -> CommandSpeaker:
    binary = shutil.which("espeak-ng") or shutil.which("espeak")
    if not binary:
        raise AudioUnavailable("espeak-ng was not found on PATH.")
    voice = "en-gb" if language.startswith("en") else language[:2]
    return CommandSpeaker("espeak", [binary, "-v", voice, "-s", str(int(165 * rate))])


def available_engines() -> list[str]:
    """Which voices this machine could use right now."""
    found = []
    if shutil.which("piper"):
        found.append("piper")
    try:
        import edge_tts  # noqa: F401

        if _external_player():
            found.append("edge")
    except ImportError:
        pass
    if shutil.which("say"):
        found.append("say")
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        found.append("espeak")
    return found


def build_speaker(config, strict: bool = False) -> Speaker:
    """Pick a voice according to the config."""
    voice = config.voice
    wanted = (voice.tts_engine or "auto").lower()
    language = voice.language
    rate = voice.speech_rate

    builders = {
        "piper": lambda: PiperSpeaker(voice.piper_binary, voice.piper_model, language, rate),
        "edge": lambda: EdgeSpeaker(voice.tts_voice, language, rate),
        "say": lambda: _macos_speaker(language, rate),
        "espeak": lambda: _espeak_speaker(language, rate),
        "none": NullSpeaker,
    }

    order = ["piper", "edge", "say", "espeak"] if wanted == "auto" else [wanted]

    last: Exception | None = None
    for key in order:
        builder = builders.get(key)
        if builder is None:
            last = AudioUnavailable(f"Unknown speech engine: {key!r}")
            continue
        try:
            return builder()
        except Exception as exc:
            last = exc
    if strict and last is not None:
        raise last if isinstance(last, AudioUnavailable) else AudioUnavailable(str(last))
    return NullSpeaker()

"""Configuration for Jarvis.

Settings come from three places, later ones winning: built-in defaults, the
TOML file at ``~/.jarvis/config.toml``, and environment variables. Nothing is
required to get started -- an ``ANTHROPIC_API_KEY`` in the environment is
enough for a first conversation.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

# Claude Opus 5 is the default brain. Voice is a latency-sensitive route, so
# the default effort is "low": thinking stays on (adaptive), but Jarvis answers
# in the tempo of a conversation rather than of an essay. Raise it per config
# when you want him to reason harder about something.
DEFAULT_MODEL = "claude-opus-5"

_ENV_PREFIX = "JARVIS_"


def default_home() -> Path:
    """Directory holding the database, credentials and logs."""
    return Path(os.environ.get(f"{_ENV_PREFIX}HOME", Path.home() / ".jarvis"))


@dataclass
class BrainConfig:
    """How Jarvis thinks."""

    model: str = DEFAULT_MODEL
    max_tokens: int = 4096
    # "low" | "medium" | "high" | "xhigh" | "max"
    effort: str = "low"
    # Show a summary of Claude's reasoning in the console HUD.
    show_thinking: bool = False
    # Server-side refusal fallbacks: if a safety classifier declines a request,
    # the API routes it to a suitable fallback model instead of failing.
    server_fallbacks: bool = True
    # Turns of conversation kept verbatim before older ones are summarised away.
    history_turns: int = 24
    # Let Claude search and read the web through Anthropic's server-side tools.
    web_access: bool = True
    request_timeout: float = 120.0


@dataclass
class VoiceConfig:
    """How Jarvis listens and speaks."""

    enabled: bool = True
    # Spoken language, as a two-letter code. Drives both transcription and the
    # voice picked for speech.
    language: str = "de"
    wake_words: tuple[str, ...] = ("jarvis", "hey jarvis")
    # openwakeword model name; used when the package is installed.
    wake_model: str = "hey_jarvis"
    # Seconds of silence that end an utterance.
    silence_timeout: float = 1.1
    # Hard cap on a single utterance, so a noisy room cannot record forever.
    max_utterance_seconds: float = 30.0
    # After answering, keep listening this long without requiring the wake word.
    followup_window: float = 12.0
    # Stop speaking when the user starts talking over Jarvis.
    barge_in: bool = True
    sample_rate: int = 16000
    input_device: int | None = None
    output_device: int | None = None

    # Speech to text: "auto" | "faster-whisper" | "whisper-cpp"
    stt_engine: str = "auto"
    stt_model: str = "small"
    # "auto" | "cpu" | "cuda"
    stt_device: str = "auto"
    whisper_cpp_binary: str = "whisper-cli"
    whisper_cpp_model: str = ""

    # Text to speech: "auto" | "piper" | "edge" | "say" | "espeak" | "none"
    tts_engine: str = "auto"
    # Engine-specific voice id. Empty means "pick a sensible one for language".
    tts_voice: str = ""
    piper_binary: str = "piper"
    piper_model: str = ""
    speech_rate: float = 1.0


@dataclass
class ToolsConfig:
    """What Jarvis is allowed to do."""

    memory: bool = True
    reminders: bool = True
    calendar: bool = True
    mail: bool = True
    # Sending mail and deleting calendar entries reach the outside world, so
    # they ask for spoken confirmation before they run.
    confirm_outbound: bool = True
    google_credentials: str = ""  # defaults to <home>/google_client_secret.json
    google_token: str = ""  # defaults to <home>/google_token.json


@dataclass
class Config:
    """Everything Jarvis needs to know about itself."""

    home: Path = field(default_factory=default_home)
    # Used in greetings: "Sir", "Ma'am", a first name, whatever you like.
    address: str = "Sir"
    timezone: str = "Europe/Berlin"
    brain: BrainConfig = field(default_factory=BrainConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)

    @property
    def db_path(self) -> Path:
        return self.home / "jarvis.db"

    @property
    def log_path(self) -> Path:
        return self.home / "jarvis.log"

    @property
    def config_path(self) -> Path:
        return self.home / "config.toml"

    @property
    def google_credentials_path(self) -> Path:
        return Path(self.tools.google_credentials or self.home / "google_client_secret.json")

    @property
    def google_token_path(self) -> Path:
        return Path(self.tools.google_token or self.home / "google_token.json")

    def ensure_home(self) -> Path:
        self.home.mkdir(parents=True, exist_ok=True)
        return self.home


def _coerce(value: Any, target: Any) -> Any:
    """Bend a TOML/env value into the type the dataclass field declares."""
    if target is bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if target is int:
        return int(value)
    if target is float:
        return float(value)
    if target is tuple:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return tuple(value)
    if target is Path:
        return Path(str(value)).expanduser()
    return value


def _field_type(declared: Any) -> Any:
    """Reduce an annotation such as ``int | None`` to something we can coerce."""
    text = str(declared)
    if "tuple" in text:
        return tuple
    if "Path" in text:
        return Path
    if "bool" in text:
        return bool
    if "int" in text:
        return int
    if "float" in text:
        return float
    return str


def _apply(section: Any, values: dict[str, Any]) -> None:
    known = {f.name: f for f in fields(section)}
    for key, value in values.items():
        target = known.get(key)
        if target is None:
            continue
        if is_dataclass(getattr(section, key, None)) and isinstance(value, dict):
            _apply(getattr(section, key), value)
            continue
        if value is None:
            continue
        setattr(section, key, _coerce(value, _field_type(target.type)))


def _env_overrides(config: Config) -> None:
    """Read ``JARVIS_<SECTION>_<FIELD>`` environment variables.

    ``JARVIS_VOICE_LANGUAGE=en`` and ``JARVIS_ADDRESS=Silas`` both work.
    """
    sections = {"brain": config.brain, "voice": config.voice, "tools": config.tools}
    for raw_key, raw_value in os.environ.items():
        if not raw_key.startswith(_ENV_PREFIX):
            continue
        key = raw_key[len(_ENV_PREFIX):].lower()
        for name, section in sections.items():
            if key.startswith(f"{name}_"):
                _apply(section, {key[len(name) + 1:]: raw_value})
                break
        else:
            _apply(config, {key: raw_value})


def load_config(path: Path | None = None) -> Config:
    """Build the effective configuration."""
    config = Config()
    candidate = path or config.config_path
    if candidate.exists():
        with candidate.open("rb") as handle:
            data = tomllib.load(handle)
        for section in ("brain", "voice", "tools"):
            if isinstance(data.get(section), dict):
                _apply(getattr(config, section), data.pop(section))
        _apply(config, data)
    _env_overrides(config)
    return config


EXAMPLE_CONFIG = """\
# ~/.jarvis/config.toml -- every value here is optional.

address = "Sir"           # how Jarvis addresses you
timezone = "Europe/Berlin"

[brain]
model = "claude-opus-5"
effort = "low"            # low | medium | high | xhigh | max
show_thinking = false
web_access = true

[voice]
language = "de"           # spoken language for both ears and voice
wake_words = ["jarvis", "hey jarvis"]
followup_window = 12.0    # seconds you may keep talking without the wake word
barge_in = true           # interrupt Jarvis by speaking over him
stt_engine = "auto"       # auto | faster-whisper | whisper-cpp
stt_model = "base"
tts_engine = "auto"       # auto | piper | edge | say | espeak | none
tts_voice = ""            # engine-specific voice id

[tools]
calendar = true
mail = true
confirm_outbound = true   # ask before sending mail or deleting events
"""

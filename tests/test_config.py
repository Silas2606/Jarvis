"""Configuration: the example file must not drift away from the real defaults."""

from __future__ import annotations

import tomllib

from jarvis.config import EXAMPLE_CONFIG, Config, load_config


def test_example_config_matches_the_actual_defaults():
    """The shipped example must state what the code actually does.

    The same setting lives in two places -- the dataclass default and the
    example file -- and editing one without the other produces a config that
    says one thing and does another. That happened: `stt_model` was changed in
    the example only, so the setting appeared to take effect and did not.
    """
    example = tomllib.loads(EXAMPLE_CONFIG)
    defaults = Config()
    sections = {"brain": defaults.brain, "voice": defaults.voice, "tools": defaults.tools}

    mismatches: list[str] = []
    for section_name, values in example.items():
        if not isinstance(values, dict):
            actual = getattr(defaults, section_name, None)
            if actual is not None and str(actual) != str(values):
                mismatches.append(f"{section_name}: example {values!r} != default {actual!r}")
            continue
        section = sections.get(section_name)
        if section is None:
            continue
        for key, value in values.items():
            actual = getattr(section, key, None)
            if isinstance(actual, tuple):
                actual = list(actual)
            if actual != value:
                mismatches.append(
                    f"[{section_name}] {key}: example {value!r} != default {actual!r}"
                )

    assert not mismatches, (
        "The example config and the code defaults disagree. Change both, or "
        "neither:\n  " + "\n  ".join(mismatches)
    )


def test_environment_variables_override_defaults(monkeypatch):
    monkeypatch.setenv("JARVIS_VOICE_STT_MODEL", "tiny")
    monkeypatch.setenv("JARVIS_VOICE_TTS_VOICE", "de-DE-TestNeural")
    monkeypatch.setenv("JARVIS_ADDRESS", "Silas")

    config = load_config()
    assert config.voice.stt_model == "tiny"
    assert config.voice.tts_voice == "de-DE-TestNeural"
    assert config.address == "Silas"


def test_booleans_survive_the_trip_through_the_environment(monkeypatch):
    monkeypatch.setenv("JARVIS_VOICE_BARGE_IN", "false")
    assert load_config().voice.barge_in is False
    monkeypatch.setenv("JARVIS_VOICE_BARGE_IN", "true")
    assert load_config().voice.barge_in is True

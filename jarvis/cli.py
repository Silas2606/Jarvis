"""Command line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from jarvis import __version__
from jarvis.config import EXAMPLE_CONFIG, Config, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="A voice-first personal assistant in the spirit of J.A.R.V.I.S.",
        epilog="Run `jarvis` with no arguments to start listening.",
    )
    parser.add_argument("--version", action="version", version=f"jarvis {__version__}")
    parser.add_argument("--config", type=Path, help="Path to a config.toml.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show reasoning and tool results.")
    parser.add_argument("--model", help="Override the Claude model.")
    parser.add_argument("--language", help="Spoken language, e.g. de or en.")
    parser.add_argument("--text", action="store_true", help="Type instead of talk.")
    parser.add_argument("--no-greeting", action="store_true", help="Start without a greeting.")
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], help="How hard to think.")

    subcommands = parser.add_subparsers(dest="command")

    ask = subcommands.add_parser("ask", help="Run a single instruction and exit.")
    ask.add_argument("instruction", nargs="+")

    subcommands.add_parser("doctor", help="Check what is installed and working.")
    subcommands.add_parser("devices", help="List the audio devices.")

    say = subcommands.add_parser("say", help="Speak a sentence, to test the voice.")
    say.add_argument("text", nargs="+")

    subcommands.add_parser("listen", help="Record one utterance and print the transcript.")
    subcommands.add_parser("voices", help="List the voices the speech engine accepts.")

    setup = subcommands.add_parser("setup", help="One-off setup steps.")
    setup.add_argument("what", choices=["google", "config"], help="What to set up.")

    return parser


def apply_overrides(config: Config, args: argparse.Namespace) -> Config:
    if args.model:
        config.brain.model = args.model
    if args.language:
        config.voice.language = args.language
    if args.effort:
        config.brain.effort = args.effort
    return config


# -- subcommands -------------------------------------------------------------


def command_doctor(config: Config) -> int:
    """Report what is present and what is missing, with the cure for each."""
    from jarvis.tools.google_auth import is_configured
    from jarvis.voice import stt, tts

    lines: list[str] = []
    problems: list[str] = []

    def check(label: str, ok: bool, detail: str = "", fix: str = "") -> None:
        mark = "✓" if ok else "✗"
        lines.append(f"  {mark} {label:<26} {detail}")
        if not ok and fix:
            problems.append(f"    {label}: {fix}")

    lines.append(f"\n  Jarvis {__version__}")
    lines.append(f"  Home: {config.home}")
    lines.append(f"  Config: {config.config_path}{'' if config.config_path.exists() else '  (not created yet)'}")
    lines.append("")

    key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    check(
        "Claude credentials",
        key,
        "ANTHROPIC_API_KEY is set" if key else "missing",
        "export ANTHROPIC_API_KEY=sk-ant-…  (or run `ant auth login`)",
    )

    try:
        import anthropic

        check("anthropic SDK", True, anthropic.__version__)
    except ImportError:
        check("anthropic SDK", False, "missing", "pip install anthropic")

    ears = stt.available_engines()
    check(
        "speech recognition",
        bool(ears),
        ", ".join(ears) if ears else "none",
        'pip install "jarvis-assistant[voice]"',
    )

    voices = tts.available_engines()
    selected = tts.build_speaker(config)
    detail = selected.describe() if selected.name != "none" else "none"
    if voices and selected.name != "none" and len(voices) > 1:
        others = [v for v in voices if v != selected.name]
        detail += f"  [also available: {', '.join(others)}]"
    check(
        "speech synthesis",
        bool(voices),
        detail,
        'install piper, or pip install "jarvis-assistant[edge]" plus ffmpeg',
    )

    if os.environ.get("ELEVENLABS_API_KEY") or config.voice.elevenlabs_key:
        try:
            from jarvis.voice.elevenlabs import ElevenLabsSpeaker

            speaker = ElevenLabsSpeaker(
                api_key=config.voice.elevenlabs_key,
                voice=config.voice.tts_voice,
                model=config.voice.elevenlabs_model,
                resolve_voice=False,
            )
            check("ElevenLabs", True, f"{speaker.check()} ({speaker.voice})")
        except Exception as exc:
            check("ElevenLabs", False, str(exc)[:80], "jarvis voices")

        state = config.tts_state_path
        if state.exists():
            import json as _json
            import time as _time

            try:
                bench = _json.loads(state.read_text("utf-8"))
                until = float(bench.get("until", 0))
            except Exception:
                until = 0
            if until > _time.time():
                when = _time.strftime("%d.%m. %H:%M", _time.localtime(until))
                lines.append(
                    f"    ElevenLabs is paused ({bench.get('reason', '?')}); "
                    f"retrying at {when}. Delete {state} to retry now."
                )

    try:
        import sounddevice  # noqa: F401

        check("audio i/o", True, "sounddevice")
    except ImportError:
        check("audio i/o", False, "missing", 'pip install "jarvis-assistant[voice]"')

    try:
        import openwakeword  # noqa: F401

        check("wake word", True, f"openwakeword ({config.voice.wake_model})")
    except ImportError:
        check(
            "wake word",
            False,
            "falling back to transcription",
            'pip install "jarvis-assistant[voice]" for a lighter, faster wake word',
        )

    try:
        import webrtcvad  # noqa: F401

        check("voice activity", True, "webrtcvad")
    except ImportError:
        check(
            "voice activity",
            False,
            "energy fallback (slower to detect end of speech)",
            "pip install webrtcvad-wheels   (on Windows; pip install webrtcvad elsewhere)",
        )

    google = is_configured(config)
    check(
        "Google (calendar, mail)",
        google,
        "authorised" if google else "not authorised",
        "jarvis setup google",
    )

    print("\n".join(lines))
    if problems:
        print("\n  To fix:")
        print("\n".join(problems))
    else:
        print("\n  Everything is in place.")
    print()
    return 0 if key else 1


def command_devices() -> int:
    from jarvis.voice import AudioUnavailable
    from jarvis.voice.audio import list_devices

    try:
        print(list_devices())
    except AudioUnavailable as exc:
        print(f"  {exc}")
        return 2
    return 0


def command_say(config: Config, text: str) -> int:
    from jarvis.voice import AudioUnavailable
    from jarvis.voice.tts import build_speaker

    try:
        speaker = build_speaker(config, strict=True)
        print(f"  voice: {speaker.describe()}")
        # Speaking is inside the try as well: a refused voice name fails here,
        # not while the speaker is being built.
        speaker.say(text)
    except AudioUnavailable as exc:
        print(f"  ✗ {exc}")
        return 2
    return 0


def command_voices(config: Config) -> int:
    """List the voice names the configured engine will accept."""
    from jarvis.voice import AudioUnavailable
    from jarvis.voice.tts import build_speaker, list_voices

    engine = config.voice.tts_engine
    if engine == "auto":
        speaker = build_speaker(config)
        # While a paid voice is benched the active speaker is the understudy,
        # but the voices worth listing are still the configured engine's.
        primary = getattr(speaker, "primary", speaker)
        engine = primary.name

    if engine not in {"edge", "piper", "elevenlabs"}:
        print(f"  The {engine!r} engine has no voice list; it uses the system voice.")
        return 0

    try:
        voices = list_voices(config.voice.language, engine)
    except AudioUnavailable as exc:
        print(f"  ✗ {exc}")
        return 2

    if not voices:
        print(f"  No {engine} voices found for language {config.voice.language!r}.")
        return 1

    current = config.voice.tts_voice
    print(f"  {len(voices)} {engine} voice(s) for {config.voice.language!r}:\n")
    for voice in voices:
        marker = " ← in use" if current and voice.split()[0] == current else ""
        print(f"    {voice}{marker}")
    print("\n  Pick one by name or by id:")
    print('    $env:JARVIS_VOICE_TTS_VOICE = "Roger"        # Windows')
    print('    export JARVIS_VOICE_TTS_VOICE="Roger"        # macOS/Linux')
    print('  then:  jarvis say "Guten Abend, Sir."')
    print("\n  To keep it, put it in ~/.jarvis/config.toml under [voice] as tts_voice.")
    return 0


def command_listen(config: Config) -> int:
    from jarvis.voice import AudioUnavailable
    from jarvis.voice.audio import Microphone, VoiceActivityDetector, record_utterance
    from jarvis.voice.stt import build_transcriber

    try:
        transcriber = build_transcriber(config, strict=True)
        print(f"  ears: {transcriber.name} — speak now…")
        with Microphone(config.voice.sample_rate, config.voice.input_device) as microphone:
            utterance = record_utterance(
                microphone,
                VoiceActivityDetector(config.voice.sample_rate),
                silence_timeout=config.voice.silence_timeout,
                max_seconds=config.voice.max_utterance_seconds,
            )
    except AudioUnavailable as exc:
        print(f"  ✗ {exc}")
        return 2

    if utterance is None:
        print("  (nothing heard)")
        return 1
    print(f"  {utterance.seconds:.1f}s recorded")
    print(f"  → {transcriber.transcribe(utterance.audio, utterance.sample_rate)!r}")
    return 0


def command_setup(config: Config, what: str) -> int:
    if what == "config":
        config.ensure_home()
        path = config.config_path
        if path.exists():
            print(f"  {path} already exists; leaving it alone.")
            return 0
        path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
        print(f"  Wrote {path}")
        return 0

    from jarvis.tools.google_auth import GoogleUnavailable, load_credentials, reset_services

    config.ensure_home()
    try:
        load_credentials(config, interactive=True)
        reset_services()
    except GoogleUnavailable as exc:
        print(f"  ✗ {exc}")
        return 2
    except Exception as exc:
        print(f"  ✗ Authorisation failed: {exc}")
        return 2
    print(f"  ✓ Google authorised. Token saved to {config.google_token_path}")
    return 0


# -- entry point -------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = apply_overrides(load_config(args.config), args)

    if args.command == "doctor":
        return command_doctor(config)
    if args.command == "devices":
        return command_devices()
    if args.command == "say":
        return command_say(config, " ".join(args.text))
    if args.command == "listen":
        return command_listen(config)
    if args.command == "voices":
        return command_voices(config)
    if args.command == "setup":
        return command_setup(config, args.what)

    from jarvis.session import ask_once, build_session, run_text, run_voice

    session = build_session(config, verbose=args.verbose)
    try:
        if args.command == "ask":
            return ask_once(session, " ".join(args.instruction))
        if args.text or not config.voice.enabled:
            return run_text(session, greet=not args.no_greeting)
        return run_voice(session, greet=not args.no_greeting)
    finally:
        session.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

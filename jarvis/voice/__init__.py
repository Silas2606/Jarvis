"""Ears and voice: wake word, transcription, speech synthesis, the listen loop."""

from __future__ import annotations

__all__ = ["AudioUnavailable"]


class AudioUnavailable(RuntimeError):
    """Audio hardware or a required package is not available."""

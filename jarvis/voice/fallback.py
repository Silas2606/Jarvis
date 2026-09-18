"""Falling back to a second voice, and coming back when the first recovers.

A paid voice on a metered plan runs out. When it does, an assistant that goes
mute is worse than one that finishes the sentence in a plainer voice -- so the
speaker in front holds the microphone only as long as it can actually speak.

Coming back is the harder half. Retrying on every sentence would cost a failed
request and a pause each time; never retrying would leave the good voice
unused for the rest of the account's life. So a failure benches the primary
until a specific moment: the quota reset the service itself reports, or a
fixed interval when it reports none. The bench survives restarts, because
otherwise every launch would spend one doomed request to rediscover the same
thing.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from jarvis.voice import AudioUnavailable
from jarvis.voice.tts import Speaker

# Used when the service reports no reset time of its own. Short enough that a
# topped-up account recovers the same day, long enough not to nag.
DEFAULT_BENCH_SECONDS = 6 * 3600

# A rejected key or an unknown voice is a configuration error, not a quota
# problem. Waiting for a quota reset would never fix it, and the user may
# correct it at any moment -- so these are benched only briefly.
CONFIG_ERROR_BENCH_SECONDS = 15 * 60


@dataclass
class BenchState:
    """Until when the primary voice is out of play, and why."""

    until: float = 0.0
    reason: str = ""

    @property
    def active(self) -> bool:
        return self.until > time.time()

    @property
    def remaining(self) -> float:
        return max(0.0, self.until - time.time())

    def to_dict(self) -> dict:
        return {"until": self.until, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: dict) -> "BenchState":
        try:
            return cls(float(data.get("until", 0.0)), str(data.get("reason", "")))
        except (TypeError, ValueError):
            return cls()


class FallbackSpeaker(Speaker):
    """Speaks with `primary` while it works, with `backup` while it does not."""

    def __init__(
        self,
        primary: Speaker,
        backup: Speaker,
        state_path: Path | str | None = None,
        notify: Callable[[str], None] | None = None,
        reset_lookup: Callable[[], float] | None = None,
    ):
        super().__init__()
        self.primary = primary
        self.backup = backup
        self.state_path = Path(state_path) if state_path else None
        self.notify = notify
        # Asks the service when the quota returns; None means use the default.
        self.reset_lookup = reset_lookup
        self._lock = threading.Lock()
        self._bench = self._load()

    # -- naming --------------------------------------------------------------

    @property
    def name(self) -> str:
        return self.backup.name if self._bench.active else self.primary.name

    @property
    def voice_name(self) -> str:
        active = self.backup if self._bench.active else self.primary
        return active.voice_name

    def describe(self) -> str:
        if self._bench.active:
            minutes = int(self._bench.remaining // 60)
            when = f"{minutes // 60} h" if minutes >= 60 else f"{minutes} min"
            return f"{self.backup.describe()}  [{self.primary.name} paused, retry in {when}]"
        return self.primary.describe()

    # -- bench bookkeeping ---------------------------------------------------

    def _load(self) -> BenchState:
        if self.state_path is None or not self.state_path.exists():
            return BenchState()
        try:
            return BenchState.from_dict(json.loads(self.state_path.read_text("utf-8")))
        except Exception:
            return BenchState()

    def _save(self) -> None:
        if self.state_path is None:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self._bench.to_dict()), encoding="utf-8")
        except OSError:
            pass  # an unwritable state file must not stop the assistant talking

    def _bench_primary(self, reason: str, exc: Exception) -> None:
        """Take the primary out of play until it is plausibly usable again."""
        # Only a spent quota is worth waiting a month for. A mistyped voice or
        # a bad key must not sideline the paid voice until next month.
        if reason in {"key rejected", "unknown voice"}:
            until = time.time() + CONFIG_ERROR_BENCH_SECONDS
        else:
            reset = self.reset_lookup() if self.reset_lookup else 0.0
            # A reported reset in the past, or absurdly far out, is not usable.
            horizon = time.time() + 40 * 24 * 3600
            until = reset if time.time() < reset < horizon else time.time() + DEFAULT_BENCH_SECONDS

        with self._lock:
            self._bench = BenchState(until, reason)
            self._save()

        if self.notify is not None:
            when = time.strftime("%d.%m. %H:%M", time.localtime(until))
            self.notify(
                f"{self.primary.name} is unavailable ({reason}); "
                f"switching to {self.backup.name} until {when}."
            )

    def _clear_bench(self) -> None:
        with self._lock:
            self._bench = BenchState()
            self._save()
        if self.notify is not None:
            self.notify(f"{self.primary.name} is available again.")

    @property
    def bench(self) -> BenchState:
        return self._bench

    # -- speaking ------------------------------------------------------------

    def _recoverable(self, exc: Exception) -> str:
        """Whether this failure is one the backup should take over, and why."""
        message = str(exc).lower()
        if "quota" in message or "rate limit" in message:
            return "quota exhausted"
        if "not reachable" in message or "timed out" in message:
            return "service unreachable"
        if "rejected" in message and "key" in message:
            return "key rejected"
        # A voice the account does not have is a setting to correct, not an
        # outage to wait out.
        if "voice" in message and ("does not exist" in message or "no elevenlabs voice" in message):
            return "unknown voice"
        if "returned an error" in message:
            return "service error"
        return ""

    def _speak(self, text: str) -> None:
        # A bench that has run out means this sentence is the primary's trial
        # run: if it works, the bench is lifted; if not, it starts again.
        recovering = bool(self._bench.until) and not self._bench.active

        if not self._bench.active:
            try:
                self.primary.say(text)
                if recovering:
                    self._clear_bench()
                return
            except AudioUnavailable as exc:
                reason = self._recoverable(exc)
                # A failure the backup cannot help with -- no player installed,
                # say -- belongs to the caller, not to the fallback logic.
                if not reason:
                    raise
                self._bench_primary(reason, exc)
            except Exception as exc:  # pragma: no cover - unexpected shapes
                self._bench_primary("unexpected failure", exc)

        self.backup.say(text)

    def stop(self) -> None:
        super().stop()
        self.primary.stop()
        self.backup.stop()

    @property
    def is_speaking(self) -> bool:
        return self.primary.is_speaking or self.backup.is_speaking

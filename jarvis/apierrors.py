"""Turning API failures into something worth saying out loud.

Two audiences, one failure. The user hears a sentence that says what went
wrong and what to do about it; the console additionally gets the technical
detail. A voice assistant that reads a JSON error body aloud is unusable, and
one that only says "something went wrong" is useless -- so every problem
carries both.

Messages are written in the user's own language, because Jarvis is speaking
them, not printing them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Each entry: what happened, what the user should do about it.
_MESSAGES: dict[str, dict[str, str]] = {
    "no_credit": {
        "de": "Auf dem Anthropic-Konto ist kein Guthaben mehr. In der Console "
              "unter Plans and Billing lässt sich welches aufladen.",
        "en": "The Anthropic account is out of credit. You can top it up under "
              "Plans and Billing in the console.",
    },
    "bad_key": {
        "de": "Der API-Schlüssel wird abgelehnt. Bitte prüfen Sie "
              "ANTHROPIC_API_KEY.",
        "en": "The API key was rejected. Please check ANTHROPIC_API_KEY.",
    },
    "no_key": {
        "de": "Ich finde keine Zugangsdaten. Bitte setzen Sie "
              "ANTHROPIC_API_KEY.",
        "en": "I cannot find any credentials. Please set ANTHROPIC_API_KEY.",
    },
    "forbidden": {
        "de": "Das Konto darf diese Anfrage nicht stellen. Möglicherweise "
              "fehlt dem Schlüssel die nötige Berechtigung.",
        "en": "The account is not allowed to make this request. The key may be "
              "missing a permission.",
    },
    "no_model": {
        "de": "Das Modell {model} steht diesem Konto nicht zur Verfügung.",
        "en": "The model {model} is not available on this account.",
    },
    "rate_limit": {
        "de": "Das Anfragelimit ist erreicht. Einen Moment, dann geht es "
              "wieder.",
        "en": "The rate limit is reached. Give it a moment and try again.",
    },
    "overloaded": {
        "de": "Die Claude-API ist gerade überlastet. Bitte gleich noch einmal.",
        "en": "The Claude API is overloaded right now. Please try again "
              "shortly.",
    },
    "server": {
        "de": "Bei Anthropic ist etwas schiefgegangen. Das liegt nicht an "
              "Ihnen; in einem Moment sollte es wieder gehen.",
        "en": "Something went wrong on Anthropic's side. Not your doing; it "
              "should recover shortly.",
    },
    "offline": {
        "de": "Ich habe keine Verbindung zur Claude-API. Bitte prüfen Sie die "
              "Internetverbindung.",
        "en": "I have no connection to the Claude API. Please check the "
              "internet connection.",
    },
    "timeout": {
        "de": "Die Anfrage hat zu lange gedauert und wurde abgebrochen.",
        "en": "The request took too long and was cancelled.",
    },
    "too_long": {
        "de": "Das Gespräch ist zu lang geworden. Sagen Sie „vergiss das "
              "Gespräch“, dann fange ich von vorne an.",
        "en": "The conversation has grown too long. Say “forget the "
              "conversation” and I will start over.",
    },
    "rejected": {
        "de": "Die Anfrage wurde abgelehnt.",
        "en": "The request was rejected.",
    },
    "unknown": {
        "de": "Es ist ein unerwarteter Fehler aufgetreten.",
        "en": "An unexpected error occurred.",
    },
}


@dataclass
class ApiProblem:
    """One failure, described for both the ear and the log."""

    kind: str
    spoken: str
    detail: str = ""
    # Whether trying the same thing again might work.
    retryable: bool = False

    def __str__(self) -> str:
        return self.spoken


def _say(kind: str, language: str, **fields: Any) -> str:
    entry = _MESSAGES.get(kind, _MESSAGES["unknown"])
    template = entry.get(language[:2].lower(), entry["en"])
    return template.format(**fields) if fields else template


def api_message(exc: Exception) -> str:
    """The server's own message, without the JSON wrapping around it.

    The SDK exposes the parsed body, so there is no need to pick the message
    out of the stringified error.
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if body.get("message"):
            return str(body["message"])
    message = getattr(exc, "message", None)
    return str(message or exc)


def diagnose(exc: Exception, model: str = "", language: str = "en") -> ApiProblem:
    """Work out what actually went wrong and how to put it."""
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return ApiProblem("unknown", _say("unknown", language), str(exc))

    detail = api_message(exc)
    lowered = detail.lower()

    if isinstance(exc, anthropic.AuthenticationError):
        return ApiProblem("bad_key", _say("bad_key", language), detail)

    if isinstance(exc, anthropic.PermissionDeniedError):
        return ApiProblem("forbidden", _say("forbidden", language), detail)

    if isinstance(exc, anthropic.NotFoundError):
        return ApiProblem("no_model", _say("no_model", language, model=model or "?"), detail)

    if isinstance(exc, anthropic.RateLimitError):
        return ApiProblem("rate_limit", _say("rate_limit", language), detail, retryable=True)

    if isinstance(exc, anthropic.BadRequestError):
        # The API has no distinct exception class for an empty balance -- it
        # arrives as a 400 whose message says so. Match on the body's message,
        # not on the stringified exception.
        if "credit balance" in lowered or "insufficient" in lowered:
            return ApiProblem("no_credit", _say("no_credit", language), detail)
        if "prompt is too long" in lowered or "maximum context" in lowered:
            return ApiProblem("too_long", _say("too_long", language), detail)
        return ApiProblem("rejected", f"{_say('rejected', language)} {detail}", detail)

    if isinstance(exc, anthropic.APITimeoutError):
        return ApiProblem("timeout", _say("timeout", language), detail, retryable=True)

    if isinstance(exc, anthropic.APIConnectionError):
        return ApiProblem("offline", _say("offline", language), detail, retryable=True)

    if isinstance(exc, anthropic.APIStatusError):
        status = getattr(exc, "status_code", None)
        # 529 is Anthropic's "overloaded", distinct from a plain 5xx.
        if status == 529 or "overloaded" in lowered:
            return ApiProblem("overloaded", _say("overloaded", language), detail, retryable=True)
        if status is not None and status >= 500:
            return ApiProblem("server", _say("server", language), detail, retryable=True)
        return ApiProblem("rejected", f"{_say('rejected', language)} {detail}", detail)

    if isinstance(exc, TypeError) and ("authentication" in lowered or "api_key" in lowered):
        return ApiProblem("no_key", _say("no_key", language), detail)

    return ApiProblem("unknown", _say("unknown", language), f"{type(exc).__name__}: {detail}")

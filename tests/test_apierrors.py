"""API failures must come out as sentences a person can act on.

These are spoken aloud, so the bar is: no JSON, no status codes, no stack
traces -- and in the user's own language.
"""

from __future__ import annotations

import anthropic
import httpx2 as httpx
import pytest

from jarvis.apierrors import api_message, diagnose


def response_error(cls, status: int, message: str, error_type: str = "invalid_request_error"):
    """Build a real SDK exception the way the server would produce it."""
    body = {"type": "error", "error": {"type": error_type, "message": message}}
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request, json=body)
    return cls(message, response=response, body=body)


def test_empty_balance_says_what_to_do():
    """The failure that actually bit: a 400 whose message is about credit."""
    exc = response_error(
        anthropic.BadRequestError,
        400,
        "Your credit balance is too low to access the Anthropic API. "
        "Please go to Plans & Billing to upgrade or purchase credits.",
    )
    problem = diagnose(exc, "claude-opus-5", "de")

    assert problem.kind == "no_credit"
    assert "Guthaben" in problem.spoken
    assert "Plans and Billing" in problem.spoken
    # The raw body is kept for the console, but never spoken.
    assert "credit balance" in problem.detail
    assert "{" not in problem.spoken and "400" not in problem.spoken


def test_bad_key_is_distinguished_from_no_credit():
    exc = response_error(anthropic.AuthenticationError, 401, "invalid x-api-key")
    problem = diagnose(exc, "claude-opus-5", "de")
    assert problem.kind == "bad_key"
    assert "ANTHROPIC_API_KEY" in problem.spoken


def test_messages_follow_the_configured_language():
    exc = response_error(anthropic.AuthenticationError, 401, "invalid x-api-key")
    assert "Schlüssel" in diagnose(exc, "", "de").spoken
    assert "API key" in diagnose(exc, "", "en").spoken
    # An unsupported language falls back to English rather than crashing.
    assert "API key" in diagnose(exc, "", "fi").spoken


def test_missing_model_names_the_model():
    exc = response_error(anthropic.NotFoundError, 404, "model not found")
    problem = diagnose(exc, "claude-opus-5", "de")
    assert problem.kind == "no_model"
    assert "claude-opus-5" in problem.spoken


@pytest.mark.parametrize(
    ("status", "message", "kind"),
    [
        (429, "rate limit exceeded", "rate_limit"),
        (529, "Overloaded", "overloaded"),
        (500, "internal error", "server"),
        (403, "forbidden", "forbidden"),
    ],
)
def test_status_codes_map_to_their_own_explanations(status, message, kind):
    cls = {
        429: anthropic.RateLimitError,
        529: anthropic.APIStatusError,
        500: anthropic.InternalServerError,
        403: anthropic.PermissionDeniedError,
    }[status]
    problem = diagnose(response_error(cls, status, message), "", "de")
    assert problem.kind == kind
    # Anything worth retrying is marked as such.
    assert problem.retryable is (kind in {"rate_limit", "overloaded", "server"})


def test_context_overflow_suggests_starting_over():
    exc = response_error(anthropic.BadRequestError, 400, "prompt is too long: 1000000 tokens")
    problem = diagnose(exc, "", "de")
    assert problem.kind == "too_long"
    assert "vergiss" in problem.spoken.lower()


def test_no_connection_is_not_blamed_on_the_user():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    problem = diagnose(anthropic.APIConnectionError(request=request), "", "de")
    assert problem.kind == "offline"
    assert problem.retryable is True


def test_missing_credentials_are_recognised():
    exc = TypeError(
        "Could not resolve authentication method. Expected one of api_key, "
        "auth_token, or credentials to be set."
    )
    problem = diagnose(exc, "", "de")
    assert problem.kind == "no_key"
    assert "ANTHROPIC_API_KEY" in problem.spoken


def test_server_message_is_unwrapped_from_the_json_body():
    exc = response_error(anthropic.BadRequestError, 400, "something specific went wrong")
    assert api_message(exc) == "something specific went wrong"


def test_every_message_exists_in_both_languages():
    from jarvis.apierrors import _MESSAGES

    for kind, translations in _MESSAGES.items():
        assert "de" in translations, f"{kind} has no German wording"
        assert "en" in translations, f"{kind} has no English wording"

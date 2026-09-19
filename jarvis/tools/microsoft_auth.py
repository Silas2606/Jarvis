"""Signing in to Microsoft, for Graph API access.

Uses the device-code flow: Jarvis prints a short code, the user types it into
a browser on any device, and the token lands here. That avoids registering
redirect URIs and works on a machine with no browser of its own.

The token is cached on disk and refreshed silently, so the sign-in happens
once rather than every launch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

# Just enough to read and write the user's own tasks. Widening this means
# re-consenting, which is the point of keeping it narrow.
SCOPES = ["Tasks.ReadWrite"]

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"

# "common" accepts both personal Microsoft accounts and work/school ones.
AUTHORITY = "https://login.microsoftonline.com/common"

_SETUP_HINT = (
    "Microsoft access is not set up. Register an application at "
    "https://entra.microsoft.com (Identity → App registrations → New "
    "registration), allow public client flows, then put the Application "
    "(client) ID in ~/.jarvis/config.toml under [tools] as microsoft_client_id "
    "and run `jarvis setup microsoft`."
)


class MicrosoftUnavailable(RuntimeError):
    """Microsoft access is not usable, with a readable reason."""


def _require_msal():
    try:
        import msal
    except ImportError as exc:
        raise MicrosoftUnavailable(
            "The Microsoft library is missing. Install it with: "
            'pip install "jarvis-assistant[microsoft]"'
        ) from exc
    return msal


def _cache(path: Path):
    """A token cache that persists between runs."""
    msal = _require_msal()
    cache = msal.SerializableTokenCache()
    if path.exists():
        try:
            cache.deserialize(path.read_text("utf-8"))
        except Exception:
            pass  # a corrupt cache just means signing in again
    return cache


def _save(cache, path: Path) -> None:
    if not cache.has_state_changed:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cache.serialize(), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows does not do POSIX modes; the file is in the user profile


def _client_id(config) -> str:
    client_id = (getattr(config.tools, "microsoft_client_id", "") or "").strip()
    if not client_id:
        raise MicrosoftUnavailable(_SETUP_HINT)
    return client_id


def get_token(config, interactive: bool = False, prompt: Callable[[str], None] | None = None) -> str:
    """Return an access token, signing in if allowed.

    Args:
        config: The Jarvis configuration.
        interactive: Permit the device-code sign-in. The voice loop never does
            this; `jarvis setup microsoft` does.
        prompt: Called with the instructions the user has to follow.
    """
    msal = _require_msal()
    path = Path(config.microsoft_token_path)
    cache = _cache(path)
    app = msal.PublicClientApplication(_client_id(config), authority=AUTHORITY, token_cache=cache)

    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _save(cache, path)
            return result["access_token"]

    if not interactive:
        raise MicrosoftUnavailable(
            "Microsoft access needs to be authorised once. Run `jarvis setup microsoft`."
        )

    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise MicrosoftUnavailable(
            f"The sign-in could not be started: {flow.get('error_description', flow)}"
        )
    if prompt is not None:
        prompt(flow["message"])

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise MicrosoftUnavailable(
            f"Sign-in failed: {result.get('error_description', result.get('error', 'unknown'))}"
        )
    _save(cache, path)
    return result["access_token"]


def is_configured(config) -> bool:
    """Whether a token cache exists -- cheap, no network."""
    return Path(config.microsoft_token_path).exists()


def graph(config, method: str, path: str, payload: dict | None = None) -> Any:
    """Make one Graph request and return the parsed body."""
    import urllib.error
    import urllib.request

    token = get_token(config)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{GRAPH_ROOT}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        raise _graph_failure(exc) from exc
    except urllib.error.URLError as exc:
        raise MicrosoftUnavailable(f"Microsoft is not reachable: {exc.reason}") from exc


def _graph_failure(exc) -> MicrosoftUnavailable:
    detail = ""
    try:
        body = json.loads(exc.read().decode("utf-8", "replace"))
        detail = str(body.get("error", {}).get("message", ""))
    except Exception:
        detail = getattr(exc, "reason", "") or ""

    if exc.code == 401:
        return MicrosoftUnavailable(
            "Microsoft rejected the sign-in. Run `jarvis setup microsoft` again."
        )
    if exc.code == 403:
        return MicrosoftUnavailable(f"That is not permitted by the granted scopes. {detail}".strip())
    if exc.code == 404:
        return MicrosoftUnavailable(f"Not found in To Do. {detail}".strip())
    if exc.code == 429:
        return MicrosoftUnavailable("Microsoft is rate limiting; try again shortly.")
    return MicrosoftUnavailable(f"Microsoft returned an error ({exc.code}): {detail}".strip())

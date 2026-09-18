"""OAuth plumbing for Google Calendar and Gmail.

The Google libraries are an optional dependency. Everything here imports them
lazily so that a Jarvis without them still starts, and simply reports that the
calendar is out of reach rather than crashing on import.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

_SETUP_HINT = (
    "Google access is not set up. Create an OAuth client (type: Desktop) in the "
    "Google Cloud console, enable the Calendar and Gmail APIs, save the JSON as "
    "{path}, then run `jarvis setup google`."
)

# One service client per API name, kept for the life of the process.
_services: dict[str, Any] = {}


class GoogleUnavailable(RuntimeError):
    """Google access is not usable, with a human-readable reason."""


def _require_libraries():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise GoogleUnavailable(
            "The Google client libraries are missing. Install them with: "
            'pip install "jarvis-assistant[google]"'
        ) from exc
    return Request, Credentials, InstalledAppFlow, build


def load_credentials(config, interactive: bool = False):
    """Return usable OAuth credentials, refreshing or running the flow.

    Args:
        config: The Jarvis config, for the credential file locations.
        interactive: Allow opening a browser for the consent screen. The voice
            loop never does this; ``jarvis setup google`` does.
    """
    Request, Credentials, InstalledAppFlow, _ = _require_libraries()

    token_path = Path(config.google_token_path)
    secrets_path = Path(config.google_credentials_path)
    credentials = None

    if token_path.exists():
        try:
            credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception:
            credentials = None

    if credentials and credentials.valid:
        return credentials

    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            token_path.write_text(credentials.to_json(), encoding="utf-8")
            return credentials
        except Exception:
            credentials = None

    if not interactive:
        raise GoogleUnavailable(
            "Google access needs to be authorised once. Run `jarvis setup google` "
            "in a terminal."
        )

    if not secrets_path.exists():
        raise GoogleUnavailable(_SETUP_HINT.format(path=secrets_path))

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    credentials = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    token_path.chmod(0o600)
    return credentials


def get_service(config, api: str, version: str, interactive: bool = False):
    """Build (and cache) a Google API client."""
    key = f"{api}:{version}"
    if key in _services:
        return _services[key]
    _, _, _, build = _require_libraries()
    credentials = load_credentials(config, interactive=interactive)
    service = build(api, version, credentials=credentials, cache_discovery=False)
    _services[key] = service
    return service


def reset_services() -> None:
    """Drop cached clients, e.g. after re-authorising."""
    _services.clear()


def is_configured(config) -> bool:
    """Whether a token exists at all -- cheap check, no network."""
    return Path(config.google_token_path).exists()

"""Reading pages that only exist once JavaScript has run.

A plain fetch gets the HTML a server sends. Modern applications send a shell
and fill it in afterwards, so the numbers a person sees are never in that
HTML. Those pages need a real browser.

Jarvis keeps his own browser profile, separate from the one you use. You sign
in to it once, per site, with `jarvis browser-login`; the session then lives in
that profile and he can read those pages later without asking again.

The profile holds login cookies, which makes it as sensitive as a password
manager -- it lives under ~/.jarvis and never leaves the machine. Only reading
happens here: no clicking, no typing, no forms.
"""

from __future__ import annotations

from pathlib import Path

from jarvis.tools.base import ToolError

# Long enough for a dashboard that fetches its own data, short enough that a
# broken page does not hang a spoken conversation.
LOAD_TIMEOUT_MS = 30_000
SETTLE_MS = 2_500


class RenderUnavailable(ToolError):
    """Rendering is not possible, with a reason worth reading."""


def profile_dir(config) -> Path:
    return Path(getattr(config, "home", Path.home() / ".jarvis")) / "browser-profile"


def _executable(config) -> str:
    """An explicit Chromium path, when the bundled one is not the right build.

    Playwright ships with a pinned browser revision; a machine that already
    has a suitable Chromium can point at it instead of downloading another.
    """
    import os

    return (
        getattr(getattr(config, "tools", None), "browser_executable", "")
        or os.environ.get("JARVIS_CHROMIUM", "")
    )


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RenderUnavailable(
            "Rendering pages needs Playwright. Install it with: "
            'pip install "jarvis-assistant[browser]" '
            "and then: playwright install chromium"
        ) from exc
    return sync_playwright


def render_text(config, url: str, wait_for: str = "", timeout_ms: int = LOAD_TIMEOUT_MS) -> str:
    """Load a page in Jarvis' browser profile and return what it says.

    Args:
        config: The Jarvis configuration, for the profile location.
        url: The page to load.
        wait_for: Optional CSS selector to wait for before reading, when the
            interesting part arrives later than the rest.
        timeout_ms: How long to allow for loading.
    """
    sync_playwright = _require_playwright()
    profile = profile_dir(config)
    profile.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        options: dict = {
            "headless": True,
            "viewport": {"width": 1440, "height": 1000},
            "locale": getattr(getattr(config, "voice", None), "language", "de") or "de",
        }
        executable = _executable(config)
        if executable:
            options["executable_path"] = executable

        try:
            context = playwright.chromium.launch_persistent_context(str(profile), **options)
        except Exception as exc:
            raise RenderUnavailable(
                f"The browser could not be started: {exc}. You may need to run "
                "`playwright install chromium` once."
            ) from exc

        try:
            page = context.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            if wait_for:
                try:
                    page.wait_for_selector(wait_for, timeout=timeout_ms)
                except Exception:
                    pass  # read what is there rather than failing outright
            else:
                # Give the page's own requests a moment to land.
                try:
                    page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
                except Exception:
                    pass
            page.wait_for_timeout(400)

            title = page.title()
            # innerText, not textContent: it respects what is actually visible,
            # so hidden markup and off-screen menus stay out of the summary.
            text = page.evaluate("() => document.body ? document.body.innerText : ''")
            current = page.url
        except Exception as exc:
            raise RenderUnavailable(f"{url} could not be loaded: {exc}") from exc
        finally:
            context.close()

    if _looks_like_sign_in(text, current):
        raise RenderUnavailable(
            f"That page wants a sign-in. Run `jarvis browser-login {url}` once, "
            "sign in in the window that opens, then ask again."
        )
    return f"{title}\n{current}\n\n{text.strip()}"


def _looks_like_sign_in(text: str, url: str) -> bool:
    """Tell a login wall from a page that merely mentions signing in."""
    lowered = text.strip().lower()[:900]
    address = url.lower()
    if any(marker in address for marker in ("accounts.google.com", "login.", "/signin", "/login")):
        return True
    prompts = ("sign in", "anmelden", "log in", "einloggen", "passwort", "password")
    # A short page that is mostly a sign-in prompt, rather than a long page
    # with a sign-in link in its footer.
    return len(lowered) < 700 and any(prompt in lowered for prompt in prompts)


def open_for_login(config, url: str) -> None:
    """Open a visible browser in Jarvis' profile so the user can sign in."""
    sync_playwright = _require_playwright()
    profile = profile_dir(config)
    profile.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        options: dict = {"headless": False, "viewport": {"width": 1280, "height": 900}}
        executable = _executable(config)
        if executable:
            options["executable_path"] = executable
        context = playwright.chromium.launch_persistent_context(str(profile), **options)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=LOAD_TIMEOUT_MS)
        print("\n  Melde dich im Fenster an. Schließe es, wenn du fertig bist.")
        print("  Die Sitzung bleibt in Jarvis' eigenem Browser-Profil gespeichert.\n")
        try:
            # Block until the user closes the window.
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        finally:
            try:
                context.close()
            except Exception:
                pass

"""Rendering pages that a plain fetch cannot read.

One test drives a real browser against a page whose content only exists after
JavaScript runs -- which is the entire reason this module exists, and not
something a mock can demonstrate.
"""

from __future__ import annotations

import pytest

from jarvis.tools.page_render import _looks_like_sign_in


# -- telling a login wall from a page that mentions logging in ---------------


@pytest.mark.parametrize(
    "url",
    [
        "https://accounts.google.com/signin/v2",
        "https://login.microsoftonline.com/common",
        "https://example.com/login",
        "https://example.com/signin?next=/studio",
    ],
)
def test_a_sign_in_url_is_recognised(url):
    assert _looks_like_sign_in("irgendein Text", url) is True


def test_a_short_page_asking_for_a_password_is_a_login_wall():
    assert _looks_like_sign_in("Bitte anmelden\nPasswort vergessen?", "https://x.de/a") is True


def test_a_long_page_with_a_login_link_in_the_footer_is_not():
    """The common false positive: a real page that merely links to a login."""
    article = (
        "Analytics für Ihren Kanal. " * 60
        + "\nImpressum · Datenschutz · Anmelden"
    )
    assert _looks_like_sign_in(article, "https://studio.youtube.com/channel/UC123/analytics") is False


# -- the real thing ----------------------------------------------------------


@pytest.fixture
def chromium_available(monkeypatch):
    """Locate a Chromium to drive, or skip.

    Playwright pins a browser revision; a machine may have a different build,
    which is why the path is configurable at all.
    """
    pytest.importorskip("playwright.sync_api")

    import glob
    import os
    import shutil

    explicit = os.environ.get("JARVIS_CHROMIUM")
    if not explicit:
        candidates = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
        explicit = candidates[-1] if candidates else shutil.which("chromium") or ""
    if not explicit or not os.path.exists(explicit):
        pytest.skip("no chromium available to drive")
    monkeypatch.setenv("JARVIS_CHROMIUM", explicit)
    return explicit


def test_javascript_built_content_is_read(chromium_available, config, tmp_path):
    """The point of the module: numbers that exist only after scripts run.

    A plain fetch of this page returns an empty div. YouTube Studio behaves
    the same way, which is why scraping its HTML would return nothing useful.
    """
    from jarvis.tools.page_render import render_text

    page = tmp_path / "dashboard.html"
    page.write_text(
        """<!DOCTYPE html><html><head><title>Kanal-Analyse</title></head>
        <body><div id="figures">lädt…</div>
        <script>
          setTimeout(() => {
            document.getElementById("figures").innerText =
              "Aufrufe: 12.345 | Wiedergabezeit: 803 Stunden | Abonnenten: +157";
          }, 150);
        </script></body></html>""",
        encoding="utf-8",
    )

    config.home = tmp_path / "profile-home"
    text = render_text(config, page.as_uri(), timeout_ms=15000)

    assert "Kanal-Analyse" in text        # the title
    assert "12.345" in text               # written by the script, not in the source
    assert "Abonnenten: +157" in text
    assert "lädt…" not in text            # the placeholder it replaced


def test_hidden_markup_stays_out_of_the_summary(chromium_available, config, tmp_path):
    """innerText, not textContent: what is invisible should not be read aloud."""
    from jarvis.tools.page_render import render_text

    page = tmp_path / "hidden.html"
    page.write_text(
        """<!DOCTYPE html><html><head><title>T</title></head><body>
        <p>Sichtbarer Absatz.</p>
        <div style="display:none">Verstecktes Menü mit vierzig Einträgen</div>
        <script>var x = "Skript-Inhalt";</script>
        </body></html>""",
        encoding="utf-8",
    )

    config.home = tmp_path / "profile-home"
    text = render_text(config, page.as_uri(), timeout_ms=15000)

    assert "Sichtbarer Absatz." in text
    assert "Verstecktes Menü" not in text
    assert "Skript-Inhalt" not in text


def test_the_profile_is_jarvis_own_not_the_users(config, tmp_path):
    """Sign-in state belongs in Jarvis' directory, not in the user's browser."""
    from jarvis.tools.page_render import profile_dir

    config.home = tmp_path / "home"
    assert profile_dir(config) == tmp_path / "home" / "browser-profile"


# -- looking like a browser, because it is one -------------------------------


def test_the_automation_flag_is_not_advertised(config):
    """Sign-in pages refuse browsers that announce themselves as automated.

    The user types their own password into this window; it being driven by a
    script to open is not a reason for Google to treat it as a bot.
    """
    from jarvis.tools.page_render import STEALTH_ARGS, _launch_options

    options = _launch_options(config, headless=False, size=(1280, 900))

    assert "--enable-automation" in options["ignore_default_args"]
    assert "--disable-blink-features=AutomationControlled" in options["args"]
    assert STEALTH_ARGS  # and the rest of them are passed through


def test_an_installed_browser_is_preferred_over_the_bundled_one(config, monkeypatch):
    """A real Chrome is far likelier to be allowed to sign in."""
    from jarvis.tools.page_render import _open_context

    tried: list[str] = []

    class FakePlaywright:
        class chromium:
            @staticmethod
            def launch_persistent_context(profile, channel=None, **kw):
                tried.append(channel or "bundled")
                if channel == "chrome":
                    raise RuntimeError("not installed")
                return "context"

    result = _open_context(FakePlaywright, __import__("pathlib").Path("/tmp/p"), {"headless": True})

    assert tried[0] == "chrome"       # asked for a real Chrome first
    assert tried[1] == "msedge"       # then Edge
    assert result == "context"


def test_an_explicit_executable_skips_the_channel_search(config, monkeypatch):
    from jarvis.tools.page_render import _open_context

    tried: list[str] = []

    class FakePlaywright:
        class chromium:
            @staticmethod
            def launch_persistent_context(profile, channel=None, **kw):
                tried.append(channel or "bundled")
                return "context"

    _open_context(
        FakePlaywright,
        __import__("pathlib").Path("/tmp/p"),
        {"headless": True, "executable_path": "/opt/chrome"},
    )
    assert tried == ["bundled"]

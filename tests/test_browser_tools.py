"""Opening and reading pages.

The fetch tool is the one place where a URL chosen by the model reaches the
network, and the model reads mail and web pages -- so what it refuses to fetch
matters more than what it fetches.
"""

from __future__ import annotations

import pytest

from jarvis.tools.base import ToolError
from jarvis.tools.browser_tools import check_public, strip_html, to_url


# -- turning speech into an address ------------------------------------------


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("youtube", "https://www.youtube.com"),
        ("YouTube", "https://www.youtube.com"),
        ("www.spiegel.de", "https://www.spiegel.de"),
        ("https://example.com/x", "https://example.com/x"),
        ("example.com", "https://example.com"),
    ],
)
def test_names_and_addresses_become_urls(said, expected):
    assert to_url(said) == expected


def test_anything_else_becomes_a_search():
    assert to_url("Wetter in Berlin").startswith("https://duckduckgo.com/?q=Wetter")


def test_an_empty_target_is_refused():
    with pytest.raises(ToolError):
        to_url("   ")


# -- what must not be fetched ------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "http://localhost:8931/api/state",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://192.168.1.1/admin",
        "http://10.0.0.5/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
    ],
)
def test_local_and_private_addresses_are_refused(address):
    """Prompt injection is the threat here, not user error.

    An email or web page can tell the model to fetch a URL. Jarvis' own
    interface server listens on localhost, and so does everything else on the
    machine -- so a fetch tool that will visit any address turns a stranger's
    text into a request from inside the network.
    """
    with pytest.raises(ToolError) as caught:
        check_public(address)
    assert "local address" in str(caught.value) or "resolved" in str(caught.value)


@pytest.mark.parametrize("address", ["file:///etc/passwd", "ftp://example.com/x", "gopher://x/"])
def test_only_http_is_fetched(address):
    with pytest.raises(ToolError) as caught:
        check_public(address)
    assert "http and https" in str(caught.value)


def test_a_public_address_passes():
    assert check_public("https://example.com/page") == "https://example.com/page"


def test_an_unresolvable_host_is_reported_not_fetched():
    with pytest.raises(ToolError) as caught:
        check_public("https://this-host-does-not-exist.invalid/")
    assert "could not be resolved" in str(caught.value)


# -- reading a page ----------------------------------------------------------

def test_scripts_and_styles_do_not_end_up_in_the_text():
    markup = """
    <html><head><title>T</title><style>body{color:red}</style>
    <script>var x = "nicht vorlesen";</script></head>
    <body><h1>Überschrift</h1><p>Erster Absatz.</p>
    <ul><li>Punkt eins</li><li>Punkt zwei</li></ul>
    <p>Zweiter &amp; letzter.</p></body></html>
    """
    text = strip_html(markup)
    assert "nicht vorlesen" not in text
    assert "color:red" not in text
    assert "Überschrift" in text
    assert "Zweiter & letzter." in text
    # Block elements keep the document's shape rather than running together.
    assert "Punkt eins" in text and "Punkt zwei" in text


def test_entities_are_decoded():
    assert strip_html("<p>Caf&eacute; &amp; Bar &mdash; 17&nbsp;Uhr</p>") == "Café & Bar — 17 Uhr"

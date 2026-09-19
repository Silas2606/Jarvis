"""Opening pages, and reading them.

Two small powers, one real hazard. The URL a tool is asked to fetch is chosen
by the model, and the model reads mail, calendar entries and web pages -- any
of which can contain text engineered to steer it. A fetch tool that will visit
any address is therefore a way for a stranger's email to reach services that
trust the local machine, including Jarvis' own interface server.

So: public internet addresses only. Loopback, private ranges and link-local
addresses are refused, and the check runs against the resolved IP rather than
the hostname, because a name can point anywhere.
"""

from __future__ import annotations

import html
import ipaddress
import re
import socket
import urllib.parse
import urllib.request
import webbrowser

from jarvis.tools.base import Tool, ToolError, tool

# Enough of a page to answer a question about it, without burying a spoken
# reply under a thousand words of navigation.
PAGE_LIMIT = 6000
FETCH_TIMEOUT = 20

# A browser-ish agent: some sites serve nothing useful to unknown clients.
USER_AGENT = "Mozilla/5.0 (compatible; Jarvis/1.0; personal assistant)"

SEARCH_URL = "https://duckduckgo.com/?q={}"

# Sites people name rather than spell out.
SHORTCUTS = {
    "youtube": "https://www.youtube.com",
    "github": "https://github.com",
    "gmail": "https://mail.google.com",
    "outlook": "https://outlook.office.com/mail/",
    "kalender": "https://calendar.google.com",
    "calendar": "https://calendar.google.com",
    "wikipedia": "https://de.wikipedia.org",
    "amazon": "https://www.amazon.de",
    "maps": "https://www.google.com/maps",
    "drive": "https://drive.google.com",
    "to-do": "https://to-do.office.com",
    "todo": "https://to-do.office.com",
}


def _looks_like_url(text: str) -> bool:
    return bool(re.match(r"^(https?://|www\.)", text.strip(), re.IGNORECASE)) or bool(
        re.match(r"^[\w-]+(\.[\w-]+)+(/|$)", text.strip())
    )


def to_url(target: str) -> str:
    """Turn what was said into something a browser can open."""
    target = target.strip()
    if not target:
        raise ToolError("No page was given.")

    shortcut = SHORTCUTS.get(target.lower())
    if shortcut:
        return shortcut

    if _looks_like_url(target):
        if not target.lower().startswith(("http://", "https://")):
            return f"https://{target}"
        return target

    # Not an address: search for it.
    return SEARCH_URL.format(urllib.parse.quote_plus(target))


def check_public(url: str) -> str:
    """Refuse anything that is not a public internet address.

    Resolves the host first: a name under someone else's control can point at
    127.0.0.1 just as easily as `localhost` does.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ToolError(f"Only http and https can be fetched, not {parsed.scheme!r}.")
    if not parsed.hostname:
        raise ToolError("That address has no host.")

    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 0, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ToolError(f"The address {parsed.hostname!r} could not be resolved: {exc}") from exc

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
        ):
            raise ToolError(
                f"{parsed.hostname} resolves to a local address ({address}), which is "
                "not something I will fetch."
            )
    return url


def strip_html(markup: str) -> str:
    """A readable rendering of a page, without a parser dependency."""
    text = re.sub(r"<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", markup, flags=re.S | re.I)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    # Keep the shape of the document: block elements become line breaks.
    text = re.sub(r"</(p|div|li|tr|h[1-6]|section|article|br)\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


@tool
def open_page(ctx, target: str) -> str:
    """Open a page in the user's browser.

    Use this when the user asks to see something rather than be told it: a
    site by name ("open YouTube"), a full address, or a search term, which
    opens a search for it.

    Args:
        target: A site name, a web address, or something to search for.
    """
    url = to_url(target)
    try:
        opened = webbrowser.open(url)
    except Exception as exc:
        raise ToolError(f"The browser could not be opened: {exc}") from exc
    if not opened:
        raise ToolError("No browser could be opened on this machine.")
    return f"Opened {url} in the browser."


@tool
def read_page(ctx, url: str, rendered: bool = False, wait_for: str = "") -> str:
    """Fetch a web page and return its text, to answer questions about it.

    For YouTube channel figures, prefer the youtube_ tools: they return the
    same numbers as structured data, which reads aloud far better than a
    rendered dashboard.

    Args:
        url: The full address of the page to read.
        rendered: Load the page in a real browser first. Needed for anything
            behind a sign-in, and for applications that build their content
            with JavaScript -- dashboards, analytics, web mail. A plain fetch
            returns an empty shell for those.
        wait_for: A CSS selector to wait for before reading, when the
            interesting part of the page arrives after the rest.
    """
    target = to_url(url)
    check_public(target)

    if rendered:
        from jarvis.tools.page_render import render_text

        text = render_text(ctx.config, target, wait_for=wait_for)
        if len(text) > PAGE_LIMIT:
            text = text[:PAGE_LIMIT] + "\n[…gekürzt]"
        return text

    request = urllib.request.Request(target, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
            kind = response.headers.get("Content-Type", "")
            if "html" not in kind and "text" not in kind:
                raise ToolError(f"That is not a readable page ({kind or 'unknown type'}).")
            raw = response.read(2_000_000)
            charset = response.headers.get_content_charset() or "utf-8"
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"{target} could not be fetched: {exc}") from exc

    text = strip_html(raw.decode(charset, errors="replace"))
    if not text:
        return f"{target} has no readable text."
    if len(text) > PAGE_LIMIT:
        text = text[:PAGE_LIMIT] + "\n[…gekürzt]"
    return f"{target}\n\n{text}"


BROWSER_TOOLS: list[Tool] = [open_page, read_page]

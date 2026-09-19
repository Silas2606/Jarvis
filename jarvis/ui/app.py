"""Opening the interface as its own window.

Two ways, tried in order. `pywebview` gives a real application window with no
browser chrome around it -- which is the point of "run it as a program". If it
is not installed, the same interface opens in the default browser, which needs
nothing at all.
"""

from __future__ import annotations

import threading
import webbrowser
from typing import Callable

WINDOW_TITLE = "J.A.R.V.I.S."
WINDOW_SIZE = (1280, 800)
MINIMUM_SIZE = (900, 620)


def have_native_window() -> bool:
    try:
        import webview  # noqa: F401

        return True
    except ImportError:
        return False


def open_window(url: str, on_close: Callable[[], None] | None = None) -> str:
    """Show the interface. Blocks until the window closes.

    Returns which route was taken: "native" or "browser".
    """
    try:
        import webview
    except ImportError:
        webbrowser.open(url)
        return "browser"

    window = webview.create_window(
        WINDOW_TITLE,
        url,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=MINIMUM_SIZE,
        background_color="#04070b",
        text_select=True,
    )
    if on_close is not None:
        window.events.closed += on_close

    # Blocks on the main thread until the window is closed -- which is why
    # everything else in the process runs on threads of its own.
    webview.start()
    return "native"


def wait_for_interrupt(stop: threading.Event) -> None:
    """Used in browser mode: nothing to block on, so wait for Ctrl-C."""
    try:
        while not stop.wait(0.5):
            pass
    except KeyboardInterrupt:
        stop.set()

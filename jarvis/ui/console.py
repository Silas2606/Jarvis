"""The heads-up display: what Jarvis is hearing, thinking and doing.

Uses ``rich`` when it is installed and degrades to plain ANSI when it is not,
so the assistant is never unusable because a formatting library is missing.
"""

from __future__ import annotations

import sys
import threading
from datetime import datetime

from jarvis.events import Event, EventBus, EventKind

try:  # pragma: no cover - presentation only
    from rich.console import Console as RichConsole

    _HAVE_RICH = True
except ImportError:  # pragma: no cover
    RichConsole = None
    _HAVE_RICH = False

# Logical style names, mapped once per backend. Rich has no plain "grey".
_RICH_STYLES = {
    "grey": "grey50",
    "bold": "bold",
    "bold cyan": "bold cyan",
    "cyan": "cyan",
    "blue": "steel_blue1",
    "green": "green",
    "yellow": "yellow",
    "red": "red",
}

ANSI = {
    "reset": "\033[0m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "bold cyan": "\033[1;36m",
    "cyan": "\033[36m",
    "blue": "\033[34m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "grey": "\033[90m",
}

BANNER = """
  ╔════════════════════════════════════════════╗
  ║      J · A · R · V · I · S                 ║
  ║      Just A Rather Very Intelligent System ║
  ╚════════════════════════════════════════════╝"""


class Console:
    """Prints events. One line per thing that happens, quietly formatted."""

    def __init__(self, bus: EventBus, address: str = "Sir", verbose: bool = False):
        self.bus = bus
        self.address = address
        self.verbose = verbose
        self._lock = threading.Lock()
        self._console = RichConsole() if _HAVE_RICH else None
        self._thinking_shown = False
        bus.subscribe(self.handle)

    # -- primitives ----------------------------------------------------------

    def _write(self, text: str, style: str = "") -> None:
        with self._lock:
            if self._console is not None:
                self._console.print(
                    text, style=_RICH_STYLES.get(style, style) or None, highlight=False
                )
            else:
                colour = ANSI.get(style, "")
                sys.stdout.write(f"{colour}{text}{ANSI['reset']}\n")
                sys.stdout.flush()

    def banner(self, model: str, voice: str, ears: str, tools: int) -> None:
        self._write(BANNER, "cyan")
        self._write(f"  Good to see you, {self.address}.", "bold cyan")
        self._write(
            f"  brain {model}   ears {ears}   voice {voice}   tools {tools}",
            "grey",
        )
        self._write("")

    def rule(self) -> None:
        with self._lock:
            if self._console is not None:
                self._console.rule(style="grey37")
            else:
                sys.stdout.write(f"{ANSI['grey']}{'-' * 60}{ANSI['reset']}\n")

    def notice(self, text: str, style: str = "grey") -> None:
        self._write(f"  {text}", style)

    # -- event handling ------------------------------------------------------

    def handle(self, event: Event) -> None:
        kind = event.kind
        stamp = datetime.now().strftime("%H:%M")

        if kind is EventKind.LISTENING:
            if event.text:
                self._write(f"  {stamp}  ◉ {event.text}", "grey")

        elif kind is EventKind.WAKE:
            self._write(f"  {stamp}  ◉ listening…", "cyan")

        elif kind is EventKind.HEARD:
            self._write(f"\n  {self.address}: {event.text}", "bold")

        elif kind is EventKind.THINKING:
            if self.verbose:
                self._write("  …thinking", "grey")

        elif kind is EventKind.REASONING:
            if self.verbose:
                self._write(f"  {event.text}", "grey")

        elif kind is EventKind.TOOL_START:
            arguments = event.data.get("arguments", {})
            rendered = ", ".join(f"{k}={_short(v)}" for k, v in list(arguments.items())[:3])
            self._write(f"  ⚙ {event.text}({rendered})", "blue")

        elif kind is EventKind.TOOL_END:
            if event.data.get("failed"):
                self._write(f"    ✗ {_short(event.data.get('result', ''), 160)}", "red")
            elif self.verbose:
                self._write(f"    → {_short(event.data.get('result', ''), 160)}", "grey")

        elif kind is EventKind.CONFIRM:
            self._write(f"  ❓ {event.text}", "yellow")

        elif kind is EventKind.ANSWER:
            self._write(f"  JARVIS: {event.text}\n", "cyan")

        elif kind is EventKind.REMINDER:
            self._write(f"\n  ⏰ {event.text}\n", "yellow")

        elif kind is EventKind.NOTICE:
            if event.text and self.verbose:
                self._write(f"  {event.text}", "grey")

        elif kind is EventKind.ERROR:
            self._write(f"  ✗ {event.text}", "red")


def _short(value: object, limit: int = 60) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def attach_console(bus: EventBus, address: str = "Sir", verbose: bool = False) -> Console:
    return Console(bus, address, verbose)

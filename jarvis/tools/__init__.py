"""Assembling the set of tools Jarvis is given for a session."""

from __future__ import annotations

from typing import Any

from jarvis.tools.base import (
    ConfirmationDenied,
    Tool,
    ToolContext,
    ToolError,
    ToolRegistry,
    tool,
)

# Research runs on Anthropic's own server-side tools: Claude searches and reads
# the web itself, so there is no separate search API to key and pay for. The
# dated type strings are the current variants with dynamic filtering, which
# Opus 5 supports.
WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 8,
}

WEB_FETCH_TOOL: dict[str, Any] = {
    "type": "web_fetch_20260209",
    "name": "web_fetch",
    "max_uses": 8,
    "max_content_tokens": 20000,
}


def build_registry(config, store, bus=None, confirm=None) -> ToolRegistry:
    """Collect every tool the configuration allows.

    Args:
        config: The active Jarvis configuration.
        store: The memory store tools read and write.
        bus: Event bus, for tools that want to announce themselves.
        confirm: Callable asking the user a yes/no question out loud.
    """
    from jarvis.tools.browser_tools import BROWSER_TOOLS
    from jarvis.tools.calendar_tools import CALENDAR_TOOLS
    from jarvis.tools.mail_tools import MAIL_TOOLS
    from jarvis.tools.memory_tools import (
        FACT_TOOLS,
        NOTE_TOOLS,
        REMINDER_TOOLS,
        TASK_TOOLS,
    )
    from jarvis.tools.time_tools import TIME_TOOLS

    context = ToolContext(config=config, store=store, bus=bus, confirm=confirm)
    registry = ToolRegistry(context=context)

    registry.register(*TIME_TOOLS)
    if config.tools.memory:
        registry.register(*NOTE_TOOLS, *FACT_TOOLS)
        # Tasks come from one backend or the other, never both: two sets of
        # tools with the same names would be ambiguous, and two lists that
        # disagree are worse than one that is merely elsewhere.
        if config.tools.tasks_backend == "microsoft":
            from jarvis.tools.microsoft_tools import MICROSOFT_TASK_TOOLS

            registry.register(*MICROSOFT_TASK_TOOLS)
        else:
            registry.register(*TASK_TOOLS)
    if config.tools.reminders:
        registry.register(*REMINDER_TOOLS)
    if config.tools.calendar:
        registry.register(*CALENDAR_TOOLS)
    if config.tools.mail:
        registry.register(*MAIL_TOOLS)
    if getattr(config.tools, "browser", True):
        registry.register(*BROWSER_TOOLS)

    if config.brain.web_access:
        registry.register_server_tool(WEB_SEARCH_TOOL)
        registry.register_server_tool(WEB_FETCH_TOOL)

    # When outbound confirmation is switched off, the user has said once and
    # for all that Jarvis may act without asking each time.
    if not config.tools.confirm_outbound:
        for item in registry:
            item.confirm = None

    return registry


__all__ = [
    "ConfirmationDenied",
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolRegistry",
    "build_registry",
    "tool",
    "WEB_SEARCH_TOOL",
    "WEB_FETCH_TOOL",
]

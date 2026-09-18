"""The tool plumbing: declare a Python function, get a Claude tool.

A tool is an ordinary function with type hints and a Google-style docstring.
The decorator turns that into the JSON schema the Messages API expects, so the
schema can never drift away from the implementation.

Tools that reach outside the house -- sending mail, deleting an appointment --
declare ``confirm=``. The registry then asks the user, out loud, before the
function body ever runs.
"""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, get_args, get_origin, get_type_hints

# Parameter name that receives the ToolContext instead of model input.
CONTEXT_PARAM = "ctx"

_JSON_TYPES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


class ToolError(Exception):
    """Raised inside a tool to report a clean failure back to Claude."""


class ConfirmationDenied(Exception):
    """The user said no to a confirmation prompt."""


@dataclass
class ToolContext:
    """Everything a tool may need, handed in as the ``ctx`` parameter."""

    config: Any = None
    store: Any = None
    bus: Any = None
    # Asks the user a yes/no question. Returns True when they agreed.
    confirm: Callable[[str], bool] | None = None

    def ask(self, question: str) -> bool:
        if self.confirm is None:
            # Without a way to ask, refuse rather than act unilaterally.
            return False
        return bool(self.confirm(question))


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., Any]
    confirm: str | None = None
    wants_context: bool = False

    def schema(self) -> dict[str, Any]:
        """The tool definition sent to the Messages API."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.func(*args, **kwargs)


def _docstring_parts(doc: str) -> tuple[str, dict[str, str]]:
    """Split a Google-style docstring into a summary and per-argument help."""
    if not doc:
        return "", {}
    text = inspect.cleandoc(doc)
    match = re.split(r"\n\s*(?:Args|Arguments|Parameters):\s*\n", text, maxsplit=1)
    summary = match[0].strip()
    params: dict[str, str] = {}
    if len(match) > 1:
        body = re.split(r"\n\s*(?:Returns|Raises|Examples?|Note):\s*\n", match[1])[0]
        current: str | None = None
        for line in body.splitlines():
            entry = re.match(r"\s*([*\w]+)\s*(?:\([^)]*\))?\s*:\s*(.*)", line)
            if entry:
                current = entry.group(1).lstrip("*")
                params[current] = entry.group(2).strip()
            elif current and line.strip():
                params[current] = f"{params[current]} {line.strip()}".strip()
    return summary, params


def _json_type(annotation: Any) -> dict[str, Any]:
    """Map a Python annotation onto a JSON-schema fragment."""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {"type": "string"}
    if annotation in _JSON_TYPES:
        return {"type": _JSON_TYPES[annotation]}

    origin = get_origin(annotation)
    args = get_args(annotation)

    # Optional[X] / X | None -> the schema of X; optionality is carried by
    # leaving the field out of "required".
    if origin is not None and type(None) in args:
        remaining = [a for a in args if a is not type(None)]
        if len(remaining) == 1:
            return _json_type(remaining[0])
        return {"type": "string"}

    if origin in (list, tuple, set):
        item = args[0] if args else str
        return {"type": "array", "items": _json_type(item)}
    if origin is dict:
        return {"type": "object"}

    # A Literal["a", "b"] becomes an enum -- the tidiest way to constrain input.
    if str(origin) == "typing.Literal" or getattr(annotation, "__origin__", None) is not None and args and all(isinstance(a, str) for a in args):
        if all(isinstance(a, str) for a in args) and args:
            return {"type": "string", "enum": list(args)}
    return {"type": "string"}


def build_schema(func: Callable[..., Any]) -> tuple[str, dict[str, Any], bool]:
    """Derive (description, input_schema, wants_context) from a function."""
    signature = inspect.signature(func)
    try:
        hints = get_type_hints(func)
    except Exception:  # pragma: no cover - exotic annotations
        hints = {}
    summary, docs = _docstring_parts(func.__doc__ or "")

    properties: dict[str, Any] = {}
    required: list[str] = []
    wants_context = False

    for name, parameter in signature.parameters.items():
        if name == CONTEXT_PARAM:
            wants_context = True
            continue
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            continue
        fragment = _json_type(hints.get(name, parameter.annotation))
        if name in docs:
            fragment["description"] = docs[name]
        properties[name] = fragment
        if parameter.default is inspect.Parameter.empty:
            required.append(name)

    schema = {
        "type": "object",
        "properties": properties,
        "required": required,
    }
    return summary, schema, wants_context


def tool(_func: Callable[..., Any] | None = None, *, name: str = "", confirm: str | None = None):
    """Turn a function into a :class:`Tool`.

    Args:
        name: Override the tool name; defaults to the function name.
        confirm: A question template. When set, the user is asked before the
            tool runs. ``{placeholders}`` are filled from the tool arguments.
    """

    def decorate(func: Callable[..., Any]) -> Tool:
        description, schema, wants_context = build_schema(func)
        return Tool(
            name=name or func.__name__,
            description=description,
            parameters=schema,
            func=func,
            confirm=confirm,
            wants_context=wants_context,
        )

    if _func is not None:
        return decorate(_func)
    return decorate


def _render_confirmation(template: str, arguments: dict[str, Any]) -> str:
    """Fill a confirmation template, tolerating missing placeholders."""
    class _Safe(dict):
        def __missing__(self, key: str) -> str:
            return "..."

    try:
        return template.format_map(_Safe(arguments))
    except Exception:  # pragma: no cover - malformed template
        return template


@dataclass
class ToolRegistry:
    """The set of tools Jarvis can reach for, plus the dispatcher."""

    tools: dict[str, Tool] = field(default_factory=dict)
    # Server-side tool definitions (web search, web fetch) passed straight
    # through to the API -- Anthropic runs these, we never execute them.
    server_tools: list[dict[str, Any]] = field(default_factory=list)
    context: ToolContext = field(default_factory=ToolContext)

    def register(self, *items: Tool) -> "ToolRegistry":
        for item in items:
            self.tools[item.name] = item
        return self

    def register_server_tool(self, definition: dict[str, Any]) -> "ToolRegistry":
        self.server_tools.append(definition)
        return self

    def __contains__(self, name: object) -> bool:
        return name in self.tools

    def __len__(self) -> int:
        return len(self.tools)

    def __iter__(self) -> Iterator[Tool]:
        return iter(self.tools.values())

    def schemas(self) -> list[dict[str, Any]]:
        """Tool definitions for the API, client tools first, order stable.

        A stable order matters: the tool block is part of the cached prompt
        prefix, and reshuffling it would invalidate the cache every turn.
        """
        client = [t.schema() for t in sorted(self.tools.values(), key=lambda t: t.name)]
        return client + list(self.server_tools)

    def call(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Run a tool. Returns ``(result_text, is_error)``."""
        target = self.tools.get(name)
        if target is None:
            return f"No such tool: {name}", True

        if target.confirm:
            question = _render_confirmation(target.confirm, arguments)
            if not self.context.ask(question):
                return "The user declined. The action was not carried out.", False

        kwargs = dict(arguments)
        if target.wants_context:
            kwargs[CONTEXT_PARAM] = self.context

        try:
            result = target.func(**kwargs)
        except ConfirmationDenied:
            return "The user declined. The action was not carried out.", False
        except ToolError as exc:
            return str(exc), True
        except TypeError as exc:
            return f"Invalid arguments for {name}: {exc}", True
        except Exception as exc:  # pragma: no cover - surfaced to the model
            return f"{type(exc).__name__}: {exc}", True

        return _stringify(result), False


def _stringify(result: Any) -> str:
    if result is None:
        return "Done."
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str, indent=None)
    except Exception:  # pragma: no cover
        return str(result)

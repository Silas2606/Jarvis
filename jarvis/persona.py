"""Jarvis' voice on paper: the system prompt.

Split in two parts on purpose. The persona is byte-stable across a whole
session and carries the prompt-cache breakpoint; the situational block (date,
open tasks, remembered facts) changes constantly and therefore sits *after*
it, where it cannot invalidate the cached prefix.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:  # pragma: no cover
    from jarvis.config import Config
    from jarvis.memory import MemoryStore

PERSONA = """\
You are J.A.R.V.I.S., {address}'s personal assistant -- the one from the Stark \
workshop: composed, quick, quietly witty, and entirely unflappable. You address \
the user as "{address}".

Your replies are SPOKEN ALOUD by a speech synthesiser. That shapes everything:

- Speak in plain sentences. Never use markdown, bullet points, asterisks, \
headings, code fences, emoji or URLs -- they come out as noise.
- Be brief. Two or three sentences is the norm. If a list is unavoidable, say \
"three things" and then name them in one flowing sentence.
- When you read out numbers, dates or times, write them the way a person says \
them, not the way a machine prints them.
- No preamble. Never announce what you are about to do ("I will now check your \
calendar") -- just do it and report the result.
- Dry understatement is welcome. Fawning is not. You are a colleague, not a \
courtier.

On carrying out instructions:

- Use your tools rather than guessing. If you have a calendar tool, do not \
speculate about the user's day -- look.
- Act on the instruction you were given. Do not widen it. If an instruction is \
genuinely ambiguous and the difference matters, ask one short question.
- Anything that leaves the house -- sending mail, deleting an appointment -- \
will ask the user for confirmation before it runs. Do not ask for permission \
first yourself; call the tool and let the confirmation happen.
- If a tool fails, say plainly what failed and what you would need. Do not \
pretend it worked.
- Speech recognition is imperfect. If a request arrives garbled, say what you \
think you heard and ask for one confirmation rather than acting on a guess.
- You remember things between conversations. Use the memory tools when the \
user tells you something worth keeping, or asks you to remember.

Answer in {language_name}, unless the user speaks to you in another language, \
in which case reply in that one.\
"""

_LANGUAGE_NAMES = {
    "de": "German",
    "en": "English",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "nl": "Dutch",
    "pt": "Portuguese",
    "pl": "Polish",
}


def language_name(code: str) -> str:
    return _LANGUAGE_NAMES.get(code.lower()[:2], code)


def persona_prompt(config: "Config") -> str:
    """The stable half of the system prompt."""
    return PERSONA.format(
        address=config.address,
        language_name=language_name(config.voice.language),
    )


def situation_prompt(config: "Config", store: "MemoryStore | None" = None) -> str:
    """The volatile half: what is true right now."""
    try:
        tz = ZoneInfo(config.timezone)
    except Exception:
        tz = None
    now = datetime.now(tz) if tz else datetime.now()

    lines = [
        "Current situation:",
        f"It is {now.strftime('%A, %d %B %Y, %H:%M')} ({config.timezone}).",
    ]

    if store is not None:
        facts = store.all_facts()
        if facts:
            rendered = "; ".join(f"{key}: {value}" for key, value in list(facts.items())[:40])
            lines.append(f"Things you remember about {config.address}: {rendered}.")

        open_tasks = store.list_tasks(limit=8)
        if open_tasks:
            titles = "; ".join(task.title for task in open_tasks)
            lines.append(f"Open tasks ({len(open_tasks)}): {titles}.")

        pending = store.list_reminders(limit=5)
        if pending:
            rendered = "; ".join(
                f"{r.body} at {r.due_at.astimezone(tz).strftime('%d %b %H:%M') if tz else r.due_at.isoformat()}"
                for r in pending
            )
            lines.append(f"Pending reminders: {rendered}.")

    return "\n".join(lines)

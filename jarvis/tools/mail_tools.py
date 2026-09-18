"""Gmail, as tools Jarvis can use in conversation.

Reading is unrestricted; anything that leaves the house -- sending, replying --
carries a ``confirm=`` template, so the user is asked out loud before it goes.
"""

from __future__ import annotations

import base64
import re
from email.message import EmailMessage

from jarvis.tools.base import Tool, ToolError, tool
from jarvis.tools.google_auth import GoogleUnavailable, get_service

# How much of a mail body to hand to Claude. Enough to summarise, not enough to
# drown a spoken answer in quoted threads.
_BODY_LIMIT = 4000


def _gmail(ctx):
    try:
        return get_service(ctx.config, "gmail", "v1")
    except GoogleUnavailable as exc:
        raise ToolError(str(exc)) from exc


def _headers(message: dict) -> dict[str, str]:
    return {
        header.get("name", "").lower(): header.get("value", "")
        for header in message.get("payload", {}).get("headers", [])
    }


def _decode(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _plain_text(payload: dict) -> str:
    """Pull readable text out of a MIME tree, preferring text/plain."""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})

    if mime == "text/plain" and body.get("data"):
        return _decode(body["data"])

    if mime.startswith("multipart/"):
        parts = payload.get("parts", [])
        for part in parts:  # a plain-text part anywhere beats HTML
            text = _plain_text(part)
            if text and part.get("mimeType") == "text/plain":
                return text
        for part in parts:
            text = _plain_text(part)
            if text:
                return text

    if mime == "text/html" and body.get("data"):
        html = _decode(body["data"])
        stripped = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
        stripped = re.sub(r"<br\s*/?>|</p>", "\n", stripped, flags=re.I)
        stripped = re.sub(r"<[^>]+>", " ", stripped)
        return re.sub(r"[ \t]{2,}", " ", stripped).strip()

    return ""


def _summarise(message: dict) -> str:
    headers = _headers(message)
    sender = headers.get("from", "unknown sender")
    subject = headers.get("subject", "(no subject)")
    date = headers.get("date", "")
    snippet = message.get("snippet", "").strip()
    unread = "UNREAD" in message.get("labelIds", [])
    flag = "unread" if unread else "read"
    return f"[{flag}] {date} -- from {sender} -- {subject} (id {message.get('id')}): {snippet}"


def _list(ctx, query: str, limit: int) -> str:
    service = _gmail(ctx)
    try:
        listing = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=min(max(limit, 1), 25))
            .execute()
        )
        ids = [item["id"] for item in listing.get("messages", [])]
        if not ids:
            return "No messages match."
        lines = []
        for message_id in ids:
            message = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
            lines.append(_summarise(message))
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"The mailbox could not be read: {exc}") from exc
    return f"{len(lines)} message(s):\n" + "\n".join(lines)


@tool
def mail_unread(ctx, limit: int = 10) -> str:
    """List unread messages in the inbox.

    Args:
        limit: Maximum number of messages to return.
    """
    return _list(ctx, "is:unread in:inbox", limit)


@tool
def mail_search(ctx, query: str, limit: int = 10) -> str:
    """Search the mailbox using Gmail's own search syntax.

    Args:
        query: A Gmail query, e.g. 'from:anna after:2026/09/01 has:attachment'.
        limit: Maximum number of messages to return.
    """
    return _list(ctx, query, limit)


@tool
def mail_read(ctx, message_id: str) -> str:
    """Read one message in full, to answer questions about it.

    Args:
        message_id: The id shown by mail_unread or mail_search.
    """
    service = _gmail(ctx)
    try:
        message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    except Exception as exc:
        raise ToolError(f"That message could not be read: {exc}") from exc

    headers = _headers(message)
    body = _plain_text(message.get("payload", {})).strip()
    if len(body) > _BODY_LIMIT:
        body = body[:_BODY_LIMIT] + "\n[...truncated]"

    return (
        f"From: {headers.get('from', '')}\n"
        f"To: {headers.get('to', '')}\n"
        f"Date: {headers.get('date', '')}\n"
        f"Subject: {headers.get('subject', '(no subject)')}\n"
        f"Thread: {message.get('threadId', '')}\n\n"
        f"{body or '(empty body)'}"
    )


def _build(to: list[str], subject: str, body: str, cc: list[str] | None = None) -> EmailMessage:
    mail = EmailMessage()
    mail["To"] = ", ".join(to)
    if cc:
        mail["Cc"] = ", ".join(cc)
    mail["Subject"] = subject
    mail.set_content(body)
    return mail


def _encode(mail: EmailMessage) -> dict:
    return {"raw": base64.urlsafe_b64encode(mail.as_bytes()).decode("utf-8")}


@tool
def mail_draft(ctx, to: list[str], subject: str, body: str, cc: list[str] = None) -> str:
    """Write an email and save it as a draft, without sending it.

    This is the safe option: the user can read it over before it goes.

    Args:
        to: Recipient email addresses.
        subject: Subject line.
        body: The full text of the message.
        cc: Addresses to copy in.
    """
    service = _gmail(ctx)
    mail = _build(to, subject, body, cc)
    try:
        draft = service.users().drafts().create(userId="me", body={"message": _encode(mail)}).execute()
    except Exception as exc:
        raise ToolError(f"The draft could not be saved: {exc}") from exc
    return f"Draft saved for {', '.join(to)} with subject {subject!r} (draft id {draft.get('id')})."


@tool(confirm="Shall I send the mail to {to} with the subject {subject}?")
def mail_send(ctx, to: list[str], subject: str, body: str, cc: list[str] = None) -> str:
    """Send an email. The user is asked to confirm out loud before it goes.

    Args:
        to: Recipient email addresses.
        subject: Subject line.
        body: The full text of the message.
        cc: Addresses to copy in.
    """
    service = _gmail(ctx)
    mail = _build(to, subject, body, cc)
    try:
        sent = service.users().messages().send(userId="me", body=_encode(mail)).execute()
    except Exception as exc:
        raise ToolError(f"The mail could not be sent: {exc}") from exc
    return f"Sent to {', '.join(to)} (message id {sent.get('id')})."


@tool(confirm="Shall I send the reply to message {message_id}?")
def mail_reply(ctx, message_id: str, body: str) -> str:
    """Reply to a message, keeping it in the same thread.

    Args:
        message_id: The id of the message being answered.
        body: The text of the reply.
    """
    service = _gmail(ctx)
    try:
        original = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="metadata", metadataHeaders=["From", "Subject", "Message-ID"])
            .execute()
        )
    except Exception as exc:
        raise ToolError(f"The message being answered could not be read: {exc}") from exc

    headers = _headers(original)
    subject = headers.get("subject", "")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    mail = _build([headers.get("from", "")], subject, body)
    if headers.get("message-id"):
        mail["In-Reply-To"] = headers["message-id"]
        mail["References"] = headers["message-id"]

    payload = _encode(mail)
    payload["threadId"] = original.get("threadId")
    try:
        sent = service.users().messages().send(userId="me", body=payload).execute()
    except Exception as exc:
        raise ToolError(f"The reply could not be sent: {exc}") from exc
    return f"Reply sent to {headers.get('from', '')} (message id {sent.get('id')})."


@tool
def mail_mark_read(ctx, message_id: str) -> str:
    """Mark a message as read.

    Args:
        message_id: The id of the message.
    """
    service = _gmail(ctx)
    try:
        service.users().messages().modify(
            userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}
        ).execute()
    except Exception as exc:
        raise ToolError(f"The message could not be marked as read: {exc}") from exc
    return f"Message {message_id} marked as read."


MAIL_TOOLS: list[Tool] = [
    mail_unread,
    mail_search,
    mail_read,
    mail_draft,
    mail_send,
    mail_reply,
    mail_mark_read,
]

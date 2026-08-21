"""A Mailbox backed by the Gmail API.

This is the second implementation of the Mailbox interface defined in
Milestone 6, alongside InMemoryMailbox. Nothing above it changes: EmailAgent,
the analyzers, the Router and the Manager all keep working against the same
``fetch(limit, label) -> list[EmailMessage]`` contract.

Read-only by construction. The class exposes no send, delete, modify, or label
methods, and the credentials it uses carry only the gmail.readonly scope - so
even a bug here cannot alter the mailbox.

The Gmail service object is injected, exactly as AnthropicProvider injects its
client. Every test in this file's suite runs against a fake, so the whole
project stays testable with no credentials, no network and no Google packages.
"""

from __future__ import annotations

import base64
import binascii
import html as html_module
import re
from datetime import datetime, timezone
from typing import Any

from src.services.email.mailbox import Mailbox, MailboxError
from src.services.email.message import EmailMessage, sort_newest_first
from src.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_USER_ID = "me"

# Gmail's own label ids are upper-case constants; user labels get opaque ids.
_SYSTEM_LABELS = frozenset(
    {"INBOX", "UNREAD", "STARRED", "IMPORTANT", "SENT", "DRAFT", "SPAM", "TRASH", "CATEGORY_PERSONAL",
     "CATEGORY_SOCIAL", "CATEGORY_PROMOTIONS", "CATEGORY_UPDATES", "CATEGORY_FORUMS"}
)

_SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_BLOCK_BREAK = re.compile(r"(?i)<(br\s*/?|/p|/div|/tr|/li|/h[1-6])\s*>")
_TAG = re.compile(r"<[^>]+>")
_BLANK_LINES = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+(?=\n)")


# --------------------------------------------------------------------------
# Pure parsing helpers - no service, no network, individually testable
# --------------------------------------------------------------------------


def decode_body_data(data: str) -> str:
    """Decode Gmail's base64url message payload.

    Gmail omits padding, so it is restored before decoding. Decoding is lenient
    about malformed bytes: a mangled body should degrade to partial text, not
    lose the whole message - the sender and subject are still useful.
    """
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError):
        return ""
    return raw.decode("utf-8", errors="replace")


def html_to_text(html: str) -> str:
    """Flatten HTML into readable plain text.

    Real job emails from Wellfound and similar platforms are HTML-only. Without
    this, every analyzer would be matching its keywords against markup.

    Deliberately regex-based rather than a parser dependency: the goal is
    readable text for keyword analysis, not faithful rendering.
    """
    if not html:
        return ""
    text = _SCRIPT_OR_STYLE.sub(" ", html)
    text = _BLOCK_BREAK.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = html_module.unescape(text)
    text = text.replace("\xa0", " ")
    # Collapse runs of spaces without destroying the line structure above.
    text = "\n".join(" ".join(line.split()) for line in text.splitlines())
    text = _TRAILING_SPACE.sub("", text)
    return _BLANK_LINES.sub("\n\n", text).strip()


def header(payload: dict[str, Any], name: str) -> str:
    """Read one header, case-insensitively. Missing headers yield ''."""
    wanted = name.lower()
    for entry in payload.get("headers", []) or []:
        if str(entry.get("name", "")).lower() == wanted:
            return str(entry.get("value", ""))
    return ""


def extract_body(payload: dict[str, Any]) -> str:
    """Pull readable text out of a Gmail payload.

    Prefers text/plain; falls back to flattened text/html. Walks nested parts,
    because multipart/alternative inside multipart/mixed is routine.
    """
    plain, html = _collect_parts(payload)
    if plain.strip():
        return plain.strip()
    return html_to_text(html)


def _collect_parts(payload: dict[str, Any]) -> tuple[str, str]:
    """Return (plain_text, html) gathered from a payload and its children."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime = str(part.get("mimeType", ""))
        data = (part.get("body") or {}).get("data", "")
        if data:
            decoded = decode_body_data(data)
            if mime == "text/plain":
                plain_parts.append(decoded)
            elif mime == "text/html":
                html_parts.append(decoded)
            elif not mime.startswith("multipart/"):
                # Unknown single-part type: keep it rather than lose the body.
                plain_parts.append(decoded)
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload or {})
    return "\n".join(plain_parts), "\n".join(html_parts)


def to_datetime(internal_date: Any) -> datetime:
    """Convert Gmail's internalDate (epoch milliseconds) to an aware UTC time.

    internalDate is used in preference to the Date header: the header is
    sender-supplied and frequently malformed, while internalDate is assigned by
    Gmail and always present.
    """
    try:
        millis = int(internal_date)
    except (TypeError, ValueError):
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)


def to_email_message(raw: dict[str, Any]) -> EmailMessage:
    """Convert one Gmail API message resource into an EmailMessage."""
    payload = raw.get("payload") or {}
    message_id = str(raw.get("id") or "").strip()
    if not message_id:
        raise MailboxError("Gmail returned a message with no id")

    sender = header(payload, "From").strip()
    if not sender:
        # EmailMessage requires a sender; an empty one would raise deeper in
        # the stack with a less useful message.
        sender = "(unknown sender)"

    return EmailMessage(
        id=message_id,
        sender=sender,
        subject=header(payload, "Subject"),
        body=extract_body(payload),
        received_at=to_datetime(raw.get("internalDate")),
        labels=frozenset(str(label) for label in (raw.get("labelIds") or [])),
        headers=all_headers(payload),
    )


def all_headers(payload: dict[str, Any]) -> dict[str, str]:
    """Collect every header Gmail returned.

    Gmail already hands these over on a 'full' fetch; before Milestone 7b they
    were parsed and thrown away. Keeping them lets source detection use
    List-Id and similar rather than guessing from the sender address.
    """
    return {
        str(entry.get("name", "")).strip().lower(): str(entry.get("value", ""))
        for entry in (payload.get("headers") or [])
        if entry.get("name")
    }


# --------------------------------------------------------------------------
# The mailbox
# --------------------------------------------------------------------------


class GmailMailbox(Mailbox):
    """Reads messages from a Gmail account.

    Note what this class does NOT have: no send, no delete, no modify, no
    archive, no label-editing method. Read-only is structural here and enforced
    again by the token's scope.
    """

    def __init__(
        self,
        service: Any | None = None,
        user_id: str = DEFAULT_USER_ID,
        credentials_path: str | None = None,
        token_path: str | None = None,
    ) -> None:
        """``service`` is injected for tests; otherwise it is built on demand.

        Building it lazily means constructing a GmailMailbox never requires
        credentials - only using one does.
        """
        self._service = service
        self._user_id = user_id
        self._credentials_path = credentials_path
        self._token_path = token_path
        self._label_ids: dict[str, str] | None = None

    def fetch(self, limit: int = 20, label: str | None = None) -> list[EmailMessage]:
        if limit <= 0:
            raise MailboxError(f"limit must be positive, got {limit}")

        label_ids: list[str] = []
        if label is not None:
            wanted = label.strip()
            if not wanted:
                raise MailboxError("label must not be blank; use None for no filter")
            label_ids = [self._resolve_label_id(wanted)]

        listing = self._call(
            lambda: self._messages()
            .list(userId=self._user_id, maxResults=limit, labelIds=label_ids or None)
            .execute(),
            "list messages",
        )

        messages: list[EmailMessage] = []
        for stub in (listing or {}).get("messages", []) or []:
            raw = self._call(
                lambda mid=stub.get("id"): self._messages()
                .get(userId=self._user_id, id=mid, format="full")
                .execute(),
                "fetch message",
            )
            if raw:
                messages.append(to_email_message(raw))

        results = sort_newest_first(messages)[:limit]
        logger.debug(
            "fetched %d message(s) from Gmail (limit=%d, label=%s)",
            len(results),
            limit,
            label,
        )
        return results

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_label_id(self, label: str) -> str:
        """Resolve a label to Gmail's label id, accepting a name or an id.

        Gmail exposes both: a display name ("Jobs") and an opaque id
        ("Label_7"), and callers reasonably use either. System labels ("INBOX")
        are already ids. Anything else is looked up by name first, then treated
        as an id if it matches a known one - so a caller who passes what Gmail
        handed them is not punished for it.

        The lookup is cached per instance, so repeated filtering does not
        re-query on every fetch.
        """
        upper = label.upper()
        if upper in _SYSTEM_LABELS:
            return upper

        if self._label_ids is None:
            listing = self._call(
                lambda: self._get_service()
                .users()
                .labels()
                .list(userId=self._user_id)
                .execute(),
                "list labels",
            )
            self._label_ids = {
                str(entry.get("name", "")).lower(): str(entry.get("id", ""))
                for entry in (listing or {}).get("labels", []) or []
            }

        by_name = self._label_ids.get(label.lower())
        if by_name:
            return by_name

        if label in set(self._label_ids.values()):
            return label

        raise MailboxError(f"No Gmail label named {label!r}")

    def _messages(self) -> Any:
        return self._get_service().users().messages()

    def _get_service(self) -> Any:
        if self._service is None:
            # Imported here so the module loads without the Google packages.
            from src.services.email.gmail_auth import (
                DEFAULT_CREDENTIALS_PATH,
                DEFAULT_TOKEN_PATH,
                build_gmail_service,
                load_credentials,
            )

            credentials = load_credentials(
                token_path=self._token_path or DEFAULT_TOKEN_PATH,
                credentials_path=self._credentials_path or DEFAULT_CREDENTIALS_PATH,
            )
            self._service = build_gmail_service(credentials)
        return self._service

    @staticmethod
    def _call(operation, description: str) -> Any:
        """Run an API call, translating any vendor error into MailboxError.

        Callers above depend on the Mailbox contract, not on googleapiclient -
        so no HttpError is allowed to escape this class.
        """
        try:
            return operation()
        except MailboxError:
            raise
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            raise MailboxError(f"Gmail request failed ({description}): {exc}") from exc

    def __repr__(self) -> str:
        state = "connected" if self._service is not None else "not connected"
        return f"<GmailMailbox user={self._user_id!r} {state}>"

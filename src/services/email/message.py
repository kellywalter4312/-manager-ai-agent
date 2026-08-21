"""The email message contract.

Like the contracts in src/core, this is a pure data type with no I/O: it
describes what an email *is*, independently of where it came from. That is what
lets the same EmailAgent work against an in-memory mailbox today and a real
Gmail mailbox later without changing a line of analysis code.

It lives under src/services/email rather than src/core because it is a
domain concept, not an orchestration one. The Manager, Router and Assessor have
no reason to know that email exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Mapping


@dataclass(frozen=True)
class EmailMessage:
    """A single email, normalized to the fields this system actually uses.

    Frozen for the same reason Task is: a message fetched from a mailbox is a
    record of something that already happened. Nothing downstream should be
    able to edit it in place.

    Deliberately *not* modelled: attachments, MIME parts, threading, HTML
    bodies. Those are real, but nothing needs them yet, and a contract invented
    for imagined requirements is harder to change than one grown from real use.
    """

    id: str
    sender: str
    subject: str
    body: str
    received_at: datetime
    labels: frozenset[str] = field(default_factory=frozenset)

    # Optional raw headers, added in Milestone 7b. Defaulted and additive, so
    # every existing caller and implementation keeps working untouched.
    # Headers such as List-Id identify platform digests (Wellfound and similar)
    # far more reliably than guessing from the sender address.
    headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("EmailMessage.id must not be empty")
        if not self.sender.strip():
            raise ValueError("EmailMessage.sender must not be empty")

        # Normalize so callers can match on labels without worrying about how
        # a particular mailbox spelled them.
        normalized = frozenset(
            label.strip().lower() for label in self.labels if label.strip()
        )
        object.__setattr__(self, "labels", normalized)

        # Header names are case-insensitive per RFC 5322; normalizing here means
        # callers never have to guess how a particular mailbox capitalised them.
        object.__setattr__(
            self,
            "headers",
            {str(k).strip().lower(): str(v) for k, v in dict(self.headers).items()},
        )

    @property
    def text(self) -> str:
        """Subject and body together - what an analyzer usually wants to read."""
        return f"{self.subject}\n\n{self.body}"

    def has_label(self, label: str) -> bool:
        return label.strip().lower() in self.labels

    def header(self, name: str, default: str = "") -> str:
        """Read a header case-insensitively; missing headers return default."""
        return self.headers.get(name.strip().lower(), default)

    def preview(self, limit: int = 60) -> str:
        """A short, log-safe description.

        Never includes the body. Email bodies carry personal data, and log
        lines get shipped and retained - the same reasoning that keeps task
        payloads out of the logs.
        """
        subject = " ".join(self.subject.split())
        if len(subject) > limit:
            subject = subject[: limit - 1] + "…"
        return f"{self.id} from={self.sender} subject={subject!r}"


def sort_newest_first(messages: Iterable[EmailMessage]) -> list[EmailMessage]:
    """Order messages by arrival, newest first.

    Ties break on id so the ordering is total and deterministic - two messages
    with identical timestamps always come back in the same order, which keeps
    tests from flaking.
    """
    return sorted(messages, key=lambda m: (m.received_at, m.id), reverse=True)

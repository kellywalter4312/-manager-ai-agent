"""A mailbox backed by an in-memory list of messages.

This is not a throwaway stub. It is the credential-free implementation that
lets the whole email feature - the agent, the analyzers, the tests, the demo -
be built and exercised without an account, an OAuth flow, or a network. A Gmail
implementation of the same interface arrives in a later milestone and changes
nothing above it.

Same reasoning as NullProvider: "no external service configured" should be a
valid operating state, not a blocker.
"""

from __future__ import annotations

from typing import Iterable

from src.services.email.mailbox import Mailbox, MailboxError
from src.services.email.message import EmailMessage, sort_newest_first
from src.utils.logging import get_logger

logger = get_logger(__name__)


class InMemoryMailbox(Mailbox):
    """Serves messages from a list held in memory.

    The message list is injected rather than hardcoded, so the same class can
    act as an empty mailbox, a realistic sample inbox, or a targeted fixture in
    a single test - the same dependency-injection move that let one EchoAgent
    stand in for any agent.
    """

    def __init__(self, messages: Iterable[EmailMessage] | None = None) -> None:
        self._messages: list[EmailMessage] = list(messages or [])
        for message in self._messages:
            if not isinstance(message, EmailMessage):
                raise TypeError(
                    f"Expected EmailMessage, got {type(message).__name__}"
                )

    def fetch(self, limit: int = 20, label: str | None = None) -> list[EmailMessage]:
        if limit <= 0:
            raise MailboxError(f"limit must be positive, got {limit}")

        selected = self._messages
        if label is not None:
            wanted = label.strip().lower()
            if not wanted:
                raise MailboxError("label must not be blank; use None for no filter")
            selected = [m for m in selected if m.has_label(wanted)]

        results = sort_newest_first(selected)[:limit]
        logger.debug(
            "fetched %d message(s) (limit=%d, label=%s)", len(results), limit, label
        )
        return results

    def __len__(self) -> int:
        return len(self._messages)

    def __repr__(self) -> str:
        return f"<InMemoryMailbox messages={len(self._messages)}>"

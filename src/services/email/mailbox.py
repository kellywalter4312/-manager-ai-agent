"""Mailbox interfaces - how the system reaches email, and how it will send it.

Two interfaces rather than one, split by direction:

    Mailbox  - read access  (implemented now)
    Outbox   - send access  (declared now, implemented in a later milestone)

This is Interface Segregation: a component that only reads email should not
have to know how to send it, and - more importantly - should not be *able* to.
An EmailAgent constructed with a Mailbox and no Outbox cannot send mail even if
it is asked to, because it holds no object with a send method. The restriction
is structural, not a policy someone has to remember to enforce.

Both interfaces are deliberately narrow. Narrow interfaces are cheap to
implement, which is what makes an in-memory implementation (today) and a Gmail
implementation (later) equally practical.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.services.email.message import EmailMessage


class MailboxError(Exception):
    """Raised when a mailbox cannot satisfy a request.

    A domain-specific error type lets callers catch *this* rather than bare
    Exception, so a genuine mailbox failure is distinguishable from a bug in
    the calling code.
    """


class Mailbox(ABC):
    """Read-only access to a collection of messages."""

    @abstractmethod
    def fetch(self, limit: int = 20, label: str | None = None) -> list[EmailMessage]:
        """Return up to ``limit`` messages, newest first.

        ``label`` optionally narrows the result. Implementations must raise
        MailboxError - not a vendor-specific exception - when the underlying
        source fails, so callers depend on this module rather than on whatever
        SDK happens to sit behind it.

        Returning a list (not an iterator) is a deliberate simplification: the
        caller always wants a bounded, re-readable result, and ``limit`` keeps
        it bounded.
        """


class Outbox(ABC):
    """Send access. Declared now; no implementation ships in this milestone.

    The interface exists so that composition has a defined shape and a place to
    live, and so an EmailAgent can be *written* against it and tested with a
    fake today. What does not exist yet is any real implementation - sending
    mail is an irreversible, outward-facing action, and it is not in scope
    until the surrounding safeguards are.
    """

    @abstractmethod
    def send(self, to: str, subject: str, body: str) -> str:
        """Send a message and return the resulting message id.

        Implementations must raise MailboxError on failure.
        """

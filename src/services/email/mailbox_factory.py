"""Turns a configured mailbox *name* into a mailbox *instance*.

The same small-registry pattern as providers/factory.py and
manager/router_factory.py - third use in this codebase. It is the single place
where configuration and mailbox implementations meet, which keeps Settings
ignorant of which mailboxes exist and the mailboxes ignorant of configuration.

"memory" stays the default, so the system runs with no credentials.
"""

from __future__ import annotations

from typing import Callable

from src.config.settings import Settings
from src.services.email.mailbox import Mailbox
from src.services.email.sample_inbox import sample_inbox
from src.utils.logging import get_logger

logger = get_logger(__name__)


def _build_gmail(settings: Settings) -> Mailbox:
    """Build the Gmail mailbox, importing its module only when selected.

    The import is local so the default path never loads Gmail code, and the
    project runs where the Google packages are not installed. No credential is
    read here - GmailMailbox connects lazily on first use.
    """
    from src.services.email.gmail_mailbox import GmailMailbox

    return GmailMailbox(
        credentials_path=settings.gmail_credentials_path,
        token_path=settings.gmail_token_path,
    )


_BUILDERS: dict[str, Callable[[Settings], Mailbox]] = {
    "memory": lambda settings: sample_inbox(),
    "gmail": _build_gmail,
}


def available_mailboxes() -> list[str]:
    """Every mailbox name that can currently be configured."""
    return sorted(_BUILDERS)


def build_mailbox(settings: Settings) -> Mailbox:
    """Construct the mailbox named by ``settings.mailbox``.

    Fails fast on an unknown name so a configuration typo stops the program
    with a readable message rather than surfacing later as an empty inbox.
    """
    try:
        builder = _BUILDERS[settings.mailbox]
    except KeyError:
        raise ValueError(
            f"Unknown mailbox {settings.mailbox!r}. "
            f"Available mailboxes: {', '.join(available_mailboxes())}"
        ) from None

    mailbox = builder(settings)
    logger.info("mailbox ready: %s (%s)", settings.mailbox, type(mailbox).__name__)
    return mailbox

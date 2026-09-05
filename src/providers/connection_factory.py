"""Turns a configured connection *name* into a connection *instance*.

The identical small registry ``live_info_factory.py``/
``communication_factory.py``/``celebration_factory.py``/
``calendar_events_factory.py`` already are. "null" (honestly
unavailable) is the default - the same choice
``communication_provider`` already makes, and for the identical reason:
a connection is consequential in exactly the way a phone/messaging
provider is, so a real one is always a deliberate
``MANAGER_CONNECTION_PROVIDER=gmail``, never a side effect of upgrading.

"gmail" reuses the existing, already-governed Gmail read boundary (see
``gmail_connection.py``'s own docstring) rather than adding a new
external dependency - no new package, no new service, no new risk
beyond what this project's Gmail integration already carries.

**Adapter version (Full Alfred OS Ecosystem Evolution).**
``adapter_version`` is threaded straight through to whichever builder
accepts it - today, only Gmail's own ``GmailConnection`` does anything
with it (see that class's own docstring). Every builder that ignores it
(``null``, and any future connection with no adapter-version axis of
its own) keeps working unchanged; the default ("1") reproduces this
factory's prior behaviour exactly for every existing caller that omits
it.
"""

from __future__ import annotations

from typing import Callable, Mapping

from src.config.settings import Settings
from src.providers.connection import Connection
from src.providers.null_connection import NullConnection
from src.utils.logging import get_logger

logger = get_logger(__name__)


def _build_gmail(settings: Settings, adapter_version: str = "1") -> Connection:
    """Build the Gmail connection, importing its module only when
    selected - the identical reason ``mailbox_factory.py``'s own
    ``_build_gmail`` imports ``GmailMailbox`` locally: the default path
    never loads Gmail code, and the project runs where the Google
    packages are not installed."""
    from src.providers.gmail_connection import GmailConnection
    from src.services.email.mailbox_factory import build_mailbox

    return GmailConnection(
        build_mailbox(settings), token_path=settings.gmail_token_path, adapter_version=adapter_version
    )


_BUILDERS: "dict[str, Callable[..., Connection]]" = {
    "null": lambda settings, adapter_version="1": NullConnection(),
    "gmail": _build_gmail,
}


def available_connection_providers() -> list[str]:
    """Every connection provider name that can currently be configured."""
    return sorted(_BUILDERS)


def build_connection(
    settings: Settings, env: "Mapping[str, str] | None" = None, adapter_version: str = "1"
) -> Connection:
    """Construct the connection named by ``settings.connection_provider``.

    ``adapter_version`` is the identical, optional, additive seam
    ``build_provider``/``build_media``/``build_speech``/
    ``build_communication_provider`` already accept for their own,
    different axis (which credential) - applied here to which real
    *implementation* a builder that has more than one should use.
    Omitted, every existing caller reproduces this function's prior
    behaviour exactly.

    Fails fast and loudly on an unknown name, the same rule every other
    factory in this project already follows.
    """
    try:
        builder = _BUILDERS[settings.connection_provider]
    except KeyError:
        raise ValueError(
            f"Unknown connection provider {settings.connection_provider!r}. "
            f"Available: {', '.join(available_connection_providers())}"
        ) from None

    connection = builder(settings, adapter_version)
    logger.info("connection ready: %s", connection.name)
    return connection

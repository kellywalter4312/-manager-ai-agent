"""Stage 16 - the smallest representative real connection, built by
wrapping the existing, already-governed Gmail read boundary rather than
duplicating it.

**Reuse, not a second Gmail implementation.** Every line of Gmail-
specific logic - OAuth, token refresh, the Gmail API shape - already
lives in ``src/services/email/gmail_auth.py``/``gmail_mailbox.py`` and
is untouched by this file. ``GmailConnection`` holds a ``Mailbox``
(built the identical way ``src/services/email/mailbox_factory.py``
already builds one for ``EmailAgent``) and does nothing but translate
its one method, ``fetch``, into the generic ``Connection.execute``
shape this stage establishes.

**Still read-only, on purpose.** ``Mailbox``/``Outbox``'s own docstring
has stated since this project's Gmail integration began that sending is
"not in scope until the surrounding safeguards are" - no ``Outbox``
implementation exists anywhere in this codebase. This connection holds
no ``Outbox`` and declares no write operation, so it cannot widen that
boundary even by accident; ``supported_operations`` names exactly one
thing, and asking for anything else is answered honestly with
``UNSUPPORTED``, never attempted.

**Authorization is a cheap, local, honest check - never a network
call.** A configured token file is what "authorized" means here;
whether that token is still *valid* is a fact ``availability`` reports
once an operation has actually been attempted (the identical "evidence,
not invented" contract ``Availability`` itself already keeps) - this
class never calls Gmail merely to answer "am I authorized".

**Adapter version (Full Alfred OS Ecosystem Evolution).** ``adapter_version``
distinguishes this class's own *implementation* from the account/
credential it authenticates with - a genuinely different axis, governed
separately (see ``src/services/evolution/handlers.py::ConnectionAdapterHandler``).
``"1"`` (the default, and this class's entire pre-existing behaviour,
byte-for-byte unchanged) forwards only ``limit`` to ``Mailbox.fetch``.
``"2"`` additionally forwards an optional ``label`` filter - real,
because ``Mailbox.fetch(limit, label=None)`` (``src/services/email/mailbox.py``)
already declares this parameter and ``GmailMailbox`` already implements
it; v1 simply never passed it through. Upgrading never touches
``token_path``/``_mailbox`` - the same account remains authorized
before and after, provably (``ConnectionAdapterHandler.apply`` verifies
this directly).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.providers.availability import Availability, AvailabilityState
from src.providers.connection import (
    AuthorizationState,
    Connection,
    ConnectionResult,
    ConnectionStatus,
    utc_now,
)
from src.services.email.mailbox import Mailbox, MailboxError

READ_EMAIL = "read_email"


class GmailConnection(Connection):
    def __init__(
        self, mailbox: Mailbox, *, token_path: str | Path, adapter_version: str = "1"
    ) -> None:
        self._mailbox = mailbox
        self._token_path = Path(token_path)
        self._adapter_version = adapter_version
        # UNKNOWN until an operation is actually attempted - the same
        # honest starting point every other use of Availability in this
        # project already has; a connection nobody has used yet is
        # unproven, not assumed working.
        self._availability = Availability(state=AvailabilityState.UNKNOWN)

    @property
    def adapter_version(self) -> str:
        return self._adapter_version

    @property
    def name(self) -> str:
        return "gmail"

    @property
    def availability(self) -> Availability:
        return self._availability

    @property
    def authorization(self) -> AuthorizationState:
        return (
            AuthorizationState.AUTHORIZED
            if self._token_path.exists()
            else AuthorizationState.UNAUTHORIZED
        )

    @property
    def supported_operations(self) -> tuple[str, ...]:
        return (READ_EMAIL,)

    def execute(self, operation: str, **params: Any) -> ConnectionResult:
        if operation not in self.supported_operations:
            return ConnectionResult(
                operation=operation,
                status=ConnectionStatus.UNSUPPORTED,
                detail=(
                    f"'{operation}' is not supported by this connection. "
                    f"Available: {', '.join(self.supported_operations) or 'none'}"
                ),
                source=self.name,
                executed_at=utc_now(),
            )

        if self.authorization is not AuthorizationState.AUTHORIZED:
            return ConnectionResult(
                operation=operation,
                status=ConnectionStatus.UNAUTHORIZED,
                detail="no Gmail authorization is configured",
                source=self.name,
                executed_at=utc_now(),
            )

        fetch_kwargs = {"limit": int(params.get("limit", 10))}
        if self._adapter_version == "2" and params.get("label") is not None:
            fetch_kwargs["label"] = params["label"]

        try:
            messages = self._mailbox.fetch(**fetch_kwargs)
        except MailboxError as exc:
            self._availability = Availability(state=AvailabilityState.UNAVAILABLE, reason=str(exc))
            return ConnectionResult(
                operation=operation,
                status=ConnectionStatus.FAILED,
                detail=str(exc),
                source=self.name,
                executed_at=utc_now(),
            )

        self._availability = Availability(state=AvailabilityState.AVAILABLE)
        return ConnectionResult(
            operation=operation,
            status=ConnectionStatus.SUCCEEDED,
            detail=f"{len(messages)} message(s)",
            source=self.name,
            executed_at=utc_now(),
        )

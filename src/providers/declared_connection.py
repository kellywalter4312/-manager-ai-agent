"""One real ``Connection`` implementation for any declared service - the
piece that turns a ``ServiceDescriptor`` (data) into something
``connected_action``/``ConnectionActionTool`` can actually invoke,
without a new Python subclass per service.

**Real, not speculative, and bounded exactly like
``WikipediaLiveInfoProvider`` already is.** Every call this class ever
makes goes to ``descriptor.base_url + operation.path`` - a fixed
destination fixed at declaration time, through the confirmed, audited
``services <name> declare`` administrative write
(``scripts/control_center.py``), never a URL Alfred chooses mid-
conversation. One HTTP method per operation, a fixed timeout, the
Python standard library's own ``urllib`` - no new dependency, the
identical "governed, not unrestricted" shape that file's own docstring
already commits to.

**Availability is evidence, never invented from configuration alone -**
the identical ``Availability.UNKNOWN`` until an actual attempt succeeds
or fails discipline ``GmailConnection`` already holds itself to.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from src.providers.availability import Availability, AvailabilityState
from src.providers.connection import (
    AuthorizationState,
    Connection,
    ConnectionResult,
    ConnectionStatus,
    utc_now,
)
from src.providers.service_descriptor import ServiceDescriptor
from src.services.credentials.account import ProviderAccount

_USER_AGENT = "AlfredOS-DeclaredConnection/1.0"
# A response this large has nothing more to tell a capability handler
# and everything to do with a service that misbehaves - truncated, not
# rejected, the same "still usable, honestly labelled" shape a too-long
# transcript elsewhere in this project already gets.
_MAX_DETAIL_CHARS = 2000


class DeclaredConnection(Connection):
    """A governed connection built entirely from data: a
    ``ServiceDescriptor`` (what the service is and can do) and,
    optionally, the ``ProviderAccount`` already on file for it under
    the identical credential store every other connection's own
    account already lives in - never a second, parallel credential
    mechanism.
    """

    def __init__(
        self,
        descriptor: ServiceDescriptor,
        account: "ProviderAccount | None",
        *,
        timeout: float = 8.0,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._descriptor = descriptor
        self._account = account
        self._timeout = timeout
        self._availability = Availability(state=AvailabilityState.UNKNOWN)

    @property
    def name(self) -> str:
        return self._descriptor.name

    @property
    def availability(self) -> Availability:
        return self._availability

    @property
    def authorization(self) -> AuthorizationState:
        if not any(op.requires_auth for op in self._descriptor.operations):
            return AuthorizationState.NOT_REQUIRED
        return (
            AuthorizationState.AUTHORIZED
            if self._account is not None
            else AuthorizationState.UNAUTHORIZED
        )

    @property
    def supported_operations(self) -> "tuple[str, ...]":
        return self._descriptor.operation_names

    def execute(self, operation: str, **params: Any) -> ConnectionResult:
        op = self._descriptor.operation(operation)
        if op is None:
            return ConnectionResult(
                operation=operation,
                status=ConnectionStatus.UNSUPPORTED,
                detail=f"'{self.name}' declares no operation named '{operation}'",
                source=self.name,
                executed_at=utc_now(),
            )
        if op.requires_auth and self._account is None:
            return ConnectionResult(
                operation=operation,
                status=ConnectionStatus.UNAUTHORIZED,
                detail=f"'{self.name}' is not authorized - no credential is on file for it",
                source=self.name,
                executed_at=utc_now(),
            )

        url = self._descriptor.base_url + op.path
        headers = {"User-Agent": _USER_AGENT}
        if op.requires_auth and self._account is not None:
            headers["Authorization"] = f"Bearer {self._account.credential}"
        request = urllib.request.Request(url, method=op.method, headers=headers)

        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            reason = str(exc)
            self._availability = Availability(state=AvailabilityState.UNAVAILABLE, reason=reason)
            return ConnectionResult(
                operation=operation,
                status=ConnectionStatus.FAILED,
                detail=f"could not reach '{self.name}': {reason}",
                source=self.name,
                executed_at=utc_now(),
            )

        self._availability = Availability(state=AvailabilityState.AVAILABLE)
        try:
            parsed = json.loads(raw)
            detail = json.dumps(parsed)
        except json.JSONDecodeError:
            detail = raw
        return ConnectionResult(
            operation=operation,
            status=ConnectionStatus.SUCCEEDED,
            detail=detail[:_MAX_DETAIL_CHARS],
            source=self.name,
            executed_at=utc_now(),
        )

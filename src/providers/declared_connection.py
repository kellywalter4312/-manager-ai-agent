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

**Operation-level authorization (this checkpoint).** ``authorization``
(the ``Connection`` ABC's own property) stays exactly what it always
was - the whole connection's blanket state, still correct wherever a
caller genuinely needs "is anything here usable at all" (Control
Center's own connection-status views). ``authorization_for(operation)``
is new and more precise: a service with one harmless, no-credential-
needed read and one credential-gated write must not report the read as
blocked merely because the write's own credential is missing - the
exact over-conservative behaviour a real, live discovery-to-execution
run surfaced. ``requires_confirmation_for(operation)`` names the other
real, pre-existing signal this checkpoint reuses rather than inventing:
an operation's own declared HTTP method. GET is safe and idempotent by
the same convention this project's own REST-facing code already relies
on everywhere else; anything else (today, only POST) changes state on
the real service and is never executed without the identical
confirm_token dance every other consequential write in this project
already goes through.

**Parameters (this checkpoint).** ``execute(operation, **params)``
previously accepted, and silently discarded, every parameter - a
correct GET, an equally correct call with real content had no way to
ever reach the real service. Bounded, not open-ended: a GET's params
become a real, safely-escaped query string (``urllib.parse.urlencode``
never lets a value smuggle a new path segment or query key); a POST's
become a real JSON body. Every value must be a plain JSON scalar
(string, number, boolean, or ``None``) - never a nested object or
list, which would only ever be a caller's own mistake or something
worth refusing outright rather than serialising blindly. Nothing here
invents a missing value: an operation that needs a parameter no one
supplied still reaches the real service without it, and the real
service's own honest rejection (a 400, a missing-field error) is what
is reported back - never a guessed value standing in for one Alfred
was never given.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
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

# The only HTTP methods a declared operation may ever carry (the
# identical allow-list ServiceOperation itself already enforces at
# construction) - GET is treated as safe/idempotent, everything else as
# consequential. Kept here, not re-derived, so the confirmation
# boundary and the method allow-list can never silently drift apart.
_SAFE_METHODS: "tuple[str, ...]" = ("GET",)

# What a parameter value is allowed to be: a plain JSON scalar. A
# nested object or list is refused outright rather than serialised
# blindly - the identical "bounded, not unrestricted" discipline this
# module's own docstring already commits every other surface to.
_SCALAR_TYPES = (str, int, float, bool, type(None))


class ParameterError(ValueError):
    """A caller supplied a parameter value this connection will not
    forward - never silently dropped, never coerced into something it
    was not."""


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

    def authorization_for(self, operation: str) -> AuthorizationState:
        """The real authorization state for THIS operation specifically
        - never this connection's own blanket ``authorization``, which
        would report a harmless, credential-free read as blocked merely
        because a *different* operation on the same service needs a
        credential nobody has provided yet.

        Raises ``KeyError`` for an operation this connection does not
        declare - the identical "an unknown operation is the caller's
        mistake, never a state value" rule ``execute`` itself already
        enforces via ``ConnectionStatus.UNSUPPORTED``, expressed here as
        an exception because ``AuthorizationState`` has no "does not
        exist" member of its own to misuse for it.
        """
        op = self._descriptor.operation(operation)
        if op is None:
            raise KeyError(f"'{self.name}' declares no operation named '{operation}'")
        if not op.requires_auth:
            return AuthorizationState.NOT_REQUIRED
        return (
            AuthorizationState.AUTHORIZED
            if self._account is not None
            else AuthorizationState.UNAUTHORIZED
        )

    def requires_confirmation_for(self, operation: str) -> bool:
        """Whether ``operation`` changes state on the real service and
        must therefore never run without the identical confirm_token
        dance every other consequential write in this project already
        requires - read from the operation's own declared HTTP method,
        the same real, pre-existing signal this checkpoint reuses
        rather than inventing a new risk category.

        Raises ``KeyError`` for an unknown operation, the identical
        reason ``authorization_for`` does.
        """
        op = self._descriptor.operation(operation)
        if op is None:
            raise KeyError(f"'{self.name}' declares no operation named '{operation}'")
        return op.method not in _SAFE_METHODS

    def operation_description_for(self, operation: str) -> str:
        """The operation's own declared, free-text description - the
        one piece of semantic metadata beyond its bare name a
        ``ServiceOperation`` can carry, so a technically-named operation
        (``fetch_state``) can still be found by what it actually does
        rather than only by what it is called.

        Empty for an unknown operation or one declared with no
        description at all, never an exception - a caller scoring
        candidates (``_select_operation``,
        ``src/manager/capabilities.py``) treats that identically to
        "nothing more to go on than the name," never a hard failure.
        """
        op = self._descriptor.operation(operation)
        return op.description if op is not None else ""

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

        for key, value in params.items():
            if not isinstance(value, _SCALAR_TYPES):
                return ConnectionResult(
                    operation=operation,
                    status=ConnectionStatus.FAILED,
                    detail=(
                        f"parameter '{key}' must be a plain string/number/boolean, "
                        f"got {type(value).__name__} - refused rather than guessed at"
                    ),
                    source=self.name,
                    executed_at=utc_now(),
                )

        url = self._descriptor.base_url + op.path
        headers = {"User-Agent": _USER_AGENT}
        if op.requires_auth and self._account is not None:
            headers["Authorization"] = f"Bearer {self._account.credential}"

        body: "bytes | None" = None
        if op.method == "GET":
            if params:
                url = url + "?" + urllib.parse.urlencode(params)
        else:
            body = json.dumps(params).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=body, method=op.method, headers=headers)

        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            # A real, honest rejection from the real service - most
            # often a missing/invalid parameter Alfred never fabricated
            # a value for - reported as the genuine failure it is,
            # never silently retried with a guessed value.
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
            self._availability = Availability(state=AvailabilityState.AVAILABLE)  # the service WAS reached
            return ConnectionResult(
                operation=operation,
                status=ConnectionStatus.FAILED,
                detail=f"'{self.name}' rejected the request (HTTP {exc.code}): {detail[:_MAX_DETAIL_CHARS]}",
                source=self.name,
                executed_at=utc_now(),
            )
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

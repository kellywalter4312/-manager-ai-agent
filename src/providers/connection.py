"""The connection contract - Stage 16's own governed boundary to an
external, authenticated service (Gmail, a calendar, commerce, travel),
distinct from every provider that came before it.

**Why this is not another ``LLMProvider``/``LiveInfoProvider``/
``CommunicationProvider``.** Those are each narrow, domain-specific
interfaces (complete, fetch, read/send/call) - the right shape when a
single kind of operation is all a caller ever needs. A connection is
asked to represent something those never had to: that a service can be
*configured* without being *reachable*, and *reachable* without being
*authorized* - three genuinely different facts a governed capability
must be able to tell apart before ever attempting an action, per this
stage's own brief ("Support the distinction between: reasoning;
capability availability; connection availability; authorization;
execution; execution result. Do not collapse these into one concept").

**Reuses ``Availability`` rather than inventing a parallel state
machine.** ``src/providers/availability.py`` already draws the exact
distinction a connection's own reachability needs - UNKNOWN (never
tried) versus AVAILABLE (a call succeeded) versus UNAVAILABLE (a call
failed) - evidence recorded from an actual attempt, never invented.
Authorization is a genuinely different axis (a stored credential can be
present or absent independent of whether the service is currently
reachable), so it gets its own small enum below rather than being
folded into the same one.

**Why this is documented in the architecture as "Connections" and lives
here anyway.** ``src/extensions.py``'s own diagram names Connections as
the layer after Capabilities - documented since early in this project,
never implemented until now. This module is that implementation; it
does not introduce a new top-level package because a Connection is,
mechanically, exactly the same shape every provider abstraction in this
project already is - a small ABC, a frozen result type, honest failure.

**Never a giant integration framework.** One narrow method
(``execute``), operating on whatever operations a specific connection
actually declares (``supported_operations``) - never a generic remote-
procedure-call surface, and never a place that invents an operation a
connection did not itself declare.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from src.providers.availability import Availability


def utc_now() -> datetime:
    """The default clock. Injectable so tests can control time."""
    return datetime.now(timezone.utc)


class AuthorizationState(str, Enum):
    """Whether a credential is configured for this connection - a
    genuinely different fact from whether the service is currently
    reachable (that is ``availability``'s job).

    Three states, not two: a connection that needs no credential at all
    (rare, but honest) is not the same claim as one that needs one and
    lacks it, and collapsing them would make "authorized" mean
    "authorized or never needed to be", the exact ambiguity this project
    refuses everywhere else.
    """

    AUTHORIZED = "authorized"
    UNAUTHORIZED = "unauthorized"
    NOT_REQUIRED = "not_required"

    @property
    def grants_access(self) -> bool:
        return self in (AuthorizationState.AUTHORIZED, AuthorizationState.NOT_REQUIRED)


class ConnectionStatus(str, Enum):
    """How one attempted operation ended.

    Five outcomes, deliberately not collapsed into ok/not-ok: a caller
    reasoning about what to tell the person needs to distinguish "this
    kind of connection does not exist here" (UNAVAILABLE) from "it
    exists but nobody authorized it" (UNAUTHORIZED) from "it is
    authorized and reachable but this specific operation is not one it
    offers" (UNSUPPORTED) from "a genuine attempt was made and failed"
    (FAILED) from an actual, verified success.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    UNAUTHORIZED = "unauthorized"
    UNSUPPORTED = "unsupported"

    @property
    def ok(self) -> bool:
        return self is ConnectionStatus.SUCCEEDED


@dataclass(frozen=True)
class ConnectionResult:
    """What actually happened when one operation was attempted through
    one connection.

    ``status`` is the honest center of this type, the identical rule
    ``CommunicationResult``/``LiveInfoResult`` already state for their
    own domains: a caller must never render this as "done" unless
    ``status.ok`` is True. Never fabricated - a connection that cannot
    reach the world answers honestly, never with an invented success.
    """

    operation: str
    status: ConnectionStatus
    detail: str = ""
    source: str = ""
    executed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not str(self.operation).strip():
            raise ValueError("ConnectionResult.operation must not be empty")
        object.__setattr__(self, "operation", " ".join(str(self.operation).split()))
        object.__setattr__(self, "source", " ".join(str(self.source or "").split()))
        if self.executed_at is not None and not isinstance(self.executed_at, datetime):
            raise ValueError("ConnectionResult.executed_at must be a datetime")

    @property
    def ok(self) -> bool:
        return self.status.ok

    def describe(self) -> str:
        """A short line for logs. Excludes ``detail``, which may carry
        service content - the same rule ``CommunicationResult.describe``
        already follows."""
        return f"{self.operation}: {self.status.value}"


class Connection(ABC):
    """A governed boundary to one external, authenticated service.

    Four properties and one method - the same narrow shape every other
    provider abstraction in this project already proved cheap to
    implement and cheap to replace, extended here with the two axes
    (availability, authorization) a connection specifically needs that
    a simpler provider never had to represent.

    Implementations must never raise for an ordinary, valid operation
    name - an unsupported operation, an unauthorized attempt, or an
    unreachable service is answered with an honestly non-ok
    ``ConnectionResult``, never an exception and never a fabricated
    success. A genuine, unexpected failure may still raise; the governed
    tool boundary (``src/agents/tools/connection.py``) catches it exactly
    as ``ToolSet.invoke`` already catches every other tool's exceptions.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in logs and provenance, e.g. "null"."""

    @property
    @abstractmethod
    def availability(self) -> Availability:
        """What is known about whether this connection can currently be
        reached - evidence from an actual attempt, the identical
        UNKNOWN/AVAILABLE/UNAVAILABLE contract every other use of
        ``Availability`` in this project already keeps. Never invented
        from configuration alone."""

    @property
    @abstractmethod
    def authorization(self) -> AuthorizationState:
        """Whether a credential is currently configured for this
        connection - independent of whether the service is reachable."""

    @property
    @abstractmethod
    def supported_operations(self) -> tuple[str, ...]:
        """Every operation name this connection genuinely implements.
        Empty for a connection with nothing to offer (see
        ``NullConnection``) - never a name declared here that
        ``execute`` cannot actually perform."""

    @abstractmethod
    def execute(self, operation: str, **params: Any) -> ConnectionResult:
        """Attempt ``operation``. Consequential - see this class's own
        docstring on honesty. An operation not in
        ``supported_operations`` is answered with
        ``ConnectionStatus.UNSUPPORTED``, never attempted."""

    # -- Operation-level extension protocol -------------------------------
    #
    # Optional, non-abstract - every existing connection kind (Gmail,
    # Null) implements only the five members above and is completely
    # unaffected. A connection whose operations genuinely differ in
    # authorization/consequence/parameters/meaning (DeclaredConnection,
    # WebsiteConnection) overrides these for real per-operation answers;
    # every other connection inherits an honest, blanket-level default
    # for free - never a KeyError, never an invented per-operation fact.
    #
    # Formalized here (Phase 7 - Universal Connection Layer) rather than
    # left as the getattr(connection, "authorization_for", None)
    # duck-typing convention it previously only existed as: a future
    # connection kind's author now finds this protocol by reading this
    # one class, never by reverse-engineering src/manager/capabilities.py's
    # own call sites. Behavior for every connection that does not override
    # these is unchanged - each default reproduces exactly what that
    # duck-typed fallback already did.

    def authorization_for(self, operation: str) -> AuthorizationState:
        """The real authorization state for ``operation`` specifically.
        Default: this connection's own blanket ``authorization`` - the
        honest answer for a connection with nothing more precise to
        offer. Override when different operations genuinely have
        different authorization requirements (see
        ``DeclaredConnection.authorization_for``)."""
        return self.authorization

    def requires_confirmation_for(self, operation: str) -> bool:
        """Whether ``operation`` changes state on the real service and
        must therefore never run without a confirmation step. Default:
        False - a connection that declares nothing about consequence is
        never treated as consequential by assumption. Override when
        some operations genuinely are (see
        ``DeclaredConnection.requires_confirmation_for``)."""
        return False

    def parameters_for(self, operation: str) -> "tuple[Any, ...]":
        """Every real input ``operation`` declares. Default: empty -
        the honest answer for a connection with no declared parameter
        shape. Override when operations genuinely take parameters (see
        ``DeclaredConnection.parameters_for``)."""
        return ()

    def operation_description_for(self, operation: str) -> str:
        """``operation``'s own free-text description, if this
        connection can supply one. Default: empty - a caller scoring
        candidates (``_select_operation``,
        ``src/manager/capabilities.py``) already treats that identically
        to "nothing more to go on than the name." Override when
        operations carry real descriptive metadata (see
        ``DeclaredConnection.operation_description_for``)."""
        return ""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"

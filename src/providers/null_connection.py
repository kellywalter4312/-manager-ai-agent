"""A connection that reaches no external service at all.

The honest "nothing is configured" default, the same role
``NullLiveInfoProvider``/``NullCommunicationProvider`` already play -
never a placeholder standing in for "not implemented yet", but a real,
complete implementation of "this installation has no connection
configured". Declares no supported operations and honestly refuses any
attempt with ``UNAVAILABLE`` - never a fabricated success.
"""

from __future__ import annotations

from typing import Any

from src.providers.availability import Availability, AvailabilityState
from src.providers.connection import (
    AuthorizationState,
    Connection,
    ConnectionResult,
    ConnectionStatus,
    utc_now,
)

_UNAVAILABLE_REASON = "no connection is configured"


class NullConnection(Connection):
    @property
    def name(self) -> str:
        return "null"

    @property
    def availability(self) -> Availability:
        return Availability(state=AvailabilityState.UNAVAILABLE, reason=_UNAVAILABLE_REASON)

    @property
    def authorization(self) -> AuthorizationState:
        return AuthorizationState.UNAUTHORIZED

    @property
    def supported_operations(self) -> tuple[str, ...]:
        return ()

    def execute(self, operation: str, **params: Any) -> ConnectionResult:
        return ConnectionResult(
            operation=operation,
            status=ConnectionStatus.UNAVAILABLE,
            detail=_UNAVAILABLE_REASON,
            source=self.name,
            executed_at=utc_now(),
        )

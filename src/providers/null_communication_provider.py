"""A communication provider that needs no key, no carrier and no network.

The same role ``NullLiveInfoProvider``/``NullProvider`` play elsewhere:
not a placeholder to be replaced later, but how the system stays
runnable and testable with zero external dependencies, and the honest
default for a project with no authorized phone/messaging connection
configured. Reading is honestly empty; sending and calling are honestly
``UNAVAILABLE`` - never a fabricated success, and never an exception for
what is, today, simply "nothing is connected yet".
"""

from __future__ import annotations

from src.providers.communication import (
    CommunicationEvent,
    CommunicationProvider,
    CommunicationResult,
    CommunicationStatus,
    utc_now,
)

_UNAVAILABLE_REASON = "no authorized communication connection is configured"


class NullCommunicationProvider(CommunicationProvider):
    @property
    def name(self) -> str:
        return "null"

    def read_messages(self, *, limit: int = 10) -> tuple[CommunicationEvent, ...]:
        return ()

    def unavailable_reason(self) -> "str | None":
        return _UNAVAILABLE_REASON

    def send_message(self, *, to: str, content: str) -> CommunicationResult:
        return CommunicationResult(
            operation="send",
            party=to,
            status=CommunicationStatus.UNAVAILABLE,
            detail=_UNAVAILABLE_REASON,
            source=self.name,
            attempted_at=utc_now(),
        )

    def place_call(self, *, to: str) -> CommunicationResult:
        return CommunicationResult(
            operation="call",
            party=to,
            status=CommunicationStatus.UNAVAILABLE,
            detail=_UNAVAILABLE_REASON,
            source=self.name,
            attempted_at=utc_now(),
        )

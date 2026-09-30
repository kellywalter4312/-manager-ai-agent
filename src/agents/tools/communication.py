"""The governed boundary between Alfred (or a specialist) and a real
communication connection.

**The Brain decides. This performs.** A ``Tool`` is handed its arguments
and nothing else - no toolset, no registry, no agent, no manager (see
``tool.py``'s own docstring) - so none of the three classes below can
reach anything beyond the one ``CommunicationProvider`` each was
constructed with.

**Three tools, not one, on purpose.** ``ToolSet`` membership is already
this project's entire permission model - a specialist can invoke exactly
what it was granted and nothing else. Splitting read/send/call into
three separately-named tools means that model *is* the governance the
brief asks for: granting ``CommunicationReadTool`` without
``CommunicationSendTool`` is a specialist that can check messages and
never send one, expressed with zero new machinery.
"""

from __future__ import annotations

from src.agents.tools.tool import Tool, ToolSpec
from src.providers.communication import CommunicationProvider

READ_TOOL_NAME = "communication_read"
SEND_TOOL_NAME = "communication_send"
CALL_TOOL_NAME = "communication_call"


class CommunicationReadTool(Tool):
    """Read-only: recent messages and calls, never a send or a call."""

    def __init__(self, provider: CommunicationProvider) -> None:
        self._provider = provider

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=READ_TOOL_NAME,
            description="Reads recent messages and calls through a governed communication connection.",
            schema={},
        )

    def run(self, **arguments: object) -> str:
        # Nothing connected is not the same as nothing received: say which
        # it is, never "No recent messages" for an inbox nobody could read.
        reason = self._provider.unavailable_reason()
        if reason:
            raise RuntimeError(f"{reason}, so there is nothing I can read")
        events = self._provider.read_messages(limit=10)
        if not events:
            return "No recent messages or calls."
        lines = [
            f"{event.kind} - {event.party} ({event.occurred_at.isoformat()}): {event.summary}"
            for event in events
        ]
        return "\n".join(lines)


class CommunicationSendTool(Tool):
    """Consequential: attempts to send a message. Raises on anything
    other than a genuine, provider-confirmed success - the same
    "describe, don't fabricate" contract every honest failure in this
    project already keeps; ``ToolSet.invoke`` turns that into a
    structured, honest ``ToolResult.failed``.
    """

    def __init__(self, provider: CommunicationProvider) -> None:
        self._provider = provider

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=SEND_TOOL_NAME,
            description="Sends a message through a governed, authorized communication connection.",
            schema={"to": str, "content": str},
        )

    def run(self, **arguments: str) -> str:
        result = self._provider.send_message(to=arguments["to"], content=arguments["content"])
        if not result.ok:
            raise RuntimeError(result.detail or f"could not send to {result.party}")
        return f"Sent to {result.party}."


class CommunicationCallTool(Tool):
    """Consequential: attempts to place a call. Same honesty contract as
    ``CommunicationSendTool``."""

    def __init__(self, provider: CommunicationProvider) -> None:
        self._provider = provider

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=CALL_TOOL_NAME,
            description="Places a call through a governed, authorized communication connection.",
            schema={"to": str},
        )

    def run(self, **arguments: str) -> str:
        result = self._provider.place_call(to=arguments["to"])
        if not result.ok:
            raise RuntimeError(result.detail or f"could not call {result.party}")
        return f"Calling {result.party}."

"""The governed boundary between Alfred (or a specialist) and one
operation on one Connection - Stage 16's own analogue of
``src/agents/tools/communication.py``.

**One tool per (connection, operation) pair, bound at construction -
never a generic dispatch a Brain could exploit to request an operation
nobody granted.** The identical reasoning Stage 15's own
``CommunicationReadTool``/``SendTool``/``CallTool`` already state: a
``Tool`` is handed its arguments and nothing else, so binding both the
connection *and* the specific operation at construction time means
``ToolSet`` membership - the same binary per-tool-name permission model
every other tool in this project already lives under - is the entire
governance surface. Granting ``connection_read_email`` never implies
any other operation, on this connection or a future one, is granted.

Generic over *which* connection and *which* operation: this file names
neither Gmail nor any future service, so a calendar/commerce/travel
connection added later needs no new Tool subclass, only a new
``ConnectionActionTool(connection, operation=..., description=...)``
constructed wherever tools are granted (see ``scripts/control_center.py``
for the identical precedent Stage 14/15 already set).
"""

from __future__ import annotations

from src.agents.tools.tool import Tool, ToolSpec
from src.providers.connection import Connection


def tool_name_for(operation: str) -> str:
    """The Tool name one operation is granted under - deterministic
    from the operation alone, so a capability handler that only knows
    the operation name (never a specific connection class) can still
    ask ``ToolSet.permits(tool_name_for(operation))``."""
    return f"connection_{operation}"


class ConnectionActionTool(Tool):
    """One governed action through one Connection."""

    def __init__(self, connection: Connection, *, operation: str, description: str) -> None:
        self._connection = connection
        self._operation = operation
        self._description = description

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name=tool_name_for(self._operation), description=self._description, schema={})

    def run(self, **arguments: object) -> str:
        result = self._connection.execute(self._operation)
        if not result.ok:
            raise RuntimeError(result.detail or f"{self._operation} did not succeed")
        return result.detail or f"{self._operation} succeeded"
